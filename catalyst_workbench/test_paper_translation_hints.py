"""Source-triggered reading checks, not translation-accuracy measurements."""
import unittest

from paper_translation_hints import reading_hints


class ReadingHintTests(unittest.TestCase):
    def joined(self, text, translated=''):
        return '\n'.join(reading_hints(text, translated))

    def test_empty_invalid_and_plain_sentence(self):
        for value in ('', '  ', None, 17, [], {}):
            self.assertEqual(reading_hints(value), [])
        self.assertEqual(reading_hints('The catalyst was prepared by impregnation.'), [])

    def test_may_is_not_asserted_as_fact(self):
        hint = self.joined('The activity may increase.')
        self.assertIn('不确定性', hint)
        self.assertIn('不等于', hint)

    def test_month_is_not_uncertainty(self):
        self.assertEqual(reading_hints('The measurements were performed on May 3, 2025.'), [])
        self.assertEqual(reading_hints('Samples were collected in May 2025.'), [])

    def test_future_and_uncertain_merge(self):
        hints = reading_hints('This may work and will be tested; an improvement is expected.')
        self.assertEqual(len(hints), 1)
        self.assertIn('可能性与预期', hints[0])

    def test_future_only(self):
        self.assertIn('已完成实验', self.joined('The catalyst will be optimized.'))

    def test_negation_scope(self):
        self.assertIn('否定', self.joined('There was no increase.'))
        self.assertIn('否定', self.joined('The activity did not increase.'))
        self.assertIn('否定', self.joined('The catalyst cannot be regenerated.'))

    def test_no_chemical_is_not_no_negation(self):
        self.assertEqual(reading_hints('NO conversion was measured over the catalyst.'), [])
        self.assertEqual(reading_hints('The sample was assigned No. 3.'), [])

    def test_not_only_is_not_negative_result(self):
        self.assertEqual(reading_hints('Not only conversion but also stability improved.'), [])

    def test_significant_not_necessarily_statistical(self):
        hint = self.joined('The catalyst gave a significant improvement.')
        self.assertIn('未必指统计显著性', hint)

    def test_no_significant_increase_merges_assertion_checks(self):
        hints = reading_hints('There was no significant increase.')
        self.assertEqual(len(hints), 1)
        self.assertIn('否定', hints[0])
        self.assertIn('统计', hints[0])

    def test_fold_change_does_not_compute_new_fact(self):
        hint = self.joined('A was 10-fold higher than B.')
        self.assertIn('倍数', hint)
        self.assertIn('参照样品', hint)
        self.assertNotIn('11', hint)

    def test_times_as_replication_is_not_fold_change(self):
        self.assertEqual(reading_hints('The experiment was performed three times.'), [])
        self.assertIn('倍数', self.joined('The rate was three times higher than the control.'))

    def test_percentage_points_not_relative_growth(self):
        hint = self.joined('The conversion increased by ten percentage points.')
        self.assertIn('百分点', hint)
        self.assertIn('不能改写', hint)

    def test_percent_composition_not_assumed_improvement(self):
        hint = self.joined('The sample contained 5 wt.%.')
        self.assertIn('计量基准', hint)
        self.assertIn('百分含量', hint)

    def test_comparison_reference_without_numbers(self):
        hint = self.joined('Compared with the reference catalyst, A had better stability.')
        self.assertIn('比较主体', hint)
        self.assertIn('测试条件', hint)

    def test_bound_and_approximation_preserved(self):
        hint = self.joined('Conversion was above approximately 90% at about 250 °C.')
        self.assertIn('约／大致', hint)
        self.assertIn('不等于精确值', hint)
        self.assertIn('不等于精确值', self.joined('Conversion was above 90%.'))
        self.assertIn('不等于精确值', self.joined('The rate was ≤ 10.'))

    def test_detection_limit_is_not_zero(self):
        self.assertIn('低于检出限不等于零', self.joined('The signal was below the detection limit.'))

    def test_over_catalyst_and_above_method_are_not_bounds(self):
        self.assertEqual(reading_hints('Measurements over the catalyst followed the above method.'), [])

    def test_chinese_wording(self):
        hint = self.joined('催化剂未来可能增加十倍，但没有显著增长，约80%，低于检出限。')
        self.assertIn('可能性', hint)
        self.assertIn('倍数', hint)
        self.assertIn('否定', hint)
        self.assertIn('近似', hint)

    def test_at_most_four_hints_for_all_risks(self):
        hints = reading_hints('A may not significantly increase by 10-fold compared with B; it will be below 90% at about 250°C.')
        self.assertEqual(len(hints), 4)
        self.assertTrue(all(isinstance(h, str) and h for h in hints))

    def test_translated_text_not_used_as_new_evidence(self):
        original = 'The sample was prepared by impregnation.'
        self.assertEqual(reading_hints(original, '必然增加十倍且显著提高90%。'), [])
        self.assertEqual(reading_hints('It may improve.', '它必然改善。'), reading_hints('It may improve.'))

    def test_source_is_unchanged(self):
        original = 'NH₃ may yield approximately 10%.'
        saved = original
        reading_hints(original, '测试')
        self.assertEqual(original, saved)

    def test_probably_due_to_preserves_result_versus_cause_scope(self):
        hints = reading_hints('The NO conversion rapidly decreased probably due to the reaction of NH3 with SO2.')
        self.assertEqual(len(hints), 1)
        self.assertIn('下降本身与下降原因的推测需分开核对', hints[0])
        self.assertIn('不确定性未必修饰整个结果', hints[0])

    def test_uncertain_causal_language_without_decline(self):
        for sentence in (
            'The higher activity is likely due to the larger surface area.',
            'The enhancement may be attributed to oxygen vacancies.',
            'Performance improved probably because more sites were exposed.',
            'The change could be ascribed to the metal dispersion.',
            '性能改善可能是因为活性位点更多。',
        ):
            with self.subTest(sentence=sentence):
                self.assertIn('结果本身与原因的推测', self.joined(sentence))

    def test_probably_without_causal_phrase_does_not_invent_cause(self):
        hint = self.joined('The next sample will probably perform better.')
        self.assertIn('可能性', hint)
        self.assertNotIn('原因的推测', hint)

    def test_causal_scope_stays_within_four_hints(self):
        hints = reading_hints('Conversion did not significantly increase by 10-fold compared with B, probably because the rate was below approximately 90%.')
        self.assertEqual(len(hints), 4)
        self.assertIn('不确定性未必修饰整个结果', hints[0])


if __name__ == '__main__':
    unittest.main()
