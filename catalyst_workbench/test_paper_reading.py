"""Reading aids must remain separate from scientific evidence and labels."""
import copy
import csv
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import paper_reading as reading
import paper_semantic_report as report
import paper_workspace as work
from paper_translation_worker import prepare_text, split_chunks, normalize_terminology
from test_paper_workspace import make_pdf


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='reading_notes_')
        self.folder=Path(self.temp.name)
        pdf=make_pdf(self.folder/'source.pdf',[[
            'The catalyst may exhibit a higher NO conversion at 250 C.',
            'The NH3 adsorption capacity decreased after aging.']])
        self.project=work.open_paper({'local_pdf':str(pdf),'source_sha256':work.digest(pdf),
            'title':'SYNTHETIC reading fixture'},self.folder/'projects')
        work.extract_document(self.project)
        self.item=next(e for e in self.project['evidence'] if e['branch']=='semantic')
        self.result={'translation':'催化剂在 250 C 时可能表现出更高的 NO 转化率。',
            'model':'TEST translator, not real model output','engine':reading.ENGINE_VERSION}

    def tearDown(self):
        self.temp.cleanup()

    def test_sidecar_does_not_change_paper_or_review_or_training_package(self):
        original=copy.deepcopy(self.project)
        path=Path(self.project['run_dir'])/'paper.json';before=path.read_bytes()
        reading.save_reading(self.project,self.item,self.result)
        self.assertEqual(self.project,original);self.assertEqual(path.read_bytes(),before)
        self.assertEqual(reading.reading_aid(self.project,self.item)['status'],'translated')
        self.assertFalse(reading.reading_aid(self.project,self.item)['for_training'])
        handoff=work.export_handoff(self.project)
        packet=json.loads((Path(handoff['directory'])/'待编码包.json').read_text(encoding='utf8'))
        encoded=json.dumps(packet,ensure_ascii=False)
        self.assertNotIn(self.result['translation'],encoded)
        self.assertNotIn('reading_aid',encoded)
        self.assertEqual(packet['records_ready_for_standardization'],[])

    def test_changed_quote_source_or_engine_never_reuses_old_translation(self):
        reading.save_reading(self.project,self.item,self.result)
        changed={**self.item,'quote':self.item['quote']+' not'}
        self.assertIsNone(reading.cached_reading(self.project,changed))
        changed_p=copy.deepcopy(self.project);changed_p['article']['source_sha256']='new'
        self.assertIsNone(reading.cached_reading(changed_p,self.item))
        with patch.object(reading,'ENGINE_VERSION','new-version'):
            self.assertIsNone(reading.cached_reading(self.project,self.item))

    def test_corrupt_cache_is_ignored_and_term_help_still_available(self):
        p=reading.cache_path(self.project,self.item);p.parent.mkdir();p.write_text('{broken')
        with patch.object(reading,'engine_ready',return_value=False):
            aid=reading.reading_aid(self.project,self.item)
        self.assertEqual(aid['status'],'unavailable');self.assertTrue(aid['terms'])
        self.assertEqual(aid['translation'],'')

    def test_chinese_with_acronyms_does_not_need_machine_translation(self):
        item={**self.item,'quote':'该催化剂在较高 GHSV 时转化率下降，需要核对测试条件。'}
        self.assertEqual(reading.reading_aid(self.project,item)['status'],'original_chinese')

    def test_export_places_cached_chinese_under_exact_original_without_inference(self):
        reading.save_reading(self.project,self.item,self.result)
        snapshot=copy.deepcopy(self.project)
        with patch.object(reading,'translate_local',side_effect=AssertionError('export must not translate')):
            folder=report.export_semantic_inventory(self.project)
        self.assertEqual(snapshot,self.project)
        html=(folder/'00_本篇语义清单.html').read_text(encoding='utf8')
        self.assertLess(html.index(self.item['quote']),html.index(self.result['translation']))
        payload=json.loads((folder/'语义与原文证据.json').read_text(encoding='utf8'))
        row=next(x for x in payload['report']['rows'] if x['evidence_id']==self.item['evidence_id'])
        self.assertEqual(row['quote'],self.item['quote'])
        self.assertEqual(row['reading_aid']['translation'],self.result['translation'])
        self.assertFalse(row['reading_aid']['for_training'])
        with (folder/'语义关系与采纳决定.csv').open(encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f))
        row=next(x for x in rows if x['证据ID']==self.item['evidence_id'])
        self.assertEqual(row['中文参考译文（非原始证据）'],self.result['translation'])

    def test_stalled_old_selection_only_processes_newest_pending_request(self):
        started=threading.Event();resume=threading.Event();calls=[]
        def translate(text):
            calls.append(text)
            if len(calls)==1:started.set();resume.wait(3)
            return {**self.result,'translation':'已翻译：'+text}
        service=reading.ReadingService(translate)
        try:
            one={**self.item,'evidence_id':'one','quote':'Sentence one.'}
            two={**self.item,'evidence_id':'two','quote':'Sentence two.'}
            three={**self.item,'evidence_id':'three','quote':'Sentence three.'}
            service.request(self.project,one);self.assertTrue(started.wait(3))
            service.request(self.project,two);service.request(self.project,three);resume.set()
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                service.drain()
                if reading.cached_reading(self.project,three):break
                time.sleep(.03)
            self.assertEqual(calls,['Sentence one.','Sentence three.'])
            self.assertIsNone(reading.cached_reading(self.project,two))
            self.assertNotEqual(reading.request_key(self.project,one),reading.request_key(self.project,three))
        finally:
            resume.set();service.close();service.thread.join(2)

    def test_translation_failure_does_not_loop_or_save_fake_translation(self):
        calls=[]
        def fail(text):calls.append(text);raise RuntimeError('test failure')
        service=reading.ReadingService(fail)
        try:
            key=service.request(self.project,self.item)
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                service.drain()
                if service.states[key]['status']=='error':break
                time.sleep(.02)
            service.request(self.project,self.item)
            self.assertEqual(len(calls),1)
            self.assertIsNone(reading.cached_reading(self.project,self.item))
            self.assertIn('test failure',service.states[key]['message'])
        finally:
            service.close();service.thread.join(2)

    def test_only_uppercase_independent_NO_expands_for_translation(self):
        text='No N2O was detected; NO conversion did not increase. NOx is different.'
        result=prepare_text(text)
        self.assertEqual(result,'No N2O was detected; nitric oxide (NO) conversion did not increase. NOx is different.')

    def test_long_sentence_is_not_silently_truncated(self):
        class Tokenizer:
            def encode(self,text,out_type=str):return text.split()
        text=' '.join('word'+str(i) for i in range(321))
        chunks=split_chunks(text,Tokenizer(),limit=100)
        self.assertEqual(' '.join(chunks),text);self.assertEqual(len(chunks),4)

    def test_pdf_ligature_and_unicode_hyphen_are_fixed_only_in_translation_copy(self):
        quote='NO conver‐\nsion efﬁciency of NH\n3-SCR'
        self.assertEqual(prepare_text(quote),'nitric oxide (NO) conversion efficiency of NH3-SCR')
        self.assertIn('‐\n',quote)

    def test_terminology_cleanup_keeps_negation_numbers_and_unknown_terms(self):
        quote='The NO conversion did not increase above 90%.'
        raw='硝氧化物(NO)的转化率没有增加到90%以上。'
        text,edits=normalize_terminology(quote,raw)
        self.assertEqual(text,'一氧化氮（NO）的转化率没有增加到90%以上。')
        self.assertTrue(edits)
        self.assertEqual(normalize_terminology('No such result was observed.',raw),(raw,[]))


if __name__=='__main__':unittest.main()
