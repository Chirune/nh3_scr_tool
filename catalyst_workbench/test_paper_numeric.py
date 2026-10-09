"""Quantity inventory regressions: published excerpts + synthetic counterexamples.

This suite checks extraction behavior, not literature-wide recall or accuracy.
Published source excerpts are explicitly identified; other cases are synthetic.
"""
import unittest

from paper_numeric import numeric_facts
from paper_semantics import MAX_TEXT_LENGTH, semantic_candidates


CATAL_TABLE = '''Table 1. Engine operation conditions during test run.
Engine Parameter Setpoint Value
Engine speed 1300 rpm
Engine torque 50 Nm
NOx concentration at engine outlet 200 ppm
Exhaust mass ﬂow rate 70 kg/h
Exhaust gas temperature at SCR inlet 230 ◦C
'''
BET_TABLE = '''Table 3
Textural properties of different ﬂy ash samples
Sample BET speciﬁc
surface area (m2/g)
Total pore
volume (cm3/g)
Original ﬂy ash 7.92 0.196
Molded ﬂy ash (0.71–1 mm) 55.49 0.580
Fly ash-X70-G300 65.62 0.840
Fly ash-X70-G300 65.62 0.840
Fe/FA-X70-G300-5%-D350 49.24 0.742
FA, ﬂy ash; 5%, loading of Fe; D, calcination temperature.
'''


class NumericFactTests(unittest.TestCase):
    def parse(self, text):
        result = numeric_facts(text)
        for item in result:
            self.assertEqual(item['evidence_quote'], text[item['start']:item['end']])
            self.assertEqual(item['review_status'], 'unreviewed')
            self.assertFalse(item['numeric_eligible'])
            self.assertEqual(item['conditions'], {})
            self.assertIn(item['quantity_role'], ('condition', 'preparation', 'characterization', 'other_result', 'measurement', 'background'))
            for key in ('duplicate_evidence', 'context_evidence'):
                for ev in item.get(key, []):
                    self.assertEqual(ev['evidence_quote'], text[ev['start']:ev['end']])
            for key in ('table_header_evidence', 'uncertainty'):
                ev = item.get(key)
                if ev:
                    self.assertEqual(ev['evidence_quote'], text[ev['start']:ev['end']])
        return result

    def test_real_catal_table_five_conditions(self):
        """DOI 10.3390/catal8060231, Table 1, page 4."""
        rows = self.parse(CATAL_TABLE)
        self.assertEqual(len(rows), 5)
        self.assertEqual([x['value'] for x in rows], [1300, 50, 200, 70, 230])
        self.assertTrue(all(x['kind'] == 'table_value' and x['source_table'] == 'Table 1' for x in rows))
        self.assertTrue(all(not x['sample_label'] for x in rows))
        self.assertEqual(rows[-1]['field_key'], 'temperature_C')

    def test_engine_out_nox_does_not_become_inlet_no(self):
        row = self.parse(CATAL_TABLE)[2]
        self.assertEqual(row['metric'], 'engine-out NOx concentration')
        self.assertEqual(row['field_key'], '')

    def test_mass_flow_does_not_become_volume_flow(self):
        row = self.parse(CATAL_TABLE)[3]
        self.assertEqual((row['unit'], row['field_key']), ('kg/h', ''))

    def test_repeated_pdf_layer_deduplicated_with_evidence(self):
        rows = self.parse(CATAL_TABLE + '\n' + CATAL_TABLE.replace('ﬂ', 'fl').replace('◦', '°'))
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(len(x['duplicate_evidence']) == 1 for x in rows))

    def test_real_ui_three_values_not_conversion(self):
        """Published excerpt, catal8060231 page 6."""
        text = ('The uniformity index value for the SCR system measured was equal to 0.934. '
                'The measurements were repeated in order to establish its repeatability. '
                'The uniformity index obtained from the second\nrun was equal to 0.926, '
                'giving a UI mean value of 0.929± 0.05.')
        rows = self.parse(text)
        self.assertEqual([x['value'] for x in rows], [0.934, 0.926, 0.929])
        self.assertTrue(all(x['metric'] == 'NH3 uniformity index' and x['quantity_role'] == 'other_result' for x in rows))
        self.assertEqual(rows[-1]['uncertainty']['value'], 0.05)
        self.assertEqual(rows[-1]['source_role'], 'reported_summary')

    def test_ui_formula_one_is_not_observation(self):
        self.assertEqual(self.parse('The uniformity index (UI) is calculated according to the formula:\nUI = 1− ∑i x/y (7).'), [])

    def test_ui_relative_times_is_not_absolute(self):
        self.assertEqual(self.parse('The uniformity index value was 10 times higher than the control.'), [])

    def test_ui_modal_is_background(self):
        row = self.parse('The predicted uniformity index value was 0.95.')[0]
        self.assertEqual(row['quantity_role'], 'background')

    def test_real_alpha_mean_is_calculated_result(self):
        row = self.parse('In this particular case, the mean calculated α value was 0.937.')[0]
        self.assertEqual((row['value'], row['source_role']), (0.937, 'author_calculated_result'))

    def test_real_inlet_ratio_not_nitrogen_conversion(self):
        row = self.parse('For the given test conditions, the NO2/NOx ratio at the\nSCR inlet was 67%.')[0]
        self.assertEqual((row['value'], row['unit'], row['quantity_role']), (67, '%', 'condition'))
        self.assertEqual(row['field_key'], '')

    def test_ratio_threshold_is_not_actual_condition(self):
        self.assertEqual(self.parse('When the NO2/NOx ratio exceeds 50%, reaction (3) becomes operative.'), [])

    def test_real_local_output_bound_preserved(self):
        row = self.parse('At the locations with excess NH3, the NOx emissions downstream of the SCR are 10 ppm or lower.')[0]
        self.assertEqual((row['operator'], row['value'], row['quantity_role']), ('le', 10, 'measurement'))
        self.assertEqual(row['field_key'], '')

    def test_relative_percent_not_concentration(self):
        self.assertEqual(self.parse('The average NOx concentration is 3% lower than the raw engine out emission.'), [])

    def test_ticks_figures_and_equations_are_not_values(self):
        self.assertEqual(self.parse('Figure 3. NOx concentration [ppm]\n-100 -80 -60 0 20 40 60\nY_POS\n0\n10\n20\nNH3in = NOxin−NOxout+NH3out (6)'), [])

    def test_real_bet_table_is_two_fields_per_named_row(self):
        """Fuel DOI 10.1016/s0016-2361(02)00321-6, Table 3, page 3."""
        rows = self.parse(BET_TABLE)
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(x['source_table'] == 'Table 3' and x['quantity_role'] == 'characterization' for x in rows))
        pairs = {(x['sample_label'], x['metric']): x['value'] for x in rows}
        self.assertEqual(pairs['Original fly ash', 'BET surface area'], 7.92)
        self.assertEqual(pairs['Fe/FA-X70-G300-5%-D350', 'pore volume'], 0.742)

    def test_table_preserves_full_row_and_header(self):
        row = self.parse(BET_TABLE)[0]
        self.assertIn('Original ﬂy ash 7.92 0.196', row['evidence_quote'])
        self.assertIn('surface area (m2/g)', row['table_header_evidence']['evidence_quote'])
        self.assertEqual(row['suggested_feature_key'], 'BET_surface_area_m2_g')

    def test_sample_code_numbers_not_preparation_facts(self):
        rows = self.parse(BET_TABLE)
        self.assertFalse(any(x['value'] in (70, 300, 350, 5) for x in rows))

    def test_table_units_not_invented_without_header(self):
        self.assertEqual(self.parse('Original fly ash 7.92 0.196\nFe/FA-X70-G300-5%-D350 49.24 0.742'), [])

    def test_reversed_or_ambiguous_table_header_not_guessed(self):
        text = BET_TABLE.replace('Sample BET speciﬁc\nsurface area (m2/g)\nTotal pore\nvolume (cm3/g)',
                                 'Sample Total pore volume (cm3/g) BET surface area (m2/g)')
        self.assertEqual(self.parse(text), [])

    def test_premix_does_not_become_final_feed(self):
        text = ('The gas mixture normally consisted of NH 3 (900 ppm, 400 ml/min, N 2 as the gas\ncarrier), '
                'NO (900 ppm, 40 ml/min, N2 as the gas carrier) and O2 (10 ml/min). '
                'The total mixture ﬂow gas was 810 ml/min.')
        rows = self.parse(text)
        self.assertEqual(len(rows), 6)
        ppm = [x for x in rows if x['unit'] == 'ppm']
        self.assertTrue(all(x['value'] == 900 and not x['field_key'] for x in ppm))
        total = [x for x in rows if x['metric'] == 'total gas flow rate'][0]
        self.assertEqual(total['field_key'], 'flow_rate_ml_min')
        self.assertEqual(total['value'], 810)
        self.assertTrue(any('不一致' in w for w in total['warnings']))

    def test_explicit_inlet_no_concentration_has_field(self):
        row = self.parse('The inlet NO concentration\nwas kept in a ﬁxed value, namely 450 ppm.')[0]
        self.assertEqual((row['value'], row['field_key']), (450, 'feed_NO_ppm'))

    def test_liquid_urea_not_gas_flow(self):
        row = self.parse('The chosen α value corresponded to an UWS ﬂow rate of 12.4 mg/s.')[0]
        self.assertEqual((row['unit'], row['field_key']), ('mg/s', ''))

    def test_catalyst_volume_not_mass(self):
        row = self.parse('For each experiment, 9.42 ml of catalyst was placed into the isothermal zone.')[0]
        self.assertEqual((row['value'], row['unit'], row['field_key']), (9.42, 'mL', ''))

    def test_damaged_preparation_temperature_not_reaction_condition(self):
        rows = self.parse('The fly ash was dried at\ntemperatures of 200, 300 and 400 8C for 3 h.')
        self.assertEqual(len(rows), 4)
        self.assertEqual(sorted(x['value'] for x in rows), [3, 200, 300, 400])
        self.assertTrue(all(x['quantity_role'] == 'preparation' and not x['field_key'] for x in rows))
        self.assertTrue(all(not x.get('suggested_feature_key') for x in rows if x['unit'] == '8C'))

    def test_explicit_calcination_feature_not_reaction_condition(self):
        row = self.parse('The samples were calcined at 500 °C for 3 h.')[0]
        self.assertEqual(row['quantity_role'], 'preparation')
        self.assertEqual(row['field_key'], '')
        self.assertIn('calcination', row['metric'])

    def test_acid_treatment_preserves_compound_evidence(self):
        text = ('The ﬂy ash was treated with concentrated nitric acid\n'
                '(acid/ash ¼ 5 ml/g) at a temperature of 70 8C for 1 h to\nobtain activated catalyst support.')
        rows = self.parse(text)
        self.assertEqual({(r['metric'], r['value']) for r in rows},
                         {('acid/ash liquid-solid ratio', 5), ('acid treatment temperature', 70), ('acid treatment duration', 1)})

    def test_preparation_pressure_not_reaction_pressure(self):
        row = self.parse('The bricks were dried with high pressure steam (90 atm.).')[0]
        self.assertEqual((row['quantity_role'], row['field_key']), ('preparation', ''))

    def test_approximate_preserved(self):
        row = self.parse('The bricks were put in a steam chest for steam activation at about 900 8C.')[0]
        self.assertEqual(row['operator'], 'approx')

    def test_no_cross_sentence_sample_binding(self):
        rows = self.parse('Sample S1 was prepared. The total gas flow rate was 200 mL/min.')
        self.assertEqual(rows[0]['sample_label'], '')

    def test_references_ignored(self):
        self.assertEqual(self.parse('References\nThe total gas flow rate was 200 mL/min.'), [])

    def test_input_contract(self):
        with self.assertRaises(TypeError):
            numeric_facts(None)
        with self.assertRaises(ValueError):
            numeric_facts('a' * (MAX_TEXT_LENGTH + 1))

    def test_real_requirement_not_experimental_value(self):
        text = ('The test method was elaborated to support Euro 6 emission-compliant SCR applications, '
                'from which NO x conversion efﬁciency above 95% is demanded, maintaining '
                'at the same time low ammonia slip and UWS consumption.')
        rows = semantic_candidates(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'outlook')
        self.assertIsNone(rows[0]['value'])
        self.assertEqual(rows[0]['quantity_role'], 'background')
        self.assertEqual(rows[0]['relations'][0]['value'], 95)
        inventory = self.parse(text)
        self.assertEqual(len(inventory), 1)
        self.assertEqual((inventory[0]['value'], inventory[0]['operator'], inventory[0]['quantity_role']),
                         (95, 'gt', 'background'))

    def test_actual_conversion_is_not_reclassified(self):
        rows = semantic_candidates('In this study, sample S1 tested at 300 °C: NO conversion was 95%.')
        self.assertEqual((rows[0]['kind'], rows[0]['value']), ('absolute', 95))

    def test_generic_98_percent_not_in_numeric_inventory(self):
        text = 'The SCR system working at the rated operating conditions allows the NOx emission to be decreased by more than 98%.'
        self.assertEqual(self.parse(text), [])


if __name__ == '__main__':
    unittest.main()
