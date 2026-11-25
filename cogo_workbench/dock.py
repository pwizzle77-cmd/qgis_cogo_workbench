import csv
import json
import math

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser,
    QVBoxLayout, QWidget)
from qgis.core import (Qgis, QgsCircularString, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsCsException, QgsCurvePolygon, QgsFeature, QgsField, QgsGeometry, QgsLineString, QgsPoint,
    QgsPointXY, QgsProject, QgsUnitTypes, QgsVectorLayer)
from qgis.gui import QgsMapToolEmitPoint, QgsProjectionSelectionWidget, QgsRubberBand
from qgis.PyQt.QtCore import QVariant

from .engine import Course, bowditch, closure, format_bearing, inverse, number, traverse
from .layers import boundary_geometry, line_layer, update_selected
from .legal import parse_description, units_match


HEADERS = ['Type', 'Direction', 'Distance / value', 'Radius', 'Measure', 'Turn', 'Bearing basis']
CSV_HEADERS = ['kind', 'direction', 'distance', 'radius', 'measure', 'side', 'basis']
UNITS = {'CRS units': None, 'Meters': 1.0, 'International feet': 0.3048,
         'US survey feet': 1200 / 3937}


def _closed_ring(segments, gap):
    """Exterior ring closed onto its start.

    A gap that is only rounding is snapped onto the start. Inserting a stub
    that short makes the ring cross the first course, so a clockwise or
    concave lot is reported as self-intersecting.
    """
    geometry = boundary_geometry(segments)
    ring = geometry.constGet().clone()
    start, end = segments[0].start, segments[-1].end
    span = max(abs(start[0]), abs(start[1]), abs(end[0]), abs(end[1]), 1.0)
    if gap > max(1e-8, span * 1e-12):
        ring.addCurve(QgsLineString([QgsPoint(end[0], end[1]), QgsPoint(start[0], start[1])]))
        return ring, gap
    curve = ring.curveAt(ring.nCurves() - 1).clone()
    points = [curve.pointN(index) for index in range(curve.numPoints())]
    points[-1] = QgsPoint(start[0], start[1])
    if isinstance(curve, QgsCircularString):
        snapped = QgsCircularString()
        snapped.setPoints(points)
    else:
        snapped = QgsLineString(points)
    ring.removeCurve(ring.nCurves() - 1)
    ring.addCurve(snapped)
    return ring, 0.0


class CogoDock(QDockWidget):
    def __init__(self, iface):
        super().__init__('COGO Workbench', iface.mainWindow())
        self._restoring = True
        self.setObjectName('CogoWorkbenchDock')
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.rubber = QgsRubberBand(self.canvas, Qgis.GeometryType.Line)
        self.rubber.setColor(QColor(0, 150, 220, 200))
        self.rubber.setWidth(3)
        self.pick_tool = None
        self.previous_tool = None
        self.tabs = QTabWidget()
        self.setWidget(self.tabs)
        page = QWidget()
        layout = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, 'Traverse')
        form = QFormLayout()
        self.crs = QgsProjectionSelectionWidget()
        self.crs.setCrs(QgsProject.instance().crs())
        form.addRow('Working projected CRS', self.crs)
        self.units = QComboBox()
        self.units.addItems(UNITS)
        form.addRow('Record distance units', self.units)
        self.scale = self.spin(1, 0.000001, 1000000, 10)
        form.addRow('Grid / ground scale', self.scale)
        self.offset = self.spin(0, -360, 360, 8)
        form.addRow('Add to record azimuth (°)', self.offset)
        self.x, self.y = QLineEdit('0'), QLineEdit('0')
        self.x.setAccessibleName('Start easting')
        self.y.setAccessibleName('Start northing')
        coordinates = QHBoxLayout()
        coordinates.addWidget(self.x)
        coordinates.addWidget(self.y)
        self.button(coordinates, 'Pick start', self.pick_start)
        form.addRow('Start X / Y (CRS units)', coordinates)
        self.target_x, self.target_y = QLineEdit(), QLineEdit()
        self.target_x.setPlaceholderText('Blank = start X')
        self.target_y.setPlaceholderText('Blank = start Y')
        target = QHBoxLayout()
        target.addWidget(self.target_x)
        target.addWidget(self.target_y)
        form.addRow('Closure target X / Y', target)
        self.tolerance = self.spin(0.01, 0.00000001, 1000000, 8)
        form.addRow('Polygon closure tolerance (CRS units)', self.tolerance)
        layout.addLayout(form)
        hint = QLabel('Bearings: N45:30:00E or 135.5 (azimuth). Curves: enter radius,\n'
                      'Arc length, Chord length (minor arc), or Delta in degrees.\n'
                      'Direction is the start tangent or chord bearing, as selected.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setMinimumHeight(180)
        self.table.setAccessibleName('Traverse courses')
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.button(row, '+ Line', lambda: self.add_course())
        self.button(row, '+ Curve', lambda: self.add_course(Course('Curve', '', '', '', 'Arc', 'R', 'Tangent')))
        self.button(row, 'Remove', self.remove_rows)
        self.button(row, 'Import CSV', self.import_csv)
        self.button(row, 'Export CSV', self.export_csv)
        layout.addLayout(row)
        self.adjust = QCheckBox('Bowditch adjustment (straight courses only)')
        layout.addWidget(self.adjust)
        self.report = QTextBrowser()
        self.report.setMinimumHeight(100)
        layout.addWidget(self.report)
        row = QHBoxLayout()
        self.button(row, 'Preview / closure', self.preview)
        self.button(row, 'Create COGO lines', self.create_lines)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.button(row, 'Create polygon', self.create_polygon)
        self.button(row, 'Create corner points', self.create_points)
        layout.addLayout(row)
        temporary = QLabel('Outputs are temporary layers. Export them to GeoPackage to keep them. '
                           'The course table is saved in the QGIS project.')
        temporary.setWordWrap(True)
        layout.addWidget(temporary)
        self.add_course()
        self.setup_legal_tab()
        tools = QWidget()
        tools_layout = QVBoxLayout(tools)
        self.tabs.addTab(tools, 'Update / Inverse')
        explanation = QLabel('Update COGO uses the active line layer and selected features only.\n'
            'It calculates record measurements from geometry using the units, scale and\n'
            'offset on the Traverse tab. The working CRS must match the layer CRS.\n'
            'Supports two-vertex lines and true single circular arcs. All selected\n'
            'features are validated first. Changes remain unsaved and undoable.')
        explanation.setWordWrap(True)
        tools_layout.addWidget(explanation)
        self.missing = QCheckBox('Fill NULL fields only')
        tools_layout.addWidget(self.missing)
        self.button(tools_layout, 'Update selected COGO lines', self.update_cogo)
        inverse_hint = QLabel('Inverse: coordinates in the working CRS; results in record units.')
        inverse_hint.setWordWrap(True)
        tools_layout.addWidget(inverse_hint)
        inverse_form = QFormLayout()
        self.inverse_inputs = [QLineEdit('0'), QLineEdit('0'), QLineEdit('100'), QLineEdit('100')]
        for label, widget in zip(['From X', 'From Y', 'To X', 'To Y'], self.inverse_inputs):
            inverse_form.addRow(label, widget)
        tools_layout.addLayout(inverse_form)
        self.button(tools_layout, 'Calculate inverse', self.calculate_inverse)
        self.inverse_result = QLabel()
        self.inverse_result.setWordWrap(True)
        tools_layout.addWidget(self.inverse_result)
        tools_layout.addStretch()
        help_page = QTextBrowser()
        help_page.setHtml(HELP)
        self.tabs.addTab(help_page, 'Help')
        self.visibilityChanged.connect(self.visibility_changed)
        self._connect_session()
        QgsProject.instance().readProject.connect(self._load_session)
        QgsProject.instance().cleared.connect(self._reset_session)
        self._load_session()

    def setup_legal_tab(self):
        self.legal_page = QWidget()
        layout = QVBoxLayout(self.legal_page)
        self.tabs.addTab(self.legal_page, 'Paste description')
        hint = QLabel('Paste one parcel description, then extract and review every call. '
            'Use semicolons or “thence” between calls. Set the working CRS and the '
            'point of beginning on Traverse. Commencement ties are normally excluded.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.legal_text = QPlainTextEdit()
        self.legal_text.setPlaceholderText('Beginning at a found monument; thence North 0 degrees East, '
            '100 feet; thence East, 200 feet; thence South, 100 feet; '
            'thence West, 200 feet to the point of beginning.')
        self.legal_text.setAccessibleName('Legal description source text')
        layout.addWidget(self.legal_text, 1)
        self.legal_boundary_only = QCheckBox('All calls are boundary (no separate commencement tie)')
        self.legal_boundary_only.setToolTip('Use when the description says Commence at the boundary start '
            'and returns to that same point. All calls will be retained; verify this interpretation.')
        layout.addWidget(self.legal_boundary_only)
        self.button(layout, 'Extract calls for review', self.extract_legal)
        self.button(layout, 'Read a deed or document…', self.open_deed)
        self.legal_review = QTableWidget(0, 3)
        self.legal_review.setHorizontalHeaderLabels(['Extracted call / units', 'Review', 'Source text'])
        self.legal_review.setColumnWidth(0, 180)
        self.legal_review.setColumnWidth(1, 170)
        self.legal_review.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.legal_review.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.legal_review.setWordWrap(True)
        self.legal_review.setAccessibleName('Extracted legal description calls and unresolved clauses')
        layout.addWidget(self.legal_review, 2)
        self.legal_status = QLabel('Nothing extracted yet. Source edits require extraction and review again.')
        self.legal_status.setWordWrap(True)
        layout.addWidget(self.legal_status)
        form = QFormLayout()
        self.legal_units = QComboBox()
        self.legal_units.addItems(['Choose record units…', 'International feet', 'US survey feet', 'Meters', 'CRS units'])
        form.addRow('Apply record distance units', self.legal_units)
        layout.addLayout(form)
        self.legal_checked = QCheckBox('I reviewed the calls, units and point of beginning')
        layout.addWidget(self.legal_checked)
        self.legal_load = self.button(layout, 'Replace Traverse with reviewed boundary', self.load_legal)
        self.legal_rows = []
        self.legal_load.setEnabled(False)
        self.legal_text.textChanged.connect(self.invalidate_legal)
        self.legal_boundary_only.toggled.connect(self.invalidate_legal)
        self.legal_units.currentIndexChanged.connect(lambda: self.legal_checked.setChecked(False))
        self.legal_checked.toggled.connect(self.legal_ready)

    def invalidate_legal(self):
        self.legal_rows = []
        self.legal_review.setRowCount(0)
        self.legal_checked.setChecked(False)
        self.legal_load.setEnabled(False)
        self.legal_status.setText('Source changed. Extract the calls again before loading.')

    def legal_ready(self):
        boundary = [row for row in self.legal_rows if row.phase == 'Boundary']
        self.legal_load.setEnabled(bool(boundary) and self.legal_checked.isChecked()
            and self.legal_units.currentIndex() > 0 and any(row.course for row in boundary)
            and not any(row.issue for row in boundary))

    def extract_legal(self):
        self.invalidate_legal()
        self.legal_rows = parse_description(self.legal_text.toPlainText(), self.legal_boundary_only.isChecked())
        for row in self.legal_rows:
            course = row.course
            call = ''
            if course:
                call = f'{course.kind}: {course.direction}, {course.distance}'
                if course.kind == 'Curve':
                    call += f' ({course.measure}), R={course.radius}, {course.side}, {course.basis}'
            status = row.issue or ('Parsed' if course else 'Context: no course')
            if row.phase == 'Commencement':
                status = 'Excluded; start coordinates must be the POB. ' + status
            index = self.legal_review.rowCount()
            self.legal_review.insertRow(index)
            summary = f'{row.phase}\n{call}'
            if course:
                summary += f'\nUnits: {row.unit or "Not stated"}'
            for col, value in enumerate([summary, status, row.source]):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if row.issue:
                    item.setBackground(QColor('#ffdfad'))
                self.legal_review.setItem(index, col, item)
        self.legal_review.resizeRowsToContents()
        boundary = [row for row in self.legal_rows if row.phase == 'Boundary']
        count = sum(row.course is not None for row in boundary)
        issues = sum(bool(row.issue) for row in boundary)
        self.legal_status.setText(f'{count} boundary calls extracted; {issues} unresolved clauses. '
            'Resolve highlighted boundary clauses in the source and extract again. '
            'Then choose units, review the calls, and load them into Traverse.')

    def load_legal(self):
        boundary = [row for row in self.legal_rows if row.phase == 'Boundary']
        courses = [row.course for row in boundary if row.course]
        if not courses or any(row.issue for row in boundary) or not self.legal_checked.isChecked():
            raise ValueError('Extract and review all boundary calls before loading.')
        unit = self.legal_units.currentText()
        if self.legal_units.currentIndex() == 0:
            raise ValueError('Choose the record distance units first.')
        for row in boundary:
            if row.course and not units_match(row.unit, unit):
                raise ValueError(f'Selected units disagree with the description: {row.unit}.')
        self.load_courses(courses, unit)

    def load_courses(self, courses, unit):
        if not courses:
            raise ValueError('No boundary courses to load.')
        if unit not in UNITS:
            raise ValueError('Choose the record distance units first.')
        self.settings()
        traverse((number(self.x.text(), 'Start X'), number(self.y.text(), 'Start Y')), courses)
        self.table.setRowCount(0)
        for course in courses:
            self.add_course(course)
        self.units.setCurrentText(unit)
        self.adjust.setChecked(False)
        self.target_x.clear()
        self.target_y.clear()
        self.rubber.reset(Qgis.GeometryType.Line)
        self.report.setPlainText('Reviewed courses loaded. The start point must be the point of beginning. '
            'Use Preview / closure, then Create corner points. No features have been created.')
        self.tabs.setCurrentIndex(0)

    def open_deed(self):
        from .deed_dialog import DeedReaderDialog
        DeedReaderDialog(self, self).exec()

    @staticmethod
    def spin(value, low, high, decimals):
        widget = QDoubleSpinBox()
        widget.setDecimals(decimals)
        widget.setRange(low, high)
        widget.setValue(value)
        return widget

    def button(self, layout, text, callback):
        button = QPushButton(text)
        button.clicked.connect(lambda checked=False: self.run(callback))
        layout.addWidget(button)
        return button

    def run(self, callback):
        try:
            callback()
        except (ValueError, OSError, UnicodeError, csv.Error, QgsCsException) as error:
            QMessageBox.warning(self, 'COGO Workbench', str(error))

    def settings(self):
        crs = self.crs.crs()
        if not crs.isValid() or crs.isGeographic():
            raise ValueError('Choose a valid projected CRS. Geographic degrees cannot be used for planar COGO.')
        meters_per_unit = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Meters)
        if crs.mapUnits() == Qgis.DistanceUnit.Unknown or not math.isfinite(meters_per_unit) or meters_per_unit <= 0:
            raise ValueError('Working CRS must have known linear units.')
        unit = self.units.currentText()
        conversion = 1 if UNITS[unit] is None else UNITS[unit] / meters_per_unit
        return crs, conversion * self.scale.value(), self.offset.value(), unit

    def add_course(self, course=None):
        course = course or Course('Line', '', '')
        row = self.table.rowCount()
        self.table.insertRow(row)
        choices = {0: ['Line', 'Curve'], 4: ['Arc', 'Chord', 'Delta'], 5: ['R', 'L'], 6: ['Tangent', 'Chord']}
        for col, value in enumerate(vars(course).values()):
            if col in choices:
                widget = QComboBox()
                widget.addItems(choices[col])
                widget.currentTextChanged.connect(self._save_session)
                widget.setCurrentText(value)
                self.table.setCellWidget(row, col, widget)
            else:
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self._save_session()

    def courses(self):
        result = []
        for row in range(self.table.rowCount()):
            values = []
            for col in range(len(HEADERS)):
                widget = self.table.cellWidget(row, col)
                item = self.table.item(row, col)
                values.append(widget.currentText() if widget else item.text().strip() if item else '')
            result.append(Course(*values))
        return result

    def remove_rows(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows and self.table.currentRow() >= 0:
            rows.add(self.table.currentRow())
        for row in sorted(rows, reverse=True):
            self.table.removeRow(row)
        self._save_session()

    def calculate(self):
        crs, factor, offset, unit = self.settings()
        start = (number(self.x.text(), 'Start X'), number(self.y.text(), 'Start Y'))
        target = (number(self.target_x.text() or start[0], 'Target X'),
                  number(self.target_y.text() or start[1], 'Target Y'))
        courses = self.courses()
        raw = traverse(start, courses, factor, offset)
        dx, dy, error, length, ratio = closure(raw, target)
        segments = bowditch(raw, target) if self.adjust.isChecked() else raw
        end = segments[-1].end
        self.report.setPlainText(f'Record traverse (before adjustment)\n'
            f'Length: {length:.6f} CRS units | ΔX: {dx:.6f} | ΔY: {dy:.6f}\n'
            f'Misclosure: {error:.6f} | Relative closure: {"exact" if math.isinf(ratio) else f"1:{ratio:,.0f}"}\n'
            f'Output end X: {end[0]:.8f}  Y: {end[1]:.8f}\n'
            f'Output: {"Bowditch adjusted; record inputs retained on lines" if self.adjust.isChecked() else "unadjusted"}')
        return crs, factor, offset, unit, courses, segments

    def preview(self):
        crs, _, _, _, _, segments = self.calculate()
        geometry = boundary_geometry(segments)
        transform = QgsCoordinateTransform(crs, self.canvas.mapSettings().destinationCrs(), QgsProject.instance())
        geometry.transform(transform)
        self.rubber.setToGeometry(geometry, None)
        extent = geometry.boundingBox()
        margin = max(extent.width(), extent.height(), 1) * 0.1
        extent.grow(margin)
        self.canvas.setExtent(extent)
        self.canvas.refresh()

    def create_lines(self):
        crs, factor, offset, unit, courses, segments = self.calculate()
        layer = line_layer(crs, segments, courses, factor, offset, unit, self.adjust.isChecked())
        QgsProject.instance().addMapLayer(layer)

    def create_polygon(self):
        crs, _, _, _, _, segments = self.calculate()
        start, end = segments[0].start, segments[-1].end
        gap = math.hypot(end[0] - start[0], end[1] - start[1])
        if gap > self.tolerance.value():
            raise ValueError(f'Boundary is open by {gap:.8f} CRS units. Correct the calls or adjust to the start point.')
        ring, gap = _closed_ring(segments, gap)
        curve_polygon = QgsCurvePolygon()
        curve_polygon.setExteriorRing(ring)
        polygon = QgsGeometry(curve_polygon)
        # A there-and-back course leaves a sliver around 1e-12. Reject that, not a thin lot.
        if polygon.isEmpty() or polygon.area() <= 1e-8 or not polygon.isGeosValid():
            raise ValueError('Boundary does not form a valid, nonzero polygon. Check crossings and course order.')
        layer = QgsVectorLayer('CurvePolygon', 'COGO parcel', 'memory')
        layer.setCrs(crs)
        provider = layer.dataProvider()
        provider.addAttributes([QgsField('area_grid', QVariant.Double), QgsField('closure_gap', QVariant.Double)])
        layer.updateFields()
        feature = QgsFeature(layer.fields())
        feature.setGeometry(polygon)
        feature.setAttributes([polygon.area(), gap])
        if not provider.addFeatures([feature])[0]:
            raise ValueError('Unable to create polygon.')
        layer.updateExtents()
        QgsProject.instance().addMapLayer(layer)
        self.report.append(f'Polygon area: {polygon.area():.6f} square CRS units. Closing connector: {gap:.8f} CRS units.')

    def create_points(self):
        crs, _, _, _, _, segments = self.calculate()
        layer = QgsVectorLayer('Point', 'COGO corners', 'memory')
        layer.setCrs(crs)
        provider = layer.dataProvider()
        provider.addAttributes([QgsField('corner', QVariant.Int)])
        layer.updateFields()
        points = [segments[0].start] + [s.end for s in segments]
        if math.hypot(points[-1][0] - points[0][0], points[-1][1] - points[0][1]) < 1e-8:
            points.pop()
        features = []
        for i, point in enumerate(points, 1):
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*point)))
            feature.setAttributes([i])
            features.append(feature)
        if not provider.addFeatures(features)[0]:
            raise ValueError('Unable to create corners.')
        layer.updateExtents()
        QgsProject.instance().addMapLayer(layer)

    def update_cogo(self):
        crs, factor, offset, unit = self.settings()
        layer = self.iface.activeLayer()
        if not isinstance(layer, QgsVectorLayer) or layer.geometryType() != Qgis.GeometryType.Line:
            raise ValueError('Activate a vector line layer first.')
        if layer.crs() != crs:
            raise ValueError('Set the working CRS to the active layer CRS before updating COGO.')
        count = layer.selectedFeatureCount()
        if count <= 0:
            raise ValueError('Select the COGO line features to update first.')
        answer = QMessageBox.question(
            self, 'Update COGO',
            f'Update {count} selected feature(s) on "{layer.name()}"?\n'
            'Fields such as distance, direction, bearing and side are rewritten. '
            'Undo is available until you save the layer.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        count = update_selected(layer, factor, offset, unit, self.missing.isChecked())
        self.iface.messageBar().pushSuccess('COGO', f'Updated {count} selected lines. Review edits, then save or roll back in QGIS.')

    def calculate_inverse(self):
        _, factor, offset, unit = self.settings()
        values = [number(w.text(), 'Coordinate') for w in self.inverse_inputs]
        direction, distance = inverse(values[:2], values[2:])
        direction = (direction - offset) % 360
        self.inverse_result.setText(f'{format_bearing(direction)}\nAzimuth: {direction:.10f}°\nDistance: {distance / factor:.8f} {unit}')

    def import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import courses (replaces table)', '', 'CSV (*.csv)')
        if not path:
            return
        with open(path, newline='', encoding='utf-8-sig') as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != CSV_HEADERS:
                raise ValueError('CSV header must be: ' + ','.join(CSV_HEADERS))
            rows = list(reader)
        if not rows or len(rows) > 10000:
            raise ValueError('CSV must contain 1–10000 courses.')
        courses = []
        for row in rows:
            if None in row or any(v is None for v in row.values()):
                raise ValueError('CSV row has missing or extra columns.')
            courses.append(Course(**{k: v.strip() for k, v in row.items()}))
        traverse((0, 0), courses)
        self.table.setRowCount(0)
        for course in courses:
            self.add_course(course)

    def export_csv(self):
        courses = self.courses()
        traverse((0, 0), courses)
        path, _ = QFileDialog.getSaveFileName(self, 'Export record courses', '', 'CSV (*.csv)')
        if path:
            with open(path, 'w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=CSV_HEADERS)
                writer.writeheader()
                writer.writerows(vars(course) for course in courses)

    def pick_start(self):
        self.settings()
        if self.pick_tool is not None:
            self.restore_tool()
        self.previous_tool = self.canvas.mapTool()
        self.pick_tool = QgsMapToolEmitPoint(self.canvas)
        self.pick_tool.canvasClicked.connect(lambda point, button: self.run(lambda: self.picked(point, button)))
        self.canvas.setMapTool(self.pick_tool)

    def picked(self, point, button):
        if button != Qt.MouseButton.LeftButton:
            self.restore_tool()
            return
        try:
            match = self.canvas.snappingUtils().snapToMap(point)
            if match.isValid():
                point = match.point()
            transform = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), self.crs.crs(), QgsProject.instance())
            point = transform.transform(point)
            self.x.setText(f'{point.x():.10f}')
            self.y.setText(f'{point.y():.10f}')
        finally:
            self.restore_tool()

    def restore_tool(self):
        if self.pick_tool is not None:
            tool = self.pick_tool
            if self.canvas.mapTool() is tool:
                self.canvas.unsetMapTool(tool)
                if self.previous_tool is not None:
                    self.canvas.setMapTool(self.previous_tool)
            self.pick_tool = None
            self.previous_tool = None
            tool.deleteLater()

    def visibility_changed(self, visible):
        if not visible:
            self.rubber.reset(Qgis.GeometryType.Line)
            self.restore_tool()

    def _connect_session(self):
        self.units.currentTextChanged.connect(self._save_session)
        self.scale.valueChanged.connect(self._save_session)
        self.offset.valueChanged.connect(self._save_session)
        self.tolerance.valueChanged.connect(self._save_session)
        self.adjust.toggled.connect(self._save_session)
        self.x.textChanged.connect(self._save_session)
        self.y.textChanged.connect(self._save_session)
        self.target_x.textChanged.connect(self._save_session)
        self.target_y.textChanged.connect(self._save_session)
        self.table.itemChanged.connect(self._save_session)
        self.crs.crsChanged.connect(self._save_session)

    def _save_session(self, *_args):
        if self._restoring:
            return
        payload = {
            'crs': self.crs.crs().authid(),
            'units': self.units.currentText(),
            'scale': self.scale.value(),
            'offset': self.offset.value(),
            'x': self.x.text(),
            'y': self.y.text(),
            'target_x': self.target_x.text(),
            'target_y': self.target_y.text(),
            'tolerance': self.tolerance.value(),
            'adjust': self.adjust.isChecked(),
            'courses': [vars(course) for course in self.courses()],
        }
        QgsProject.instance().writeEntry('CogoWorkbench', 'session', json.dumps(payload))

    def _load_session(self, *_args):
        from qgis.PyQt import sip
        if sip.isdeleted(self):
            return
        raw, ok = QgsProject.instance().readEntry('CogoWorkbench', 'session', '')
        self._restoring = True
        try:
            if not ok or not raw:
                return
            data = json.loads(raw)
            courses = [Course(**row) for row in data['courses']]
            authid = data.get('crs') or ''
            if authid:
                crs = QgsCoordinateReferenceSystem(authid)
                if crs.isValid():
                    self.crs.setCrs(crs)
            if data.get('units') in UNITS:
                self.units.setCurrentText(data['units'])
            self.scale.setValue(float(data.get('scale', 1)))
            self.offset.setValue(float(data.get('offset', 0)))
            self.x.setText(str(data.get('x', '0')))
            self.y.setText(str(data.get('y', '0')))
            self.target_x.setText(str(data.get('target_x', '')))
            self.target_y.setText(str(data.get('target_y', '')))
            self.tolerance.setValue(float(data.get('tolerance', 0.01)))
            self.adjust.setChecked(bool(data.get('adjust', False)))
            self.table.setRowCount(0)
            for course in courses:
                self.add_course(course)
            if self.table.rowCount() == 0:
                self.add_course()
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return
        finally:
            self._restoring = False

    def _reset_session(self):
        from qgis.PyQt import sip
        if sip.isdeleted(self):
            return
        self._restoring = True
        try:
            self.table.setRowCount(0)
            self.add_course()
            self.units.setCurrentIndex(0)
            self.scale.setValue(1)
            self.offset.setValue(0)
            self.x.setText('0')
            self.y.setText('0')
            self.target_x.clear()
            self.target_y.clear()
            self.tolerance.setValue(0.01)
            self.adjust.setChecked(False)
        finally:
            self._restoring = False

    def cleanup(self):
        project = QgsProject.instance()
        for signal, slot in ((project.readProject, self._load_session), (project.cleared, self._reset_session)):
            try:
                signal.disconnect(slot)
            except TypeError:
                pass
        self.restore_tool()
        self.canvas.scene().removeItem(self.rubber)


HELP = '''<h2>COGO Workbench</h2>
<p>Enter courses in order from the point of beginning. Use a local projected CRS
appropriate for the property. Calculations are planar, with no automatic geodesic,
magnetic declination, convergence, or elevation correction.</p>
<h3>Directions and curves</h3><p>North azimuths increase clockwise. Quadrant bearings
accept N45:30:00E, N45°30'00"E, N. 45° E., or N45.5E. On this grid, 45.3030 means decimal degrees,
not packed DMS. Paste rejects a four-digit fraction such as 00.2013 and asks for 00:20:13.
Due east, north, south and west are shown as E, N, S and W. For an arc, Direction refers to the start tangent or chord.
“The chord of which bears” is a chord bearing.
R turns clockwise, L counterclockwise. Delta is decimal degrees or separated DMS.
Chord input chooses the minor arc; use Arc or Delta for a major arc. Enter the
next course direction explicitly, including tangent continuations.</p>
<h3>Ground to grid</h3><p>Grid length = record length × unit conversion × scale.
Grid azimuth = record azimuth + offset. Coordinates and closure tolerance always
use working CRS units. Update COGO and Inverse apply the inverse conversion.
The direction field stores chord azimuth for curves; distance stores chord length,
radius is positive, side stores L/R, and arclength stores arc length.</p>
<h3>Closure and adjustment</h3><p>Blank target coordinates use the start point.
An explicit target supports an open traverse to known control. Misclosure and
relative closure are reported before adjustment. Bowditch distributes X/Y error
in proportion to course length and is available for straight courses only.
Record inputs remain in record_* fields; adjusted=1 marks adjusted line output.
CSV exports retain the original entered courses.</p>
<h3>Outputs and edits</h3><p>Each course becomes a separate COGO line with true
circular geometry, including true curves in parcel polygons. A gap that is only
rounding is snapped onto the start. A larger gap within tolerance is closed with
a straight connector and recorded as closure_gap. Larger gaps are rejected.
Self-crossing and zero-area polygons are rejected. Corner points include course endpoints, not arc centers.</p>
<p>New layers are temporary: export to GeoPackage to preserve them and true line
curves. The course table, start point, units, scale and offset are saved in the
QGIS project. Update COGO asks before it rewrites measurement fields on the
active line layer. It never changes geometry. Use QGIS Undo or roll
back the layer edits to reverse it. Save edits explicitly after review.</p>
<p>CSV stores courses only, not CRS, coordinates, units, scale, or offset. Record
these settings separately.</p>
<h3>Paste description</h3><p>Paste one parcel, extract calls, and compare each
call with its source text. Resolve highlighted boundary clauses by editing the
source and extracting again. The parser recognizes explicit bearings/distances
and curves with radius, turn, arc/chord/delta and labeled chord/tangent bearing.
It does not infer tangent continuations or missing dimensions. Separate calls
with thence or semicolons.</p>
<p>Commencement calls before an explicit point of beginning are shown but excluded.
If Commence names the boundary starting corner and the POB occurs only at the
close, select All calls are boundary and extract again. This explicitly retains
all courses; do not select it for a genuine separate commencement tie.
The start coordinates must be the POB, not the commencement monument. Choose
the foot definition explicitly when the text says feet. Confirm the review and
load the boundary into Traverse, replacing its course table. This resets Bowditch
and closure targets, retains CRS/start/scale/offset, and creates no features.
Use Preview / closure and the normal output buttons afterward. Changed source
text requires extraction and review again.</p>
<p>A running call such as “West along the south line, 208 feet” is one course.
“Along” or “parallel” names the line being followed. A curve written with
concave or convex wording still needs an explicit left or right, a radius,
an arc, chord or delta, and a labeled chord or tangent bearing. A caption
that only names a section is not a course.</p>
<p>Read a deed or document sends each page image to a model you choose. Ollama
stays on this computer. xAI (Grok) sends the images to api.x.ai and needs
XAI_API_KEY or a QGIS authentication config. The model must quote the deed.
Review the calls, load them into Traverse, set the point of beginning, then
create corner points. Nothing is drawn before that.</p>
<p>Exceptions, approximate calls, implicit directions, unsupported units, and
conflicting curve dimensions require manual resolution. Spirals, automatic
parcel topology, and boundary adjudication are outside this tool.</p>
<p>Verify calls, control, units and closure before relying on a boundary. This
tool does not determine a legal boundary or replace a licensed survey.</p>'''
