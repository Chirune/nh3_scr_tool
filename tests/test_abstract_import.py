import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from scrtool.abstract_import import collect_files, export_collection, html_record, read_abstract_file

ABSTRACT = 'NH3-SCR catalysts were experimentally prepared and tested for NOx reduction. ' + 'Chemical structure, conversion and selectivity were measured at different temperatures. ' * 2

class AbstractImportTests(unittest.TestCase):
    def test_html_excludes_highlights_and_preserves_chemical_symbols(self):
        html = '<meta name="citation_doi" content="10.1016/test"><meta name="citation_title" content="NH3-SCR catalyst"><div id="abstracts"><div><h2>Highlights</h2><p>HIGHLIGHTS_NOT_ABSTRACT</p></div><div id="ab0005"><h2>Abstract</h2><p>NH<sub>3</sub>-SCR: Å ø ±. ' + ABSTRACT + '</p></div><div><h2>Graphical abstract</h2><p>GRAPHICAL_NOT_ABSTRACT</p></div></div>'
        row = html_record(html.encode('utf-8'))
        self.assertIn('NH3-SCR', row['abstract'])
        self.assertIn('Å ø ±', row['abstract'])
        self.assertNotIn('NOT_ABSTRACT', row['abstract'])
        self.assertEqual(row['abstract_locator'], '#ab0005 > heading:Abstract')

    def test_zotero_csl_and_ris_preserve_abstract_and_doi(self):
        with tempfile.TemporaryDirectory() as tmp:
            csl = Path(tmp) / 'zotero.json'
            csl.write_text(json.dumps([{'DOI': 'https://doi.org/10.1016/test', 'title': 'NH3-SCR catalyst', 'abstract': ABSTRACT, 'issued': {'date-parts': [[2020, 1]]}}]), encoding='utf-8')
            row = read_abstract_file(csl)[0]
            self.assertEqual(row['doi'], '10.1016/test')
            self.assertEqual(row['year'], 2020)
            self.assertEqual(row['abstract'], ABSTRACT.strip())
            ris = Path(tmp) / 'zotero.ris'
            ris.write_text('TY  - JOUR\nTI  - NH3-SCR catalyst\nDO  - 10.1016/test\nAB  - ' + ABSTRACT + '\n continuation of abstract\nER  - \n', encoding='utf-8')
            self.assertTrue(read_abstract_file(ris)[0]['abstract'].endswith('continuation of abstract'))

    def test_missing_abstract_and_declared_short_summary_stay_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'items.json'
            file.write_text(json.dumps({'items': [{'doi': '10.1016/a', 'title': 'NH3-SCR catalyst'}, {'doi': '10.1016/b', 'title': 'NH3-SCR catalyst', 'abstract': ABSTRACT, 'abstract_is_full': False}]}), encoding='utf-8')
            rows, errors = collect_files([file])
            self.assertFalse(errors)
            self.assertTrue(all(r['effective_decision'] == 'review' for r in rows))
            self.assertFalse(rows[1]['abstract_is_full'])

    def test_conflicting_duplicate_keeps_both_abstracts_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i, abstract in enumerate((ABSTRACT, ABSTRACT + 'Conflicting additional claim.')):
                file = Path(tmp) / (str(i) + '.json')
                file.write_text(json.dumps([{'doi': '10.1016/test', 'title': 'NH3-SCR catalyst', 'abstract': abstract}]), encoding='utf-8')
                paths.append(file)
            rows, errors = collect_files(paths)
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(rows[0]['source_inputs']), 2)
            self.assertIn('abstract_conflict', rows[0]['import_issues'])
            self.assertEqual(rows[0]['effective_decision'], 'review')
            self.assertIn('Conflicting', rows[0]['abstract_alternatives'][0]['abstract'])

    def test_one_bad_file_does_not_discard_good_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = Path(tmp) / 'good.json', Path(tmp) / 'bad.json'
            good.write_text(json.dumps([{'doi': '10.1016/test', 'title': 'NH3-SCR catalyst', 'abstract': ABSTRACT}]), encoding='utf-8')
            bad.write_text('{broken', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                rows, report = export_collection([good, bad], Path(tmp) / 'out')
            self.assertEqual(len(rows), 1)
            self.assertEqual(report['import_errors'], 1)
            self.assertTrue((Path(tmp) / 'out/manual_review_template.csv').exists())
            self.assertFalse(report['machine_learning_ready'])

    def test_wrong_json_is_rejected_instead_of_inventing_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'settings.json'
            file.write_text('{"model":"anything"}', encoding='utf-8')
            rows, errors = collect_files([file])
            self.assertEqual(rows, [])
            self.assertEqual(len(errors), 1)

    def test_csv_export_reimport_keeps_decisions_and_empty_issue_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'input.json'
            file.write_text(json.dumps([{'doi': '10.1016/test', 'title': 'NH3-SCR catalyst', 'abstract': ABSTRACT, 'document_type': 'Review'}]), encoding='utf-8')
            rows, report = export_collection([file], Path(tmp) / 'out')
            imported, errors = collect_files([Path(tmp) / 'out/records.csv'])
            self.assertFalse(errors)
            self.assertEqual(imported[0]['import_issues'], [])
            self.assertEqual(imported[0]['effective_decision'], rows[0]['effective_decision'])
            self.assertEqual(imported[0]['effective_decision'], 'review')
            self.assertEqual(imported[0]['abstract'], rows[0]['abstract'])

    def test_html_paragraph_boundaries_preserve_words_and_formula(self):
        row = html_record('<meta name="citation_doi" content="10.1016/test"><div><h2>Abstract</h2><p>NH<sub>3</sub>-SCR. ' + ABSTRACT + '</p><p>Second paragraph.</p></div>')
        self.assertIn('NH3-SCR', row['abstract'])
        self.assertTrue(row['abstract'].endswith('measured at different temperatures. Second paragraph.'))

    def test_other_main_reactions_with_nh3_keywords_are_kept_for_review(self):
        from scrtool.literature import rules_result
        for title in ('Mechanistic investigation of NH3 oxidation over an NH3-SCR catalyst',
                      'Ethanol-SCR of NO: influential role of NH3 and catalyst characterization'):
            result = rules_result({'title': title, 'abstract': ABSTRACT})
            self.assertEqual(result['decision'], 'review')

if __name__ == '__main__':
    unittest.main()
