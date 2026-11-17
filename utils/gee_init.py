"""
Wrapper único de inicialización de Google Earth Engine para GeoSpecies Tools.
"""

from qgis.core import QgsProcessingException, QgsSettings, QgsTask, QgsApplication, QgsMessageLog, Qgis


SETTINGS_KEY_PROJECT = 'geospeciestools/gee_project'

_AUTH_HELP_MSG = (
    "No se pudo conectar a Google Earth Engine.\n\n"
    "Posibles causas:\n"
    "  1. Aún no has autenticado con Google. Ve al menú "
    "'GeoSpecies Tools → Autenticación GEE' y completa el proceso.\n"
    "  2. El ID de Proyecto de Google Cloud configurado no es válido o "
    "no tiene la Earth Engine API habilitada.\n"
    "  3. Tu cuenta no está aprobada para Earth Engine. Solicita acceso en "
    "https://earthengine.google.com/\n\n"
    "Detalle técnico: {error}"
)


def ensure_gee_initialized(feedback=None, force=False):
    import ee
    if not force and ee.data.is_initialized():
        return
    if feedback is not None:
        feedback.pushInfo("Inicializando Google Earth Engine...")
    settings = QgsSettings()
    project_id = settings.value(SETTINGS_KEY_PROJECT, '', type=str)
    try:
        if project_id:
            ee.Initialize(project=project_id)
        else:
            ee.Initialize()
    except Exception as e:
        raise QgsProcessingException(_AUTH_HELP_MSG.format(error=str(e)))


def is_initialized():
    try:
        import ee
        return ee.data.is_initialized()
    except ImportError:
        return False


class GEEInitTask(QgsTask):
    def __init__(self, force=False):
        super().__init__("Inicializando Earth Engine (GeoSpecies)", QgsTask.Flag.CanCancel)
        self.force = force
        self.exception = None

    def run(self):
        try:
            import ee
            if not self.force and ee.data.is_initialized():
                return True
            settings = QgsSettings()
            project_id = settings.value(SETTINGS_KEY_PROJECT, '', type=str)
            if project_id:
                ee.Initialize(project=project_id)
            else:
                ee.Initialize()
            return True
        except Exception as e:
            self.exception = str(e)
            return False

    def finished(self, result):
        if result:
            QgsMessageLog.logMessage("GEE Inicializado silenciosamente en segundo plano.", "GeoSpeciesTools", Qgis.MessageLevel.Success)
        else:
            QgsMessageLog.logMessage(
                f"Fallo al inicializar GEE en segundo plano (puede requerir autenticación): {self.exception}",
                "GeoSpeciesTools", Qgis.MessageLevel.Warning)


def background_gee_initialize(force=False):
    """Lanza la inicialización de GEE en un hilo en segundo plano."""
    try:
        task = GEEInitTask(force=force)
        QgsApplication.taskManager().addTask(task)
    except Exception as err:
        QgsMessageLog.logMessage(
            f"No se pudo registrar la tarea de inicialización de GEE: {err}",
            "GeoSpeciesTools",
            Qgis.MessageLevel.Info
        )
