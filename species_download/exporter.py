"""
exporter.py — Escritura de registros GBIF (dicts crudos de la API) como
capa vectorial de puntos en disco (Shapefile o GeoPackage, según la
extensión de txtOutputName).
"""

import os

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

from ..utils.qt_compat import QMETATYPE_STRING, QMETATYPE_INT, QMETATYPE_DOUBLE

# Columnas exportadas por registro: (nombre, tipo, campo fuente en el
# dict de GBIF, ancho máximo para campos de texto).
#
# El ancho SÍ se pasa explícitamente a QgsField ahora -- antes el
# comentario decía "se mantienen acotadas" pero el código nunca pasaba
# `len` a QgsField, así que el ancho real quedaba a criterio del driver
# OGR (255 por defecto en muchos casos, pero no garantizado igual entre
# versiones/drivers). sciName en particular puede superar 254 caracteres
# con autoría taxonómica larga; se trunca explícitamente en Python en
# record_to_feature() para que el truncamiento sea predecible y visible
# en el propio dato, en vez de un corte silencioso a nivel de driver.
FIELD_SPECS = [
    ("gbifID", QMETATYPE_STRING, "key", 20),
    ("species", QMETATYPE_STRING, "species", 254),
    ("sciName", QMETATYPE_STRING, "scientificName", 254),
    ("kingdom", QMETATYPE_STRING, "kingdom", 50),
    ("country", QMETATYPE_STRING, "countryCode", 10),
    ("basisRec", QMETATYPE_STRING, "basisOfRecord", 50),
    ("eventDate", QMETATYPE_STRING, "eventDate", 30),
    ("year", QMETATYPE_INT, "year", None),
    ("uncertM", QMETATYPE_DOUBLE, "coordinateUncertaintyInMeters", None),
    ("lat", QMETATYPE_DOUBLE, "decimalLatitude", None),
    ("lon", QMETATYPE_DOUBLE, "decimalLongitude", None),
]


class ExportError(Exception):
    """Error al escribir la capa de salida."""


def build_fields():
    """Construye el QgsFields de salida según FIELD_SPECS, con ancho
    explícito en los campos de texto (antes no se pasaba)."""
    fields = QgsFields()
    for name, qtype, _src_key, width in FIELD_SPECS:
        if width:
            fields.append(QgsField(name, qtype, len=width))
        else:
            fields.append(QgsField(name, qtype))
    return fields


def record_to_feature(record, fields):
    """
    Convierte un dict crudo de GBIF en un QgsFeature de punto.
    Devuelve None si el registro no tiene coordenadas válidas.
    Los campos de texto se truncan explícitamente al ancho de
    FIELD_SPECS antes de asignarlos (no se deja que el driver OGR
    trunque en silencio sin que quede constancia).
    """
    lat = record.get("decimalLatitude")
    lon = record.get("decimalLongitude")
    if lat is None or lon is None:
        return None

    feat = QgsFeature(fields)
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(lon), float(lat))))

    for name, _qtype, src_key, width in FIELD_SPECS:
        value = record.get(src_key)
        if value is None:
            value = ""
        if width and isinstance(value, str) and len(value) > width:
            value = value[:width]
        feat.setAttribute(name, value)

    return feat


def export_points(records, output_path, target_crs_authid=None):
    """
    Escribe `records` (lista de dicts crudos de GBIF) como capa de puntos
    en `output_path`. El formato de salida se decide por la extensión del
    archivo (.shp -> ESRI Shapefile, .gpkg -> GeoPackage; cualquier otra
    extensión soportada por GDAL/OGR también funciona vía QgsVectorFileWriter).

    Parámetros
    ----------
    target_crs_authid : str | None
        CRS de salida (ej. 'EPSG:32718', UTM 18S — típico en balance
        hídrico/SWAT para Perú). GBIF entrega decimalLatitude/
        decimalLongitude en WGS84 (EPSG:4326); si target_crs_authid es
        None, inválido, o coincide con EPSG:4326, no se reproyecta nada
        y la capa sale en 4326 (comportamiento anterior, sin cambios).
        Si difiere, se reproyecta cada punto antes de escribir.

    Retorna
    -------
    tuple(str, int)
        (ruta final del archivo escrito, número de features escritas).

    Lanza
    -----
    ExportError
        Si QgsVectorFileWriter reporta un error de escritura.
    """
    fields = build_fields()

    source_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    target_crs = QgsCoordinateReferenceSystem(target_crs_authid) if target_crs_authid else source_crs
    if not target_crs.isValid():
        target_crs = source_crs

    needs_transform = target_crs.authid() != source_crs.authid()
    xform = None
    if needs_transform:
        xform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance().transformContext())

    ext = os.path.splitext(output_path)[1].lstrip(".").upper()
    driver_map = {"SHP": "ESRI Shapefile", "GPKG": "GPKG"}
    driver_name = driver_map.get(ext, "ESRI Shapefile")

    # Se usa una capa de memoria + writeAsVectorFormatV3 en vez de
    # QgsVectorFileWriter.create(): esa sobrecarga exige transformContext
    # como argumento posicional y, en las bindings SIP de varias versiones
    # de PyQGIS, 'options' tampoco se acepta como keyword — falla con
    # "'options' is not a valid keyword argument". La ruta de memoria es
    # equivalente en resultado y más estable entre QGIS 3.28 y 4.x.
    mem_layer = QgsVectorLayer(f"Point?crs={target_crs.authid()}", "gbif_occurrences", "memory")
    provider = mem_layer.dataProvider()
    provider.addAttributes(fields)
    mem_layer.updateFields()

    feats = []
    for record in records:
        feat = record_to_feature(record, mem_layer.fields())
        if feat is None:
            continue
        if xform is not None:
            geom = feat.geometry()
            geom.transform(xform)
            feat.setGeometry(geom)
        feats.append(feat)
    provider.addFeatures(feats)
    mem_layer.updateExtents()

    save_options = QgsVectorFileWriter.SaveVectorOptions()
    save_options.driverName = driver_name
    save_options.fileEncoding = "UTF-8"

    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        mem_layer,
        output_path,
        QgsProject.instance().transformContext(),
        save_options,
    )

    # writeAsVectorFormatV3 devuelve una tupla (error_code, error_message, ...)
    error_code = result[0] if isinstance(result, tuple) else result
    if error_code != QgsVectorFileWriter.NoError:
        error_msg = result[1] if isinstance(result, tuple) and len(result) > 1 else str(result)
        raise ExportError(f"No se pudo escribir la capa de salida: {error_msg}")

    return output_path, len(feats)
