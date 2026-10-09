"""Behavioural tests: automatic calibration must fail closed when ambiguous."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from digitizer.auto_axes import calibrate_from_text, auto_calibrate, _axis_fit, _number
from digitizer.digitize import pixel_to_data


def plot_fixture():
    image = Image.new("RGB", (450, 420), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 30, 390, 320), outline="black", width=2)
    lines = []
    for x, value in ((70, 0), (230, 5), (390, 10)):
        lines.append({"text": str(value), "bbox": [x-8, 332, x+8, 348], "source": "test_text"})
    for y, value in ((30, 100), (175, 50), (320, 0)):
        lines.append({"text": str(value), "bbox": [40, y-8, 60, y+8], "source": "test_text"})
    lines.extend([{"text": "Temperature (°C)", "bbox": [110, 360, 340, 380]},
                  {"text": "Conversion (%)", "bbox": [5, 80, 25, 280], "orientation": -90}])
    return image, lines


class AutomaticAxesTests(unittest.TestCase):
    def test_three_printed_ticks_calibrate_without_clicks(self):
        image, lines = plot_fixture()
        result = calibrate_from_text(image, lines)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["calibration"]["x"]["unit"], "°C")
        self.assertEqual(result["calibration"]["y"]["unit"], "%")
        values = pixel_to_data(230, 175, result["calibration"])
        self.assertAlmostEqual(values["x"], 5, places=6)
        self.assertAlmostEqual(values["y"], 50, places=6)

    def test_legends_and_rotated_ocr_numbers_are_not_ticks(self):
        image, lines = plot_fixture()
        lines.extend([{"text": "34", "bbox": [200, 80, 220, 96]},
                      {"text": "3", "bbox": [260, 80, 280, 96]},
                      {"text": "999", "bbox": [42, 92, 62, 108], "orientation": 90}])
        result = calibrate_from_text(image, lines)
        self.assertEqual(result["status"], "ready")
        self.assertEqual([t["value"] for t in result["ticks"]["y"]], [100, 50, 0])

    def test_two_ticks_are_not_sufficient(self):
        image, lines = plot_fixture()
        lines = [line for line in lines if line["text"] != "5"]
        result = calibrate_from_text(image, lines)
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["calibration"])

    def test_x_ticks_without_y_numbers_report_missing_y_scale(self):
        image, lines = plot_fixture()
        lines = [line for line in lines if line["text"] not in ("100", "50", "0") or line["bbox"][0] > 60]
        result = calibrate_from_text(image, lines)
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["calibration"])
        self.assertEqual(result["partial_axes"]["x"]["tick_count"], 3)
        self.assertIn("y_numeric_ticks_not_recognized", result["reason_codes"])
        self.assertIn("纵轴", result["reasons"][0])

    def test_normalized_caption_does_not_create_invented_y_values(self):
        image, lines = plot_fixture()
        lines = [line for line in lines if line["text"] not in ("100", "50", "0") or line["bbox"][0] > 60]
        caption = "The values on the vertical axis were normalized using the maximum and minimum values for each line profile."
        result = calibrate_from_text(image, lines, caption)
        self.assertIn("normalized_profile_without_numeric_y", result["reason_codes"])
        self.assertIsNone(result["calibration"])
        self.assertEqual(result["ticks"]["y"], [])

    def test_normalized_profile_with_unreadable_ticks_explains_actual_limit(self):
        image, _ = plot_fixture()
        result = calibrate_from_text(image, [{"text": "6.3 Å", "bbox": [10, 20, 40, 35]}],
            "The values were normalized using the maximum and minimum values for each line profile.")
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("normalized_profile_without_numeric_y", result["reason_codes"])
        self.assertIn("本版尚未自动导出", result["next_action"])
        self.assertIsNone(result["calibration"])

    def test_separate_numbers_in_one_ocr_line_are_not_concatenated(self):
        self.assertIsNone(_number("0 20"))
        self.assertIsNone(_number("100 200"))

    def test_split_decimal_glyphs_form_one_tick_without_flattening_exponents(self):
        self.assertEqual(_number('0 ． 2'), .2)
        self.assertEqual(_number('０ ， ６'), .6)
        self.assertIsNone(_number('10²'))
        self.assertIsNone(_number('10⁻³'))
        image,lines=plot_fixture()
        lines=[v for v in lines if not (v['bbox'][1]>=332 and v['bbox'][3]<=348)]
        for x,value in [(70,'0 ． 2'),(230,'0 ． 5'),(390,'0 ． 8')]:
            lines.append({'text':value,'bbox':[x-14,332,x+14,348],
                          'words':[{'text':'0','bbox':[x-14,332,x-4,348]},
                                   {'text':value[-1],'bbox':[x+4,332,x+14,348]}]})
        axes=calibrate_from_text(image,lines)
        self.assertEqual(axes['status'],'ready')
        self.assertEqual([v['value'] for v in axes['ticks']['x']],[.2,.5,.8])
        self.assertAlmostEqual(pixel_to_data(230,175,axes['calibration'])['x'],.5)

    def test_changed_pdf_cannot_fall_back_to_ocr_and_appear_valid(self):
        image, lines = plot_fixture()
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/"changed.pdf"
            source.write_bytes(b"changed source")
            session = {"source_metadata": {"source_path": str(source), "source_sha256": "old"}}
            result = auto_calibrate(session, image, extra_text_lines=lines)
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["calibration"])
        self.assertIn("source_pdf_identity_changed", result["reason_codes"])

    def test_linear_logarithmic_ambiguity_is_not_guessed(self):
        ticks = [{"value": value, "pixel": [pixel, 100], "bbox": [pixel-8, 92, pixel+8, 108], "text": str(value)}
                 for pixel, value in ((0, 100000), (100, 100100), (200, 100200))]
        fit, reason = _axis_fit(ticks, "x", 200)
        self.assertIsNone(fit)
        self.assertEqual(reason, "linear_log_ambiguous")

    def test_true_logarithmic_scale_is_supported(self):
        ticks = [{"value": value, "pixel": [pixel, 100], "bbox": [pixel-8, 92, pixel+8, 108], "text": str(value)}
                 for pixel, value in ((0, 1), (100, 10), (200, 100))]
        fit, reason = _axis_fit(ticks, "x", 200)
        self.assertEqual(reason, "")
        self.assertEqual(fit["scale"], "log10")

    def test_reversed_axes_and_negative_values_survive_complete_calibration(self):
        image,lines=plot_fixture()
        for line,value in zip(lines[:6],(10,0,-10,-2,0,2)):
            line['text']=str(value)
        result=calibrate_from_text(image,lines)
        self.assertEqual(result['status'],'ready')
        values=pixel_to_data(150,102.5,result['calibration'])
        self.assertAlmostEqual(values['x'],5)
        self.assertAlmostEqual(values['y'],-1)
        self.assertAlmostEqual(values['x_pixel_resolution'],20/320)
        self.assertAlmostEqual(values['y_pixel_resolution'],4/290)

    def test_reversed_log_axis_has_local_multiplicative_pixel_resolution(self):
        image,lines=plot_fixture()
        for line,value in zip(lines[:6],(100,10,1,.001,.01,.1)):
            line['text']=str(value)
        result=calibrate_from_text(image,lines)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['calibration']['x']['scale'],'log10')
        self.assertEqual(result['calibration']['y']['scale'],'log10')
        values=pixel_to_data(150,102.5,result['calibration'])
        self.assertAlmostEqual(values['x'],10**1.5)
        self.assertAlmostEqual(values['y'],10**-2.5)
        self.assertAlmostEqual(values['x_pixel_resolution'],values['x']*(10**(1/320)-10**(-1/320)))
        self.assertAlmostEqual(values['y_pixel_resolution'],values['y']*(10**(1/290)-10**(-1/290)))

    def test_ocr_lost_digits_are_excluded_without_inventing_replacements(self):
        values = [50,100,150,2,250,3,0,4,450,500,550]
        ticks = [{"value":v,"pixel":[50+i*50,100],"bbox":[45+i*50,95,55+i*50,105],
                  "text":str(v),"source":"windows_axis_tile_ocr"} for i,v in enumerate(values)]
        fit,reason = _axis_fit(ticks,'x',525)
        self.assertEqual(reason,'')
        self.assertEqual([t['value'] for t in fit['ticks']],[50,100,150,250,450,500,550])
        self.assertEqual([t['value'] for t in fit['rejected_ticks']],[2,3,0,4])
        self.assertAlmostEqual(fit['slope'],1)
        self.assertAlmostEqual(fit['intercept'],0)

    def test_conflicting_ocr_majorities_stay_unresolved(self):
        # Two disjoint equally supported scales must not be chosen by order.
        ticks = [{"value":v,"pixel":[x,100],"bbox":[x-3,95,x+3,105],"text":str(v),"source":"ocr"}
                 for x,v in [(0,0),(100,1),(200,2),(300,3),(400,80),(500,100),(600,120),(700,140)]]
        fit,reason = _axis_fit(ticks,'x',700)
        self.assertIsNone(fit)

    def test_pdf_nonuniform_scale_cannot_be_repaired_as_ocr_noise(self):
        ticks = [{"value":v,"pixel":[50+i*50,100],"bbox":[45+i*50,95,55+i*50,105],
                  "text":str(v),"source":"pdf_visible_word"}
                 for i,v in enumerate([50,100,150,2,250,3,0,4,450,500,550])]
        self.assertIsNone(_axis_fit(ticks,'x',525)[0])

    def test_ocr_consensus_keeps_negative_and_logarithmic_values(self):
        for scale,values in [('linear',[-3,-2,-1,0,8,2,3]),('log10',[.001,.01,.1,1,.08,100,1000])]:
            ticks=[{'value':v,'pixel':[i*50,100],'bbox':[i*50-3,95,i*50+3,105],
                    'text':str(v),'source':'ocr'} for i,v in enumerate(values)]
            fit,reason=_axis_fit(ticks,'x',300)
            self.assertEqual(reason,'')
            self.assertEqual(fit['scale'],scale)
            self.assertEqual(len(fit['ticks']),6)

    def test_probe_layout_is_refused_even_if_axes_are_present(self):
        image, lines = plot_fixture()
        result = calibrate_from_text(image, lines, "Figure 1. Measuring probe positions over the SCR outlet face.")
        self.assertEqual(result["status"], "refuse_numeric")
        self.assertEqual(result["chart_type"], "non_performance")
        self.assertIsNone(result["calibration"])

    def test_real_ocr_observations_preserve_provenance(self):
        root = Path(__file__).resolve().parents[3]
        record = root / "outputs/文献到图片_衔接版_20260930/自动裁图测试结果.json"
        ocr_path = Path(__file__).resolve().parent.parent / "crop_validation/figure7_ocr.json"
        if not record.is_file() or not ocr_path.is_file():
            self.skipTest("Local real-paper fixtures unavailable")
        summary = json.loads(record.read_text(encoding="utf-8"))
        batch = json.loads(Path(summary["batch_path"]).read_text(encoding="utf-8"))
        figure = next(f for f in batch["figures"] if f["doi"] == "10.3390/ijerph192214749" and f["page"] == 10)
        ocr = json.loads(ocr_path.read_text(encoding="utf-8"))
        with Image.open(figure["crop_path"]) as image:
            result = calibrate_from_text(image, ocr["lines"], figure["caption"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["calibration"]["x"]["name"], "Temperature")
        self.assertEqual(result["calibration"]["x"]["unit"], "°C")
        self.assertEqual(result["calibration"]["y"]["name"], "NO conversion")
        self.assertEqual(result["calibration"]["y"]["unit"], "%")
        self.assertGreaterEqual(result["axis_evidence"]["x"]["tick_count"], 3)
        self.assertGreaterEqual(result["axis_evidence"]["y"]["tick_count"], 3)
        self.assertIn("℃", result["axis_evidence"]["x"]["name_evidence"]["text"])


if __name__ == "__main__":
    unittest.main()
