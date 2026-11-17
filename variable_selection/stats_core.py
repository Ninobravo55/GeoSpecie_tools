"""
stats_core.py — Núcleo estadístico de la herramienta "Selección de Variables".

Implementa en **numpy puro** (sin scipy) todo lo necesario para replicar el
comportamiento de `virtualspecies::removeCollinearity()` de R:

1. Matriz de correlación por los métodos "pearson", "spearman" y "kendall".
2. Matriz de distancias  d = 1 - |r|.
3. Clasificación jerárquica ascendente con enlace *complete*
   (equivalente a `stats::hclust(d, method = "complete")` de R).
4. Corte del dendrograma a la altura  h = 1 - cutoff
   (equivalente a `stats::cutree(hc, h = ...)` de R).
5. Elección de una variable representativa por grupo.

Motivo de no usar scipy: QGIS trae numpy y GDAL de fábrica, pero scipy no
está garantizado en todas las instalaciones (OSGeo4W, macOS, Linux). Todo
lo que sigue depende únicamente de numpy, ya presente en cualquier QGIS.

La salida de `linkage_complete()` respeta el formato de matriz de enlace de
scipy (`[idx_a, idx_b, altura, n_observaciones]`), de modo que el módulo de
gráficos puede dibujar el dendrograma sin depender de scipy.
"""

import numpy as np

METHODS = ("pearson", "spearman", "kendall")

# Estrategias de elección del representante de cada grupo colineal.
SELECTION_STRATEGIES = ("menor_correlacion_media", "primera_alfabetica", "aleatoria")


# ---------------------------------------------------------------------------
# 1. Correlaciones
# ---------------------------------------------------------------------------

def _rankdata_average(x):
    """
    Rangos con promedio en los empates — equivalente a `rank(x)` de R.
    Necesario para Spearman y para el denominador tau-b de Kendall.
    """
    x = np.asarray(x, dtype=np.float64)
    n = x.size
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(n, dtype=np.float64)
    sorted_x = x[order]

    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_x[j + 1] == sorted_x[i]:
            j += 1
        # Rango promedio (base 1) para todo el bloque de empates
        avg = (i + j) / 2.0 + 1.0
        ranks[order[i:j + 1]] = avg
        i = j + 1
    return ranks


def _count_inversions(arr):
    """Cuenta inversiones mediante merge sort — O(n log n). Auxiliar de Kendall."""
    arr = np.asarray(arr, dtype=np.float64)
    n = arr.size
    if n < 2:
        return 0

    tmp = np.empty(n, dtype=np.float64)
    inversions = 0
    width = 1
    src = arr.copy()

    while width < n:
        for lo in range(0, n, 2 * width):
            mid = min(lo + width, n)
            hi = min(lo + 2 * width, n)
            i, j, k = lo, mid, lo
            while i < mid and j < hi:
                if src[i] <= src[j]:
                    tmp[k] = src[i]
                    i += 1
                else:
                    # src[i..mid) son todos mayores que src[j]
                    inversions += mid - i
                    tmp[k] = src[j]
                    j += 1
                k += 1
            while i < mid:
                tmp[k] = src[i]; i += 1; k += 1
            while j < hi:
                tmp[k] = src[j]; j += 1; k += 1
        src, tmp = tmp, src
        width *= 2

    return inversions


def _tie_correction(x):
    """Suma de t*(t-1)/2 sobre los bloques de empates. Auxiliar de Kendall tau-b."""
    x_sorted = np.sort(np.asarray(x, dtype=np.float64))
    total = 0
    n = x_sorted.size
    i = 0
    while i < n:
        j = i
        while j + 1 < n and x_sorted[j + 1] == x_sorted[i]:
            j += 1
        t = j - i + 1
        if t > 1:
            total += t * (t - 1) // 2
        i = j + 1
    return total


def kendall_tau_b(x, y):
    """
    Coeficiente tau-b de Kendall en O(n log n), equivalente a
    `cor(x, y, method = "kendall")` de R.

    Se usa el conteo de inversiones por merge sort en lugar de la
    comparación O(n²) de todos los pares, porque con muestras de miles de
    píxeles el enfoque cuadrático vuelve la herramienta inutilizable.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n = x.size
    if n < 2:
        return np.nan

    # Ordenar por x, desempatando por y
    order = np.lexsort((y, x))
    x_s = x[order]
    y_s = y[order]

    n0 = n * (n - 1) // 2
    n1 = _tie_correction(x_s)   # pares empatados en x
    n2 = _tie_correction(y_s)   # pares empatados en y

    # Pares empatados simultáneamente en x e y (no cuentan como discordantes)
    n3 = 0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and x_s[j + 1] == x_s[i]:
            j += 1
        if j > i:
            n3 += _tie_correction(y_s[i:j + 1])
        i = j + 1

    # Tras ordenar por (x, y), el nº de inversiones en y equivale al nº de
    # pares discordantes. Los pares empatados se descuentan vía n1/n2/n3.
    dis = _count_inversions(y_s)
    con_minus_dis = n0 - n1 - n2 + n3 - 2 * dis

    denom = np.sqrt(float(n0 - n1)) * np.sqrt(float(n0 - n2))
    if denom == 0:
        return np.nan
    return con_minus_dis / denom


def correlation_matrix(data, method="pearson"):
    """
    Matriz de correlación de las columnas de `data` (filas = observaciones).

    Parámetros
    ----------
    data : ndarray (n_obs, n_vars)
    method : "pearson" | "spearman" | "kendall"

    Retorna
    -------
    ndarray (n_vars, n_vars) — los NaN (p. ej. por varianza cero) se
    sustituyen por 0, igual que hace el flujo de trabajo en R.
    """
    if method not in METHODS:
        raise ValueError(f"Método de correlación inválido: {method}. Use uno de {METHODS}.")

    data = np.asarray(data, dtype=np.float64)
    if data.ndim != 2:
        raise ValueError("Se esperaba un arreglo 2D (observaciones x variables).")

    n_vars = data.shape[1]

    # Una variable de varianza cero (ráster constante) produce división por
    # cero en np.corrcoef. Es un caso legítimo en datos ambientales
    # recortados, y se resuelve abajo forzando esas correlaciones a 0, así
    # que se silencia el aviso en vez de dejar que ensucie el log de QGIS.
    if method == "pearson":
        with np.errstate(invalid="ignore", divide="ignore"):
            mat = np.corrcoef(data, rowvar=False)
    elif method == "spearman":
        ranked = np.column_stack([_rankdata_average(data[:, j]) for j in range(n_vars)])
        with np.errstate(invalid="ignore", divide="ignore"):
            mat = np.corrcoef(ranked, rowvar=False)
    else:  # kendall
        mat = np.eye(n_vars, dtype=np.float64)
        for i in range(n_vars):
            for j in range(i + 1, n_vars):
                tau = kendall_tau_b(data[:, i], data[:, j])
                mat[i, j] = tau
                mat[j, i] = tau

    mat = np.atleast_2d(np.asarray(mat, dtype=np.float64))
    mat[~np.isfinite(mat)] = 0.0
    np.fill_diagonal(mat, 1.0)
    return mat


# ---------------------------------------------------------------------------
# 2. Clasificación jerárquica ascendente (complete linkage)
# ---------------------------------------------------------------------------

def distance_matrix(cor_mat):
    """Distancia d = 1 - |r|, tal como la usa removeCollinearity()."""
    d = 1.0 - np.abs(np.asarray(cor_mat, dtype=np.float64))
    np.fill_diagonal(d, 0.0)
    return d


def linkage_complete(dist_mat):
    """
    Clasificación jerárquica ascendente con enlace completo, equivalente a
    `stats::hclust(d, method = "complete")` de R.

    Retorna una matriz de enlace en formato scipy, de tamaño (n-1, 4):
        [id_cluster_a, id_cluster_b, altura_de_fusion, n_observaciones]

    Los IDs de 0 a n-1 son las observaciones originales; el i-ésimo
    cluster creado recibe el ID n+i.
    """
    dist_mat = np.array(dist_mat, dtype=np.float64, copy=True)
    n = dist_mat.shape[0]
    if n < 2:
        return np.empty((0, 4), dtype=np.float64)

    # Estado activo de cada cluster
    active = np.ones(n, dtype=bool)
    cluster_ids = np.arange(n, dtype=np.int64)
    sizes = np.ones(n, dtype=np.int64)

    d = dist_mat.copy()
    np.fill_diagonal(d, np.inf)

    linkage = np.zeros((n - 1, 4), dtype=np.float64)

    for step in range(n - 1):
        # Buscar el par activo más cercano
        masked = np.where(
            active[:, None] & active[None, :], d, np.inf
        )
        idx = np.argmin(masked)
        i, j = np.unravel_index(idx, masked.shape)
        if i > j:
            i, j = j, i
        height = masked[i, j]

        linkage[step] = [cluster_ids[i], cluster_ids[j], height, sizes[i] + sizes[j]]

        # Enlace completo: la distancia al nuevo cluster es el MÁXIMO
        new_row = np.maximum(d[i, :], d[j, :])
        new_row[i] = np.inf
        new_row[j] = np.inf

        d[i, :] = new_row
        d[:, i] = new_row
        d[i, i] = np.inf

        active[j] = False
        sizes[i] = sizes[i] + sizes[j]
        cluster_ids[i] = n + step

    return linkage


def cutree_height(linkage, n_obs, height):
    """
    Corta el dendrograma a una altura dada, equivalente a
    `stats::cutree(hc, h = height)` de R.

    Retorna un arreglo de enteros de tamaño n_obs con la etiqueta de grupo
    (base 1) de cada observación. La numeración sigue la convención de R:
    los grupos se numeran según el orden de aparición de las observaciones.
    """
    parent = np.arange(n_obs + len(linkage), dtype=np.int64)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for step, (a, b, h, _size) in enumerate(linkage):
        if h > height:
            break
        ra, rb = find(int(a)), find(int(b))
        new_id = n_obs + step
        parent[ra] = new_id
        parent[rb] = new_id
        parent[new_id] = new_id

    roots = np.array([find(i) for i in range(n_obs)])

    # Renumerar en orden de aparición (como R)
    labels = np.zeros(n_obs, dtype=np.int64)
    mapping = {}
    next_label = 1
    for i, r in enumerate(roots):
        if r not in mapping:
            mapping[r] = next_label
            next_label += 1
        labels[i] = mapping[r]

    return labels


# ---------------------------------------------------------------------------
# 3. Selección del representante de cada grupo
# ---------------------------------------------------------------------------

def _natural_key(name):
    """
    Clave de orden natural: 'bio4' va antes que 'bio10'.
    Con nombres tipo bioclimático el orden alfabético puro es engañoso
    (ordena bio1, bio10, bio11, bio2...), así que se separan los tramos
    numéricos para compararlos como números.
    """
    import re
    parts = re.split(r"(\d+)", str(name))
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def select_representatives(var_names, groups, cor_mat,
                            strategy="menor_correlacion_media", seed=42):
    """
    Elige una variable por grupo colineal.

    Estrategias
    -----------
    "menor_correlacion_media" (por defecto)
        Dentro de cada grupo se elige la variable con menor correlación
        absoluta media frente a las variables de los OTROS grupos. Es
        determinista y reproducible, y tiende a conservar la variable menos
        redundante respecto del resto del conjunto.

    "primera_alfabetica"
        La primera del grupo en orden alfabético. Determinista y trivial de
        auditar, útil cuando se quiere un criterio neutral.

    "aleatoria"
        Una al azar, replicando exactamente lo que hace
        `virtualspecies::removeCollinearity()`. Se respeta la semilla para
        que el resultado sea reproducible entre corridas.

    Retorna (seleccionadas, grupos_dict) donde grupos_dict mapea
    nº de grupo -> lista de nombres de variables de ese grupo.
    """
    if strategy not in SELECTION_STRATEGIES:
        raise ValueError(f"Estrategia inválida: {strategy}. Use una de {SELECTION_STRATEGIES}.")

    var_names = list(var_names)
    groups = np.asarray(groups)
    cor_abs = np.abs(np.asarray(cor_mat, dtype=np.float64))

    grupos_dict = {}
    for idx, g in enumerate(groups):
        grupos_dict.setdefault(int(g), []).append(var_names[idx])

    rng = np.random.default_rng(seed)
    seleccionadas = []

    for g in sorted(grupos_dict.keys()):
        miembros = grupos_dict[g]
        if len(miembros) == 1:
            seleccionadas.append(miembros[0])
            continue

        if strategy == "primera_alfabetica":
            seleccionadas.append(sorted(miembros, key=_natural_key)[0])
        elif strategy == "aleatoria":
            ordenados = sorted(miembros, key=_natural_key)
            seleccionadas.append(ordenados[int(rng.integers(len(ordenados)))])
        else:  # menor_correlacion_media
            idx_miembros = [var_names.index(m) for m in miembros]
            idx_externos = [i for i in range(len(var_names)) if groups[i] != g]

            if not idx_externos:
                # Un solo grupo en todo el conjunto: usar la correlación
                # media dentro del propio grupo como criterio de centralidad.
                medias = [
                    np.mean([cor_abs[i, k] for k in idx_miembros if k != i])
                    for i in idx_miembros
                ]
            else:
                medias = [
                    np.mean([cor_abs[i, k] for k in idx_externos])
                    for i in idx_miembros
                ]
            mejor = int(np.argmin(medias))
            seleccionadas.append(miembros[mejor])

    return seleccionadas, grupos_dict


# ---------------------------------------------------------------------------
# 4. Función de alto nivel
# ---------------------------------------------------------------------------

def remove_collinearity(data, var_names, cutoff=0.8, method="pearson",
                        strategy="menor_correlacion_media", seed=42):
    """
    Equivalente en Python de `virtualspecies::removeCollinearity()`.

    Parámetros
    ----------
    data : ndarray (n_obs, n_vars) — valores muestreados de los rásters.
    var_names : lista de nombres de las variables (columnas de `data`).
    cutoff : umbral de correlación por encima del cual se agrupan variables.
    method : "pearson" | "spearman" | "kendall".
    strategy : criterio de elección del representante de cada grupo.
    seed : semilla para la estrategia "aleatoria".

    Retorna un dict con la matriz de correlación, la matriz de enlace, las
    etiquetas de grupo, los grupos y las variables seleccionadas.
    """
    if not (0 < cutoff < 1):
        raise ValueError("El umbral de correlación (cutoff) debe estar entre 0 y 1 (exclusivo).")

    cor_mat = correlation_matrix(data, method=method)
    dist = distance_matrix(cor_mat)
    linkage = linkage_complete(dist)
    groups = cutree_height(linkage, len(var_names), height=1.0 - cutoff)
    seleccionadas, grupos_dict = select_representatives(
        var_names, groups, cor_mat, strategy=strategy, seed=seed
    )

    return {
        "cor_matrix": cor_mat,
        "linkage": linkage,
        "groups": groups,
        "groups_dict": grupos_dict,
        "selected": seleccionadas,
        "var_names": list(var_names),
        "cutoff": cutoff,
        "method": method,
        "strategy": strategy,
    }
