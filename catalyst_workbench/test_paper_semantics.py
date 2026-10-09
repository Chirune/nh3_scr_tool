"""Behavior tests for conservative statements, not model accuracy claims."""
import unittest

from paper_semantics import MAX_TEXT_LENGTH, semantic_candidates


class SemanticCandidateTests(unittest.TestCase):
    def one(self, text):
        result = semantic_candidates(text)
        self.assertEqual(len(result), 1, result)
        return result[0]

    def test_observation_matching_expectation_is_not_a_prediction(self):
        for text in ('As expected, NO conversion was 90%.','正如预期，NO转化率为90%。'):
            item=self.one(text)
            self.assertEqual(item['kind'],'absolute');self.assertEqual(item['value'],90)
        future=self.one('As expected, NO conversion may reach 90%.')
        self.assertEqual(future['kind'],'outlook');self.assertIsNone(future['value'])

    def test_flow_units_are_not_slash_named_catalysts(self):
        from paper_semantics import _samples
        self.assertEqual(_samples('Filtration at a flow rate of 8 mL/min reduced to 10% of the feed.'),[])
        self.assertEqual([s[2] for s in _samples('Cu/CeO2 was tested at 8 mL/min.')],['Cu/CeO2'])

    def test_ratio_to_tenfold_keeps_relative_value(self):
        for text in ("NO conversion was ten times as high as the control.", "NO转化率提高到十倍。"):
            item = self.one(text)
            self.assertEqual((item["kind"], item["operator"], item["value"], item["unit"]),
                             ("comparison", "ratio", 10.0, "fold"))
            self.assertEqual(item["reference_sample"], "")
            self.assertFalse(item["numeric_eligible"])

    def test_by_tenfold_is_ambiguous(self):
        for text in ("NO conversion increased by tenfold.", "NO转化率提高了十倍。", "NO conversion was ten times higher."):
            item = self.one(text)
            self.assertEqual(item["kind"], "ambiguous")
            self.assertEqual(item["value"], 10.0)
            self.assertEqual(item["operator"], "unknown")
            self.assertFalse(item["numeric_eligible"])

    def test_percent_relative_and_percentage_points_differ(self):
        relative = self.one("NO conversion increased by 10%.")
        delta = self.one("NO conversion increased by 10 percentage points.")
        self.assertEqual((relative["operator"], relative["unit"]), ("ratio", "%"))
        self.assertEqual((delta["operator"], delta["unit"]), ("delta", "percentage points"))
        self.assertEqual(relative["value"], delta["value"])
        self.assertFalse(relative["numeric_eligible"] or delta["numeric_eligible"])

    def test_from_to_does_not_invent_sample_or_absolute_label(self):
        item = self.one("NO conversion increased from 20% to 30% at 300 °C.")
        self.assertEqual(item["kind"], "comparison")
        self.assertIsNone(item["value"])
        self.assertIsNone(item["value_high"])
        self.assertEqual(item["reference_sample"], "")
        self.assertIn("20% to 30%", item["evidence_quote"])

    def test_prospect_is_not_observation(self):
        for text in ("In this study, NO conversion may reach 99%.", "预计NO转化率达到99%。", "NO conversion is expected to be ten times as high as the control."):
            item = self.one(text)
            self.assertEqual(item["kind"], "outlook")
            self.assertIsNone(item["value"])
            self.assertFalse(item["numeric_eligible"])

    def test_negation_is_not_zero_or_improvement(self):
        for text in ("NO conversion did not increase by 10%.", "NO转化率没有提高。", "No improvement in N2 selectivity was observed."):
            item = self.one(text)
            self.assertEqual(item["kind"], "negated")
            self.assertIsNone(item["value"])
            self.assertFalse(item["numeric_eligible"])

    def test_explicit_current_measurement_is_still_unreviewed(self):
        item = self.one("In this study, sample S1 tested at 300 °C: NO conversion was 90%.")
        self.assertEqual(item["metric"], "NO conversion")
        self.assertEqual((item["value"], item["unit"], item["operator"]), (90.0, "%", "eq"))
        self.assertEqual(item["sample_label"], "S1")
        self.assertEqual(item["conditions"]["temperature"]["value"], 300.0)
        self.assertEqual(item["assertion_scope"], "current_study")
        self.assertEqual(item["review_status"], "unreviewed")
        self.assertTrue(item["numeric_eligible"])

    def test_review_citation_is_not_current_study(self):
        item = self.one("According to previous work, sample S1 at 300 °C: NO conversion was 90%.")
        self.assertEqual(item["assertion_scope"], "prior_work")
        self.assertFalse(item["numeric_eligible"])

    def test_unknown_scope_is_not_guessed(self):
        item = self.one("Sample S1 showed NO conversion of 90% at 300 °C.")
        self.assertEqual(item["assertion_scope"], "unknown")
        self.assertFalse(item["numeric_eligible"])

    def test_approximate_is_not_exact_point(self):
        item = self.one("In this study, sample S1 at 300 °C: NO conversion was about 90%.")
        self.assertEqual(item["value"], 90.0)
        self.assertEqual(item["operator"], "unknown")
        self.assertFalse(item["numeric_eligible"])

    def test_upper_and_lower_bounds_are_not_equalities(self):
        for bound, op in ((">", "gt"), ("≥", "ge"), ("less than", "lt"), ("up to", "le")):
            item = self.one(f"In this study, sample S1 at 300 °C: NO conversion was {bound} 90%.")
            self.assertEqual(item["operator"], op)
            self.assertFalse(item["numeric_eligible"])

    def test_explicit_range_stays_range(self):
        item = self.one("In this study, sample S1 tested at 300 °C: NO conversion was 90–95%.")
        self.assertEqual((item["kind"], item["operator"], item["value"], item["value_high"]),
                         ("range", "range", 90.0, 95.0))
        self.assertTrue(item["numeric_eligible"])

    def test_temperature_range_is_not_performance(self):
        item = self.one("The reaction was tested at 200–300°C.")
        self.assertEqual((item["value"], item["value_high"], item["unit"]), (200.0, 300.0, "°C"))
        self.assertEqual(item["metric"], "unknown")
        self.assertFalse(item["numeric_eligible"])

    def test_temperature_is_not_assigned_to_conversion(self):
        items = semantic_candidates("NO conversion at 300 °C was 90%.")
        self.assertFalse(any(item["value"] == 300 for item in items))
        # This explicit construction is now supported, retaining the original
        # regression's essential rule: 300 is a condition, not conversion.
        self.assertEqual([i['value'] for i in items], [90])
        self.assertEqual(items[0]['conditions']['temperature']['value'], 300)

    def test_preparation_temperature_is_not_test_condition(self):
        item = self.one("In this study, sample S1 was calcined at 300 °C: NO conversion was 90%.")
        self.assertEqual(item["value"], 90)
        self.assertEqual(item["conditions"], {})
        self.assertFalse(item["numeric_eligible"])

    def test_range_with_two_units_is_not_shortened_to_first_value(self):
        item = self.one("NO conversion was 90%–95%.")
        self.assertEqual((item["kind"], item["value"], item["value_high"]), ("range", 90.0, 95.0))

    def test_between_and_is_explicit_range_but_respectively_is_not(self):
        item = self.one("NO conversion ranged between 90 and 95%.")
        self.assertEqual((item["kind"], item["value"], item["value_high"]), ("range", 90.0, 95.0))
        for text in ("NO conversion was 90% and 95% for the two samples, respectively.",
                     "NO conversion was 90 and 95% for the two samples, respectively."):
            item = self.one(text)
            self.assertIsNone(item["value"])
            self.assertFalse(item["numeric_eligible"])

    def test_invalid_and_uncertain_numbers_cannot_be_exact(self):
        for value in ("1,20%", "90–1e999%", "1e999%", "1.2×10^3%"):
            item = self.one(f"In this study, sample S1 tested at 300 °C: NO conversion was {value}.")
            self.assertEqual(item["kind"], "ambiguous")
            self.assertIsNone(item["value"])
            self.assertFalse(item["numeric_eligible"])
        # Explicit uncertainty and well-formed thousands separators are now
        # retained, while the original no-exact-label safety remains.
        for value in ("90 ± 2%", "90% ± 2%", "90% (±2%)"):
            item = self.one(f"In this study, sample S1 tested at 300 °C: NO conversion was {value}.")
            self.assertEqual(item['value'], 90)
            self.assertEqual(item['uncertainty']['value'], 2)
            self.assertEqual(item['uncertainty']['type'], 'unspecified')
            self.assertEqual(item['operator'], 'unknown')
            self.assertFalse(item['numeric_eligible'])
        item = self.one('In this study, sample S1 tested at 300 °C: NO conversion was 1,200%.')
        self.assertEqual(item['value'],1200)
        self.assertFalse(item['numeric_eligible'])

    def test_implausible_percentage_is_retained_but_flagged(self):
        item = self.one("In this study, sample S1 tested at 300 °C: NO conversion was 190%.")
        self.assertEqual(item["value"], 190)
        self.assertFalse(item["numeric_eligible"])

    def test_author_citation_is_prior_work(self):
        item = self.one("Li et al. reported NO conversion of 90%.")
        self.assertEqual(item["assertion_scope"], "prior_work")
        self.assertFalse(item["numeric_eligible"])

    def test_wrong_explicit_unit_does_not_become_performance(self):
        item = self.one("NO conversion was 300 °C.")
        self.assertEqual(item["kind"], "ambiguous")
        self.assertIsNone(item["value"])
        self.assertFalse(item["numeric_eligible"])

    def test_missing_unit_is_not_guessed(self):
        item = self.one("In this study, sample S1 at 300 °C: NO conversion was 0.9.")
        self.assertEqual(item["value"], 0.9)
        self.assertEqual(item["unit"], "")
        self.assertFalse(item["numeric_eligible"])

    def test_multiple_metrics_use_local_values(self):
        items = semantic_candidates("NO conversion was 90% and N2 selectivity was 95% at 300 °C.")
        self.assertEqual([(i["metric"], i["value"]) for i in items],
                         [("NO conversion", 90.0), ("N2 selectivity", 95.0)])
        self.assertFalse(any(i["value"] == 300 for i in items))

    def test_multiple_metrics_do_not_receive_same_relative_claim(self):
        item = self.one("NO conversion and N2 selectivity improved by 10%.")
        self.assertEqual(item["metric"], "unknown")
        self.assertFalse(item["numeric_eligible"])

    def test_energy_negative_and_unicode_original_are_preserved(self):
        text = "第一句。\n  本研究样品S1在300℃，吸附能为−0.82 eV。 后续解释。"
        item = self.one(text)
        self.assertEqual(item["value"], -0.82)
        self.assertEqual(item["unit"], "eV")
        self.assertEqual(item["evidence_quote"], text[item["start"]:item["end"]])
        self.assertIn("−", item["evidence_quote"])

    def test_decimal_offsets_across_sentences(self):
        text = "  NO conversion was 90.5%.\nN2 selectivity was 99.1%. "
        items = semantic_candidates(text)
        self.assertEqual(len(items), 2)
        self.assertEqual([x["value"] for x in items], [90.5, 99.1])
        for item in items:
            self.assertEqual(item["evidence_quote"], text[item["start"]:item["end"]])

    def test_surface_area_unit_is_not_percent(self):
        item = self.one("BET surface area was 120 m²/g.")
        self.assertEqual((item["value"], item["unit"]), (120.0, "m²/g"))

    def test_qualitative_claim_has_no_fabricated_number(self):
        item = self.one("The catalyst had excellent N2 selectivity.")
        self.assertEqual(item["kind"], "qualitative")
        self.assertIsNone(item["value"])

    def test_length_limits_are_explicit_and_no_offset_rewriting(self):
        with self.assertRaises(ValueError):
            semantic_candidates("a" * (MAX_TEXT_LENGTH + 1))
        items = semantic_candidates("x" * 1300 + " NO conversion was 80%.")
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]["numeric_eligible"])
        self.assertLessEqual(len(items[0]["evidence_quote"]), 1200)

    def test_no_meaningless_candidates_for_empty_or_unrelated_text(self):
        self.assertEqual(semantic_candidates(""), [])
        self.assertEqual(semantic_candidates("Experimental methods and catalyst preparation."), [])

    def test_material_identity_and_explicit_composition_are_retained(self):
        item = self.one('Cu/FA catalyst showed NO conversion of 90% at 300 °C.')
        self.assertEqual(item['sample_label'], 'Cu/FA')
        item = self.one('FeOx/SAPO-34 catalyst with 3% Fe loading maintained more than 90% NO conversion at 350 °C.')
        self.assertEqual(item['sample_label'], 'FeOx/SAPO-34 catalyst with 3% Fe loading')
        self.assertEqual((item['value'],item['operator']),(90,'gt'))

    def test_letter_only_sample_names_are_explicit(self):
        items = semantic_candidates('In this study, samples A and B tested at 300 °C showed NO conversion of 20 and 40%, respectively.')
        self.assertEqual([(i['sample_label'],i['value']) for i in items], [('A',20),('B',40)])
        self.assertTrue(all(i['review_status']=='unreviewed' for i in items))

    def test_sample_respectively_has_distinct_stable_ids(self):
        text = 'In this study, samples S1 and S2 tested at 300 °C showed NO conversion of 20%, 40%, respectively.'
        items = semantic_candidates(text)
        self.assertEqual([(i['sample_label'],i['value']) for i in items],[('S1',20),('S2',40)])
        self.assertEqual(len(set(i['semantic_id_key'] for i in items)),2)
        self.assertEqual([i['semantic_id_key'] for i in items], [i['semantic_id_key'] for i in semantic_candidates(text)])

    def test_values_before_sample_list_are_paired_only_with_respectively(self):
        text = 'NO conversion was 20 and 40% for samples S1 and S2, respectively.'
        self.assertEqual([(i['sample_label'],i['value']) for i in semantic_candidates(text)],[('S1',20),('S2',40)])
        item = self.one(text.replace(', respectively',''))
        self.assertIsNone(item['value'])
        self.assertFalse(item['numeric_eligible'])

    def test_metric_respectively_is_not_a_shared_single_value(self):
        items = semantic_candidates('NO conversion and N2 selectivity were 90% and 95%, respectively.')
        self.assertEqual([(i['metric'],i['value']) for i in items],[('NO conversion',90),('N2 selectivity',95)])

    def test_respectively_count_mismatch_and_grid_are_unresolved(self):
        for text in ('Samples S1, S2 and S3 showed NO conversion of 20 and 40%, respectively.',
                     'Samples S1 and S2 showed NO conversion and N2 selectivity of 20 and 40%, respectively.'):
            item = self.one(text)
            self.assertEqual(item['kind'],'ambiguous')
            self.assertIsNone(item['value'])
            self.assertFalse(item['numeric_eligible'])

    def test_negated_and_modal_respectively_are_never_observations(self):
        for predicate,kind in [('did not achieve','negated'),('may achieve','outlook')]:
            items = semantic_candidates(f'In this study, samples S1 and S2 tested at 300 °C {predicate} NO conversion of 90% and 95%, respectively.')
            self.assertEqual(len(items),2)
            self.assertTrue(all(i['kind']==kind and i['value'] is None and not i['numeric_eligible'] for i in items))
            self.assertTrue(all(r.get('assertion') for i in items for r in i['relations']))

    def test_shared_relative_claim_obeys_negation(self):
        item = self.one('NO conversion and N2 selectivity did not improve by 10%.')
        self.assertEqual((item['kind'],item['metric'],item['value']),('negated','unknown',None))

    def test_negation_has_local_metric_scope(self):
        items = semantic_candidates('Sample S1 showed NO conversion of 90%, but N2 selectivity did not improve.')
        self.assertEqual([(i['metric'],i['kind'],i['value']) for i in items],[('NO conversion','absolute',90),('N2 selectivity','negated',None)])

    def test_comparison_baseline_is_explicit_not_invented(self):
        item = self.one('NO conversion of catalyst S1 was two times as high as catalyst S2.')
        self.assertEqual((item['sample_label'],item['reference_sample']),('S1','S2'))
        self.assertEqual((item['operator'],item['value']),('ratio',2))
        self.assertFalse(item['numeric_eligible'])

    def test_from_to_retains_both_endpoint_bindings(self):
        item = self.one('NO conversion increased from 20% for S1 to 30% for S2.')
        relation = next(r for r in item['relations'] if r['type']=='change')
        self.assertEqual((relation['source']['value'],relation['target']['value']),(20,30))
        self.assertEqual((item['reference_sample'],item['sample_label']),('S1','S2'))
        self.assertIsNone(item['value'])

    def test_loading_change_is_not_conversion_change(self):
        item = self.one('Increasing Cu content from 1 to 10% increased NO conversion from 20 to 90%.')
        relation = next(r for r in item['relations'] if r['type']=='change')
        self.assertEqual((relation['source']['value'],relation['target']['value']),(20,90))
        self.assertEqual(item['metric'],'NO conversion')

    def test_independent_samples_and_conditions_are_local(self):
        items = semantic_candidates('In this study, sample S1 tested at 300 °C: NO conversion was 90%, while sample S2 tested at 400 °C: NO conversion was 40%.')
        self.assertEqual([(i['sample_label'],i['value'],i['conditions']['temperature']['value']) for i in items],[('S1',90,300),('S2',40,400)])

    def test_preparation_and_test_temperature_do_not_conflict(self):
        item = self.one('In this study, sample S1 was calcined at 500 °C and tested at 300 °C: NO conversion was 90%.')
        self.assertEqual(item['conditions']['temperature']['value'],300)

    def test_preparation_temperature_after_performance_is_not_test(self):
        item = self.one('NO conversion was 90% for sample S1 calcined at 500 °C.')
        self.assertEqual(item['conditions'],{})

    def test_two_temperatures_without_pairing_do_not_select_first(self):
        item = self.one('NO conversion was 90% at 300 °C and at 400 °C.')
        self.assertEqual(item['conditions'],{})
        self.assertFalse(item['numeric_eligible'])

    def test_approximate_temperature_is_not_exact_condition(self):
        item = self.one('In this study, sample S1 showed NO conversion of 90% at about 300 °C.')
        self.assertEqual(item['conditions']['temperature']['operator'],'unknown')
        self.assertFalse(item['numeric_eligible'])

    def test_bound_temperature_preserves_operator(self):
        item = self.one('NO conversion was 90% at temperatures below 300 °C.')
        self.assertEqual(item['conditions']['temperature']['operator'],'lt')

    def test_causal_hypothesis_does_not_erase_measured_value(self):
        item = self.one('NO conversion was 90%, which may be due to the surface sites.')
        self.assertEqual((item['kind'],item['value']),('absolute',90))

    def test_not_less_than_is_a_bound_not_a_denied_observation(self):
        for text,operator in [('NO conversion was not less than 90%.','ge'),('NO conversion was no more than 90%.','le')]:
            item = self.one(text)
            self.assertEqual((item['value'],item['operator']),(90,operator))
            self.assertFalse(item['numeric_eligible'])

    def test_literature_clause_does_not_steal_later_current_sample(self):
        items = semantic_candidates('According to Li et al., NO conversion was 80%, whereas in this study sample S1 gave NO conversion of 90%.')
        self.assertEqual([(i['value'],i['assertion_scope'],i['sample_label']) for i in items],[(80,'prior_work',''),(90,'current_study','S1')])

    def test_bracket_citation_does_not_confirm_current_study(self):
        item = self.one('Sample S1 gave NO conversion of 90% [12].')
        self.assertEqual(item['assertion_scope'],'unknown')
        self.assertFalse(item['numeric_eligible'])

    def test_cross_sentence_context_is_never_promoted_to_binding(self):
        text = 'In this study, sample S1 was tested at 300 °C. Its NO conversion was 90%.'
        item = self.one(text)
        self.assertEqual((item['sample_label'],item['assertion_scope'],item['conditions']),('','unknown',{}))
        self.assertTrue(item['context_evidence'])
        self.assertFalse(item['numeric_eligible'])

    def test_bibliography_title_is_not_an_outlook_claim(self):
        self.assertEqual(semantic_candidates('References\n[1] Li et al. NO conversion: a future look. Fuel 2000;12:20.'),[])
        self.assertEqual(semantic_candidates('Coal fly ash: a retrospective and future look.'),[])

    def test_nh3_energy_is_specific_only_when_explicit(self):
        for prefix in ('NH3 adsorption energy','ammonia adsorption energy','adsorption energy of NH3','adsorption energy of ammonia','氨吸附能'):
            text = prefix + ('为−0.82 eV。' if prefix=='氨吸附能' else ' was −0.82 eV.')
            item = self.one(text)
            self.assertEqual(item['metric'],'NH3 adsorption energy')
            self.assertEqual(item['sample_label'],'')
        item = self.one('The adsorption energy was −0.82 eV.')
        self.assertEqual(item['metric'],'adsorption energy')

    def test_co_selectivity_is_distinct_from_co_conversion(self):
        items = semantic_candidates('CO selectivity was 20% and CO conversion was 30%.')
        self.assertEqual([(i['metric'],i['value']) for i in items],[('CO selectivity',20),('CO conversion',30)])

    def test_valid_thousands_separator_for_surface_area(self):
        item = self.one('BET surface area was 1,200 m²/g.')
        self.assertEqual(item['value'],1200)

    def test_mixed_range_units_are_not_silently_converted(self):
        item = self.one('Adsorption energy was −80 kJ/mol to −0.5 eV.')
        self.assertEqual(item['kind'],'ambiguous')
        self.assertIsNone(item['value'])

    def test_reverse_range_is_retained_and_flagged(self):
        item = self.one('In this study, sample S1 tested at 300 °C: NO conversion was 95–90%.')
        self.assertEqual((item['value'],item['value_high']),(95,90))
        self.assertFalse(item['numeric_eligible'])

    def test_source_alignment_survives_ligature_newline_and_dehyphenation(self):
        text = '前文。\n FeO x/SAPO-34-3 maintains more than 90% NO conver-\nsion efﬁciency at 350 ◦C.'
        item = self.one(text)
        self.assertEqual((item['metric'],item['value'],item['operator']),('NO conversion',90,'gt'))
        self.assertEqual(item['sample_label'],'FeOx/SAPO-34-3')
        self.assertEqual(item['evidence_quote'],text[item['start']:item['end']])
        for field in (item['relations'],item['context_evidence'],list(item['conditions'].values())):
            for evidence in field:
                self.assertEqual(evidence['evidence_quote'],text[evidence['start']:evidence['end']])

    def test_malformed_temperature_ocr_is_not_repaired_as_fact(self):
        item = self.one('NO conversion was about 95% for temperatures less than 350 8C.')
        self.assertEqual(item['conditions'],{})
        self.assertEqual(item['value'],95)
        self.assertTrue(any('8C' in w for w in item['warnings']))

    def test_heading_number_is_not_a_measurement(self):
        self.assertEqual(semantic_candidates('Keywords: NO conversion\n1. Introduction'),[])

    def test_ramp_rate_exponent_is_not_temperature_range(self):
        self.assertEqual(semantic_candidates('The samples were heated during the measuring process at a ramping rate of 10 °C min−1 to 900 °C.'),[])

    def test_excluded_catalyst_is_not_bound_as_tested_sample(self):
        item = self.one('In this study, NO conversion was 90% at 300 °C without catalyst S1.')
        self.assertEqual(item['sample_label'],'')
        self.assertFalse(item['numeric_eligible'])

    def test_material_family_does_not_identify_one_preparation(self):
        item = self.one('A series of FeOx/SAPO-34 catalysts with different loadings showed NO conversion of 90%.')
        self.assertEqual(item['sample_label'],'')

    def test_shared_absolute_expression_does_not_select_last_metric(self):
        item = self.one('NO conversion and N2 selectivity were 90%.')
        self.assertEqual((item['kind'],item['metric'],item['value']),('ambiguous','unknown',None))

    def test_celsius_and_kelvin_sentence_end_do_not_merge_measurements(self):
        for unit in ('°C','K'):
            item = self.one(f'In this study, sample S1 was tested at 300 {unit}. Its NO conversion was 90%.')
            self.assertEqual((item['sample_label'],item['assertion_scope'],item['conditions']),('','unknown',{}))
            self.assertTrue(item['context_evidence'])

    def test_prior_named_material_catalyst_is_recognized(self):
        item = self.one('Li et al. reported that Fe-ZSM-5 catalyst maintained more than 80% NO conversion.')
        self.assertEqual((item['sample_label'],item['assertion_scope']),('Fe-ZSM-5','prior_work'))

    def test_ramping_temperature_is_not_a_test_condition(self):
        item = self.one('NO conversion was 90% during heating at 10 °C min−1.')
        self.assertEqual(item['conditions'],{})

    def test_percent_higher_lower_is_relative_not_absolute_target(self):
        for adjective,direction in [('higher','increase'),('lower','decrease')]:
            item=self.one(f'In this study, sample S1 tested at 300 °C showed NO conversion of 20% {adjective} than the control.')
            self.assertEqual((item['kind'],item['operator'],item['value']),('comparison','ratio',20))
            self.assertFalse(item['numeric_eligible'])
            self.assertEqual(item['relations'][-1]['direction'],direction)
            self.assertEqual(item['relations'][-1]['ratio_definition'],'relative_change_percent')
            self.assertEqual(item['relations'][-1]['reference_description'],'the control')

    def test_written_percent_and_relative_direction_are_preserved(self):
        for phrase,direction in [('increased by','increase'),('decreased by','decrease'),('higher by','increase'),('lower by','decrease')]:
            item=self.one(f'NO conversion {phrase} 10 percent.')
            self.assertEqual((item['kind'],item['value'],item['unit']),('comparison',10,'%'))
            self.assertEqual(item['relations'][-1]['direction'],direction)

    def test_percentage_point_direction_and_reference_remain_separate(self):
        item=self.one('NO conversion was 5 percentage points lower than for sample S2.')
        self.assertEqual((item['reference_sample'],item['sample_label']),('S2',''))
        self.assertEqual(item['relations'][-1]['direction'],'decrease')
        self.assertEqual((item['value'],item['operator']),(5,'delta'))

    def test_only_reference_sample_is_not_promoted_to_subject(self):
        item=self.one('NO conversion was twice as high as catalyst S2.'.replace('twice','two times'))
        self.assertEqual((item['sample_label'],item['reference_sample']),('','S2'))
        self.assertFalse(item['numeric_eligible'])

    def test_chinese_embedded_reference_is_preserved(self):
        item=self.one('本研究样品S1在300℃测试，NO转化率是样品S2的十倍。')
        self.assertEqual((item['sample_label'],item['reference_sample']),('S1','S2'))
        self.assertEqual(item['relations'][-1]['ratio_definition'],'target_divided_by_reference')
        self.assertFalse(item['numeric_eligible'])

    def test_factor_reduction_defines_denominator_without_computing_target(self):
        item=self.one('NO conversion was reduced by a factor of ten compared with sample S2.')
        self.assertEqual(item['relations'][-1]['ratio_definition'],'reference_divided_by_target')
        self.assertEqual(item['relations'][-1]['direction'],'decrease')
        self.assertEqual(item['reference_sample'],'S2')
        self.assertFalse(item['numeric_eligible'])

    def test_fold_range_does_not_turn_upper_endpoint_into_negative_number(self):
        for dash in ('–','-',' to '):
            text=f'NO conversion was 2{dash}3 times higher than catalyst S2.'
            item=self.one(text)
            relation=item['relations'][-1]
            self.assertEqual((relation['value'],relation['value_high']),(2,3))
            self.assertEqual(item['kind'],'ambiguous')
            self.assertIsNone(item['value'])
            self.assertFalse(item['numeric_eligible'])
            self.assertEqual(text[relation['start']:relation['end']],relation['evidence_quote'])

    def test_plus_minus_percentage_points_is_uncertainty_not_relative_change(self):
        item=self.one('NO conversion was 90% ± 2 percentage points.')
        self.assertEqual((item['value'],item['operator']),(90,'unknown'))
        self.assertEqual((item['uncertainty']['value'],item['uncertainty']['unit']),(2,'percentage points'))
        self.assertFalse(any(r['type']=='relative_change' for r in item['relations']))
        self.assertFalse(item['numeric_eligible'])

    def test_temperature_list_with_elided_anchor_never_selects_first(self):
        for temperatures in ('300 °C and 400 °C','300 and 400 °C','300 °C or 400 °C','300, 400 °C'):
            item=self.one(f'In this study, sample S1 showed NO conversion of 90% at {temperatures}.')
            self.assertEqual(item['conditions'],{})
            self.assertFalse(item['numeric_eligible'])

    def test_multi_axis_respectively_does_not_copy_temperature_to_all_samples(self):
        text='In this study, samples S1 and S2 showed NO conversion of 90% and 95% at 300 °C and 400 °C, respectively.'
        items=semantic_candidates(text)
        self.assertEqual([(i['sample_label'],i['value']) for i in items],[('S1',90),('S2',95)])
        self.assertTrue(all(not i['conditions'] and not i['numeric_eligible'] for i in items))

    def test_predicted_simulated_conversion_is_not_an_experimental_label(self):
        for kind in ('predicted','simulated','model-estimated'):
            item=self.one(f'In this study, for sample S1 tested at 300 °C, the {kind} NO conversion was 90%.')
            self.assertEqual(item['kind'],'ambiguous')
            self.assertIsNone(item['value'])
            self.assertFalse(item['numeric_eligible'])
            relation=next(r for r in item['relations'] if r['type']=='model_output')
            self.assertEqual(relation['value'],90)
            self.assertEqual(item['review_status'],'unreviewed')

    def test_negated_reduction_is_not_measured_zero_or_observed_reduction(self):
        item=self.one('Consequently, the adsorption capacity is not significantly reduced or eliminated.')
        self.assertEqual(item['kind'],'negated')
        self.assertIsNone(item['value'])
        self.assertFalse(item['numeric_eligible'])

    def test_did_not_exceed_and_did_not_fall_below_are_bounds(self):
        for phrase,operator in [('did not exceed','le'),('does not exceed','le'),('did not fall below','ge')]:
            item=self.one(f'NO conversion {phrase} 50%.')
            self.assertEqual((item['value'],item['operator']),(50,operator))
            self.assertFalse(item['numeric_eligible'])

    def test_letter_sample_sentence_end_does_not_inherit_identity_or_scope(self):
        text='In this study we measured sample A. Its NO conversion was 90% at 300 °C.'
        item=self.one(text)
        self.assertEqual((item['sample_label'],item['assertion_scope']),('','unknown'))
        self.assertTrue(item['context_evidence'])
        self.assertFalse(item['numeric_eligible'])
        self.assertTrue(item['evidence_quote'].startswith('Its '))

    def test_parenthetic_change_endpoints_retain_independent_samples(self):
        item=self.one('Total pore volume decreases from 0.301 cm3/g (SAPO-34) to 0.292 cm3/g (FeOx/SAPO-34).')
        relation=item['relations'][-1]
        self.assertEqual((relation['source']['value'],relation['target']['value']),(0.301,0.292))
        self.assertEqual((item['reference_sample'],item['sample_label']),('SAPO-34','FeOx/SAPO-34'))
        self.assertEqual(relation['direction'],'decrease')
        self.assertIsNone(item['value'])

    def test_surface_area_literal_cm2_unit_is_not_silently_replaced(self):
        item=self.one('BET surface area of FeOx/SAPO-34-3 decreases from 541.78 cm2/g (SAPO-34) to 474.78 cm2/g (FeOx/SAPO-34).')
        relation=item['relations'][-1]
        self.assertEqual((relation['source']['unit'],relation['target']['unit']),('cm2/g','cm2/g'))
        self.assertEqual((relation['source']['value'],relation['target']['value']),(541.78,474.78))
        self.assertFalse(item['numeric_eligible'])

    def test_new_relations_preserve_all_original_evidence_offsets(self):
        texts=['前文。\nNO conversion was 2–3 times higher than catalyst S2.',
               '本研究样品S1的NO转化率是样品S2的十倍。',
               'BET surface area of FeOx/SAPO‐34‐3 decreases from 541.78 cm2/g (SAPO‐34) to 474.78 cm2/g (FeOx/SAPO‐34).',
               'In this study, sample S1 tested at 300 ◦C: the predicted NO conver-\nsion was 90%.']
        def check(value,text):
            if isinstance(value,dict):
                if 'evidence_quote' in value:
                    self.assertEqual(value['evidence_quote'],text[value['start']:value['end']])
                for nested in value.values(): check(nested,text)
            elif isinstance(value,list):
                for nested in value: check(nested,text)
        for text in texts:
            items=semantic_candidates(text)
            self.assertTrue(items)
            self.assertTrue(all(i['review_status']=='unreviewed' for i in items))
            check(items,text)

    def test_two_metric_changes_do_not_merge_into_shared_comparison(self):
        text='BET surface area of FeOx/SAPO-34-3 decreases from 541.78 cm2/g (SAPO-34) to 474.78 cm2/g (FeOx/SAPO-\n34), total pore volume decreases from 0.301 cm3/g (SAPO-34) to 0.292 cm3/g (FeOx/SAPO-\n34).'
        items=semantic_candidates(text)
        self.assertEqual([i['metric'] for i in items],['BET surface area','pore volume'])
        self.assertEqual([i['relations'][-1]['source']['value'] for i in items],[541.78,0.301])
        self.assertTrue(all(i['relations'][-1]['target']['sample_label']=='FeOx/SAPO-34' for i in items))
        self.assertTrue(all(i['sample_label']=='FeOx/SAPO-34' for i in items))
        for item in items:
            relation=item['relations'][-1]
            self.assertEqual(relation['evidence_quote'],text[relation['start']:relation['end']])

    def test_incomplete_parenthesis_never_truncates_target_sample(self):
        item=self.one('Pore volume decreases from 0.3 cm3/g (SAPO-34) to 0.2 cm3/g (FeOx/SAPO-\n34')
        self.assertEqual(item['relations'][-1]['target']['sample_label'],'')


if __name__ == "__main__":
    unittest.main()
