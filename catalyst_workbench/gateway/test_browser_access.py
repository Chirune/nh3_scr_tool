import copy
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from contextlib import nullcontext
from unittest.mock import Mock,patch

from pypdf import PdfWriter
import access_status
import browser_acquire as browser
import engine
import pdf_fetch

DOI='10.9999/browser.paper'
TITLE='Synthetic catalyst browser access experiment'


def pdf(doi=DOI):
    writer=PdfWriter();writer.add_blank_page(width=200,height=200)
    writer.add_metadata({'/Title':TITLE,'/doi':doi})
    stream=io.BytesIO();writer.write(stream);return stream.getvalue()


class BrowserAccessTests(unittest.TestCase):
    def test_http_codes_are_visible_even_when_nested_under_acquisition(self):
        record={'pdf_acquisition':{'status':'api_permission','publisher_api_check':{
            'http_status':403,'message':'Configuration insufficient','xml_attempt':{'http_status':401,'message':'No access'}}}}
        self.assertIn('HTTP 403/401',pdf_fetch.status_label(record))
        self.assertIn('HTTP 403',access_status.failure_text(record))
        self.assertIn('HTTP 401',access_status.failure_text(record))
        record['pdf_acquisition']['status']='available'
        self.assertEqual(pdf_fetch.status_label(record),'已有 PDF')

    def test_job_file_is_bound_only_to_exact_retained_record_and_reverified(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);job=root/'job';job.mkdir()
            record={'id':'paper','doi':DOI,'title':TITLE,'manual_decision':'target','effective_decision':'target'}
            run={'run_dir':str(root),'records':[record]}
            result=browser.save_pdf(pdf(),record,job,'browser_pdf');result.update(doi=DOI)
            (job/'result.json').write_text(json.dumps(result),encoding='utf-8')
            with patch.object(engine,'_save'):
                browser.receive_result(run,'paper',job)
            self.assertEqual(record['pdf_acquisition']['acquisition_method'],'browser_pdf')
            self.assertEqual(record['pdf_acquisition']['page_basis'] if 'page_basis' in record['pdf_acquisition'] else 'publisher_pdf','publisher_pdf')
            self.assertEqual(Path(record['local_path']).read_bytes(),pdf())
            record['manual_decision']='non_target'
            with self.assertRaises(ValueError):browser.receive_result(run,'paper',job)

    def test_wrong_doi_or_path_outside_job_does_not_replace_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);job=root/'job';job.mkdir()
            record={'id':'paper','doi':DOI,'title':TITLE,'effective_decision':'target'}
            run={'run_dir':str(root),'records':[record]};before=copy.deepcopy(record)
            source=root/'outside.pdf';source.write_bytes(pdf())
            for updates in ({'doi':'10.9999/wrong'},{'doi':DOI}):
                result={'local_path':str(source),'local_sha256':'wrong',**updates}
                (job/'result.json').write_text(json.dumps(result),encoding='utf-8')
                with self.assertRaises(ValueError):browser.receive_result(run,'paper',job)
                self.assertEqual(record,before)

    def test_browser_does_not_accept_login_or_wrong_pdf_as_fulltext(self):
        with tempfile.TemporaryDirectory() as folder:
            for data in (b'<html>Sign in</html>',pdf('10.9999/wrong')):
                with self.assertRaises(pdf_fetch.FetchError):
                    browser.save_pdf(data,{'doi':DOI,'title':TITLE},folder,'browser_pdf')
            self.assertFalse(list(Path(folder).glob('*.pdf')))

    def test_links_cannot_open_local_files_or_private_services(self):
        for value in ('file:///C:/secret.pdf','http://127.0.0.1/paper','http://localhost/paper','https://example.org:8443/paper','https://user:pass@example.org/paper'):
            with self.assertRaises(ValueError):browser.web_url(value)
        self.assertEqual(browser.web_url('https://www.sciencedirect.com/science/article/pii/test'),'https://www.sciencedirect.com/science/article/pii/test')


class BrowserCollectorTests(unittest.TestCase):
    def test_authorized_worker_waits_for_login_then_collects_exact_article(self):
        from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeoutError
        with tempfile.TemporaryDirectory() as folder,sync_playwright() as actual:
            root=Path(folder)
            job={'authorized':True,'url':'https://browser-fixture.example/login',
                 'record':{'id':'fixture','doi':DOI,'title':TITLE}}
            (root/'job.json').write_text(json.dumps(job),encoding='utf-8')
            fixture=f'<meta name="citation_pdf_url" content="https://browser-fixture.example/paper.pdf"><meta name="citation_doi" content="{DOI}"><meta name="citation_title" content="{TITLE}"><article><h1>{TITLE}</h1><p>DOI: {DOI}</p><h2>Introduction</h2><p>'+('Catalyst measurements were performed. '*120)+'</p><h2>Results</h2><p>NO conversion was 90%.</p></article>'
            def launch(*args,**kwargs):
                context=actual.chromium.launch_persistent_context(*args,**kwargs)
                def route(request):
                    body=fixture if request.request.url.endswith('/paper') else '<h1>Sign in</h1><form><input type="password" value="PASSWORD_SENTINEL"></form><script>setTimeout(()=>location.href="/paper",350)</script>'
                    request.fulfill(status=200,content_type='text/html',body=body)
                context.route('https://browser-fixture.example/**',route)
                return context
            fake=SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
            with patch('playwright.sync_api.sync_playwright',return_value=nullcontext(fake)),patch('playwright.sync_api.APIRequestContext.get',side_effect=BrowserTimeoutError('synthetic PDF timeout')):
                browser.run_job(root/'job.json',profile=root/'profile',headless=True)
            status=json.loads((root/'status.json').read_text(encoding='utf-8'))
            result=json.loads((root/'result.json').read_text(encoding='utf-8'))
            self.assertEqual(status['state'],'ready')
            self.assertEqual(result['acquisition_method'],'browser_visible_text')
            self.assertEqual(result['page_basis'],'browser_reflowed_text')
            self.assertEqual(result['doi_verified'],DOI)
            observations=json.loads((root/'observations.json').read_text(encoding='utf-8'))
            self.assertEqual(observations['pdf_request_errors'],[{'error_type':'TimeoutError'}])
            self.assertNotIn('PASSWORD_SENTINEL',(root/'capture.json').read_text(encoding='utf-8'))

    def test_real_chrome_collector_distinguishes_abstract_from_visible_article(self):
        from playwright.sync_api import sync_playwright
        meta=f'<meta name="citation_doi" content="{DOI}"><meta name="citation_title" content="{TITLE}">'
        with sync_playwright() as pw:
            chrome=pw.chromium.launch(channel='chrome',headless=True)
            try:
                page=chrome.new_page();page.goto('about:blank')
                page.set_content(meta+'<h1>'+TITLE+'</h1><section><h2>Abstract</h2><p>'+('A catalyst study. '*20)+'</p></section>')
                result=page.evaluate(browser.collector_source())
                self.assertEqual(result['doi'],DOI);self.assertFalse(result['fulltext_available'])
                page.set_content(meta+'<article><h1>'+TITLE+'</h1><h2>Introduction</h2><p>'+('Catalytic activity was investigated. '*120)+'</p><div style="display:none">HIDDEN_PRIVATE_SENTINEL</div><h2>Results</h2><p>NO conversion was 90%.</p><table><tr><td>Cu</td><td>1.4</td></tr></table></article>')
                result=page.evaluate(browser.collector_source())
                self.assertTrue(result['fulltext_available']);self.assertEqual(result['table_count'],1)
                self.assertNotIn('HIDDEN_PRIVATE_SENTINEL',result['fulltext_html'])
                self.assertNotIn('HIDDEN_PRIVATE_SENTINEL',result['fulltext_text'])
                self.assertIn('NO conversion was 90%',result['fulltext_text'])
                with tempfile.TemporaryDirectory() as folder:
                    printed=chrome.new_page()
                    printed.set_content('<h1>'+TITLE+'</h1><p>DOI: '+DOI+'</p><p>NO conversion was 90%.</p>')
                    checked=browser.save_pdf(printed.pdf(),{'doi':DOI,'title':TITLE},folder,'browser_visible_text')
                    checked.update(doi=DOI,page_basis='browser_reflowed_text')
                    record={'id':'browser-case','doi':DOI,'title':TITLE,'effective_decision':'target'}
                    with patch.object(engine,'_save'):
                        (Path(folder)/'result.json').write_text(json.dumps(checked),encoding='utf-8')
                        browser.receive_result({'run_dir':folder,'records':[record]},record['id'],folder)
                    self.assertEqual(record['pdf_acquisition']['page_basis'],'browser_reflowed_text')
                    with patch.object(pdf_fetch,'request_bytes') as network:
                        reused=pdf_fetch.fetch_record(record,folder)
                    self.assertEqual(reused['page_basis'],'browser_reflowed_text');network.assert_not_called()
                    printed.close()
                page.set_content('<h1>Sign in</h1><form><input type="password" value="PASSWORD_SENTINEL"></form>')
                result=page.evaluate(browser.collector_source())
                self.assertFalse(result['doi']);self.assertFalse(result['fulltext_available'])
                self.assertNotIn('PASSWORD_SENTINEL',json.dumps(result))
            finally:chrome.close()


if __name__=='__main__':unittest.main()
