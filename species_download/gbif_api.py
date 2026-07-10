"""
gbif_api.py — Cliente HTTP para la API de GBIF (species + occurrence).

Endpoints usados:

  1. species/suggest    -> autocompletado de nombres científicos
     (Grupo 1 del diálogo: txtSpecies + btnSearch + lstSpecies)

  2. species/{key}      -> resolución de sinónimos (ver resolve_accepted_taxon_key)

  3. occurrence/search (limit=0) -> conteo previo, para estimar volumen
     y tiempo ANTES de comprometerse a una descarga larga.

  4. occurrence/search (paginado) -> descarga real de registros
     (worker.py la consume en un hilo aparte para no bloquear la UI)

La lógica de paginación/reintentos está adaptada del algoritmo Processing
ya existente en el ecosistema (GbifOccurrencesAlgorithm), manteniendo los
mismos límites conservadores de la API pública de GBIF.

Cancelación: todas las funciones que hacen red aceptan una `requests.Session`
inyectada. El llamador (worker.py) puede invocar `session.close()` desde
OTRO hilo para abortar una petición en curso — es la técnica que permite
que "Cancelar" no tenga que esperar a que expire un timeout de 60s.
"""

import time

import requests

# ============================================================
# Constantes API GBIF
# ============================================================
GBIF_SUGGEST_URL = "https://api.gbif.org/v1/species/suggest"
GBIF_SPECIES_DETAIL_URL = "https://api.gbif.org/v1/species/{key}"
GBIF_OCCURRENCE_URL = "https://api.gbif.org/v1/occurrence/search"
GBIF_COUNTRY_ENUM_URL = "https://api.gbif.org/v1/enumeration/country"

# datasetKey del GBIF Backbone Taxonomy. Es CRÍTICO restringir
# species/suggest a este dataset: por defecto ese endpoint autocompleta
# contra cualquier checklist indexado por GBIF (Catalogue of Life, IUCN
# Red List, checklists nacionales, etc.), no solo el backbone. Si el
# 'key' devuelto viene de otro checklist, NO coincide con los taxonKey
# que occurrence/search usa para indexar ocurrencias, y la búsqueda
# posterior por taxonKey devuelve 0 registros aunque la especie exista
# y tenga datos — el bug se manifiesta como "encuentra la especie pero
# la descarga sale vacía".
GBIF_BACKBONE_DATASET_KEY = "d7dddbf4-2cf0-4f39-9b2a-bb099caae36c"

PER_PAGE_LIMIT = 300            # límite máximo por página según la API
ABSOLUTE_MAX_RECORDS = 100000   # límite duro impuesto por GBIF (offset+limit)
HTTP_TIMEOUT = 60
COUNT_TIMEOUT = 15              # timeout para la consulta previa de conteo (limit=0)
SYNONYM_CHECK_TIMEOUT = 10      # timeout para species/{key} (resolución de sinónimos)
RETRY_ATTEMPTS = 5              # reintentos ante errores 5xx / timeouts
SERVER_ERROR_SLEEP = 10         # segundos base de espera ante error 5xx (interrumpible)
RATE_LIMIT_SLEEP = 0.15         # pausa entre páginas para no saturar la API


class GbifApiError(Exception):
    """Error de comunicación o de datos al consultar la API de GBIF."""


class GbifCanceled(Exception):
    """Señal interna: la descarga fue cancelada mientras una petición estaba en curso."""


def suggest_species(query, limit=20, timeout=10):
    """
    Autocompleta nombres científicos usando GBIF species/suggest,
    restringido al GBIF Backbone Taxonomy (ver GBIF_BACKBONE_DATASET_KEY).

    Retorna
    -------
    list[dict]
        Cada dict trae: key (usageKey/taxonKey), scientificName, rank,
        status (ACCEPTED/SYNONYM/...), kingdom.

    Lanza
    -----
    GbifApiError
        Si la petición falla o la respuesta no es válida.
    """
    query = (query or "").strip()
    if not query:
        return []

    try:
        resp = requests.get(
            GBIF_SUGGEST_URL,
            params={"q": query, "limit": limit, "datasetKey": GBIF_BACKBONE_DATASET_KEY},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise GbifApiError(f"No se pudo conectar con GBIF (species/suggest): {exc}") from exc
    except ValueError as exc:
        raise GbifApiError(f"Respuesta inválida de GBIF (species/suggest): {exc}") from exc

    if not isinstance(data, list):
        return []

    results = []
    for item in data:
        key = item.get("key")
        if key is None:
            continue
        results.append({
            "key": key,
            "scientificName": item.get("scientificName") or item.get("canonicalName") or "(sin nombre)",
            "rank": item.get("rank", ""),
            "status": item.get("status", ""),
            "kingdom": item.get("kingdom", ""),
        })
    return results


def resolve_accepted_taxon_key(usage_key, timeout=SYNONYM_CHECK_TIMEOUT):
    """
    Resuelve un usageKey de species/suggest al taxonKey que realmente hay
    que usar en occurrence/search.

    Por qué es necesario: species/suggest puede devolver sinónimos dentro
    del propio GBIF Backbone (restringir por datasetKey, como ya hace
    suggest_species, NO basta — un sinónimo también vive en el backbone).
    Las ocurrencias en GBIF se indexan bajo el taxonKey del nombre
    ACEPTADO, no el del sinónimo. Buscar occurrence/search?taxonKey=<key
    de un sinónimo> devuelve 0 registros aunque la especie tenga miles de
    ocurrencias — mismo síntoma que el bug de datasetKey, causa distinta.

    Parámetros
    ----------
    usage_key : int
        key devuelto por suggest_species() / seleccionado en lstSpecies.
    timeout : int
        Timeout HTTP en segundos.

    Retorna
    -------
    tuple(int, str | None)
        (taxonKey a usar en occurrence/search — el original si ya era un
        nombre aceptado, o el acceptedKey si era sinónimo; mensaje
        informativo para mostrar al usuario si se resolvió un sinónimo,
        o None si no hizo falta resolver nada).

    Lanza
    -----
    GbifApiError
        Si la petición falla o la respuesta no es válida.
    """
    url = GBIF_SPECIES_DETAIL_URL.format(key=usage_key)
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise GbifApiError(f"No se pudo verificar el estado taxonómico (species/{usage_key}): {exc}") from exc
    except ValueError as exc:
        raise GbifApiError(f"Respuesta inválida de GBIF al verificar sinonimia: {exc}") from exc

    is_synonym = bool(data.get("synonym"))
    accepted_key = data.get("acceptedKey")

    if is_synonym and accepted_key:
        original_name = data.get("scientificName") or data.get("canonicalName") or str(usage_key)
        accepted_name = data.get("accepted") or str(accepted_key)
        message = (
            f"'{original_name}' es un sinónimo taxonómico. Se usará el nombre "
            f"aceptado '{accepted_name}' (taxonKey {accepted_key}) para la búsqueda."
        )
        return accepted_key, message

    if is_synonym and not accepted_key:
        # Caso raro: GBIF marca la especie como sinónimo pero no expone
        # acceptedKey. Se usa el key original con una advertencia en vez
        # de fallar — es preferible intentar la búsqueda a bloquear al
        # usuario, pero el resultado puede seguir siendo 0.
        return usage_key, (
            "Advertencia: esta especie está marcada como sinónimo en GBIF pero "
            "no se pudo determinar el nombre aceptado. La búsqueda puede devolver 0 registros."
        )

    return usage_key, None


def count_occurrences(params, timeout=COUNT_TIMEOUT):
    """
    Consulta previa y rápida (limit=0) para saber cuántos registros hay
    ANTES de comprometerse a una descarga paginada larga.

    Se usa desde el hilo de la UI (síncrona) para mostrar al usuario un
    conteo real y una estimación de tiempo antes de iniciar el worker.

    Retorna
    -------
    tuple(int, float)
        (conteo total reportado por GBIF, segundos que tardó esta consulta
        — sirve como estimador de latencia por página para el cálculo de
        tiempo total).

    Lanza
    -----
    GbifApiError
        Si la petición falla o la respuesta no es válida.
    """
    query = dict(params)
    query["limit"] = 0
    query["offset"] = 0

    start = time.monotonic()
    try:
        resp = requests.get(GBIF_OCCURRENCE_URL, params=query, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise GbifApiError(f"No se pudo conectar con GBIF (occurrence/search, conteo): {exc}") from exc
    except ValueError as exc:
        raise GbifApiError(f"Respuesta inválida de GBIF (conteo): {exc}") from exc
    elapsed = time.monotonic() - start

    return int(data.get("count", 0)), elapsed


def fetch_country_enum(timeout=8):
    """
    Descarga la lista OFICIAL de países/territorios que acepta el
    parámetro `country` de occurrence/search, directamente desde el
    enumerador de GBIF (ISO 3166-1 alpha-2 + códigos especiales como
    XK para Kosovo).

    Se prefiere esto sobre mantener una lista hardcodeada en filters.py:
    garantiza que los códigos coincidan exactamente con lo que GBIF
    acepta -- un ISO 3166-1 genérico armado a mano podría divergir en
    casos límite (territorios, códigos reservados) -- y elimina el
    mantenimiento manual de ~195+ entradas.

    Retorna
    -------
    list[tuple(str, str)]
        Lista de (iso2, title) en inglés (tal como los expone GBIF),
        ordenada alfabéticamente por title.

    Lanza
    -----
    GbifApiError
        Si la petición falla o la respuesta no es válida. El llamador
        (species_download_dialog.py) debe capturarla y usar
        filters.COUNTRY_LIST_FALLBACK si esto falla (por ejemplo, sin
        conexión al abrir el diálogo).
    """
    try:
        resp = requests.get(GBIF_COUNTRY_ENUM_URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise GbifApiError(f"No se pudo descargar la lista de países de GBIF: {exc}") from exc
    except ValueError as exc:
        raise GbifApiError(f"Respuesta inválida de GBIF (enumeration/country): {exc}") from exc

    if not isinstance(data, list):
        return []

    countries = []
    for item in data:
        iso2 = item.get("iso2")
        title = item.get("title")
        if not iso2 or not title:
            continue
        countries.append((iso2, title))

    countries.sort(key=lambda pair: pair[1])
    return countries


def search_occurrences(params, max_records, progress_cb=None, status_cb=None,
                        cancel_check=None, session=None):
    """
    Descarga paginada de ocurrencias desde occurrence/search.

    Parámetros
    ----------
    params : dict
        Parámetros de búsqueda de GBIF (taxonKey, country, geometry,
        hasCoordinate, hasGeospatialIssue, year, coordinateUncertaintyInMeters
        etc.). No debe incluir 'offset' ni 'limit': los gestiona esta función.
    max_records : int
        Tope máximo de registros a recuperar (se acota además a
        ABSOLUTE_MAX_RECORDS por restricción de la API).
    progress_cb : callable(int, int) opcional
        Se invoca como progress_cb(descargados, total_estimado) tras cada
        página.
    status_cb : callable(str) opcional
        Se invoca con mensajes de estado legibles para el usuario.
    cancel_check : callable() -> bool opcional
        Si devuelve True, la descarga se interrumpe de forma ordenada.
        Se revisa tanto entre páginas como durante las esperas de reintento
        (que son interrumpibles, no un time.sleep() monolítico).
    session : requests.Session opcional
        Sesión HTTP a usar. El llamador puede cerrarla desde OTRO hilo
        (session.close()) para abortar una petición en curso — es lo que
        hace worker.py al cancelar, en vez de esperar el timeout completo.

    Retorna
    -------
    list[dict]
        Lista de registros crudos (tal como los entrega GBIF) hasta
        max_records, hasta agotar resultados, o hasta cancelación.
    """
    owns_session = session is None
    session = session or requests.Session()

    max_records = min(int(max_records), ABSOLUTE_MAX_RECORDS)
    records = []
    offset = 0
    total_available = None

    try:
        while len(records) < max_records:
            if cancel_check is not None and cancel_check():
                if status_cb:
                    status_cb("Descarga cancelada por el usuario.")
                break

            page_limit = min(PER_PAGE_LIMIT, max_records - len(records))
            query = dict(params)
            query["offset"] = offset
            query["limit"] = page_limit

            try:
                page = _fetch_page(query, session, status_cb, cancel_check)
            except GbifCanceled:
                if status_cb:
                    status_cb("Descarga cancelada por el usuario.")
                break

            if page is None:
                # Se agotaron los reintentos en esta página (no fue
                # cancelación): se corta la descarga con lo acumulado.
                break

            if total_available is None:
                total_available = page.get("count", 0)
                if status_cb:
                    if total_available == 0:
                        status_cb(
                            "GBIF reporta 0 registros para estos filtros "
                            "(taxonKey/país/año/geometría/incertidumbre). "
                            "No es un filtro local: la API ya devolvió 0."
                        )
                    else:
                        status_cb(f"GBIF reporta {total_available} registro(s) disponibles para estos filtros.")

            results = page.get("results", [])
            records.extend(results)

            if progress_cb:
                total_estimate = min(total_available or max_records, max_records)
                progress_cb(len(records), max(total_estimate, 1))

            if page.get("endOfRecords", True) or not results:
                break

            offset += page_limit
            _interruptible_sleep(RATE_LIMIT_SLEEP, cancel_check)
    finally:
        if owns_session:
            try:
                session.close()
            except Exception:
                pass

    return records[:max_records]


def _fetch_page(query, session, status_cb=None, cancel_check=None):
    """
    Descarga una única página de occurrence/search con reintentos ante
    errores 5xx o timeouts. Devuelve None si se agotan los reintentos sin
    éxito. Lanza GbifCanceled si la petición fue interrumpida por un
    session.close() disparado desde otro hilo (cancelación real).
    """
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = session.get(GBIF_OCCURRENCE_URL, params=query, timeout=HTTP_TIMEOUT)

            if resp.status_code >= 500:
                raise requests.HTTPError(f"Error de servidor GBIF: HTTP {resp.status_code}")

            resp.raise_for_status()
            return resp.json()

        except (requests.RequestException, ValueError, OSError) as exc:
            # Si la excepción llegó porque cancel_check() ya está activo,
            # lo más probable es que sea el propio session.close() llamado
            # desde el hilo principal al cancelar — no es un error real de
            # red, es la cancelación surtiendo efecto.
            if cancel_check is not None and cancel_check():
                raise GbifCanceled() from exc

            if status_cb:
                status_cb(f"Intento {attempt}/{RETRY_ATTEMPTS} falló ({exc}). Reintentando...")

            if attempt < RETRY_ATTEMPTS:
                _interruptible_sleep(SERVER_ERROR_SLEEP, cancel_check)
                if cancel_check is not None and cancel_check():
                    raise GbifCanceled() from exc
            else:
                if status_cb:
                    status_cb("Se agotaron los reintentos contra la API de GBIF.")
                return None
    return None


def _interruptible_sleep(total_seconds, cancel_check, step=0.25):
    """
    Duerme `total_seconds`, pero revisa `cancel_check()` cada `step`
    segundos para poder cortar la espera de inmediato ante cancelación,
    en vez de un time.sleep() monolítico que bloquea la respuesta de
    'Cancelar' hasta agotar todo el intervalo.
    """
    elapsed = 0.0
    while elapsed < total_seconds:
        if cancel_check is not None and cancel_check():
            return
        pause = min(step, total_seconds - elapsed)
        time.sleep(pause)
        elapsed += pause
