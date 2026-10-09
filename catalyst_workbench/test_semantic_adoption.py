"""Review decisions must not turn relative or future claims into numeric labels."""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import paper_workspace as work
import paper_semantic_report as report
from test_paper_workspace import make_pdf


class SemanticAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='semantic_review_')
        self.root=Path(self.temp.name)
        pdf=make_pdf(self.root/'fixture.pdf',[[
            'Catalytic activity will increase substantially.',
            'The mean concentration was 3% lower than the inlet concentration.',
            'The feed ratio of NO2/NOx was 67%.']])
        self.project=work.open_paper({'local_pdf':str(pdf),'source_sha256':work.digest(pdf),
            'title':'SYNTHETIC semantic review fixture','doi':'10.1234/semantics','paper_id':'fixture'},self.root/'projects')
        work.extract_document(self.project)

    def tearDown(self):self.temp.cleanup()

    def item(self):
        return next(e for e in self.project['evidence'] if e.get('branch')=='semantic' and 'will increase' in e.get('quote',''))

    def test_adoption_does_not_fill_values_and_requires_explicit_confirmation(self):
        item=self.item();source=copy.deepcopy(item)
        saved=work.set_semantic_adoption(self.project,item['evidence_id'],'background','reviewer-A','Future possibility only.')
        self.assertEqual(saved['review_status'],'unreviewed');self.assertFalse(saved['usable'])
        for key in ('value','value_high','relations','quote','source_ref'):
            self.assertEqual(saved.get(key),source.get(key))
        work.set_semantic_adoption(self.project,item['evidence_id'],'background','reviewer-A','Future possibility only.',True)
        self.assertEqual(item['review_status'],'reviewed');self.assertIsNone(item['value'])
        self.assertFalse(self.project['records'])

    def test_same_reextract_preserves_adoption_and_exact_quote(self):
        item=self.item();eid=item['evidence_id']
        work.set_semantic_adoption(self.project,eid,'background','reviewer-A','Not observed yet.',True)
        adopted=copy.deepcopy(item['semantic_adoption'])
        work.extract_document(self.project)
        current=next(e for e in self.project['evidence'] if e['evidence_id']==eid)
        self.assertEqual(current['semantic_adoption'],adopted)
        self.assertEqual(current['review_status'],'reviewed')
        self.assertEqual(current['quote'],item['quote'])

    def test_changed_interpretation_invalidates_even_pending_human_adoption(self):
        import paper_relations
        item=self.item();eid=item['evidence_id']
        work.set_semantic_adoption(self.project,eid,'background','reviewer-A','Need check.')
        original=paper_relations.relation_candidates
        def changed(*args,**kwargs):
            found=original(*args,**kwargs)
            for row in found:
                if 'will increase' in row.get('evidence_quote',''):
                    row['relations'][0]['strength']='CHANGED INTERPRETATION'
            return found
        with patch.object(paper_relations,'relation_candidates',side_effect=changed):work.extract_document(self.project)
        current=next(e for e in self.project['evidence'] if e['evidence_id']==eid)
        self.assertNotIn('semantic_adoption',current)
        self.assertEqual(current['previous_interpretation']['semantic_adoption']['purpose'],'background')
        self.assertEqual(current['review_status'],'unreviewed')

    def test_obsolete_candidate_keeps_human_decision_as_stale(self):
        import paper_relations
        item=self.item();eid=item['evidence_id']
        work.set_semantic_adoption(self.project,eid,'background','reviewer-A','Need check.')
        with patch.object(paper_relations,'relation_candidates',return_value=[]):work.extract_document(self.project)
        old=next(e for e in self.project['evidence'] if e['evidence_id']==eid)
        self.assertTrue(old['stale']);self.assertEqual(old['semantic_adoption']['purpose'],'background')
        with self.assertRaises(ValueError):work.set_semantic_adoption(self.project,eid,'qualitative','A','Cannot revive old evidence.')

    def test_adopted_outlook_cannot_pass_absolute_value_gate(self):
        e=self.item();work.set_semantic_adoption(self.project,e['evidence_id'],'background','A','Future only.',True)
        record=work.record_from_evidence(self.project,[e['evidence_id']])
        record.update(sample_label='S1',experiment_id='test',composition='Cu/CeO2',metric='NO conversion',
            value=95,unit='%',operator='eq',assertion_scope='current_study',measurement_type='experiment',
            conditions={'temperature_C':300,'feed_description':'NO + NH3','space_velocity_h_inv':40000})
        saved=work.put_record(self.project,record,'A',True)
        self.assertTrue(any('语义证据' in reason for reason in work.record_issues(self.project,saved)))
        folder=Path(work.export_handoff(self.project)['directory'])
        packet=json.loads((folder/'待编码包.json').read_text(encoding='utf8'))
        self.assertFalse(packet['records_ready_for_standardization'])

    def test_export_without_performance_records_preserves_decisions_and_offsets(self):
        e=self.item();work.set_semantic_adoption(self.project,e['evidence_id'],'background','A','No observed value.')
        before=copy.deepcopy(self.project);folder=report.export_semantic_inventory(self.project)
        self.assertEqual(before,self.project)
        payload=json.loads((folder/'语义与原文证据.json').read_text(encoding='utf8'))
        found=next(r for r in payload['report']['rows'] if r['evidence_id']==e['evidence_id'])
        self.assertEqual(found['semantic_adoption']['note'],'No observed value.')
        self.assertEqual(found['quote'],self.project['pages'][0]['text'][found['start']:found['end']])
        with (folder/'语义关系与采纳决定.csv').open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
        self.assertTrue(rows);self.assertTrue(all(r['可直接用于训练']=='False' for r in rows))
        self.assertIn('本页为导出快照',(folder/'00_本篇语义清单.html').read_text(encoding='utf8'))

    def test_decision_requires_person_and_reason_but_unknown_purpose_is_rejected(self):
        eid=self.item()['evidence_id']
        for purpose,reviewer,note in [('background','','why'),('qualitative','A',''),('invalid','A','reason')]:
            with self.assertRaises(ValueError):work.set_semantic_adoption(self.project,eid,purpose,reviewer,note)
        work.set_semantic_adoption(self.project,eid,'exclude','A','Not relevant.')
        with self.assertRaises(ValueError):work.review_evidence(self.project,eid,'reviewed','A')
        work.set_semantic_adoption(self.project,eid,'pending','A','')
        self.assertEqual(self.item()['review_status'],'unreviewed')


if __name__=='__main__':unittest.main()
