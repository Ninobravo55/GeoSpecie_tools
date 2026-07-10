import os
from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMenu, QMessageBox
from .utils.qt_compat import dialog_exec

class GeoSpeciesToolsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.menu = 'GeoSpecies Tools'
        self.provider = None

    def tr(self, message):
        return QCoreApplication.translate('GeoSpeciesToolsPlugin', message)

    def initGui(self):
        # Register processing provider
        from .core.provider import GeoSpeciesProvider
        self.provider = GeoSpeciesProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

        # Cargar ícono principal del plugin
        icon_path = os.path.join(self.plugin_dir, 'icono.png')
        self.icon = QIcon(icon_path)

        # ── Menú principal ────────────────────────────────────────────
        self.main_menu = QMenu(self.menu, self.iface.mainWindow())
        self.main_menu.setIcon(self.icon)
        self.iface.pluginMenu().addMenu(self.main_menu)

        # ── GEE: Instalación y Autenticación ─────────────────────────
        self.action_install = QAction(self.icon, 'Instalación dependencias GEE', self.iface.mainWindow())
        self.action_install.triggered.connect(self.run_install)
        self.main_menu.addAction(self.action_install)

        self.action_auth = QAction(self.icon, 'Autenticación GEE', self.iface.mainWindow())
        self.action_auth.triggered.connect(self.run_auth)
        self.main_menu.addAction(self.action_auth)

        # ── Separador ────────────────────────────────────────────────
        self.main_menu.addSeparator()

        # ── Submenú: Variables ────────────────────────────────────────
        self.menu_variables = self.main_menu.addMenu(self.icon, '🌿  Variables')

        self.action_worldclim = QAction(self.icon, 'WorldClim BIO v1', self.iface.mainWindow())
        self.action_worldclim.setObjectName('geospecies_worldclim_bio_v1')
        self.action_worldclim.triggered.connect(self.run_worldclim_bio)
        self.menu_variables.addAction(self.action_worldclim)

        self.action_worldclim_climatology = QAction(self.icon, 'WorldClim Climatology v1', self.iface.mainWindow())
        self.action_worldclim_climatology.setObjectName('geospecies_worldclim_climatology_v1')
        self.action_worldclim_climatology.triggered.connect(self.run_worldclim_climatology)
        self.menu_variables.addAction(self.action_worldclim_climatology)

        self.action_soilgrids_isric = QAction(self.icon, 'SoilGrids 250m v2', self.iface.mainWindow())
        self.action_soilgrids_isric.setObjectName('geospecies_soilgrids_isric_v2')
        self.action_soilgrids_isric.triggered.connect(self.run_soilgrids_isric)
        self.menu_variables.addAction(self.action_soilgrids_isric)

        self.action_topografia_terreno = QAction(self.icon, 'Topografía Terreno', self.iface.mainWindow())
        self.action_topografia_terreno.setObjectName('geospecies_topografia_terreno')
        self.action_topografia_terreno.triggered.connect(self.run_topografia_terreno)
        self.menu_variables.addAction(self.action_topografia_terreno)

        self.action_climate_tiles = QAction(self.icon, 'WorldClim 2.1 Histórico y CMIP6', self.iface.mainWindow())
        self.action_climate_tiles.setObjectName('geospecies_climate_tiles')
        self.action_climate_tiles.triggered.connect(self.run_climate_tiles_download)
        self.menu_variables.addAction(self.action_climate_tiles)

        # ── Submenú: Biodiversidad ─────────────────────────────────────
        self.menu_biodiversidad = self.main_menu.addMenu(self.icon, '🐾 Biodiversidad')

        self.action_gbif_download = QAction(self.icon, 'Descargar Registros GBIF', self.iface.mainWindow())
        self.action_gbif_download.setObjectName('geospecies_gbif_download')
        self.action_gbif_download.triggered.connect(self.run_gbif_download)
        self.menu_biodiversidad.addAction(self.action_gbif_download)

        # ── Submenú: Modelamiento MaxEnt ──────────────────────────────
        self.menu_modelamiento = self.main_menu.addMenu(self.icon, '📊 Modelamiento MaxEnt')

        self.action_maxent_analysis = QAction(self.icon, 'Análisis del Resultado MaxEnt 3.4.4', self.iface.mainWindow())
        self.action_maxent_analysis.setObjectName('geospecies_maxent_analysis')
        self.action_maxent_analysis.triggered.connect(self.run_maxent_analysis)
        self.menu_modelamiento.addAction(self.action_maxent_analysis)

        # ── Submenú: Preparación de datos MaxEnt ──────────────────────
        self.menu_preparacion = self.main_menu.addMenu(self.icon, '🛠️ Preparación de datos MaxEnt')

        self.action_limpieza_especies = QAction(self.icon, 'Limpieza de especies MaxEnt', self.iface.mainWindow())
        self.action_limpieza_especies.setObjectName('geospecies_limpieza_especies_maxent')
        self.action_limpieza_especies.triggered.connect(self.run_limpieza_especies_maxent)
        self.menu_preparacion.addAction(self.action_limpieza_especies)

        self.action_uniformizar_raster = QAction(self.icon, 'Uniformizar raster ASCII', self.iface.mainWindow())
        self.action_uniformizar_raster.setObjectName('geospecies_uniformizar_raster')
        self.action_uniformizar_raster.triggered.connect(self.run_uniformizar_raster)
        self.menu_preparacion.addAction(self.action_uniformizar_raster)

        self.action_uniformizar_raster_cmip6 = QAction(self.icon, 'Uniformizar raster CMIP6 ASCII', self.iface.mainWindow())
        self.action_uniformizar_raster_cmip6.setObjectName('geospecies_uniformizar_raster_cmip6_ascii')
        self.action_uniformizar_raster_cmip6.triggered.connect(self.run_uniformizar_raster_cmip6_ascii)
        self.menu_preparacion.addAction(self.action_uniformizar_raster_cmip6)

        self.action_uniformizar_raster_worldclim = QAction(self.icon, 'Uniformizar raster WorldClim 2.1 Histórico', self.iface.mainWindow())
        self.action_uniformizar_raster_worldclim.setObjectName('geospecies_uniformizar_raster_worldclim')
        self.action_uniformizar_raster_worldclim.triggered.connect(self.run_uniformizar_raster_worldclim)
        self.menu_preparacion.addAction(self.action_uniformizar_raster_worldclim)

        # Init GEE en segundo plano si ya está autenticado
        from .utils.gee_init import background_gee_initialize
        background_gee_initialize()

    def unload(self):
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
        if self.main_menu:
            self.iface.pluginMenu().removeAction(self.main_menu.menuAction())

    # ------------------------------------------------------------------
    # Acciones GEE
    # ------------------------------------------------------------------
    def run_install(self):
        # Mapeo pkg_install_name -> import_name real
        REQUIRED = {
            'earthengine-api': 'ee',
        }
        missing, installed = [], []

        for pkg, import_name in REQUIRED.items():
            try:
                mod = __import__(import_name)
                version = getattr(mod, '__version__', 'instalado')
                installed.append(f"{pkg} (v{version})")
            except ImportError:
                missing.append(pkg)

        if not missing:
            from qgis.PyQt.QtWidgets import QMessageBox
            QMessageBox.information(
                self.iface.mainWindow(),
                "GeoSpecies Tools — Dependencias",
                "✅ Todas las dependencias ya están instaladas:\n\n" +
                "\n".join(f"  • {p}" for p in installed) +
                "\n\nNo es necesario reinstalar."
            )
            return

        from .gui.install_deps_dialog import InstallDepsDialog
        dlg = InstallDepsDialog(missing, self.iface.mainWindow(), installed_deps=installed)
        dlg.exec()



    def run_auth(self):
        try:
            import ee
        except ImportError:
            QMessageBox.critical(
                self.iface.mainWindow(), "Error",
                "earthengine-api no está instalado.\n"
                "Ejecute primero 'Instalación dependencias GEE'."
            )
            return
        from .gui.gee_auth_dialog import GEEAuthDialog
        dlg = GEEAuthDialog(self.iface.mainWindow())
        dlg.exec()

    # ------------------------------------------------------------------
    # Biodiversidad
    # ------------------------------------------------------------------
    def run_gbif_download(self):
        """Abre el diálogo standalone 'Descargar Registros GBIF'.

        No es un QgsProcessingAlgorithm: la búsqueda interactiva de
        especies (autocompletado + lista de resultados) no encaja en el
        modelo de parámetros de Processing, por lo que se implementa como
        un QDialog independiente (mismo enfoque que install_deps_dialog
        y gee_auth_dialog).
        """
        try:
            from .species_download.species_download_dialog import SpeciesDownloadDialog
            dlg = SpeciesDownloadDialog(iface=self.iface, parent=self.iface.mainWindow())
            dialog_exec(dlg)
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "GeoSpecies Tools — Error",
                f"No se pudo abrir la herramienta Descargar Registros GBIF:\n{str(e)}"
            )

    # ------------------------------------------------------------------
    # Clima
    # ------------------------------------------------------------------
    def run_climate_tiles_download(self):
        """Abre el diálogo standalone 'WorldClim 2.1 Histórico y CMIP6'
        (WorldClim histórico / CMIP6). Mismo motivo que run_gbif_download
        para no ser un QgsProcessingAlgorithm: el checklist de variables
        depende dinámicamente del modo elegido (histórico vs CMIP6)."""
        try:
            from .climate_tiles.climate_tiles_dialog import ClimateTilesDialog
            dlg = ClimateTilesDialog(iface=self.iface, parent=self.iface.mainWindow())
            dialog_exec(dlg)
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "GeoSpecies Tools — Error",
                f"No se pudo abrir la herramienta WorldClim 2.1 Histórico y CMIP6:\n{str(e)}"
            )

    # ------------------------------------------------------------------
    # Modelamiento MaxEnt
    # ------------------------------------------------------------------
    def run_maxent_analysis(self):
        """Abre el diálogo standalone 'Análisis del Resultado MaxEnt 3.4.4'."""
        try:
            from .maxent_analysis.maxent_analysis_dialog import MaxentAnalysisDialog
            dlg = MaxentAnalysisDialog(iface=self.iface, parent=self.iface.mainWindow())
            dialog_exec(dlg)
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "GeoSpecies Tools — Error",
                f"No se pudo abrir la herramienta Análisis del Resultado MaxEnt 3.4.4:\n{str(e)}"
            )

    # ------------------------------------------------------------------
    # Variables
    # ------------------------------------------------------------------
    def run_worldclim_bio(self):
        """Abre el diálogo de Processing para WorldClim BIO v1."""
        try:
            import processing
            processing.execAlgorithmDialog('geospecies:worldclim_bio_v1')
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "GeoSpecies Tools — Error",
                f"No se pudo abrir la herramienta WorldClim BIO v1:\n{str(e)}"
            )

    def run_worldclim_climatology(self):
        """Abre el diálogo de Processing para WorldClim Climatology v1."""
        try:
            import processing
            processing.execAlgorithmDialog('geospecies:worldclim_climatology_v1')
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "GeoSpecies Tools — Error",
                f"No se pudo abrir la herramienta WorldClim Climatology v1:\n{str(e)}"
            )

    def run_soilgrids_isric(self):
        """Abre el diálogo de Processing para SoilGrids 250m v2."""
        try:
            import processing
            processing.execAlgorithmDialog('geospecies:soilgrids_isric_v2')
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "GeoSpecies Tools — Error",
                f"No se pudo abrir la herramienta SoilGrids 250m v2:\n{str(e)}"
            )

    def run_topografia_terreno(self):
        """Abre el diálogo de Processing para Topografía Terreno."""
        try:
            import processing
            processing.execAlgorithmDialog('geospecies:topografia_terreno')
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "GeoSpecies Tools — Error",
                f"No se pudo abrir la herramienta Topografía Terreno:\n{str(e)}"
            )

    def run_uniformizar_raster(self):
        """Abre el diálogo de Processing para Uniformizar Raster."""
        try:
            import processing
            processing.execAlgorithmDialog('geospecies:uniformizar_raster')
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "GeoSpecies Tools — Error",
                f"No se pudo abrir la herramienta Uniformizar Raster:\n{str(e)}"
            )

    def run_uniformizar_raster_cmip6_ascii(self):
        """Abre el diálogo de Processing para Uniformizar Raster CMIP6 ASCII."""
        try:
            import processing
            processing.execAlgorithmDialog('geospecies:uniformizar_raster_cmip6_ascii')
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "GeoSpecies Tools — Error",
                f"No se pudo abrir la herramienta Uniformizar Raster CMIP6 ASCII:\n{str(e)}"
            )

    def run_uniformizar_raster_worldclim(self):
        """Abre el diálogo de Processing para Uniformizar Raster WorldClim 2.1 Histórico."""
        try:
            import processing
            processing.execAlgorithmDialog('geospecies:uniformizar_raster_worldclim')
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "GeoSpecies Tools — Error",
                f"No se pudo abrir la herramienta Uniformizar Raster WorldClim 2.1 Histórico:\n{str(e)}"
            )

    def run_limpieza_especies_maxent(self):
        """Abre el diálogo de Processing para Limpieza de especies MaxEnt."""
        try:
            import processing
            processing.execAlgorithmDialog('geospecies:limpieza_especies_maxent')
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "GeoSpecies Tools — Error",
                f"No se pudo abrir la herramienta Limpieza de especies MaxEnt:\n{str(e)}"
            )
