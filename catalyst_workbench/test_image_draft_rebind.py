import copy
import unittest
from paper_workspace import _rebind_unchanged_image_drafts


class ImageDraftRebindTests(unittest.TestCase):
    def fixture(self):
        old={'evidence_id':'old','branch':'image','stale':True,'metric':'NOx conversion','value':85.,
             'unit':'%','operator':'eq','sample_label':'Cu-0.5','series_label':'Cu-0.5',
             'conditions':{'x_value':400},'source_ref':{'session_id':'s','point_id':'p','calibration_id':'c',
             'figure_revision':1,'point_pixels':[120,50],'source_sha256':'same-pdf'}}
        new=copy.deepcopy(old);new.update(evidence_id='new',stale=False,usable=True,review_status='reviewed')
        record={'record_id':'r','review_status':'draft','evidence_ids':['old','context'],
                'evidence_roles':{'old':'value','context':'context'}}
        return {'evidence':[old,new],'records':[record]},new

    def test_unchanged_draft_follows_review_export_without_approval(self):
        project,new=self.fixture()
        changes=_rebind_unchanged_image_drafts(project,{'new':new})
        r=project['records'][0]
        self.assertEqual(r['evidence_ids'],['new','context'])
        self.assertEqual(r['review_status'],'draft')
        self.assertEqual(r['evidence_roles'],{'new':'value','context':'context'})
        self.assertEqual(changes[0]['before']['evidence_ids'],['old','context'])

    def test_changed_or_incomplete_source_stays_stale(self):
        for field,value in [('value',86.),('sample_label','Cu-1.4'),('conditions',{'x_value':420})]:
            project,new=self.fixture();new[field]=value
            self.assertFalse(_rebind_unchanged_image_drafts(project,{'new':new}))
            self.assertEqual(project['records'][0]['evidence_ids'][0],'old')
        for field,value in [('calibration_id','different'),('point_pixels',[125,50]),('source_sha256','other-pdf'),('point_id',None)]:
            project,new=self.fixture();new['source_ref'][field]=value
            self.assertFalse(_rebind_unchanged_image_drafts(project,{'new':new}))

    def test_reviewed_record_and_ambiguous_replacement_not_changed(self):
        project,new=self.fixture();project['records'][0]['review_status']='reviewed'
        self.assertFalse(_rebind_unchanged_image_drafts(project,{'new':new}))
        project,new=self.fixture();other=copy.deepcopy(new);other['evidence_id']='other'
        self.assertFalse(_rebind_unchanged_image_drafts(project,{'new':new,'other':other}))
