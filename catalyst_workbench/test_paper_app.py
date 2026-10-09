"""Hidden-window behavior checks; no network or real research edits."""
from __future__ import annotations

import copy
import gc
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import paper_app as app
import paper_workspace as work
from test_paper_workspace import make_pdf, PAGE_ONE, PAGE_TWO


class PaperAppTests(unittest.TestCase):
    def setUp(self):
        self.reading_patch=patch.object(app.reading,'engine_ready',return_value=False)
        self.reading_patch.start();self.addCleanup(self.reading_patch.stop)
        self.temp=tempfile.TemporaryDirectory(prefix='paper_ui_')
        self.folder=Path(self.temp.name)
        self.pdf=make_pdf(self.folder/'article.pdf',[PAGE_ONE,PAGE_TWO])
        self.article={'local_pdf':str(self.pdf),'source_sha256':work.digest(self.pdf),
                      'title':'SYNTHETIC UI FIXTURE','doi':'10.1234/ui-fixture','paper_id':'paper-a'}
        self.project=work.open_paper(self.article,self.folder/'projects')
        self.root=tk.Tk();self.root.withdraw()
        self.gui=app.PaperWorkbench(self.root,self.folder/'projects',self.folder/'images')
        self.gui.set_project(self.project)

    def tearDown(self):
        # Remove scheduled callbacks before Tcl is destroyed across tests.
        for key in self.root.tk.splitlist(self.root.tk.call('after','info')):
            self.root.after_cancel(key)
        self.root.destroy();self.gui=None;self.root=None
        # Tk-owned variables must be finalized on the UI thread between cases,
        # not by a later background extraction thread's cyclic GC pass.
        gc.collect();self.temp.cleanup()

    def pump(self):
        deadline=time.monotonic()+8
        while self.gui.busy and time.monotonic()<deadline:
            self.root.update();time.sleep(.02)
        self.assertFalse(self.gui.busy,'background job did not complete')
        self.root.update_idletasks()

    def test_text_job_populates_same_article_and_semantic_tabs(self):
        self.gui.extract();self.pump()
        self.assertEqual(len(self.gui.project['pages']),2)
        self.assertTrue(self.gui.semantic_tree.get_children())
        self.assertIn('Synthetic NH3',self.gui.page_text.get('1.0','end'))
        self.assertEqual(self.gui.project['article']['source_sha256'],work.digest(self.pdf))

    def test_chinese_reading_appears_after_original_and_before_semantic_details(self):
        work.extract_document(self.project);self.gui.set_project(self.project)
        evidence=next(e for e in self.project['evidence'] if e['branch']=='semantic')
        before=copy.deepcopy(self.project)
        app.reading.save_reading(self.project,evidence,{'translation':'本条中文参考译文。','model':'TEST'})
        self.gui.semantic_tree.selection_set(evidence['evidence_id'])
        self.gui.describe_evidence(self.gui.semantic_tree,self.gui.semantic_detail)
        text=self.gui.semantic_detail.get('1.0','end')
        self.assertLess(text.index(evidence['quote']),text.index('本条中文参考译文。'))
        self.assertLess(text.index('本条中文参考译文。'),text.index('结果归属：'))
        self.assertTrue(self.gui.semantic_detail.tag_ranges('chinese_reading'))
        self.assertEqual(before,self.project)
        self.gui.show_reading.set(False)
        self.gui.describe_evidence(self.gui.semantic_tree,self.gui.semantic_detail)
        self.assertNotIn('本条中文参考译文。',self.gui.semantic_detail.get('1.0','end'))
        self.assertEqual(before,self.project)

    def test_completed_translation_cannot_overwrite_new_selection_or_clear_job_busy(self):
        work.extract_document(self.project);self.gui.set_project(self.project)
        a,b=[e for e in self.project['evidence'] if e['branch']=='semantic'][:2]
        self.gui.semantic_tree.selection_set(b['evidence_id'])
        self.gui.describe_evidence(self.gui.semantic_tree,self.gui.semantic_detail)
        self.gui.busy=True
        self.gui.reading_service.completed.put((app.reading.request_key(self.project,a),{'translation':'这是旧句子的译文。'}))
        self.gui.poll()
        self.assertNotIn('这是旧句子的译文。',self.gui.semantic_detail.get('1.0','end'))
        self.assertTrue(self.gui.busy)
        self.gui.reading_service.completed.put((app.reading.request_key(self.project,b),{'translation':'这是新句子的译文。'}))
        self.gui.poll()
        self.assertIn('这是新句子的译文。',self.gui.semantic_detail.get('1.0','end'))
        self.assertTrue(self.gui.busy)

    def test_semantic_rescan_keeps_semantic_tab_and_shows_adoption(self):
        self.gui.extract(focus='semantic');self.pump()
        self.assertEqual(self.gui.tabs.index(self.gui.tabs.select()),1)
        self.assertIn('adoption',self.gui.semantic_tree['columns'])
        self.assertIn('语义候选',self.gui.semantic_hint.get())
        eid=self.gui.semantic_tree.get_children()[0]
        work.set_semantic_adoption(self.gui.project,eid,'background','reviewer','Review only.')
        self.gui.refresh_lists()
        self.assertEqual(self.gui.semantic_tree.set(eid,'adoption'),'仅作背景 / 展望')

    def test_semantic_adoption_dialog_is_modal_and_does_not_auto_save(self):
        self.gui.extract(focus='semantic');self.pump()
        self.gui.semantic_tree.selection_set(self.gui.semantic_tree.get_children()[0])
        snapshot=copy.deepcopy(self.gui.project)
        self.gui.adopt_semantics()
        dialog=self.root.grab_current()
        self.assertIsNotNone(dialog);self.assertEqual(dialog.title(),'这句话准备如何使用')
        self.assertEqual(snapshot,self.gui.project);dialog.destroy()

    def test_numeric_home_shows_performance_and_keeps_raw_text_separate(self):
        self.gui.task_var.set(work.TASKS['scr_conversion']['title']);self.gui.change_task()
        self.gui.extract();self.pump()
        self.assertEqual(self.gui.text_tabs.index(self.gui.text_tabs.select()),0)
        self.assertTrue(self.gui.numeric_tree.get_children())
        self.gui.numeric_scope.set('当前目标性能');self.gui.refresh_numbers()
        self.assertTrue(self.gui.numeric_tree.get_children())
        self.assertTrue(all(row['display_role']=='target' for row in self.gui.numeric_rows.values()))

    def test_number_links_to_exact_raw_quote_in_second_subtab(self):
        self.gui.extract();self.pump()
        eid=next(iter(self.gui.numeric_rows))
        self.gui.numeric_tree.selection_set(eid);self.gui.goto_evidence(self.gui.numeric_tree)
        self.assertEqual(self.gui.text_tabs.index(self.gui.text_tabs.select()),1)
        self.assertTrue(self.gui.page_text.tag_ranges('evidence_focus'))

    def test_switch_to_empty_paper_list_clears_numeric_results(self):
        self.gui.extract();self.pump()
        self.assertTrue(self.gui.numeric_tree.get_children())
        self.gui.set_papers(([],[]))
        self.assertFalse(self.gui.numeric_tree.get_children())
        self.assertFalse(self.gui.numeric_rows)

    def test_relative_claim_cannot_open_absolute_record_editor(self):
        work.extract_document(self.project);self.gui.set_project(self.project)
        evidence=next(e for e in self.project['evidence'] if e.get('kind')=='comparison')
        self.gui.semantic_tree.selection_set(evidence['evidence_id'])
        with patch.object(app,'RecordDialog') as dialog:
            self.gui.use_evidence(self.gui.semantic_tree)
        dialog.assert_not_called();self.assertEqual(self.project['records'],[])
        self.assertIn('关系',self.gui.status.get())

    def test_record_editor_is_modal_and_does_not_auto_confirm(self):
        work.extract_document(self.project)
        evidence=next(e for e in self.project['evidence'] if e.get('kind')=='absolute')
        record=work.record_from_evidence(self.project,[evidence['evidence_id']])
        dialog=app.RecordDialog(self.root,record,'reviewer',lambda *args:None)
        self.assertEqual(self.root.grab_current(),dialog)
        self.assertFalse(dialog.confirm.get());dialog.destroy()

    def test_new_empty_paper_list_clears_previous_article(self):
        work.extract_document(self.project);self.gui.set_project(self.project)
        self.gui.set_papers(([],[{'reason':'missing PDF'}]))
        self.assertIsNone(self.gui.project)
        self.assertFalse(self.gui.semantic_tree.get_children())
        self.assertEqual(self.gui.paper_var.get(),'')
        self.assertNotIn('Synthetic NH3',self.gui.page_text.get('1.0','end'))

    def test_task_change_only_affects_new_records(self):
        work.extract_document(self.project)
        evidence=next(e for e in self.project['evidence'] if e.get('kind')=='absolute')
        record=work.record_from_evidence(self.project,[evidence['evidence_id']])
        work.put_record(self.project,record)
        before=record['task_id']
        self.gui.task_var.set(work.TASKS['nh3_adsorption_energy']['title']);self.gui.change_task()
        self.assertEqual(self.project['task_id'],'nh3_adsorption_energy')
        self.assertEqual(self.project['records'][0]['task_id'],before)

    def test_image_entry_routes_only_current_paper(self):
        with patch.object(work,'ensure_image_batch',return_value={'run_dir':str(self.folder)}) as build, patch.object(app.subprocess,'Popen') as launch:
            self.gui.open_images();self.pump()
        command=launch.call_args.args[0]
        self.assertEqual(command[command.index('--paper-id')+1],'paper-a')
        self.assertEqual(command[command.index('--open-batch')+1],str(self.folder/'batch.json'))
        self.assertEqual(build.call_args.args[0]['article']['source_sha256'],work.digest(self.pdf))

    def test_export_zero_ready_is_explained_and_keeps_candidates(self):
        work.extract_document(self.project);self.gui.set_project(self.project)
        with patch.object(app.messagebox,'showinfo') as notice:
            self.gui.export();self.pump()
        self.assertIn('当前没有满足标准化条件',notice.call_args.args[1])
        self.assertIn('尚未编码或训练模型',notice.call_args.args[1])
        directory=Path(self.gui.project['exports'][-1]['directory'])
        self.assertTrue((directory/'待编码包.json').is_file())

    def test_semantic_navigation_highlights_exact_original_and_clears_on_page_change(self):
        work.extract_document(self.project);self.gui.set_project(self.project)
        evidence=next(e for e in self.project['evidence'] if e['branch']=='semantic')
        self.gui.semantic_tree.selection_set(evidence['evidence_id'])
        self.gui.goto_evidence(self.gui.semantic_tree)
        indexes=self.gui.page_text.tag_ranges('evidence_focus')
        self.assertEqual(len(indexes),2)
        self.assertEqual(self.gui.page_text.get(*indexes),evidence['quote'])
        self.assertIn('已高亮',self.gui.location_hint.get())
        self.gui.page_var.set('2');self.gui.show_page()
        self.assertFalse(self.gui.page_text.tag_ranges('evidence_focus'))

    def test_navigation_does_not_highlight_stale_or_ambiguous_quotation(self):
        work.extract_document(self.project);self.gui.set_project(self.project)
        evidence=next(e for e in self.project['evidence'] if e['branch']=='semantic')
        self.gui.semantic_tree.selection_set(evidence['evidence_id'])
        evidence['stale']=True
        self.gui.goto_evidence(self.gui.semantic_tree)
        self.assertFalse(self.gui.page_text.tag_ranges('evidence_focus'))
        self.assertIn('无法唯一核实',self.gui.location_hint.get())
        evidence.pop('stale');evidence['quote']='NO';evidence['start']=-1;evidence['end']=-1
        self.project['pages'][0]['text']='NO conversion NO conversion'
        evidence['page']=1
        self.gui.goto_evidence(self.gui.semantic_tree)
        self.assertFalse(self.gui.page_text.tag_ranges('evidence_focus'))

    def test_semantic_filters_and_next_pending_never_mutate_evidence(self):
        work.extract_document(self.project);self.gui.set_project(self.project)
        snapshot=copy.deepcopy(self.project)
        self.gui.semantic_kind.set(app.KINDS['comparison']);self.gui.refresh_lists()
        visible=set(self.gui.semantic_tree.get_children())
        expected={e['evidence_id'] for e in self.project['evidence'] if e.get('kind')=='comparison'}
        self.assertTrue(visible);self.assertEqual(visible,expected)
        self.gui.next_pending_semantic()
        self.assertIn(self.gui.semantic_tree.selection()[0],visible)
        self.assertEqual(snapshot,self.project)
        selected=self.gui.semantic_tree.selection()
        self.gui.refresh_lists();self.assertEqual(self.gui.semantic_tree.selection(),selected)
        self.gui.semantic_query.set('NO_SUCH_SENTENCE');self.gui.refresh_lists()
        self.assertFalse(self.gui.semantic_tree.get_children())
        self.assertIn('选择一条',self.gui.semantic_detail.get('1.0','end'))

    def test_other_paper_clears_filters(self):
        self.gui.semantic_query.set('old phrase');self.gui.semantic_kind.set(app.KINDS['comparison'])
        other=copy.deepcopy(self.project);other['article']['source_sha256']='another-hash'
        self.gui.set_project(other)
        self.assertEqual(self.gui.semantic_kind.get(),'全部类型')
        self.assertEqual(self.gui.semantic_query.get(),'')

    def test_scientific_filter_preserves_future_kind_and_source_navigation(self):
        pdf=make_pdf(self.folder/'future.pdf',[[
            'The catalyst may remain stable for 40 h.',
            'The catalyst has a maximum NO conversion of 95%.']])
        project=work.open_paper({'local_pdf':str(pdf),'source_sha256':work.digest(pdf),'title':'SYNTHETIC SCIENTIFIC FACETS'},self.folder/'projects')
        work.extract_document(project);self.gui.set_project(project)
        self.gui.semantic_kind.set('科研关系：稳定性 / 循环');self.gui.refresh_lists()
        selected=self.gui.semantic_tree.get_children()
        self.assertTrue(selected)
        for eid in selected:
            e=next(e for e in project['evidence'] if e['evidence_id']==eid)
            self.assertTrue(any(f['type']=='stability' for f in e.get('semantic_facets',[])))
            self.assertEqual(e['kind'],'outlook')
        self.gui.semantic_tree.selection_set(selected[0]);self.gui.describe_evidence(self.gui.semantic_tree,self.gui.semantic_detail)
        self.assertIn('40 h',self.gui.semantic_detail.get('1.0','end'))
        self.gui.goto_evidence(self.gui.semantic_tree)
        self.assertTrue(self.gui.page_text.tag_ranges('evidence_focus'))
        self.assertEqual(project['records'],[])


if __name__=='__main__':unittest.main()
