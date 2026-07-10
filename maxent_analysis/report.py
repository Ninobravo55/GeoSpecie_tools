"""
report.py — Reporte HTML profesional de interpretación por especie,
con identidad institucional de Geomática Ambiental.

Puro Python (base64/html, sin qgis) -- testeable sin QGIS instalado.
Las imágenes se embeben como data URI (base64) para que el .html sea
un archivo autocontenido y portable.

Requisito explícito del usuario: no se recalcula ni modifica ningún
resultado científico acá -- este módulo solo formatea/presenta datos
que ya vienen calculados por batch.py/reader.py/raster_ops.py.
"""

import base64
import html as html_lib
import os

from . import branding


def _embed_image(path, alt=""):
    """<img> embebido en base64, o aviso si falta -- nunca lanza
    excepción por una imagen faltante (varias son opcionales)."""
    if not path or not os.path.isfile(path):
        return f'<div class="missing">[{html_lib.escape(alt)} no disponible]</div>'
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return f'<div class="missing">[{html_lib.escape(alt)} no se pudo leer]</div>'

    ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
    mime = "jpeg" if ext in ("jpg", "jpeg") else ext
    return (
        f'<figure class="img-card">'
        f'<img alt="{html_lib.escape(alt)}" src="data:image/{mime};base64,{data}">'
        f'<figcaption>{html_lib.escape(alt)}</figcaption>'
        f'</figure>'
    )


def _table_html(headers, rows):
    thead = "".join(f"<th>{html_lib.escape(str(h))}</th>" for h in headers)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{html_lib.escape(str(c))}</td>" for c in row)
        body_rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _ranking_table_html(ranking_df):
    rows = []
    for _, r in ranking_df.iterrows():
        jack_norm = r["Jackknife (normalizado 0-100)"]
        jack_txt = f"{jack_norm:.1f}" if jack_norm is not None and jack_norm == jack_norm else "—"
        rows.append([
            int(r["Ranking"]), r["Variable"], f"{r['Contribución (%)']:.1f}",
            f"{r['Permutación (%)']:.1f}", jack_txt, f"{r['Índice Integrado']:.2f}",
        ])
    return _table_html(
        ["Ranking", "Variable", "Contribución (%)", "Permutación (%)",
         "Jackknife (norm. 0-100)", "Índice Integrado"],
        rows,
    )


def _model_info_table_html(model_info):
    if not model_info:
        return '<div class="missing">[Información general no disponible]</div>'
    rows = [[k, v] for k, v in model_info.items()]
    return _table_html(["Parámetro", "Valor"], rows)


def _auc_table_html(auc_info):
    if not auc_info:
        return '<div class="missing">[Evaluación AUC no disponible]</div>'
    rows = [[k, v] for k, v in auc_info.items()]
    return _table_html(["Métrica", "Valor"], rows)


def _habitat_summary_table_html(habitat_summary_df):
    if habitat_summary_df is None or habitat_summary_df.empty:
        return '<div class="missing">[Estadísticas espaciales no disponibles]</div>'
    rows = [list(r) for _, r in habitat_summary_df.iterrows()]
    return _table_html(list(habitat_summary_df.columns), rows)


def _auc_quality_label(test_auc):
    """
    Clasificación textual estándar de AUC (Swets 1988, ampliamente
    citada en literatura de modelamiento de nicho) -- descriptiva, no
    una afirmación ecológica sobre la especie.
    """
    if test_auc is None:
        return None
    if test_auc >= 0.9:
        return "excelente capacidad discriminatoria"
    if test_auc >= 0.8:
        return "buena capacidad discriminatoria"
    if test_auc >= 0.7:
        return "capacidad discriminatoria aceptable"
    return "capacidad discriminatoria limitada"


def _conclusions_html(species_base, ranking_df, auc_info, habitat_summary_df, any_jackknife):
    parts = []

    top = ranking_df.iloc[0]
    parts.append(
        f"La variable con mayor índice de importancia integrada para <i>{html_lib.escape(species_base)}</i> "
        f"es <b>{html_lib.escape(str(top['Variable']))}</b> (Índice = {top['Índice Integrado']:.2f})."
    )

    test_auc = (auc_info or {}).get("Test AUC")
    if isinstance(test_auc, (int, float)):
        quality = _auc_quality_label(test_auc)
        parts.append(f"El modelo alcanzó un AUC de prueba de {test_auc:.3f}, correspondiente a {quality}.")

    if habitat_summary_df is not None and not habitat_summary_df.empty and "%" in habitat_summary_df.columns:
        best_row = habitat_summary_df.loc[habitat_summary_df["%"].idxmax()]
        desc_col = "Descripción" if "Descripción" in habitat_summary_df.columns else habitat_summary_df.columns[1]
        parts.append(
            f"La categoría de hábitat con mayor extensión es '{html_lib.escape(str(best_row[desc_col]))}', "
            f"con {best_row['%']:.1f}% del área analizada."
        )

    if not any_jackknife:
        parts.append(
            "El índice integrado de esta especie se calculó sin el término de Jackknife "
            "(no disponible en esta corrida), con los pesos renormalizados entre Contribución y Permutación."
        )

    return "<p>" + " ".join(parts) + "</p>"


_CSS = f"""
* {{ box-sizing: border-box; }}
body {{
  font-family: {branding.FONT_STACK}; margin: 0; padding: 0;
  color: {branding.COLOR_TEXTO}; background: {branding.COLOR_FONDO};
}}
.container {{ max-width: 1100px; margin: 0 auto; padding: 0 24px 40px; }}

header.site-header {{
  display: flex; align-items: center; gap: 24px; padding: 20px 24px;
  border-bottom: 4px solid {branding.COLOR_AZUL}; background: {branding.COLOR_FONDO};
  flex-wrap: wrap;
}}
header.site-header img.logo {{ height: 70px; width: auto; }}
header.site-header .titles h1 {{
  margin: 0; font-size: 22px; color: {branding.COLOR_AZUL}; font-weight: 700;
}}
header.site-header .titles h2 {{
  margin: 4px 0 0; font-size: 14px; color: {branding.COLOR_VERDE}; font-weight: 500;
}}
h1.species-title {{
  color: {branding.COLOR_AZUL}; font-size: 26px; margin: 28px 0 4px; text-align: center;
}}
.method-note {{
  background: {branding.COLOR_GRIS_CLARO}; border-left: 4px solid {branding.COLOR_AZUL};
  padding: 12px 16px; margin: 16px 0; font-size: 13.5px; border-radius: 4px;
}}

section {{ margin-top: 36px; }}
section h2 {{
  color: {branding.COLOR_AZUL}; border-bottom: 2px solid {branding.COLOR_VERDE};
  padding-bottom: 6px; font-size: 19px;
}}

table {{ border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 14px; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: center; }}
th {{ background: {branding.COLOR_AZUL}; color: white; font-weight: 600; }}
tbody tr:nth-child(even) {{ background: {branding.COLOR_GRIS_CLARO}; }}

.missing {{ color: #b45f06; font-style: italic; padding: 8px 0; }}

.gallery {{ display: flex; flex-wrap: wrap; gap: 20px; justify-content: center; }}
figure.img-card {{
  margin: 0; background: {branding.COLOR_GRIS_CLARO}; border-radius: 10px;
  padding: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.12); max-width: 480px;
}}
figure.img-card img {{
  max-width: 100%; display: block; border-radius: 6px;
}}
figure.img-card figcaption {{
  text-align: center; font-size: 12.5px; color: {branding.COLOR_TEXTO}; margin-top: 6px;
}}

footer.site-footer {{
  margin-top: 48px; padding: 24px; text-align: center; font-size: 12.5px;
  color: #777; border-top: 1px solid #ddd;
}}
footer.site-footer a {{ color: {branding.COLOR_AZUL}; text-decoration: none; }}

@media print {{
  header.site-header {{ break-after: avoid; }}
  section {{ break-inside: avoid; }}
}}
@media (max-width: 700px) {{
  header.site-header {{ flex-direction: column; text-align: center; }}
}}
"""


def render_species_report(species_base, ranking_df, any_jackknife,
                           model_info=None, auc_info=None,
                           contribution_chart_path=None, permutation_chart_path=None,
                           response_curve_paths=None, jackknife_paths=None,
                           binary_raster_preview_path=None, habitat_raster_preview_path=None,
                           habitat_summary_df=None, threshold_info=None,
                           html_source_note=None, output_path=None):
    """
    Genera el reporte HTML institucional autocontenido de una especie.

    No recalcula ni modifica ningún resultado científico -- solo
    formatea/presenta datos ya calculados por batch.py (ranking,
    métricas del CSV, previews de raster ya renderizados, resumen de
    hábitat). Ver raster_preview.py para generar
    binary_raster_preview_path / habitat_raster_preview_path a partir
    de los .tif reales.

    Parámetros
    ----------
    model_info : dict opcional
        Ej. {"N° muestras de entrenamiento": 120, "Iteraciones": 500, ...}
    auc_info : dict opcional
        Ej. {"Training AUC": 0.99, "Test AUC": 0.9876, ...}
    threshold_info : dict opcional
        Ej. {"Criterio": "Maximum training sensitivity plus specificity", "Valor": 0.0546}
    habitat_summary_df : pd.DataFrame opcional
        Columnas: Clase/Descripción/Área (ha)/Área (km²)/%.
    html_source_note : str opcional
        Nota de metodología (ver batch.py: modelo promedio vs. mejor
        réplica individual para variables).

    Retorna
    -------
    str
        `output_path`.
    """
    response_curve_paths = response_curve_paths or []
    jackknife_paths = jackknife_paths or {}

    logo_html = ""
    if branding.logo_exists():
        with open(branding.LOGO_PATH, "rb") as f:
            logo_b64 = base64.b64encode(f.read()).decode("ascii")
        logo_html = f'<img class="logo" src="data:image/png;base64,{logo_b64}" alt="Geomática Ambiental">'

    curves_html = "".join(
        _embed_image(p, alt=os.path.basename(p).replace(".png", "")) for p in response_curve_paths
    ) or '<div class="missing">[Sin curvas de respuesta disponibles]</div>'

    jk_labels = {"training": "Ganancia de entrenamiento", "test": "Ganancia de prueba", "auc": "AUC"}
    jackknife_html = "".join(
        _embed_image(jackknife_paths.get(key), alt=jk_labels.get(key, key))
        for key in ("training", "test", "auc")
    )

    threshold_html = ""
    if threshold_info:
        threshold_html = (
            f"<p><b>Criterio de umbral:</b> {html_lib.escape(str(threshold_info.get('Criterio', '')))} "
            f"(valor logístico = {threshold_info.get('Valor', '—')})</p>"
        )

    method_note_html = (
        f'<div class="method-note"><b>Nota de metodología:</b> {html_lib.escape(html_source_note)}</div>'
        if html_source_note else ""
    )

    html_doc = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_lib.escape(branding.TITULO_PRINCIPAL)} — {html_lib.escape(species_base)}</title>
<style>{_CSS}</style>
</head>
<body>

<header class="site-header">
  {logo_html}
  <div class="titles">
    <h1>{html_lib.escape(branding.TITULO_PRINCIPAL)}</h1>
    <h2>{html_lib.escape(branding.SUBTITULO)}</h2>
  </div>
</header>

<div class="container">

<h1 class="species-title">{html_lib.escape(species_base)}</h1>
{method_note_html}

<section id="info-general">
  <h2>1. Información general del modelo</h2>
  {_model_info_table_html(model_info)}
</section>

<section id="evaluacion-auc">
  <h2>2. Evaluación del modelo (AUC)</h2>
  {_auc_table_html(auc_info)}
  {threshold_html}
</section>

<section id="variables-ambientales">
  <h2>3. Variables ambientales</h2>
  <p>{len(ranking_df)} variable(s) ambiental(es) evaluada(s): {html_lib.escape(", ".join(str(v) for v in ranking_df["Variable"]))}.</p>
</section>

<section id="contribucion-variables">
  <h2>4. Contribución de variables</h2>
  <div class="gallery">{_embed_image(contribution_chart_path, alt="Contribución de Variables")}</div>
</section>

<section id="importancia-permutacion">
  <h2>5. Importancia por permutación</h2>
  <div class="gallery">{_embed_image(permutation_chart_path, alt="Importancia por Permutación")}</div>
</section>

<section id="ranking-integrado">
  <h2>Ranking de importancia integrada</h2>
  <p>Índice = (0.4×Contribución + 0.3×Permutación + 0.3×Jackknife<sub>norm</sub>) / 100
  (pesos renormalizados si falta algún término).</p>
  {_ranking_table_html(ranking_df)}
</section>

<section id="curvas-respuesta">
  <h2>6. Curvas de respuesta</h2>
  <div class="gallery">{curves_html}</div>
</section>

<section id="jackknife">
  <h2>7. Jackknife</h2>
  <div class="gallery">{jackknife_html}</div>
</section>

<section id="distribucion-potencial">
  <h2>8. Distribución potencial</h2>
  <div class="gallery">{_embed_image(binary_raster_preview_path, alt="Distribución Potencial (Binario)")}</div>
</section>

<section id="clasificacion-habitat">
  <h2>9. Clasificación del hábitat</h2>
  <div class="gallery">{_embed_image(habitat_raster_preview_path, alt="Clasificación de Hábitat Potencial")}</div>
</section>

<section id="estadisticas-espaciales">
  <h2>10. Estadísticas espaciales</h2>
  {_habitat_summary_table_html(habitat_summary_df)}
</section>

<section id="conclusiones">
  <h2>11. Conclusiones</h2>
  {_conclusions_html(species_base, ranking_df, auc_info, habitat_summary_df, any_jackknife)}
</section>

</div>

<footer class="site-footer">
  <p>
    🌐 <a href="{branding.CONTACTO_WEB}">{branding.CONTACTO_WEB_LABEL}</a> &nbsp;|&nbsp;
    📧 <a href="mailto:{branding.CONTACTO_EMAIL}">{branding.CONTACTO_EMAIL}</a> &nbsp;|&nbsp;
    📞 {branding.CONTACTO_TELEFONO}
  </p>
  <p>{html_lib.escape(branding.COPYRIGHT_TEXTO)}</p>
  <p>Generado automáticamente por GeoSpecies Tools — Analizar Resultados MaxEnt.</p>
</footer>

</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_doc)
    return output_path
