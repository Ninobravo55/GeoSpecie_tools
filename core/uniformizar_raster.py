import os
import glob
import shutil
import processing

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterFile,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFolderDestination,
    QgsProcessingException,
)
from qgis.PyQt.QtCore import QCoreApplication


class UniformizarRasterAlgorithm(QgsProcessingAlgorithm):
    REF_RASTER     = 'REF_RASTER'
    IN_FOLDER      = 'IN_FOLDER'
    OUT_FORMAT     = 'OUT_FORMAT'
    OUT_FOLDER     = 'OUT_FOLDER'

    def __init__(self):
        super().__init__()
        self.formatos = ["ASCII (.asc)", "GeoTiff (.tif)"]

    # ------------------------------------------------------------------
    def tr(self, string):
        return QCoreApplication.translate('UniformizarRasterAlgorithm', string)

    def createInstance(self):
        return UniformizarRasterAlgorithm()

    def name(self):
        return 'uniformizar_raster'

    def displayName(self):
        return self.tr('Uniformizar raster ASCII')

    def group(self):
        return self.tr('Preparación de datos MaxEnt')

    def groupId(self):
        return 'preparacion_maxent'

    def shortHelpString(self):
        return self.tr(
            "<b>Descripción:</b><br>"
            "Uniformiza una carpeta llena de imágenes TIF, proyectándolas, remuestreándolas y "
            "recortándolas exactamente con los parámetros de un Raster de Referencia (Maestro).<br><br>"
            "Es ideal para preparar variables climáticas y de suelo para modelos de nicho ecológico "
            "(como MaxEnt).<br><br>"
            "<b>Opciones:</b><br>"
            "Formato de salida: ASCII (.asc) o GeoTiff (.tif)."
        )

    # ------------------------------------------------------------------
    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.REF_RASTER,
                self.tr('Raster Maestro (Referencia)'),
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.IN_FOLDER,
                self.tr('Carpeta de entrada (con archivos .tif)'),
                behavior=QgsProcessingParameterFile.Behavior.Folder,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.OUT_FORMAT,
                self.tr('Formato de salida'),
                options=self.formatos,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUT_FOLDER,
                self.tr('Carpeta de salida (MAXENT)'),
            )
        )

    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        ref_layer = self.parameterAsRasterLayer(parameters, self.REF_RASTER, context)
        if not ref_layer or not ref_layer.isValid():
            raise QgsProcessingException("No se pudo cargar el raster de referencia.")

        in_folder  = self.parameterAsString(parameters, self.IN_FOLDER, context)
        out_idx    = self.parameterAsEnum(parameters, self.OUT_FORMAT, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)

        is_ascii = (out_idx == 0)

        # ── Info del Raster Maestro ─────────────────────────────────────
        extent = ref_layer.extent()
        crs = ref_layer.crs()
        pixel_x = ref_layer.rasterUnitsPerPixelX()
        
        extent_string = f"{extent.xMinimum()},{extent.xMaximum()},{extent.yMinimum()},{extent.yMaximum()}"

        feedback.pushInfo("================================")
        feedback.pushInfo("RASTER DE REFERENCIA")
        feedback.pushInfo(f"Nombre     : {ref_layer.name()}")
        feedback.pushInfo(f"CRS        : {crs.authid()}")
        feedback.pushInfo(f"Resolución : {pixel_x}")
        feedback.pushInfo(f"Extensión  : {extent_string}")
        feedback.pushInfo("================================")

        # ── Buscar TIFFs ──────────────────────────────────────────────
        lista_raster = sorted(glob.glob(os.path.join(in_folder, "*.tif")))
        total = len(lista_raster)
        if total == 0:
            raise QgsProcessingException(f"No se encontraron archivos .tif en {in_folder}")

        feedback.pushInfo(f"Total raster encontrados: {total}")

        # ── Carpetas ──────────────────────────────────────────────────
        os.makedirs(out_folder, exist_ok=True)
        temp_folder = os.path.join(out_folder, "Temp")
        os.makedirs(temp_folder, exist_ok=True)

        # ── Procesamiento ─────────────────────────────────────────────
        step = 100.0 / total if total > 0 else 100

        for i, raster in enumerate(lista_raster):
            if feedback.isCanceled():
                break

            nombre = os.path.splitext(os.path.basename(raster))[0]
            feedback.pushInfo(f"[{i+1}/{total}] Procesando: {nombre}...")

            raster_temp = os.path.join(temp_folder, f"{nombre}_temp.tif")
            raster_clip = os.path.join(temp_folder, f"{nombre}_Clip.tif")

            # 1. Reproyectar y Remuestrear
            processing.run("gdal:warpreproject", {
                'INPUT': raster,
                'TARGET_CRS': crs,
                'RESAMPLING': 1, # Bilinear
                'NODATA': -9999,
                'TARGET_RESOLUTION': pixel_x,
                'TARGET_EXTENT': extent_string,
                'TARGET_EXTENT_CRS': crs,
                'MULTITHREADING': True,
                'OUTPUT': raster_temp
            }, context=context)

            # 2. Recortar (Asegurar extensión)
            processing.run("gdal:cliprasterbyextent", {
                'INPUT': raster_temp,
                'PROJWIN': extent_string,
                'OVERCRS': False,
                'NODATA': -9999,
                'OUTPUT': raster_clip
            }, context=context)

            # 3. Formato final
            ext = ".asc" if is_ascii else ".tif"
            salida = os.path.join(out_folder, f"{nombre}{ext}")
            
            processing.run("gdal:translate", {
                'INPUT': raster_clip,
                'NODATA': -9999,
                'OUTPUT': salida
            }, context=context)

            feedback.setProgress((i + 1) * step)

        # ── Limpieza ──────────────────────────────────────────────────
        feedback.pushInfo("Limpiando archivos temporales...")
        
        # Forzar recolección de basura para que GDAL/QGIS libere los archivos
        import gc
        import time
        gc.collect()
        time.sleep(1)

        try:
            shutil.rmtree(temp_folder)
            feedback.pushInfo("✓ Carpeta temporal eliminada.")
        except Exception as e:
            feedback.pushInfo(f"⚠ No se pudo eliminar la carpeta temporal automáticamente (archivos en uso): {e}. Puede borrarla manualmente: {temp_folder}")

        feedback.pushInfo("================================")
        feedback.pushInfo(f"Proceso finalizado. Archivos guardados en: {out_folder}")
        feedback.pushInfo("================================")

        return {}
