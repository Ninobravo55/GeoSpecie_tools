"""
model.py — Lógica de procesamiento para Random Forest Niche Modeling.

Incluye:
1. Lectura y parser de CSV de presencia.
2. Lectura y apilado de rasters ambientales (.asc / .tif).
3. Muestreo de presencias y pseudo-ausencias.
4. Entrenamiento de RandomForest (con scikit-learn o fallback).
5. Evaluación de métricas (AUC, ROC, Confusión, Acc, Sens, Spec, F1).
6. Predicción espacial de probabilidad y generación de raster de salida.
"""

import csv
import logging
import math
import os
import random
import numpy as np
from osgeo import gdal, osr

logger = logging.getLogger(__name__)


class RFModelError(Exception):
    """Excepción específica para errores en el modelamiento Random Forest."""


def get_csv_headers(csv_path):
    """Retorna los encabezados de las columnas de un archivo CSV."""
    if not os.path.exists(csv_path):
        return []
    try:
        with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f)
            return [h.strip() for h in next(reader)]
    except Exception as err:
        logger.debug("Error leyendo encabezados de %s: %s", csv_path, err)
        return []


def get_unique_species(csv_path, species_col_name=None):
    """Retorna la lista de nombres únicos de especies presentes en la columna dada."""
    headers = get_csv_headers(csv_path)
    if not headers:
        return []

    sp_col = None
    if species_col_name and species_col_name in headers:
        sp_col = headers.index(species_col_name)
    else:
        norm_headers = [h.lower() for h in headers]
        for cand in ["especie", "species", "taxa", "taxon", "nombre", "sp", "scientificname"]:
            if cand in norm_headers:
                sp_col = norm_headers.index(cand)
                break

    if sp_col is None:
        return ["Especie"]

    species = set()
    try:
        with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f)
            next(reader)
            for row in reader:
                if sp_col < len(row) and row[sp_col].strip():
                    species.add(row[sp_col].strip())
    except Exception as err:
        logger.debug("Error leyendo especies de %s: %s", csv_path, err)

    return sorted(list(species)) if species else ["Especie"]


def parse_presence_csv(csv_path, species_col=None, lon_col=None, lat_col=None, target_species=None):
    """
    Lee un archivo CSV con registros de ocurrencia/presencia.
    Permite especificar explícitamente los nombres/índices de las columnas de especie, latitud y longitud,
    o las detecta automáticamente si no se indican.
    """
    if not os.path.exists(csv_path):
        raise RFModelError(f"El archivo CSV no existe: {csv_path}")

    with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f)
        try:
            header = [h.strip() for h in next(reader)]
        except StopIteration:
            raise RFModelError("El archivo CSV está vacío.")

    norm_headers = [h.lower() for h in header]

    # Resolver columna Especie
    sp_idx = None
    if species_col:
        if isinstance(species_col, int) and 0 <= species_col < len(header):
            sp_idx = species_col
        elif str(species_col) in header:
            sp_idx = header.index(str(species_col))
    if sp_idx is None:
        for cand in ["especie", "species", "taxa", "taxon", "nombre", "sp", "scientificname"]:
            if cand in norm_headers:
                sp_idx = norm_headers.index(cand)
                break

    # Resolver columna Latitud
    lat_idx = None
    if lat_col:
        if isinstance(lat_col, int) and 0 <= lat_col < len(header):
            lat_idx = lat_col
        elif str(lat_col) in header:
            lat_idx = header.index(str(lat_col))
    if lat_idx is None:
        for cand in ["lat", "latitude", "latitud", "y", "decimallatitude"]:
            if cand in norm_headers:
                lat_idx = norm_headers.index(cand)
                break

    # Resolver columna Longitud
    lon_idx = None
    if lon_col:
        if isinstance(lon_col, int) and 0 <= lon_col < len(header):
            lon_idx = lon_col
        elif str(lon_col) in header:
            lon_idx = header.index(str(lon_col))
    if lon_idx is None:
        for cand in ["lon", "long", "longitude", "log", "x", "decimallongitude"]:
            if cand in norm_headers:
                lon_idx = norm_headers.index(cand)
                break

    if lat_idx is None or lon_idx is None:
        raise RFModelError(
            "No se pudieron identificar las columnas de coordenadas (Latitud/Longitud) en el CSV.\n"
            f"Encabezados encontrados: {header}\n"
            "Por favor selecciona explícitamente las columnas de Latitud y Longitud."
        )

    species_data = {}
    with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f)
        next(reader)  # Saltar encabezado
        for row in reader:
            max_req = max(lat_idx, lon_idx)
            if sp_idx is not None:
                max_req = max(max_req, sp_idx)

            if len(row) <= max_req:
                continue

            sp_name = row[sp_idx].strip() if (sp_idx is not None and row[sp_idx].strip()) else "Especie"

            try:
                x_val = float(row[lon_idx].strip())
                y_val = float(row[lat_idx].strip())
            except ValueError:
                continue

            # Aceptar cualquier par de coordenadas numéricas (UTM en metros o Geográficas en grados)
            if sp_name not in species_data:
                species_data[sp_name] = []
            species_data[sp_name].append((x_val, y_val))

    available_species = sorted(list(species_data.keys()))
    if not available_species:
        raise RFModelError("No se encontraron coordenadas válidas en el archivo CSV.")

    if target_species and target_species in species_data:
        sel_sp = target_species
    else:
        sel_sp = available_species[0]

    coords = species_data[sel_sp]
    if len(coords) < 3:
        raise RFModelError(f"La especie '{sel_sp}' tiene muy pocos registros válidos ({len(coords)}). Se requieren al menos 3.")

    return {
        "species_data": species_data,
        "available_species": available_species,
        "selected_species": sel_sp,
        "coords": coords,
        "total_records": len(coords)
    }


def load_rasters(raster_dir):
    """
    Escanea la carpeta de rasters (.asc y .tif), verifica su alineación
    y carga sus matrices de datos.
    
    Retorna
    -------
    dict:
        {
            "names": [var_name1, ...],
            "paths": [path1, ...],
            "arrays": [2D numpy array, ...],
            "geotransform": tuple,
            "projection": str,
            "ncols": int,
            "nrows": int,
            "valid_mask": 2D boolean numpy array
        }
    """
    if not os.path.exists(raster_dir) or not os.path.isdir(raster_dir):
        raise RFModelError(f"El directorio de rasters no existe: {raster_dir}")

    # Buscar archivos .asc y .tif
    candidates = []
    for fname in os.listdir(raster_dir):
        ext = os.path.splitext(fname)[1].lower()
        if ext in [".asc", ".tif", ".tiff"]:
            candidates.append(os.path.join(raster_dir, fname))

    candidates.sort()
    if not candidates:
        raise RFModelError(f"No se encontraron archivos raster (.asc o .tif) en: {raster_dir}")

    names = []
    paths = []
    arrays = []
    nodatas = []
    ref_gt = None
    ref_proj = None
    ref_ncols = None
    ref_nrows = None

    valid_mask = None

    for filepath in candidates:
        ds = gdal.Open(filepath)
        if ds is None:
            continue

        var_name = os.path.splitext(os.path.basename(filepath))[0]
        band = ds.GetRasterBand(1)
        arr = band.ReadAsArray()
        nodata = band.GetNoDataValue()
        gt = ds.GetGeoTransform()
        proj = ds.GetProjection()
        ncols = ds.RasterXSize
        nrows = ds.RasterYSize

        if ref_ncols is None:
            ref_ncols = ncols
            ref_nrows = nrows
            ref_gt = gt
            ref_proj = proj
            valid_mask = np.ones((nrows, ncols), dtype=bool)
        else:
            # Validar dimensiones
            if ncols != ref_ncols or nrows != ref_nrows:
                raise RFModelError(
                    f"El raster '{var_name}' tiene dimensiones ({ncols}x{nrows}) "
                    f"distintas al raster de referencia ({ref_ncols}x{ref_nrows}). "
                    "Uniformiza los rasters antes de ejecutar el modelo."
                )

        # Actualizar máscara de celdas válidas (no nodata)
        if nodata is not None:
            if np.isnan(nodata):
                curr_valid = ~np.isnan(arr)
            else:
                curr_valid = (arr != nodata) & (~np.isnan(arr))
        else:
            curr_valid = ~np.isnan(arr)

        valid_mask &= curr_valid

        names.append(var_name)
        paths.append(filepath)
        arrays.append(arr)
        nodatas.append(nodata)

        ds = None

    if not names:
        raise RFModelError("No se pudo cargar ningún raster válido.")

    if not np.any(valid_mask):
        raise RFModelError("No hay celdas válidas superpuestas entre todos los rasters ambientales.")

    return {
        "names": names,
        "paths": paths,
        "arrays": arrays,
        "nodatas": nodatas,
        "geotransform": ref_gt,
        "projection": ref_proj,
        "ncols": ref_ncols,
        "nrows": ref_nrows,
        "valid_mask": valid_mask
    }


def reproject_coords(coords, src_crs_str, dst_crs_str):
    """
    Reproyecta una lista de coordenadas (x, y) desde el CRS de origen al CRS de destino usando OSR / GDAL.
    """
    if not coords or not src_crs_str or not dst_crs_str:
        return coords

    src_srs = osr.SpatialReference()
    dst_srs = osr.SpatialReference()

    try:
        if isinstance(src_crs_str, int):
            src_srs.ImportFromEPSG(src_crs_str)
        elif str(src_crs_str).upper().startswith("EPSG:"):
            code = int(str(src_crs_str).split(":")[1])
            src_srs.ImportFromEPSG(code)
        else:
            src_srs.ImportFromWkt(str(src_crs_str))

        if isinstance(dst_crs_str, int):
            dst_srs.ImportFromEPSG(dst_crs_str)
        elif str(dst_crs_str).upper().startswith("EPSG:"):
            code = int(str(dst_crs_str).split(":")[1])
            dst_srs.ImportFromEPSG(code)
        else:
            dst_srs.ImportFromWkt(str(dst_crs_str))

        try:
            src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            dst_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        except Exception as err:
            logger.debug("SetAxisMappingStrategy no soportado: %s", err)

        if src_srs.IsSame(dst_srs):
            return coords

        transform = osr.CoordinateTransformation(src_srs, dst_srs)
        reprojected = []
        for x, y in coords:
            try:
                pt = transform.TransformPoint(float(x), float(y))
                reprojected.append((pt[0], pt[1]))
            except Exception as err:
                logger.debug("Error transformando punto (%s, %s): %s", x, y, err)
                reprojected.append((x, y))
        return reprojected
    except Exception as err:
        logger.debug("Error en transformacion de coordenadas: %s", err)
        return coords


def extract_presence_values(coords, raster_info):
    """
    Extrae los valores ambientales en las coordenadas de presencia.
    Mapea directamente las coordenadas seleccionadas (ej. UTM Este/Norte o Lon/Lat).
    Si las coordenadas del CSV están en grados y el raster en UTM, realiza una conversión automática de respaldo.
    """
    gt = raster_info["geotransform"]
    ncols = raster_info["ncols"]
    nrows = raster_info["nrows"]
    valid_mask = raster_info["valid_mask"]
    arrays = raster_info["arrays"]
    raster_proj = raster_info.get("projection")

    x_origin = gt[0]
    pixel_w = gt[1]
    y_origin = gt[3]
    pixel_h = gt[5]

    def _extract_from_list(pts):
        xp = []
        vc = []
        for x_val, y_val in pts:
            col = int((x_val - x_origin) / pixel_w)
            row = int((y_val - y_origin) / pixel_h)
            if 0 <= col < ncols and 0 <= row < nrows:
                if valid_mask[row, col]:
                    vals = [arr[row, col] for arr in arrays]
                    xp.append(vals)
                    vc.append((x_val, y_val))
        return xp, vc

    # 1. Intentar extracción directa con las coordenadas del CSV (ej. Este/Norte UTM o Lon/Lat)
    X_pres, valid_coords = _extract_from_list(coords)

    # 2. Si no cayeron puntos suficientes, comprobar si las coordenadas están en grados y el raster en UTM
    if len(X_pres) < 3:
        is_csv_geo = all(-180.0 <= c[0] <= 180.0 and -90.0 <= c[1] <= 90.0 for c in coords[:20])
        is_raster_utm = abs(gt[0]) > 1000 or abs(gt[3]) > 1000
        if is_csv_geo and is_raster_utm and raster_proj:
            proc_coords = reproject_coords(coords, "EPSG:4326", raster_proj)
            X_pres, valid_coords = _extract_from_list(proc_coords)

    if len(X_pres) < 3:
        raise RFModelError(
            f"Solo {len(X_pres)} puntos de presencia cayeron dentro del área válida de los rasters.\n"
            "Verifica que las columnas seleccionadas para X e Y en el CSV coincidan con la extensión del área de tus rasters."
        )

    return np.array(X_pres, dtype=np.float32), valid_coords


def sample_pseudo_absences(raster_info, count, random_state=42):
    """
    Genera puntos de pseudo-ausencia seleccionando celdas válidas al azar.
    """
    valid_mask = raster_info["valid_mask"]
    arrays = raster_info["arrays"]

    rows, cols = np.where(valid_mask)
    n_valid = len(rows)

    if n_valid == 0:
        raise RFModelError("No hay celdas válidas para generar pseudo-ausencias.")

    num_samples = min(count, n_valid)
    rng = np.random.RandomState(random_state)
    chosen_indices = rng.choice(n_valid, size=num_samples, replace=False)

    sampled_rows = rows[chosen_indices]
    sampled_cols = cols[chosen_indices]

    X_abs = []
    for r, c in zip(sampled_rows, sampled_cols):
        vals = [arr[r, c] for arr in arrays]
        X_abs.append(vals)

    return np.array(X_abs, dtype=np.float32)


def train_and_evaluate_rf(
    X_pres, X_abs, var_names,
    n_estimators=100, max_depth=None, min_samples_leaf=1,
    test_size=0.25, random_state=42
):
    """
    Combina presencias y pseudo-ausencias, entrena RandomForestClassifier,
    calcula matriz de confusión, métricas (AUC, Acc, Sens, Spec, F1) e importancias.
    """
    n_pres = len(X_pres)
    n_abs = len(X_abs)

    y_pres = np.ones(n_pres, dtype=np.int32)
    y_abs = np.zeros(n_abs, dtype=np.int32)

    X_full = np.vstack([X_pres, X_abs])
    y_full = np.concatenate([y_pres, y_abs])

    # Barajar y dividir en Train / Test
    rng = np.random.RandomState(random_state)
    indices = np.arange(len(y_full))
    rng.shuffle(indices)

    X_full = X_full[indices]
    y_full = y_full[indices]

    n_test = int(len(y_full) * (test_size / 100.0))
    if n_test < 2:
        n_test = 2

    X_train = X_full[:-n_test]
    y_train = y_full[:-n_test]
    X_test = X_full[-n_test:]
    y_test = y_full[-n_test:]

    # Intentar usar sklearn.ensemble.RandomForestClassifier
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import roc_curve, auc

        depth_val = None if (max_depth is None or max_depth <= 0) else int(max_depth)
        clf = RandomForestClassifier(
            n_estimators=int(n_estimators),
            max_depth=depth_val,
            min_samples_leaf=int(min_samples_leaf),
            random_state=int(random_state),
            n_jobs=-1
        )
        clf.fit(X_train, y_train)

        y_prob = clf.predict_proba(X_test)[:, 1]
        y_pred = (y_prob >= 0.5).astype(int)

        fpr, tpr, thresholds = roc_curve(y_test, y_prob)
        auc_score = float(auc(fpr, tpr))
        importances = list(clf.feature_importances_)

    except ImportError:
        # Fallback puro Python/NumPy en caso de no tener scikit-learn instalado
        clf, y_prob, y_pred, fpr, tpr, auc_score, importances = _fallback_random_forest(
            X_train, y_train, X_test, y_test,
            n_estimators=n_estimators, max_depth=max_depth,
            min_samples_leaf=min_samples_leaf, random_state=random_state
        )

    # Matriz de confusión
    tp = int(np.sum((y_test == 1) & (y_pred == 1)))
    tn = int(np.sum((y_test == 0) & (y_pred == 0)))
    fp = int(np.sum((y_test == 0) & (y_pred == 1)))
    fn = int(np.sum((y_test == 1) & (y_pred == 0)))

    total_test = len(y_test)
    accuracy = float((tp + tn) / total_test) if total_test > 0 else 0.0
    sensitivity = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    specificity = float(tn / (tn + fp)) if (tn + fp) > 0 else 0.0
    precision = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    f1_score = float(2 * precision * sensitivity / (precision + sensitivity)) if (precision + sensitivity) > 0 else 0.0

    # Normalizar importancias a porcentaje (%)
    sum_imp = sum(importances)
    if sum_imp > 0:
        imp_percent = [(v / sum_imp) * 100.0 for v in importances]
    else:
        imp_percent = [100.0 / len(var_names)] * len(var_names)

    # Ordenar variables por importancia descendente
    var_imp_pairs = sorted(zip(var_names, imp_percent), key=lambda x: x[1], reverse=True)

    return {
        "model": clf,
        "n_presences": n_pres,
        "n_absences": n_abs,
        "n_train": len(y_train),
        "n_test": len(y_test),
        "y_test": y_test,
        "y_prob": y_prob,
        "fpr": fpr,
        "tpr": tpr,
        "auc": auc_score,
        "accuracy": accuracy,
        "sensitivity": sensitivity,
        "specificity": specificity,
        "precision": precision,
        "f1_score": f1_score,
        "confusion_matrix": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
        "var_importances": var_imp_pairs
    }


class FallbackRFModel:
    def __init__(self, trees):
        self.trees = trees

    def predict_proba(self, X):
        y_prob = np.zeros(len(X))
        for best_feat, thresh, direction in self.trees:
            if direction == ">=":
                y_prob += (X[:, best_feat] >= thresh).astype(float)
            else:
                y_prob += (X[:, best_feat] < thresh).astype(float)
        if len(self.trees) > 0:
            y_prob /= len(self.trees)
        
        probs = np.zeros((len(X), 2))
        probs[:, 1] = y_prob
        probs[:, 0] = 1.0 - y_prob
        return probs


def _fallback_random_forest(X_train, y_train, X_test, y_test, n_estimators=50, max_depth=10, min_samples_leaf=1, random_state=42):
    """
    Fallback ligero basado en Decision Trees ensamblados usando NumPy si scikit-learn no estuviese instalado.
    """
    rng = np.random.RandomState(random_state)
    n_samples, n_features = X_train.shape
    trees = []
    feature_counts = np.zeros(n_features)

    # Simple Random Forest con Bagging
    y_prob = np.zeros(len(X_test))

    for i in range(min(n_estimators, 50)):
        sample_indices = rng.choice(n_samples, size=n_samples, replace=True)
        feat_indices = rng.choice(n_features, size=max(1, int(math.sqrt(n_features))), replace=False)
        for f in feat_indices:
            feature_counts[f] += 1

        # Promedio ponderado simple de la característica más correlacionada
        mean_pres = np.mean(X_train[sample_indices][y_train[sample_indices] == 1], axis=0)
        mean_abs = np.mean(X_train[sample_indices][y_train[sample_indices] == 0], axis=0)
        diff = np.abs(mean_pres - mean_abs)
        best_feat = np.argmax(diff)
        thresh = (mean_pres[best_feat] + mean_abs[best_feat]) / 2.0

        if mean_pres[best_feat] >= mean_abs[best_feat]:
            pred_prob = (X_test[:, best_feat] >= thresh).astype(float)
            trees.append((best_feat, thresh, ">="))
        else:
            pred_prob = (X_test[:, best_feat] < thresh).astype(float)
            trees.append((best_feat, thresh, "<"))

        y_prob += pred_prob

    y_prob /= min(n_estimators, 50)
    y_pred = (y_prob >= 0.5).astype(int)

    # Calcular AUC trapezoidal
    order = np.argsort(-y_prob)
    y_test_sorted = y_test[order]
    tpr = np.cumsum(y_test_sorted) / max(1, np.sum(y_test))
    fpr = np.cumsum(1 - y_test_sorted) / max(1, np.sum(1 - y_test))

    _trapz = getattr(np, 'trapezoid', None) or np.trapz
    auc_val = float(_trapz(tpr, fpr))
    importances = (feature_counts / np.sum(feature_counts)).tolist()

    fallback_model = FallbackRFModel(trees)

    return fallback_model, y_prob, y_pred, fpr, tpr, abs(auc_val), importances


def predict_spatial_raster(model_dict, raster_info, output_dir, species_name="Especie"):
    """
    Aplica el modelo entrenado a toda la grilla de rasters ambientales
    y genera el raster de probabilidad predicho en formato GeoTIFF y ASCII.
    """
    os.makedirs(output_dir, exist_ok=True)
    clf = model_dict["model"]
    valid_mask = raster_info["valid_mask"]
    arrays = raster_info["arrays"]
    nrows = raster_info["nrows"]
    ncols = raster_info["ncols"]
    gt = raster_info["geotransform"]
    proj = raster_info["projection"]

    clean_sp_name = "".join(c if c.isalnum() else "_" for c in species_name)
    tif_filename = f"random_forest_{clean_sp_name}_prediccion.tif"
    asc_filename = f"random_forest_{clean_sp_name}_prediccion.asc"

    tif_path = os.path.join(output_dir, tif_filename)
    asc_path = os.path.join(output_dir, asc_filename)

    # Crear matriz vacía de predicción inicializada en nodata (-9999)
    pred_grid = np.full((nrows, ncols), -9999.0, dtype=np.float32)

    # Extraer celdas válidas
    rows, cols = np.where(valid_mask)
    if len(rows) > 0:
        # Construir matriz de características de entrada
        X_grid = np.column_stack([arr[rows, cols] for arr in arrays])

        if clf is not None:
            # Predicción con sklearn RandomForestClassifier o el FallbackRFModel
            probabilities = clf.predict_proba(X_grid)[:, 1]
        else:
            # Fallback simple
            probabilities = np.ones(len(rows)) * 0.5

        pred_grid[rows, cols] = probabilities.astype(np.float32)

    # 1. Guardar como GeoTIFF
    driver_tif = gdal.GetDriverByName("GTiff")
    ds_out = driver_tif.Create(
        tif_path, ncols, nrows, 1, gdal.GDT_Float32,
        options=["COMPRESS=LZW", "TILED=YES"]
    )
    ds_out.SetGeoTransform(gt)
    if proj:
        ds_out.SetProjection(proj)
    band_out = ds_out.GetRasterBand(1)
    band_out.SetNoDataValue(-9999.0)
    band_out.WriteArray(pred_grid)
    ds_out.FlushCache()
    ds_out = None

    # 2. Guardar como ASCII Grid (.asc) usando CreateCopy desde el GeoTIFF
    ds_tif = gdal.Open(tif_path, gdal.GA_ReadOnly)
    driver_asc = gdal.GetDriverByName("AAIGrid")
    ds_asc = driver_asc.CreateCopy(asc_path, ds_tif, strict=0)
    ds_asc.FlushCache()
    ds_asc = None
    ds_tif = None

    return {
        "tif_path": tif_path,
        "asc_path": asc_path
    }
