"""
batch.py — Orquesta el análisis de resultados MaxEnt para todas las
especies de un maxentResults.csv, sin depender de QThread (worker.py
solo envuelve esto en señales Qt).

Estructura de salida (subcarpetas creadas de forma perezosa -- solo se
crea lo que realmente se va a usar, ver _species_output_dirs):

<output_root>/
  log_ejecucion.txt
  Resumen_General.csv
  <especie_1>/
    Reporte_Interpretacion.html  -- ranking integrado + gráficos embebidos
    Tablas/{Datos_AUC.csv, Contribucion_Variables.csv, Indice_Integrado.csv, Resumen_Habitat.csv}
    Figuras/{Contribucion_Variables.png, Importancia_Permutacion.png}
    Raster/{Modelo_Binario.tif, Habitat_Potencial.tif}
    Vector/{Distribucion_Potencial.gpkg, Habitat_Potencial.gpkg, Habitat_Potencial_Disuelto.gpkg}
    Curvas_Respuesta/  -- solo si se encontró al menos una imagen
    Jackknife/         -- solo si se encontró al menos una imagen
  <especie_2>/
    ...

Decisión explícita (no auto-carga capas en QGIS): con muchas especies
en un solo batch, cargar automáticamente 5 capas por especie llenaría
el panel de capas de QGIS sin que se pidiera. Esta función deja todo
escrito en disco + el resumen/log; cargar layers puntuales queda a
criterio del usuario (o de una acción futura "abrir resultado" en el
diálogo), no automático para todo el batch.
"""

import os

import pandas as pd

from . import catalog, charts, file_finder, raster_ops, raster_preview, reader, report
from .catalog import HABITAT_CLASSES


class SpeciesResult:
    """Resultado de procesar una especie: qué se generó y qué se saltó."""

    def __init__(self, species_base):
        self.species_base = species_base
        self.outputs = {}
        self.warnings = []
        self.error = None
        self.skipped = False

    def to_summary_row(self):
        row = {"Especie": self.species_base, "Error": self.error or "", "Advertencias": "; ".join(self.warnings)}
        row.update({k: v for k, v in self.outputs.items() if not isinstance(v, (list, dict))})
        return row


def _species_output_dirs(output_root, species_base):
    base = os.path.join(output_root, species_base)
    dirs = {
        "base": base,
        "tablas": os.path.join(base, "Tablas"),
        "figuras": os.path.join(base, "Figuras"),
        "raster": os.path.join(base, "Raster"),
        "vector": os.path.join(base, "Vector"),
        "curvas": os.path.join(base, "Curvas_Respuesta"),
        "jackknife": os.path.join(base, "Jackknife"),
    }
    return dirs


def process_species(group, carpeta_maxent, output_root, threshold_criterion,
                    target_crs=None, max_response_curves=10, status_cb=None):
    """
    Procesa UNA especie (una entrada de reader.group_species_rows).

    No lanza excepción si faltan archivos opcionales (jackknife, tabla
    de contribución de variables) -- se registra en `.warnings` y se
    sigue con lo que sí esté disponible. Solo se marca `.skipped = True`
    si falta el raster continuo (.asc): sin eso no hay nada que analizar
    para esa especie.

    Retorna
    -------
    SpeciesResult
    """
    species_base = group["species_base"]
    row = group["row"]
    result = SpeciesResult(species_base)

    def _status(msg):
        if status_cb:
            status_cb(f"[{species_base}] {msg}")

    raster_path = file_finder.find_species_raster(carpeta_maxent, species_base)
    if raster_path is None:
        result.skipped = True
        result.error = "No se encontró el raster continuo (.asc) -- especie omitida."
        return result

    dirs = _species_output_dirs(output_root, species_base)
    os.makedirs(dirs["tablas"], exist_ok=True)
    os.makedirs(dirs["figuras"], exist_ok=True)
    os.makedirs(dirs["raster"], exist_ok=True)
    os.makedirs(dirs["vector"], exist_ok=True)

    # --- Exportar raster continuo sin clasificar a GeoTIFF ---
    _status("Exportando raster continuo sin clasificar (GeoTIFF)")
    continuo_tif_path = os.path.join(dirs["raster"], "Habitat_Potencial_Continuo.tif")
    base_raster_for_ops = raster_path
    try:
        continuo_tif_path = raster_ops.copy_raster_as_tif(
            raster_path, continuo_tif_path, target_crs_authid=target_crs
        )
        result.outputs["Raster continuo"] = continuo_tif_path
        base_raster_for_ops = continuo_tif_path
    except raster_ops.RasterOpsError as exc:
        result.warnings.append(f"No se pudo exportar el raster continuo a GeoTIFF: {exc}")

    # --- Tabla AUC ---
    _status("Exportando Datos_AUC.csv")
    auc_cols = [c for c in ("Species", "Test AUC", "AUC Standard Deviation") if c in row.index]
    pd.DataFrame([row[auc_cols]]).to_csv(os.path.join(dirs["tablas"], "Datos_AUC.csv"), index=False)
    result.outputs["Test AUC"] = row.get("Test AUC")

    model_info = {
        "Especie": species_base,
        "Modelo usado para variables": (
            f"Réplica {group.get('best_replicate_index')}" if group.get("best_replicate_index") is not None
            else "Corrida única (sin réplicas)"
        ),
        "N° de muestras de entrenamiento": row.get("#Training samples"),
        "Iteraciones": row.get("Iterations"),
        "N° de puntos de fondo": row.get("#Background points"),
    }
    auc_info = {
        "Training AUC": row.get("Training AUC"),
        "Test AUC": row.get("Test AUC"),
        "Desviación estándar AUC": row.get("AUC Standard Deviation"),
        "Ganancia de entrenamiento regularizada": row.get("Regularized training gain"),
        "Ganancia de prueba": row.get("Test gain"),
    }

    # --- Threshold ---
    threshold_value = None
    threshold_info = None
    try:
        threshold_value, threshold_col = reader.resolve_threshold_column(row, threshold_criterion)
        result.outputs["Threshold"] = threshold_value
        result.outputs["Threshold columna"] = threshold_col
        threshold_info = {"Criterio": threshold_criterion, "Valor": threshold_value}
    except reader.MaxentReadError as exc:
        result.warnings.append(f"Threshold no disponible ({exc}); se omite el raster binario.")

    # --- Tabla de contribución de variables (HTML) ---
    contrib_df = None
    html_path = file_finder.find_species_html(carpeta_maxent, species_base, group.get("best_replicate_index"))
    if html_path is None:
        result.warnings.append("No se encontró el reporte HTML -- se omiten variables/figuras/curvas.")
    else:
        try:
            contrib_df = reader.parse_variable_contribution_table(html_path)
            contrib_df.to_csv(os.path.join(dirs["tablas"], "Contribucion_Variables.csv"),
                               index=False, encoding="utf-8-sig")
            _status("Generando figuras de contribución/importancia")
            charts.plot_variable_contribution(contrib_df, os.path.join(dirs["figuras"], "Contribucion_Variables.png"))
            charts.plot_permutation_importance(contrib_df, os.path.join(dirs["figuras"], "Importancia_Permutacion.png"))
        except reader.MaxentReadError as exc:
            result.warnings.append(f"No se pudo leer la tabla de variables del HTML: {exc}")

    # --- Curvas de respuesta (top N por contribución) ---
    curve_dest_paths = []
    if contrib_df is not None:
        ordered_vars = list(contrib_df["Variable"])
        curve_images = file_finder.find_response_curve_images(carpeta_maxent, species_base, ordered_vars)
        if curve_images:
            os.makedirs(dirs["curvas"], exist_ok=True)
            import shutil
            for i, src in enumerate(curve_images[:max_response_curves], start=1):
                var_name = os.path.basename(src).replace(f"{species_base}_", "")
                dest = os.path.join(dirs["curvas"], f"{i:02d}_{var_name}")
                shutil.copy2(src, dest)
                curve_dest_paths.append(dest)
            result.outputs["Curvas de respuesta copiadas"] = min(len(curve_images), max_response_curves)
        else:
            result.warnings.append("No se encontraron imágenes de curvas de respuesta.")

    # --- Jackknife (opcional, se copia lo que exista) ---
    jk = file_finder.find_jackknife_images(carpeta_maxent, species_base)
    jk_labels = {"training": "01_Jackknife_Training_Gain.png",
                 "test": "02_Jackknife_Test_Gain.png", "auc": "03_Jackknife_AUC.png"}
    jk_dest_paths = {"training": None, "test": None, "auc": None}
    any_jk = any(v is not None for v in jk.values())
    if any_jk:
        os.makedirs(dirs["jackknife"], exist_ok=True)
        import shutil
        for key, src in jk.items():
            if src is not None:
                dest = os.path.join(dirs["jackknife"], jk_labels[key])
                shutil.copy2(src, dest)
                jk_dest_paths[key] = dest
            else:
                result.warnings.append(f"Jackknife '{key}' no disponible (¿no se activó al correr MaxEnt?).")
    else:
        result.warnings.append("Jackknife no disponible para esta especie (¿no se activó al correr MaxEnt?).")

    # --- Raster binario (solo si hay threshold) ---
    binary_preview_path = None
    if threshold_value is not None:
        _status("Generando raster binario")
        try:
            binario_path = raster_ops.make_binary_raster(
                base_raster_for_ops, threshold_value, os.path.join(dirs["raster"], "Modelo_Binario.tif"))
            result.outputs["Raster binario"] = binario_path

            vector_binario = raster_ops.polygonize(
                binario_path, "VALUE", os.path.join(dirs["vector"], "Distribucion_Potencial.gpkg"))

            # Agregar campo area_ha al vector binario
            try:
                raster_ops.add_area_field(vector_binario)
            except raster_ops.RasterOpsError as exc:
                result.warnings.append(f"No se pudo añadir campo area_ha a Distribucion_Potencial.gpkg: {exc}")

            result.outputs["Vector distribución potencial"] = vector_binario

            try:
                binary_preview_path = raster_preview.render_raster_preview(
                    binario_path, os.path.join(dirs["figuras"], "Distribucion_Potencial_Preview.png"),
                    title="Distribución Potencial (Binario)", cmap="YlGnBu",
                )
            except raster_preview.RasterPreviewError as exc:
                result.warnings.append(f"No se pudo generar la vista previa del raster binario: {exc}")
        except raster_ops.RasterOpsError as exc:
            result.warnings.append(f"Raster binario/polígono no generado: {exc}")

    # --- Clasificación de hábitat (4 clases, independiente del threshold) ---
    habitat_preview_path = None
    habitat_summary_df = None
    _status("Generando clasificación de hábitat")
    try:
        clasificado_path = raster_ops.make_habitat_classification_raster(
            base_raster_for_ops, os.path.join(dirs["raster"], "Habitat_Potencial.tif"))
        result.outputs["Raster clasificado"] = clasificado_path

        try:
            habitat_preview_path = raster_preview.render_raster_preview(
                clasificado_path, os.path.join(dirs["figuras"], "Habitat_Potencial_Preview.png"),
                title="Clasificación de Hábitat Potencial", discrete_classes=HABITAT_CLASSES,
            )
        except raster_preview.RasterPreviewError as exc:
            result.warnings.append(f"No se pudo generar la vista previa del raster clasificado: {exc}")

        vector_clases = raster_ops.polygonize(
            clasificado_path, "Clase", os.path.join(dirs["vector"], "Habitat_Potencial.gpkg"))

        # Agregar campo Descripcion con nombre de hábitat y campo area_ha
        try:
            raster_ops.add_description_field(vector_clases)
        except raster_ops.RasterOpsError as exc:
            result.warnings.append(f"No se pudo añadir campo Descripcion a Habitat_Potencial.gpkg: {exc}")

        try:
            raster_ops.add_area_field(vector_clases)
        except raster_ops.RasterOpsError as exc:
            result.warnings.append(f"No se pudo añadir campo area_ha a Habitat_Potencial.gpkg: {exc}")

        vector_disuelto = raster_ops.dissolve_by_field(
            vector_clases, "Clase", os.path.join(dirs["vector"], "Habitat_Potencial_Disuelto.gpkg"))
        result.outputs["Vector hábitat disuelto"] = vector_disuelto

        # Agregar campo Descripcion y campo area_ha al vector disuelto
        try:
            raster_ops.add_description_field(vector_disuelto)
        except raster_ops.RasterOpsError as exc:
            result.warnings.append(f"No se pudo añadir campo Descripcion a Habitat_Potencial_Disuelto.gpkg: {exc}")

        try:
            raster_ops.add_area_field(vector_disuelto)
        except raster_ops.RasterOpsError as exc:
            result.warnings.append(f"No se pudo añadir campo area_ha a Habitat_Potencial_Disuelto.gpkg: {exc}")

        _status("Calculando áreas por clase")
        areas = raster_ops.compute_areas_by_feature(vector_disuelto, extra_fields=["Clase"])
        total_area = sum(a["area_m2"] for a in areas) or 1.0

        class_by_value = {c["value"]: c for c in HABITAT_CLASSES}
        resumen_rows = []
        for area_info in areas:
            clase = area_info["Clase"]
            info = class_by_value.get(clase, {"label": f"Clase {clase}"})
            resumen_rows.append({
                "Clase": clase, "Descripción": info.get("label", ""),
                "Área (ha)": round(area_info["area_m2"] / 10000, 2),
                "Área (km²)": round(area_info["area_m2"] / 1_000_000, 2),
                "%": round(area_info["area_m2"] * 100 / total_area, 2),
            })
        habitat_summary_df = pd.DataFrame(resumen_rows)
        habitat_summary_df.to_csv(
            os.path.join(dirs["tablas"], "Resumen_Habitat.csv"), index=False, encoding="utf-8-sig")
        result.outputs["Resumen hábitat"] = os.path.join(dirs["tablas"], "Resumen_Habitat.csv")

    except raster_ops.RasterOpsError as exc:
        result.warnings.append(f"Clasificación de hábitat no generada: {exc}")

    # --- Índice de importancia integrada + reporte HTML institucional ---
    # (va AL FINAL: necesita las vistas previas de raster y el resumen
    # de hábitat, que recién existen después de los bloques anteriores)
    if contrib_df is not None:
        try:
            ranking_df, any_jackknife_used = reader.build_integrated_ranking(contrib_df, row)
            ranking_df.to_csv(os.path.join(dirs["tablas"], "Indice_Integrado.csv"),
                               index=False, encoding="utf-8-sig")
            result.outputs["Variable top (Índice Integrado)"] = ranking_df.iloc[0]["Variable"]

            html_used_name = os.path.basename(html_path)
            is_plain_html = html_used_name == f"{species_base}.html"
            if is_plain_html:
                source_note = f"Contribución/Permutación/Jackknife provienen de '{html_used_name}'."
            else:
                source_note = (
                    f"MaxEnt no genera tabla de contribución de variables para un modelo 'promedio' en "
                    f"corridas con réplicas -- Contribución/Permutación/Jackknife provienen de la MEJOR "
                    f"réplica individual por Test AUC ('{html_used_name}'), mientras que el raster continuo "
                    f"y los mapas de este reporte provienen del modelo promedio de todas las réplicas. "
                    f"Son dos modelos distintos combinados en este reporte -- no un error."
                )

            _status("Generando reporte HTML institucional")
            report_path = report.render_species_report(
                species_base, ranking_df, any_jackknife_used,
                model_info=model_info, auc_info=auc_info, threshold_info=threshold_info,
                contribution_chart_path=os.path.join(dirs["figuras"], "Contribucion_Variables.png"),
                permutation_chart_path=os.path.join(dirs["figuras"], "Importancia_Permutacion.png"),
                response_curve_paths=curve_dest_paths, jackknife_paths=jk_dest_paths,
                binary_raster_preview_path=binary_preview_path,
                habitat_raster_preview_path=habitat_preview_path,
                habitat_summary_df=habitat_summary_df,
                output_path=os.path.join(dirs["base"], "Reporte_Interpretacion.html"),
                html_source_note=source_note,
            )
            result.outputs["Reporte HTML"] = report_path
            if not any_jackknife_used:
                result.warnings.append(
                    "El índice integrado se calculó sin Jackknife (dato no encontrado en maxentResults.csv "
                    "para ninguna variable) -- pesos renormalizados a Contribución/Permutación."
                )
        except Exception as exc:  # noqa: BLE001 - el reporte no debe tumbar el resto del procesamiento de la especie
            result.warnings.append(f"No se pudo generar el índice integrado/reporte: {exc}")

    return result


def run_batch(csv_path, carpeta_maxent, output_root, threshold_criterion=catalog.DEFAULT_THRESHOLD_CRITERION,
              target_crs=None, status_cb=None, progress_cb=None, cancel_check=None):
    """
    Procesa TODAS las especies de `csv_path` (agrupadas por
    reader.group_species_rows) y escribe el resumen global + log.

    Cancelación: se revisa entre especies (no a mitad de una), ya que
    cada especie es una unidad de trabajo relativamente corta (segundos,
    no minutos) comparado con las descargas de otras herramientas.

    Retorna
    -------
    list[SpeciesResult]
    """
    os.makedirs(output_root, exist_ok=True)
    df = reader.read_results_csv(csv_path)
    groups = reader.group_species_rows(df)

    results = []
    log_lines = []

    for i, group in enumerate(groups):
        if cancel_check is not None and cancel_check():
            log_lines.append(f"Cancelado antes de procesar: {group['species_base']}")
            break

        if status_cb:
            status_cb(f"Procesando {group['species_base']} ({i + 1}/{len(groups)})...")
        if progress_cb:
            progress_cb(i, len(groups))

        try:
            result = process_species(group, carpeta_maxent, output_root, threshold_criterion,
                                     target_crs=target_crs, status_cb=status_cb)
        except Exception as exc:  # noqa: BLE001 - una especie no debe tumbar el resto del batch
            result = SpeciesResult(group["species_base"])
            result.error = f"Error inesperado: {exc}"
            result.skipped = True

        results.append(result)
        log_lines.append(
            f"{group['species_base']}: "
            f"{'OMITIDA - ' + result.error if result.skipped else 'OK'}"
            + (f" | Advertencias: {'; '.join(result.warnings)}" if result.warnings else "")
        )

    if progress_cb:
        progress_cb(len(results), len(groups))

    with open(os.path.join(output_root, "log_ejecucion.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

    summary_df = pd.DataFrame([r.to_summary_row() for r in results])
    summary_df.to_csv(os.path.join(output_root, "Resumen_General.csv"), index=False, encoding="utf-8-sig")

    return results
