"""
test_batch.py — batch.py es el módulo más importante de testear: es el
pipeline que AHORA comparten worker.py (GUI) y el uso por consola/script
(item 5). Un bug aquí afecta ambos caminos.

Se mockea gbif_api.search_occurrences / exporter.export_points -- no se
golpea la red real ni se escribe a disco. filters.py (dedupe/living) se
deja correr real porque ya está cubierto y es puro.
"""

import pytest

from GeoSpecies_Tools.species_download import batch, gbif_api


# ---------------------------------------------------------------------
# resolve_species
# ---------------------------------------------------------------------

def test_resolve_species_int_delegates_to_resolve_accepted_taxon_key(monkeypatch):
    monkeypatch.setattr(gbif_api, "resolve_accepted_taxon_key", lambda k: (k, None))
    key, message = batch.resolve_species(2435099)
    assert key == 2435099
    assert message is None


def test_resolve_species_name_picks_exact_prefix_match(monkeypatch):
    monkeypatch.setattr(gbif_api, "suggest_species", lambda q, limit=20: [
        {"key": 1, "scientificName": "Puma yagouaroundi", "rank": "SPECIES", "status": "ACCEPTED", "kingdom": "Animalia"},
        {"key": 2, "scientificName": "Puma concolor", "rank": "SPECIES", "status": "ACCEPTED", "kingdom": "Animalia"},
    ])
    monkeypatch.setattr(gbif_api, "resolve_accepted_taxon_key", lambda k: (k, None))

    key, message = batch.resolve_species("Puma concolor")
    assert key == 2  # el que empieza EXACTAMENTE con el texto buscado, no el primero de la lista
    assert "Puma concolor" in message


def test_resolve_species_name_ambiguous_warns_and_takes_first(monkeypatch):
    monkeypatch.setattr(gbif_api, "suggest_species", lambda q, limit=20: [
        {"key": 1, "scientificName": "Algo distinto", "rank": "SPECIES", "status": "ACCEPTED", "kingdom": "Animalia"},
        {"key": 2, "scientificName": "Otra cosa", "rank": "SPECIES", "status": "ACCEPTED", "kingdom": "Animalia"},
    ])
    monkeypatch.setattr(gbif_api, "resolve_accepted_taxon_key", lambda k: (k, None))

    key, message = batch.resolve_species("nombre que no matchea nada")
    assert key == 1  # se tomó el primero
    assert "ambiguo" in message.lower()
    assert "taxonkey" in message.lower()  # sugiere pasar el taxonKey para evitar la ambigüedad


def test_resolve_species_name_not_found_raises():
    def _empty(q, limit=20):
        return []
    import unittest.mock as mock
    with mock.patch.object(gbif_api, "suggest_species", side_effect=_empty):
        with pytest.raises(gbif_api.GbifApiError):
            batch.resolve_species("especie inexistente xyz")


def test_resolve_species_name_appends_synonym_message(monkeypatch):
    monkeypatch.setattr(gbif_api, "suggest_species", lambda q, limit=20: [
        {"key": 1, "scientificName": "Felis concolor", "rank": "SPECIES", "status": "SYNONYM", "kingdom": "Animalia"},
    ])
    monkeypatch.setattr(gbif_api, "resolve_accepted_taxon_key", lambda k: (999, "es un sinónimo, se usa 999"))

    key, message = batch.resolve_species("Felis concolor")
    assert key == 999
    assert "sinónimo" in message.lower()
    assert "Felis concolor" in message  # conserva también el contexto de qué se buscó


# ---------------------------------------------------------------------
# run_download_pipeline -- el núcleo compartido GUI/script
# ---------------------------------------------------------------------

def test_run_download_pipeline_happy_path(monkeypatch):
    records = [
        {"decimalLatitude": -12.0, "decimalLongitude": -77.0, "basisOfRecord": "HUMAN_OBSERVATION",
         "eventDate": "2020-01-01", "speciesKey": 1},
    ]
    monkeypatch.setattr(gbif_api, "search_occurrences", lambda *a, **kw: records)
    monkeypatch.setattr(batch.exporter, "export_points", lambda recs, path, target_crs_authid=None: (path, len(recs)))

    result = batch.run_download_pipeline(
        {"taxonKey": 1}, "/tmp/out.shp", max_records=100,
        only_living=False, remove_duplicates=False,
    )
    assert result == {"output_path": "/tmp/out.shp", "count": 1, "canceled": False}


def test_run_download_pipeline_stops_before_export_if_canceled_after_search(monkeypatch):
    monkeypatch.setattr(gbif_api, "search_occurrences", lambda *a, **kw: [{"decimalLatitude": 1, "decimalLongitude": 1}])
    export_called = {"called": False}

    def _fake_export(*a, **kw):
        export_called["called"] = True
        return ("/tmp/out.shp", 1)

    monkeypatch.setattr(batch.exporter, "export_points", _fake_export)

    result = batch.run_download_pipeline(
        {"taxonKey": 1}, "/tmp/out.shp", max_records=100,
        only_living=False, remove_duplicates=False, cancel_check=lambda: True,
    )
    assert result["canceled"] is True
    assert result["output_path"] is None
    assert result["count"] == 0
    assert export_called["called"] is False  # nunca se llega a exportar tras cancelar


def test_run_download_pipeline_applies_only_living_filter(monkeypatch):
    records = [
        {"decimalLatitude": 1, "decimalLongitude": 1, "basisOfRecord": "FOSSIL_SPECIMEN"},
        {"decimalLatitude": 2, "decimalLongitude": 2, "basisOfRecord": "HUMAN_OBSERVATION"},
    ]
    monkeypatch.setattr(gbif_api, "search_occurrences", lambda *a, **kw: records)
    captured = {}

    def _fake_export(recs, path, target_crs_authid=None):
        captured["records"] = recs
        return (path, len(recs))

    monkeypatch.setattr(batch.exporter, "export_points", _fake_export)

    batch.run_download_pipeline(
        {"taxonKey": 1}, "/tmp/out.shp", max_records=100,
        only_living=True, remove_duplicates=False,
    )
    assert len(captured["records"]) == 1
    assert captured["records"][0]["basisOfRecord"] == "HUMAN_OBSERVATION"


def test_run_download_pipeline_applies_dedupe_filter(monkeypatch):
    records = [
        {"decimalLatitude": 1, "decimalLongitude": 1, "eventDate": "2020-01-01", "speciesKey": 1},
        {"decimalLatitude": 1, "decimalLongitude": 1, "eventDate": "2020-01-01", "speciesKey": 1},
    ]
    monkeypatch.setattr(gbif_api, "search_occurrences", lambda *a, **kw: records)
    captured = {}

    def _fake_export(recs, path, target_crs_authid=None):
        captured["records"] = recs
        return (path, len(recs))

    monkeypatch.setattr(batch.exporter, "export_points", _fake_export)

    batch.run_download_pipeline(
        {"taxonKey": 1}, "/tmp/out.shp", max_records=100,
        only_living=False, remove_duplicates=True,
    )
    assert len(captured["records"]) == 1


def test_run_download_pipeline_forwards_target_crs_to_exporter(monkeypatch):
    monkeypatch.setattr(gbif_api, "search_occurrences", lambda *a, **kw: [])
    captured = {}

    def _fake_export(recs, path, target_crs_authid=None):
        captured["target_crs_authid"] = target_crs_authid
        return (path, 0)

    monkeypatch.setattr(batch.exporter, "export_points", _fake_export)

    batch.run_download_pipeline(
        {"taxonKey": 1}, "/tmp/out.shp", max_records=100,
        only_living=False, remove_duplicates=False, target_crs_authid="EPSG:32718",
    )
    assert captured["target_crs_authid"] == "EPSG:32718"


# ---------------------------------------------------------------------
# download_species_records
# ---------------------------------------------------------------------

def test_download_species_records_builds_query_and_delegates_to_pipeline(monkeypatch):
    monkeypatch.setattr(batch, "resolve_species", lambda species: (2435099, None))
    captured = {}

    def _fake_pipeline(query_params, output_path, **kwargs):
        captured["query_params"] = query_params
        captured["output_path"] = output_path
        return {"output_path": output_path, "count": 5, "canceled": False}

    monkeypatch.setattr(batch, "run_download_pipeline", _fake_pipeline)

    result = batch.download_species_records(
        "Puma concolor", "/tmp/out.shp", country_code="PE", year_min=2000, year_max=2024,
    )

    assert captured["query_params"]["taxonKey"] == 2435099
    assert captured["query_params"]["country"] == "PE"
    assert captured["query_params"]["year"] == "2000,2024"
    assert result["taxon_key"] == 2435099
    assert result["count"] == 5


def test_download_species_records_skip_resolve_key_avoids_extra_network_call(monkeypatch):
    resolve_called = {"called": False}

    def _resolve(species):
        resolve_called["called"] = True
        return (999, None)

    monkeypatch.setattr(batch, "resolve_species", _resolve)
    monkeypatch.setattr(batch, "run_download_pipeline", lambda qp, path, **kw: {"output_path": path, "count": 0, "canceled": False})

    result = batch.download_species_records(
        123, "/tmp/out.shp", skip_resolve_key=123,
    )
    assert resolve_called["called"] is False  # se saltó resolve_species() a propósito
    assert result["taxon_key"] == 123


def test_download_species_records_computes_geometry_from_aoi_layer_when_no_explicit_wkt(monkeypatch):
    monkeypatch.setattr(batch, "resolve_species", lambda species: (1, None))
    monkeypatch.setattr(batch, "layer_geometry_to_wkt_4326", lambda layer: "POLYGON((0 0,1 0,1 1,0 1,0 0))")
    captured = {}

    def _fake_pipeline(query_params, output_path, **kwargs):
        captured["query_params"] = query_params
        return {"output_path": output_path, "count": 0, "canceled": False}

    monkeypatch.setattr(batch, "run_download_pipeline", _fake_pipeline)

    batch.download_species_records(1, "/tmp/out.shp", aoi_layer=object())
    assert "geometry" in captured["query_params"]


# ---------------------------------------------------------------------
# download_multiple_species
# ---------------------------------------------------------------------

def test_download_multiple_species_continues_after_individual_failure(monkeypatch, tmp_path):
    def _fake_download(species, output_path, **kwargs):
        if species == "especie_mala":
            raise gbif_api.GbifApiError("no se encontró")
        return {"output_path": output_path, "count": 3, "canceled": False}

    monkeypatch.setattr(batch, "download_species_records", _fake_download)

    results = batch.download_multiple_species(
        ["Puma concolor", "especie_mala", "Panthera onca"], str(tmp_path),
    )

    assert len(results) == 3
    species_ok = [r for s, r in results if "error" not in r]
    species_failed = [r for s, r in results if "error" in r]
    assert len(species_ok) == 2
    assert len(species_failed) == 1


def test_download_multiple_species_sanitizes_filenames(monkeypatch, tmp_path):
    captured_paths = []

    def _fake_download(species, output_path, **kwargs):
        captured_paths.append(output_path)
        return {"output_path": output_path, "count": 1, "canceled": False}

    monkeypatch.setattr(batch, "download_species_records", _fake_download)

    batch.download_multiple_species(["Puma concolor (Linnaeus, 1771)"], str(tmp_path))

    assert len(captured_paths) == 1
    filename = captured_paths[0].split("/")[-1]
    assert " " not in filename
    assert "(" not in filename and ")" not in filename
    assert filename.endswith(".shp")
