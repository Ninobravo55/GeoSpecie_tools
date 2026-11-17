"""
gee_climate_engine.py — Motor de procesamiento y cálculo en Google Earth Engine
para Variables Climáticas Actuales (GeoSpecies Tools).
"""

from .catalog import get_product

BAND_CONFIGS = {
    "prec": {
        "out_name": "Precipitacion_Acumulada",
        "label": "Precipitación Acumulada (mm/año)",
        "unit": "mm/año",
        "description": "Precipitación anual acumulada (promedio multianual en rangos de años)",
    },
    "tmin": {
        "out_name": "Temperatura_Minima",
        "label": "Temperatura Mínima Media (°C)",
        "unit": "°C",
        "description": "Temperatura mínima media del período",
    },
    "tmax": {
        "out_name": "Temperatura_Maxima",
        "label": "Temperatura Máxima Media (°C)",
        "unit": "°C",
        "description": "Temperatura máxima media del período",
    },
    "tmean": {
        "out_name": "Temperatura_Media",
        "label": "Temperatura Media (°C)",
        "unit": "°C",
        "description": "Temperatura media del período",
    },
    "wind": {
        "out_name": "Velocidad_Viento",
        "label": "Velocidad del Viento Media (m/s)",
        "unit": "m/s",
        "description": "Velocidad del viento media del período",
    },
    "rh": {
        "out_name": "Humedad_Relativa",
        "label": "Humedad Relativa Media (%)",
        "unit": "%",
        "description": "Humedad relativa media del período",
    },
    "solar": {
        "out_name": "Radiacion_Solar",
        "label": "Radiación Solar Media (MJ/m²/día)",
        "unit": "MJ/m²/día",
        "description": "Radiación solar media diaria del período",
    },
}


def build_climate_images(ee, product_id, year_start, year_end, selected_var_ids, aoi_ee=None):
    """
    Construye y retorna un diccionario con las imágenes individuales de GEE
    para cada variable climática seleccionada.
    
    Retorna:
    --------
    dict[str, dict]:
        {
            var_id: {
                "image": ee.Image,
                "out_name": str,
                "label": str,
                "unit": str,
                "filename": str
            }
        }
    """
    prod = get_product(product_id)
    if not prod:
        raise ValueError(f"Producto climático '{product_id}' no reconocido.")

    if year_start > year_end:
        year_start, year_end = year_end, year_start

    period_tag = f"{year_start}" if year_start == year_end else f"{year_start}_{year_end}"
    start_date = f"{year_start}-01-01"
    end_date = f"{year_end + 1}-01-01"
    years = list(range(year_start, year_end + 1))
    ee_years = ee.List(years)

    results = {}

    # =========================================================================
    # 1. ERA5-Land Daily Aggr
    # =========================================================================
    if product_id == "ERA5":
        col = ee.ImageCollection(prod["collection"])
        col_period = col.filterDate(start_date, end_date)

        for var_id in selected_var_ids:
            cfg = BAND_CONFIGS[var_id]
            out_filename = f"{cfg['out_name']}_{period_tag}.tif"

            if var_id == "prec":
                # En ERA5 total_precipitation_sum es en metros diarios
                def annual_sum_era5(y):
                    c_y = col.filter(ee.Filter.calendarRange(y, y, 'year'))
                    return c_y.select('total_precipitation_sum').sum().multiply(1000.0).toFloat().rename(cfg['out_name'])

                yearly_col = ee.ImageCollection(ee_years.map(annual_sum_era5))
                img = yearly_col.mean().toFloat().rename(cfg['out_name'])

            elif var_id == "tmin":
                img = col_period.select('temperature_2m_min').mean().subtract(273.15).toFloat().rename(cfg['out_name'])

            elif var_id == "tmax":
                img = col_period.select('temperature_2m_max').mean().subtract(273.15).toFloat().rename(cfg['out_name'])

            elif var_id == "tmean":
                img = col_period.select('temperature_2m').mean().subtract(273.15).toFloat().rename(cfg['out_name'])

            elif var_id == "wind":
                def era5_wind(i):
                    return i.select('u_component_of_wind_10m').hypot(i.select('v_component_of_wind_10m')).rename('wind')
                img = col_period.map(era5_wind).mean().toFloat().rename(cfg['out_name'])

            elif var_id == "rh":
                def era5_rh(i):
                    t = i.select('temperature_2m').subtract(273.15)
                    td = i.select('dewpoint_temperature_2m').subtract(273.15)
                    # Fórmula de Magnus-Tetens
                    top = td.multiply(17.67).divide(td.add(243.5)).exp()
                    bot = t.multiply(17.67).divide(t.add(243.5)).exp()
                    rh = top.divide(bot).multiply(100.0).clamp(0, 100).rename('rh')
                    return rh
                img = col_period.map(era5_rh).mean().toFloat().rename(cfg['out_name'])

            elif var_id == "solar":
                # surface_solar_radiation_downwards_sum está en J/m²/día -> convertimos a MJ/m²/día
                img = col_period.select('surface_solar_radiation_downwards_sum').mean().multiply(1e-6).toFloat().rename(cfg['out_name'])

            else:
                continue

            results[var_id] = {
                "image": img,
                "out_name": cfg['out_name'],
                "label": cfg['label'],
                "unit": cfg['unit'],
                "filename": out_filename,
            }

    # =========================================================================
    # 2. CHIRPS Daily
    # =========================================================================
    elif product_id == "CHIRPS":
        col = ee.ImageCollection(prod["collection"])
        for var_id in selected_var_ids:
            if var_id != "prec":
                continue
            cfg = BAND_CONFIGS["prec"]
            out_filename = f"{cfg['out_name']}_{period_tag}.tif"

            def annual_sum_chirps(y):
                c_y = col.filter(ee.Filter.calendarRange(y, y, 'year'))
                return c_y.select('precipitation').sum().toFloat().rename(cfg['out_name'])

            yearly_col = ee.ImageCollection(ee_years.map(annual_sum_chirps))
            img = yearly_col.mean().toFloat().rename(cfg['out_name'])

            results[var_id] = {
                "image": img,
                "out_name": cfg['out_name'],
                "label": cfg['label'],
                "unit": cfg['unit'],
                "filename": out_filename,
            }

    # =========================================================================
    # 3. NOAA CFSR / CFSv2
    # =========================================================================
    elif product_id == "CFR":
        col1 = ee.ImageCollection(prod["collection"])
        col2 = ee.ImageCollection(prod.get("collection_v2", "NOAA/CFSV2/FOR6H"))
        col = col1.merge(col2)
        col_period = col.filterDate(start_date, end_date)

        for var_id in selected_var_ids:
            if var_id not in BAND_CONFIGS:
                continue
            cfg = BAND_CONFIGS[var_id]
            out_filename = f"{cfg['out_name']}_{period_tag}.tif"

            if var_id == "tmin":
                img = col_period.select('Temperature_surface').min().subtract(273.15).toFloat().rename(cfg['out_name'])

            elif var_id == "tmax":
                img = col_period.select('Temperature_surface').max().subtract(273.15).toFloat().rename(cfg['out_name'])

            elif var_id == "rh":
                img = col_period.select('Relative_humidity_entire_atmosphere_single_layer').mean().toFloat().rename(cfg['out_name'])

            elif var_id == "wind":
                def cfr_wind(i):
                    return i.select('u-component_of_wind_hybrid').hypot(i.select('v-component_of_wind_hybrid')).rename('wind')
                img = col_period.map(cfr_wind).mean().toFloat().rename(cfg['out_name'])

            elif var_id == "solar":
                # Downward_Short-Wave_Radiation_Flux_surface en W/m² -> convertimos a MJ/m²/día (* 0.0864)
                img = col_period.select('Downward_Short-Wave_Radiation_Flux_surface').mean().multiply(0.0864).toFloat().rename(cfg['out_name'])

            else:
                continue

            results[var_id] = {
                "image": img,
                "out_name": cfg['out_name'],
                "label": cfg['label'],
                "unit": cfg['unit'],
                "filename": out_filename,
            }

    # =========================================================================
    # 4. NASA GPM IMERG V07
    # =========================================================================
    elif product_id == "GPM":
        col = ee.ImageCollection(prod["collection"])
        for var_id in selected_var_ids:
            if var_id != "prec":
                continue
            cfg = BAND_CONFIGS["prec"]
            out_filename = f"{cfg['out_name']}_{period_tag}.tif"

            def annual_sum_gpm(y):
                c_y = col.filter(ee.Filter.calendarRange(y, y, 'year'))
                # precipitationCal en mm/hr con pasos de 30 min (* 0.5 = mm por intervalo)
                return c_y.select('precipitationCal').sum().multiply(0.5).toFloat().rename(cfg['out_name'])

            yearly_col = ee.ImageCollection(ee_years.map(annual_sum_gpm))
            img = yearly_col.mean().toFloat().rename(cfg['out_name'])

            results[var_id] = {
                "image": img,
                "out_name": cfg['out_name'],
                "label": cfg['label'],
                "unit": cfg['unit'],
                "filename": out_filename,
            }

    # =========================================================================
    # 5. PERSIANN-CDR
    # =========================================================================
    elif product_id == "PERSIANN":
        col = ee.ImageCollection(prod["collection"])
        for var_id in selected_var_ids:
            if var_id != "prec":
                continue
            cfg = BAND_CONFIGS["prec"]
            out_filename = f"{cfg['out_name']}_{period_tag}.tif"

            def annual_sum_persiann(y):
                c_y = col.filter(ee.Filter.calendarRange(y, y, 'year'))
                return c_y.select('precipitation').sum().toFloat().rename(cfg['out_name'])

            yearly_col = ee.ImageCollection(ee_years.map(annual_sum_persiann))
            img = yearly_col.mean().toFloat().rename(cfg['out_name'])

            results[var_id] = {
                "image": img,
                "out_name": cfg['out_name'],
                "label": cfg['label'],
                "unit": cfg['unit'],
                "filename": out_filename,
            }

    # =========================================================================
    # 6. JAXA GSMaP v8
    # =========================================================================
    elif product_id == "GSMAP":
        col = ee.ImageCollection(prod["collection"])
        for var_id in selected_var_ids:
            if var_id != "prec":
                continue
            cfg = BAND_CONFIGS["prec"]
            out_filename = f"{cfg['out_name']}_{period_tag}.tif"

            def annual_sum_gsmap(y):
                c_y = col.filter(ee.Filter.calendarRange(y, y, 'year'))
                return c_y.select('hourlyPrecipRateGC').sum().toFloat().rename(cfg['out_name'])

            yearly_col = ee.ImageCollection(ee_years.map(annual_sum_gsmap))
            img = yearly_col.mean().toFloat().rename(cfg['out_name'])

            results[var_id] = {
                "image": img,
                "out_name": cfg['out_name'],
                "label": cfg['label'],
                "unit": cfg['unit'],
                "filename": out_filename,
            }

    # =========================================================================
    # 7. RAIN4PE
    # =========================================================================
    elif product_id == "RAIN4PE":
        col = ee.ImageCollection(prod["collection"])
        for var_id in selected_var_ids:
            if var_id != "prec":
                continue
            cfg = BAND_CONFIGS["prec"]
            out_filename = f"{cfg['out_name']}_{period_tag}.tif"

            def annual_sum_rain4pe(y):
                c_y = col.filter(ee.Filter.calendarRange(y, y, 'year'))
                return c_y.select('b1').sum().toFloat().rename(cfg['out_name'])

            yearly_col = ee.ImageCollection(ee_years.map(annual_sum_rain4pe))
            img = yearly_col.mean().toFloat().rename(cfg['out_name'])

            results[var_id] = {
                "image": img,
                "out_name": cfg['out_name'],
                "label": cfg['label'],
                "unit": cfg['unit'],
                "filename": out_filename,
            }

    # =========================================================================
    # 8. PISCOee pm v1.0
    # =========================================================================
    elif product_id == "PISCO":
        col = ee.ImageCollection(prod["collection"])
        for var_id in selected_var_ids:
            if var_id != "prec":
                continue
            cfg = BAND_CONFIGS["prec"]
            out_filename = f"{cfg['out_name']}_{period_tag}.tif"

            def annual_sum_pisco(y):
                c_y = col.filter(ee.Filter.calendarRange(y, y, 'year'))
                return c_y.select('pp').sum().toFloat().rename(cfg['out_name'])

            yearly_col = ee.ImageCollection(ee_years.map(annual_sum_pisco))
            img = yearly_col.mean().toFloat().rename(cfg['out_name'])

            results[var_id] = {
                "image": img,
                "out_name": cfg['out_name'],
                "label": cfg['label'],
                "unit": cfg['unit'],
                "filename": out_filename,
            }

    # =========================================================================
    # 9. PISCOp v3.0
    # =========================================================================
    elif product_id == "PISCOV3":
        col = ee.ImageCollection(prod["collection"])
        for var_id in selected_var_ids:
            if var_id != "prec":
                continue
            cfg = BAND_CONFIGS["prec"]
            out_filename = f"{cfg['out_name']}_{period_tag}.tif"

            def annual_sum_piscov3(y):
                y_str = ee.Number(y).format('%d')
                img_y = col.filter(ee.Filter.stringContains('system:index', y_str)).first()
                # Sumar todas las bandas del asset anual
                return ee.Image(img_y).reduce(ee.Reducer.sum()).toFloat().rename(cfg['out_name'])

            yearly_col = ee.ImageCollection(ee_years.map(annual_sum_piscov3))
            img = yearly_col.mean().toFloat().rename(cfg['out_name'])

            results[var_id] = {
                "image": img,
                "out_name": cfg['out_name'],
                "label": cfg['label'],
                "unit": cfg['unit'],
                "filename": out_filename,
            }

    return results
