"""
AOI builder — construcción centralizada del área de interés.

Reemplaza ~50 líneas duplicadas en 13+ algoritmos y corrige el bug
B-01 (GeometryCollection truncado).

API:
    build_aoi(parameters, context, feedback,
              layer_param='INPUT_LAYER', extent_param='INPUT_EXTENT')
        -> AOIResult

    to_ee_geometry(qgs_geom_4326, ee_module)
        -> ee.Geometry (Polygon o MultiPolygon — nunca trunca)

AOIResult expone:
    geom_4326     QgsGeometry unificada en EPSG:4326
    geo_dict      dict GeoJSON correspondiente
    source_crs    QgsCoordinateReferenceSystem original del input
    aoi_layer     QgsVectorLayer original (capa) o memoria (extent)
                  útil para clips locales tras descarga
    is_from_layer bool — True si vino de capa, False si vino de extent
"""

import json
import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsProcessingException,
    QgsProject,
    QgsVectorLayer,
)


CRS_4326 = QgsCoordinateReferenceSystem('EPSG:4326')


class AOIResult:
    """Contenedor inmutable con todos los productos derivados del AOI."""

    __slots__ = (
        'geom_4326', 'geo_dict', 'source_crs', 'aoi_layer', 'is_from_layer'
    )

    def __init__(self, geom_4326, geo_dict, source_crs, aoi_layer, is_from_layer):
        self.geom_4326 = geom_4326
        self.geo_dict = geo_dict
        self.source_crs = source_crs
        self.aoi_layer = aoi_layer
        self.is_from_layer = is_from_layer

    @property
    def source_crs_authid(self):
        """Retorna el authid del CRS (ej: 'EPSG:32718')."""
        return self.source_crs.authid() if self.source_crs.isValid() else 'EPSG:4326'

    def to_ee_geometry(self, ee_module):
        """Atajo: convierte la geometría a ee.Geometry sin truncar."""
        return to_ee_geometry(self.geom_4326, ee_module, self.geo_dict)

    def bbox_degrees(self):
        """Retorna (xmin, ymin, xmax, ymax) del bounding box en grados (4326)."""
        bb = self.geom_4326.boundingBox()
        return bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum()

    def bbox_size_m(self):
        """Retorna (ancho_m, alto_m) aproximados del bounding box.

        Usa la fórmula ecuatorial ajustada por coseno de la latitud media.
        Suficiente para decidir estrategia de grilla — no es geodésico exacto.
        """
        xmin, ymin, xmax, ymax = self.bbox_degrees()
        lat_mid = (ymin + ymax) / 2.0
        m_per_deg_x = 111_320.0 * math.cos(math.radians(lat_mid))
        m_per_deg_y = 111_320.0
        return (xmax - xmin) * m_per_deg_x, (ymax - ymin) * m_per_deg_y

    def estimate_pixels(self, scale_m=30):
        """Estima la cantidad de píxeles del bounding box a la escala dada.

        Permite decidir antes de hacer requests a GEE si el área necesita
        descarga por grilla o si cabe en un solo getDownloadURL.

        Parámetros
        ----------
        scale_m : int | float
            Resolución espacial del producto en metros (ej: 10, 30, 100).

        Retorna
        -------
        int
            Píxeles estimados (width_px * height_px).
        """
        w_m, h_m = self.bbox_size_m()
        w_px = max(1, int(w_m / scale_m))
        h_px = max(1, int(h_m / scale_m))
        return w_px * h_px


def build_aoi(algorithm, parameters, context, feedback,
              layer_param='INPUT_LAYER', extent_param='INPUT_EXTENT'):
    """
    Construye el AOI a partir de los parámetros de un QgsProcessingAlgorithm.

    Soporta dos modos de entrada (mutuamente excluyentes, prioridad a capa):

    Opción 1 — Capa Vectorial:
        Reproyecta cada feature al vuelo desde su CRS original a EPSG:4326
        y une todas las geometrías antes de enviar a GEE. Preserva el CRS
        original en AOIResult.source_crs para recortes locales posteriores.

    Opción 2 — Recuadro del Canvas (Extensión):
        Confía en el CRS que QGIS reporta vía parameterAsExtentCrs() —
        el CRS activo del canvas cuando el usuario dibujó el recuadro.
        Solo hace fallback al CRS del proyecto si ese valor no es válido.
        Luego reproyecta el rectángulo a EPSG:4326 antes de enviar a GEE.

    Lanza QgsProcessingException si ambos parámetros están vacíos.

    Estrategia: usa QgsCoordinateTransform directamente (sin invocar
    processing.run("native:reprojectlayer")). Resultado equivalente, más
    rápido y sin dependencia de Processing API.
    """
    input_layer = algorithm.parameterAsLayer(parameters, layer_param, context)
    extent = algorithm.parameterAsExtent(parameters, extent_param, context)

    if input_layer is not None:
        return _build_from_layer(input_layer, feedback)

    if extent is not None and not extent.isNull() and not extent.isEmpty():
        # Usar siempre el CRS que QGIS reporta para el extent del canvas.
        # parameterAsExtentCrs() devuelve el CRS activo del canvas al momento
        # de dibujar el recuadro — es la fuente de verdad correcta.
        # Solo hacemos fallback al CRS del proyecto si ese valor no es válido.
        source_crs = algorithm.parameterAsExtentCrs(parameters, extent_param, context)

        if not source_crs.isValid():
            proj_crs = QgsProject.instance().crs()
            source_crs = proj_crs if proj_crs.isValid() else QgsCoordinateReferenceSystem('EPSG:4326')
            if feedback is not None:
                feedback.pushInfo(
                    f"AOI [Opción 2 - Canvas]: CRS del extent no válido. "
                    f"Usando CRS del proyecto: {source_crs.authid()}."
                )
        else:
            if feedback is not None:
                feedback.pushInfo(
                    f"AOI [Opción 2 - Canvas]: CRS del canvas = {source_crs.authid()} "
                    f"| extensión ({extent.xMinimum():.4f}, {extent.yMinimum():.4f}, "
                    f"{extent.xMaximum():.4f}, {extent.yMaximum():.4f})."
                )
        return _build_from_extent(extent, source_crs, feedback)

    raise QgsProcessingException(
        "Debes proporcionar una Capa Vectorial (Opción 1) o dibujar un Recuadro "
        "en el Canvas (Opción 2) como Área de Interés."
    )


def _build_from_layer(input_layer, feedback):
    if feedback is not None:
        feedback.pushInfo(
            f"AOI [Opción 1 - Capa Vectorial]: CRS origen = {input_layer.crs().authid()} "
            f"| reproyectando a EPSG:4326 para GEE..."
        )

    source_crs = input_layer.crs()
    transform_ctx = QgsProject.instance().transformContext()
    needs_transform = source_crs.isValid() and source_crs.authid() != 'EPSG:4326'

    if needs_transform:
        xform = QgsCoordinateTransform(source_crs, CRS_4326, transform_ctx)
    else:
        xform = None

    features = list(input_layer.getFeatures())
    if not features:
        raise QgsProcessingException("La capa seleccionada no tiene geometrías.")

    geoms_4326 = []
    for feat in features:
        g = feat.geometry()
        if g is None or g.isEmpty():
            continue
        if xform is not None:
            g = QgsGeometry(g)  # copia para no mutar la fuente
            g.transform(xform)
        geoms_4326.append(g)

    if not geoms_4326:
        raise QgsProcessingException("La capa no contiene geometrías válidas.")

    geom_union = geoms_4326[0]
    for g in geoms_4326[1:]:
        geom_union = geom_union.combine(g)

    geo_dict = json.loads(geom_union.asJson())

    return AOIResult(
        geom_4326=geom_union,
        geo_dict=geo_dict,
        source_crs=source_crs,
        aoi_layer=input_layer,
        is_from_layer=True,
    )


def _build_from_extent(extent, source_crs, feedback):
    if feedback is not None:
        if source_crs.isValid() and source_crs.authid() != 'EPSG:4326':
            feedback.pushInfo(
                f"AOI [Opción 2 - Canvas]: reproyectando recuadro de "
                f"{source_crs.authid()} → EPSG:4326..."
            )
        else:
            feedback.pushInfo(
                "AOI [Opción 2 - Canvas]: recuadro ya en EPSG:4326. Sin reproyección."
            )

    geom = QgsGeometry.fromRect(extent)

    # La detección del CRS se realiza ahora al construir el AOI (en build_aoi)

    if source_crs.isValid() and source_crs.authid() != 'EPSG:4326':
        transform_ctx = QgsProject.instance().transformContext()
        xform = QgsCoordinateTransform(source_crs, CRS_4326, transform_ctx)
        geom = QgsGeometry(geom)
        geom.transform(xform)

    # Capa de memoria en EPSG:4326 para usos posteriores (clips locales, etc.).
    mem_layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "aoi_temp", "memory")
    feat = QgsFeature()
    feat.setGeometry(geom)
    mem_layer.dataProvider().addFeature(feat)
    mem_layer.updateExtents()

    geo_dict = json.loads(geom.asJson())

    return AOIResult(
        geom_4326=geom,
        geo_dict=geo_dict,
        source_crs=source_crs if source_crs.isValid() else CRS_4326,
        aoi_layer=mem_layer,
        is_from_layer=False,
    )


# ---------------------------------------------------------------------------
# Conversión a ee.Geometry (BUG B-01 FIX)
# ---------------------------------------------------------------------------

def to_ee_geometry(qgs_geom_4326, ee_module, geo_dict=None):
    """
    Convierte una QgsGeometry (en EPSG:4326) a ee.Geometry sin perder partes.

    El bug original: cuando el GeoJSON resultaba en 'GeometryCollection'
    (caso común con multipolígonos QGIS), el código tomaba sólo
    geometries[0] y descartaba silenciosamente el resto del AOI.

    Esta función:
      - Polygon  → ee.Geometry.Polygon
      - MultiPolygon → ee.Geometry.MultiPolygon
      - GeometryCollection → MultiPolygon agregando todos los polígonos
        contenidos (descarta Points/Lines, que no aplican como AOI).
    """
    if geo_dict is None:
        geo_dict = json.loads(qgs_geom_4326.asJson())

    gtype = geo_dict.get('type')

    if gtype == 'Polygon':
        return ee_module.Geometry.Polygon(
            coords=geo_dict['coordinates'],
            proj='EPSG:4326',
            geodesic=False,
            evenOdd=True,
        )

    if gtype == 'MultiPolygon':
        return ee_module.Geometry.MultiPolygon(
            coords=geo_dict['coordinates'],
            proj='EPSG:4326',
            geodesic=False,
            evenOdd=True,
        )

    if gtype == 'GeometryCollection':
        poly_coords = []
        for sub in geo_dict.get('geometries', []):
            sub_type = sub.get('type')
            if sub_type == 'Polygon':
                poly_coords.append(sub['coordinates'])
            elif sub_type == 'MultiPolygon':
                poly_coords.extend(sub['coordinates'])
            # se descartan Point/LineString/etc. — no son válidos como AOI poligonal
        if not poly_coords:
            raise QgsProcessingException(
                "El AOI no contiene polígonos válidos tras la conversión. "
                "Revisa que la capa de entrada sea poligonal."
            )
        return ee_module.Geometry.MultiPolygon(
            coords=poly_coords,
            proj='EPSG:4326',
            geodesic=False,
            evenOdd=True,
        )

    # Fallback: tipo no esperado, lo pasamos crudo a ee.Geometry (puede
    # fallar, pero al menos el usuario verá un error explícito de GEE).
    return ee_module.Geometry(geo_dict)


# ---------------------------------------------------------------------------
# Simplificación segura del AOI (para reducir tamaño de payload a GEE)
# ---------------------------------------------------------------------------

def safe_simplify(geom_4326, tolerance_deg=0.0001, min_area_deg2=1e-8):
    """
    Simplifica la geometría en grados decimales sólo si el área supera
    cierto umbral. Evita el caso del AOI <100ha que el simplify(0.0001)
    aplastaba a vacío.

    Retorna QgsGeometry. Si la simplificación da resultado vacío,
    devuelve la geometría original sin modificar.
    """
    if geom_4326 is None or geom_4326.isEmpty():
        return geom_4326

    # area en grados^2 — barato porque la geom ya está en 4326
    area_deg2 = geom_4326.area()
    if area_deg2 < min_area_deg2:
        return geom_4326  # AOI muy pequeño, no simplificar

    simplified = geom_4326.simplify(tolerance_deg)
    if simplified is None or simplified.isEmpty():
        return geom_4326
    return simplified
