"""
utils.py — Helpers pequeños para climate_tiles_dialog.py.
"""

import os


def validate_name_prefix(prefix):
    """
    Normaliza y valida el prefijo de nombre de archivo (se le agrega
    "_<var>.tif" por variable descargada, ver batch.run_tile_downloads).
    """
    prefix = (prefix or "").strip()
    if not prefix:
        return None, "El prefijo de nombre de archivo no puede estar vacío."
    if os.path.basename(prefix) != prefix:
        return None, "El prefijo de nombre de archivo no debe incluir rutas."
    invalid_chars = set('<>:"/\\|?*')
    if any(c in invalid_chars for c in prefix):
        return None, f"El prefijo no puede contener ninguno de estos caracteres: {' '.join(invalid_chars)}"
    return prefix, None


def format_bytes(n):
    """Formatea bytes a texto legible (MB/GB) para la confirmación previa de tamaño."""
    if n is None:
        return "tamaño desconocido"
    mb = n / (1024 * 1024)
    if mb < 1024:
        return f"{mb:.1f} MB"
    return f"{mb / 1024:.2f} GB"
