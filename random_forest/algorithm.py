"""
algorithm.py — Integración de Random Forest Niche Modeling en la Caja de Herramientas de Procesamiento de QGIS.
"""

import os
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFile,
    QgsProcessingParameterString,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
    QgsProcessingOutputRasterLayer,
    QgsProcessingException,
)
from qgis.PyQt.QtCore import QCoreApplication
from . import model, charts, report


class RandomForestAlgorithm(QgsProcessingAlgorithm):
    INPUT_CSV       = 'INPUT_CSV'
    SPECIES_COL     = 'SPECIES_COL'
    LON_COL         = 'LON_COL'
    LAT_COL         = 'LAT_COL'
    RASTERS_FOLDER  = 'RASTERS_FOLDER'
    OUT_FOLDER      = 'OUT_FOLDER'
    N_ESTIMATORS    = 'N_ESTIMATORS'
    MAX_DEPTH       = 'MAX_DEPTH'
    MIN_SAMPLES_LEAF = 'MIN_SAMPLES_LEAF'
    PSEUDO_ABSENCES = 'PSEUDO_ABSENCES'
    TEST_PERCENT    = 'TEST_PERCENT'
    RANDOM_STATE    = 'RANDOM_STATE'
    OUTPUT_RASTER   = 'OUTPUT_RASTER'

    def __init__(self):
        super().__init__()

    def tr(self, string):
        return QCoreApplication.translate('RandomForestAlgorithm', string)

    def createInstance(self):
        return RandomForestAlgorithm()

    def name(self):
        return 'random_forest'

    def displayName(self):
        return self.tr('Random Forest')

    def group(self):
        return self.tr('🤖 Machine Learning')

    def groupId(self):
        return 'machine_learning'

    def shortHelpString(self):
        return self.tr(
            "<b>Descripción:</b><br>"
            "Modela la idoneidad de hábitat / distribución potencial de especies usando el algoritmo de Machine Learning "
            "<b>Random Forest</b>.<br><br>"
            "<b>Entradas:</b><br>"
            "• <b>CSV de Presencia:</b> Archivo CSV con columnas para la especie (especie/species), latitud (lat) y longitud (lon/log).<br>"
            "• <b>Directorio Rasters ASCII:</b> Carpeta con rasters ambientales (.asc).<br><br>"
            "<b>Criterios e Hiperparámetros:</b><br>"
            "Permite personalizar n_estimators, max_depth, min_samples_leaf, pseudo-ausencias, porcentaje de validación y semilla.<br><br>"
            "<b>Salidas:</b><br>"
            "Genera el raster de predicción de probabilidad (.tif y .asc), gráficos de Curva ROC e Importancia de Variables, "
            "y un reporte completo en HTML con métricas (AUC, Exactitud, Sensibilidad, Especificidad, F1) y Matriz de Confusión."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                self.INPUT_CSV,
                self.tr('Tabla CSV de Presencia de Especie (especie, lat, lon)'),
                extension='csv'
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.SPECIES_COL,
                self.tr('Nombre de la columna de Especie (opcional, detección automática si vacía)'),
                optional=True,
                defaultValue=''
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.LON_COL,
                self.tr('Nombre de la columna de Longitud X (opcional, detección automática si vacía)'),
                optional=True,
                defaultValue=''
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.LAT_COL,
                self.tr('Nombre de la columna de Latitud Y (opcional, detección automática si vacía)'),
                optional=True,
                defaultValue=''
            )
        )

        self.addParameter(
            QgsProcessingParameterFile(
                self.RASTERS_FOLDER,
                self.tr('Directorio de Rasters Ambientales ASCII (.asc)'),
                behavior=QgsProcessingParameterFile.Behavior.Folder
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUT_FOLDER,
                self.tr('Carpeta de Salida para Resultados')
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.N_ESTIMATORS,
                self.tr('Número de Árboles (n_estimators)'),
                type=QgsProcessingParameterNumber.Type.Integer,
                defaultValue=100,
                minValue=10,
                maxValue=5000
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MAX_DEPTH,
                self.tr('Profundidad Máxima (max_depth, 0 = Sin límite)'),
                type=QgsProcessingParameterNumber.Type.Integer,
                defaultValue=0,
                minValue=0,
                maxValue=100
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MIN_SAMPLES_LEAF,
                self.tr('Mínimo Muestras por Hoja (min_samples_leaf)'),
                type=QgsProcessingParameterNumber.Type.Integer,
                defaultValue=1,
                minValue=1,
                maxValue=50
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.PSEUDO_ABSENCES,
                self.tr('Cantidad de Pseudo-ausencias'),
                type=QgsProcessingParameterNumber.Type.Integer,
                defaultValue=1000,
                minValue=50,
                maxValue=100000
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TEST_PERCENT,
                self.tr('Porcentaje de Datos para Validación (% Test)'),
                type=QgsProcessingParameterNumber.Type.Integer,
                defaultValue=25,
                minValue=10,
                maxValue=50
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.RANDOM_STATE,
                self.tr('Semilla Aleatoria (Random State)'),
                type=QgsProcessingParameterNumber.Type.Integer,
                defaultValue=42
            )
        )

        self.addOutput(
            QgsProcessingOutputRasterLayer(
                self.OUTPUT_RASTER,
                self.tr('Raster de Predicción de Idoneidad (Random Forest)')
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        csv_path = self.parameterAsString(parameters, self.INPUT_CSV, context)
        species_col = self.parameterAsString(parameters, self.SPECIES_COL, context).strip() or None
        lon_col = self.parameterAsString(parameters, self.LON_COL, context).strip() or None
        lat_col = self.parameterAsString(parameters, self.LAT_COL, context).strip() or None

        raster_dir = self.parameterAsString(parameters, self.RASTERS_FOLDER, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)

        n_estimators = self.parameterAsInt(parameters, self.N_ESTIMATORS, context)
        max_depth_val = self.parameterAsInt(parameters, self.MAX_DEPTH, context)
        max_depth = None if max_depth_val == 0 else max_depth_val
        min_samples_leaf = self.parameterAsInt(parameters, self.MIN_SAMPLES_LEAF, context)
        pseudo_absences = self.parameterAsInt(parameters, self.PSEUDO_ABSENCES, context)
        test_size = self.parameterAsInt(parameters, self.TEST_PERCENT, context)
        random_state = self.parameterAsInt(parameters, self.RANDOM_STATE, context)
        feedback.pushInfo("1. Leyendo datos de presencia desde CSV...")
        try:
            csv_data = model.parse_presence_csv(csv_path, species_col=species_col, lon_col=lon_col, lat_col=lat_col)
        except model.RFModelError as e:
            raise QgsProcessingException(str(e))

        species_name = csv_data["selected_species"]
        coords = csv_data["coords"]
        feedback.pushInfo(f"   Especie identificada: '{species_name}' ({len(coords)} registros)")

        feedback.pushInfo("2. Cargando rasters ambientales ASCII...")
        try:
            raster_info = model.load_rasters(raster_dir)
        except model.RFModelError as e:
            raise QgsProcessingException(str(e))

        feedback.pushInfo(f"   Variables cargadas ({len(raster_info['names'])}): {', '.join(raster_info['names'])}")

        feedback.pushInfo("3. Extrayendo valores en puntos de presencia y pseudo-ausencias...")
        X_pres, valid_coords = model.extract_presence_values(coords, raster_info)
        X_abs = model.sample_pseudo_absences(raster_info, count=pseudo_absences, random_state=random_state)
        feedback.pushInfo(f"   Presencias válidas en rasters: {len(X_pres)} | Pseudo-ausencias: {len(X_abs)}")

        feedback.pushInfo("4. Entrenando modelo Random Forest y calculando métricas...")
        params_dict = {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "min_samples_leaf": min_samples_leaf,
            "pseudo_absences": pseudo_absences,
            "test_size": test_size,
            "random_state": random_state
        }
        eval_results = model.train_and_evaluate_rf(
            X_pres, X_abs, raster_info["names"],
            n_estimators=n_estimators, max_depth=max_depth,
            min_samples_leaf=min_samples_leaf, test_size=test_size,
            random_state=random_state
        )

        feedback.pushInfo(f"   AUC - ROC: {eval_results['auc']:.4f}")
        feedback.pushInfo(f"   Exactitud (Accuracy): {eval_results['accuracy']*100:.2f}%")
        feedback.pushInfo(f"   Sensibilidad: {eval_results['sensitivity']*100:.2f}%")
        feedback.pushInfo(f"   Especificidad: {eval_results['specificity']*100:.2f}%")

        feedback.pushInfo("5. Generando predicción espacial y mapa de idoneidad...")
        raster_paths = model.predict_spatial_raster(
            eval_results, raster_info, out_folder, species_name=species_name
        )

        feedback.pushInfo("6. Generando gráficos y reporte HTML...")
        roc_path = os.path.join(out_folder, "grafico_curva_roc.png")
        imp_path = os.path.join(out_folder, "grafico_importancia_variables.png")

        charts.plot_roc_curve(eval_results["fpr"], eval_results["tpr"], eval_results["auc"], roc_path)
        charts.plot_feature_importance(eval_results["var_importances"], imp_path)

        html_report = report.generate_html_report(
            eval_results, params_dict, species_name,
            roc_chart_rel="grafico_curva_roc.png",
            imp_chart_rel="grafico_importancia_variables.png",
            output_dir=out_folder
        )

        feedback.pushInfo(f"   Resultados guardados en: {out_folder}")
        feedback.pushInfo(f"   Reporte HTML: {html_report}")

        return {
            self.OUTPUT_RASTER: raster_paths["tif_path"]
        }
