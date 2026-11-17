"""
maxent_analysis_dialog.py — Diálogo "Analizar Resultados MaxEnt".

Mismo enfoque que species_download_dialog.py / climate_tiles_dialog.py:
QDialog 100% Python (sin .ui), worker en QThread aparte, cierre
diferido si hay un batch en curso.

Decisiones acordadas con el usuario:
- Se procesan TODAS las especies del CSV (una subcarpeta por especie),
  no solo la de mayor Test AUC global.
- El criterio de threshold logístico es elegible (11 opciones).
- Archivos opcionales faltantes (jackknife, tabla de variables) se
  registran como advertencia y no detienen el batch.
"""

import os

from qgis.core import QgsProject
from qgis.gui import QgsProjectionSelectionWidget
from qgis.PyQt.QtCore import Qt, QCoreApplication
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from . import catalog
from .worker import MaxentAnalysisWorker


class MaxentAnalysisDialog(QDialog):
    """Diálogo para analizar resultados de MaxEnt (todas las especies de un maxentResults.csv)."""

    def __init__(self, iface=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._worker = None
        self._close_requested = False
        self._last_output_folder = ""

        self.setWindowTitle(self.tr("Analizar Resultados MaxEnt"))
        self.resize(620, 600)
        self.setMinimumSize(520, 520)

        self._build_ui()
        self._connect_signals()

    def tr(self, message):
        return QCoreApplication.translate("MaxentAnalysisDialog", message)

    # ------------------------------------------------------------------
    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(self._build_group_input())
        main_layout.addWidget(self._build_group_params())
        main_layout.addWidget(self._build_group_output())
        main_layout.addWidget(self._build_group_status())
        main_layout.addLayout(self._build_bottom_buttons())

    def _build_group_input(self):
        group = QGroupBox(self.tr("Carpeta de resultados de MaxEnt"))
        layout = QGridLayout(group)

        layout.addWidget(QLabel(self.tr("Carpeta (contiene maxentResults.csv, .asc, .html, plots/)")), 0, 0)
        row = QHBoxLayout()
        self.txtInputFolder = QLineEdit()
        self.txtInputFolder.setObjectName("txtInputFolder")
        self.btnBrowseInput = QPushButton(self.tr("..."))
        self.btnBrowseInput.setObjectName("btnBrowseInput")
        row.addWidget(self.txtInputFolder)
        row.addWidget(self.btnBrowseInput)
        layout.addLayout(row, 1, 0)

        self.lblCsvStatus = QLabel("")
        self.lblCsvStatus.setObjectName("lblCsvStatus")
        self.lblCsvStatus.setWordWrap(True)
        self.lblCsvStatus.setStyleSheet("color: #555555; font-size: 9pt;")
        layout.addWidget(self.lblCsvStatus, 2, 0)

        return group

    def _build_group_params(self):
        group = QGroupBox(self.tr("Parámetros"))
        layout = QGridLayout(group)

        # Criterio de threshold
        layout.addWidget(QLabel(self.tr("Criterio de threshold logístico")), 0, 0)
        self.cmbThreshold = QComboBox()
        self.cmbThreshold.setObjectName("cmbThreshold")
        self.cmbThreshold.addItems(catalog.THRESHOLD_CRITERIA)
        default_idx = self.cmbThreshold.findText(catalog.DEFAULT_THRESHOLD_CRITERION)
        if default_idx >= 0:
            self.cmbThreshold.setCurrentIndex(default_idx)
        layout.addWidget(self.cmbThreshold, 0, 1)

        # Selector de CRS de salida (por defecto el del proyecto QGIS)
        layout.addWidget(QLabel(self.tr("SRC de salida (Proyección)")), 1, 0)
        self.crsSelector = QgsProjectionSelectionWidget()
        self.crsSelector.setObjectName("crsSelector")
        proj_crs = QgsProject.instance().crs()
        if proj_crs.isValid():
            self.crsSelector.setCrs(proj_crs)
        layout.addWidget(self.crsSelector, 1, 1)

        # Checkbox para cargar capas en QGIS
        self.chkLoadLayers = QCheckBox(self.tr("Cargar capas en QGIS al finalizar con simbología"))
        self.chkLoadLayers.setObjectName("chkLoadLayers")
        self.chkLoadLayers.setChecked(True)
        layout.addWidget(self.chkLoadLayers, 2, 0, 1, 2)

        layout.setColumnStretch(1, 1)
        return group

    def _build_group_output(self):
        group = QGroupBox(self.tr("Salida"))
        layout = QGridLayout(group)

        layout.addWidget(QLabel(self.tr("Carpeta de salida (se crea una subcarpeta por especie)")), 0, 0)
        row = QHBoxLayout()
        self.txtOutputFolder = QLineEdit()
        self.txtOutputFolder.setObjectName("txtOutputFolder")
        self.btnBrowseOutput = QPushButton(self.tr("..."))
        self.btnBrowseOutput.setObjectName("btnBrowseOutput")
        row.addWidget(self.txtOutputFolder)
        row.addWidget(self.btnBrowseOutput)
        layout.addLayout(row, 1, 0)
        return group

    def _build_group_status(self):
        group = QGroupBox(self.tr("Estado"))
        layout = QVBoxLayout(group)

        self.progressBar = QProgressBar()
        self.progressBar.setObjectName("progressBar")
        self.progressBar.setRange(0, 100)
        layout.addWidget(self.progressBar)

        self.lblStatus = QLabel(self.tr("Listo"))
        self.lblStatus.setObjectName("lblStatus")
        layout.addWidget(self.lblStatus)

        self.txtLog = QTextEdit()
        self.txtLog.setObjectName("txtLog")
        self.txtLog.setReadOnly(True)
        layout.addWidget(self.txtLog)

        return group

    def _build_bottom_buttons(self):
        row = QHBoxLayout()
        row.addStretch(1)
        self.btnRun = QPushButton(self.tr("Analizar"))
        self.btnRun.setObjectName("btnRun")
        self.btnCancel = QPushButton(self.tr("Cancelar"))
        self.btnCancel.setObjectName("btnCancel")
        self.btnCancel.setEnabled(False)
        row.addWidget(self.btnRun)
        row.addWidget(self.btnCancel)
        return row

    # ------------------------------------------------------------------
    def _connect_signals(self):
        self.btnBrowseInput.clicked.connect(self._browse_input)
        self.btnBrowseOutput.clicked.connect(self._browse_output)
        self.txtInputFolder.textChanged.connect(self._update_csv_status)
        self.btnRun.clicked.connect(self.run_analysis)
        self.btnCancel.clicked.connect(self.cancel_analysis)

    def _browse_input(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr("Selecciona la carpeta de resultados de MaxEnt"))
        if folder:
            self.txtInputFolder.setText(folder)

    def _browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr("Selecciona la carpeta de salida"))
        if folder:
            self.txtOutputFolder.setText(folder)

    def _update_csv_status(self):
        """
        Verificación rápida y local (sin abrir el CSV completo con
        pandas) de que exista algún *Results*.csv en la carpeta, para
        avisar temprano si el usuario apuntó a la carpeta equivocada.
        """
        folder = self.txtInputFolder.text().strip()
        if not folder or not os.path.isdir(folder):
            self.lblCsvStatus.setText("")
            return

        import glob
        matches = glob.glob(os.path.join(folder, "*Results*.csv"))
        if not matches:
            self.lblCsvStatus.setText(self.tr("⚠ No se encontró ningún archivo *Results*.csv en esta carpeta."))
        elif len(matches) > 1:
            names = ", ".join(os.path.basename(m) for m in matches)
            self.lblCsvStatus.setText(self.tr(f"⚠ Se encontraron varios: {names}. Se usará el primero."))
        else:
            self.lblCsvStatus.setText(self.tr(f"✓ {os.path.basename(matches[0])}"))

    # ------------------------------------------------------------------
    def run_analysis(self):
        folder = self.txtInputFolder.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, self.tr("Carpeta de entrada"), self.tr("Selecciona una carpeta de resultados de MaxEnt válida."))
            return

        import glob
        matches = glob.glob(os.path.join(folder, "*Results*.csv"))
        if not matches:
            QMessageBox.warning(self, self.tr("Sin resultados"), self.tr("No se encontró ningún archivo *Results*.csv en la carpeta indicada."))
            return
        csv_path = matches[0]

        output_folder = self.txtOutputFolder.text().strip()
        if not output_folder:
            QMessageBox.warning(self, self.tr("Carpeta de salida"), self.tr("Selecciona una carpeta de salida."))
            return
        os.makedirs(output_folder, exist_ok=True)
        self._last_output_folder = output_folder

        threshold_criterion = self.cmbThreshold.currentText()

        # CRS de destino
        target_crs = None
        if hasattr(self, 'crsSelector') and self.crsSelector.crs().isValid():
            target_crs = self.crsSelector.crs().authid()

        self.txtLog.clear()
        self._set_running(True)
        self.progressBar.setValue(0)
        self.lblStatus.setText(self.tr("Iniciando análisis..."))

        self._worker = MaxentAnalysisWorker(
            csv_path, folder, output_folder, threshold_criterion,
            target_crs=target_crs, parent=self
        )
        self._worker.sig_progress.connect(self._on_worker_progress)
        self._worker.sig_status.connect(self._on_worker_status)
        self._worker.sig_finished.connect(self._on_worker_finished)
        self._worker.sig_error.connect(self._on_worker_error)
        self._worker.finished.connect(self._on_worker_thread_finished)
        self._worker.start()

    def _on_worker_progress(self, pct, message):
        self.progressBar.setValue(pct)

    def _on_worker_status(self, message):
        self.lblStatus.setText(message)
        self.txtLog.append(message)

    def _on_worker_finished(self, results):
        ok = [r for r in results if not r.skipped]
        skipped = [r for r in results if r.skipped]
        with_warnings = [r for r in ok if r.warnings]

        self.lblStatus.setText(self.tr(f"Completado: {len(ok)} especie(s) procesadas, {len(skipped)} omitida(s)."))
        self.txtLog.append("")
        self.txtLog.append("=== RESUMEN ===")
        for r in results:
            estado = "OMITIDA" if r.skipped else ("OK (con advertencias)" if r.warnings else "OK")
            self.txtLog.append(f"{r.species_base}: {estado}")
            if r.error:
                self.txtLog.append(f"  Error: {r.error}")
            for w in r.warnings:
                self.txtLog.append(f"  Advertencia: {w}")

        # Cargar capas en QGIS si está activado
        if self.chkLoadLayers.isChecked() and ok and self._last_output_folder:
            try:
                from . import batch, qgis_loader
                total_loaded = 0
                for r in ok:
                    dirs = batch._species_output_dirs(self._last_output_folder, r.species_base)
                    loaded = qgis_loader.load_species_results_to_qgis(r.species_base, dirs, self.iface)
                    total_loaded += len(loaded)
                if total_loaded > 0:
                    self.txtLog.append(f"✓ {total_loaded} capa(s) cargadas en QGIS con simbología.")
            except Exception as exc:
                self.txtLog.append(f"⚠ Error cargando capas en QGIS: {exc}")

        summary = f"Procesadas correctamente: {len(ok)}\nOmitidas: {len(skipped)}\nCon advertencias: {len(with_warnings)}"
        if skipped:
            QMessageBox.warning(self, self.tr("Análisis finalizado con especies omitidas"), summary)
        else:
            QMessageBox.information(self, self.tr("Análisis completado"), summary)

    def _on_worker_error(self, message):
        self.lblStatus.setText(self.tr("Error durante el análisis."))
        QMessageBox.critical(self, self.tr("Error"), message)

    def _on_worker_thread_finished(self):
        self._set_running(False)
        self._worker = None
        self._close_requested = False

    def _set_running(self, running):
        self.btnRun.setEnabled(not running)
        self.btnCancel.setEnabled(running)

    # ------------------------------------------------------------------
    def cancel_analysis(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            self.btnCancel.setEnabled(False)
        else:
            self.lblStatus.setText(self.tr("Listo"))

    def closeEvent(self, event):
        """Nunca destruye un QThread vivo -- mismo patrón que las demás
        herramientas del plugin."""
        if self._worker is not None and self._worker.isRunning():
            if not self._close_requested:
                reply = QMessageBox.question(
                    self, self.tr("Análisis en curso"),
                    self.tr("Hay un análisis en curso. ¿Deseas cancelarlo y cerrar la ventana?"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
                self._close_requested = True
                self.lblStatus.setText(self.tr("Cancelando... la ventana se cerrará automáticamente."))
                self.btnCancel.setEnabled(False)
                self._worker.request_cancel()
                self._worker.finished.connect(self.close)
            event.ignore()
            return
        event.accept()
