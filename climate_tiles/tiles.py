"""
tiles.py — Matemática de la grilla de tiles de WorldClim/CMIP6.

Los tiles NO son pequeños: son celdas de 30 GRADOS (no 30 arco-segundos
-- ese "30s" es la resolución del píxel DENTRO de cada tile, algo
completamente distinto). La grilla global es de 12 columnas x 6 filas
= 72 tiles, calculada por el paquete R `geodata` con
`terra::rast(res=30)` + `terra::cellFromXY(...)`.

Esta función replica EXACTAMENTE esa numeración (fila-mayor, 1-based,
empezando arriba-izquierda) porque el ID de tile va embebido en el
nombre de archivo real en el servidor -- si el número no coincide
exactamente con el que usa terra, la URL construida apunta a un
archivo que no existe. Verificado contra un caso real reportado en
github.com/rspatial/geodata/issues/81: la URL
".../cmip6/tiles/EC-Earth3-Veg/ssp245/wc2.1_30s_bioc_EC-Earth3-Veg_ssp245_2041-2060_tile-19.tif"
sí es un tile-id real y descargable.
"""

TILE_SIZE_DEG = 30.0  # tamaño de celda en GRADOS (no confundir con "30s" = 30 arco-segundos del píxel)
GRID_XMIN, GRID_XMAX = -180.0, 180.0
GRID_YMIN, GRID_YMAX = -90.0, 90.0

NCOLS = int(round((GRID_XMAX - GRID_XMIN) / TILE_SIZE_DEG))  # 12
NROWS = int(round((GRID_YMAX - GRID_YMIN) / TILE_SIZE_DEG))  # 6


class TileError(Exception):
    """Coordenadas fuera de rango o entrada inválida para el cálculo de tile."""


def tile_id_from_lonlat(lon, lat):
    """
    Calcula el tile ID (1-based, fila-mayor desde arriba-izquierda) que
    contiene el punto (lon, lat) -- exactamente la misma convención que
    terra::cellFromXY(rast(res=30), cbind(lon, lat)) en R.

    Parámetros
    ----------
    lon, lat : float
        Coordenadas geográficas (EPSG:4326).

    Retorna
    -------
    int
        Tile ID entre 1 y 72.

    Lanza
    -----
    TileError
        Si lon/lat están fuera del rango [-180,180] / [-90,90].
    """
    if not (GRID_XMIN <= lon <= GRID_XMAX):
        raise TileError(f"Longitud fuera de rango: {lon} (debe estar en [-180, 180])")
    if not (GRID_YMIN <= lat <= GRID_YMAX):
        raise TileError(f"Latitud fuera de rango: {lat} (debe estar en [-90, 90])")

    col = int((lon - GRID_XMIN) // TILE_SIZE_DEG)
    col = min(col, NCOLS - 1)  # clamp: lon == 180 no debe desbordar a una columna inexistente

    row = int((GRID_YMAX - lat) // TILE_SIZE_DEG)
    row = min(row, NROWS - 1)  # clamp: lat == -90 no debe desbordar

    return row * NCOLS + col + 1  # 1-based, como en R/terra


def tile_bbox(tile_id):
    """
    Devuelve el bounding box (xmin, ymin, xmax, ymax) del tile `tile_id`,
    en grados, EPSG:4326.

    Lanza
    -----
    TileError
        Si tile_id no está en [1, 72].
    """
    if not (1 <= tile_id <= NCOLS * NROWS):
        raise TileError(f"tile_id inválido: {tile_id} (debe estar entre 1 y {NCOLS * NROWS})")

    idx = tile_id - 1
    row = idx // NCOLS
    col = idx % NCOLS

    xmin = GRID_XMIN + col * TILE_SIZE_DEG
    xmax = xmin + TILE_SIZE_DEG
    ymax = GRID_YMAX - row * TILE_SIZE_DEG
    ymin = ymax - TILE_SIZE_DEG

    return (xmin, ymin, xmax, ymax)


def tiles_spanned_by_bbox(xmin, ymin, xmax, ymax):
    """
    Devuelve el conjunto de tile IDs que una caja delimitadora
    (xmin, ymin, xmax, ymax) toca, sin necesidad de una geometría real.

    Uso principal: verificar SIN descargar nada si un polígono cae en
    un solo tile o cruza varios -- para poder advertir al usuario antes
    de aceptar la limitación de "solo tile del centroide" (ver
    climate_tiles_dialog.py). Se evalúan las 4 esquinas de la caja; no
    hace falta muestrear el interior porque la grilla es regular: si
    las 4 esquinas caen en el mismo tile, toda la caja está en ese tile.
    """
    corners = [(xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax)]
    return {tile_id_from_lonlat(x, y) for x, y in corners}
