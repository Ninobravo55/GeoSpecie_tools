"""
worker.py — Ejecuta la descarga (y recorte opcional al AOI real) de
tiles climáticos en un QThread aparte. Mismo patrón que
species_download/worker.py: la lógica pesada vive en batch.py/clip.py,
este archivo solo la envuelve en señales Qt + cancelación real vía
session.close() desde el hilo de la UI.

Señales
-------
sig_progress(int, str)  -> (porcentaje 0-100, mensaje corto)
sig_status(str)         -> mensaje de estado libre (para lblStatus)
sig_finished(list)      -> lista de dicts {var, path, error, canceled}
sig_error(str)          -> error no recuperable (detiene el flujo completo)
"""

import threading

import requests
from qgis.PyQt.QtCore import QThread, pyqtSignal
from qgis.core import QgsMessageLog, Qgis

from . import batch, geodata_api


class ClimateTileWorker(QThread):
    """Hilo de descarga+recorte para la herramienta de tiles climáticos."""

    sig_progress = pyqtSignal(int, str)
    sig_status = pyqtSignal(str)
    sig_finished = pyqtSignal(list)
    sig_error = pyqtSignal(str)

    def __init__(self, plan, output_folder, name_prefix,
                 clip_mask_layer=None, target_crs=None, parent=None):
        """
        Parámetros
        ----------
        plan : list[dict]
            Salida de batch.build_download_plan() -- ya validado por el
            diálogo antes de crear este worker (variables por modo y
            combinación CMIP6 ya verificadas contra el servidor).
        output_folder, name_prefix : str
            Cada variable se descarga a
            <output_folder>/<name_prefix>_<var>.tif.
        clip_mask_layer : QgsVectorLayer | None
            Si se pasa una capa de POLÍGONOS, cada tile descargado se
            recorta a su geometría real (ver clip.py). None si el AOI
            fue un punto (no hay nada que recortar: se usa el tile
            completo tal cual).
        """
        super().__init__(parent)
        self.plan = plan
        self.output_folder = output_folder
        self.name_prefix = name_prefix
        self.clip_mask_layer = clip_mask_layer
        self.target_crs = target_crs
        self._cancel_event = threading.Event()
        self._session = None

    def request_cancel(self):
        """
        Cancelación real: cierra la sesión HTTP activa desde este hilo,
        igual que species_download.worker -- aborta la descarga en
        curso en vez de esperar a que termine el chunk/archivo actual.
        """
        self._cancel_event.set()
        self.sig_status.emit("Cancelando... cerrando conexión.")
        session = self._session
        if session is not None:
            try:
                session.close()
            except Exception as err:
                QgsMessageLog.logMessage(
                    f"Error cerrando sesion HTTP en cancelacion: {err}",
                    "GeoSpeciesTools",
                    Qgis.MessageLevel.Info
                )

    def _is_canceled(self):
        return self._cancel_event.is_set()

    def run(self):
        self._session = requests.Session()
        try:
            total_vars = len(self.plan)

            def _progress_cb(downloaded, total):
                if total:
                    pct = int(min(95, (downloaded / total) * 95))
                    mb = downloaded / (1024 * 1024)
                    total_mb = total / (1024 * 1024)
                    self.sig_progress.emit(pct, f"{mb:.1f} / {total_mb:.1f} MB")
                else:
                    self.sig_progress.emit(0, f"{downloaded / (1024 * 1024):.1f} MB descargados")

            results = batch.run_tile_downloads(
                self.plan, self.output_folder, self.name_prefix,
                status_cb=self.sig_status.emit, progress_cb=_progress_cb,
                cancel_check=self._is_canceled, session=self._session,
            )

            if self._is_canceled():
                self.sig_status.emit("Descarga cancelada por el usuario.")
                self.sig_finished.emit(results)
                return

            if self.clip_mask_layer is not None:
                self._clip_results(results)

            self.sig_progress.emit(100, "Completado")
            self.sig_finished.emit(results)

        except Exception as exc:  # noqa: BLE001 - se reporta cualquier falla inesperada a la UI
            self.sig_error.emit(f"Error inesperado durante la descarga: {exc}")
        finally:
            session, self._session = self._session, None
            if session is not None:
                try:
                    session.close()
                except Exception as err:
                    QgsMessageLog.logMessage(
                        f"Error cerrando sesion HTTP de descarga: {err}",
                        "GeoSpeciesTools",
                        Qgis.MessageLevel.Info
                    )

    def _clip_results(self, results):
        import os
        from . import clip  # import diferido: requiere qgis.core + processing

        for item in results:
            if item.get("error") or item.get("canceled") or not item.get("path"):
                continue
            self.sig_status.emit(f"Recortando '{item['var']}' al polígono del AOI...")
            clipped_path = item["path"].replace(".tif", "_clip.tif")
            try:
                final_path = clip.clip_raster_to_layer(
                    item["path"], self.clip_mask_layer, clipped_path, self.target_crs
                )
                
                # Eliminar el tile original después de recortar exitosamente
                try:
                    if os.path.exists(item["path"]):
                        os.remove(item["path"])
                except OSError as err:
                    QgsMessageLog.logMessage(
                        f"No se pudo eliminar tile temporal sin recortar: {err}",
                        "GeoSpeciesTools",
                        Qgis.MessageLevel.Warning
                    )
                
                item["path"] = final_path
            except clip.ClipError as exc:
                # No se aborta todo el resultado por esto: se deja el
                # tile SIN recortar y se avisa, en vez de perder el
                # archivo ya descargado.
                item["error"] = f"Descargado pero no se pudo recortar: {exc}"
