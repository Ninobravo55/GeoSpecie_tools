"""
worker.py — Ejecuta la descarga multi-tile + mosaico + recorte en un
QThread aparte. Mismo patrón que species_download/worker.py: la lógica
pesada vive en batch.py/clip.py, este archivo solo la envuelve en
señales Qt + cancelación real vía session.close() desde el hilo de la UI.

Flujo:
  run_tile_downloads()    → descarga tiles individuales (0-80% progreso)
  merge_and_clip_tiles()  → mosaico + recorte al AOI (80-100%)

Señales
-------
sig_progress(int, str)  → (porcentaje 0-100, mensaje corto)
sig_status(str)         → mensaje de estado libre (para lblStatus)
sig_finished(list)      → lista de dicts {var, path, error, canceled}
sig_error(str)          → error no recuperable (detiene el flujo completo)
"""

import threading

import requests
from qgis.PyQt.QtCore import QThread, pyqtSignal
from qgis.core import QgsMessageLog, Qgis

from . import batch


class ClimateTileWorker(QThread):
    """Hilo de descarga+mosaico+recorte para la herramienta de tiles climáticos."""

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
            Salida de batch.build_download_plan() — formato:
            [{"var": str, "tile_id": int, "url": str}, ...]
        output_folder, name_prefix : str
            Carpeta y prefijo para los archivos de salida finales:
            <output_folder>/<name_prefix>_<var>.tif
        clip_mask_layer : QgsVectorLayer | None
            Si se pasa una capa de POLÍGONOS, cada variable se recorta
            a su geometría real después del mosaico. None → sin recorte.
        target_crs : QgsCoordinateReferenceSystem | None
            CRS destino opcional para reproyectar el resultado.
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
        igual que species_download.worker — aborta la descarga en curso
        en vez de esperar a que termine el chunk/archivo actual.
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
            # ── Fase 1: Descarga de tiles individuales (0–80%) ──────────────
            tile_results = batch.run_tile_downloads(
                self.plan, self.output_folder, self.name_prefix,
                status_cb=self.sig_status.emit,
                progress_cb=self.sig_progress.emit,
                cancel_check=self._is_canceled,
                session=self._session,
            )

            if self._is_canceled():
                self.sig_status.emit("Descarga cancelada por el usuario.")
                # Convertir resultados de tiles a resultados por variable
                final = _collapse_to_var_results(tile_results)
                self.sig_finished.emit(final)
                return

            # ── Fase 2: Mosaico + recorte (80–100%) ─────────────────────────
            self.sig_progress.emit(80, "Ensamblando mosaicos...")
            final_results = batch.merge_and_clip_tiles(
                tile_results,
                output_folder=self.output_folder,
                name_prefix=self.name_prefix,
                clip_mask_layer=self.clip_mask_layer,
                target_crs=self.target_crs,
                status_cb=self.sig_status.emit,
                progress_cb=self.sig_progress.emit,
                cancel_check=self._is_canceled,
            )

            self.sig_progress.emit(100, "Completado")
            self.sig_finished.emit(final_results)

        except Exception as exc:  # noqa: BLE001
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


def _collapse_to_var_results(tile_results):
    """
    Convierte la lista de resultados por (var, tile) a una lista de
    resultados por variable, para el caso de cancelación durante la
    descarga (antes de llegar a merge_and_clip_tiles).
    """
    import collections
    by_var = collections.OrderedDict()
    for r in tile_results:
        var = r["var"]
        if var not in by_var:
            by_var[var] = {"var": var, "path": None, "error": None, "canceled": False}
        if r.get("canceled"):
            by_var[var]["canceled"] = True
        if r.get("error") and not by_var[var]["error"]:
            by_var[var]["error"] = r["error"]
    return list(by_var.values())
