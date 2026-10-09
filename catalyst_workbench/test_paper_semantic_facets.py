"""Synthetic, source-fidelity regressions; not an accuracy benchmark."""
import copy
import unittest

from paper_semantic_facets import FACET_LABELS, enrich_candidates
from paper_semantics import MAX_TEXT_LENGTH


class ScientificFacetTests(unittest.TestCase):
    def parse(self, text, existing=None):
        rows = enrich_candidates(text, existing or [])
        def evidence(value):
            if isinstance(value, dict):
                if 'evidence_quote' in value:
                    self.assertEqual(value['evidence_quote'], text[value['start']:value['end']])
                for v in value.values(): evidence(v)
            elif isinstance(value, list):
                for v in value: evidence(v)
        evidence(rows)
        for row in rows[len(existing or []):]:
            self.assertIsNone(row['value'])
            self.assertIsNone(row['value_high'])
            self.assertFalse(row['usable'])
            self.assertFalse(row['numeric_eligible'])
            self.assertEqual(row['review_status'], 'unreviewed')
            self.assertEqual(row['sample_label'], '')
            self.assertEqual(row['conditions'], {})
        for row in rows:
            for facet in row.get('semantic_facets', []):
                self.assertIn(facet['type'], FACET_LABELS)
                self.assertFalse(facet['usable'])
            for mention in row.get('conditions_mentions', []):
                self.assertIn(mention['role'], ('preparation', 'test', 'unknown'))
                self.assertEqual(mention['binding_status'], 'needs_review')
        return rows

    def facet(self, text, kind):
        rows = self.parse(text)
        found = [(r, f) for r in rows for f in r['semantic_facets'] if f['type'] == kind]
        self.assertEqual(len(found), 1, rows)
        return found[0]

    def lacks(self, text, kind):
        rows = self.parse(text)
        self.assertFalse(any(f['type'] == kind for r in rows for f in r.get('semantic_facets', [])), rows)

    def test_condition_dependence_english(self):
        row, facet = self.facet('The reaction conversion depended on temperature and was tested at 300 °C.', 'condition_dependence')
        self.assertEqual(row['conditions_mentions'][0]['value'], 300)
        self.assertEqual(row['conditions_mentions'][0]['role'], 'test')

    def test_condition_dependence_chinese(self):
        self.facet('催化活性随温度升高而增加。', 'condition_dependence')

    def test_single_condition_does_not_prove_dependence(self):
        self.lacks('The reaction was tested at 300 °C.', 'condition_dependence')

    def test_preparation_is_not_test_condition(self):
        text = 'The sample was calcined at 500 °C for 3 h.'
        old = [dict(start=0, end=len(text), evidence_quote=text, kind='qualitative', evidence_id='keep')]
        row = self.parse(text, old)[0]
        self.assertEqual(row['conditions_mentions'][0]['role'], 'preparation')

    def test_multiple_temperatures_unknown_role(self):
        row, _ = self.facet('The optimum reaction conversion was tested at 300 °C and 400 °C.', 'optimum')
        self.assertEqual([x['value'] for x in row['conditions_mentions']], [300, 400])
        self.assertTrue(all(x['role'] == 'unknown' for x in row['conditions_mentions']))

    def test_explicit_preparation_and_test_clauses_english(self):
        text = 'The sample was calcined at 500°C and tested at 250°C.'
        old = [dict(start=0, end=len(text), evidence_quote=text, kind='qualitative', evidence_id='phase-evidence')]
        row = self.parse(text, old)[0]
        self.assertEqual([(m['value'], m['role']) for m in row['conditions_mentions']], [(500, 'preparation'), (250, 'test')])
        self.assertTrue(all(m['sample_label'] == '' for m in row['conditions_mentions']))

    def test_explicit_preparation_and_test_clauses_chinese(self):
        text = '500℃焙烧，在250℃测试。'
        old = [dict(start=0, end=len(text), evidence_quote=text, kind='qualitative', evidence_id='phase-zh')]
        row = self.parse(text, old)[0]
        self.assertEqual([(m['value'], m['role']) for m in row['conditions_mentions']], [(500, 'preparation'), (250, 'test')])

    def test_postfix_english_phase_role(self):
        text = '500°C calcined + tested at 250°C.'
        old = [dict(start=0, end=len(text), evidence_quote=text, kind='qualitative')]
        row = self.parse(text, old)[0]
        self.assertEqual([(m['value'], m['role']) for m in row['conditions_mentions']], [(500, 'preparation'), (250, 'test')])

    def test_future_test_does_not_relabel_preparation_or_original(self):
        text = 'The sample was calcined at 500°C, and it will be tested at 250°C.'
        old = [dict(start=0, end=len(text), evidence_quote=text, kind='absolute', value=90, evidence_id='unchanged', review_status='reviewed')]
        row = self.parse(text, old)[0]
        self.assertEqual((row['kind'], row['value'], row['review_status']), ('absolute', 90, 'reviewed'))
        self.assertNotIn('assertion_status', row)
        self.assertEqual(row['conditions_mentions'][0]['assertion_status'], ['asserted'])
        self.assertEqual(row['conditions_mentions'][1]['assertion_status'], ['future_or_modal'])

    def test_future_test_does_not_relabel_chinese_preparation(self):
        text = '500℃焙烧，随后将在250℃测试。'
        old = [dict(start=0, end=len(text), evidence_quote=text, kind='qualitative')]
        row = self.parse(text, old)[0]
        self.assertEqual(row['conditions_mentions'][0]['assertion_status'], ['asserted'])
        self.assertEqual(row['conditions_mentions'][1]['assertion_status'], ['future_or_modal'])

    def test_pressure_unit_not_converted(self):
        row, _ = self.facet('The measured conversion depended on pressure and was tested at 0.2 MPa.', 'condition_dependence')
        self.assertEqual((row['conditions_mentions'][0]['value'], row['conditions_mentions'][0]['unit']), (.2, 'MPa'))

    def test_uppercase_bound_retains_direction(self):
        row, _ = self.facet('Above 300 °C, the catalyst had the highest activity.', 'optimum')
        self.assertEqual(row['conditions_mentions'][0]['operator'], 'gt')

    def test_optimum_english(self):
        _, f = self.facet('The maximum NO conversion was observed at 350 °C.', 'optimum')
        self.assertFalse(f['parameters']['global_optimality_proven'])
        self.assertEqual(f['parameters']['extremum_type'], 'maximum')

    def test_optimum_chinese(self):
        self.facet('反应在温度为250–350°C时具有最佳活性温窗。', 'optimum')

    def test_mixed_extrema_are_not_one_global_minimum(self):
        _, f = self.facet('The catalyst activity was the lowest of the four catalysts with a maximum NO conversion of 50%.', 'optimum')
        self.assertEqual(f['parameters']['extremum_type'], 'mixed_or_ambiguous')
        self.assertEqual([m['form'] for m in f['parameters']['extrema_mentions']], ['minimum', 'maximum'])

    def test_hardware_maximum_is_not_optimum(self):
        self.lacks('The maximum oven temperature was 600 °C.', 'optimum')

    def test_stability_hours(self):
        _, f = self.facet('The catalyst remained stable at 300 °C for 40 h.', 'stability')
        self.assertEqual(f['parameters']['durations'][0]['value'], 40)

    def test_stability_cycles_chinese(self):
        _, f = self.facet('催化剂在5次循环后保持稳定。', 'stability')
        self.assertEqual(f['parameters']['cycles'][0]['value'], 5)

    def test_stabilization_protocol_not_material_stability(self):
        self.lacks('The catalyst instrument needed 1 h to obtain stable readings.', 'stability')

    def test_duration_without_stability_is_not_stability(self):
        self.lacks('The catalyst was tested for 40 h.', 'stability')

    def test_preparation_hours_not_stability_duration(self):
        row, f = self.facet('The catalyst was calcined at 500 °C for 4 h and retained its activity for 20 h.', 'stability')
        self.assertEqual([d['value'] for d in f['parameters']['durations']], [20])
        self.assertEqual([d['value'] for d in f['parameters']['excluded_preparation_durations']], [4])
        self.assertEqual(f['parameters']['durations'][0]['timing_context'], 'stability_statement')
        self.assertTrue(all(r['role'] != 'preparation' for r in row['semantic_roles'] if r['facet_type'] == 'stability'))

    def test_chinese_preparation_hours_not_stability_duration(self):
        _, f = self.facet('催化剂在500℃焙烧4小时，并保持活性20小时。', 'stability')
        self.assertEqual([d['value'] for d in f['parameters']['durations']], [20])
        self.assertEqual([d['value'] for d in f['parameters']['excluded_preparation_durations']], [4])

    def test_future_stability_stays_outlook(self):
        row, f = self.facet('The catalyst will remain stable for 100 h.', 'stability')
        self.assertEqual(row['kind'], 'outlook')
        self.assertIn('future_or_modal', f['assertion_status'])

    def test_hypothetical_stability_separate_from_facet(self):
        _, f = self.facet('If the catalyst retained its activity for 20 h, it would be suitable.', 'stability')
        self.assertIn('hypothetical', f['assertion_status'])
        self.assertIn('future_or_modal', f['assertion_status'])

    def test_deactivation_english(self):
        self.facet('The catalyst underwent deactivation after the test.', 'deactivation')

    def test_deactivation_chinese(self):
        self.facet('催化剂出现活性衰退。', 'deactivation')

    def test_activation_is_not_deactivation(self):
        self.lacks('The catalyst activation was performed at 300 °C.', 'deactivation')

    def test_denied_deactivation_is_not_loss(self):
        row, f = self.facet('No deactivation of the catalyst was observed.', 'deactivation')
        self.assertEqual(row['kind'], 'negated')
        self.assertIn('negated', f['assertion_status'])

    def test_recovery_english(self):
        self.facet('The catalyst activity was completely restored after removing water.', 'recovery')

    def test_recovery_chinese(self):
        self.facet('除水后催化剂活性逐渐恢复。', 'recovery')

    def test_recovered_physical_sample_not_performance_recovery(self):
        self.lacks('The recovered catalyst sample was dried.', 'recovery')

    def test_water_tolerance(self):
        row, f = self.facet('The tested catalyst was resistant to 5 vol% H2O.', 'perturbation_tolerance')
        self.assertEqual(f['parameters']['response_type'], 'reported_tolerance')
        self.assertFalse(f['parameters']['tolerance_proven'])
        self.assertEqual(row['conditions_mentions'][0]['value'], 5)

    def test_conversion_percent_is_not_gas_composition(self):
        for text in ('The catalyst reached 90% NO conversion.',
                     'The maximum conversion was higher than 90% NO conversion efficiency.',
                     '3% Fe gave >90% NO conversion.',
                     '催化剂达到90%NO转化率。',
                     '最高性能高于90%NO转化率。'):
            old = [dict(start=0, end=len(text), evidence_quote=text, kind='qualitative')]
            rows = self.parse(text, old)
            self.assertFalse(any(m['entity'] == 'gas_composition' for r in rows for m in r.get('conditions_mentions', [])), rows)

    def test_explicit_feed_ppm_and_water_percent(self):
        text = 'The tested catalyst remained stable for 10 h with a feed containing 500 ppm NO and 5% H2O.'
        row, _ = self.facet(text, 'stability')
        gas = [m for m in row['conditions_mentions'] if m['entity'] == 'gas_composition']
        self.assertEqual([(m['value'], m['unit'], m['gas']) for m in gas], [(500, 'ppm', 'NO'), (5, '%', 'H2O')])

    def test_percent_gas_without_concentration_context_not_guessed(self):
        text = 'The catalyst contained a performance phrase: 90% NO removal.'
        old = [dict(start=0, end=len(text), evidence_quote=text, kind='qualitative')]
        rows = self.parse(text, old)
        self.assertEqual(rows[0].get('conditions_mentions', []), [])

    def test_prepared_catalyst_does_not_own_performance_window(self):
        text = ('Brandenberger et al. prepared a Fe-ZSM-5 catalyst that could maintain >80% NO conversion '
                'over the temperature range (300 to 600°C).')
        old = [dict(start=0, end=len(text), evidence_quote=text, kind='outlook')]
        rows = self.parse(text, old)
        temperatures = [m for m in rows[0].get('conditions_mentions', []) if m['entity'] == 'temperature']
        self.assertEqual(len(temperatures), 1)
        self.assertEqual(temperatures[0]['role'], 'unknown')

    def test_methanol_synthesis_window_not_material_preparation(self):
        text = 'Within the conventional methanol synthesis window (180–250°C), the catalyst exhibits high stability for 40 h.'
        row, _ = self.facet(text, 'stability')
        self.assertEqual(row['conditions_mentions'][0]['role'], 'unknown')
        self.assertTrue(all(role['role'] == 'unknown' for role in row['semantic_roles']))

    def test_local_drying_action_still_preparation(self):
        text = 'The sample was dried at 50°C.'
        old = [dict(start=0, end=len(text), evidence_quote=text, kind='qualitative')]
        row = self.parse(text, old)[0]
        self.assertEqual(row['conditions_mentions'][0]['role'], 'preparation')
        self.assertEqual(row['conditions_mentions'][0]['role_trigger_evidence']['evidence_quote'], 'dried')

    def test_sulfur_inhibition_chinese(self):
        _, f = self.facet('二氧化硫明显抑制催化剂活性。', 'perturbation_tolerance')
        self.assertEqual(f['parameters']['response_type'], 'inhibition')

    def test_water_as_solvent_not_tolerance(self):
        self.lacks('The catalyst was washed with water and dried.', 'perturbation_tolerance')

    def test_tolerance_denial_separate(self):
        row, f = self.facet('The catalyst was not resistant to sulfur.', 'perturbation_tolerance')
        self.assertEqual(row['kind'], 'negated')
        self.assertIn('negated', f['assertion_status'])

    def test_resistance_to_poisoning_not_inhibition(self):
        _, f = self.facet('The catalyst showed resistance to chemical poisoning by sulfur.', 'perturbation_tolerance')
        self.assertEqual(f['parameters']['response_type'], 'reported_tolerance')
        self.assertEqual(f['parameters']['response_context'], 'tolerance_property')

    def test_chinese_resistance_to_poisoning_not_inhibition(self):
        _, f = self.facet('该催化剂具有抗硫中毒能力。', 'perturbation_tolerance')
        self.assertEqual(f['parameters']['response_type'], 'reported_tolerance')

    def test_poison_resistance_criterion_not_observed_response(self):
        text = ('Hydrothermal stability and resistance to chemical poisoning '
                '(e.g., sulfur and phosphorus) are among the most important criteria for catalyst selection.')
        _, f = self.facet(text, 'perturbation_tolerance')
        self.assertEqual(f['parameters']['response_type'], 'unknown')
        self.assertEqual(f['parameters']['response_context'], 'evaluation_criterion')
        self.assertFalse(f['parameters']['tolerance_proven'])

    def test_chinese_tolerance_criterion_not_observed_response(self):
        _, f = self.facet('抗硫中毒能力是催化剂的重要评价指标。', 'perturbation_tolerance')
        self.assertEqual(f['parameters']['response_type'], 'unknown')
        self.assertEqual(f['parameters']['response_context'], 'evaluation_criterion')

    def test_recovered_after_poisoning_not_current_inhibition(self):
        _, f = self.facet('The catalyst activity recovered after sulfur poisoning.', 'perturbation_tolerance')
        self.assertEqual(f['parameters']['response_type'], 'unknown')
        self.assertEqual(f['parameters']['response_context'], 'recovery_context')

    def test_chinese_recovered_after_poisoning(self):
        _, f = self.facet('催化剂活性在硫中毒后恢复。', 'perturbation_tolerance')
        self.assertEqual(f['parameters']['response_context'], 'recovery_context')
        self.assertNotEqual(f['parameters']['response_type'], 'inhibition')

    def test_actual_poisoning_predicate_keeps_inhibition(self):
        _, f = self.facet('SO2 poisoning inhibited the catalyst activity.', 'perturbation_tolerance')
        self.assertEqual(f['parameters']['response_type'], 'inhibition')
        self.assertEqual(f['parameters']['response_context'], 'explicit_adverse_predicate')

    def test_chinese_actual_poisoning_keeps_inhibition(self):
        _, f = self.facet('SO2硫中毒抑制了催化剂活性。', 'perturbation_tolerance')
        self.assertEqual(f['parameters']['response_type'], 'inhibition')

    def test_denied_poisoning_effect_not_inhibition(self):
        _, f = self.facet('SO2 poisoning did not inhibit the catalyst activity.', 'perturbation_tolerance')
        self.assertEqual(f['parameters']['response_type'], 'unknown')
        self.assertIn('negated', f['assertion_status'])

    def test_chinese_denied_poisoning_effect_not_inhibition(self):
        _, f = self.facet('SO2未抑制催化剂活性。', 'perturbation_tolerance')
        self.assertEqual(f['parameters']['response_type'], 'unknown')
        self.assertIn('negated', f['assertion_status'])

    def test_as_expected_stability_is_not_future(self):
        _, f = self.facet('The catalyst remained stable for 40 h, as one would expect.', 'stability')
        self.assertEqual(f['assertion_status'], ['asserted'])

    def test_expectation_aside_does_not_remove_actual_may(self):
        row, f = self.facet('As expected, the catalyst may remain stable for 40 h.', 'stability')
        self.assertEqual(row['kind'], 'outlook')
        self.assertIn('future_or_modal', f['assertion_status'])

    def test_tradeoff_english(self):
        _, f = self.facet('The conversion increased but the selectivity decreased.', 'tradeoff')
        self.assertEqual(f['parameters']['metrics'], ['conversion', 'selectivity'])

    def test_tradeoff_chinese(self):
        self.facet('提高活性是以牺牲稳定性为代价。', 'tradeoff')

    def test_both_improved_not_tradeoff(self):
        self.lacks('The conversion and selectivity both increased.', 'tradeoff')

    def test_single_metric_not_tradeoff(self):
        self.lacks('The activity increased but temperature decreased.', 'tradeoff')

    def test_uncertainty_plus_minus_keeps_type_unknown(self):
        _, f = self.facet('The mean concentration was 40 ± 2 ppm.', 'uncertainty')
        self.assertEqual(f['parameters']['uncertainty_type'], 'unspecified')
        self.assertEqual(f['parameters']['reported_values'][0]['error'], 2)
        self.assertFalse(f['parameters']['replicate_count_assumed'])

    def test_uncertainty_chinese_explicit_std(self):
        _, f = self.facet('测量浓度为40±2 ppm，其中2 ppm表示标准差。', 'uncertainty')
        self.assertEqual(f['parameters']['uncertainty_type'], 'standard_deviation')

    def test_confidence_interval_not_conversion(self):
        _, f = self.facet('The measured activity had a 95% confidence interval.', 'uncertainty')
        self.assertEqual(f['parameters']['uncertainty_type'], 'confidence_interval')

    def test_suggested_error_check_not_measured_uncertainty(self):
        row, f = self.facet('The signal should be investigated for measurement errors.', 'uncertainty')
        self.assertEqual(row['kind'], 'requirement')
        self.assertIn('recommended_or_required', f['assertion_status'])
        self.assertEqual(f['parameters']['reported_values'], [])

    def test_bare_arithmetic_not_measurement_uncertainty(self):
        self.lacks('The formula is x = a ± b.', 'uncertainty')

    def test_detection_limit_english(self):
        _, f = self.facet('The NO concentration was below the detection limit of 2 ppm.', 'detection_limit')
        self.assertEqual(f['parameters']['reported_limits'][0]['value'], 2)
        self.assertFalse(f['parameters']['zero_imputed'])

    def test_detection_limit_chinese(self):
        _, f = self.facet('NO未检出，检测限为1 ppm。', 'detection_limit')
        self.assertEqual(f['parameters']['detection_status'], 'not_detected_or_below_limit')
        self.assertEqual(f['parameters']['reported_limits'][0]['value'], 1)

    def test_undetected_without_reported_limit_not_zero(self):
        _, f = self.facet('NO2 was not detected.', 'detection_limit')
        self.assertEqual(f['parameters']['reported_limits'], [])
        self.assertFalse(f['parameters']['zero_imputed'])

    def test_conversion_bound_not_detection_limit(self):
        self.lacks('The NO conversion was below 10%.', 'detection_limit')

    def test_preserves_existing_identity_kind_and_review(self):
        text = 'The catalyst will remain stable for 100 h.'
        old = [dict(evidence_id='e-original', semantic_id_key='old-id', start=0, end=len(text), evidence_quote=text,
                    kind='outlook', value=None, review_status='reviewed', custom_flag={'a': [1]})]
        saved = copy.deepcopy(old)
        rows = self.parse(text, old)
        self.assertEqual(old, saved)
        self.assertEqual(len(rows), 1)
        for key, value in saved[0].items(): self.assertEqual(rows[0][key], value)
        self.assertEqual(rows[0]['semantic_facets'][0]['type'], 'stability')

    def test_idempotent_enrichment(self):
        text = 'The catalyst remained stable for 40 h.'
        once = self.parse(text)
        twice = self.parse(text, once)
        self.assertEqual(once, twice)

    def test_pronouns_not_bound_from_previous_sample(self):
        text = 'Sample S1 was prepared. Its catalytic activity will remain stable for 40 h.'
        rows = self.parse(text)
        self.assertEqual(rows[0]['sample_label'], '')
        self.assertEqual(rows[0]['context_evidence'][0]['binding_status'], 'needs_review')

    def test_plain_prose_and_plot_ticks_not_added(self):
        self.assertEqual(self.parse('The samples were shown in Figure 3.\n-100 -80 -60 0 20 40\nX axis [mm]'), [])

    def test_references_not_added(self):
        self.assertEqual(self.parse('References\nThe maximum catalytic activity was stable for 40 h.'), [])

    def test_ligature_and_whitespace_offsets(self):
        self.facet('The catalyst showed the highest efﬁciency at\n300 ◦C.', 'optimum')

    def test_input_contract(self):
        with self.assertRaises(TypeError): enrich_candidates(None, [])
        with self.assertRaises(ValueError): enrich_candidates('a' * (MAX_TEXT_LENGTH + 1), [])


if __name__ == '__main__':
    unittest.main()
