"""
catalog.py — Listas de valores válidos para el diálogo de tiles
climáticos. Todo extraído directamente del código fuente del paquete R
`geodata` (R/worldclim.R, constante `.cmods`) y verificado contra el
listado real del servidor (geodata.ucdavis.edu/cmip6/2.5m/), no de
memoria.

Separación deliberada de variables por modo: WorldClim (histórico) y
CMIP6 (proyección) NO comparten el mismo set de variables. Mezclarlas
en un único checklist permite armar combinaciones que no existen en el
servidor (ver conversación: cmip6_tile/cmip6_world en R solo aceptan
tmin/tmax/prec/bioc -- wind, srad y tavg son exclusivos del
histórico).
"""

MODE_HISTORICAL = "historical"
MODE_CMIP6 = "cmip6"

# ---------------------------------------------------------------------
# Modelos CMIP6 -- lista completa (26), copiada literal de `.cmods` en
# R/worldclim.R del paquete geodata (rspatial/geodata). NO es una lista
# reducida de ejemplo: son los únicos 26 modelos que el propio paquete
# R considera válidos (`.check_cmip6` rechaza cualquier otro con un
# error explícito).
# ---------------------------------------------------------------------
CMIP6_MODELS = [
    "ACCESS-CM2", "ACCESS-ESM1-5", "AWI-CM-1-1-MR", "BCC-CSM2-MR",
    "CanESM5", "CanESM5-CanOE", "CMCC-ESM2", "CNRM-CM6-1", "CNRM-CM6-1-HR",
    "CNRM-ESM2-1", "EC-Earth3-Veg", "EC-Earth3-Veg-LR", "FIO-ESM-2-0",
    "GFDL-ESM4", "GISS-E2-1-G", "GISS-E2-1-H", "HadGEM3-GC31-LL",
    "INM-CM4-8", "INM-CM5-0", "IPSL-CM6A-LR", "MIROC-ES2L", "MIROC6",
    "MPI-ESM1-2-HR", "MPI-ESM1-2-LR", "MRI-ESM2-0", "UKESM1-0-LL",
]

CMIP6_SSPS = ["126", "245", "370", "585"]

CMIP6_PERIODS = ["2021-2040", "2041-2060", "2061-2080", "2081-2100"]

# Variable interna -> (etiqueta visible, disponible en histórico, disponible en CMIP6)
# "bio"/"bioc" es la misma variable en ambos productos (CMIP6 la llama
# internamente "bioc" en el servidor, pero se expone una sola opción
# "bio" al usuario -- el propio R remapea bio->bioc para CMIP6).
VARIABLES = {
    "tmin":  {"label": "Temperatura mínima (tmin)",     "historical": True,  "cmip6": True},
    "tmax":  {"label": "Temperatura máxima (tmax)",     "historical": True,  "cmip6": True},
    "prec":  {"label": "Precipitación (prec)",          "historical": True,  "cmip6": True},
    "bio":   {"label": "Variables bioclimáticas (bio)", "historical": True,  "cmip6": True},
    "tavg":  {"label": "Temperatura promedio (tavg)",   "historical": True,  "cmip6": False},
    "wind":  {"label": "Velocidad del viento (wind)",   "historical": True,  "cmip6": False},
    "srad":  {"label": "Radiación solar (srad)",        "historical": True,  "cmip6": False},
}


def variables_for_mode(mode):
    """
    Devuelve [(var_interna, etiqueta), ...] filtrado según el modo
    ('historical' o 'cmip6'). Esto es lo que alimenta el checklist de
    variables en el diálogo -- nunca se muestran las 9 variables juntas
    sin filtrar por modo.
    """
    if mode not in (MODE_HISTORICAL, MODE_CMIP6):
        raise ValueError(f"Modo inválido: {mode!r} (debe ser {MODE_HISTORICAL!r} o {MODE_CMIP6!r})")
    key = "historical" if mode == MODE_HISTORICAL else "cmip6"
    return [(var, spec["label"]) for var, spec in VARIABLES.items() if spec[key]]


def is_variable_valid_for_mode(var, mode):
    if var not in VARIABLES:
        return False
    key = "historical" if mode == MODE_HISTORICAL else "cmip6"
    return bool(VARIABLES[var].get(key))
