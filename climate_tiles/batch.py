"""
batch.py — Orquestación de la descarga multi-tile de tiles climáticos.

Flujo completo (sin depender de QThread ni QWidget):

  1. resolve_centroid()     → (lon, lat, bbox) desde punto o capa AOI
  2. tiles_for_aoi()        → lista de tile IDs que intersectan el AOI
  3. build_download_plan()  → lista de {var, tile_id, url} a descargar
  4. run_tile_downloads()   → descarga cada (var, tile) a un .tif temporal
  5. merge_and_clip_tiles() → mosaico por variable + recorte al AOI real
                              + limpieza de temporales

Cambio respecto a la versión anterior: se eliminó la advertencia de
"solo se descarga el tile del centroide". Ahora SIEMPRE se descargan
todos los tiles que intersectan el AOI, se ensamblan con gdal:merge
y se recortan al polígono real.
"""

import os

from . import catalog, geodata_api, tiles


class ClimateTileError(Exception):
    """Error de validación o de armado del plan de descarga (antes de tocar la red)."""


# ─────────────────────────────────────────────────────────────────────────────
# Resolución del AOI
# ─────────────────────────────────────────────────────────────────────────────

def resolve_centroid(lon=None, lat=None, aoi_layer=None):
    """
    Determina el punto (lon, lat) y bbox del AOI.

    - Si se pasan lon/lat explícitos → caso "punto", bbox=None.
    - Si se pasa aoi_layer (QgsVectorLayer) → calcula centroide y bbox
      de la geometría disuelta de sus features (requiere qgis.core).

    Retorna
    -------
    tuple(float, float, tuple|None)
        (lon, lat, bbox). bbox es (xmin,ymin,xmax,ymax) o None si es punto.
    """
    if lon is not None and lat is not None:
        return float(lon), float(lat), None

    if aoi_layer is None:
        raise ClimateTileError("Debes indicar lon/lat o una capa de AOI (punto o polígono).")

    from .geom import centroid_and_bbox_from_layer  # import diferido: requiere qgis.core
    return centroid_and_bbox_from_layer(aoi_layer)


# ─────────────────────────────────────────────────────────────────────────────
# Detección de tiles intersectados
# ─────────────────────────────────────────────────────────────────────────────

def tiles_for_aoi(lon, lat, bbox):
    """
    Devuelve la lista ordenada de tile IDs que deben descargarse para
    cubrir el AOI completo.

    - Si bbox es None (caso punto): devuelve [tile del punto].
    - Si bbox no es None (polígono): devuelve todos los tiles que
      intersectan el bounding box (1 a 4+ tiles para una grilla de 30°).

    Retorna
    -------
    list[int]  — siempre al menos 1 elemento.
    """
    if bbox is None:
        return [tiles.tile_id_from_lonlat(lon, lat)]
    return sorted(tiles.tiles_spanned_by_bbox(*bbox))


# ─────────────────────────────────────────────────────────────────────────────
# Construcción del plan de descarga
# ─────────────────────────────────────────────────────────────────────────────

def build_download_plan(mode, variables, tile_ids, model=None, ssp=None, period=None):
    """
    Valida variables y (si CMIP6) la combinación real contra el servidor,
    y arma la lista de (variable, tile_id, url) a descargar.

    Parámetros
    ----------
    mode : str
        catalog.MODE_HISTORICAL o catalog.MODE_CMIP6.
    variables : list[str]
        Variables internas (ver catalog.VARIABLES).
    tile_ids : list[int]
        Lista de tile IDs a descargar (uno o más, calculados por tiles_for_aoi).
    model, ssp, period : str, requeridos solo si mode == MODE_CMIP6.

    Retorna
    -------
    list[dict]: [{"var": str, "tile_id": int, "url": str}, ...]
        Un elemento por (variable, tile_id). Si hay 2 variables y 4 tiles
        el resultado tiene 8 entradas.

    Lanza
    -----
    ClimateTileError
        Si alguna variable no es válida para el modo, si faltan parámetros
        CMIP6, o si el servidor confirma que la combinación no existe.
    """
    for var in variables:
        if not catalog.is_variable_valid_for_mode(var, mode):
            modo_txt = "CMIP6 (proyección)" if mode == catalog.MODE_CMIP6 else "histórico"
            raise ClimateTileError(f"La variable '{var}' no está disponible en modo {modo_txt}.")

    plan = []
    if mode == catalog.MODE_CMIP6:
        if not (model and ssp and period):
            raise ClimateTileError("El modo CMIP6 requiere modelo, SSP y periodo.")
        for var in variables:
            ok, warn_msg = geodata_api.check_cmip6_combo(var, model, ssp, period)
            if not ok:
                raise ClimateTileError(warn_msg)
            for tile_id in tile_ids:
                url = geodata_api.build_cmip6_tile_url(model, ssp, period, var, tile_id)
                plan.append({"var": var, "tile_id": tile_id, "url": url})
    else:
        for var in variables:
            for tile_id in tile_ids:
                url = geodata_api.build_worldclim_tile_url(var, tile_id)
                plan.append({"var": var, "tile_id": tile_id, "url": url})

    return plan


# ─────────────────────────────────────────────────────────────────────────────
# Descarga de tiles individuales
# ─────────────────────────────────────────────────────────────────────────────

def run_tile_downloads(plan, output_folder, name_prefix, status_cb=None,
                        progress_cb=None, cancel_check=None, session=None):
    """
    Descarga cada entrada de `plan` a un archivo temporal individual:
      <output_folder>/<name_prefix>_<var>_tile<tile_id>.tif

    No se detiene ante el error de un tile individual: sigue con los
    demás y reporta el error en esa entrada del resultado.

    Retorna
    -------
    list[dict]: [{"var", "tile_id", "path", "error", "canceled"}, ...]
    """
    os.makedirs(output_folder, exist_ok=True)
    results = []
    total_items = len(plan)

    for idx, item in enumerate(plan):
        var, tile_id, url = item["var"], item["tile_id"], item["url"]
        output_path = os.path.join(output_folder, f"{name_prefix}_{var}_tile{tile_id}.tif")

        if cancel_check is not None and cancel_check():
            results.append({"var": var, "tile_id": tile_id, "path": None,
                            "error": None, "canceled": True})
            continue

        if status_cb:
            status_cb(f"Descargando '{var}' — tile {tile_id} ({idx + 1}/{total_items})...")

        # Callback de progreso ajustado a la fracción de este tile dentro del total
        def _scaled_progress(downloaded, total, _idx=idx, _total=total_items):
            if progress_cb is None:
                return
            base_pct = int((_idx / _total) * 80)   # descargas ocupan 0-80%
            if total:
                tile_pct = int((downloaded / total) * (80 / _total))
                pct = min(79, base_pct + tile_pct)
            else:
                pct = base_pct
            mb = downloaded / (1024 * 1024)
            total_mb = total / (1024 * 1024) if total else 0
            msg = f"{mb:.1f}/{total_mb:.1f} MB (tile {_idx + 1}/{_total})" if total else f"{mb:.1f} MB"
            progress_cb(pct, msg)

        try:
            path = geodata_api.download_tile(
                url, output_path, session=session, cancel_check=cancel_check,
                status_cb=status_cb, progress_cb=_scaled_progress,
            )
            results.append({"var": var, "tile_id": tile_id, "path": path,
                            "error": None, "canceled": False})
        except geodata_api.GeodataCanceled:
            results.append({"var": var, "tile_id": tile_id, "path": None,
                            "error": None, "canceled": True})
        except geodata_api.GeodataApiError as exc:
            results.append({"var": var, "tile_id": tile_id, "path": None,
                            "error": str(exc), "canceled": False})

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Mosaico + recorte al AOI
# ─────────────────────────────────────────────────────────────────────────────

def merge_and_clip_tiles(tile_results, output_folder, name_prefix,
                          clip_mask_layer=None, target_crs=None,
                          status_cb=None, progress_cb=None, cancel_check=None):
    """
    Para cada variable en `tile_results`:
      1. Agrupa los tiles descargados exitosamente.
      2. Si hay >1 tile: ensambla un mosaico con clip.merge_rasters.
      3. Recorta el mosaico (o tile único) al AOI con clip.clip_raster_to_layer
         (solo si se pasó clip_mask_layer).
      4. Borra los tiles individuales temporales y el mosaico intermedio.

    Retorna
    -------
    list[dict]: [{"var", "path", "error", "canceled"}, ...]
        Un elemento por variable (ya no por tile).
    """
    from . import clip  # import diferido: requiere qgis.core + processing
    import collections

    # Agrupar tiles por variable preservando el orden
    by_var = collections.OrderedDict()
    for r in tile_results:
        var = r["var"]
        if var not in by_var:
            by_var[var] = []
        by_var[var].append(r)

    final_results = []
    n_vars = len(by_var)

    for var_idx, (var, var_tiles) in enumerate(by_var.items()):
        # Progreso: mosaico y recorte ocupan el 80-100%
        pct_base = 80 + int((var_idx / n_vars) * 20)

        # ¿Cancelado?
        if cancel_check is not None and cancel_check():
            final_results.append({"var": var, "path": None, "error": None, "canceled": True})
            continue

        # Separar tiles ok / con error / cancelados
        ok_tiles = [r for r in var_tiles if r.get("path") and not r.get("error") and not r.get("canceled")]
        any_canceled = any(r.get("canceled") for r in var_tiles)
        errors = [r["error"] for r in var_tiles if r.get("error")]

        if not ok_tiles:
            # No se descargó ningún tile para esta variable
            err_msg = "; ".join(errors) if errors else None
            final_results.append({"var": var, "path": None,
                                   "error": err_msg, "canceled": any_canceled})
            _cleanup_files([r["path"] for r in var_tiles if r.get("path")])
            continue

        tile_paths = [r["path"] for r in ok_tiles]

        # — Fase 1: Mosaico —
        if status_cb:
            status_cb(f"Ensamblando mosaico de '{var}' ({len(tile_paths)} tile(s))...")
        if progress_cb:
            progress_cb(pct_base, f"Mosaico '{var}'...")

        if clip_mask_layer is not None:
            # Si vamos a recortar, el mosaico es un temporal
            mosaic_path = os.path.join(output_folder, f"{name_prefix}_{var}_mosaic.tif")
        else:
            # Sin recorte el mosaico ES el resultado final
            mosaic_path = os.path.join(output_folder, f"{name_prefix}_{var}.tif")

        try:
            merge_out = clip.merge_rasters(tile_paths, mosaic_path)
        except clip.ClipError as exc:
            final_results.append({"var": var, "path": None,
                                   "error": f"No se pudo ensamblar el mosaico: {exc}",
                                   "canceled": False})
            _cleanup_files(tile_paths)
            continue

        # Tiles individuales ya no se necesitan
        _cleanup_files(tile_paths)

        # — Fase 2: Recorte al AOI —
        if clip_mask_layer is None:
            # Sin recorte: el mosaico ya es el resultado final
            final_results.append({"var": var, "path": merge_out,
                                   "error": None, "canceled": False})
            continue

        if status_cb:
            status_cb(f"Recortando '{var}' al polígono del AOI...")
        if progress_cb:
            pct_clip = min(99, pct_base + int(10 / n_vars))
            progress_cb(pct_clip, f"Recortando '{var}'...")

        clip_path = os.path.join(output_folder, f"{name_prefix}_{var}.tif")
        try:
            final_path = clip.clip_raster_to_layer(
                merge_out, clip_mask_layer, clip_path, target_crs
            )
            final_results.append({"var": var, "path": final_path,
                                   "error": None, "canceled": False})
        except clip.ClipError as exc:
            # Se deja el mosaico sin recortar y se avisa
            final_results.append({"var": var, "path": merge_out,
                                   "error": f"Descargado pero no se pudo recortar: {exc}",
                                   "canceled": False})
            continue
        finally:
            # Borrar el mosaico temporal solo si el recorte produjo un archivo distinto
            if os.path.exists(merge_out) and merge_out != clip_path:
                _cleanup_files([merge_out])

    return final_results


def _cleanup_files(paths):
    """Borra una lista de archivos ignorando errores (son temporales)."""
    import logging
    logger = logging.getLogger(__name__)
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError as err:
                logger.debug("No se pudo eliminar temporal %s: %s", p, err)


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────────────────────────────────────

def get_cmip6_output_folder(base_folder, model, period, ssp):
    """
    Construye la estructura de subcarpetas jerárquica para proyecciones CMIP6:
    <base_folder>/<model>/<period>/ssp<ssp>
    """
    if not (model and period and ssp):
        return base_folder
    ssp_str = str(ssp)
    ssp_folder = ssp_str if ssp_str.lower().startswith("ssp") else f"ssp{ssp_str}"
    return os.path.join(base_folder, str(model), str(period), ssp_folder)
