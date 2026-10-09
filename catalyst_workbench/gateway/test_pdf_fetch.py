"""Acquisition must respect screening, verify identity and hand off actual files."""
import hashlib,io,json,threading,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock,patch
from pypdf import PdfWriter
import pdf_fetch as pdf
import app,engine

TITLE='Copper catalyst ammonia reduction research'
DOI='10.9999/test.paper'

def pdf_bytes(doi=DOI,title=TITLE):
    writer=PdfWriter();writer.add_blank_page(width=200,height=200)
    writer.add_metadata({'/Title':title,'/doi':doi});out=io.BytesIO();writer.write(out);return out.getvalue()


def record():
    return {'id':'r1','doi':DOI,'title':TITLE,'manual_decision':'target','effective_decision':'target',
            'fulltext_candidates':[{'url':'https://example.org/article.pdf','format':'application/pdf'}]}


class AcquisitionTests(unittest.TestCase):
    def test_pdf_is_verified_saved_and_attached_without_local_import(self):
        with TemporaryDirectory() as folder:
            data=pdf_bytes();run={'run_dir':folder,'records':[record()]};writes=[]
            with patch.object(pdf,'request_bytes',return_value=(data,'https://example.org/article.pdf','application/pdf')) as network:
                result=pdf.acquire_retained(run,persist=lambda r:writes.append(r['records'][0].get('local_path')))
            self.assertEqual(result['pdf_acquisition_summary']['ready'],1)
            self.assertEqual(result['pdf_acquisition_summary']['downloaded'],1)
            self.assertEqual(Path(run['records'][0]['local_path']).read_bytes(),data)
            self.assertEqual(run['records'][0]['local_sha256'],hashlib.sha256(data).hexdigest())
            self.assertTrue(writes and all(writes));network.assert_called_once()

    def test_manual_exclusion_and_pending_review_are_not_downloaded(self):
        with TemporaryDirectory() as folder:
            excluded={**record(),'manual_decision':'non_target'}
            review={**record(),'id':'r2','manual_decision':'review'}
            run={'run_dir':folder,'records':[excluded,review]}
            with patch.object(pdf,'request_bytes') as network:pdf.acquire_retained(run)
            network.assert_not_called();self.assertEqual(run['pdf_acquisition_summary']['selected'],0)

    def test_repeat_reuses_checked_file_without_network(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'a.pdf';path.write_bytes(pdf_bytes());r=record()
            r.update(local_path=str(path),local_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            with patch.object(pdf,'request_bytes') as network:result=pdf.fetch_record(r,folder)
            network.assert_not_called();self.assertEqual(result['status'],'available')

    def test_changed_existing_file_is_preserved_and_not_silently_replaced(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'a.pdf';path.write_bytes(pdf_bytes());r=record()
            r.update(local_path=str(path),local_sha256='0'*64)
            with patch.object(pdf,'request_bytes') as network:result=pdf.fetch_record(r,folder)
            self.assertEqual(result['status'],'source_changed');network.assert_not_called()
            self.assertEqual(path.read_bytes(),pdf_bytes())

    def test_login_html_is_never_saved_as_pdf(self):
        with TemporaryDirectory() as folder:
            def reply(url,*args):
                if 'api.openalex.org' in url:return b'{"locations":[]}',url,'application/json'
                return b'<html>Please log in</html>',url,'text/html'
            with patch.object(pdf,'request_bytes',side_effect=reply):result=pdf.fetch_record(record(),folder)
            self.assertNotEqual(result['status'],'downloaded');self.assertFalse(list(Path(folder).rglob('*.pdf')))

    def test_wrong_doi_and_reference_only_title_mismatch_are_rejected(self):
        with self.assertRaises(pdf.FetchError):pdf.inspect_pdf(pdf_bytes('10.9999/wrong'),record())
        with self.assertRaises(pdf.FetchError):pdf.inspect_pdf(pdf_bytes(DOI,'Completely different biomedical manuscript'),record())

    def test_oa_repository_fallback_is_linked_to_same_record(self):
        with TemporaryDirectory() as folder:
            def reply(url,*args):
                if url=='https://example.org/article.pdf':raise pdf.FetchError('access_required','HTTP 403')
                if 'api.openalex.org' in url:return json.dumps({'locations':[{'is_oa':True,'pdf_url':'https://repository.example.edu/paper.pdf'}]}).encode(),url,'application/json'
                return pdf_bytes(),url,'application/pdf'
            with patch.object(pdf,'request_bytes',side_effect=reply):result=pdf.fetch_record(record(),folder)
            self.assertEqual(result['status'],'downloaded')
            self.assertEqual(result['source_url'],'https://repository.example.edu/paper.pdf')
            self.assertEqual(result['record_id'],'r1')

    def test_cancel_does_not_start_new_requests_or_drop_completed_files(self):
        with TemporaryDirectory() as folder:
            event=threading.Event();event.set();run={'run_dir':folder,'records':[record()]}
            with patch.object(pdf,'request_bytes') as network:pdf.acquire_retained(run,cancel_event=event)
            network.assert_not_called();self.assertTrue(run['pdf_acquisition_summary']['cancelled'])
            self.assertEqual(run['pdf_acquisition_summary']['unprocessed'],1)

    def test_mdpi_asset_is_derived_from_article_metadata_not_a_paper_list(self):
        url=pdf.mdpi_asset('https://www.mdpi.com/2073-4344/12/3/456/pdf',{})
        self.assertEqual(url,'https://mdpi-res.com/d_attachment/catalysts/catalysts-12-00456/article_deploy/catalysts-12-00456.pdf')
        self.assertFalse(pdf.mdpi_asset('https://evil.example/2073-4344/12/3/456/pdf',{}))
        self.assertEqual(pdf.mdpi_asset('https://www.mdpi.com/2077-0375/16/1/45',{}),'https://mdpi-res.com/d_attachment/membranes/membranes-16-00045/article_deploy/membranes-16-00045.pdf')
        self.assertIn('/sustainability-15-14468/',pdf.mdpi_asset('https://www.mdpi.com/2071-1050/15/19/14468',{}))

    def test_download_urls_reject_private_hosts_and_credentials(self):
        for url in ('file:///secret','https://localhost/a.pdf','http://127.0.0.1/a.pdf','http://169.254.169.254/a.pdf','https://a:b@example.org/a.pdf'):
            with self.assertRaises(pdf.FetchError):pdf.public_url(url,resolve=False)

    def test_network_failure_is_retryable_even_if_publisher_requires_access(self):
        with TemporaryDirectory() as folder:
            def reply(url,*args):
                raise pdf.FetchError('access_required' if url.endswith('article.pdf') else 'network_error','failure')
            with patch.object(pdf,'request_bytes',side_effect=reply):result=pdf.fetch_record(record(),folder)
            self.assertEqual(result['status'],'network_error')


class AcquisitionUiTests(unittest.TestCase):
    def instance(self):
        view=object.__new__(app.LiteratureApp);view.run={'records':[record()]};view.busy=False;view.root=None
        view._show_run=Mock();view._launch_figures=Mock();view.status_var=SimpleNamespace(set=Mock())
        view.cancel_event=threading.Event();view._progress=Mock();view._start_job=Mock()
        return view

    def test_top_button_fetches_then_launches_with_partial_success(self):
        view=self.instance();view._open_figures()
        worker,done=view._start_job.call_args.args[1:]
        updated={'pdf_acquisition_summary':{'selected':3,'downloaded':2,'available':0,'failed':1,'ready':2}}
        with patch.object(app.engine,'acquire_pdfs',return_value=updated) as acquire:
            done(worker())
        acquire.assert_called_once_with(view.run,progress=view._progress,cancel_event=view.cancel_event)
        view._launch_figures.assert_called_once();view._show_run.assert_called_once_with(updated)

    def test_no_downloaded_pdfs_or_cancellation_does_not_launch_empty_stage(self):
        for cancelled in (False,True):
            view=self.instance();view._open_figures();done=view._start_job.call_args.args[2]
            with patch.object(app.messagebox,'showinfo'):
                done({'pdf_acquisition_summary':{'ready':0,'cancelled':cancelled}})
            view._launch_figures.assert_not_called()

    def test_no_kept_records_or_busy_does_not_start_download(self):
        for busy in (True,False):
            view=self.instance();view.busy=busy;view.run={'records':[]}
            with patch.object(app.messagebox,'showinfo'):view._open_figures()
            view._start_job.assert_not_called()

if __name__=='__main__':unittest.main()
