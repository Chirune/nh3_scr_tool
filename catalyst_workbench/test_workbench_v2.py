"""Regression checks for reviewed evidence migration and series preparation."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import paper_workspace as work
from test_paper_workspace import make_pdf, PAGE_ONE, PAGE_TWO


class WorkbenchV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='workbench_v2_')
        self.folder=Path(self.temp.name)
        pdf=make_pdf(self.folder/'paper.pdf',[PAGE_ONE,PAGE_TWO])
        self.project=work.open_paper({'local_pdf':str(pdf),'doi':'10.1234/v2-fixture','profile':'scr_ammonia'},self.folder/'projects')
        work.extract_document(self.project)

    def tearDown(self):self.temp.cleanup()

    def candidate(self):
        return next(e for e in self.project['evidence'] if e['branch']=='semantic' and e['kind']=='absolute' and e['operator']=='eq' and e['page']==1)

    def image(self,i,x,y,series='A'):
        return {'evidence_id':'image_'+str(i),'branch':'image','kind':'absolute','page':1,'quote':'Synthetic image test only',
            'metric':'NO conversion','value':y,'value_high':None,'unit':'fraction','operator':'eq','x_name':'Temperature',
            'x_value':x,'x_unit':'°C','conditions':{'x_name':'Temperature','x_value':x,'x_unit':'°C'},
            'sample_label':'A','series_label':series,'review_status':'reviewed','usable':True,
            'source_ref':{'source_sha256':self.project['article']['source_sha256'],'session_id':'synthetic-session'}}

    def test_human_binding_survives_unchanged_reextraction(self):
        e=self.candidate()
        work.annotate_semantics(self.project,e['evidence_id'],{'sample_label':'renamed-A','reference_sample':'control','assertion_scope':'current_study'},'reviewer-A','Methods identifies A as the same sample.')
        work.review_evidence(self.project,e['evidence_id'],'reviewed','reviewer-A')
        work.extract_document(self.project)
        current=next(v for v in self.project['evidence'] if v['evidence_id']==e['evidence_id'])
        self.assertEqual(current['sample_label'],'renamed-A');self.assertEqual(current['review_status'],'reviewed')
        self.assertTrue(current['parser_interpretation']);self.assertTrue(current['human_annotation'])

    def test_removed_referenced_evidence_becomes_stale_without_losing_records(self):
        e=self.candidate();r=work.record_from_evidence(self.project,[e['evidence_id']]);work.put_record(self.project,r)
        with patch('paper_semantics.semantic_candidates',return_value=[]):work.extract_document(self.project)
        old=next(v for v in self.project['evidence'] if v['evidence_id']==e['evidence_id'])
        self.assertTrue(old['stale']);self.assertEqual(len(self.project['records']),1)
        with self.assertRaisesRegex(ValueError,'过期'):work.review_evidence(self.project,e['evidence_id'],'reviewed','A')
        self.assertIn('关联证据已过期或缺失',work.record_issues(self.project,self.project['records'][0]))

    def test_binding_change_returns_reviewed_record_to_draft(self):
        e=self.candidate();r=work.record_from_evidence(self.project,[e['evidence_id']]);r['sample_label']=e.get('sample_label','')
        saved=work.put_record(self.project,r,'A',True)
        work.annotate_semantics(self.project,e['evidence_id'],{'sample_label':'A2','assertion_scope':'current_study'},'B','Binding corrected after inspecting the source.')
        self.assertEqual(saved['review_status'],'draft')
        self.assertEqual(e['review_status'],'unreviewed')

    def test_series_keeps_each_points_values_temperature_and_units(self):
        entries=[self.image(1,300,.8),self.image(2,400,.9)]
        self.project['evidence'].extend(entries)
        template=work.record_from_evidence(self.project,[entries[0]['evidence_id']])
        template.update(sample_label='A',experiment_id='run-1',value=999,unit='%',composition='Cu/CeO2')
        template['conditions'].update(temperature_C=999,feed_description='source-reported feed',space_velocity_h_inv=10000)
        created=work.put_image_records(self.project,[e['evidence_id'] for e in entries],template,'A',False)
        self.assertEqual([r['value'] for r in created],[.8,.9]);self.assertEqual([r['unit'] for r in created],['fraction']*2)
        self.assertEqual([r['conditions']['temperature_C'] for r in created],[300,400])
        self.assertTrue(all(r['conditions']['space_velocity_h_inv']==10000 for r in created))
        with self.assertRaisesRegex(ValueError,'已有统一记录'):
            work.put_image_records(self.project,[entries[0]['evidence_id']],template)

    def test_series_rejects_mixed_or_unreviewed_before_saving_anything(self):
        entries=[self.image(1,300,.8),self.image(2,400,.9,series='B')];self.project['evidence'].extend(entries)
        template=work.record_from_evidence(self.project,[entries[0]['evidence_id']])
        with self.assertRaisesRegex(ValueError,'同一条系列'):work.put_image_records(self.project,[e['evidence_id'] for e in entries],template)
        self.assertEqual(self.project['records'],[])
        entries[1]['series_label']='A';entries[1]['usable']=False
        with self.assertRaisesRegex(ValueError,'核验'):work.put_image_records(self.project,[e['evidence_id'] for e in entries],template)
        self.assertEqual(self.project['records'],[])

    def test_confirming_renamed_sample_needs_mapping_reason(self):
        e=self.image(1,300,.8);self.project['evidence'].append(e)
        record=work.record_from_evidence(self.project,[e['evidence_id']]);record['sample_label']='Cu/CeO2'
        with self.assertRaisesRegex(ValueError,'对应依据'):work.put_record(self.project,record,'A',True)
        record['sample_mapping_note']='Fig. 1 legend A is Cu/CeO2 in the methods paragraph.'
        self.assertEqual(work.put_record(self.project,record,'A',True)['review_status'],'reviewed')

    def test_boolean_is_not_a_measured_number(self):
        with self.assertRaises(ValueError):work.finite_number(True)

    def test_unlinked_human_annotation_is_preserved_as_stale_when_parser_identity_changes(self):
        e=self.candidate()
        work.annotate_semantics(self.project,e['evidence_id'],{'sample_label':'checked-A'},'A','Verified the material label in the methods.')
        with patch('paper_semantics.semantic_candidates',return_value=[]):work.extract_document(self.project)
        old=next(v for v in self.project['evidence'] if v['evidence_id']==e['evidence_id'])
        self.assertTrue(old['stale'])
        self.assertEqual(old['human_annotation']['note'],'Verified the material label in the methods.')
        self.assertEqual(self.project['records'],[])

    def test_uncertain_temperature_is_not_flattened_to_an_exact_point(self):
        e=self.candidate()
        for temperature in ({'value':300,'value_high':400,'unit':'°C','operator':'range'},
                            {'value':300,'unit':'°C','operator':'unknown'},
                            {'value':300,'unit':'°C','operator':'gt'}):
            with self.subTest(temperature=temperature):
                e['conditions']={'temperature':temperature}
                row=work.record_from_evidence(self.project,[e['evidence_id']])
                self.assertNotIn('temperature_C',row['conditions'])
                self.assertEqual(row['conditions']['temperature'],temperature)
        e['conditions']={'temperature':{'value':300,'unit':'°C','operator':'eq'}}
        row=work.record_from_evidence(self.project,[e['evidence_id']])
        self.assertEqual(row['conditions']['temperature_C'],300)


if __name__=='__main__':unittest.main()
