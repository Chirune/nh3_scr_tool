import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scrtool.fulltext import acquire_pdf
from scrtool.workbench import Project


class FakeHarvester:
    email = None

    def __init__(self):
        self.calls = []

    def resolve_openalex_doi(self, doi):
        self.calls.append(('openalex', doi))
        return ['https://open.example/article.pdf']

    def download_pdf(self, urls, destination):
        self.calls.append(('download', urls))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b'%PDF-1.7\nexample')
        return {'status': 'downloaded_pdf', 'path': str(destination),
                'url': urls[0], 'errors': []}


class FulltextWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        abstract = self.folder / 'abstract.json'
        abstract.write_text(json.dumps([{'doi': '10.1234/article',
            'title': 'An experimental NH3-SCR catalyst study',
            'abstract': 'Original catalyst experiments for ammonia selective catalytic reduction were conducted at several temperatures.'}]), encoding='utf-8')
        self.project = Project(self.folder / 'project')
        self.project.import_abstracts([abstract])
        self.identity = self.project.state['papers'][0]['record_id']

    def test_resolver_labels_valid_pdf(self):
        client = FakeHarvester()
        result = acquire_pdf(self.project.paper(self.identity), self.folder / 'paper.pdf', harvester=client)
        self.assertEqual(result['source'], 'OpenAlex')
        self.assertEqual(result['status'], 'downloaded_pdf')
        self.assertEqual(client.calls[0], ('openalex', '10.1234/article'))

    def test_only_kept_paper_is_attached_and_provenance_survives_resume(self):
        with self.assertRaises(ValueError):
            self.project.acquire_primary(self.identity)
        self.project.decide_papers([self.identity], 'target', 'reviewer')
        def fake_acquire(record, destination, **kwargs):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b'%PDF-1.7\nexample')
            return {'status': 'downloaded_pdf', 'path': str(destination),
                    'url': 'https://open.example/article.pdf', 'source': 'OpenAlex', 'errors': []}
        with patch('scrtool.fulltext.acquire_pdf', side_effect=fake_acquire) as mocked:
            result = self.project.acquire_primary(self.identity)
            self.assertEqual(self.project.acquire_primary(self.identity)['status'], 'already_attached')
            self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result['status'], 'downloaded_pdf')
        resumed = Project(self.project.folder)
        attached = resumed.state['attachments'][self.identity]
        self.assertEqual(len(attached), 1)
        self.assertTrue((resumed.folder / attached[0]['path']).exists())
        self.assertEqual(resumed.state['acquisition'][self.identity]['url'], 'https://open.example/article.pdf')

    def test_missing_doi_and_failed_download_do_not_create_attachment(self):
        self.project.decide_papers([self.identity], 'target', 'reviewer')
        self.project.paper(self.identity)['doi'] = ''
        result = self.project.acquire_primary(self.identity)
        self.assertEqual(result['status'], 'missing_doi')
        self.assertFalse(self.project.state['attachments'].get(self.identity))
        self.project.paper(self.identity)['doi'] = '10.1234/article'
        with patch('scrtool.fulltext.acquire_pdf', return_value={
            'status': 'no_pdf', 'path': None, 'source': None, 'url': None,
            'errors': ['OpenAlex：403 Forbidden']}) as mocked:
            result = self.project.acquire_primary(self.identity)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result['status'], 'no_pdf')
        self.assertFalse(self.project.state['attachments'].get(self.identity))
        self.assertIn('403', Project(self.project.folder).state['acquisition'][self.identity]['errors'][0])


if __name__ == '__main__':
    unittest.main()
