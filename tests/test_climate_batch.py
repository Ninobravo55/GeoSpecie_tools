"""
test_climate_batch.py — batch.py orquesta sin depender de Qt. El caso
lon/lat directo (resolve_centroid) no requiere qgis.core en absoluto
(el import de geom.py es diferido, solo se dispara con aoi_layer).
"""

import pytest

from GeoSpecies_Tools.climate_tiles import batch, catalog, geodata_api, tiles


# ---------------------------------------------------------------------
# resolve_centroid -- caso punto (sin qgis)
# ---------------------------------------------------------------------

def test_resolve_centroid_with_explicit_lonlat_returns_no_bbox():
    lon, lat, bbox = batch.resolve_centroid(lon=-77.05, lat=-12.05)
    assert lon == -77.05
    assert lat == -12.05
    assert bbox is None


def test_resolve_centroid_without_lonlat_or_layer_raises():
    with pytest.raises(batch.ClimateTileError):
        batch.resolve_centroid()


# ---------------------------------------------------------------------
# check_multi_tile_warning
# ---------------------------------------------------------------------

def test_check_multi_tile_warning_none_for_point_case():
    assert batch.check_multi_tile_warning(None) is None


def test_check_multi_tile_warning_none_when_single_tile():
    # bbox chico, bien adentro de un solo tile
    assert batch.check_multi_tile_warning((-179, 89, -151, 61)) is None


def test_check_multi_tile_warning_present_when_multiple_tiles():
    msg = batch.check_multi_tile_warning((-160, 50, -140, 70))  # cruza 4 tiles
    assert msg is not None
    assert "4 tiles" in msg
    assert "CENTROIDE" in msg


# ---------------------------------------------------------------------
# build_download_plan
# ---------------------------------------------------------------------

def test_build_download_plan_historical_no_network_validation_needed(monkeypatch):
    plan = batch.build_download_plan(catalog.MODE_HISTORICAL, ["tmax", "prec"], tile_id=5)
    assert len(plan) == 2
    urls = {item["var"]: item["url"] for item in plan}
    assert "tile_5_wc2.1_30s_tmax.tif" in urls["tmax"]
    assert "tile_5_wc2.1_30s_prec.tif" in urls["prec"]


def test_build_download_plan_rejects_variable_not_valid_for_mode():
    with pytest.raises(batch.ClimateTileError):
        batch.build_download_plan(catalog.MODE_CMIP6, ["wind"], tile_id=5, model="MIROC6", ssp="585", period="2081-2100")


def test_build_download_plan_cmip6_requires_model_ssp_period(monkeypatch):
    monkeypatch.setattr(geodata_api, "check_cmip6_combo", lambda *a, **kw: (True, None))
    with pytest.raises(batch.ClimateTileError):
        batch.build_download_plan(catalog.MODE_CMIP6, ["bio"], tile_id=5)  # sin model/ssp/period


def test_build_download_plan_cmip6_calls_check_combo_and_builds_url(monkeypatch):
    captured = {}

    def _fake_check(var, model, ssp, period):
        captured["args"] = (var, model, ssp, period)
        return True, None

    monkeypatch.setattr(geodata_api, "check_cmip6_combo", _fake_check)

    plan = batch.build_download_plan(
        catalog.MODE_CMIP6, ["bio"], tile_id=19, model="EC-Earth3-Veg", ssp="245", period="2041-2060",
    )
    assert captured["args"] == ("bio", "EC-Earth3-Veg", "245", "2041-2060")
    assert len(plan) == 1
    assert "tile-19" in plan[0]["url"]
    assert "bioc" in plan[0]["url"]


def test_build_download_plan_raises_when_combo_does_not_exist(monkeypatch):
    monkeypatch.setattr(geodata_api, "check_cmip6_combo", lambda *a, **kw: (False, "no existe esta combinación"))
    with pytest.raises(batch.ClimateTileError):
        batch.build_download_plan(
            catalog.MODE_CMIP6, ["bio"], tile_id=1, model="MIROC6", ssp="126", period="2021-2040",
        )


# ---------------------------------------------------------------------
# run_tile_downloads
# ---------------------------------------------------------------------

def test_run_tile_downloads_happy_path(monkeypatch, tmp_path):
    def _fake_download(url, output_path, **kwargs):
        with open(output_path, "wb") as f:
            f.write(b"fake tif")
        return output_path

    monkeypatch.setattr(geodata_api, "download_tile", _fake_download)

    plan = [{"var": "tmax", "url": "https://x/tmax.tif"}, {"var": "prec", "url": "https://x/prec.tif"}]
    results = batch.run_tile_downloads(plan, str(tmp_path), "clima")

    assert len(results) == 2
    assert all(r["error"] is None and not r["canceled"] for r in results)
    assert all(r["path"].endswith(f"clima_{r['var']}.tif") for r in results)


def test_run_tile_downloads_continues_after_individual_error(monkeypatch, tmp_path):
    def _fake_download(url, output_path, **kwargs):
        if "prec" in url:
            raise geodata_api.GeodataApiError("404 not found")
        with open(output_path, "wb") as f:
            f.write(b"ok")
        return output_path

    monkeypatch.setattr(geodata_api, "download_tile", _fake_download)

    plan = [{"var": "tmax", "url": "https://x/tmax.tif"}, {"var": "prec", "url": "https://x/prec.tif"}]
    results = batch.run_tile_downloads(plan, str(tmp_path), "clima")

    ok = [r for r in results if r["error"] is None and not r["canceled"]]
    failed = [r for r in results if r["error"]]
    assert len(ok) == 1
    assert len(failed) == 1
    assert "404" in failed[0]["error"]


def test_run_tile_downloads_stops_remaining_items_when_already_canceled(monkeypatch, tmp_path):
    call_count = {"n": 0}

    def _fake_download(url, output_path, **kwargs):
        call_count["n"] += 1
        with open(output_path, "wb") as f:
            f.write(b"ok")
        return output_path

    monkeypatch.setattr(geodata_api, "download_tile", _fake_download)

    plan = [{"var": "tmax", "url": "https://x/tmax.tif"}, {"var": "prec", "url": "https://x/prec.tif"}]
    results = batch.run_tile_downloads(plan, str(tmp_path), "clima", cancel_check=lambda: True)

    assert call_count["n"] == 0  # ni siquiera intenta descargar si ya está cancelado
    assert all(r["canceled"] for r in results)
