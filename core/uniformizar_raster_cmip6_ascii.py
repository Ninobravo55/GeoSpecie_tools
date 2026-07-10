import os
import glob
import shutil
import numpy as np

from osgeo import gdal
import processing

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterBoolean,
    QgsProcessingException,
    QgsRasterLayer,
)
from qgis.PyQt.QtCore import QCoreApplication

class UniformizarRasterCmip6AsciiAlgorithm(QgsProcessingAlgorithm):
    IN_FOLDER = 'IN_FOLDER'
    REF_RASTER = 'REF_RASTER'
    GEN_BIO = 'GEN_BIO'
    GEN_PREC = 'GEN_PREC'
    GEN_TMIN = 'GEN_TMIN'
    GEN_TMAX = 'GEN_TMAX'
    OUT_FOLDER = 'OUT_FOLDER'

    def __init__(self):
        super().__init__()

    def tr(self, string):
        return QCoreApplication.translate('UniformizarRasterCmip6AsciiAlgorithm', string)

    def createInstance(self):
        return UniformizarRasterCmip6AsciiAlgorithm()

    def name(self):
        return 'uniformizar_raster_cmip6_ascii'

    def displayName(self):
        return self.tr('Uniformizar raster CMIP6 ASCII')

    def group(self):
        return self.tr('Preparación de datos MaxEnt')

    def groupId(self):
        return 'preparacion_maxent'

    def shortHelpString(self):
        return self.tr(
            "<b>Descripción:</b><br>"
            "Prepara datos descargados de CMIP6, ajustándolos a un Raster Maestro (MDE), separando las bandas, "
            "calculando promedios/sumatorias según corresponda, y exportando a ASCII.<br><br>"
            "<b>Generación:</b><br>"
            "- BIO: Separa las bandas 1 a 19.<br>"
            "- PREC: Suma las 12 bandas.<br>"
            "- TMIN/TMAX: Promedia las 12 bandas."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                self.IN_FOLDER,
                self.tr('Directorio CMIP6'),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.REF_RASTER,
                self.tr('Raster de referencia (MDE)'),
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.GEN_BIO,
                self.tr('Variables Bioclimáticas (BIO1-BIO19)'),
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.GEN_PREC,
                self.tr('Precipitación anual'),
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.GEN_TMIN,
                self.tr('Temperatura mínima media'),
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.GEN_TMAX,
                self.tr('Temperatura máxima media'),
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUT_FOLDER,
                self.tr('Carpeta de salida'),
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        in_folder = self.parameterAsString(parameters, self.IN_FOLDER, context)
        ref_layer = self.parameterAsRasterLayer(parameters, self.REF_RASTER, context)
        gen_bio = self.parameterAsBool(parameters, self.GEN_BIO, context)
        gen_prec = self.parameterAsBool(parameters, self.GEN_PREC, context)
        gen_tmin = self.parameterAsBool(parameters, self.GEN_TMIN, context)
        gen_tmax = self.parameterAsBool(parameters, self.GEN_TMAX, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)

        if not ref_layer or not ref_layer.isValid():
            raise QgsProcessingException("No se pudo cargar el raster de referencia.")

        # Buscar archivos
        bio, prec, tmin, tmax = None, None, None, None
        archivos = glob.glob(os.path.join(in_folder, "*.tif"))
        for archivo in archivos:
            nombre = os.path.basename(archivo).lower()
            if "_bio" in nombre:
                bio = archivo
            elif "_prec" in nombre:
                prec = archivo
            elif "_tmin" in nombre:
                tmin = archivo
            elif "_tmax" in nombre:
                tmax = archivo

        # Validar archivos requeridos
        if gen_bio and bio is None:
            raise QgsProcessingException("No se encontró archivo BIO en el directorio de entrada.")
        if gen_prec and prec is None:
            raise QgsProcessingException("No se encontró archivo PREC en el directorio de entrada.")
        if gen_tmin and tmin is None:
            raise QgsProcessingException("No se encontró archivo TMIN en el directorio de entrada.")
        if gen_tmax and tmax is None:
            raise QgsProcessingException("No se encontró archivo TMAX en el directorio de entrada.")

        # Info de referencia
        extent = ref_layer.extent()
        crs = ref_layer.crs()
        pixel_x = ref_layer.rasterUnitsPerPixelX()
        pixel_y = ref_layer.rasterUnitsPerPixelY()
        
        feedback.pushInfo(f"Referencia CRS: {crs.authid()} | Res: {pixel_x}, {pixel_y}")

        # Carpetas de trabajo
        salida = out_folder
        ajustados = os.path.join(salida, "01_Ajustados_MDE")
        variables = os.path.join(salida, "02_Variables")
        asc_dir = os.path.join(salida, "03_ASC")
        
        for d in [salida, ajustados, variables, asc_dir]:
            os.makedirs(d, exist_ok=True)

        rasters_a_procesar = []
        if gen_bio: rasters_a_procesar.append(bio)
        if gen_prec: rasters_a_procesar.append(prec)
        if gen_tmin: rasters_a_procesar.append(tmin)
        if gen_tmax: rasters_a_procesar.append(tmax)

        # PARTE 2: Ajustar al MDE
        feedback.pushInfo("\\n--- Ajustando rasters al MDE ---")
        archivos_ajustados = {}
        for raster in rasters_a_procesar:
            if feedback.isCanceled():
                return {}
            
            nombre = os.path.basename(raster)
            capa = QgsRasterLayer(raster, nombre)
            salida_raster = os.path.join(ajustados, nombre)
            archivos_ajustados[raster] = salida_raster

            mismo_crs = (capa.crs() == crs)
            misma_res = (abs(capa.rasterUnitsPerPixelX() - pixel_x) < 1e-10 and abs(capa.rasterUnitsPerPixelY() - pixel_y) < 1e-10)
            misma_extension = (capa.extent() == extent)

            if mismo_crs and misma_res and misma_extension:
                feedback.pushInfo(f"{nombre}: Ya coincide con el MDE.")
                shutil.copy2(raster, salida_raster)
            else:
                feedback.pushInfo(f"{nombre}: Ajustando...")
                opciones = gdal.WarpOptions(
                    dstSRS=crs.authid(),
                    xRes=pixel_x,
                    yRes=pixel_y,
                    outputBounds=(extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()),
                    resampleAlg="bilinear",
                    targetAlignedPixels=True,
                    dstNodata=-9999,
                    multithread=True,
                    creationOptions=["COMPRESS=LZW", "TILED=YES"]
                )
                gdal.Warp(salida_raster, raster, options=opciones)

        # PARTE 3: Separar las bandas y calcular sumas/promedios
        feedback.pushInfo("\\n--- Generando variables ---")
        driver = gdal.GetDriverByName("GTiff")
        
        def procesar_bandas(ruta_origen, nombre_base, tipo_calc, n_bandas):
            ds = gdal.Open(ruta_origen)
            salida_calc = os.path.join(variables, nombre_base)
            acum = None
            for banda in range(1, n_bandas + 1):
                if feedback.isCanceled(): break
                datos = ds.GetRasterBand(banda).ReadAsArray()
                if acum is None:
                    acum = datos.astype(np.float32)
                else:
                    acum += datos
            
            if tipo_calc == 'promedio':
                acum = acum / float(n_bandas)
                
            out = driver.Create(
                salida_calc, ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Float32,
                options=["COMPRESS=LZW", "TILED=YES"]
            )
            out.SetProjection(ds.GetProjection())
            out.SetGeoTransform(ds.GetGeoTransform())
            band = out.GetRasterBand(1)
            band.SetNoDataValue(-9999) # Asignamos nodata standard
            band.WriteArray(acum)
            out.FlushCache()
            out = None
            ds = None
            feedback.pushInfo(f"Generado: {nombre_base}")

        if gen_bio:
            bio_ajustado = archivos_ajustados[bio]
            ds = gdal.Open(bio_ajustado)
            for banda in range(1, 20):
                if feedback.isCanceled(): break
                nombre_bio = os.path.join(variables, f"bio{banda:02d}.tif")
                gdal.Translate(
                    nombre_bio, ds, bandList=[banda],
                    creationOptions=["COMPRESS=LZW", "TILED=YES"]
                )
            ds = None
            feedback.pushInfo("Generado: BIO01-BIO19")

        if gen_prec:
            prec_ajustado = archivos_ajustados[prec]
            procesar_bandas(prec_ajustado, "prec_anual.tif", 'suma', 12)
            
        if gen_tmin:
            tmin_ajustado = archivos_ajustados[tmin]
            procesar_bandas(tmin_ajustado, "tmin_media.tif", 'promedio', 12)
            
        if gen_tmax:
            tmax_ajustado = archivos_ajustados[tmax]
            procesar_bandas(tmax_ajustado, "tmax_media.tif", 'promedio', 12)

        # PARTE 5: Exportar a ASCII
        feedback.pushInfo("\\n--- Exportando a ASCII ---")
        rasters_variables = sorted(glob.glob(os.path.join(variables, "*.tif")))
        
        for raster in rasters_variables:
            if feedback.isCanceled(): break
            nombre = os.path.splitext(os.path.basename(raster))[0]
            salida_asc = os.path.join(asc_dir, nombre + ".asc")
            ds = gdal.Open(raster)
            opciones = gdal.TranslateOptions(
                format="AAIGrid",
                creationOptions=[],
                noData=-9999
            )
            gdal.Translate(salida_asc, ds, options=opciones)
            ds = None
            feedback.pushInfo(f"Exportado: {nombre}.asc")

        feedback.pushInfo("\\nProceso finalizado correctamente.")
        return {}
