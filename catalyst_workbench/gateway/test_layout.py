"""Geometry-only regression checks for the gateway's visible second-stage entry.

The windows are fully transparent, never receive synthetic input, and never
start a search, download, or figure scan. Run from this directory with:
    python -m unittest test_layout -v
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import tkinter.font as tkfont
import unittest


APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
try:
    _spec = importlib.util.spec_from_file_location("gateway_layout_application", APP_DIR / "app.py")
    _application = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_application)
finally:
    sys.path.remove(str(APP_DIR))


class GatewayLayoutTests(unittest.TestCase):
    def setUp(self):
        self.root = None
        self.output = tempfile.TemporaryDirectory(prefix="gateway_layout_only_")

    def tearDown(self):
        if self.root is not None:
            try:
                for callback in self.root.tk.call("after", "info"):
                    self.root.after_cancel(callback)
                self.root.destroy()
            except tk.TclError:
                pass
        self.output.cleanup()

    def check_geometry(self, width, height, scaling):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        try:
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            self.skipTest("This platform cannot create a fully transparent measurement window.")
        self.root.tk.call("tk", "scaling", scaling)
        application = _application.LiteratureApp(self.root, Path(self.output.name))
        self.root.geometry(f"{width}x{height}+0+0")
        self.root.deiconify()
        self.root.update_idletasks()
        self.root.update()
        self.root.update_idletasks()

        button = application.figure_button
        root_x, root_y = self.root.winfo_rootx(), self.root.winfo_rooty()
        x, y = button.winfo_rootx() - root_x, button.winfo_rooty() - root_y
        actual_w, actual_h = button.winfo_width(), button.winfo_height()
        requested_w, requested_h = button.winfo_reqwidth(), button.winfo_reqheight()
        actual_client_w, actual_client_h = self.root.winfo_width(), self.root.winfo_height()
        details = (
            f"window={width}x{height}, scaling={scaling}, "
            f"actual_client={actual_client_w}x{actual_client_h}, "
            f"button=({x},{y},{actual_w},{actual_h}), requested={requested_w}x{requested_h}"
        )
        self.assertTrue(button.winfo_ismapped(), "Button is not mapped: " + details)
        self.assertGreaterEqual(actual_w, requested_w - 2, "Button text was clipped horizontally: " + details)
        self.assertGreaterEqual(actual_h, requested_h - 2, "Button was vertically compressed: " + details)
        font_spec = application.root.tk.call("ttk::style", "lookup", "TButton", "-font")
        font = tkfont.Font(root=self.root, font=font_spec)
        self.assertGreaterEqual(actual_h, font.metrics("linespace") + 4, "Button has no readable text height: " + details)
        self.assertGreaterEqual(x, 0, details)
        self.assertGreaterEqual(y, 0, details)
        self.assertLessEqual(x + actual_w, actual_client_w, "Button extends beyond the window width: " + details)
        self.assertLessEqual(y + actual_h, actual_client_h, "Button extends below the window: " + details)

        # A mapped widget can still be clipped by an intermediate container.
        ancestor = button.master
        while ancestor is not None and ancestor is not self.root:
            ax = ancestor.winfo_rootx() - root_x
            ay = ancestor.winfo_rooty() - root_y
            self.assertGreaterEqual(x, ax, "Button extends left of a parent: " + details)
            self.assertGreaterEqual(y, ay, "Button extends above a parent: " + details)
            self.assertLessEqual(x + actual_w, ax + ancestor.winfo_width(), "Button clipped by parent width: " + details)
            self.assertLessEqual(y + actual_h, ay + ancestor.winfo_height(), "Button clipped by parent height: " + details)
            ancestor = ancestor.master

        # Compare against the first search control. At extreme font scaling the
        # results table itself may have no allocated area; its root coordinate
        # then is meaningless for checking the independently pinned top entry.
        first_search_control_top = application.profile_combo.winfo_rooty() - root_y
        self.assertLess(y + actual_h, first_search_control_top, "Second-stage entry is not above the search controls: " + details)
        self.assertIsNone(application.run, "Layout check must not load a real run.")
        self.assertFalse(application.busy, "Layout check must not start background work.")


def make_layout_test(width, height, scaling):
    def test(self):
        self.check_geometry(width, height, scaling)
    return test


for _width, _height in ((900, 650), (1260, 920)):
    for _scaling in (1.33, 2.0, 2.66):
        _name = f"test_second_stage_entry_{_width}x{_height}_scale_{str(_scaling).replace('.', '_')}"
        setattr(GatewayLayoutTests, _name, make_layout_test(_width, _height, _scaling))


if __name__ == "__main__":
    unittest.main()
