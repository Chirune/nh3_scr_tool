"""Offline protocol and provenance checks; no real API key or network traffic."""
import copy
import io
import json
import importlib.util
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from pypdf import PdfWriter
import app
import elsevier_api as api
import engine
import pdf_fetch as pdf
from publisher_api_ui import PublisherAPIWindow

KEY='TEST_KEY_NOT_A_REAL_CREDENTIAL_12345'
TOKEN='TEST_INSTITUTION_TOKEN_NOT_REAL_54321'
DOI='10.1016/j.synthetic.2026.000001'
TITLE='Synthetic copper catalyst ammonia reduction fixture'


def record():
    return {'id':'api-fixture','doi':DOI,'title':TITLE,'manual_decision':'target','effective_decision':'target',
        'screening':{'decision':'target'},'route':{'publisher_group':'Elsevier','access_hint':'test only',
        'official_entry':'https://doi.org/'+DOI},'fulltext_candidates':[]}


def pdf_bytes(doi=DOI,title=TITLE):
    writer=PdfWriter();writer.add_blank_page(width=300,height=300)
    writer.add_metadata({'/Title':title,'/doi':doi});stream=io.BytesIO();writer.write(stream);return stream.getvalue()


class Response(io.BytesIO):
    def __init__(self,body,headers=None):super().__init__(body);self.headers=headers or {}


class Opener:
    def __init__(self,*replies):self.replies=list(replies);self.calls=[]
    def open(self,request,timeout=None):
        self.calls.append(request)
        if not self.replies:raise AssertionError('Unexpected additional API request')
        item=self.replies.pop(0)
        if isinstance(item,Exception):raise item
        return item


def failure(code,headers=None):return HTTPError(api.API_ORIGIN+'/fixture',code,'fixture',headers or {},io.BytesIO(b'not read'))
def client(opener,token=TOKEN):return api.ElsevierClient(KEY,token,opener=opener,minimum_interval=0)


class ElsevierProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='publisher_api_offline_');self.folder=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_pdf_request_uses_headers_and_full_view_and_checks_identity(self):
        opener=Opener(Response(pdf_bytes(),{'X-RateLimit-Remaining':'17'}))
        active=client(opener);result=active.fetch_pdf(record(),self.folder)
        req=opener.calls[0]
        self.assertEqual(req.get_header('X-els-apikey'),KEY)
        self.assertEqual(req.get_header('X-els-insttoken'),TOKEN)
        self.assertEqual(req.get_header('Accept'),'application/pdf')
        self.assertIn('view=FULL',req.full_url)
        self.assertEqual(Path(result['local_path']).read_bytes(),pdf_bytes())
        self.assertEqual(result['doi_verified'],DOI)
        self.assertEqual(result['quota']['X-RateLimit-Remaining'],17)
        self.assertNotIn(KEY,req.full_url+repr(active)+json.dumps(result))
        self.assertNotIn(TOKEN,req.full_url+repr(active)+json.dumps(result))

    def test_missing_key_and_header_injection_are_rejected_locally(self):
        for key in ('','wrong\r\nHeader: secret','school password','学校账号'):
            with self.subTest(key=key),self.assertRaises(ValueError):api.ElsevierClient(key)

    def test_acs_is_not_routed_to_elsevier(self):
        opener=Opener();paper={**record(),'doi':'10.1021/test','route':{'publisher_group':'ACS'}}
        with self.assertRaises(api.APIError):client(opener).fetch_pdf(paper,self.folder)
        self.assertEqual(opener.calls,[])

    def test_401_disables_bad_key_until_reconfigured(self):
        opener=Opener(failure(401));active=client(opener)
        for _ in range(2):
            with self.assertRaises(api.APIError) as caught:active.fetch_pdf(record(),self.folder)
            self.assertEqual(caught.exception.status,'api_authentication')
        self.assertEqual(len(opener.calls),1)

    def test_403_is_permission_diagnostic_not_success(self):
        with self.assertRaises(api.APIError) as caught:client(Opener(failure(403))).fetch_pdf(record(),self.folder)
        self.assertEqual(caught.exception.status,'api_permission')
        self.assertFalse(list(self.folder.rglob('*.pdf')))

    def test_429_pauses_subsequent_requests_and_preserves_retry_information(self):
        opener=Opener(failure(429,{'Retry-After':'120'}));active=client(opener)
        for _ in range(2):
            with self.assertRaises(api.APIError) as caught:active.fetch_pdf(record(),self.folder)
            self.assertEqual(caught.exception.status,'api_rate_limit')
            self.assertGreater(caught.exception.retry_after_seconds,110)
        self.assertEqual(len(opener.calls),1)

    def test_redirect_to_official_cdn_does_not_forward_secrets(self):
        opener=Opener(failure(303,{'Location':'https://pdf.els-cdn.com/content/article.pdf?signature=fixture'}),Response(pdf_bytes()))
        with patch.object(api,'public_url',side_effect=lambda x:x):result=client(opener).fetch_pdf(record(),self.folder)
        self.assertEqual(result['status'],'downloaded')
        self.assertIsNone(opener.calls[1].get_header('X-els-apikey'))
        self.assertIsNone(opener.calls[1].get_header('X-els-insttoken'))
        self.assertNotIn('signature',json.dumps(result))

    def test_external_insecure_or_secret_bearing_redirect_is_refused(self):
        for url in ('https://evil.example/pdf','http://pdf.els-cdn.com/a.pdf',
                    'https://pdf.els-cdn.com/a.pdf?key='+KEY,'https://localhost/a.pdf'):
            opener=Opener(failure(303,{'Location':url}))
            with self.subTest(url=url),self.assertRaises(api.APIError):client(opener).fetch_pdf(record(),self.folder)
            self.assertEqual(len(opener.calls),1)

    def test_metadata_or_login_page_is_never_pdf(self):
        for body in (b'<html>Login</html>',b'<full-text-retrieval-response><coredata/></full-text-retrieval-response>'):
            with self.assertRaises(api.APIError):client(Opener(Response(body))).fetch_pdf(record(),self.folder)
        self.assertFalse(list(self.folder.rglob('*.pdf')))

    def test_wrong_article_is_not_attached(self):
        with self.assertRaises(pdf.FetchError):client(Opener(Response(pdf_bytes('10.1016/wrong')))).fetch_pdf(record(),self.folder)
        self.assertFalse(list(self.folder.rglob('*.pdf')))

    def test_cancelled_task_does_not_send_request(self):
        event=threading.Event();event.set();opener=Opener()
        with self.assertRaises(pdf.Stopped):client(opener).fetch_pdf(record(),self.folder,event)
        self.assertEqual(opener.calls,[])

    def test_network_exception_cannot_leak_key_in_diagnostic(self):
        opener=Opener(URLError('server echoed '+KEY+' '+TOKEN))
        with self.assertRaises(api.APIError) as caught:client(opener).fetch_pdf(record(),self.folder)
        self.assertEqual(caught.exception.status,'api_network')
        self.assertNotIn(KEY,str(caught.exception));self.assertNotIn(TOKEN,str(caught.exception))

    def test_content_length_limit_stops_download_before_writing(self):
        opener=Opener(Response(pdf_bytes(),{'Content-Length':str(pdf.MAX_PDF_BYTES+1)}))
        with self.assertRaises(api.APIError):client(opener).fetch_pdf(record(),self.folder)
        self.assertFalse(list(self.folder.rglob('*.pdf')))

    def test_same_document_can_be_checked_again_without_rewriting_file(self):
        active=client(Opener(Response(pdf_bytes()),Response(pdf_bytes())))
        first=active.fetch_pdf(record(),self.folder);path=Path(first['local_path']);before=path.stat().st_mtime_ns
        second=active.fetch_pdf(record(),self.folder)
        self.assertEqual(first['local_path'],second['local_path']);self.assertEqual(path.stat().st_mtime_ns,before)

    def test_batch_routes_eligible_paper_and_preserves_api_provenance_on_reuse(self):
        active=client(Opener(Response(pdf_bytes())));run={'run_dir':str(self.folder),'records':[record()]}
        with patch.object(pdf,'request_bytes',side_effect=AssertionError('Public fallback must not run')):
            pdf.acquire_retained(run,publisher_client=active)
            result=pdf.fetch_record(run['records'][0],self.folder,publisher_client=active)
        self.assertEqual(run['pdf_acquisition_summary']['downloaded'],1)
        self.assertEqual(result['status'],'available');self.assertEqual(result['acquisition_method'],'official_elsevier_api')
        for path in self.folder.rglob('*.json'):
            self.assertNotIn(KEY,path.read_text(encoding='utf8'));self.assertNotIn(TOKEN,path.read_text(encoding='utf8'))

    def test_batch_keeps_api_permission_reason_after_public_sources_fail(self):
        active=client(Opener(failure(403)))
        with patch.object(pdf,'request_bytes',side_effect=pdf.FetchError('network_error','test offline')):
            result=pdf.fetch_record(record(),self.folder,publisher_client=active)
        self.assertEqual(result['status'],'api_permission');self.assertIn('403',result['message'])

    def test_manual_exclusions_never_use_api(self):
        opener=Opener();run={'run_dir':str(self.folder),'records':[{**record(),'manual_decision':'non_target'}]}
        pdf.acquire_retained(run,publisher_client=client(opener));self.assertEqual(opener.calls,[])

    def test_failed_explicit_check_preserves_existing_pdf_and_review(self):
        path=self.folder/'existing.pdf';path.write_bytes(pdf_bytes())
        item=record();item.update(local_path=str(path),manual_note='keep existing decision',pdf_acquisition={'status':'available','message':'already verified'})
        run={'run_dir':str(self.folder),'records':[item]};before=copy.deepcopy(item['pdf_acquisition'])
        with patch.object(engine,'_save') as save:result=engine.check_elsevier_pdf(run,item['id'],client(Opener(failure(403))))
        self.assertEqual(item['pdf_acquisition'],before)
        self.assertEqual(item['local_path'],str(path));self.assertEqual(item['manual_note'],'keep existing decision')
        self.assertEqual(result['check']['status'],'api_permission');save.assert_called_once_with(run)

    def test_explicit_check_really_uses_api_even_if_pdf_already_exists(self):
        path=self.folder/'existing.pdf';path.write_bytes(pdf_bytes());item={**record(),'local_path':str(path)}
        run={'run_dir':str(self.folder),'records':[item]};opener=Opener(Response(pdf_bytes()))
        with patch.object(engine,'_save'):result=engine.check_elsevier_pdf(run,item['id'],client(opener))
        self.assertEqual(len(opener.calls),1);self.assertEqual(result['check']['status'],'downloaded')
        self.assertEqual(path.read_bytes(),pdf_bytes())

    def test_saved_api_pdf_enters_second_stage_with_same_doi_hash_and_source(self):
        run=engine._new_run('scr_ammonia',self.folder,'api_offline_fixture');run['records']=[record()]
        result=engine.check_elsevier_pdf(run,record()['id'],client(Opener(Response(pdf_bytes()))))
        run_path=Path(run['run_dir'])/'run.json'
        spec=importlib.util.spec_from_file_location('api_fixture_intake',Path(__file__).resolve().parents[1]/'intake.py')
        intake=importlib.util.module_from_spec(spec);spec.loader.exec_module(intake)
        received=intake.load_screened_input(run_path)
        self.assertEqual(len(received['papers']),1)
        paper=received['papers'][0]
        self.assertEqual(paper['doi'],DOI)
        self.assertEqual(paper['source_sha256'],result['check']['local_sha256'])
        self.assertIn('api.elsevier.com/content/article/doi/',paper['download_source_url'])
        stored=run_path.read_text(encoding='utf8')
        self.assertNotIn(KEY,stored);self.assertNotIn(TOKEN,stored)


class PublisherInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='api_ui_fixture_');self.root=tk.Tk();self.root.withdraw()
        self.owner=app.LiteratureApp(self.root,Path(self.temp.name),'scr_ammonia')
        self.owner._show_run({'run_dir':self.temp.name,'profile':'scr_ammonia','records':[record()]})
        self.dialog=PublisherAPIWindow(self.owner);self.dialog.window.withdraw()
        self.root.update_idletasks()
    def tearDown(self):
        self.root.update_idletasks()
        for callback in self.root.tk.call('after','info'):
            self.root.tk.call('after','cancel',callback)
        self.root.destroy();self.temp.cleanup()

    def test_apply_key_only_enables_session_without_network_or_disk_config(self):
        self.dialog.key.set(KEY);self.dialog.token.set(TOKEN)
        with patch.object(api,'build_opener') as factory:
            factory.return_value=Opener();self.assertTrue(self.dialog.apply())
            self.assertEqual(factory.return_value.calls,[])
        self.assertIsNotNone(self.owner.publisher_client)
        self.assertEqual(list(Path(self.temp.name).iterdir()),[])

    def test_download_button_passes_client_to_existing_batch_flow(self):
        active=client(Opener());self.owner.publisher_client=active
        with patch.object(self.owner,'_start_job') as start,patch.object(engine,'acquire_pdfs') as acquire:
            self.owner._open_figures();worker=start.call_args.args[1];worker()
            self.assertIs(acquire.call_args.kwargs['publisher_client'],active)

    def test_api_window_fields_remain_private_and_disable_clears_session(self):
        self.dialog.key.set(KEY);self.dialog.apply();self.dialog.disable()
        self.assertIsNone(self.owner.publisher_client);self.assertEqual(self.dialog.key.get(),'')
        def entries(widget):
            found=[]
            for child in widget.winfo_children():
                if isinstance(child,tk.ttk.Entry) and not isinstance(child,tk.ttk.Combobox):found.append(child)
                found.extend(entries(child))
            return found
        self.assertEqual(len(entries(self.dialog.window)),2)
        self.assertTrue(all(e.cget('show')=='*' for e in entries(self.dialog.window)))

    def test_diagnostic_display_handles_permission_error_without_overwriting_local_data(self):
        self.owner.publisher_client=client(Opener(failure(403)))
        with patch.object(engine,'_save'),patch.object(self.owner,'_start_job') as start:
            self.dialog.test_selected();worker,done=start.call_args.args[1:];payload=worker();done(payload)
        text=self.dialog.result.get('1.0','end')
        self.assertIn('403',text);self.assertNotIn(KEY,text)
        self.assertNotIn('local_path',self.owner.run['records'][0])

    def test_api_entry_and_test_button_fit_minimum_windows(self):
        self.root.attributes('-alpha',0);self.root.geometry('900x650+0+0');self.root.deiconify()
        self.dialog.window.attributes('-alpha',0);self.dialog.window.geometry('840x650+0+0');self.dialog.window.deiconify()
        self.root.update()
        for widget,window in ((self.owner.api_button,self.root),(self.dialog.test_button,self.dialog.window),(self.dialog.apply_button,self.dialog.window)):
            self.assertTrue(widget.winfo_ismapped())
            self.assertGreaterEqual(widget.winfo_width(),widget.winfo_reqwidth())
            self.assertLessEqual(widget.winfo_rootx()-window.winfo_rootx()+widget.winfo_width(),window.winfo_width())
            self.assertLessEqual(widget.winfo_rooty()-window.winfo_rooty()+widget.winfo_height(),window.winfo_height())


if __name__=='__main__':unittest.main()
