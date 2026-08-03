"""
report.py — Generador de reporte HTML para los resultados del modelo Random Forest.
"""

import os
import datetime


def generate_html_report(eval_results, params_dict, species_name, roc_chart_rel, imp_chart_rel, output_dir):
    """
    Genera un archivo HTML interactivo y estilizado con los resultados de la validación.
    """
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, f"reporte_random_forest_{species_name}.html")

    cm = eval_results["confusion_matrix"]
    auc_val = eval_results["auc"]
    acc_val = eval_results["accuracy"]
    sens_val = eval_results["sensitivity"]
    spec_val = eval_results["specificity"]
    prec_val = eval_results["precision"]
    f1_val = eval_results["f1_score"]

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Filas de tabla de importancia
    imp_rows_html = ""
    for idx, (var_name, imp_pct) in enumerate(eval_results["var_importances"], start=1):
        imp_rows_html += f"""
        <tr>
            <td><b>{idx}</b></td>
            <td><code>{var_name}</code></td>
            <td>
                <div style="display:flex; align-items:center; gap:8px;">
                    <div style="background-color:#E0E0E0; width:120px; height:12px; border-radius:6px; overflow:hidden;">
                        <div style="background-color:#1E73B8; width:{min(100, imp_pct):.1f}%; height:100%;"></div>
                    </div>
                    <span><b>{imp_pct:.2f}%</b></span>
                </div>
            </td>
        </tr>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Reporte Random Forest — {species_name}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 25px;
            background-color: #F8F9FA;
            color: #333;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            background: #FFFFFF;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.08);
        }}
        .header {{
            border-bottom: 3px solid #2E7D32;
            padding-bottom: 15px;
            margin-bottom: 25px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .header h1 {{
            margin: 0;
            color: #2E7D32;
            font-size: 24px;
        }}
        .header .meta {{
            font-size: 12px;
            color: #666;
            text-align: right;
        }}
        .section-title {{
            font-size: 18px;
            font-weight: bold;
            color: #1E73B8;
            margin-top: 30px;
            margin-bottom: 15px;
            border-left: 4px solid #1E73B8;
            padding-left: 10px;
        }}
        .cards-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 15px;
            margin-bottom: 25px;
        }}
        .card {{
            background: #F1F8E9;
            border: 1px solid #C8E6C9;
            border-radius: 8px;
            padding: 15px;
            text-align: center;
        }}
        .card .val {{
            font-size: 24px;
            font-weight: bold;
            color: #2E7D32;
        }}
        .card .lbl {{
            font-size: 12px;
            color: #555;
            margin-top: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
        }}
        th, td {{
            padding: 10px 12px;
            border: 1px solid #E0E0E0;
            text-align: left;
        }}
        th {{
            background-color: #F5F5F5;
            color: #444;
            font-weight: 600;
        }}
        tr:nth-child(even) {{
            background-color: #FAFAFA;
        }}
        .cm-table {{
            width: auto;
            margin: 0 auto;
            text-align: center;
        }}
        .cm-table th, .cm-table td {{
            padding: 15px 25px;
            text-align: center;
        }}
        .cm-header {{
            background-color: #E3F2FD;
            font-weight: bold;
        }}
        .cm-tp {{ background-color: #C8E6C9; font-weight: bold; color: #1B5E20; }}
        .cm-tn {{ background-color: #E8F5E9; font-weight: bold; color: #2E7D32; }}
        .cm-fp {{ background-color: #FFECB3; font-weight: bold; color: #E65100; }}
        .cm-fn {{ background-color: #FFCDD2; font-weight: bold; color: #B71C1C; }}
        .charts-container {{
            display: flex;
            flex-wrap: wrap;
            gap: 20px;
            justify-content: center;
            margin-top: 20px;
        }}
        .chart-box {{
            flex: 1;
            min-width: 320px;
            background: #FAFAFA;
            border: 1px solid #E0E0E0;
            border-radius: 8px;
            padding: 15px;
            text-align: center;
        }}
        .chart-box img {{
            max-width: 100%;
            height: auto;
            border-radius: 4px;
        }}
        .footer {{
            margin-top: 40px;
            padding-top: 15px;
            border-top: 1px solid #E0E0E0;
            text-align: center;
            font-size: 12px;
            color: #888;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>🌲 GeoSpecies Tools — Random Forest</h1>
                <div style="font-size:14px; color:#555; margin-top:4px;">Modelamiento de idoneidad de hábitat para: <b>{species_name}</b></div>
            </div>
            <div class="meta">
                <div>Generado: <b>{now_str}</b></div>
                <div>Plugin GeoSpecies Tools</div>
            </div>
        </div>

        <!-- MÉRTRICAS CLAVE -->
        <div class="section-title">📊 Métricas Clave de Validación</div>
        <div class="cards-grid">
            <div class="card">
                <div class="val">{auc_val:.4f}</div>
                <div class="lbl">AUC - ROC</div>
            </div>
            <div class="card">
                <div class="val">{acc_val * 100:.1f}%</div>
                <div class="lbl">Exactitud (Accuracy)</div>
            </div>
            <div class="card">
                <div class="val">{sens_val * 100:.1f}%</div>
                <div class="lbl">Sensibilidad (Recall)</div>
            </div>
            <div class="card">
                <div class="val">{spec_val * 100:.1f}%</div>
                <div class="lbl">Especificidad</div>
            </div>
            <div class="card">
                <div class="val">{prec_val * 100:.1f}%</div>
                <div class="lbl">Precisión (Precision)</div>
            </div>
            <div class="card">
                <div class="val">{f1_val:.4f}</div>
                <div class="lbl">F1 - Score</div>
            </div>
        </div>

        <!-- CONFIGURACIÓN DE PARÁMETROS -->
        <div class="section-title">⚙️ Parámetros del Modelo</div>
        <table>
            <tr>
                <th>Parámetro</th>
                <th>Valor Configurado</th>
                <th>Descripción</th>
            </tr>
            <tr>
                <td><b>Número de Árboles (n_estimators)</b></td>
                <td>{params_dict.get('n_estimators', 100)}</td>
                <td>Cantidad de árboles en el ensamble Random Forest</td>
            </tr>
            <tr>
                <td><b>Profundidad Máxima (max_depth)</b></td>
                <td>{params_dict.get('max_depth') or 'Sin límite'}</td>
                <td>Límite de profundidad de cada árbol de decisión</td>
            </tr>
            <tr>
                <td><b>Puntos de Presencia</b></td>
                <td>{eval_results.get('n_presences', 0)}</td>
                <td>Registros válidos extraídos del CSV</td>
            </tr>
            <tr>
                <td><b>Puntos de Pseudo-ausencia</b></td>
                <td>{eval_results.get('n_absences', 0)}</td>
                <td>Muestras aleatorias de fondo (background)</td>
            </tr>
            <tr>
                <td><b>División Validación / Test</b></td>
                <td>{params_dict.get('test_size', 25)}% ({eval_results.get('n_test', 0)} muestras de prueba)</td>
                <td>Proporción de datos reservados para calcular las métricas</td>
            </tr>
        </table>

        <!-- MATRIZ DE CONFUSIÓN -->
        <div class="section-title">🧮 Matriz de Confusión (Datos de Validación)</div>
        <table class="cm-table">
            <tr>
                <th colspan="2" rowspan="2"></th>
                <th colspan="2" class="cm-header">Valor Real</th>
            </tr>
            <tr>
                <th class="cm-header">Presencia (1)</th>
                <th class="cm-header">Ausencia (0)</th>
            </tr>
            <tr>
                <th rowspan="2" class="cm-header">Predicción</th>
                <th class="cm-header">Presencia (1)</th>
                <td class="cm-tp">Verdaderos Positivos (TP)<br><b>{cm['tp']}</b></td>
                <td class="cm-fp">Falsos Positivos (FP)<br><b>{cm['fp']}</b></td>
            </tr>
            <tr>
                <th class="cm-header">Ausencia (0)</th>
                <td class="cm-fn">Falsos Negativos (FN)<br><b>{cm['fn']}</b></td>
                <td class="cm-tn">Verdaderos Negativos (TN)<br><b>{cm['tn']}</b></td>
            </tr>
        </table>

        <!-- GRÁFICOS -->
        <div class="section-title">📈 Visualizaciones y Gráficos</div>
        <div class="charts-container">
            {"<div class='chart-box'><h4>Curva ROC</h4><img src='" + roc_chart_rel + "' alt='Curva ROC'></div>" if roc_chart_rel else ""}
            {"<div class='chart-box'><h4>Importancia de Variables</h4><img src='" + imp_chart_rel + "' alt='Importancia de Variables'></div>" if imp_chart_rel else ""}
        </div>

        <!-- IMPORTANCIA DE VARIABLES -->
        <div class="section-title">🌿 Contribución e Importancia de Variables Ambientales</div>
        <table>
            <thead>
                <tr>
                    <th style="width:40px;">#</th>
                    <th>Variable Ambiental</th>
                    <th>Porcentaje de Importancia (%)</th>
                </tr>
            </thead>
            <tbody>
                {imp_rows_html}
            </tbody>
        </table>

        <div class="footer">
            GeoSpecies Tools — Módulo de Machine Learning (Random Forest) para QGIS
        </div>
    </div>
</body>
</html>
"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return report_path
