"""
algorithm.py — Integración de la Selección de Variables en la Caja de
Herramientas de Procesamiento de QGIS.

Al exponerse como QgsProcessingAlgorithm, la herramienta queda disponible
para el Modelador Gráfico y para el procesamiento por lotes, además de
poder invocarse desde PyQGIS con processing.run('geospecies:seleccion_variables', ...).
"""

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import model, report

METHOD_OPTIONS = ["pearson", "spearman", "kendall"]
STRATEGY_OPTIONS = [
    "menor_correlacion_media",
    "primera_alfabetica",
    "aleatoria",
]
STRATEGY_LABELS = [
    "Menor correlación media (determinista, recomendado)",
    "Primera en orden natural (determinista)",
    "Aleatoria con semilla (como virtualspecies)",
]


class VariableSelectionAlgorithm(QgsProcessingAlgorithm):
    INPUT_FOLDER = "INPUT_FOLDER"
    OUT_FOLDER = "OUT_FOLDER"
    CUTOFF = "CUTOFF"
    METHOD = "METHOD"
    STRATEGY = "STRATEGY"
    SAMPLE_POINTS = "SAMPLE_POINTS"
    NB_POINTS = "NB_POINTS"
    SEED = "SEED"
    MAKE_CHARTS = "MAKE_CHARTS"

    def tr(self, string):
        return QCoreApplication.translate("VariableSelectionAlgorithm", string)

    def createInstance(self):
        return VariableSelectionAlgorithm()

    def name(self):
        return "seleccion_variables"

    def displayName(self):
        return self.tr("Selección de Variables (Colinealidad)")

    def group(self):
        # Debe coincidir EXACTAMENTE con el group() de uniformizar_raster.py y
        # limpieza_especies_maxent.py: Processing agrupa por el texto visible,
        # así que cualquier variación (p. ej. añadir un emoji) crearía un
        # segundo grupo separado en la caja de herramientas.
        return self.tr("Preparación de datos MaxEnt")

    def groupId(self):
        return "preparacion_maxent"

    def shortHelpString(self):
        return self.tr(
            "<b>Descripción:</b><br>"
            "Reduce un conjunto de variables ambientales ASCII (.asc) a un subconjunto "
            "no colineal, aplicando el mismo procedimiento que "
            "<code>virtualspecies::removeCollinearity()</code> de R, pero implementado "
            "íntegramente en Python/NumPy y GDAL (sin necesidad de tener R instalado).<br><br>"
            "<b>Procedimiento:</b><br>"
            "1. Lee todos los .asc del directorio y verifica que compartan la misma grilla.<br>"
            "2. Calcula la matriz de correlación (Pearson, Spearman o Kendall) sobre los "
            "píxeles válidos comunes a todas las capas.<br>"
            "3. Construye una matriz de distancias d = 1 − |r| y aplica clasificación "
            "jerárquica ascendente con enlace <i>complete</i>.<br>"
            "4. Corta el dendrograma a la altura correspondiente al umbral elegido, "
            "agrupando las variables cuya correlación lo supera.<br>"
            "5. Elige una variable representativa por grupo y exporta esas capas en "
            "formato ASCII a la subcarpeta <b>Select_variable</b>.<br><br>"
            "<b>Salidas:</b> rásters .asc seleccionados, dendrograma y mapa de calor (PNG), "
            "matriz de correlación y tabla de clusters (CSV), y reporte HTML.<br><br>"
            "<b>Nota sobre el umbral:</b> 0.8 es una convención habitual en modelamiento de "
            "nicho, no un valor óptimo universal. Conviene revisar la tabla de clusters y, "
            "cuando dos variables de un mismo grupo sean estadísticamente equivalentes, "
            "conservar la que tenga sentido ecológico para la especie modelada."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            self.INPUT_FOLDER,
            self.tr("Directorio de rásters ASCII (.asc)"),
            behavior=QgsProcessingParameterFile.Behavior.Folder,
        ))

        self.addParameter(QgsProcessingParameterEnum(
            self.METHOD,
            self.tr("Método de correlación"),
            options=METHOD_OPTIONS,
            defaultValue=0,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.CUTOFF,
            self.tr("Umbral de correlación |r| (agrupa por encima de este valor)"),
            type=QgsProcessingParameterNumber.Type.Double,
            defaultValue=0.8, minValue=0.01, maxValue=0.99,
        ))

        self.addParameter(QgsProcessingParameterEnum(
            self.STRATEGY,
            self.tr("Criterio de elección dentro de cada grupo"),
            options=STRATEGY_LABELS,
            defaultValue=0,
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.SAMPLE_POINTS,
            self.tr("Submuestrear píxeles (recomendado para rásters grandes)"),
            defaultValue=True,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.NB_POINTS,
            self.tr("Número de píxeles de la submuestra"),
            type=QgsProcessingParameterNumber.Type.Integer,
            defaultValue=10000, minValue=100, maxValue=1000000,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.SEED,
            self.tr("Semilla aleatoria"),
            type=QgsProcessingParameterNumber.Type.Integer,
            defaultValue=42, minValue=0, maxValue=999999,
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.MAKE_CHARTS,
            self.tr("Generar dendrograma, mapa de calor y reporte HTML"),
            defaultValue=True,
        ))

        self.addParameter(QgsProcessingParameterFolderDestination(
            self.OUT_FOLDER,
            self.tr("Carpeta de salida"),
        ))

    def processAlgorithm(self, parameters, context, feedback):
        input_dir = self.parameterAsFile(parameters, self.INPUT_FOLDER, context)
        output_dir = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        cutoff = self.parameterAsDouble(parameters, self.CUTOFF, context)
        method = METHOD_OPTIONS[self.parameterAsEnum(parameters, self.METHOD, context)]
        strategy = STRATEGY_OPTIONS[self.parameterAsEnum(parameters, self.STRATEGY, context)]
        sample_points = self.parameterAsBool(parameters, self.SAMPLE_POINTS, context)
        nb_points = self.parameterAsInt(parameters, self.NB_POINTS, context)
        seed = self.parameterAsInt(parameters, self.SEED, context)
        make_charts = self.parameterAsBool(parameters, self.MAKE_CHARTS, context)

        def progress_cb(pct, msg):
            if feedback.isCanceled():
                return
            feedback.setProgress(pct)
            feedback.pushInfo(msg)

        try:
            results = model.run_variable_selection(
                input_dir=input_dir,
                output_dir=output_dir,
                cutoff=cutoff,
                method=method,
                strategy=strategy,
                sample_points=sample_points,
                nb_points=nb_points,
                seed=seed,
                make_charts=make_charts,
                progress_cb=progress_cb,
            )
        except model.VariableSelectionError as err:
            raise QgsProcessingException(str(err))

        html_path = ""
        if make_charts:
            try:
                html_path = report.generate_html_report(results, output_dir)
            except Exception as err:
                feedback.pushWarning(f"No se pudo generar el reporte HTML: {err}")

        feedback.pushInfo("")
        feedback.pushInfo("─" * 58)
        feedback.pushInfo(
            f"{results['n_vars']} variables → {len(results['groups_dict'])} grupos → "
            f"{len(results['selected'])} seleccionadas"
        )
        for g in sorted(results["groups_dict"]):
            miembros = results["groups_dict"][g]
            elegida = [m for m in miembros if m in set(results["selected"])]
            marca = elegida[0] if elegida else "?"
            feedback.pushInfo(f"  Grupo {g}: {', '.join(miembros)}  →  {marca}")
        feedback.pushInfo("─" * 58)
        feedback.pushInfo(f"Rásters exportados en: {results['select_dir']}")

        return {
            self.OUT_FOLDER: output_dir,
            "SELECTED_VARIABLES": results["selected"],
            "SELECT_DIR": results["select_dir"],
            "HTML_REPORT": html_path,
        }
