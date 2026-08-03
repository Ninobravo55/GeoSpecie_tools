"""
species_download — Herramienta "Descargar Registros GBIF".

Uso desde la GUI: species_download_dialog.SpeciesDownloadDialog (ver
plugin.py -> run_gbif_download).

Uso desde la consola de Python de QGIS / scripts / batch (sin ningún
QWidget ni QThread):

    >>> from GeoSpecies_Tools.species_download import batch
    >>> batch.download_species_records(
    ...     "Puma concolor", "/tmp/puma_peru.gpkg",
    ...     country_code="PE", only_living=True, status_cb=print,
    ... )

Ver batch.py para download_species_records() y download_multiple_species().
"""

try:
    from . import batch  # noqa: F401 - reexportado para uso directo: species_download.batch.*
except ImportError:
    # batch.py importa qgis.core (vía exporter.py/aoi.py) porque de verdad
    # lo necesita para escribir capas vectoriales -- no es un acoplamiento
    # gratuito. Pero eso significaba que ni siquiera gbif_api.py o
    # filters.py (100% puro Python, sin qgis) podían importarse fuera de
    # QGIS, porque importar CUALQUIER submódulo ejecuta primero este
    # __init__.py. Este try/except aísla ese acoplamiento legítimo para
    # que no bloquee los módulos que sí son puros (relevante para poder
    # correr tests/ con pytest fuera de QGIS).
    batch = None  # noqa: F401
