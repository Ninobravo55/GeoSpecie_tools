"""
test_climate_tiles.py — tiles.py es 100% puro Python (sin qgis, sin
red). La numeración de tiles se verifica contra el cálculo manual de la
grilla de 12x6 (30° por celda), que es la misma convención de
terra::cellFromXY(rast(res=30), xy) usada por el paquete R `geodata`.
"""

import pytest

from GeoSpecies_Tools.climate_tiles import tiles


def test_grid_dimensions():
    assert tiles.NCOLS == 12
    assert tiles.NROWS == 6


def test_tile_id_four_corners_of_the_globe():
    # esquina noroeste: fila 0, columna 0 -> id 1
    assert tiles.tile_id_from_lonlat(-180, 90) == 1
    # esquina noreste: fila 0, última columna (clamp en lon=180) -> id 12
    assert tiles.tile_id_from_lonlat(180, 90) == 12
    # esquina suroeste: última fila (clamp en lat=-90), columna 0 -> id 61
    assert tiles.tile_id_from_lonlat(-180, -90) == 61
    # esquina sureste: última fila, última columna -> id 72
    assert tiles.tile_id_from_lonlat(180, -90) == 72


def test_tile_id_center_of_grid():
    # (0,0) cae justo en el borde entre 4 tiles; con floor() debe caer
    # de forma determinística en un tile específico, no lanzar error.
    tid = tiles.tile_id_from_lonlat(0.0, 0.0)
    assert 1 <= tid <= 72


def test_tile_id_out_of_range_raises():
    with pytest.raises(tiles.TileError):
        tiles.tile_id_from_lonlat(-200, 0)
    with pytest.raises(tiles.TileError):
        tiles.tile_id_from_lonlat(0, 95)


def test_tile_bbox_of_tile_1_matches_nw_corner():
    xmin, ymin, xmax, ymax = tiles.tile_bbox(1)
    assert xmin == -180
    assert xmax == -150
    assert ymax == 90
    assert ymin == 60


def test_tile_bbox_invalid_id_raises():
    with pytest.raises(tiles.TileError):
        tiles.tile_bbox(0)
    with pytest.raises(tiles.TileError):
        tiles.tile_bbox(73)


def test_tile_bbox_roundtrip_for_all_tiles():
    """El centro de cada tile debe mapear de vuelta al mismo tile_id."""
    for tid in range(1, tiles.NCOLS * tiles.NROWS + 1):
        xmin, ymin, xmax, ymax = tiles.tile_bbox(tid)
        cx = (xmin + xmax) / 2
        cy = (ymin + ymax) / 2
        assert tiles.tile_id_from_lonlat(cx, cy) == tid


def test_tiles_spanned_by_bbox_single_tile():
    # una caja bien adentro del tile 1 (-180,60,-150,90), sin tocar bordes
    spanned = tiles.tiles_spanned_by_bbox(-179, 89, -151, 61)
    assert spanned == {1}


def test_tiles_spanned_by_bbox_crossing_column_boundary():
    # cruza el borde de columna en lon=-150 (entre tile 1 y tile 2)
    spanned = tiles.tiles_spanned_by_bbox(-160, 70, -140, 80)
    assert spanned == {1, 2}


def test_tiles_spanned_by_bbox_crossing_row_boundary():
    # cruza el borde de fila en lat=60 (entre fila 0 y fila 1)
    spanned = tiles.tiles_spanned_by_bbox(-170, 50, -160, 70)
    assert len(spanned) == 2


def test_tiles_spanned_by_bbox_crossing_four_tiles():
    # una caja que cruza tanto el borde de columna como el de fila
    spanned = tiles.tiles_spanned_by_bbox(-160, 50, -140, 70)
    assert len(spanned) == 4
