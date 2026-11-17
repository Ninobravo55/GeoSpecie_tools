"""
gee_tiled_download.py — Descarga local de imágenes GEE divididas en grilla.

Problema que resuelve:
    GEE limita cada petición de `getDownloadURL` a ~48 MB (~50 331 648 bytes).
    Para áreas de interés grandes ese límite se supera y el servidor devuelve:
        "Total request size (XXXXXX bytes) must be less than or equal to
         50331648 bytes"

Solución:
    1. Calcular el tamaño estimado del request a partir del bbox, escala y
       tipo de dato — sin tocar la red.
    2. Si supera el umbral, dividir el bbox en una grilla de celdas que sí
       quepan dentro del límite.
    3. Descargar cada celda como un GeoTIFF temporal independiente en la
       subcarpeta <out_folder>/tiles_<banda>/.
    4. Ensamblar todas las celdas en un único mosaico con `gdal:merge`.
    5. (Opcional) Recortar el mosaico al AOI real con `gdal:cliprasterbyextent`.
    6. Borrar los tiles temporales.

Uso:
    from ..utils.gee_tiled_download import download_band_local

    download_band_local(
        ee, imagen, banda, aoi_ee, crs, scale, out_folder, filepath, feedback
    )

Notas de diseño:
    - El corte en grilla se hace SIEMPRE en coordenadas geográficas (EPSG:4326)
      ya que es el CRS que GEE entiende para la región de descarga. El raster
      resultante se reproyecta al CRS de destino porque la imagen ya viene
      reproyectada desde GEE (`.reproject(crs, scale)`).
    - No se usa multithread: las peticiones GEE tienen rate-limiting propio y
      el bottleneck es la red, no la CPU.
    - gdal:merge y gdal:cliprasterbyextent vienen con QGIS/GDAL; no se añade
      ninguna dependencia externa.
"""

import os
import math
import re
import shutil
import logging
import time

logger = logging.getLogger(__name__)

# Límite oficial de GEE para getDownloadURL (bytes).
GEE_MAX_BYTES = 50_331_648
# Margen de seguridad para el CÁLCULO INICIAL de la grilla. Se usa 80% y no
# más, porque el estimado en `_estimate_request_bytes` convierte grados a
# metros de forma lineal (asumiendo una proyección equirectangular), pero la
# imagen real se reproyecta al CRS de destino (a menudo UTM) antes de
# descargarse. Para AOIs anchos que cruzan zonas UTM, el tamaño real puede
# superar el estimado en 20-30% por la distorsión de área de la proyección
# lejos del meridiano central. El margen aquí es la primera línea de
# defensa; la subdivisión adaptativa en `_download_cell` (que reacciona al
# tamaño REAL que GEE reporta en el error) es la que garantiza que la
# descarga eventualmente cabe, sin importar cuánto se haya errado la
# estimación inicial.
GEE_SAFE_BYTES = int(GEE_MAX_BYTES * 0.80)  # ~40 MB

# Bytes por píxel según el tipo de dato GEE (float32 = 4 bytes, int16 = 2 bytes)
# Todas las herramientas usan .toFloat() → 4 bytes
BYTES_PER_PIXEL_FLOAT = 4

# Reintentos por celda ante errores transitorios (timeouts, 5xx, cortes de
# red). NO se reintenta si el error es "request demasiado grande" — eso
# no se arregla repitiendo la misma petición, se arregla subdividiendo
# (ver _download_cell / MAX_SPLIT_DEPTH).
TILE_MAX_RETRIES = 2
TILE_RETRY_BACKOFF_S = 3  # se duplica en cada intento: 3s, 6s...

# Cuántas veces como máximo se puede volver a subdividir UNA celda que
# sigue siendo demasiado grande. GEE reporta el tamaño real en el mensaje
# de error, así que cada subdivisión usa ese dato real (no una nueva
# suposición) para decidir en cuántas partes dividir. 3 niveles alcanza
# incluso para errores de estimación severos (cada nivel puede dividir en
# 2x2, 3x3... según haga falta).
MAX_SPLIT_DEPTH = 3

_SIZE_ERROR_RE = re.compile(r"Total request size \((\d+) bytes\)")


def _is_size_error(msg):
    return "must be less than or equal to" in msg or "Total request size" in msg


def _estimate_request_bytes(xmin, ymin, xmax, ymax, scale_m):
    """
    Estima el tamaño en bytes de un getDownloadURL para un bbox dado.

    Usa la resolución en metros y el tamaño del bbox en grados para
    calcular cuántos píxeles tendría el raster descargado.

    Parámetros
    ----------
    xmin, ymin, xmax, ymax : float
        Bounding box en EPSG:4326 (grados).
    scale_m : float
        Resolución en metros del raster a descargar.

    Retorna
    -------
    int  — bytes estimados (Float32, 1 banda).
    """
    # Conversión grados → metros (aproximada, suficiente para estimar)
    lat_center = (ymin + ymax) / 2.0
    cos_lat = math.cos(math.radians(lat_center))
    if cos_lat < 0.01:
        cos_lat = 0.01

    width_m = (xmax - xmin) * 111_320.0 * cos_lat
    height_m = (ymax - ymin) * 111_320.0

    n_cols = math.ceil(width_m / scale_m)
    n_rows = math.ceil(height_m / scale_m)

    return n_cols * n_rows * BYTES_PER_PIXEL_FLOAT


def _compute_grid(xmin, ymin, xmax, ymax, scale_m):
    """
    Divide el bbox en filas × columnas de celdas tal que cada celda
    cabe dentro de GEE_SAFE_BYTES (Float32, 1 banda).

    Retorna
    -------
    list[tuple(float,float,float,float)]
        Lista de bboxes (xmin,ymin,xmax,ymax) de cada celda, en EPSG:4326.
    """
    total_bytes = _estimate_request_bytes(xmin, ymin, xmax, ymax, scale_m)
    if total_bytes <= GEE_SAFE_BYTES:
        return [(xmin, ymin, xmax, ymax)]

    # Número mínimo de celdas para bajar de GEE_SAFE_BYTES
    n_cells = math.ceil(total_bytes / GEE_SAFE_BYTES)

    # Grilla cuadrada: filas ≈ cols ≈ sqrt(n_cells)
    side = math.ceil(math.sqrt(n_cells))
    n_cols = side
    n_rows = math.ceil(n_cells / n_cols)

    dx = (xmax - xmin) / n_cols
    dy = (ymax - ymin) / n_rows

    cells = []
    for row in range(n_rows):
        for col in range(n_cols):
            cx0 = xmin + col * dx
            cy0 = ymin + row * dy
            cx1 = min(cx0 + dx, xmax)
            cy1 = min(cy0 + dy, ymax)
            cells.append((cx0, cy0, cx1, cy1))

    return cells


def _bbox_from_ee_geometry(ee, aoi_ee):
    """
    Extrae el bounding box (xmin, ymin, xmax, ymax) de un ee.Geometry
    en EPSG:4326 haciendo una llamada .bounds().coordinates().getInfo().

    Retorna
    -------
    tuple(float, float, float, float)
    """
    coords = aoi_ee.bounds().coordinates().getInfo()[0]
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return min(xs), min(ys), max(xs), max(ys)


def estimate_tile_count(ee, aoi_ee, scale_m):
    """
    Calcula cuántas celdas de grilla requerirá UNA banda a esta escala,
    sin descargar nada. Pensado para que cada algoritmo lo llame UNA
    sola vez, antes de su loop de variables/bandas, y así avisarle al
    usuario el volumen total del trabajo (celdas × variables) antes de
    arrancar — en vez de que se entere banda por banda, ya empezado.

    Parámetros
    ----------
    ee : módulo earthengine-api importado.
    aoi_ee : ee.Geometry
        Área de interés (se usa su bounding box).
    scale_m : float
        Resolución en metros del raster a descargar.

    Retorna
    -------
    int — número de celdas que necesitará cada banda a esa escala.
    """
    xmin, ymin, xmax, ymax = _bbox_from_ee_geometry(ee, aoi_ee)
    return len(_compute_grid(xmin, ymin, xmax, ymax, scale_m))


def announce_grid_plan(feedback, n_cells, n_variables=1):
    """
    Imprime UNA vez, antes de arrancar el trabajo, el volumen total de
    descargas que va a ejecutarse. No hace nada si el AOI es chico
    (n_cells == 1) — en ese caso no hay grilla, no hay nada que anunciar.
    """
    if n_cells <= 1:
        return
    if n_variables > 1:
        feedback.pushInfo(
            f"⚠ Área grande: cada variable se descargará en grilla de "
            f"{n_cells} celdas. Con {n_variables} variables seleccionadas, "
            f"esto son hasta {n_cells * n_variables} descargas de tiles en total."
        )
    else:
        feedback.pushInfo(
            f"⚠ Área grande: la descarga se dividirá en grilla de {n_cells} celdas."
        )


def _merge_tiles(tile_paths, output_path, feedback=None):
    """
    Ensambla una lista de GeoTIFF en un mosaico con `gdal:merge`.

    Si solo hay un tile se copia directamente (sin merge innecesario).
    """
    import processing

    if not tile_paths:
        raise RuntimeError("No hay tiles para ensamblar.")

    if len(tile_paths) == 1:
        shutil.copy2(tile_paths[0], output_path)
        return output_path

    if feedback:
        feedback.pushInfo(f"    Ensamblando {len(tile_paths)} tiles → {os.path.basename(output_path)}...")

    result = processing.run("gdal:merge", {
        "INPUT": tile_paths,
        "PCT": False,
        "SEPARATE": False,
        "NODATA_INPUT": None,
        "NODATA_OUTPUT": None,
        "OPTIONS": "COMPRESS=LZW",
        "EXTRA": "",
        "DATA_TYPE": 5,
        "OUTPUT": output_path,
    })

    out = result.get("OUTPUT") if isinstance(result, dict) else None
    if not out or not os.path.exists(out):
        raise RuntimeError("gdal:merge no generó un archivo de salida válido.")

    return out


def _cleanup_tile_dir(tile_dir):
    """Borra la carpeta temporal de tiles si existe."""
    if tile_dir and os.path.isdir(tile_dir):
        try:
            shutil.rmtree(tile_dir)
        except OSError as err:
            logger.debug("No se pudo borrar carpeta temporal %s: %s", tile_dir, err)


def download_band_local(ee, imagen, banda, aoi_ee, crs, scale, out_folder,
                         filepath, feedback, download_params=None):
    """
    Descarga una banda de una ee.Image GEE como GeoTIFF local.

    Si el AOI es pequeño, descarga directamente en un solo request.
    Si es grande (superaría el límite de GEE), divide en grilla, descarga
    cada celda como tile temporal, ensambla el mosaico y borra los tiles.

    Parámetros
    ----------
    ee : módulo earthengine-api importado.
    imagen : ee.Image
        Imagen GEE ya reproyectada y con factores de escala aplicados.
    banda : str
        Nombre de la banda a descargar.
    aoi_ee : ee.Geometry
        Área de interés en EPSG:4326 (bounds del polígono).
    crs : str
        CRS de destino, e.g. 'EPSG:32718'.
    scale : float
        Resolución en metros.
    out_folder : str
        Carpeta donde se guarda el resultado final.
    filepath : str
        Ruta completa del GeoTIFF de salida final.
    feedback : QgsProcessingFeedback
        Para pushInfo / reportError / isCanceled.
    download_params : dict | None
        Parámetros adicionales para getDownloadURL (e.g. formatOptions).

    Retorna
    -------
    bool — True si la descarga fue exitosa, False si hubo error.
    """
    import urllib.request

    if download_params is None:
        download_params = {}

    # ── Calcular bbox del AOI ────────────────────────────────────────────
    try:
        xmin, ymin, xmax, ymax = _bbox_from_ee_geometry(ee, aoi_ee)
    except Exception as exc:
        feedback.reportError(f"    ✗ No se pudo obtener el bbox del AOI: {exc}")
        return False

    # ── Calcular grilla ──────────────────────────────────────────────────
    cells = _compute_grid(xmin, ymin, xmax, ymax, scale)
    n_cells = len(cells)

    if n_cells == 1:
        # Descarga directa — no se necesita grilla
        return _download_single(
            ee, imagen, banda, aoi_ee, crs, scale, filepath,
            feedback, download_params
        )

    # ── Descarga por tiles ───────────────────────────────────────────────
    feedback.pushInfo(
        f"    ⚠ Área grande detectada: dividiendo en grilla de {n_cells} celdas..."
    )
    tile_dir = os.path.join(out_folder, f"_tiles_{banda}")
    os.makedirs(tile_dir, exist_ok=True)

    tile_paths = []
    failed_cells = []  # [(label, (cx0,cy0,cx1,cy1), error_msg), ...]
    try:
        for idx, (cx0, cy0, cx1, cy1) in enumerate(cells):
            if feedback.isCanceled():
                return False

            feedback.pushInfo(
                f"    Celda {idx + 1}/{n_cells} "
                f"[{cx0:.4f},{cy0:.4f} → {cx1:.4f},{cy1:.4f}]..."
            )

            paths, fails = _download_cell(
                ee, imagen, banda, cx0, cy0, cx1, cy1, scale, crs,
                tile_dir, str(idx + 1), feedback, download_params,
            )
            tile_paths.extend(paths)
            failed_cells.extend(fails)

        # ── Resumen explícito de celdas (no solo logs dispersos) ──────────
        n_ok = len(tile_paths)
        if failed_cells:
            feedback.reportError(
                f"    ⚠ Resumen '{banda}': {n_ok} tile(s) OK, "
                f"{len(failed_cells)} fallida(s): "
                + ", ".join(label for label, _bbox, _err in failed_cells)
                + ". El mosaico final puede tener huecos en esas zonas."
            )
        else:
            feedback.pushInfo(f"    ✓ Resumen '{banda}': {n_ok} tile(s) OK.")

        if not tile_paths:
            feedback.reportError(f"    ✗ No se descargó ningún tile para '{banda}'.")
            return False

        # ── Ensamblar mosaico ────────────────────────────────────────────
        try:
            _merge_tiles(tile_paths, filepath, feedback)
            feedback.pushInfo(f"    ✓ Guardado: {filepath}")
            return True
        except Exception as exc:
            feedback.reportError(f"    ✗ Error ensamblando mosaico de '{banda}': {exc}")
            return False

    finally:
        # Borrar tiles temporales siempre, aunque haya habido error
        _cleanup_tile_dir(tile_dir)


def _download_cell(ee, imagen, banda, cx0, cy0, cx1, cy1, scale, crs,
                    tile_dir, label, feedback, download_params, depth=0):
    """
    Descarga una celda (bbox EPSG:4326). Si GEE la rechaza por tamaño,
    parsea el tamaño REAL reportado en el mensaje de error y subdivide
    ESA celda en un NxN calculado a partir de ese dato real — no de una
    nueva suposición — y reintenta cada sub-celda recursivamente, hasta
    MAX_SPLIT_DEPTH niveles. Errores transitorios (red, timeouts) usan
    el reintento con backoff normal, no subdivisión.

    Retorna
    -------
    tuple(list[str], list[tuple]) — (tile_paths descargados, fallos)
    """
    import urllib.request

    tile_path = os.path.join(tile_dir, f"{banda}_tile_{label}.tif")
    cell_aoi = ee.Geometry.Rectangle([cx0, cy0, cx1, cy1])
    params = {
        "name": f"{banda}_tile_{label}",
        "scale": scale,
        "crs": crs,
        "region": cell_aoi,
        "format": "GEO_TIFF",
    }
    params.update(download_params)

    last_error = None
    for attempt in range(1, TILE_MAX_RETRIES + 2):  # intento 1 + reintentos
        try:
            url = imagen.select(banda).getDownloadURL(params)
            urllib.request.urlretrieve(url, tile_path)  # nosec
            return [tile_path], []
        except Exception as exc:
            last_error = exc
            msg = str(exc)

            if _is_size_error(msg):
                if feedback.isCanceled():
                    return [], []
                m = _SIZE_ERROR_RE.search(msg)
                if not m or depth >= MAX_SPLIT_DEPTH:
                    feedback.reportError(
                        f"    ✗ Celda {label} de '{banda}' sigue excediendo el "
                        f"límite tras {depth} subdivisión(es): {msg}"
                    )
                    return [], [(label, (cx0, cy0, cx1, cy1), msg)]

                actual_bytes = int(m.group(1))
                split_n = max(2, math.ceil(actual_bytes / GEE_SAFE_BYTES))
                side = math.ceil(math.sqrt(split_n))
                feedback.pushInfo(
                    f"      ↳ Celda {label} excede el límite ({actual_bytes / 1e6:.1f} MB "
                    f"reales vs {GEE_MAX_BYTES / 1e6:.0f} MB máx.). Subdividiendo en "
                    f"{side}x{side}..."
                )
                dx = (cx1 - cx0) / side
                dy = (cy1 - cy0) / side
                sub_paths, sub_fails = [], []
                for r in range(side):
                    for c in range(side):
                        if feedback.isCanceled():
                            return sub_paths, sub_fails
                        sx0 = cx0 + c * dx
                        sy0 = cy0 + r * dy
                        sx1 = min(sx0 + dx, cx1)
                        sy1 = min(sy0 + dy, cy1)
                        p, f = _download_cell(
                            ee, imagen, banda, sx0, sy0, sx1, sy1, scale, crs,
                            tile_dir, f"{label}-{r}{c}", feedback, download_params,
                            depth=depth + 1,
                        )
                        sub_paths.extend(p)
                        sub_fails.extend(f)
                return sub_paths, sub_fails

            # Error transitorio (no de tamaño): reintento normal con backoff
            if attempt <= TILE_MAX_RETRIES:
                wait_s = TILE_RETRY_BACKOFF_S * (2 ** (attempt - 1))
                feedback.pushInfo(
                    f"      ↻ Reintento {attempt}/{TILE_MAX_RETRIES} de la "
                    f"celda {label} en {wait_s}s (motivo: {msg})..."
                )
                time.sleep(wait_s)

    feedback.reportError(
        f"    ✗ Error en celda {label} de '{banda}' "
        f"tras {TILE_MAX_RETRIES + 1} intento(s): {last_error}"
    )
    return [], [(label, (cx0, cy0, cx1, cy1), str(last_error))]


def _download_single(ee, imagen, banda, aoi_ee, crs, scale, filepath,
                      feedback, download_params):
    """
    Descarga directa de una sola región. Si GEE la rechaza por tamaño
    (el estimado inicial dijo que una celda alcanzaba, pero la
    distorsión real de la reproyección hizo que no fuera así), cae al
    mismo mecanismo de subdivisión adaptativa que usa la grilla grande.
    """
    import urllib.request

    params = {
        "name": banda,
        "scale": scale,
        "crs": crs,
        "region": aoi_ee,
        "format": "GEO_TIFF",
    }
    params.update(download_params)

    try:
        url = imagen.select(banda).getDownloadURL(params)
        urllib.request.urlretrieve(url, filepath)  # nosec
        feedback.pushInfo(f"    ✓ Guardado: {filepath}")
        return True
    except Exception as exc:
        msg = str(exc)
        if not _is_size_error(msg):
            feedback.reportError(f"    ✗ Error en '{banda}': {msg}")
            return False

        # El estimado dijo "1 celda alcanza" pero GEE dice que no.
        # Subdividir usando el tamaño real, igual que en la grilla grande.
        feedback.pushInfo(
            f"    ⚠ La estimación inicial subestimó el tamaño real. Subdividiendo..."
        )
        xmin, ymin, xmax, ymax = _bbox_from_ee_geometry(ee, aoi_ee)
        tile_dir_name = os.path.dirname(filepath) or "."
        tile_dir = os.path.join(tile_dir_name, f"_tiles_{banda}")
        os.makedirs(tile_dir, exist_ok=True)
        try:
            paths, fails = _download_cell(
                ee, imagen, banda, xmin, ymin, xmax, ymax, scale, crs,
                tile_dir, "1", feedback, download_params,
            )
            if not paths:
                feedback.reportError(f"    ✗ No se descargó ningún tile para '{banda}'.")
                return False
            if fails:
                feedback.reportError(
                    f"    ⚠ {len(paths)} tile(s) OK, {len(fails)} fallida(s). "
                    "El mosaico final puede tener huecos en esas zonas."
                )
            _merge_tiles(paths, filepath, feedback)
            feedback.pushInfo(f"    ✓ Guardado: {filepath}")
            return True
        except Exception as exc2:
            feedback.reportError(f"    ✗ Error en '{banda}' tras subdividir: {exc2}")
            return False
        finally:
            _cleanup_tile_dir(tile_dir)
