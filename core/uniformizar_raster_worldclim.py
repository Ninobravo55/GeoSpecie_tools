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
    QgsProcessingException,
    QgsRasterLayer,
)
from qgis.PyQt.QtCore import QCoreApplication

class UniformizarRasterWorldClimAlgorithm(QgsProcessingAlgorithm):
    IN_FOLDER = 'IN_FOLDER'
    REF_RASTER = 'REF_RASTER'
    OUT_FOLDER = 'OUT_FOLDER'

    def __init__(self):
        super().__init__()

    def tr(self, string):
        return QCoreApplication.translate('UniformizarRasterWorldClimAlgorithm', string)

    def createInstance(self):
        return UniformizarRasterWorldClimAlgorithm()

    def name(self):
        return 'uniformizar_raster_worldclim'

    def displayName(self):
        return self.tr('Uniformizar raster WorldClim 2.1 Histórico')

    def group(self):
        return self.tr('Preparación de datos MaxEnt')

    def groupId(self):
        return 'preparacion_maxent'

    def shortHelpString(self):
        return self.tr(
            "<b>Descripción:</b><br>"
            "Prepara datos descargados de WorldClim 2.1 Histórico, ajustándolos a un Raster Maestro (MDE), "
            "y exportando a ASCII. Automáticamente detecta las variables en la carpeta:<br><br>"
            "- <b>tmin, tmax, tavg, wind, vapr, srad</b>: Promedia las 12 bandas.<br>"
            "- <b>prec</b>: Suma las 12 bandas.<br>"
            "- <b>bio</b>: Separa las bandas 1 a 19.<br>"
            "- <b>elev</b>: Simplemente recorta/ajusta (1 banda)."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                self.IN_FOLDER,
                self.tr('Directorio de variables WorldClim 2.1 (TIF)'),
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
            QgsProcessingParameterFolderDestination(
                self.OUT_FOLDER,
                self.tr('Carpeta de salida final (exportará ASCII)'),
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        in_folder = self.parameterAsString(parameters, self.IN_FOLDER, context)
        ref_layer = self.parameterAsRasterLayer(parameters, self.REF_RASTER, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)

        if not ref_layer or not ref_layer.isValid():
            raise QgsProcessingException("No se pudo cargar el raster de referencia.")

        # Buscar archivos automáticamente
        archivos = glob.glob(os.path.join(in_folder, "*.tif"))
        variables_encontradas = {}
        for archivo in archivos:
            nombre = os.path.basename(archivo).lower()
            if "tmin" in nombre:
                variables_encontradas["tmin"] = archivo
            elif "tmax" in nombre:
                variables_encontradas["tmax"] = archivo
            elif "tavg" in nombre:
                variables_encontradas["tavg"] = archivo
            elif "wind" in nombre:
                variables_encontradas["wind"] = archivo
            elif "vapr" in nombre:
                variables_encontradas["vapr"] = archivo
            elif "srad" in nombre:
                variables_encontradas["srad"] = archivo
            elif "prec" in nombre:
                variables_encontradas["prec"] = archivo
            elif "bio" in nombre:
                variables_encontradas["bio"] = archivo
            elif "elev" in nombre:
                variables_encontradas["elev"] = archivo

        if not variables_encontradas:
            raise QgsProcessingException("No se encontraron archivos TIF de WorldClim 2.1 (con tmin, tmax, bio, etc.) en el directorio.")

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

        # PARTE 1: Ajustar al MDE
        feedback.pushInfo("\\n--- Ajustando rasters al MDE ---")
        archivos_ajustados = {}
        for var_name, raster in variables_encontradas.items():
            if feedback.isCanceled():
                return {}
            
            nombre = os.path.basename(raster)
            capa = QgsRasterLayer(raster, nombre)
            salida_raster = os.path.join(ajustados, nombre)
            archivos_ajustados[var_name] = salida_raster

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

        # PARTE 2: Separar las bandas y calcular sumas/promedios
        feedback.pushInfo("\\n--- Generando variables finales ---")
        driver = gdal.GetDriverByName("GTiff")
        
        def procesar_bandas(ruta_origen, nombre_base, tipo_calc, n_bandas):
            ds = gdal.Open(ruta_origen)
            salida_calc = os.path.join(variables, nombre_base)
            acum = None
            bandas_count = ds.RasterCount
            
            # Tomar mínimo entre n_bandas y bandas reales en el raster para evitar error si el raster tiene menos
            n_iter = min(n_bandas, bandas_count)
            
            for banda in range(1, n_iter + 1):
                if feedback.isCanceled(): break
                datos = ds.GetRasterBand(banda).ReadAsArray()
                if acum is None:
                    acum = datos.astype(np.float32)
                else:
                    acum += datos
            
            if tipo_calc == 'promedio' and n_iter > 0:
                acum = acum / float(n_iter)
                
            out = driver.Create(
                salida_calc, ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Float32,
                options=["COMPRESS=LZW", "TILED=YES"]
            )
            out.SetProjection(ds.GetProjection())
            out.SetGeoTransform(ds.GetGeoTransform())
            band = out.GetRasterBand(1)
            band.SetNoDataValue(-9999)
            band.WriteArray(acum)
            out.FlushCache()
            out = None
            ds = None
            feedback.pushInfo(f"Generado: {nombre_base} ({tipo_calc})")

        # variables de media (tmin, tmax, tavg, wind, vapr, srad)
        vars_promedio = {"tmin": "tmin_media.tif", "tmax": "tmax_media.tif", 
                         "tavg": "tavg_media.tif", "wind": "wind_media.tif", 
                         "vapr": "vapr_media.tif", "srad": "srad_media.tif"}
                         
        for v, out_name in vars_promedio.items():
            if v in archivos_ajustados:
                procesar_bandas(archivos_ajustados[v], out_name, 'promedio', 12)

        # variables de suma (prec)
        if "prec" in archivos_ajustados:
            procesar_bandas(archivos_ajustados["prec"], "prec_acumulada.tif", 'suma', 12)

        # elev (copia simple, ya está ajustado a MDE)
        if "elev" in archivos_ajustados:
            ds = gdal.Open(archivos_ajustados["elev"])
            datos = ds.GetRasterBand(1).ReadAsArray()
            salida_calc = os.path.join(variables, "elev.tif")
            out = driver.Create(
                salida_calc, ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Float32,
                options=["COMPRESS=LZW", "TILED=YES"]
            )
            out.SetProjection(ds.GetProjection())
            out.SetGeoTransform(ds.GetGeoTransform())
            band = out.GetRasterBand(1)
            band.SetNoDataValue(-9999)
            band.WriteArray(datos)
            out.FlushCache()
            out = None
            ds = None
            feedback.pushInfo("Generado: elev.tif")

        # bio (separar 19 bandas)
        if "bio" in archivos_ajustados:
            ds = gdal.Open(archivos_ajustados["bio"])
            bandas_count = min(19, ds.RasterCount)
            for banda in range(1, bandas_count + 1):
                if feedback.isCanceled(): break
                nombre_bio = os.path.join(variables, f"bio{banda:02d}.tif")
                gdal.Translate(
                    nombre_bio, ds, bandList=[banda],
                    creationOptions=["COMPRESS=LZW", "TILED=YES"],
                    noData=-9999
                )
            ds = None
            feedback.pushInfo("Generado: BIO01-BIO19")

        # PARTE 3: Exportar a ASCII
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
