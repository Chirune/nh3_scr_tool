"""Offline source admission tests using real, minimal PDF files."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfWriter

from intake import IntakeError, load_pdf_folder, load_screened_input


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="figure_intake_")
        self.root = Path(self.temp.name)
        self.pdf = self.make_pdf(self.root / "中文论文.pdf")
        self.sha = hashlib.sha256(self.pdf.read_bytes()).hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def make_pdf(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with path.open("wb") as stream:
            writer.write(stream)
        return path

    def record(self, **changes):
        result = {"id": "paper-1", "title": "氨吸附研究", "doi": "10.1234/EXAMPLE", "local_path": str(self.pdf), "local_sha256": self.sha, "effective_decision": "target", "manual_decision": "", "screening": {"decision": "target", "reason": "关键词命中"}, "warnings": []}
        result.update(changes)
        return result

    def run_file(self, records, **changes):
        value = {"profile": "scr_ammonia", "records": records, "run_dir": "Z:/not-used"}
        value.update(changes)
        path = self.root / "run.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def test_kept_pdf_unicode_path_and_source_hash(self):
        path = self.run_file([self.record(local_sha256=self.sha.upper())])
        result = load_screened_input(path)
        self.assertEqual(len(result["papers"]), 1)
        paper = result["papers"][0]
        self.assertEqual(paper["doi"], "10.1234/example")
        self.assertEqual(paper["page_count"], 1)
        self.assertEqual(paper["source_sha256"], self.sha)
        self.assertEqual(paper["source_hash_status"], "matches_record")
        self.assertEqual(result["source_run_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_download_link_survives_screening_only_resave(self):
        path = self.run_file([self.record(local_path="", local_sha256="")])
        data = {"schema_version":"local-pdf-links/1.0", "links":[{"record_id":"paper-1", "doi":"10.1234/example", "local_path":str(self.pdf), "local_sha256":self.sha}]}
        (self.root/"local_pdf_links.json").write_text(json.dumps(data),encoding="utf-8")
        result = load_screened_input(path)
        self.assertEqual(len(result["papers"]),1)
        self.assertEqual(result["papers"][0]["pdf_link_origin"],"downloaded_pdf_sidecar")
        self.run_file([self.record(local_path="",local_sha256="",manual_decision="non_target")])
        self.assertEqual(load_screened_input(path)["excluded_count"],1)

    def test_download_link_rejects_wrong_identity_and_changed_file(self):
        path = self.run_file([self.record(local_path="",local_sha256="")])
        data = {"schema_version":"local-pdf-links/1.0", "links":[{"record_id":"paper-1", "doi":"10.1234/different", "local_path":str(self.pdf), "local_sha256":self.sha}]}
        link_path = self.root/"local_pdf_links.json"
        link_path.write_text(json.dumps(data),encoding="utf-8")
        self.assertFalse(load_screened_input(path)["papers"])
        data["links"][0]["doi"] = "10.1234/example"
        data["links"][0]["local_sha256"] = "0"*64
        link_path.write_text(json.dumps(data),encoding="utf-8")
        result = load_screened_input(path)
        self.assertFalse(result["papers"])
        self.assertEqual(result["waiting"][0]["reason_code"],"source_changed")

    def test_manual_exclusion_overrides_stale_effective_target(self):
        result = load_screened_input(self.run_file([self.record(manual_decision="non_target")]), include_review=True)
        self.assertFalse(result["papers"])
        self.assertEqual(result["excluded_count"], 1)
        self.assertEqual(result["excluded"][0]["effective_decision"], "non_target")

    def test_manual_keep_overrides_stale_automatic_exclusion(self):
        result = load_screened_input(self.run_file([self.record(manual_decision="target", effective_decision="non_target", screening={"decision": "non_target"})]))
        self.assertEqual(len(result["papers"]), 1)
        self.assertTrue(result["papers"][0]["warnings"])

    def test_review_waits_by_default_and_is_flagged_when_opted_in(self):
        path = self.run_file([self.record(effective_decision="review")])
        default = load_screened_input(path)
        self.assertFalse(default["papers"])
        self.assertEqual(default["waiting"][0]["reason_code"], "screening_review_required")
        included = load_screened_input(path, include_review=True)
        self.assertEqual(included["papers"][0]["screening_status"], "review")
        self.assertTrue(included["papers"][0]["requires_review"])

    def test_no_pdf_and_xml_fulltext_are_not_treated_as_pdf(self):
        xml = self.root / "fulltext.xml"
        xml.write_text("<article/>", encoding="utf-8")
        result = load_screened_input(self.run_file([
            self.record(id="doi-only", local_path="", local_sha256=""),
            self.record(id="xml-only", local_path="", downloaded_fulltext=str(xml)),
            self.record(id="xml-in-local", local_path=str(xml)),
        ]))
        self.assertFalse(result["papers"])
        self.assertEqual([r["reason_code"] for r in result["waiting"]], ["pdf_not_downloaded", "fulltext_not_local_pdf", "not_pdf"])

    def test_changed_pdf_hash_does_not_silently_admit_new_file(self):
        path = self.run_file([self.record()])
        with self.pdf.open("ab") as stream:
            stream.write(b"\n% Local change after screening\n")
        result = load_screened_input(path)
        self.assertFalse(result["papers"])
        self.assertEqual(result["waiting"][0]["reason_code"], "source_changed")
        self.assertNotEqual(result["waiting"][0]["source_sha256"], self.sha)

    def test_missing_doi_is_allowed_with_clear_flag(self):
        result = load_screened_input(self.run_file([self.record(doi="")]))
        self.assertEqual(len(result["papers"]), 1)
        self.assertEqual(result["papers"][0]["doi_status"], "missing_or_invalid")
        self.assertTrue(result["papers"][0]["requires_review"])

    def test_relative_pdf_resolves_against_actual_run_location(self):
        result = load_screened_input(self.run_file([self.record(local_path="中文论文.pdf")]))
        self.assertEqual(result["papers"][0]["local_pdf"], str(self.pdf.resolve()))

    def test_folder_is_unscreened_nonrecursive_case_insensitive(self):
        upper = self.make_pdf(self.root / "第二篇.PDF")
        nested = self.make_pdf(self.root / "子目录" / "第三篇.pdf")
        result = load_pdf_folder(self.root)
        self.assertEqual(len(result["papers"]), 2)
        self.assertTrue(all(p["screening_status"] == "not_screened" and p["requires_review"] for p in result["papers"]))
        self.assertEqual(result["source_run_path"], "")
        recursive = load_pdf_folder(self.root, recursive=True)
        self.assertEqual(len(recursive["papers"]), 3)
        self.assertTrue(any(p["local_pdf"] == str(nested.resolve()) for p in recursive["papers"]))

    def test_invalid_and_disguised_pdf_wait_for_download_repair(self):
        fake = self.root / "错误页.pdf"
        fake.write_text("<html>Login required</html>", encoding="utf-8")
        broken = self.root / "损坏.pdf"
        broken.write_bytes(b"%PDF-1.7\nnot a complete PDF")
        result = load_screened_input(self.run_file([self.record(id="fake", local_path=str(fake), local_sha256=""), self.record(id="broken", local_path=str(broken), local_sha256="")]))
        self.assertFalse(result["papers"])
        self.assertTrue(all(p["reason_code"] == "invalid_pdf" for p in result["waiting"]))

    def test_unknown_decisions_do_not_fall_back_to_automatic_target(self):
        result = load_screened_input(self.run_file([self.record(id="missing", effective_decision=""), self.record(id="invalid-manual", manual_decision="whatever")]))
        self.assertFalse(result["papers"])
        self.assertEqual(len(result["waiting"]), 2)

    def test_urls_network_paths_and_command_text_are_not_executed(self):
        inputs = ["https://doi.org/10.1234/example", "file:///tmp/example.pdf", "\\\\server\\share\\example.pdf", "$(Remove-Item example).pdf"]
        result = load_screened_input(self.run_file([self.record(id=str(i), local_path=p) for i, p in enumerate(inputs)]))
        self.assertFalse(result["papers"])
        self.assertEqual(len(result["waiting"]), len(inputs))

    def test_invalid_run_or_flag_is_rejected(self):
        with self.assertRaises(IntakeError):
            load_screened_input(self.run_file([], profile="unknown"))
        with self.assertRaises(IntakeError):
            load_screened_input(self.run_file([]), include_review="false")


if __name__ == "__main__":
    unittest.main(verbosity=2)
