"""
test_climate_geodata_api.py — geodata_api.py es puro Python (solo
requests/os/time). Se mockea la red; no se golpea geodata.ucdavis.edu.
"""

from unittest.mock import patch, MagicMock

import pytest
import requests

from GeoSpecies_Tools.climate_tiles import geodata_api


class FakeResponse:
    def __init__(self, text="", status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1024):
        data = self.text.encode() if isinstance(self.text, str) else self.text
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]


@pytest.fixture(autouse=True)
def _reset_file_index_cache():
    """El índice de CMIP6 se cachea en memoria a nivel de módulo -- hay
    que resetearlo entre tests para que no se filtren resultados de un
    test mockeado a otro."""
    geodata_api._file_index_cache = None
    yield
    geodata_api._file_index_cache = None


# ---------------------------------------------------------------------
# Construcción de URLs -- regresión directa contra el caso real
# confirmado en github.com/rspatial/geodata/issues/81
# ---------------------------------------------------------------------

def test_build_cmip6_tile_url_matches_confirmed_real_case():
    url = geodata_api.build_cmip6_tile_url("EC-Earth3-Veg", "245", "2041-2060", "bio", 19)
    assert url == (
        "https://geodata.ucdavis.edu/cmip6/tiles/EC-Earth3-Veg/ssp245/"
        "wc2.1_30s_bioc_EC-Earth3-Veg_ssp245_2041-2060_tile-19.tif"
    )


def test_build_cmip6_tile_url_remaps_bio_to_bioc_in_filename():
    url = geodata_api.build_cmip6_tile_url("MIROC6", "585", "2081-2100", "bio", 1)
    assert "bioc" in url
    assert "_bio_" not in url


def test_build_worldclim_tile_url():
    url = geodata_api.build_worldclim_tile_url("tmax", 5)
    assert url == "https://geodata.ucdavis.edu/climate/worldclim/2_1/tiles/tile/tile_5_wc2.1_30s_tmax.tif"


def test_build_worldclim_tile_url_uses_bio_not_bioc():
    url = geodata_api.build_worldclim_tile_url("bio", 1)
    assert "wc2.1_30s_bio.tif" in url


# ---------------------------------------------------------------------
# fetch_cmip6_file_index / check_cmip6_combo
# ---------------------------------------------------------------------

def test_fetch_cmip6_file_index_returns_none_below_sanity_threshold():
    """Menos de MIN_VALID_INDEX_ROWS líneas -> se asume corrupto/incompleto,
    igual que el chequeo `if (nrow(ff) > 1000)` en R."""
    few_lines = "\n".join(["bio_MIROC6_ssp126_2021-2040.tif"] * 5)
    with patch("requests.get", return_value=FakeResponse(text=few_lines)):
        index = geodata_api.fetch_cmip6_file_index()
    assert index is None


def test_fetch_cmip6_file_index_parses_valid_index():
    lines = [f"bio_MIROC6_ssp126_2021-2040.tif"] * geodata_api.MIN_VALID_INDEX_ROWS
    lines[0] = "tmin_MIROC6_ssp585_2081-2100.tif"
    text = "\n".join(lines)
    with patch("requests.get", return_value=FakeResponse(text=text)):
        index = geodata_api.fetch_cmip6_file_index()
    assert index is not None
    assert ("tmin", "MIROC6", "ssp585", "2081-2100.tif") in index


def test_fetch_cmip6_file_index_returns_none_on_network_failure():
    with patch("requests.get", side_effect=requests.ConnectionError("offline")):
        index = geodata_api.fetch_cmip6_file_index()
    assert index is None


def test_check_cmip6_combo_fails_open_when_index_unavailable():
    """Si no se pudo validar (red caída), NO se bloquea al usuario --
    mismo comportamiento que R: fail-open, no fail-closed."""
    with patch("requests.get", side_effect=requests.ConnectionError("offline")):
        ok, msg = geodata_api.check_cmip6_combo("bio", "MIROC6", "585", "2081-2100")
    assert ok is True
    assert msg is None


def test_check_cmip6_combo_true_when_present_in_index():
    lines = [f"tmin_MIROC6_ssp585_2081-2100.tif"] * geodata_api.MIN_VALID_INDEX_ROWS
    with patch("requests.get", return_value=FakeResponse(text="\n".join(lines))):
        ok, msg = geodata_api.check_cmip6_combo("tmin", "MIROC6", "585", "2081-2100")
    assert ok is True
    assert msg is None


def test_check_cmip6_combo_false_when_absent_from_valid_index():
    lines = [f"tmin_MIROC6_ssp585_2081-2100.tif"] * geodata_api.MIN_VALID_INDEX_ROWS
    with patch("requests.get", return_value=FakeResponse(text="\n".join(lines))):
        ok, msg = geodata_api.check_cmip6_combo("bio", "ACCESS-CM2", "126", "2021-2040")
    assert ok is False
    assert "ACCESS-CM2" in msg


# ---------------------------------------------------------------------
# get_remote_file_size
# ---------------------------------------------------------------------

def test_get_remote_file_size_reads_content_length():
    resp = MagicMock(status_code=200, headers={"Content-Length": "123456789"})
    with patch("requests.head", return_value=resp):
        size = geodata_api.get_remote_file_size("https://example.com/x.tif")
    assert size == 123456789


def test_get_remote_file_size_returns_none_on_missing_header_or_error():
    resp = MagicMock(status_code=200, headers={})
    with patch("requests.head", return_value=resp):
        assert geodata_api.get_remote_file_size("https://example.com/x.tif") is None

    with patch("requests.head", side_effect=requests.ConnectionError("offline")):
        assert geodata_api.get_remote_file_size("https://example.com/x.tif") is None


# ---------------------------------------------------------------------
# download_tile -- streaming, cancelación, limpieza de .part
# ---------------------------------------------------------------------

def test_download_tile_writes_file_and_reports_progress(tmp_path):
    session = MagicMock()
    session.get.return_value = FakeResponse(text="X" * 5000, headers={"Content-Length": "5000"})
    output_path = str(tmp_path / "out.tif")
    progress_calls = []

    result = geodata_api.download_tile(
        "https://example.com/x.tif", output_path, session=session,
        progress_cb=lambda d, t: progress_calls.append((d, t)), chunk_size=1000,
    )

    assert result == output_path
    assert (tmp_path / "out.tif").exists()
    assert (tmp_path / "out.tif").stat().st_size == 5000
    assert not (tmp_path / "out.tif.part").exists()
    assert progress_calls[-1] == (5000, 5000)


def test_download_tile_cancels_mid_download_and_cleans_up_partial_file(tmp_path):
    session = MagicMock()
    session.get.return_value = FakeResponse(text="X" * 5000, headers={})
    output_path = str(tmp_path / "out.tif")

    calls = {"n": 0}

    def cancel_check():
        calls["n"] += 1
        return calls["n"] > 2  # cancela a mitad de los chunks

    with pytest.raises(geodata_api.GeodataCanceled):
        geodata_api.download_tile(
            "https://example.com/x.tif", output_path, session=session,
            cancel_check=cancel_check, chunk_size=1000,
        )

    assert not (tmp_path / "out.tif").exists()
    assert not (tmp_path / "out.tif.part").exists()  # se limpia el .part al cancelar


def test_download_tile_raises_geodata_api_error_on_http_failure(tmp_path):
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("no network")
    output_path = str(tmp_path / "out.tif")

    with pytest.raises(geodata_api.GeodataApiError):
        geodata_api.download_tile("https://example.com/x.tif", output_path, session=session)

    assert not (tmp_path / "out.tif.part").exists()
