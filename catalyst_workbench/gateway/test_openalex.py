"""Offline protocol, provenance and existing-project compatibility checks."""
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import adapters
import engine

DOI='10.1016/j.synthetic.2026.001'
KEY='SYNTHETIC_OPENALEX_KEY_FOR_TESTS_ONLY'


def work(doi=DOI):
    return {'id':'https://openalex.org/W123','doi':'https://doi.org/'+doi,'title':'Cu catalysts for NH3-SCR',
            'publication_year':2026,'type':'article',
            'abstract_inverted_index':{'NH3-SCR':[0],'catalysts':[1],'were':[2],'tested.':[3]},
            'primary_location':{'source':{'display_name':'Synthetic journal','host_organization_name':'Elsevier'},
                                'landing_page_url':'https://www.sciencedirect.com/science/article/pii/SYNTHETIC','is_oa':False},
            'locations':[{'is_oa':True,'pdf_url':'https://repository.example/paper.pdf'},
                         {'is_oa':False,'pdf_url':'https://publisher.example/subscription.pdf'}]}


def crossref(abstract=''):
    return adapters._crossref_record({'DOI':DOI,'title':['Cu catalysts for NH3-SCR'],'type':'journal-article',
                                      'abstract':abstract,'publisher':'Elsevier'})


class OpenAlexAdapterTests(unittest.TestCase):
    def test_abstract_order_repeated_words_and_chemical_formula(self):
        text=adapters.decode_openalex_abstract({'NH₃':[0,3],'SCR':[2], 'and':[1]})
        self.assertEqual(text,'NH₃ and SCR NH₃')

    def test_incomplete_or_conflicting_index_never_becomes_complete_abstract(self):
        for index in ({'x':[1]}, {'x':[0], 'y':[0]}, {'x':[-1]}, {'x':[True]}, {'x':['0']}, ['invalid']):
            with self.subTest(index=index), self.assertRaises(ValueError):adapters.decode_openalex_abstract(index)
        raw=work();raw['abstract_inverted_index']={'x':[2]}
        record=adapters._openalex_record(raw)
        self.assertEqual(record['abstract'],'')
        self.assertTrue(record['warnings'])

    def test_normalization_keeps_source_and_only_reported_oa_candidates(self):
        record=adapters._openalex_record(work())
        self.assertEqual(record['doi'],DOI)
        self.assertEqual(record['abstract_source']['provider'],'OpenAlex')
        self.assertEqual(record['abstract'],'NH3-SCR catalysts were tested.')
        self.assertEqual(len(record['fulltext_candidates']),1)
        self.assertEqual(record['fulltext_candidates'][0]['access_status'],'open_access_reported')
        self.assertEqual(record['publisher'],'Elsevier')

    def test_lookup_refuses_wrong_article_identity(self):
        with patch.object(adapters,'_get_json',return_value=work('10.9999/other')):
            with self.assertRaises(adapters.AdapterError):adapters.lookup_openalex_doi(DOI)

    def test_key_goes_in_header_not_url(self):
        response=io.BytesIO(json.dumps({'results':[work()]}).encode())
        opener=Mock();opener.open.return_value=response
        with patch.object(adapters,'build_opener',return_value=opener):
            rows=adapters.search_openalex('NH3-SCR',limit=2,api_key=KEY)
        request=opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'),'Bearer '+KEY)
        self.assertNotIn(KEY,request.full_url)
        self.assertNotIn(KEY,json.dumps(rows))

    def test_authorization_never_redirects_to_other_host(self):
        handler=adapters._OpenAlexRedirect()
        self.assertIsNone(handler.redirect_request(Mock(),None,302,'',{},'https://other.example/download'))
        self.assertIsNone(handler.redirect_request(Mock(),None,302,'',{},'http://api.openalex.org/works'))

    def test_429_stops_immediately_and_explains_anonymous_budget(self):
        error=HTTPError('https://api.openalex.org/works',429,'quota',{'Retry-After':'30000'},io.BytesIO(b'not persisted'))
        with patch.object(adapters,'urlopen',side_effect=error) as get, patch.object(adapters.time,'sleep') as sleep:
            with self.assertRaises(adapters.AdapterError) as caught:adapters.search_openalex('NH3-SCR')
        self.assertEqual(get.call_count,1);sleep.assert_not_called()
        self.assertEqual(caught.exception.http_status,429)
        self.assertEqual(caught.exception.retry_after_seconds,30000)
        self.assertIn('共享额度',str(caught.exception))


class OpenAlexWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_missing_crossref_abstract_filled_and_deduplicated_with_provenance(self):
        with patch.object(engine,'search_crossref',return_value=[crossref()]),patch.object(engine,'lookup_openalex_doi',return_value=adapters._openalex_record(work())) as lookup:
            run=engine.run_search('scr_ammonia',['NH3-SCR'],providers=['Crossref'],output_root=self.root,openalex_api_key=KEY)
        self.assertEqual(len(run['records']),1)
        record=run['records'][0]
        self.assertEqual(record['abstract_status'],'available')
        self.assertEqual(record['abstract_source']['provider'],'OpenAlex')
        self.assertEqual(record['sources'],['Crossref','OpenAlex'])
        self.assertEqual(record['effective_decision'],'target')
        self.assertEqual(len(record['source_records']),2)
        self.assertEqual(run['abstract_enrichment']['filled'],1)
        self.assertEqual(lookup.call_args.kwargs['api_key'],KEY)
        for file in self.root.rglob('*'):
            if file.is_file():self.assertNotIn(KEY,file.read_text(encoding='utf-8-sig'))

    def test_missing_abstract_even_with_relevant_title_remains_review(self):
        with patch.object(engine,'search_crossref',return_value=[crossref()]),patch.object(engine,'lookup_openalex_doi',side_effect=adapters.AdapterError('quota',429)):
            run=engine.run_search('scr_ammonia',['NH3-SCR'],providers=['Crossref'],output_root=self.root)
        self.assertEqual(run['records'][0]['effective_decision'],'review')
        self.assertEqual(run['summary']['missing_abstract'],1)
        self.assertIn('stopped_reason',run['abstract_enrichment'])

    def test_lookup_budget_error_does_not_repeat_for_remaining_dois(self):
        raw=[crossref()];raw.append(dict(crossref(),doi='10.9999/second'))
        with patch.object(engine,'search_crossref',return_value=raw),patch.object(engine,'lookup_openalex_doi',side_effect=adapters.AdapterError('quota',429)) as lookup:
            run=engine.run_search('scr_ammonia',['NH3-SCR'],providers=['Crossref'],output_root=self.root)
        self.assertEqual(lookup.call_count,1)
        self.assertEqual(len(run['records']),2)

    def test_search_failure_keeps_other_provider_results(self):
        with patch.object(engine,'search_openalex',side_effect=adapters.AdapterError('quota',429)),patch.object(engine,'search_crossref',return_value=[crossref('NH3-SCR catalyst experiment.')]):
            run=engine.run_search('scr_ammonia',['NH3-SCR'],providers=['OpenAlex','Crossref'],output_root=self.root)
        self.assertEqual(len(run['records']),1)
        self.assertEqual(run['records'][0]['effective_decision'],'target')
        self.assertTrue(run['errors'])

    def test_search_429_skips_further_queries_and_enrichment_in_same_batch(self):
        with patch.object(engine,'search_openalex',side_effect=adapters.AdapterError('quota',429)) as search,patch.object(engine,'search_crossref',return_value=[crossref()]),patch.object(engine,'lookup_openalex_doi') as lookup:
            run=engine.run_search('scr_ammonia',['first query','second query'],providers=['OpenAlex','Crossref'],output_root=self.root)
        self.assertEqual(search.call_count,1);lookup.assert_not_called()
        self.assertEqual(len(run['records']),1)
        self.assertEqual(run['records'][0]['effective_decision'],'review')
        self.assertEqual(len(run['errors']),1)
        self.assertTrue(any(r['status']=='deferred' for r in run['requests']))

    def test_saved_openalex_missing_record_can_be_checked_again(self):
        missing=work();missing['abstract_inverted_index']=None
        run=engine._new_run('scr_ammonia',self.root,'test');engine._screen(run,[adapters._openalex_record(missing)])
        with patch.object(engine,'lookup_openalex_doi',return_value=adapters._openalex_record(work())) as lookup:
            updated=engine.enrich_existing_run(run)
        self.assertEqual(lookup.call_count,1)
        self.assertEqual(updated['records'][0]['abstract_status'],'available')

    def test_old_title_only_auto_keep_is_rechecked_even_when_enrichment_fails(self):
        run=engine._new_run('scr_ammonia',self.root,'test');engine._screen(run,[crossref()])
        run['records'][0]['effective_decision']='target'
        run['records'][0]['screening']['decision']='target'
        with patch.object(engine,'lookup_openalex_doi',side_effect=adapters.AdapterError('quota',429)):
            updated=engine.enrich_existing_run(run)
        self.assertEqual(updated['records'][0]['effective_decision'],'review')
        self.assertEqual(run['records'][0]['effective_decision'],'target')

    def test_already_available_abstract_is_not_replaced_by_doi_enrichment(self):
        original='NH3-SCR catalyst experiment with detailed measurements.'
        with patch.object(engine,'search_crossref',return_value=[crossref(original)]),patch.object(engine,'lookup_openalex_doi') as lookup:
            run=engine.run_search('scr_ammonia',['NH3-SCR'],providers=['Crossref'],output_root=self.root)
        lookup.assert_not_called();self.assertEqual(run['records'][0]['abstract'],original)
        self.assertEqual(run['records'][0]['abstract_source']['provider'],'Crossref')

    def test_merge_selects_one_intact_abstract_and_its_own_source(self):
        source=crossref('Short.');other=adapters._openalex_record(work())
        r=engine.merge_records([source,other])[0]
        self.assertEqual(r['abstract'],other['abstract']);self.assertEqual(r['abstract_source']['provider'],'OpenAlex')
        r=engine.merge_records([other,crossref('A much longer full NH3-SCR abstract with experimental information.')])[0]
        self.assertEqual(r['abstract_source']['provider'],'Crossref')
        self.assertNotIn('Short.',r['abstract'])

    def test_saved_enrichment_preserves_id_manual_decision_note_and_pdf(self):
        run=engine._new_run('scr_ammonia',self.root,'test');engine._screen(run,[crossref()])
        record=run['records'][0];record.update(local_path='kept.pdf',local_sha256='same-hash')
        engine.save_review(run,record['id'],'non_target','Human excluded this paper.')
        before=copy.deepcopy(run)
        with patch.object(engine,'lookup_openalex_doi',return_value=adapters._openalex_record(work())):
            updated=engine.enrich_existing_run(run)
        r=updated['records'][0]
        for key in ('id','manual_decision','manual_note','local_path','local_sha256'):
            self.assertEqual(r[key],record[key])
        self.assertEqual(r['effective_decision'],'non_target')
        self.assertEqual(r['screening']['decision'],'target')
        self.assertEqual(run,before)
        self.assertEqual(engine.load_run(updated['run_dir'])['records'][0]['id'],record['id'])

    def test_doi_import_falls_back_when_crossref_does_not_have_metadata(self):
        with patch.object(engine,'lookup_doi',side_effect=adapters.AdapterError('not found',404)),patch.object(engine,'lookup_openalex_doi',return_value=adapters._openalex_record(work())):
            run=engine.run_dois('scr_ammonia',DOI,output_root=self.root)
        r=run['records'][0]
        self.assertEqual(r['title'],work()['title'])
        self.assertEqual(r['effective_decision'],'target')
        self.assertEqual(r['abstract_source']['provider'],'OpenAlex')

    def test_cancelled_enrichment_and_disabled_option_make_no_lookup(self):
        event=threading.Event();event.set()
        with patch.object(engine,'lookup_openalex_doi') as lookup:
            run=engine._new_run('scr_ammonia',self.root,'test')
            engine._enrich_missing_abstracts(run,[crossref()],self.root/'cache',cancel_event=event)
            engine._enrich_missing_abstracts(run,[crossref()],self.root/'cache',enabled=False)
        lookup.assert_not_called()

    def test_same_doi_is_looked_up_once_and_reuses_cache(self):
        with patch.object(engine,'search_crossref',return_value=[crossref(),crossref()]),patch.object(engine,'lookup_openalex_doi',return_value=adapters._openalex_record(work())) as lookup:
            engine.run_search('scr_ammonia',['NH3-SCR'],providers=['Crossref'],output_root=self.root)
            second=engine.run_search('scr_ammonia',['NH3-SCR'],providers=['Crossref'],output_root=self.root)
        self.assertEqual(lookup.call_count,1)
        self.assertTrue(next(r for r in second['requests'] if r.get('operation')=='abstract_enrichment')['cached_24h'])

    def test_local_pdf_excerpt_does_not_trigger_network_enrichment(self):
        raw=dict(crossref(),abstract='PDF first pages',extraction_status='ok',abstract_origin='PDF前两页片段')
        with patch.object(engine,'lookup_openalex_doi') as lookup:
            run=engine._new_run('scr_ammonia',self.root,'test')
            engine._enrich_missing_abstracts(run,[raw],self.root/'cache')
        lookup.assert_not_called()
        self.assertEqual(engine.merge_records([raw])[0]['abstract_status'],'local_excerpt')

if __name__=='__main__':unittest.main()
