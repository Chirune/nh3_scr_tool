"""Verify automatic extraction feedback without touching the user's projects."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from digitizer import app as digitizer_app


class AutoUiTests(unittest.TestCase):
    def bare_app(self):
        instance = object.__new__(digitizer_app.DigitizerApp)
        instance.root = None
        instance.busy = False
        instance.session = {"points": [{"method": "manual"}], "exports": [{"directory": "old_export"}]}
        instance.require_image = lambda: True
        instance.sync_metadata = Mock()
        instance.set_status = Mock()
        instance.set_session = Mock()
        instance.cal_status = SimpleNamespace(set=Mock())
        instance.reviewed_var = SimpleNamespace(set=Mock())
        instance.show_error = Mock()
        return instance

    def test_automatic_worker_preserves_context_before_loading_saved_session(self):
        instance = self.bare_app()
        calls = []
        instance.sync_metadata = lambda: calls.append("metadata")
        extraction = Mock(return_value={"status": "unsupported", "count": 0, "message": "示意图"})
        def run_job(worker, complete, label):
            self.assertIn("自动识别", label)
            self.assertEqual(calls, ["metadata", "saved"])
            result = worker()
            self.assertIs(complete.__self__, instance)
            self.assertEqual(result["status"], "unsupported")
        instance.run_job = run_job
        def save(_session):
            calls.append("saved")
            return Path("existing") / "session.json"
        with patch.object(digitizer_app, "save_session", side_effect=save), patch.dict("sys.modules", {"auto_extract": SimpleNamespace(auto_extract_session=extraction)}):
            instance.auto_read()
        extraction.assert_called_once_with(str(Path("existing") / "session.json"))
        self.assertEqual(instance.session["points"], [{"method": "manual"}])
        self.assertEqual(instance.session["exports"], [{"directory": "old_export"}])

    def test_success_opens_returned_independent_session_and_reports_unreviewed_csv(self):
        instance = self.bare_app()
        with TemporaryDirectory() as temporary:
            automatic_session = str(Path(temporary) / "automatic" / "session.json")
            export_folder = str(Path(temporary) / "automatic" / "export_latest")
            loaded = ({"points": [{"method": "automatic"}]}, object())
            with patch.object(digitizer_app, "load_session", return_value=loaded) as loader, patch.object(digitizer_app.messagebox, "showinfo") as info:
                instance.complete_auto_read({"status": "success", "count": 8, "session_path": automatic_session, "export_dir": export_folder})
            loader.assert_called_once_with(automatic_session)
            instance.set_session.assert_called_once_with(loaded)
            instance.reviewed_var.set.assert_called_once_with(False)
            self.assertIn("8 个", info.call_args.args[1])
            self.assertIn("待核对", info.call_args.args[1])
            self.assertIn(str(Path(export_folder) / "读数数据.csv"), info.call_args.args[1])

    def test_schematic_rejection_leaves_current_session_and_gives_reason(self):
        instance = self.bare_app()
        original = instance.session
        with patch.object(digitizer_app, "load_session") as loader, patch.object(digitizer_app.messagebox, "showinfo") as info:
            instance.complete_auto_read({"status": "unsupported", "count": 0, "message": "这是装置截面示意图，不能作为性能曲线读取。", "report_path": "report.json"})
        loader.assert_not_called()
        instance.set_session.assert_not_called()
        self.assertIs(instance.session, original)
        self.assertIn("装置截面示意图", info.call_args.args[1])
        self.assertIn("report.json", info.call_args.args[1])
        self.assertNotIn("读取完成", info.call_args.args[0])

    def test_partial_bundle_success_shows_outputs_and_missing_series(self):
        instance=self.bare_app()
        loaded=({'points':[{}]},object())
        with patch.object(digitizer_app,'load_session',return_value=loaded),patch.object(digitizer_app.messagebox,'showinfo') as info:
            instance.complete_auto_read({'status':'partial_success','count':40,'panel_count':3,
                'session_path':'panel/session.json','export_dir':'bundle','message':'已自动拆分3个子图',
                'warnings':['(a)：未输出遮挡系列']})
        instance.set_session.assert_called_once_with(loaded)
        self.assertIn('查看各子图结果',info.call_args.args[1])
        self.assertIn('未输出遮挡系列',info.call_args.args[1])
        self.assertIn('部分内容未完整识别',info.call_args.args[1])

    def test_missing_y_scale_explains_original_data_instead_of_repeated_manual_rescaling(self):
        instance = self.bare_app()
        with patch.object(digitizer_app.messagebox, "showinfo") as info:
            instance.complete_auto_read({"status": "needs_review", "count": 0,
                "message": "横轴已识别，纵轴没有可用数字刻度。",
                "next_action": "请查询补充原始数据，不能手动猜测 Y 轴。"})
        self.assertIn("不能手动猜测", info.call_args.args[1])
        self.assertNotIn("使用左侧手动修正", info.call_args.args[1])

    def test_success_without_points_is_not_reported_as_exported_data(self):
        instance = self.bare_app()
        with patch.object(digitizer_app, "load_session") as loader, patch.object(digitizer_app.messagebox, "showinfo") as info:
            instance.complete_auto_read({"status": "success", "count": 0, "message": "没有可靠的数据点"})
        loader.assert_not_called()
        self.assertIn("没有可靠的数据点", info.call_args.args[1])

    def test_worker_not_started_while_existing_job_is_busy(self):
        instance = self.bare_app()
        instance.busy = True
        instance.run_job = Mock()
        with patch.object(digitizer_app, "save_session") as save:
            instance.auto_read()
        save.assert_not_called()
        instance.run_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()
