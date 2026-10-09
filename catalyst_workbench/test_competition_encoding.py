"""Regression checks for model-input safety and readable local exports."""
import copy
import csv
import json
from pathlib import Path
import unittest

import paper_encoding as encoding
import paper_workspace as work
import ml_preparation as prep
import test_paper_encoding as fixtures
import test_ml_preparation as model_fixtures


class CompetitionEncodingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EncodingTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_inspection_x_masks_unknown_and_post_result_assays_but_keeps_source_facts(self):
        project, _, record = self.fixture.packet(features={'active_metals': ['Cu'], 'support': 'CeO2',
            'BET_surface_area_m2_g': 60, 'metal_particle_size_nm': 8, 'ce3_fraction_pct': 20})
        record['feature_availability'] = {'BET_surface_area_m2_g': 'before_prediction',
            'metal_particle_size_nm': 'after_prediction'}
        work.put_record(project, record, reviewer='synthetic-reviewer', confirm=True)
        result = self.fixture.build(self.fixture.export(project))
        raw = result['observations'][0]['X_raw']; x = result['encoded_rows'][0]['X']
        self.assertEqual(raw['metal_particle_size_nm'], 8)
        self.assertEqual(raw['ce3_fraction_pct'], 20)
        self.assertEqual(x['BET_surface_area_m2_g'], 60)
        self.assertIsNone(x['metal_particle_size_nm']); self.assertIsNone(x['ce3_fraction_pct'])
        self.assertEqual(x['metal_particle_size_nm__missing'], 1)
        self.assertEqual(result['summary']['unavailable_characterization_count'], 2)

    def test_joint_mass_and_volume_fraction_checks_do_not_renormalize(self):
        valid = {'features': {'pd_loading_wt_pct': 1, 'ru_loading_wt_pct': .5, 'metal_loading_wt_pct': 2},
            'conditions': {'feed_O2_vol_pct': 5, 'feed_H2O_vol_pct': 10}}
        self.assertFalse(encoding.feature_issues(valid))
        for change, phrase in [({'features': {'pd_loading_wt_pct': 70, 'ru_loading_wt_pct': 40}}, '之和超过100'),
                ({'features': {'pd_loading_wt_pct': 2, 'metal_loading_wt_pct': 1}}, '大于总活性'),
                ({'conditions': {'feed_O2_vol_pct': 60, 'feed_H2O_vol_pct': 50}}, '体积分数之和')]:
            row = {**copy.deepcopy(valid), **change}; before = copy.deepcopy(row)
            self.assertIn(phrase, '；'.join(encoding.feature_issues(row)))
            self.assertEqual(row, before)
        # Unknown component is not a declared zero or a reason to invent totals.
        self.assertFalse(encoding.feature_issues({'features': {'pd_loading_wt_pct': 1}, 'conditions': {}}))

    def test_export_has_readable_summary_but_scores_and_ids_stay_out_of_x(self):
        _, packet, _ = self.fixture.packet()
        result = self.fixture.build(packet)
        out = Path(encoding.export_encoding(result, self.fixture.root / 'out')['directory'])
        summary = (out / '00_先看这里_编码结果.html').read_text(encoding='utf8')
        self.assertIn('尚未训练模型', summary); self.assertIn('人工核对_逐行摘要.csv', summary)
        with (out / '人工核对_逐行摘要.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0]['统一值y'], '0.9')
        self.assertEqual(rows[0]['论文DOI'], result['observations'][0]['doi'])
        with (out / 'X.csv').open(encoding='utf-8-sig', newline='') as stream:
            fields = csv.DictReader(stream).fieldnames
        for key in ('y', 'doi', 'sample_label', 'quality_score', 'performance_score'):
            self.assertNotIn(key, fields)

    def test_html_escapes_source_labels_and_csv_does_not_execute_formulas(self):
        project, _, record = self.fixture.packet()
        record['sample_label'] = '<script>alert(1)</script>'
        work.put_record(project, record, reviewer='synthetic-reviewer', confirm=True)
        result = self.fixture.build(self.fixture.export(project))
        out = Path(encoding.export_encoding(result, self.fixture.root / 'out')['directory'])
        page = (out / '00_先看这里_编码结果.html').read_text(encoding='utf8')
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('&lt;script&gt;', page)

    def test_real_fold_export_is_aligned_and_has_no_reserved_test_rows(self):
        fixture = model_fixtures.ModelPreparationTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        result, plan = fixture.multi()
        output = prep.export_fold(result, self.fixture.root / 'fold', 'scaled_one_hot', plan['folds'][0]['fold_id'])
        self.assertFalse(output['model_trained']); self.assertFalse(output['test_exported'])
        folder = Path(output['directory'])
        self.assertFalse((folder / 'X_test.csv').exists())
        for part in ('train', 'validation'):
            with (folder / ('X_' + part + '.csv')).open(encoding='utf-8-sig', newline='') as f: xs = list(csv.DictReader(f))
            with (folder / ('y_' + part + '.csv')).open(encoding='utf-8-sig', newline='') as f: ys = list(csv.DictReader(f))
            with (folder / ('行与来源_' + part + '.csv')).open(encoding='utf-8-sig', newline='') as f: ids = list(csv.DictReader(f))
            self.assertEqual(len(xs), len(ys)); self.assertEqual(len(xs), len(ids))
            self.assertFalse({r['observation_id'] for r in ids} & set(plan['outer_test_ids']))
            self.assertTrue(all('y' not in r and 'doi' not in r for r in xs))
        self.assertEqual(json.loads((folder / '本训练折拟合的预处理器.json').read_text(encoding='utf8'))['fit_observation_ids'], plan['folds'][0]['train_ids'])

    def test_fold_export_revalidates_pdf_before_creating_files(self):
        fixture = model_fixtures.ModelPreparationTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        result, plan = fixture.multi()
        packet = encoding.load_handoff(result['input_packets'][0]['path'])
        pdf = Path(packet['article']['local_pdf']); pdf.write_bytes(pdf.read_bytes() + b'\n% changed\n')
        destination = self.fixture.root / 'stale'
        with self.assertRaisesRegex(ValueError, '改变'):
            prep.export_fold(result, destination, 'tree_one_hot', plan['folds'][0]['fold_id'])
        self.assertFalse(destination.exists())

    def test_coverage_distinguishes_reported_from_prediction_available(self):
        fixture = model_fixtures.ModelPreparationTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        _, plan = fixture.multi()
        coverage = {f['field']: f for f in plan['coverage']}
        self.assertEqual(coverage['metal_particle_size_nm']['reported'], 8)
        self.assertEqual(coverage['metal_particle_size_nm']['available_before_prediction'], 0)
        self.assertEqual(coverage['metal_particle_size_nm']['reported_but_unavailable'], 8)
        self.assertEqual(coverage['BET_surface_area_m2_g']['available_before_prediction'], 8)
        self.assertIn('undefined_single_observation', {f['r2_applicability'] for f in plan['folds']})


if __name__ == '__main__':
    unittest.main()
