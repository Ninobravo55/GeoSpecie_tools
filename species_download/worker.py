"""
worker.py — Ejecuta la descarga GBIF en un QThread aparte, para no
congelar la interfaz de QGIS. No usa QgsTask/feedback de Processing
porque este diálogo es standalone (no un QgsProcessingAlgorithm).

La lógica pesada (buscar + filtrar + exportar) vive en batch.py
(run_download_pipeline), no aquí: este worker es solo el envoltorio de
QThread + señales alrededor de esa misma función que también usa
batch.download_species_records() para uso desde consola/script. Antes
esta lógica estaba duplicada inline en run() -- eso era justamente el
riesgo de mantener dos implementaciones (GUI vs. script) que terminan
divergiendo con el tiempo.

Señales
-------
sig_progress(int, str)   -> (porcentaje 0-100, mensaje corto)
sig_status(str)          -> mensaje de estado libre (para lblStatus)
sig_finished(str, int)   -> (ruta del archivo final, nro. de registros escritos)
sig_error(str)           -> mensaje de error (detiene el flujo)
"""

import threading

import requests
from qgis.PyQt.QtCore import QThread, pyqtSignal
from qgis.core import QgsMessageLog, Qgis

from . import batch, gbif_api, exporter


class GbifDownloadWorker(QThread):
    """Hilo de descarga+filtrado+exportación para 'Descargar Registros GBIF'."""

    sig_progress = pyqtSignal(int, str)
    sig_status = pyqtSignal(str)
    sig_finished = pyqtSignal(str, int)
    sig_error = pyqtSignal(str)

    def __init__(self, query_params, output_path, max_records,
                 only_living, remove_duplicates, target_crs_authid=None, parent=None):
        """
        Parámetros
        ----------
        query_params : dict
            Salida de filters.build_query_params(...). El diálogo ya
            resolvió sinónimos y armó este dict antes de crear el worker.
        output_path : str
            Ruta completa del archivo de salida (carpeta + nombre).
        max_records : int
            Tope de registros a descargar.
        only_living : bool
            chkLiving -> aplica filters.is_living_record tras la descarga.
        remove_duplicates : bool
            chkDuplicate -> aplica filters.dedupe_records tras la descarga.
        target_crs_authid : str | None
            CRS de salida (ej. 'EPSG:32718'). Si es None o coincide con
            EPSG:4326, exporter.export_points no reproyecta nada (las
            coordenadas de GBIF ya vienen en WGS84).
        """
        super().__init__(parent)
        self.query_params = query_params
        self.output_path = output_path
        self.max_records = max_records
        self.only_living = only_living
        self.remove_duplicates = remove_duplicates
        self.target_crs_authid = target_crs_authid
        self._cancel_event = threading.Event()
        self._session = None  # se crea dentro de run(), en el hilo del worker

    def request_cancel(self):
        """
        Solicita cancelación. A diferencia de solo marcar un flag revisado
        entre páginas (lo que podía tardar hasta ~70s si una petición
        estaba en curso con timeout de 60s + reintentos), esto además
        cierra la sesión HTTP activa desde este hilo (el de la UI): eso
        aborta de inmediato cualquier request en vuelo, que en
        gbif_api._fetch_page se traduce en una excepción interpretada
        como cancelación (GbifCanceled) en vez de un error de red.
        """
        self._cancel_event.set()
        self.sig_status.emit("Cancelando... cerrando conexión con GBIF.")
        session = self._session
        if session is not None:
            try:
                session.close()
            except Exception as err:
                QgsMessageLog.logMessage(
                    f"Error cerrando sesion HTTP GBIF en cancelacion: {err}",
                    "GeoSpeciesTools",
                    Qgis.MessageLevel.Info
                )

    def _is_canceled(self):
        return self._cancel_event.is_set()

    def run(self):
        self._session = requests.Session()
        try:
            self.sig_status.emit("Consultando registros en GBIF...")

            def _progress_cb(done, total):
                pct = int(min(85, (done / total) * 85)) if total else 0
                self.sig_progress.emit(pct, f"Descargados {done} de ~{total} registros")

            result = batch.run_download_pipeline(
                self.query_params,
                self.output_path,
                max_records=self.max_records,
                only_living=self.only_living,
                remove_duplicates=self.remove_duplicates,
                target_crs_authid=self.target_crs_authid,
                progress_cb=_progress_cb,
                status_cb=self.sig_status.emit,
                cancel_check=self._is_canceled,
                session=self._session,
            )

            if result["canceled"]:
                self.sig_status.emit("Descarga cancelada por el usuario.")
                return

            self.sig_progress.emit(100, "Completado")
            self.sig_finished.emit(result["output_path"], result["count"])

        except gbif_api.GbifApiError as exc:
            self.sig_error.emit(f"Error de conexión con GBIF: {exc}")
        except exporter.ExportError as exc:
            self.sig_error.emit(f"Error al exportar la capa de salida: {exc}")
        except Exception as exc:  # noqa: BLE001 - se reporta cualquier falla inesperada a la UI
            self.sig_error.emit(f"Error inesperado durante la descarga: {exc}")
        finally:
            session, self._session = self._session, None
            if session is not None:
                try:
                    session.close()
                except Exception as err:
                    QgsMessageLog.logMessage(
                        f"Error cerrando sesion HTTP GBIF al finalizar: {err}",
                        "GeoSpeciesTools",
                        Qgis.MessageLevel.Info
                    )
