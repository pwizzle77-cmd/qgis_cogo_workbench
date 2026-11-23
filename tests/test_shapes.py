"""Parcel shapes the traverse can close. No QGIS required.

Straight rings, a concave notch, clockwise and counter-clockwise order,
quarter-circle, semicircle, full circle, a stadium, a concave bite,
a major arc, and a rounded rectangle. Spirals and holes are not shapes
this tool builds.
"""
import math
import unittest

from cogo_workbench.engine import Course, closure, inverse, traverse


def lines_through(points):
    courses = []
    for start, end in zip(points, points[1:] + points[:1]):
        azimuth, distance = inverse(start, end)
        courses.append(Course('Line', repr(azimuth), repr(distance)))
    return courses


def ring_area(points):
    total = 0.0
    for start, end in zip(points, points[1:] + points[:1]):
        total += start[0] * end[1] - end[0] * start[1]
    return abs(total) / 2.0


def curve(direction, delta, radius, side, basis='Tangent'):
    return Course('Curve', direction, str(delta), str(radius), 'Delta', side, basis)


# Straight rings. The last point is not repeated.
TRIANGLE = [(0, 0), (100, 0), (40, 80)]
IRREGULAR = [(0, 0), (120, 10), (90, 80), (15, 70)]
NOTCH = [(0, 0), (100, 0), (100, 100), (60, 100), (60, 40), (0, 40)]
CLOCKWISE = [(0, 0), (100, 0), (100, 100), (0, 100)]
COUNTER = [(0, 0), (0, 100), (100, 100), (100, 0)]
SKINNY = [(0, 0), (1000, 0), (1000, 0.25), (0, 0.25)]

# Tangent north, right 90: ends at (100, 100). Then the two radii close a quarter disk.
QUARTER = [curve('N', 90, 100, 'R'), Course('Line', 'S', '100'), Course('Line', 'W', '100')]
# North semicircle of radius 100 on the chord from (0, 0) to (200, 0).
SEMICIRCLE = [curve('N', 180, 100, 'R'), Course('Line', 'W', '200')]
# Circle of radius 50 centered at (50, 0): north half, then the south half back.
CIRCLE = [curve('N', 180, 50, 'R'), curve('W', 180, 50, 'R', 'Chord')]
# 100 by 100 box with a semicircle of radius 50 added on the east side.
# Chord-north and side L bows east. Side R bows west, into the box.
BULGE = [Course('Line', 'E', '100'), curve('N', 180, 50, 'L', 'Chord'),
         Course('Line', 'W', '100'), Course('Line', 'S', '100')]
BITE = [Course('Line', 'E', '100'), curve('N', 180, 50, 'R', 'Chord'),
        Course('Line', 'W', '100'), Course('Line', 'S', '100')]
# Stadium: outward semicircle on the east and the west.
STADIUM = [Course('Line', 'E', '100'), curve('N', 180, 50, 'L', 'Chord'),
           Course('Line', 'W', '100'), curve('S', 180, 50, 'L', 'Chord')]
# Major segment, central angle 270 degrees, radius 10, plus its chord.
MAJOR = [curve('N', 270, 10, 'R'), Course('Line', repr(inverse((10, -10), (0, 0))[0]),
                                           repr(inverse((10, -10), (0, 0))[1]))]
# Straights of 100 and four right-hand quarter-circles of radius 10.
ROUNDED = []
for _direction in ('E', 'S', 'W', 'N'):
    ROUNDED.append(Course('Line', _direction, '100'))
    ROUNDED.append(curve(_direction, 90, 10, 'R'))


STRAIGHT = {
    'triangle': (TRIANGLE, 3),
    'irregular quadrilateral': (IRREGULAR, 4),
    'concave notch': (NOTCH, 6),
    'clockwise square': (CLOCKWISE, 4),
    'counter-clockwise square': (COUNTER, 4),
    'skinny rectangle': (SKINNY, 4),
}

CURVED = {
    'quarter circle': (QUARTER, math.pi * 10000 / 4, 3),
    'semicircle': (SEMICIRCLE, math.pi * 5000, 2),
    'full circle': (CIRCLE, math.pi * 2500, 2),
    'convex bulge': (BULGE, 10000 + math.pi * 1250, 4),
    'concave bite': (BITE, 10000 - math.pi * 1250, 4),
    'stadium': (STADIUM, 10000 + math.pi * 2500, 4),
    'major arc': (MAJOR, 50 * (1 + 1.5 * math.pi), 2),
    'rounded rectangle': (ROUNDED, 14000 + math.pi * 100, 8),
}


class ShapeTests(unittest.TestCase):
    def test_straight_rings_close_with_their_area(self):
        for name, (points, corners) in STRAIGHT.items():
            with self.subTest(name=name):
                courses = lines_through(points)
                segments = traverse((0, 0), courses)
                self.assertEqual(len(segments), corners)
                self.assertAlmostEqual(closure(segments, (0, 0))[2], 0, places=6)
                self.assertAlmostEqual(ring_area([segment.start for segment in segments]),
                                       ring_area(points), places=6)

    def test_curved_rings_close(self):
        for name, (courses, _area, corners) in CURVED.items():
            with self.subTest(name=name):
                segments = traverse((0, 0), courses)
                self.assertEqual(len(segments), corners)
                self.assertAlmostEqual(closure(segments, (0, 0))[2], 0, places=5)
                self.assertTrue(any(segment.mid is not None for segment in segments))

    def test_described_notch_closes(self):
        from cogo_workbench.legal import parse_description
        text = ('Beginning at a corner; thence East, 100 feet; thence North, 100 feet; '
                'thence West, 40 feet; thence South, 60 feet; thence West, 60 feet; '
                'thence South, 40 feet to the point of beginning.')
        rows = parse_description(text)
        courses = [row.course for row in rows if row.course]
        self.assertEqual(len(courses), 6)
        segments = traverse((0, 0), courses)
        self.assertAlmostEqual(closure(segments, (0, 0))[2], 0, places=6)
        self.assertAlmostEqual(ring_area([segment.start for segment in segments]), 6400, places=4)


if __name__ == '__main__':
    unittest.main()
