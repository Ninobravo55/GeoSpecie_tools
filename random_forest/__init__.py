"""
random_forest — Módulo para Modelamiento de Nicho / Distribución de Especies
usando algoritmos de Machine Learning (Random Forest).

model.py y report.py son prácticamente puro Python/numpy (con un import
diferido de sklearn dentro de la función de entrenamiento, con fallback si
no está instalado). charts.py importa matplotlib a nivel de módulo, y
algorithm.py importa QgsProcessingAlgorithm y compañía desde qgis.core.
Igual que en maxent_analysis/__init__.py y species_download/__init__.py,
se aísla ese acoplamiento con try/except: así, si al usuario le falta
matplotlib (o el paquete se importa fuera de un entorno QGIS, p. ej. para
correr tests/ con pytest), el resto del plugin (WorldClim, SoilGrids,
GBIF, CMIP6, etc.) sigue cargando con normalidad en vez de que
core/provider.py -- y por lo tanto todo GeoSpecies Tools -- falle al
iniciar QGIS.
"""

try:
    from .algorithm import RandomForestAlgorithm
    from .dialog import RandomForestDialog
    __all__ = ["RandomForestAlgorithm", "RandomForestDialog"]
except ImportError:
    __all__ = []
