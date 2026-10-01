import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scrtool.fulltext import acquire_pdf, guidance, publisher_route
from scrtool.core import write_json
from scrtool.workbench import Project


class FakeHarvester:
    email = None
    elsevier_key = None
    springer_key = None

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

    def test_rate_limit_is_explained_without_raw_exception_in_user_message(self):
        paper = dict(self.project.paper(self.identity), doi='10.1016/j.apsusc.2019.145153',
                     publisher='Elsevier BV')
        result = {'status': 'no_pdf', 'path': None,
                  'errors': ['Semantic Scholar：RetryError: too many 429 error responses']}
        message = guidance(paper, result)
        self.assertIn('机构访问', message)
        self.assertIn('暂时限流', message)
        self.assertNotIn('RetryError', message)

    def test_elsevier_route_uses_publisher_api_before_open_resolvers(self):
        class ElsevierClient(FakeHarvester):
            elsevier_key = 'test-only'
            def download_elsevier_pdf(self, doi, destination):
                self.calls.append(('elsevier_pdf', doi))
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b'%PDF-1.7\nexample')
                return {'status':'downloaded_pdf','path':str(destination),
                        'url':'https://api.elsevier.com/content/article/doi/test','errors':[]}
        paper = dict(self.project.paper(self.identity), doi='10.1016/j.apsusc.2019.145153',
                     publisher='Elsevier BV')
        client = ElsevierClient()
        result = acquire_pdf(paper, self.folder / 'elsevier.pdf', harvester=client)
        self.assertEqual(publisher_route(paper), 'elsevier')
        self.assertEqual(client.calls, [('elsevier_pdf', paper['doi'])])
        self.assertEqual(result['source'], 'Elsevier 全文 API')

    def test_online_search_imports_existing_harvester_results_without_downloading(self):
        def fake_harvest(args):
            self.assertFalse(args.download)
            self.assertEqual(args.sources, ['openalex', 'crossref', 'scopus'])
            write_json(Path(args.output) / 'records.json', [{'doi':'10.1234/new',
                'title':'New NH3-SCR catalyst paper','abstract':'Original experimental ammonia SCR catalyst results and measurements.'}])
            write_json(Path(args.output) / 'errors.json', [])
            return 0
        with patch('scrtool.harvest.run_harvest', side_effect=fake_harvest):
            result = self.project.search_online('NH3-SCR catalyst', ['openalex','crossref','scopus'],
                                                elsevier_key='secret-for-test')
        self.assertEqual(result['found'], 1)
        self.assertEqual(result['total'], 2)
        self.assertFalse(self.project.paper(next(r['record_id'] for r in self.project.state['papers']
                           if r['doi']=='10.1234/new')).get('human_decision'))
        self.assertNotIn('secret-for-test', (self.project.folder / 'project.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
