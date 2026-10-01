import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scrtool.core import write_csv, write_json
from scrtool.harvest import run_harvest
from scrtool.literature import (AbstractClient, load_metadata, rules_result,
                               screen_records, validate_result, write_screening)


def target_record():
    return {'record_id': 'a', 'doi': '10.1234/scr', 'title': 'Cu-CHA catalysts for NH3-SCR',
            'abstract': 'Cu-CHA catalysts were prepared and tested for NH3-SCR. NO conversion was measured.'}


def ai_result(fields, **changes):
    return {'decision': 'target', 'article_type': 'experimental_primary',
            'reaction_relation': 'studied', 'material_relation': 'studied',
            'reason': '实验研究 Cu-CHA 催化剂用于 NH3-SCR。',
            'evidence': [{'field': 'abstract', 'quote': fields['abstract']}], **changes}


class ScreeningTests(unittest.TestCase):
    def test_missing_abstract_is_not_exclusion(self):
        result = rules_result({'title': 'Catalyst structure and activity', 'abstract': None})
        self.assertEqual(result['decision'], 'review')
        self.assertNotIn('解析失败', result['reason'])

    def test_missing_abstract_and_parse_failure_and_permission_are_distinct(self):
        missing = rules_result({'title': 'NH3-SCR catalyst'})
        denied = rules_result({'title': 'NH3-SCR catalyst', 'abstract_status': 'access_denied'})
        failed = rules_result({'title': 'paper', 'abstract_extraction_status': 'failed'})
        self.assertIn('题录未提供摘要', missing['reason'])
        self.assertIn('401/403', denied['reason'])
        self.assertIn('本地文件解析失败', failed['reason'])
        self.assertTrue(all(row['decision'] == 'review' for row in [missing, denied, failed]))

    def test_studied_material_no_element_requirement(self):
        result = rules_result({'title': 'Stable catalysts for ammonia SCR',
                               'abstract': 'Catalysts were prepared for the selective catalytic reduction of NO with ammonia.'})
        self.assertEqual(result['decision'], 'target')

    def test_wrong_reaction_and_background_overlap(self):
        self.assertEqual(rules_result({'title': 'Cu-Zn catalysts for CO2 hydrogenation',
                                      'abstract': 'Cu-Zn catalysts were tested for methanol synthesis.'})['decision'], 'non_target')
        self.assertEqual(rules_result({'title': 'Copper oxide ammonia sensor',
                                      'abstract': 'Cu catalysts are used in NH3-SCR. Here we report an ammonia sensor.'})['decision'], 'review')
        self.assertEqual(rules_result({'title': 'Copper oxide ammonia sensor',
                                      'abstract': 'The sensor detects ammonia at room temperature.'})['decision'], 'non_target')

    def test_review_and_theory_are_separate_from_experiments(self):
        self.assertEqual(rules_result({'title': 'NH3-SCR catalysts: a review',
                                      'abstract': 'Recent advances in catalyst conversion are reviewed.'})['decision'], 'review')
        self.assertEqual(rules_result({'title': 'DFT study of Cu catalysts in NH3-SCR',
                                      'abstract': 'DFT predicts catalyst activation barriers and conversion.'})['decision'], 'review')
        self.assertEqual(rules_result(dict(target_record(), abstract='Cu catalysts were prepared and tested for NH3-SCR with DFT analysis.'))['decision'], 'target')

    def test_xml_abstract_and_multiline_evidence(self):
        result = rules_result(dict(target_record(), abstract='<jats:p>Cu catalysts were prepared for NH<sub>3</sub>-SCR.</jats:p>'))
        self.assertEqual(result['decision'], 'target')

    def test_ai_cannot_invent_evidence_or_accept_background(self):
        fields = {k: target_record().get(k, '') for k in ['title', 'abstract', 'keywords']}
        with self.assertRaises(ValueError):
            validate_result(ai_result(fields, evidence=[{'field': 'abstract', 'quote': 'invented'}]), fields)
        self.assertEqual(validate_result(ai_result(fields, reaction_relation='background'), fields)['decision'], 'review')
        self.assertEqual(validate_result(ai_result(fields, article_type='review_article'), fields)['decision'], 'review')
        self.assertEqual(validate_result(ai_result(fields, decision='non_target', evidence=[]), fields)['decision'], 'review')

    def test_ai_failure_stays_in_review_and_key_is_not_logged(self):
        with (patch.dict(os.environ, {'TEST_AI_KEY': 'super-secret'}),
              patch('scrtool.literature.urllib.request.urlopen', side_effect=RuntimeError('super-secret')),
              contextlib.redirect_stdout(io.StringIO())):
            rows = screen_records([target_record()], 'llm', {'base_url': 'https://example.test/v1', 'model': 'test', 'api_key_env': 'TEST_AI_KEY'})
        self.assertEqual(rows[0]['effective_decision'], 'review')
        self.assertEqual(rows[0]['screening_status'], 'ai_error:RuntimeError')
        self.assertNotIn('super-secret', json.dumps(rows))

    def test_compatible_api_request_and_json_evidence_validation(self):
        fields = {k: target_record().get(k, '') for k in ['title', 'abstract', 'keywords']}
        payload = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(ai_result(fields))}}],
                   'usage': {'prompt_tokens': 100, 'completion_tokens': 30}}
        with patch('scrtool.literature.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())) as mocked:
            result, usage = AbstractClient({'base_url': 'http://localhost:11434/v1', 'model': 'local-test'}).screen(fields)
        self.assertEqual(result['decision'], 'target')
        self.assertEqual(usage['prompt_tokens'], 100)
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, 'http://localhost:11434/v1/chat/completions')
        self.assertEqual(json.loads(request.data)['messages'][1]['role'], 'user')

    def test_truncated_output_is_rejected(self):
        payload = {'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]}
        with patch('scrtool.literature.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
            with self.assertRaises(ValueError):
                AbstractClient({'base_url': 'http://localhost:11434/v1', 'model': 'local'}).screen({'title': '', 'abstract': '', 'keywords': ''})

    def test_manual_replay_preserves_input_and_reviewer(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            decisions = Path(tmp) / 'manual_review_template.csv'
            write_csv(decisions, [{'record_id': 'a', 'manual_decision': 'non_target', 'reviewer': 'expert', 'reviewer_notes': 'wrong scope'}])
            before = decisions.read_bytes()
            rows = screen_records([target_record()], decisions_path=decisions)
            write_screening(tmp, rows, decisions)
            self.assertEqual(rows[0]['effective_decision'], 'non_target')
            self.assertEqual(rows[0]['reviewer'], 'expert')
            self.assertEqual(decisions.read_bytes(), before)
            self.assertTrue((Path(tmp) / 'manual_review_next.csv').exists())
            write_csv(decisions, [{'record_id': 'a', 'manual_decision': '相关', 'reviewer': 'expert'}])
            self.assertEqual(screen_records([target_record()], decisions_path=decisions)[0]['effective_decision'], 'target')
            write_csv(decisions, [{'record_id': 'wrong', 'manual_decision': 'target', 'reviewer': 'expert'}])
            with self.assertRaises(ValueError):
                screen_records([target_record()], decisions_path=decisions)

    def test_csv_metadata_can_be_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'records.csv'
            write_csv(path, [dict(target_record(), pdf_urls=['https://example.test/paper.pdf'], needs_ocr=False)])
            record = load_metadata(path)[0]
            self.assertEqual(record['pdf_urls'], ['https://example.test/paper.pdf'])
            self.assertIs(record['needs_ocr'], False)

    def test_selective_download_does_not_spend_limit_on_excluded_paper(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            source = Path(tmp) / 'records.json'
            records = [dict(target_record(), record_id='b', doi='10.1234/co2', title='Cu-Zn catalysts for CO2 hydrogenation', abstract='Methanol synthesis catalysts were tested.'),
                       target_record(), dict(target_record(), record_id='c', doi='10.1234/missing', abstract=None)]
            write_json(source, records)
            out = Path(tmp) / 'run'
            args = SimpleNamespace(limit=10, max_downloads=1, max_file_mb=1, timeout=1, delay=0,
                from_year=None, to_year=None, output=str(out), query=None, sources=['none'], email=None,
                openalex_key=None, elsevier_key=None, springer_key=None, records=str(source), local_papers=[],
                download=True, library_dir=str(Path(tmp) / 'library'), screen_engine='rules', download_review=False,
                public_abstracts=False)
            def fake_pdf(self, urls, destination, max_bytes):
                destination.write_bytes(b'%PDF-1.7\nfixture')
                return {'status': 'downloaded_pdf', 'path': str(destination), 'errors': []}
            with patch('scrtool.harvest.Harvester.download_pdf', fake_pdf), \
                 patch('scrtool.harvest.Harvester.resolve_openalex_doi', return_value=[]), \
                 patch('scrtool.harvest.Harvester.resolve_semantic_scholar_doi', return_value=[]):
                self.assertEqual(run_harvest(args), 0)
            manifest = json.loads((out / 'download_manifest.json').read_text(encoding='utf-8'))
            statuses = {r['doi']: r['status'] for r in manifest}
            self.assertEqual(statuses, {'10.1234/scr': 'downloaded_pdf', '10.1234/co2': 'screened_out', '10.1234/missing': 'awaiting_review'})
            self.assertEqual(len(list((out / 'files').glob('*.pdf'))), 1)
            linked = json.loads((out / 'extraction_manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(list(linked.values())[0]['paper_id'], '10.1234/scr')

    def test_downloaded_batch_passes_doi_to_extraction(self):
        from scrtool.cli import run_extract
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            files = root / 'files'
            files.mkdir()
            (files / 'raw.csv').write_text('catalyst,temperature [degC],NOx conversion [%]\nA,200,90\n', encoding='utf-8')
            write_json(root / 'extraction_manifest.json', {'raw.csv': {'paper_id': '10.1234/link'}})
            args = SimpleNamespace(input=str(files), output=str(root / 'extract'), engine='rules',
                                   config=None, mapping=None, manifest=None, paper_id=None, reaction='NH3-SCR', max_chars=24000)
            self.assertEqual(run_extract(args), 0)
            records = json.loads((root / 'extract/candidates.json').read_text(encoding='utf-8'))
            self.assertTrue(records)
            self.assertTrue(all(r['paper_id'] == '10.1234/link' for r in records))


if __name__ == '__main__':
    unittest.main()
