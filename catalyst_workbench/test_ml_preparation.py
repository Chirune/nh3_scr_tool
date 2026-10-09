"""Model input tests on synthetic records; no model libraries or fitting."""
import copy
import json
from pathlib import Path
import unittest

import ml_preparation as prep
import paper_encoding as encoding
import paper_scoring as scoring
import paper_workspace as work
import test_paper_encoding as fixtures


class ModelPreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EncodingTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def multi(self):
        paths = []
        for i in range(8):
            project, _, record = self.fixture.packet('ml' + str(i), value=40 + i,
                features={'active_metals': ['Cu', 'Zn'] if i % 2 else ['Cu'],
                    'support': 'CeO2' if i < 7 else 'TiO2', 'metal_loading_wt_pct': float(i + 1),
                    'BET_surface_area_m2_g': 40 + i, 'metal_particle_size_nm': 3 + i})
            record['feature_availability'] = {'BET_surface_area_m2_g': 'before_prediction',
                'metal_particle_size_nm': 'after_prediction'}
            work.put_record(project, record, reviewer='synthetic-reviewer', confirm=True)
            paths.append(self.fixture.export(project))
        result = self.fixture.build(*paths)
        return result, prep.build_plan(result)

    def mutate_plan(self, plan):
        plan['plan_fingerprint'] = prep._plan_fingerprint(plan)
        return plan

    def test_no_winner_or_model_accuracy_is_inferred_from_data_preparation(self):
        result, plan = self.multi()
        self.assertFalse(plan['model_trained']); self.assertIsNone(plan['winning_model'])
        self.assertFalse(plan['accuracy_estimated'])
        self.assertEqual(len(plan['recipes']), 4)
        self.assertEqual(plan['encoding_snapshot'], result['snapshot_fingerprint'])
        for row in plan['rows']:
            self.assertNotIn('y', row['X_facts'])
            self.assertNotIn('quality_score', row['X_facts'])
            self.assertNotIn('doi', row['X_facts'])

    def test_same_paper_never_crosses_development_or_outer_test_boundaries(self):
        _, plan = self.multi()
        self.assertTrue(plan['folds'])
        validation_ids = []
        for fold in plan['folds']:
            self.assertFalse(set(fold['train_groups']) & set(fold['validation_groups']))
            self.assertFalse(set(fold['train_groups'] + fold['validation_groups']) & set(plan['outer_test_groups']))
            self.assertFalse(set(fold['train_ids'] + fold['validation_ids']) & set(plan['outer_test_ids']))
            validation_ids += fold['validation_ids']
        train_ids = {r['observation_id'] for r in plan['rows'] if r['split'] == 'train'}
        self.assertEqual(set(validation_ids), train_ids)
        self.assertEqual(len(validation_ids), len(train_ids))

    def test_validation_and_test_extremes_do_not_change_fitted_statistics(self):
        _, plan = self.multi(); fold = plan['folds'][0]
        before = prep.fit_fold_preprocessor(plan, 'scaled_one_hot', fold['fold_id'])
        changed = copy.deepcopy(plan)
        for row in changed['rows']:
            if row['observation_id'] not in fold['train_ids']:
                row['X_facts']['temperature_C'] = 100000
                row['X_facts']['support'] = 'ZrO2'
        self.mutate_plan(changed)
        after = prep.fit_fold_preprocessor(changed, 'scaled_one_hot', fold['fold_id'])
        self.assertEqual(before['numeric'], after['numeric'])
        self.assertEqual(before['vocabulary'], after['vocabulary'])
        self.assertNotIn('ZrO2', after['vocabulary']['support'])
        rows = prep.transform_partition(changed, after)
        self.assertEqual(rows[0]['X']['support__unknown'], 1)

    def test_scaled_tree_native_missing_and_native_categories_use_same_fold(self):
        _, plan = self.multi(); fold = plan['folds'][0]
        target = fold['validation_ids'][0]
        for row in plan['rows']:
            if row['observation_id'] == target:
                row['X_facts']['metal_loading_wt_pct'] = None
                row['X_facts']['support'] = 'ZrO2'
        self.mutate_plan(plan)
        prepared = {key: prep.prepare_fold(plan, key, fold['fold_id']) for key in prep.RECIPES}
        for output in prepared.values():
            self.assertEqual(output['state']['fit_observation_ids'], fold['train_ids'])
            self.assertFalse(output['model_trained'])
        get = lambda key: next(r for r in prepared[key]['validation'] if r['observation_id'] == target)['X']
        self.assertIsNotNone(get('tree_one_hot')['metal_loading_wt_pct'])
        self.assertIsNone(get('native_missing_one_hot')['metal_loading_wt_pct'])
        self.assertEqual(get('scaled_one_hot')['metal_loading_wt_pct__missing'], 1)
        self.assertEqual(get('native_categories')['support'], 'ZrO2')
        self.assertIn('support', prepared['native_categories']['state']['categorical_columns'])
        self.assertTrue(any(k.startswith('active_metals=') for k in get('native_categories')))

    def test_all_missing_training_feature_is_dropped_without_looking_at_validation(self):
        _, plan = self.multi(); fold = plan['folds'][0]
        for row in plan['rows']:
            row['X_facts']['feed_SO2_ppm'] = None if row['observation_id'] in fold['train_ids'] else 500
        self.mutate_plan(plan)
        state = prep.fit_fold_preprocessor(plan, 'scaled_one_hot', fold['fold_id'])
        self.assertNotIn('feed_SO2_ppm', state['columns'])
        self.assertIn('feed_SO2_ppm', [item['field'] for item in state['dropped_fields']])

    def test_characterization_values_require_explicit_pre_prediction_availability(self):
        _, plan = self.multi(); fold = plan['folds'][0]
        design = prep.fit_fold_preprocessor(plan, 'tree_one_hot', fold['fold_id'], 'design')
        characterized = prep.fit_fold_preprocessor(plan, 'tree_one_hot', fold['fold_id'], 'characterized')
        self.assertNotIn('BET_surface_area_m2_g', design['columns'])
        self.assertIn('BET_surface_area_m2_g', characterized['columns'])
        self.assertNotIn('metal_particle_size_nm', characterized['columns'])
        self.assertTrue(all(r['X_facts']['metal_particle_size_nm'] is not None for r in plan['rows']))

    def test_facts_and_model_views_never_mutate_original_observations(self):
        result, plan = self.multi(); before = copy.deepcopy(result); plan_before = copy.deepcopy(plan)
        prep.prepare_fold(plan, 'tree_one_hot', plan['folds'][0]['fold_id'])
        self.assertEqual(result, before); self.assertEqual(plan, plan_before)
        report = encoding.export_encoding(result, self.fixture.root / 'export')
        folder = Path(report['directory'])
        for name in ('机器学习准备包.json', '模型编码方案.json', '模型比较_论文分组.json', '建模事实表_尚未拟合填补缩放.csv'):
            self.assertTrue((folder / name).exists())
        saved = json.loads((folder / '机器学习准备包.json').read_text(encoding='utf8'))
        self.assertEqual(saved['plan_fingerprint'], plan['plan_fingerprint'])

    def test_too_few_papers_never_fit_preprocessing_on_all_rows(self):
        _, path, _ = self.fixture.packet()
        plan = prep.build_plan(self.fixture.build(path))
        self.assertFalse(plan['folds'])
        self.assertEqual(plan['status'], 'facts_only_not_enough_training_papers')
        with self.assertRaises(ValueError): prep.fit_fold_preprocessor(plan, 'tree_one_hot', 'fold-1')

    def test_test_partition_and_modified_preprocessor_are_refused(self):
        _, plan = self.multi(); state = prep.fit_fold_preprocessor(plan, 'tree_one_hot', plan['folds'][0]['fold_id'])
        with self.assertRaises(ValueError): prep.transform_partition(plan, state, 'test')
        state['numeric']['temperature_C']['median'] = 999
        with self.assertRaises(ValueError): prep.transform_partition(plan, state, 'validation')
        plan['rows'][0]['y'] = .99
        with self.assertRaises(ValueError): prep.fit_fold_preprocessor(plan, 'tree_one_hot', 'fold-1')

    def test_new_scientific_fields_are_typed_zero_is_not_unknown_and_tasks_stay_separate(self):
        conditions = {'temperature_C': 300, 'space_velocity_h_inv': 40000,
            'feed_description': 'Fixture explicitly dry without SO2',
            'feed_NO_ppm': 500, 'feed_NH3_ppm': 500, 'feed_O2_vol_pct': 5,
            'feed_H2O_vol_pct': 0, 'feed_SO2_ppm': 0, 'h2_co2_molar_ratio': 3}
        _, path, _ = self.fixture.packet(conditions=conditions, features={'active_metals': ['Pd', 'Ru'],
            'support': 'CeO2', 'pd_loading_wt_pct': 1, 'ru_loading_wt_pct': .5})
        row = self.fixture.build(path)['observations'][0]
        self.assertEqual(row['X_raw']['feed_H2O_vol_pct'], 0)
        self.assertIsNone(row['X_raw']['feed_NO2_ppm'])
        self.assertEqual(row['X_raw']['pd_loading_wt_pct'], 1)
        self.assertNotIn('h2_co2_molar_ratio', row['X_raw'])
        self.assertNotIn('feed_NO_ppm', encoding._feature_specs('cuzn_co2_conversion'))
        self.assertNotIn('temperature_C', encoding._feature_specs('nh3_adsorption_energy'))
        bad = copy.deepcopy(row['raw_record']); bad['conditions']['feed_O2_vol_pct'] = 110
        self.assertTrue(encoding.feature_issues(bad))

    def test_dft_plan_does_not_mix_bulk_measurements_or_later_validation_energy(self):
        _, path, _ = self.fixture.packet('dft', task='nh3_adsorption_energy', value=-1, unit='eV')
        plan = prep.build_plan(self.fixture.build(path, task='nh3_adsorption_energy'))
        self.assertEqual(plan['default_profile'], 'dft_model')
        self.assertIn('dft_functional', plan['profiles']['dft_model']['fields'])
        self.assertNotIn('BET_surface_area_m2_g', plan['profiles']['dft_model']['fields'])
        self.assertNotIn('NH3_adsorption_energy', plan['fields'])

    def test_previous_scoring_annotations_survive_only_empty_schema_additions(self):
        _, path, _ = self.fixture.packet()
        row = self.fixture.build(path)['observations'][0]
        old = copy.deepcopy(row)
        additions = {'pd_loading_wt_pct','ru_loading_wt_pct','reduction_time_h','metal_particle_size_nm',
            'metal_dispersion_pct','ce3_fraction_pct','zeolite_si_al_atomic_ratio','pore_volume_cm3_g',
            'feed_NO_ppm','feed_NO2_ppm','feed_NH3_ppm','feed_O2_vol_pct','feed_H2O_vol_pct','feed_SO2_ppm','reaction_time_h'}
        old['X_raw'] = {k: v for k, v in old['X_raw'].items() if k not in additions}
        note = scoring.make_annotation(old, {}, 'fixture-reviewer', 'Old reviewed supplement')
        _, errors = scoring.checked_annotation(row, note); self.assertFalse(errors)
        changed = copy.deepcopy(row); changed['X_raw']['pd_loading_wt_pct'] = 1
        _, errors = scoring.checked_annotation(changed, note); self.assertTrue(errors)


if __name__ == '__main__':
    unittest.main()
