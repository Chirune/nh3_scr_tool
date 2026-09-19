"""Summarize vector marker candidates and text coordinates on selected PDF pages."""
import argparse
import collections
import json
from pathlib import Path

import pdfplumber


def rounded_color(value):
    if not isinstance(value, (list, tuple)):
        return None
    return tuple(round(float(x), 4) for x in value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    parser.add_argument("pages", nargs="+", type=int)
    args = parser.parse_args()
    with pdfplumber.open(Path(args.pdf)) as pdf:
        for page_number in args.pages:
            page = pdf.pages[page_number - 1]
            objects = []
            for object_kind in ("curves", "rects", "lines"):
                for obj in getattr(page, object_kind):
                    width, height = float(obj.get("width", 0)), float(obj.get("height", 0))
                    if 1 <= width <= 15 and 1 <= height <= 15:
                        objects.append({
                            "kind": object_kind,
                            "color": rounded_color(obj.get("non_stroking_color")),
                            "stroke": rounded_color(obj.get("stroking_color")),
                            "width": round(width, 3),
                            "height": round(height, 3),
                            "points": len(obj.get("pts", [])),
                            "x": round((obj["x0"] + obj["x1"]) / 2, 3),
                            "y": round((obj["top"] + obj["bottom"]) / 2, 3),
                        })
            counts = collections.Counter((o["kind"], o["color"], o["stroke"], o["width"], o["height"], o["points"]) for o in objects)
            words = [
                {"text": w["text"], "x0": round(w["x0"], 2), "x1": round(w["x1"], 2), "top": round(w["top"], 2), "bottom": round(w["bottom"], 2)}
                for w in page.extract_words()
                if any(ch.isdigit() for ch in w["text"]) or w["text"] in {"T", "Time", "Conversion", "Selectivity"}
            ]
            print(json.dumps({
                "page": page_number,
                "width": page.width,
                "height": page.height,
                "marker_signatures": [{"signature": key, "count": count} for key, count in counts.most_common()],
                "words": words,
            }, ensure_ascii=False))


if __name__ == "__main__":
    main()
