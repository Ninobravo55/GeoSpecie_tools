"""
clip.py — Recorta el tile completo descargado (30°x30°) a la geometría
real del AOI, usando el algoritmo `gdal:cliprasterbymasklayer` de
Processing (QGIS ya trae GDAL; no se gestiona otra dependencia aparte).

IMPORTANTE: esto NO reduce el tamaño de la descarga -- el tile completo
se descarga igual (esa es la limitación aceptada de "solo tile del
centroide", ver batch.check_multi_tile_warning). Este recorte solo
reduce el tamaño del archivo final en disco y de lo que se carga en
QGIS después de la descarga.
"""

import os
import math
import processing
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject


class ClipError(Exception):
    """Error al recortar el raster descargado a la geometría del AOI."""


def clip_raster_to_layer(raster_path, mask_layer, output_path, target_crs=None):
    """
    Recorta `raster_path` usando el recuadro de `mask_layer` más un buffer
    de 5000 metros, escribiendo `output_path`. Si `target_crs` es válido, 
    reproyecta el raster final a ese CRS.
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
                "DATA_TYPE": 0,
                "OUTPUT": "TEMPORARY_OUTPUT",
            })
            temp_clip = clip_res.get("OUTPUT")
            
            result = processing.run("gdal:warpreproject", {
                "INPUT": temp_clip,
                "TARGET_CRS": target_crs.authid(),
                "NODATA": None,
                "OPTIONS": "",
                "DATA_TYPE": 0,
                "OUTPUT": output_path,
            })
        else:
            result = processing.run("gdal:cliprasterbyextent", {
                "INPUT": raster_path,
                "PROJWIN": projwin,
                "NODATA": None,
                "OPTIONS": "",
                "DATA_TYPE": 0,
                "OUTPUT": output_path,
            })
    except Exception as exc:  # noqa: BLE001
        raise ClipError(f"No se pudo recortar/reproyectar el raster: {exc}") from exc

    out = result.get("OUTPUT") if isinstance(result, dict) else None
    if not out or not os.path.exists(out):
        raise ClipError("El algoritmo de recorte/reproyección no generó un archivo de salida válido.")
    return out
