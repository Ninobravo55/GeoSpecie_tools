import os
from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon
from .worldclim_bio import WorldClimBioAlgorithm
from .worldclim_climatology import WorldClimClimatologyAlgorithm
from .soilgrids_isric import SoilGridsIsricAlgorithm
from .topografia_terreno import TopografiaTerrenoAlgorithm
from .uniformizar_raster import UniformizarRasterAlgorithm
from .uniformizar_raster_cmip6_ascii import UniformizarRasterCmip6AsciiAlgorithm
from .uniformizar_raster_worldclim import UniformizarRasterWorldClimAlgorithm
from .limpieza_especies_maxent import LimpiezaEspeciesMaxEntAlgorithm


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


    def id(self):
        return 'geospecies'

    def name(self):
        return 'GeoSpecies Tools'

    def icon(self):
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'icono.png')
        return QIcon(icon_path)

    def svgIconPath(self):
        return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'icono.png')
