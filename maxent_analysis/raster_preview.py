"""
raster_preview.py — Genera una imagen PNG de vista previa de un raster
(continuo, binario o clasificado por categorías), para poder mostrar
"Distribución potencial" y "Clasificación del hábitat" como imágenes en
el reporte HTML (el reporte no puede embeber un GeoTIFF directamente).

render_array_preview() es pura matplotlib/numpy -- testeable sin GDAL.
render_raster_preview() es el wrapper que sí necesita GDAL (disponible
dentro de QGIS, pero no en este sandbox de desarrollo) -- se mantiene
deliberadamente delgado (solo lee el array y delega el resto), mismo
criterio que aoi.py/geom.py en los otros módulos del plugin.
"""

import logging
logging.getLogger("matplotlib").setLevel(logging.WARNING)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch


class RasterPreviewError(Exception):
    """Error al generar la vista previa de un raster."""


def render_array_preview(array, output_path, title=None, cmap="YlGnBu",
                          discrete_classes=None, nodata_value=None, figsize=(8, 6)):
    """
    Renderiza `array` (numpy 2D, banda ya leída de un raster) a un PNG.

    Parámetros
    ----------
    discrete_classes : list[dict] opcional
        Mismo formato que catalog.HABITAT_CLASSES
        ({"value":int,"label":str,"color":str,...}) -- si se pasa, se
        renderiza como mapa categórico con leyenda de colores fijos
        (para la clasificación de hábitat). Si es None, se usa un
        colormap continuo con barra de color (para el raster de
        probabilidad/binario).
    nodata_value : float opcional
        Valor a enmascarar (transparente) en el render.

    Retorna
    -------
    str
        `output_path`.
    """
    import numpy as np

    arr = np.array(array, dtype=float)
    if nodata_value is not None:
        arr = np.ma.masked_equal(arr, nodata_value)

    with plt.rc_context({"font.family": "DejaVu Sans"}):
        fig, ax = plt.subplots(figsize=figsize)

        if discrete_classes:
            values = [c["value"] for c in discrete_classes]
            colors = [c["color"] for c in discrete_classes]
            listed_cmap = ListedColormap(colors)
            bounds = [v - 0.5 for v in values] + [values[-1] + 0.5]
            norm = BoundaryNorm(bounds, listed_cmap.N)
            ax.imshow(arr, cmap=listed_cmap, norm=norm)
            legend_patches = [
                Patch(facecolor=c["color"], edgecolor="#999999", label=c["label"])
                for c in discrete_classes
            ]
            ax.legend(handles=legend_patches, loc="upper center", bbox_to_anchor=(0.5, -0.03),
                      ncol=2, fontsize=9, frameon=False)
        else:
            im = ax.imshow(arr, cmap=cmap)
            fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)

        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        if title:
            ax.set_title(title, fontsize=13, fontweight="bold", color="#404040")

        try:
            fig.savefig(output_path, dpi=200, bbox_inches="tight")
        except Exception as exc:  # noqa: BLE001 - matplotlib puede fallar por permisos/disco
            raise RasterPreviewError(f"No se pudo guardar la vista previa en {output_path}: {exc}") from exc
        finally:
            plt.close(fig)

    return output_path


def render_raster_preview(raster_path, output_path, **kwargs):
    """
    Wrapper delgado: lee la banda 1 de `raster_path` con GDAL y delega
    el render a render_array_preview(). No testeado en este sandbox
    (GDAL no está disponible aquí) -- verificar dentro de QGIS real.
    """
    from osgeo import gdal

    ds = gdal.Open(raster_path)
    if ds is None:
        raise RasterPreviewError(f"No se pudo abrir el raster con GDAL: {raster_path}")

    band = ds.GetRasterBand(1)
    array = band.ReadAsArray()
    nodata = band.GetNoDataValue()
    ds = None  # liberar el dataset explícitamente

    return render_array_preview(array, output_path, nodata_value=nodata, **kwargs)
