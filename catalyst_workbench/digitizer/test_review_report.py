"""Scientific export invariants and per-series review artifacts."""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image

from digitizer.session import new_session, set_calibration, add_points, export_session, validate_session_readings
from digitizer.review_report import series_records


class ReadingIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source.png'
        with Image.new('RGB', (240, 180), 'white') as image:
            image.save(source)
        self.session, image = new_session(source, output_root=self.root / 'readings'); image.close()
        cal = {'x': {'p1': [20,150], 'p2': [220,150], 'v1': 100, 'v2': 500, 'name': 'Temperature', 'unit': '°C'},
               'y': {'p1': [20,150], 'p2': [20,30], 'v1': 0, 'v2': 100, 'name': 'Conversion', 'unit': '%'}}
        set_calibration(self.session, cal)
        self.session['roi'] = [20,30,220,150]
        self.session['figure_label'] = 'Figure 1'
        add_points(self.session, [{'px':60,'py':110}, {'px':100,'py':70}], 'Sample A', sample_label='A')

    def assert_failed_unchanged(self, text):
        before = copy.deepcopy(self.session)
        contents = {p: p.read_bytes() for p in Path(self.session['run_dir']).rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, text):
            export_session(self.session, {'reviewed':True, 'notes':'Must not be committed'})
        self.assertEqual(before, self.session)
        self.assertEqual(contents, {p:p.read_bytes() for p in Path(self.session['run_dir']).rglob('*') if p.is_file()})

    def test_stale_values_cannot_be_exported_or_marked_reviewed(self):
        self.session['points'][0]['y'] += 1
        self.assert_failed_unchanged('数值或像素分辨率')

    def test_pixel_resolution_is_verified_too(self):
        self.session['points'][0]['y_pixel_resolution'] = 0
        self.assert_failed_unchanged('数值或像素分辨率')

    def test_calibration_change_requires_recalculation(self):
        self.session['calibration']['x']['v2'] = 600
        self.assert_failed_unchanged('数值或像素分辨率')

    def test_wrong_calibration_version_and_duplicate_ids_fail(self):
        self.session['points'][0]['calibration_id']='old'
        self.assert_failed_unchanged('版本不一致')
        self.session['points'][0]['calibration_id']=self.session['calibration_id']
        self.session['points'][1]['point_id']=self.session['points'][0]['point_id']
        self.assert_failed_unchanged('编号缺失或重复')

    def test_roi_changed_to_another_panel_cannot_export_existing_points(self):
        self.session['roi']=[150,30,220,150]
        self.assert_failed_unchanged('当前绘图区之外')

    def test_metadata_roi_is_checked_before_changes(self):
        before=copy.deepcopy(self.session)
        with self.assertRaisesRegex(ValueError,'绘图区为空或超出图片'):
            export_session(self.session,{'roi':[0,0,900,900], 'reviewed':True})
        self.assertEqual(before,self.session)

    def test_nonfinite_and_boolean_points_fail_without_writes(self):
        self.session['points'][0]['px']=True
        self.assert_failed_unchanged('是/否')
        self.session['points'][0]['px']=float('inf')
        self.assert_failed_unchanged('无效数值')

    def test_negative_values_are_not_clipped_or_rejected(self):
        cal=copy.deepcopy(self.session['calibration']);cal['y']['v1']=-100
        set_calibration(self.session,cal)
        export_session(self.session)
        self.assertLess(self.session['points'][0]['y'],0)

    def test_tiny_log_values_cannot_be_wrong_within_an_absolute_tolerance(self):
        cal=copy.deepcopy(self.session['calibration'])
        cal['y'].update(v1=1e-120,v2=1e-100,scale='log10')
        set_calibration(self.session,cal)
        self.session['points'][0]['y']*=2
        self.assert_failed_unchanged('数值或像素分辨率')

    def test_export_generates_review_artifacts_and_csv_references(self):
        export_session(self.session)
        folder=Path(self.session['last_export_dir'])
        for name in ('系列核对清单.csv','图例与数值匹配.png','逐系列核对.html'):
            self.assertTrue((folder/name).is_file(),name)
        with (folder/'系列核对清单.csv').open(encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f))
        self.assertEqual(rows[0]['CSV行号（含表头）'],'2;3')
        self.assertEqual(rows[0]['可直接进入机器学习'],'False')
        self.assertEqual(rows[0]['审核状态'],'unreviewed')
        html=(folder/'逐系列核对.html').read_text(encoding='utf8')
        self.assertIn('../source.png',html)
        self.assertIn(self.session['points'][0]['point_id'],html)
        self.assertNotIn('<script src=',html)

    def test_color_trace_is_sampling_not_independent_experiments(self):
        for p in self.session['points']:p['method']='color_trace'
        export_session(self.session,{'reviewed':True})
        row=series_records(self.session)[0]
        self.assertEqual(row['value_origin'],'image_curve_samples_approximate')
        self.assertFalse(row['ml_ready'])
        with (Path(self.session['last_export_dir'])/'读数数据.csv').open(encoding='utf-8-sig',newline='') as f:
            self.assertEqual({r['value_origin'] for r in csv.DictReader(f)},{'image_curve_samples_approximate'})

    def test_truthy_string_is_not_review_confirmation(self):
        export_session(self.session,{'reviewed':'false'})
        self.assertIs(self.session['reviewed'],False)

    def test_formula_and_html_labels_are_not_executed(self):
        label='=HYPERLINK("example") <script>alert(1)</script>'
        for p in self.session['points']:p['series_label']=label
        export_session(self.session)
        folder=Path(self.session['last_export_dir'])
        with (folder/'系列核对清单.csv').open(encoding='utf-8-sig',newline='') as f:
            self.assertTrue(next(csv.DictReader(f))['系列名称候选'].startswith("'="))
        html=(folder/'逐系列核对.html').read_text(encoding='utf8')
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('&lt;script&gt;',html)

    def test_bar_category_axis_is_never_presented_as_numeric_measurement(self):
        self.session['chart_type']='bar'
        for p in self.session['points']:p['category']='A'
        export_session(self.session)
        self.assertNotIn('x',series_records(self.session)[0]['axes'])
        html=(Path(self.session['last_export_dir'])/'逐系列核对.html').read_text(encoding='utf8')
        self.assertIn('类别轴，见 category',html)


if __name__=='__main__':unittest.main()
