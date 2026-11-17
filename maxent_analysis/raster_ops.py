"""
raster_ops.py — Operaciones sobre el raster continuo de MaxEnt:
umbral binario, clasificación de hábitat en 4 clases, poligonización,
disolución por clase, cálculo de áreas, reproyección y exportación del
raster continuo original.

Depende de qgis.core + processing (gdal:rastercalculator,
gdal:polygonize, native:dissolve, gdal:warpreproject) -- no es puro
Python, a diferencia de catalog.py/reader.py/file_finder.py.

Manejo de archivos bloqueados: TODAS las salidas de este módulo pasan
por safe_output_path(), a diferencia del script original donde solo
vector_disuelto tenía ese fallback y raster_binario/vector/
raster_clasificado podían fallar con "archivo en uso" si el usuario
corría la herramienta dos veces con las capas anteriores todavía
cargadas en QGIS.
"""

import os

import processing
from qgis.core import (
    QgsDistanceArea,
    QgsField,
    QgsProject,
    QgsVectorDataProvider,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant

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


def reproject_raster(input_path, output_path, target_crs_authid):
    """
    Reproyecta un raster al CRS indicado (authid, e.g. 'EPSG:32718')
    usando gdal:warpreproject.

    Devuelve `output_path` en caso de éxito.
    Si `target_crs_authid` está vacío o es None se devuelve `input_path`
    sin reproyectar (el archivo ya está en el CRS correcto).
    """
    if not target_crs_authid:
        return input_path
    output_path = safe_output_path(output_path)
    _run_processing("gdal:warpreproject", {
        "INPUT": input_path,
        "SOURCE_CRS": None,
        "TARGET_CRS": target_crs_authid,
        "RESAMPLING": 0,       # Nearest neighbour (preserva clases discretas)
        "NODATA": None,
        "TARGET_RESOLUTION": None,
        "OPTIONS": "COMPRESS=LZW",
        "DATA_TYPE": 5,        # Usar tipo del raster fuente (Float32)
        "TARGET_EXTENT": None,
        "TARGET_EXTENT_CRS": None,
        "MULTITHREADING": False,
        "EXTRA": "",
        "OUTPUT": output_path,
    }, f"No se pudo reproyectar el raster a {target_crs_authid}")
    return output_path


def copy_raster_as_tif(asc_path, output_path, target_crs_authid=None):
    """
    Convierte el raster continuo de MaxEnt (.asc) a GeoTIFF y lo
    reproyecta al CRS indicado.

    El raster continuo tiene valores de probabilidad 0–1 (Float32);
    se exporta sin modificar (sin clasificar) para que el usuario pueda
    aplicar sus propios umbrales o análisis post-hoc.

    Parámetros
    ----------
    asc_path : str
        Ruta al .asc continuo de MaxEnt.
    output_path : str
        Ruta de salida del GeoTIFF.
    target_crs_authid : str | None
        CRS de destino (e.g. 'EPSG:32718'). None = no reproyecta.

    Retorna
    -------
    str — ruta del GeoTIFF generado.
    """
    output_path = safe_output_path(output_path)

    if target_crs_authid:
        # Convertir + reproyectar en un solo paso
        _run_processing("gdal:warpreproject", {
            "INPUT": asc_path,
            "SOURCE_CRS": None,
            "TARGET_CRS": target_crs_authid,
            "RESAMPLING": 0,
            "NODATA": None,
            "TARGET_RESOLUTION": None,
            "OPTIONS": "COMPRESS=LZW",
            "DATA_TYPE": 5,
            "TARGET_EXTENT": None,
            "TARGET_EXTENT_CRS": None,
            "MULTITHREADING": False,
            "EXTRA": "",
            "OUTPUT": output_path,
        }, "No se pudo convertir/reproyectar el raster continuo")
    else:
        # Solo convertir formato .asc → .tif
        _run_processing("gdal:translate", {
            "INPUT": asc_path,
            "TARGET_CRS": None,
            "NODATA": None,
            "COPY_SUBDATASETS": False,
            "OPTIONS": "COMPRESS=LZW",
            "EXTRA": "",
            "DATA_TYPE": 5,
            "OUTPUT": output_path,
        }, "No se pudo convertir el raster continuo a GeoTIFF")

    return output_path


def reproject_vector(vector_path, output_path, target_crs_authid):
    """
    Reproyecta un vector al CRS indicado usando native:reprojectlayer.

    Devuelve `output_path` en caso de éxito.
    Si `target_crs_authid` está vacío o es None se devuelve `vector_path`
    sin reproyectar.
    """
    if not target_crs_authid:
        return vector_path
    output_path = safe_output_path(output_path)
    _run_processing("native:reprojectlayer", {
        "INPUT": vector_path,
        "TARGET_CRS": target_crs_authid,
        "CONVERT_CURVED_GEOMETRIES": False,
        "OUTPUT": output_path,
    }, f"No se pudo reproyectar el vector a {target_crs_authid}")
    return output_path


def add_description_field(vector_path):
    """
    Añade el campo 'Descripcion' (String, 60 chars) al vector y lo rellena
    con el nombre del hábitat según el valor del campo 'Clase', usando los
    labels definidos en catalog.HABITAT_CLASSES.

    Modifica el archivo en sitio (edita la capa directamente con
    QgsVectorDataProvider). Si el campo ya existe, solo actualiza los valores.

    Parámetros
    ----------
    vector_path : str
        Ruta al GeoPackage/Shapefile a modificar.

    Retorna
    -------
    str — la misma `vector_path` para encadenamiento.
    """
    # Mapa valor_clase → label
    label_map = {c["value"]: c["label"] for c in catalog.HABITAT_CLASSES}

    layer = QgsVectorLayer(vector_path, "tmp_desc", "ogr")
    if not layer.isValid():
        raise RasterOpsError(
            f"No se pudo abrir el vector para añadir campo Descripcion: {vector_path}"
        )

    provider = layer.dataProvider()
    caps = provider.capabilities()

    # ── Añadir campo si no existe ─────────────────────────────────────
    field_names = [f.name() for f in layer.fields()]
    if "Descripcion" not in field_names:
        if not (caps & QgsVectorDataProvider.Capability.AddAttributes):
            raise RasterOpsError(
                f"El proveedor de '{vector_path}' no admite añadir campos."
            )
        ok = provider.addAttributes([QgsField("Descripcion", QVariant.String, len=60)])
        if not ok:
            raise RasterOpsError(
                f"No se pudo añadir el campo 'Descripcion' a '{vector_path}'."
            )
        layer.updateFields()

    # ── Rellenar campo ────────────────────────────────────────────────
    desc_idx = layer.fields().indexOf("Descripcion")
    clase_idx = layer.fields().indexOf("Clase")
    if clase_idx < 0:
        raise RasterOpsError(
            f"El vector '{vector_path}' no tiene el campo 'Clase' esperado."
        )

    if not (caps & QgsVectorDataProvider.Capability.ChangeAttributeValues):
        raise RasterOpsError(
            f"El proveedor de '{vector_path}' no admite editar atributos."
        )

    attr_map = {}
    for feat in layer.getFeatures():
        clase_val = feat["Clase"]
        descripcion = label_map.get(clase_val, f"Clase {clase_val}")
        attr_map[feat.id()] = {desc_idx: descripcion}

    provider.changeAttributeValues(attr_map)
    return vector_path


def add_area_field(vector_path, field_name="area_ha"):
    """
    Calcula el área de cada feature del vector en hectáreas (ha) usando
    QgsDistanceArea (medición elipsoidal WGS84) y añade/actualiza el campo
    `field_name` (Double, 4 decimales).

    Modifica el archivo in situ con QgsVectorDataProvider.

    Parámetros
    ----------
    vector_path : str
        Ruta al GeoPackage/Shapefile.
    field_name : str
        Nombre del campo de área (por defecto 'area_ha').

    Retorna
    -------
    str — la misma `vector_path`.
    """
    layer = QgsVectorLayer(vector_path, "tmp_area_field", "ogr")
    if not layer.isValid():
        raise RasterOpsError(
            f"No se pudo abrir el vector para calcular campo {field_name}: {vector_path}"
        )

    provider = layer.dataProvider()
    caps = provider.capabilities()

    field_names = [f.name() for f in layer.fields()]
    if field_name not in field_names:
        if not (caps & QgsVectorDataProvider.Capability.AddAttributes):
            raise RasterOpsError(
                f"El proveedor de '{vector_path}' no admite añadir campos."
            )
        ok = provider.addAttributes([QgsField(field_name, QVariant.Double, prec=4)])
        if not ok:
            raise RasterOpsError(
                f"No se pudo añadir el campo '{field_name}' a '{vector_path}'."
            )
        layer.updateFields()

    area_idx = layer.fields().indexOf(field_name)
    if area_idx < 0:
        raise RasterOpsError(
            f"No se encontró el campo '{field_name}' en '{vector_path}'."
        )

    if not (caps & QgsVectorDataProvider.Capability.ChangeAttributeValues):
        raise RasterOpsError(
            f"El proveedor de '{vector_path}' no admite editar atributos."
        )

    d = QgsDistanceArea()
    d.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
    d.setEllipsoid("WGS84")

    attr_map = {}
    for feat in layer.getFeatures():
        geom = feat.geometry()
        if geom and not geom.isEmpty():
            area_m2 = d.measureArea(geom)
            area_ha = round(area_m2 / 10000.0, 4)
        else:
            area_ha = 0.0
        attr_map[feat.id()] = {area_idx: area_ha}

    provider.changeAttributeValues(attr_map)
    return vector_path

