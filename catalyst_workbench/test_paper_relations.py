"""Synthetic behavior cases plus explicitly labeled published excerpts.

These tests establish source fidelity and guards, not benchmark accuracy.
"""
import unittest

from paper_relations import relation_candidates
from paper_semantics import MAX_TEXT_LENGTH, semantic_candidates


class RelationCandidateTests(unittest.TestCase):
    def parse(self, text, existing=None):
        rows = relation_candidates(text, existing)
        def quotes(v):
            if isinstance(v, dict):
                if 'evidence_quote' in v:
                    self.assertEqual(v['evidence_quote'], text[v['start']:v['end']])
                for value in v.values():
                    quotes(value)
            elif isinstance(v, list):
                for value in v:
                    quotes(value)
        quotes(rows)
        for item in rows:
            self.assertIsNone(item['value'])
            self.assertIsNone(item['value_high'])
            self.assertFalse(item['numeric_eligible'])
            self.assertEqual(item['review_status'], 'unreviewed')
            self.assertEqual(item['conditions'], {})
            self.assertNotEqual(item['kind'], 'absolute')
        return rows

    def one(self, text, existing=None):
        rows = self.parse(text, existing)
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def test_user_future_large_growth_without_number(self):
        row = self.one('它将会大幅度增长')
        self.assertEqual((row['kind'], row['metric']), ('outlook', 'unknown'))
        self.assertTrue(row['unresolved_subject'])
        self.assertEqual(row['relations'][0]['direction'], 'increase')
        self.assertEqual(row['relations'][0]['strength'], '大幅度')

    def test_reported_trend_as_one_would_expect_is_not_future(self):
        row=self.one('From this plot, it is seen that low temperature NO conversion increases with increasing Fe loading as one would expect.')
        self.assertNotEqual(row['kind'],'outlook')
        future=self.one('As expected, catalytic activity may increase in future experiments.')
        self.assertEqual(future['kind'],'outlook')

    def test_chinese_relative_percent(self):
        row = self.one('A比B高20%。')
        rel = row['relations'][0]
        self.assertEqual((row['kind'], rel['target'], rel['reference'], rel['value'], rel['unit']), ('comparison', 'A', 'B', 20, '%'))
        self.assertEqual(rel['ratio_definition'], '(target-reference)/reference')

    def test_chinese_percentage_points(self):
        rel = self.one('A比B低5个百分点。')['relations'][0]
        self.assertEqual((rel['type'], rel['value'], rel['unit'], rel['direction']), ('difference', 5, 'percentage points', 'decrease'))

    def test_chinese_explicit_ratio(self):
        rel = self.one('A是B的2倍。')['relations'][0]
        self.assertEqual((rel['type'], rel['target'], rel['reference'], rel['value']), ('ratio', 'A', 'B', 2))

    def test_increased_by_twofold_is_ambiguous(self):
        for text in ('提高了两倍。', 'The response increased by twofold.'):
            row = self.one(text)
            self.assertEqual(row['kind'], 'ambiguous')
            self.assertEqual(row['relations'][0]['value'], 2)
            self.assertEqual(row['relations'][0]['ratio_definition'], 'unresolved_increase_by_fold')

    def test_increased_to_twofold_has_direction(self):
        rel = self.one('提高到两倍。')['relations'][0]
        self.assertEqual((rel['type'], rel['value'], rel['ratio_definition']), ('ratio', 2, 'final/initial'))

    def test_english_ratio(self):
        rel = self.one('A is two times as high as B.')['relations'][0]
        self.assertEqual((rel['target'], rel['reference'], rel['value']), ('A', 'B', 2))

    def test_english_relative_percent(self):
        rel = self.one('A was 20% higher than B.')['relations'][0]
        self.assertEqual((rel['target'], rel['reference'], rel['unit']), ('A', 'B', '%'))

    def test_english_percentage_points(self):
        rel = self.one('A was 20 percentage points lower than B.')['relations'][0]
        self.assertEqual((rel['type'], rel['unit'], rel['direction']), ('difference', 'percentage points', 'decrease'))

    def test_increase_by_percentage_retains_unknown_baseline(self):
        rel = self.one('The activity increased by 20%.')['relations'][0]
        self.assertEqual((rel['value'], rel['reference']), (20, ''))

    def test_real_catal_three_percent_lower(self):
        """catal8060231, page 4."""
        rel = self.one('The average NOx concentration is 3% lower than the raw engine out emission.')['relations'][0]
        self.assertEqual((rel['value'], rel['direction'], rel['reference']), (3, 'decrease', 'the raw engine out emission'))

    def test_real_catal_inlet_ratio_67(self):
        """catal8060231, pages 4/5 duplicate layers."""
        row = self.one('For the given test conditions, the NO2/NOx ratio at the\nSCR inlet was 67%.')
        rel = row['relations'][0]
        self.assertEqual((row['kind'], rel['target'], rel['reference'], rel['value']), ('ratio', 'NO2', 'NOx', 67))

    def test_ratio_threshold_condition_not_measured_label(self):
        row = self.one('When the NO2/NOx ratio exceeds 50%, then reaction (3) becomes operative.')
        self.assertEqual(row['assertion_mode'], 'conditional')
        self.assertEqual(row['relations'][0]['operator'], 'gt')

    def test_qualitative_comparison_without_number(self):
        row = self.one('A is higher than B.')
        self.assertEqual(row['relations'][0]['type'], 'qualitative_comparison')
        self.assertEqual(row['relations'][0]['reference'], 'B')

    def test_numeric_comparison_is_threshold_not_reference_object(self):
        """catal8060231 page 2: a general UI capability threshold."""
        row = self.one('Well-optimized mixing systems are able to achieve UI values greater than 0.98.')
        rel = row['relations'][0]
        self.assertEqual((rel['type'], rel['subtype'], rel['value'], rel['operator']),
                         ('constraint', 'numeric_threshold', 0.98, 'gt'))
        self.assertEqual(rel['reference'], '')
        self.assertEqual(rel['unit'], 'dimensionless')

    def test_lower_numeric_threshold_keeps_decimal_and_bound(self):
        rel = self.one('The concentration was lower than 0.25 ppm.')['relations'][0]
        self.assertEqual((rel['value'], rel['unit'], rel['operator'], rel['reference']), (0.25, 'ppm', 'lt', ''))

    def test_sample_label_with_number_is_not_numeric_threshold(self):
        rel = self.one('The activity was greater than sample S2.')['relations'][0]
        self.assertEqual(rel['type'], 'qualitative_comparison')
        self.assertEqual(rel['reference'], 'sample S2')

    def test_chinese_qualitative_comparison_direction(self):
        row = self.one('A比B高。')
        self.assertEqual(row['relations'][0]['direction'], 'increase')

    def test_relative_change_before_after_is_not_one_label(self):
        row = self.one('The concentration increased from 20 ppm to 30 ppm.')
        rel = row['relations'][0]
        self.assertEqual((rel['source_value'], rel['target_value']), (20, 30))

    def test_real_quotient_definition_keeps_formula(self):
        """catal8060231, page 3, shortened complete definition."""
        text = ('The quantity of urea that was dosed is expressed by a stoichiometric ratio,α, which was calculated\n'
                'as a quotient of the amount of ammonia molecules from the urea NH3in and the amount of nitrogen\n'
                'oxides molecules NOxin in the elementary exhaust gas mass ﬂow:\nα = NH3in\nNOxin\n(5)')
        row = self.one(text)
        self.assertEqual(row['kind'], 'calculation')
        self.assertEqual(row['relations'][0]['evaluation_status'], 'not_evaluated')
        self.assertIn('NH3in', row['relations'][0]['expression'])

    def test_real_ammonia_formula_source_preserved(self):
        text = ('Based on the obtained measurement results, the ammonia concentration at SCR inlet face NH3ini '
                'is calculated according to the formula:\nNH3ini [ppm] = NOxini− NOxouti + NH3outi (6)')
        row = self.one(text)
        self.assertEqual(row['kind'], 'calculation')
        self.assertEqual(row['assertion_scope'], 'unknown')
        self.assertIn('NOxini− NOxouti', row['relations'][0]['expression'])
        self.assertEqual(row['relations'][0]['formula_status'], 'expression_present_requires_review')

    def test_formula_cue_without_expression_is_flagged(self):
        row = self.one('For this purpose, the uniformity index (UI) is calculated according to the formula:')
        self.assertEqual(row['kind'], 'calculation')
        self.assertEqual(row['relations'][0]['formula_status'], 'missing_or_unreadable')
        self.assertTrue(any('未找到可辨认' in w for w in row['warnings']))

    def test_formula_interrupted_by_ticks_is_flagged(self):
        text = ('The uniformity index is calculated according to the formula:\n'
                '-100 -80 -60 0 20 40 60\nX axis [mm]\nY axis [mm]')
        row = self.one(text)
        self.assertNotIn('-100', row['evidence_quote'])
        self.assertEqual(row['relations'][0]['formula_status'], 'missing_or_unreadable')

    def test_quotient_without_printed_equation_is_textual_definition(self):
        row = self.one('The ratio is calculated as a quotient of A and B.')
        self.assertEqual(row['relations'][0]['formula_status'], 'textual_definition_requires_review')

    def test_chinese_calculation(self):
        row = self.one('平均浓度根据测量值计算，计算公式为C=(A+B)/2。')
        self.assertEqual(row['kind'], 'calculation')

    def test_real_expected_alpha_lower(self):
        row = self.one('It was expected that the mean α value was lower than the setpoint value.')
        self.assertEqual(row['kind'], 'outlook')
        self.assertEqual(row['relations'][0]['reference'], 'the setpoint value')

    def test_real_alpha_investigation_is_recommendation(self):
        row = self.one('Cases of αmean values higher than the setpoint α should be investigated in detail for measurement errors.')
        self.assertEqual(row['kind'], 'requirement')
        self.assertEqual(row['assertion_mode'], 'recommended')
        self.assertTrue(all(r['assertion'] == 'recommended' for r in row['relations']))
        self.assertEqual(row['relations'][0]['reference'], 'the setpoint α')
        self.assertTrue(any('建议' in w for w in row['warnings']))

    def test_real_slow_scr_constraint(self):
        text = 'The NOx reduction of the slow SCR reaction mechanism is limited by kinetic factors other than the inlet NH3 maldistribution [10].'
        row = self.one(text)
        self.assertEqual(row['relations'][0]['type'], 'constraint')
        self.assertEqual(row['assertion_scope'], 'unknown')

    def test_causal_explanation_not_established_causality(self):
        rel = self.one('The activity increased because the pore volume was larger.')['relations'][0]
        self.assertEqual(rel['type'], 'explanation')
        self.assertEqual(rel['causal_status'], 'author_statement_not_established_causality')

    def test_correlation_does_not_become_causality(self):
        rel = self.one('温度与活性呈正相关。')['relations'][0]
        self.assertEqual((rel['type'], rel['direction']), ('correlation', 'positive'))

    def test_negative_correlation(self):
        rel = self.one('A is negatively correlated with B.')['relations'][0]
        self.assertEqual((rel['type'], rel['direction']), ('correlation', 'negative'))

    def test_negated_increase_not_zero(self):
        row = self.one('The activity did not increase by 20%.')
        self.assertEqual(row['kind'], 'negated')
        self.assertEqual(row['relations'][0]['value'], 20)
        self.assertEqual(row['relations'][0]['assertion'], 'negated')

    def test_no_change_without_metric_still_meaningful(self):
        row = self.one('No significant change was observed.')
        self.assertEqual(row['kind'], 'negated')

    def test_assumed_no_conversion_not_actual_zero(self):
        row = self.one('Assuming no NOx conversion in the SCR, the inlet and outlet concentrations are equivalent.')
        self.assertEqual(row['kind'], 'negated')
        self.assertTrue(any('假设' in w for w in row['warnings']))

    def test_prior_work_not_current_result(self):
        row = self.one('According to previous work, the activity increased substantially.')
        self.assertEqual(row['assertion_scope'], 'prior_work')

    def test_explicit_current_scope_without_approval(self):
        row = self.one('In this study, the activity increased substantially.')
        self.assertEqual(row['assertion_scope'], 'current_study')

    def test_pronoun_context_not_binding(self):
        rows = self.parse('Sample S1 was prepared. It will increase substantially.')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['sample_label'], '')
        self.assertTrue(rows[0]['context_evidence'])
        self.assertEqual(rows[0]['context_evidence'][0]['binding_status'], 'needs_review')

    def test_precise_existing_comparison_not_duplicated(self):
        text = 'NO conversion increased by 10%.'
        self.assertEqual(self.parse(text, semantic_candidates(text)), [])

    def test_existing_requirement_not_duplicated(self):
        text = 'NOx conversion efficiency above 95% is demanded.'
        self.assertEqual(self.parse(text, semantic_candidates(text)), [])

    def test_distinct_calculation_not_suppressed_by_absolute(self):
        text = 'The concentration was calculated using C=A/B.'
        old = [dict(kind='absolute', start=0, end=len(text), metric='concentration', value=1)]
        self.assertEqual(len(self.parse(text, old)), 1)

    def test_duplicate_text_layer_is_recorded_once(self):
        text = 'The activity increased substantially.\nThe activity increased substantially.'
        rows = self.parse(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]['duplicate_evidence']), 1)

    def test_plain_method_sentence_not_blanket_candidate(self):
        self.assertEqual(self.parse('The experiment used a fixed-bed reactor. The sample was S1.'), [])

    def test_reduction_is_process_name_not_trend(self):
        self.assertEqual(self.parse('Selective catalytic reduction (SCR) was studied. SCR: selective catalytic reduction.'), [])

    def test_reducing_agent_is_not_direction(self):
        self.assertEqual(self.parse('Urea was used as a reducing agent.'), [])

    def test_figure_caption_not_explanation(self):
        self.assertEqual(self.parse('Figure 3. Results of test run with an active UWS dose; (a) NH3 concentration.'), [])

    def test_axes_and_bare_numbers_ignored(self):
        self.assertEqual(self.parse('-100 -80 -60 0 20 40 60\nX axis [mm]\nY axis [mm]\n1 2 3 4 5'), [])

    def test_reference_heading_stops_extraction(self):
        self.assertEqual(self.parse('References\n1. An increased catalytic activity was observed.'), [])

    def test_reference_continuation_page_ignored(self):
        text = ('Catalysts 2018, 8, 231 9 of 9\n14. Zheng, G.; Fila, A. Increased catalytic activity. 2010. [CrossRef]\n'
                '15. Koebel, M.; Strutz, E. Decreased conversion. 2003. [CrossRef]')
        self.assertEqual(self.parse(text), [])

    def test_input_contract(self):
        with self.assertRaises(TypeError):
            relation_candidates(None)
        with self.assertRaises(ValueError):
            relation_candidates('a' * (MAX_TEXT_LENGTH + 1))


if __name__ == '__main__':
    unittest.main()
