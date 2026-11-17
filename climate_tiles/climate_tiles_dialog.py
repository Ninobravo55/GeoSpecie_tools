"""
climate_tiles_dialog.py — Diálogo "WorldClim 2.1 Histórico y CMIP6".

Mismo enfoque que species_download_dialog.py: QDialog 100% en Python
(sin .ui), no registrado como QgsProcessingAlgorithm (la selección
interactiva de AOI + variables dependientes del modo no encaja bien en
el modelo de parámetros de Processing), worker en QThread aparte,
cierre diferido si hay una descarga en curso.

Comportamiento de descarga:
- Si el AOI cae en un solo tile: se descarga ese tile directamente.
- Si el AOI abarca 2–4 tiles: se descargan TODOS los tiles, se
  ensambla un mosaico con gdal:merge y se recorta al polígono real.
- Resolución fija en 30 arco-segundos (los tiles solo existen a esa
  resolución).
- Lista completa de 26 modelos CMIP6 (catalog.CMIP6_MODELS).
"""

import os

from qgis.PyQt.QtCore import Qt, QCoreApplication
from qgis.PyQt.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTextBrowser,
    QVBoxLayout,
)
from qgis.gui import QgsFileWidget, QgsMapLayerComboBox, QgsProjectionSelectionWidget
from qgis.core import QgsMapLayerProxyModel, QgsProject, QgsRasterLayer

from . import batch, catalog, geodata_api, help_text, tiles
from .tiles import tiles_spanned_by_bbox
from .geom import centroid_and_bbox_from_layer, GeomError
from .utils import format_bytes, validate_name_prefix
from .worker import ClimateTileWorker


class ClimateTilesDialog(QDialog):
    """Diálogo para descargar tiles WorldClim (histórico) o CMIP6 (proyección)."""

    def __init__(self, iface=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._worker = None
        self._close_requested = False
        self._var_checkboxes = {}  # var interna -> QCheckBox, repoblado según el modo

        self.setWindowTitle(self.tr("WorldClim 2.1 Histórico y CMIP6"))
        self.resize(560, 720)
        self.setMinimumSize(480, 600)

        self._build_ui()
        self._connect_signals()
        self._on_mode_changed()
        self._on_aoi_kind_changed()

    def tr(self, message):
        return QCoreApplication.translate("ClimateTilesDialog", message)

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------
    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(self._build_group_mode())
        main_layout.addWidget(self._build_group_aoi())
        main_layout.addWidget(self._build_group_variables())
        main_layout.addWidget(self._build_group_cmip6_params())
        main_layout.addWidget(self._build_group_output())
        main_layout.addWidget(self._build_group_status())
        main_layout.addLayout(self._build_bottom_buttons())

    def _build_group_mode(self):
        group = QGroupBox(self.tr("Producto"))
        outer = QVBoxLayout(group)

        row = QHBoxLayout()
        self.rbHistorical = QRadioButton(self.tr("Histórico (WorldClim baseline)"))
        self.rbHistorical.setObjectName("rbHistorical")
        self.rbHistorical.setChecked(True)
        self.rbCmip6 = QRadioButton(self.tr("Proyección CMIP6"))
        self.rbCmip6.setObjectName("rbCmip6")

        self.mode_button_group = QButtonGroup(self)
        self.mode_button_group.addButton(self.rbHistorical)
        self.mode_button_group.addButton(self.rbCmip6)

        self.btnHelp = QPushButton(self.tr("❓ Ayuda"))
        self.btnHelp.setObjectName("btnHelp")
        self.btnHelp.setToolTip(self.tr("Qué es cada producto, resolución real de esta herramienta, y links de descarga manual"))

        row.addWidget(self.rbHistorical)
        row.addWidget(self.rbCmip6)
        row.addStretch(1)
        row.addWidget(self.btnHelp)
        outer.addLayout(row)

        self.lblModeHelp = QLabel("")
        self.lblModeHelp.setObjectName("lblModeHelp")
        self.lblModeHelp.setWordWrap(True)
        self.lblModeHelp.setTextFormat(Qt.TextFormat.RichText)
        self.lblModeHelp.setStyleSheet("color: #555555; font-size: 9pt;")
        outer.addWidget(self.lblModeHelp)

        return group

    def _build_group_aoi(self):
        group = QGroupBox(self.tr("Área de interés"))
        layout = QGridLayout(group)

        self.rbPoint = QRadioButton(self.tr("Punto (coordenadas)"))
        self.rbPoint.setObjectName("rbPoint")
        self.rbPoint.setChecked(True)
        self.rbPolygon = QRadioButton(self.tr("Polígono (capa cargada en QGIS)"))
        self.rbPolygon.setObjectName("rbPolygon")

        self.aoi_button_group = QButtonGroup(self)
        self.aoi_button_group.addButton(self.rbPoint)
        self.aoi_button_group.addButton(self.rbPolygon)

        self.spLon = QDoubleSpinBox()
        self.spLon.setObjectName("spLon")
        self.spLon.setRange(-180.0, 180.0)
        self.spLon.setDecimals(6)
        self.spLat = QDoubleSpinBox()
        self.spLat.setObjectName("spLat")
        self.spLat.setRange(-90.0, 90.0)
        self.spLat.setDecimals(6)

        self.cmbPolygonLayer = QgsMapLayerComboBox()
        self.cmbPolygonLayer.setObjectName("cmbPolygonLayer")
        self.cmbPolygonLayer.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)
        self.cmbPolygonLayer.setAllowEmptyLayer(True)

        self.lblTileWarning = QLabel("")
        self.lblTileWarning.setObjectName("lblTileWarning")
        self.lblTileWarning.setWordWrap(True)
        self.lblTileWarning.setStyleSheet("color: #1a6e9e;")  # azul informativo

        self.chkClipToPolygon = QCheckBox(self.tr("Recortar el mosaico descargado al polígono real"))
        self.chkClipToPolygon.setObjectName("chkClipToPolygon")
        self.chkClipToPolygon.setChecked(True)

        layout.addWidget(self.rbPoint, 0, 0)
        layout.addWidget(QLabel(self.tr("Lon")), 0, 1)
        layout.addWidget(self.spLon, 0, 2)
        layout.addWidget(QLabel(self.tr("Lat")), 0, 3)
        layout.addWidget(self.spLat, 0, 4)
        layout.addWidget(self.rbPolygon, 1, 0)
        layout.addWidget(self.cmbPolygonLayer, 1, 1, 1, 4)
        layout.addWidget(self.chkClipToPolygon, 2, 0, 1, 5)
        layout.addWidget(self.lblTileWarning, 3, 0, 1, 5)
        layout.setColumnStretch(4, 1)
        return group

    def _build_group_variables(self):
        self.groupVariables = QGroupBox(self.tr("Variables"))
        self.variables_layout = QGridLayout(self.groupVariables)
        return self.groupVariables

    def _build_group_cmip6_params(self):
        self.groupCmip6 = QGroupBox(self.tr("Parámetros CMIP6"))
        layout = QGridLayout(self.groupCmip6)

        self.cmbModel = QComboBox()
        self.cmbModel.setObjectName("cmbModel")
        self.cmbModel.addItems(catalog.CMIP6_MODELS)
        default_idx = self.cmbModel.findText("MIROC6")
        if default_idx >= 0:
            self.cmbModel.setCurrentIndex(default_idx)

        self.cmbSsp = QComboBox()
        self.cmbSsp.setObjectName("cmbSsp")
        for ssp in catalog.CMIP6_SSPS:
            self.cmbSsp.addItem(f"SSP{ssp}", ssp)

        self.cmbPeriod = QComboBox()
        self.cmbPeriod.setObjectName("cmbPeriod")
        for period in catalog.CMIP6_PERIODS:
            self.cmbPeriod.addItem(period, period)

        layout.addWidget(QLabel(self.tr("Modelo")), 0, 0)
        layout.addWidget(self.cmbModel, 0, 1)
        layout.addWidget(QLabel(self.tr("SSP")), 1, 0)
        layout.addWidget(self.cmbSsp, 1, 1)
        layout.addWidget(QLabel(self.tr("Periodo")), 2, 0)
        layout.addWidget(self.cmbPeriod, 2, 1)
        layout.setColumnStretch(1, 1)
        return self.groupCmip6

    def _build_group_output(self):
        group = QGroupBox(self.tr("Salida"))
        layout = QGridLayout(group)

        layout.addWidget(QLabel(self.tr("Carpeta de salida")), 0, 0)
        self.txtOutputFolder = QgsFileWidget()
        self.txtOutputFolder.setObjectName("txtOutputFolder")
        self.txtOutputFolder.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        layout.addWidget(self.txtOutputFolder, 0, 1)

        layout.addWidget(QLabel(self.tr("Prefijo de archivo")), 1, 0)
        self.txtNamePrefix = QLineEdit("clima")
        self.txtNamePrefix.setObjectName("txtNamePrefix")
        layout.addWidget(self.txtNamePrefix, 1, 1)

        layout.addWidget(QLabel(self.tr("SRC de salida (opcional)")), 2, 0)
        self.crsSelector = QgsProjectionSelectionWidget()
        self.crsSelector.setObjectName("crsSelector")
        layout.addWidget(self.crsSelector, 2, 1)

        layout.setColumnStretch(1, 1)
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
        return group

    def _build_bottom_buttons(self):
        row = QHBoxLayout()
        row.addStretch(1)
        self.btnDownload = QPushButton(self.tr("Descargar"))
        self.btnDownload.setObjectName("btnDownload")
        self.btnCancel = QPushButton(self.tr("Cancelar"))
        self.btnCancel.setObjectName("btnCancel")
        self.btnCancel.setEnabled(False)
        row.addWidget(self.btnDownload)
        row.addWidget(self.btnCancel)
        return row

    # ------------------------------------------------------------------
    # Señales
    # ------------------------------------------------------------------
    def _connect_signals(self):
        self.rbHistorical.toggled.connect(self._on_mode_changed)
        self.rbPoint.toggled.connect(self._on_aoi_kind_changed)
        self.rbPolygon.toggled.connect(self._on_aoi_kind_changed)
        self.cmbPolygonLayer.layerChanged.connect(self._update_tile_warning_preview)
        self.btnDownload.clicked.connect(self.download_tiles)
        self.btnCancel.clicked.connect(self.cancel_download)
        self.btnHelp.clicked.connect(self._show_help)

    def _show_help(self):
        """Abre el detalle completo: qué es cada producto, resolución
        real de esta herramienta (no la de la web), y links de descarga
        manual. Ver help_text.py -- redactado a partir de la
        documentación pública de WorldClim, no copiado literal."""
        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr("Ayuda — Histórico vs. Proyección CMIP6"))
        dlg.resize(560, 520)
        layout = QVBoxLayout(dlg)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(help_text.FULL_HELP_HTML)
        layout.addWidget(browser)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dlg.accept)
        layout.addWidget(buttons)

        dlg.exec()

    def _on_mode_changed(self):
        """Repuebla el checklist de variables según catalog.variables_for_mode()
        -- nunca se muestran las 9 variables juntas sin filtrar por modo."""
        mode = catalog.MODE_HISTORICAL if self.rbHistorical.isChecked() else catalog.MODE_CMIP6

        for cb in self._var_checkboxes.values():
            cb.setParent(None)
        self._var_checkboxes.clear()

        for row, (var, label) in enumerate(catalog.variables_for_mode(mode)):
            cb = QCheckBox(label)
            cb.setObjectName(f"chkVar_{var}")
            self.variables_layout.addWidget(cb, row // 2, row % 2)
            self._var_checkboxes[var] = cb

        self.groupCmip6.setEnabled(mode == catalog.MODE_CMIP6)
        self.lblModeHelp.setText(
            help_text.SHORT_CMIP6_HTML if mode == catalog.MODE_CMIP6 else help_text.SHORT_HISTORICAL_HTML
        )

    def _on_aoi_kind_changed(self):
        is_point = self.rbPoint.isChecked()
        self.spLon.setEnabled(is_point)
        self.spLat.setEnabled(is_point)
        self.cmbPolygonLayer.setEnabled(not is_point)
        self.chkClipToPolygon.setEnabled(not is_point)
        self.lblTileWarning.setText("")
        self._update_tile_warning_preview()

    def _update_tile_warning_preview(self):
        """
        Muestra de inmediato (sin red, es pura matemática local) cuántos
        tiles abarca el AOI seleccionado, para que el usuario lo sepa
        ANTES de tocar 'Descargar'. Si el AOI abarca varios tiles se
        muestra un mensaje informativo (azul) indicando que SE DESCARGARÁN
        TODOS y se ensamblará un mosaico — ya no es una advertencia.
        """
        if self.rbPoint.isChecked():
            self.lblTileWarning.setText("")
            return

        layer = self.cmbPolygonLayer.currentLayer()
        if layer is None:
            self.lblTileWarning.setText("")
            return

        try:
            _, _, bbox = centroid_and_bbox_from_layer(layer)
        except GeomError:
            self.lblTileWarning.setText("")
            return

        if bbox is None:
            self.lblTileWarning.setText("")
            return

        spanned = tiles_spanned_by_bbox(*bbox)
        n = len(spanned)
        if n <= 1:
            self.lblTileWarning.setText("")
        else:
            self.lblTileWarning.setText(
                self.tr(
                    f"ℹ️ Tu AOI abarca {n} tiles distintos (IDs: {sorted(spanned)}). "
                    "Se descargarán TODOS y se ensamblará un mosaico antes del recorte."
                )
            )

    # ------------------------------------------------------------------
    # download_tiles
    # ------------------------------------------------------------------
    def download_tiles(self):
        selected_vars = [var for var, cb in self._var_checkboxes.items() if cb.isChecked()]
        if not selected_vars:
            QMessageBox.warning(self, self.tr("Variables"), self.tr("Selecciona al menos una variable."))
            return

        mode = catalog.MODE_HISTORICAL if self.rbHistorical.isChecked() else catalog.MODE_CMIP6

        output_folder = self.txtOutputFolder.filePath()
        if not output_folder or not os.path.isdir(output_folder):
            QMessageBox.warning(self, self.tr("Carpeta de salida"), self.tr("Selecciona una carpeta de salida válida."))
            return

        name_prefix, name_error = validate_name_prefix(self.txtNamePrefix.text())
        if name_error:
            QMessageBox.warning(self, self.tr("Prefijo de archivo"), name_error)
            return

        # --- Resolver centroide, bbox y lista de tile IDs ---
        polygon_layer = None
        try:
            if self.rbPoint.isChecked():
                lon, lat, bbox = batch.resolve_centroid(lon=self.spLon.value(), lat=self.spLat.value())
            else:
                polygon_layer = self.cmbPolygonLayer.currentLayer()
                lon, lat, bbox = batch.resolve_centroid(aoi_layer=polygon_layer)
        except (batch.ClimateTileError, GeomError) as exc:
            QMessageBox.warning(self, self.tr("Área de interés"), str(exc))
            return

        try:
            tile_ids = batch.tiles_for_aoi(lon, lat, bbox)
        except tiles.TileError as exc:
            QMessageBox.warning(self, self.tr("Coordenadas inválidas"), str(exc))
            return

        # --- Armar y validar el plan de descarga (puede pegarle a la red: check_cmip6_combo) ---
        model = self.cmbModel.currentText() if mode == catalog.MODE_CMIP6 else None
        ssp = self.cmbSsp.currentData() if mode == catalog.MODE_CMIP6 else None
        period = self.cmbPeriod.currentData() if mode == catalog.MODE_CMIP6 else None

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.lblStatus.setText(self.tr("Validando disponibilidad..."))
        try:
            plan = batch.build_download_plan(mode, selected_vars, tile_ids, model=model, ssp=ssp, period=period)
        except batch.ClimateTileError as exc:
            QApplication.restoreOverrideCursor()
            self.lblStatus.setText(self.tr("Listo"))
            QMessageBox.warning(self, self.tr("Combinación no disponible"), str(exc))
            return

        # --- Confirmación previa de tamaño total (los tiles pueden pesar cientos de MB a >1 GB) ---
        self.lblStatus.setText(self.tr("Consultando tamaño de los archivos..."))
        total_size = 0
        unknown_size = False
        for item in plan:
            size = geodata_api.get_remote_file_size(item["url"])
            if size is None:
                unknown_size = True
            else:
                total_size += size
        QApplication.restoreOverrideCursor()

        size_txt = format_bytes(total_size) if total_size else "desconocido"
        if unknown_size:
            size_txt += " (al menos — algún archivo no reportó tamaño)"

        n_tiles = len(tile_ids)
        n_vars = len(selected_vars)
        n_files = len(plan)  # = n_vars × n_tiles
        tiles_info = (
            f"{n_tiles} tile(s) por variable → {n_files} archivo(s) a descargar"
            if n_tiles > 1
            else f"{n_files} archivo(s) a descargar (uno por variable)"
        )
        mosaic_info = (
            f"\nSe ensamblará un mosaico de {n_tiles} tiles por variable."
            if n_tiles > 1
            else ""
        )

        reply = QMessageBox.question(
            self, self.tr("Confirmar descarga"),
            self.tr(
                f"{tiles_info}.\n"
                f"Tamaño total estimado: {size_txt}.{mosaic_info}\n\n¿Continuar?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self.lblStatus.setText(self.tr("Listo"))
            return

        clip_mask_layer = polygon_layer if (polygon_layer is not None and self.chkClipToPolygon.isChecked()) else None
        target_crs = self.crsSelector.crs()

        target_output_folder = output_folder
        if mode == catalog.MODE_CMIP6:
            target_output_folder = batch.get_cmip6_output_folder(output_folder, model, period, ssp)

        self._set_download_in_progress(True)
        self.progressBar.setValue(0)
        self.lblStatus.setText(self.tr("Iniciando descarga..."))

        self._worker = ClimateTileWorker(
            plan=plan, output_folder=target_output_folder, name_prefix=name_prefix,
            clip_mask_layer=clip_mask_layer, target_crs=target_crs, parent=self,
        )
        self._worker.sig_progress.connect(self._on_worker_progress)
        self._worker.sig_status.connect(self._on_worker_status)
        self._worker.sig_finished.connect(self._on_worker_finished)
        self._worker.sig_error.connect(self._on_worker_error)
        self._worker.finished.connect(self._on_worker_thread_finished)
        self._worker.start()

    def _on_worker_progress(self, pct, message):
        self.progressBar.setValue(pct)
        if message:
            self.lblStatus.setText(message)

    def _on_worker_status(self, message):
        self.lblStatus.setText(message)

    def _on_worker_finished(self, results):
        ok = [r for r in results if r.get("path") and not r.get("error")]
        errors = [r for r in results if r.get("error")]
        canceled = [r for r in results if r.get("canceled")]

        for r in ok:
            layer_name = f"{os.path.splitext(os.path.basename(r['path']))[0]}"
            layer = QgsRasterLayer(r["path"], layer_name)
            if layer.isValid():
                QgsProject.instance().addMapLayer(layer)

        self.lblStatus.setText(self.tr(f"Completado: {len(ok)} capa(s) cargada(s)."))

        msg_parts = [f"Descargadas correctamente: {len(ok)}"]
        if errors:
            msg_parts.append("Errores:\n" + "\n".join(f"- {r['var']}: {r['error']}" for r in errors))
        if canceled:
            msg_parts.append(f"Canceladas: {len(canceled)}")

        if errors:
            QMessageBox.warning(self, self.tr("Descarga finalizada con errores"), "\n\n".join(msg_parts))
        else:
            QMessageBox.information(self, self.tr("Descarga completada"), "\n\n".join(msg_parts))

    def _on_worker_error(self, message):
        self.lblStatus.setText(self.tr("Error durante la descarga."))
        QMessageBox.critical(self, self.tr("Error"), message)

    def _on_worker_thread_finished(self):
        self._set_download_in_progress(False)
        self._worker = None
        self._close_requested = False

    def _set_download_in_progress(self, in_progress):
        self.btnDownload.setEnabled(not in_progress)
        self.btnCancel.setEnabled(in_progress)

    # ------------------------------------------------------------------
    # cancel_download / closeEvent -- mismo patrón que species_download
    # ------------------------------------------------------------------
    def cancel_download(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            self.btnCancel.setEnabled(False)
        else:
            self.lblStatus.setText(self.tr("Listo"))

    def closeEvent(self, event):
        """Nunca destruye un QThread vivo -- ver species_download_dialog.py
        para la explicación completa de por qué esto es necesario."""
        if self._worker is not None and self._worker.isRunning():
            if not self._close_requested:
                reply = QMessageBox.question(
                    self, self.tr("Descarga en curso"),
                    self.tr("Hay una descarga en curso. ¿Deseas cancelarla y cerrar la ventana?"),
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
