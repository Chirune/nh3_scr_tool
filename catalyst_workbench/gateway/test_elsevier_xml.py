"""Synthetic protocol tests; no entitlement/accuracy claims or live keys."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import engine
import elsevier_api as api
import elsevier_xml as xml
import pdf_fetch
from test_elsevier_api import KEY, TOKEN, DOI, record, Opener, Response, failure, pdf_bytes

FIXTURE=Path(__file__).resolve().parents[2]/'tests/fixtures/elsevier_fulltext_synthetic.xml'

class ElsevierXMLTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.data=FIXTURE.read_bytes()
    def tearDown(self):self.temp.cleanup()
    def client(self,*replies):
        self.opener=Opener(*replies)
        return api.ElsevierClient(KEY,TOKEN,opener=self.opener,minimum_interval=0,prefer_xml=True)
    def test_source_identity_unicode_tables_and_exclusion_of_references(self):
        parsed=xml.parse_xml(self.data,record())
        self.assertEqual(parsed['doi_verified'],DOI)
        self.assertEqual(len(parsed['blocks']),3)
        self.assertIn('Å, ø, ±; NH3.',parsed['blocks'][1]['text'])
        self.assertNotIn('95%',json.dumps(parsed))
        self.assertEqual(parsed['tables'][0]['rows'][1]['cells'][0]['attributes']['morerows'],'1')
        self.assertEqual(parsed['tables'][0]['rows'][2]['cells'][0]['attributes']['nameend'],'c3')
        self.assertTrue(all(b['xml_path'] and b['location_label'].startswith('XML') for b in parsed['blocks']))
    def test_abstract_errors_wrong_identity_and_entities_are_not_fulltext(self):
        cases=[b'<html>login</html>', b'<service-error>permission</service-error>',
               self.data.replace(b'<originalText>',b'<noFullText>').replace(b'</originalText>',b'</noFullText>'),
               self.data.replace(DOI.encode(),b'10.1016/wrong'),
               b'<!DOCTYPE test [<!ENTITY secret "payload">]>'+self.data.split(b'?>',1)[1],
               self.data.replace(b'Synthetic copper catalyst ammonia reduction fixture',b'Unrelated biology study about plants')]
        for data in cases:
            with self.subTest(data=data[:30]),self.assertRaises(ValueError):xml.parse_xml(data,record())
    def test_header_full_view_and_exact_raw_file_with_no_credentials(self):
        active=self.client(Response(self.data,{'X-RateLimit-Remaining':'12'}))
        info=active.fetch_xml(record(),self.root)
        req=self.opener.calls[0]
        self.assertEqual(req.get_header('Accept'),'text/xml')
        self.assertEqual(req.get_header('X-els-apikey'),KEY)
        self.assertEqual(req.get_header('X-els-insttoken'),TOKEN)
        self.assertIn('view=FULL',req.full_url)
        self.assertEqual(Path(info['local_xml']).read_bytes(),self.data)
        self.assertNotIn('local_path',info)
        self.assertEqual(info['status'],'downloaded_xml')
        self.assertEqual(info['quota']['X-RateLimit-Remaining'],12)
        self.assertNotIn(KEY,json.dumps(info))
        self.assertNotIn(TOKEN,json.dumps(info))
    def test_preferred_xml_success_does_not_request_pdf(self):
        active=self.client(Response(self.data))
        self.assertEqual(active.fetch_fulltext(record(),self.root)['status'],'downloaded_xml')
        self.assertEqual(len(self.opener.calls),1)
    def test_xml_unavailable_can_fall_back_to_verified_pdf(self):
        active=self.client(Response(b'<html>not full XML</html>'),Response(pdf_bytes()))
        info=active.fetch_fulltext(record(),self.root)
        self.assertEqual(info['status'],'downloaded')
        self.assertEqual(info['xml_attempt']['status'],'api_xml_unavailable')
        self.assertEqual(len(self.opener.calls),2)
    def test_authentication_and_rate_limit_do_not_repeat_in_other_format(self):
        for code in (401,429):
            active=self.client(failure(code))
            with self.assertRaises(api.APIError):active.fetch_fulltext(record(),self.root)
            self.assertEqual(len(self.opener.calls),1)
            self.assertFalse(list(self.root.rglob('*.xml')))
    def test_batch_xml_cache_and_modified_source_detection(self):
        active=self.client(Response(self.data))
        run={'run_dir':str(self.root),'records':[record()]}
        pdf_fetch.acquire_retained(run,publisher_client=active)
        self.assertEqual(run['pdf_acquisition_summary']['xml_ready'],1)
        self.assertEqual(run['pdf_acquisition_summary']['ready'],1)
        self.assertFalse(run['records'][0].get('local_path'))
        cached=pdf_fetch.fetch_record(run['records'][0],self.root,publisher_client=active)
        self.assertEqual(cached['status'],'available_xml')
        self.assertEqual(len(self.opener.calls),1)
        Path(run['records'][0]['local_xml']).write_bytes(self.data+b' ')
        self.assertEqual(pdf_fetch.fetch_record(run['records'][0],self.root)['status'],'source_changed')
    def test_explicit_xml_check_preserves_existing_pdf(self):
        active=self.client(Response(self.data))
        row=record();row.update(local_path='keep-original.pdf',local_sha256='keep-hash')
        run={'profile':'scr_ammonia','profile_title':'Synthetic NH3-SCR test','created_at':'2026-10-13',
             'run_dir':str(self.root),'records':[row],'summary':{},'errors':[],'queries':[]}
        engine.check_publisher_pdf(run,row['id'],active,format='xml')
        saved=json.loads((self.root/'run.json').read_text(encoding='utf-8'))['records'][0]
        self.assertEqual(saved['local_path'],'keep-original.pdf')
        self.assertEqual(saved['local_sha256'],'keep-hash')
        self.assertTrue(saved['local_xml'])
