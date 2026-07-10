"""
test_climate_catalog.py — catalog.py es puro Python. Lo importante acá
es la separación de variables por modo (histórico vs CMIP6): es la
regresión directa del punto señalado en la conversación (wind/srad/
tavg/vapr no existen en CMIP6).
"""

import pytest

from GeoSpecies_Tools.climate_tiles import catalog


def test_cmip6_models_has_26_entries_matching_r_source():
    assert len(catalog.CMIP6_MODELS) == 26
    assert "MIROC6" in catalog.CMIP6_MODELS
    assert "ACCESS-CM2" in catalog.CMIP6_MODELS


def test_cmip6_only_variables_excluded_from_cmip6_mode_if_not_flagged():
    cmip6_vars = {var for var, _ in catalog.variables_for_mode(catalog.MODE_CMIP6)}
    assert "wind" not in cmip6_vars
    assert "srad" not in cmip6_vars
    assert "tavg" not in cmip6_vars
    assert "vapr" not in cmip6_vars
    assert "elev" not in cmip6_vars


def test_cmip6_mode_only_exposes_the_four_supported_variables():
    cmip6_vars = {var for var, _ in catalog.variables_for_mode(catalog.MODE_CMIP6)}
    assert cmip6_vars == {"tmin", "tmax", "prec", "bio"}


def test_historical_mode_exposes_all_nine_variables():
    hist_vars = {var for var, _ in catalog.variables_for_mode(catalog.MODE_HISTORICAL)}
    assert hist_vars == {"tmin", "tmax", "tavg", "prec", "bio", "wind", "srad", "vapr", "elev"}


def test_variables_for_mode_invalid_mode_raises():
    with pytest.raises(ValueError):
        catalog.variables_for_mode("no_existe")


def test_is_variable_valid_for_mode():
    assert catalog.is_variable_valid_for_mode("wind", catalog.MODE_HISTORICAL) is True
    assert catalog.is_variable_valid_for_mode("wind", catalog.MODE_CMIP6) is False
    assert catalog.is_variable_valid_for_mode("no_existe", catalog.MODE_HISTORICAL) is False
