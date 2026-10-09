"""Unsupported XML routes are guidance, never failed download jobs."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import app


def pmc_record():
    return {"id": "pmc", "fulltext_candidates": [{"url": "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123/fullTextXML",
             "access_status": "open_access"}]}


class FulltextAccessTests(unittest.TestCase):
    def bare_app(self, record):
        instance = object.__new__(app.LiteratureApp)
        instance.busy = False
        instance.run = {"records": [record]}
        instance.root = None
        instance._selected = lambda: record
        instance.record_controls = [Mock(), Mock()]
        instance.xml_button = instance.record_controls[1]
        instance.fulltext_hint_var = SimpleNamespace(set=Mock())
        instance.status_var = SimpleNamespace(set=Mock())
        instance.note_text = Mock()
        instance.folder_button = Mock()
        instance.figure_button = Mock()
        instance._start_job = Mock()
        instance._progress = Mock()
        return instance

    def test_elsevier_does_not_enable_pmc_xml_or_start_a_failed_job(self):
        record = {"id": "elsevier", "route": {"publisher_group": "Elsevier"}, "fulltext_candidates": []}
        instance = self.bare_app(record)
        instance._enable_record_controls()
        self.assertEqual(instance.xml_button.configure.call_args.kwargs["state"], "disabled")
        self.assertIn("公开 PDF", instance.fulltext_hint_var.set.call_args.args[0])
        self.assertIn("API 设置 / 验证", instance.fulltext_hint_var.set.call_args.args[0])
        with patch.object(app.messagebox, "showinfo") as info, patch.object(app.engine, "download_open_fulltext") as download:
            instance._download()
        instance._start_job.assert_not_called()
        download.assert_not_called()
        self.assertEqual(info.call_args.args[0], "当前全文获取方式")

    def test_stale_open_available_flag_cannot_enable_an_unconnected_route(self):
        record = {"id": "stale", "route": {"open_xml_available": True},
                  "fulltext_candidates": [{"url": "https://www.sciencedirect.com/article.pdf", "access_status": "open"}]}
        self.assertFalse(app.has_open_xml(record))
        instance = self.bare_app(record)
        instance._enable_record_controls()
        self.assertEqual(instance.xml_button.configure.call_args.kwargs["state"], "disabled")

    def test_connected_pmc_remains_enabled_and_downloads_through_existing_worker(self):
        instance = self.bare_app(pmc_record())
        instance._enable_record_controls()
        self.assertEqual(instance.xml_button.configure.call_args.kwargs["state"], "normal")
        with patch.object(app.engine, "download_open_fulltext", return_value=Path("article.xml")) as download:
            instance._download()
            result = instance._start_job.call_args.args[1]()
        self.assertEqual(result, Path("article.xml"))
        download.assert_called_once_with(instance.run, "pmc", progress=instance._progress)

    def test_busy_state_blocks_repeated_downloads(self):
        instance = self.bare_app(pmc_record())
        instance.busy = True
        instance._enable_record_controls()
        self.assertEqual(instance.xml_button.configure.call_args.kwargs["state"], "disabled")
        instance._download()
        instance._start_job.assert_not_called()

    def test_local_pdf_guidance_continues_to_image_stage(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "local.pdf"
            path.write_bytes(b"%PDF test")
            hint = app.fulltext_access_hint({"local_path": str(path)})
        self.assertIn("已有本地 PDF", hint)
        self.assertIn("进入第二板块", hint)


if __name__ == "__main__":
    unittest.main()
