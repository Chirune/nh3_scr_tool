"""Dual-score behavior checks on explicitly synthetic local PDF fixtures."""
import copy
import csv
import json
from pathlib import Path
import unittest

import paper_encoding as encoding
import paper_scoring as scoring
import paper_workspace as work
import test_paper_encoding as fixtures


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EncodingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def result(self, key='one', **kwargs):
        _, path, _ = self.fixture.packet(key, **kwargs)
        return self.fixture.build(path, task=kwargs.get('task', 'scr_conversion'))

    def annotation(self, row, extra=None):
        # These human assertions and their PDF are test fixtures, not scientific validation.
        values = {'metric_definition': 'Fixture: (NOin-NOout)/NOin',
            'feed_composition': 'Fixture: NO=500ppm; NH3=500ppm; O2=5%; balance=N2; H2O=0; SO2=0',
            'contact_time': 'Fixture: GHSV=40000 h-1, STP gas per catalyst-bed volume',
            'reactor_protocol': 'Fixture: fixed bed; stable value at 1 hour',
            'material_state': 'Fixture: fresh, same declared pretreatment'}
        values.update(extra or {})
        return scoring.make_annotation(row, {k: {'value': v, 'evidence_id': row['evidence'][0]['evidence_id']}
            for k, v in values.items()}, 'synthetic-reviewer', 'Synthetic behavior test; not a real paper assessment.')

    def test_quality_is_independent_of_high_or_low_performance(self):
        low = scoring.score_encoding(self.result('low', value=15))['cards'][0]
        high = scoring.score_encoding(self.result('high', value=95))['cards'][0]
        self.assertEqual(low['quality']['score'], high['quality']['score'])
        self.assertEqual(low['performance']['score'], 15)
        self.assertEqual(high['performance']['score'], 95)
        self.assertTrue(low['quality']['missing'])

    def test_percent_and_fraction_have_same_score_and_are_not_batch_rescaled(self):
        _, one, _ = self.fixture.packet('percent', value=80)
        _, two, _ = self.fixture.packet('fraction', value=.8, unit='fraction')
        _, three, _ = self.fixture.packet('extreme', value=100)
        alone = scoring.score_encoding(self.fixture.build(one))['cards'][0]['performance']['score']
        together = scoring.score_encoding(self.fixture.build(one, two, three))
        self.assertEqual(alone, 80)
        self.assertEqual(sorted(c['performance']['score'] for c in together['cards']), [80, 80, 100])

    def test_missing_comparison_fields_prevent_grouping_without_hiding_raw_metric(self):
        card = scoring.score_encoding(self.result())['cards'][0]
        self.assertEqual(card['performance']['score'], 90)
        self.assertIsNone(card['comparison']['group_id'])
        self.assertIn(scoring.CONTEXT_FIELDS['contact_time'][0], card['comparison']['missing'])
        self.assertIsNone(card['comparison']['rank'])

    def test_quality_improves_with_reviewed_context_and_not_with_weight_on_performance(self):
        result = self.result()
        row = result['observations'][0]
        before = scoring.score_encoding(result)['cards'][0]
        annotation = self.annotation(row, {'repeat_count': 3, 'uncertainty_value': .01, 'uncertainty_definition': 'SD of three independent fixture runs'})
        after = scoring.score_encoding(result, annotations={row['observation_id']: annotation})['cards'][0]
        self.assertGreater(after['quality']['score'], before['quality']['score'])
        self.assertEqual(after['quality']['score'], 100)
        self.assertEqual(after['performance'], before['performance'])

    def test_same_explicit_conditions_group_but_temperature_or_feed_changes_do_not(self):
        _, first, _ = self.fixture.packet('first')
        _, second, _ = self.fixture.packet('second', value=80)
        _, third, _ = self.fixture.packet('third', conditions={'temperature_C': 200, 'pressure_kPa': 101.325,
            'space_velocity_h_inv': 40000, 'feed_description': '500 ppm NO, 500 ppm NH3, 5% O2 in N2'})
        result = self.fixture.build(first, second, third)
        notes = {r['observation_id']: self.annotation(r) for r in result['observations']}
        cards = scoring.score_encoding(result, annotations=notes)['cards']
        groups = {c['doi'].rsplit('-', 1)[-1]: c['comparison'] for c in cards}
        self.assertEqual(groups['first']['group_id'], groups['second']['group_id'])
        self.assertNotEqual(groups['first']['group_id'], groups['third']['group_id'])
        self.assertEqual(groups['first']['group_papers'], 2)
        self.assertEqual(groups['third']['group_observations'], 1)
        row = next(r for r in result['observations'] if r['doi'].endswith('second'))
        notes[row['observation_id']] = self.annotation(row, {'feed_composition': 'Fixture different H2O=5%'})
        changed = scoring.score_encoding(result, annotations=notes)
        self.assertEqual(changed['summary']['groups_with_multiple_samples'], 0)

    def test_known_extra_conditions_are_never_silently_ignored(self):
        result = self.result()
        row = result['observations'][0]
        ctx, _ = scoring.checked_annotation(row, self.annotation(row))
        a = scoring._comparison(row, ctx)
        other = copy.deepcopy(row)
        other['raw_record']['conditions']['other'] = 'Fixture distinct time on stream'
        b = scoring._comparison(other, ctx)
        self.assertNotEqual(a['group_id'], b['group_id'])

    def test_stale_or_foreign_annotations_are_not_used_for_quality_or_comparison(self):
        result = self.result(); row = result['observations'][0]
        note = self.annotation(row)
        note['observation_fingerprint'] = 'old'
        report = scoring.score_encoding(result, annotations={row['observation_id']: note})
        self.assertTrue(report['annotation_issues'])
        self.assertIsNone(report['cards'][0]['comparison']['group_id'])
        self.assertEqual(report['cards'][0]['quality'], scoring.score_encoding(result)['cards'][0]['quality'])

    def test_each_annotation_requires_linked_evidence_reviewer_and_explicit_information(self):
        row = self.result()['observations'][0]
        for entries, reviewer in (({'contact_time': {'value': '40000 h-1', 'evidence_id': 'foreign'}}, 'reviewer'),
            ({'contact_time': {'value': 'unknown', 'evidence_id': row['evidence'][0]['evidence_id']}}, 'reviewer'),
            ({'contact_time': {'value': '40000 h-1', 'evidence_id': row['evidence'][0]['evidence_id']}}, '')):
            with self.subTest(entries=entries, reviewer=reviewer), self.assertRaises(ValueError):
                scoring.make_annotation(row, entries, reviewer, 'Checked against fixture')

    def test_incomplete_uncertainty_has_no_credit_and_repeats_cannot_be_points(self):
        result = self.result(); row = result['observations'][0]
        note = self.annotation(row, {'uncertainty_value': .01})
        q = scoring.score_encoding(result, annotations={row['observation_id']: note})['cards'][0]['quality']
        self.assertEqual(q['components']['uncertainty']['contribution'], 0)
        for value in (1, 1.5, True, 'NaN'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.annotation(row, {'repeat_count': value})

    def test_dft_sty_tof_and_co_selectivity_do_not_receive_arbitrary_default_utility(self):
        for task, unit, value in (('nh3_adsorption_energy', 'eV', -1),
            ('cuzn_methanol_sty', 'g/kgcat/h', 100), ('cuzn_tof', 'h^-1', 500), ('cuzn_co_selectivity', '%', 30)):
            with self.subTest(task=task):
                card = scoring.score_encoding(self.result(task, task=task, value=value, unit=unit))['cards'][0]
                self.assertIsNone(card['performance']['score'])
                self.assertIsNotNone(card['performance']['value'])

    def test_custom_target_range_is_explicit_and_retains_out_of_range_raw_value(self):
        config = scoring.default_config()
        p = config['profiles']['nh3_adsorption_energy']
        p.update(mode='target_range', anchor_low=-3, target_low=-1.5, target_high=-.5, anchor_high=0,
            goal='Synthetic utility demonstration', rationale='Illustrative bounds, not a scientific optimum')
        one = scoring.score_encoding(self.result('dft1', task='nh3_adsorption_energy', value=-1, unit='eV'), config)['cards'][0]
        two = scoring.score_encoding(self.result('dft2', task='nh3_adsorption_energy', value=-4, unit='eV'), config)['cards'][0]
        self.assertEqual(one['performance']['score'], 100)
        self.assertEqual(two['performance']['score'], 0)
        self.assertEqual(two['performance']['value'], -4)
        self.assertTrue(two['performance']['clipped'])

    def test_weight_changes_change_version_fingerprint_and_never_performance(self):
        result = self.result(); a = scoring.score_encoding(result)
        config = scoring.default_config(); config['quality_weights']['uncertainty'] = 40
        b = scoring.score_encoding(result, config)
        self.assertNotEqual(a['config_id'], b['config_id'])
        self.assertGreater(a['cards'][0]['quality']['score'], b['cards'][0]['quality']['score'])
        self.assertEqual(a['cards'][0]['performance'], b['cards'][0]['performance'])

    def test_bad_weights_units_anchors_and_unknown_rules_fail_closed(self):
        for field, value in (('uncertainty', -1), ('traceability', float('nan')), ('review', True)):
            config = scoring.default_config(); config['quality_weights'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): scoring.validate_config(config)
        config = scoring.default_config(); config['quality_weights'] = dict.fromkeys(scoring.DIMENSIONS, 0)
        with self.assertRaises(ValueError): scoring.validate_config(config)
        for change in ({'unit': '%'}, {'anchor_high': 0}, {'anchor_low': float('inf')}, {'mode': 'data_minmax'}):
            config = scoring.default_config(); config['profiles']['scr_conversion'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError): scoring.validate_config(config)

    def test_unreviewed_records_have_no_scores_and_no_weight_can_promote_them(self):
        project, _, record = self.fixture.packet()
        work.put_record(project, record, reviewer='fixture', confirm=False)
        result = self.fixture.build(self.fixture.export(project))
        report = scoring.score_encoding(result)
        self.assertFalse(report['cards'])
        self.assertIsNone(report['excluded'][0]['quality_score'])
        self.assertIsNone(report['excluded'][0]['performance_score'])

    def test_scoring_does_not_mutate_inputs_or_inject_scores_into_x_y(self):
        result = self.result(); before = copy.deepcopy(result)
        scoring.score_encoding(result)
        self.assertEqual(result, before)
        export = encoding.export_encoding(result, self.fixture.root / 'out')
        folder = Path(export['directory'])
        for name in ('数据质量分.csv', '单项性能分.csv', '双评分明细.json', '评分模板.json', '质量逐项依据.csv'):
            self.assertTrue((folder / name).is_file(), name)
        with (folder / 'y.csv').open(encoding='utf-8-sig', newline='') as stream:
            self.assertEqual(list(csv.DictReader(stream)), [{'y': '0.9'}])
        with (folder / 'X.csv').open(encoding='utf-8-sig', newline='') as stream:
            self.assertEqual(next(csv.reader(stream)), result['encoder']['columns'])
        reloaded = json.loads((folder / '评分方案与补充.json').read_text(encoding='utf8'))
        self.assertEqual(scoring.score_encoding(result, reloaded['config'], reloaded['annotations'])['cards'], scoring.score_encoding(result)['cards'])

    def test_changed_sources_or_changed_preview_cannot_export_stale_scores(self):
        result = self.result()
        modified = copy.deepcopy(result); modified['observations'][0]['y'] = 1
        with self.assertRaises(ValueError): scoring.score_encoding(modified)
        packet = encoding.load_handoff(result['input_packets'][0]['path'])
        project = work.load_project(packet['source_workspace']['path'])
        pdf = Path(project['article']['local_pdf']); pdf.write_bytes(pdf.read_bytes() + b'\nchanged fixture')
        with self.assertRaises(ValueError): encoding.export_encoding(result, self.fixture.root / 'out')
        self.assertFalse((self.fixture.root / 'out').exists())


if __name__ == '__main__':
    unittest.main()
