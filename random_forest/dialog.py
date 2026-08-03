"""
dialog.py — Diálogo interactivo para la herramienta Random Forest Niche Modeling.
"""

import os
from qgis.PyQt.QtCore import Qt, QCoreApplication, QThread, pyqtSignal, QUrl
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QProgressBar, QMessageBox,
    QSpinBox, QDoubleSpinBox, QTabWidget, QTextBrowser, QTableWidget,
    QTableWidgetItem, QHeaderView, QSplitter, QComboBox
)
from qgis.PyQt.QtGui import QPixmap, QIcon, QDesktopServices
from qgis.gui import QgsFileWidget, QgsProjectionSelectionWidget
from qgis.core import QgsProject, QgsRasterLayer, QgsCoordinateReferenceSystem

from . import model, charts, report


class RFWorker(QThread):
    """Worker QThread para ejecutar el modelamiento Random Forest en segundo plano."""
    sig_progress = pyqtSignal(int, str)
    sig_status = pyqtSignal(str)
    sig_finished = pyqtSignal(dict)
    sig_error = pyqtSignal(str)

    def __init__(self, csv_path, raster_dir, output_dir, params_dict,
                 species_col=None, lon_col=None, lat_col=None, target_species=None, parent=None):
        super().__init__(parent)
        self.csv_path = csv_path
        self.raster_dir = raster_dir
        self.output_dir = output_dir
        self.params = params_dict
        self.species_col = species_col
        self.lon_col = lon_col
        self.lat_col = lat_col
        self.target_species = target_species

    def run(self):
        try:
            self.sig_progress.emit(5, "Leyendo registros de presencia CSV...")
            csv_data = model.parse_presence_csv(
                self.csv_path,
                species_col=self.species_col,
                lon_col=self.lon_col,
                lat_col=self.lat_col,
                target_species=self.target_species
            )
            species_name = csv_data["selected_species"]
            coords = csv_data["coords"]

            self.sig_progress.emit(20, "Cargando rasters ambientales ASCII...")
            raster_info = model.load_rasters(self.raster_dir)

            self.sig_progress.emit(35, "Extrayendo valores en puntos de presencia...")
            X_pres, valid_coords = model.extract_presence_values(coords, raster_info)

            self.sig_progress.emit(45, "Generando pseudo-ausencias aleatorias...")
            n_abs = self.params.get("pseudo_absences", 1000)
            X_abs = model.sample_pseudo_absences(raster_info, count=n_abs, random_state=self.params.get("random_state", 42))

            self.sig_progress.emit(60, "Entrenando modelo Random Forest y evaluando métricas...")
            eval_results = model.train_and_evaluate_rf(
                X_pres, X_abs, raster_info["names"],
                n_estimators=self.params.get("n_estimators", 100),
                max_depth=self.params.get("max_depth"),
                min_samples_leaf=self.params.get("min_samples_leaf", 1),
                test_size=self.params.get("test_size", 25),
                random_state=self.params.get("random_state", 42)
            )

            self.sig_progress.emit(75, "Generando mapa espacial de idoneidad...")
            raster_paths = model.predict_spatial_raster(
                eval_results, raster_info, self.output_dir, species_name=species_name
            )

            self.sig_progress.emit(88, "Generando gráficos y reporte de validación...")
            roc_path = os.path.join(self.output_dir, "grafico_curva_roc.png")
            imp_path = os.path.join(self.output_dir, "grafico_importancia_variables.png")

            charts.plot_roc_curve(eval_results["fpr"], eval_results["tpr"], eval_results["auc"], roc_path)
            charts.plot_feature_importance(eval_results["var_importances"], imp_path)

            html_report = report.generate_html_report(
                eval_results, self.params, species_name,
                roc_chart_rel="grafico_curva_roc.png",
                imp_chart_rel="grafico_importancia_variables.png",
                output_dir=self.output_dir
            )

            self.sig_progress.emit(100, "Completado exitosamente")

            res_payload = {
                "species_name": species_name,
                "eval_results": eval_results,
                "raster_paths": raster_paths,
                "roc_path": roc_path,
                "imp_path": imp_path,
                "html_report": html_report,
                "output_dir": self.output_dir
            }
            self.sig_finished.emit(res_payload)

        except Exception as e:
            self.sig_error.emit(str(e))


class RandomForestDialog(QDialog):
    """Diálogo principal para la herramienta Random Forest Niche Modeling."""

    def __init__(self, iface=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._worker = None
        self.last_results = None

        self.setWindowTitle(self.tr("GeoSpecies Tools — Random Forest"))
        self.resize(750, 720)
        self.setMinimumSize(620, 600)

        self._build_ui()

    def tr(self, message):
        return QCoreApplication.translate("RandomForestDialog", message)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)

        # ── Pestañas Principales (Configuración / Resultados) ──────────
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Pestaña 1: Configuración
        tab_config = QDialog(self)
        layout_config = QVBoxLayout(tab_config)

        layout_config.addWidget(self._build_group_inputs())
        layout_config.addWidget(self._build_group_hyperparams())
        layout_config.addWidget(self._build_group_status())

        # Botones de ejecución
        btn_layout = QHBoxLayout()
        self.btnRun = QPushButton(self.tr("🚀 Ejecutar Random Forest"))
        self.btnRun.setStyleSheet("font-weight: bold; background-color: #2E7D32; color: white; padding: 8px 16px; font-size: 11pt; border-radius: 4px;")
        self.btnRun.clicked.connect(self._start_processing)

        self.btnClose = QPushButton(self.tr("Cerrar"))
        self.btnClose.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btnClose)
        btn_layout.addWidget(self.btnRun)
        layout_config.addLayout(btn_layout)

        self.tabs.addTab(tab_config, self.tr("⚙️ Configuración del Modelo"))

        # Pestaña 2: Resultados y Métricas
        self.tab_results = QDialog(self)
        self.layout_results = QVBoxLayout(self.tab_results)
        self.tabs.addTab(self.tab_results, self.tr("📊 Resultados y Métricas"))
        self.tabs.setTabEnabled(1, False)  # Habilitar solo tras ejecutar

    def _build_group_inputs(self):
        group = QGroupBox(self.tr("Datos de Entrada y Selección de Campos"))
        layout = QGridLayout(group)

        # 1. CSV Presencia
        layout.addWidget(QLabel(self.tr("Tabla CSV de Presencia:")), 0, 0)
        self.fwCsv = QgsFileWidget()
        self.fwCsv.setFilter("Archivos CSV (*.csv)")
        self.fwCsv.setStorageMode(QgsFileWidget.StorageMode.GetFile)
        self.fwCsv.fileChanged.connect(self._on_csv_changed)
        layout.addWidget(self.fwCsv, 0, 1, 1, 3)

        # 2. Selección de Campos (Especie, Longitud, Latitud)
        layout.addWidget(QLabel(self.tr("Campo Especie:")), 1, 0)
        self.cmbSpeciesCol = QComboBox()
        self.cmbSpeciesCol.currentIndexChanged.connect(self._update_target_species_list)
        layout.addWidget(self.cmbSpeciesCol, 1, 1)

        layout.addWidget(QLabel(self.tr("Especie a modelar:")), 1, 2)
        self.cmbTargetSpecies = QComboBox()
        layout.addWidget(self.cmbTargetSpecies, 1, 3)

        layout.addWidget(QLabel(self.tr("Campo Longitud / Este (X):")), 2, 0)
        self.cmbLonCol = QComboBox()
        layout.addWidget(self.cmbLonCol, 2, 1)

        layout.addWidget(QLabel(self.tr("Campo Latitud / Norte (Y):")), 2, 2)
        self.cmbLatCol = QComboBox()
        layout.addWidget(self.cmbLatCol, 2, 3)

        # 3. Directorio Rasters ASC
        layout.addWidget(QLabel(self.tr("Directorio Rasters ASCII (.asc):")), 3, 0)
        self.fwRasters = QgsFileWidget()
        self.fwRasters.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        layout.addWidget(self.fwRasters, 3, 1, 1, 3)

        # 4. Carpeta Salida
        layout.addWidget(QLabel(self.tr("Carpeta Salida Resultados:")), 4, 0)
        self.fwOutput = QgsFileWidget()
        self.fwOutput.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        layout.addWidget(self.fwOutput, 4, 1, 1, 3)

        return group

    def _on_csv_changed(self, csv_path):
        """Al seleccionar/cambiar el archivo CSV, detecta y puebla los campos disponibles."""
        if not csv_path or not os.path.exists(csv_path):
            self.cmbSpeciesCol.clear()
            self.cmbLonCol.clear()
            self.cmbLatCol.clear()
            self.cmbTargetSpecies.clear()
            return

        headers = model.get_csv_headers(csv_path)
        self.cmbSpeciesCol.clear()
        self.cmbLonCol.clear()
        self.cmbLatCol.clear()

        if not headers:
            return

        self.cmbSpeciesCol.addItems(headers)
        self.cmbLonCol.addItems(headers)
        self.cmbLatCol.addItems(headers)

        norm = [h.lower() for h in headers]

        # Auto-seleccionar Especie
        for cand in ["especie", "species", "taxa", "taxon", "nombre", "sp", "scientificname"]:
            if cand in norm:
                self.cmbSpeciesCol.setCurrentIndex(norm.index(cand))
                break

        # Auto-seleccionar Longitud / Este (X)
        for cand in ["este", "easting", "lon", "long", "longitude", "log", "x", "decimallongitude"]:
            if cand in norm:
                self.cmbLonCol.setCurrentIndex(norm.index(cand))
                break

        # Auto-seleccionar Latitud / Norte (Y)
        for cand in ["norte", "northing", "lat", "latitude", "latitud", "y", "decimallatitude"]:
            if cand in norm:
                self.cmbLatCol.setCurrentIndex(norm.index(cand))
                break

        self._update_target_species_list()

    def _update_target_species_list(self):
        """Puebla el ComboBox de especies únicas según la columna seleccionada."""
        csv_path = self.fwCsv.filePath()
        sp_col_name = self.cmbSpeciesCol.currentText()
        self.cmbTargetSpecies.clear()

        if not csv_path or not os.path.exists(csv_path):
            return

        species_list = model.get_unique_species(csv_path, sp_col_name)
        if species_list:
            self.cmbTargetSpecies.addItems(species_list)

    def _build_group_hyperparams(self):
        group = QGroupBox(self.tr("Criterios e Hiperparámetros de Random Forest"))
        layout = QGridLayout(group)

        # n_estimators
        layout.addWidget(QLabel(self.tr("Número de Árboles (n_estimators):")), 0, 0)
        self.spNTree = QSpinBox()
        self.spNTree.setRange(10, 2000)
        self.spNTree.setValue(100)
        self.spNTree.setSingleStep(50)
        layout.addWidget(self.spNTree, 0, 1)

        # max_depth
        layout.addWidget(QLabel(self.tr("Profundidad Máxima (max_depth):")), 0, 2)
        self.spMaxDepth = QSpinBox()
        self.spMaxDepth.setRange(0, 100)
        self.spMaxDepth.setValue(0)
        self.spMaxDepth.setSpecialValueText(self.tr("Sin límite"))
        layout.addWidget(self.spMaxDepth, 0, 3)

        # min_samples_leaf
        layout.addWidget(QLabel(self.tr("Mínimo Muestras por Hoja:")), 1, 0)
        self.spMinLeaf = QSpinBox()
        self.spMinLeaf.setRange(1, 50)
        self.spMinLeaf.setValue(1)
        layout.addWidget(self.spMinLeaf, 1, 1)

        # pseudo_absences
        layout.addWidget(QLabel(self.tr("Puntos de Pseudo-ausencia:")), 1, 2)
        self.spPseudoAbs = QSpinBox()
        self.spPseudoAbs.setRange(50, 50000)
        self.spPseudoAbs.setValue(1000)
        self.spPseudoAbs.setSingleStep(500)
        layout.addWidget(self.spPseudoAbs, 1, 3)

        # test_size
        layout.addWidget(QLabel(self.tr("Porcentaje de Validación (% Test):")), 2, 0)
        self.spTestPercent = QSpinBox()
        self.spTestPercent.setRange(10, 50)
        self.spTestPercent.setValue(25)
        self.spTestPercent.setSuffix("%")
        layout.addWidget(self.spTestPercent, 2, 1)

        # random_state
        layout.addWidget(QLabel(self.tr("Semilla Aleatoria (Seed):")), 2, 2)
        self.spSeed = QSpinBox()
        self.spSeed.setRange(0, 999999)
        self.spSeed.setValue(42)
        layout.addWidget(self.spSeed, 2, 3)

        return group

    def _build_group_status(self):
        group = QGroupBox(self.tr("Estado del Procesamiento"))
        layout = QVBoxLayout(group)

        self.lblStatus = QLabel(self.tr("Listo para ejecutar."))
        layout.addWidget(self.lblStatus)

        self.progressBar = QProgressBar()
        self.progressBar.setValue(0)
        layout.addWidget(self.progressBar)

        return group

    def _start_processing(self):
        csv_path = self.fwCsv.filePath()
        raster_dir = self.fwRasters.filePath()
        output_dir = self.fwOutput.filePath()

        if not csv_path or not os.path.isfile(csv_path):
            QMessageBox.warning(self, self.tr("Atención"), self.tr("Selecciona un archivo CSV de presencia válido."))
            return

        if not raster_dir or not os.path.isdir(raster_dir):
            QMessageBox.warning(self, self.tr("Atención"), self.tr("Selecciona un directorio válido de rasters ASCII."))
            return

        if not output_dir:
            QMessageBox.warning(self, self.tr("Atención"), self.tr("Selecciona una carpeta de salida válida."))
            return

        species_col = self.cmbSpeciesCol.currentText()
        lon_col = self.cmbLonCol.currentText()
        lat_col = self.cmbLatCol.currentText()
        target_species = self.cmbTargetSpecies.currentText()

        params_dict = {
            "n_estimators": self.spNTree.value(),
            "max_depth": None if self.spMaxDepth.value() == 0 else self.spMaxDepth.value(),
            "min_samples_leaf": self.spMinLeaf.value(),
            "pseudo_absences": self.spPseudoAbs.value(),
            "test_size": self.spTestPercent.value(),
            "random_state": self.spSeed.value(),
        }

        self.btnRun.setEnabled(False)
        self.progressBar.setValue(0)
        self.lblStatus.setText(self.tr("Iniciando Random Forest..."))

        self._worker = RFWorker(
            csv_path, raster_dir, output_dir, params_dict,
            species_col=species_col, lon_col=lon_col, lat_col=lat_col,
            target_species=target_species, parent=self
        )
        self._worker.sig_progress.connect(self._on_progress)
        self._worker.sig_status.connect(self._on_status)
        self._worker.sig_finished.connect(self._on_finished)
        self._worker.sig_error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, val, msg):
        self.progressBar.setValue(val)
        if msg:
            self.lblStatus.setText(msg)

    def _on_status(self, msg):
        self.lblStatus.setText(msg)

    def _on_error(self, err):
        self.btnRun.setEnabled(True)
        self.lblStatus.setText(self.tr("Error en la ejecución."))
        QMessageBox.critical(self, self.tr("Error Random Forest"), f"Ocurrió un error durante el procesamiento:\n\n{err}")

    def _on_finished(self, results):
        self.btnRun.setEnabled(True)
        self.last_results = results
        self.lblStatus.setText(self.tr("Procesamiento completado con éxito."))

        self._populate_results_tab(results)
        self.tabs.setTabEnabled(1, True)
        self.tabs.setCurrentIndex(1)

        QMessageBox.information(
            self, self.tr("Éxito"),
            self.tr(
                f"Modelamiento Random Forest para '{results['species_name']}' finalizado exitosamente.\n\n"
                f"Métricas obtenidas:\n"
                f" • AUC - ROC: {results['eval_results']['auc']:.4f}\n"
                f" • Exactitud: {results['eval_results']['accuracy']*100:.1f}%\n"
                f" • Sensibilidad: {results['eval_results']['sensitivity']*100:.1f}%\n\n"
                "El raster de predicción y los gráficos fueron guardados."
            )
        )

    def _populate_results_tab(self, results):
        while self.layout_results.count():
            item = self.layout_results.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        eval_res = results["eval_results"]
        cm = eval_res["confusion_matrix"]

        top_btn_layout = QHBoxLayout()
        btn_open_report = QPushButton(self.tr("🌐 Abrir Reporte HTML Completo"))
        btn_open_report.setStyleSheet("background-color: #1E73B8; color: white; font-weight: bold; padding: 6px 12px;")
        btn_open_report.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(results["html_report"])))

        btn_load_raster = QPushButton(self.tr("🗺️ Cargar Raster en QGIS"))
        btn_load_raster.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 6px 12px;")
        btn_load_raster.clicked.connect(self._load_raster_into_qgis)

        top_btn_layout.addWidget(btn_open_report)
        top_btn_layout.addWidget(btn_load_raster)
        top_btn_layout.addStretch()
        self.layout_results.addLayout(top_btn_layout)

        splitter = QSplitter(Qt.Orientation.Vertical)

        table_group = QGroupBox(self.tr("Resumen de Métricas de Validación"))
        grid_table = QGridLayout(table_group)

        tbl_metrics = QTableWidget(5, 2)
        tbl_metrics.setHorizontalHeaderLabels([self.tr("Métrica"), self.tr("Valor")])
        tbl_metrics.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        metrics_list = [
            ("AUC - ROC", f"{eval_res['auc']:.4f}"),
            ("Exactitud (Accuracy)", f"{eval_res['accuracy']*100:.2f}%"),
            ("Sensibilidad (Recall)", f"{eval_res['sensitivity']*100:.2f}%"),
            ("Especificidad", f"{eval_res['specificity']*100:.2f}%"),
            ("F1 - Score", f"{eval_res['f1_score']:.4f}")
        ]
        for row, (k, v) in enumerate(metrics_list):
            tbl_metrics.setItem(row, 0, QTableWidgetItem(k))
            tbl_metrics.setItem(row, 1, QTableWidgetItem(v))

        tbl_cm = QTableWidget(2, 2)
        tbl_cm.setHorizontalHeaderLabels([self.tr("Real Presencia (1)"), self.tr("Real Ausencia (0)")])
        tbl_cm.setVerticalHeaderLabels([self.tr("Pred. Presencia (1)"), self.tr("Pred. Ausencia (0)")])
        tbl_cm.setItem(0, 0, QTableWidgetItem(f"TP: {cm['tp']}"))
        tbl_cm.setItem(0, 1, QTableWidgetItem(f"FP: {cm['fp']}"))
        tbl_cm.setItem(1, 0, QTableWidgetItem(f"FN: {cm['fn']}"))
        tbl_cm.setItem(1, 1, QTableWidgetItem(f"TN: {cm['tn']}"))

        grid_table.addWidget(QLabel("<b>Métricas Globales</b>"), 0, 0)
        grid_table.addWidget(tbl_metrics, 1, 0)
        grid_table.addWidget(QLabel("<b>Matriz de Confusión</b>"), 0, 1)
        grid_table.addWidget(tbl_cm, 1, 1)

        splitter.addWidget(table_group)

        charts_tab = QTabWidget()

        if os.path.exists(results["roc_path"]):
            lbl_roc = QLabel()
            pix = QPixmap(results["roc_path"])
            lbl_roc.setPixmap(pix.scaled(600, 450, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            lbl_roc.setAlignment(Qt.AlignmentFlag.AlignCenter)
            charts_tab.addTab(lbl_roc, self.tr("📈 Curva ROC"))

        if os.path.exists(results["imp_path"]):
            lbl_imp = QLabel()
            pix = QPixmap(results["imp_path"])
            lbl_imp.setPixmap(pix.scaled(600, 450, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            lbl_imp.setAlignment(Qt.AlignmentFlag.AlignCenter)
            charts_tab.addTab(lbl_imp, self.tr("🌿 Importancia de Variables"))

        splitter.addWidget(charts_tab)
        self.layout_results.addWidget(splitter)

    def _load_raster_into_qgis(self):
        if not self.last_results or "raster_paths" not in self.last_results:
            return

        tif_path = self.last_results["raster_paths"]["tif_path"]
        if not os.path.exists(tif_path):
            QMessageBox.warning(self, self.tr("Error"), self.tr("No se encontró el archivo raster predicho."))
            return

        layer_name = f"RF_Prediccion_{self.last_results['species_name']}"
        layer = QgsRasterLayer(tif_path, layer_name)
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            QMessageBox.information(self, self.tr("Cargado"), self.tr(f"La capa '{layer_name}' se cargó correctamente en QGIS."))
        else:
            QMessageBox.critical(self, self.tr("Error"), self.tr("La capa raster no pudo cargarse en QGIS."))
