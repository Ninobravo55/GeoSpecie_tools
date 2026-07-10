"""
geom.py — Centroide y bbox de un QgsVectorLayer (punto o polígono),
para decidir qué tile descargar. A diferencia de tiles.py/catalog.py/
geodata_api.py, este módulo SÍ depende de qgis.core (necesita
reproyectar y disolver geometrías reales).
"""

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
    QgsWkbTypes,
)


class GeomError(Exception):
    """Error al extraer geometría/centroide de la capa de AOI."""


def centroid_and_bbox_from_layer(layer):
    """
    Disuelve todas las features de `layer` en una sola geometría (en
    EPSG:4326) y devuelve su centroide y bounding box.

    Parámetros
    ----------
    layer : QgsVectorLayer
        Capa de punto o polígono seleccionada como AOI.

    Retorna
    -------
    tuple(float, float, tuple|None)
        (lon, lat, bbox). bbox es (xmin,ymin,xmax,ymax) si `layer` es
        de polígonos, o None si es de puntos (un punto no "abarca"
        nada -- no aplica la advertencia de multi-tile).

    Lanza
    -----
    GeomError
        Si la capa no tiene entidades o geometrías válidas.
    """
    if layer is None:
        raise GeomError("No se seleccionó ninguna capa de AOI (punto o polígono).")

    features = list(layer.getFeatures())
    if not features:
        raise GeomError("La capa de AOI no contiene entidades.")

    source_crs = layer.crs()
    target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    xform = None
    if source_crs.isValid() and source_crs.authid() != "EPSG:4326":
        xform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance().transformContext())

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
        raise GeomError("La capa de AOI no contiene geometrías válidas.")

    union_geom = geoms[0]
    for g in geoms[1:]:
        union_geom = union_geom.combine(g)

    centroid = union_geom.centroid()
    point = centroid.asPoint()

    is_point_geom = union_geom.type() == QgsWkbTypes.PointGeometry
    if is_point_geom:
        bbox = None
    else:
        rect = union_geom.boundingBox()
        bbox = (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())

    return point.x(), point.y(), bbox
