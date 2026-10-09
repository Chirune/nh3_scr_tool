"""Exercise read-only paper import against real PDF/image/session exports."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image, ImageDraw
from pypdf import PdfWriter

import pipeline
from digitizer.session import add_points, export_session, load_session, save_session, set_calibration
from paper_image_bridge import read_image_evidence


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, content):
    Path(path).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")


def hash_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PaperImageBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="paper-image-bridge-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.pdf = self.root / "paper.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=240, height=180)
        with self.pdf.open("wb") as stream:
            writer.write(stream)
        self.batch_dir = self.root / "batch"
        self.batch_dir.mkdir()
        page_path, crop_path = self.batch_dir / "page.png", self.batch_dir / "crop.png"
        page = Image.new("RGB", (240, 180), "white")
        ImageDraw.Draw(page).line((20, 100, 135, 40), fill="black", width=2)
        page.save(page_path)
        page.crop((20, 30, 140, 110)).save(crop_path)
        page.close()
        self.paper = {
            "paper_id": "paper-one", "record_id": "record-one", "local_pdf": str(self.pdf),
            "source_sha256": hash_file(self.pdf), "title": "Sample adsorption", "doi": "10.1234/sample",
            "pages": [], "screening_status": "keep",
        }
        self.figure = {
            "figure_id": "figure-one", "paper_id": "paper-one", "record_id": "record-one",
            "figure_label": "Figure 1", "page": 1, "doi": self.paper["doi"], "title": self.paper["title"],
            "local_pdf": str(self.pdf), "source_sha256": self.paper["source_sha256"],
            "page_image_path": str(page_path), "page_image_sha256": hash_file(page_path),
            "crop_path": str(crop_path), "crop_sha256": hash_file(crop_path), "revision": 1,
            "review_status": "keep", "review_history": [], "screening_status": "keep",
            "bbox": [20, 30, 140, 110], "bbox_coordinate_system": "rendered_page_pixels",
            "caption": "Figure 1. Adsorption of sample A.", "chart_type": "xy",
            "locator_status": "manually_located", "quality_flags": [],
        }
        self.batch = {
            "schema_version": pipeline.SCHEMA, "batch_id": "batch-one", "run_dir": str(self.batch_dir),
            "papers": [self.paper], "figures": [self.figure], "reading_sessions": [],
            "waiting": [], "errors": [], "status": "complete",
        }
        self.batch_path = self.batch_dir / "batch.json"
        pipeline.save_batch(self.batch)
        self.path = pipeline.create_reading_session(self.batch, "figure-one")
        self.session, image = load_session(self.path)
        image.close()
        cal = {
            "x": {"p1": [0, 70], "p2": [100, 70], "v1": 0, "v2": 1,
                  "scale": "linear", "name": "Relative pressure", "unit": "无量纲"},
            "y": {"p1": [0, 70], "p2": [0, 10], "v1": 0, "v2": 10,
                  "scale": "linear", "name": "Quantity adsorbed", "unit": "mmol/g"},
        }
        set_calibration(self.session, cal)
        add_points(self.session, [{"px": 50, "py": 40}], "Adsorption", sample_label="sample A")
        self.session["roi"] = [0, 0, 119, 79]
        export_session(self.session, {"reviewed": True})

    def read(self):
        return read_image_evidence(self.batch_path, "paper-one")

    def rewrite_batch(self):
        # Direct fixture write avoids save_batch's reference merge by design.
        write_json(self.batch_path, self.batch)

    def assert_blocked(self, message):
        report = self.read()
        self.assertEqual(report["items"], [])
        self.assertIn(message, " ".join(report["warnings"]))
        return report

    def write_legacy_damaged_export(self):
        # Bypass today's export gate only to model a corrupt/old snapshot.
        # Production export now rejects these values before writing CSV.
        save_session(self.session)
        write_json(Path(self.session['last_export_dir']) / '读数与溯源.json', self.session)

    def test_real_reviewed_export_keeps_scientific_value_context_and_provenance(self):
        report = self.read()
        self.assertEqual(len(report["items"]), 1)
        item = report["items"][0]
        self.assertTrue(item["usable"])
        self.assertFalse(item["ml_ready"])
        self.assertEqual(item["review_status"], "reviewed")
        self.assertEqual((item["metric"], item["value"], item["unit"]), ("Quantity adsorbed", 5.0, "mmol/g"))
        self.assertEqual((item["branch"], item["kind"], item["operator"], item["value_high"]), ("image", "absolute", "eq", None))
        self.assertEqual(item["conditions"], {"x_name": "Relative pressure", "x_unit": "无量纲", "x_value": 0.5})
        self.assertNotIn("temperature", item["conditions"])
        self.assertEqual(item["x_name"], "Relative pressure")
        self.assertEqual(item["x_value"], 0.5)
        self.assertEqual(item["sample_label"], "sample A")
        self.assertEqual(item["sample_label_status"], "candidate")
        self.assertEqual(item["source_ref"]["page_pixel_x"], 70)
        self.assertEqual(item["source_ref"]["page_pixel_y"], 70)
        self.assertEqual(item["source_ref"]["source_pdf_sha256"], self.paper["source_sha256"])
        self.assertIn("图像近似", " ".join(item["warnings"]))

    def test_read_is_stable_and_does_not_modify_any_file_or_timestamp(self):
        def state():
            return {str(p.relative_to(self.root)): (hash_file(p), p.stat().st_mtime_ns) for p in self.root.rglob("*") if p.is_file()}
        before = state()
        first, second = self.read(), self.read()
        self.assertEqual(first, second)
        self.assertEqual(before, state())

    def test_only_selected_paper_is_read(self):
        other = copy.deepcopy(self.figure)
        other.update(figure_id="figure-two", paper_id="paper-two", record_id="record-two")
        self.batch["figures"].append(other)
        self.batch["papers"].append({**self.paper, "paper_id": "paper-two", "record_id": "record-two"})
        self.batch["reading_sessions"].append({"figure_id": "figure-two", "figure_revision": 1, "session_path": str(self.root / "missing.json")})
        self.rewrite_batch()
        self.assertEqual(len(self.read()["items"]), 1)
        report = read_image_evidence(self.batch_path, "paper-two")
        self.assertEqual(report["items"], [])
        self.assertTrue(report["warnings"])

    def test_unknown_paper_never_imports_another_paper(self):
        report = read_image_evidence(self.batch_path, "absent-paper")
        self.assertEqual(report["items"], [])
        self.assertIn("唯一对应", " ".join(report["warnings"]))

    def test_duplicate_session_references_do_not_duplicate_points(self):
        self.batch["reading_sessions"].append(copy.deepcopy(self.batch["reading_sessions"][0]))
        self.rewrite_batch()
        self.assertEqual(len(self.read()["items"]), 1)

    def test_superseded_duplicate_reference_blocks_old_export(self):
        duplicate = copy.deepcopy(self.batch["reading_sessions"][0])
        duplicate["superseded_by"] = "new-session.json"
        self.batch["reading_sessions"].append(duplicate)
        self.rewrite_batch()
        self.assert_blocked("更新的读数")

    def test_session_itself_marked_superseded_is_blocked(self):
        self.session["superseded_by"] = "new-session.json"
        save_session(self.session)
        self.assert_blocked("新项目替代")

    def test_old_revision_is_not_imported(self):
        self.figure["revision"] = 2
        self.rewrite_batch()
        self.assert_blocked("旧版本")

    def test_excluded_figure_is_not_imported(self):
        self.figure["review_status"] = "exclude"
        self.rewrite_batch()
        self.assert_blocked("图片已排除")

    def test_pending_figure_is_visible_but_unusable(self):
        self.figure["review_status"] = "review"
        self.rewrite_batch()
        item = self.read()["items"][0]
        self.assertFalse(item["usable"])
        self.assertIn("尚未标记保留", " ".join(item["warnings"]))

    def test_modified_pdf_page_crop_session_image_or_csv_is_blocked(self):
        candidates = [
            (self.pdf, "来源 PDF已变化"),
            (Path(self.figure["page_image_path"]), "整页原图已变化"),
            (Path(self.figure["crop_path"]), "候选裁图已变化"),
            (self.path.parent / "source.png", "读图原图已变化"),
            (Path(self.session["last_export_dir"]) / "读数数据.csv", "导出数值表已变化"),
        ]
        for path, message in candidates:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"changed")
                    self.assert_blocked(message)
                finally:
                    path.write_bytes(original)

    def test_unexported_edit_never_reuses_older_reviewed_export(self):
        self.session["points"][0]["sample_label"] = "edited sample"
        save_session(self.session)
        self.assert_blocked("未导出的修改")

    def test_review_flag_requires_a_new_export_after_a_change(self):
        self.session["reviewed"] = False
        save_session(self.session)
        self.assert_blocked("旧快照已过期")

    def test_latest_export_selected_and_review_is_not_inherited(self):
        old = self.read()["items"][0]["evidence_id"]
        export_session(self.session, {"reviewed": False})
        item = self.read()["items"][0]
        self.assertNotEqual(item["evidence_id"], old)
        self.assertEqual(item["review_status"], "unreviewed")
        self.assertFalse(item["usable"])

    def test_point_also_must_be_explicitly_reviewed(self):
        self.session["points"][0]["review_status"] = "unreviewed"
        save_session(self.session)
        write_json(Path(self.session["last_export_dir"]) / "读数与溯源.json", self.session)
        item = self.read()["items"][0]
        self.assertFalse(item["usable"])
        self.assertEqual(item["review_status"], "unreviewed")

    def test_missing_axis_name_does_not_create_scientific_metric(self):
        self.session["calibration"]["x"]["name"] = "横轴指标"
        self.session["calibration"]["y"]["name"] = ""
        export_session(self.session, {"reviewed": True})
        item = self.read()["items"][0]
        self.assertFalse(item["usable"])
        self.assertEqual(item["metric"], "")
        self.assertEqual(item["x_name"], "")

    def test_missing_units_remain_candidates_even_after_review(self):
        complete=copy.deepcopy(self.session['calibration'])
        for axis in ('x', 'y'):
            with self.subTest(axis=axis):
                cal=copy.deepcopy(complete)
                cal[axis]['unit']=''
                set_calibration(self.session,cal)
                export_session(self.session,{'reviewed':True})
                item=self.read()['items'][0]
                self.assertFalse(item['usable'])
                self.assertIn('单位',' '.join(item['warnings']))

    def test_missing_bar_category_and_unresolved_series_remain_candidates(self):
        self.session['chart_type']='bar'
        export_session(self.session,{'reviewed':True})
        self.assertFalse(self.read()['items'][0]['usable'])
        self.session['points'][0]['category']='Catalyst A'
        for name in ('', '未命名系列 1', '颜色系列 1（图例待核对）'):
            with self.subTest(name=name):
                self.session['points'][0]['series_label']=name
                export_session(self.session,{'reviewed':True})
                self.assertFalse(self.read()['items'][0]['usable'])

    def test_stored_values_and_resolution_must_match_calibrated_pixels(self):
        point=copy.deepcopy(self.session['points'][0])
        for field in ('x','y','x_pixel_resolution','y_pixel_resolution'):
            with self.subTest(field=field):
                self.session['points'][0]=copy.deepcopy(point)
                self.session['points'][0][field]+=1
                self.write_legacy_damaged_export()
                self.assert_blocked('像素与标定')

    def test_stale_point_calibration_id_is_blocked(self):
        self.session['points'][0]['calibration_id']='previous-calibration'
        self.write_legacy_damaged_export()
        self.assert_blocked('标定版本')

    def test_point_outside_reviewed_roi_is_blocked(self):
        self.session['roi']=[0,0,30,30]
        self.write_legacy_damaged_export()
        self.assert_blocked('绘图区之外')

    def test_continuous_samples_keep_their_non_experimental_origin(self):
        self.session['points'][0]['method']='automatic_curve_sample'
        export_session(self.session,{'reviewed':True})
        item=self.read()['items'][0]
        self.assertEqual(item['value_origin'],'image_curve_samples_approximate')
        self.assertFalse(item['ml_ready'])
        self.assertIn('不等于独立实验',' '.join(item['warnings']))

    def test_color_trace_samples_also_keep_their_non_experimental_origin(self):
        self.session['points'][0]['method']='color_trace'
        export_session(self.session,{'reviewed':True})
        item=self.read()['items'][0]
        self.assertEqual(item['value_origin'],'image_curve_samples_approximate')
        self.assertFalse(item['ml_ready'])

    def test_tiny_log_values_and_negative_reversed_axis_still_import(self):
        cal=copy.deepcopy(self.session['calibration'])
        cal['x'].update(v1=2,v2=-2)
        cal['y'].update(v1=1e-120,v2=1e-100,scale='log10')
        set_calibration(self.session,cal)
        export_session(self.session,{'reviewed':True})
        item=self.read()['items'][0]
        self.assertTrue(item['usable'])
        self.assertAlmostEqual(item['value']/1e-110,1)
        self.assertEqual(item['x_value'],0)

    def test_wrong_tiny_log_value_is_not_hidden_by_absolute_tolerance(self):
        cal=copy.deepcopy(self.session['calibration'])
        cal['y'].update(v1=1e-120,v2=1e-100,scale='log10')
        set_calibration(self.session,cal)
        self.session['points'][0]['y']*=2
        self.write_legacy_damaged_export()
        self.assert_blocked('像素与标定')

    def test_vertical_bar_keeps_category_separate_from_x_and_sample(self):
        self.session["chart_type"] = "bar"
        self.session["points"][0].update(category="Catalyst A", sample_label="")
        export_session(self.session, {"reviewed": True})
        item = self.read()["items"][0]
        self.assertEqual(item["value"], 5)
        self.assertEqual(item["conditions"], {})
        self.assertNotIn("x_value", item)
        self.assertEqual(item["sample_label"], "")
        self.assertEqual(item["category"], "Catalyst A")

    def test_horizontal_bar_uses_x_as_value_not_a_condition(self):
        self.session["chart_type"] = "bar_horizontal"
        self.session["points"][0]["category"] = "Catalyst A"
        export_session(self.session, {"reviewed": True})
        item = self.read()["items"][0]
        self.assertEqual((item["value"], item["metric"]), (0.5, "Relative pressure"))
        self.assertEqual(item["conditions"], {})

    def test_no_export_is_an_actionable_waiting_reason(self):
        self.session["exports"] = []
        save_session(self.session)
        self.assert_blocked("尚无数值导出")

    def test_missing_newest_snapshot_does_not_fall_back_to_previous_export(self):
        self.session["exports"].append({"directory": "export_missing", "csv_sha256": "0" * 64})
        save_session(self.session)
        report = self.read()
        self.assertEqual(report["items"], [])
        self.assertTrue(report["warnings"])

    def test_export_path_must_remain_in_session(self):
        self.session["exports"][-1]["directory"] = "../../escape"
        save_session(self.session)
        self.assert_blocked("导出目录不在")

    def test_moved_batch_rebases_paths_and_keeps_evidence_id(self):
        old_item = self.read()["items"][0]
        relocated = self.root / "moved-batch"
        shutil.copytree(self.batch_dir, relocated)
        report = read_image_evidence(relocated, "paper-one")
        self.assertEqual(len(report["items"]), 1, report["warnings"])
        item = report["items"][0]
        self.assertEqual(item["evidence_id"], old_item["evidence_id"])
        self.assertTrue(Path(item["source_ref"]["session_path"]).is_relative_to(relocated))

    def test_pdf_metadata_claiming_a_different_paper_is_blocked(self):
        self.session["source_metadata"]["figure_context"]["record_id"] = "different-paper"
        export_session(self.session, {"reviewed": True})
        self.assert_blocked("论文记录不匹配")

    def test_automatic_panel_keeps_current_parent_crop_and_page_transform(self):
        from digitizer.auto_panels import create_panel_session
        with Image.open(self.path.parent / "source.png") as image:
            panel, panel_path = create_panel_session(self.session, image, {"label": "(a)", "bbox": [10, 10, 100, 75]})
        set_calibration(panel, {
            "x": {"p1": [0, 60], "p2": [80, 60], "v1": 0, "v2": 1, "name": "Relative pressure", "unit": "无量纲"},
            "y": {"p1": [0, 60], "p2": [0, 0], "v1": 0, "v2": 10, "name": "Quantity adsorbed", "unit": "mmol/g"},
        })
        add_points(panel, [{"px": 40, "py": 30}], "Adsorption", sample_label="sample A")
        export_session(panel, {"reviewed": True})
        self.batch["reading_sessions"] = [{"session_path": str(panel_path), "figure_id": "figure-one", "figure_revision": 1}]
        self.rewrite_batch()
        item = self.read()["items"][0]
        self.assertEqual(item["source_ref"]["page_pixel_x"], 70)
        self.assertEqual(item["source_ref"]["page_pixel_y"], 70)
        self.assertEqual(item["source_ref"]["automatic_subfigure"]["label"], "(a)")
        self.assertTrue(item["usable"])


if __name__ == "__main__":
    unittest.main()
