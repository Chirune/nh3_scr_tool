"""Behaviour checks for automatic marker reading, legend masking and ambiguity."""
import unittest

from PIL import Image, ImageDraw

try:
    from .auto_curves import extract_curve_series
except ImportError:
    from auto_curves import extract_curve_series


def chart(overlap=False, ambiguous=False, filled=True):
    image = Image.new("RGB", (500, 380), "white")
    d = ImageDraw.Draw(image)
    box = [30, 20, 470, 340]
    d.rectangle(box, outline="black", width=1)
    colors = [(20, 90, 200), (220, 45, 50), (85, 85, 85)]
    labels = ["Catalyst-A", "Catalyst-B", "Catalyst-C"]
    rows, truth = [], {}
    for i, (color, label) in enumerate(zip(colors, labels)):
        coords = [(80+45*j, 75+45*i+8*j) for j in range(5)]
        if overlap and i == 1:
            coords[2] = (170, 91)
        if ambiguous and i == 0:
            coords += [(80, 210), (125, 215), (170, 220)]
        d.line(coords, fill=color, width=1)
        for x, y in coords:
            if filled:
                d.ellipse([x-6, y-6, x+6, y+6], fill=color)
        ly = 230+23*i
        d.line([318, ly, 354, ly], fill=color, width=1)
        if filled:
            d.ellipse([330, ly-6, 342, ly+6], fill=color)
        rows.append({"text": label, "bbox": [361, ly-7, 450, ly+7]})
        truth[label] = coords
    return image, box, rows, truth


class AutomaticMarkerTests(unittest.TestCase):
    def test_numeric_prefix_of_legend_is_retained(self):
        image,box,rows,truth=chart()
        for index,row in enumerate(rows):
            left,top,right,bottom=row['bbox'];number=str(1500+index*500)
            row.update(text='• '+number+' rpm',words=[{'text':'•','bbox':[330,top,342,bottom]},
                       {'text':number,'bbox':[left,top,left+42,bottom]},{'text':'rpm','bbox':[left+45,top,right,bottom]}])
        result=extract_curve_series(image,box,rows)
        self.assertEqual({s['label'] for s in result['series']},{'1500 rpm','2000 rpm','2500 rpm'})
        self.assertEqual(result['point_count'],15)
    def test_labels_and_legend_samples_are_excluded(self):
        image, box, rows, truth = chart()
        result = extract_curve_series(image, box, rows)
        self.assertEqual({s["label"] for s in result["series"]}, set(truth))
        self.assertEqual(result["point_count"], 15)
        self.assertIsNotNone(result["legend_bbox"])
        for series in result["series"]:
            self.assertEqual(series["method"], "automatic_marker_candidates")
            self.assertEqual(series["marker_count"], 5)
            for point, expected in zip(series["points_px"], truth[series["label"]]):
                self.assertAlmostEqual(point["px"], expected[0], delta=2)
                self.assertAlmostEqual(point["py"], expected[1], delta=2)
                self.assertLess(point["px"], 300)  # No legend glyph becomes data.

    def test_overlapping_markers_are_not_fabricated_or_mixed(self):
        image, box, rows, truth = chart(overlap=True)
        result = extract_curve_series(image, box, rows)
        self.assertLessEqual(result["point_count"], 15)
        for series in result["series"]:
            for p in series["points_px"]:
                self.assertTrue(any(abs(p["px"]-x) <= 2 and abs(p["py"]-y) <= 2 for x, y in truth[series["label"]]))

    def test_no_markers_does_not_turn_line_pixels_into_experiments(self):
        image, box, rows, _ = chart(filled=False)
        result = extract_curve_series(image, box, rows)
        self.assertEqual(result["point_count"], 0)
        self.assertFalse(result["is_verified"])

    def test_same_color_multiple_series_are_flagged_and_skipped(self):
        image, box, rows, _ = chart(ambiguous=True)
        result = extract_curve_series(image, box, rows)
        self.assertNotIn("Catalyst-A", {s["label"] for s in result["series"]})
        self.assertTrue(any("同色多系列" in w for w in result["warnings"]))

    def test_ocr_line_strokes_do_not_become_legend_name(self):
        image, box, rows, truth = chart()
        for row in rows:
            label = row["text"]
            row["words"] = [{"text": "一", "bbox": [318, row["bbox"][1], 328, row["bbox"][3]]},
                            {"text": label, "bbox": row["bbox"]}]
            row["text"] = "一 "+label
            row["bbox"] = [318, row["bbox"][1], 450, row["bbox"][3]]
        result = extract_curve_series(image, box, rows)
        self.assertEqual({s["label"] for s in result["series"]}, set(truth))
        self.assertEqual(result["point_count"], 15)


if __name__ == "__main__":
    unittest.main()
