"""
worker.py — Ejecuta batch.run_batch() en un QThread aparte.

Cancelación: se revisa entre especies, no a mitad de una (cada especie
es una unidad de trabajo corta -- segundos, no las descargas de minutos
de las otras herramientas del plugin).

Señales
-------
sig_progress(int, str)  -> (porcentaje 0-100, mensaje corto)
sig_status(str)         -> mensaje de estado libre
sig_finished(list)      -> lista de batch.SpeciesResult
sig_error(str)          -> error no recuperable (detiene el flujo completo)
"""

import threading

from qgis.PyQt.QtCore import QThread, pyqtSignal

from . import batch


class MaxentAnalysisWorker(QThread):

    sig_progress = pyqtSignal(int, str)
    sig_status = pyqtSignal(str)
    sig_finished = pyqtSignal(list)
    sig_error = pyqtSignal(str)

    def __init__(self, csv_path, carpeta_maxent, output_root, threshold_criterion,
                 target_crs=None, parent=None):
        super().__init__(parent)
        self.csv_path = csv_path
        self.carpeta_maxent = carpeta_maxent
        self.output_root = output_root
        self.threshold_criterion = threshold_criterion
        self.target_crs = target_crs
        self._cancel_event = threading.Event()

    def request_cancel(self):
        self._cancel_event.set()
        self.sig_status.emit("Cancelando... se detendrá antes de la próxima especie.")

    def _is_canceled(self):
        return self._cancel_event.is_set()

    def run(self):
        try:
            def _progress_cb(done, total):
                pct = int((done / total) * 100) if total else 0
                self.sig_progress.emit(pct, f"{done}/{total} especies procesadas")

            results = batch.run_batch(
                self.csv_path, self.carpeta_maxent, self.output_root, self.threshold_criterion,
                target_crs=self.target_crs,
                status_cb=self.sig_status.emit, progress_cb=_progress_cb, cancel_check=self._is_canceled,
            )
            self.sig_finished.emit(results)
        except Exception as exc:  # noqa: BLE001 - se reporta cualquier falla inesperada a la UI
            self.sig_error.emit(f"Error inesperado durante el análisis: {exc}")
