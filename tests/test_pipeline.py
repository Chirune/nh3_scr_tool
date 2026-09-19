import argparse
import csv
import json
import tempfile
import unittest
from pathlib import Path
from scrtool.core import quantity, make_record, write_json, write_csv, read_json, infer_header
from scrtool.ingest import blocks
from scrtool.extract import rule_records, table_records
from scrtool.cli import calibrate, run_extract, run_review, run_export, run_digitize
from scrtool.documents import classify_document
from scrtool.screening import classify


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self, name, text):
        p = self.root / name
        p.write_text(text, encoding='utf-8')
        return p

    def test_units_and_censoring(self):
        self.assertAlmostEqual(quantity('473.15', 'K', 'temperature')[0], 200)
        self.assertEqual(quantity('1000', 'μmol/g', 'acid_amount')[0], 1)
        for text in ['>95', '90-100', '95 ± 2', '', 'NaN', '=A2*2']:
            self.assertIsNone(quantity(text, '%', 'nox_conversion')[0])
        self.assertIsNone(quantity('101', '%', 'nox_conversion')[0])
        self.assertIsNone(quantity('90', '', 'nox_conversion')[0])
        self.assertEqual(quantity('1.88(7)', 'Å', 'bond_distance')[0], 1.88)
        self.assertEqual(infer_header('CNª'), ('coordination_number', '1'))
        self.assertEqual(infer_header('R b(Å)'), ('bond_distance', 'å'))

    def test_nh3_scr_primary_screening(self):
        decision, article_type, reason = classify(
            'Low-temperature NH3-SCR over Mn-Ce catalysts',
            'The catalysts were prepared and characterized. NOx conversion and N2 selectivity were measured at different temperatures.',
        )
        self.assertEqual((decision, article_type), ('target', 'experimental_primary'))
        self.assertIn('strong NH3-SCR', reason)

    def test_nh3_scr_review_is_not_primary_training_source(self):
        decision, article_type, _ = classify(
            'Selective catalytic reduction of NOx with NH3 over copper catalysts: recent advances and future prospects',
            'This review summarizes catalyst development for ammonia SCR.',
        )
        self.assertEqual((decision, article_type), ('review', 'review_article'))

    def test_no_selective_reduction_with_nh3_word_order(self):
        decision, article_type, _ = classify(
            'Single-atom catalysts reveal active sites in NO selective reduction with NH3',
            'Catalysts were prepared and catalytic activity, conversion and selectivity were measured as a function of temperature.',
        )
        self.assertEqual((decision, article_type), ('target', 'experimental_primary'))

    def test_co2_methanol_is_non_target(self):
        decision, article_type, _ = classify(
            'Cu-Zn catalysts for CO2 hydrogenation',
            'Methanol synthesis activity and selectivity were measured.',
        )
        self.assertEqual((decision, article_type), ('non_target', 'other_reaction'))

    def test_distinguish_no_and_nox(self):
        p = self.source('text.txt', 'NO conversion: 90%. NOx conversion: 80%.')
        records = [r for b in blocks(p) for r in rule_records(b)]
        self.assertEqual({r['property']: r['value'] for r in records}, {'no_conversion': 90, 'nox_conversion': 80})

    def test_different_rows_never_join(self):
        p = self.source('raw.csv', 'catalyst,temperature [K],nox_conversion [%]\nA,473.15,90\nB,573.15,80\n')
        records = [r for b in blocks(p, 'doi:test') for r in table_records(b)]
        perf = [r for r in records if r['category'] == 'performance']
        self.assertEqual([(r['catalyst'], round(r['conditions']['temperature']['value'])) for r in perf], [('A', 200), ('B', 300)])
        self.assertNotEqual(perf[0]['experiment_id'], perf[1]['experiment_id'])
        self.assertTrue(all(r['paper_id'] == 'doi:test' for r in perf))

    def test_markdown_table(self):
        p = self.source('test.md', '| catalyst | nox_conversion [%] |\n|---|---|\n| A | 90 |\n')
        records = [r for b in blocks(p) for r in table_records(b)]
        self.assertEqual(records[0]['value'], 90)
        self.assertEqual(records[0]['locator'], 'line:3')

    def test_saved_html_text_table_and_declared_review(self):
        p = self.source('paper.html', '''<html><head>
        <meta name="citation_title" content="Catalyst advances">
        <meta name="citation_article_type" content="Review article"></head>
        <body><script>NO conversion 99%</script><p>Catalyst: A; NOx conversion: 80%.</p>
        <table><tr><th>catalyst</th><th>nox_conversion [%]</th></tr><tr><td>A</td><td>80</td></tr></table></body></html>''')
        bs = list(blocks(p, 'doi:test'))
        self.assertTrue(any(b['kind'] == 'html_text' for b in bs))
        self.assertTrue(any(b.get('row', {}).get('catalyst') == 'A' for b in bs))
        doc = classify_document(bs, p)
        self.assertEqual(doc['document_type'], 'review')
        self.assertFalse(doc['training_eligible'])

    def test_review_record_cannot_be_approved(self):
        p = self.source('review.html', '<meta name="citation_article_type" content="Review article"><p>Catalyst: A; NOx conversion: 80%.</p>')
        b = list(blocks(p, 'doi:review'))[0]
        doc = classify_document([b], p)
        b.update(document_type=doc['document_type'], document_type_confidence=doc['document_type_confidence'], training_eligible=False)
        r = list(rule_records(b))[0]
        self.assertIn('secondary_source_document', r['issues'])

    def test_mineru_document_uses_late_research_sections(self):
        p = self.source('primary_content_list.json', '[]')
        bs = [dict(text='introductory text', document_meta={}) for _ in range(15)]
        bs.extend([dict(text='Materials and methods', document_meta={}), dict(text='Data availability', document_meta={})])
        doc = classify_document(bs, p)
        self.assertEqual(doc['document_type'], 'research_article')
        self.assertTrue(doc['training_eligible'])

    def test_mineru_page_and_image(self):
        p = self.root / 'content_list.json'
        write_json(p, [{'type': 'text', 'text': 'NO conversion 90%', 'page_idx': 2}, {'type': 'image', 'img_path': 'images/fig1.jpg', 'page_idx': 3}])
        results = list(blocks(p))
        self.assertEqual(results[0]['locator'], 'page:3;item:0')
        self.assertEqual(results[1]['image_path'], 'images/fig1.jpg')

    def test_mineru_html_table_rowspan(self):
        p = self.root / 'content_list.json'
        table = '<table><tr><td>Sample</td><td>Shell</td><td>CN</td></tr><tr><td rowspan="2">A</td><td>A-O</td><td>6</td></tr><tr><td>A-M</td><td>3</td></tr></table>'
        write_json(p, [{'type': 'table', 'table_body': table, 'page_idx': 4}])
        results = list(blocks(p, 'doi:test'))
        self.assertEqual([b['row']['Sample'] for b in results], ['A', 'A'])
        self.assertEqual([b['row']['Shell'] for b in results], ['A-O', 'A-M'])
        self.assertEqual(results[1]['locator'], 'page:5;item:0;table:1;row:2')

    def test_invalid_evidence_rejected(self):
        b = next(blocks(self.source('x.txt', 'NO conversion 90%')))
        with self.assertRaises(ValueError):
            make_record(b, 'A', 'no_conversion', '90', '%', 'invented evidence')
        with self.assertRaises(ValueError):
            make_record(b, 'A', 'no_conversion', '90', '%', b['text'], {'temperature': {'raw_value': 200, 'unit': 'degC', 'evidence': 'invented'}})

    def test_duplicate_headers_rejected(self):
        p = self.source('bad.csv', 'catalyst,catalyst\nA,B\n')
        with self.assertRaises(ValueError):
            list(blocks(p))

    def test_mapping_requires_real_column(self):
        b = next(blocks(self.source('raw.csv', 'catalyst,foo\nA,12\n')))
        with self.assertRaises(ValueError):
            list(table_records(b, {'columns': {'absent': {'property': 'temperature', 'unit': 'K'}}}))

    def test_calibration(self):
        self.assertEqual(calibrate(250, 400, 100, 0, 100, 'linear'), 50)
        self.assertAlmostEqual(calibrate(50, 0, 100, 1, 100, 'log'), 10)
        with self.assertRaises(ValueError):
            calibrate(1, 0, 0, 1, 10, 'linear')
        with self.assertRaises(ValueError):
            calibrate(1, 0, 10, 0, 10, 'log')

    def test_review_and_export_end_to_end(self):
        p = self.source('raw.csv', 'catalyst,temperature [degC],nox_conversion [%]\nA,200,90\n')
        out = self.root / 'out'
        args = argparse.Namespace(input=str(p), output=str(out), config=None, engine='rules', mapping=None, manifest=None, paper_id='test', max_chars=24000, reaction='NH3-SCR')
        self.assertEqual(run_extract(args), 0)
        candidates = out / 'candidates.json'
        records = read_json(candidates)
        # Pending values must not enter ML export.
        mlout = self.root / 'ml'
        run_export(argparse.Namespace(input=str(candidates), output=str(mlout)))
        self.assertEqual(read_json(mlout / 'export_report.json')['ml_rows'], 0)
        decisions = self.root / 'decisions.csv'
        write_csv(decisions, [dict(record_id=r['record_id'], decision='approve', reviewer='test') for r in records])
        reviewed = self.root / 'reviewed.json'
        run_review(argparse.Namespace(candidates=str(candidates), decisions=str(decisions), output=str(reviewed)))
        run_export(argparse.Namespace(input=str(reviewed), output=str(mlout)))
        self.assertEqual(read_json(mlout / 'export_report.json')['ml_rows'], 1)

    def test_uncertain_value_cannot_be_approved(self):
        b = next(blocks(self.source('data.csv', 'catalyst,nox_conversion [%]\nA,>90\n')))
        r = list(table_records(b))[0]
        candidates, decisions = self.root / 'c.json', self.root / 'd.csv'
        write_json(candidates, [r])
        write_csv(decisions, [dict(record_id=r['record_id'], decision='approve', reviewer='test')])
        with self.assertRaises(ValueError):
            run_review(argparse.Namespace(candidates=str(candidates), decisions=str(decisions), output=str(self.root / 'r.json')))

    def test_excel_formula_not_treated_as_measurement(self):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest('openpyxl not installed')
        p = self.root / 'raw.xlsx'
        wb = Workbook(); ws = wb.active
        ws.append(['catalyst', 'nox_conversion [%]']); ws.append(['A', '=40+50']); wb.save(p); wb.close()
        r = list(table_records(next(blocks(p))))[0]
        self.assertIsNone(r['value'])

    def test_curve_estimation_flag_survives_import(self):
        p = self.source('curve.csv', 'catalyst,temperature [degC],nox_conversion [%],estimated\nA,200,90,True\n')
        records = list(table_records(next(blocks(p))))
        self.assertTrue(all(r['estimated'] for r in records))
        self.assertTrue(all(r['source_kind'] == 'digitized_curve' for r in records))

    def test_approved_performance_without_temperature_is_withheld(self):
        p = self.source('raw.csv', 'catalyst,nox_conversion [%]\nA,90\n')
        r = list(table_records(next(blocks(p))))[0]
        r['review_status'] = 'approved'
        reviewed = self.root / 'reviewed.json'
        write_json(reviewed, [r])
        out = self.root / 'ml'
        run_export(argparse.Namespace(input=str(reviewed), output=str(out)))
        self.assertEqual(read_json(out / 'export_report.json'), {'approved': 1, 'ml_rows': 0, 'withheld': 1})


if __name__ == '__main__':
    unittest.main()
