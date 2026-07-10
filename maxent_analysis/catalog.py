"""
catalog.py — Constantes del análisis de resultados MaxEnt: los 11
criterios de threshold que expone maxentResults.csv, y los cortes fijos
de clasificación de hábitat potencial (heredados tal cual del script
original -- no se tocan salvo que se pida explícitamente).
"""

# ---------------------------------------------------------------------
# Criterios de threshold logístico -- copiados literal del script
# original, que ya los tenía correctos (solo estaban armados y sin usar).
# Clave = etiqueta visible en el combobox; valor = sufijo de columna en
# maxentResults.csv (se le antepone el nombre del criterio + " Logistic
# threshold", ver reader.resolve_threshold_column).
# ---------------------------------------------------------------------
THRESHOLD_CRITERIA = [
    "Maximum training sensitivity plus specificity",
    "Maximum test sensitivity plus specificity",
    "Equal training sensitivity and specificity",
    "Equal test sensitivity and specificity",
    "10 percentile training presence",
    "Minimum training presence",
    "Balance training omission, predicted area and threshold value",
    "Fixed cumulative value 1",
    "Fixed cumulative value 5",
    "Fixed cumulative value 10",
    "Equate entropy of thresholded and original distributions",
]

DEFAULT_THRESHOLD_CRITERION = "Maximum training sensitivity plus specificity"

# Sufijos de columna que MaxEnt usa según el tipo de salida del modelo.
# Versiones recientes (>=3.4) usan "Cloglog" por defecto en vez de
# "Logistic" -- el script original asumía "Logistic" sin verificar, lo
# que rompe silenciosamente con corridas más nuevas.
THRESHOLD_COLUMN_SUFFIXES = ["Logistic threshold", "Cloglog threshold"]

# ---------------------------------------------------------------------
# Clasificación de hábitat potencial -- 4 clases con cortes FIJOS
# (0.2/0.4/0.6), tal como en el script original. Esto es independiente
# del threshold logístico elegido arriba -- son dos cosas distintas:
# el threshold define el mapa BINARIO (presencia/ausencia), estos
# cortes definen el mapa de 4 clases de idoneidad. No se modificó este
# comportamiento porque no fue parte de lo acordado en esta sesión.
# ---------------------------------------------------------------------
HABITAT_CLASSES = [
    {"value": 1, "max": 0.2, "label": "Hábitat inadecuado", "color": "#b6b2ab"},
    {"value": 2, "max": 0.4, "label": "Hábitat poco adecuado", "color": "#33b63e"},
    {"value": 3, "max": 0.6, "label": "Hábitat adecuado", "color": "#e0ee1a"},
    {"value": 4, "max": None, "label": "Hábitat muy adecuado", "color": "#ee0004"},
]


# ---------------------------------------------------------------------
# Índice de importancia integrada -- I = (w1*Contribución + w2*Permutación
# + w3*Jackknife_normalizado) / (suma_pesos_disponibles * 100)
#
# Pesos propuestos por el usuario: 0.4 Contribución, 0.3 Permutación,
# 0.3 Jackknife. Si Jackknife no está disponible para una especie
# (opción no activada al correr MaxEnt), se renormaliza dividiendo por
# la suma de los pesos SÍ disponibles (0.4+0.3=0.7) en vez de asumir 0
# para el término faltante -- así una especie sin jackknife no queda
# artificialmente penalizada frente a una que sí lo tiene.
# ---------------------------------------------------------------------
INDEX_WEIGHT_CONTRIBUTION = 0.4
INDEX_WEIGHT_PERMUTATION = 0.3
INDEX_WEIGHT_JACKKNIFE = 0.3

# Señal de Jackknife a usar: poder explicativo AISLADO de la variable
# (equivalente en espíritu a Contribución/Permutación), no el costo de
# removerla (esa es otra pregunta -- útil para decidir qué eliminar por
# colinealidad, no para rankear importancia).
DEFAULT_JACKKNIFE_SIGNAL = "with_only"


def habitat_raster_formula(band="A"):
    """
    Arma la fórmula de gdal:rastercalculator para las 4 clases, a partir
    de HABITAT_CLASSES, en vez de tenerla hardcodeada como string suelto
    (si se cambian los cortes en HABITAT_CLASSES, la fórmula se
    recalcula sola).
    """
    parts = []
    prev_max = None
    for cls in HABITAT_CLASSES:
        if cls["max"] is None:
            cond = f"({band}>={prev_max})"
        elif prev_max is None:
            cond = f"({band}<{cls['max']})"
        else:
            cond = f"(({band}>={prev_max})*({band}<{cls['max']}))"
        parts.append(f"{cond}*{cls['value']}")
        prev_max = cls["max"]
    return " + ".join(parts)
