import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from pypdf import PdfWriter
import zotero_bridge as bridge

DOI = '10.9999/catalyst.paper'
TITLE = 'Ammonia catalytic reduction over ceria supported ruthenium'


def pdf_bytes(doi=DOI, title=TITLE, size=200):
    writer = PdfWriter()
    writer.add_blank_page(width=size, height=200)
    writer.add_metadata({'/Title': title, '/doi': doi})
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def record():
    return {'id': 'r1', 'doi': DOI, 'title': TITLE, 'manual_decision': 'target',
            'effective_decision': 'target', 'screening': {'decision': 'target', 'reason': 'fixture'}}


class FakeClient:
    def __init__(self, path):
        self.path = path
        self.check = Mock(return_value={'connected': True})
        self.find_papers = Mock(return_value=[{'key': 'PARENT01'}])
        self.attachments = Mock(return_value=[{'key': 'ATTACH01', 'data': {'itemType': 'attachment', 'contentType': 'application/pdf', 'title': 'Full Text PDF'}}])
        self.attachment_path = Mock(return_value=path)


class ZoteroBridgeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='zotero_bridge_')
        self.root = Path(self.folder.name)
        self.original = self.root / 'library' / 'article.pdf'
        self.original.parent.mkdir()
        self.original.write_bytes(pdf_bytes())
        self.client = FakeClient(self.original)
        self.run = {'run_dir': str(self.root / 'run'), 'records': [record()], 'profile': 'scr_ammonia'}

    def tearDown(self):
        self.folder.cleanup()

    def receive(self, item=None):
        return bridge.receive_record(item or self.run['records'][0], self.run['run_dir'], self.client)

    def test_verified_copy_is_handed_to_existing_figure_intake(self):
        original = self.original.read_bytes()
        def persist(run):
            Path(run['run_dir']).mkdir(exist_ok=True)
            (Path(run['run_dir']) / 'run.json').write_text(json.dumps(run), encoding='utf-8')
        result = bridge.sync_run(self.run, self.client, persist=persist)
        self.assertEqual(result['zotero_sync_summary']['received'], 1)
        item = result['records'][0]
        self.assertNotEqual(Path(item['local_path']), self.original)
        self.assertEqual(Path(item['local_path']).read_bytes(), original)
        self.assertEqual(self.original.read_bytes(), original)
        self.assertEqual(item['local_sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(item['pdf_acquisition']['acquisition_method'], 'zotero_local')
        spec = importlib.util.spec_from_file_location('zotero_intake_test', Path(__file__).resolve().parents[1] / 'intake.py')
        intake = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(intake)
        accepted = intake.load_screened_input(Path(self.run['run_dir']) / 'run.json')
        self.assertEqual(len(accepted['papers']), 1)
        self.assertEqual(accepted['papers'][0]['source_sha256'], item['local_sha256'])

    def test_repeat_does_not_read_zotero_file_or_add_duplicate_history(self):
        bridge.sync_run(self.run, self.client)
        self.client.find_papers.reset_mock()
        bridge.sync_run(self.run, self.client)
        self.client.find_papers.assert_not_called()
        self.assertEqual(self.run['zotero_sync_summary']['received'], 0)
        self.assertEqual(self.run['zotero_sync_summary']['ready'], 1)
        self.assertEqual(len(list((self.root / 'run').rglob('*.pdf'))), 1)

    def test_normal_auto_pdf_entry_preserves_zotero_origin(self):
        import pdf_fetch
        bridge.sync_run(self.run, self.client)
        with patch.object(pdf_fetch, 'request_bytes') as network:
            result = pdf_fetch.fetch_record(self.run['records'][0], self.run['run_dir'])
        network.assert_not_called()
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['acquisition_method'], 'zotero_local')
        self.assertEqual(result['source_url'], 'https://doi.org/' + DOI)
        self.assertEqual(result['zotero_attachment_key'], 'ATTACH01')

    def test_excluded_and_review_papers_are_never_received(self):
        self.run['records'] = [{**record(), 'manual_decision': 'non_target'}, {**record(), 'manual_decision': 'review'}]
        bridge.sync_run(self.run, self.client)
        self.client.find_papers.assert_not_called()

    def test_metadata_without_pdf_is_waiting_not_success(self):
        self.client.attachments.return_value = []
        result = self.receive()
        self.assertEqual(result['status'], 'no_pdf')
        self.assertNotIn('local_path', result)

    def test_unsaved_missing_doi_and_missing_file_remain_waiting(self):
        self.client.find_papers.return_value = []
        self.assertEqual(self.receive()['status'], 'not_saved')
        self.assertEqual(self.receive({**record(), 'doi': ''})['status'], 'missing_doi')
        self.client.find_papers.return_value = [{'key': 'PARENT01'}]
        self.client.attachment_path.return_value = self.root / 'not_downloaded.pdf'
        self.assertEqual(self.receive()['status'], 'file_missing')

    def test_html_wrong_doi_and_wrong_title_never_bind(self):
        for data in (b'<html>Institution login</html>', pdf_bytes('10.9999/wrong'), pdf_bytes(title='Unrelated biological manuscript')):
            self.original.write_bytes(data)
            self.assertEqual(self.receive()['status'], 'identity_unverified')
            self.assertFalse(list((self.root / 'run').rglob('*.pdf')))

    def test_supplement_cannot_replace_main_article(self):
        self.client.attachments.return_value[0]['data']['filename'] = 'paper_supplementary.pdf'
        self.assertEqual(self.receive()['status'], 'supplement_only')
        self.client.attachment_path.assert_not_called()

    def test_different_versions_are_reported_without_arbitrary_choice(self):
        other = self.original.with_name('version2.pdf')
        other.write_bytes(pdf_bytes(size=220))
        self.client.attachments.return_value.append({'key': 'ATTACH02', 'data': {'title': 'Full Text'}})
        self.client.attachment_path.side_effect = [self.original, other]
        self.assertEqual(self.receive()['status'], 'ambiguous')
        self.assertFalse(list((self.root / 'run').rglob('*.pdf')))

    def test_duplicate_identical_attachments_are_received_once(self):
        self.client.attachments.return_value.append({'key': 'ATTACH02', 'data': {'title': 'Full Text'}})
        self.assertEqual(self.receive()['status'], 'received')

    def test_changed_bound_copy_is_not_replaced(self):
        bridge.sync_run(self.run, self.client)
        target = Path(self.run['records'][0]['local_path'])
        target.write_bytes(pdf_bytes(size=230))
        self.assertEqual(self.receive()['status'], 'source_changed')
        self.assertEqual(target.read_bytes(), pdf_bytes(size=230))

    def test_cancel_never_copies_file(self):
        event = threading.Event()
        event.set()
        bridge.sync_run(self.run, self.client, cancel_event=event)
        self.assertTrue(self.run['zotero_sync_summary']['cancelled'])
        self.assertFalse(list((self.root / 'run').rglob('*.pdf')))

    def test_source_size_limit_is_checked_before_read(self):
        with patch.object(bridge, 'MAX_PDF_BYTES', 10):
            self.assertEqual(self.receive()['status'], 'file_missing')

    def test_attachment_url_cannot_fetch_remote_network_or_non_pdf(self):
        for url in ('https://example.org/paper.pdf', 'file://remote/share/paper.pdf', 'file:relative.pdf',
                    'file:///C:/secret.txt', 'file:////server/paper.pdf', 'file:///C:/bad%00.pdf'):
            with self.assertRaises((bridge.BridgeError, ValueError)):
                bridge.local_file_url(url)
        self.assertEqual(bridge.local_file_url(self.original.as_uri()), self.original.resolve())

    def test_exact_parent_doi_required_after_quicksearch(self):
        client = bridge.LocalZotero()
        rows = [{'key': 'PARENT01', 'data': {'itemType': 'journalArticle', 'DOI': DOI}},
                {'key': 'OTHER001', 'data': {'itemType': 'journalArticle', 'DOI': DOI + 'x'}},
                {'key': 'ATTACH01', 'data': {'itemType': 'attachment', 'DOI': DOI}}]
        with patch.object(client, 'items', return_value=rows):
            self.assertEqual([r['key'] for r in client.find_papers(DOI)], ['PARENT01'])

    def test_pagination_and_duplicate_page_guard(self):
        client = bridge.LocalZotero()
        first = [{'key': f'{i:08d}'} for i in range(100)]
        with patch.object(client, 'request', side_effect=[(json.dumps(first).encode(), {}), (b'[]', {})]) as req:
            self.assertEqual(len(client.items('users/0/items/top')), 100)
            self.assertEqual(req.call_args_list[1].args[1]['start'], 100)
        with patch.object(client, 'request', return_value=(json.dumps(first).encode(), {})):
            with self.assertRaises(bridge.BridgeError):
                client.items('users/0/items/top')

    def test_failed_connection_gives_setup_instructions(self):
        client = bridge.LocalZotero()
        from urllib.error import URLError, HTTPError
        for error in (URLError('offline'), HTTPError('http://127.0.0.1', 403, 'denied', {}, None)):
            with patch.object(client.opener, 'open', side_effect=error):
                with self.assertRaisesRegex(bridge.BridgeError, '设置 → 高级'):
                    client.check()

    def test_local_api_does_not_follow_redirects(self):
        self.assertIsNone(bridge.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.org/secret'))
        client = bridge.LocalZotero()
        for path in ('https://example.org', '../secret', '/api/items', 'items?x=y'):
            with self.assertRaises(bridge.BridgeError):
                client.request(path)


if __name__ == '__main__':
    unittest.main()
