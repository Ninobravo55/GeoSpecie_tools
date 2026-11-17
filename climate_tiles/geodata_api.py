"""
geodata_api.py — Construcción de URLs y descarga de tiles WorldClim/CMIP6.

URLs verificadas contra el código fuente real de R/worldclim.R
(paquete rspatial/geodata) y contra un caso de descarga real reportado
en github.com/rspatial/geodata/issues/81, no inventadas ni adivinadas:

  Histórico (baseline 30s):
    https://geodata.ucdavis.edu/climate/worldclim/2_1/tiles/tile/tile_<id>_wc2.1_30s_<var>.tif

  CMIP6 (proyección, tileada):
    https://geodata.ucdavis.edu/cmip6/tiles/<model>/ssp<ssp>/wc2.1_30s_<var>_<model>_ssp<ssp>_<periodo>_tile-<id>.tif

  (nota: CMIP6 usa "bioc" en el nombre de archivo donde WorldClim usa
  "bio" -- son la misma variable, el propio paquete R remapea uno al
  otro según el producto; catalog.py expone una sola opción "bio" al
  usuario y este módulo hace el remapeo correcto para cada caso.)
"""

import logging
import os
import requests

logger = logging.getLogger(__name__)

WORLDCLIM_BASE_URL = "https://geodata.ucdavis.edu/climate/worldclim/2_1/"
CMIP6_BASE_URL = "https://geodata.ucdavis.edu/cmip6/"
CMIP6_FILES_INDEX_URL = CMIP6_BASE_URL + "files.txt"

HTTP_TIMEOUT = 30
DOWNLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB -- granularidad de cancelación durante la descarga

# Sanity threshold igual al de R (`if (nrow(ff) > 1000)`): si el índice
# descargado tiene menos de esto, se asume corrupto/incompleto y se
# omite la validación en vez de bloquear al usuario con falsos negativos.
MIN_VALID_INDEX_ROWS = 1000

_file_index_cache = None  # cache en memoria del proceso, igual que el tmpfile de R por sesión


class GeodataApiError(Exception):
    """Error de comunicación con el servidor de geodata.ucdavis.edu."""


class GeodataCanceled(Exception):
    """Señal interna: descarga cancelada mientras una petición estaba en curso."""


def worldclim_var_filename(var):
    """WorldClim usa 'bio' en el nombre de archivo (no 'bioc')."""
    return "bio" if var == "bioc" else var


def cmip6_var_filename(var):
    """CMIP6 usa 'bioc' en el nombre de archivo (no 'bio')."""
    return "bioc" if var == "bio" else var


def build_worldclim_tile_url(var, tile_id):
    """
    URL de un tile histórico (baseline WorldClim 2.1, 30 arco-segundos).

    Ruta real: tiles/tile/tile_<id>_wc2.1_30s_<var>.tif
    (distinta de tiles/iso/<ISO>_wc2.1_30s_<var>.tif, que es por país,
    no por tile numérico -- no confundir ambas).
    """
    fname = f"tile_{tile_id}_wc2.1_30s_{worldclim_var_filename(var)}.tif"
    return f"{WORLDCLIM_BASE_URL}tiles/tile/{fname}"


def build_cmip6_tile_url(model, ssp, period, var, tile_id):
    """
    URL de un tile CMIP6 (proyección futura, 30 arco-segundos).

    Ruta real: tiles/<model>/ssp<ssp>/wc2.1_30s_<var>_<model>_ssp<ssp>_<periodo>_tile-<id>.tif
    """
    fname = f"wc2.1_30s_{cmip6_var_filename(var)}_{model}_ssp{ssp}_{period}_tile-{tile_id}.tif"
    return f"{CMIP6_BASE_URL}tiles/{model}/ssp{ssp}/{fname}"


def fetch_cmip6_file_index(timeout=15, force_refresh=False):
    """
    Descarga y cachea (en memoria, por proceso) el índice de archivos
    CMIP6 que expone el servidor (files.txt), para poder validar
    combinaciones modelo+ssp+periodo+variable ANTES de intentar
    descargar un tile que no existe.

    Réplica directa de lo que hace `.check_cmip6()` en R/worldclim.R:
    ese índice se construyó en el servidor con
    `find 30s -type f -printf "%f\\n" | cut -c11-100 > files.txt`,
    es decir, nombres de archivo sin el prefijo "wc2.1_30s_", separados
    por guion bajo en 4 campos: var, modelo, sspXXX, periodo.tif.

    Retorna
    -------
    set[tuple(str,str,str,str)] | None
        Set de tuplas (var, model, ssp3, "periodo.tif"). None si la
        descarga falló O si el índice tiene menos de
        MIN_VALID_INDEX_ROWS líneas (se asume corrupto/incompleto;
        igual que en R, esto significa "no validar", no "todo inválido").
    """
    global _file_index_cache
    if _file_index_cache is not None and not force_refresh:
        return _file_index_cache

    try:
        resp = requests.get(CMIP6_FILES_INDEX_URL, timeout=timeout)
        resp.raise_for_status()
        lines = resp.text.splitlines()
    except requests.RequestException:
        return None

    if len(lines) < MIN_VALID_INDEX_ROWS:
        return None

    index = set()
    for line in lines:
        parts = line.strip().split("_")
        if len(parts) < 4:
            continue
        var, model, ssp_field, time_field = parts[0], parts[1], parts[2], parts[3]
        index.add((var, model, ssp_field, time_field))

    _file_index_cache = index
    return index


def check_cmip6_combo(var, model, ssp, period):
    """
    Verifica si (var, model, ssp, period) existe en el índice de CMIP6.

    Retorna
    -------
    tuple(bool, str | None)
        (True, None) si existe, o si el índice no se pudo validar
        (fail-open, igual que R: no bloquea al usuario si el propio
        chequeo de existencia no está disponible).
        (False, mensaje) si el índice SÍ cargó correctamente y la
        combinación no aparece en él -- ahí sí se bloquea, porque hay
        certeza de que no existe.
    """
    index = fetch_cmip6_file_index()
    if index is None:
        return True, None  # no se pudo validar -- no bloquear al usuario por esto

    key = (cmip6_var_filename(var), model, f"ssp{ssp}", f"{period}.tif")
    if key in index:
        return True, None

    return False, (
        f"La combinación variable={var}, modelo={model}, ssp={ssp}, periodo={period} "
        "no aparece en el índice de archivos CMIP6 del servidor de geodata.ucdavis.edu. "
        "Es probable que esta combinación específica no exista (no todos los modelos "
        "corrieron todos los SSP/periodos)."
    )


def get_remote_file_size(url, timeout=HTTP_TIMEOUT):
    """
    HEAD request para conocer el tamaño del archivo ANTES de descargar
    -- estos tiles pueden pesar varios cientos de MB a >1 GB por
    variable/modelo/periodo. Se usa para mostrar una confirmación previa
    al usuario, igual que gbif_api.count_occurrences() en species_download.

    Retorna
    -------
    int | None
        Tamaño en bytes, o None si el servidor no expone Content-Length
        o la petición falla (no se bloquea la descarga por esto: solo
        se pierde la estimación de tamaño en la confirmación).
    """
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        if resp.status_code >= 400:
            return None
        size = resp.headers.get("Content-Length")
        return int(size) if size is not None else None
    except requests.RequestException:
        return None


def download_tile(url, output_path, session=None, cancel_check=None,
                   status_cb=None, progress_cb=None, chunk_size=DOWNLOAD_CHUNK_SIZE):
    """
    Descarga `url` a `output_path` con streaming (no carga el archivo
    completo en memoria -- crítico dado que algunos tiles superan 1 GB).

    Cancelación real: se revisa `cancel_check()` entre CADA chunk
    (1 MiB por defecto), no solo entre archivos -- a diferencia de
    gbif_api, acá un solo archivo puede tardar minutos, así que la
    cancelación tiene que poder cortar a mitad de descarga, no solo
    entre tiles.

    Parámetros
    ----------
    session : requests.Session opcional
        Si se pasa, el llamador puede cerrarla desde otro hilo
        (session.close()) para abortar la descarga en curso -- mismo
        mecanismo que gbif_api.search_occurrences.
    progress_cb : callable(int, int) opcional
        (bytes_descargados, total_bytes_o_None).

    Retorna
    -------
    str
        Ruta del archivo descargado.

    Lanza
    -----
    GeodataApiError
        Si la petición HTTP falla (404, error de servidor, etc.).
    GeodataCanceled
        Si se canceló a mitad de descarga.
    """
    owns_session = session is None
    session = session or requests.Session()

    tmp_path = output_path + ".part"
    downloaded = 0

    try:
        if cancel_check is not None and cancel_check():
            raise GeodataCanceled()

        try:
            resp = session.get(url, stream=True, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            if cancel_check is not None and cancel_check():
                raise GeodataCanceled() from exc
            raise GeodataApiError(f"No se pudo descargar {url}: {exc}") from exc

        total = resp.headers.get("Content-Length")
        total = int(total) if total is not None else None

        if status_cb:
            status_cb(f"Descargando {os.path.basename(output_path)}...")

        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if cancel_check is not None and cancel_check():
                    raise GeodataCanceled()
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)

        os.replace(tmp_path, output_path)
        return output_path

    except GeodataCanceled:
        _cleanup_partial(tmp_path)
        raise
    except Exception:
        _cleanup_partial(tmp_path)
        raise
    finally:
        if owns_session:
            try:
                session.close()
            except Exception as err:
                logger.debug("Error cerrando sesion HTTP: %s", err)


def _cleanup_partial(tmp_path):
    """Borra el archivo .part si quedó a medio escribir (cancelación o error)."""
    try:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    except OSError as err:
        logger.debug("No se pudo eliminar archivo temporal %s: %s", tmp_path, err)
