"""
catalog.py — Catálogo de productos climáticos actuales de Google Earth Engine
para GeoSpecies Tools.
"""

CLIMATIC_PRODUCTS = [
    {
        "id": "ERA5",
        "name": "ERA5-Land Daily Aggr (ECMWF)",
        "collection": "ECMWF/ERA5_LAND/DAILY_AGGR",
        "res_m": 11132,
        "res_text": "~11 km (0.1°)",
        "min_year": 1950,
        "max_year": 2025,
        "date_range": "1950-01-01 hasta actualidad",
        "desc": "Precipitación, Temperatura (Mín, Máx, Media), Viento, Humedad Relativa, Radiación Solar",
        "vars": [
            {
                "id": "prec",
                "name": "Precipitación Anual Acumulada",
                "unit": "mm/año",
                "default": True,
                "desc": "Suma diaria de precipitación total (en mm). En rangos multianuales calcula el promedio de precipitación anual."
            },
            {
                "id": "tmin",
                "name": "Temperatura Mínima Media",
                "unit": "°C",
                "default": True,
                "desc": "Promedio temporal de temperatura mínima diaria a 2 m."
            },
            {
                "id": "tmax",
                "name": "Temperatura Máxima Media",
                "unit": "°C",
                "default": True,
                "desc": "Promedio temporal de temperatura máxima diaria a 2 m."
            },
            {
                "id": "tmean",
                "name": "Temperatura Media",
                "unit": "°C",
                "default": False,
                "desc": "Promedio temporal de temperatura media diaria a 2 m."
            },
            {
                "id": "wind",
                "name": "Velocidad del Viento Media",
                "unit": "m/s",
                "default": False,
                "desc": "Velocidad del viento a 10 m calculada a partir de los vectores u y v."
            },
            {
                "id": "rh",
                "name": "Humedad Relativa Media",
                "unit": "%",
                "default": False,
                "desc": "Humedad relativa estimada mediante la fórmula de Magnus con temperatura y temperatura de rocío."
            },
            {
                "id": "solar",
                "name": "Radiación Solar Media",
                "unit": "MJ/m²/día",
                "default": False,
                "desc": "Radiación solar de onda corta descendente en superficie diaria promedio."
            },
        ]
    },
    {
        "id": "CHIRPS",
        "name": "CHIRPS Daily Precipitation (UCSB)",
        "collection": "UCSB-CHG/CHIRPS/DAILY",
        "res_m": 5566,
        "res_text": "~5 km (0.05°)",
        "min_year": 1981,
        "max_year": 2025,
        "date_range": "1981-01-01 hasta actualidad",
        "desc": "Precipitación de alta resolución cuasi-global (50°S a 50°N)",
        "vars": [
            {
                "id": "prec",
                "name": "Precipitación Anual Acumulada",
                "unit": "mm/año",
                "default": True,
                "desc": "Suma diaria de precipitación (mm). En rangos multianuales calcula el promedio de precipitación anual."
            }
        ]
    },
    {
        "id": "CFR",
        "name": "NOAA CFSR / CFSv2 (NOAA)",
        "collection": "NOAA/CFSR",
        "collection_v2": "NOAA/CFSV2/FOR6H",
        "res_m": 38000,
        "res_text": "~38 km (0.3°)",
        "min_year": 1979,
        "max_year": 2025,
        "date_range": "1979-01-01 hasta actualidad",
        "desc": "Temperatura Mín/Máx, Humedad Relativa, Viento y Radiación Solar",
        "vars": [
            {
                "id": "tmin",
                "name": "Temperatura Mínima Media",
                "unit": "°C",
                "default": True,
                "desc": "Promedio temporal de temperatura superficial mínima diaria."
            },
            {
                "id": "tmax",
                "name": "Temperatura Máxima Media",
                "unit": "°C",
                "default": True,
                "desc": "Promedio temporal de temperatura superficial máxima diaria."
            },
            {
                "id": "rh",
                "name": "Humedad Relativa Media",
                "unit": "%",
                "default": True,
                "desc": "Humedad relativa media atmosférica en capa superficial."
            },
            {
                "id": "wind",
                "name": "Velocidad del Viento Media",
                "unit": "m/s",
                "default": True,
                "desc": "Velocidad del viento a partir de componentes híbridos u y v."
            },
            {
                "id": "solar",
                "name": "Radiación Solar Media",
                "unit": "MJ/m²/día",
                "default": True,
                "desc": "Flujo de radiación solar de onda corta descendente en superficie."
            },
        ]
    },
    {
        "id": "GPM",
        "name": "NASA GPM IMERG V07",
        "collection": "NASA/GPM_L3/IMERG_V07",
        "res_m": 11132,
        "res_text": "~11 km (0.1°)",
        "min_year": 2000,
        "max_year": 2025,
        "date_range": "2000-06-01 hasta actualidad",
        "desc": "Precipitación calibrada multiradar satelital GPM IMERG V07",
        "vars": [
            {
                "id": "prec",
                "name": "Precipitación Anual Acumulada",
                "unit": "mm/año",
                "default": True,
                "desc": "Precipitación calibrada diaria integrada a partir de la tasa precipitationCal (mm/h)."
            }
        ]
    },
    {
        "id": "PERSIANN",
        "name": "PERSIANN-CDR Precipitation (NOAA)",
        "collection": "NOAA/PERSIANN-CDR",
        "res_m": 27830,
        "res_text": "~28 km (0.25°)",
        "min_year": 1983,
        "max_year": 2025,
        "date_range": "1983-01-01 hasta 2025-11-30",
        "desc": "Precipitación diaria estimada por redes neuronales y microondas",
        "vars": [
            {
                "id": "prec",
                "name": "Precipitación Anual Acumulada",
                "unit": "mm/año",
                "default": True,
                "desc": "Precipitación diaria acumulada (mm/día)."
            }
        ]
    },
    {
        "id": "GSMAP",
        "name": "JAXA GSMaP v8 Operational",
        "collection": "JAXA/GPM_L3/GSMaP/v8/operational",
        "res_m": 11132,
        "res_text": "~11 km (0.1°)",
        "min_year": 1998,
        "max_year": 2025,
        "date_range": "1998-01-01 hasta actualidad",
        "desc": "Precipitación global satelital GSMaP operacional versión 8",
        "vars": [
            {
                "id": "prec",
                "name": "Precipitación Anual Acumulada",
                "unit": "mm/año",
                "default": True,
                "desc": "Suma diaria de precipitación obtenida de la tasa horaria corrigida por pluviómetros."
            }
        ]
    },
    {
        "id": "RAIN4PE",
        "name": "RAIN4PE Perú y Ecuador",
        "collection": "projects/sat-io/open-datasets/rainpe/daily",
        "res_m": 10000,
        "res_text": "~10 km (0.1°)",
        "min_year": 1981,
        "max_year": 2015,
        "date_range": "1981-01-01 hasta 2015-12-31",
        "desc": "Precipitación diaria grillada corregida para cuencas de Perú y Ecuador",
        "vars": [
            {
                "id": "prec",
                "name": "Precipitación Anual Acumulada",
                "unit": "mm/año",
                "default": True,
                "desc": "Precipitación diaria acumulada b1 (mm/día)."
            }
        ]
    },
    {
        "id": "PISCO",
        "name": "PISCOee pm v1.0 Perú",
        "collection": "users/lgutierrezlf/PISCOeo_pm/daily",
        "res_m": 1000,
        "res_text": "~1 km (0.01°)",
        "min_year": 1981,
        "max_year": 2016,
        "date_range": "1981-01-01 hasta 2016-12-31",
        "desc": "Precipitación diaria grillada PISCO a 1 km para Perú",
        "vars": [
            {
                "id": "prec",
                "name": "Precipitación Anual Acumulada",
                "unit": "mm/año",
                "default": True,
                "desc": "Precipitación diaria pp (mm/día) corregida por SENAMHI."
            }
        ]
    },
    {
        "id": "PISCOV3",
        "name": "PISCOp v3.0 Perú",
        "collection": "projects/gee-unas/assets/PISCO_V3",
        "res_m": 10000,
        "res_text": "~10 km (0.1°)",
        "min_year": 1981,
        "max_year": 2025,
        "date_range": "1981-01-01 hasta 2025-12-31",
        "desc": "Precipitación grillada PISCOp versión 3.0 para Perú",
        "vars": [
            {
                "id": "prec",
                "name": "Precipitación Anual Acumulada",
                "unit": "mm/año",
                "default": True,
                "desc": "Precipitación diaria multianual (assets anuales multibanda)."
            }
        ]
    }
]


def get_product(product_id):
    """Devuelve el diccionario de producto correspondiente o None."""
    for p in CLIMATIC_PRODUCTS:
        if p["id"] == product_id:
            return p
    return None
