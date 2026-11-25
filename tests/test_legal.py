import math
from pathlib import Path
import unittest

from cogo_workbench.engine import closure, traverse
from cogo_workbench.legal import parse_description, units_match


RECTANGLE = '''Beginning at a found iron rod;
thence North 00 degrees 00 minutes 00 seconds East, a distance of 100.00 feet to an iron rod;
thence N90°00'00"E 200 feet;
thence South 0 degrees East, 100 feet;
thence due West, 200 feet to the point of beginning.'''


class LegalTests(unittest.TestCase):
    def calls(self, text):
        rows = parse_description(text)
        self.assertFalse([row.issue for row in rows if row.phase == 'Boundary' and row.issue], rows)
        return [row.course for row in rows if row.phase == 'Boundary' and row.course]

    def test_rectangle_and_original_text(self):
        rows = parse_description(RECTANGLE)
        courses = self.calls(RECTANGLE)
        self.assertEqual(len(courses), 4)
        self.assertEqual(courses[0].direction, 'N00:00:00E')
        self.assertIn('iron rod', rows[1].source)
        self.assertTrue(all(row.unit == 'Feet (choose definition)' for row in rows if row.course))
        self.assertAlmostEqual(closure(traverse((0, 0), courses), (0, 0))[2], 0)

    def test_formats_and_wrapping(self):
        courses = self.calls('Thence N45-30-00E 1,234.50 ft.; thence S45.5W for 1234.50 ft.')
        self.assertEqual(courses[0].direction, 'N45-30-00E')
        self.assertEqual(courses[0].distance, '1234.50')
        self.assertAlmostEqual(closure(traverse((0, 0), courses), (0, 0))[2], 0, places=8)
        self.assertEqual(len(self.calls('North 45 degrees\n30 minutes East 10 meters\nSouth 45:30 West 10 meters')), 2)
        self.assertEqual(len(self.calls('N45E 10 feet. S45W 10 feet.')), 2)
        dotted = self.calls('thence N. 45° 30\' 15" E., 100.00 feet; thence S. 45° 30\' 15" W., 100.00 feet')
        self.assertEqual(dotted[0].direction, 'N45:30:15E')
        self.assertEqual(dotted[0].distance, '100.00')
        along = self.calls('thence run S. 00° 12\' 30" E. along the west line, a distance of 400.00 feet; '
                           'thence N. 00° 12\' 30" W., 400.00 feet')
        self.assertEqual(along[0].direction, 'S00:12:30E')
        self.assertEqual(along[0].distance, '400.00')

    def test_commencement_excluded(self):
        text = 'Commencing at a monument; thence N10E 500 feet to the TRUE POINT OF BEGINNING; ' + RECTANGLE
        rows = parse_description(text)
        ties = [row for row in rows if row.phase == 'Commencement' and row.course]
        self.assertEqual(len(ties), 1)
        self.assertEqual(len(self.calls(text)), 4)
        self.assertIn('500 feet', ties[0].source)

    def test_section30_closing_pob_and_intervening_along_clauses(self):
        text = (Path(__file__).parent / 'fixtures' / 'section30.txt').read_text(encoding='utf-8')
        rows = parse_description(text)
        self.assertEqual(sum(row.course is not None for row in rows), 5)
        self.assertTrue(any('All calls are boundary' in row.issue for row in rows))
        for source in [text, '**' + text + '**']:
            rows = parse_description(source, boundary_only=True)
            self.assertFalse([row.issue for row in rows if row.issue], rows)
            courses = [row.course for row in rows if row.course]
            self.assertEqual([c.direction for c in courses],
                ['N00:20:13W', 'N88:42:51E', 'S24:53:48E', 'S25:21:21W', 'S89:40:22W'])
            self.assertEqual([c.distance for c in courses], ['927.70', '529.46', '304.57', '815.60', '404.10'])
            self.assertTrue(all(row.phase == 'Boundary' for row in rows))

    def test_commencement_override_keeps_ties_only_when_requested(self):
        text = 'Commencing at a monument; thence N10E 500 feet to the POB; ' + RECTANGLE
        rows = parse_description(text, boundary_only=True)
        self.assertEqual(len([row for row in rows if row.course]), 5)
        self.assertTrue(all(row.phase == 'Boundary' for row in rows))
        self.assertEqual(len(self.calls(text)), 4)

    def test_running_calls_and_curve_wording(self):
        section9 = (
            "Section 9: For a POINT OF BEGINNING, commence at the SE corner of the SW of the SW 1/4 of "
            "the NE 1/4 of Section 9, Township 4 South, Range 16 East, thence run West along the South line "
            "of said SW 1/4 a distance of 208 feet; thence run North parallel to the East line of the "
            "SW 1/4, a distance of 208 feet; thence run East parallel to the South line of the "
            "SW 1/4 a distance of 208 feet; thence run South along said East line a distance of 208 feet "
            "and to the POINT OF BEGINNING.")
        courses = self.calls(section9)
        self.assertEqual([c.direction for c in courses], ['W', 'N', 'E', 'S'])
        self.assertEqual([c.distance for c in courses], ['208', '208', '208', '208'])
        self.assertAlmostEqual(closure(traverse((0, 0), courses), (0, 0))[2], 0, places=6)
        along = ("Beginning at a monument; thence North 45°00'00\" East along the property line 50 feet; "
                 "thence South, 50 feet to the point of beginning.")
        self.assertEqual([c.distance for c in self.calls(along)], ['50', '50'])
        mixed = parse_description("thence North 100 feet East 50 feet")
        self.assertTrue(any(row.issue for row in mixed))
        self.assertFalse(any(row.course for row in mixed))
        curve = (
            "Thence along the arc of a curve to the right, concave Westerly, having a radius of 100 feet, "
            "a central angle of 180 degrees, an arc length of 314.159265 feet, a chord bearing of North "
            "and a chord distance of 200 feet; thence South, 200 feet")
        courses = self.calls(curve)
        self.assertEqual(courses[0].kind, 'Curve')
        self.assertEqual(courses[0].side, 'R')
        self.assertAlmostEqual(closure(traverse((0, 0), courses), (0, 0))[2], 0, places=4)
        circle = (
            "Thence along the arc of a curve to the right, concave Easterly, having a radius of 50 feet, "
            "a central angle of 180 degrees, an arc length of 157.079633 feet, a chord bearing of North "
            "and a chord distance of 100 feet; thence along the arc of a curve to the right, concave "
            "Westerly, having a radius of 50 feet, a central angle of 180 degrees, an arc length of "
            "157.079633 feet, a chord bearing of South and a chord distance of 100 feet")
        courses = self.calls(circle)
        self.assertEqual([c.kind for c in courses], ['Curve', 'Curve'])
        self.assertAlmostEqual(closure(traverse((0, 0), courses), (0, 0))[2], 0, places=4)

    def test_curves(self):
        for turn in ['right', 'left']:
            text = f'Thence along a curve to the {turn}, radius of 100 feet, arc length of 157.079633 feet, chord bearing N45E, chord distance of 141.421356 feet'
            course = self.calls(text)[0]
            self.assertEqual(course.kind, 'Curve')
            self.assertEqual(course.basis, 'Chord')
            self.assertEqual(course.side, turn[0].upper())
            self.assertAlmostEqual(traverse((0, 0), [course])[0].delta, 90, places=5)
        course = self.calls('Thence curve to the right, radius 100 meters, delta 90 degrees 0 minutes 0 seconds, tangent bearing North 0 East')[0]
        self.assertEqual(course.measure, 'Delta')
        self.assertAlmostEqual(traverse((0, 0), [course])[0].arc, 50 * math.pi)
        whose = self.calls('Thence along a curve to the right having a radius of 100 feet and whose chord bears '
                           'North 45 degrees East a distance of 141.421356 feet')[0]
        self.assertEqual((whose.basis, whose.measure, whose.side), ('Chord', 'Chord', 'R'))
        bears = ('Thence along a curve to the right, having a radius of 100.00 feet, a central angle of 90 degrees, '
                 'a chord distance of 141.42 feet, the chord of which bears North 45 degrees East')
        course = self.calls(bears)[0]
        self.assertEqual(course.basis, 'Chord')
        self.assertAlmostEqual(traverse((0, 0), [course])[0].delta, 90, places=4)
        course = self.calls('Thence curve to the left, radius 100 feet, chord length 100 feet, chord bearing N45E')[0]
        self.assertEqual(course.measure, 'Chord')
        self.assertEqual(self.calls('Thence along the right-of-way on a curve to the left, radius 100 feet, chord length 100 feet, chord bearing N45E')[0].side, 'L')

    def test_invalid_or_ambiguous_calls_remain_visible(self):
        for text in [
            'Thence northeasterly along the adjoining parcel 100 feet',
            'Thence N91E 100 feet', 'Thence N45:60E 100 feet',
            'Thence N45E -100 feet', 'Thence N45E 0 feet',
            'Thence N45E 100 feet more or less', 'Thence N45E 100 rods',
            'Thence N45E 100 yards',
            'Thence N45E 100 inches', 'Thence N45E 100 feetx',
            'Thence N45E 100 1/2', 'Thence N45E 100,50 to an iron rod',
            'Thence N45E 100 feet and S45W 100 feet',
            'Thence N45E -100 feet, a distance of 100 feet',
            'Thence N45E along a line for 50 feet, a distance of 100 feet',
            'Thence curve right radius 100 feet arc length 30 feet',
            'Thence curve right radius 100 feet arc length 30 feet bearing N45E',
            'Thence curve right radius 100 feet arc length 30 feet chord distance 50 feet chord bearing N45E',
            'Thence curve right radius 100 feet arc length 30 feet delta 90 degrees chord bearing N45E',
            'Thence curve right radius 100 feet chord length 250 feet chord bearing N45E',
            'Thence major curve right radius 100 feet chord length 100 feet chord bearing N45E',
            'Thence N45E 100 feet; EXCEPT the west 20 feet',
            'Commencing at a monument; thence N45E 100 feet',
            'Thence N45E 100 feet; thence along the lot line to the creek',
            'Thence N45E 100 feet; thence S45W 100 meters',
            'Thence North 00.2013 West, 927.70 feet',
        ]:
            with self.subTest(text=text):
                rows = parse_description(text)
                self.assertTrue(any(row.issue for row in rows if row.phase == 'Boundary'), rows)
                self.assertTrue(any(row.source for row in rows))

    def test_packed_decimal_and_unit_choice(self):
        rows = parse_description('Thence North 00.2013 West, 927.70 feet')
        self.assertTrue(any('00:20:13' in row.issue for row in rows))
        self.assertFalse(any(row.course for row in rows))
        self.assertTrue(units_match('Feet (choose definition)', 'US survey feet'))
        self.assertFalse(units_match('Meters', 'US survey feet'))
        self.assertTrue(units_match('', 'Meters'))

    def test_explicit_and_inherited_units(self):
        rows = parse_description('Thence N45E 100 US survey feet; thence S45W 100')
        self.assertEqual([r.unit for r in rows if r.course], ['US survey feet'] * 2)
        self.assertEqual(len(self.calls('Thence N45E 100 international feet')), 1)
        rows = parse_description('All distances are in feet; thence N45E 100; thence S45W 100')
        self.assertEqual([r.unit for r in rows if r.course], ['Feet (choose definition)'] * 2)
        self.assertEqual(len(self.calls(RECTANGLE + '; containing 0.46 acres, more or less')), 4)

    def test_shipped_examples_close(self):
        folder = Path(__file__).resolve().parent.parent / 'examples'
        for path in folder.glob('legal_*.txt'):
            with self.subTest(path=path.name):
                courses = self.calls(path.read_text(encoding='utf-8'))
                self.assertAlmostEqual(closure(traverse((0, 0), courses), (0, 0))[2], 0, places=5)

    def test_input_bounds(self):
        for text in ['', ' ' * 10, 'x' * 100001]:
            with self.assertRaises(ValueError):
                parse_description(text)


if __name__ == '__main__':
    unittest.main()
