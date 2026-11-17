from qgis.PyQt.QtCore import Qt
try:
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication


class CogoWorkbench:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None

    def initGui(self):
        self.action = QAction(QgsApplication.getThemeIcon('/mActionMeasure.svg'),
                              'COGO Workbench', self.iface.mainWindow())
        self.action.triggered.connect(self.show)
        self.iface.addPluginToVectorMenu('COGO Workbench', self.action)
        self.iface.addToolBarIcon(self.action)

    def show(self):
        if self.dock is None:
            from .dock import CogoDock
            self.dock = CogoDock(self.iface)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.show()
        self.dock.raise_()

    def unload(self):
        if self.dock is not None:
            self.dock.cleanup()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
        self.iface.removePluginVectorMenu('COGO Workbench', self.action)
        self.iface.removeToolBarIcon(self.action)
        self.action.deleteLater()
