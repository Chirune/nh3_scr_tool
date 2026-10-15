"""XML-only article admission, evidence review and stage3 source revalidation."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import intake
import paper_workspace as work
import paper_encoding as encoding
from gateway.elsevier_xml import read_xml

FIXTURE=Path(__file__).resolve().parents[1]/'tests/fixtures/elsevier_fulltext_synthetic.xml'
DOI='10.1016/j.synthetic.2026.000001'

class XMLWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.xml=self.root/'article.xml';self.xml.write_bytes(FIXTURE.read_bytes())
        row={'id':'xml-test','doi':DOI,'title':'Synthetic copper catalyst ammonia reduction fixture',
             'manual_decision':'target','effective_decision':'target','local_xml':str(self.xml),
             'xml_sha256':hashlib.sha256(self.xml.read_bytes()).hexdigest()}
        self.run=self.root/'run.json';self.run.write_text(json.dumps({'profile':'scr_ammonia','records':[row]}),encoding='utf-8')
        papers,waiting=work.list_papers(screening_run=self.run)
        self.assertFalse(waiting)
        self.project=work.open_paper(papers[0],self.root/'papers')
        work.extract_document(self.project)
    def tearDown(self):self.temp.cleanup()
    def reviewed_record(self):
        e=next(e for e in self.project['evidence'] if e['branch']=='semantic' and e.get('kind')=='absolute' and e.get('metric')=='NO conversion')
        work.review_evidence(self.project,e['evidence_id'],'reviewed','test-reviewer')
        row=work.record_from_evidence(self.project,[e['evidence_id']])
        row.update(sample_label='S1',experiment_id='exp-1',composition='Cu/CeO2',metric='NO conversion',unit='%',
                   conditions={'temperature_C':300,'feed_description':'500 ppm NO, 500 ppm NH3, 5% O2 in N2','space_velocity_h_inv':40000},
                   assertion_scope='current_study',measurement_type='experiment')
        work.put_record(self.project,row,reviewer='test-reviewer',confirm=True)
        folder=Path(work.export_handoff(self.project)['directory'])
        return folder/'待编码包.json',e
    def test_xml_only_admitted_by_article_workspace_but_not_image_pipeline(self):
        self.assertEqual(self.project['article']['source_kind'],'elsevier_xml')
        self.assertFalse(intake.load_screened_input(self.run)['papers'])
        self.assertEqual(len(self.project['pages']),3)
        with self.assertRaisesRegex(ValueError,'XML'):work.ensure_image_batch(self.project,self.root/'figures')
    def test_exact_quotes_with_block_paths_and_raw_table(self):
        self.assertTrue(self.project['evidence'])
        for e in self.project['evidence']:
            self.assertEqual(e['review_status'],'unreviewed')
            self.assertEqual(e['source_ref']['page'],None)
            self.assertTrue(e['source_ref']['xml_path'])
            self.assertEqual(e['source_ref']['block_index'],e['page'])
            b=self.project['pages'][e['page']-1]
            self.assertEqual(b['text'][e['start']:e['end']],e['quote'])
        table=next(e for e in self.project['evidence'] if e['source_ref'].get('extraction')=='elsevier_xml_table')
        self.assertEqual(table['source_table']['rows'][1]['cells'][2]['text'],'90 ± 1')
    def test_reviewed_handoff_can_be_standardized_using_actual_xml(self):
        packet,e=self.reviewed_record()
        result=encoding.build_encoding([packet],'scr_conversion')
        self.assertEqual(len(result['observations']),1)
        self.assertEqual(result['observations'][0]['y'],.9)
    def test_xml_tampering_blocks_old_workspace_and_handoff(self):
        packet,e=self.reviewed_record()
        self.xml.write_bytes(self.xml.read_bytes().replace(b'90%',b'91%'))
        with self.assertRaises(ValueError):work.load_project(work.workspace_path(self.project))
        result=encoding.build_encoding([packet],'scr_conversion')
        self.assertFalse(result['observations'])
    def test_locator_tampering_never_passes_source_revalidation(self):
        packet,e=self.reviewed_record()
        e['source_ref']['xml_path']='/fabricated/path'
        work.save_project(self.project)
        packet=Path(work.export_handoff(self.project)['directory'])/'待编码包.json'
        result=encoding.build_encoding([packet],'scr_conversion')
        self.assertFalse(result['observations'])
    def test_manual_capture_preserves_xml_locator(self):
        text=self.project['pages'][0]['text']
        e=work.add_text_evidence(self.project,1,0,len(text))
        self.assertTrue(e['source_ref']['xml_path'])
        self.assertEqual(e['location_kind'],'xml_block')
