"""Check export feedback and folder navigation without touching user projects."""
from pathlib import Path
import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from digitizer import app as digitizer_app


class ExportUiTests(unittest.TestCase):
    def bare_app(self, session):
        instance = object.__new__(digitizer_app.DigitizerApp)
        instance.root = None
        instance.session = session
        instance.require_image = lambda: True
        instance.show_error = lambda exc: self.fail(str(exc))
        return instance

    def test_open_output_opens_latest_csv_snapshot(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export_latest"
            export.mkdir()
            (export / "读数数据.csv").write_text("x,y\n200,10\n", encoding="utf-8")
            instance = self.bare_app({"run_dir": str(root), "exports": [{"directory": "export_old"}, {"directory": "export_latest"}]})
            with patch.object(digitizer_app.os, "startfile", create=True) as startfile:
                instance.open_output()
            startfile.assert_called_once_with(str(export.resolve()))

    def test_open_output_guides_when_empty_and_rejects_outside_project(self):
        with TemporaryDirectory() as temporary:
            instance = self.bare_app({"run_dir": temporary, "exports": []})
            with patch.object(digitizer_app.messagebox, "showinfo") as info, patch.object(digitizer_app.os, "startfile", create=True) as startfile:
                instance.open_output()
            self.assertIn("标定坐标轴", info.call_args.args[1])
            startfile.assert_not_called()
            instance.session["exports"] = [{"directory": "../outside"}]
            errors = []
            instance.show_error = lambda exc: errors.append(str(exc))
            with patch.object(digitizer_app.os, "startfile", create=True) as startfile:
                instance.open_output()
            self.assertEqual(len(errors), 1)
            self.assertIn("不在当前读数项目", errors[0])
            startfile.assert_not_called()

    def test_export_feedback_names_point_count_and_csv(self):
        with TemporaryDirectory() as temporary:
            export = Path(temporary) / "export_latest"
            instance = self.bare_app({"points": [{}, {}],"run_dir":temporary,"exports":[{'directory':'export_latest'}]})
            instance.require_calibration = lambda: True
            instance.busy = False
            instance.sync_metadata = lambda: None
            instance.refresh_table = lambda: None
            messages = []
            instance.set_status = messages.append
            instance.reviewed_var = SimpleNamespace(get=lambda: True)
            instance.doi_var = SimpleNamespace(get=lambda: "test")
            instance.figure_var = SimpleNamespace(get=lambda: "Figure 7")
            instance.mode_var = SimpleNamespace(get=lambda: "xy")
            instance.notes_box = SimpleNamespace(get=lambda *_: "")
            with patch.object(digitizer_app, "export_session", return_value=Path(temporary)), patch.object(digitizer_app.messagebox, "showinfo") as info:
                instance.export()
            self.assertIn("2 个点", info.call_args.args[1])
            self.assertIn(str(export / "读数数据.csv"), info.call_args.args[1])
            self.assertIn("打开导出文件夹", messages[0])

    def test_bundle_opens_combined_csv_until_child_is_reexported(self):
        with TemporaryDirectory() as temporary:
            parent=Path(temporary);root=parent/'child';export=root/'export_latest';bundle=parent/'original'/'bundle'
            export.mkdir(parents=True);bundle.mkdir(parents=True)
            for folder in (export,bundle):(folder/'读数数据.csv').write_text('x,y\n1,2\n')
            instance=self.bare_app({'run_dir':str(root),'exports':[{'directory':'export_latest'}],
                                   'automatic_bundle':{'export_dir':str(bundle),'session_export_directory':'export_latest'}})
            with patch.object(digitizer_app.os,'startfile',create=True) as opened:
                instance.open_output()
            opened.assert_called_once_with(str(bundle.resolve()))
            instance.session['automatic_bundle']['session_export_directory']='previous_snapshot'
            with patch.object(digitizer_app.os,'startfile',create=True) as opened:
                instance.open_output()
            opened.assert_called_once_with(str(export.resolve()))

    def test_series_review_opens_current_snapshot_and_blocks_unexported_changes(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);folder=root/'export_latest';folder.mkdir()
            session={'run_dir':str(root),'exports':[{'directory':'export_latest'}],'points':[{'x':1,'y':2}], 'reviewed':False}
            (folder/'逐系列核对.html').write_text('<h1>fixture</h1>',encoding='utf8')
            (folder/'读数与溯源.json').write_text(json.dumps(session),encoding='utf8')
            instance=self.bare_app(session)
            with patch.object(digitizer_app.os,'startfile',create=True) as opened:
                instance.open_series_review()
            opened.assert_called_once_with(str(folder/'逐系列核对.html'))
            session['points'][0]['y']=3
            with patch.object(digitizer_app.os,'startfile',create=True) as opened, patch.object(digitizer_app.messagebox,'showinfo') as message:
                instance.open_series_review()
            opened.assert_not_called()
            self.assertIn('需要更新',message.call_args.args[0])

    def test_series_review_explains_legacy_export_without_rewriting(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'old').mkdir()
            instance=self.bare_app({'run_dir':str(root),'exports':[{'directory':'old'}]})
            with patch.object(digitizer_app.messagebox,'showinfo') as message, patch.object(digitizer_app.os,'startfile',create=True) as opened:
                instance.open_series_review()
            self.assertIn('旧版',message.call_args.args[1]);opened.assert_not_called()
            self.assertEqual(list((root/'old').iterdir()),[])


if __name__ == "__main__":
    unittest.main()
