"""
qgis_loader.py — Carga de capas de resultados MaxEnt en QGIS con simbología.

Separado de batch.py porque la carga en QGIS (QgsProject, QgsMapLayer) solo
tiene sentido cuando la herramienta se ejecuta desde la GUI de QGIS, no en
contextos de procesamiento por lotes puro o pruebas unitarias.

Simbología aplicada:
  - Raster Habitat_Potencial_Continuo : rampa de color (verde–amarillo–rojo)
  - Raster Habitat_Potencial          : pseudocolor por clase (4 colores fijos)
  - Raster Modelo_Binario             : bicolor (ausencia=gris, presencia=azul)
  - Vector Habitat_Potencial          : categorizado por campo Clase
  - Vector Distribucion_Potencial     : color único
  - Vector Habitat_Potencial_Disuelto : categorizado por campo Clase
"""

import os

from qgis.core import (
    QgsColorRampShader,
    QgsFillSymbol,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsLayerTreeNode,
    QgsPalettedRasterRenderer,
    QgsProject,
    QgsRasterBandStats,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsVectorLayer,
    QgsMessageLog,
    Qgis,
)
from qgis.PyQt.QtGui import QColor

from . import catalog


# ---------------------------------------------------------------------------
# Simbología raster
# ---------------------------------------------------------------------------

def _make_continuous_renderer(layer):
    """
    Rampa SingleBandPseudoColor para el raster continuo (0–1).
    Colores: blanco (0) → verde → amarillo → rojo (1), igual que el
    output por defecto de MaxEnt.
    """
    provider = layer.dataProvider()
    stats = provider.bandStatistics(1, QgsRasterBandStats.Stats.All)
    min_val = max(stats.minimumValue, 0.0)
    max_val = min(stats.maximumValue, 1.0)

    color_ramp_items = [
        QgsColorRampShader.ColorRampItem(min_val, QColor("#ffffff"), "Mín"),
        QgsColorRampShader.ColorRampItem(0.2,     QColor("#b6b2ab"), "0.2"),
        QgsColorRampShader.ColorRampItem(0.4,     QColor("#33b63e"), "0.4"),
        QgsColorRampShader.ColorRampItem(0.6,     QColor("#e0ee1a"), "0.6"),
        QgsColorRampShader.ColorRampItem(max_val, QColor("#ee0004"), "Máx"),
    ]

    ramp = QgsColorRampShader()
    ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
    ramp.setColorRampItemList(color_ramp_items)

    shader = QgsRasterShader()
    shader.setRasterShaderFunction(ramp)

    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    renderer.setClassificationMin(min_val)
    renderer.setClassificationMax(max_val)
    return renderer


def _make_classified_renderer(layer):
    """
    PalettedRaster para el raster clasificado en 4 clases.
    Usa los colores y etiquetas de catalog.HABITAT_CLASSES.
    """
    classes = [
        QgsPalettedRasterRenderer.Class(
            c["value"],
            QColor(c["color"]),
            c["label"],
        )
        for c in catalog.HABITAT_CLASSES
    ]
    renderer = QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes)
    return renderer


def _make_binary_renderer(layer):
    """
    PalettedRaster bicolor: 0 = sin distribución (gris semitransparente),
    1 = distribución potencial (azul oscuro).
    """
    classes = [
        QgsPalettedRasterRenderer.Class(0, QColor(180, 180, 180, 80), "Sin distribución"),
        QgsPalettedRasterRenderer.Class(1, QColor("#1a6fa6"),           "Distribución potencial"),
    ]
    renderer = QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes)
    return renderer


def apply_raster_continuous_symbology(layer):
    """Aplica simbología de rampa al raster continuo."""
    layer.setRenderer(_make_continuous_renderer(layer))
    layer.triggerRepaint()


def apply_raster_classified_symbology(layer):
    """Aplica simbología de clases al raster clasificado de hábitat."""
    layer.setRenderer(_make_classified_renderer(layer))
    layer.triggerRepaint()


def apply_raster_binary_symbology(layer):
    """Aplica simbología bicolor al raster binario."""
    layer.setRenderer(_make_binary_renderer(layer))
    layer.triggerRepaint()


# ---------------------------------------------------------------------------
# Simbología vectorial
# ---------------------------------------------------------------------------

def _habitat_color_map():
    """Devuelve {valor_clase: QColor} desde HABITAT_CLASSES."""
    return {c["value"]: QColor(c["color"]) for c in catalog.HABITAT_CLASSES}


def apply_vector_habitat_symbology(layer):
    """
    Simbología categorizada por campo 'Clase' en el vector de hábitat.
    Usa los mismos colores de catalog.HABITAT_CLASSES.
    """
    from qgis.core import (
        QgsCategorizedSymbolRenderer,
        QgsRendererCategory,
        QgsFillSymbol,
    )
    color_map = _habitat_color_map()
    label_map = {c["value"]: c["label"] for c in catalog.HABITAT_CLASSES}

    categories = []
    for cls in catalog.HABITAT_CLASSES:
        val = cls["value"]
        color = color_map.get(val, QColor("#cccccc"))
        symbol = QgsFillSymbol.createSimple({
            "color": color.name(),
            "outline_color": "#555555",
            "outline_width": "0.1",
        })
        cat = QgsRendererCategory(val, symbol, label_map.get(val, str(val)))
        categories.append(cat)

    renderer = QgsCategorizedSymbolRenderer("Clase", categories)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def apply_vector_binary_symbology(layer):
    """Simbología simple azul oscuro para el vector de distribución potencial."""
    from qgis.core import QgsSingleSymbolRenderer
    symbol = QgsFillSymbol.createSimple({
        "color": "#1a6fa6",
        "color_border": "#0d3f5f",
        "width_border": "0.2",
    })
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()


# ---------------------------------------------------------------------------
# Carga de capas en QGIS
# ---------------------------------------------------------------------------

def _load_raster(path, name, renderer_fn=None):
    """Carga un raster en QGIS y aplica simbología opcional."""
    if not path or not os.path.exists(path):
        return None
    layer = QgsRasterLayer(path, name)
    if not layer.isValid():
        return None
    if renderer_fn:
        try:
            renderer_fn(layer)
        except Exception as exc:
            QgsMessageLog.logMessage(
                f"No se pudo aplicar simbología al raster {name}: {exc}",
                "GeoSpecies Tools",
                Qgis.MessageLevel.Warning
            )
    return layer


def _load_vector(path, name, renderer_fn=None):
    """Carga un vector en QGIS y aplica simbología opcional."""
    if not path or not os.path.exists(path):
        return None
    layer = QgsVectorLayer(path, name, "ogr")
    if not layer.isValid():
        return None
    if renderer_fn:
        try:
            renderer_fn(layer)
        except Exception as exc:
            QgsMessageLog.logMessage(
                f"No se pudo aplicar simbología al vector {name}: {exc}",
                "GeoSpecies Tools",
                Qgis.MessageLevel.Warning
            )
    return layer


def load_species_results_to_qgis(species_base, dirs, iface=None):
    """
    Carga en el proyecto QGIS todas las capas generadas para una especie,
    con su simbología correspondiente, organizadas en un grupo de capas.

    Parámetros
    ----------
    species_base : str
        Nombre base de la especie (para nombrar el grupo y las capas).
    dirs : dict
        Diccionario con rutas de las carpetas de salida por categoría
        (resultado de batch._species_output_dirs).
    iface : QgisInterface | None
        Interfaz de QGIS para refrescar el canvas (opcional).

    Retorna
    -------
    list[QgsMapLayer] — capas cargadas con éxito.
    """
    project = QgsProject.instance()
    root = project.layerTreeRoot()

    # ── Grupo de capas por especie ──────────────────────────────────────
    group = root.insertGroup(0, species_base)

    layers_to_add = []
    loaded = []

    # ── Rasters ────────────────────────────────────────────────────────
    raster_defs = [
        (
            os.path.join(dirs["raster"], "Habitat_Potencial_Continuo.tif"),
            f"Hábitat Continuo — {species_base}",
            apply_raster_continuous_symbology,
        ),
        (
            os.path.join(dirs["raster"], "Habitat_Potencial.tif"),
            f"Hábitat Clasificado — {species_base}",
            apply_raster_classified_symbology,
        ),
        (
            os.path.join(dirs["raster"], "Modelo_Binario.tif"),
            f"Distribución Binaria — {species_base}",
            apply_raster_binary_symbology,
        ),
    ]
    for path, name, renderer_fn in raster_defs:
        layer = _load_raster(path, name, renderer_fn)
        if layer:
            layers_to_add.append(layer)

    # ── Vectores ────────────────────────────────────────────────────────
    vector_defs = [
        (
            os.path.join(dirs["vector"], "Habitat_Potencial_Disuelto.gpkg"),
            f"Hábitat Disuelto — {species_base}",
            apply_vector_habitat_symbology,
        ),
        (
            os.path.join(dirs["vector"], "Habitat_Potencial.gpkg"),
            f"Hábitat Potencial — {species_base}",
            apply_vector_habitat_symbology,
        ),
        (
            os.path.join(dirs["vector"], "Distribucion_Potencial.gpkg"),
            f"Distribución Potencial — {species_base}",
            apply_vector_binary_symbology,
        ),
    ]
    for path, name, renderer_fn in vector_defs:
        layer = _load_vector(path, name, renderer_fn)
        if layer:
            layers_to_add.append(layer)

    # ── Agregar al proyecto y al grupo ──────────────────────────────────
    for layer in layers_to_add:
        project.addMapLayer(layer, addToLegend=False)
        group.addLayer(layer)
        loaded.append(layer)

    # ── Refrescar canvas ────────────────────────────────────────────────
    if iface:
        iface.mapCanvas().refresh()

    return loaded
