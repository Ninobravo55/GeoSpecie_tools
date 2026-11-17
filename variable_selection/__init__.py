"""
variable_selection — Herramienta de Selección de Variables por colinealidad.

Reduce un conjunto de variables ambientales ASCII (.asc) a un subconjunto no
colineal, replicando el procedimiento de `virtualspecies::removeCollinearity()`
de R pero implementado íntegramente con las librerías que QGIS ya trae
(NumPy, GDAL y matplotlib), sin requerir una instalación de R.

Estructura:
    stats_core.py — correlaciones (pearson/spearman/kendall), clasificación
                    jerárquica complete-linkage y corte del dendrograma.
                    Solo NumPy: validado contra cor()/hclust()/cutree() de R.
    raster_io.py  — lectura de .asc, verificación de alineación, muestreo de
                    píxeles válidos y exportación a la carpeta Select_variable.
    charts.py     — dendrograma y mapa de calor (matplotlib).
    report.py     — reporte HTML.
    model.py      — orquestador del flujo completo.
    algorithm.py  — QgsProcessingAlgorithm.
    dialog.py     — diálogo interactivo Qt.

charts.py importa matplotlib a nivel de módulo, y algorithm.py/dialog.py
dependen de qgis.core y Qt. Igual que en random_forest/__init__.py,
maxent_analysis/__init__.py y species_download/__init__.py, ese acoplamiento
se aísla con try/except: si al usuario le falta matplotlib (o el paquete se
importa fuera de QGIS, p. ej. para correr tests con pytest), el resto del
plugin sigue cargando con normalidad en vez de que core/provider.py -- y por
lo tanto todo GeoSpecies Tools -- falle al iniciar QGIS.
"""

try:
    from .algorithm import VariableSelectionAlgorithm
    from .dialog import VariableSelectionDialog

    __all__ = ["VariableSelectionAlgorithm", "VariableSelectionDialog"]
except ImportError:
    __all__ = []
