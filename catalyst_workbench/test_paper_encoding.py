"""Data-integrity tests using temporary synthetic fixtures, never research data.

The tests exercise real reviewed paper exports and real image source chains.
No network, model training, user document changes or accuracy claims occur.
"""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

import paper_encoding as encoding
import paper_workspace as workspace
import pipeline
import test_paper_image_bridge as image_fixtures
from test_paper_workspace import make_pdf
from digitizer.session import export_session, save_session, set_calibration


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


class EncodingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='encoding-integrity-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def packet(self, key='a', *, task='scr_conversion', value=90, unit='%', doi=None,
               features=None, pdf=None, project_root=None, conditions=None, branch='text'):
        pdf = pdf or make_pdf(self.root / (key + '.pdf'), [[
            'Synthetic encoding fixture ' + key,
            f'Value reported in this test fixture is {value} {unit}.',
            'A second independent condition was measured for the fixture.',
        ]])
        article = {'local_pdf': str(pdf), 'source_sha256': pipeline.digest(pdf),
                   'doi': doi if doi is not None else '10.1234/encoding-' + key,
                   'title': 'Synthetic fixture only', 'profile': 'scr_ammonia'}
        project = workspace.open_paper(article, project_root or self.root / 'papers')
        workspace.set_task(project, task)
        workspace.extract_document(project)
        text = project['pages'][0]['text']
        start, end = text.index('Value reported'), text.index('\n', text.index('Value reported'))
        evidence = workspace.add_text_evidence(project, 1, start, end)
        if branch == 'semantic':
            evidence.update(branch='semantic', kind='absolute', operator='eq',
                            metric=workspace.TASKS[task]['metric'], value=value, unit=unit,
                            assertion_scope='current_study')
        workspace.review_evidence(project, evidence['evidence_id'], 'reviewed', 'fixture-reviewer')
        record = workspace.record_from_evidence(project, [evidence['evidence_id']])
        dft = task == 'nh3_adsorption_energy'
        record.update(task_id=task, sample_label='S1', experiment_id='experiment-' + key,
                      composition='Cu/CeO2; raw material paragraph retained for audit',
                      preparation='Prepared by impregnation; this paragraph is never a category',
                      characterization='BET measurement and performance explanation stay as text',
                      metric=workspace.TASKS[task]['metric'], value=value, value_high=None, unit=unit,
                      operator='eq', assertion_scope='current_study', measurement_type='dft' if dft else 'experiment',
                      features=copy.deepcopy(features if features is not None else {
                          'active_metals': ['Cu'], 'support': 'CeO2', 'preparation_method': 'impregnation',
                          'metal_loading_wt_pct': 5.0}),
                      conditions=copy.deepcopy(conditions if conditions is not None else
                          {'surface_site': 'Cu(111), atop site', 'calculation_method': 'PBE, adsorption defined Etotal-Esurface-ENH3'} if dft else
                          {'temperature_C': 300, 'pressure_kPa': 101.325,
                           'space_velocity_h_inv': 40000, 'feed_description': '500 ppm NO, 500 ppm NH3, 5% O2 in N2'}))
        saved = workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        return project, self.export(project), saved

    def export(self, project):
        summary = workspace.export_handoff(project)
        file = Path(summary['directory']) / '待编码包.json'
        packet = read_json(file)
        # The new pointer is added by the integration change. Retaining this
        # fixture assignment also lets these tests run against legacy exports.
        packet['source_workspace'] = {'path': str(workspace.workspace_path(project)), 'project_id': project['project_id']}
        write_json(file, packet)
        return file

    def build(self, *files, task='scr_conversion'):
        return encoding.build_encoding(list(files), task)

    def rejected(self, result, phrase=''):
        self.assertEqual(result['summary']['observation_count'], 0, result)
        self.assertGreater(result['summary']['excluded_count'], 0)
        if phrase:
            self.assertIn(phrase, ' '.join(reason for row in result['excluded'] for reason in row['reasons']))

    def test_percent_fraction_standardization_preserves_original_and_provenance(self):
        _, percent, _ = self.packet('percent', value=80, unit='%')
        _, fraction, _ = self.packet('fraction', value=0.8, unit='fraction')
        result = self.build(percent, fraction)
        self.assertEqual(result['summary']['observation_count'], 2)
        self.assertEqual([r['y'] for r in result['observations']], [0.8, 0.8])
        self.assertEqual({r['raw_unit'] for r in result['observations']}, {'%', 'fraction'})
        for row in result['observations']:
            self.assertEqual(row['target_unit'], 'fraction')
            self.assertEqual(row['row_id'], row['observation_id'])
            self.assertEqual(row['evidence'][0]['source_ref']['source_sha256'], row['source_sha256'])
            self.assertEqual(row['feature_values'], row['X_raw'])
            self.assertFalse(row['approximate'])

    def test_dft_energy_units_use_exact_si_constants_without_changing_sign(self):
        _, file, _ = self.packet('energy', task='nh3_adsorption_energy', value=-encoding.EV_TO_KJ_MOL,
                                 unit='kJ/mol', features={'active_metals': ['Cu'], 'surface_facet': '(1 1 1)',
                                 'adsorption_site': 'top', 'dft_functional': 'PBE', 'coverage_ml': 0.25})
        result = self.build(file, task='nh3_adsorption_energy')
        self.assertEqual(result['summary']['observation_count'], 1)
        row = result['observations'][0]
        self.assertAlmostEqual(row['y'], -1.0, places=14)
        self.assertEqual(row['y_unit'], 'eV')
        self.assertEqual(row['X_raw']['surface_facet'], '111')
        self.assertIn('adsorption_site=top', result['encoder']['columns'])
        self.assertNotIn('temperature_C', row['X_raw'])
        self.assertIn('nist.gov', result['unit_conversions']['kJ_mol_to_eV']['source'])
        self.assertAlmostEqual(encoding.EV_TO_KJ_MOL, 96.48533212331002, places=12)

    def test_single_paper_has_preview_only_without_fake_holdout_or_score(self):
        _, file, _ = self.packet()
        result = self.build(file)
        self.assertEqual(result['split']['status'], 'preview_only')
        self.assertEqual(result['split']['train_groups'], [])
        self.assertEqual(result['split']['test_groups'], [])
        self.assertEqual(result['encoder']['fit_scope'], 'preview_only')
        self.assertFalse(result['scientific_evaluation']['performed'])
        self.assertIsNone(result['scientific_evaluation']['scores'])
        self.assertEqual(result['summary']['preview_count'], 1)

    def test_missing_numeric_stays_null_and_missing_category_has_own_indicator(self):
        _, file, _ = self.packet(features={'active_metals': [], 'metal_loading_wt_pct': None})
        result = self.build(file)
        values = result['encoded_rows'][0]['X']
        self.assertIsNone(values['metal_loading_wt_pct'])
        self.assertEqual(values['metal_loading_wt_pct__missing'], 1)
        self.assertEqual(values['active_metals__missing'], 1)
        self.assertEqual(values['active_metals__unknown'], 0)
        self.assertEqual(result['encoder']['imputation'], 'none')

    def test_categories_are_onehot_multihot_and_raw_text_identity_never_enters_x(self):
        _, file, _ = self.packet(features={'active_metals': ['Zn', 'Cu', 'Cu'], 'support': 'fly_ash',
                                           'cu_zn_atomic_ratio': 2.5, 'preparation_method': 'coprecipitation'})
        result = self.build(file)
        values = result['encoded_rows'][0]['X']
        self.assertEqual(values['active_metals=Cu'], 1)
        self.assertEqual(values['active_metals=Zn'], 1)
        self.assertEqual(values['support=fly_ash'], 1)
        self.assertNotIn('active_metals', values)
        self.assertNotIn('support', values)
        for name in ('doi', 'composition', 'preparation', 'characterization', 'sample_label', 'experiment_id', 'feed_description'):
            self.assertFalse(any(key == name or key.startswith(name + '=') for key in values), name)
        self.assertIn('paragraph', result['observations'][0]['raw_record']['composition'])

    def test_only_train_categories_are_fitted_and_unknown_test_tokens_are_explicit(self):
        fixtures = [self.packet(str(i)) for i in range(6)]
        files = [v[1] for v in fixtures]
        initial = self.build(*files)
        test_groups = set(initial['split']['test_groups'])
        self.assertTrue(test_groups)
        for i, (project, file, record) in enumerate(fixtures):
            if 'doi:' + encoding.normalize_doi(project['article']['doi']) in test_groups:
                record['features']['support'] = 'TiO2'
                record['features']['active_metals'] = ['Cu', 'Fe']
                workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
                files[i] = self.export(project)
        result = self.build(*files)
        self.assertEqual(result['encoder']['fit_scope'], 'train_only')
        self.assertEqual(result['encoder']['vocabulary']['support'], ['CeO2'])
        self.assertEqual(result['encoder']['vocabulary']['active_metals'], ['Cu'])
        training_ids = {r['observation_id'] for r in result['encoded_rows'] if r['split'] == 'train'}
        self.assertEqual(set(result['encoder']['fit_observation_ids']), training_ids)
        for row in result['encoded_rows']:
            if row['split'] == 'test':
                self.assertEqual(row['X']['support__unknown'], 1)
                self.assertEqual(row['X']['support__missing'], 0)
                self.assertEqual(row['X']['active_metals=Cu'], 1)
                self.assertEqual(row['X']['active_metals__unknown'], 1)
                self.assertEqual(row['unknown_categories']['support'], ['TiO2'])
                self.assertEqual(row['unknown_categories']['active_metals'], ['Fe'])
        self.assertFalse(set(result['split']['train_groups']) & set(result['split']['test_groups']))

    def test_doi_case_and_url_variants_stay_in_one_group(self):
        _, first, _ = self.packet('first', doi='https://doi.org/10.1234/SHARED')
        _, second, _ = self.packet('second', doi='doi:10.1234/shared')
        result = self.build(first, second)
        self.assertEqual(result['summary']['observation_count'], 2)
        self.assertEqual(result['summary']['paper_count'], 1)
        self.assertEqual({r['split_group'] for r in result['observations']}, {'doi:10.1234/shared'})

    def test_missing_doi_uses_source_hash_not_sample_or_random_group(self):
        project, file, _ = self.packet(doi='')
        result = self.build(file)
        self.assertEqual(result['observations'][0]['split_group'], 'sha256:' + project['article']['source_sha256'])

    def test_repeated_packet_and_reexport_do_not_multiply_observations(self):
        project, file, _ = self.packet()
        later = self.export(project)
        result = self.build(file, file, later)
        self.assertEqual(result['summary']['observation_count'], 1)
        self.assertEqual(len(result['observations'][0]['packet_paths']), 2)
        self.assertIn('repeated_record_ignored', {entry['code'] for entry in result['audit']})

    def test_waiting_record_is_never_promoted_when_readiness_flags_are_changed(self):
        project, file, record = self.packet()
        record['review_status'] = 'draft'
        workspace.put_record(project, record, confirm=False)
        waiting = self.export(project)
        self.rejected(self.build(waiting), '待核对')
        forged = read_json(waiting)
        row = forged['records_waiting_for_review'].pop()
        row.update(stage3_status='ready_for_standardization', issues=[])
        forged['records_ready_for_standardization'].append(row)
        write_json(waiting, forged)
        self.rejected(self.build(waiting), '人工确认')

    def test_packet_with_evidence_but_no_unified_records_explains_next_step(self):
        project, _, _ = self.packet()
        project['records'] = []
        workspace.save_project(project)
        result = self.build(self.export(project))
        self.assertEqual(result['summary']['observation_count'], 0)
        self.assertEqual(result['summary']['excluded_count'], 1)
        pending = result['excluded'][0]
        self.assertEqual(pending['code'], 'no_unified_records')
        self.assertEqual(pending['entity_type'], 'packet')
        self.assertEqual(pending['record_id'], '')
        self.assertIn(f"{len(project['evidence'])} 条证据候选", pending['reasons'][0])
        self.assertIn('返回板块2', pending['reasons'][0])
        self.assertIn('核验样品', pending['reasons'][0])

    def test_bad_schema_and_duplicate_evidence_ids_fail_closed(self):
        _, file, _ = self.packet()
        packet = read_json(file)
        packet['schema_version'] = 'unrelated/1.0'
        bad = self.root / 'wrong.json'
        write_json(bad, packet)
        with self.assertRaisesRegex(ValueError, 'stage3'):
            encoding.load_handoff(bad)
        self.rejected(self.build(bad), 'stage3')
        packet = read_json(file)
        packet['evidence'].append(copy.deepcopy(packet['evidence'][0]))
        write_json(file, packet)
        self.rejected(self.build(file), '重复编号')

    def test_changed_pdf_rejects_old_packet(self):
        project, file, _ = self.packet()
        pdf = Path(project['article']['local_pdf'])
        pdf.write_bytes(pdf.read_bytes() + b'\n% source changed\n')
        self.rejected(self.build(file), 'PDF 已改变')

    def test_record_edit_and_evidence_review_downgrade_reject_old_packet(self):
        project, file, record = self.packet('edit')
        record['notes'] = 'reviewed record was later changed'
        workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        self.rejected(self.build(file), '导出后已改变')
        latest = self.export(project)
        workspace.review_evidence(project, record['evidence_ids'][0], 'unreviewed', 'fixture-reviewer')
        self.rejected(self.build(latest), '导出后已修改')

    def test_modified_evidence_excerpt_cannot_keep_ready_status(self):
        project, _, record = self.packet()
        evidence = next(e for e in project['evidence'] if e['evidence_id'] == record['evidence_ids'][0])
        evidence['quote'] = 'This forged sentence does not exist at its page offsets.'
        workspace.save_project(project)
        file = self.export(project)
        self.rejected(self.build(file), '原文证据')

    def test_changed_cached_page_and_quote_cannot_override_actual_pdf_text(self):
        project, _, record = self.packet()
        evidence = next(e for e in project['evidence'] if e['evidence_id'] == record['evidence_ids'][0])
        text = project['pages'][0]['text']
        changed = 'X' * len(evidence['quote'])
        project['pages'][0]['text'] = text[:evidence['start']] + changed + text[evidence['end']:]
        evidence['quote'] = changed
        workspace.save_project(project)
        self.rejected(self.build(self.export(project)), '实际源 PDF')

    def test_no_conversion_and_nox_and_dft_are_not_pooled(self):
        _, no, _ = self.packet('no')
        _, nox, _ = self.packet('nox', task='scr_nox_conversion')
        _, dft, _ = self.packet('dft', task='nh3_adsorption_energy', value=-1, unit='eV')
        result = self.build(no, nox, dft)
        self.assertEqual(result['summary']['observation_count'], 1)
        self.assertEqual(len([r for r in result['excluded'] if r['code'] == 'different_task']), 2)
        self.assertEqual(result['observations'][0]['metric'], 'NO conversion')

    def test_semantic_value_and_metric_cannot_be_rewritten_in_record_form(self):
        project, _, record = self.packet(branch='semantic')
        record['value'] = 80
        workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        self.rejected(self.build(self.export(project)), '数值与标签不一致')
        record['value'] = 90
        record['task_id'] = 'scr_nox_conversion'
        record['metric'] = 'NOx conversion'
        workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        self.rejected(self.build(self.export(project), task='scr_nox_conversion'), '指标与标签不同')

    def semantic_temperature_packet(self, key, temperature, recorded=300):
        project, _, record = self.packet(key, branch='semantic')
        source = next(e for e in project['evidence'] if e['evidence_id'] == record['evidence_ids'][0])
        source['conditions'] = {'temperature': copy.deepcopy(temperature)}
        workspace.save_project(project)
        record['conditions']['temperature_C'] = recorded
        record['source_conditions'] = copy.deepcopy(source['conditions'])
        workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        return project, self.export(project), record

    def test_exact_semantic_temperature_must_equal_the_recorded_condition(self):
        project, file, record = self.semantic_temperature_packet('exact-temperature',
            {'value': 300, 'value_high': None, 'unit': '°C', 'operator': 'eq'})
        result = self.build(file)
        self.assertEqual(result['summary']['observation_count'], 1, result['excluded'])
        self.assertEqual(result['encoded_rows'][0]['X']['temperature_C'], 300)
        record['conditions']['temperature_C'] = 350
        workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        self.rejected(self.build(self.export(project)), '明确单点温度与记录温度不一致')

    def test_nonpoint_semantic_temperatures_cannot_be_edited_into_exact_x(self):
        for index, (operator, upper) in enumerate([
                ('range', 350), ('lt', None), ('gt', None), ('unknown', None),
                ('eq', 350), (None, None)]):
            with self.subTest(operator=operator, value_high=upper):
                _, file, _ = self.semantic_temperature_packet('bounded-temperature-' + str(index),
                    {'value': 300, 'value_high': upper, 'unit': '°C', 'operator': operator}, recorded=300)
                self.rejected(self.build(file), '不能改写为精确温度进入 X')

    def test_legacy_semantic_temperature_without_operator_remains_compatible(self):
        _, file, _ = self.semantic_temperature_packet('legacy-temperature',
            {'value': 300, 'unit': '℃'}, recorded=300)
        result = self.build(file)
        self.assertEqual(result['summary']['observation_count'], 1, result['excluded'])
        self.assertEqual(result['observations'][0]['raw_record']['source_conditions']['temperature'],
                         {'value': 300, 'unit': '℃'})

    def test_equal_and_conflicting_experiments_in_separate_workspaces_are_blocked(self):
        for value in (90, 80):
            with self.subTest(value=value):
                first_project, first, first_record = self.packet('same' + str(value))
                second_project, _, second_record = self.packet('copy' + str(value), value=value,
                    doi=first_project['article']['doi'], pdf=Path(first_project['article']['local_pdf']),
                    project_root=self.root / ('copy-workspace-' + str(value)))
                second_record['experiment_id'] = first_record['experiment_id']
                workspace.put_record(second_project, second_record, reviewer='fixture-reviewer', confirm=True)
                result = self.build(first, self.export(second_project))
                self.rejected(result, '同一论文、样品、实验和条件')
                self.assertEqual(len(result['excluded']), 2)
                self.assertIn('duplicate_experiment' if value == 90 else 'conflicting_experiment',
                              {r['code'] for r in result['excluded']})

    def test_shared_pdf_claimed_as_two_dois_is_blocked(self):
        project, first, _ = self.packet('first')
        _, second, _ = self.packet('second', doi='10.1234/a-different-paper',
                                   pdf=Path(project['article']['local_pdf']))
        self.rejected(self.build(first, second), '不同 DOI')

    def test_one_semantic_value_cannot_be_copied_by_changing_experiment_id(self):
        project, _, record = self.packet(branch='semantic')
        copy_record = copy.deepcopy(record)
        copy_record.update(record_id='another-record', experiment_id='another-experiment')
        workspace.put_record(project, copy_record, reviewer='fixture-reviewer', confirm=True)
        self.rejected(self.build(self.export(project)), '不能靠改样品名或实验号复制样本')

    def test_time_axis_conditions_remain_distinct_in_duplicate_identity(self):
        project, _, record = self.packet()
        record['conditions'].update(x_name='Time on stream', x_value=1, x_unit='h')
        workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        text = project['pages'][0]['text']
        start = text.index('A second independent')
        second = workspace.add_text_evidence(project, 1, start, start + len('A second independent condition was measured for the fixture.'))
        workspace.review_evidence(project, second['evidence_id'], 'reviewed', 'fixture-reviewer')
        other = copy.deepcopy(record)
        other.update(record_id='time-point-two', evidence_ids=[second['evidence_id']],
                     evidence_roles={second['evidence_id']: 'value'})
        other['conditions']['x_value'] = 2
        workspace.put_record(project, other, reviewer='fixture-reviewer', confirm=True)
        result = self.build(self.export(project))
        self.assertEqual(result['summary']['observation_count'], 2, result['excluded'])
        self.assertEqual({r['raw_record']['conditions']['x_value'] for r in result['observations']}, {1, 2})

    def test_feature_schema_rejects_free_prose_boolean_nonfinite_and_identifiers(self):
        bad_features = [{'active_metals': 'Cu and Zn were used to obtain 99% conversion'},
                        {'support': 'High activity support gave 95% conversion'},
                        {'support_detail': 'S1'}, {'support_detail': 'high_conversion'},
                        {'support_detail': '10.1234/paper'}, {'metal_loading_wt_pct': True},
                        {'metal_loading_wt_pct': []},
                        {'metal_loading_wt_pct': float('nan')}, {'metal_loading_wt_pct': 101},
                        {'surface_facet': 'stable (111) with 99% conversion'},
                        {'target_derived_conversion': 95}]
        for features in bad_features:
            with self.subTest(features=features):
                self.assertTrue(encoding.feature_issues({'features': features, 'sample_label': 'S1'}))
        self.assertEqual(encoding.feature_issues({'features': {'support_detail': 'SSZ-13'}, 'sample_label': 'SSZ-13'}), [])
        self.assertEqual(encoding.feature_issues({'features': {'support_detail': 'CeO2'}, 'composition': 'CeO2', 'sample_label': 'CeO2'}), [])

    def test_malformed_nested_schema_and_nonfinite_json_are_rejected(self):
        _, file, _ = self.packet()
        original = read_json(file)
        malformed = copy.deepcopy(original)
        malformed['evidence'][0]['source_ref'] = []
        write_json(file, malformed)
        self.rejected(self.build(file), 'source_ref')
        file.write_text(json.dumps(original).replace('90.0', '1e9999'), encoding='utf8')
        self.rejected(self.build(file), '有限数字')

    def test_read_only_build_preserves_source_file_contents_and_timestamps(self):
        _, file, _ = self.packet()
        def state():
            return {str(p): (pipeline.digest(p), p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
        before = state()
        self.assertEqual(self.build(file)['summary']['observation_count'], 1)
        self.assertEqual(before, state())

    def test_exports_have_aligned_numeric_x_y_no_identifier_columns_and_full_audit(self):
        _, file, _ = self.packet()
        result = self.build(file)
        summary = encoding.export_encoding(result, self.root / 'outputs')
        output = Path(summary['directory'])
        with (output / 'X.csv').open(encoding='utf-8-sig', newline='') as stream:
            xrows = list(csv.DictReader(stream))
        with (output / 'y.csv').open(encoding='utf-8-sig', newline='') as stream:
            yrows = list(csv.DictReader(stream))
        self.assertEqual(len(xrows), len(yrows))
        self.assertEqual(float(yrows[0]['y']), 0.9)
        self.assertEqual(xrows[0]['BET_surface_area_m2_g'], '')
        self.assertNotIn('doi', xrows[0])
        self.assertNotIn('sample_label', xrows[0])
        self.assertTrue((output / 'X_preview.csv').is_file())
        self.assertFalse((output / 'X_test.csv').exists())
        saved = read_json(output / '编码结果.json')
        self.assertEqual(saved['snapshot_fingerprint'], result['snapshot_fingerprint'])
        self.assertIn('evidence', saved['observations'][0])
        self.assertTrue((output / '排除原因.csv').is_file())

    def test_export_rechecks_changes_after_preview_and_rejects_mutated_x(self):
        project, file, record = self.packet()
        result = self.build(file)
        modified = copy.deepcopy(result)
        modified['encoded_rows'][0]['y'] = 1
        with self.assertRaisesRegex(ValueError, '预览后已改变'):
            encoding.export_encoding(modified, self.root / 'outputs')
        record['features']['metal_loading_wt_pct'] = 8
        workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        with self.assertRaisesRegex(ValueError, '预览后已改变'):
            encoding.export_encoding(result, self.root / 'outputs')
        self.assertFalse((self.root / 'outputs').exists())

    def test_valid_subset_can_export_with_invalid_packet_exclusion_retained(self):
        _, file, _ = self.packet()
        bad = self.root / 'unrelated.json'
        write_json(bad, {'schema_version': 'unrelated/1.0'})
        result = self.build(file, bad)
        self.assertEqual(result['summary']['observation_count'], 1)
        self.assertEqual(result['summary']['excluded_count'], 1)
        output = encoding.export_encoding(result, self.root / 'outputs')
        saved = read_json(output['path'])
        self.assertEqual(saved['excluded'][0]['code'], 'packet_source_invalid')

    def test_no_source_workspace_means_no_unverifiable_approval(self):
        _, file, _ = self.packet()
        packet = read_json(file)
        packet.pop('source_workspace')
        detached = self.root / 'detached.json'
        write_json(detached, packet)
        self.rejected(self.build(detached))

    def test_bad_detached_copy_does_not_shadow_identical_verifiable_legacy_packet(self):
        _, file, _ = self.packet()
        packet = read_json(file)
        packet.pop('source_workspace')
        write_json(file, packet)
        detached = self.root / 'detached-copy.json'
        detached.write_bytes(file.read_bytes())
        result = self.build(detached, file)
        self.assertEqual(result['summary']['observation_count'], 1, result['excluded'])
        self.assertEqual(result['summary']['excluded_count'], 1)
        self.assertEqual(result['observations'][0]['packet_paths'], [str(file)])

    def image_packet(self):
        fixture = image_fixtures.PaperImageBridgeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        calibration = copy.deepcopy(fixture.session['calibration'])
        calibration['x'].update(name='Temperature', unit='°C', v1=300, v2=400)
        calibration['y'].update(name='NO conversion (%)', unit='%', v1=0, v2=100)
        set_calibration(fixture.session, calibration)
        export_session(fixture.session, {'reviewed': True})
        article = {**fixture.paper, 'batch_path': str(fixture.batch_path), 'profile': 'scr_ammonia'}
        project = workspace.open_paper(article, self.root / 'image-papers')
        workspace.refresh_images(project)
        evidence = project['evidence'][0]
        record = workspace.record_from_evidence(project, [evidence['evidence_id']])
        record.update(task_id='scr_conversion', sample_label='sample A', experiment_id='figure-test-1',
                      composition='Cu/CeO2', metric='NO conversion', assertion_scope='current_study',
                      measurement_type='experiment', features={'active_metals': ['Cu'], 'support': 'CeO2'})
        record['conditions'].update(feed_description='500 ppm NO, 500 ppm NH3, 5% O2', space_velocity_h_inv=40000)
        record = workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        return fixture, project, self.export(project), record

    def test_real_image_export_encodes_approximate_value_and_keeps_pixel_lineage(self):
        _, _, file, _ = self.image_packet()
        result = self.build(file)
        self.assertEqual(result['summary']['observation_count'], 1, result['excluded'])
        row = result['observations'][0]
        self.assertTrue(row['approximate'])
        self.assertEqual(row['y'], 0.5)
        self.assertEqual(row['X_raw']['temperature_C'], 350)
        self.assertIn('point_pixels', row['evidence'][0]['source_ref'])
        self.assertIn('export_snapshot_sha256', row['evidence'][0]['source_ref'])

    def test_real_image_unexported_session_change_blocks_old_packet(self):
        fixture, _, file, _ = self.image_packet()
        fixture.session['points'][0]['sample_label'] = 'changed after export'
        save_session(fixture.session)
        self.rejected(self.build(file), '图像来源')

    def test_real_image_source_crop_change_blocks_old_packet(self):
        fixture, _, file, _ = self.image_packet()
        crop = Path(fixture.figure['crop_path'])
        crop.write_bytes(crop.read_bytes() + b'changed')
        self.rejected(self.build(file), '图像来源')

    def test_real_image_value_cannot_be_changed_only_in_record_form(self):
        _, project, _, record = self.image_packet()
        record['value'] = 95
        workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        self.rejected(self.build(self.export(project)), '数值与标签不一致')

    def test_image_sample_name_change_requires_reviewed_mapping_note(self):
        _, project, _, record = self.image_packet()
        record['sample_label'] = 'Cu-CeO2-standardized'
        with self.assertRaisesRegex(ValueError, '样品'):
            workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        # An old or externally edited workspace can predate the UI guard.
        project['records'] = [copy.deepcopy(record)]
        workspace.save_project(project)
        self.rejected(self.build(self.export(project)), '样品')
        record['sample_mapping_note'] = '图例 sample A 对应原文 Cu-CeO2-standardized，见第1页图注与组成表。'
        workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        result = self.build(self.export(project))
        self.assertEqual(result['summary']['observation_count'], 1, result['excluded'])
        self.assertIn('第1页', result['observations'][0]['raw_record']['sample_mapping_note'])

    def test_semantic_sample_name_change_requires_mapping_note(self):
        project, _, record = self.packet(branch='semantic')
        entry = next(e for e in project['evidence'] if e['evidence_id'] == record['evidence_ids'][0])
        entry['sample_label'] = 'source-sample'
        workspace.save_project(project)
        self.rejected(self.build(self.export(project)), '样品映射')
        record['sample_mapping_note'] = 'Source sample explicitly corresponds to S1; see page 1.'
        workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        result = self.build(self.export(project))
        self.assertEqual(result['summary']['observation_count'], 1, result['excluded'])

    def test_missing_image_sample_uses_series_label_for_mapping_check(self):
        fixture, project, _, record = self.image_packet()
        fixture.session['points'][0]['sample_label'] = ''
        fixture.session['points'][0]['series_label'] = 'Series B'
        export_session(fixture.session, {'reviewed': True})
        workspace.refresh_images(project)
        entry = next(e for e in project['evidence'] if e.get('usable'))
        record['evidence_ids'] = [entry['evidence_id']]
        record['evidence_roles'] = {entry['evidence_id']: 'value'}
        with self.assertRaisesRegex(ValueError, '样品'):
            workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        project['records'] = [copy.deepcopy(record)]
        workspace.save_project(project)
        self.rejected(self.build(self.export(project)), '样品')
        record['sample_mapping_note'] = '图例 Series B 对应 sample A，见第1页图注。'
        workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        result = self.build(self.export(project))
        self.assertEqual(result['summary']['observation_count'], 1, result['excluded'])

    def test_two_value_evidence_branches_are_one_observation(self):
        project, _, record = self.packet(branch='semantic')
        source = next(e for e in project['evidence'] if e['evidence_id'] == record['evidence_ids'][0])
        text = copy.deepcopy(source)
        text.update(evidence_id='manual-text-of-same-result', branch='text')
        project['evidence'].append(text)
        workspace.save_project(project)
        record['evidence_ids'].append(text['evidence_id'])
        record['evidence_roles'][text['evidence_id']] = 'value'
        workspace.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        result = self.build(self.export(project))
        self.assertEqual(result['summary']['observation_count'], 1, result['excluded'])
        self.assertEqual(len(result['observations'][0]['evidence']), 2)

    def test_image_metric_alias_does_not_conflate_no_and_nox(self):
        self.assertTrue(encoding._image_metric_matches({'metric': 'NO conversion (%)'}, {'metric': 'NO conversion'}))
        self.assertTrue(encoding._image_metric_matches({'metric': 'NOₓ conversion'}, {'metric': 'NOx conversion'}))
        self.assertFalse(encoding._image_metric_matches({'metric': 'NOx conversion'}, {'metric': 'NO conversion'}))
        self.assertFalse(encoding._image_metric_matches({'metric': 'conversion'}, {'metric': 'NO conversion'}))


if __name__ == '__main__':
    unittest.main()
