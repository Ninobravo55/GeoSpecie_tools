"""
batch.py — API síncrona y desacoplada de Qt para descargar ocurrencias
GBIF desde la consola de Python de QGIS, un script standalone, o (a
futuro) un modelo de Processing -- sin instanciar SpeciesDownloadDialog
ni ningún QWidget.

Comparte el mismo pipeline de descarga+filtrado+exportación que usa
worker.py detrás del diálogo (run_download_pipeline): la GUI y el uso
por script llaman a la MISMA función para la parte pesada, en vez de
mantener dos implementaciones que puedan divergir con el tiempo (esa
duplicación fue justamente el riesgo que motivó este módulo).

Ejemplo (consola de Python de QGIS)
------------------------------------
>>> from GeoSpecies_Tools.species_download import batch
>>> resultado = batch.download_species_records(
...     "Puma concolor", "/tmp/puma_peru.gpkg",
...     country_code="PE", only_living=True,
...     status_cb=print,
... )
>>> resultado["count"]

Batch de varias especies (por ejemplo, insumos para MaxEnt)
-------------------------------------------------------------
>>> batch.download_multiple_species(
...     ["Puma concolor", "Panthera onca", 2435099],
...     "/tmp/especies_estudio",
...     country_code="PE",
... )
"""

import os

from . import gbif_api, exporter
from .aoi import layer_geometry_to_wkt_4326
from .filters import build_query_params, is_living_record, dedupe_records


def run_download_pipeline(query_params, output_path, max_records,
                           only_living, remove_duplicates, target_crs_authid=None,
                           progress_cb=None, status_cb=None, cancel_check=None, session=None):
    """
    Núcleo compartido: busca ocurrencias con `query_params` ya armado,
    aplica los filtros locales (living/duplicados, que GBIF no resuelve
    server-side) y exporta. Es lo único que worker.py delega ahora --
    antes tenía esta misma lógica duplicada inline dentro de run().

    Retorna
    -------
    dict: {output_path, count, canceled}
        Si `canceled` es True, output_path es None y count es 0 (no se
        llegó a exportar nada).
    """
    records = gbif_api.search_occurrences(
        query_params, max_records=max_records, progress_cb=progress_cb,
        status_cb=status_cb, cancel_check=cancel_check, session=session,
    )

    if cancel_check is not None and cancel_check():
        return {"output_path": None, "count": 0, "canceled": True}

    if status_cb:
        status_cb(f"GBIF devolvió {len(records)} registro(s) crudos. Aplicando filtros locales...")

    if only_living:
        before = len(records)
        records = [r for r in records if is_living_record(r)]
        if status_cb:
            status_cb(f"Filtro 'solo vivos': {before} -> {len(records)} registros.")

    if remove_duplicates:
        before = len(records)
        records = dedupe_records(records)
        if status_cb:
            status_cb(f"Filtro 'eliminar duplicados': {before} -> {len(records)} registros.")

    if cancel_check is not None and cancel_check():
        return {"output_path": None, "count": 0, "canceled": True}

    if status_cb:
        status_cb("Exportando capa de salida...")

    final_path, written = exporter.export_points(records, output_path, target_crs_authid=target_crs_authid)
    return {"output_path": final_path, "count": written, "canceled": False}


def resolve_species(species):
    """
    Resuelve `species` al taxonKey correcto para occurrence/search.

    Parámetros
    ----------
    species : int | str
        taxonKey/usageKey de GBIF (int), o nombre científico (str) a
        buscar vía species/suggest (se toma la primera coincidencia cuyo
        scientificName empiece con el texto dado; si no hay ninguna así,
        se toma la primera sugerencia sin más, y se avisa que fue
        ambiguo). Para evitar cualquier ambigüedad en un script, pasa
        directamente el taxonKey (int) en vez del nombre.

    Retorna
    -------
    tuple(int, str | None)
        (taxonKey a usar, mensaje informativo -- ambigüedad resuelta,
        sinónimo resuelto, o None si no hubo nada que informar).

    Lanza
    -----
    gbif_api.GbifApiError
        Si species es un nombre y no se encuentra ninguna coincidencia,
        o si alguna petición de red falla.
    """
    if isinstance(species, str):
        matches = gbif_api.suggest_species(species, limit=20)
        if not matches:
            raise gbif_api.GbifApiError(f"No se encontraron especies para '{species}' en GBIF.")

        name_lower = species.strip().lower()
        exact = [m for m in matches if m["scientificName"].lower().startswith(name_lower)]
        chosen = exact[0] if exact else matches[0]

        prefix = f"'{species}' -> '{chosen['scientificName']}' (taxonKey {chosen['key']})."
        if not exact and len(matches) > 1:
            prefix += (
                f" Ambiguo: {len(matches)} coincidencias en GBIF, se tomó la primera. "
                "Pasa el taxonKey (int) directamente para evitar esta ambigüedad."
            )

        resolved_key, synonym_message = gbif_api.resolve_accepted_taxon_key(chosen["key"])
        message = f"{prefix} {synonym_message}" if synonym_message else prefix
        return resolved_key, message

    return gbif_api.resolve_accepted_taxon_key(int(species))


def download_species_records(species, output_path, *,
                              country_code=None, aoi_layer=None, geometry_wkt=None,
                              year_min=1990, year_max=2025, uncertainty_m=1000,
                              only_valid_coordinates=True, no_geo_issues=True,
                              only_living=False, remove_duplicates=True,
                              target_crs_authid=None,
                              max_records=gbif_api.ABSOLUTE_MAX_RECORDS,
                              progress_cb=None, status_cb=None, cancel_check=None, session=None,
                              skip_resolve_key=None):
    """
    Descarga, filtra y exporta ocurrencias de UNA especie. Punto de
    entrada síncrono para consola de Python / scripts / modelos de
    Processing -- no depende de ningún QWidget ni de QThread.

    Parámetros
    ----------
    species : int | str
        Ver resolve_species().
    aoi_layer : QgsVectorLayer opcional
        Capa de polígonos para delimitar el área. Si se pasa junto con
        country_code o geometry_wkt por error, se prioriza en ese orden:
        geometry_wkt explícito > aoi_layer > country_code.
    geometry_wkt : str opcional
        WKT ya calculado, útil para reutilizar la misma AOI entre varias
        llamadas de un batch sin recalcular la geometría en cada especie.
    skip_resolve_key : int opcional
        Uso interno (worker.py): si se pasa, se asume que `species` ya
        es un taxonKey resuelto (sinónimo ya verificado por el llamador)
        y se salta resolve_species() para no duplicar esa petición de
        red. No usar desde script/consola -- ahí siempre conviene dejar
        que se resuelva.

    Retorna
    -------
    dict: {output_path, count, taxon_key, resolve_message, canceled}
    """
    if skip_resolve_key is not None:
        taxon_key, resolve_message = skip_resolve_key, None
    else:
        taxon_key, resolve_message = resolve_species(species)
        if status_cb and resolve_message:
            status_cb(resolve_message)

    if geometry_wkt is None and aoi_layer is not None:
        geometry_wkt = layer_geometry_to_wkt_4326(aoi_layer)

    query_params = build_query_params(
        species_key=taxon_key, year_min=year_min, year_max=year_max,
        uncertainty_m=uncertainty_m, only_valid_coordinates=only_valid_coordinates,
        no_geo_issues=no_geo_issues, country_code=country_code, geometry_wkt=geometry_wkt,
    )

    result = run_download_pipeline(
        query_params, output_path, max_records=max_records,
        only_living=only_living, remove_duplicates=remove_duplicates,
        target_crs_authid=target_crs_authid, progress_cb=progress_cb,
        status_cb=status_cb, cancel_check=cancel_check, session=session,
    )
    result["taxon_key"] = taxon_key
    result["resolve_message"] = resolve_message
    return result


def download_multiple_species(species_list, output_folder, **common_kwargs):
    """
    Descarga en lote: una capa por especie dentro de `output_folder`
    (nombre de archivo derivado del nombre/taxonKey dado, saneado).

    NO se detiene ante un error individual -- sigue con la siguiente
    especie y acumula el error en el resultado. Pensado para preparar
    insumos de modelamiento de nicho (MaxEnt, ya presente en este mismo
    plugin) con varias especies del mismo estudio en una sola llamada.

    Parámetros
    ----------
    species_list : list[int | str]
        Especies a descargar (mismo formato que resolve_species()).
    output_folder : str
        Carpeta de salida (se crea si no existe). Cada especie genera
        <nombre_saneado>.shp dentro de esta carpeta.
    **common_kwargs
        Se pasan tal cual a download_species_records() para cada especie
        (country_code, aoi_layer, filtros, target_crs_authid, etc.).

    Retorna
    -------
    list[tuple(species, dict)]
        dict trae las mismas claves que download_species_records(), o
        {'error': str(...)} si esa especie en particular falló (una
        especie sin datos o con nombre no encontrado no aborta el resto
        del lote).
    """
    os.makedirs(output_folder, exist_ok=True)
    results = []
    for species in species_list:
        raw_name = species if isinstance(species, str) else str(species)
        safe_name = "".join(c if c.isalnum() else "_" for c in raw_name).strip("_") or "especie"
        output_path = os.path.join(output_folder, f"{safe_name}.shp")
        try:
            result = download_species_records(species, output_path, **common_kwargs)
        except gbif_api.GbifApiError as exc:
            result = {"error": str(exc)}
        except exporter.ExportError as exc:
            result = {"error": str(exc)}
        results.append((species, result))
    return results
