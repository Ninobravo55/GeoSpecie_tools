"""
algorithm.py — Algoritmo de procesamiento QGIS para Variables Climáticas Actuales (GEE).
"""

import os
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterMapLayer,
    QgsProcessingParameterExtent,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterString,
    QgsProcessingException,
)
from qgis.PyQt.QtCore import QCoreApplication

from ..utils.aoi_builder import build_aoi, to_ee_geometry, safe_simplify
from ..utils.gee_init import ensure_gee_initialized
from ..utils.gee_tiled_download import download_band_local, estimate_tile_count, announce_grid_plan
from .catalog import CLIMATIC_PRODUCTS
from .gee_climate_engine import build_climate_images


class VariablesClimaticasActualesAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER    = 'INPUT_LAYER'
    INPUT_EXTENT   = 'INPUT_EXTENT'
    EPSG           = 'EPSG'
    PRODUCT        = 'PRODUCT'
    YEAR_MODE      = 'YEAR_MODE'
    YEAR_SINGLE    = 'YEAR_SINGLE'
    YEAR_START     = 'YEAR_START'
    YEAR_END       = 'YEAR_END'
    VAR_PREC       = 'VAR_PREC'
    VAR_TMIN       = 'VAR_TMIN'
    VAR_TMAX       = 'VAR_TMAX'
    VAR_TMEAN      = 'VAR_TMEAN'
    VAR_WIND       = 'VAR_WIND'
    VAR_RH         = 'VAR_RH'
    VAR_SOLAR      = 'VAR_SOLAR'
    EXPORT_METHOD  = 'EXPORT_METHOD'
    OUT_FOLDER     = 'OUT_FOLDER'
    GCS_BUCKET     = 'GCS_BUCKET'

    def __init__(self):
        super().__init__()
        self.product_options = [f"{p['name']} ({p['res_text']})" for p in CLIMATIC_PRODUCTS]
        self.year_modes = ["Año Individual", "Rango de Años / Período (Promedio Multianual)"]
        self.export_methods = [
            "Descarga Directa Local",
            "Exportar a Google Drive",
            "Exportar a Google Cloud Storage (GCS)",
        ]

    def tr(self, string):
        return QCoreApplication.translate('VariablesClimaticasActualesAlgorithm', string)

    def createInstance(self):
        return VariablesClimaticasActualesAlgorithm()

    def name(self):
        return 'variables_climaticas_actuales'

    def displayName(self):
        return self.tr('Variables Climáticas Actuales (GEE)')

    def group(self):
        return self.tr('Variables')

    def groupId(self):
        return 'variables'

    def shortHelpString(self):
        return self.tr(
            "<b>Descripción:</b><br>"
            "Descarga y procesa variables climáticas actuales (Precipitación acumulada, "
            "Temperatura mínima, máxima y media, Velocidad del viento, Humedad relativa y "
            "Radiación solar) desde Google Earth Engine utilizando las principales colecciones "
            "mundiales y regionales (ERA5, CHIRPS, CFR, GPM, PERSIANN, GSMaP, RAIN4PE, PISCO).<br><br>"
            "<b>Lógica de cálculo:</b><br>"
            "• <b>Precipitación:</b> Suma de precipitaciones diarias para obtener la "
            "precipitación anual acumulada (mm/año). Si se selecciona un rango de años, "
            "calcula el promedio multianual de las sumas anuales.<br>"
            "• <b>Temperatura, Viento, Humedad Relativa y Radiación:</b> Calcula los promedios "
            "del período seleccionado.<br><br>"
            "<b>Área de Interés (AOI):</b><br>"
            "• <b>Opción 1:</b> Seleccionar una capa vectorial (polígono). Se aplica automáticamente "
            "un buffer de 5 000 m y recorte en rectángulo (bounds).<br>"
            "• <b>Opción 2:</b> Dibujar recuadro en el canvas de QGIS.<br><br>"
            "<b>Descarga Segura:</b><br>"
            "Las descargas locales dividen automáticamente el área en cuadrículas para "
            "evitar límites de memoria y solicitudes en GEE."
        )

    def initAlgorithm(self, config=None):
        # AOI
        self.addParameter(
            QgsProcessingParameterMapLayer(
                self.INPUT_LAYER,
                self.tr('Opción 1: Seleccionar Capa Vectorial (Buffer 5 000 m aplicado)'),
                [QgsProcessing.SourceType.TypeVectorPolygon],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterExtent(
                self.INPUT_EXTENT,
                self.tr('Opción 2: Dibujar Recuadro en el Canvas'),
                optional=True,
            )
        )

        # CRS
        self.addParameter(
            QgsProcessingParameterCrs(
                self.EPSG,
                self.tr('CRS de Destino'),
                defaultValue='EPSG:32718',
            )
        )

        # Producto
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PRODUCT,
                self.tr('Producto Climático'),
                options=self.product_options,
                defaultValue=0,
            )
        )

        # Filtro Temporal
        self.addParameter(
            QgsProcessingParameterEnum(
                self.YEAR_MODE,
                self.tr('Modo de Tiempo'),
                options=self.year_modes,
                defaultValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.YEAR_SINGLE,
                self.tr('Año Individual (si Modo = Año Individual)'),
                type=QgsProcessingParameterNumber.Type.Integer,
                defaultValue=2020,
                minValue=1950,
                maxValue=2025,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.YEAR_START,
                self.tr('Año Inicial (si Modo = Rango)'),
                type=QgsProcessingParameterNumber.Type.Integer,
                defaultValue=2000,
                minValue=1950,
                maxValue=2025,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.YEAR_END,
                self.tr('Año Final (si Modo = Rango)'),
                type=QgsProcessingParameterNumber.Type.Integer,
                defaultValue=2020,
                minValue=1950,
                maxValue=2025,
            )
        )

        # Variables a Descargar
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.VAR_PREC,
                self.tr('Precipitación Anual Acumulada (mm/año)'),
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.VAR_TMIN,
                self.tr('Temperatura Mínima Media (°C)'),
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.VAR_TMAX,
                self.tr('Temperatura Máxima Media (°C)'),
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.VAR_TMEAN,
                self.tr('Temperatura Media (°C)'),
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.VAR_WIND,
                self.tr('Velocidad del Viento Media (m/s)'),
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.VAR_RH,
                self.tr('Humedad Relativa Media (%)'),
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.VAR_SOLAR,
                self.tr('Radiación Solar Media (MJ/m²/día)'),
                defaultValue=False,
            )
        )

        # Opciones de Exportación
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

    def processAlgorithm(self, parameters, context, feedback):
        try:
            import ee
        except ImportError:
            raise QgsProcessingException(
                "El paquete earthengine-api no está instalado. "
                "Ejecute 'Instalación dependencias GEE'."
            )
        ensure_gee_initialized(feedback)

        crs_obj       = self.parameterAsCrs(parameters, self.EPSG, context)
        crs           = crs_obj.authid()
        prod_idx      = self.parameterAsEnum(parameters, self.PRODUCT, context)
        year_mode     = self.parameterAsEnum(parameters, self.YEAR_MODE, context)
        year_single   = self.parameterAsInt(parameters, self.YEAR_SINGLE, context)
        year_start    = self.parameterAsInt(parameters, self.YEAR_START, context)
        year_end      = self.parameterAsInt(parameters, self.YEAR_END, context)
        export_method = self.parameterAsEnum(parameters, self.EXPORT_METHOD, context)
        out_folder    = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        bucket        = self.parameterAsString(parameters, self.GCS_BUCKET, context)

        prod_item = CLIMATIC_PRODUCTS[prod_idx]
        prod_id = prod_item["id"]

        if year_mode == 0:
            y_start = year_single
            y_end = year_single
        else:
            y_start = min(year_start, year_end)
            y_end = max(year_start, year_end)

        # Variables seleccionadas
        selected_vars = []
        var_flags = {
            "prec": self.parameterAsBoolean(parameters, self.VAR_PREC, context),
            "tmin": self.parameterAsBoolean(parameters, self.VAR_TMIN, context),
            "tmax": self.parameterAsBoolean(parameters, self.VAR_TMAX, context),
            "tmean": self.parameterAsBoolean(parameters, self.VAR_TMEAN, context),
            "wind": self.parameterAsBoolean(parameters, self.VAR_WIND, context),
            "rh": self.parameterAsBoolean(parameters, self.VAR_RH, context),
            "solar": self.parameterAsBoolean(parameters, self.VAR_SOLAR, context),
        }

        # Filtrar solo las variables que el producto realmente soporta
        supported_var_ids = [v["id"] for v in prod_item["vars"]]
        for vid, is_checked in var_flags.items():
            if is_checked and vid in supported_var_ids:
                selected_vars.append(vid)

        if not selected_vars:
            raise QgsProcessingException(
                f"El producto '{prod_item['name']}' no tiene ninguna variable seleccionada compatible.\n"
                f"Variables soportadas por este producto: {', '.join(supported_var_ids)}"
            )

        feedback.pushInfo(f"Producto: {prod_item['name']}")
        feedback.pushInfo(f"Período: {y_start} - {y_end}")
        feedback.pushInfo(f"Variables seleccionadas: {', '.join(selected_vars)}")
        feedback.pushInfo(f"Resolución nativa: {prod_item['res_text']}")

        # ── AOI ───────────────────────────────────────────────────────
        feedback.pushInfo("Construyendo Área de Interés (AOI)...")
        aoi_result = build_aoi(self, parameters, context, feedback,
                               layer_param=self.INPUT_LAYER,
                               extent_param=self.INPUT_EXTENT)

        geom_simple  = safe_simplify(aoi_result.geom_4326)
        geom_ee_4326 = to_ee_geometry(geom_simple, ee)

        # Aplicar buffer de 5 000 m y bounds
        feedback.pushInfo("Aplicando buffer de 5 000 m y calculando bounds...")
        aoi_ee = geom_ee_4326.buffer(5000).bounds()

        # Construcción de imágenes en GEE
        feedback.pushInfo("Construyendo grafo de cómputo en Google Earth Engine...")
        climate_images = build_climate_images(
            ee=ee,
            product_id=prod_id,
            year_start=y_start,
            year_end=y_end,
            selected_var_ids=selected_vars,
            aoi_ee=aoi_ee
        )

        res_scale = prod_item.get("res_m", 1000)

        # ── Exportación ───────────────────────────────────────────────
        if export_method == 0:  # Descarga directa local
            os.makedirs(out_folder, exist_ok=True)

            n_cells = estimate_tile_count(ee, aoi_ee, scale_m=res_scale)
            announce_grid_plan(feedback, n_cells, n_variables=len(climate_images))

            for var_id, item in climate_images.items():
                if feedback.isCanceled():
                    break
                feedback.pushInfo(f"  Descargando {item['label']} -> {item['filename']} ...")
                img_clipped = item["image"].clip(aoi_ee).reproject(crs=crs, scale=res_scale)
                filepath = os.path.join(out_folder, item["filename"])
                download_band_local(
                    ee=ee,
                    imagen=img_clipped,
                    banda=item["out_name"],
                    aoi_ee=aoi_ee,
                    crs=crs,
                    scale=res_scale,
                    out_folder=out_folder,
                    filepath=filepath,
                    feedback=feedback,
                )

        elif export_method == 1:  # Google Drive
            folder_name = os.path.basename(out_folder.rstrip('/\\')) or "Climaticos_GEE"
            task_ids = []
            for var_id, item in climate_images.items():
                if feedback.isCanceled():
                    break
                feedback.pushInfo(f"  Enviando tarea Drive para {item['label']}...")
                img_clipped = item["image"].clip(aoi_ee).reproject(crs=crs, scale=res_scale)
                task = ee.batch.Export.image.toDrive(
                    image          = img_clipped,
                    description    = item["filename"].replace(".tif", ""),
                    folder         = folder_name,
                    fileNamePrefix = item["filename"].replace(".tif", ""),
                    region         = aoi_ee,
                    scale          = res_scale,
                    crs            = crs,
                    maxPixels      = int(1e13),
                    fileFormat     = 'GeoTIFF',
                )
                task.start()
                task_ids.append(task.status().get('id', 'desconocido'))
            feedback.pushInfo(f"Tareas Drive iniciadas ({len(task_ids)}): {task_ids}")
            feedback.pushInfo("Revisa el progreso en https://code.earthengine.google.com/tasks")

        elif export_method == 2:  # GCS
            if not bucket:
                raise QgsProcessingException("Debe especificar un nombre de Bucket para exportar a GCS.")
            prefix = os.path.basename(out_folder.rstrip('/\\')) or "Climaticos_GEE"
            task_ids = []
            for var_id, item in climate_images.items():
                if feedback.isCanceled():
                    break
                feedback.pushInfo(f"  Enviando tarea GCS para {item['label']}...")
                img_clipped = item["image"].clip(aoi_ee).reproject(crs=crs, scale=res_scale)
                task = ee.batch.Export.image.toCloudStorage(
                    image          = img_clipped,
                    description    = item["filename"].replace(".tif", ""),
                    bucket         = bucket,
                    fileNamePrefix = f"{prefix}/{item['filename'].replace('.tif', '')}",
                    region         = aoi_ee,
                    scale          = res_scale,
                    crs            = crs,
                    maxPixels      = int(1e13),
                    fileFormat     = 'GeoTIFF',
                )
                task.start()
                task_ids.append(task.status().get('id', 'desconocido'))
            feedback.pushInfo(f"Tareas GCS iniciadas ({len(task_ids)}): {task_ids}")
            feedback.pushInfo("Revisa el progreso en https://code.earthengine.google.com/tasks")

        return {}
