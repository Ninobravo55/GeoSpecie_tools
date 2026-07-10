"""
maxent_analysis — Herramienta "Analizar Resultados MaxEnt".

catalog.py, reader.py y file_finder.py son 100% puro Python (pandas,
bs4, os/glob -- sin qgis) y se pueden importar/testear sin QGIS
instalado. raster_ops.py, charts.py (matplotlib es pesado pero puro),
batch.py (según lo que orqueste), worker.py y el diálogo sí requieren
qgis.core/qgis.gui/processing.
"""

try:
    from . import batch  # noqa: F401 - importa raster_ops (qgis.core/processing) transitivamente
except ImportError:
    # Permite seguir importando catalog.py/reader.py/file_finder.py
    # (100% puro Python) fuera de un entorno QGIS, ej. para correr los
    # tests con pytest sin QGIS instalado -- mismo criterio que
    # climate_tiles/__init__.py y species_download/__init__.py.
    pass
