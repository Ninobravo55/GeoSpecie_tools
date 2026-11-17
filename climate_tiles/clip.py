"""
clip.py — Mosaico de tiles y recorte al AOI real.

Funciones:
  merge_rasters(input_paths, output_path)
      Ensambla múltiples tiles descargados en un único mosaico usando
      el algoritmo `gdal:merge` de Processing (sin dependencias externas
      a QGIS+GDAL que ya vienen incluidos).

  clip_raster_to_layer(raster_path, mask_layer, output_path, target_crs)
      Recorta el raster (tile único o mosaico) al bounding box real del
      AOI + buffer de 5 km, opcionalmente reproyectando al CRS destino.

Flujo típico (ver worker.py y batch.merge_and_clip_tiles):
  tiles individuales ─→ merge_rasters ─→ mosaico ─→ clip_raster_to_layer ─→ resultado final
"""

import os
import math
import processing
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject


class ClipError(Exception):
    """Error al recortar o ensamblar el raster."""


# ─────────────────────────────────────────────────────────────────────────────
# Mosaico de tiles
# ─────────────────────────────────────────────────────────────────────────────

def merge_rasters(input_paths, output_path):
    """
    Ensambla `input_paths` (lista de rutas a GeoTIFF) en un único mosaico
    `output_path` usando `gdal:merge`.

    Parámetros
    ----------
    input_paths : list[str]
        Rutas de los tiles a fusionar. Deben estar en el mismo CRS
        (EPSG:4326 siempre, porque vienen del servidor de WorldClim).
        Si la lista tiene un solo elemento, se copia directamente
        (evita un merge innecesario de un único tile).
    output_path : str
        Ruta de salida del mosaico.

    Retorna
    -------
    str
        Ruta del archivo de mosaico generado (== output_path).

    Lanza
    -----
    ClipError
        Si alguna ruta de entrada no existe o gdal:merge falla.
    """
    if not input_paths:
        raise ClipError("No se pasaron rutas de entrada para el mosaico.")

    missing = [p for p in input_paths if not os.path.exists(p)]
    if missing:
        raise ClipError(f"Archivo(s) de tile no encontrado(s): {', '.join(missing)}")

    # Con un solo tile no hace falta gdal:merge — solo se renombra/copia.
    if len(input_paths) == 1:
        import shutil
        shutil.copy2(input_paths[0], output_path)
        return output_path

    try:
        result = processing.run("gdal:merge", {
            "INPUT": input_paths,
            "PCT": False,
            "SEPARATE": False,
            "NODATA_INPUT": None,
            "NODATA_OUTPUT": None,
            "OPTIONS": "COMPRESS=LZW",
            "EXTRA": "",
            "DATA_TYPE": 5,  # usar el mismo tipo de datos que el input
            "OUTPUT": output_path,
        })
    except Exception as exc:
        raise ClipError(f"gdal:merge falló al ensamblar el mosaico: {exc}") from exc

    out = result.get("OUTPUT") if isinstance(result, dict) else None
    if not out or not os.path.exists(out):
        raise ClipError("gdal:merge no generó un archivo de salida válido.")

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Recorte al AOI
# ─────────────────────────────────────────────────────────────────────────────

def clip_raster_to_layer(raster_path, mask_layer, output_path, target_crs=None):
    """
    Recorta `raster_path` usando el bounding box de `mask_layer` más un
    buffer de 5000 metros, escribiendo `output_path`. Si `target_crs` es
    válido, reproyecta el raster final a ese CRS.
    """
    try:
        # Extent original de la capa
        extent = mask_layer.extent()
        layer_crs = mask_layer.crs()
        raster_crs = QgsCoordinateReferenceSystem("EPSG:4326")

        # Asegurar que las coordenadas del extent estén en EPSG:4326
        transform = QgsCoordinateTransform(layer_crs, raster_crs, QgsProject.instance())
        extent_4326 = transform.transformBoundingBox(extent)

        # Buffer de 5000 metros convertido a grados
        buffer_meters = 5000.0
        lat_center = extent_4326.center().y()

        lat_buffer_deg = buffer_meters / 111320.0
        cos_lat = math.cos(math.radians(lat_center))
        if cos_lat < 0.01:
            cos_lat = 0.01
        lon_buffer_deg = buffer_meters / (111320.0 * cos_lat)

        # Recuadro (bounding box) expandido
        x_min = extent_4326.xMinimum() - lon_buffer_deg
        x_max = extent_4326.xMaximum() + lon_buffer_deg
        y_min = extent_4326.yMinimum() - lat_buffer_deg
        y_max = extent_4326.yMaximum() + lat_buffer_deg

        projwin = f"{x_min},{x_max},{y_min},{y_max} [EPSG:4326]"

        if target_crs is not None and target_crs.isValid() and target_crs.authid() != "EPSG:4326":
            clip_res = processing.run("gdal:cliprasterbyextent", {
                "INPUT": raster_path,
                "PROJWIN": projwin,
                "NODATA": None,
                "OPTIONS": "",
                "DATA_TYPE": 5,
                "OUTPUT": "TEMPORARY_OUTPUT",
            })
            temp_clip = clip_res.get("OUTPUT")

            result = processing.run("gdal:warpreproject", {
                "INPUT": temp_clip,
                "TARGET_CRS": target_crs.authid(),
                "NODATA": None,
                "OPTIONS": "",
                "DATA_TYPE": 5,
                "OUTPUT": output_path,
            })
        else:
            result = processing.run("gdal:cliprasterbyextent", {
                "INPUT": raster_path,
                "PROJWIN": projwin,
                "NODATA": None,
                "OPTIONS": "",
                "DATA_TYPE": 5,
                "OUTPUT": output_path,
            })
    except Exception as exc:  # noqa: BLE001
        raise ClipError(f"No se pudo recortar/reproyectar el raster: {exc}") from exc

    out = result.get("OUTPUT") if isinstance(result, dict) else None
    if not out or not os.path.exists(out):
        raise ClipError("El algoritmo de recorte/reproyección no generó un archivo de salida válido.")
    return out
