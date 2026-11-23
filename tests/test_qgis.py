"""Run with the QGIS Python launcher, QT_QPA_PLATFORM=offscreen."""
import math
from pathlib import Path
import unittest

from qgis.PyQt.QtCore import QVariant
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsField,
                       QgsGeometry, QgsProject, QgsVectorFileWriter, QgsVectorLayer)
from qgis.gui import QgsMapCanvas

from cogo_workbench.engine import Course, traverse
from cogo_workbench.layers import line_layer, measured_values, update_selected


APP = QgsApplication.instance() or QgsApplication([], False)
APP.initQgis()


class LayerTests(unittest.TestCase):
    def setUp(self):
        self.crs = QgsCoordinateReferenceSystem('EPSG:26917')

    def make_layer(self):
        courses = [Course('Line', 'N45E', '100'), Course('Curve', 'N', '270', '20', 'Delta')]
        segments = traverse((500000, 1000000), courses, 0.3048, 2)
        return line_layer(self.crs, segments, courses, 0.3048, 2, 'International feet')

    def test_curved_output_and_inverse(self):
        layer = self.make_layer()
        self.assertTrue(layer.isValid())
        self.assertEqual(layer.featureCount(), 2)
        features = list(layer.getFeatures())
        self.assertAlmostEqual(features[0]['distance'], 100, places=7)
        self.assertAlmostEqual(features[0]['direction'], 45, places=7)
        self.assertAlmostEqual(features[1]['radius'], 20, places=7)
        self.assertAlmostEqual(features[1]['arclength'], math.pi * 30, places=7)
        self.assertEqual(features[1]['side'], 'R')

    def test_update_undo_and_preserve_edits(self):
        layer = self.make_layer()
        fid = next(layer.getFeatures()).id()
        layer.startEditing()
        layer.changeAttributeValue(fid, layer.fields().indexFromName('record_dir'), 'keep me')
        layer.selectByIds([fid])
        self.assertEqual(update_selected(layer, 1, 0, 'CRS units'), 1)
        self.assertAlmostEqual(layer.getFeature(fid)['direction'], 47)
        self.assertEqual(layer.getFeature(fid)['record_dir'], 'keep me')
        layer.undoStack().undo()
        self.assertAlmostEqual(layer.getFeature(fid)['direction'], 45)
        self.assertEqual(layer.getFeature(fid)['record_dir'], 'keep me')

    def test_add_fields_and_fill_null(self):
        layer = QgsVectorLayer('LineString?crs=EPSG:26917', 'test', 'memory')
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromWkt('LINESTRING(0 0, 100 0)'))
        layer.dataProvider().addFeatures([feature])
        fid = next(layer.getFeatures()).id()
        layer.selectByIds([fid])
        update_selected(layer, 1, 0, 'CRS units')
        self.assertTrue(layer.isEditable())
        self.assertAlmostEqual(layer.getFeature(fid)['direction'], 90)
        update_selected(layer, 2, 0, 'Meters', True)
        self.assertAlmostEqual(layer.getFeature(fid)['distance'], 100)
        layer.rollBack()
        self.assertEqual(len(layer.fields()), 0)

    def test_reject_multicourse_before_edits(self):
        layer = QgsVectorLayer('LineString?crs=EPSG:26917', 'test', 'memory')
        features = []
        for wkt in ['LINESTRING(0 0, 100 0)', 'LINESTRING(0 0, 1 1, 2 1)']:
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromWkt(wkt))
            features.append(feature)
        layer.dataProvider().addFeatures(features)
        layer.selectAll()
        with self.assertRaises(ValueError):
            update_selected(layer, 1, 0, 'CRS units')
        self.assertFalse(layer.isEditable())
        self.assertEqual(len(layer.fields()), 0)

    def test_existing_field_case_and_type(self):
        layer = QgsVectorLayer('LineString?crs=EPSG:26917', 'test', 'memory')
        layer.dataProvider().addAttributes([QgsField('Direction', QVariant.Double)])
        layer.updateFields()
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt('LINESTRING(0 0, 100 0)'))
        layer.dataProvider().addFeatures([feature])
        layer.selectAll()
        update_selected(layer, 1, 0, 'CRS units')
        self.assertAlmostEqual(next(layer.getFeatures())['Direction'], 90)
        self.assertNotIn('direction', layer.fields().names())
        layer.rollBack()
        layer = QgsVectorLayer('LineString?crs=EPSG:26917&field=distance:string', 'test', 'memory')
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt('LINESTRING(0 0, 100 0)'))
        layer.dataProvider().addFeatures([feature])
        layer.selectAll()
        with self.assertRaisesRegex(ValueError, 'incompatible type'):
            update_selected(layer, 1, 0, 'CRS units')
        self.assertFalse(layer.isEditable())

    def test_geopackage_roundtrip(self):
        layer = self.make_layer()
        artifacts = Path(__file__).resolve().parent.parent / 'artifacts'
        artifacts.mkdir(exist_ok=True)
        path = str(artifacts / 'roundtrip.gpkg')
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = 'GPKG'
        result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path,
            QgsProject.instance().transformContext(), options)
        self.assertEqual(result[0], QgsVectorFileWriter.NoError, result)
        saved = QgsVectorLayer(path, 'saved', 'ogr')
        self.assertTrue(saved.isValid())
        self.assertEqual(saved.featureCount(), 2)
        curve = list(saved.getFeatures())[1]
        values = measured_values(curve.geometry(), 0.3048, 2)
        self.assertAlmostEqual(values[2], 20, places=7)
        self.assertAlmostEqual(values[4], 270, places=7)

    def test_plugin_entry_point(self):
        from cogo_workbench import classFactory
        from qgis.PyQt.QtWidgets import QMainWindow
        window = QMainWindow()
        canvas = QgsMapCanvas()
        calls = []

        class Iface:
            def mainWindow(self): return window
            def mapCanvas(self): return canvas
            def addPluginToVectorMenu(self, *args): calls.append('menu')
            def addToolBarIcon(self, *args): calls.append('toolbar')
            def removePluginVectorMenu(self, *args): calls.append('remove menu')
            def removeToolBarIcon(self, *args): calls.append('remove toolbar')
            def addDockWidget(self, area, dock): window.addDockWidget(area, dock)
            def removeDockWidget(self, dock): window.removeDockWidget(dock)

        plugin = classFactory(Iface())
        plugin.initGui()
        plugin.show()
        self.assertIsNotNone(plugin.dock)
        plugin.unload()
        self.assertEqual(calls, ['menu', 'toolbar', 'remove menu', 'remove toolbar'])
        window.deleteLater()
        canvas.deleteLater()

    def test_dock_outputs_and_lifecycle(self):
        from cogo_workbench.dock import CogoDock
        canvas = QgsMapCanvas()

        class Iface:
            def mainWindow(self):
                return None

            def mapCanvas(self):
                return canvas

        dock = CogoDock(Iface())
        dock.crs.setCrs(self.crs)
        dock.table.setRowCount(0)
        for direction in ['N', 'E', 'S', 'W']:
            dock.add_course(Course('Line', direction, '100'))
        dock.preview()
        dock.create_lines()
        dock.create_polygon()
        dock.create_points()
        layers = list(QgsProject.instance().mapLayers().values())
        self.assertEqual(len(layers), 3)
        polygon = next(layer for layer in layers if layer.name() == 'COGO parcel')
        self.assertAlmostEqual(next(polygon.getFeatures()).geometry().area(), 10000)
        dock.table.setRowCount(0)
        dock.add_course(Course('Curve', 'N', '180', '100', 'Delta'))
        dock.add_course(Course('Line', 'W', '200'))
        dock.create_polygon()
        parcels = [layer for layer in QgsProject.instance().mapLayers().values()
                   if layer.name() == 'COGO parcel']
        self.assertTrue(any(abs(next(layer.getFeatures()).geometry().area() - math.pi * 5000) < 1e-6
                            for layer in parcels))
        dock.cleanup()
        dock.deleteLater()
        QgsProject.instance().removeAllMapLayers()
        canvas.deleteLater()

    def test_parcel_shapes_build_polygons_and_corners(self):
        from cogo_workbench.dock import CogoDock
        from test_shapes import CURVED, STRAIGHT, lines_through, ring_area
        canvas = QgsMapCanvas()

        class Iface:
            def mainWindow(self):
                return None

            def mapCanvas(self):
                return canvas

        dock = CogoDock(Iface())
        dock.crs.setCrs(self.crs)
        dock.x.setText('0')
        dock.y.setText('0')
        shapes = [(name, lines_through(points), ring_area(points), len(points))
                  for name, (points, _corners) in STRAIGHT.items()]
        shapes.extend((name, courses, area, corners) for name, (courses, area, corners) in CURVED.items())
        shapes.append(('state-plane clockwise square', lines_through([
            (500000, 1000000), (500100, 1000000), (500100, 999900), (500000, 999900)]), 10000, 4))
        for name, courses, area, corners in shapes:
            with self.subTest(name=name):
                QgsProject.instance().removeAllMapLayers()
                dock.table.setRowCount(0)
                if name.startswith('state-plane'):
                    dock.x.setText('500000')
                    dock.y.setText('1000000')
                else:
                    dock.x.setText('0')
                    dock.y.setText('0')
                for course in courses:
                    dock.add_course(course)
                dock.create_polygon()
                dock.create_points()
                layers = list(QgsProject.instance().mapLayers().values())
                polygon = next(layer for layer in layers if layer.name() == 'COGO parcel')
                points = next(layer for layer in layers if layer.name() == 'COGO corners')
                feature = next(polygon.getFeatures())
                self.assertAlmostEqual(feature.geometry().area(), area, places=4)
                self.assertAlmostEqual(feature['area_grid'], area, places=4)
                self.assertEqual(points.featureCount(), corners)
                self.assertTrue(feature.geometry().isGeosValid())
        QgsProject.instance().removeAllMapLayers()
        dock.table.setRowCount(0)
        dock.add_course(Course('Line', 'N', '100'))
        dock.add_course(Course('Line', 'E', '100'))
        with self.assertRaisesRegex(ValueError, 'open'):
            dock.create_polygon()
        dock.table.setRowCount(0)
        dock.add_course(Course('Line', 'E', '100'))
        dock.add_course(Course('Line', 'W', '100'))
        with self.assertRaisesRegex(ValueError, 'nonzero'):
            dock.create_polygon()
        dock.table.setRowCount(0)
        for course in lines_through([(0, 0), (100, 100), (0, 100), (100, 0)]):
            dock.add_course(course)
        with self.assertRaisesRegex(ValueError, 'valid'):
            dock.create_polygon()
        self.assertEqual(QgsProject.instance().count(), 0)
        dock.cleanup()
        dock.deleteLater()
        canvas.deleteLater()

    def test_legal_review_load_and_blocking(self):
        from cogo_workbench.dock import CogoDock
        from test_legal import RECTANGLE
        canvas = QgsMapCanvas()

        class Iface:
            def mainWindow(self): return None
            def mapCanvas(self): return canvas

        dock = CogoDock(Iface())
        dock.crs.setCrs(self.crs)
        dock.x.setText('500000')
        dock.y.setText('1000000')
        dock.legal_text.setPlainText('Commencing at a monument; thence N10E 500 feet to the '
                                    'true point of beginning; ' + RECTANGLE)
        dock.extract_legal()
        self.assertFalse(dock.legal_load.isEnabled())
        dock.legal_units.setCurrentText('Meters')
        dock.legal_checked.setChecked(True)
        before = dock.courses()
        with self.assertRaisesRegex(ValueError, 'disagree'):
            dock.load_legal()
        self.assertEqual(dock.courses(), before)
        dock.legal_units.setCurrentText('International feet')
        dock.legal_checked.setChecked(True)
        self.assertTrue(dock.legal_load.isEnabled())
        dock.adjust.setChecked(True)
        dock.target_x.setText('123')
        dock.load_legal()
        self.assertEqual(len(dock.courses()), 4)
        self.assertEqual(dock.units.currentText(), 'International feet')
        self.assertFalse(dock.adjust.isChecked())
        self.assertEqual(dock.target_x.text(), '')
        self.assertEqual(dock.x.text(), '500000')
        dock.create_polygon()
        parcel = next(iter(QgsProject.instance().mapLayers().values()))
        self.assertAlmostEqual(next(parcel.getFeatures()).geometry().area(), 20000 * 0.3048 ** 2, places=5)
        dock.legal_text.setPlainText('Thence along the lot line to the creek')
        self.assertFalse(dock.legal_load.isEnabled())
        self.assertEqual(dock.legal_review.rowCount(), 0)
        dock.extract_legal()
        dock.legal_checked.setChecked(True)
        with self.assertRaisesRegex(ValueError, 'all boundary calls'):
            dock.load_legal()
        self.assertEqual(len(dock.courses()), 4)
        section30 = (Path(__file__).parent / 'fixtures' / 'section30.txt').read_text(encoding='utf-8')
        dock.legal_text.setPlainText(section30)
        dock.extract_legal()
        dock.legal_checked.setChecked(True)
        self.assertFalse(dock.legal_load.isEnabled())
        dock.legal_boundary_only.setChecked(True)
        self.assertEqual(dock.legal_review.rowCount(), 0)
        self.assertFalse(dock.legal_checked.isChecked())
        dock.extract_legal()
        dock.legal_checked.setChecked(True)
        self.assertTrue(dock.legal_load.isEnabled())
        dock.load_legal()
        self.assertEqual(len(dock.courses()), 5)
        from cogo_workbench.deed_dialog import DeedReaderDialog
        dialog = DeedReaderDialog(dock, None)
        self.assertEqual(dialog.provider.currentData(), 'ollama')
        self.assertIn('localhost', dialog.url.text())
        dialog.close()
        dialog.deleteLater()
        dock.cleanup()
        dock.deleteLater()
        QgsProject.instance().removeAllMapLayers()
        canvas.deleteLater()


if __name__ == '__main__':
    unittest.main()
