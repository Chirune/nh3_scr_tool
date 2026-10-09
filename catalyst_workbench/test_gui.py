"""Local, hidden-window tests of coordinate mapping and human-review actions."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image
import app


class GuiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.temp = tempfile.TemporaryDirectory(prefix="figure_pipeline_gui_test_")
        self.directory = Path(self.temp.name)
        self.page = self.directory / "demo_page.png"
        Image.new("RGB", (1003, 757), "white").save(self.page)
        self.gui = app.FigurePipelineApp(self.root, output_root=self.directory)
        self.batch = {
            "run_dir": str(self.directory), "papers": [{"paper_id": "demo"}],
            "input_summary": {}, "waiting": [], "errors": [],
            "figures": [self.figure("demo_a"), self.figure("demo_b")],
        }

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.update_idletasks()
            self.root.destroy()
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def figure(self, figure_id):
        return {
            "figure_id": figure_id, "paper_id": "demo", "doi": "",
            "title": "SYNTHETIC GUI TEST ONLY", "page": 1,
            "figure_label": "Fig. 1", "caption": "Fig. 1. Synthetic fixture, not a research result.",
            "bbox": [100, 100, 800, 600], "page_image_path": str(self.page),
            "crop_path": "", "chart_type": "xy", "locator_status": "graphics_caption_candidate",
            "quality_flags": ["chart_type_hint_requires_review"], "review_status": "unreviewed",
        }

    @staticmethod
    def update(batch, figure_id, decision="review", bbox=None, figure_label=None, chart_type=None):
        figure = next(f for f in batch["figures"] if f["figure_id"] == figure_id)
        figure["review_status"] = decision
        if bbox is not None:
            figure["bbox"] = list(bbox)
        if figure_label is not None:
            figure["figure_label"] = figure_label
        if chart_type is not None:
            figure["chart_type"] = chart_type
        return batch

    def test_paper_scope_only_displays_one_article_without_discarding_other_figures(self):
        self.batch['figures'][1]['paper_id']='another-paper'
        self.gui.paper_scope_id='demo'
        self.gui.set_batch(self.batch)
        self.assertEqual(self.gui.tree.get_children(),('demo_a',))
        self.assertEqual(len(self.gui.batch['figures']),2)
        self.assertIn('有图号 1 张',self.gui.count_var.get())

    def test_coordinate_mapping_uses_actual_render_dimensions(self):
        self.gui.set_batch(self.batch)
        view = self.gui.page_view
        view.fit_mode = False
        view.scale = 0.333
        view.draw()
        expected_x_scale = round(1003 * 0.333) / 1003
        expected_y_scale = round(757 * 0.333) / 757
        self.assertEqual(view.scale_x, expected_x_scale)
        self.assertEqual(view.scale_y, expected_y_scale)
        x, y = view.to_original(SimpleNamespace(x=110, y=90))
        self.assertAlmostEqual(x, 100 / expected_x_scale)
        self.assertAlmostEqual(y, 80 / expected_y_scale)

    def test_multiple_decisions_update_every_selected_figure(self):
        self.gui.set_batch(self.batch)
        self.gui.tree.selection_set("demo_a", "demo_b")
        with patch.object(app, "update_figure", side_effect=self.update) as update:
            self.gui.set_decision("keep")
        self.assertEqual(update.call_count, 2)
        self.assertEqual([f["review_status"] for f in self.batch["figures"]], ["keep", "keep"])

    def test_default_queue_hides_unlabelled_legacy_regions_and_recovery_keeps_edits(self):
        self.batch["figures"][1].update(caption="", figure_label="", review_status="keep")
        self.gui.set_batch(self.batch)
        self.assertEqual(self.gui.tree.get_children(), ("demo_a",))
        self.assertIn("暂存查漏 1 张", self.gui.count_var.get())
        self.gui.scope_var.set(app.SCOPE_VALUES[1])
        self.gui.refresh_list()
        self.assertEqual(self.gui.tree.get_children(), ("demo_b",))
        self.assertEqual(self.gui.current_id, "demo_b")
        self.assertEqual(self.batch["figures"][1]["review_status"], "keep")
        self.gui.scope_var.set(app.SCOPE_VALUES[2])
        self.gui.refresh_list()
        self.assertEqual(self.gui.tree.get_children(), ("demo_a", "demo_b"))

    def test_only_unlabelled_candidates_explain_recovery_without_pdf_reimport(self):
        for f in self.batch["figures"]:
            f.update(caption="", figure_label="")
        self.gui.set_batch(self.batch)
        self.assertEqual(self.gui.tree.get_children(), ())
        self.assertIn("未识别到编号图注", self.gui.page_view.empty_message)
        self.assertIn("不需要重新导入", self.gui.page_view.empty_message)
        self.assertIsNone(self.gui.page_view.empty_action)
        self.gui.scope_var.set(app.SCOPE_VALUES[1])
        self.gui.refresh_list()
        self.assertEqual(len(self.gui.tree.get_children()), 2)

    def test_box_correction_returns_candidate_to_review(self):
        self.gui.set_batch(self.batch)
        self.batch["figures"][0]["review_status"] = "keep"
        with patch.object(app, "update_figure", side_effect=self.update):
            self.gui.correct_box([120, 110, 850, 610])
        figure = self.batch["figures"][0]
        self.assertEqual(figure["review_status"], "review")
        self.assertEqual(figure["bbox"], [120, 110, 850, 610])
        self.assertEqual(self.gui.page_view.box, figure["bbox"])

    def test_excluded_candidate_cannot_launch_digitizer(self):
        self.batch["figures"][0]["review_status"] = "exclude"
        self.gui.set_batch(self.batch)
        with patch.object(app, "open_digitizer") as launch, patch.object(app.messagebox, "showinfo"):
            self.gui.launch_digitizer()
        launch.assert_not_called()

    def test_unreviewed_candidate_may_open_for_inspection(self):
        self.gui.set_batch(self.batch)
        with patch.object(app, "open_digitizer", return_value=self.directory / "session.json") as launch:
            self.gui.launch_digitizer()
        launch.assert_called_once_with(self.batch, "demo_a")
        self.assertEqual(self.batch["figures"][0]["review_status"], "unreviewed")

    def test_independent_subfigure_does_not_replace_parent(self):
        self.gui.set_batch(self.batch)
        def duplicate(batch, figure_id):
            parent = next(f for f in batch["figures"] if f["figure_id"] == figure_id)
            child = copy.deepcopy(parent)
            child["figure_id"] = "demo_child"
            batch["figures"].append(child)
            return child["figure_id"]
        with patch.object(app, "create_subfigure", side_effect=duplicate):
            self.gui.duplicate_subfigure()
        self.assertEqual(len(self.batch["figures"]), 3)
        self.assertEqual(self.gui.current_id, "demo_child")
        self.assertEqual(self.gui.tree.selection(), ("demo_child",))
        self.assertEqual(self.batch["figures"][0]["figure_id"], "demo_a")

    def test_scan_runs_outside_gui_thread_and_can_cancel(self):
        self.gui.input_var.set(str(self.directory))
        self.gui.kind_var.set("folder")
        caller = threading.get_ident()
        workers = []
        def build(*args, progress=None, cancel_event=None, **kwargs):
            workers.append(threading.get_ident())
            progress("Synthetic progress")
            cancel_event.wait(timeout=2)
            return self.batch
        with patch.object(app, "build_batch", side_effect=build):
            self.gui.start_scan()
            self.gui.cancel_scan()
            deadline = time.monotonic() + 3
            while self.gui.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
        self.assertFalse(self.gui.busy)
        self.assertTrue(self.gui.cancel_event.is_set())
        self.assertTrue(workers)
        self.assertNotEqual(workers[0], caller)
        self.assertIs(self.gui.batch, self.batch)

    def test_no_downloaded_pdf_explains_missing_files_without_opening_dialog(self):
        self.batch["papers"] = []
        self.batch["figures"] = []
        self.batch["waiting"] = ([{"reason_code": "pdf_not_downloaded"}] * 20
                                 + [{"reason_code": "screening_review_required"}] * 25)
        with patch.object(app.filedialog, "askdirectory") as dialog:
            self.gui.set_batch(self.batch)
        dialog.assert_not_called()
        self.assertIn("入选待获取 PDF 20 篇", self.gui.summary_var.get())
        self.assertIn("实际可扫描 PDF 0 篇", self.gui.summary_var.get())
        self.assertIn("筛选待复核 25 篇", self.gui.summary_var.get())
        self.assertIn("尚无已下载 PDF 可扫描", self.gui.page_view.empty_message)
        self.assertIn("不代表筛选通过", self.gui.page_view.empty_message)

    def test_collect_without_reading_project_explains_next_step_and_creates_no_empty_csv(self):
        self.gui.set_batch(self.batch)
        with patch.object(app, "collect_readings") as collect, patch.object(app.messagebox, "showinfo") as info:
            self.gui.collect()
        collect.assert_not_called()
        self.assertIn("还没有图上的数值", self.gui.status_var.get())
        self.assertIn("自动识别并生成数值表", info.call_args.args[1])
        self.assertFalse(list(self.directory.glob("汇总_*")))

    def test_visible_read_button_requests_automatic_extraction(self):
        self.gui.set_batch(self.batch)
        self.gui.current_id = self.batch["figures"][0]["figure_id"]
        with patch.object(self.gui, "save_edits"), patch.object(app, "open_digitizer", return_value=self.directory / "session.json") as reader:
            self.gui.read_button.invoke()
        reader.assert_called_once_with(self.batch, self.gui.current_id, auto_read=True)
        self.assertIn("自动读数", self.gui.status_var.get())

    def test_collect_unexported_reading_project_explains_export_before_collection(self):
        session = self.directory / "session.json"
        session.write_text(json.dumps({"points": [], "exports": []}), encoding="utf-8")
        self.batch["reading_sessions"] = [{"session_path": str(session)}]
        self.gui.set_batch(self.batch)
        with patch.object(app, "collect_readings") as collect, patch.object(app.messagebox, "showinfo") as info:
            self.gui.collect()
        collect.assert_not_called()
        self.assertEqual(info.call_args.args[0], "先在读数窗口导出")

    def test_collection_with_zero_valid_rows_reports_reason_instead_of_success(self):
        session = self.directory / "session.json"
        session.write_text(json.dumps({"exports": [{"directory": "export_fixture"}]}), encoding="utf-8")
        self.batch["reading_sessions"] = [{"session_path": str(session)}]
        summary = {"row_count": 0, "skipped": [{"reason": "图框在导出后发生变化"}]}
        (self.directory / "汇总说明.json").write_text(json.dumps(summary), encoding="utf-8")
        self.gui.set_batch(self.batch)
        with patch.object(app, "collect_readings", return_value=self.directory / "图片读数汇总.csv"), patch.object(app.messagebox, "showinfo") as info:
            self.gui.collect()
        self.assertEqual(info.call_args.args[0], "没有可汇总的有效数值")
        self.assertIn("图框在导出后发生变化", info.call_args.args[1])

    def test_scanned_without_candidates_is_not_reported_as_missing_pdf(self):
        self.batch["papers"] = [{"paper_id": "demo", "scan_status": "scanned"}]
        self.batch["figures"] = []
        self.gui.set_batch(self.batch)
        self.assertIn("已扫描，但未找到候选图", self.gui.page_view.empty_message)
        self.assertNotIn("尚无已下载", self.gui.page_view.empty_message)

    def test_waiting_reason_groups_keep_review_and_unreadable_files_separate(self):
        counts = self.gui.waiting_counts({"waiting": [
            {"reason_code": "pdf_not_downloaded"},
            {"reason_code": "fulltext_not_local_pdf"},
            {"reason_code": "screening_review_required"},
            {"reason_code": "local_file_unavailable"},
        ]})
        self.assertEqual(counts, {"missing_pdf": 2, "review": 1, "other": 1})

    def real_crop_fixture(self):
        self.batch["figures"] = self.batch["figures"][:1]
        self.batch["input_summary"] = {}
        figure = self.batch["figures"][0]
        crop_path = self.directory / "initial_crop.png"
        with Image.open(self.page) as image:
            image.crop(tuple(figure["bbox"])).save(crop_path)
        figure.update({"crop_path": str(crop_path), "original_bbox": list(figure["bbox"]),
                       "revision": 1, "review_history": [],
                       "page_image_sha256": hashlib.sha256(self.page.read_bytes()).hexdigest(),
                       "crop_sha256": hashlib.sha256(crop_path.read_bytes()).hexdigest()})
        app.save_batch(self.batch)
        self.gui.set_batch(self.batch)
        return figure

    def test_confirm_crop_persists_new_image_and_preserves_full_page(self):
        figure = self.real_crop_fixture()
        app.update_figure(self.batch, figure["figure_id"], decision="keep")
        self.gui.filter_var.set("保留")
        self.gui.refresh_list()
        original_hash = hashlib.sha256(self.page.read_bytes()).hexdigest()
        with Image.open(self.page) as image:
            dialog = app.CropDialog(self.root, image, figure["bbox"], self.gui.correct_box, show=False)
        dialog.stage_box([120, 130, 720, 530])
        self.assertEqual(figure["bbox"], [100, 100, 800, 600])
        dialog.confirm()
        saved = json.loads((self.directory / "batch.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["figures"][0]["bbox"], [120, 130, 720, 530])
        with Image.open(saved["figures"][0]["crop_path"]) as cropped:
            self.assertEqual(cropped.size, (600, 400))
        self.assertEqual(hashlib.sha256(self.page.read_bytes()).hexdigest(), original_hash)
        self.assertEqual(self.gui.notebook.index("current"), 1)
        self.assertEqual(self.gui.current_id, figure["figure_id"])
        self.assertTrue(self.gui.restore_original_box())
        self.assertEqual(figure["bbox"], figure["original_bbox"])

    def test_cancel_crop_does_not_change_batch_or_images(self):
        figure = self.real_crop_fixture()
        before = (self.directory / "batch.json").read_bytes()
        image_before = Path(figure["crop_path"]).read_bytes()
        with Image.open(self.page) as image:
            dialog = app.CropDialog(self.root, image, figure["bbox"], self.gui.correct_box, show=False)
        dialog.stage_box([150, 160, 750, 560])
        dialog.cancel()
        self.assertEqual((self.directory / "batch.json").read_bytes(), before)
        self.assertEqual(Path(figure["crop_path"]).read_bytes(), image_before)
        self.assertEqual(figure["bbox"], [100, 100, 800, 600])

    def test_crop_drag_maps_scaled_display_to_original_pixels(self):
        with Image.open(self.page) as image:
            dialog = app.CropDialog(self.root, image, [100, 100, 800, 600], lambda box: None, show=False)
        view = dialog.page_view
        view.fit_mode = False
        view.scale = 0.5
        view.draw()
        view.on_press(SimpleNamespace(x=70, y=60))
        view.on_release(SimpleNamespace(x=270, y=210))
        expected = [round(60 / view.scale_x), round(50 / view.scale_y),
                    round(260 / view.scale_x), round(200 / view.scale_y)]
        self.assertEqual(dialog.pending_bbox, expected)
        dialog.cancel()

    def test_crop_and_read_actions_visible_at_1366x768_with_large_fonts(self):
        self.root.update()
        self.root.destroy()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.attributes("-alpha", 0.0)
        self.root.tk.call("tk", "scaling", 2.0)
        self.gui = app.FigurePipelineApp(self.root, output_root=self.directory)
        self.gui.set_batch(self.batch)
        self.root.geometry("1280x680+0+0")
        self.root.deiconify()
        self.root.update_idletasks()
        self.root.update()
        for button in (self.gui.crop_button, self.gui.read_button, self.gui.restore_crop_button):
            self.assertTrue(button.winfo_ismapped())
            self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight() - 2)
            self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth() - 2)
            x = button.winfo_rootx() - self.root.winfo_rootx()
            y = button.winfo_rooty() - self.root.winfo_rooty()
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + button.winfo_width(), self.root.winfo_width())
            self.assertLessEqual(y + button.winfo_height(), self.root.winfo_height())

    def test_crop_dialog_confirm_and_cancel_visible_with_large_fonts(self):
        self.root.tk.call("tk", "scaling", 2.0)
        with Image.open(self.page) as image:
            dialog = app.CropDialog(self.root, image, [100, 100, 800, 600], lambda box: None, show=False)
        try:
            dialog.attributes("-alpha", 0.0)
            dialog.geometry("820x560+0+0")
            dialog.deiconify()
            self.root.update_idletasks()
            self.root.update()
            footer = dialog.confirm_button.master
            buttons = [child for child in footer.winfo_children() if isinstance(child, app.ttk.Button)]
            self.assertEqual({str(button.cget("text")) for button in buttons}, {"确认裁剪并保存", "取消，不修改"})
            for button in buttons:
                self.assertTrue(button.winfo_ismapped())
                self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth() - 2)
                self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight() - 2)
                x = button.winfo_rootx() - dialog.winfo_rootx()
                y = button.winfo_rooty() - dialog.winfo_rooty()
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + button.winfo_width(), dialog.winfo_width())
                self.assertLessEqual(y + button.winfo_height(), dialog.winfo_height())
            self.assertGreater(dialog.page_view.canvas.winfo_height(), 100)
            self.assertGreater(dialog.preview_view.canvas.winfo_width(), 100)
        finally:
            dialog.cancel()


if __name__ == "__main__":
    unittest.main()
