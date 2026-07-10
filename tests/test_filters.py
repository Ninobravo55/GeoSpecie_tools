"""
test_filters.py — filters.py es 100% puro Python (sin imports de qgis
ni de red). Se testea directo, sin mocks.
"""

from GeoSpecies_Tools.species_download import filters


# ---------------------------------------------------------------------
# build_query_params
# ---------------------------------------------------------------------

def test_build_query_params_world_has_no_spatial_filter():
    params = filters.build_query_params(
        species_key=123, year_min=1990, year_max=2025, uncertainty_m=1000,
        only_valid_coordinates=True, no_geo_issues=True,
    )
    assert "country" not in params
    assert "geometry" not in params
    assert params["taxonKey"] == 123
    assert params["year"] == "1990,2025"


def test_build_query_params_country_sets_country_not_geometry():
    params = filters.build_query_params(
        species_key=123, year_min=1990, year_max=2025, uncertainty_m=1000,
        only_valid_coordinates=True, no_geo_issues=True, country_code="PE",
    )
    assert params["country"] == "PE"
    assert "geometry" not in params


def test_build_query_params_geometry_takes_priority_over_country():
    """
    Si por algún motivo llegan ambos (no debería pasar desde el diálogo,
    pero es una garantía barata de tener), geometry_wkt gana: es la
    delimitación más específica.
    """
    params = filters.build_query_params(
        species_key=123, year_min=1990, year_max=2025, uncertainty_m=1000,
        only_valid_coordinates=True, no_geo_issues=True,
        country_code="PE", geometry_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))",
    )
    assert "geometry" in params
    assert "country" not in params


def test_build_query_params_uncertainty_zero_omits_filter():
    """
    uncertainty_m=0 significa 'sin límite' (así lo interpreta el
    diálogo tras el fix de este mismo hilo) -- no debe mandarse
    coordinateUncertaintyInMeters=0,0, que excluiría casi todo.
    """
    params = filters.build_query_params(
        species_key=123, year_min=1990, year_max=2025, uncertainty_m=0,
        only_valid_coordinates=True, no_geo_issues=True,
    )
    assert "coordinateUncertaintyInMeters" not in params


def test_build_query_params_flags_only_set_when_true():
    params = filters.build_query_params(
        species_key=123, year_min=1990, year_max=2025, uncertainty_m=1000,
        only_valid_coordinates=False, no_geo_issues=False,
    )
    assert "hasCoordinate" not in params
    assert "hasGeospatialIssue" not in params


# ---------------------------------------------------------------------
# is_living_record
# ---------------------------------------------------------------------

def test_is_living_record_excludes_fossil_and_preserved():
    assert filters.is_living_record({"basisOfRecord": "FOSSIL_SPECIMEN"}) is False
    assert filters.is_living_record({"basisOfRecord": "PRESERVED_SPECIMEN"}) is False


def test_is_living_record_includes_observations_and_living_specimens():
    for basis in ("HUMAN_OBSERVATION", "MACHINE_OBSERVATION", "OBSERVATION", "LIVING_SPECIMEN"):
        assert filters.is_living_record({"basisOfRecord": basis}) is True


def test_is_living_record_handles_missing_field():
    assert filters.is_living_record({}) is True  # sin dato -> no se excluye por este filtro


# ---------------------------------------------------------------------
# dedupe_records
# ---------------------------------------------------------------------

def test_dedupe_records_removes_exact_spatiotemporal_duplicates():
    records = [
        {"decimalLatitude": -12.05, "decimalLongitude": -77.05, "eventDate": "2020-01-01", "speciesKey": 1},
        {"decimalLatitude": -12.05, "decimalLongitude": -77.05, "eventDate": "2020-01-01", "speciesKey": 1},
    ]
    result = filters.dedupe_records(records)
    assert len(result) == 1


def test_dedupe_records_keeps_records_with_different_dates():
    """
    Caso que señalé como riesgo real: grillas de cámaras trampa con el
    mismo punto pero fechas distintas NO deben perderse.
    """
    records = [
        {"decimalLatitude": -12.05, "decimalLongitude": -77.05, "eventDate": "2020-01-01", "speciesKey": 1},
        {"decimalLatitude": -12.05, "decimalLongitude": -77.05, "eventDate": "2020-01-02", "speciesKey": 1},
    ]
    result = filters.dedupe_records(records)
    assert len(result) == 2


def test_dedupe_records_rounds_coordinates_before_comparing():
    records = [
        {"decimalLatitude": -12.050000001, "decimalLongitude": -77.05, "eventDate": "2020-01-01", "speciesKey": 1},
        {"decimalLatitude": -12.050000002, "decimalLongitude": -77.05, "eventDate": "2020-01-01", "speciesKey": 1},
    ]
    result = filters.dedupe_records(records)
    assert len(result) == 1  # misma coordenada al redondear a 6 decimales
