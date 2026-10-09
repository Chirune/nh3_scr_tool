"""Automatic reading orchestration: real sessions/exports, controlled detectors."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.append(str(Path(__file__).resolve().parent.parent))
from digitizer.auto_extract import auto_extract_session
from digitizer.session import new_session, save_session, set_calibration, add_points, export_session
from pipeline import SCHEMA, save_batch, load_batch, collect_readings


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def csv_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


class AutomaticExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "plot.png"
        image = Image.new("RGB", (100, 100), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 80, 80), outline="black")
        draw.ellipse((38, 58, 42, 62), fill="blue")
        draw.ellipse((58, 38, 62, 42), fill="blue")
        image.save(self.source)
        image.close()
        self.calibration = {
            "x": {"p1": [20, 80], "p2": [80, 80], "v1": 0, "v2": 100,
                  "scale": "linear", "name": "Temperature", "unit": "°C"},
            "y": {"p1": [20, 80], "p2": [20, 20], "v1": 0, "v2": 100,
                  "scale": "linear", "name": "Conversion", "unit": "%"},
        }
        self.axes = {"status": "ready", "plot_bbox": [20, 20, 80, 80],
                     "calibration": self.calibration, "visible_text_lines": [], "reasons": []}
        self.curves = {"status": "ready", "series": [{"label": "Sample A", "method": "auto_detected_marker",
            "points_px": [{"px": 40, "py": 60}, {"px": 60, "py": 40}],
            "label_evidence": {"source": "fixture"}}], "warnings": []}

    def tearDown(self):
        self.temporary.cleanup()

    def make_session(self, with_batch=False):
        batch_dir = self.root / "batch"
        output_root = batch_dir / "readings" if with_batch else self.root / "readings"
        session, image = new_session(self.source, output_root=output_root)
        image.close()
        session["doi"] = "10.test/local"
        session["figure_label"] = "Figure 1"
        if with_batch:
            context = {"batch_id": "test_batch", "figure_id": "test_figure", "figure_revision": 1,
                       "figure_review_status": "keep", "record_id": "test_record",
                       "crop_to_page": {"offset_x": 0, "offset_y": 0, "scale_x": 1, "scale_y": 1}}
            session["source_metadata"]["figure_context"] = context
            batch = {"schema_version": SCHEMA, "batch_id": "test_batch", "run_dir": str(batch_dir),
                     "papers": [], "waiting": [], "errors": [], "figures": [{
                         "figure_id": "test_figure", "revision": 1, "review_status": "keep",
                         "crop_path": str(self.source), "crop_sha256": digest(self.source),
                         "page_image_path": str(self.source), "page_image_sha256": digest(self.source),
                         "screening_status": "target"}],
                     "reading_sessions": [{"session_path": str(Path(session["run_dir"])/"session.json"),
                         "figure_id": "test_figure", "figure_revision": 1}], "status": "complete"}
            save_batch(batch)
        return session, save_session(session)

    def no_numeric_csv(self):
        self.assertEqual(list(self.root.rglob("读数数据.csv")), [])

    def assert_candidate_export(self, result):
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 2)
        rows = csv_rows(Path(result["export_dir"])/"读数数据.csv")
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["review_status"] for row in rows}, {"unreviewed"})
        self.assertEqual({row["value_origin"] for row in rows}, {"image_digitized_approximate"})
        self.assertEqual({row["ml_ready"] for row in rows}, {"requires_scientific_context_review"})
        self.assertEqual({row["method"] for row in rows}, {"auto_detected_marker"})
        self.assertAlmostEqual(float(rows[0]["x_value"]), 100/3)
        self.assertAlmostEqual(float(rows[0]["y_value"]), 100/3)
        snapshot = json.loads((Path(result["export_dir"])/"读数与溯源.json").read_text(encoding="utf-8"))
        self.assertFalse(snapshot["reviewed"])
        self.assertEqual(snapshot["exports"][-1]["csv_sha256"], digest(Path(result["export_dir"])/"读数数据.csv"))
        self.assertTrue((Path(result["export_dir"])/"原图与读数标记.png").is_file())
        report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertTrue(report["requires_review"])
        self.assertEqual(report["series_count"], 1)

    def test_fresh_project_automatically_exports_real_candidate_rows(self):
        session, path = self.make_session()
        with (patch("digitizer.auto_extract.auto_calibrate", return_value=self.axes) as axes,
             patch("digitizer.auto_extract.extract_curve_series", return_value=self.curves),
             patch("digitizer.auto_extract.recognize_image") as ocr):
            result = auto_extract_session(path)
        self.assert_candidate_export(result)
        self.assertEqual(Path(result["session_path"]), path)
        ocr.assert_not_called()
        axes.assert_called_once()
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(stored["automatic_extraction"]["isolated_from_previous_readings"])

    def test_repeated_automatic_export_does_not_duplicate_batch_rows(self):
        session,path=self.make_session(with_batch=True)
        stale=load_batch(self.root/'batch/batch.json')
        with (patch('digitizer.auto_extract.auto_calibrate',return_value=self.axes),
              patch('digitizer.auto_extract.extract_curve_series',return_value=self.curves)):
            first=auto_extract_session(path)
            before=Path(first['session_path']).read_bytes()
            second=auto_extract_session(path)
        self.assertNotEqual(first['session_path'],second['session_path'])
        self.assertEqual(Path(first['session_path']).read_bytes(),before)
        combined=collect_readings(stale)
        self.assertEqual(len(csv_rows(combined)),2)
        self.assertTrue(Path(first['export_dir']).is_dir())

    def test_diagram_is_rejected_before_ocr_or_csv(self):
        _, path = self.make_session()
        before = path.read_bytes()
        diagram = {"status": "refuse_numeric", "reasons": ["Probe layout, not a performance plot"]}
        with (patch("digitizer.auto_extract.auto_calibrate", return_value=diagram),
             patch("digitizer.auto_extract.extract_curve_series") as curves,
             patch("digitizer.auto_extract.recognize_image") as ocr):
            result = auto_extract_session(path)
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["count"], 0)
        self.no_numeric_csv()
        curves.assert_not_called()
        ocr.assert_not_called()
        self.assertEqual(before, path.read_bytes())

    def test_unknown_axes_remain_explicitly_unread_without_empty_csv(self):
        _, path = self.make_session()
        unknown = {"status": "needs_review", "reasons": ["fewer than three printed ticks"]}
        with (patch("digitizer.auto_extract.auto_calibrate", return_value=unknown) as axes,
             patch("digitizer.auto_extract.extract_curve_series") as curves,
             patch("digitizer.auto_extract.recognize_image", return_value={"lines": []}) as ocr):
            result = auto_extract_session(path)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["count"], 0)
        self.no_numeric_csv()
        self.assertEqual(axes.call_count, 2)
        ocr.assert_called_once()
        curves.assert_not_called()

    def test_axes_without_separable_series_do_not_export_empty_tables(self):
        _, path = self.make_session()
        with (patch("digitizer.auto_extract.auto_calibrate", return_value=self.axes),
             patch("digitizer.auto_extract.extract_curve_series", return_value={"series": [], "warnings": ["overlapping series"]})):
            result = auto_extract_session(path)
        self.assertEqual(result["status"], "needs_review")
        self.no_numeric_csv()

    def test_existing_manual_export_is_preserved_in_separate_registered_project(self):
        session, path = self.make_session(with_batch=True)
        set_calibration(session, self.calibration)
        add_points(session, [{"px": 30, "py": 70}], "Manual sample", sample_label="Manual sample")
        export_session(session, metadata={"reviewed": True})
        original_csv = Path(session["last_export_dir"])/"读数数据.csv"
        original_csv_hash = digest(original_csv)
        original_session_bytes = path.read_bytes()
        batch_path = self.root / "batch/batch.json"
        older_workbench = load_batch(batch_path)
        with (patch("digitizer.auto_extract.auto_calibrate", return_value=self.axes),
             patch("digitizer.auto_extract.extract_curve_series", return_value=self.curves)):
            result = auto_extract_session(path)
        self.assert_candidate_export(result)
        self.assertNotEqual(Path(result["session_path"]), path)
        self.assertEqual(path.read_bytes(), original_session_bytes)
        self.assertEqual(digest(original_csv), original_csv_hash)
        automatic = json.loads(Path(result["session_path"]).read_text(encoding="utf-8"))
        self.assertTrue(automatic["automatic_extraction"]["isolated_from_previous_readings"])
        batch = load_batch(batch_path)
        self.assertEqual(len(batch["reading_sessions"]), 2)
        new_ref = next(r for r in batch["reading_sessions"] if r.get("mode") == "automatic")
        self.assertEqual(Path(new_ref["session_path"]), Path(result["session_path"]))
        # Saving the already-open workbench must not erase the new project.
        save_batch(older_workbench)
        batch = load_batch(batch_path)
        self.assertEqual(len(batch["reading_sessions"]), 2)
        combined_csv = collect_readings(batch)
        rows = csv_rows(combined_csv)
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(row["review_status"] == "user_reviewed" for row in rows), 1)
        self.assertEqual(sum(row["review_status"] == "unreviewed" for row in rows), 2)

    def test_changed_source_pdf_cannot_export_even_if_ocr_returns_ticks(self):
        session, path = self.make_session()
        changed = self.root / "changed.pdf"
        changed.write_bytes(b"Changed source PDF")
        session["source_metadata"].update(source_path=str(changed), source_sha256="previous_sha256")
        save_session(session)
        with (patch("digitizer.auto_extract.recognize_image", return_value={"lines": []}) as ocr,
             patch("digitizer.auto_extract.extract_curve_series") as curves):
            result = auto_extract_session(path)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["count"], 0)
        self.no_numeric_csv()
        curves.assert_not_called()
        ocr.assert_not_called()
        report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertIn("source_pdf_identity_changed", report["axes"]["reason_codes"])

    def test_unexported_manual_points_are_also_preserved_by_cloning(self):
        session, path = self.make_session()
        set_calibration(session, self.calibration)
        add_points(session, [{"px": 30, "py": 70}], "Pending manual sample")
        before = path.read_bytes()
        with (patch("digitizer.auto_extract.auto_calibrate", return_value=self.axes),
              patch("digitizer.auto_extract.extract_curve_series", return_value=self.curves)):
            result = auto_extract_session(path)
        self.assert_candidate_export(result)
        self.assertNotEqual(Path(result["session_path"]), path)
        self.assertEqual(path.read_bytes(), before)
        original = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(original["points"]), 1)
        self.assertEqual(original["exports"], [])

    def test_excluded_figure_stops_before_detector_and_export(self):
        _, path = self.make_session(with_batch=True)
        batch_path = path.parent.parent.parent / "batch.json"
        batch = load_batch(batch_path)
        batch["figures"][0]["review_status"] = "exclude"
        save_batch(batch)
        with patch("digitizer.auto_extract.auto_calibrate") as axes:
            result = auto_extract_session(path)
        self.assertEqual(result["status"], "error")
        axes.assert_not_called()
        self.no_numeric_csv()

    def test_reframing_while_detection_runs_stops_before_csv_export(self):
        _, path = self.make_session(with_batch=True)
        batch_path = path.parent.parent.parent / "batch.json"
        def detect(*args, **kwargs):
            batch = load_batch(batch_path)
            batch["figures"][0]["revision"] = 2
            save_batch(batch)
            return self.curves
        with (patch("digitizer.auto_extract.auto_calibrate", return_value=self.axes),
              patch("digitizer.auto_extract.extract_curve_series", side_effect=detect)):
            result = auto_extract_session(path)
        self.assertEqual(result["status"], "error")
        self.no_numeric_csv()


if __name__ == "__main__":
    unittest.main()
