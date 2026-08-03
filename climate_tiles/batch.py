"""
batch.py — Orquestación de la descarga de tiles climáticos, sin
depender de QThread ni de ningún QWidget (mismo espíritu que
species_download/batch.py: la lógica pesada vive aquí, worker.py solo
la envuelve en señales Qt).

Flujo: centroide (punto directo o de una capa) -> tile ID -> armar plan
de descarga (validando variables por modo y, si es CMIP6, la
combinación real contra el servidor) -> ejecutar descargas.
"""

import os

from . import catalog, geodata_api, tiles


class ClimateTileError(Exception):
    """Error de validación o de armado del plan de descarga (antes de tocar la red)."""


def resolve_centroid(lon=None, lat=None, aoi_layer=None):
    """
    Determina el punto (lon, lat) que decide qué tile se descarga.

    - Si se pasan lon/lat explícitos -> caso "punto", se usan tal cual.
    - Si se pasa aoi_layer (QgsVectorLayer, punto o polígono) -> se
      calcula el centroide de la geometría disuelta de sus features
      (requiere qgis.core; ver geom.py).

    Retorna
    -------
    tuple(float, float, tuple|None)
        (lon, lat, bbox). bbox es (xmin,ymin,xmax,ymax) de la geometría
        de aoi_layer, o None si el caso es un punto directo (un punto
        no puede "abarcar más de un tile").
    """
    if lon is not None and lat is not None:
        return float(lon), float(lat), None

    if aoi_layer is None:
        raise ClimateTileError("Debes indicar lon/lat o una capa de AOI (punto o polígono).")

    from .geom import centroid_and_bbox_from_layer  # import diferido: requiere qgis.core
    return centroid_and_bbox_from_layer(aoi_layer)


def check_multi_tile_warning(bbox):
    """
    Si `bbox` abarca más de un tile, devuelve un mensaje de advertencia
    explícito. Decisión ya tomada con el usuario: se acepta descargar
    solo el tile del centroide (no el mosaico completo), pero eso no
    debe pasar en silencio -- si el AOI cruza un borde de tile, el
    usuario tiene que saber que parte de su polígono va a quedar sin
    datos en el resultado, ANTES de descargar, no después.

    Retorna None si bbox es None (caso punto) o si todo cae en 1 tile.
    """
    if bbox is None:
        return None
    spanned = tiles.tiles_spanned_by_bbox(*bbox)
    if len(spanned) <= 1:
        return None
    return (
        f"Tu área de interés abarca {len(spanned)} tiles distintos (IDs: {sorted(spanned)}), "
        "pero solo se descargará el tile que contiene el CENTROIDE. La parte de tu polígono "
        "que cae fuera de ese tile quedará sin datos en el resultado."
    )


def build_download_plan(mode, variables, tile_id, model=None, ssp=None, period=None):
    """
    Valida variables y (si CMIP6) la combinación real contra el
    servidor, y arma la lista de (variable, url) a descargar.

    Parámetros
    ----------
    mode : str
        catalog.MODE_HISTORICAL o catalog.MODE_CMIP6.
    variables : list[str]
        Variables internas (ver catalog.VARIABLES), ya filtradas por
        el checklist del diálogo -- pero se revalidan aquí igual, no
        se confía en que la UI ya filtró correctamente.
    tile_id : int
        Tile calculado por tiles.tile_id_from_lonlat().
    model, ssp, period : str, requeridos solo si mode == MODE_CMIP6.

    Retorna
    -------
    list[dict]: [{"var": str, "url": str}, ...]

    Lanza
    -----
    ClimateTileError
        Si alguna variable no es válida para el modo, si faltan
        parámetros de CMIP6, o si el servidor confirma que la
        combinación no existe.
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
            url = geodata_api.build_cmip6_tile_url(model, ssp, period, var, tile_id)
            plan.append({"var": var, "url": url})
    else:
        for var in variables:
            url = geodata_api.build_worldclim_tile_url(var, tile_id)
            plan.append({"var": var, "url": url})

    return plan


def run_tile_downloads(plan, output_folder, name_prefix, status_cb=None,
                        progress_cb=None, cancel_check=None, session=None):
    """
    Ejecuta la descarga de cada entrada de `plan` (ver
    build_download_plan) a `output_folder/<name_prefix>_<var>.tif`.

    No se detiene ante el error de una variable individual: sigue con
    las demás y reporta el error en el resultado de esa entrada.

    Retorna
    -------
    list[dict]: [{"var", "path", "error", "canceled"}, ...]
    """
    os.makedirs(output_folder, exist_ok=True)
    results = []

    for item in plan:
        var, url = item["var"], item["url"]
        output_path = os.path.join(output_folder, f"{name_prefix}_{var}.tif")

        if cancel_check is not None and cancel_check():
            results.append({"var": var, "path": None, "error": None, "canceled": True})
            continue

        if status_cb:
            status_cb(f"Descargando variable '{var}'...")

        try:
            path = geodata_api.download_tile(
                url, output_path, session=session, cancel_check=cancel_check,
                status_cb=status_cb, progress_cb=progress_cb,
            )
            results.append({"var": var, "path": path, "error": None, "canceled": False})
        except geodata_api.GeodataCanceled:
            results.append({"var": var, "path": None, "error": None, "canceled": True})
        except geodata_api.GeodataApiError as exc:
            results.append({"var": var, "path": None, "error": str(exc), "canceled": False})

    return results


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

