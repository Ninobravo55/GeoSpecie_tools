"""
charts.py — Gráficos de barras horizontales para contribución de
variables e importancia por permutación. Puro matplotlib (sin qgis).
"""

import matplotlib
matplotlib.use("Agg")  # sin display -- corre dentro del proceso de QGIS, no debe abrir ventanas
import matplotlib.pyplot as plt

AZUL = "#1E73B8"
VERDE = "#8CC63F"
NEGRO = "#404040"

_STYLE = {
    "font.family": "DejaVu Sans", "font.size": 11,
    "axes.titlesize": 18, "axes.labelsize": 13,
    "xtick.labelsize": 11, "ytick.labelsize": 11,
    "figure.facecolor": "white", "axes.facecolor": "white",
    "axes.edgecolor": "#D9D9D9", "axes.linewidth": 1.2,
    "grid.color": "#E8E8E8", "grid.linestyle": "--", "grid.linewidth": 0.8,
    "legend.frameon": False,
}


def _horizontal_bar_chart(labels, values, title, xlabel, ylabel, highlight_color, base_color, output_path):
    with plt.rc_context(_STYLE):
        fig, ax = plt.subplots(figsize=(10, max(4, 0.4 * len(labels) + 2)))

        colors = [base_color] * len(labels)
        if colors:
            colors[0] = highlight_color

        ax.barh(labels, values, color=colors, edgecolor="white", linewidth=0.8)
        ax.invert_yaxis()
        ax.set_xlabel(xlabel, fontweight="bold")
        ax.set_ylabel(ylabel, fontweight="bold")
        ax.set_title(title, fontsize=18, fontweight="bold", color=NEGRO)
        ax.grid(axis="x")
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        for i, v in enumerate(values):
            ax.text(v + 0.25, i, f"{v:.1f}%", va="center", fontsize=10, fontweight="bold", color=NEGRO)

        plt.tight_layout()
        fig.savefig(output_path, dpi=600, bbox_inches="tight")
        plt.close(fig)

    return output_path


def plot_variable_contribution(df, output_path):
    """df: salida de reader.parse_variable_contribution_table()."""
    return _horizontal_bar_chart(
        labels=list(df["Variable"]), values=list(df["Porcentaje contribucion"]),
        title="Contribución de Variables Ambientales",
        xlabel="Contribución (%)", ylabel="Variables",
        highlight_color=VERDE, base_color=AZUL, output_path=output_path,
    )


def plot_permutation_importance(df, output_path):
    """
    df: salida de reader.parse_variable_contribution_table(), pero
    reordenada por 'Importancia contribucion' -- a diferencia del script
    original, que reutilizaba el orden de contribución de variables
    (ordenado por % contribución) para el gráfico de PERMUTACIÓN, lo
    cual no es lo mismo (una variable puede contribuir poco pero tener
    alta importancia por permutación, o viceversa).
    """
    df_sorted = df.sort_values(by="Importancia contribucion", ascending=False).reset_index(drop=True)
    return _horizontal_bar_chart(
        labels=list(df_sorted["Variable"]), values=list(df_sorted["Importancia contribucion"]),
        title="Importancia por Permutación",
        xlabel="Importancia (%)", ylabel="Variables",
        highlight_color=AZUL, base_color=VERDE, output_path=output_path,
    )
