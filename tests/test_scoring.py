import unittest
from scrtool.scoring import score_record, validate_config, DEFAULT_WEIGHTS
from scrtool.literature import screen_records


class ScoringTests(unittest.TestCase):
    def record(self):
        return {'doi': '10.1016/test', 'title': 'NH₃-SCR catalysts',
                'abstract': 'We prepared catalysts and measured 90% NOx conversion at 200 °C. EXAFS revealed active sites.'}

    def test_score_and_evidence_reconcile(self):
        row = self.record(); result = score_record(row)
        self.assertEqual(result['priority_score'], 100)
        for component in result['score_components'].values():
            for evidence in component['evidence']:
                self.assertIn(evidence['quote'], row[evidence['field']])

    def test_incomplete_abstract_does_not_supply_score_evidence(self):
        row = self.record(); row['abstract_is_full'] = False
        result = score_record(row)
        self.assertEqual(result['priority_score'], 55)
        self.assertEqual(result['score_basis'], 'title_keywords_only')
        self.assertFalse(result['score_components']['experimental']['matched'])

    def test_written_out_experimental_and_characterization_methods(self):
        row = {'title': 'NH3-SCR over Cu-CHA catalysts',
               'abstract': 'We combine kinetic measurements and operando electron paramagnetic resonance spectroscopy.'}
        result = score_record(row)
        self.assertEqual(result['priority_score'], 80)
        self.assertTrue(result['score_components']['experimental']['matched'])
        self.assertEqual(result['score_components']['characterization']['evidence'][0]['quote'],
                         'electron paramagnetic resonance')

    def test_weight_changes_do_not_override_screening_or_manual_decisions(self):
        weights = {k: 0 for k in DEFAULT_WEIGHTS}; weights['characterization'] = 1
        row = self.record()
        first = screen_records([row])[0]
        second = screen_records([row], score_config={'weights': weights})[0]
        self.assertEqual(first['effective_decision'], second['effective_decision'])
        self.assertNotEqual(first['score_config_id'], second['score_config_id'])

    def test_invalid_weights_rejected(self):
        for bad in [{'weights': {}}, {'weights': {k: 0 for k in DEFAULT_WEIGHTS}},
                    {'weights': dict(DEFAULT_WEIGHTS, reaction=-1)},
                    {'weights': dict(DEFAULT_WEIGHTS, reaction=float('nan'))}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_config(bad)


if __name__ == '__main__': unittest.main()
