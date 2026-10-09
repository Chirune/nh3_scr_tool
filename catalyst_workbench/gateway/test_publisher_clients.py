"""Offline protocol, mixed-publisher routing, and stage-handoff regressions."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import app
import engine
import pdf_fetch
import publisher_clients as pc
from publisher_api_ui import PublisherAPIWindow, PROVIDERS
from test_elsevier_api import Opener, Response, pdf_bytes, record, KEY, TOKEN

WILEY_DOI = '10.1002/synthetic.fixture.2026'
SPRINGER_DOI = '10.1007/synthetic-fixture-2026'
PDF_URL = 'https://link.springer.com/content/pdf/' + SPRINGER_DOI + '.pdf'


def paper(provider):
    groups = {'wiley': 'Wiley', 'springer': 'Springer Nature', 'elsevier': 'Elsevier'}
    row = record()
    row.update(id=provider + '-fixture', doi={'wiley': WILEY_DOI, 'springer': SPRINGER_DOI}.get(provider, row['doi']))
    row['route'].update(publisher_group=groups[provider], official_entry='https://doi.org/' + row['doi'])
    return row


def api_failure(code, url='https://api.wiley.com/test', headers=None):
    return HTTPError(url, code, 'synthetic', headers or {}, io.BytesIO(b'never log response credentials'))


def oa_reply(doi=SPRINGER_DOI, links=None, open_access=True):
    return Response(json.dumps({'records': [{'doi': doi, 'openaccess': open_access,
        'url': links if links is not None else [{'format': 'pdf', 'value': PDF_URL}]}]}).encode())


class PublisherProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='multi_publisher_offline_')
        self.folder = Path(self.temp.name)
        self.dns = patch.object(pc, 'public_url', side_effect=lambda u: u)
        self.dns.start()
    def tearDown(self):
        self.dns.stop()
        self.temp.cleanup()

    def wiley(self, *responses):
        opener = Opener(*responses)
        return pc.WileyClient(KEY, opener=opener, minimum_interval=0), opener

    def springer(self, *responses):
        opener = Opener(*responses)
        return pc.SpringerOAClient(KEY, opener=opener, minimum_interval=0), opener

    def test_wiley_uses_encoded_doi_and_token_header_with_verified_pdf(self):
        active, opener = self.wiley(Response(pdf_bytes(WILEY_DOI)))
        result = active.fetch_pdf(paper('wiley'), self.folder)
        req = opener.calls[0]
        self.assertEqual(req.get_header('Wiley-tdm-client-token'), KEY)
        self.assertIn('10.1002%2F', req.full_url)
        self.assertNotIn(KEY, req.full_url + json.dumps(result) + repr(active))
        self.assertEqual(result['doi_verified'], WILEY_DOI)
        self.assertEqual(result['access_basis'], 'wiley_tdm_api')
        self.assertEqual(Path(result['local_path']).read_bytes(), pdf_bytes(WILEY_DOI))

    def test_wiley_redirect_strips_credential_on_binary_host(self):
        active, opener = self.wiley(api_failure(302, headers={'Location': 'https://onlinelibrary.wiley.com/doi/pdf/test'}),
                                    Response(pdf_bytes(WILEY_DOI)))
        active.fetch_pdf(paper('wiley'), self.folder)
        self.assertEqual(opener.calls[0].get_header('Wiley-tdm-client-token'), KEY)
        self.assertIsNone(opener.calls[1].get_header('Wiley-tdm-client-token'))

    def test_wiley_rejects_untrusted_and_secret_bearing_redirects(self):
        for target in ('http://api.wiley.com/file', 'https://wiley.com.evil.test/file',
                       'https://127.0.0.1/file', 'https://onlinelibrary.wiley.com/file?key=' + KEY):
            with self.subTest(target=target):
                active, opener = self.wiley(api_failure(302, headers={'Location': target}))
                with self.assertRaises(pc.APIError):
                    active.fetch_pdf(paper('wiley'), self.folder)
                self.assertEqual(len(opener.calls), 1)

    def test_wiley_403_disables_token_but_404_keeps_article_access_diagnostic(self):
        active, opener = self.wiley(api_failure(403))
        for _ in range(2):
            with self.assertRaises(pc.APIError) as caught:
                active.fetch_pdf(paper('wiley'), self.folder)
            self.assertEqual(caught.exception.status, 'api_authentication')
        self.assertEqual(len(opener.calls), 1)
        active, _ = self.wiley(api_failure(404))
        with self.assertRaises(pc.APIError) as caught:
            active.fetch_pdf(paper('wiley'), self.folder)
        self.assertEqual(caught.exception.status, 'api_permission')
        self.assertIn('机构网络', str(caught.exception))

    def test_rate_limit_prevents_repeated_network_requests(self):
        active, opener = self.wiley(api_failure(429, headers={'Retry-After': '900'}))
        for _ in range(2):
            with self.assertRaises(pc.APIError) as caught:
                active.fetch_pdf(paper('wiley'), self.folder)
            self.assertEqual(caught.exception.status, 'api_rate_limit')
        self.assertEqual(len(opener.calls), 1)
        self.assertGreater(caught.exception.retry_after_seconds, 850)

    def test_cancelled_request_and_pacing_wait_do_not_hit_network(self):
        active, opener = self.wiley()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(pdf_fetch.Stopped):
            active.fetch_pdf(paper('wiley'), self.folder, stop)
        self.assertEqual(opener.calls, [])
        stop.clear()
        active._minimum_interval = 10.1
        active._last_started = time.monotonic()
        timer = threading.Timer(.04, stop.set)
        timer.start()
        try:
            with self.assertRaises(pdf_fetch.Stopped):
                active.fetch_pdf(paper('wiley'), self.folder, stop)
        finally:
            timer.join()
        self.assertEqual(opener.calls, [])

    def test_concurrent_requests_are_paced_by_shared_provider(self):
        moments = []
        class TimedOpener:
            def open(self, request, timeout=None):
                moments.append(time.monotonic())
                return Response(b'ok')
        active = pc.WileyClient(KEY, opener=TimedOpener(), minimum_interval=.07)
        workers = [threading.Thread(target=active._request, args=('https://api.wiley.com/test', 'application/pdf', 100)) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(2)
        self.assertEqual(len(moments), 2)
        self.assertGreaterEqual(moments[1] - moments[0], .065)
        self.assertGreaterEqual(pc.WileyClient(KEY)._minimum_interval, 10.)

    def test_html_and_truncated_responses_are_never_saved_as_pdf(self):
        for response in (Response(b'<html>login</html>'), Response(pdf_bytes(WILEY_DOI), {'Content-Length': '999999'})):
            active, _ = self.wiley(response)
            with self.assertRaises(pc.APIError):
                active.fetch_pdf(paper('wiley'), self.folder)
            self.assertEqual(list(self.folder.rglob('*.pdf')), [])

    def test_response_size_limit_and_network_errors_do_not_expose_secrets(self):
        for response in (Response(b'', {'Content-Length': str(pdf_fetch.MAX_PDF_BYTES + 1)}),
                         URLError('failed URL https://api.wiley.com/?key=' + KEY)):
            active, _ = self.wiley(response)
            with self.assertRaises(pc.APIError) as caught:
                active.fetch_pdf(paper('wiley'), self.folder)
            self.assertNotIn(KEY, str(caught.exception))

    def test_wrong_pdf_identity_rejected(self):
        active, _ = self.wiley(Response(pdf_bytes('10.1002/wrong-paper')))
        with self.assertRaises(pdf_fetch.FetchError):
            active.fetch_pdf(paper('wiley'), self.folder)
        self.assertEqual(list(self.folder.rglob('*.pdf')), [])

    def test_springer_queries_oa_doi_then_downloads_advertised_pdf_without_key(self):
        active, opener = self.springer(oa_reply(), Response(pdf_bytes(SPRINGER_DOI)))
        result = active.fetch_pdf(paper('springer'), self.folder)
        params = parse_qs(urlsplit(opener.calls[0].full_url).query)
        self.assertEqual(params['q'], ['doi:' + SPRINGER_DOI])
        self.assertEqual(params['api_key'], [KEY])
        self.assertEqual(opener.calls[1].full_url, PDF_URL)
        self.assertNotIn(KEY, str(opener.calls[1].headers) + opener.calls[1].full_url)
        self.assertNotIn(KEY, json.dumps(result) + repr(active))
        self.assertEqual(result['access_basis'], 'springer_oa_api_pdf_link')
        self.assertTrue(result['open_access_verified'])

    def test_springer_empty_oa_result_is_not_permission_failure_or_pdf_success(self):
        active, opener = self.springer(Response(b'{"records":[]}'))
        with self.assertRaises(pc.APIError) as caught:
            active.fetch_pdf(paper('springer'), self.folder)
        self.assertEqual(caught.exception.status, 'api_oa_not_found')
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_springer_record_doi_must_match_query(self):
        active, opener = self.springer(oa_reply(doi='10.1007/wrong'))
        with self.assertRaises(pc.APIError) as caught:
            active.fetch_pdf(paper('springer'), self.folder)
        self.assertEqual(caught.exception.status, 'identity_unverified')
        self.assertEqual(len(opener.calls), 1)

    def test_springer_non_oa_record_and_missing_pdf_are_not_saved(self):
        for reply, status in ((oa_reply(open_access=False), 'api_subscription_required'), (oa_reply(links=[]), 'api_pdf_unavailable')):
            active, opener = self.springer(reply)
            with self.assertRaises(pc.APIError) as caught:
                active.fetch_pdf(paper('springer'), self.folder)
            self.assertEqual(caught.exception.status, status)
            self.assertEqual(len(opener.calls), 1)

    def test_springer_identifier_field_and_http_link_upgrade(self):
        payload = {'records': [{'identifier': 'doi:' + SPRINGER_DOI,
                   'url': [{'format': 'pdf', 'value': PDF_URL.replace('https:', 'http:')}]}]}
        active, opener = self.springer(Response(json.dumps(payload).encode()), Response(pdf_bytes(SPRINGER_DOI)))
        active.fetch_pdf(paper('springer'), self.folder)
        self.assertEqual(opener.calls[1].full_url, PDF_URL)

    def test_springer_can_follow_pdf_advertised_on_oa_article_page(self):
        links = [{'format': 'html', 'value': 'https://www.nature.com/articles/synthetic'}]
        html = ('<meta name="citation_pdf_url" content="' + PDF_URL + '">').encode()
        active, opener = self.springer(oa_reply(links=links), Response(html), Response(pdf_bytes(SPRINGER_DOI)))
        result = active.fetch_pdf(paper('springer'), self.folder)
        self.assertEqual(result['status'], 'downloaded')
        self.assertEqual(len(opener.calls), 3)

    def test_springer_rejects_private_or_secret_bearing_asset_links(self):
        for link in ('https://127.0.0.1/file.pdf', PDF_URL + '?api_key=' + KEY,
                     'https://springer.com.evil.test/file.pdf', 'https://[invalid/file.pdf'):
            active, opener = self.springer(oa_reply(links=[{'format': 'pdf', 'value': link}]))
            with self.assertRaises(pc.APIError):
                active.fetch_pdf(paper('springer'), self.folder)
            self.assertEqual(len(opener.calls), 1)

    def test_springer_error_does_not_print_key_from_request_url(self):
        active, _ = self.springer(URLError('https://api.springernature.com/?api_key=' + KEY))
        with self.assertRaises(pc.APIError) as caught:
            active.fetch_pdf(paper('springer'), self.folder)
        self.assertNotIn(KEY, json.dumps(caught.exception.public_details()))

    def test_springer_oa_404_then_subscription_metadata_explains_scope_without_downloading(self):
        active, opener = self.springer(api_failure(404), oa_reply(open_access=False))
        with self.assertRaises(pc.APIError) as caught:
            active.fetch_pdf(paper('springer'), self.folder)
        details = caught.exception.public_details()
        self.assertEqual(details['status'], 'api_subscription_required')
        self.assertEqual(details['article_access'], 'subscription')
        self.assertEqual(details['key_check'], 'metadata_lookup_responded')
        self.assertIn('学校登录', details['message'])
        self.assertEqual(urlsplit(opener.calls[1].full_url).path, '/meta/v2/json')
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertNotIn(KEY, json.dumps(details))

    def test_springer_oa_404_can_follow_only_explicitly_open_exact_metadata_record(self):
        active, opener = self.springer(api_failure(404), oa_reply(), Response(pdf_bytes(SPRINGER_DOI)))
        result = active.fetch_pdf(paper('springer'), self.folder)
        self.assertEqual(result['status'], 'downloaded')
        self.assertEqual(result['lookup_stage'], 'meta/v2')
        self.assertTrue(result['open_access_verified'])
        self.assertEqual(opener.calls[2].full_url, PDF_URL)
        self.assertNotIn(KEY, opener.calls[2].full_url + json.dumps(result))

    def test_springer_metadata_missing_open_flag_is_not_assumed_open(self):
        row = {'records': [{'doi': SPRINGER_DOI, 'url': [{'format': 'pdf', 'value': PDF_URL}]}]}
        active, opener = self.springer(api_failure(404), Response(json.dumps(row).encode()))
        with self.assertRaises(pc.APIError) as caught:
            active.fetch_pdf(paper('springer'), self.folder)
        self.assertEqual(caught.exception.status, 'api_access_unknown')
        self.assertEqual(len(opener.calls), 2)

    def test_springer_metadata_wrong_doi_does_not_use_pdf(self):
        active, opener = self.springer(api_failure(404), oa_reply(doi='10.1007/wrong'))
        with self.assertRaises(pc.APIError) as caught:
            active.fetch_pdf(paper('springer'), self.folder)
        self.assertEqual(caught.exception.status, 'identity_unverified')
        self.assertEqual(len(opener.calls), 2)

    def test_springer_ambiguous_open_flags_do_not_grant_download(self):
        for rows in ([{'doi': SPRINGER_DOI, 'openaccess': True, 'openAccess': False}],
                     [{'doi': SPRINGER_DOI, 'openaccess': True}, {'doi': SPRINGER_DOI, 'openaccess': False}]):
            active, opener = self.springer(Response(json.dumps({'records': rows}).encode()))
            with self.assertRaises(pc.APIError) as caught:
                active.fetch_pdf(paper('springer'), self.folder)
            self.assertEqual(caught.exception.status, 'api_access_unknown')
            self.assertEqual(len(opener.calls), 1)

    def test_springer_two_404s_do_not_claim_bad_key_or_missing_article(self):
        active, opener = self.springer(api_failure(404), api_failure(404))
        with self.assertRaises(pc.APIError) as caught:
            active.fetch_pdf(paper('springer'), self.folder)
        self.assertEqual(caught.exception.status, 'api_lookup_unresolved')
        self.assertEqual(caught.exception.public_details()['key_check'], 'not_determined')
        self.assertEqual(len(opener.calls), 2)

    def test_springer_no_fallback_when_original_request_is_401_or_limited(self):
        for code, status in ((401, 'api_authentication'), (429, 'api_rate_limit')):
            active, opener = self.springer(api_failure(code))
            with self.assertRaises(pc.APIError) as caught:
                active.fetch_pdf(paper('springer'), self.folder)
            self.assertEqual(caught.exception.status, status)
            self.assertEqual(len(opener.calls), 1)

    def test_springer_metadata_failure_preserves_authentication_and_rate_limit(self):
        for code, status in ((401, 'api_authentication'), (403, 'api_permission'), (429, 'api_rate_limit')):
            active, opener = self.springer(api_failure(404), api_failure(code))
            with self.assertRaises(pc.APIError) as caught:
                active.fetch_pdf(paper('springer'), self.folder)
            self.assertEqual(caught.exception.status, status)
            self.assertEqual(caught.exception.public_details()['lookup_stage'], 'metadata_after_oa_404')
            self.assertEqual(len(opener.calls), 2)

    def test_springer_pdf_404_distinct_from_lookup_404(self):
        active, opener = self.springer(oa_reply(), api_failure(404, url=PDF_URL))
        with self.assertRaises(pc.APIError) as caught:
            active.fetch_pdf(paper('springer'), self.folder)
        info = caught.exception.public_details()
        self.assertEqual(info['status'], 'api_pdf_unavailable')
        self.assertEqual(info['lookup_stage'], 'public_pdf_or_page')
        self.assertEqual(info['article_access'], 'open_access')
        self.assertEqual(len(opener.calls), 2)

    def test_springer_key_probe_never_downloads_or_creates_records(self):
        active, opener = self.springer(oa_reply(doi=pc.SPRINGER_PROBE_DOI))
        result = active.check_access()
        self.assertEqual(result['status'], 'api_probe_ok')
        self.assertFalse(result['pdf_downloaded'])
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertNotIn(KEY, json.dumps(result))

    def test_springer_key_probe_metadata_success_does_not_claim_fulltext_access(self):
        active, opener = self.springer(api_failure(404), oa_reply(doi=pc.SPRINGER_PROBE_DOI))
        result = active.check_access()
        self.assertEqual(result['status'], 'api_metadata_only')
        self.assertEqual(result['key_check'], 'metadata_lookup_responded')
        self.assertIn('尚不能验证', result['message'])
        self.assertFalse(result['pdf_downloaded'])
        self.assertEqual(len(opener.calls), 2)

    def test_mixed_publisher_batch_selects_only_matching_configured_client(self):
        wiley, wiley_net = self.wiley(Response(pdf_bytes(WILEY_DOI)))
        springer, springer_net = self.springer(oa_reply(), Response(pdf_bytes(SPRINGER_DOI)))
        registry = pc.PublisherClients()
        registry.clients.update(wiley=wiley, springer=springer)
        run = {'run_dir': str(self.folder), 'records': [paper('springer'), paper('wiley')]}
        with patch.object(pdf_fetch, 'request_bytes', side_effect=AssertionError('Unexpected public fallback')):
            result = pdf_fetch.acquire_retained(run, publisher_client=registry)
        self.assertEqual(run['pdf_acquisition_summary']['downloaded'], 2)
        self.assertEqual(len(wiley_net.calls), 1)
        self.assertEqual(len(springer_net.calls), 2)
        self.assertNotIn(KEY, (self.folder / 'PDF获取记录.json').read_text(encoding='utf8'))
        for row in run['records']:
            cached = pdf_fetch.fetch_record(row, self.folder, publisher_client=registry)
            self.assertEqual(cached['status'], 'available')
            self.assertEqual(cached['publisher_api_provider'], pc.provider_for(row))
        self.assertEqual(len(wiley_net.calls), 1)
        self.assertEqual(len(springer_net.calls), 2)

    def test_other_publishers_do_not_receive_any_configured_credentials(self):
        wiley, opener = self.wiley()
        registry = pc.PublisherClients()
        registry.clients['wiley'] = wiley
        row = paper('wiley')
        row['route']['publisher_group'] = 'ACS'
        self.assertIsNone(pc.select_client(registry, row))
        with self.assertRaises(pc.APIError):
            wiley.fetch_pdf(row, self.folder)
        self.assertEqual(opener.calls, [])

    def test_failed_api_does_not_overwrite_existing_local_pdf_or_human_decision(self):
        active, _ = self.wiley(api_failure(404))
        row = paper('wiley')
        existing = self.folder / 'prior.pdf'
        existing.write_bytes(pdf_bytes(WILEY_DOI))
        row['local_path'] = str(existing)
        run = {'run_dir': str(self.folder), 'records': [row]}
        with patch.object(engine, '_save'):
            result = engine.check_publisher_pdf(run, row['id'], active)
        self.assertEqual(result['check']['status'], 'api_permission')
        self.assertEqual(row['local_path'], str(existing))
        self.assertEqual(row['manual_decision'], 'target')
        self.assertEqual(existing.read_bytes(), pdf_bytes(WILEY_DOI))

    def test_new_api_pdf_passes_into_module2_with_correct_source_identity(self):
        active, _ = self.springer(oa_reply(), Response(pdf_bytes(SPRINGER_DOI)))
        run = engine._new_run('scr_ammonia', self.folder, 'multi_api_offline')
        row = paper('springer')
        run['records'] = [row]
        result = engine.check_publisher_pdf(run, row['id'], active)
        spec = importlib.util.spec_from_file_location('multi_api_intake', Path(__file__).resolve().parents[1] / 'intake.py')
        intake = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(intake)
        incoming = intake.load_screened_input(Path(run['run_dir']) / 'run.json')['papers']
        self.assertEqual(len(incoming), 1)
        self.assertEqual(incoming[0]['doi'], SPRINGER_DOI)
        self.assertEqual(incoming[0]['source_sha256'], result['check']['local_sha256'])
        self.assertEqual(incoming[0]['download_source_url'], PDF_URL)
        self.assertNotIn(KEY, (Path(run['run_dir']) / 'run.json').read_text(encoding='utf8'))


class MultiPublisherInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='multi_api_ui_')
        self.root = tk.Tk()
        self.root.withdraw()
        self.owner = app.LiteratureApp(self.root, Path(self.temp.name), 'scr_ammonia')
        self.owner._show_run({'run_dir': self.temp.name, 'profile': 'scr_ammonia', 'records': [paper('wiley')]})
        self.dialog = PublisherAPIWindow(self.owner)
        self.dialog.window.withdraw()
        self.root.update_idletasks()
    def tearDown(self):
        for callback in self.root.tk.call('after', 'info'):
            self.root.tk.call('after', 'cancel', callback)
        self.root.destroy()
        self.temp.cleanup()
    def switch(self, provider):
        self.dialog.provider.set(PROVIDERS[provider]['label'])
        self.dialog.change_provider()

    def test_each_provider_keeps_independent_memory_credentials(self):
        self.assertEqual(self.dialog.provider_id, 'wiley')
        self.dialog.key.set(KEY)
        self.dialog.apply()
        wiley = self.owner.publisher_client.clients['wiley']
        self.switch('springer')
        self.assertEqual(self.dialog.key.get(), '')
        self.dialog.key.set(TOKEN)
        self.dialog.apply()
        self.assertIs(self.owner.publisher_client.clients['wiley'], wiley)
        self.dialog.disable()
        self.assertEqual(set(self.owner.publisher_client.clients), {'wiley'})
        self.switch('wiley')
        self.assertEqual(self.dialog.key.get(), KEY)
        self.dialog.disable()
        self.assertIsNone(self.owner.publisher_client)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_reapplying_same_token_does_not_reset_rate_limit(self):
        self.dialog.key.set(KEY)
        self.dialog.apply()
        active = self.owner.publisher_client.clients['wiley']
        active._blocked_until = time.monotonic() + 100
        self.dialog.apply()
        self.assertIs(self.owner.publisher_client.clients['wiley'], active)
        self.assertGreater(active._blocked_until, time.monotonic() + 90)

    def test_wrong_publisher_selection_disables_validation_with_explanation(self):
        self.switch('springer')
        self.assertEqual(str(self.dialog.test_button.cget('state')), 'disabled')
        self.assertIn('Wiley', self.dialog.selected.get())
        self.dialog.test_selected()
        self.assertIn('对应', self.dialog.result.get('1.0', 'end'))

    def test_dialog_close_clears_fields_but_session_can_still_download(self):
        self.dialog.key.set(KEY)
        self.dialog.apply()
        active = self.owner.publisher_client.clients['wiley']
        self.dialog.close()
        self.assertEqual(self.dialog.key.get(), '')
        self.assertEqual(self.dialog.drafts, {})
        self.assertIs(self.owner.publisher_client.for_record(paper('wiley')), active)

    def test_all_publishers_main_controls_fit_minimum_window(self):
        self.dialog.window.attributes('-alpha', 0)
        self.dialog.window.geometry('840x650+0+0')
        self.dialog.window.deiconify()
        for provider in PROVIDERS:
            self.switch(provider)
            self.root.update()
            controls = [self.dialog.provider_box, self.dialog.apply_button, self.dialog.test_button,
                        self.dialog.article_button, self.dialog.school_button]
            if provider == 'springer': controls.append(self.dialog.probe_button)
            for control in controls:
                self.assertTrue(control.winfo_ismapped())
                self.assertLessEqual(control.winfo_rooty() - self.dialog.window.winfo_rooty() + control.winfo_height(), 650)
                self.assertLessEqual(control.winfo_rootx() - self.dialog.window.winfo_rootx() + control.winfo_width(), 840)

    def test_key_probe_does_not_alter_selected_paper_or_run(self):
        import copy
        self.switch('springer')
        self.dialog.key.set(KEY); self.dialog.apply()
        client = self.owner.publisher_client.clients['springer']
        previous = copy.deepcopy(self.owner.run)
        def run_now(label, work, done): done(work())
        with patch.object(client, 'check_access', return_value={'message': '开放查询通过；没有下载PDF。'}) as probe, \
             patch.object(self.owner, '_start_job', side_effect=run_now):
            self.dialog.check_access()
        probe.assert_called_once()
        self.assertEqual(self.owner.run, previous)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        self.assertIn('没有下载PDF', self.dialog.result.get('1.0', 'end'))

    def test_school_button_opens_existing_receiver_without_saving_passwords(self):
        with patch.object(self.owner, '_open_zotero') as opened:
            self.dialog.open_school()
            opened.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
