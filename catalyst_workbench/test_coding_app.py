"""Hidden-window integration checks using temporary real PDF/image fixtures.

These fixtures are not research data. No user projects, network requests or
external windows are used. Semantic migration checks inject parser outputs,
while retaining exact text offsets from the actual temporary PDF.
"""
from __future__ import annotations

import copy
import csv
import gc
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import types
import unittest
from unittest.mock import patch

from pypdf import PdfReader

import coding_app
import paper_app
import paper_encoding as encoding
import paper_workspace as work
from digitizer.session import export_session, set_calibration
import test_paper_image_bridge as image_fixtures
from test_paper_workspace import make_pdf


class CodingAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='coding-ui-integration-')
        self.folder = Path(self.temp.name).resolve()
        self.pdf = make_pdf(self.folder / 'fixture.pdf', [[
            'Synthetic coding UI fixture, not scientific data.',
            'Sample S1 showed NO conversion of 90% at 300 C.',
            'The feed contained NO, NH3 and oxygen; GHSV was 40000 h-1.',
        ]])
        self.project = work.open_paper({
            'local_pdf': str(self.pdf), 'source_sha256': work.digest(self.pdf),
            'doi': '10.1234/coding-ui', 'title': 'Synthetic coding UI fixture',
            'profile': 'scr_ammonia'}, self.folder / 'papers')
        self.root = tk.Tk()
        self.root.withdraw()
        self.gui = coding_app.CodingWorkbench(self.root, self.folder / 'encoding')
        self.errors = []
        self.patches = [
            patch.object(coding_app.messagebox, 'showerror', side_effect=lambda *a, **k: self.errors.append(a)),
            patch.object(coding_app.messagebox, 'showinfo'),
        ]
        for mocked in self.patches:
            mocked.start()

    def tearDown(self):
        self.root.update_idletasks()
        def descendants(widget):
            result = [widget]
            for child in widget.winfo_children():
                result.extend(descendants(child))
            return result
        owners = descendants(self.root)
        for key in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            command = self.root.tk.call('after', 'info', key)[0]
            owner = next((widget for widget in owners if command in (widget._tclCommands or [])), self.root)
            owner.after_cancel(key)
        self.root.destroy()
        for mocked in reversed(self.patches):
            mocked.stop()
        # Tk variables from destroyed windows must be collected on the UI
        # thread, not by a later test's PDF worker when cyclic GC happens.
        self.gui = None
        self.root = None
        gc.collect()
        self.temp.cleanup()

    def pump(self, gui=None):
        gui = gui or self.gui
        deadline = time.monotonic() + 12
        while gui.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(gui.busy, 'background operation did not finish')
        self.root.update_idletasks()

    def export_packet(self, project):
        result = work.export_handoff(project)
        return Path(result['directory']) / '待编码包.json'

    def record(self, project, evidence, confirmed=True):
        record = work.record_from_evidence(project, [evidence['evidence_id']])
        record.update(task_id='scr_conversion', sample_label='S1', experiment_id='SCR-01',
                      composition='Cu/CeO2', metric='NO conversion', value=90, unit='%',
                      value_high=None, operator='eq', assertion_scope='current_study',
                      measurement_type='experiment',
                      features={'active_metals': ['Cu'], 'support': 'CeO2'},
                      conditions={'temperature_C': 300, 'space_velocity_h_inv': 40000,
                                  'feed_description': '500 ppm NO, 500 ppm NH3, 5% O2'})
        return work.put_record(project, record, reviewer='fixture-reviewer', confirm=confirmed)

    def ready_packet(self, confirmed=True):
        text = PdfReader(self.pdf).pages[0].extract_text()
        self.project['pages'] = [{'page': 1, 'text': text, 'status': 'text_ready',
                                 'text_sha256': hashlib.sha256(text.encode()).hexdigest()}]
        work.save_project(self.project)
        quote = 'Sample S1 showed NO conversion of 90% at 300 C.'
        start = text.index(quote)
        evidence = work.add_text_evidence(self.project, 1, start, start + len(quote))
        work.review_evidence(self.project, evidence['evidence_id'], 'reviewed', 'fixture-reviewer')
        record = self.record(self.project, evidence, confirmed)
        return self.export_packet(self.project), record

    def image_packet(self):
        fixture = image_fixtures.PaperImageBridgeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cal = copy.deepcopy(fixture.session['calibration'])
        cal['x'].update(name='Temperature', unit='°C', v1=300, v2=400)
        cal['y'].update(name='NO conversion', unit='%', v1=0, v2=100)
        set_calibration(fixture.session, cal)
        export_session(fixture.session, {'reviewed': True})
        project = work.open_paper({**fixture.paper, 'profile': 'scr_ammonia',
                                  'batch_path': str(fixture.batch_path)}, self.folder / 'image-papers')
        work.refresh_images(project)
        evidence = next(e for e in project['evidence'] if e['branch'] == 'image')
        record = work.record_from_evidence(project, [evidence['evidence_id']])
        record.update(task_id='scr_conversion', sample_label='sample A', experiment_id='Fig1-01',
                      composition='Cu/CeO2', metric='NO conversion', assertion_scope='current_study',
                      measurement_type='experiment', features={'active_metals': ['Cu'], 'support': 'CeO2'})
        record['conditions'].update(space_velocity_h_inv=40000, feed_description='500 ppm NO and NH3')
        record = work.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        return project, self.export_packet(project), record, evidence

    def parser(self, **changes):
        module = types.ModuleType('paper_semantics')
        def candidates(text):
            quote = 'Sample S1 showed NO conversion of 90% at 300 C.'
            if quote not in text:
                return []
            start = text.index(quote)
            item = {'start': start, 'end': start + len(quote), 'evidence_quote': quote,
                    'kind': 'absolute', 'metric': 'NO conversion', 'operator': 'eq',
                    'value': 90.0, 'value_high': None, 'unit': '%', 'sample_label': 'S1',
                    'reference_sample': '', 'assertion_scope': 'current_study',
                    'conditions': {}, 'review_status': 'unreviewed', 'warnings': []}
            item.update(copy.deepcopy(changes))
            return [item]
        module.semantic_candidates = candidates
        return patch.dict(sys.modules, {'paper_semantics': module})

    def paper_gui(self, project):
        # A child window remains withdrawn throughout behavior tests.
        window = tk.Toplevel(self.root)
        window.withdraw()
        gui = paper_app.PaperWorkbench(window, self.folder / 'papers', self.folder / 'images')
        gui.set_project(project)
        return window, gui

    def test_preview_export_and_source_rows_use_the_same_real_pdf_packet(self):
        packet, _ = self.ready_packet()
        self.gui.add_paths([packet])
        self.gui.build()
        self.pump()
        self.assertEqual(self.gui.result['summary']['observation_count'], 1)
        self.assertEqual(self.gui.result['observations'][0]['y'], .9)
        self.assertEqual(len(self.gui.observations.get_children()), 1)
        self.assertIn('Sample S1 showed NO conversion', self.gui.observation_detail.get('1.0', 'end'))
        self.gui.export()
        self.pump()
        self.assertFalse(self.errors, self.errors)
        directory = Path(self.gui.exported['directory'])
        with (directory / 'y.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows, [{'y': '0.9'}])
        saved = json.loads(Path(self.gui.exported['path']).read_text(encoding='utf8'))
        self.assertEqual(saved['observations'][0]['source_sha256'], work.digest(self.pdf))
        self.assertFalse(saved['scientific_evaluation']['performed'])

    def test_dual_score_tab_shows_separate_scores_and_exported_files(self):
        packet, _ = self.ready_packet()
        self.gui.add_paths([packet]); self.gui.build(); self.pump()
        self.assertEqual(self.gui.tabs.tab(4, 'text'), '⑤ 质量分 / 性能分')
        self.assertEqual(len(self.gui.scoring.tree.get_children()), 1)
        card = self.gui.scoring.report['cards'][0]
        self.assertEqual(card['performance']['score'], 90)
        self.assertIsNone(card['comparison']['group_id'])
        self.assertIn('质量', self.gui.scoring.detail.get('1.0', 'end'))
        self.gui.export(); self.pump()
        folder = Path(self.gui.exported['directory'])
        self.assertTrue((folder / '数据质量分.csv').exists())
        self.assertTrue((folder / '单项性能分.csv').exists())
        self.gui.invalidate()
        self.assertIsNone(self.gui.scoring.report)
        self.assertFalse(self.gui.scoring.tree.get_children())

    def test_scoring_template_dialog_changes_only_scoring(self):
        import copy
        packet, _ = self.ready_packet()
        self.gui.add_paths([packet]); self.gui.build(); self.pump()
        before = copy.deepcopy(self.gui.result)
        old_id = self.gui.scoring.report['config_id']
        dialog = self.gui.scoring.configure(); dialog.withdraw()
        dialog.weights['uncertainty'].set('40'); dialog.save()
        self.assertNotEqual(old_id, self.gui.scoring.report['config_id'])
        self.assertEqual(before, self.gui.result)
        self.assertEqual(self.gui.scoring.report['cards'][0]['performance']['score'], 90)

    def test_comparison_dialog_does_not_preconfirm_and_references_linked_evidence(self):
        packet, _ = self.ready_packet()
        self.gui.add_paths([packet]); self.gui.build(); self.pump()
        dialog = self.gui.scoring.annotate(); dialog.withdraw()
        self.assertFalse(dialog.confirm.get())
        self.assertIn('contact_time', dialog.fields)
        self.assertIn('uncertainty_value', dialog.fields)
        row = self.gui.result['observations'][0]
        source_label = next(k for k, v in dialog.evidence_options.items() if v == row['evidence'][0]['evidence_id'])
        dialog.fields['contact_time'].set('Synthetic fixture: 40000 h-1 per catalyst volume')
        dialog.sources['contact_time'].set(source_label)
        dialog.reviewer.set('fixture-reviewer'); dialog.note.set('Synthetic UI test only')
        dialog.confirm.set(True); dialog.save()
        context = self.gui.scoring.report['cards'][0]['reviewed_context']
        self.assertEqual(context['contact_time']['evidence_id'], row['evidence'][0]['evidence_id'])
        self.assertIn('待补', self.gui.scoring.tree.item('0', 'values')[-1])
        self.assertFalse(self.errors, self.errors)

    def test_dual_score_controls_are_visible_at_minimum_window(self):
        self.root.attributes('-alpha', 0.0)
        self.root.geometry('1000x680+0+0'); self.root.deiconify()
        self.gui.tabs.select(4); self.root.update()
        for button in self.gui.scoring.buttons:
            with self.subTest(button=button.cget('text')):
                self.assertTrue(button.winfo_ismapped())
                self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth())
                self.assertLessEqual(button.winfo_rootx()-self.root.winfo_rootx()+button.winfo_width(), self.root.winfo_width())
        self.root.withdraw()

    def test_model_preparation_tab_preserves_facts_without_training_a_model(self):
        packet, _ = self.ready_packet()
        self.gui.add_paths([packet]); self.gui.build(); self.pump()
        panel = self.gui.model_preparation
        self.assertEqual(self.gui.tabs.tab(5, 'text'), '⑥ 机器学习准备')
        self.assertEqual(len(panel.recipes.get_children()), 4)
        self.assertFalse(panel.plan['model_trained'])
        self.assertIn('逐论文误差', panel.detail.get('1.0','end'))
        panel.prepare_preview()
        self.assertIsNone(panel.preview)
        self.assertIn('论文分组不足', self.gui.status.get())
        self.gui.invalidate(); self.assertIsNone(panel.plan)

    def test_model_preparation_controls_fit_minimum_window(self):
        self.root.attributes('-alpha', 0.0)
        self.root.geometry('1000x680+0+0'); self.root.deiconify()
        self.gui.tabs.select(5); self.root.update()
        for widget in (self.gui.model_preparation.profile_box, self.gui.model_preparation.preview_button,
                       self.gui.model_preparation.export_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth())
            self.assertLessEqual(widget.winfo_rootx()-self.root.winfo_rootx()+widget.winfo_width(), self.root.winfo_width())
        self.root.withdraw()

    def test_model_preparation_shows_fold_rows_exports_and_invalidates_old_recipe_preview(self):
        import test_ml_preparation as model_fixtures
        fixture = model_fixtures.ModelPreparationTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        result, plan = fixture.multi()
        self.gui.show_result(result)
        panel = self.gui.model_preparation
        panel.prepare_preview()
        self.assertIsNotNone(panel.preview)
        self.assertEqual(len(panel.matrix.get_children()), len(plan['folds'][0]['train_ids'] + plan['folds'][0]['validation_ids']))
        self.assertIn('不是预测值', panel.preview_summary.get())
        self.root.attributes('-alpha', 0.0); self.root.deiconify(); self.gui.tabs.select(5)
        for size in ('1000x680', '1320x900'):
            self.root.geometry(size + '+0+0'); self.root.update()
            self.assertTrue(panel.matrix.winfo_ismapped(), size)
            self.assertGreaterEqual(panel.matrix.winfo_height(), 50, size)
        self.root.withdraw()
        panel.export_preview(); self.pump()
        self.assertFalse(self.errors)
        folder = Path(self.gui.exported['directory'])
        self.assertTrue((folder / 'X_train.csv').exists())
        self.assertTrue((folder / 'X_validation.csv').exists())
        self.assertFalse((folder / 'X_test.csv').exists())
        panel.recipes.selection_set('native_categories'); panel.describe()
        self.assertIsNone(panel.preview)
        self.assertEqual(panel.matrix.get_children(), ())
        self.assertIn('重新生成', panel.preview_summary.get())

    def test_pending_record_is_visible_as_excluded_and_cannot_export_empty_matrices(self):
        packet, _ = self.ready_packet(confirmed=False)
        self.gui.add_paths([packet])
        self.gui.build()
        self.pump()
        self.assertEqual(self.gui.result['observations'], [])
        self.assertTrue(self.gui.excluded.get_children())
        self.assertEqual(self.gui.tabs.index(self.gui.tabs.select()), 2)
        self.gui.export()
        self.assertIsNone(self.gui.exported)
        self.assertFalse(self.gui.output_root.exists())
        self.assertIn('没有合格记录', self.gui.status.get())

    def test_task_change_and_packet_removal_clear_old_preview(self):
        packet, _ = self.ready_packet()
        self.gui.add_paths([packet, packet])
        self.assertEqual(len(self.gui.paths), 1)
        self.gui.build()
        self.pump()
        self.gui.task.set(work.TASKS['scr_nox_conversion']['title'])
        self.gui.task_combo.event_generate('<<ComboboxSelected>>')
        self.root.update()
        self.assertIsNone(self.gui.result)
        self.assertFalse(self.gui.matrix.get_children())
        self.gui.files.selection_set(str(packet.resolve()))
        self.gui.remove_file()
        self.assertEqual(self.gui.paths, [])

    def test_changed_pdf_after_preview_fails_export_without_writing_old_x_y(self):
        packet, _ = self.ready_packet()
        self.gui.add_paths([packet])
        self.gui.build()
        self.pump()
        self.pdf.write_bytes(self.pdf.read_bytes() + b'changed in synthetic fixture')
        self.gui.export()
        self.pump()
        self.assertTrue(self.errors)
        self.assertIsNone(self.gui.exported)
        self.assertFalse(self.gui.output_root.exists())

    def test_return_to_paper_uses_packet_workspace_and_hides_console(self):
        packet, _ = self.ready_packet()
        self.gui.add_paths([packet])
        self.gui.files.selection_set(str(packet.resolve()))
        with patch.object(coding_app.subprocess, 'Popen') as launch:
            self.gui.open_paper()
        command = launch.call_args.args[0]
        self.assertEqual(command[command.index('--open-project') + 1], str(work.workspace_path(self.project)))
        self.assertEqual(Path(command[2]).name, 'paper_app.py')
        self.assertFalse(self.errors)

    def test_paper_entry_exports_current_packet_and_routes_current_task(self):
        _, _ = self.ready_packet()
        _, gui = self.paper_gui(self.project)
        with patch.object(paper_app.subprocess, 'Popen') as launch:
            gui.open_encoding()
            self.pump(gui)
        command = launch.call_args.args[0]
        packet = Path(command[command.index('--handoff') + 1])
        self.assertTrue(packet.is_file())
        self.assertEqual(command[command.index('--task-id') + 1], 'scr_conversion')
        self.assertEqual(Path(command[2]).name, 'coding_app.py')
        loaded = encoding.load_handoff(packet)
        self.assertEqual(loaded['source_workspace']['project_id'], self.project['project_id'])
        self.assertFalse(self.errors)

    def test_image_encoding_preview_retains_pixel_lineage_and_approximate_label(self):
        _, packet, _, evidence = self.image_packet()
        self.gui.add_paths([packet])
        self.gui.build()
        self.pump()
        row = self.gui.result['observations'][0]
        self.assertTrue(row['approximate'])
        self.assertEqual(row['y'], .5)
        self.assertEqual(row['X_raw']['temperature_C'], 350)
        self.assertEqual(row['evidence'][0]['source_ref'], evidence['source_ref'])
        shown = self.gui.observations.item('0', 'values')
        self.assertEqual(shown[-1], '是')
        self.assertIn('像素点', self.gui.observation_detail.get('1.0', 'end'))

    def test_image_sample_alias_with_explicit_note_preserves_both_names(self):
        project, _, record, evidence = self.image_packet()
        record.update(sample_label='Cu-CeO2-standardized',
                      sample_mapping_note='Figure 1 sample A corresponds to Cu-CeO2-standardized in the text.')
        work.put_record(project, record, reviewer='fixture-reviewer', confirm=True)
        packet = self.export_packet(project)
        self.gui.add_paths([packet])
        self.gui.build()
        self.pump()
        row = self.gui.result['observations'][0]
        self.assertEqual(row['sample_label'], 'Cu-CeO2-standardized')
        self.assertEqual(row['evidence'][0]['sample_label'], 'sample A')
        self.assertIn('Figure 1', row['raw_record']['sample_mapping_note'])
        self.assertEqual(row['evidence'][0]['source_ref'], evidence['source_ref'])
        self.gui.describe_observation()
        detail=self.gui.observation_detail.get('1.0','end')
        self.assertIn('样品对应依据：',detail)
        self.assertIn('sample A',detail)
        self.assertIn('Figure 1 sample A corresponds',detail)

    def test_annotation_reread_preserves_human_mapping_and_requires_new_review(self):
        with self.parser(sample_label=''):
            work.extract_document(self.project)
            evidence = self.project['evidence'][0]
            work.review_evidence(self.project, evidence['evidence_id'], 'reviewed', 'first-reviewer')
            self.record(self.project, evidence)
            work.annotate_semantics(self.project, evidence['evidence_id'],
                                    {'sample_label': 'S1', 'assertion_scope': 'current_study'},
                                    'mapping-reviewer', 'The preceding table identifies this catalyst as S1.')
            work.extract_document(self.project)
        current = next(e for e in self.project['evidence'] if e['evidence_id'] == evidence['evidence_id'])
        self.assertEqual(current['sample_label'], 'S1')
        self.assertEqual(current['parser_interpretation']['sample_label'], '')
        self.assertEqual(current['review_status'], 'unreviewed')
        self.assertIn('preceding table', current['human_annotation']['note'])
        self.assertEqual(self.project['records'][0]['review_status'], 'draft')

    def test_parser_identity_change_keeps_stale_record_evidence_visible(self):
        with self.parser():
            work.extract_document(self.project)
        evidence = self.project['evidence'][0]
        work.review_evidence(self.project, evidence['evidence_id'], 'reviewed', 'fixture-reviewer')
        record = self.record(self.project, evidence)
        with self.parser(semantic_id_key='new-split-identity'):
            work.extract_document(self.project)
        old = next(e for e in self.project['evidence'] if e['evidence_id'] == evidence['evidence_id'])
        self.assertTrue(old['stale'])
        self.assertEqual(old['review_status'], 'stale')
        self.assertIn('关联证据已过期或缺失', work.record_issues(self.project, record))
        _, gui = self.paper_gui(self.project)
        gui.record_tree.selection_set(record['record_id'])
        gui.describe_record()
        self.assertIn('过期', gui.record_detail.get('1.0', 'end'))

    def test_changed_interpretation_cannot_inherit_previous_review(self):
        with self.parser():
            work.extract_document(self.project)
        evidence = self.project['evidence'][0]
        work.review_evidence(self.project, evidence['evidence_id'], 'reviewed', 'fixture-reviewer')
        with self.parser(value=80):
            work.extract_document(self.project)
        current = self.project['evidence'][0]
        self.assertEqual(current['evidence_id'], evidence['evidence_id'])
        self.assertEqual(current['review_status'], 'unreviewed')
        self.assertEqual(current['previous_interpretation']['value'], 90)

    def test_unconfirmed_human_annotation_is_preserved_when_parser_changes(self):
        with self.parser(sample_label=''):
            work.extract_document(self.project)
        evidence = self.project['evidence'][0]
        work.annotate_semantics(self.project, evidence['evidence_id'], {'sample_label': 'S1'},
                                'mapping-reviewer', 'Sample association checked against the preceding table.')
        with self.parser(sample_label='', value=80):
            work.extract_document(self.project)
        current = self.project['evidence'][0]
        self.assertEqual(current['review_status'], 'unreviewed')
        self.assertIn('preceding table', current['previous_interpretation']['human_annotation']['note'])

    def test_required_buttons_fit_minimum_width_without_becoming_clipped(self):
        # A fully transparent window permits actual geometry allocation while
        # remaining invisible to the user. All other tests remain withdrawn.
        self.root.attributes('-alpha', 0.0)
        self.root.geometry('1000x680+0+0')
        self.root.deiconify()
        self.root.update()
        for button in self.gui.buttons:
            with self.subTest(button=button.cget('text')):
                self.assertTrue(button.winfo_ismapped())
                self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth())
                right = button.winfo_rootx() - self.root.winfo_rootx() + button.winfo_width()
                self.assertLessEqual(right, self.root.winfo_width())
        self.root.withdraw()

    def test_paper_mapping_and_encoding_buttons_fit_minimum_window(self):
        window, gui = self.paper_gui(self.project)
        window.attributes('-alpha', 0.0)
        window.geometry('1020x700+0+0')
        window.deiconify()
        for tab, labels in ((1, ('确认句子含义', '核对样品与对照', '补充到已有记录')),
                            (3, ('交给第三板块：导出待编码包', '进入第三板块：数据编码', '打开论文结果文件夹'))):
            gui.tabs.select(tab)
            self.root.update()
            for label in labels:
                with self.subTest(button=label):
                    visible = [b for b in gui.controls if b.cget('text') == label and b.winfo_ismapped()]
                    self.assertEqual(len(visible), 1)
                    button = visible[0]
                    self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth())
                    right = button.winfo_rootx() - window.winfo_rootx() + button.winfo_width()
                    bottom = button.winfo_rooty() - window.winfo_rooty() + button.winfo_height()
                    self.assertLessEqual(right, window.winfo_width())
                    self.assertLessEqual(bottom, window.winfo_height())
        window.withdraw()

    def test_record_editor_exposes_material_fields_and_never_preconfirms(self):
        _, record = self.ready_packet()
        saved = []
        dialog = paper_app.RecordDialog(self.root, record, 'fixture-reviewer', lambda *args: saved.append(args))
        dialog.withdraw()
        self.assertFalse(dialog.confirm.get())
        self.assertIn('sample_mapping_note', dialog.vars)
        for name in encoding.FEATURE_SCHEMA:
            self.assertIn('feature:' + name, dialog.vars)
        for name in encoding.CHARACTERIZATION_FIELDS:
            self.assertEqual(dialog.vars['availability:' + name].get(), '尚未核对获得时点')
        dialog.vars['availability:BET_surface_area_m2_g'].set('预测该性能之前已知')
        dialog.vars['availability:metal_particle_size_nm'].set('预测之后 / 反应后才获得')
        dialog.vars['feature:active_metals'].set('Cu, Zn')
        dialog.vars['sample_mapping_note'].set('Sample alias checked against the original figure legend.')
        dialog.save()
        self.assertEqual(saved[0][0]['features']['active_metals'], ['Cu', 'Zn'])
        self.assertIn('figure legend', saved[0][0]['sample_mapping_note'])
        self.assertEqual(saved[0][0]['feature_availability']['BET_surface_area_m2_g'], 'before_prediction')
        self.assertEqual(saved[0][0]['feature_availability']['metal_particle_size_nm'], 'after_prediction')
        self.assertFalse(saved[0][2])


if __name__ == '__main__':
    unittest.main()
