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
from ..utils.gee_tiled_download import download_band_local, estimate_tile_count, announce_grid_plan


class SoilGridsIsricAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER    = 'INPUT_LAYER'
    INPUT_EXTENT   = 'INPUT_EXTENT'
    PROFUNDIDAD    = 'PROFUNDIDAD'
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
        self.profundidades = [
            "0-5",
            "5-15",
            "15-30",
            "30-60",
            "60-100",
            "100-200",
            "0-30",
            "0-100",
            "0-200"
        ]

    # ------------------------------------------------------------------
    def tr(self, string):
        return QCoreApplication.translate('SoilGridsIsricAlgorithm', string)

    def createInstance(self):
        return SoilGridsIsricAlgorithm()

    def name(self):
        return 'soilgrids_isric_v2'

    def displayName(self):
        return self.tr('SoilGrids 250m v2')

    def group(self):
        return self.tr('Variables')

    def groupId(self):
        return 'variables'

    def shortHelpString(self):
        return self.tr(
            "<b>Descripción:</b><br>"
            "Descarga todas las variables de suelo de SoilGrids 250m v2 (ISRIC) "
            "desde Google Earth Engine, aplicando factores de escala.<br><br>"
            "Se puede seleccionar una profundidad específica, y la herramienta "
            "calculará el promedio ponderado si se elige un rango compuesto "
            "(ej. 0-30 cm, 0-100 cm).<br><br>"
            "El área de interés se expande con un buffer de 5 000 m y se recorta "
            "en rectángulo (bounds) antes de exportar a 250 m de resolución.<br><br>"
            "<b>Opciones de AOI:</b><br>"
            "• <b>Opción 1</b>: Selecciona una capa vectorial cargada en QGIS.<br>"
            "• <b>Opción 2</b>: Dibuja un recuadro directamente en el canvas.<br><br>"
            "<b>Fuente:</b> "
            "<a href='https://developers.google.com/earth-engine/datasets/catalog/projects_soilgrids-isric_bdod_mean'>"
            "SoilGrids ISRIC — GEE Catalog</a>"
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

        # ── Profundidad ───────────────────────────────────────────────
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PROFUNDIDAD,
                self.tr('Profundidad (cm)'),
                options=self.profundidades,
                defaultValue=6,  # Por defecto 0-30
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
        try:
            import ee
        except ImportError:
            raise QgsProcessingException(
                "El paquete earthengine-api no está instalado. "
                "Ejecute 'Instalación dependencias GEE'."
            )
        ensure_gee_initialized(feedback)

        crs            = self.parameterAsCrs(parameters, self.EPSG, context).authid()
        prof_index     = self.parameterAsEnum(parameters, self.PROFUNDIDAD, context)
        profundidad    = self.profundidades[prof_index]
        export_method  = self.parameterAsEnum(parameters, self.EXPORT_METHOD, context)
        out_folder     = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        bucket         = self.parameterAsString(parameters, self.GCS_BUCKET, context)

        # ── AOI ───────────────────────────────────────────────────────
        feedback.pushInfo("Construyendo Área de Interés (AOI)...")
        aoi_result = build_aoi(self, parameters, context, feedback,
                               layer_param=self.INPUT_LAYER,
                               extent_param=self.INPUT_EXTENT)

        geom_simple  = safe_simplify(aoi_result.geom_4326)
        geom_ee_4326 = to_ee_geometry(geom_simple, ee)

        feedback.pushInfo("Aplicando buffer de 5 000 m y calculando bounds...")
        aoi_ee = geom_ee_4326.buffer(5000).bounds()

        # ── SoilGrids Variables ───────────────────────────────────────
        variables = [
            {"nombre": "BDOD", "asset": "projects/soilgrids-isric/bdod_mean", "banda": "bdod", "escala": 0.01},
            {"nombre": "CEC", "asset": "projects/soilgrids-isric/cec_mean", "banda": "cec", "escala": 0.1},
            {"nombre": "CFVO", "asset": "projects/soilgrids-isric/cfvo_mean", "banda": "cfvo", "escala": 0.1},
            {"nombre": "CLAY", "asset": "projects/soilgrids-isric/clay_mean", "banda": "clay", "escala": 0.1},
            {"nombre": "SAND", "asset": "projects/soilgrids-isric/sand_mean", "banda": "sand", "escala": 0.1},
            {"nombre": "SILT", "asset": "projects/soilgrids-isric/silt_mean", "banda": "silt", "escala": 0.1},
            {"nombre": "NITROGEN", "asset": "projects/soilgrids-isric/nitrogen_mean", "banda": "nitrogen", "escala": 0.01},
            {"nombre": "PH", "asset": "projects/soilgrids-isric/phh2o_mean", "banda": "phh2o", "escala": 0.1},
            {"nombre": "SOC", "asset": "projects/soilgrids-isric/soc_mean", "banda": "soc", "escala": 0.1},
            {"nombre": "OCD", "asset": "projects/soilgrids-isric/ocd_mean", "banda": "ocd", "escala": 0.1},
            {"nombre": "OCS", "asset": "projects/soilgrids-isric/ocs_mean", "banda": "ocs", "escala": 0.1}
        ]

        # Función para calcular profundidad
        def obtenerProfundidad(img, variable, prof):
            # Excepción especial para OCS (Stock de Carbono Orgánico)
            # Solo está disponible en 0-30cm en el catálogo oficial de ISRIC
            if variable == "ocs":
                if prof == "0-30":
                    return img.select("ocs_0-30cm_mean")
                else:
                    return None

            if prof == "0-5":
                return img.select(f"{variable}_0-5cm_mean")
            elif prof == "5-15":
                return img.select(f"{variable}_5-15cm_mean")
            elif prof == "15-30":
                return img.select(f"{variable}_15-30cm_mean")
            elif prof == "30-60":
                return img.select(f"{variable}_30-60cm_mean")
            elif prof == "60-100":
                return img.select(f"{variable}_60-100cm_mean")
            elif prof == "100-200":
                return img.select(f"{variable}_100-200cm_mean")
            elif prof == "0-30":
                return img.expression(
                    '(b1*5 + b2*10 + b3*15) / 30',
                    {
                        'b1': img.select(f"{variable}_0-5cm_mean"),
                        'b2': img.select(f"{variable}_5-15cm_mean"),
                        'b3': img.select(f"{variable}_15-30cm_mean")
                    }
                )
            elif prof == "0-100":
                return img.expression(
                    '(b1*5 + b2*10 + b3*15 + b4*30 + b5*40) / 100',
                    {
                        'b1': img.select(f"{variable}_0-5cm_mean"),
                        'b2': img.select(f"{variable}_5-15cm_mean"),
                        'b3': img.select(f"{variable}_15-30cm_mean"),
                        'b4': img.select(f"{variable}_30-60cm_mean"),
                        'b5': img.select(f"{variable}_60-100cm_mean")
                    }
                )
            elif prof == "0-200":
                return img.expression(
                    '(b1*5 + b2*10 + b3*15 + b4*30 + b5*40 + b6*100) / 200',
                    {
                        'b1': img.select(f"{variable}_0-5cm_mean"),
                        'b2': img.select(f"{variable}_5-15cm_mean"),
                        'b3': img.select(f"{variable}_15-30cm_mean"),
                        'b4': img.select(f"{variable}_30-60cm_mean"),
                        'b5': img.select(f"{variable}_60-100cm_mean"),
                        'b6': img.select(f"{variable}_100-200cm_mean")
                    }
                )
            return None

        # Consolidar bandas
        bandas_procesadas = []
        nombres_bandas = []
        feedback.pushInfo(f"Calculando variables para profundidad: {profundidad}")

        for v in variables:
            img = ee.Image(v["asset"])
            salida = obtenerProfundidad(img, v["banda"], profundidad)
            if salida:
                # factor de escala, renombrar, float y clip
                salida = salida.multiply(v["escala"]).rename(v["nombre"]).toFloat().clip(aoi_ee)
                bandas_procesadas.append(salida)
                nombres_bandas.append(v["nombre"])

        # Crear imagen con todas las bandas consolidadas y reproyectar a 250m
        imagen_final = ee.Image.cat(bandas_procesadas).reproject(crs=crs, scale=250)

        feedback.pushInfo(f"Bandas a exportar: {', '.join(nombres_bandas)}")

        # ── Exportación ───────────────────────────────────────────────
        if export_method == 0:  # Descarga directa local
            self._export_local(ee, imagen_final, nombres_bandas, aoi_ee, crs, profundidad, out_folder, feedback)

        elif export_method == 1:  # Google Drive
            self._export_drive(ee, imagen_final, nombres_bandas, aoi_ee, crs, profundidad, out_folder, feedback)

        elif export_method == 2:  # GCS
            if not bucket:
                raise QgsProcessingException(
                    "Debe especificar un nombre de Bucket para exportar a GCS."
                )
            self._export_gcs(ee, imagen_final, nombres_bandas, aoi_ee, crs, profundidad, bucket, out_folder, feedback)

        return {}

    # ------------------------------------------------------------------
    # Métodos de exportación
    # ------------------------------------------------------------------

    def _export_local(self, ee, imagen, bandas, aoi, crs, prof, out_folder, feedback):
        """Descarga directa de cada banda SoilGrids como GeoTIFF local.

        Si el área es grande para un solo request de GEE, se divide
        automáticamente en una grilla de celdas, se descargan por
        separado y se ensambla un mosaico (ver utils/gee_tiled_download.py).
        """
        os.makedirs(out_folder, exist_ok=True)

        n_cells = estimate_tile_count(ee, aoi, scale_m=250)
        announce_grid_plan(feedback, n_cells, n_variables=len(bandas))

        for banda in bandas:
            if feedback.isCanceled():
                break
            filename = f"{banda}_{prof}"
            feedback.pushInfo(f"  Descargando {filename} ...")
            filepath = os.path.join(out_folder, f"{filename}.tif")
            download_band_local(
                ee, imagen, banda, aoi, crs,
                scale=250,
                out_folder=out_folder,
                filepath=filepath,
                feedback=feedback,
                download_params={
                    'formatOptions': {
                        'cloudOptimized': False,
                        'noData': -9999
                    }
                }
            )

    def _export_drive(self, ee, imagen, bandas, aoi, crs, prof, out_folder, feedback):
        folder_name = os.path.basename(out_folder.rstrip('/\\')) or "SoilGrids_ISRIC"
        task_ids = []
        for banda in bandas:
            if feedback.isCanceled():
                break
            feedback.pushInfo(f"  Enviando tarea Drive para {banda}...")
            filename = f"{banda}_{prof}"
            task = ee.batch.Export.image.toDrive(
                image          = imagen.select(banda),
                description    = filename,
                folder         = folder_name,
                fileNamePrefix = filename,
                region         = aoi,
                scale          = 250,
                crs            = crs,
                maxPixels      = int(1e13),
                fileFormat     = 'GeoTIFF',
                formatOptions  = {
                    'cloudOptimized': False,
                    'noData': -9999
                }
            )
            task.start()
            task_ids.append(task.status().get('id', 'desconocido'))
        feedback.pushInfo(f"Tareas Drive iniciadas ({len(task_ids)}): {task_ids}")
        feedback.pushInfo("Revisa el progreso en https://code.earthengine.google.com/tasks")

    def _export_gcs(self, ee, imagen, bandas, aoi, crs, prof, bucket, out_folder, feedback):
        prefix = os.path.basename(out_folder.rstrip('/\\')) or "SoilGrids_ISRIC"
        task_ids = []
        for banda in bandas:
            if feedback.isCanceled():
                break
            feedback.pushInfo(f"  Enviando tarea GCS para {banda}...")
            filename = f"{banda}_{prof}"
            task = ee.batch.Export.image.toCloudStorage(
                image          = imagen.select(banda),
                description    = filename,
                bucket         = bucket,
                fileNamePrefix = f"{prefix}/{filename}",
                region         = aoi,
                scale          = 250,
                crs            = crs,
                maxPixels      = int(1e13),
                fileFormat     = 'GeoTIFF',
                formatOptions  = {
                    'cloudOptimized': False,
                    'noData': -9999
                }
            )
            task.start()
            task_ids.append(task.status().get('id', 'desconocido'))
        feedback.pushInfo(f"Tareas GCS iniciadas ({len(task_ids)}): {task_ids}")
        feedback.pushInfo("Revisa el progreso en https://code.earthengine.google.com/tasks")
