import os
import urllib.request
import urllib.error
import processing

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterMapLayer,
    QgsProcessingParameterExtent,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFolderDestination,
    QgsProcessingException,
    QgsProject,
    QgsRasterLayer,
)
from qgis.PyQt.QtCore import QCoreApplication

from ..utils.aoi_builder import build_aoi, to_ee_geometry, safe_simplify
from ..utils.gee_init import ensure_gee_initialized


GEE_DEMS = [
    {
        'label': 'SRTM GL1 — 30 m global (NASA/USGS)',
        'collection': 'USGS/SRTMGL1_003',
        'band': 'elevation',
        'scale': 30,
        'tipo': 'Image',
    },
    {
        'label': 'Copernicus DEM GLO-30 — 30 m global',
        'collection': 'COPERNICUS/DEM/GLO30',
        'band': 'DEM',
        'scale': 30,
        'tipo': 'ImageCollection',
    },
    {
        'label': 'NASADEM — 30 m global (NASA)',
        'collection': 'NASA/NASADEM_HGT/001',
        'band': 'elevation',
        'scale': 30,
        'tipo': 'Image',
    },
    {
        'label': 'ALOS AW3D30 — 30 m global (JAXA)',
        'collection': 'JAXA/ALOS/AW3D30/V3_2',
        'band': 'DSM',
        'scale': 30,
        'tipo': 'ImageCollection',
    },
    {
        'label': 'MERIT DEM — 90 m global (corregido)',
        'collection': 'MERIT/DEM/v1_0_3',
        'band': 'dem',
        'scale': 90,
        'tipo': 'Image',
    },
    {
        'label': 'SRTM 90 m (CGIAR-CSI v4)',
        'collection': 'CGIAR/SRTM90_V4',
        'band': 'elevation',
        'scale': 90,
        'tipo': 'Image',
    },
    {
        'label': 'HydroSHEDS — 90 m void-filled (WWF)',
        'collection': 'WWF/HydroSHEDS/03VFDEM',
        'band': 'b1',
        'scale': 90,
        'tipo': 'Image',
    },
    {
        'label': 'ASTER GDEM v3 — 30 m global (NASA/METI)',
        'collection': 'NASA/ASTER_GED/AG100_003',
        'band': 'elevation',
        'scale': 30,
        'tipo': 'Image',
    },
]


class TopografiaTerrenoAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER    = 'INPUT_LAYER'
    INPUT_EXTENT   = 'INPUT_EXTENT'
    DEM_SOURCE     = 'DEM_SOURCE'
    EPSG           = 'EPSG'
    OUT_FOLDER     = 'OUT_FOLDER'

    def __init__(self):
        super().__init__()
        self.dem_labels = [d['label'] for d in GEE_DEMS]

    # ------------------------------------------------------------------
    def tr(self, string):
        return QCoreApplication.translate('TopografiaTerrenoAlgorithm', string)

    def createInstance(self):
        return TopografiaTerrenoAlgorithm()

    def name(self):
        return 'topografia_terreno'

    def displayName(self):
        return self.tr('Topografía terreno')

    def group(self):
        return self.tr('Variables')

    def groupId(self):
        return 'variables'

    def shortHelpString(self):
        return self.tr(
            "<b>Descripción:</b><br>"
            "Descarga un Modelo Digital de Elevación (MDE) desde Google Earth Engine "
            "y calcula variables topográficas usando herramientas nativas de QGIS/GDAL/GRASS.<br><br>"
            "<b>Variables generadas:</b><br>"
            "• Pendiente (Slope_porcentaje)<br>"
            "• Aspecto (Aspect)<br>"
            "• Índice de Posición Topográfica (TPI)<br>"
            "• Índice de Rugosidad del Terreno (TRI)<br>"
            "• Dirección de Flujo (FlowDirection) con GRASS (r.watershed)<br><br>"
            "<b>Opciones de AOI:</b><br>"
            "• <b>Opción 1</b>: Selecciona una capa vectorial cargada en QGIS.<br>"
            "• <b>Opción 2</b>: Dibuja un recuadro directamente en el canvas.<br>"
        )

    # ------------------------------------------------------------------
    def initAlgorithm(self, config=None):
        # ── Área de Interés ───────────────────────────────────────────
        self.addParameter(
            QgsProcessingParameterMapLayer(
                self.INPUT_LAYER,
                self.tr('Opción 1: Seleccionar Capa Vectorial (se recorta según su proyección)'),
                [QgsProcessing.SourceType.TypeVectorPolygon],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterExtent(
                self.INPUT_EXTENT,
                self.tr('Opción 2: Dibujar Recuadro en el Canvas (se recorta según proyección del proyecto)'),
                optional=True,
            )
        )

        # ── Fuente DEM ────────────────────────────────────────────────
        self.addParameter(
            QgsProcessingParameterEnum(
                self.DEM_SOURCE,
                self.tr('Fuente del MDE (DEM)'),
                options=self.dem_labels,
                defaultValue=0,
            )
        )

        # ── Opciones de salida ────────────────────────────────────────
        self.addParameter(
            QgsProcessingParameterCrs(
                self.EPSG,
                self.tr('CRS de Destino (Reproyección)'),
                defaultValue='EPSG:32718',
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUT_FOLDER,
                self.tr('Carpeta de Salida (donde se guardarán los rasters)'),
            )
        )

    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        try:
            import ee
        except ImportError:
            raise QgsProcessingException(
                "El paquete earthengine-api no está instalado. "
                "Ejecute 'Instalación dependencias GEE'."
            )
        ensure_gee_initialized(feedback)

        crs            = self.parameterAsCrs(parameters, self.EPSG, context).authid()
        dem_idx        = self.parameterAsEnum(parameters, self.DEM_SOURCE, context)
        out_folder     = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        dem_info       = GEE_DEMS[dem_idx]

        # ── AOI ───────────────────────────────────────────────────────
        feedback.pushInfo("Construyendo Área de Interés (AOI)...")
        aoi_result = build_aoi(self, parameters, context, feedback,
                               layer_param=self.INPUT_LAYER,
                               extent_param=self.INPUT_EXTENT)

        geom_simple  = safe_simplify(aoi_result.geom_4326)
        geom_ee_4326 = to_ee_geometry(geom_simple, ee)
        aoi_ee = geom_ee_4326.bounds()

        # ── Obtener DEM ───────────────────────────────────────────────
        feedback.pushInfo(f"Conectando a {dem_info['label']}...")
        if dem_info['tipo'] == 'Image':
            img = ee.Image(dem_info['collection']).select(dem_info['band'])
        else:
            img = (ee.ImageCollection(dem_info['collection'])
                     .filterBounds(aoi_ee)
                     .mosaic()
                     .select(dem_info['band']))
        
        # Reproyectar a CRS destino
        img_reproj = img.clip(aoi_ee).reproject(crs=crs, scale=dem_info['scale'])

        # ── Descarga Local del DEM ────────────────────────────────────
        os.makedirs(out_folder, exist_ok=True)
        dem_path = os.path.join(out_folder, "DEM.tif")

        feedback.pushInfo("Descargando el DEM base desde Google Earth Engine...")
        try:
            url = img_reproj.getDownloadURL({
                'name': 'DEM',
                'scale': dem_info['scale'],
                'crs': crs,
                'region': aoi_ee,
                'format': 'GEO_TIFF'
            })
            urllib.request.urlretrieve(url, dem_path)  # nosec
            feedback.pushInfo("✓ DEM descargado correctamente.")
        except Exception as e:
            raise QgsProcessingException(f"Error al descargar DEM desde GEE: {str(e)}\nPosiblemente el área sea demasiado grande.")

        # ── Definir salidas ───────────────────────────────────────────
        slope = os.path.join(out_folder, "Slope_porcentaje.tif")
        aspect = os.path.join(out_folder, "Aspect.tif")
        tpi = os.path.join(out_folder, "TPI.tif")
        tri = os.path.join(out_folder, "TRI.tif")
        flowdir = os.path.join(out_folder, "FlowDirection.tif")

        # ── 1. Pendiente (Slope) ──────────────────────────────────────
        if feedback.isCanceled(): return {}
        feedback.pushInfo("Calculando Pendiente (%) ...")
        feedback.setProgress(20)
        processing.run("native:slope", {
            'INPUT': dem_path,
            'Z_FACTOR': 1,
            'OUTPUT': slope
        }, context=context)

        # ── 2. Aspecto ────────────────────────────────────────────────
        if feedback.isCanceled(): return {}
        feedback.pushInfo("Calculando Aspecto ...")
        feedback.setProgress(40)
        processing.run("native:aspect", {
            'INPUT': dem_path,
            'Z_FACTOR': 1,
            'OUTPUT': aspect
        }, context=context)

        # ── 3. TPI ────────────────────────────────────────────────────
        if feedback.isCanceled(): return {}
        feedback.pushInfo("Calculando TPI ...")
        feedback.setProgress(60)
        processing.run("gdal:tpitopographicpositionindex", {
            'INPUT': dem_path,
            'BAND': 1,
            'COMPUTE_EDGES': False,
            'CREATION_OPTIONS': '',
            'OUTPUT': tpi
        }, context=context)

        # ── 4. TRI ────────────────────────────────────────────────────
        if feedback.isCanceled(): return {}
        feedback.pushInfo("Calculando TRI ...")
        feedback.setProgress(80)
        processing.run("gdal:triterrainruggednessindex", {
            'INPUT': dem_path,
            'BAND': 1,
            'COMPUTE_EDGES': False,
            'CREATION_OPTIONS': '',
            'OUTPUT': tri
        }, context=context)

        # ── 5. Flow Direction ─────────────────────────────────────────
        if feedback.isCanceled(): return {}
        feedback.pushInfo("Calculando Flow Direction (D8) con GRASS ...")
        feedback.setProgress(90)
        processing.run("grass:r.watershed", {
            'elevation': dem_path,
            'depression': None,
            'flow': None,
            'disturbed_land': None,
            'blocking': None,
            'threshold': 1000,
            'max_slope_length': 100,
            'convergence': 5,
            'memory': 300,
            '-s': True,      # D8
            '-m': False,
            '-4': False,
            '-a': False,
            '-b': False,
            'accumulation': 'TEMPORARY_OUTPUT',
            'drainage': flowdir,
            'basin': 'TEMPORARY_OUTPUT',
            'stream': 'TEMPORARY_OUTPUT',
            'half_basin': 'TEMPORARY_OUTPUT',
            'length_slope': 'TEMPORARY_OUTPUT',
            'slope_steepness': 'TEMPORARY_OUTPUT',
            'tci': 'TEMPORARY_OUTPUT',
            'spi': 'TEMPORARY_OUTPUT',
            'GRASS_REGION_PARAMETER': None,
            'GRASS_REGION_CELLSIZE_PARAMETER': 0,
            'GRASS_RASTER_FORMAT_OPT': '',
            'GRASS_RASTER_FORMAT_META': ''
        }, context=context)

        # ── Cargar resultados al proyecto ─────────────────────────────
        feedback.setProgress(100)
        feedback.pushInfo("Agregando capas al proyecto...")
        project = QgsProject.instance()
        for raster_path in [slope, aspect, tpi, tri, flowdir]:
            if os.path.exists(raster_path):
                layer = QgsRasterLayer(raster_path, os.path.basename(raster_path))
                if layer.isValid():
                    project.addMapLayer(layer)
        
        # Opcional: Cargar también el DEM base
        if os.path.exists(dem_path):
            layer = QgsRasterLayer(dem_path, os.path.basename(dem_path))
            if layer.isValid():
                project.addMapLayer(layer)

        return {}
