"""
raster_io.py — Lectura, muestreo y exportación de rásters ASCII (.asc) para
la herramienta "Selección de Variables".

Usa únicamente GDAL y numpy, ambos incluidos de fábrica en QGIS, sin
dependencias externas (ni R, ni scipy, ni rasterio).

Responsabilidades:
1. Descubrir los .asc de un directorio y verificar que estén alineados
   (misma grilla), condición indispensable para comparar píxel a píxel.
2. Construir la matriz de observaciones (píxeles válidos x variables),
   descartando los NoData, con opción de submuestreo regular.
3. Exportar las variables seleccionadas como ASCII Grid a la carpeta
   'Select_variable', preservando georreferenciación y NoData.
"""

import logging
import os

import numpy as np
from osgeo import gdal

gdal.UseExceptions()

logger = logging.getLogger(__name__)

ASCII_EXTENSIONS = (".asc",)


class RasterIOError(Exception):
    """Errores de lectura/escritura de rásters en la herramienta."""


# ---------------------------------------------------------------------------
# 1. Descubrimiento y carga
# ---------------------------------------------------------------------------

def list_ascii_rasters(directory):
    """Retorna la lista ordenada de rutas .asc del directorio indicado."""
    if not directory or not os.path.isdir(directory):
        raise RasterIOError(f"El directorio de entrada no existe: {directory}")

    files = [
        os.path.join(directory, fn)
        for fn in os.listdir(directory)
        if os.path.splitext(fn)[1].lower() in ASCII_EXTENSIONS
    ]
    files.sort()
    return files


def load_stack(directory, progress_cb=None):
    """
    Carga todos los .asc del directorio como un "stack" alineado.

    Verifica que todas las capas compartan dimensiones y geotransformación.
    Si alguna difiere, lanza RasterIOError indicando cuál: continuar con
    grillas distintas produciría una matriz de correlación sin sentido.

    Retorna
    -------
    dict con:
        names        : [nombres de variable, ...]  (nombre de archivo sin extensión)
        paths        : [rutas, ...]
        arrays       : [ndarray 2D float64 con NaN en NoData, ...]
        geotransform : tupla GDAL
        projection   : WKT (puede ser cadena vacía si el .asc no trae .prj)
        ncols, nrows : int
        nodata       : valor NoData de referencia
        valid_mask   : ndarray 2D booleano — píxeles válidos en TODAS las capas
    """
    paths = list_ascii_rasters(directory)
    if len(paths) < 2:
        raise RasterIOError(
            f"Se requieren al menos 2 archivos .asc para analizar colinealidad. "
            f"Encontrados: {len(paths)} en {directory}"
        )

    names, arrays = [], []
    ref_gt = ref_proj = None
    ref_ncols = ref_nrows = None
    ref_nodata = None
    valid_mask = None

    for i, path in enumerate(paths):
        if progress_cb:
            progress_cb(f"Leyendo {os.path.basename(path)} ({i + 1}/{len(paths)})")

        try:
            ds = gdal.Open(path)
        except RuntimeError as err:
            raise RasterIOError(f"No se pudo abrir el ráster {os.path.basename(path)}: {err}")
        if ds is None:
            raise RasterIOError(f"No se pudo abrir el ráster: {path}")

        band = ds.GetRasterBand(1)
        arr = band.ReadAsArray().astype(np.float64)
        nodata = band.GetNoDataValue()
        gt = ds.GetGeoTransform()
        proj = ds.GetProjection()
        ncols, nrows = ds.RasterXSize, ds.RasterYSize
        ds = None

        if ref_ncols is None:
            ref_ncols, ref_nrows = ncols, nrows
            ref_gt, ref_proj, ref_nodata = gt, proj, nodata
        else:
            if (ncols, nrows) != (ref_ncols, ref_nrows):
                raise RasterIOError(
                    f"El ráster '{os.path.basename(path)}' tiene dimensiones "
                    f"{ncols}x{nrows}, distintas de {ref_ncols}x{ref_nrows} del resto. "
                    "Uniformiza los rásters antes de ejecutar esta herramienta "
                    "(menú 'Preparación de datos MaxEnt' → 'Uniformizar raster ASCII')."
                )
            if any(abs(a - b) > 1e-9 for a, b in zip(gt, ref_gt)):
                raise RasterIOError(
                    f"El ráster '{os.path.basename(path)}' no está alineado con el resto "
                    "(geotransformación distinta). Uniformiza los rásters antes de continuar."
                )

        # Marcar NoData como NaN
        if nodata is not None:
            arr[arr == nodata] = np.nan
        arr[~np.isfinite(arr)] = np.nan

        layer_valid = np.isfinite(arr)
        valid_mask = layer_valid if valid_mask is None else (valid_mask & layer_valid)

        names.append(os.path.splitext(os.path.basename(path))[0])
        arrays.append(arr)

    n_valid = int(valid_mask.sum())
    if n_valid < 10:
        raise RasterIOError(
            f"Solo hay {n_valid} píxeles válidos comunes a todas las capas. "
            "Verifica que los rásters cubran la misma área y que el valor NoData sea correcto."
        )

    return {
        "names": names,
        "paths": paths,
        "arrays": arrays,
        "geotransform": ref_gt,
        "projection": ref_proj,
        "ncols": ref_ncols,
        "nrows": ref_nrows,
        "nodata": ref_nodata if ref_nodata is not None else -9999.0,
        "valid_mask": valid_mask,
    }


# ---------------------------------------------------------------------------
# 2. Construcción de la matriz de observaciones
# ---------------------------------------------------------------------------

def build_sample_matrix(stack, sample_points=True, nb_points=10000, seed=42):
    """
    Construye la matriz (n_observaciones x n_variables) con los valores de
    los píxeles válidos en todas las capas.

    Con `sample_points=True` se toma una submuestra aleatoria de tamaño
    `nb_points`. Con rásters grandes esto es lo que hace la diferencia entre
    segundos y minutos, sobre todo con Kendall; el efecto sobre el
    coeficiente de correlación es despreciable a partir de unos miles de
    puntos.

    Retorna (matriz, n_pixeles_usados).
    """
    valid_mask = stack["valid_mask"]
    idx = np.flatnonzero(valid_mask.ravel())
    n_valid = idx.size

    if sample_points and nb_points and nb_points < n_valid:
        rng = np.random.default_rng(seed)
        idx = rng.choice(idx, size=int(nb_points), replace=False)
        idx.sort()

    cols = [arr.ravel()[idx] for arr in stack["arrays"]]
    matrix = np.column_stack(cols)

    # Salvaguarda: descartar filas con cualquier NaN residual
    finite_rows = np.all(np.isfinite(matrix), axis=1)
    matrix = matrix[finite_rows]

    if matrix.shape[0] < 10:
        raise RasterIOError(
            "No hay suficientes observaciones válidas para calcular correlaciones."
        )

    return matrix, matrix.shape[0]


# ---------------------------------------------------------------------------
# 3. Exportación
# ---------------------------------------------------------------------------

def export_selected(stack, selected_names, output_dir, prefix="Select_",
                    progress_cb=None):
    """
    Exporta las variables seleccionadas como ASCII Grid (.asc) en la
    subcarpeta 'Select_variable' de `output_dir`.

    Se escribe primero en memoria (driver MEM) y luego se traduce con el
    driver AAIGrid, porque AAIGrid es de solo-copia (CreateCopy) y no
    admite Create() directo.

    Retorna la lista de rutas escritas.
    """
    select_dir = os.path.join(output_dir, "Select_variable")
    os.makedirs(select_dir, exist_ok=True)

    name_to_idx = {n: i for i, n in enumerate(stack["names"])}
    nodata = float(stack["nodata"])
    written = []

    mem_driver = gdal.GetDriverByName("MEM")
    asc_driver = gdal.GetDriverByName("AAIGrid")
    if asc_driver is None:
        raise RasterIOError("El driver AAIGrid de GDAL no está disponible en esta instalación.")

    for i, var_name in enumerate(selected_names):
        if var_name not in name_to_idx:
            raise RasterIOError(f"La variable seleccionada '{var_name}' no está en el stack.")

        if progress_cb:
            progress_cb(f"Exportando {prefix}{var_name}.asc ({i + 1}/{len(selected_names)})")

        arr = stack["arrays"][name_to_idx[var_name]].copy()
        arr[~np.isfinite(arr)] = nodata

        mem_ds = mem_driver.Create("", stack["ncols"], stack["nrows"], 1, gdal.GDT_Float32)
        mem_ds.SetGeoTransform(stack["geotransform"])
        if stack["projection"]:
            mem_ds.SetProjection(stack["projection"])
        band = mem_ds.GetRasterBand(1)
        band.WriteArray(arr.astype(np.float32))
        band.SetNoDataValue(nodata)
        band.FlushCache()

        out_path = os.path.join(select_dir, f"{prefix}{var_name}.asc")
        try:
            out_ds = asc_driver.CreateCopy(
                out_path, mem_ds, strict=0, options=["DECIMAL_PRECISION=6", "FORCE_CELLSIZE=TRUE"]
            )
        except RuntimeError as err:
            mem_ds = None
            raise RasterIOError(f"No se pudo escribir {out_path}: {err}")

        out_ds = None
        mem_ds = None
        written.append(out_path)

    return written
