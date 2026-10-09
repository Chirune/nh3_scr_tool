"""Offline bridge integration checks; no GUI, network or locator fixtures."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageDraw
from pypdf import PdfWriter

import pipeline
from digitizer.session import add_points, export_session, load_session, save_session, set_calibration


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def csv_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


class PipelineTests(unittest.TestCase):
    def test_stale_window_save_keeps_automatic_project_references(self):
        batch, _ = self.make_batch()
        stale = copy.deepcopy(batch)
        path = pipeline.create_reading_session(batch, batch["figures"][0]["figure_id"])
        pipeline.save_batch(stale)
        saved = read_json(Path(batch["run_dir"]) / "batch.json")
        self.assertEqual([ref["session_path"] for ref in saved["reading_sessions"]], [str(path)])
        pipeline.save_batch(stale)
        self.assertEqual(len(stale["reading_sessions"]), 1)

    def test_open_reader_propagates_automatic_flag(self):
        batch, _ = self.make_batch()
        with mock.patch.object(pipeline.subprocess, "Popen") as process:
            path = pipeline.open_digitizer(batch, batch["figures"][0]["figure_id"], auto_read=True)
        args = process.call_args.args[0]
        self.assertIn("--auto-read", args)
        self.assertIn(str(path), args)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="figure_pipeline_test_")
        self.root = Path(self.temp.name).resolve()
        self.pdf = self.root / "真实结构_合成论文.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=150)
        with self.pdf.open("wb") as stream:
            writer.write(stream)
        self.pdf_hash = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        self.output = self.root / "输出"

    def tearDown(self):
        self.temp.cleanup()

    def record(self, **updates):
        record = {
            "id": "screening-record-1", "doi": "10.1234/demo", "title": "合成测试论文",
            "local_path": str(self.pdf), "local_sha256": self.pdf_hash,
            "effective_decision": "target", "manual_decision": "", "warnings": [],
            "screening": {"decision": "target", "reason": "合成测试"},
        }
        record.update(updates)
        return record

    def run_file(self, records=None):
        path = self.root / "run.json"
        path.write_text(json.dumps({"profile": "scr_ammonia", "records": records if records is not None else [self.record()]}, ensure_ascii=False), encoding="utf-8")
        return path

    def fake_locator(self, pdf_path, output_dir, progress=None, cancel_event=None):
        """Controlled image geometry, with a real PDF as the source identity."""
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        page_path = destination / "page_001.png"
        crop_path = destination / "figure_001.png"
        box = [20, 30, 140, 110]
        page = Image.new("RGB", (200, 150), "white")
        draw = ImageDraw.Draw(page)
        draw.line((30, 100, 130, 40), fill=(220, 30, 40), width=2)
        page.save(page_path)
        crop = page.crop(box)
        crop.save(crop_path)
        crop.close()
        page.close()
        return {
            "source_sha256": pipeline.digest(pdf_path), "warnings": [],
            "pages": [{"page": 1, "image_path": str(page_path), "image_width": 200, "image_height": 150}],
            "figures": [{
                "page": 1, "page_image_path": str(page_path), "crop_path": str(crop_path),
                "bbox": box, "bbox_coordinate_system": "rendered_page_pixels",
                "bbox_method": "test_known_geometry", "figure_label": "Fig. 1",
                "caption": "Fig. 1. Synthetic data used only for tests.",
                "chart_type": "xy", "locator_status": "candidate", "quality_flags": [],
            }],
        }

    def make_batch(self, records=None):
        with mock.patch.object(pipeline, "locate_pdf", side_effect=self.fake_locator) as locate:
            batch = pipeline.build_batch(self.run_file(records), output_root=self.output)
        return batch, locate

    def make_export(self, batch):
        figure = batch["figures"][0]
        pipeline.update_figure(batch, figure["figure_id"], decision="keep")
        path = pipeline.create_reading_session(batch, figure["figure_id"])
        session, image = load_session(path)
        image.close()
        cal = {
            "x": {"p1": [10, 70], "v1": 0, "p2": [110, 70], "v2": 100, "scale": "linear", "name": "温度", "unit": "°C"},
            "y": {"p1": [10, 70], "v1": 0, "p2": [10, 10], "v2": 60, "scale": "linear", "name": "转化率", "unit": "%"},
        }
        set_calibration(session, cal)
        add_points(session, [{"px": 60, "py": 40}], "Series A", sample_label="Sample A")
        export_session(session, {"reviewed": True})
        return path, session

    def test_excluded_missing_and_review_records_do_not_reach_locator(self):
        records = [
            self.record(),
            self.record(id="excluded", manual_decision="non_target"),
            self.record(id="missing", local_path="", local_sha256=""),
            self.record(id="review", effective_decision="review"),
        ]
        batch, locate = self.make_batch(records)
        self.assertEqual(locate.call_count, 1)
        self.assertEqual(len(batch["figures"]), 1)
        self.assertEqual(len(batch["waiting"]), 2)
        self.assertEqual(batch["input_summary"]["excluded_count"], 1)
        self.assertEqual(batch["figures"][0]["record_id"], "screening-record-1")

    def test_no_downloaded_pdfs_means_no_locator_calls(self):
        batch, locate = self.make_batch([self.record(local_path="", downloaded_fulltext="fulltext.xml")])
        locate.assert_not_called()
        self.assertEqual(batch["figures"], [])
        self.assertEqual(len(batch["waiting"]), 1)

    def test_candidates_and_source_provenance_are_persisted(self):
        batch, _ = self.make_batch()
        saved = read_json(Path(batch["run_dir"]) / "batch.json")
        figure = saved["figures"][0]
        self.assertEqual(figure["source_sha256"], self.pdf_hash)
        self.assertEqual(figure["doi"], "10.1234/demo")
        self.assertEqual(figure["bbox"], [20, 30, 140, 110])
        self.assertEqual(figure["crop_sha256"], pipeline.digest(figure["crop_path"]))
        self.assertEqual(figure["page_image_sha256"], pipeline.digest(figure["page_image_path"]))
        self.assertEqual(figure["review_status"], "unreviewed")
        self.assertTrue((Path(batch["run_dir"]) / "候选图清单.csv").is_file())

    def test_new_scan_routes_unlabelled_regions_to_recovery_csv(self):
        def mixed_locator(*args, **kwargs):
            result = self.fake_locator(*args, **kwargs)
            result["figures"].append(dict(result["figures"][0], figure_label="", caption="", locator_status="graphics_without_caption"))
            result["figures"].append(dict(result["figures"][0], figure_label="Figure S2", caption="Figure S2. Scanned boundary pending.", locator_status="page_fallback"))
            return result
        with mock.patch.object(pipeline, "locate_pdf", side_effect=mixed_locator):
            batch = pipeline.build_batch(self.run_file(), output_root=self.output)
        root = Path(batch["run_dir"])
        self.assertEqual(len(batch["figures"]), 3)
        self.assertEqual([f["figure_label"] for f in csv_rows(root / "候选图清单.csv")], ["Fig. 1", "Figure S2"])
        other = csv_rows(root / "未识别图号_查漏清单.csv")
        self.assertEqual(len(other), 1)
        self.assertEqual(other[0]["caption_filter_reason"], "caption_missing")
        report = read_json(root / "图号筛选统计.json")
        self.assertEqual(report["numbered_candidate_count"], 2)
        self.assertEqual(report["unmatched_candidate_count"], 1)
        self.assertTrue(all(f["review_status"] == "unreviewed" for f in batch["figures"]))

    def test_legacy_unlabelled_review_and_readings_survive_default_filter(self):
        batch, _ = self.make_batch()
        batch["figures"][0].update(figure_label="", caption="", locator_status="graphics_without_caption")
        session_path, session = self.make_export(batch)
        original_csv = Path(session["last_export_dir"]) / "读数数据.csv"
        export_bytes = original_csv.read_bytes()
        batch_path = Path(batch["run_dir"]) / "batch.json"
        legacy = read_json(batch_path)
        legacy.pop("figure_selection")
        batch_path.write_text(json.dumps(legacy), encoding="utf-8")
        loaded = pipeline.load_batch(batch_path)
        pipeline.save_batch(loaded)
        self.assertEqual(loaded["figures"][0]["review_status"], "keep")
        self.assertEqual(loaded["reading_sessions"][0]["session_path"], str(session_path))
        self.assertEqual(csv_rows(batch_path.parent / "候选图清单.csv"), [])
        self.assertEqual(len(csv_rows(batch_path.parent / "未识别图号_查漏清单.csv")), 1)
        self.assertEqual(len(csv_rows(pipeline.collect_readings(loaded))), 1)
        self.assertEqual(original_csv.read_bytes(), export_bytes)

    def test_reframing_preserves_old_crop_and_history(self):
        batch, _ = self.make_batch()
        figure = batch["figures"][0]
        old_path, old_bytes = Path(figure["crop_path"]), Path(figure["crop_path"]).read_bytes()
        pipeline.update_figure(batch, figure["figure_id"], decision="keep", bbox=[25, 35, 145, 115])
        self.assertEqual(figure["revision"], 2)
        self.assertEqual(old_path.read_bytes(), old_bytes)
        self.assertNotEqual(Path(figure["crop_path"]), old_path)
        self.assertEqual(figure["review_history"][-1]["previous"]["bbox"], [20, 30, 140, 110])
        previous_revised_path = Path(figure["crop_path"])
        previous_revised_bytes = previous_revised_path.read_bytes()
        pipeline.update_figure(batch, figure["figure_id"], decision="review", bbox=[30, 40, 150, 120])
        self.assertEqual(figure["revision"], 3)
        self.assertEqual(previous_revised_path.read_bytes(), previous_revised_bytes)

    def test_reading_session_is_headless_and_keeps_pdf_identity_and_transform(self):
        batch, _ = self.make_batch()
        figure = batch["figures"][0]
        with mock.patch.object(pipeline.subprocess, "Popen", side_effect=AssertionError("GUI must not start")) as popen:
            path = pipeline.create_reading_session(batch, figure["figure_id"])
        popen.assert_not_called()
        session = read_json(path)
        self.assertEqual(session["doi"], "10.1234/demo")
        self.assertEqual(session["source_metadata"]["source_sha256"], self.pdf_hash)
        self.assertEqual(session["source_metadata"]["source_path"], str(self.pdf))
        self.assertEqual(session["source_metadata"]["page"], 1)
        context = session["source_metadata"]["figure_context"]
        self.assertEqual(context["crop_to_page"], {"offset_x": 20, "offset_y": 30, "scale_x": 1.0, "scale_y": 1.0})
        self.assertEqual(context["record_id"], "screening-record-1")
        self.assertEqual(session["source_metadata"]["crop_source_metadata"]["source_sha256"], figure["crop_sha256"])

    def test_export_collection_coordinates_map_back_to_page(self):
        batch, _ = self.make_batch()
        _, session = self.make_export(batch)
        target = pipeline.collect_readings(batch)
        rows = csv_rows(target)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(float(row["x_value"]), 50)
        self.assertAlmostEqual(float(row["y_value"]), 30)
        self.assertAlmostEqual(float(row["page_pixel_x"]), 80)
        self.assertAlmostEqual(float(row["page_pixel_y"]), 70)
        self.assertEqual(row["source_sha256"], self.pdf_hash)
        self.assertEqual(row["doi"], "10.1234/demo")
        self.assertEqual(row["source_screening_record_id"], "screening-record-1")
        self.assertEqual(row["current_session_has_unexported_changes"], "False")

    def test_new_box_excludes_older_reading_exports(self):
        batch, _ = self.make_batch()
        path, session = self.make_export(batch)
        exported_path = Path(session["last_export_dir"]) / "读数数据.csv"
        old_bytes = exported_path.read_bytes()
        figure = batch["figures"][0]
        pipeline.update_figure(batch, figure["figure_id"], decision="keep", bbox=[25, 35, 145, 115])
        target = pipeline.collect_readings(batch)
        self.assertEqual(csv_rows(target), [])
        self.assertEqual(exported_path.read_bytes(), old_bytes)
        report = read_json(target.parent / "汇总说明.json")
        self.assertEqual(len(report["skipped"]), 1)

    def test_excluding_a_figure_excludes_its_earlier_readings(self):
        batch, _ = self.make_batch()
        self.make_export(batch)
        pipeline.update_figure(batch, batch["figures"][0]["figure_id"], decision="exclude")
        target = pipeline.collect_readings(batch)
        self.assertFalse(csv_rows(target))
        self.assertEqual(len(read_json(target.parent / "汇总说明.json")["skipped"]), 1)

    def test_moved_batch_rebases_images_sessions_and_still_collects(self):
        batch, _ = self.make_batch()
        self.make_export(batch)
        old_root = Path(batch["run_dir"]).resolve()
        moved_root = self.root / "移动后的批次"
        shutil.copytree(old_root, moved_root)
        moved = pipeline.load_batch(moved_root)
        self.assertEqual(Path(moved["run_dir"]), moved_root)
        self.assertTrue(Path(moved["figures"][0]["crop_path"]).is_relative_to(moved_root))
        self.assertTrue(Path(moved["papers"][0]["pages"][0]["image_path"]).is_relative_to(moved_root))
        self.assertTrue(Path(moved["reading_sessions"][0]["session_path"]).is_relative_to(moved_root))
        rows = csv_rows(pipeline.collect_readings(moved))
        self.assertEqual(len(rows), 1)
        self.assertTrue(Path(rows[0]["reading_session"]).is_relative_to(moved_root))
        self.assertEqual(rows[0]["source_sha256"], self.pdf_hash)

    def test_unexported_new_points_leave_snapshot_unchanged_and_flagged(self):
        batch, _ = self.make_batch()
        _, session = self.make_export(batch)
        add_points(session, [{"px": 80, "py": 30}], "Series A", sample_label="Sample A")
        rows = csv_rows(pipeline.collect_readings(batch))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["current_session_has_unexported_changes"], "True")

    def test_metadata_only_unexported_change_is_flagged(self):
        batch, _ = self.make_batch()
        _, session = self.make_export(batch)
        session["notes"] = "New experimental context, not yet exported."
        save_session(session)
        rows = csv_rows(pipeline.collect_readings(batch))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["current_session_has_unexported_changes"], "True")

    def test_changed_csv_is_not_collected_with_original_provenance(self):
        batch, _ = self.make_batch()
        _, session = self.make_export(batch)
        csv_path = Path(session["last_export_dir"]) / "读数数据.csv"
        rows = csv_rows(csv_path)
        fields = list(rows[0])
        rows[0]["y_value"] = "999999"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        target = pipeline.collect_readings(batch)
        self.assertEqual(csv_rows(target), [])
        self.assertEqual(len(read_json(target.parent / "汇总说明.json")["skipped"]), 1)

    def test_replaced_crop_is_rejected_before_reading_session(self):
        batch, _ = self.make_batch()
        figure = batch["figures"][0]
        with Path(figure["crop_path"]).open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError, "发生变化"):
            pipeline.create_reading_session(batch, figure["figure_id"])
        self.assertFalse(batch["reading_sessions"])

    def test_multiple_subfigures_keep_parent_and_independent_revisions(self):
        batch, _ = self.make_batch()
        parent = batch["figures"][0]
        pipeline.update_figure(batch, parent["figure_id"], decision="keep", figure_label="Fig. 1 composite")
        parent_before = copy.deepcopy(parent)
        parent_crop_path = Path(parent["crop_path"])
        parent_crop_bytes = parent_crop_path.read_bytes()
        first_id = pipeline.create_subfigure(batch, parent["figure_id"])
        second_id = pipeline.create_subfigure(batch, parent["figure_id"])
        self.assertNotEqual(first_id, second_id)
        first = pipeline.get_figure(batch, first_id)
        second = pipeline.get_figure(batch, second_id)
        for child in (first, second):
            self.assertEqual(child["parent_figure_id"], parent["figure_id"])
            self.assertEqual(child["revision"], 1)
            self.assertEqual(child["review_status"], "review")
            self.assertEqual(child["source_sha256"], self.pdf_hash)
        pipeline.update_figure(batch, first_id, decision="keep", bbox=[20, 30, 80, 100], figure_label="Fig. 1a")
        self.assertEqual(first["revision"], 2)
        self.assertEqual(second["revision"], 1)
        self.assertEqual(second["bbox"], parent_before["bbox"])
        first_crop_path = Path(first["crop_path"])
        first_crop_bytes = first_crop_path.read_bytes()
        pipeline.update_figure(batch, second_id, decision="keep", bbox=[80, 30, 140, 100], figure_label="Fig. 1b")
        self.assertEqual(second["revision"], 2)
        self.assertEqual(first_crop_path.read_bytes(), first_crop_bytes)
        self.assertEqual(parent, parent_before)
        self.assertEqual(parent_crop_path.read_bytes(), parent_crop_bytes)
        self.assertEqual(len({parent_crop_path, first_crop_path, Path(second["crop_path"])}), 3)
        reading = read_json(pipeline.create_reading_session(batch, first_id))
        context = reading["source_metadata"]["figure_context"]
        self.assertEqual(context["figure_id"], first_id)
        self.assertEqual(context["figure_revision"], 2)
        saved = read_json(Path(batch["run_dir"]) / "batch.json")
        stored_first = next(figure for figure in saved["figures"] if figure["figure_id"] == first_id)
        self.assertEqual(stored_first["parent_figure_id"], parent["figure_id"])
        self.assertEqual(stored_first["revision"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
