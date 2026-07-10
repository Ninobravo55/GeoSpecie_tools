"""
file_finder.py — Localiza los archivos de salida de MaxEnt para una
especie dada (raster, html, curvas de respuesta, jackknife), tolerando
tanto corridas con réplicas como sin ellas, y sin lanzar excepción por
archivos opcionales faltantes (jackknife) -- según lo acordado: se
avisa y se sigue, no se detiene todo el batch por una especie con
menos archivos que otra.

Puro Python (os/glob) -- sin qgis, testeable sin QGIS instalado.
"""

import glob
import os


def find_species_raster(carpeta, species_base):
    """
    Busca el raster continuo (.asc) de una especie, probando primero el
    promedio de réplicas ("<especie>_avg.asc") y luego el archivo sin
    sufijo ("<especie>.asc") -- el script original solo probaba la
    segunda variante, que no existe si el run usó réplicas.

    Retorna
    -------
    str | None
        Ruta del archivo, o None si no se encontró ninguna variante
        (no lanza excepción: el llamador decide si eso es fatal).
    """
    candidates = [
        os.path.join(carpeta, f"{species_base}_avg.asc"),
        os.path.join(carpeta, f"{species_base}.asc"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def find_species_html(carpeta, species_base, best_replicate_index=None):
    """
    Reporte HTML de la especie (contiene la tabla de contribución de
    variables que reader.parse_variable_contribution_table() necesita).

    Prueba primero "<especie>.html" (corridas sin réplicas, o un
    reporte combinado si existiera). Si no existe, cae a
    "<especie>_<best_replicate_index>.html" -- confirmado con un caso
    real que MaxEnt NO genera un .html con tabla de contribución para
    un modelo "promedio" en corridas con réplicas (el promediado ocurre
    sobre la superficie de predicción, no sobre un modelo reentrenado);
    el reporte de variables solo existe por réplica individual.

    Parámetros
    ----------
    best_replicate_index : int | None
        Índice de la réplica con mayor Test AUC para esta especie (ver
        reader.group_species_rows). None si no hubo réplicas.
    """
    candidates = [os.path.join(carpeta, f"{species_base}.html")]
    if best_replicate_index is not None:
        candidates.append(os.path.join(carpeta, f"{species_base}_{best_replicate_index}.html"))

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def find_response_curve_images(carpeta, species_base, ordered_variables):
    """
    Curvas de respuesta (plots/<especie>_<variable>.png), ordenadas
    según `ordered_variables` (típicamente el orden de contribución de
    variables, de mayor a menor). Excluye los "_only.png" (curvas
    marginales, no las de interés aquí -- mismo criterio del script
    original).

    Retorna
    -------
    list[str]
        Solo las imágenes que SÍ existen, en el orden de
        `ordered_variables`. Variables sin imagen simplemente no
        aparecen en la lista -- no se lanza excepción.
    """
    plots_dir = os.path.join(carpeta, "plots")
    if not os.path.isdir(plots_dir):
        return []

    all_png = glob.glob(os.path.join(plots_dir, f"{species_base}_*.png"))
    curves = {}
    prefix = species_base + "_"
    for path in all_png:
        name = os.path.basename(path)
        if name.endswith("_only.png"):
            continue
        var_name = name[len(prefix):].rsplit(".png", 1)[0] if name.startswith(prefix) else None
        if var_name:
            curves[var_name] = path

    ordered = []
    for var in ordered_variables:
        if var in curves:
            ordered.append(curves[var])
    return ordered


def find_jackknife_images(carpeta, species_base):
    """
    Imágenes de jackknife (training gain, test gain, AUC). Devuelve
    None para cualquiera que no exista -- el llamador debe registrar la
    ausencia en el log y seguir, no detener el batch (jackknife es
    opcional en la configuración de la corrida de MaxEnt).

    Retorna
    -------
    dict: {"training": str|None, "test": str|None, "auc": str|None}
    """
    plots_dir = os.path.join(carpeta, "plots")
    candidates = {
        "training": os.path.join(plots_dir, f"{species_base}_jacknife.png"),
        "test": os.path.join(plots_dir, f"{species_base}_jacknife_test.png"),
        "auc": os.path.join(plots_dir, f"{species_base}_jacknife_auc.png"),
    }
    return {key: (path if os.path.isfile(path) else None) for key, path in candidates.items()}
