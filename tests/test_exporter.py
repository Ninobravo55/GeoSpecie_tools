"""
test_exporter.py — Cubre build_fields() y record_to_feature(): la parte
de exporter.py que es lógica propia (anchos de campo, truncamiento).

NO se testea export_points() aquí: requiere QgsVectorFileWriter /
QgsVectorLayer reales para escribir a disco, y el stub de conftest.py
los deja explícitamente sin implementar (ver NotImplementedError en el
stub) para no fingir que se validó una escritura real de Shapefile/GPKG
cuando no fue así. Ese camino solo se valida corriendo dentro de QGIS.
"""

from GeoSpecies_Tools.species_download import exporter


def test_build_fields_matches_field_specs_count_and_names():
    fields = exporter.build_fields()
    names = [f.name_ for f in fields]
    expected_names = [spec[0] for spec in exporter.FIELD_SPECS]
    assert names == expected_names


def test_build_fields_applies_explicit_width_to_text_fields():
    fields = {f.name_: f for f in exporter.build_fields()}
    assert fields["sciName"].len_ == 254
    assert fields["gbifID"].len_ == 20
    assert fields["kingdom"].len_ == 50
    assert fields["country"].len_ == 10


def test_build_fields_numeric_fields_have_no_forced_width():
    fields = {f.name_: f for f in exporter.build_fields()}
    assert fields["year"].len_ == 0
    assert fields["lat"].len_ == 0
    assert fields["lon"].len_ == 0


def test_record_to_feature_returns_none_without_coordinates():
    fields = exporter.build_fields()
    assert exporter.record_to_feature({"scientificName": "Puma concolor"}, fields) is None
    assert exporter.record_to_feature({"decimalLatitude": -12.0}, fields) is None  # falta lon


def test_record_to_feature_populates_expected_attributes():
    fields = exporter.build_fields()
    record = {
        "key": 123, "species": "Puma concolor", "scientificName": "Puma concolor (Linnaeus, 1771)",
        "kingdom": "Animalia", "countryCode": "PE", "basisOfRecord": "HUMAN_OBSERVATION",
        "eventDate": "2020-05-01", "year": 2020, "coordinateUncertaintyInMeters": 50.0,
        "decimalLatitude": -12.05, "decimalLongitude": -77.05,
    }
    feat = exporter.record_to_feature(record, fields)
    assert feat is not None
    assert feat.attribute("species") == "Puma concolor"
    assert feat.attribute("lat") == -12.05
    assert feat.attribute("lon") == -77.05


def test_record_to_feature_truncates_long_scientific_name_instead_of_letting_ogr_do_it_silently():
    """
    Regresión directa del punto señalado: sciName sin ancho explícito
    podía truncarse en silencio a nivel de driver OGR de forma no
    predecible. Ahora se trunca en Python, de forma determinística.
    """
    fields = exporter.build_fields()
    long_name = "X" * 400  # supera el ancho de 254 definido en FIELD_SPECS
    record = {
        "scientificName": long_name,
        "decimalLatitude": -12.0, "decimalLongitude": -77.0,
    }
    feat = exporter.record_to_feature(record, fields)
    assert len(feat.attribute("sciName")) == 254
    assert feat.attribute("sciName") == long_name[:254]


def test_record_to_feature_missing_optional_fields_become_empty_string():
    fields = exporter.build_fields()
    record = {"decimalLatitude": -12.0, "decimalLongitude": -77.0}
    feat = exporter.record_to_feature(record, fields)
    assert feat.attribute("species") == ""
    assert feat.attribute("kingdom") == ""
