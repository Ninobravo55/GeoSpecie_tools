import os
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterMapLayer,
    QgsProcessingParameterExtent,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterString,
    QgsProcessingException,
)
from qgis.PyQt.QtCore import QCoreApplication

from ..utils.aoi_builder import build_aoi, to_ee_geometry, safe_simplify
from ..utils.gee_init import ensure_gee_initialized


class WorldClimClimatologyAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER    = 'INPUT_LAYER'
    INPUT_EXTENT   = 'INPUT_EXTENT'
    EPSG           = 'EPSG'
    EXPORT_METHOD  = 'EXPORT_METHOD'
    OUT_FOLDER     = 'OUT_FOLDER'
    GCS_BUCKET     = 'GCS_BUCKET'

    def __init__(self):
        super().__init__()
        self.export_methods = [
            "Descarga Directa Local",
            "Exportar a Google Drive",
            "Exportar a Google Cloud Storage (GCS)",
        ]

    # ------------------------------------------------------------------
    def tr(self, string):
        return QCoreApplication.translate('WorldClimClimatologyAlgorithm', string)

    def createInstance(self):
        return WorldClimClimatologyAlgorithm()

    def name(self):
        return 'worldclim_climatology_v1'

    def displayName(self):
        return self.tr('WorldClim Climatology v1')

    def group(self):
        return self.tr('Variables')

    def groupId(self):
        return 'variables'

    def shortHelpString(self):
        return self.tr(
            "<b>Descripción:</b><br>"
            "Descarga las variables climatológicas (Temperatura mínima media anual, Temperatura media anual, "
            "Temperatura máxima media anual, Precipitación anual acumulada) de WorldClim v1 "
            "(WORLDCLIM/V1/MONTHLY) desde Google Earth Engine, aplicando los "
            "factores de escala oficiales.<br><br>"
            "El área de interés se expande internamente con un buffer de "
            "5 000 m y se recorta en rectángulo (bounds) antes de exportar.<br><br>"
            "<b>Opciones de AOI:</b><br>"
            "• <b>Opción 1</b>: Selecciona una capa vectorial cargada en QGIS.<br>"
            "• <b>Opción 2</b>: Dibuja un recuadro directamente en el canvas.<br><br>"
            "<b>Fuente:</b> "
            "<a href='https://developers.google.com/earth-engine/datasets/catalog/WORLDCLIM_V1_MONTHLY'>"
            "WorldClim V1 Monthly — GEE Catalog</a>"
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

        # ── Opciones de salida ────────────────────────────────────────
        self.addParameter(
            QgsProcessingParameterCrs(
                self.EPSG,
                self.tr('CRS de Destino'),
                defaultValue='EPSG:32718',
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.EXPORT_METHOD,
                self.tr('Método de Exportación'),
                options=self.export_methods,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUT_FOLDER,
                self.tr('Carpeta de Salida (local) / Nombre de carpeta en Drive o GCS'),
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.GCS_BUCKET,
                self.tr('Nombre del Bucket GCS (solo si usa GCS)'),
                optional=True,
            )
        )

    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        # ── Importar earthengine-api ──────────────────────────────────
        try:
            import ee
        except ImportError:
            raise QgsProcessingException(
                "El paquete earthengine-api no está instalado. "
                "Ejecute 'Instalación dependencias GEE'."
            )
        ensure_gee_initialized(feedback)

        # ── Parámetros ────────────────────────────────────────────────
        crs           = self.parameterAsCrs(parameters, self.EPSG, context).authid()
        export_method = self.parameterAsEnum(parameters, self.EXPORT_METHOD, context)
        out_folder    = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        bucket        = self.parameterAsString(parameters, self.GCS_BUCKET, context)

        # ── AOI ───────────────────────────────────────────────────────
        feedback.pushInfo("Construyendo Área de Interés (AOI)...")
        aoi_result = build_aoi(self, parameters, context, feedback,
                               layer_param=self.INPUT_LAYER,
                               extent_param=self.INPUT_EXTENT)

        geom_simple  = safe_simplify(aoi_result.geom_4326)
        geom_ee_4326 = to_ee_geometry(geom_simple, ee)

        feedback.pushInfo("Aplicando buffer de 5 000 m y calculando bounds...")
        aoi_ee = geom_ee_4326.buffer(5000).bounds()

        # ── WorldClim Climatology ─────────────────────────────────────
        feedback.pushInfo("Cargando ImageCollection WORLDCLIM/V1/MONTHLY desde GEE...")
        monthly = ee.ImageCollection("WORLDCLIM/V1/MONTHLY")

        # Temperatura mínima media anual
        tmin = monthly.select('tmin').mean().multiply(0.1).clip(aoi_ee).toFloat().rename('TMIN_ANUAL')
        
        # Temperatura media anual
        tavg = monthly.select('tavg').mean().multiply(0.1).clip(aoi_ee).toFloat().rename('TAVG_ANUAL')
        
        # Temperatura máxima media anual
        tmax = monthly.select('tmax').mean().multiply(0.1).clip(aoi_ee).toFloat().rename('TMAX_ANUAL')
        
        # Precipitación anual acumulada
        prec = monthly.select('prec').sum().clip(aoi_ee).toFloat().rename('PREC_ANUAL')

        imagen = ee.Image.cat([tmin, tavg, tmax, prec])

        # Reproyectar al CRS de destino (escala 1 000 m)
        imagen = imagen.reproject(crs=crs, scale=1000)

        bandas = imagen.bandNames().getInfo()
        feedback.pushInfo(f"Bandas a exportar: {', '.join(bandas)}")

        # ── Exportación ───────────────────────────────────────────────
        if export_method == 0:  # Descarga directa local
            self._export_local(ee, imagen, bandas, aoi_ee, crs, out_folder, feedback)

        elif export_method == 1:  # Google Drive
            self._export_drive(ee, imagen, bandas, aoi_ee, crs, out_folder, feedback)

        elif export_method == 2:  # GCS
            if not bucket:
                raise QgsProcessingException(
                    "Debe especificar un nombre de Bucket para exportar a GCS."
                )
            self._export_gcs(ee, imagen, bandas, aoi_ee, crs, bucket, out_folder, feedback)

        return {}

    # ------------------------------------------------------------------
    # Métodos de exportación
    # ------------------------------------------------------------------

    def _export_local(self, ee, imagen, bandas, aoi, crs, out_folder, feedback):
        import urllib.request, urllib.error

        os.makedirs(out_folder, exist_ok=True)
        for banda in bandas:
            if feedback.isCanceled():
                break
            feedback.pushInfo(f"  Descargando {banda} ...")
            filepath = os.path.join(out_folder, f"{banda}.tif")
            try:
                url = imagen.select(banda).getDownloadURL({
                    'name'   : banda,
                    'scale'  : 1000,
                    'crs'    : crs,
                    'region' : aoi,
                    'format' : 'GEO_TIFF',
                })
                urllib.request.urlretrieve(url, filepath)  # nosec
                feedback.pushInfo(f"    ✓ Guardado: {filepath}")
            except Exception as e:
                feedback.reportError(f"    ✗ Error en {banda}: {str(e)}")

    def _export_drive(self, ee, imagen, bandas, aoi, crs, out_folder, feedback):
        folder_name = os.path.basename(out_folder.rstrip('/\\')) or "WorldClim_Annual"
        task_ids = []
        for banda in bandas:
            if feedback.isCanceled():
                break
            feedback.pushInfo(f"  Enviando tarea Drive para {banda}...")
            task = ee.batch.Export.image.toDrive(
                image          = imagen.select(banda),
                description    = banda,
                folder         = folder_name,
                fileNamePrefix = banda,
                region         = aoi,
                scale          = 1000,
                crs            = crs,
                maxPixels      = int(1e13),
                fileFormat     = 'GeoTIFF',
            )
            task.start()
            task_ids.append(task.status().get('id', 'desconocido'))
        feedback.pushInfo(f"Tareas Drive iniciadas ({len(task_ids)}): {task_ids}")
        feedback.pushInfo("Revisa el progreso en https://code.earthengine.google.com/tasks")

    def _export_gcs(self, ee, imagen, bandas, aoi, crs, bucket, out_folder, feedback):
        prefix = os.path.basename(out_folder.rstrip('/\\')) or "WorldClim_Annual"
        task_ids = []
        for banda in bandas:
            if feedback.isCanceled():
                break
            feedback.pushInfo(f"  Enviando tarea GCS para {banda}...")
            task = ee.batch.Export.image.toCloudStorage(
                image          = imagen.select(banda),
                description    = banda,
                bucket         = bucket,
                fileNamePrefix = f"{prefix}/{banda}",
                region         = aoi,
                scale          = 1000,
                crs            = crs,
                maxPixels      = int(1e13),
                fileFormat     = 'GeoTIFF',
            )
            task.start()
            task_ids.append(task.status().get('id', 'desconocido'))
        feedback.pushInfo(f"Tareas GCS iniciadas ({len(task_ids)}): {task_ids}")
        feedback.pushInfo("Revisa el progreso en https://code.earthengine.google.com/tasks")
