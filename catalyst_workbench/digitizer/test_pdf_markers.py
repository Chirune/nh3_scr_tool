import unittest
from types import SimpleNamespace
from .pdf_markers import vector_markers


class VectorMarkerTests(unittest.TestCase):
    def test_duplicate_fill_stroke_legend_and_connecting_line(self):
        rect = dict(x0=10, x1=16, top=20, bottom=26, fill=True, stroke=False,
                    non_stroking_color=(0, 0, 1), stroking_color=(0, 0, 1))
        copy = dict(rect, fill=False, stroke=True)
        legend = dict(rect, x0=70, x1=76)
        line = dict(rect, x1=60, bottom=21)
        page = SimpleNamespace(curves=[], rects=[rect, copy, legend, line])
        series = [dict(label='S1', shape='square', color=[0, 0, 1])]
        found = vector_markers(page, [0, 0, 100, 100], series, [[65, 0, 90, 40]])
        self.assertEqual(len(found['S1']), 1)
        self.assertEqual((found['S1'][0]['x_pdf'], found['S1'][0]['y_pdf']), (13, 23))
        self.assertEqual(found['S1'][0]['review_status'], 'unreviewed')

    def test_ambiguous_legend_assignment_refused(self):
        obj = dict(x0=10, x1=16, top=20, bottom=26, fill=True, stroke=False,
                   non_stroking_color=(1, 0, 0))
        page = SimpleNamespace(curves=[], rects=[obj])
        with self.assertRaises(ValueError):
            vector_markers(page, [0, 0, 100, 100], [
                dict(label='S1', shape='square', color=[1, 0, 0]),
                dict(label='S2', shape='square', color=[1, 0, 0])])

    def test_circle_and_diamond_are_not_exchanged(self):
        base = dict(x0=10, x1=16, top=20, bottom=26, fill=True, stroke=False,
                    non_stroking_color=(1, 0, 0))
        circle = dict(base, path=[('m',), ('c',), ('c',), ('c',), ('c',), ('h',)], pts=[])
        diamond = dict(base, path=[('m',), ('l',), ('l',), ('l',), ('l',)],
                       pts=[(13, 20), (16, 23), (13, 26), (10, 23), (13, 20)])
        page = SimpleNamespace(curves=[circle, diamond], rects=[])
        found = vector_markers(page, [0, 0, 100, 100], [
            dict(label='round', shape='circle', color=[1, 0, 0]),
            dict(label='diamond', shape='diamond', color=[1, 0, 0])])
        self.assertEqual([len(found[x]) for x in ('round', 'diamond')], [1, 1])

    def test_hollow_symbol_uses_its_stroke_not_inactive_fill_colour(self):
        obj = dict(x0=10, x1=16, top=20, bottom=26, fill=False, stroke=True,
                   non_stroking_color=(1, 0, 0), stroking_color=(.75, 0, 0),
                   path=[('m',), ('c',), ('c',), ('c',), ('c',), ('h',)], pts=[])
        page = SimpleNamespace(curves=[obj], rects=[])
        series = [dict(label='outline', shape='circle', color=[.75, 0, 0]),
                  dict(label='inactive', shape='circle', color=[1, 0, 0])]
        found = vector_markers(page, [0, 0, 100, 100], series)
        self.assertEqual([len(found[x]) for x in ('outline', 'inactive')], [1, 0])
