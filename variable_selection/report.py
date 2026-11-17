"""
report.py — Generador de reporte HTML para la herramienta "Selección de
Variables".

Mantiene la misma línea gráfica que el reporte de Random Forest del plugin
(mismos colores corporativos, tarjetas de métricas y estilos de tabla) para
que los productos de GeoSpecies Tools sean coherentes entre sí.
"""

import datetime
import os

STRATEGY_LABELS = {
    "menor_correlacion_media": "Menor correlación media (determinista)",
    "primera_alfabetica": "Primera en orden natural (determinista)",
    "aleatoria": "Aleatoria con semilla (equivalente a virtualspecies)",
}


def _cor_cell_style(value, cutoff, is_diagonal):
    if is_diagonal:
        return "background-color:#ECEFF1; color:#607D8B;"
    av = abs(value)
    if av >= cutoff:
        return "background-color:#FFCDD2; color:#B71C1C; font-weight:bold;"
    if av >= cutoff * 0.75:
        return "background-color:#FFECB3; color:#8D6E00;"
    return ""


def generate_html_report(results, output_dir):
    """
    Genera el reporte HTML a partir del dict devuelto por
    model.run_variable_selection(). Retorna la ruta del archivo escrito.
    """
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, "reporte_seleccion_variables.html")

    cutoff = results["cutoff"]
    method = results["method"]
    var_names = results["var_names"]
    selected = results["selected"]
    cor_mat = results["cor_matrix"]
    cluster_rows = results["cluster_rows"]
    groups_dict = results["groups_dict"]
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    n_grupos = len(groups_dict)
    n_descartadas = results["n_vars"] - len(selected)

    # ── Tabla de clusters ──────────────────────────────────────────────
    cluster_rows_html = ""
    for row in cluster_rows:
        if row["seleccionada"] == "SI":
            estado = '<span style="color:#1B5E20; font-weight:bold;">✔ Seleccionada</span>'
        else:
            estado = '<span style="color:#B71C1C;">✘ Descartada (colineal)</span>'
        max_r = row["max_r_dentro_grupo"]
        max_r_txt = "—" if row["n_variables_grupo"] == 1 else f"{max_r:.3f}"
        cluster_rows_html += f"""
        <tr>
            <td><code>{row['variable']}</code></td>
            <td style="text-align:center;">{row['grupo']}</td>
            <td style="text-align:center;">{row['n_variables_grupo']}</td>
            <td style="text-align:center;">{max_r_txt}</td>
            <td>{estado}</td>
        </tr>"""

    # ── Composición de grupos ──────────────────────────────────────────
    grupos_html = ""
    selected_set = set(selected)
    for g in sorted(groups_dict):
        miembros = groups_dict[g]
        chips = ""
        for m in miembros:
            if m in selected_set:
                chips += (f'<span style="background:#C8E6C9; color:#1B5E20; padding:2px 8px; '
                          f'border-radius:10px; margin-right:5px; font-weight:bold; '
                          f'display:inline-block; margin-bottom:4px;">{m} ✔</span>')
            else:
                chips += (f'<span style="background:#ECEFF1; color:#546E7A; padding:2px 8px; '
                          f'border-radius:10px; margin-right:5px; display:inline-block; '
                          f'margin-bottom:4px;">{m}</span>')
        etiqueta = "colineal" if len(miembros) > 1 else "independiente"
        grupos_html += f"""
        <tr>
            <td style="text-align:center;"><b>{g}</b></td>
            <td style="text-align:center;">{len(miembros)} ({etiqueta})</td>
            <td>{chips}</td>
        </tr>"""

    # ── Matriz de correlación ──────────────────────────────────────────
    cor_header_html = "".join(f"<th>{h}</th>" for h in var_names)
    cor_rows_html = ""
    for i, name in enumerate(var_names):
        cells = "".join(
            f'<td style="{_cor_cell_style(cor_mat[i][j], cutoff, i == j)}">{cor_mat[i][j]:.2f}</td>'
            for j in range(len(var_names))
        )
        cor_rows_html += f"<tr><th>{name}</th>{cells}</tr>"

    # ── Gráficos ───────────────────────────────────────────────────────
    charts_html = ""
    if results.get("dendrogram_path"):
        charts_html += (
            '<div class="chart-box"><h4>Dendrograma de colinealidad</h4>'
            '<img src="dendrograma_variables.png" alt="Dendrograma"></div>'
        )
    if results.get("heatmap_path"):
        charts_html += (
            '<div class="chart-box"><h4>Mapa de calor de correlación</h4>'
            '<img src="matriz_correlacion.png" alt="Matriz de correlación"></div>'
        )
    if not charts_html:
        charts_html = "<p>No se generaron gráficos en esta ejecución.</p>"

    exported_html = "".join(
        f"<li><code>{os.path.basename(p)}</code></li>" for p in results["exported_files"]
    )

    muestreo_txt = (
        f"Submuestra aleatoria de {results['n_obs']:,} píxeles (semilla {results['seed']})"
        if results["sample_points"]
        else f"Todos los píxeles válidos ({results['n_obs']:,})"
    ).replace(",", " ")

    strategy_txt = STRATEGY_LABELS.get(results["strategy"], results["strategy"])

    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Reporte Selección de Variables — GeoSpecies Tools</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0; padding: 25px; background-color: #F8F9FA; color: #333;
        }}
        .container {{
            max-width: 1100px; margin: 0 auto; background: #FFFFFF; padding: 30px;
            border-radius: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.08);
        }}
        .header {{
            border-bottom: 3px solid #2E7D32; padding-bottom: 15px; margin-bottom: 25px;
            display: flex; justify-content: space-between; align-items: center;
        }}
        .header h1 {{ margin: 0; color: #2E7D32; font-size: 24px; }}
        .header .meta {{ font-size: 12px; color: #666; text-align: right; }}
        .section-title {{
            font-size: 18px; font-weight: bold; color: #1E73B8; margin-top: 32px;
            margin-bottom: 15px; border-left: 4px solid #1E73B8; padding-left: 10px;
        }}
        .cards-grid {{
            display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
            gap: 15px; margin-bottom: 25px;
        }}
        .card {{
            background: #F1F8E9; border: 1px solid #C8E6C9; border-radius: 8px;
            padding: 15px; text-align: center;
        }}
        .card .val {{ font-size: 24px; font-weight: bold; color: #2E7D32; }}
        .card .lbl {{
            font-size: 11px; color: #555; margin-top: 4px;
            text-transform: uppercase; letter-spacing: 0.5px;
        }}
        table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 13px; }}
        th, td {{ padding: 8px 10px; border: 1px solid #E0E0E0; text-align: left; }}
        th {{ background-color: #F5F5F5; color: #444; font-weight: 600; }}
        tbody tr:nth-child(even) {{ background-color: #FAFAFA; }}
        .cor-wrap {{ overflow-x: auto; }}
        .cor-wrap table {{ font-size: 11px; }}
        .cor-wrap th, .cor-wrap td {{ text-align: center; white-space: nowrap; padding: 5px 6px; }}
        .charts-container {{
            display: flex; flex-wrap: wrap; gap: 20px; justify-content: center; margin-top: 20px;
        }}
        .chart-box {{
            flex: 1 1 100%; background: #FAFAFA; border: 1px solid #E0E0E0;
            border-radius: 8px; padding: 15px; text-align: center;
        }}
        .chart-box h4 {{ margin: 0 0 10px 0; color: #1E73B8; font-size: 14px; }}
        .chart-box img {{ max-width: 100%; height: auto; border-radius: 4px; }}
        .params {{ background:#F5F7FA; border:1px solid #E0E0E0; border-radius:8px; padding:15px; }}
        .params td:first-child {{ font-weight:600; width: 34%; color:#546E7A; }}
        .params table {{ margin-bottom:0; }}
        .params td {{ border:none; padding:5px 8px; }}
        .note {{
            background:#FFF8E1; border-left:4px solid #FFA000; padding:12px 15px;
            border-radius:4px; font-size:13px; color:#5D4037; margin-top:15px;
        }}
        .footer {{
            margin-top: 40px; padding-top: 15px; border-top: 1px solid #E0E0E0;
            text-align: center; font-size: 12px; color: #888;
        }}
        code {{ background: #F1F1F1; padding: 1px 5px; border-radius: 3px; }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🌿 Selección de Variables — Colinealidad</h1>
        <div class="meta">GeoSpecies Tools<br>{now_str}</div>
    </div>

    <div class="cards-grid">
        <div class="card"><div class="val">{results['n_vars']}</div><div class="lbl">Variables de entrada</div></div>
        <div class="card"><div class="val">{len(selected)}</div><div class="lbl">Seleccionadas</div></div>
        <div class="card"><div class="val">{n_descartadas}</div><div class="lbl">Descartadas</div></div>
        <div class="card"><div class="val">{n_grupos}</div><div class="lbl">Grupos (clusters)</div></div>
        <div class="card"><div class="val">{cutoff:.2f}</div><div class="lbl">Umbral |r|</div></div>
    </div>

    <div class="section-title">⚙️ Parámetros de la ejecución</div>
    <div class="params">
        <table>
            <tr><td>Método de correlación</td><td>{method}</td></tr>
            <tr><td>Umbral de colinealidad</td><td>|r| ≥ {cutoff:.2f}</td></tr>
            <tr><td>Enlace jerárquico</td><td>Complete linkage sobre distancia d = 1 − |r|</td></tr>
            <tr><td>Criterio de selección</td><td>{strategy_txt}</td></tr>
            <tr><td>Muestreo</td><td>{muestreo_txt}</td></tr>
            <tr><td>Carpeta de salida</td><td><code>{results['select_dir']}</code></td></tr>
        </table>
    </div>

    <div class="section-title">📋 Tabla de clusters</div>
    <table>
        <thead>
        <tr>
            <th>Variable</th><th style="text-align:center;">Grupo</th>
            <th style="text-align:center;">Tamaño</th>
            <th style="text-align:center;">|r| máx. dentro del grupo</th>
            <th>Estado</th>
        </tr>
        </thead>
        <tbody>{cluster_rows_html}</tbody>
    </table>

    <div class="section-title">🧬 Composición de los grupos</div>
    <table>
        <thead><tr><th style="text-align:center;">Grupo</th><th style="text-align:center;">Tamaño</th><th>Variables</th></tr></thead>
        <tbody>{grupos_html}</tbody>
    </table>

    <div class="section-title">📊 Gráficos</div>
    <div class="charts-container">{charts_html}</div>

    <div class="section-title">🔢 Matriz de correlación ({method})</div>
    <div class="cor-wrap">
        <table>
            <thead><tr><th></th>{cor_header_html}</tr></thead>
            <tbody>{cor_rows_html}</tbody>
        </table>
    </div>
    <p style="font-size:12px; color:#888;">
        Rojo: |r| ≥ {cutoff:.2f} (supera el umbral). Ámbar: se acerca al umbral (≥ {cutoff * 0.75:.2f}).
    </p>

    <div class="section-title">✅ Rásters exportados (<code>Select_variable/</code>)</div>
    <ul>{exported_html}</ul>

    <div class="note">
        <b>Sobre la interpretación:</b> el umbral de colinealidad es una decisión
        metodológica, no un resultado estadístico. Un |r| de 0.8 es una convención
        frecuente en modelamiento de nicho, pero conviene contrastar la selección
        con criterio ecológico: si dos variables de un mismo grupo son
        estadísticamente intercambiables, la que tenga sentido biológico para la
        especie modelada es la que debería conservarse, por encima de lo que
        sugiera el algoritmo.
    </div>

    <div class="footer">
        Generado por GeoSpecies Tools — Selección de Variables<br>
        Implementación en Python/NumPy equivalente a <code>virtualspecies::removeCollinearity()</code> (R)
    </div>
</div>
</body>
</html>"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return report_path
