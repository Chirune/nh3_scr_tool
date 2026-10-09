"""Article-workbench integration checks using real, temporary PDF text layers.

These are synthetic fixtures, not scientific datasets or an accuracy estimate.
No network, paid APIs, user Zotero library, or delivered project is accessed.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import paper_workspace as workspace
import pipeline


PAGE_ONE = [
    "Synthetic NH3-SCR study (integration fixture only)",
    "In this study, sample S1 tested at 300 C: NO conversion was 90%.",
    "Sample S1 was Cu/CeO2; feed: 500 ppm NO, 500 ppm NH3, 5% O2 in N2.",
    "Space velocity was 40000 h-1.",
    "Table 1. Experimental performance",
    "Sample Temperature_C NO_conversion_percent",
    "S1 300 90",
]
PAGE_TWO = [
    "According to previous work, NO conversion was 85%.",
    "NO conversion increased by 10%.",
    "N2 selectivity may reach 99%.",
    "NO conversion was 80-90%.",
    "NO conversion was >90%.",
    "NO conversion was about 90%.",
    "NO conversion did not increase.",
]


def make_pdf(path, page_lines):
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    font_ref = writer._add_object(font)
    for lines in page_lines:
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        content = ["BT /F1 10 Tf 10 760 Td"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content.append(f"({escaped}) Tj 0 -22 Td")
        content.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(content).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": "Synthetic article workspace integration fixture"})
    with Path(path).open("wb") as stream:
        writer.write(stream)
    return Path(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


class PaperWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="paper_workspace_test_")
        self.root = Path(self.temporary.name).resolve()
        self.pdf = make_pdf(self.root / "article_a.pdf", [PAGE_ONE, PAGE_TWO])
        self.other_pdf = make_pdf(self.root / "article_b.pdf", [["Other selected article", "NO conversion was 70%."]])
        self.run_path = self.root / "run.json"
        self.run_path.write_text(json.dumps({
            "profile": "scr_ammonia",
            "records": [self.screening_record("article-a", self.pdf, "10.1234/fixture-a"),
                        self.screening_record("article-b", self.other_pdf, "10.1234/fixture-b"),
                        {"id": "waiting-c", "doi": "10.1234/fixture-c", "title": "Unrelated waiting record",
                         "effective_decision": "target", "manual_decision": "target", "local_path": ""}],
        }), encoding="utf-8")
        papers, self.waiting = workspace.list_papers(screening_run=self.run_path)
        self.article = next(p for p in papers if p["record_id"] == "article-a")
        self.project = workspace.open_paper(self.article, self.root / "workspaces")
        workspace.extract_document(self.project)

    def tearDown(self):
        self.temporary.cleanup()

    def screening_record(self, record_id, pdf, doi):
        return {"id": record_id, "doi": doi, "title": pdf.stem,
                "effective_decision": "target", "manual_decision": "target",
                "local_path": str(pdf), "local_sha256": pipeline.digest(pdf),
                "screening": {"decision": "target", "reason": "Synthetic fixture"}}

    def evidence(self, *, kind="absolute", operator="eq", page=None, scope=None):
        return next(e for e in self.project["evidence"]
                    if e["branch"] == "semantic" and e["kind"] == kind
                    and (operator is None or e["operator"] == operator)
                    and (page is None or e["page"] == page)
                    and (scope is None or e["assertion_scope"] == scope))

    def approve(self, *entries):
        for entry in entries:
            workspace.review_evidence(self.project, entry["evidence_id"], "reviewed", "reviewer-A")

    def record(self, entry=None, **updates):
        entry = entry or self.evidence(page=1)
        record = workspace.record_from_evidence(self.project, [entry["evidence_id"]])
        record.update(task_id="scr_conversion", sample_label="S1", experiment_id="experiment-1",
                      composition="Cu/CeO2", metric="NO conversion", unit="%",
                      conditions={"temperature_C": 300.0,
                                  "feed_description": "500 ppm NO, 500 ppm NH3, 5% O2 in N2",
                                  "space_velocity_h_inv": 40000.0},
                      assertion_scope="current_study", measurement_type="experiment")
        record.update(updates)
        return record

    def save_confirmed(self, record):
        return workspace.put_record(self.project, record, reviewer="reviewer-A", confirm=True)

    def export(self):
        summary = workspace.export_handoff(self.project)
        packet = read_json(Path(summary["directory"]) / "待编码包.json")
        return summary, packet

    def test_real_pdf_page_text_and_semantic_quotes_have_exact_offsets(self):
        self.assertEqual(len(self.project["pages"]), 2)
        self.assertEqual(self.project["pages"][0]["text"].splitlines(), PAGE_ONE)
        self.assertEqual(self.project["pages"][1]["text"].splitlines(), PAGE_TWO)
        by_page = {p["page"]: p["text"] for p in self.project["pages"]}
        semantics = [e for e in self.project["evidence"] if e["branch"] == "semantic"]
        self.assertGreaterEqual(len(semantics), 7)
        for item in semantics:
            self.assertEqual(item["quote"], by_page[item["page"]][item["start"]:item["end"]])
            self.assertEqual(item["source_ref"]["source_sha256"], pipeline.digest(self.pdf))
            self.assertEqual(item["review_status"], "unreviewed")
            self.assertFalse(item["usable"])
        tables = [e for e in self.project["evidence"] if e["kind"] == "table_passage"]
        self.assertEqual(len(tables), 1)
        self.assertFalse(tables[0]["usable"])
        self.assertIn("尚未恢复行列", " ".join(tables[0]["warnings"]))

    def test_manual_selection_is_exact_deduplicated_and_persistent(self):
        text = self.project["pages"][0]["text"]
        selection = "feed: 500 ppm NO, 500 ppm NH3, 5% O2 in N2."
        start = text.index(selection)
        item = workspace.add_text_evidence(self.project, 1, start, start + len(selection))
        again = workspace.add_text_evidence(self.project, 1, start, start + len(selection))
        self.assertEqual(item["evidence_id"], again["evidence_id"])
        self.assertEqual(item["quote"], selection)
        self.approve(item)
        reloaded = workspace.load_project(workspace.workspace_path(self.project))
        saved = next(e for e in reloaded["evidence"] if e["evidence_id"] == item["evidence_id"])
        self.assertEqual(saved["quote"], selection)
        self.assertEqual(saved["reviewer"], "reviewer-A")
        self.assertEqual(saved["review_status"], "reviewed")
        with self.assertRaises(ValueError):
            workspace.add_text_evidence(self.project, 1, -1, 200)
        with self.assertRaises(ValueError):
            workspace.add_text_evidence(self.project, 1, 0, len(text) + 1)

    def test_reextract_keeps_review_and_manual_evidence(self):
        absolute = self.evidence(page=1)
        self.approve(absolute)
        text = self.project["pages"][0]["text"]
        start = text.index("Space velocity")
        manual = workspace.add_text_evidence(self.project, 1, start, start + len("Space velocity was 40000 h-1."))
        self.approve(manual)
        workspace.extract_document(self.project)
        by_id = {e["evidence_id"]: e for e in self.project["evidence"]}
        self.assertEqual(by_id[absolute["evidence_id"]]["review_status"], "reviewed")
        self.assertEqual(by_id[manual["evidence_id"]]["quote"], manual["quote"])
        self.assertEqual(by_id[manual["evidence_id"]]["reviewer"], "reviewer-A")

    def test_nonexistent_quote_from_future_parser_is_rejected(self):
        fake = {"start": 0, "end": 8, "evidence_quote": "Not in PDF", "kind": "absolute",
                "metric": "NO conversion", "operator": "eq", "value": 100, "unit": "%"}
        with mock.patch("paper_semantics.semantic_candidates", return_value=[fake]), \
             mock.patch("paper_relations.relation_candidates", return_value=[fake]), \
             mock.patch("paper_semantic_facets.enrich_candidates", return_value=[fake]):
            workspace.extract_document(self.project)
        self.assertFalse(any(e["branch"] == "semantic" for e in self.project["evidence"]))
        self.assertTrue(any("准确原文位置" in warning for warning in self.project["text_warnings"]))

    def test_optimistic_save_does_not_overwrite_a_second_window(self):
        path = workspace.workspace_path(self.project)
        first = workspace.load_project(path)
        stale = workspace.load_project(path)
        workspace.set_task(first, "scr_selectivity")
        with self.assertRaisesRegex(ValueError, "另一个窗口更新"):
            workspace.set_task(stale, "scr_conversion")
        self.assertEqual(workspace.load_project(path)["task_id"], "scr_selectivity")

    def test_complete_reviewed_current_experiment_enters_standardization_not_training(self):
        absolute = self.evidence(page=1)
        self.approve(absolute)
        record = self.save_confirmed(self.record(absolute))
        summary, packet = self.export()
        self.assertEqual((summary["ready_count"], summary["waiting_count"]), (1, 0))
        row = packet["records_ready_for_standardization"][0]
        self.assertEqual(row["record_id"], record["record_id"])
        self.assertEqual(row["value"], 90)
        self.assertEqual(row["stage3_status"], "ready_for_standardization")
        self.assertFalse(row["ml_ready"])
        self.assertEqual(row["split_group"], "10.1234/fixture-a")
        self.assertEqual(row["source_sha256"], pipeline.digest(self.pdf))

    def test_review_material_scope_and_conditions_are_all_required(self):
        absolute = self.evidence(page=1)
        record = self.save_confirmed(self.record(absolute))
        self.assertTrue(workspace.record_issues(self.project, record), "Evidence was not reviewed")
        self.approve(absolute)
        self.assertEqual(workspace.record_issues(self.project, record), [])
        changes = [
            {"composition": ""}, {"sample_label": ""}, {"experiment_id": ""},
            {"assertion_scope": "unknown"}, {"assertion_scope": "prior_work"},
            {"measurement_type": "dft"}, {"review_status": "draft"},
            {"conditions": {"temperature_C": 300}},
            {"conditions": {"feed_description": "same feed", "space_velocity_h_inv": 40000}},
            {"conditions": {"temperature_C": 300, "feed_description": "same feed"}},
        ]
        for change in changes:
            with self.subTest(change=change):
                candidate = dict(record, **change)
                self.assertTrue(workspace.record_issues(self.project, candidate))

    def test_reviewer_and_value_evidence_cannot_be_omitted(self):
        record = self.record()
        with self.assertRaises(ValueError):
            workspace.put_record(self.project, record, confirm=True, reviewer="")
        record["evidence_ids"] = []
        with self.assertRaises(ValueError):
            workspace.put_record(self.project, record)

    def test_comparison_outlook_and_negation_cannot_be_edited_into_absolute_labels(self):
        for index, kind in enumerate(("comparison", "outlook", "negated")):
            evidence = self.evidence(kind=kind, operator=None)
            self.approve(evidence)
            record = self.record(evidence, value=90, value_high=None, operator="eq",
                                 experiment_id=f"experiment-{index}")
            confirmed = self.save_confirmed(record)
            issues = workspace.record_issues(self.project, confirmed)
            self.assertTrue(issues, kind)
        summary, packet = self.export()
        self.assertEqual(summary["ready_count"], 0)
        self.assertEqual(len(packet["records_waiting_for_review"]), 3)
        self.assertGreaterEqual(len(packet["semantic_relations"]), 3)

    def test_context_evidence_attaches_without_becoming_an_extra_label(self):
        absolute = self.evidence(page=1)
        relationship = self.evidence(kind="comparison", operator=None)
        self.approve(absolute, relationship)
        record = self.record(absolute)
        record["evidence_ids"].append(relationship["evidence_id"])
        record["evidence_roles"] = {absolute["evidence_id"]: "value", relationship["evidence_id"]: "context"}
        self.save_confirmed(record)
        summary, packet = self.export()
        self.assertEqual((summary["ready_count"], summary["waiting_count"]), (1, 0))
        self.assertEqual(len(packet["records_ready_for_standardization"]), 1)
        self.assertEqual(packet["records_ready_for_standardization"][0]["value"], 90)
        self.assertTrue(any(e["evidence_id"] == relationship["evidence_id"] for e in packet["semantic_relations"]))

    def test_context_only_record_cannot_supply_an_absolute_value(self):
        absolute = self.evidence(page=1)
        self.approve(absolute)
        record = self.record(absolute)
        record["evidence_roles"] = {absolute["evidence_id"]: "context"}
        confirmed = self.save_confirmed(record)
        self.assertTrue(workspace.record_issues(self.project, confirmed))
        self.assertEqual(self.export()[0]["ready_count"], 0)

    def test_attach_context_resets_confirmation_and_preserves_one_observation(self):
        absolute = self.evidence(page=1)
        relationship = self.evidence(kind="comparison", operator=None)
        self.approve(absolute, relationship)
        saved = self.save_confirmed(self.record(absolute))
        workspace.attach_context(self.project, saved["record_id"], relationship["evidence_id"], "reviewer-B")
        self.assertEqual(len(self.project["records"]), 1)
        record = self.project["records"][0]
        self.assertEqual(record["value"], 90)
        self.assertEqual(record["review_status"], "draft")
        self.assertEqual(record["evidence_roles"][relationship["evidence_id"]], "context")
        self.assertEqual(self.export()[0]["ready_count"], 0)
        self.save_confirmed(record)
        self.assertEqual(self.export()[0]["ready_count"], 1)
        event = next(e for e in self.project["events"] if e["action"] == "attach_context_evidence")
        self.assertEqual(event["reviewer"], "reviewer-B")
        self.assertEqual(event["detail"]["before"]["review_status"], "reviewed")

    def test_prior_work_value_cannot_be_relabelled_as_current_experiment(self):
        cited = self.evidence(page=2, scope="prior_work")
        self.approve(cited)
        saved = self.save_confirmed(self.record(cited, assertion_scope="current_study"))
        self.assertTrue(workspace.record_issues(self.project, saved))
        self.assertEqual(self.export()[0]["ready_count"], 0)

    def test_wrong_evidence_source_hash_blocks_handoff(self):
        absolute = self.evidence(page=1)
        self.approve(absolute)
        saved = self.save_confirmed(self.record(absolute))
        self.assertEqual(workspace.record_issues(self.project, saved), [])
        absolute["source_ref"]["source_sha256"] = pipeline.digest(self.other_pdf)
        self.assertTrue(workspace.record_issues(self.project, saved))
        self.assertEqual(self.export()[0]["ready_count"], 0)

    def test_old_records_without_roles_default_to_value_evidence(self):
        absolute = self.evidence(page=1)
        self.approve(absolute)
        record = self.record(absolute)
        record.pop("evidence_roles", None)
        self.save_confirmed(record)
        self.assertEqual(self.export()[0]["ready_count"], 1)

    def test_range_bound_and_approximate_value_evidence_cannot_bypass_as_equality(self):
        entries = [self.evidence(kind="range", operator="range"),
                   self.evidence(kind="absolute", operator="gt"),
                   self.evidence(kind="absolute", operator="unknown")]
        self.approve(*entries)
        for index, entry in enumerate(entries):
            with self.subTest(original_operator=entry["operator"]):
                record = self.record(entry, value=90, value_high=None, operator="eq",
                                     experiment_id=f"boundary-{index}")
                saved = self.save_confirmed(record)
                self.assertTrue(workspace.record_issues(self.project, saved),
                                "Changing the form operator must not erase original evidence meaning")
        self.assertEqual(self.export()[0]["ready_count"], 0)

    def test_missing_values_remain_null_in_saved_and_exported_records(self):
        absolute = self.evidence(page=1)
        self.approve(absolute)
        record = self.record(absolute, value="", value_high=None)
        record["conditions"]["pressure_kPa"] = ""
        saved = self.save_confirmed(record)
        self.assertIsNone(saved["value"])
        self.assertIsNone(saved["conditions"]["pressure_kPa"])
        loaded = workspace.load_project(workspace.workspace_path(self.project))
        self.assertIsNone(loaded["records"][0]["value"])
        summary, packet = self.export()
        self.assertEqual(summary["ready_count"], 0)
        self.assertIsNone(packet["records_waiting_for_review"][0]["value"])
        for non_finite in ("nan", "inf", "-inf"):
            with self.assertRaises(ValueError):
                workspace.put_record(self.project, self.record(value=non_finite))

    def test_duplicates_require_merge_and_merge_preserves_evidence_and_review_history(self):
        absolute = self.evidence(page=1)
        relationship = self.evidence(kind="comparison", operator=None)
        self.approve(absolute, relationship)
        manual = workspace.add_text_evidence(self.project, 1, absolute["start"], absolute["end"])
        self.approve(manual)
        first = self.record(absolute)
        first["evidence_ids"].append(relationship["evidence_id"])
        first["evidence_roles"] = {absolute["evidence_id"]: "value", relationship["evidence_id"]: "context"}
        first = self.save_confirmed(first)
        second = self.save_confirmed(self.record(manual, value=90, operator="eq"))
        ids = [first["record_id"], second["record_id"]]
        self.assertEqual(set(workspace.conflicts(self.project)), set(ids))
        summary, packet = self.export()
        self.assertEqual((summary["ready_count"], summary["waiting_count"]), (0, 2))
        workspace.merge_equal_records(self.project, ids, "reviewer-B")
        kept = next(r for r in self.project["records"] if r["record_id"] == ids[0])
        removed = next(r for r in self.project["records"] if r["record_id"] == ids[1])
        self.assertEqual(kept["value"], 90)
        self.assertEqual(kept["review_status"], "draft")
        self.assertEqual(removed["review_status"], "excluded")
        self.assertEqual(removed["merged_into"], kept["record_id"])
        self.assertEqual(set(kept["evidence_ids"]), {absolute["evidence_id"], relationship["evidence_id"], manual["evidence_id"]})
        self.assertEqual(kept["evidence_roles"][relationship["evidence_id"]], "context")
        events = [event for event in self.project["events"] if event["action"] == "merge_equal_evidence"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reviewer"], "reviewer-B")
        self.assertEqual(len(events[0]["detail"]["before"]), 2)
        self.assertEqual(self.export()[0]["ready_count"], 0, "Merged evidence needs a new confirmation")
        self.save_confirmed(kept)
        self.assertEqual(self.export()[0]["ready_count"], 1)

    def test_conflicting_values_are_never_averaged_or_silently_merged(self):
        absolute = self.evidence(page=1)
        self.approve(absolute)
        first = self.save_confirmed(self.record(absolute, value=90))
        second = self.save_confirmed(self.record(absolute, value=80))
        ids = [first["record_id"], second["record_id"]]
        self.assertEqual(len(workspace.conflicts(self.project)), 2)
        with self.assertRaises(ValueError):
            workspace.merge_equal_records(self.project, ids, "reviewer-A")
        self.assertEqual(sorted(r["value"] for r in self.project["records"]), [80, 90])
        summary, packet = self.export()
        self.assertEqual((summary["ready_count"], summary["waiting_count"]), (0, 2))
        self.assertEqual(sorted(r["value"] for r in packet["records_waiting_for_review"]), [80, 90])

    def test_merge_keeps_context_role_when_only_second_record_has_the_context(self):
        absolute = self.evidence(page=1)
        relationship = self.evidence(kind="comparison", operator=None)
        self.approve(absolute, relationship)
        first = self.save_confirmed(self.record(absolute))
        second = self.save_confirmed(self.record(absolute))
        workspace.attach_context(self.project, second["record_id"], relationship["evidence_id"], "reviewer-B")
        second = next(r for r in self.project["records"] if r["record_id"] == second["record_id"])
        self.save_confirmed(second)
        workspace.merge_equal_records(self.project, [first["record_id"], second["record_id"]], "reviewer-B")
        kept = next(r for r in self.project["records"] if r["record_id"] == first["record_id"])
        self.assertEqual(kept["evidence_roles"][relationship["evidence_id"]], "context",
                         "A context appearing only on the second record must not become a value source")
        self.save_confirmed(kept)
        self.assertEqual(self.export()[0]["ready_count"], 1)

    def test_numeric_condition_format_and_blank_optional_fields_do_not_hide_duplicates(self):
        absolute = self.evidence(page=1)
        self.approve(absolute)
        first = self.save_confirmed(self.record(absolute))
        second = self.record(absolute)
        second["conditions"]["temperature_C"] = "300.0"
        second["conditions"]["pressure_kPa"] = ""
        second["conditions"]["other"] = " "
        second = self.save_confirmed(second)
        self.assertEqual(set(workspace.conflicts(self.project)), {first["record_id"], second["record_id"]})
        self.assertEqual(self.export()[0]["ready_count"], 0)

    def test_changed_source_pdf_blocks_reload_extraction_and_handoff(self):
        absolute = self.evidence(page=1)
        self.approve(absolute)
        self.save_confirmed(self.record(absolute))
        with self.pdf.open("ab") as stream:
            stream.write(b"\n% changed source after review\n")
        for operation in (lambda: workspace.load_project(workspace.workspace_path(self.project)),
                          lambda: workspace.extract_document(self.project),
                          lambda: workspace.export_handoff(self.project),
                          lambda: workspace.ensure_image_batch(self.project, self.root / "images")):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(ValueError, "PDF 已改变"):
                    operation()

    def test_image_batch_scans_only_selected_record_using_real_pdf(self):
        original_locator = pipeline.locate_pdf
        with mock.patch.object(pipeline, "locate_pdf", wraps=original_locator) as locator:
            batch = workspace.ensure_image_batch(self.project, self.root / "images")
        self.assertEqual(locator.call_count, 1)
        self.assertEqual(Path(locator.call_args.args[0]).resolve(), self.pdf)
        self.assertEqual([p["record_id"] for p in batch["papers"]], ["article-a"])
        self.assertEqual(batch["papers"][0]["scan_status"], "scanned")
        self.assertEqual(batch["waiting"], [])
        self.assertEqual(batch["errors"], [])
        self.assertEqual(self.project["article"]["paper_id"], batch["papers"][0]["paper_id"])
        with mock.patch.object(pipeline, "locate_pdf", wraps=original_locator) as locator:
            again = workspace.ensure_image_batch(self.project, self.root / "images")
        locator.assert_not_called()
        self.assertEqual(again["batch_id"], batch["batch_id"])

    def test_empty_selection_does_not_fall_back_to_every_paper(self):
        with mock.patch.object(pipeline, "locate_pdf", wraps=pipeline.locate_pdf) as locator:
            batch = pipeline.build_batch(self.run_path, self.root / "images", only_record_ids=[])
        locator.assert_not_called()
        self.assertEqual(batch["papers"], [])
        self.assertEqual(batch["waiting"], [])


if __name__ == "__main__":
    unittest.main()
