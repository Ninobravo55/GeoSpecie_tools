"""
utils.py — Helpers pequeños usados por species_download_dialog.py:
resolución de iconos con fallback y validaciones de formulario.
"""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QStyle

ICONS_DIR = os.path.join(os.path.dirname(__file__), "icons")

# Mapeo icono propio -> ícono estándar de Qt como fallback si el .svg
# no está presente en icons/ (por ejemplo, si el paquete se distribuyó
# sin esa carpeta). Así el diálogo nunca falla por un ícono faltante.
_FALLBACK_STYLE_ICONS = {
    "search.svg": QStyle.StandardPixmap.SP_FileDialogContentsView,
    "download.svg": QStyle.StandardPixmap.SP_ArrowDown,
    "cancel.svg": QStyle.StandardPixmap.SP_DialogCancelButton,
    "gbif.svg": QStyle.StandardPixmap.SP_DriveNetIcon,
}


def load_icon(filename, widget_for_style=None):
    """
    Carga un ícono desde species_download/icons/<filename>. Si el archivo
    no existe, cae a un ícono estándar de Qt (QStyle) para no romper la
    UI por un recurso faltante.
    """
    path = os.path.join(ICONS_DIR, filename)
    if os.path.isfile(path):
        return QIcon(path)

    if widget_for_style is not None:
        style = widget_for_style.style()
        standard_icon = _FALLBACK_STYLE_ICONS.get(filename)
        if standard_icon is not None:
            return style.standardIcon(standard_icon)

    return QIcon()


def validate_output_name(name):
    """
    Normaliza y valida txtOutputName. Acepta solo extensiones .shp/.gpkg
    (las que exporter.py sabe escribir de forma explícita) y evita rutas
    o caracteres inválidos en el nombre de archivo.
    """
    name = (name or "").strip()
    if not name:
        return None, "El nombre de archivo de salida no puede estar vacío."

    if os.path.basename(name) != name:
        return None, "El nombre de archivo de salida no debe incluir rutas."

    valid_ext = (".shp", ".gpkg")
    if not name.lower().endswith(valid_ext):
        return None, f"La extensión debe ser una de: {', '.join(valid_ext)}."

    return name, None


def validate_year_range(year_min, year_max):
    """Valida que spYearMin <= spYearMax."""
    if year_min > year_max:
        return "El año mínimo no puede ser mayor que el año máximo."
    return None
