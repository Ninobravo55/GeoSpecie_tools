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


class WorldClimBioAlgorithm(QgsProcessingAlgorithm):
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
        return QCoreApplication.translate('WorldClimBioAlgorithm', string)

    def createInstance(self):
        return WorldClimBioAlgorithm()

    def name(self):
        return 'worldclim_bio_v1'

    def displayName(self):
        return self.tr('WorldClim BIO v1')

    def group(self):
        return self.tr('Variables')

    def groupId(self):
        return 'variables'

    def shortHelpString(self):
        return self.tr(
            "<b>Descripción:</b><br>"
            "Descarga las 19 variables bioclimáticas de WorldClim v1 "
            "(WORLDCLIM/V1/BIO) desde Google Earth Engine, aplicando los "
            "factores de escala oficiales de cada banda.<br><br>"
            "El área de interés se expande internamente con un buffer de "
            "5 000 m y se recorta en rectángulo (bounds) antes de exportar.<br><br>"
            "<b>Opciones de AOI:</b><br>"
            "• <b>Opción 1</b>: Selecciona una capa vectorial cargada en QGIS.<br>"
            "• <b>Opción 2</b>: Dibuja un recuadro directamente en el canvas.<br><br>"
            "<b>Fuente:</b> "
            "<a href='https://developers.google.com/earth-engine/datasets/catalog/WORLDCLIM_V1_BIO'>"
            "WorldClim V1 BIO — GEE Catalog</a>"
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

        # ── AOI — idéntico al patrón de GEOBOSQUES ────────────────────
        feedback.pushInfo("Construyendo Área de Interés (AOI)...")
        aoi_result = build_aoi(self, parameters, context, feedback,
                               layer_param=self.INPUT_LAYER,
                               extent_param=self.INPUT_EXTENT)

        # Simplificar y convertir a ee.Geometry
        geom_simple  = safe_simplify(aoi_result.geom_4326)
        geom_ee_4326 = to_ee_geometry(geom_simple, ee)

        # Buffer de 5 000 m y bounds (rectángulo)
        feedback.pushInfo("Aplicando buffer de 5 000 m y calculando bounds...")
        aoi_ee = geom_ee_4326.buffer(5000).bounds()

        # ── WorldClim BIO — aplicar factores de escala ────────────────
        feedback.pushInfo("Cargando imagen WORLDCLIM/V1/BIO desde GEE...")
        bio = ee.Image("WORLDCLIM/V1/BIO")

        bioEscalada = ee.Image.cat([
            bio.select('bio01').multiply(0.1).rename('bio01'),
            bio.select('bio02').multiply(0.1).rename('bio02'),
            bio.select('bio03')              .rename('bio03'),   # sin escala
            bio.select('bio04').multiply(0.01).rename('bio04'),
            bio.select('bio05').multiply(0.1).rename('bio05'),
            bio.select('bio06').multiply(0.1).rename('bio06'),
            bio.select('bio07').multiply(0.1).rename('bio07'),
            bio.select('bio08').multiply(0.1).rename('bio08'),
            bio.select('bio09').multiply(0.1).rename('bio09'),
            bio.select('bio10').multiply(0.1).rename('bio10'),
            bio.select('bio11').multiply(0.1).rename('bio11'),
            bio.select('bio12')              .rename('bio12'),   # mm — sin escala
            bio.select('bio13')              .rename('bio13'),
            bio.select('bio14')              .rename('bio14'),
            bio.select('bio15')              .rename('bio15'),
            bio.select('bio16')              .rename('bio16'),
            bio.select('bio17')              .rename('bio17'),
            bio.select('bio18')              .rename('bio18'),
            bio.select('bio19')              .rename('bio19'),
        ]).clip(aoi_ee).toFloat()

        # Reproyectar al CRS de destino (escala 1 000 m)
        bioEscalada = bioEscalada.reproject(crs=crs, scale=1000)

        bandas = bioEscalada.bandNames().getInfo()
        feedback.pushInfo(f"Bandas a exportar: {', '.join(bandas)}")

        # ── Exportación ───────────────────────────────────────────────
        if export_method == 0:  # Descarga directa local
            self._export_local(ee, bioEscalada, bandas, aoi_ee, crs, out_folder, feedback)

        elif export_method == 1:  # Google Drive
            self._export_drive(ee, bioEscalada, bandas, aoi_ee, crs, out_folder, feedback)

        elif export_method == 2:  # GCS
            if not bucket:
                raise QgsProcessingException(
                    "Debe especificar un nombre de Bucket para exportar a GCS."
                )
            self._export_gcs(ee, bioEscalada, bandas, aoi_ee, crs, bucket, out_folder, feedback)

        return {}

    # ------------------------------------------------------------------
    # Métodos de exportación
    # ------------------------------------------------------------------

    def _export_local(self, ee, imagen, bandas, aoi, crs, out_folder, feedback):
        """Descarga directa de cada banda como GeoTIFF local."""
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
        """Envía tareas de exportación a Google Drive."""
        folder_name = os.path.basename(out_folder.rstrip('/\\')) or "WorldClim_BIO"
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
        """Envía tareas de exportación a Google Cloud Storage."""
        prefix = os.path.basename(out_folder.rstrip('/\\')) or "WorldClim_BIO"
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
