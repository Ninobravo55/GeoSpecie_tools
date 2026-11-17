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
    QgsProcessingParameterBoolean,
    QgsProcessingException,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant

from ..utils.aoi_builder import build_aoi, to_ee_geometry, safe_simplify
from ..utils.gee_init import ensure_gee_initialized
from ..utils.gee_tiled_download import download_band_local, estimate_tile_count, announce_grid_plan


class SueloIniaPeruAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER    = 'INPUT_LAYER'
    INPUT_EXTENT   = 'INPUT_EXTENT'
    VARIABLES      = 'VARIABLES'
    TIPO_SALIDA    = 'TIPO_SALIDA'
    VECTORIZAR     = 'VECTORIZAR'
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
        self.tipos_salida = [
            "Solo Clasificado (1-5)",
            "Solo Original",
            "Ambos (Clasificado y Original)"
        ]
        self.variables_opciones = [
            "Fertilidad (SFI)",
            "pH",
            "Potasio Disponible (KDIS)",
            "Fósforo Disponible (PDISP)",
            "Materia Orgánica (MO)",
            "Conductividad Eléctrica (CE)",
            "Limo",
            "Arcilla",
            "Arena"
        ]

    # ------------------------------------------------------------------
    def tr(self, string):
        return QCoreApplication.translate('SueloIniaPeruAlgorithm', string)

    def createInstance(self):
        return SueloIniaPeruAlgorithm()

    def name(self):
        return 'suelo_inia_peru'

    def displayName(self):
        return self.tr('Variables de Suelo INIA - Perú')

    def group(self):
        return self.tr('Variables')

    def groupId(self):
        return 'variables'

    def shortHelpString(self):
        return self.tr(
            "<b>Descripción:</b><br>"
            "Descarga variables de suelo clasificadas de INIA - Perú "
            "desde Google Earth Engine.<br><br>"
            "Permite seleccionar una o más variables que se descargarán "
            "clasificadas en 5 clases.<br><br>"
            "El área de interés se expande con un buffer y se recorta "
            "en rectángulo (bounds) a 500 m de resolución.<br><br>"
            "<b>Variables Disponibles:</b><br>"
            "Fertilidad, pH, Potasio, Fósforo, Materia Orgánica, CE, Limo, Arcilla, Arena.<br><br>"
            "<b>Opciones de AOI:</b><br>"
            "• <b>Opción 1</b>: Selecciona una capa vectorial cargada en QGIS.<br>"
            "• <b>Opción 2</b>: Dibuja un recuadro directamente en el canvas."
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

        # ── Variables ───────────────────────────────────────────────
        self.addParameter(
            QgsProcessingParameterEnum(
                self.VARIABLES,
                self.tr('Variables de suelo a exportar'),
                options=self.variables_opciones,
                allowMultiple=True,
                defaultValue=[0,1,2,3,4,5,6,7,8]
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.VECTORIZAR,
                self.tr('Convertir raster clasificado a vector (shp) y calcular área (Solo para Descarga Local)'),
                defaultValue=True
            )
        )

        # ── Opciones de salida ────────────────────────────────────────
        self.addParameter(
            QgsProcessingParameterEnum(
                self.TIPO_SALIDA,
                self.tr('Tipo de salida (Clasificado / Original)'),
                options=self.tipos_salida,
                defaultValue=2,
            )
        )
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
        try:
            import ee
        except ImportError:
            raise QgsProcessingException(
                "El paquete earthengine-api no está instalado. "
                "Ejecute 'Instalación dependencias GEE'."
            )
        ensure_gee_initialized(feedback)

        crs            = self.parameterAsCrs(parameters, self.EPSG, context).authid()
        vars_indices   = self.parameterAsEnums(parameters, self.VARIABLES, context)
        tipo_salida    = self.parameterAsEnum(parameters, self.TIPO_SALIDA, context)
        vectorizar     = self.parameterAsBool(parameters, self.VECTORIZAR, context)
        export_method  = self.parameterAsEnum(parameters, self.EXPORT_METHOD, context)
        out_folder     = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        bucket         = self.parameterAsString(parameters, self.GCS_BUCKET, context)

        if not vars_indices:
            raise QgsProcessingException("Debe seleccionar al menos una variable de suelo.")

        # ── AOI ───────────────────────────────────────────────────────
        feedback.pushInfo("Construyendo Área de Interés (AOI)...")
        aoi_result = build_aoi(self, parameters, context, feedback,
                               layer_param=self.INPUT_LAYER,
                               extent_param=self.INPUT_EXTENT)

        geom_simple  = safe_simplify(aoi_result.geom_4326)
        geom_ee_4326 = to_ee_geometry(geom_simple, ee)

        # Para INIA 500m, aplicamos un buffer similar (ej: 5000m)
        feedback.pushInfo("Aplicando buffer de 5 000 m y calculando bounds...")
        aoi_ee = geom_ee_4326.buffer(5000).bounds()

        # ── Definición de Variables y Clasificación ───────────────────
        def clasificar5Clases(imagen, limite1, limite2, limite3, limite4):
            clasificacion = ee.Image(0) \
                .where(imagen.lte(limite1), 1) \
                .where(imagen.gt(limite1).And(imagen.lte(limite2)), 2) \
                .where(imagen.gt(limite2).And(imagen.lte(limite3)), 3) \
                .where(imagen.gt(limite3).And(imagen.lte(limite4)), 4) \
                .where(imagen.gt(limite4), 5)
            
            return clasificacion.updateMask(imagen.mask()).toByte()

        # Metadatos de variables (asset, funcion_limites, nombre)
        var_config = [
            {
                "nombre": "Fertilidad_CLASE", 
                "asset": "projects/ee-sebasgch5/assets/PERU_SFI_GEE_RF_500m",
                "limites": [0.51, 0.59, 0.66, 0.73]
            },
            {
                "nombre": "PH_CLASE", 
                "asset": "projects/ee-sebasgch5/assets/PERU_pH_500m",
                "limites": [5.51, 6.01, 6.41, 6.78]
            },
            {
                "nombre": "KDIS_CLASE", 
                "asset": "projects/ee-sebasgch5/assets/PERU_Kdisp_500m",
                "limites": [109.41, 132.62, 171.60, 218.30]
            },
            {
                "nombre": "PDIS_CLASE", 
                "asset": "projects/ee-sebasgch5/assets/PERU_Pdisp_500m",
                "limites": [11.86, 13.07, 16.38, 19.98]
            },
            {
                "nombre": "MO_CLASE", 
                "asset": "projects/ee-sebasgch5/assets/PERU_MO_500m",
                "limites": [2.92, 3.40, 3.79, 4.68]
            },
            {
                "nombre": "CE_CLASE", 
                "asset": "projects/ee-sebasgch5/assets/PERU_CE_500m",
                "limites": [0.53, 0.73, 0.84, 1.10]
            },
            {
                "nombre": "LIMO_CLASE", 
                "asset": "projects/ee-sebasgch5/assets/PERU_Limo_500m",
                "limites": [25.29, 28.14, 32.26, 38.11]
            },
            {
                "nombre": "ARCILLA_CLASE", 
                "asset": "projects/ee-sebasgch5/assets/PERU_Arcilla_500m",
                "limites": [15.37, 19.08, 21.77, 24.34]
            },
            {
                "nombre": "ARENA_CLASE", 
                "asset": "projects/ee-sebasgch5/assets/PERU_Arena_500m",
                "limites": [42.52, 47.22, 50.70, 54.08]
            }
        ]

        bandas_procesadas = []
        nombres_bandas = []

        feedback.pushInfo("Procesando variables seleccionadas...")
        for idx in vars_indices:
            cfg = var_config[idx]
            nombre_base = cfg["nombre"].replace("_CLASE", "")
            
            img_bruta = ee.Image(cfg["asset"])
            
            # Recortar al area de interes
            img_clip = img_bruta.clip(aoi_ee)
            
            if tipo_salida in [0, 2]: # Clasificado
                img_clase = clasificar5Clases(img_clip, *cfg["limites"]).rename(f"{nombre_base}_CLASE")
                bandas_procesadas.append(img_clase)
                nombres_bandas.append(f"{nombre_base}_CLASE")
                
            if tipo_salida in [1, 2]: # Original
                img_orig = img_clip.rename(f"{nombre_base}_ORIG").toFloat()
                bandas_procesadas.append(img_orig)
                nombres_bandas.append(f"{nombre_base}_ORIG")

        # Crear imagen con todas las bandas consolidadas y reproyectar a 500m
        imagen_final = ee.Image.cat(bandas_procesadas).reproject(crs=crs, scale=500)

        feedback.pushInfo(f"Variables a exportar: {', '.join(nombres_bandas)}")

        # ── Exportación ───────────────────────────────────────────────
        limites_dict = {cfg['nombre'].replace("_CLASE", ""): cfg['limites'] for cfg in var_config}
        if export_method == 0:  # Descarga directa local
            self._export_local(ee, imagen_final, nombres_bandas, aoi_ee, crs, out_folder, feedback, vectorizar, limites_dict)

        elif export_method == 1:  # Google Drive
            self._export_drive(ee, imagen_final, nombres_bandas, aoi_ee, crs, out_folder, feedback)

        elif export_method == 2:  # GCS
            if not bucket:
                raise QgsProcessingException(
                    "Debe especificar un nombre de Bucket para exportar a GCS."
                )
            self._export_gcs(ee, imagen_final, nombres_bandas, aoi_ee, crs, bucket, out_folder, feedback)

        return {}

    # ------------------------------------------------------------------
    # Métodos de exportación
    # ------------------------------------------------------------------

    def _export_local(self, ee, imagen, bandas, aoi, crs, out_folder, feedback, vectorizar=False, limites_dict=None):
        os.makedirs(out_folder, exist_ok=True)

        n_cells = estimate_tile_count(ee, aoi, scale_m=500)
        announce_grid_plan(feedback, n_cells, n_variables=len(bandas))

        for banda in bandas:
            if feedback.isCanceled():
                break
            filename = f"{banda}"
            feedback.pushInfo(f"  Descargando {filename} ...")
            filepath = os.path.join(out_folder, f"{filename}.tif")
            download_band_local(
                ee, imagen, banda, aoi, crs,
                scale=500,
                out_folder=out_folder,
                filepath=filepath,
                feedback=feedback,
                download_params={
                    'formatOptions': {
                        'cloudOptimized': False,
                        'noData': 0
                    }
                }
            )
            
            if vectorizar and banda.endswith('_CLASE'):
                nombre_base = banda.replace('_CLASE', '')
                limites = limites_dict.get(nombre_base) if limites_dict else None
                self._vectorizar_banda(filepath, banda, out_folder, limites, feedback)

    def _vectorizar_banda(self, raster_path, banda, out_folder, limites, feedback):
        feedback.pushInfo(f"  Vectorizando {banda}...")
        try:
            import processing
            
            vector_path = os.path.join(out_folder, f"{banda}.shp")
            
            # 1. Poligonizar
            res_poly = processing.run("gdal:polygonize", {
                'INPUT': raster_path,
                'BAND': 1,
                'FIELD': 'DN',
                'EIGHT_CONNECTEDNESS': False,
                'EXTRA': '',
                'OUTPUT': 'TEMPORARY_OUTPUT'
            }, feedback=feedback)
            
            # 2. Calcular Area_ha
            res_area = processing.run("native:fieldcalculator", {
                'INPUT': res_poly['OUTPUT'],
                'FIELD_NAME': 'Area_ha',
                'FIELD_TYPE': 0, # Decimal
                'FIELD_LENGTH': 15,
                'FIELD_PRECISION': 4,
                'FORMULA': '$area / 10000',
                'OUTPUT': 'TEMPORARY_OUTPUT'
            }, feedback=feedback)
            
            # 3. Calcular Rango
            formula = "'N/A'"
            if limites and len(limites) == 4:
                formula = (
                    f"CASE "
                    f"WHEN \"DN\" = 1 THEN '<= {limites[0]}' "
                    f"WHEN \"DN\" = 2 THEN '{limites[0]} - {limites[1]}' "
                    f"WHEN \"DN\" = 3 THEN '{limites[1]} - {limites[2]}' "
                    f"WHEN \"DN\" = 4 THEN '{limites[2]} - {limites[3]}' "
                    f"WHEN \"DN\" = 5 THEN '> {limites[3]}' "
                    f"ELSE 'N/A' END"
                )
            
            res_rango = processing.run("native:fieldcalculator", {
                'INPUT': res_area['OUTPUT'],
                'FIELD_NAME': 'Rango',
                'FIELD_TYPE': 2, # String
                'FIELD_LENGTH': 50,
                'FIELD_PRECISION': 0,
                'FORMULA': formula,
                'OUTPUT': 'TEMPORARY_OUTPUT'
            }, feedback=feedback)
            
            # 4. Guardar archivo final
            processing.run("native:savefeatures", {
                'INPUT': res_rango['OUTPUT'],
                'OUTPUT': vector_path
            }, feedback=feedback)
            
            feedback.pushInfo(f"    Guardado vector: {vector_path}")
        except Exception as e:
            feedback.reportError(f"Error al vectorizar {banda}: {str(e)}")

    def _export_drive(self, ee, imagen, bandas, aoi, crs, out_folder, feedback):
        folder_name = os.path.basename(out_folder.rstrip('/\\')) or "Suelo_INIA_Peru"
        task_ids = []
        for banda in bandas:
            if feedback.isCanceled():
                break
            feedback.pushInfo(f"  Enviando tarea Drive para {banda}...")
            filename = f"{banda}"
            task = ee.batch.Export.image.toDrive(
                image          = imagen.select(banda),
                description    = filename,
                folder         = folder_name,
                fileNamePrefix = filename,
                region         = aoi,
                scale          = 500,
                crs            = crs,
                maxPixels      = int(1e13),
                fileFormat     = 'GeoTIFF',
                formatOptions  = {
                    'cloudOptimized': False,
                    'noData': 0
                }
            )
            task.start()
            task_ids.append(task.status().get('id', 'desconocido'))
        feedback.pushInfo(f"Tareas Drive iniciadas ({len(task_ids)}): {task_ids}")
        feedback.pushInfo("Revisa el progreso en https://code.earthengine.google.com/tasks")

    def _export_gcs(self, ee, imagen, bandas, aoi, crs, bucket, out_folder, feedback):
        prefix = os.path.basename(out_folder.rstrip('/\\')) or "Suelo_INIA_Peru"
        task_ids = []
        for banda in bandas:
            if feedback.isCanceled():
                break
            feedback.pushInfo(f"  Enviando tarea GCS para {banda}...")
            filename = f"{banda}"
            task = ee.batch.Export.image.toCloudStorage(
                image          = imagen.select(banda),
                description    = filename,
                bucket         = bucket,
                fileNamePrefix = f"{prefix}/{filename}",
                region         = aoi,
                scale          = 500,
                crs            = crs,
                maxPixels      = int(1e13),
                fileFormat     = 'GeoTIFF',
                formatOptions  = {
                    'cloudOptimized': False,
                    'noData': 0
                }
            )
            task.start()
            task_ids.append(task.status().get('id', 'desconocido'))
        feedback.pushInfo(f"Tareas GCS iniciadas ({len(task_ids)}): {task_ids}")
        feedback.pushInfo("Revisa el progreso en https://code.earthengine.google.com/tasks")
