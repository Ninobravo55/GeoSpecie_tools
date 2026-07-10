"""
branding.py — Identidad visual institucional de Geomática Ambiental,
usada por report.py para el reporte HTML de MaxEnt.

Puro Python (solo constantes + resolución de ruta de archivo) -- sin
qgis, testeable sin QGIS instalado.
"""

import os

COLOR_AZUL = "#1E73B8"
COLOR_VERDE = "#8CC63F"
COLOR_GRIS_CLARO = "#F4F4F4"
COLOR_TEXTO = "#404040"
COLOR_FONDO = "#FFFFFF"

TITULO_PRINCIPAL = "GeoSpecies Tools — Análisis de Resultados MaxEnt"
SUBTITULO = "Reporte automático de modelamiento de distribución potencial de especies"

CONTACTO_WEB = "https://www.geomatica.pe"
CONTACTO_WEB_LABEL = "www.geomatica.pe"
CONTACTO_EMAIL = "nino@geomatica.pe"
CONTACTO_TELEFONO = "+51 995664488"
COPYRIGHT_TEXTO = "© Geomática Ambiental – Todos los derechos reservados."

FONT_STACK = "'Segoe UI', Roboto, Arial, sans-serif"

# Logo pre-procesado: el archivo original (logo3x1m-01.jpg) era un
# banner de imprenta de 8000x2666 px pensado para un formato físico de
# 3x1 m -- se redujo una sola vez a 900 px de ancho (~88 KB) para no
# embeber ese peso completo en cada reporte HTML por especie. Ver
# assets/logo_geomatica.png.
LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "logo_geomatica.png")


def logo_exists():
    return os.path.isfile(LOGO_PATH)
