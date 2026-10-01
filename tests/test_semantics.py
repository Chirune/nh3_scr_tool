import unittest
from scrtool.semantics import semantic_records


class SemanticTests(unittest.TestCase):
    def extract(self, text):
        block = dict(paper_id='paper', source_id='sha', source_file='paper.pdf',
                     kind='pdf_text', locator='page:2', block_id='block', text=text)
        rows = list(semantic_records(block))
        for row in rows:
            self.assertIn(row['evidence'], text)
            self.assertIsNone(row['absolute_value'])
            self.assertIsNone(row['baseline_value'])
            self.assertFalse(row['training_eligible'])
        return rows

    def test_fold_comparison_is_not_an_absolute_measurement(self):
        row = self.extract('Catalyst: Cu-A showed tenfold turnover frequency compared with Cu-B at 200 °C.')[0]
        self.assertEqual(row['reported_change']['amount'], 10)
        self.assertEqual(row['subject'], 'Cu-A')
        self.assertIn('turnover_frequency', row['metric_candidates'])
        self.assertIn('Cu-B', row['comparator'])
        self.assertIn('missing_absolute_baseline', row['issues'])

    def test_chinese_fold_increment_remains_ambiguous(self):
        row = self.extract('催化剂:A 的效率提高十倍。')[0]
        self.assertEqual(row['reported_change']['amount'], 10)
        self.assertIn('ambiguous_fold_wording', row['issues'])

    def test_prospective_and_negated_language_preserved(self):
        prospective = self.extract('This catalyst may improve NOx conversion in future experiments.')[0]
        self.assertEqual(prospective['statement_type'], 'prospective_claim')
        negated = self.extract('The catalyst did not improve NOx conversion.')[0]
        self.assertEqual(negated['statement_type'], 'negated_statement')
        self.assertTrue(negated['negated'])

    def test_relative_percent_is_not_conversion_percent(self):
        row = self.extract('NOx conversion increased by 20% compared with Cu-B.')[0]
        self.assertEqual(row['reported_change']['amount'], 20)
        self.assertIn('unresolved', row['reported_change']['interpretation'])

    def test_plain_numeric_statement_is_left_to_measurement_extractor(self):
        self.assertEqual(self.extract('NOx conversion was 90% at 200 °C.'), [])

    def test_decimal_and_pdf_line_wrapping_keep_exact_quote(self):
        text = 'A catalyst showed a 1.5-fold\nincrease in turnover frequency. This may improve performance.'
        rows = self.extract(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['reported_change']['amount'], 1.5)


if __name__ == '__main__': unittest.main()
