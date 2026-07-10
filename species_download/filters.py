"""
filters.py — Traducción del estado de la UI a parámetros de consulta GBIF
y reglas de post-filtrado que la API no resuelve directamente (duplicados,
"solo registros vivos").
"""

# ============================================================
# basisOfRecord — GBIF no tiene un flag "solo vivos" directo.
# Se excluyen los tipos de registro que representan material NO vivo
# (fósiles, especímenes preservados). El resto (observaciones humanas,
# de máquina, especímenes vivos, muestras de material) se consideran
# "registros vivos" para efectos de este filtro.
# ============================================================
NOT_LIVING_BASIS_OF_RECORD = ("FOSSIL_SPECIMEN", "PRESERVED_SPECIMEN")


# ============================================================
# Lista de RESPALDO para cmbCountry (código ISO 3166-1 alpha-2 que espera
# el parámetro `country` de GBIF, con nombre en español). Esta lista
# NO es la fuente principal: species_download_dialog.py intenta primero
# gbif_api.fetch_country_enum() (la lista oficial y completa que expone
# GBIF en vivo) y solo cae a esta lista reducida si esa petición falla
# (por ejemplo, sin conexión al abrir el diálogo). Se mantiene chica a
# propósito -- no tiene sentido duplicar a mano las ~195+ entradas que
# GBIF ya expone correctamente por API.
# ============================================================
COUNTRY_LIST_FALLBACK = [
    ("PE", "Perú"), ("CO", "Colombia"), ("EC", "Ecuador"), ("BO", "Bolivia"),
    ("CL", "Chile"), ("AR", "Argentina"), ("BR", "Brasil"), ("VE", "Venezuela"),
    ("PY", "Paraguay"), ("UY", "Uruguay"), ("MX", "México"), ("GT", "Guatemala"),
    ("HN", "Honduras"), ("SV", "El Salvador"), ("NI", "Nicaragua"),
    ("CR", "Costa Rica"), ("PA", "Panamá"), ("CU", "Cuba"),
    ("DO", "República Dominicana"), ("US", "Estados Unidos"), ("CA", "Canadá"),
    ("ES", "España"), ("PT", "Portugal"), ("FR", "Francia"), ("DE", "Alemania"),
    ("IT", "Italia"), ("GB", "Reino Unido"), ("NL", "Países Bajos"),
    ("CH", "Suiza"), ("SE", "Suecia"), ("NO", "Noruega"), ("FI", "Finlandia"),
    ("AU", "Australia"), ("NZ", "Nueva Zelanda"), ("ZA", "Sudáfrica"),
    ("KE", "Kenia"), ("IN", "India"), ("CN", "China"), ("JP", "Japón"),
    ("ID", "Indonesia"), ("MY", "Malasia"), ("PH", "Filipinas"),
    ("TH", "Tailandia"), ("VN", "Vietnam"),
]


def build_query_params(species_key, year_min, year_max, uncertainty_m,
                        only_valid_coordinates, no_geo_issues,
                        country_code=None, geometry_wkt=None):
    """
    Construye el dict de parámetros de occurrence/search a partir del
    estado de los widgets del Grupo 2 (Área de estudio) y Grupo 3 (Filtros).

    Parámetros
    ----------
    species_key : int
        usageKey/taxonKey de la especie seleccionada en lstSpecies.
    year_min, year_max : int
        Rango de años (spYearMin / spYearMax). GBIF espera "min,max".
    uncertainty_m : int
        Incertidumbre máxima en metros (spUncertainty). Se traduce a
        coordinateUncertaintyInMeters=0,<uncertainty_m>.
    only_valid_coordinates : bool
        chkCoordinate -> hasCoordinate=true.
    no_geo_issues : bool
        chkGeoIssue -> hasGeospatialIssue=false.
    country_code : str | None
        Código ISO2 si el Área de estudio es "País". None si es Mundial
        o si se usa una capa de polígonos (en ese caso se usa geometry_wkt).
    geometry_wkt : str | None
        WKT del polígono/multipolígono disuelto de la capa AOI, en
        EPSG:4326, cuando el Área de estudio es "Usar capa de polígonos".

    Retorna
    -------
    dict
        Parámetros listos para pasar a gbif_api.search_occurrences
        (sin 'offset'/'limit', que gestiona esa función).
    """
    params = {
        "taxonKey": species_key,
        "year": f"{int(year_min)},{int(year_max)}",
    }

    if only_valid_coordinates:
        params["hasCoordinate"] = "true"

    if no_geo_issues:
        params["hasGeospatialIssue"] = "false"

    if uncertainty_m is not None and uncertainty_m > 0:
        params["coordinateUncertaintyInMeters"] = f"0,{int(uncertainty_m)}"

    if geometry_wkt:
        params["geometry"] = geometry_wkt
    elif country_code:
        params["country"] = country_code
    # Si no hay country_code ni geometry_wkt => búsqueda Mundial, sin filtro espacial.

    return params


def is_living_record(record):
    """
    True si el registro NO corresponde a material fósil o preservado,
    es decir, si aplica como "registro vivo" (chkLiving).
    """
    basis = (record.get("basisOfRecord") or "").upper()
    return basis not in NOT_LIVING_BASIS_OF_RECORD


def dedupe_records(records):
    """
    Elimina duplicados (chkDuplicate) usando como clave la combinación
    de coordenadas redondeadas + fecha del evento + especie. gbifID por
    sí solo no basta porque distintos registros pueden ser duplicados
    de campo (mismo punto/fecha capturado por dos datasets distintos).
    """
    seen = set()
    unique = []
    for rec in records:
        key = (
            round(rec.get("decimalLatitude", 0.0) or 0.0, 6),
            round(rec.get("decimalLongitude", 0.0) or 0.0, 6),
            rec.get("eventDate"),
            rec.get("speciesKey") or rec.get("taxonKey"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(rec)
    return unique
