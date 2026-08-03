"""
charts.py — Generación de gráficos para el análisis de Random Forest:
1. Gráfico de Curva ROC (AUC).
2. Gráfico de Importancia de Variables Ambientales.
"""

import os
import matplotlib
matplotlib.use("Agg")  # Operación sin interfaz gráfica en el hilo de QGIS
import matplotlib.pyplot as plt
import numpy as np

VERDE_GEOSPECIES = "#2E7D32"
AZUL_GEOSPECIES = "#1E73B8"
NEGRO_TEXTO = "#333333"

_STYLE = {
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "axes.titlesize": 14,
    "axes.labelsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.edgecolor": "#CCCCCC",
    "axes.linewidth": 1.0,
    "grid.color": "#E0E0E0",
    "grid.linestyle": "--",
    "grid.linewidth": 0.7,
    "legend.frameon": True,
    "legend.facecolor": "white",
    "legend.edgecolor": "#CCCCCC",
}


def plot_roc_curve(fpr, tpr, auc_score, output_path):
    """
    Genera el gráfico de la Curva ROC (Receiver Operating Characteristic) y guarda el archivo PNG.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with plt.rc_context(_STYLE):
        fig, ax = plt.subplots(figsize=(7, 6))

        # Curva ROC
        ax.plot(fpr, tpr, color=VERDE_GEOSPECIES, lw=2.5, label=f"Random Forest (AUC = {auc_score:.4f})")
        # Relleno bajo la curva
        ax.fill_between(fpr, tpr, alpha=0.15, color=VERDE_GEOSPECIES)
        # Línea de referencia diagonal (Azar)
        ax.plot([0, 1], [0, 1], color="#888888", lw=1.5, linestyle="--", label="Clasificador aleatorio (AUC = 0.50)")

        ax.set_xlim([-0.02, 1.02])
        ax.set_ylim([-0.02, 1.02])
        ax.set_xlabel("Tasa de Falsos Positivos (1 - Especificidad)", fontweight="bold")
        ax.set_ylabel("Tasa de Verdaderos Positivos (Sensibilidad)", fontweight="bold")
        ax.set_title("Curva ROC — Evaluación de Validación", fontweight="bold", color=NEGRO_TEXTO)
        ax.grid(True)
        ax.legend(loc="lower right")

        plt.tight_layout()
        fig.savefig(output_path, dpi=300, bbox_inches="tight")
        plt.close(fig)

    return output_path


def plot_feature_importance(var_importances, output_path):
    """
    Genera un gráfico de barras horizontales con la importancia de las variables.
    var_importances: list[tuple(str, float)] -> [(var_name, percentage), ...]
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if not var_importances:
        return None

    # Invertir para que la variable más importante aparezca arriba
    vars_reversed = list(reversed(var_importances))
    labels = [pair[0] for pair in vars_reversed]
    values = [pair[1] for pair in vars_reversed]

    with plt.rc_context(_STYLE):
        fig, ax = plt.subplots(figsize=(8, max(4, 0.4 * len(labels) + 1.5)))

        colors = [AZUL_GEOSPECIES] * len(labels)
        if colors:
            colors[-1] = VERDE_GEOSPECIES  # Destacar la primera (más alta)

        ax.barh(labels, values, color=colors, edgecolor="white", linewidth=0.8)
        ax.set_xlabel("Importancia Relativa (%)", fontweight="bold")
        ax.set_ylabel("Variables Ambientales", fontweight="bold")
        ax.set_title("Importancia de Variables — Random Forest", fontweight="bold", color=NEGRO_TEXTO)
        ax.grid(axis="x")
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        max_val = max(values) if values else 100
        for i, v in enumerate(values):
            ax.text(v + (max_val * 0.01), i, f"{v:.1f}%", va="center", fontsize=9, color=NEGRO_TEXTO)

        ax.set_xlim(0, max_val * 1.15)

        plt.tight_layout()
        fig.savefig(output_path, dpi=300, bbox_inches="tight")
        plt.close(fig)

    return output_path
