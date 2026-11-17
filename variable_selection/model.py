"""
model.py — Orquestador de la herramienta "Selección de Variables".

Encadena el flujo completo, sin dependencias de Qt ni de qgis.core, para
poder ejecutarse igual desde el diálogo interactivo, desde un algoritmo de
Processing o desde un script:

    directorio .asc
        → raster_io.load_stack()          (GDAL)
        → raster_io.build_sample_matrix() (muestreo de píxeles válidos)
        → stats_core.remove_collinearity()(correlación + cluster + selección)
        → charts.*                        (dendrograma + mapa de calor)
        → tablas CSV
        → raster_io.export_selected()     (carpeta Select_variable)
"""

import csv
import logging
import os

import numpy as np

from . import charts, raster_io, stats_core
from .raster_io import RasterIOError

logger = logging.getLogger(__name__)

METHODS = stats_core.METHODS
SELECTION_STRATEGIES = stats_core.SELECTION_STRATEGIES


class VariableSelectionError(Exception):
    """Error de alto nivel de la herramienta de Selección de Variables."""


def _write_correlation_csv(path, cor_mat, var_names):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([""] + list(var_names))
        for i, name in enumerate(var_names):
            w.writerow([name] + [f"{v:.6f}" for v in cor_mat[i]])


def _write_clusters_csv(path, var_names, groups, selected, cor_mat):
    """
    Tabla de clusters con, además del grupo y el estado, la correlación
    absoluta máxima de cada variable frente al resto de su grupo. Ese
    número es lo que justifica por qué la variable fue descartada, y sin él
    la tabla obliga a volver a la matriz completa para entenderla.
    """
    selected_set = set(selected)
    groups = np.asarray(groups)
    cor_abs = np.abs(cor_mat)

    rows = []
    for i, name in enumerate(var_names):
        g = int(groups[i])
        compañeros = [j for j in range(len(var_names)) if int(groups[j]) == g and j != i]
        max_r = max((cor_abs[i, j] for j in compañeros), default=0.0)
        rows.append({
            "variable": name,
            "grupo": g,
            "n_variables_grupo": int((groups == g).sum()),
            "max_r_dentro_grupo": round(float(max_r), 6),
            "seleccionada": "SI" if name in selected_set else "NO",
        })

    rows.sort(key=lambda r: (r["grupo"], stats_core._natural_key(r["variable"])))

    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "variable", "grupo", "n_variables_grupo", "max_r_dentro_grupo", "seleccionada"
        ])
        w.writeheader()
        w.writerows(rows)

    return rows


def _write_selected_csv(path, selected):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["variable_seleccionada"])
        for name in selected:
            w.writerow([name])


def run_variable_selection(input_dir, output_dir, cutoff=0.8, method="pearson",
                           strategy="menor_correlacion_media",
                           sample_points=True, nb_points=10000, seed=42,
                           make_charts=True, progress_cb=None):
    """
    Ejecuta el flujo completo de selección de variables.

    Parámetros
    ----------
    input_dir : carpeta con los rásters ASCII (.asc).
    output_dir : carpeta de salida; dentro se crea 'Select_variable'.
    cutoff : umbral de correlación (0-1, exclusivo). Por defecto 0.8.
    method : "pearson" | "spearman" | "kendall".
    strategy : criterio de elección del representante de cada grupo.
    sample_points : submuestrear píxeles en vez de usar todos.
    nb_points : tamaño de la submuestra.
    seed : semilla del muestreo y de la estrategia aleatoria.
    make_charts : generar dendrograma y mapa de calor.
    progress_cb : callable(porcentaje:int, mensaje:str) opcional.

    Retorna un dict con los resultados y las rutas generadas.
    """
    def report(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)

    if method not in METHODS:
        raise VariableSelectionError(
            f"Método de correlación inválido: '{method}'. Use pearson, spearman o kendall."
        )
    if strategy not in SELECTION_STRATEGIES:
        raise VariableSelectionError(f"Estrategia de selección inválida: '{strategy}'.")
    if not (0 < cutoff < 1):
        raise VariableSelectionError(
            f"El umbral de correlación debe estar entre 0 y 1 (exclusivo). Recibido: {cutoff}"
        )

    os.makedirs(output_dir, exist_ok=True)

    # ── 1. Carga de rásters ────────────────────────────────────────────
    report(5, "Leyendo rásters ASCII...")
    try:
        stack = raster_io.load_stack(input_dir, progress_cb=lambda m: report(10, m))
    except RasterIOError as err:
        raise VariableSelectionError(str(err))

    var_names = stack["names"]
    n_vars = len(var_names)

    # ── 2. Muestreo ────────────────────────────────────────────────────
    report(30, f"Muestreando píxeles válidos de {n_vars} variables...")
    try:
        matrix, n_obs = raster_io.build_sample_matrix(
            stack, sample_points=sample_points, nb_points=nb_points, seed=seed
        )
    except RasterIOError as err:
        raise VariableSelectionError(str(err))

    # ── 3. Colinealidad ────────────────────────────────────────────────
    if method == "kendall" and n_obs > 20000:
        report(40, f"Calculando correlación de Kendall sobre {n_obs} puntos (puede tardar)...")
    else:
        report(40, f"Calculando correlación de {method} sobre {n_obs} puntos...")

    analysis = stats_core.remove_collinearity(
        matrix, var_names, cutoff=cutoff, method=method, strategy=strategy, seed=seed
    )

    selected = analysis["selected"]
    cor_mat = analysis["cor_matrix"]
    groups = analysis["groups"]
    linkage = analysis["linkage"]

    report(60, f"{n_vars} variables agrupadas en {len(analysis['groups_dict'])} clusters; "
               f"{len(selected)} seleccionadas.")

    # ── 4. Tablas ──────────────────────────────────────────────────────
    report(68, "Escribiendo tablas de resultados...")
    cor_csv = os.path.join(output_dir, "matriz_correlacion.csv")
    clusters_csv = os.path.join(output_dir, "tabla_clusters.csv")
    selected_csv = os.path.join(output_dir, "variables_seleccionadas.csv")

    _write_correlation_csv(cor_csv, cor_mat, var_names)
    cluster_rows = _write_clusters_csv(clusters_csv, var_names, groups, selected, cor_mat)
    _write_selected_csv(selected_csv, selected)

    # ── 5. Gráficos ────────────────────────────────────────────────────
    dendro_path = heatmap_path = None
    if make_charts:
        report(75, "Generando dendrograma y mapa de calor...")
        dendro_path = os.path.join(output_dir, "dendrograma_variables.png")
        heatmap_path = os.path.join(output_dir, "matriz_correlacion.png")
        try:
            charts.plot_dendrogram(
                linkage, var_names, groups, cutoff, method, dendro_path, selected=selected
            )
            charts.plot_correlation_heatmap(
                cor_mat, var_names, cutoff, method, heatmap_path, linkage=linkage
            )
        except Exception as err:
            # Un fallo de matplotlib no debe tirar abajo la exportación de
            # los rásters, que es el producto principal de la herramienta.
            logger.warning("No se pudieron generar los gráficos: %s", err)
            dendro_path = heatmap_path = None

    # ── 6. Exportación ASC ─────────────────────────────────────────────
    report(85, "Exportando variables seleccionadas a 'Select_variable'...")
    try:
        exported = raster_io.export_selected(
            stack, selected, output_dir, progress_cb=lambda m: report(90, m)
        )
    except RasterIOError as err:
        raise VariableSelectionError(str(err))

    report(100, f"Completado: {len(selected)} de {n_vars} variables exportadas.")

    return {
        "var_names": var_names,
        "n_vars": n_vars,
        "n_obs": n_obs,
        "selected": selected,
        "groups": groups.tolist() if hasattr(groups, "tolist") else list(groups),
        "groups_dict": analysis["groups_dict"],
        "cor_matrix": cor_mat,
        "linkage": linkage,
        "cluster_rows": cluster_rows,
        "cutoff": cutoff,
        "method": method,
        "strategy": strategy,
        "sample_points": sample_points,
        "nb_points": nb_points,
        "seed": seed,
        "output_dir": output_dir,
        "select_dir": os.path.join(output_dir, "Select_variable"),
        "exported_files": exported,
        "dendrogram_path": dendro_path,
        "heatmap_path": heatmap_path,
        "csv_correlation": cor_csv,
        "csv_clusters": clusters_csv,
        "csv_selected": selected_csv,
    }
