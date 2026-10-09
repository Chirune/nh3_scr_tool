"""Check first-stage local PDF startup and the second-stage handoff offline."""
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pypdf import PdfWriter

import app as gateway_app


class LocalEntryTests(unittest.TestCase):
    def test_smoke_local_import_validates_pdf_and_keeps_existing_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            pdfs = base / "pdfs"
            pdfs.mkdir()
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=300)
            writer.add_metadata({"/Title": "Local PDF startup fixture"})
            pdf = pdfs / "local.pdf"
            with pdf.open("wb") as stream:
                writer.write(stream)
            outputs = base / "runs"
            old = outputs / "existing" / "run.json"
            old.parent.mkdir(parents=True)
            old.write_bytes(b'{"preserve":"existing user decisions"}')
            original = old.read_bytes()
            stdout = io.StringIO()
            with patch("socket.socket.connect", side_effect=AssertionError("No network is allowed during local import")), redirect_stdout(stdout):
                result = gateway_app.main(["--smoke", "--pdf-folder", str(pdfs), "--profile", "cuzn", "--output-root", str(outputs)])
            self.assertEqual(result, 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["profile"], "cuzn")
            self.assertEqual(report["mode"], "local_pdf_offline")
            self.assertEqual(report["records"], 1)
            self.assertEqual(report["local_pdfs_verified"], 1)
            self.assertFalse(report["network_called"])
            new_file = Path(report["run_path"])
            self.assertNotEqual(new_file, old)
            stored = json.loads(new_file.read_text(encoding="utf-8"))
            record = stored["records"][0]
            self.assertEqual(Path(record["local_path"]), pdf.resolve())
            self.assertEqual(record["local_sha256"], hashlib.sha256(pdf.read_bytes()).hexdigest())
            self.assertEqual(old.read_bytes(), original)

    def test_cli_rejects_conflicting_input_without_opening_window(self):
        with patch.object(gateway_app.tk, "Tk") as window, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                gateway_app.main(["--load-run", "old.json", "--pdf-folder", "papers"])
        self.assertEqual(error.exception.code, 2)
        window.assert_not_called()

    def test_explicit_folder_bypasses_dialog_and_runs_asynchronous_local_job(self):
        app = object.__new__(gateway_app.LiteratureApp)
        app.busy = False
        app.output_root = Path("output")
        app.cancel_event = object()
        app._profile = lambda: "scr_ammonia"
        app._progress = Mock()
        app._show_run = Mock()
        received = []
        app._start_job = lambda label, worker, done: received.append((label, worker, done))
        with patch.object(gateway_app.filedialog, "askdirectory") as dialog, patch.object(gateway_app.engine, "run_local_pdfs", return_value={"records": []}) as read:
            app._local_pdfs(Path("papers"))
            self.assertEqual(len(received), 1)
            self.assertIn("本地", received[0][0])
            self.assertEqual(received[0][1](), {"records": []})
        dialog.assert_not_called()
        read.assert_called_once_with("scr_ammonia", Path("papers"), output_root=Path("output"),
                                     progress=app._progress, cancel_event=app.cancel_event)

    def test_second_stage_receives_current_saved_run_in_paper_workbench(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            run_file = folder / "run.json"
            run_file.write_text("{}", encoding="utf-8")
            app = object.__new__(gateway_app.LiteratureApp)
            app.run = {"run_dir": str(folder)}
            app.busy = False
            app.root = None
            app.paper_output_root = folder / 'isolated_papers'
            app.image_output_root = folder / 'isolated_images'
            messages = []
            app.status_var = SimpleNamespace(set=messages.append)
            with patch.object(gateway_app.subprocess, "Popen") as launch:
                app._launch_figures()
            command = launch.call_args.args[0]
            self.assertTrue(any(str(value).endswith('paper_app.py') for value in command))
            self.assertNotIn("--auto-scan", command)
            self.assertIn("--image-output-root", command)
            self.assertEqual(command[command.index("--screening-run") + 1], str(run_file))
            self.assertEqual(command[command.index("--output-root") + 1], str(app.paper_output_root))
            self.assertEqual(command[command.index("--image-output-root") + 1], str(app.image_output_root))
            self.assertIn("第二板块", messages[0])


if __name__ == "__main__":
    unittest.main()
