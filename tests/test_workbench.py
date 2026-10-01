import json
import tempfile
import unittest
from pathlib import Path
from scrtool.core import read_json
from scrtool.workbench import Project, questions

ABSTRACT='NH3-SCR catalysts were prepared and experimentally tested. NOx conversion and selectivity were measured at different temperatures.'
HTML='''<meta name="citation_title" content="NH3-SCR catalyst study"><h2>Materials and methods</h2><p>Cu-A catalyst was prepared.</p><h2>Results and discussion</h2><table><tr><th>catalyst</th><th>temperature [degC]</th><th>nox_conversion [%]</th><th>loading [wt%]</th></tr><tr><td>Cu-A</td><td>200</td><td>90</td><td>5</td></tr></table>'''

class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.abstract=self.root/'abstract.json'
        self.abstract.write_text(json.dumps([{'doi':'10.1016/test','record_id':'../../outside','title':'NH3-SCR original experiment','abstract':ABSTRACT}]),encoding='utf-8')
        self.project=Project(self.root/'project')
        self.project.import_abstracts([self.abstract])
        self.rid=self.project.state['papers'][0]['record_id']
        self.primary=self.root/'original.html'; self.primary.write_text(HTML,encoding='utf-8')

    def prepare(self):
        self.project.decide_papers([self.rid],'target','expert')
        self.project.attach(self.rid,[self.primary],'primary')
        self.project.extract([self.rid])
        return next(r for r in self.project.candidates() if r['property']=='nox_conversion')

    def test_unconfirmed_papers_do_not_enter_extraction(self):
        with self.assertRaises(ValueError):self.project.attach(self.rid,[self.primary],'primary')
        with self.assertRaises(ValueError):self.project.extract([self.rid])
        self.assertNotIn('/',self.rid)

    def test_source_review_resume_and_export_are_linked(self):
        row=self.prepare()
        self.assertIn('90',self.project.source(self.rid,row['block_id'])['text'])
        self.assertIn('200',questions(row))
        with self.assertRaises(ValueError):self.project.export()
        self.project.review([row['record_id']],'approve','expert','Checked source row')
        resumed=Project(self.project.folder)
        output,report=resumed.export()
        self.assertEqual(report['ml_rows'],1)
        self.assertTrue((output/'性能数据长表.csv').exists())
        self.assertFalse(report['machine_learning_ready'])
        self.assertEqual(resumed.candidates()[0]['paper_id'],self.rid)

    def test_reimport_keeps_manual_decisions_but_conflicts_reset_them(self):
        self.prepare()
        self.project.import_abstracts([self.abstract])
        self.assertEqual(self.project.paper(self.rid)['human_decision'],'target')
        conflict=self.root/'conflict.json'
        conflict.write_text(json.dumps([{'doi':'10.1016/test','title':'NH3-SCR original experiment','abstract':ABSTRACT+' Additional changed claim.'}]),encoding='utf-8')
        self.project.import_abstracts([conflict])
        self.assertNotIn('human_decision',self.project.paper(self.rid))
        self.assertEqual(self.project.candidates(),[])

    def test_manual_correction_requires_source_and_preserves_conditions(self):
        row=self.prepare()
        with self.assertRaises(ValueError):
            self.project.manual_record(self.rid,row['block_id'],'Cu-A','nox_conversion','99','%','invented 99','expert',old_id=row['record_id'])
        corrected=self.project.manual_record(self.rid,row['block_id'],'Cu-A','nox_conversion','90','%',row['evidence'],'expert',old_id=row['record_id'])
        self.assertEqual(corrected['conditions'],row['conditions'])
        self.assertEqual(corrected['review_status'],'pending')
        self.assertEqual(corrected['method'],'curated_annotation')
        self.assertEqual(corrected['experiment_id'],row['experiment_id'])

    def test_secondary_data_cannot_be_approved(self):
        self.primary.write_text('<meta name="citation_article_type" content="Review article">'+HTML,encoding='utf-8')
        row=self.prepare()
        self.assertIn('secondary_source_document',row['issues'])
        with self.assertRaises(ValueError):self.project.review([row['record_id']],'approve','expert')

    def test_bad_file_keeps_valid_abstracts_and_import_error(self):
        broken=self.root/'bad.json'; broken.write_text('{broken',encoding='utf-8')
        count,errors=self.project.import_abstracts([broken])
        self.assertEqual(count,1); self.assertEqual(len(errors),1)

    def test_imported_file_does_not_supply_a_human_approval(self):
        external=self.root/'external.json'
        external.write_text(json.dumps([{'doi':'10.1016/external','title':'NH3-SCR external import','abstract':ABSTRACT,
                                         'human_decision':'target','human_reviewer':'from file'}]),encoding='utf-8')
        self.project.import_abstracts([external])
        row=next(r for r in self.project.state['papers'] if r['doi']=='10.1016/external')
        self.assertNotIn('human_decision',row)
        self.assertNotIn('human_reviewer',row)

    def test_old_approved_run_is_not_overwritten_by_reextract(self):
        row=self.prepare(); previous=self.project.run_path(self.rid)
        self.project.review([row['record_id']],'approve','expert')
        self.project.extract([self.rid])
        self.assertNotEqual(previous,self.project.run_path(self.rid))
        self.assertEqual(next(r for r in read_json(previous/'reviewed.json') if r['record_id']==row['record_id'])['review_status'],'approved')
        self.assertTrue(all(r['review_status']=='pending' for r in self.project.candidates()))

    def test_batch_checks_all_materials_before_reextracting_any_paper(self):
        self.prepare(); previous=self.project.run_path(self.rid)
        second=self.root/'second.json'
        second.write_text(json.dumps([{'doi':'10.1016/second','title':'Another NH3-SCR experiment','abstract':ABSTRACT}]),encoding='utf-8')
        self.project.import_abstracts([second])
        other=next(r['record_id'] for r in self.project.state['papers'] if r['doi']=='10.1016/second')
        self.project.decide_papers([other],'target','expert')
        with self.assertRaises(ValueError):self.project.extract([self.rid,other])
        self.assertEqual(previous,self.project.run_path(self.rid))

if __name__=='__main__':unittest.main()
