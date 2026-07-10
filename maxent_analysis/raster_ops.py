"""
raster_ops.py — Operaciones sobre el raster continuo de MaxEnt:
umbral binario, clasificación de hábitat en 4 clases, poligonización,
disolución por clase y cálculo de áreas.

Depende de qgis.core + processing (gdal:rastercalculator,
gdal:polygonize, native:dissolve) -- no es puro Python, a diferencia de
catalog.py/reader.py/file_finder.py.

Manejo de archivos bloqueados: TODAS las salidas de este módulo pasan
por safe_output_path(), a diferencia del script original donde solo
vector_disuelto tenía ese fallback y raster_binario/vector/
raster_clasificado podían fallar con "archivo en uso" si el usuario
corría la herramienta dos veces con las capas anteriores todavía
cargadas en QGIS.
"""

import os

import processing
from qgis.core import QgsDistanceArea, QgsProject, QgsVectorLayer

from . import catalog


class RasterOpsError(Exception):
    """Error en una operación raster/vector de Processing."""


def safe_output_path(path):
    """
    Si `path` existe y está bloqueado (abierto en QGIS -- típicamente
    da PermissionError al intentar borrarlo en Windows), devuelve una
    variante "<nombre>_1.<ext>", "_2", etc. hasta encontrar una libre.
    Si no existe o se puede borrar, devuelve `path` tal cual.

    Aplica el mismo criterio que el script original usaba SOLO para
    vector_disuelto -- acá se generaliza a toda salida de este módulo.
    """
    if not os.path.exists(path):
        return path

    try:
        os.remove(path)
        return path
    except PermissionError:
        base, ext = os.path.splitext(path)
        i = 1
        while True:
            candidate = f"{base}_{i}{ext}"
            if not os.path.exists(candidate):
                return candidate
            i += 1


def _run_processing(alg_name, params, error_context):
    try:
        return processing.run(alg_name, params)
    except Exception as exc:  # noqa: BLE001 - Processing puede lanzar tipos variados según el algoritmo
        raise RasterOpsError(f"{error_context}: {exc}") from exc


def make_binary_raster(raster_path, threshold, output_path):
    """Mapa binario presencia/ausencia (gdal:rastercalculator, A >= threshold)."""
    output_path = safe_output_path(output_path)
    formula = f"(A >= {threshold})"
    _run_processing("gdal:rastercalculator", {
        "INPUT_A": raster_path, "BAND_A": 1, "FORMULA": formula,
        "NO_DATA": 0, "RTYPE": 1, "OUTPUT": output_path,
    }, "No se pudo generar el raster binario")
    return output_path


def make_habitat_classification_raster(raster_path, output_path):
    """Mapa de 4 clases de idoneidad (cortes fijos, ver catalog.HABITAT_CLASSES)."""
    output_path = safe_output_path(output_path)
    formula = catalog.habitat_raster_formula("A")
    _run_processing("gdal:rastercalculator", {
        "INPUT_A": raster_path, "BAND_A": 1, "FORMULA": formula,
        "NO_DATA": -9999, "RTYPE": 1, "OUTPUT": output_path,
    }, "No se pudo generar el raster de clasificación de hábitat")
    return output_path


def polygonize(raster_path, field_name, output_path, eight_connectedness=False):
    output_path = safe_output_path(output_path)
    _run_processing("gdal:polygonize", {
        "INPUT": raster_path, "BAND": 1, "FIELD": field_name,
        "EIGHT_CONNECTEDNESS": eight_connectedness, "EXTRA": "", "OUTPUT": output_path,
    }, "No se pudo poligonizar el raster")
    return output_path


def dissolve_by_field(vector_path, field_name, output_path):
    output_path = safe_output_path(output_path)
    _run_processing("native:dissolve", {
        "INPUT": vector_path, "FIELD": [field_name], "OUTPUT": output_path,
    }, "No se pudo disolver el vector")
    return output_path


def compute_areas_by_feature(vector_path, extra_fields=None):
    """
    Calcula el área real (elipsoidal WGS84, vía QgsDistanceArea -- igual
    que el script original) de cada feature de `vector_path`, en UNA
    sola lectura -- si se necesitan además otros atributos (ej. el
    campo "Clase"), se piden en `extra_fields` para incluirlos en el
    mismo resultado, en vez de abrir el vector dos veces y emparejar
    área <-> atributo por POSICIÓN entre dos iteraciones separadas
    (frágil: depende de que ambas iteraciones devuelvan el mismo orden).

    Retorna
    -------
    list[dict]: [{"fid": int, "area_m2": float, <extra_fields>...}, ...]
    """
    layer = QgsVectorLayer(vector_path, "temp_area_calc", "ogr")
    if not layer.isValid():
        raise RasterOpsError(f"No se pudo abrir el vector para calcular áreas: {vector_path}")

    d = QgsDistanceArea()
    d.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
    d.setEllipsoid("WGS84")

    extra_fields = extra_fields or []
    results = []
    for feat in layer.getFeatures():
        area_m2 = d.measureArea(feat.geometry())
        entry = {"fid": feat.id(), "area_m2": area_m2}
        for field in extra_fields:
            entry[field] = feat[field]
        results.append(entry)
    return results
