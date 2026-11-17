"""
dialog.py — Diálogo interactivo para la herramienta Variables Climáticas Actuales (GEE).
"""

import os
import traceback

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QCoreApplication
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QRadioButton,
    QButtonGroup,
    QComboBox,
    QCheckBox,
    QSpinBox,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QTextEdit,
    QMessageBox,
    QScrollArea,
    QWidget,
    QFrame,
)
from qgis.gui import (
    QgsMapLayerComboBox,
    QgsProjectionSelectionWidget,
    QgsExtentGroupBox,
    QgsFileWidget,
)
from qgis.core import (
    QgsProject,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMapLayerProxyModel,
    QgsGeometry,
    QgsRectangle,
    QgsVectorLayer,
)

from ..utils.gee_init import ensure_gee_initialized
from ..utils.aoi_builder import safe_simplify, to_ee_geometry, CRS_4326
from ..utils.gee_tiled_download import download_band_local, estimate_tile_count, announce_grid_plan
from .catalog import CLIMATIC_PRODUCTS, get_product
from .gee_climate_engine import build_climate_images


class ClimateDownloadWorker(QThread):
    """Worker en segundo plano para procesar y descargar variables climáticas desde GEE."""
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished_success = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, params):
        super().__init__()
        self.params = params
        self._canceled = False

    def cancel(self):
        self._canceled = True

    def isCanceled(self):
        return self._canceled

    # Implementar interfaz compatible con feedback de QGIS Processing
    def pushInfo(self, msg):
        self.log.emit(msg)

    def reportError(self, msg, fatal=False):
        self.log.emit(f"ERROR: {msg}")

    def setProgress(self, val):
        self.progress.emit(int(val))

    def setProgressPercent(self, val):
        self.progress.emit(int(val))

    def run(self):
        try:
            import ee
            self.log.emit("Iniciando conexión con Google Earth Engine...")
            ensure_gee_initialized(self)

            prod_id = self.params["product_id"]
            prod_item = get_product(prod_id)
            year_start = self.params["year_start"]
            year_end = self.params["year_end"]
            selected_vars = self.params["selected_vars"]
            crs_authid = self.params["crs_authid"]
            export_method = self.params["export_method"]
            out_folder = self.params["out_folder"]
            bucket = self.params.get("bucket", "")
            aoi_type = self.params["aoi_type"]  # "layer" o "extent"
            layer = self.params.get("layer")
            extent_rect = self.params.get("extent_rect")
            extent_crs = self.params.get("extent_crs")

            self.log.emit(f"Producto: {prod_item['name']}")
            self.log.emit(f"Período: {year_start} - {year_end}")
            self.log.emit(f"Variables seleccionadas ({len(selected_vars)}): {', '.join(selected_vars)}")
            self.log.emit(f"CRS de destino: {crs_authid}")

            # ── 1. Construir AOI ─────────────────────────────────────────
            self.log.emit("Construyendo Área de Interés (AOI)...")
            if aoi_type == "layer":
                if not layer or not layer.isValid():
                    raise ValueError("La capa vectorial seleccionada no es válida.")
                
                source_crs = layer.crs()
                transform = QgsCoordinateTransform(source_crs, CRS_4326, QgsProject.instance())
                geoms_4326 = []
                for feat in layer.getFeatures():
                    g = feat.geometry()
                    if g and not g.isEmpty():
                        g_copy = QgsGeometry(g)
                        g_copy.transform(transform)
                        geoms_4326.append(g_copy)

                if not geoms_4326:
                    raise ValueError("La capa vectorial no contiene geometrías válidas.")
                
                geom_combined = QgsGeometry.unaryUnion(geoms_4326) if len(geoms_4326) > 1 else geoms_4326[0]
            else:
                if not extent_rect or extent_rect.isNull() or extent_rect.isEmpty():
                    raise ValueError("El recuadro del canvas no es válido o está vacío.")
                
                ext_crs = extent_crs if extent_crs and extent_crs.isValid() else QgsProject.instance().crs()
                transform = QgsCoordinateTransform(ext_crs, CRS_4326, QgsProject.instance())
                rect_geom = QgsGeometry.fromRect(extent_rect)
                rect_geom.transform(transform)
                geom_combined = rect_geom

            geom_simple = safe_simplify(geom_combined)
            geom_ee_4326 = to_ee_geometry(geom_simple, ee)

            # Buffer de 5000 m y bounds
            self.log.emit("Aplicando buffer de 5 000 m y calculando rectángulo envolvente (bounds)...")
            aoi_ee = geom_ee_4326.buffer(5000).bounds()

            # ── 2. Construir imágenes en GEE ─────────────────────────────
            self.log.emit("Construyendo grafo de cómputo en Google Earth Engine...")
            climate_images = build_climate_images(
                ee=ee,
                product_id=prod_id,
                year_start=year_start,
                year_end=year_end,
                selected_var_ids=selected_vars,
                aoi_ee=aoi_ee
            )

            res_scale = prod_item.get("res_m", 1000)
            n_vars = len(climate_images)

            # ── 3. Exportación ───────────────────────────────────────────
            if export_method == 0:  # Descarga directa local
                os.makedirs(out_folder, exist_ok=True)

                n_cells = estimate_tile_count(ee, aoi_ee, scale_m=res_scale)
                announce_grid_plan(self, n_cells, n_variables=n_vars)

                for idx, (var_id, item) in enumerate(climate_images.items()):
                    if self._canceled:
                        self.log.emit("Descarga cancelada por el usuario.")
                        return

                    self.log.emit(f"Descargando [{idx+1}/{n_vars}] {item['label']} -> {item['filename']} ...")
                    img_clipped = item["image"].clip(aoi_ee).reproject(crs=crs_authid, scale=res_scale)
                    filepath = os.path.join(out_folder, item["filename"])
                    
                    download_band_local(
                        ee=ee,
                        imagen=img_clipped,
                        banda=item["out_name"],
                        aoi_ee=aoi_ee,
                        crs=crs_authid,
                        scale=res_scale,
                        out_folder=out_folder,
                        filepath=filepath,
                        feedback=self,
                    )
                    prog = int(((idx + 1) / n_vars) * 100)
                    self.progress.emit(prog)

                self.finished_success.emit(out_folder)

            elif export_method == 1:  # Google Drive
                folder_name = os.path.basename(out_folder.rstrip('/\\')) or "Climaticos_GEE"
                task_ids = []
                for var_id, item in climate_images.items():
                    if self._canceled:
                        return
                    self.log.emit(f"Enviando tarea Drive para {item['label']}...")
                    img_clipped = item["image"].clip(aoi_ee).reproject(crs=crs_authid, scale=res_scale)
                    task = ee.batch.Export.image.toDrive(
                        image          = img_clipped,
                        description    = item["filename"].replace(".tif", ""),
                        folder         = folder_name,
                        fileNamePrefix = item["filename"].replace(".tif", ""),
                        region         = aoi_ee,
                        scale          = res_scale,
                        crs            = crs_authid,
                        maxPixels      = int(1e13),
                        fileFormat     = 'GeoTIFF',
                    )
                    task.start()
                    task_ids.append(task.status().get('id', 'desconocido'))

                self.log.emit(f"Tareas Drive iniciadas con éxito ({len(task_ids)}): {', '.join(task_ids)}")
                self.log.emit("Revisa el progreso en: https://code.earthengine.google.com/tasks")
                self.progress.emit(100)
                self.finished_success.emit(f"Drive ({folder_name})")

            elif export_method == 2:  # GCS
                if not bucket:
                    raise ValueError("Debe ingresar el nombre del Bucket GCS.")
                prefix = os.path.basename(out_folder.rstrip('/\\')) or "Climaticos_GEE"
                task_ids = []
                for var_id, item in climate_images.items():
                    if self._canceled:
                        return
                    self.log.emit(f"Enviando tarea GCS para {item['label']}...")
                    img_clipped = item["image"].clip(aoi_ee).reproject(crs=crs_authid, scale=res_scale)
                    task = ee.batch.Export.image.toCloudStorage(
                        image          = img_clipped,
                        description    = item["filename"].replace(".tif", ""),
                        bucket         = bucket,
                        fileNamePrefix = f"{prefix}/{item['filename'].replace('.tif', '')}",
                        region         = aoi_ee,
                        scale          = res_scale,
                        crs            = crs_authid,
                        maxPixels      = int(1e13),
                        fileFormat     = 'GeoTIFF',
                    )
                    task.start()
                    task_ids.append(task.status().get('id', 'desconocido'))

                self.log.emit(f"Tareas GCS iniciadas con éxito ({len(task_ids)}): {', '.join(task_ids)}")
                self.log.emit("Revisa el progreso en: https://code.earthengine.google.com/tasks")
                self.progress.emit(100)
                self.finished_success.emit(f"GCS ({bucket})")

        except Exception as e:
            err = traceback.format_exc()
            self.log.emit(f"ERROR: {str(e)}\n{err}")
            self.error.emit(str(e))


class ClimaticosActualesDialog(QDialog):
    """Diálogo interactivo para la descarga de variables climáticas actuales desde GEE."""

    def __init__(self, iface=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._worker = None
        self._var_checkboxes = {}

        self.setWindowTitle(self.tr("Variables Climáticas Actuales (GEE)"))
        self.resize(680, 840)
        self.setMinimumSize(580, 700)

        self._build_ui()
        self._connect_signals()
        self._on_product_changed()
        self._on_time_mode_changed()
        self._on_aoi_mode_changed()
        self._on_export_method_changed()

    def tr(self, message):
        return QCoreApplication.translate("ClimaticosActualesDialog", message)

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        cont = QWidget()
        layout = QVBoxLayout(cont)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        # ── Banner Header ──────────────────────────────────────────────
        header = QFrame()
        header.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1b3a4b, stop:1 #065a60);
                border-radius: 6px;
                padding: 12px;
            }
        """)
        h_layout = QVBoxLayout(header)
        h_title = QLabel("⛅  Variables Climáticas Actuales (GEE)")
        h_title.setStyleSheet("color: #ffffff; font-size: 15pt; font-weight: bold;")
        h_sub = QLabel("Descarga y agregación multianual de precipitaciones y temperaturas desde Google Earth Engine")
        h_sub.setStyleSheet("color: #b0e0e6; font-size: 9.5pt;")
        h_layout.addWidget(h_title)
        h_layout.addWidget(h_sub)
        layout.addWidget(header)

        # ── 1. Área de Interés (AOI) ──────────────────────────────────
        g_aoi = QGroupBox(self.tr("1. Área de Interés (AOI)"))
        g_aoi_l = QVBoxLayout(g_aoi)

        h_aoi_type = QHBoxLayout()
        self.rb_aoi_layer = QRadioButton(self.tr("Opción 1: Capa Vectorial (Polígono)"))
        self.rb_aoi_layer.setChecked(True)
        self.rb_aoi_canvas = QRadioButton(self.tr("Opción 2: Dibujar Recuadro / Extensión en Canvas"))
        self.aoi_group = QButtonGroup(self)
        self.aoi_group.addButton(self.rb_aoi_layer)
        self.aoi_group.addButton(self.rb_aoi_canvas)
        h_aoi_type.addWidget(self.rb_aoi_layer)
        h_aoi_type.addWidget(self.rb_aoi_canvas)
        g_aoi_l.addLayout(h_aoi_type)

        # Opción 1: Capa
        self.w_layer_container = QWidget()
        l_layer = QVBoxLayout(self.w_layer_container)
        l_layer.setContentsMargins(0, 4, 0, 0)
        self.cmb_layer = QgsMapLayerComboBox()
        self.cmb_layer.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)

        lbl_buf = QLabel(self.tr("ℹ️ Se aplicará automáticamente un <b>buffer de 5 000 m</b> y recorte bounds."))
        lbl_buf.setStyleSheet("color: #0b5394; font-size: 8.5pt;")
        l_layer.addWidget(self.cmb_layer)
        l_layer.addWidget(lbl_buf)
        g_aoi_l.addWidget(self.w_layer_container)

        # Opción 2: Extent Canvas
        self.extent_group_box = QgsExtentGroupBox()
        self.extent_group_box.setTitle(self.tr("Extensión del Canvas"))
        if self.iface:
            self.extent_group_box.setMapCanvas(self.iface.mapCanvas())
        self.extent_group_box.hide()
        g_aoi_l.addWidget(self.extent_group_box)

        layout.addWidget(g_aoi)

        # ── 2. CRS de Destino ─────────────────────────────────────────
        g_crs = QGroupBox(self.tr("2. CRS de Destino"))
        g_crs_l = QHBoxLayout(g_crs)
        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setCrs(QgsCoordinateReferenceSystem("EPSG:32718"))
        g_crs_l.addWidget(QLabel(self.tr("Proyección de salida:")))
        g_crs_l.addWidget(self.crs_widget, stretch=1)
        layout.addWidget(g_crs)

        # ── 3. Producto Climático ──────────────────────────────────────
        g_prod = QGroupBox(self.tr("3. Catálogo de Producto Climático"))
        g_prod_l = QVBoxLayout(g_prod)

        h_prod = QHBoxLayout()
        h_prod.addWidget(QLabel(self.tr("Seleccionar Colección:")))
        self.cmb_product = QComboBox()
        for p in CLIMATIC_PRODUCTS:
            self.cmb_product.addItem(f"{p['name']} ({p['res_text']})", userData=p["id"])
        h_prod.addWidget(self.cmb_product, stretch=1)
        g_prod_l.addLayout(h_prod)

        # Info Card
        self.lbl_prod_info = QLabel()
        self.lbl_prod_info.setWordWrap(True)
        self.lbl_prod_info.setStyleSheet("""
            QLabel {
                background: #f0f7f9;
                border: 1px solid #b8dae4;
                border-radius: 5px;
                padding: 10px;
                color: #213547;
                font-size: 9pt;
            }
        """)
        g_prod_l.addWidget(self.lbl_prod_info)
        layout.addWidget(g_prod)

        # ── 4. Selección de Variables ──────────────────────────────────
        self.g_vars = QGroupBox(self.tr("4. Selección de Variables del Producto"))
        self.g_vars_l = QVBoxLayout(self.g_vars)

        self.w_vars_container = QWidget()
        self.vars_grid = QGridLayout(self.w_vars_container)
        self.vars_grid.setContentsMargins(0, 0, 0, 0)
        self.g_vars_l.addWidget(self.w_vars_container)

        h_vars_btn = QHBoxLayout()
        self.btn_select_all_vars = QPushButton(self.tr("Seleccionar Todas"))
        self.btn_select_all_vars.setStyleSheet("font-size: 8.5pt;")
        self.btn_unselect_all_vars = QPushButton(self.tr("Deseleccionar"))
        self.btn_unselect_all_vars.setStyleSheet("font-size: 8.5pt;")
        h_vars_btn.addWidget(self.btn_select_all_vars)
        h_vars_btn.addWidget(self.btn_unselect_all_vars)
        h_vars_btn.addStretch(1)
        self.g_vars_l.addLayout(h_vars_btn)
        layout.addWidget(self.g_vars)

        # ── 5. Filtro Temporal / Período ───────────────────────────────
        g_time = QGroupBox(self.tr("5. Filtro Temporal / Período"))
        g_time_l = QVBoxLayout(g_time)

        h_time_mode = QHBoxLayout()
        self.rb_time_single = QRadioButton(self.tr("Año Individual"))
        self.rb_time_range = QRadioButton(self.tr("Rango de Años / Período (Promedio Multianual)"))
        self.rb_time_range.setChecked(True)
        self.time_group = QButtonGroup(self)
        self.time_group.addButton(self.rb_time_single)
        self.time_group.addButton(self.rb_time_range)
        h_time_mode.addWidget(self.rb_time_single)
        h_time_mode.addWidget(self.rb_time_range)
        g_time_l.addLayout(h_time_mode)

        # Controls Single
        self.w_single_year = QWidget()
        h_single = QHBoxLayout(self.w_single_year)
        h_single.setContentsMargins(0, 0, 0, 0)
        h_single.addWidget(QLabel(self.tr("Año a Procesar:")))
        self.spn_single_year = QSpinBox()
        self.spn_single_year.setRange(1950, 2025)
        self.spn_single_year.setValue(2020)
        h_single.addWidget(self.spn_single_year)
        h_single.addStretch(1)
        g_time_l.addWidget(self.w_single_year)

        # Controls Range
        self.w_range_year = QWidget()
        h_range = QHBoxLayout(self.w_range_year)
        h_range.setContentsMargins(0, 0, 0, 0)
        h_range.addWidget(QLabel(self.tr("Año Inicial:")))
        self.spn_year_start = QSpinBox()
        self.spn_year_start.setRange(1950, 2025)
        self.spn_year_start.setValue(2000)
        h_range.addWidget(self.spn_year_start)

        h_range.addWidget(QLabel(self.tr("Año Final:")))
        self.spn_year_end = QSpinBox()
        self.spn_year_end.setRange(1950, 2025)
        self.spn_year_end.setValue(2020)
        h_range.addWidget(self.spn_year_end)
        h_range.addStretch(1)
        g_time_l.addWidget(self.w_range_year)

        lbl_time_hint = QLabel(self.tr(
            "💡 <b>Nota:</b> Para precipitación se calculará la suma acumulada de cada año y luego el promedio multianual entre ellos. "
            "Para temperatura, viento y radiación se calculará el promedio global del período."
        ))
        lbl_time_hint.setWordWrap(True)
        lbl_time_hint.setStyleSheet("color: #4a5568; font-size: 8.5pt;")
        g_time_l.addWidget(lbl_time_hint)
        layout.addWidget(g_time)

        # ── 6. Opciones de Salida ─────────────────────────────────────
        g_out = QGroupBox(self.tr("6. Exportación y Salida"))
        g_out_l = QGridLayout(g_out)

        self.cmb_export_method = QComboBox()
        self.cmb_export_method.addItem(self.tr("Descarga Directa Local (GeoTIFF)"), userData=0)
        self.cmb_export_method.addItem(self.tr("Exportar a Google Drive"), userData=1)
        self.cmb_export_method.addItem(self.tr("Exportar a Google Cloud Storage (GCS)"), userData=2)

        self.file_widget_folder = QgsFileWidget()
        self.file_widget_folder.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        self.file_widget_folder.setDialogTitle(self.tr("Seleccionar carpeta de salida"))

        self.lbl_gcs_bucket = QLabel(self.tr("Bucket GCS:"))
        self.txt_gcs_bucket = QLineEdit()
        self.txt_gcs_bucket.setPlaceholderText(self.tr("nombre-del-bucket"))

        g_out_l.addWidget(QLabel(self.tr("Método de Exportación:")), 0, 0)
        g_out_l.addWidget(self.cmb_export_method, 0, 1)
        g_out_l.addWidget(QLabel(self.tr("Carpeta de Salida / Destino:")), 1, 0)
        g_out_l.addWidget(self.file_widget_folder, 1, 1)
        g_out_l.addWidget(self.lbl_gcs_bucket, 2, 0)
        g_out_l.addWidget(self.txt_gcs_bucket, 2, 1)
        layout.addWidget(g_out)

        # ── 7. Progreso y Consola ─────────────────────────────────────
        g_prog = QGroupBox(self.tr("Progreso y Registro"))
        g_prog_l = QVBoxLayout(g_prog)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #bbb;
                border-radius: 4px;
                text-align: center;
                height: 18px;
            }
            QProgressBar::chunk {
                background-color: #2b8a3e;
            }
        """)
        g_prog_l.addWidget(self.progress_bar)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFixedHeight(120)
        self.txt_log.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 8.5pt;
                border: 1px solid #333;
                border-radius: 4px;
            }
        """)
        g_prog_l.addWidget(self.txt_log)
        layout.addWidget(g_prog)

        # ── Botones de Acción ─────────────────────────────────────────
        h_actions = QHBoxLayout()
        self.btn_run = QPushButton(self.tr("🚀  Descargar y Procesar"))
        self.btn_run.setStyleSheet("""
            QPushButton {
                background-color: #1e7e34;
                color: white;
                font-weight: bold;
                font-size: 10pt;
                padding: 10px 20px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #218838;
            }
            QPushButton:disabled {
                background-color: #6c757d;
            }
        """)
        self.btn_cancel = QPushButton(self.tr("⏹  Cancelar"))
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setStyleSheet("padding: 10px 15px; font-weight: bold;")
        self.btn_close = QPushButton(self.tr("Cerrar"))
        self.btn_close.setStyleSheet("padding: 10px 15px;")

        h_actions.addWidget(self.btn_run)
        h_actions.addWidget(self.btn_cancel)
        h_actions.addStretch(1)
        h_actions.addWidget(self.btn_close)
        layout.addLayout(h_actions)

        cont.setLayout(layout)
        scroll.setWidget(cont)
        root_layout.addWidget(scroll)

    def _connect_signals(self):
        self.rb_aoi_layer.toggled.connect(self._on_aoi_mode_changed)
        self.rb_aoi_canvas.toggled.connect(self._on_aoi_mode_changed)
        self.cmb_product.currentIndexChanged.connect(self._on_product_changed)
        self.rb_time_single.toggled.connect(self._on_time_mode_changed)
        self.rb_time_range.toggled.connect(self._on_time_mode_changed)
        self.cmb_export_method.currentIndexChanged.connect(self._on_export_method_changed)
        self.btn_select_all_vars.clicked.connect(self._select_all_vars)
        self.btn_unselect_all_vars.clicked.connect(self._unselect_all_vars)
        self.btn_run.clicked.connect(self._on_run_clicked)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        self.btn_close.clicked.connect(self.close)

    def _on_aoi_mode_changed(self):
        is_layer = self.rb_aoi_layer.isChecked()
        self.w_layer_container.setVisible(is_layer)
        self.extent_group_box.setVisible(not is_layer)

    def _on_time_mode_changed(self):
        is_single = self.rb_time_single.isChecked()
        self.w_single_year.setVisible(is_single)
        self.w_range_year.setVisible(not is_single)

    def _on_export_method_changed(self):
        method = self.cmb_export_method.currentData()
        is_gcs = (method == 2)
        self.lbl_gcs_bucket.setVisible(is_gcs)
        self.txt_gcs_bucket.setVisible(is_gcs)

    def _on_product_changed(self):
        prod_id = self.cmb_product.currentData()
        prod = get_product(prod_id)
        if not prod:
            return

        # Actualizar Info Card
        info_html = (
            f"<b>🛰️ Colección GEE:</b> <code>{prod['collection']}</code><br>"
            f"<b>📐 Resolución Espacial:</b> <b>{prod['res_text']}</b><br>"
            f"<b>📅 Disponibilidad Temporal:</b> {prod['date_range']}<br>"
            f"<b>📋 Variables del producto:</b> {prod['desc']}"
        )
        self.lbl_prod_info.setText(info_html)

        # Ajustar límites de SpinBox de fechas
        min_y = prod.get("min_year", 1950)
        max_y = prod.get("max_year", 2025)
        for spn in (self.spn_single_year, self.spn_year_start, self.spn_year_end):
            spn.setRange(min_y, max_y)

        # Reconstruir checkboxes de variables
        # Limpiar layout anterior
        while self.vars_grid.count():
            item = self.vars_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._var_checkboxes.clear()

        for idx, var in enumerate(prod["vars"]):
            chk = QCheckBox(f"{var['name']} ({var['unit']})")
            chk.setChecked(var.get("default", True))
            chk.setToolTip(var.get("desc", ""))
            chk.setStyleSheet("font-size: 9.5pt; padding: 2px;")
            self._var_checkboxes[var["id"]] = chk
            row = idx // 2
            col = idx % 2
            self.vars_grid.addWidget(chk, row, col)

    def _select_all_vars(self):
        for chk in self._var_checkboxes.values():
            chk.setChecked(True)

    def _unselect_all_vars(self):
        for chk in self._var_checkboxes.values():
            chk.setChecked(False)

    def _log(self, text):
        self.txt_log.append(text)

    def _on_run_clicked(self):
        # Validar variables seleccionadas
        selected_vars = [vid for vid, chk in self._var_checkboxes.items() if chk.isChecked()]
        if not selected_vars:
            QMessageBox.warning(self, self.tr("Advertencia"), self.tr("Debe seleccionar al menos una variable para descargar."))
            return

        # Validar AOI
        aoi_type = "layer" if self.rb_aoi_layer.isChecked() else "extent"
        layer = None
        extent_rect = None
        extent_crs = None

        if aoi_type == "layer":
            layer = self.cmb_layer.currentLayer()
            if not layer:
                QMessageBox.warning(self, self.tr("Advertencia"), self.tr("Seleccione una capa vectorial válida."))
                return
        else:
            extent_rect = self.extent_group_box.outputExtent()
            extent_crs = self.extent_group_box.outputCrs()
            if extent_rect.isNull() or extent_rect.isEmpty():
                QMessageBox.warning(self, self.tr("Advertencia"), self.tr("Defina una extensión válida en el canvas."))
                return

        # Validar carpeta de salida
        out_folder = self.file_widget_folder.filePath().strip()
        export_method = self.cmb_export_method.currentData()
        if not out_folder:
            QMessageBox.warning(self, self.tr("Advertencia"), self.tr("Especifique una carpeta de salida o nombre de destino."))
            return

        bucket = self.txt_gcs_bucket.text().strip()
        if export_method == 2 and not bucket:
            QMessageBox.warning(self, self.tr("Advertencia"), self.tr("Especifique el nombre del Bucket GCS."))
            return

        # Años
        if self.rb_time_single.isChecked():
            y_start = self.spn_single_year.value()
            y_end = self.spn_single_year.value()
        else:
            y_start = self.spn_year_start.value()
            y_end = self.spn_year_end.value()
            if y_start > y_end:
                y_start, y_end = y_end, y_start

        crs_authid = self.crs_widget.crs().authid()

        params = {
            "product_id": self.cmb_product.currentData(),
            "year_start": y_start,
            "year_end": y_end,
            "selected_vars": selected_vars,
            "crs_authid": crs_authid,
            "export_method": export_method,
            "out_folder": out_folder,
            "bucket": bucket,
            "aoi_type": aoi_type,
            "layer": layer,
            "extent_rect": extent_rect,
            "extent_crs": extent_crs,
        }

        self.txt_log.clear()
        self.progress_bar.setValue(0)
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        self._worker = ClimateDownloadWorker(params)
        self._worker.log.connect(self._log)
        self._worker.progress.connect(self.progress_bar.setValue)
        self._worker.finished_success.connect(self._on_worker_success)
        self._worker.error.connect(self._on_worker_error)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_cancel_clicked(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._log("Cancelando proceso...")
            self.btn_cancel.setEnabled(False)

    def _on_worker_finished(self):
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)

    def _on_worker_success(self, target):
        QMessageBox.information(
            self,
            self.tr("Proceso Completado"),
            self.tr(f"✅ Las variables climáticas han sido procesadas y exportadas con éxito a:\n{target}")
        )

    def _on_worker_error(self, err_msg):
        QMessageBox.critical(
            self,
            self.tr("Error en la Descarga"),
            self.tr(f"Ocurrió un error al procesar las variables climáticas:\n\n{err_msg}")
        )
