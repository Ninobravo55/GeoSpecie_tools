"""
current_climate — Módulo de descarga y procesamiento de Variables Climáticas Actuales (GEE)
para GeoSpecies Tools.
"""

from .catalog import CLIMATIC_PRODUCTS, get_product
from .gee_climate_engine import build_climate_images, BAND_CONFIGS

try:
    from .algorithm import VariablesClimaticasActualesAlgorithm
except ImportError:
    VariablesClimaticasActualesAlgorithm = None

try:
    from .dialog import ClimaticosActualesDialog
except ImportError:
    ClimaticosActualesDialog = None

__all__ = [
    "CLIMATIC_PRODUCTS",
    "get_product",
    "build_climate_images",
    "BAND_CONFIGS",
    "VariablesClimaticasActualesAlgorithm",
    "ClimaticosActualesDialog",
]
