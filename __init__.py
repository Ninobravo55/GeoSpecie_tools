def classFactory(iface):
    from .plugin import GeoSpeciesToolsPlugin
    return GeoSpeciesToolsPlugin(iface)
