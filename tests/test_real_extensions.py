import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from scrtool.core import infer_header
from scrtool.vectors import marker_centers, axis_value


class ExtensionTests(unittest.TestCase):
    def test_scientific_multiline_header(self):
        self.assertEqual(infer_header('Cu content\n(wt%) a'), ('cu_content', 'wt%'))
        self.assertEqual(infer_header('Si/Al\nratio'), ('si_al_ratio', '1'))

    def test_vector_deduplicates_fill_and_stroke(self):
        obj = dict(x0=10, x1=16, top=20, bottom=26, width=6, height=6, non_stroking_color=[1,0,0])
        line = dict(obj, width=100, x1=110)
        page = SimpleNamespace(curves=[obj, dict(obj), line])
        profile = {'region': [0,0,200,200]}
        selector = dict(catalyst='A', color=[1,0,0], width=[5,7], height=[5,7], expected_count=1)
        centers = marker_centers(page, profile, selector)
        self.assertEqual([(p['x'],p['y']) for p in centers], [(13,23)])

    def test_legend_exclusion_and_count_guard(self):
        obj = dict(x0=10, x1=16, top=20, bottom=26, width=6, height=6, non_stroking_color=[1,0,0])
        page = SimpleNamespace(curves=[obj])
        profile = dict(region=[0,0,200,200], exclude=[[0,0,30,30]])
        selector = dict(catalyst='A', color=[1,0,0], width=[5,7], height=[5,7], expected_count=1)
        with self.assertRaises(ValueError):
            marker_centers(page, profile, selector)

    def test_dual_y_axes(self):
        left = dict(pixel1=500,pixel2=100,value1=5,value2=30)
        right = dict(pixel1=500,pixel2=100,value1=0,value2=100)
        self.assertEqual(axis_value(300,left),17.5)
        self.assertEqual(axis_value(300,right),50)

    def test_grayscale_marker_color(self):
        obj = dict(x0=10, x1=16, top=20, bottom=26, width=6, height=6, non_stroking_color=0.502)
        page = SimpleNamespace(curves=[obj])
        profile = {'region': [0,0,200,200]}
        selector = dict(catalyst='gray', color=0.502, width=[5,7], height=[5,7], expected_count=1)
        self.assertEqual(len(marker_centers(page, profile, selector)), 1)

    def test_broken_axis_segments(self):
        axis = dict(segments=[
            dict(pixel1=100, pixel2=200, value1=0, value2=5),
            dict(pixel1=210, pixel2=310, value1=15, value2=20),
        ])
        self.assertEqual(axis_value(150, axis), 2.5)
        self.assertEqual(axis_value(260, axis), 17.5)
        with self.assertRaises(ValueError):
            axis_value(205, axis)


class RealCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'output/real_paper/combined_reviewed.json'
        if not path.exists():
            raise unittest.SkipTest('Run real-paper case study first')
        cls.records = json.loads(path.read_text(encoding='utf-8'))

    def test_known_figures_and_conditions(self):
        a = [r for r in self.records if r.get('figure') == 'Fig.1a']
        b = [r for r in self.records if r.get('figure') == 'Fig.1b']
        self.assertEqual((len(a),len(b)), (46,55))
        self.assertTrue(all(r['estimated'] for r in a+b))
        self.assertTrue(all(r['conditions']['ghsv']['value']==150000 for r in a+b))
        red = [r['value'] for r in b if r['catalyst']=='Cu-0.5']
        gray = [r['value'] for r in b if r['catalyst']=='Cu-1.4']
        self.assertLess(max(red),30)
        self.assertGreater(max(gray),80)

    def test_no_conflicted_composition_approved(self):
        conflicts = [r for r in self.records if 'cross_source_conflict' in r['issues']]
        self.assertEqual({r['catalyst'] for r in conflicts}, {'Cu-0.5','Cu-1.4'})
        self.assertTrue(all(r['review_status']=='pending' for r in conflicts))

    def test_merged_table_values_retained(self):
        for cat in ['Cu-0.5','Cu-1.4','Cu-2.5']:
            rs = [r for r in self.records if r['catalyst']==cat and r['property']=='si_al_ratio']
            self.assertEqual([r['value'] for r in rs], [11.9])

    def test_curator_is_not_claimed_as_human(self):
        self.assertTrue(all(r.get('review_level')=='agent_checked' for r in self.records if r['review_status']=='approved'))


if __name__ == '__main__':
    unittest.main()
