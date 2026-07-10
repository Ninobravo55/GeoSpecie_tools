"""
climate_tiles — Herramienta "WorldClim 2.1 Histórico y CMIP6" (WorldClim
histórico / CMIP6).

Uso desde la GUI: climate_tiles_dialog.ClimateTilesDialog (ver
plugin.py -> run_climate_tiles_download).

tiles.py, catalog.py y geodata_api.py son 100% puro Python (sin qgis) y
se pueden importar/testear sin QGIS instalado. geom.py, clip.py,
worker.py y climate_tiles_dialog.py sí requieren qgis.core/qgis.gui/
processing.
"""

from . import batch  # noqa: F401 - reexportado; batch.py es puro Python (no importa qgis a nivel de módulo)
