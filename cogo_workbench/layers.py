"""QGIS geometry and edit-buffer operations."""

from qgis.PyQt.QtCore import QVariant
from qgis.core import (QgsCircularString, QgsCompoundCurve, QgsFeature, QgsField,
                       QgsGeometry, QgsLineString, QgsPoint, QgsVectorLayer)

from .engine import circle_parameters, format_bearing, inverse


FIELDS = [("direction", QVariant.Double), ("distance", QVariant.Double),
          ("radius", QVariant.Double), ("arclength", QVariant.Double),
          ("delta", QVariant.Double), ("bearing", QVariant.String),
          ("side", QVariant.String), ("course", QVariant.Int),
          ("record_dir", QVariant.String), ("record_val", QVariant.String),
          ("record_rad", QVariant.String), ("record_mode", QVariant.String),
          ("adjusted", QVariant.Int), ("cogo_unit", QVariant.String),
          ("grid_factor", QVariant.Double), ("dir_offset", QVariant.Double)]


def segment_geometry(segment):
    points = [QgsPoint(*segment.start), QgsPoint(*segment.end)]
    if segment.mid is not None:
        curve = QgsCircularString()
        curve.setPoints([points[0], QgsPoint(*segment.mid), points[1]])
    else:
        curve = QgsLineString(points)
    compound = QgsCompoundCurve()
    compound.addCurve(curve)
    return QgsGeometry(compound)


def boundary_geometry(segments):
    compound = QgsCompoundCurve()
    for segment in segments:
        geometry = segment_geometry(segment)
        compound.addCurve(geometry.constGet().curveAt(0).clone())
    return QgsGeometry(compound)


def measured_values(geometry, factor, offset):
    if geometry.isNull() or geometry.isEmpty() or geometry.isMultipart():
        raise ValueError("Use nonempty, single-part lines.")
    curve = geometry.constGet()
    if isinstance(curve, QgsCompoundCurve):
        if curve.nCurves() != 1:
            raise ValueError("Compound lines with multiple components must be split into courses first.")
        curve = curve.curveAt(0)
    if isinstance(curve, QgsCircularString) and curve.numPoints() == 3:
        points = [(curve.pointN(i).x(), curve.pointN(i).y()) for i in range(3)]
        a, chord = inverse(points[0], points[2])
        r, arc, delta, side = circle_parameters(*points)
        return [(a - offset) % 360, chord / factor, r / factor, arc / factor,
                delta, format_bearing(a - offset), side]
    if isinstance(curve, QgsLineString) and curve.numPoints() == 2:
        start, end = curve.startPoint(), curve.endPoint()
        a, distance = inverse((start.x(), start.y()), (end.x(), end.y()))
        a = (a - offset) % 360
        return [a, distance / factor, None, None, None, format_bearing(a), None]
    raise ValueError("Use two-vertex straight lines or true three-point circular arcs. Split multi-course lines first.")


def line_layer(crs, segments, courses, factor, offset, unit, adjusted=False):
    layer = QgsVectorLayer("CompoundCurve", "COGO traverse", "memory")
    layer.setCrs(crs)
    provider = layer.dataProvider()
    if not provider.addAttributes([QgsField(name, kind) for name, kind in FIELDS]):
        raise ValueError("Unable to create COGO fields.")
    layer.updateFields()
    features = []
    for i, (segment, course) in enumerate(zip(segments, courses), 1):
        geometry = segment_geometry(segment)
        feature = QgsFeature(layer.fields())
        feature.setGeometry(geometry)
        feature.setAttributes(measured_values(geometry, factor, offset) + [i,
            course.direction, course.distance, course.radius,
            f"{course.kind}/{course.measure}/{course.basis}/{course.side}",
            int(adjusted), unit, factor, offset])
        features.append(feature)
    if not provider.addFeatures(features)[0]:
        raise ValueError("Unable to create traverse features.")
    layer.updateExtents()
    return layer


def update_selected(layer, factor, offset, unit, only_missing=False):
    """Validate every feature before opening one undoable edit command."""
    features = list(layer.getSelectedFeatures())
    if not features:
        raise ValueError("Select the COGO line features to update first.")
    required = FIELDS[:7] + FIELDS[13:]
    for name, kind in required:
        index = layer.fields().lookupField(name)
        if index >= 0 and layer.fields()[index].type() != kind:
            raise ValueError(f"Existing field {name!r} has an incompatible type; rename it before updating.")
    updates = []
    for feature in features:
        try:
            values = measured_values(feature.geometry(), factor, offset)
        except ValueError as error:
            raise ValueError(f"Feature {feature.id()}: {error}") from None
        updates.append((feature.id(), values + [unit, factor, offset]))
    was_editing = layer.isEditable()
    if not was_editing and not layer.startEditing():
        raise ValueError("Layer cannot be edited.")
    layer.beginEditCommand("Update COGO attributes")
    try:
        for name, kind in required:
            if layer.fields().lookupField(name) < 0 and not layer.addAttribute(QgsField(name, kind)):
                raise ValueError(f"Cannot add field {name}.")
        layer.updateFields()
        from qgis.core import QgsVariantUtils
        feature_by_id = {f.id(): f for f in features}
        for fid, values in updates:
            for (name, _), value in zip(required, values):
                index = layer.fields().lookupField(name)
                old_index = feature_by_id[fid].fields().lookupField(name)
                if only_missing and old_index >= 0 and not QgsVariantUtils.isNull(feature_by_id[fid][old_index]):
                    continue
                if not layer.changeAttributeValue(fid, index, value):
                    raise ValueError(f"Cannot update feature {fid}, field {name}.")
        layer.endEditCommand()
    except Exception:
        layer.destroyEditCommand()
        if not was_editing:
            layer.rollBack()
        raise
    layer.triggerRepaint()
    return len(updates)
