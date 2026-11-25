import math
import unittest

from cogo_workbench.engine import (Course, angle, bearing, bowditch, circle_parameters,
                                  closure, format_bearing, inverse, traverse)


class EngineTests(unittest.TestCase):
    def test_bearings(self):
        for text, expected in [('N45:30:00E', 45.5), ('S45°30\'00"E', 134.5),
                               ('S45-30-00W', 225.5), ('N45.5W', 314.5), ('E', 90),
                               ('360', 0), ('45.3030', 45.303)]:
            self.assertAlmostEqual(bearing(text), expected)
        for a in [0, 0.0001, 45.123456, 90, 135, 180, 225, 270, 359.999]:
            self.assertAlmostEqual(bearing(format_bearing(a)), a, places=5)
        self.assertEqual([format_bearing(a) for a in (0, 90, 180, 270)], ['N', 'E', 'S', 'W'])
        for text in ['N91E', '361', '-5', 'NaN', 'N45:60E', '1.5:20', '45:20:60', 'north']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                bearing(text)

    def test_square_and_grid_conversion(self):
        courses = [Course('Line', direction, '100') for direction in ['N', 'E', 'S', 'W']]
        segments = traverse((500000, 1000000), courses, 0.3048, 90)
        self.assertAlmostEqual(segments[0].end[0], 500030.48)
        self.assertAlmostEqual(closure(segments, segments[0].start)[2], 0, places=8)
        self.assertAlmostEqual(closure(segments, segments[0].start)[3], 121.92)

    def test_curves(self):
        for side, expected_x in [('R', 100), ('L', -100)]:
            for measure, value in [('Arc', str(50 * math.pi)), ('Chord', str(math.sqrt(20000))),
                                   ('Delta', '90:00:00')]:
                segment = traverse((1000000, 2000000), [Course('Curve', 'N', value, '100', measure, side)])[0]
                self.assertAlmostEqual(segment.end[0], 1000000 + expected_x)
                self.assertAlmostEqual(segment.end[1], 2000100)
                radius, arc, delta, turn = circle_parameters(segment.start, segment.mid, segment.end)
                self.assertAlmostEqual(radius, 100, places=7)
                self.assertAlmostEqual(arc, 50 * math.pi, places=7)
                self.assertAlmostEqual(delta, 90, places=7)
                self.assertEqual(turn, side)
        segment = traverse((0, 0), [Course('Curve', '45', '90', '100', 'Delta', 'R', 'Chord')])[0]
        self.assertAlmostEqual(segment.end[0], 100)
        self.assertAlmostEqual(segment.end[1], 100)

    def test_major_curve(self):
        segment = traverse((0, 0), [Course('Curve', 'N', '270', '10', 'Delta', 'R')])[0]
        radius, arc, delta, side = circle_parameters(segment.start, segment.mid, segment.end)
        self.assertAlmostEqual(delta, 270)
        self.assertAlmostEqual(arc, math.pi * 15)
        self.assertEqual(side, 'R')

    def test_bowditch(self):
        segments = traverse((0, 0), [Course('Line', a, d) for a, d in
                                    [('0', '100'), ('90', '100'), ('180', '99'), ('270', '100')]])
        adjusted = bowditch(segments, (0, 0))
        self.assertAlmostEqual(closure(adjusted, (0, 0))[2], 0, places=10)
        self.assertAlmostEqual(adjusted[0].end[1], 100 - 100 / 399)
        target = (0.5, 0.5)
        self.assertAlmostEqual(closure(bowditch(segments, target), target)[2], 0, places=10)
        curve = traverse((0, 0), [Course('Curve', '0', '90', '10', 'Delta')])
        with self.assertRaises(ValueError):
            bowditch(curve, (0, 0))

    def test_invalid_values(self):
        for course in [Course('Line', '0', 'nan'), Course('Line', '0', '-1'),
                       Course('Curve', '0', '21', '10', 'Chord'),
                       Course('Curve', '0', '360', '10', 'Delta'),
                       Course('Curve', '0', '90', '0', 'Delta')]:
            with self.assertRaisesRegex(ValueError, 'Course 1'):
                traverse((0, 0), [course])
        with self.assertRaises(ValueError):
            inverse((1, 1), (1, 1))
        with self.assertRaises(ValueError):
            traverse((0, 0), [])


if __name__ == '__main__':
    unittest.main()
