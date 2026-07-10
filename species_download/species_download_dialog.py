"""
species_download_dialog.py — Diálogo "Descargar Registros GBIF".

Diseño deliberado: QDialog construido 100% en código Python (sin archivo
.ui de Qt Designer), siguiendo el mismo enfoque que el resto de diálogos
custom del plugin (gui/install_deps_dialog.py, gui/gee_auth_dialog.py).
Se evita .ui porque los archivos compilados con pyuic no son portables
entre PyQt5 (QGIS 3.28/3.34) y PyQt6 (QGIS 4.x) sin recompilar por
versión, y este plugin ya centraliza esa compatibilidad en
utils/qt_compat.py en vez de depender de Qt Designer.

Esta herramienta NO se registra como QgsProcessingAlgorithm: la búsqueda
interactiva de especies (autocompletado + selección en lista) no encaja
bien en el modelo de parámetros de Processing, así que se abre como un
diálogo independiente desde el menú del plugin (ver plugin.py), sin tocar
core/provider.py ni las herramientas existentes.
"""

import os

from qgis.PyQt.QtCore import Qt, QCoreApplication
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qgis.gui import QgsFileWidget, QgsMapLayerComboBox, QgsProjectionSelectionWidget
from qgis.core import QgsCoordinateReferenceSystem, QgsMapLayerProxyModel, QgsProject, QgsVectorLayer

from . import gbif_api
from .aoi import layer_geometry_to_wkt_4326, AoiError
from .filters import build_query_params, COUNTRY_LIST_FALLBACK
from .utils import load_icon, validate_output_name, validate_year_range
from .worker import GbifDownloadWorker


def _format_duration(seconds):
    """Formatea segundos a un texto corto tipo '2 min 15 s' para mostrar
    la estimación de tiempo en el diálogo de confirmación previa."""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {secs} s"
    hours, mins = divmod(minutes, 60)
    return f"{hours} h {mins} min"


class SpeciesDownloadDialog(QDialog):
    """Diálogo para buscar una especie en GBIF y descargar sus ocurrencias."""

    def __init__(self, iface=None, parent=None):
        """
        Parámetros
        ----------
        iface : QgisInterface | None
            Interfaz de QGIS (para leer capas de polígonos cargadas vía
            cmbAOI). Puede ser None en pruebas fuera de QGIS.
        parent : QWidget | None
            Widget padre (normalmente iface.mainWindow()).
        """
        super().__init__(parent)
        self.iface = iface
        self._worker = None
        self._selected_species_key = None
        self._close_requested = False  # ver closeEvent(): cierre diferido hasta que el worker realmente termine

        self.setWindowTitle(self.tr("Descargar Registros GBIF"))
        self.resize(900, 700)
        self.setMinimumSize(700, 550)

        self._build_ui()
        self._connect_signals()
        self._update_aoi_widgets_enabled()

        if getattr(self, "_country_list_fallback_used", False):
            self.lblStatus.setText(
                self.tr(
                    "Sin conexión a GBIF al abrir: cmbCountry usa una lista local reducida "
                    "(no la lista oficial completa). Cierra y reabre con conexión para corregirlo."
                )
            )

    def tr(self, message):
        return QCoreApplication.translate("SpeciesDownloadDialog", message)

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------
    def _build_ui(self):
        """Arma todos los QGroupBox y los agrega al layout principal."""
        main_layout = QVBoxLayout(self)

        main_layout.addWidget(self._build_group_species())
        main_layout.addWidget(self._build_group_area())
        main_layout.addWidget(self._build_group_filters())
        main_layout.addWidget(self._build_group_output())
        main_layout.addWidget(self._build_group_status())
        main_layout.addLayout(self._build_bottom_buttons())

    def _build_group_species(self):
        """Grupo 1 — Buscar especie."""
        group = QGroupBox(self.tr("Buscar especie"))
        layout = QVBoxLayout(group)

        search_row = QHBoxLayout()
        label = QLabel(self.tr("Nombre científico"))
        self.txtSpecies = QLineEdit()
        self.txtSpecies.setObjectName("txtSpecies")
        self.txtSpecies.setPlaceholderText(self.tr("Escriba el nombre científico..."))

        self.btnSearch = QPushButton(self.tr("Buscar"))
        self.btnSearch.setObjectName("btnSearch")
        self.btnSearch.setIcon(load_icon("search.svg", self.btnSearch))

        search_row.addWidget(label)
        search_row.addWidget(self.txtSpecies, stretch=1)
        search_row.addWidget(self.btnSearch)
        layout.addLayout(search_row)

        self.lstSpecies = QListWidget()
        self.lstSpecies.setObjectName("lstSpecies")
        self.lstSpecies.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self.lstSpecies)

        return group

    def _build_group_area(self):
        """Grupo 2 — Área de estudio."""
        group = QGroupBox(self.tr("Área de estudio"))
        layout = QGridLayout(group)

        self.rbWorld = QRadioButton(self.tr("Mundial"))
        self.rbWorld.setObjectName("rbWorld")
        self.rbCountry = QRadioButton(self.tr("País"))
        self.rbCountry.setObjectName("rbCountry")
        self.rbAOI = QRadioButton(self.tr("Usar capa de polígonos"))
        self.rbAOI.setObjectName("rbAOI")
        self.rbWorld.setChecked(True)

        self.area_button_group = QButtonGroup(self)
        for rb in (self.rbWorld, self.rbCountry, self.rbAOI):
            self.area_button_group.addButton(rb)

        self.cmbCountry = QComboBox()
        self.cmbCountry.setObjectName("cmbCountry")
        self._populate_country_combo()

        self.cmbAOI = QgsMapLayerComboBox()
        self.cmbAOI.setObjectName("cmbAOI")
        self.cmbAOI.setFilters(QgsMapLayerProxyModel.PolygonLayer)

        layout.addWidget(self.rbWorld, 0, 0)
        layout.addWidget(self.rbCountry, 1, 0)
        layout.addWidget(self.cmbCountry, 1, 1)
        layout.addWidget(self.rbAOI, 2, 0)
        layout.addWidget(self.cmbAOI, 2, 1)
        layout.setColumnStretch(1, 1)

        return group

    def _populate_country_combo(self):
        """
        Llena cmbCountry con la lista OFICIAL de países que GBIF acepta
        en el parámetro `country` (gbif_api.fetch_country_enum(), en
        vivo). Si la petición falla (por ejemplo, sin conexión al abrir
        el diálogo), cae a filters.COUNTRY_LIST_FALLBACK -- una lista
        reducida en español -- y deja registrado el hecho para avisar
        en lblStatus una vez que ese widget exista (ver __init__).
        """
        self._country_list_fallback_used = False
        try:
            countries = gbif_api.fetch_country_enum()
            if not countries:
                raise gbif_api.GbifApiError("La respuesta de GBIF vino vacía.")
        except gbif_api.GbifApiError:
            countries = COUNTRY_LIST_FALLBACK
            self._country_list_fallback_used = True

        for code, name in countries:
            self.cmbCountry.addItem(f"{name} ({code})", code)

        # Perú preseleccionado por defecto (contexto principal de uso de
        # este plugin), sin forzar rbCountry -- Mundial sigue siendo el
        # radio button activo por defecto.
        pe_index = self.cmbCountry.findData("PE")
        if pe_index >= 0:
            self.cmbCountry.setCurrentIndex(pe_index)

    def _build_group_filters(self):
        """Grupo 3 — Filtros."""
        group = QGroupBox(self.tr("Filtros"))
        layout = QGridLayout(group)

        self.chkCoordinate = QCheckBox(self.tr("Solo coordenadas válidas"))
        self.chkCoordinate.setObjectName("chkCoordinate")
        self.chkCoordinate.setChecked(True)

        self.chkGeoIssue = QCheckBox(self.tr("Sin problemas geográficos"))
        self.chkGeoIssue.setObjectName("chkGeoIssue")
        self.chkGeoIssue.setChecked(True)

        self.chkDuplicate = QCheckBox(self.tr("Eliminar duplicados"))
        self.chkDuplicate.setObjectName("chkDuplicate")
        self.chkDuplicate.setChecked(True)

        self.chkLiving = QCheckBox(self.tr("Solo registros vivos"))
        self.chkLiving.setObjectName("chkLiving")

        layout.addWidget(self.chkCoordinate, 0, 0)
        layout.addWidget(self.chkGeoIssue, 0, 1)
        layout.addWidget(self.chkDuplicate, 1, 0)
        layout.addWidget(self.chkLiving, 1, 1)

        self.spYearMin = QSpinBox()
        self.spYearMin.setObjectName("spYearMin")
        self.spYearMin.setRange(1700, 2100)
        self.spYearMin.setValue(1990)

        self.spYearMax = QSpinBox()
        self.spYearMax.setObjectName("spYearMax")
        self.spYearMax.setRange(1700, 2100)
        self.spYearMax.setValue(2025)

        self.spUncertainty = QSpinBox()
        self.spUncertainty.setObjectName("spUncertainty")
        self.spUncertainty.setRange(0, 1000000)
        self.spUncertainty.setSingleStep(100)
        self.spUncertainty.setValue(1000)

        year_row = QHBoxLayout()
        year_row.addWidget(QLabel(self.tr("Año mínimo")))
        year_row.addWidget(self.spYearMin)
        year_row.addWidget(QLabel(self.tr("Año máximo")))
        year_row.addWidget(self.spYearMax)
        year_row.addStretch(1)

        uncert_row = QHBoxLayout()
        uncert_row.addWidget(QLabel(self.tr("Incertidumbre máx.")))
        uncert_row.addWidget(self.spUncertainty)
        uncert_row.addWidget(QLabel(self.tr("metros")))
        uncert_row.addStretch(1)

        layout.addLayout(year_row, 2, 0, 1, 2)
        layout.addLayout(uncert_row, 3, 0, 1, 2)

        return group

    def _build_group_output(self):
        """Grupo 4 — Salida."""
        group = QGroupBox(self.tr("Salida"))
        layout = QGridLayout(group)

        layout.addWidget(QLabel(self.tr("Carpeta de salida")), 0, 0)
        self.txtOutputFolder = QgsFileWidget()
        self.txtOutputFolder.setObjectName("txtOutputFolder")
        self.txtOutputFolder.setStorageMode(QgsFileWidget.GetDirectory)
        layout.addWidget(self.txtOutputFolder, 0, 1)

        layout.addWidget(QLabel(self.tr("Nombre de archivo")), 1, 0)
        self.txtOutputName = QLineEdit("Species.shp")
        self.txtOutputName.setObjectName("txtOutputName")
        layout.addWidget(self.txtOutputName, 1, 1)

        # CRS de salida — antes esto quedaba fijo en EPSG:4326 (nativo de
        # las coordenadas que entrega GBIF). Se ofrece el CRS del proyecto
        # activo como valor por defecto (razonable para SWAT/balance
        # hídrico/ordenamiento territorial, donde igual se reproyecta a
        # UTM después), pero el selector queda editable — no se asume
        # silenciosamente cuál CRS quiere el usuario.
        layout.addWidget(QLabel(self.tr("CRS de salida")), 2, 0)
        self.crsOutput = QgsProjectionSelectionWidget()
        self.crsOutput.setObjectName("crsOutput")
        project_crs = QgsProject.instance().crs()
        default_crs = project_crs if project_crs.isValid() else QgsCoordinateReferenceSystem("EPSG:4326")
        self.crsOutput.setCrs(default_crs)
        layout.addWidget(self.crsOutput, 2, 1)

        layout.setColumnStretch(1, 1)
        return group

    def _build_group_status(self):
        """Grupo 5 — Estado."""
        group = QGroupBox(self.tr("Estado"))
        layout = QVBoxLayout(group)

        self.progressBar = QProgressBar()
        self.progressBar.setObjectName("progressBar")
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        layout.addWidget(self.progressBar)

        self.lblStatus = QLabel(self.tr("Listo"))
        self.lblStatus.setObjectName("lblStatus")
        layout.addWidget(self.lblStatus)

        return group

    def _build_bottom_buttons(self):
        """Parte inferior — Descargar / Cancelar."""
        row = QHBoxLayout()
        row.addStretch(1)

        self.btnDownload = QPushButton(self.tr("Descargar"))
        self.btnDownload.setObjectName("btnDownload")
        self.btnDownload.setIcon(load_icon("download.svg", self.btnDownload))

        self.btnCancel = QPushButton(self.tr("Cancelar"))
        self.btnCancel.setObjectName("btnCancel")
        self.btnCancel.setIcon(load_icon("cancel.svg", self.btnCancel))
        self.btnCancel.setEnabled(False)

        row.addWidget(self.btnDownload)
        row.addWidget(self.btnCancel)
        return row

    # ------------------------------------------------------------------
    # Señales
    # ------------------------------------------------------------------
    def _connect_signals(self):
        self.btnSearch.clicked.connect(self.search_species)
        self.btnDownload.clicked.connect(self.download_species)
        self.btnCancel.clicked.connect(self.cancel_download)
        self.txtSpecies.returnPressed.connect(self.search_species)

        for rb in (self.rbWorld, self.rbCountry, self.rbAOI):
            rb.toggled.connect(self._update_aoi_widgets_enabled)

        self.lstSpecies.itemSelectionChanged.connect(self._on_species_selection_changed)

    def _update_aoi_widgets_enabled(self):
        """Habilita solo el combo correspondiente al radio button activo."""
        self.cmbCountry.setEnabled(self.rbCountry.isChecked())
        self.cmbAOI.setEnabled(self.rbAOI.isChecked())

    def _on_species_selection_changed(self):
        item = self.lstSpecies.currentItem()
        self._selected_species_key = item.data(Qt.UserRole) if item else None

    # ------------------------------------------------------------------
    # search_species — Grupo 1
    # ------------------------------------------------------------------
    def search_species(self):
        """
        Busca coincidencias de nombre científico en GBIF (species/suggest)
        y llena lstSpecies. Llamada síncrona: species/suggest responde en
        general en unos cientos de ms, por lo que no requiere hilo aparte;
        se muestra un cursor de espera mientras dura la petición.
        """
        query = self.txtSpecies.text().strip()
        if not query:
            QMessageBox.information(
                self, self.tr("Buscar especie"),
                self.tr("Escribe un nombre científico para buscar."),
            )
            return

        self.lstSpecies.clear()
        self._selected_species_key = None
        self.lblStatus.setText(self.tr("Buscando especie en GBIF..."))

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            matches = gbif_api.suggest_species(query)
        except gbif_api.GbifApiError as exc:
            QApplication.restoreOverrideCursor()
            self.lblStatus.setText(self.tr("Listo"))
            QMessageBox.critical(self, self.tr("Error de conexión"), str(exc))
            return
        QApplication.restoreOverrideCursor()

        if not matches:
            self.lblStatus.setText(self.tr("Listo"))
            QMessageBox.information(
                self, self.tr("Buscar especie"),
                self.tr("No se encontraron especies que coincidan con la búsqueda."),
            )
            return

        for match in matches:
            label = match["scientificName"]
            extra = " / ".join(v for v in (match.get("rank"), match.get("kingdom")) if v)
            if extra:
                label = f"{label}  [{extra}]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, match["key"])
            self.lstSpecies.addItem(item)

        self.lblStatus.setText(self.tr(f"{len(matches)} especie(s) encontradas."))

    # ------------------------------------------------------------------
    # download_species — validación + lanzamiento del worker
    # ------------------------------------------------------------------
    def download_species(self):
        """Valida el formulario, arma los parámetros de consulta y lanza
        la descarga en un GbifDownloadWorker (QThread) para no bloquear
        la interfaz de QGIS."""
        if self._selected_species_key is None:
            QMessageBox.warning(
                self, self.tr("Especie no seleccionada"),
                self.tr("Busca y selecciona una especie en la lista antes de descargar."),
            )
            return

        year_error = validate_year_range(self.spYearMin.value(), self.spYearMax.value())
        if year_error:
            QMessageBox.warning(self, self.tr("Rango de años inválido"), year_error)
            return

        output_folder = self.txtOutputFolder.filePath()
        if not output_folder or not os.path.isdir(output_folder):
            QMessageBox.warning(
                self, self.tr("Carpeta de salida"),
                self.tr("Selecciona una carpeta de salida válida."),
            )
            return

        output_name, name_error = validate_output_name(self.txtOutputName.text())
        if name_error:
            QMessageBox.warning(self, self.tr("Nombre de archivo"), name_error)
            return

        # ------------------------------------------------------------
        # Resolución de sinónimos: species/suggest puede devolver un
        # sinónimo dentro del propio GBIF Backbone (restringir por
        # datasetKey no alcanza para esto). Las ocurrencias se indexan
        # bajo el taxonKey del nombre ACEPTADO, así que hay que resolverlo
        # antes de armar la query o la búsqueda puede volver a salir
        # vacía por esta causa en vez de por country/year/uncertainty.
        # ------------------------------------------------------------
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            taxon_key, synonym_message = gbif_api.resolve_accepted_taxon_key(self._selected_species_key)
        except gbif_api.GbifApiError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, self.tr("Error de conexión"), str(exc))
            return
        QApplication.restoreOverrideCursor()

        if synonym_message:
            self.lblStatus.setText(synonym_message)

        country_code = None
        geometry_wkt = None
        if self.rbCountry.isChecked():
            country_code = self.cmbCountry.currentData()
        elif self.rbAOI.isChecked():
            try:
                geometry_wkt = layer_geometry_to_wkt_4326(self.cmbAOI.currentLayer())
            except AoiError as exc:
                QMessageBox.warning(self, self.tr("Área de interés"), str(exc))
                return

        query_params = build_query_params(
            species_key=taxon_key,
            year_min=self.spYearMin.value(),
            year_max=self.spYearMax.value(),
            uncertainty_m=self.spUncertainty.value(),
            only_valid_coordinates=self.chkCoordinate.isChecked(),
            no_geo_issues=self.chkGeoIssue.isChecked(),
            country_code=country_code,
            geometry_wkt=geometry_wkt,
        )

        output_path = os.path.join(output_folder, output_name)

        # ------------------------------------------------------------
        # Conteo previo (limit=0): antes de comprometerse a una descarga
        # que puede tomar minutos u horas (334 páginas para 100k registros,
        # sin contar reintentos), se pregunta a GBIF cuántos registros hay
        # en total para estos filtros exactos. Es una sola petición rápida,
        # síncrona, igual que search_species().
        # ------------------------------------------------------------
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.lblStatus.setText(self.tr("Consultando conteo total en GBIF..."))
        try:
            total_count, elapsed = gbif_api.count_occurrences(query_params)
        except gbif_api.GbifApiError as exc:
            QApplication.restoreOverrideCursor()
            self.lblStatus.setText(self.tr("Listo"))
            QMessageBox.critical(self, self.tr("Error de conexión"), str(exc))
            return
        QApplication.restoreOverrideCursor()

        if total_count == 0:
            self.lblStatus.setText(self.tr("Listo"))
            QMessageBox.information(
                self, self.tr("Sin registros"),
                self.tr(
                    "GBIF reporta 0 registros para esta combinación de especie, "
                    "área de estudio y filtros. No se inició ninguna descarga.\n\n"
                    "Revisa el rango de años, la incertidumbre máxima, o si la "
                    "especie seleccionada es un sinónimo (el taxonKey del nombre "
                    "aceptado puede ser distinto)."
                ),
            )
            return

        records_to_fetch = min(total_count, gbif_api.ABSOLUTE_MAX_RECORDS)
        estimated_pages = -(-records_to_fetch // gbif_api.PER_PAGE_LIMIT)  # ceil
        # Estimación gruesa: latencia observada en la consulta de conteo
        # como proxy de latencia por página, más la pausa entre páginas.
        estimated_seconds = estimated_pages * (max(elapsed, 0.2) + gbif_api.RATE_LIMIT_SLEEP)
        estimate_text = _format_duration(estimated_seconds)

        warning_extra = ""
        if total_count > gbif_api.ABSOLUTE_MAX_RECORDS:
            warning_extra = (
                f"\n\nGBIF tiene {total_count} registros en total, pero la API "
                f"solo permite recuperar hasta {gbif_api.ABSOLUTE_MAX_RECORDS} "
                "por consulta (limitación de GBIF, no de este plugin). Se "
                f"descargarán los primeros {records_to_fetch}."
            )

        reply = QMessageBox.question(
            self, self.tr("Confirmar descarga"),
            self.tr(
                f"GBIF reporta {total_count} registro(s) para esta especie y estos filtros.\n"
                f"Se descargarán aprox. {records_to_fetch} registros en ~{estimated_pages} páginas.\n"
                f"Tiempo estimado: {estimate_text} (puede variar según la conexión y la API)."
                f"{warning_extra}\n\n¿Continuar con la descarga?"
            ),
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            self.lblStatus.setText(self.tr("Listo"))
            return

        self._set_download_in_progress(True)
        self.progressBar.setValue(0)
        self.lblStatus.setText(self.tr("Iniciando descarga..."))

        self._worker = GbifDownloadWorker(
            query_params=query_params,
            output_path=output_path,
            max_records=gbif_api.ABSOLUTE_MAX_RECORDS,
            only_living=self.chkLiving.isChecked(),
            remove_duplicates=self.chkDuplicate.isChecked(),
            target_crs_authid=self.crsOutput.crs().authid(),
            parent=self,
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

    def _on_worker_finished(self, output_path, count):
        self.lblStatus.setText(self.tr(f"Completado: {count} registros exportados."))

        if count == 0:
            QMessageBox.information(
                self, self.tr("Descarga completada"),
                self.tr(f"La descarga terminó sin registros. No se cargó ninguna capa.\nArchivo: {output_path}"),
            )
            return

        layer_name = os.path.splitext(os.path.basename(output_path))[0]
        layer = QgsVectorLayer(output_path, layer_name, "ogr")

        if not layer.isValid():
            QMessageBox.warning(
                self, self.tr("Descarga completada"),
                self.tr(
                    f"Se exportaron {count} registros a:\n{output_path}\n\n"
                    "Pero QGIS no pudo abrir la capa automáticamente "
                    "(verifica el archivo manualmente)."
                ),
            )
            return

        QgsProject.instance().addMapLayer(layer)
        if self.iface is not None:
            try:
                self.iface.mapCanvas().setExtent(layer.extent())
                self.iface.mapCanvas().refresh()
            except Exception:
                pass  # self.iface puede ser None o un stub en pruebas fuera de QGIS

        QMessageBox.information(
            self, self.tr("Descarga completada"),
            self.tr(f"Se exportaron {count} registros a:\n{output_path}\n\nLa capa se cargó en QGIS."),
        )

    def _on_worker_error(self, message):
        self.lblStatus.setText(self.tr("Error durante la descarga."))
        QMessageBox.critical(self, self.tr("Error"), message)

    def _on_worker_thread_finished(self):
        self._set_download_in_progress(False)
        self._worker = None
        self._close_requested = False

    def _set_download_in_progress(self, in_progress):
        self.btnDownload.setEnabled(not in_progress)
        self.btnSearch.setEnabled(not in_progress)
        self.btnCancel.setEnabled(in_progress)

    # ------------------------------------------------------------------
    # cancel_download
    # ------------------------------------------------------------------
    def cancel_download(self):
        """Solicita cancelación ordenada del worker en curso."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            self.btnCancel.setEnabled(False)
        else:
            self.lblStatus.setText(self.tr("Listo"))

    def closeEvent(self, event):
        """
        Nunca cierra el diálogo mientras el worker siga vivo.

        Antes esto hacía `self._worker.wait(5000)` y luego aceptaba el
        cierre igual si no terminaba a tiempo: si el hilo seguía corriendo
        pasado ese margen (por ejemplo, atascado en un reintento con
        timeout de red), Qt podía destruir el QThread en plena ejecución
        -> crash de QGIS, no una excepción de Python capturable.

        Ahora: se pide cancelación, se ignora el evento de cierre, y se
        conecta la señal `finished` del QThread a un nuevo intento de
        cierre. Cuando el hilo realmente termine (cancelado o no), este
        método se vuelve a ejecutar y esta vez `isRunning()` es False,
        por lo que el cierre se acepta de forma segura.
        """
        if self._worker is not None and self._worker.isRunning():
            if not self._close_requested:
                reply = QMessageBox.question(
                    self, self.tr("Descarga en curso"),
                    self.tr("Hay una descarga en curso. ¿Deseas cancelarla y cerrar la ventana?"),
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    event.ignore()
                    return

                self._close_requested = True
                self.lblStatus.setText(self.tr("Cancelando descarga... la ventana se cerrará automáticamente."))
                self.btnCancel.setEnabled(False)
                self._worker.request_cancel()
                self._worker.finished.connect(self.close)

            event.ignore()
            return

        event.accept()
