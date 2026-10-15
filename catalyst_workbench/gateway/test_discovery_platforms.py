"""Official discovery protocols and credential isolation; entirely offline."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import adapters as a
import engine
import publisher_clients as pc
from test_elsevier_api import Response,pdf_bytes,KEY
from test_publisher_clients import paper,WILEY_DOI

def core():
    return {'id':123,'doi':WILEY_DOI,'title':'NH3-SCR catalyst','abstract':'NO conversion was 90%.',
            'fullText':'Not available for public API users.','yearPublished':2026,'publisher':'Wiley',
            'downloadUrl':'','sourceFulltextUrls':['https://repository.example/source.pdf'],'dataProviders':[{'name':'Repository'}]}

def semantic():
    return {'paperId':'s2-123','externalIds':{'DOI':WILEY_DOI},'title':'NH3-SCR catalyst',
            'abstract':'Cu catalyst NO conversion was 90%.','year':2026,'publicationTypes':['JournalArticle'],
            'journal':{'name':'Journal'},'openAccessPdf':{'url':'https://repository.example/accepted.pdf','status':'GREEN'}}

class DiscoveryTests(unittest.TestCase):
    def test_core_does_not_mistake_fulltext_sentinel_for_abstract(self):
        source=core();source['abstract']=''
        row=a._core_record(source)
        self.assertEqual(row['abstract'],'')
        self.assertEqual(row['fulltext_candidates'][0]['url'],source['sourceFulltextUrls'][0])
        self.assertEqual(row['publisher'],'Wiley')
        self.assertEqual(row['repository_providers'][0]['name'],'Repository')
    def test_scholar_metadata_and_reported_pdf_keep_provenance(self):
        row=a._semantic_record(semantic())
        self.assertEqual(row['doi'],WILEY_DOI)
        self.assertEqual(row['abstract_source']['provider'],'Semantic Scholar')
        self.assertEqual(row['document_type'],'JournalArticle')
        self.assertEqual(len(row['fulltext_candidates']),1)
    def test_relevance_query_replaces_unsupported_hyphen(self):
        with patch.object(a,'_get_json',return_value={'data':[semantic()]}) as get:
            row=a.search_semantic_scholar('NH3-SCR catalyst',limit=1)[0]
        self.assertEqual(get.call_args.args[1]['query'],'NH3 SCR catalyst')
        self.assertEqual(row['provider_query'],'NH3 SCR catalyst')
    def test_key_headers_not_query_and_redirects_stay_on_same_platform(self):
        for provider,base,header,prefix in [('CORE',a.CORE_BASE,'Authorization','Bearer '),('Semantic Scholar',a.SEMANTIC_BASE,'X-api-key','')]:
            key='SYNTHETIC_DISCOVERY_KEY'
            with self.subTest(provider=provider),patch.object(a,'build_opener') as build,patch.dict(a._LAST_STARTED,{},clear=True):
                build.return_value.open.return_value=Response(b'{"results":[],"data":[]}')
                a._get_json(base,{'q':'NH3'},provider,'',15,api_key=key)
                request=build.return_value.open.call_args.args[0]
                self.assertNotIn(key,request.full_url)
                self.assertEqual(request.get_header(header),prefix+key)
                handler=build.call_args.args[0]
                self.assertIsNone(handler.redirect_request(request,None,302,'',{},'https://other.example/receive'))
    def test_rate_limit_stops_remaining_queries_but_keeps_other_sources(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(engine,'search_core',side_effect=a.AdapterError('CORE quota',429)) as denied,patch.object(engine,'search_semantic_scholar',return_value=[a._semantic_record(semantic())]):
            run=engine.run_search('scr_ammonia',['NH3-SCR','ammonia reduction'],providers=['CORE','Semantic Scholar'],
                                  output_root=folder,enrich_abstracts=False)
            self.assertEqual(denied.call_count,1)
            self.assertEqual(run['summary']['total'],1)
            self.assertTrue(any(r['status']=='deferred' for r in run['requests']))
    def test_multi_source_merge_and_keys_never_enter_exports(self):
        secret='SYNTHETIC_DISCOVERY_KEY'
        with tempfile.TemporaryDirectory() as folder,patch.object(engine,'search_core',return_value=[a._core_record(core())]),patch.object(engine,'search_semantic_scholar',return_value=[a._semantic_record(semantic())]):
            run=engine.run_search('scr_ammonia',['NH3'],providers=['CORE','Semantic Scholar'],output_root=folder,
                                  enrich_abstracts=False,search_api_keys={'CORE':secret,'Semantic Scholar':secret})
            self.assertEqual(run['summary']['total'],1)
            self.assertEqual(run['records'][0]['route']['publisher_group'],'Wiley')
            for p in Path(folder).rglob('*'):
                if p.is_file():self.assertNotIn(secret,p.read_text(encoding='utf-8-sig'))
    def test_official_wiley_success_still_checks_pdf_identity(self):
        with tempfile.TemporaryDirectory() as folder,patch('official_sdks.wiley_download',return_value={'status':'SUCCESS','http_status':200,'data':pdf_bytes(WILEY_DOI)}) as official:
            result=pc.WileyClient(KEY,minimum_interval=0).fetch_pdf(paper('wiley'),folder)
            self.assertEqual(result['client_backend'],'wiley-tdm official')
            self.assertEqual(result['doi_verified'],WILEY_DOI)
            official.assert_called_once()
    def test_official_wiley_wrong_pdf_and_403_do_not_become_success(self):
        with tempfile.TemporaryDirectory() as folder:
            for response in ({'http_status':403,'data':None},{'http_status':200,'data':b'<html>login</html>'}):
                with self.subTest(response=response),patch('official_sdks.wiley_download',return_value=response),self.assertRaises(pc.FetchError):
                    pc.WileyClient(KEY,minimum_interval=0).fetch_pdf(paper('wiley'),folder)
            self.assertFalse(list(Path(folder).rglob('*.pdf')))
    def test_springer_default_uses_official_sdk_and_no_credential_in_result(self):
        with patch('official_sdks.springer_search',return_value={'records':[{'doi':'10.1038/test','title':'Fixture'}]}) as official:
            records,url,quota=pc.SpringerOAClient(KEY,minimum_interval=0)._lookup('10.1038/test','openaccess')
            official.assert_called_once_with(KEY,'doi:10.1038/test',kind='openaccess',limit=5)
            self.assertNotIn(KEY,url+json.dumps(records)+json.dumps(quota))
    def test_official_sdks_preserve_rate_limit_stop(self):
        with tempfile.TemporaryDirectory() as folder,patch('official_sdks.wiley_download',return_value={'http_status':429,'data':None,'headers':{'Retry-After':'900'}}) as official:
            client=pc.WileyClient(KEY,minimum_interval=0)
            for _ in range(2):
                with self.assertRaises(pc.APIError) as error:client.fetch_pdf(paper('wiley'),folder)
                self.assertEqual(error.exception.status,'api_rate_limit')
            self.assertEqual(official.call_count,1)

    def test_springer_keys_use_separate_channels_and_meta_401_keeps_oa_active(self):
        import requests
        meta_key = 'SYNTHETIC_META_KEY'
        client = pc.SpringerOAClient(KEY, meta_key, minimum_interval=0)
        response = requests.Response(); response.status_code = 401
        cause = requests.HTTPError(response=response)
        denied = RuntimeError('synthetic'); denied.__cause__ = cause
        with patch('official_sdks.springer_search', side_effect=[denied, {'records': []}]) as official:
            with self.assertRaises(pc.APIError):
                client._lookup('10.1038/test', 'meta/v2')
            client._lookup('10.1038/test', 'openaccess')
        self.assertEqual(official.call_args_list[0].args[0], meta_key)
        self.assertEqual(official.call_args_list[1].args[0], KEY)
        self.assertFalse(client._invalid)
        self.assertTrue(client._metadata_client._invalid)
        self.assertTrue(pc.same_credentials(client, KEY, meta_key))
        self.assertFalse(pc.same_credentials(client, KEY, 'OTHER_META_KEY'))

    def test_springer_oa_key_is_not_reused_for_meta_when_unconfigured(self):
        with patch('official_sdks.springer_search') as official, self.assertRaises(pc.APIError) as error:
            pc.SpringerOAClient(KEY)._lookup('10.1038/test', 'meta/v2')
        self.assertEqual(error.exception.status, 'api_metadata_key_missing')
        official.assert_not_called()
        rate=type('RateLimitExceededError',(Exception,),{})('quota')
        with patch('official_sdks.springer_search',side_effect=rate) as official:
            client=pc.SpringerOAClient(KEY,minimum_interval=0)
            for _ in range(2):
                with self.assertRaises(pc.APIError) as error:client._lookup('10.1038/test','openaccess')
                self.assertEqual(error.exception.status,'api_rate_limit')
            self.assertEqual(official.call_count,1)
