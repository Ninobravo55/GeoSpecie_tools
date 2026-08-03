import os
from qgis.core import QgsProcessingProvider, QgsMessageLog, Qgis
from qgis.PyQt.QtGui import QIcon
from .worldclim_bio import WorldClimBioAlgorithm
from .worldclim_climatology import WorldClimClimatologyAlgorithm
from .soilgrids_isric import SoilGridsIsricAlgorithm
from .topografia_terreno import TopografiaTerrenoAlgorithm
from .uniformizar_raster import UniformizarRasterAlgorithm
from .uniformizar_raster_cmip6_ascii import UniformizarRasterCmip6AsciiAlgorithm
from .uniformizar_raster_worldclim import UniformizarRasterWorldClimAlgorithm
from .limpieza_especies_maxent import LimpiezaEspeciesMaxEntAlgorithm

# RandomForestAlgorithm (vía random_forest/algorithm.py -> charts.py) importa
# matplotlib a nivel de módulo. Si al usuario le falta esa dependencia, antes
# esto hacía fallar la importación de TODO core/provider.py -- y por lo tanto
# TODO GeoSpecies Tools (WorldClim, SoilGrids, CMIP6, limpieza de especies,
# etc.) dejaba de cargar en QGIS, no solo la herramienta de Random Forest.
# Se aísla igual que ya se hacía en maxent_analysis/__init__.py y
# species_download/__init__.py.
try:
    from ..random_forest import RandomForestAlgorithm
except ImportError as e:
    RandomForestAlgorithm = None
    _RANDOM_FOREST_IMPORT_ERROR = str(e)


class GeoSpeciesProvider(QgsProcessingProvider):
    def __init__(self):
        super().__init__()

    def loadAlgorithms(self):
        self.addAlgorithm(WorldClimBioAlgorithm())
        self.addAlgorithm(WorldClimClimatologyAlgorithm())
        self.addAlgorithm(SoilGridsIsricAlgorithm())
        self.addAlgorithm(TopografiaTerrenoAlgorithm())
        self.addAlgorithm(UniformizarRasterAlgorithm())
        self.addAlgorithm(UniformizarRasterCmip6AsciiAlgorithm())
        self.addAlgorithm(UniformizarRasterWorldClimAlgorithm())
        self.addAlgorithm(LimpiezaEspeciesMaxEntAlgorithm())
        if RandomForestAlgorithm is not None:
            self.addAlgorithm(RandomForestAlgorithm())
        else:
            QgsMessageLog.logMessage(
                f"Random Forest no está disponible (falta una dependencia: "
                f"{_RANDOM_FOREST_IMPORT_ERROR}). Instale matplotlib/scikit-learn "
                f"desde 'Instalación dependencias GEE' para habilitarlo. El resto "
                f"de herramientas de GeoSpecies Tools no se ven afectadas.",
                'GeoSpecies Tools', Qgis.MessageLevel.Warning
            )


    def id(self):
        return 'geospecies'

    def name(self):
        return 'GeoSpecies Tools'

    def icon(self):
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'icono.png')
        return QIcon(icon_path)

    def svgIconPath(self):
        return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'icono.png')
