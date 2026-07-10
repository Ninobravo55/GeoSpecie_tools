"""
reader.py — Lectura de maxentResults.csv y del reporte HTML de MaxEnt.

Puro Python + pandas/bs4 (sin qgis) -- testeable sin QGIS instalado.
"""

import io
import os
import re

import pandas as pd
from bs4 import BeautifulSoup

from . import catalog


class MaxentReadError(Exception):
    """Error al leer o interpretar resultados de MaxEnt."""


# ---------------------------------------------------------------------
# maxentResults.csv
# ---------------------------------------------------------------------

def read_results_csv(csv_path):
    """Lee maxentResults.csv tal cual, sin reordenar (el orden de
    procesamiento lo decide group_species_rows, no la posición en el
    archivo)."""
    if not os.path.isfile(csv_path):
        raise MaxentReadError(f"No existe el archivo: {csv_path}")
    try:
        return pd.read_csv(csv_path)
    except Exception as exc:  # noqa: BLE001 - pandas puede lanzar varios tipos según el error de parseo
        raise MaxentReadError(f"No se pudo leer {csv_path}: {exc}") from exc


def species_base_name(species_display):
    """
    Quita el sufijo " (average)" (con o sin mayúsculas/espacios raros)
    que MaxEnt agrega a la fila promedio cuando se corrieron réplicas.
    Este nombre "base" es el que hay que usar para buscar los archivos
    .asc/.html en disco -- esos archivos NUNCA incluyen el sufijo
    "(average)" en su nombre.
    """
    return re.sub(r"\s*\(average\)\s*$", "", str(species_display), flags=re.IGNORECASE).strip()


def is_average_row(species_display):
    return bool(re.search(r"\(average\)\s*$", str(species_display), flags=re.IGNORECASE))


def is_individual_replicate_row(species_display):
    """
    Filas de réplicas individuales (no el promedio) suelen venir como
    "especie_0", "especie_1", etc. en la columna Species cuando se usó
    Crossvalidate/Bootstrap. Se detectan para EXCLUIRLAS del
    procesamiento por defecto (ver group_species_rows): si existe una
    fila "(average)" para esa especie, se usa esa; las réplicas
    individuales solo generarían ruido/duplicados, no fueron pedidas.
    """
    return bool(re.search(r"_\d+$", str(species_display).strip()))


def group_species_rows(df, species_column="Species"):
    """
    Agrupa las filas de maxentResults.csv en UNA fila representativa por
    especie:

    - Si existe una fila "<especie> (average)", esa es la representativa
      para las MÉTRICAS (Test AUC, threshold) -- pero OJO: MaxEnt no
      genera un .html con tabla de contribución de variables para un
      modelo "promedio" (el promediado ocurre sobre la superficie de
      predicción, no sobre un modelo reentrenado). El HTML de
      contribución de variables solo existe por réplica individual
      ("<especie>_N.html") -- confirmado con un caso real: no hay
      "<especie>.html" cuando el run usó réplicas, sí "<especie>_0.html".
      Por eso este grupo también incluye `best_replicate_index`: el
      índice N de la réplica con mayor Test AUC, para que
      file_finder.find_species_html() sepa qué archivo buscar.
    - Si no hay réplicas (una sola fila, sin sufijo "_N" ni
      "(average)"), esa fila es la representativa y no hay sufijo de
      réplica que buscar (`best_replicate_index=None`).
    - Filas de réplicas individuales se DESCARTAN del procesamiento de
      MÉTRICAS cuando existe la fila "(average)" (no se genera una
      subcarpeta de salida por réplica), pero igual se usan para
      determinar `best_replicate_index`.

    Retorna
    -------
    list[dict]
        [{"species_display": str, "species_base": str, "row": pd.Series,
          "is_average": bool, "best_replicate_index": int|None}, ...]

    Lanza
    -----
    MaxentReadError
        Si `species_column` no existe en el DataFrame.
    """
    if species_column not in df.columns:
        raise MaxentReadError(f"El CSV no tiene una columna '{species_column}'.")

    average_rows = {}
    single_rows = {}
    replicate_rows = {}  # base -> list[(idx:int|None, row:pd.Series)]

    for _, row in df.iterrows():
        display = row[species_column]

        if is_average_row(display):
            base = species_base_name(display)
            average_rows[base] = row
        elif is_individual_replicate_row(display):
            # OJO: acá el sufijo a quitar es "_N" (el índice de réplica),
            # NO "(average)" -- species_base_name() solo sabe quitar
            # "(average)", así que una réplica "Puma concolor_0" quedaba
            # con base "Puma concolor_0" completo, sin agrupar nunca con
            # la fila "Puma concolor (average)".
            base = re.sub(r"_\d+\s*$", "", str(display)).strip()
            idx_match = re.search(r"_(\d+)\s*$", str(display))
            idx = int(idx_match.group(1)) if idx_match else None
            replicate_rows.setdefault(base, []).append((idx, row))
        else:
            base = species_base_name(display)
            single_rows[base] = row

    all_bases = list(dict.fromkeys(
        list(average_rows.keys()) + list(single_rows.keys()) + list(replicate_rows.keys())
    ))

    def _best_replicate(base):
        """(idx, row) de la réplica con mayor Test AUC para `base`, o
        (None, None) si no hay réplicas individuales registradas."""
        reps = replicate_rows.get(base)
        if not reps:
            return None, None
        return max(reps, key=lambda pair: pair[1].get("Test AUC", float("-inf")))

    result = []
    for base in all_bases:
        best_idx, best_rep_row = _best_replicate(base)

        if base in average_rows:
            result.append({
                "species_display": average_rows[base][species_column],
                "species_base": base, "row": average_rows[base], "is_average": True,
                "best_replicate_index": best_idx,
            })
        elif base in single_rows:
            result.append({
                "species_display": single_rows[base][species_column],
                "species_base": base, "row": single_rows[base], "is_average": False,
                "best_replicate_index": None,
            })
        else:
            # Solo hay réplicas individuales, sin fila "(average)" --
            # caso atípico (debería existir el promedio si hubo
            # réplicas), se usa la de mejor Test AUC como representativa
            # tanto para métricas como para el HTML.
            result.append({
                "species_display": best_rep_row[species_column],
                "species_base": base, "row": best_rep_row, "is_average": False,
                "best_replicate_index": best_idx,
            })

    return result


def resolve_threshold_column(row, criterion):
    """
    Encuentra la columna de threshold correcta para `criterion`,
    probando primero "<criterion> Logistic threshold" y luego
    "<criterion> Cloglog threshold" -- el script original asumía
    "Logistic" siempre, lo que rompe con corridas MaxEnt >=3.4 que usan
    Cloglog por defecto.

    Retorna
    -------
    tuple(float, str)
        (valor del threshold, nombre de columna usado).

    Lanza
    -----
    MaxentReadError
        Si ninguna de las variantes de columna existe en `row`.
    """
    for suffix in catalog.THRESHOLD_COLUMN_SUFFIXES:
        col = f"{criterion} {suffix}"
        if col in row.index:
            return float(row[col]), col

    raise MaxentReadError(
        f"No se encontró ninguna columna de threshold para el criterio '{criterion}' "
        f"(se probó con los sufijos: {', '.join(catalog.THRESHOLD_COLUMN_SUFFIXES)})."
    )


# ---------------------------------------------------------------------
# HTML de MaxEnt -- tabla de contribución de variables
# ---------------------------------------------------------------------

def parse_variable_contribution_table(html_path):
    """
    Extrae la tabla de "Analysis of variable contributions" del reporte
    HTML de una especie.

    A diferencia del script original (`tablas_html[1]`, que asume que
    esa tabla siempre está en la posición 1 del documento), esta función
    busca la tabla POR CONTENIDO de sus encabezados
    ("percent contribution" / "permutation importance"), no por
    posición -- la posición puede variar entre versiones de MaxEnt o
    según qué otras secciones se generaron en ese reporte particular.

    Retorna
    -------
    pd.DataFrame
        Columnas: "Variable", "Porcentaje contribucion",
        "Importancia contribucion". Ordenado por "Porcentaje
        contribucion" descendente.

    Lanza
    -----
    MaxentReadError
        Si no se encuentra ninguna tabla con esos encabezados.
    """
    if not os.path.isfile(html_path):
        raise MaxentReadError(f"No existe el archivo HTML: {html_path}")

    with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    target = None
    for table in tables:
        header_text = " ".join(th.get_text(strip=True).lower() for th in table.find_all(["th", "td"])[:6])
        if "percent contribution" in header_text and "permutation importance" in header_text:
            target = table
            break

    if target is None:
        raise MaxentReadError(
            f"No se encontró la tabla de contribución de variables en {os.path.basename(html_path)} "
            "(se buscó por encabezados 'percent contribution' / 'permutation importance', no por posición)."
        )

    try:
        df = pd.read_html(io.StringIO(str(target)), flavor="lxml")[0]
    except Exception as exc:  # noqa: BLE001 - pandas/lxml pueden lanzar varios tipos según el HTML
        raise MaxentReadError(f"No se pudo parsear la tabla de variables en {html_path}: {exc}") from exc

    df.columns = ["Variable", "Porcentaje contribucion", "Importancia contribucion"][:len(df.columns)]
    for col in ("Porcentaje contribucion", "Importancia contribucion"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.sort_values(by="Porcentaje contribucion", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------
# Índice de importancia integrada (Contribución + Permutación + Jackknife)
# ---------------------------------------------------------------------

def _normalize_colname(name):
    """Colapsa separadores (puntos/espacios/guiones bajos) y mayúsculas
    para poder buscar columnas por PATRÓN en vez de nombre exacto -- no
    hay una convención de nombre de columna de jackknife confirmada de
    forma literal en la documentación de MaxEnt."""
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()


def extract_jackknife_gain(row, variable, signal="with_only"):
    """
    Busca en las columnas de `row` (una fila de maxentResults.csv) el
    valor de ganancia de jackknife para `variable`.

    Formato de columna CONFIRMADO contra un maxentResults.csv real
    (subido por el usuario, corrida con jackknife activado):
    "Training gain with only <variable>" / "Training gain without
    <variable>" -- con espacios, sin la palabra "variable" de por
    medio. El matcher tolera además variantes con puntos/guiones bajos
    y mayúsculas distintas, por si otra versión de MaxEnt difiere.

    Parámetros
    ----------
    row : pd.Series
    variable : str
        Nombre exacto de la variable (ej. "bio1"). Se hace match por
        TOKEN completo tras normalizar -- "bio1" no matchea "bio12".
    signal : str
        "with_only" -> "training gain with only <variable>" (poder
        explicativo aislado; default para el índice integrado).
        "without" -> "training gain without <variable>" (costo de
        remover la variable).

    Retorna
    -------
    float | None
        None si no se encontró ninguna columna que matchee (jackknife
        no estaba activado en esa corrida, o el nombre de columna real
        difiere más de lo que este matcher tolera).
    """
    var_norm = _normalize_colname(variable)
    var_pattern = re.compile(rf"(^|\s){re.escape(var_norm)}(\s|$)")

    candidates = []
    for col in row.index:
        col_norm = _normalize_colname(col)
        if "gain" not in col_norm:
            continue
        if not var_pattern.search(col_norm):
            continue
        if signal == "with_only" and "with only" in col_norm:
            candidates.append(col)
        elif signal == "without" and "without" in col_norm and "with only" not in col_norm:
            candidates.append(col)

    if not candidates:
        return None

    # Preferir "training gain" sobre "test gain" si ambas están presentes.
    training = [c for c in candidates if "training" in _normalize_colname(c)]
    chosen = training[0] if training else candidates[0]

    try:
        return float(row[chosen])
    except (TypeError, ValueError):
        return None


def normalize_gains_to_0_100(raw_gains):
    """
    Normaliza un dict {variable: gain_crudo|None} a escala 0-100,
    relativa entre las variables CON dato disponible de esa misma
    especie (min-max). Necesario porque el gain de jackknife (nats,
    típicamente 0-4) no está en la misma escala que Contribución/
    Permutación (0-100) -- combinarlos sin normalizar no es válido.

    Si todas las variables tienen el mismo gain (o solo hay una), se
    asigna 100 a las que sí tienen dato (evita división por cero sin
    inventar un valor arbitrario para desempatar).
    """
    valid = {k: v for k, v in raw_gains.items() if v is not None}
    if not valid:
        return {k: None for k in raw_gains}

    vmin, vmax = min(valid.values()), max(valid.values())
    if vmax == vmin:
        return {k: (100.0 if v is not None else None) for k, v in raw_gains.items()}

    return {
        k: (100.0 * (v - vmin) / (vmax - vmin) if v is not None else None)
        for k, v in raw_gains.items()
    }


def compute_integrated_index(contribution, permutation, jackknife_norm=None,
                              w_contribution=catalog.INDEX_WEIGHT_CONTRIBUTION,
                              w_permutation=catalog.INDEX_WEIGHT_PERMUTATION,
                              w_jackknife=catalog.INDEX_WEIGHT_JACKKNIFE):
    """
    I = (w1*Contribución + w2*Permutación + w3*Jackknife_norm) /
        (suma_de_pesos_disponibles * 100)

    Si jackknife_norm es None, se excluye del promedio y se renormaliza
    dividiendo solo por (w_contribution + w_permutation) -- una especie
    sin jackknife activado no queda penalizada con un índice más bajo
    solo por faltarle ese dato.
    """
    terms = [(w_contribution, contribution), (w_permutation, permutation)]
    if jackknife_norm is not None:
        terms.append((w_jackknife, jackknife_norm))

    weight_sum = sum(w for w, _ in terms)
    if weight_sum == 0:
        return None

    weighted = sum(w * v for w, v in terms)
    return weighted / (weight_sum * 100.0)


def build_integrated_ranking(contrib_df, row, jackknife_signal=catalog.DEFAULT_JACKKNIFE_SIGNAL):
    """
    Arma el ranking único de variables (Contribución + Permutación +
    Jackknife integrados en un solo índice), a partir de la tabla de
    contribución/permutación ya parseada y la fila de maxentResults.csv
    (para extraer jackknife, si está disponible).

    Retorna
    -------
    tuple(pd.DataFrame, bool)
        (df con columnas Ranking/Variable/Contribución/Permutación/
        Jackknife crudo/Jackknife normalizado/Índice Integrado,
        ordenado por Índice descendente; bool = True si se pudo usar
        jackknife para al menos una variable).
    """
    variables = list(contrib_df["Variable"])
    raw_gains = {var: extract_jackknife_gain(row, var, signal=jackknife_signal) for var in variables}
    norm_gains = normalize_gains_to_0_100(raw_gains)
    any_jackknife = any(v is not None for v in raw_gains.values())

    rows = []
    for _, r in contrib_df.iterrows():
        var = r["Variable"]
        contrib = r["Porcentaje contribucion"]
        perm = r["Importancia contribucion"]
        jack_norm = norm_gains.get(var)
        index = compute_integrated_index(contrib, perm, jack_norm)
        rows.append({
            "Variable": var,
            "Contribución (%)": contrib,
            "Permutación (%)": perm,
            "Jackknife (gain crudo)": raw_gains.get(var),
            "Jackknife (normalizado 0-100)": jack_norm,
            "Índice Integrado": index,
        })

    ranking_df = pd.DataFrame(rows).sort_values("Índice Integrado", ascending=False).reset_index(drop=True)
    ranking_df.insert(0, "Ranking", range(1, len(ranking_df) + 1))
    return ranking_df, any_jackknife
