import os
import csv
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterFile,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFolderDestination,
    QgsProcessingException,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsFeature,
    QgsRaster,
    QgsSpatialIndex,
    QgsVectorFileWriter,
    QgsDistanceArea,
    QgsProject,
    QgsCoordinateTransformContext
)
from qgis.PyQt.QtCore import QCoreApplication

class LimpiezaEspeciesMaxEntAlgorithm(QgsProcessingAlgorithm):
    # Parámetros
    INPUT_VECTOR = 'INPUT_VECTOR'
    SPECIES_FIELD = 'SPECIES_FIELD'
    RASTER_FOLDER = 'RASTER_FOLDER'
    
    CLEAN_INVALID_GEOM = 'CLEAN_INVALID_GEOM'
    CLEAN_DUP_COORD = 'CLEAN_DUP_COORD'
    CLEAN_OUT_EXTENT = 'CLEAN_OUT_EXTENT'
    CLEAN_NODATA = 'CLEAN_NODATA'
    CLEAN_DUP_PIXEL = 'CLEAN_DUP_PIXEL'
    CLEAN_EMPTY = 'CLEAN_EMPTY'
    
    MIN_DISTANCE = 'MIN_DISTANCE'
    OUTPUT_FOLDER = 'OUTPUT_FOLDER'

    def __init__(self):
        super().__init__()

    def tr(self, string):
        return QCoreApplication.translate('LimpiezaEspeciesMaxEntAlgorithm', string)

    def createInstance(self):
        return LimpiezaEspeciesMaxEntAlgorithm()

    def name(self):
        return 'limpieza_especies_maxent'

    def displayName(self):
        return self.tr('Limpieza de especies MaxEnt')

    def group(self):
        return self.tr('Preparación de datos MaxEnt')

    def groupId(self):
        return 'preparacion_maxent'

    def shortHelpString(self):
        return self.tr(
            "<b>Descripción:</b><br>"
            "Herramienta para limpieza de registros de especies para MaxEnt.<br>"
            "Permite aplicar múltiples filtros espaciales y tabulares."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT_VECTOR, 
            self.tr('Capa de especies (Shapefile)'), 
            [QgsProcessing.TypeVectorPoint]
        ))
        
        self.addParameter(QgsProcessingParameterField(
            self.SPECIES_FIELD, 
            self.tr('Campo Especie (Nombre especie)'), 
            parentLayerParameterName=self.INPUT_VECTOR
        ))
            
        self.addParameter(QgsProcessingParameterFile(
            self.RASTER_FOLDER, 
            self.tr('Directorio de variables ambientales (MAXENT)'), 
            behavior=QgsProcessingParameterFile.Folder
        ))
            
        self.addParameter(QgsProcessingParameterBoolean(
            self.CLEAN_INVALID_GEOM, 
            self.tr('Eliminar geometrías inválidas'), 
            defaultValue=True
        ))
            
        self.addParameter(QgsProcessingParameterBoolean(
            self.CLEAN_DUP_COORD, 
            self.tr('Eliminar coordenadas duplicadas'), 
            defaultValue=True
        ))
            
        self.addParameter(QgsProcessingParameterBoolean(
            self.CLEAN_OUT_EXTENT, 
            self.tr('Eliminar puntos fuera de la extensión'), 
            defaultValue=True
        ))
            
        self.addParameter(QgsProcessingParameterBoolean(
            self.CLEAN_NODATA, 
            self.tr('Eliminar valores NoData'), 
            defaultValue=True
        ))
            
        self.addParameter(QgsProcessingParameterBoolean(
            self.CLEAN_DUP_PIXEL, 
            self.tr('Eliminar duplicados por pixel'), 
            defaultValue=True
        ))
            
        self.addParameter(QgsProcessingParameterBoolean(
            self.CLEAN_EMPTY, 
            self.tr('Eliminar registros vacíos (sin nombre de especie)'), 
            defaultValue=True
        ))
            
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_DISTANCE, 
            self.tr('Distancia mínima entre puntos (metros)'), 
            type=QgsProcessingParameterNumber.Double, 
            defaultValue=0.0, 
            minValue=0.0
        ))
            
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.OUTPUT_FOLDER, 
            self.tr('Carpeta de Salida')
        ))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT_VECTOR, context)
        vector = self.parameterAsVectorLayer(parameters, self.INPUT_VECTOR, context)
        species_field = self.parameterAsString(parameters, self.SPECIES_FIELD, context)
        raster_folder = self.parameterAsString(parameters, self.RASTER_FOLDER, context)
        
        clean_invalid_geom = self.parameterAsBool(parameters, self.CLEAN_INVALID_GEOM, context)
        clean_dup_coord = self.parameterAsBool(parameters, self.CLEAN_DUP_COORD, context)
        clean_out_extent = self.parameterAsBool(parameters, self.CLEAN_OUT_EXTENT, context)
        clean_nodata = self.parameterAsBool(parameters, self.CLEAN_NODATA, context)
        clean_dup_pixel = self.parameterAsBool(parameters, self.CLEAN_DUP_PIXEL, context)
        clean_empty = self.parameterAsBool(parameters, self.CLEAN_EMPTY, context)
        
        min_distance = self.parameterAsDouble(parameters, self.MIN_DISTANCE, context)
        out_folder = self.parameterAsString(parameters, self.OUTPUT_FOLDER, context)

        if not vector or not vector.isValid():
            raise QgsProcessingException("No se pudo cargar la capa vectorial.")
            
        os.makedirs(out_folder, exist_ok=True)
        salida_shp = os.path.join(out_folder, "Especie_Limpia.shp")
        salida_csv = os.path.join(out_folder, "Especie_Limpia.csv")
        
        # Cargar rasters
        rasters = []
        if os.path.isdir(raster_folder):
            for archivo in os.listdir(raster_folder):
                if archivo.lower().endswith((".tif", ".img", ".bil", ".asc")):
                    ruta = os.path.join(raster_folder, archivo)
                    r = QgsRasterLayer(ruta, archivo)
                    if r.isValid():
                        rasters.append(r)
        
        feedback.pushInfo(f"Rasters válidos encontrados: {len(rasters)}")
        if (clean_out_extent or clean_nodata or clean_dup_pixel) and not rasters:
            raise QgsProcessingException("No se encontraron rasters válidos pero se seleccionó limpieza basada en raster.")

        # Capa temporal en memoria
        crs = vector.crs()
        salida = QgsVectorLayer(f"Point?crs={crs.authid()}", "Limpio", "memory")
        prov = salida.dataProvider()
        prov.addAttributes(vector.fields())
        salida.updateFields()
        
        # Setup
        pixeles = set()
        coords_vistas = set()
        
        da = QgsDistanceArea()
        da.setSourceCrs(crs, context.transformContext())
        da.setEllipsoid(context.project().ellipsoid() if context.project() and context.project().ellipsoid() else 'WGS84')
        
        spatial_index = QgsSpatialIndex() if min_distance > 0 else None
        puntos_validos = {} 
        
        contador_total = 0
        contador_validos = 0
        
        total = source.featureCount()
        step = 100.0 / total if total > 0 else 100
        
        for idx, feat in enumerate(vector.getFeatures()):
            if feedback.isCanceled():
                break
                
            contador_total += 1
            feedback.setProgress(int(idx * step))
            
            # 1. Vacíos
            if clean_empty:
                val = feat[species_field]
                if val is None or str(val).strip() == "":
                    continue
            
            geom = feat.geometry()
            
            # 2. Inválidos
            if clean_invalid_geom:
                if geom is None or geom.isEmpty() or not geom.isGeosValid():
                    continue
            else:
                if geom is None or geom.isEmpty():
                    continue
                    
            punto = geom.asPoint()
            
            # 3. Coordenadas duplicadas
            if clean_dup_coord:
                coord_tuple = (round(punto.x(), 6), round(punto.y(), 6))
                if coord_tuple in coords_vistas:
                    continue
                coords_vistas.add(coord_tuple)
                
            valido = True
            llave_pixel = None
            
            # 4. Raster
            if clean_out_extent or clean_nodata or clean_dup_pixel:
                for r in rasters:
                    extent = r.extent()
                    if clean_out_extent:
                        if not extent.contains(punto):
                            valido = False
                            break
                            
                    if clean_nodata:
                        provider = r.dataProvider()
                        resultado = provider.identify(punto, QgsRaster.IdentifyFormatValue)
                        if not resultado.isValid():
                            valido = False
                            break
                        valores = resultado.results()
                        if not valores:
                            valido = False
                            break
                        valor = list(valores.values())[0]
                        if valor is None:
                            valido = False
                            break
                        if isinstance(valor, float):
                            if r.dataProvider().sourceNoDataValue(1) == valor:
                                valido = False
                                break
                    
                    if clean_dup_pixel and llave_pixel is None:
                        xres = r.rasterUnitsPerPixelX()
                        yres = r.rasterUnitsPerPixelY()
                        col = int((punto.x() - extent.xMinimum()) / xres)
                        row = int((extent.yMaximum() - punto.y()) / yres)
                        llave_pixel = (col, row)
                        
            if not valido:
                continue
                
            if clean_dup_pixel:
                if llave_pixel in pixeles:
                    continue
                pixeles.add(llave_pixel)
                
            # 5. Distancia
            if min_distance > 0:
                vecinos_ids = spatial_index.nearestNeighbor(punto, 1)
                muy_cerca = False
                if vecinos_ids:
                    id_cercano = vecinos_ids[0]
                    punto_cercano = puntos_validos[id_cercano].geometry().asPoint()
                    dist = da.measureLine(punto, punto_cercano)
                    if dist < min_distance:
                        muy_cerca = True
                if muy_cerca:
                    continue
                
                spatial_index.addFeature(feat)
                puntos_validos[feat.id()] = feat
                
            prov.addFeature(feat)
            contador_validos += 1

        salida.updateExtents()
        feedback.pushInfo(f"Puntos originales : {contador_total}")
        feedback.pushInfo(f"Puntos válidos    : {contador_validos}")

        if contador_validos == 0:
            feedback.pushInfo("ADVERTENCIA: No quedaron puntos válidos luego de la limpieza.")

        # Exportar SHP
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "ESRI Shapefile"
        options.fileEncoding = "UTF-8"
        
        QgsVectorFileWriter.writeAsVectorFormatV3(
            salida,
            salida_shp,
            context.transformContext(),
            options
        )
        feedback.pushInfo(f"Shapefile exportado en: {salida_shp}")
        
        # Exportar CSV
        campos = [c.name() for c in salida.fields()]
        with open(salida_csv, "w", newline="", encoding="utf-8") as archivo:
            writer = csv.writer(archivo)
            writer.writerow(campos + ["Longitud", "Latitud"])
            for f in salida.getFeatures():
                p = f.geometry().asPoint()
                fila = [f[c] for c in campos]
                fila.extend([p.x(), p.y()])
                writer.writerow(fila)
                
        feedback.pushInfo(f"CSV exportado en: {salida_csv}")
        
        return {self.OUTPUT_FOLDER: out_folder}
