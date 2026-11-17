"""COGO Workbench QGIS entry point."""


def classFactory(iface):
    from .plugin import CogoWorkbench
    return CogoWorkbench(iface)
