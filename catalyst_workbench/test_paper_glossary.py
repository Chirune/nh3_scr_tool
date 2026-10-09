"""Reading-aid regressions; these do not measure translation accuracy."""
import unittest

from paper_glossary import terms_for_text


class GlossaryTests(unittest.TestCase):
    def terms(self, text):
        return [row['term'] for row in terms_for_text(text)]

    def test_empty_and_non_string(self):
        for value in ('', '  ', None, 0, [], {}):
            self.assertEqual(terms_for_text(value), [])

    def test_distinguishes_adsorption_absorption_desorption(self):
        rows = terms_for_text('adsorption, absorption and desorption')
        self.assertEqual([r['zh'] for r in rows], ['吸附', '吸收', '脱附'])

    def test_no_false_chemical_symbols_in_words(self):
        self.assertEqual(terms_for_text('not current cursor peculiar ruin address support activity no bet sem'), [])

    def test_symbols_are_case_sensitive(self):
        self.assertEqual(self.terms('Pd Ru Cu NO'), ['Pd / palladium', 'Ru / ruthenium', 'Cu / copper', 'NO'])
        self.assertEqual(self.terms('pd ru cu no'), [])

    def test_unicode_formulas_are_matched_without_modifying_evidence(self):
        sentence = 'NH₃-SCR over Pd/CeO₂ contained Ce³⁺ at 250 °C.'
        saved = sentence
        result = self.terms(sentence)
        self.assertEqual(result, ['NH₃-SCR', 'Pd / palladium', 'CeO₂ / ceria', 'Ce³⁺'])
        self.assertEqual(sentence, saved)

    def test_longer_compound_suppresses_substrings_only_at_same_span(self):
        self.assertEqual(self.terms('NH₃-TPD'), ['NH₃-TPD'])
        self.assertEqual(self.terms('NH₃-TPD uses NH₃. TPD was measured.'), ['NH₃-TPD', 'NH₃', 'TPD'])

    def test_english_expanded_name_and_acronym_are_deduplicated(self):
        self.assertEqual(self.terms('gas hourly space velocity (GHSV). GHSV'), ['GHSV'])

    def test_longer_technique_beats_shared_absorption_term(self):
        self.assertEqual(self.terms('X-ray absorption spectroscopy (XAS)'), ['XAS'])

    def test_chemical_vs_physical_adsorption_remain_distinct(self):
        self.assertEqual(self.terms('Chemical adsorption and physical adsorption'), ['chemisorption', 'physisorption'])

    def test_performance_measures_not_conflated(self):
        self.assertEqual([r['zh'] for r in terms_for_text('conversion, selectivity, yield')], ['转化率', '选择性', '收率'])

    def test_support_and_activity_need_local_domain_context(self):
        self.assertIn('support', self.terms('Ceria was used as the support.'))
        self.assertIn('activity', self.terms('The catalyst activity increased.'))
        self.assertEqual(self.terms('We offer support for this activity.'), [])
        self.assertNotIn('support', self.terms('The catalyst worked. We support this idea.'))
        self.assertNotIn('activity', self.terms('The catalyst was tested. Daily activity increased.'))

    def test_support_is_not_evidence_or_financial_support(self):
        self.assertNotIn('support', self.terms('These data support the catalyst mechanism.'))
        self.assertNotIn('support', self.terms('This catalyst study received financial support.'))
        self.assertNotIn('support', self.terms('The catalyst findings support the conclusion.'))

    def test_thermodynamic_activity_not_catalytic_activity(self):
        self.assertNotIn('activity', self.terms('In the reaction, thermodynamic activity changed.'))

    def test_sem_in_standard_error_phrase_not_microscope(self):
        terms = self.terms('Error bars represent standard error of the mean (SEM).')
        self.assertIn('standard error', terms)
        self.assertNotIn('SEM', terms)
        self.assertIn('SEM', self.terms('SEM images reveal the surface morphology.'))

    def test_oxide_ratio_not_atomic_ratio(self):
        self.assertEqual(self.terms('Si/Al and silica-to-alumina ratio (SiO₂/Al₂O₃)'), ['Si/Al', 'SiO₂/Al₂O₃'])

    def test_measurement_units_remain_separate(self):
        self.assertEqual(self.terms('5 wt.% and 3 at.% with 2 vol.% at 100 ppm (a.u.).'), ['wt.%', 'at.%', 'vol.%', 'ppm', 'a.u.'])

    def test_fraction_is_not_assumed_percent(self):
        self.assertNotIn('wt.%', self.terms('The weight fraction was 0.03.'))

    def test_comparative_language_is_explanation_only(self):
        rows = terms_for_text('The conversion increased by 5 percentage points and activity was 10-fold higher.')
        self.assertIn('percentage point', [r['term'] for r in rows])
        self.assertIn('fold change', [r['term'] for r in rows])
        for row in rows:
            self.assertEqual(set(row) - {'term', 'zh', 'explanation', 'source'}, set())
            self.assertTrue(all(isinstance(v, str) for v in row.values()))

    def test_rows_are_fresh_and_cannot_modify_catalog(self):
        first = terms_for_text('adsorption')
        first[0]['zh'] = 'changed'
        self.assertEqual(terms_for_text('adsorption')[0]['zh'], '吸附')

    def test_phrases_with_pdf_hyphens(self):
        self.assertEqual(self.terms('NH₃‑SCR and temperature‐programmed desorption.'), ['NH₃-SCR', 'TPD'])

    def test_no_acronym_inside_identifiers(self):
        self.assertEqual(self.terms('my_GHSV GHSV2 pNO NO2abc xyz_Cu'), [])

    def test_order_is_first_mention_not_catalog_order(self):
        self.assertEqual(self.terms('EXAFS, TOF, BET and adsorption. BET'), ['EXAFS', 'TOF', 'BET', 'adsorption'])

    def test_pdf_detached_ammonia_subscript(self):
        for formula in ('NH 3', 'NH\n3', 'NH\r\n 3', 'NH\u00a03', 'NH ₃'):
            with self.subTest(formula=formula):
                source = formula + ' adsorption improved.'
                self.assertEqual(self.terms(source), ['NH₃', 'adsorption'])
                self.assertEqual(source, formula + ' adsorption improved.')

    def test_pdf_detached_subscript_preserves_long_expression_priority(self):
        for formula in ('NH 3-TPD', 'NH\n3-TPD', 'NH 3 – TPD'):
            with self.subTest(formula=formula):
                self.assertEqual(self.terms(formula), ['NH₃-TPD'])
        self.assertEqual(self.terms('NH 3-SCR and H2-TPR'), ['NH₃-SCR', 'H₂-TPR'])
        self.assertEqual(self.terms('H 2-TPR'), ['H₂-TPR'])

    def test_pdf_formula_match_does_not_join_arbitrary_numbers(self):
        self.assertEqual(self.terms('NO 2 measurements were taken.'), ['NO'])
        self.assertEqual(self.terms('NO\n2 measurements were taken.'), ['NO'])
        self.assertNotIn('NH₃', self.terms('NH\n\n3'))
        self.assertNotIn('NH₃', self.terms('NH          3'))
        self.assertNotIn('NH₃', self.terms('NH 30'))
        self.assertNotIn('NH₃', self.terms('abcNH 3'))
        self.assertEqual(self.terms('NH 3 uptake exceeded NO 2 measurements.'), ['NH₃', 'NO'])


if __name__ == '__main__':
    unittest.main()
