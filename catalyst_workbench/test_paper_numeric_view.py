"""Meaningful numeric inventory contracts; synthetic fixtures are not research data."""
from __future__ import annotations
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

import paper_workspace as work
import paper_numeric_view as view
from test_paper_workspace import make_pdf


def fact(eid='condition', **changes):
    data = {'evidence_id': eid, 'branch': 'text', 'kind': 'table_value', 'metric': 'reaction temperature',
            'metric_label': '反应温度', 'value': 230., 'value_high': None, 'unit': '°C', 'operator': 'eq',
            'quantity_role': 'condition', 'field_key': 'temperature_C', 'sample_label': '', 'conditions': {},
            'assertion_scope': 'unknown', 'review_status': 'unreviewed', 'page': 1,
            'quote': 'Temperature 230 C', 'start': 0, 'end': 17}
    data.update(changes)
    return data


def project(evidence):
    return {'task_id': 'scr_conversion', 'article': {'title': 'Fixture only'}, 'pages': [{'page': 1}],
            'evidence': evidence, 'numeric_extraction_version': 'scientific-numeric/1.0'}


class NumericViewTests(unittest.TestCase):
    def test_units_conditions_and_uniformity_do_not_become_conversion(self):
        data = [fact(), fact('ui', metric='ammonia uniformity index', metric_label='均匀性指数',
                            value=.934, unit='dimensionless', quantity_role='other_result')]
        result = view.numeric_overview(project(data))
        self.assertEqual(result['counts']['target'], 0)
        self.assertEqual(result['counts']['condition'], 1)
        self.assertEqual(result['counts']['other_result'], 1)
        self.assertIn('候选为 0', result['message'])

    def test_target_candidates_expose_missing_sample_and_conditions(self):
        entry = fact('y', branch='semantic', kind='absolute', quantity_role=None,
                     metric='NO conversion', value=90, unit='%')
        result = view.numeric_overview(project([entry]))
        self.assertEqual(result['counts']['target'], 1)
        self.assertIn('样品未绑定', result['rows'][0]['missing'])
        self.assertTrue(any('进料' in reason for reason in result['rows'][0]['missing']))

    def test_reference_scope_and_relative_claims_are_not_target_rows(self):
        background = fact('prior', branch='semantic', kind='absolute', metric='NO conversion',
                          value=95, unit='%', assertion_scope='prior_work')
        relative = fact('relative', branch='semantic', kind='comparison', metric='NO conversion', value=10)
        result = view.numeric_overview(project([background, relative]))
        self.assertEqual(result['counts']['target'], 0)
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['rows'][0]['display_role'], 'background')

    def test_same_quote_pdf_layer_duplicates_collapsed_with_all_offsets(self):
        a = fact(); b = fact('duplicate', quote='Temperature  230\nC', page=2)
        result = view.numeric_overview(project([a, b]))
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(len(result['rows'][0]['occurrences']), 2)

    def test_same_value_other_sample_is_not_deduplicated(self):
        result = view.numeric_overview(project([fact(sample_label='A'), fact('B', sample_label='B')]))
        self.assertEqual(len(result['rows']), 2)

    def test_old_archives_show_reread_instruction_without_mutation(self):
        p = project([]); p.pop('numeric_extraction_version'); before = copy.deepcopy(p)
        self.assertIn('读取全文并提取数值', view.numeric_overview(p)['message'])
        self.assertEqual(p, before)

    def test_context_prefill_is_explicit_reversible_draft(self):
        record = {'conditions': {}, 'features': {}, 'sample_label': 'A', 'evidence_ids': ['y'],
                  'evidence_roles': {'y': 'value'}, 'review_status': 'reviewed', 'reviewer': 'r'}
        updated = view.with_numeric_context(project([]), record, fact())
        self.assertEqual(updated['conditions']['temperature_C'], 230)
        self.assertEqual(updated['evidence_roles']['condition'], 'context')
        self.assertEqual(updated['review_status'], 'draft')
        self.assertEqual(record['conditions'], {})

    def test_context_prefill_rejects_wrong_sample_conflict_unit_and_boundary(self):
        record = {'conditions': {}, 'features': {}, 'sample_label': 'A', 'evidence_ids': ['y']}
        cases = [fact(sample_label='B'), fact(unit='K'), fact(operator='le'),
                 fact(quantity_role='background'), fact(assertion_scope='prior_work'), fact(field_key='unknown')]
        for entry in cases:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                view.with_numeric_context(project([]), record, entry)
        record['conditions']['temperature_C'] = 400
        with self.assertRaises(ValueError):
            view.with_numeric_context(project([]), record, fact())

    def test_condition_cannot_be_used_as_a_performance_record(self):
        with self.assertRaisesRegex(ValueError, '不能当成'):
            work.record_from_evidence(project([fact()]), ['condition'])

    def test_csv_exports_zero_reviewed_facts_as_candidates_with_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = make_pdf(Path(tmp)/'test.pdf', [['Temperature 230 C']])
            p = work.open_paper({'local_pdf': str(pdf), 'title': 'Fixture', 'doi': '10.1234/fixture'}, Path(tmp)/'projects')
            entry = fact(); entry['source_ref'] = {'source_sha256': p['article']['source_sha256']}
            p.update(evidence=[entry], pages=[{'page': 1, 'text': 'Temperature 230 C'}],
                     numeric_extraction_version='scientific-numeric/1.0')
            folder = view.export_numeric_inventory(p)
            with (folder/'本篇科学数值_待核对.csv').open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]['原值'], '230.0')
            self.assertEqual(rows[0]['可直接用于训练'], 'False')
            self.assertEqual(rows[0]['原句'], entry['quote'])
            self.assertEqual(rows[0]['审核状态'], 'unreviewed')
            self.assertEqual(p['records'], [])
            self.assertTrue((folder/'00_本篇数值清单.html').is_file())


if __name__ == '__main__':
    unittest.main()
