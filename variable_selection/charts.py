"""
charts.py — Gráficos profesionales y elegantes de "Selección de Variables".

Dibuja el dendrograma directamente a partir de la matriz de enlace generada
por `stats_core.linkage_complete()`, sin dependencias externas como scipy.

Gráficos generados:
1. Dendrograma de la clasificación jerárquica:
   - Estilo científico pulido con fondo blanco puro.
   - Ramas coloreadas por grupo colineal con paleta vibrante de alto contraste.
   - Tronco superior en color grafito oscuro / negro.
   - Línea de corte punteada con badge/etiqueta descriptiva.
   - Hojas con rotación de 90° e indicación destacada de variables seleccionadas.
   - Eje Y con escala 'Height' y correlación equivalente |r|.

2. Mapa de calor de la matriz de correlación:
   - Paleta divergente estilo 'coolwarm' (Azul <-> Blanco <-> Rojo).
   - Rejilla con celdas cuadradas y separadores nítidos blancos.
   - Valores numéricos centrados en cada celda con contraste de color adaptativo.
   - Barra de color elegante y proporcional.
   - Ordenamiento por dendrograma para visualizar clústeres colineales.
"""

import logging

import matplotlib
matplotlib.use("Agg")  # backend sin ventana: obligatorio dentro de QGIS
import matplotlib.pyplot as plt
import numpy as np

logger = logging.getLogger(__name__)

# Paleta profesional de alta distinción para los clústeres (estilo Nature / Seaborn / Tableau)
GROUP_COLORS = [
    "#2563EB",  # Azul vibrante (Blue 600)
    "#059669",  # Verde esmeralda (Emerald 600)
    "#D97706",  # Ámbar / Naranja oscuro (Amber 600)
    "#DC2626",  # Rojo carmesí (Red 600)
    "#7C3AED",  # Violeta / Púrpura (Violet 600)
    "#0891B2",  # Cian / Teal (Cyan 600)
    "#DB2777",  # Rosa magenta (Pink 600)
    "#4F46E5",  # Índigo profundo (Indigo 600)
    "#65A30D",  # Verde lima (Lime 600)
    "#EA580C",  # Naranja fuego (Orange 600)
    "#0D9488",  # Verde azulado (Teal 600)
    "#9333EA",  # Púrpura brillante (Purple 600)
]
BASE_BRANCH_COLOR = "#1E293B"  # Grafito oscuro / Slate para las ramas superiores


# ---------------------------------------------------------------------------
# Orden de las hojas
# ---------------------------------------------------------------------------

def leaf_order(linkage, n_obs):
    """
    Orden de las hojas del dendrograma, recorriendo el árbol de arriba abajo.
    Equivale a `hc$order` de R, salvo por reflejos locales de ramas que no
    alteran la estructura del árbol.
    """
    if len(linkage) == 0:
        return list(range(n_obs))

    children = {}
    for step, (a, b, _h, _n) in enumerate(linkage):
        children[n_obs + step] = (int(a), int(b))

    root = n_obs + len(linkage) - 1
    order, stack = [], [root]
    while stack:
        node = stack.pop()
        if node < n_obs:
            order.append(node)
        else:
            a, b = children[node]
            stack.append(b)
            stack.append(a)
    return order


def _members_of(linkage, n_obs, node):
    """Hojas contenidas bajo un nodo del dendrograma."""
    if node < n_obs:
        return [node]
    stack, out = [node], []
    while stack:
        nd = stack.pop()
        if nd < n_obs:
            out.append(nd)
        else:
            a, b, _h, _n = linkage[nd - n_obs]
            stack.append(int(a))
            stack.append(int(b))
    return out


# ---------------------------------------------------------------------------
# Dendrograma Elegante y de Alta Claridad
# ---------------------------------------------------------------------------

# Paleta profesional de alto contraste y viveza para los clústeres
CLUSTER_PALETTE = [
    "#2563EB",  # Azul eléctrico
    "#059669",  # Verde esmeralda
    "#D97706",  # Ámbar intenso
    "#DC2626",  # Rojo carmesí
    "#7C3AED",  # Violeta brillante
    "#0891B2",  # Cian / Océano
    "#DB2777",  # Rosa magenta
    "#4F46E5",  # Índigo real
    "#65A30D",  # Verde lima
    "#EA580C",  # Naranja fuego
    "#0D9488",  # Teal profundo
    "#9333EA",  # Púrpura eléctrico
    "#C026D3",  # Fucsia
    "#0284C7",  # Azul cielo
    "#16A34A",  # Verde bosque
    "#CA8A04",  # Mostaza
]


def plot_dendrogram(linkage, var_names, groups, cutoff, method, output_path,
                    selected=None):
    """
    Dibuja y guarda un dendrograma de alta claridad y resolución en `output_path` (PNG).
    Utiliza una escala Y expandida (raíz cuadrada / potencia) para que las fusiones
    con alta colinealidad (|r| > 0.90, distancias h < 0.10) se distingan perfectamente
    y no queden aplastadas contra la base.
    """
    n_obs = len(var_names)
    selected_set = set(selected or [])

    if len(linkage) == 0:
        raise ValueError("La matriz de enlace está vacía; se requieren al menos 2 variables.")

    order = leaf_order(linkage, n_obs)
    # Espaciado horizontal amplio y generoso entre variables
    leaf_x = {leaf: i * 20.0 + 10.0 for i, leaf in enumerate(order)}

    groups = np.asarray(groups)
    
    # Asignar a CADA grupo su propio color distintivo vibrante
    unique_groups = sorted(list(set(int(g) for g in groups)))
    group_of_color = {}
    for idx, g in enumerate(unique_groups):
        group_of_color[g] = CLUSTER_PALETTE[idx % len(CLUSTER_PALETTE)]

    cut_height = 1.0 - cutoff

    # Función de transformación Y (raíz cuadrada exacta) para dar máxima amplitud
    # y separación vertical a las agrupaciones iniciales y fusiones de alta correlación
    def transform_y(h_val):
        h_val = np.clip(h_val, 0.0, 1.0)
        return float(h_val ** 0.50)

    cut_y_disp = transform_y(cut_height)

    # ── Dimensiones del Lienzo Ampliado (Altura 13.5 in y ancho generoso) ──
    fig_w = max(16.0, n_obs * 0.95)
    fig_h = 13.5
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=220, facecolor="#FFFFFF")
    ax.set_facecolor("#FFFFFF")

    node_x = {}
    node_y = {}
    node_color = {}

    # ── Posiciones y marcadores de las variables (hojas) ──────────────
    for i in range(n_obs):
        node_x[i] = leaf_x[i]
        node_y[i] = 0.0
        g = int(groups[i])
        c = group_of_color.get(g, "#2563EB")
        node_color[i] = c
        
        # Marcador circular visible en la base de cada variable
        ax.scatter([leaf_x[i]], [0.0], color=c, s=65, zorder=6, edgecolors="#FFFFFF", linewidths=1.5)

    # ── Trazado de ramas jerárquicas con escala expandida ─────────────
    for step, (a, b, h, _n) in enumerate(linkage):
        a, b, h = int(a), int(b), float(h)
        xa, xb = node_x[a], node_x[b]
        ya, yb = node_y[a], node_y[b]
        ca, cb = node_color[a], node_color[b]

        mid_x = (xa + xb) / 2.0
        new_id = n_obs + step
        y_disp = transform_y(h)
        node_x[new_id] = mid_x
        node_y[new_id] = y_disp

        if h <= cut_height and ca == cb and ca != BASE_BRANCH_COLOR:
            # Fusión dentro del mismo clúster colineal
            c_merge = ca
            node_color[new_id] = c_merge
            ax.plot([xa, xa, xb, xb], [ya, y_disp, y_disp, yb], color=c_merge, lw=3.0,
                    solid_joinstyle="miter", solid_capstyle="round", zorder=3)
            # Nodo de unión destacado
            ax.scatter([mid_x], [y_disp], color=c_merge, s=36, zorder=5, edgecolors="#FFFFFF", linewidths=0.8)
        else:
            # Fusión entre diferentes clústeres (por encima del umbral de corte)
            node_color[new_id] = BASE_BRANCH_COLOR
            # Rama izquierda con el color de su subárbol
            ax.plot([xa, xa, mid_x], [ya, y_disp, y_disp], color=ca, lw=2.4,
                    solid_joinstyle="miter", solid_capstyle="round", zorder=3)
            # Rama derecha con el color de su subárbol
            ax.plot([xb, xb, mid_x], [yb, y_disp, y_disp], color=cb, lw=2.4,
                    solid_joinstyle="miter", solid_capstyle="round", zorder=3)
            # Conector en la unión alta
            ax.scatter([mid_x], [y_disp], color=BASE_BRANCH_COLOR, s=36, zorder=5)

    # ── Línea de corte de colinealidad ────────────────────────────────
    ax.axhline(cut_y_disp, color="#DC2626", ls="--", lw=2.4, alpha=0.9, zorder=4)

    # Badge descriptivo del umbral
    x_max = n_obs * 20.0
    ax.text(
        x_max - 2.0, cut_y_disp,
        f"  Umbral de corte: |r| = {cutoff:.2f}  (h = {cut_height:.2f})  ",
        color="#991B1B", fontsize=11.5, va="bottom", ha="right",
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#FEF2F2", edgecolor="#DC2626", lw=1.5, alpha=0.97),
        zorder=7,
    )

    # ── Etiquetas de las variables (hojas) ─────────────────────────────
    ax.set_xticks([leaf_x[i] for i in order])
    labels = []
    for i in order:
        nm = var_names[i]
        labels.append(f"✔  {nm}" if nm in selected_set else f"   {nm}")
    
    font_size_leaves = max(10.0, min(12.5, 260.0 / n_obs))
    ax.tick_params(axis="x", which="both", pad=12)
    ax.set_xticklabels(labels, rotation=90, fontsize=font_size_leaves, ha="right", va="top")

    # Colorear cada etiqueta con el color exacto de su clúster
    for tick, i in zip(ax.get_xticklabels(), order):
        nm = var_names[i]
        g = int(groups[i])
        c = group_of_color.get(g, "#1E293B")
        if nm in selected_set:
            tick.set_fontweight("bold")
            tick.set_color(c)
        else:
            tick.set_fontweight("normal")
            tick.set_color("#64748B")

    # ── Eje Y con escala ampliada de detalle en correlaciones altas ───
    ax.set_ylabel("Height (Distancia 1 - |r|)  [Escala Expandida de Detalle]", fontsize=13, fontweight="bold", color="#0F172A", labelpad=14)
    
    # Altura máxima visual
    max_h = float(linkage[:, 2].max())
    max_y_disp = transform_y(min(1.0, max_h * 1.05))
    ax.set_ylim(-0.025, max(1.02, max_y_disp))

    # Ticks estratégicos que detallan los niveles críticos
    h_ticks_raw = [0.0, 0.02, 0.05, 0.10, 0.20, 0.40, 0.60, 0.80, 1.0]
    h_ticks_filtered = [h for h in h_ticks_raw if h <= max_h * 1.1 or h <= 1.0]
    y_tick_positions = [transform_y(h) for h in h_ticks_filtered]
    
    y_tick_labels = []
    for h in h_ticks_filtered:
        r_val = 1.0 - h
        if h in (0.0, 0.02, 0.05, 0.10, 0.20):
            y_tick_labels.append(f"{h:.2f} (|r|={r_val:.2f})")
        else:
            y_tick_labels.append(f"{h:.2f}")

    ax.set_yticks(y_tick_positions)
    ax.set_yticklabels(y_tick_labels, fontsize=11, color="#334155", fontweight="bold")

    # ── Estilos de ejes y fondo ───────────────────────────────────────
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color("#94A3B8")
    ax.spines["bottom"].set_linewidth(1.2)
    ax.spines["left"].set_color("#94A3B8")
    ax.spines["left"].set_linewidth(1.2)
    ax.tick_params(axis="both", which="both", colors="#64748B", length=5, width=1.1)
    ax.grid(axis="y", color="#F1F5F9", ls="-", lw=1.2, zorder=1)

    n_grupos = len(unique_groups)
    n_sel = len(selected_set) if selected_set else n_grupos
    ax.set_title(
        f"Dendrograma de Clasificación Jerárquica — Método {method.capitalize()}\n"
        f"{n_obs} Variables Analizadas  •  {n_grupos} Grupos Formados  •  {n_sel} Variables Seleccionadas (✔)",
        fontsize=14, fontweight="bold", color="#0F172A", pad=22,
    )

    fig.tight_layout()
    fig.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="#FFFFFF")
    plt.close(fig)

    return output_path


# ---------------------------------------------------------------------------
# Mapa de calor de correlación Elegante
# ---------------------------------------------------------------------------

def plot_correlation_heatmap(cor_mat, var_names, cutoff, method, output_path,
                             linkage=None):
    """
    Mapa de calor de la matriz de correlación estilo profesional de publicación.
    Paleta divergente coolwarm con celdas cuadradas, separadores nítidos blancos
    y valores numéricos legibles en cada casilla.
    """
    cor_mat = np.asarray(cor_mat, dtype=np.float64)
    n = len(var_names)

    if linkage is not None and len(linkage) > 0:
        order = leaf_order(linkage, n)
    else:
        order = list(range(n))

    mat = cor_mat[np.ix_(order, order)]
    labels = [var_names[i] for i in order]

    # Tamaño dinámico garantizando celdas cuadradas y legibilidad óptima
    cell_size = max(0.55, min(0.9, 11.0 / n))
    fig_dim = max(7.0, n * cell_size + 2.2)
    fig, ax = plt.subplots(figsize=(fig_dim, fig_dim), dpi=200, facecolor="#FFFFFF")
    ax.set_facecolor("#FFFFFF")

    # Colormap divergente suave y vibrante estilo coolwarm / RdBu_r
    cmap = plt.get_cmap("coolwarm")

    im = ax.imshow(mat, cmap=cmap, vmin=-1.0, vmax=1.0, aspect="equal", interpolation="nearest")

    # Ticks y etiquetas de variables
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))

    font_size_labels = max(7.5, min(10.5, 120.0 / n))
    ax.set_xticklabels(labels, rotation=90, fontsize=font_size_labels, fontweight="bold", color="#1E293B", ha="center")
    ax.set_yticklabels(labels, fontsize=font_size_labels, fontweight="bold", color="#1E293B")

    # Rejilla blanca limpia separando cada celda
    ax.set_xticks(np.arange(n + 1) - 0.5, minor=True)
    ax.set_yticks(np.arange(n + 1) - 0.5, minor=True)
    ax.grid(which="minor", color="#FFFFFF", linestyle="-", linewidth=1.5)
    ax.tick_params(which="minor", bottom=False, left=False)

    # Anotar valores numéricos en todas las celdas
    font_size_nums = max(6.0, min(10.0, 115.0 / n))
    for i in range(n):
        for j in range(n):
            v = mat[i, j]
            # Color adaptativo para máxima legibilidad
            # Celdas oscuras (valores cercanos a -1 o 1) llevan texto blanco puro
            # Celdas claras (valores cercanos a 0) llevan texto grafito oscuro
            text_color = "#FFFFFF" if abs(v) >= 0.42 else "#0F172A"

            # Formato de dos decimales con signo
            txt = f"{v:.2f}"

            is_collinear = (i != j and abs(v) >= cutoff)
            fw = "bold" if (is_collinear or i == j) else "normal"

            ax.text(
                j, i, txt, ha="center", va="center",
                fontsize=font_size_nums, color=text_color,
                fontweight=fw,
            )

    # Barra de color profesional y estilizada
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, shrink=0.92)
    cbar.outline.set_edgecolor("#CBD5E1")
    cbar.outline.set_linewidth(0.8)
    cbar_ticks = [-1.0, -0.8, -0.6, -0.4, -0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    cbar.set_ticks(cbar_ticks)
    cbar.set_ticklabels([f"{t:.1f}" for t in cbar_ticks])
    cbar.ax.tick_params(labelsize=8.5, colors="#334155", length=3)
    cbar.set_label("Coeficiente de Correlación", fontsize=10, fontweight="bold", color="#1E293B", labelpad=10)

    # Quitar bordes externos de los ejes
    for spine in ax.spines.values():
        spine.set_visible(False)

    ax.set_title(
        f"Matriz de Correlación de Variables — Método {method.capitalize()}\n"
        f"Valores con |r| ≥ {cutoff:.2f} indican colinealidad crítica",
        fontsize=13, fontweight="bold", color="#0F172A", pad=18,
    )

    fig.tight_layout()
    fig.savefig(output_path, dpi=200, bbox_inches="tight", facecolor="#FFFFFF")
    plt.close(fig)

    return output_path

