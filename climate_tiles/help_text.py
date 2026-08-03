"""
help_text.py — Texto de ayuda mostrado en climate_tiles_dialog.py.

Contenido redactado a partir de la documentación pública de WorldClim
(worldclim.org/data/worldclim21.html y
worldclim.org/data/cmip6/cmip6_clim30s.html), NO copiado literalmente
(ver reglas de copyright del propio flujo de trabajo). Todo lo que dice
sobre el comportamiento de ESTA herramienta (resolución fija en 30",
26 modelos) refleja el código real de climate_tiles/, no la web.
"""

SOURCE_HISTORICAL_URL = "https://www.worldclim.org/data/worldclim21.html"
SOURCE_CMIP6_URL = "https://www.worldclim.org/data/cmip6/cmip6_clim30s.html"

SHORT_HISTORICAL_HTML = (
    "<b>Histórico (WorldClim 2.1 Baseline):</b> promedios climáticos "
    "mensuales 1970–2000. Este complemento siempre descarga a "
    "<b>30 arco-segundos</b> (~1 km² por píxel en el ecuador)."
)

SHORT_CMIP6_HTML = (
    "<b>Proyección CMIP6:</b> clima futuro downscaleado sobre la línea "
    "base de WorldClim 2.1, para 4 SSPs y 4 periodos 2021–2100. "
    "También fijo a <b>30 arco-segundos</b> en este complemento."
)

FULL_HELP_HTML = f"""
<h3>Histórico — WorldClim 2.1 Baseline</h3>
<p>
Promedios climáticos mensuales (temperatura mínima, máxima y promedio,
precipitación, radiación solar y velocidad del viento)
correspondientes al periodo <b>1970–2000</b>, más 19 variables
bioclimáticas derivadas de esos promedios. Es la línea base contra la
que se comparan las proyecciones CMIP6 -- ambos productos comparten la
misma metodología de interpolación (Fick &amp; Hijmans, 2017).
</p>
<p><b>Resolución espacial:</b> la web pública de WorldClim ofrece este
producto en 4 resoluciones angulares (10', 5', 2.5' y 30''). <b>Este
complemento descarga siempre a 30'' (~1 km² por píxel en el ecuador)</b>
-- la más fina, y la única que existe en tiles individuales de 30°x30°
(las otras 3 resoluciones son productos globales continuos, sin
tileado, y no están implementadas en esta herramienta). Si necesitas
una resolución más gruesa para trabajo a escala continental/global con
archivos más livianos, descárgala directamente desde el link de abajo.
</p>
<p><b>Fuente / descarga manual:</b>
<a href="{SOURCE_HISTORICAL_URL}">{SOURCE_HISTORICAL_URL}</a></p>

<hr>

<h3>Proyección — CMIP6</h3>
<p>
Clima futuro downscaleado y calibrado a partir de simulaciones CMIP6
(Coupled Model Intercomparison Project Phase 6), usando WorldClim 2.1
como línea base de calibración. Disponible para 4 SSP (trayectorias
socioeconómicas compartidas -- SSP126 es la de menores emisiones,
SSP585 la de mayores) y 4 periodos de 20 años, de 2021-2040 a
2081-2100.
</p>
<p><b>Modelos (GCM):</b> la página pública de WorldClim documenta un
subconjunto de 9 modelos para su propia interfaz web de descarga. <b>El
servidor de archivos real (de donde descarga este complemento) expone
26 modelos climáticos globales distintos</b> -- son fuentes distintas
con distinto alcance, no un error: el combobox "Modelo" de esta
herramienta muestra los 26, tomados directamente del listado que valida
el servidor, no de la página web pública.
</p>
<p><b>Resolución espacial:</b> igual que el histórico, la web ofrece
10'/5'/2.5'/30''. <b>Este complemento fija 30''</b> -- la resolución en
la que existen los tiles individuales por modelo/SSP/periodo.
</p>
<p><b>Combinaciones inexistentes:</b> no todos los modelos corrieron
todos los SSP/periodos. Esta herramienta valida la combinación elegida
contra el índice real del servidor antes de descargar; si no existe,
te avisa antes de intentar el archivo, no después de un error 404.
</p>
<p><b>Fuente / descarga manual:</b>
<a href="{SOURCE_CMIP6_URL}">{SOURCE_CMIP6_URL}</a></p>
"""
