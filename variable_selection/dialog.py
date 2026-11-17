"""
dialog.py — Diálogo interactivo de la herramienta "Selección de Variables".

Sigue el mismo patrón que random_forest/dialog.py: un QThread hace el
trabajo pesado para no congelar la interfaz de QGIS, y los resultados se
presentan en una segunda pestaña con tablas y gráficos.
"""

import os

from qgis.core import QgsProject, QgsRasterLayer
from qgis.gui import QgsFileWidget
from qgis.PyQt.QtCore import QCoreApplication, QThread, QUrl, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QDesktopServices, QPixmap
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSpinBox, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..utils.qt_compat import edit_triggers_none
from . import model, report

METHOD_OPTIONS = ["pearson", "spearman", "kendall"]
METHOD_LABELS = [
    "Pearson (lineal)",
    "Spearman (rangos, robusto a asimetría)",
    "Kendall (rangos, tau-b)",
]
STRATEGY_OPTIONS = ["menor_correlacion_media", "primera_alfabetica", "aleatoria"]
STRATEGY_LABELS = [
    "Menor correlación media (determinista, recomendado)",
    "Primera en orden natural (determinista)",
    "Aleatoria con semilla (como virtualspecies)",
]


class VarSelWorker(QThread):
    """Ejecuta la selección de variables en segundo plano."""

    sig_progress = pyqtSignal(int, str)
    sig_finished = pyqtSignal(dict)
    sig_error = pyqtSignal(str)

    def __init__(self, params, parent=None):
        super().__init__(parent)
        self.params = params

    def run(self):
        try:
            results = model.run_variable_selection(
                input_dir=self.params["input_dir"],
                output_dir=self.params["output_dir"],
                cutoff=self.params["cutoff"],
                method=self.params["method"],
                strategy=self.params["strategy"],
                sample_points=self.params["sample_points"],
                nb_points=self.params["nb_points"],
                seed=self.params["seed"],
                make_charts=self.params["make_charts"],
                progress_cb=lambda p, m: self.sig_progress.emit(p, m),
            )
            if self.params["make_charts"]:
                try:
                    results["html_report"] = report.generate_html_report(
                        results, self.params["output_dir"]
                    )
                except Exception:
                    results["html_report"] = None
            else:
                results["html_report"] = None

            self.sig_finished.emit(results)
        except Exception as err:
            self.sig_error.emit(str(err))


class VariableSelectionDialog(QDialog):
    """Diálogo principal de la herramienta de Selección de Variables."""

    def __init__(self, iface=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._worker = None
        self.last_results = None

        self.setWindowTitle(self.tr("GeoSpecies Tools — Selección de Variables"))
        self.resize(820, 760)
        self.setMinimumSize(680, 620)

        self._build_ui()

    def tr(self, message):
        return QCoreApplication.translate("VariableSelectionDialog", message)

    # ── Construcción de la interfaz ────────────────────────────────────

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        tab_config = QWidget(self)
        layout_config = QVBoxLayout(tab_config)
        layout_config.addWidget(self._build_group_inputs())
        layout_config.addWidget(self._build_group_criteria())
        layout_config.addWidget(self._build_group_status())
        layout_config.addStretch()

        btn_layout = QHBoxLayout()
        self.btnRun = QPushButton(self.tr("🚀 Ejecutar Selección de Variables"))
        self.btnRun.setStyleSheet(
            "font-weight: bold; background-color: #2E7D32; color: white; "
            "padding: 8px 16px; font-size: 11pt; border-radius: 4px;"
        )
        self.btnRun.clicked.connect(self._start_processing)

        self.btnClose = QPushButton(self.tr("Cerrar"))
        self.btnClose.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btnClose)
        btn_layout.addWidget(self.btnRun)
        layout_config.addLayout(btn_layout)

        self.tabs.addTab(tab_config, self.tr("⚙️ Configuración"))

        self.tab_results = QWidget(self)
        self.layout_results = QVBoxLayout(self.tab_results)
        self.tabs.addTab(self.tab_results, self.tr("📊 Resultados"))
        self.tabs.setTabEnabled(1, False)

    def _build_group_inputs(self):
        group = QGroupBox(self.tr("Datos de entrada y salida"))
        layout = QGridLayout(group)

        layout.addWidget(QLabel(self.tr("Directorio de rásters ASCII (.asc):")), 0, 0)
        self.fwInput = QgsFileWidget()
        self.fwInput.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        self.fwInput.fileChanged.connect(self._on_input_changed)
        layout.addWidget(self.fwInput, 0, 1)

        self.lblDetected = QLabel(self.tr("Selecciona un directorio para detectar los .asc."))
        self.lblDetected.setStyleSheet("color: #666; font-size: 11px;")
        self.lblDetected.setWordWrap(True)
        layout.addWidget(self.lblDetected, 1, 0, 1, 2)

        layout.addWidget(QLabel(self.tr("Carpeta de salida:")), 2, 0)
        self.fwOutput = QgsFileWidget()
        self.fwOutput.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        layout.addWidget(self.fwOutput, 2, 1)

        hint = QLabel(self.tr(
            "Los rásters seleccionados se escribirán en la subcarpeta "
            "<b>Select_variable</b> de la carpeta de salida."
        ))
        hint.setStyleSheet("color: #666; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint, 3, 0, 1, 2)

        return group

    def _build_group_criteria(self):
        group = QGroupBox(self.tr("Criterios de colinealidad"))
        layout = QGridLayout(group)

        layout.addWidget(QLabel(self.tr("Método de correlación:")), 0, 0)
        self.cmbMethod = QComboBox()
        self.cmbMethod.addItems(METHOD_LABELS)
        self.cmbMethod.currentIndexChanged.connect(self._on_method_changed)
        layout.addWidget(self.cmbMethod, 0, 1)

        layout.addWidget(QLabel(self.tr("Umbral de correlación |r|:")), 0, 2)
        self.spCutoff = QDoubleSpinBox()
        self.spCutoff.setRange(0.01, 0.99)
        self.spCutoff.setSingleStep(0.05)
        self.spCutoff.setDecimals(2)
        self.spCutoff.setValue(0.80)
        self.spCutoff.setToolTip(self.tr(
            "Las variables cuya correlación absoluta supere este valor se agrupan "
            "y solo una de cada grupo se conserva."
        ))
        layout.addWidget(self.spCutoff, 0, 3)

        layout.addWidget(QLabel(self.tr("Elección dentro del grupo:")), 1, 0)
        self.cmbStrategy = QComboBox()
        self.cmbStrategy.addItems(STRATEGY_LABELS)
        layout.addWidget(self.cmbStrategy, 1, 1, 1, 3)

        self.chkSample = QCheckBox(self.tr("Submuestrear píxeles (recomendado)"))
        self.chkSample.setChecked(True)
        self.chkSample.toggled.connect(self._on_sample_toggled)
        layout.addWidget(self.chkSample, 2, 0, 1, 2)

        layout.addWidget(QLabel(self.tr("Nº de píxeles:")), 2, 2)
        self.spNbPoints = QSpinBox()
        self.spNbPoints.setRange(100, 1000000)
        self.spNbPoints.setSingleStep(1000)
        self.spNbPoints.setValue(10000)
        layout.addWidget(self.spNbPoints, 2, 3)

        layout.addWidget(QLabel(self.tr("Semilla aleatoria:")), 3, 0)
        self.spSeed = QSpinBox()
        self.spSeed.setRange(0, 999999)
        self.spSeed.setValue(42)
        layout.addWidget(self.spSeed, 3, 1)

        self.chkCharts = QCheckBox(self.tr("Generar gráficos y reporte HTML"))
        self.chkCharts.setChecked(True)
        layout.addWidget(self.chkCharts, 3, 2, 1, 2)

        self.lblMethodHint = QLabel()
        self.lblMethodHint.setStyleSheet("color: #666; font-size: 11px;")
        self.lblMethodHint.setWordWrap(True)
        layout.addWidget(self.lblMethodHint, 4, 0, 1, 4)
        self._on_method_changed(0)

        return group

    def _build_group_status(self):
        group = QGroupBox(self.tr("Estado del procesamiento"))
        layout = QVBoxLayout(group)

        self.lblStatus = QLabel(self.tr("Listo para ejecutar."))
        layout.addWidget(self.lblStatus)

        self.progressBar = QProgressBar()
        self.progressBar.setValue(0)
        layout.addWidget(self.progressBar)

        return group

    # ── Reacciones de la interfaz ──────────────────────────────────────

    def _on_input_changed(self, path):
        if not path or not os.path.isdir(path):
            self.lblDetected.setText(self.tr("Selecciona un directorio para detectar los .asc."))
            return
        try:
            from . import raster_io
            files = raster_io.list_ascii_rasters(path)
        except Exception:
            files = []

        if not files:
            self.lblDetected.setText(self.tr(
                "⚠️ No se encontraron archivos .asc en el directorio."
            ))
            self.lblDetected.setStyleSheet("color: #C62828; font-size: 11px;")
            return

        nombres = [os.path.splitext(os.path.basename(f))[0] for f in files]
        vista = ", ".join(nombres[:12]) + (" …" if len(nombres) > 12 else "")
        color = "#C62828" if len(files) < 2 else "#2E7D32"
        aviso = "" if len(files) >= 2 else self.tr("  (se requieren al menos 2)")
        self.lblDetected.setText(f"✔ {len(files)} rásters detectados{aviso}: {vista}")
        self.lblDetected.setStyleSheet(f"color: {color}; font-size: 11px;")

        if not self.fwOutput.filePath():
            self.fwOutput.setFilePath(os.path.join(path, "Seleccion_Variables"))

    def _on_method_changed(self, idx):
        hints = [
            self.tr("Pearson mide asociación lineal. Es el criterio por defecto de "
                    "virtualspecies, pero es sensible a valores extremos."),
            self.tr("Spearman trabaja sobre rangos: conviene cuando las variables están "
                    "sesgadas o tienen valores atípicos, como suele ocurrir con la precipitación."),
            self.tr("Kendall (tau-b) también trabaja sobre rangos y es más conservador que "
                    "Spearman; su cálculo es más lento con muestras grandes."),
        ]
        self.lblMethodHint.setText(hints[idx])

    def _on_sample_toggled(self, checked):
        self.spNbPoints.setEnabled(checked)

    # ── Ejecución ──────────────────────────────────────────────────────

    def _start_processing(self):
        input_dir = self.fwInput.filePath()
        output_dir = self.fwOutput.filePath()

        if not input_dir or not os.path.isdir(input_dir):
            QMessageBox.warning(self, self.tr("Atención"),
                                self.tr("Selecciona un directorio válido de rásters ASCII."))
            return
        if not output_dir:
            QMessageBox.warning(self, self.tr("Atención"),
                                self.tr("Selecciona una carpeta de salida."))
            return

        params = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "cutoff": self.spCutoff.value(),
            "method": METHOD_OPTIONS[self.cmbMethod.currentIndex()],
            "strategy": STRATEGY_OPTIONS[self.cmbStrategy.currentIndex()],
            "sample_points": self.chkSample.isChecked(),
            "nb_points": self.spNbPoints.value(),
            "seed": self.spSeed.value(),
            "make_charts": self.chkCharts.isChecked(),
        }

        self.btnRun.setEnabled(False)
        self.progressBar.setValue(0)
        self.lblStatus.setText(self.tr("Iniciando..."))

        self._worker = VarSelWorker(params, parent=self)
        self._worker.sig_progress.connect(self._on_progress)
        self._worker.sig_finished.connect(self._on_finished)
        self._worker.sig_error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, val, msg):
        self.progressBar.setValue(val)
        if msg:
            self.lblStatus.setText(msg)

    def _on_error(self, err):
        self.btnRun.setEnabled(True)
        self.lblStatus.setText(self.tr("Error en la ejecución."))
        QMessageBox.critical(
            self, self.tr("Error — Selección de Variables"),
            f"Ocurrió un error durante el procesamiento:\n\n{err}"
        )

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
                f"Selección de variables completada.\n\n"
                f" • Variables de entrada: {results['n_vars']}\n"
                f" • Grupos detectados: {len(results['groups_dict'])}\n"
                f" • Variables seleccionadas: {len(results['selected'])}\n\n"
                f"Rásters exportados en:\n{results['select_dir']}"
            )
        )

    # ── Pestaña de resultados ──────────────────────────────────────────

    def _populate_results_tab(self, results):
        while self.layout_results.count():
            item = self.layout_results.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        top_btns = QHBoxLayout()

        if results.get("html_report"):
            btn_report = QPushButton(self.tr("🌐 Abrir reporte HTML"))
            btn_report.setStyleSheet(
                "background-color: #1E73B8; color: white; font-weight: bold; padding: 6px 12px;"
            )
            btn_report.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(results["html_report"]))
            )
            top_btns.addWidget(btn_report)

        btn_load = QPushButton(self.tr("🗺️ Cargar rásters seleccionados en QGIS"))
        btn_load.setStyleSheet(
            "background-color: #2E7D32; color: white; font-weight: bold; padding: 6px 12px;"
        )
        btn_load.clicked.connect(self._load_rasters_into_qgis)
        top_btns.addWidget(btn_load)

        btn_folder = QPushButton(self.tr("📁 Abrir carpeta de salida"))
        btn_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(results["output_dir"]))
        )
        top_btns.addWidget(btn_folder)
        top_btns.addStretch()

        container = QWidget()
        cont_layout = QVBoxLayout(container)
        cont_layout.addLayout(top_btns)

        resumen = QLabel(self.tr(
            f"<b>{results['n_vars']}</b> variables → "
            f"<b>{len(results['groups_dict'])}</b> grupos → "
            f"<b>{len(results['selected'])}</b> seleccionadas &nbsp;|&nbsp; "
            f"método <b>{results['method']}</b>, umbral <b>|r| ≥ {results['cutoff']:.2f}</b>, "
            f"<b>{results['n_obs']}</b> píxeles analizados"
        ))
        resumen.setStyleSheet(
            "background:#F1F8E9; border:1px solid #C8E6C9; border-radius:6px; padding:8px;"
        )
        resumen.setWordWrap(True)
        cont_layout.addWidget(resumen)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Tabla de clusters
        table_group = QGroupBox(self.tr("Tabla de clusters"))
        tg_layout = QVBoxLayout(table_group)

        rows = results["cluster_rows"]
        tbl = QTableWidget(len(rows), 5)
        tbl.setHorizontalHeaderLabels([
            self.tr("Variable"), self.tr("Grupo"), self.tr("Tamaño"),
            self.tr("|r| máx. en grupo"), self.tr("Estado"),
        ])
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tbl.setEditTriggers(edit_triggers_none())

        for r, row in enumerate(rows):
            tbl.setItem(r, 0, QTableWidgetItem(row["variable"]))
            tbl.setItem(r, 1, QTableWidgetItem(str(row["grupo"])))
            tbl.setItem(r, 2, QTableWidgetItem(str(row["n_variables_grupo"])))
            max_r = "—" if row["n_variables_grupo"] == 1 else f"{row['max_r_dentro_grupo']:.3f}"
            tbl.setItem(r, 3, QTableWidgetItem(max_r))

            if row["seleccionada"] == "SI":
                item = QTableWidgetItem("✔ Seleccionada")
                item.setForeground(QColor("#1B5E20"))
            else:
                item = QTableWidgetItem("✘ Descartada")
                item.setForeground(QColor("#B71C1C"))
            tbl.setItem(r, 4, item)

        tg_layout.addWidget(tbl)
        splitter.addWidget(table_group)

        # Gráficos
        charts_tabs = QTabWidget()
        for path, title in (
            (results.get("dendrogram_path"), self.tr("🌳 Dendrograma")),
            (results.get("heatmap_path"), self.tr("🔢 Matriz de correlación")),
        ):
            if path and os.path.exists(path):
                scroll = QScrollArea()
                lbl = QLabel()
                lbl.setPixmap(QPixmap(path).scaled(
                    980, 620, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                scroll.setWidget(lbl)
                scroll.setWidgetResizable(True)
                charts_tabs.addTab(scroll, title)

        if charts_tabs.count():
            splitter.addWidget(charts_tabs)

        cont_layout.addWidget(splitter)
        self.layout_results.addWidget(container)

    def _load_rasters_into_qgis(self):
        if not self.last_results:
            return

        loaded = 0
        for path in self.last_results["exported_files"]:
            if not os.path.exists(path):
                continue
            name = os.path.splitext(os.path.basename(path))[0]
            layer = QgsRasterLayer(path, name)
            if layer.isValid():
                QgsProject.instance().addMapLayer(layer)
                loaded += 1

        if loaded:
            QMessageBox.information(
                self, self.tr("Cargado"),
                self.tr(f"Se cargaron {loaded} capas ráster en QGIS.")
            )
        else:
            QMessageBox.warning(
                self, self.tr("Error"),
                self.tr("No se pudo cargar ninguna capa ráster.")
            )
