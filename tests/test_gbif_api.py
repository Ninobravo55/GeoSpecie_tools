"""
test_gbif_api.py — gbif_api.py es 100% puro Python (solo `time` y
`requests`), así que estos tests corren sin necesitar el stub de qgis.
Se mockean las llamadas HTTP; no se golpea la API real de GBIF.
"""

from unittest.mock import patch, MagicMock

import pytest
import requests

from GeoSpecies_Tools.species_download import gbif_api


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


# ---------------------------------------------------------------------
# suggest_species
# ---------------------------------------------------------------------

def test_suggest_species_empty_query_returns_empty_list_without_network():
    with patch("requests.get") as mock_get:
        assert gbif_api.suggest_species("") == []
        assert gbif_api.suggest_species("   ") == []
        mock_get.assert_not_called()


def test_suggest_species_restricts_to_gbif_backbone_dataset():
    """
    Regresión directa del bug reportado: species/suggest sin restringir
    por datasetKey puede devolver keys de otros checklists, que luego
    no coinciden con los taxonKey de occurrence/search.
    """
    with patch("requests.get", return_value=FakeResponse([])) as mock_get:
        gbif_api.suggest_species("Puma concolor")
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["datasetKey"] == gbif_api.GBIF_BACKBONE_DATASET_KEY


def test_suggest_species_parses_expected_fields():
    raw = [{
        "key": 2435099, "scientificName": "Puma concolor (Linnaeus, 1771)",
        "canonicalName": "Puma concolor", "rank": "SPECIES",
        "status": "ACCEPTED", "kingdom": "Animalia",
    }]
    with patch("requests.get", return_value=FakeResponse(raw)):
        results = gbif_api.suggest_species("Puma concolor")

    assert len(results) == 1
    assert results[0]["key"] == 2435099
    assert results[0]["status"] == "ACCEPTED"


def test_suggest_species_skips_entries_without_key():
    raw = [{"scientificName": "Sin key"}, {"key": 1, "scientificName": "Con key"}]
    with patch("requests.get", return_value=FakeResponse(raw)):
        results = gbif_api.suggest_species("x")
    assert len(results) == 1
    assert results[0]["key"] == 1


def test_suggest_species_raises_gbif_api_error_on_network_failure():
    with patch("requests.get", side_effect=requests.ConnectionError("no network")):
        with pytest.raises(gbif_api.GbifApiError):
            gbif_api.suggest_species("Puma concolor")


# ---------------------------------------------------------------------
# resolve_accepted_taxon_key -- el bug de sinónimos
# ---------------------------------------------------------------------

def test_resolve_accepted_taxon_key_passthrough_when_already_accepted():
    data = {"synonym": False, "scientificName": "Puma concolor"}
    with patch("requests.get", return_value=FakeResponse(data)):
        key, message = gbif_api.resolve_accepted_taxon_key(2435099)
    assert key == 2435099
    assert message is None


def test_resolve_accepted_taxon_key_redirects_synonym_to_accepted_key():
    data = {
        "synonym": True, "acceptedKey": 9999,
        "scientificName": "Felis concolor", "accepted": "Puma concolor",
    }
    with patch("requests.get", return_value=FakeResponse(data)):
        key, message = gbif_api.resolve_accepted_taxon_key(1234)
    assert key == 9999
    assert message is not None
    assert "sinónimo" in message.lower()
    assert "9999" in message


def test_resolve_accepted_taxon_key_synonym_without_accepted_key_warns_but_does_not_fail():
    data = {"synonym": True, "acceptedKey": None, "scientificName": "Rareza taxonómica"}
    with patch("requests.get", return_value=FakeResponse(data)):
        key, message = gbif_api.resolve_accepted_taxon_key(1234)
    # No hay acceptedKey -> se usa el original, pero se advierte, no se lanza excepción.
    assert key == 1234
    assert message is not None
    assert "advertencia" in message.lower()


def test_resolve_accepted_taxon_key_raises_on_network_failure():
    with patch("requests.get", side_effect=requests.Timeout("timeout")):
        with pytest.raises(gbif_api.GbifApiError):
            gbif_api.resolve_accepted_taxon_key(1234)


# ---------------------------------------------------------------------
# count_occurrences
# ---------------------------------------------------------------------

def test_count_occurrences_uses_limit_zero_and_returns_count():
    with patch("requests.get", return_value=FakeResponse({"count": 4821})) as mock_get:
        count, elapsed = gbif_api.count_occurrences({"taxonKey": 1})
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["limit"] == 0
    assert count == 4821
    assert elapsed >= 0


# ---------------------------------------------------------------------
# fetch_country_enum
# ---------------------------------------------------------------------

def test_fetch_country_enum_parses_and_sorts_by_title():
    raw = [
        {"iso2": "PE", "title": "Peru"},
        {"iso2": "AR", "title": "Argentina"},
        {"iso2": "", "title": "Sin código, debe descartarse"},
    ]
    with patch("requests.get", return_value=FakeResponse(raw)):
        countries = gbif_api.fetch_country_enum()
    assert countries == [("AR", "Argentina"), ("PE", "Peru")]


def test_fetch_country_enum_raises_on_failure():
    with patch("requests.get", side_effect=requests.ConnectionError("offline")):
        with pytest.raises(gbif_api.GbifApiError):
            gbif_api.fetch_country_enum()


# ---------------------------------------------------------------------
# search_occurrences / _fetch_page / _interruptible_sleep -- paginación
# y cancelación real (item 1 del historial de esta conversación)
# ---------------------------------------------------------------------

def _make_session(pages):
    """Sesión falsa que devuelve `pages` (lista de dicts) en orden, una
    por llamada a session.get(...)."""
    session = MagicMock()
    session.get.side_effect = [FakeResponse(p) for p in pages]
    return session


def test_search_occurrences_stops_at_end_of_records():
    pages = [{"count": 2, "results": [{"a": 1}, {"a": 2}], "endOfRecords": True}]
    session = _make_session(pages)

    records = gbif_api.search_occurrences({"taxonKey": 1}, max_records=100, session=session)

    assert len(records) == 2
    assert session.get.call_count == 1


def test_search_occurrences_paginates_across_multiple_pages():
    pages = [
        {"count": 3, "results": [{"a": 1}], "endOfRecords": False},
        {"count": 3, "results": [{"a": 2}], "endOfRecords": False},
        {"count": 3, "results": [{"a": 3}], "endOfRecords": True},
    ]
    session = _make_session(pages)

    with patch("time.sleep"):  # no esperar de verdad los RATE_LIMIT_SLEEP entre páginas
        records = gbif_api.search_occurrences({"taxonKey": 1}, max_records=100, session=session)

    assert len(records) == 3
    assert session.get.call_count == 3


def test_search_occurrences_respects_max_records_cap():
    pages = [{"count": 1000, "results": [{"a": i} for i in range(50)], "endOfRecords": False}]
    session = _make_session(pages)

    with patch("time.sleep"):
        records = gbif_api.search_occurrences({"taxonKey": 1}, max_records=10, session=session)

    assert len(records) == 10  # se corta en el tope aunque la página trajera más


def test_search_occurrences_stops_immediately_if_already_canceled():
    session = _make_session([])
    records = gbif_api.search_occurrences(
        {"taxonKey": 1}, max_records=100, session=session, cancel_check=lambda: True,
    )
    assert records == []
    session.get.assert_not_called()  # ni siquiera pide la primera página


def test_search_occurrences_reports_zero_count_via_status_cb():
    pages = [{"count": 0, "results": [], "endOfRecords": True}]
    session = _make_session(pages)
    messages = []

    gbif_api.search_occurrences(
        {"taxonKey": 1}, max_records=100, session=session, status_cb=messages.append,
    )

    assert any("0 registros" in m for m in messages)


def test_fetch_page_retries_on_5xx_then_succeeds():
    session = MagicMock()
    session.get.side_effect = [
        FakeResponse({}, status_code=502),
        FakeResponse({"count": 1, "results": [{"a": 1}], "endOfRecords": True}),
    ]
    with patch("GeoSpecies_Tools.species_download.gbif_api._interruptible_sleep"):
        page = gbif_api._fetch_page({"taxonKey": 1, "offset": 0, "limit": 10}, session)

    assert page is not None
    assert session.get.call_count == 2


def test_fetch_page_returns_none_after_exhausting_retries():
    session = MagicMock()
    session.get.side_effect = [FakeResponse({}, status_code=503)] * gbif_api.RETRY_ATTEMPTS

    with patch("GeoSpecies_Tools.species_download.gbif_api._interruptible_sleep"):
        page = gbif_api._fetch_page({"taxonKey": 1, "offset": 0, "limit": 10}, session)

    assert page is None
    assert session.get.call_count == gbif_api.RETRY_ATTEMPTS


def test_fetch_page_raises_canceled_when_cancel_flag_set_during_error():
    """
    Simula lo que pasa cuando request_cancel() cierra la sesión desde
    otro hilo mientras hay una petición en curso: session.get lanza una
    excepción Y cancel_check() ya está en True -- debe interpretarse
    como cancelación, no como error de red a reintentar.
    """
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("session was closed")

    with pytest.raises(gbif_api.GbifCanceled):
        gbif_api._fetch_page(
            {"taxonKey": 1, "offset": 0, "limit": 10}, session, cancel_check=lambda: True,
        )
    assert session.get.call_count == 1  # no reintenta: se corta apenas ve la cancelación


def test_interruptible_sleep_stops_early_when_canceled():
    calls = {"n": 0}

    def cancel_check():
        calls["n"] += 1
        return calls["n"] > 1  # cancela en la segunda revisión

    with patch("time.sleep") as mock_sleep:
        gbif_api._interruptible_sleep(10.0, cancel_check, step=0.25)

    # Se detuvo mucho antes de acumular los 10 segundos completos.
    assert mock_sleep.call_count <= 2
