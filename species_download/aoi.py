"""
aoi.py — Construcción de geometría de Área de Interés a partir de una capa
de polígonos seleccionada en cmbAOI (QgsMapLayerComboBox).

No reutiliza utils/aoi_builder.py porque ese módulo está acoplado a la
firma de QgsProcessingAlgorithm (parameterAsLayer/parameterAsExtent).
Aquí el origen es directamente una QgsVectorLayer elegida en un widget
de un QDialog independiente, por lo que se resuelve con una función
propia y más simple, pensada solo para el caso "capa de polígonos".
"""

import json

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
)

CRS_4326 = QgsCoordinateReferenceSystem("EPSG:4326")

# Tope de vértices tolerado en el parámetro `geometry` (WKT) de GBIF.
# GBIF admite geometrías razonablemente complejas, pero conviene simplificar
# para no generar URLs enormes ni WKT que la API rechace.
MAX_WKT_VERTICES = 500


class AoiError(Exception):
    """Error al construir el AOI desde la capa de polígonos seleccionada."""


def layer_geometry_to_wkt_4326(layer, max_vertices=MAX_WKT_VERTICES):
    """
    Disuelve todas las features de una capa de polígonos en una sola
    geometría, la reproyecta a EPSG:4326 y la simplifica si excede
    `max_vertices`, devolviendo su representación WKT.

    Parámetros
    ----------
    layer : QgsVectorLayer
        Capa de polígonos seleccionada en cmbAOI.
    max_vertices : int
        Umbral de vértices por encima del cual se simplifica
        progresivamente antes de exportar a WKT.

    Retorna
    -------
    str
        WKT (Polygon o MultiPolygon) en EPSG:4326.

    Lanza
    -----
    AoiError
        Si la capa no tiene geometrías válidas.
    """
    if layer is None:
        raise AoiError("No se seleccionó ninguna capa de polígonos en 'Usar capa de polígonos'.")

    features = list(layer.getFeatures())
    if not features:
        raise AoiError("La capa de polígonos seleccionada no contiene entidades.")

    source_crs = layer.crs()
    needs_transform = source_crs.isValid() and source_crs.authid() != "EPSG:4326"
    xform = None
    if needs_transform:
        xform = QgsCoordinateTransform(source_crs, CRS_4326, QgsProject.instance().transformContext())

    geoms = []
    for feat in features:
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        if xform is not None:
            geom = QgsGeometry(geom)
            geom.transform(xform)
        geoms.append(geom)

    if not geoms:
        raise AoiError("La capa de polígonos no contiene geometrías válidas.")

    union_geom = geoms[0]
    for g in geoms[1:]:
        union_geom = union_geom.combine(g)

    union_geom = _simplify_to_vertex_budget(union_geom, max_vertices)

    wkt = union_geom.asWkt()
    if not wkt:
        raise AoiError("No se pudo generar el WKT del AOI a partir de la capa seleccionada.")
    return wkt


def _simplify_to_vertex_budget(geom, max_vertices):
    """
    Simplifica progresivamente `geom` hasta que su cantidad de vértices
    no supere `max_vertices`, o hasta agotar los intentos. Si la
    simplificación colapsa la geometría a vacío, se conserva el último
    resultado válido.
    """
    if _vertex_count(geom) <= max_vertices:
        return geom

    tolerance = 0.0001
    current = geom
    for _ in range(12):
        simplified = current.simplify(tolerance)
        if simplified is None or simplified.isEmpty():
            break
        current = simplified
        if _vertex_count(current) <= max_vertices:
            return current
        tolerance *= 2

    return current


def _vertex_count(geom):
    """Cuenta vértices totales de una geometría (soporta multi-partes)."""
    try:
        geo_dict = json.loads(geom.asJson())
    except (ValueError, TypeError):
        return 0

    def _count_coords(coords):
        if not coords:
            return 0
        if isinstance(coords[0], (int, float)):
            return 1
        total = 0
        for c in coords:
            total += _count_coords(c)
        return total

    gtype = geo_dict.get("type")
    if gtype == "GeometryCollection":
        return sum(_count_coords(sub.get("coordinates", [])) for sub in geo_dict.get("geometries", []))
    return _count_coords(geo_dict.get("coordinates", []))
