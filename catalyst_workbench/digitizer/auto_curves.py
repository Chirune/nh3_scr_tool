"""Local, conservative extraction of visible chart markers.

Coordinates are in the supplied image. Legend samples are never measurements;
only separated, sufficiently thick marker cores become ``marker_candidates``.
No missing points, labels, colours or experimental temperatures are invented.
"""
from __future__ import annotations

import re

import numpy as np
from PIL import Image, ImageDraw


def _components(mask, minimum=1):
    """Eight-connected components; sparse masks keep this inexpensive."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    result = []
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        stack, pixels = [(int(x), int(y))], []
        seen[y, x] = True
        while stack:
            xx, yy = stack.pop()
            pixels.append((xx, yy))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not dx and not dy:
                        continue
                    nx, ny = xx + dx, yy + dy
                    if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((nx, ny))
        if len(pixels) >= minimum:
            a = np.asarray(pixels, dtype=int)
            result.append({"bbox": [int(a[:, 0].min()), int(a[:, 1].min()),
                                    int(a[:, 0].max()) + 1, int(a[:, 1].max()) + 1],
                           "center": a.mean(axis=0).tolist(), "area": len(pixels)})
    return result


def _erode(mask, radius):
    h, w = mask.shape
    padded = np.pad(mask, radius)
    out = np.ones_like(mask)
    for dy in range(2 * radius + 1):
        for dx in range(2 * radius + 1):
            out &= padded[dy:dy+h, dx:dx+w]
    return out


def _prototypes(rgb, region):
    l, t, r, b = region
    a = rgb[t:b, l:r].astype(np.float32)
    saturation = a.max(axis=2) - a.min(axis=2)
    eligible = (saturation >= 55) & (a.min(axis=2) < 190)
    if not eligible.any():
        chromatic = []
    else:
        original = a[eligible]
        quantized = (original.astype(int) // 24) * 24
        colors, counts = np.unique(quantized, axis=0, return_counts=True)
        chromatic = []
        threshold = max(30, int((r-l)*(b-t)*0.00012))
        for i in np.argsort(counts)[::-1]:
            if counts[i] < threshold:
                break
            color = np.median(original[np.all(quantized == colors[i], axis=1)], axis=0)
            vector = color-color.min()
            vector /= max(1, np.linalg.norm(vector))
            if any(np.linalg.norm(vector-(np.asarray(p)-min(p))/max(1, np.linalg.norm(np.asarray(p)-min(p)))) < .22
                   for p in chromatic):
                continue
            chromatic.append(color.tolist())
            if len(chromatic) >= 12:
                break
    # Mid-grey filled markers can be distinguished from black axes/text. A
    # colour is retained only if marker detection subsequently supports it.
    grey = (saturation < 15) & (a.mean(axis=2) >= 35) & (a.mean(axis=2) <= 205)
    if grey.any():
        bins, counts = np.unique((a[grey].mean(axis=1).astype(int)//16)*16, return_counts=True)
        i = int(np.argmax(counts))
        if counts[i] >= max(40, int((r-l)*(b-t)*0.00012)):
            chosen = a[grey][np.abs(a[grey].mean(axis=1)-(float(bins[i])+8)) <= 8]
            if len(chosen):
                chromatic.append(np.median(chosen, axis=0).tolist())
    return chromatic


def _color_masks(rgb, color):
    a, c = rgb.astype(np.float32), np.asarray(color, dtype=np.float32)
    sat = a.max(axis=2)-a.min(axis=2)
    chroma = c.max()-c.min()
    if chroma < 25:
        delta = np.abs(a.mean(axis=2)-c.mean())
        return (sat < 18) & (delta < 25), (sat < 18) & (delta < 25)
    v = a-a.min(axis=2, keepdims=True)
    v /= np.maximum(np.linalg.norm(v, axis=2, keepdims=True), 1)
    cv = c-c.min()
    cv /= np.linalg.norm(cv)
    distance = np.linalg.norm(v-cv, axis=2)
    broad = (sat >= 23) & (distance < 0.22)
    strong = broad & ((sat >= chroma*0.66) | (np.linalg.norm(a-c, axis=2) < 36))
    return broad, strong


def _text_rows(lines):
    valid = []
    for line in lines or []:
        if line.get("orientation", 0) != 0:
            continue
        text = str(line.get("text", "")).strip()
        box = line.get("bbox")
        words = line.get("words", [])
        first_word = next((i for i, w in enumerate(words) if re.search(r"[A-Za-z]{2,}|[\u4e00-\u9fff]{2,}|\d{2,}|\d[.,]\d", str(w.get("text", "")))), None)
        if first_word is not None:
            # OCR often calls the legend line a dash/Chinese stroke and the
            # marker a punctuation glyph. Preserve only the actual text side.
            words = words[first_word:]
            text = " ".join(str(w["text"]) for w in words)
            box = [min(w["bbox"][0] for w in words), min(w["bbox"][1] for w in words),
                   max(w["bbox"][2] for w in words), max(w["bbox"][3] for w in words)]
        if text and isinstance(box, (list, tuple)) and len(box) == 4:
            valid.append({"text": text, "bbox": list(map(float, box)), "source":line.get('source','ocr')})
    # PDF word boxes help calibration but repeat complete legend lines. OCR
    # may also repeat the same visible text. Prefer the complete observation
    # instead of concatenating it two or three times into a series name.
    dedup = []
    priority={'pdf_visible_text':0,'pdf_rotated_text_object':1,'pdf_visible_word':3}
    for line in sorted(valid, key=lambda v: (priority.get(v['source'],2),-(v['bbox'][2]-v['bbox'][0]), v['bbox'][1])):
        x0,y0,x1,y1=line['bbox']
        area=max(1,(x1-x0)*(y1-y0))
        duplicate=False
        for old in dedup:
            a,b,c,d=old['bbox']
            intersection=max(0,min(x1,c)-max(x0,a))*max(0,min(y1,d)-max(y0,b))
            if intersection/min(area,max(1,(c-a)*(d-b)))>.65 and abs((y0+y1-b-d)/2)<max(3,(y1-y0)*.65):
                duplicate=True; break
        if not duplicate: dedup.append(line)
    valid=dedup
    rows = []
    for line in sorted(valid, key=lambda x: ((x["bbox"][1]+x["bbox"][3])/2, x["bbox"][0])):
        x0, y0, x1, y1 = line["bbox"]
        cy = (y0+y1)/2
        found = next((r for r in rows if abs(r["cy"]-cy) < max(3, min(r["height"], y1-y0)*0.55)), None)
        if found is None:
            rows.append({"cy": cy, "height": y1-y0, "items": [line]})
        else:
            found["items"].append(line)
    result = []
    for row in rows:
        items = sorted(row["items"], key=lambda x: x["bbox"][0])
        # Adjacent OCR tokens can be assembled, but large gaps split rows.
        chunks = []
        for item in items:
            if chunks and item["bbox"][0]-chunks[-1][-1]["bbox"][2] < max(15, row["height"]*2.0):
                chunks[-1].append(item)
            else:
                chunks.append([item])
        for chunk in chunks:
            result.append({"text": " ".join(x["text"] for x in chunk),
                           "bbox": [min(x["bbox"][0] for x in chunk), min(x["bbox"][1] for x in chunk),
                                    max(x["bbox"][2] for x in chunk), max(x["bbox"][3] for x in chunk)]})
    return result


def _find_legend(color_infos, rows, plot, allow_numeric=False):
    l, t, r, b = plot
    width = r-l
    # OCR/PDF text is stronger evidence than a faint legend line. Associate a
    # marker to the left of a non-numeric text row, then require a vertically
    # aligned multi-colour group. These are generic layout constraints.
    text_candidates = []
    for row in rows:
        x0, y0, x1, y1 = row["bbox"]
        if not (l <= x0 < x1 <= r and t <= y0 < y1 <= b) or not re.search(r"[A-Za-z\u4e00-\u9fff]", row["text"]):
            continue
        cy = (y0+y1)/2
        options = []
        for index, info in enumerate(color_infos):
            for core in info["cores"]:
                cx, my = core["center"]
                if 3 < x0-cx < width*.15 and abs(my-cy) < max(6, (y1-y0)*.65):
                    options.append((abs(my-cy), x0-cx, index, core))
        if options:
            _, _, index, core = min(options)
            text_candidates.append({"color_index": index, "label": row["text"], "bbox": row["bbox"],
                                    "swatch_bbox": core["bbox"], "center": core["center"]})
    text_groups = []
    for c in text_candidates:
        for group in text_groups:
            if abs(c["bbox"][0]-np.median([a["bbox"][0] for a in group])) < width*.04 and abs(c["center"][0]-np.median([a["center"][0] for a in group])) < width*.025:
                group.append(c)
                break
        else:
            text_groups.append([c])
    text_groups = [g for g in text_groups if len({c["color_index"] for c in g}) >= 2]
    if text_groups:
        group = max(text_groups, key=lambda g: len({c["color_index"] for c in g}))
        labels = {c["color_index"]: {k: c[k] for k in ("label", "bbox", "swatch_bbox")} for c in group}
        legend = [int(min(c["center"][0] for c in group)-width*.04),
                  int(min(min(c["bbox"][1], c["swatch_bbox"][1]) for c in group)-6),
                  int(max(c["bbox"][2] for c in group)+6),
                  int(max(max(c["bbox"][3], c["swatch_bbox"][3]) for c in group)+6)]
        return [max(l, legend[0]), max(t, legend[1]), min(r, legend[2]), min(b, legend[3])], labels
    candidates = []
    for index, info in enumerate(color_infos):
        for comp in info["components"]:
            x0, y0, x1, y1 = comp["bbox"]
            if width*0.035 <= x1-x0 <= width*0.20 and 3 <= y1-y0 <= width*0.055 and x1-x0 >= (y1-y0)*1.7:
                candidates.append({"color_index": index, "bbox": comp["bbox"], "center": [(x0+x1)/2, (y0+y1)/2]})
    groups = []
    for candidate in candidates:
        for group in groups:
            if abs(candidate["center"][0]-np.median([c["center"][0] for c in group])) < max(5, width*0.014):
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    groups = [g for g in groups if len({c["color_index"] for c in g}) >= 2]
    groups.sort(key=lambda g: len({c["color_index"] for c in g}), reverse=True)
    if not groups:
        return None, {}
    group = groups[0]
    ys = sorted(c["center"][1] for c in group)
    if len(ys) > 2:
        gaps = np.diff(ys)
        if min(gaps) < 3 or max(gaps) > min(gaps)*1.9:
            return None, {}
    gap = float(np.median(np.diff(ys))) if len(ys) > 1 else max(12, width*.025)
    labels = {}
    legend = [min(c["bbox"][0] for c in group), min(c["bbox"][1] for c in group),
              max(c["bbox"][2] for c in group), max(c["bbox"][3] for c in group)]
    for row in rows:
        x0, y0, x1, y1 = row["bbox"]
        cy = (y0+y1)/2
        match = min(group, key=lambda c: abs(c["center"][1]-cy))
        if abs(match["center"][1]-cy) <= max(gap*.4, (y1-y0)*.55) and x0 >= match["bbox"][2]-5 and x0 < match["bbox"][2]+width*.08:
            if re.search(r"[A-Za-z\u4e00-\u9fff]", row["text"]) or (allow_numeric and re.fullmatch(r"[+-]?\d+(?:\.\d+)?", row["text"])):
                labels[match["color_index"]] = {"label": row["text"], "bbox": row["bbox"], "swatch_bbox": match["bbox"]}
                legend = [min(legend[0], x0), min(legend[1], y0), max(legend[2], x1), max(legend[3], y1)]
    # Include a grey row immediately above/below chromatic legend rows. It is
    # associated by the same horizontal symbol position, never by a fixed name.
    center_x = float(np.median([c["center"][0] for c in group]))
    for index, info in enumerate(color_infos):
        if np.ptp(info["color"]) >= 25:
            continue
        for comp in info["cores"]:
            cx, cy = comp["center"]
            if abs(cx-center_x) < width*.017 and ys[0]-gap*1.4 <= cy <= ys[-1]+gap*1.4:
                a = comp["bbox"]
                legend[1] = min(legend[1], a[1]-4)
                legend[3] = max(legend[3], a[3]+4)
                for row in rows:
                    bx = row["bbox"]
                    if abs((bx[1]+bx[3])/2-cy) < max(5, gap*.4) and bx[0] > cx+4 and bx[0] < cx+width*.15:
                        labels[index] = {"label": row["text"], "bbox": bx, "swatch_bbox": [cx-width*.035, cy-gap*.3, cx+width*.035, cy+gap*.3]}
                        legend[2] = max(legend[2], bx[2])
    # Without OCR, mask the swatch column and a bounded area to its right where
    # a legend conventionally prints text. This geometric region is explicit.
    if not labels:
        legend[2] = min(r, legend[2]+width*.28)
    margin = max(4, width*.006)
    legend = [max(l, int(legend[0]-margin)), max(t, int(legend[1]-margin)),
              min(r, int(legend[2]+margin)), min(b, int(legend[3]+margin))]
    return legend, labels


def extract_curve_series(image, plot_bbox, visible_text_lines, calibration=None):
    """Return visible marker centres per legend colour, with explicit evidence.

    ``visible_text_lines`` accepts PDF/OCR dictionaries with ``text``/``bbox``.
    Axis calibration is passed through for callers; no axis values are assumed.
    A connected thin line is not interpreted as many experimental observations.
    """
    image = image.convert("RGB")
    rgb = np.asarray(image)
    if not isinstance(plot_bbox, (list, tuple)) or len(plot_bbox) != 4:
        raise ValueError("自动曲线读取需要有效的绘图区边界。")
    plot = [int(round(x)) for x in plot_bbox]
    l, t, r, b = plot
    if not (0 <= l < r <= image.width and 0 <= t < b <= image.height) or r-l < 60 or b-t < 50:
        raise ValueError("绘图区过小或越出图片，无法可靠读取。")
    radius = max(1, min(4, int(round((r-l)/350))))
    infos = []
    roi = np.zeros((image.height, image.width), dtype=bool)
    roi[t+2:b-2, l+2:r-2] = True
    for color in _prototypes(rgb, plot):
        broad, strong = _color_masks(rgb, color)
        broad &= roi
        strong &= roi
        cores = _components(_erode(strong, radius), 3)
        comps = _components(broad, 5)
        if len(cores) >= 3:
            infos.append({"color": [int(round(c)) for c in color], "cores": cores, "components": comps, "strong": strong})
    legend, labels = _find_legend(infos, _text_rows(visible_text_lines), plot)
    warnings = ["像素定位得到近似值；需核对坐标标定、图例与标记，图像分辨率会影响误差。"]
    if legend is None:
        warnings.append("未能可靠定位图例；只输出可分离的标记候选，系列名称待核对。")
    elif not labels:
        warnings.append("已按色样排除图例，但图例文字未识别；系列名称待核对。")
    series = []
    markers_overlay = []
    for index, info in enumerate(infos):
        if np.ptp(info["color"]) < 25 and index not in labels:
            warnings.append("灰色标记缺少可关联的图例文字，可能与网格或注释混淆；该颜色跳过自动输出。")
            continue
        points = []
        for core in info["cores"]:
            cx, cy = core["center"]
            if legend and legend[0] <= cx <= legend[2] and legend[1] <= cy <= legend[3]:
                continue
            box = core["bbox"]
            # A long surviving thick path or text blob is never one marker.
            if max(box[2]-box[0], box[3]-box[1]) > max(25, (r-l)*.045):
                continue
            if min(box[2]-box[0], box[3]-box[1]) < 2:
                continue
            points.append({"px": float(cx), "py": float(cy), "marker_core_bbox": box,
                           "pixel_radius": radius+1, "method": "automatic_marker_candidate", "review_status": "unreviewed"})
        points.sort(key=lambda p: (p["px"], p["py"]))
        if len(points) < 3:
            continue
        # Parallel curves/independent scatter may share a colour. Do not merge
        # distinct vertical clusters at the same x into a named single series.
        ambiguous = any(abs(a["px"]-z["px"]) < max(3, radius*2) and abs(a["py"]-z["py"]) > max(10, radius*5)
                        for a, z in zip(points, points[1:]))
        if ambiguous:
            warnings.append("一个颜色对应多个纵向标记簇，可能存在同色多系列；该颜色跳过自动输出。")
            continue
        label_info = labels.get(index, {})
        raw_label = label_info.get("label", f"未命名系列 {len(series)+1}")
        label = re.sub(r"(?<=[A-Z])\s+(?=[A-Z])", "", raw_label)
        label = re.sub(r"\s*[·•]\s*(?=[A-Za-z0-9])", "-", label)
        sw = [] if label_info else ["series_label_unresolved"]
        item = {"label": label, "label_raw": raw_label, "label_requires_review": True,
                "color": info["color"], "points_px": points,
                "method": "automatic_marker_candidates", "warnings": sw,
                "label_basis": "visible_legend_text" if label_info else "unresolved",
                "legend_text_bbox": label_info.get("bbox"), "legend_swatch_bbox": label_info.get("swatch_bbox"),
                "marker_count": len(points), "value_origin": "image_digitized_approximate"}
        series.append(item)
        markers_overlay.extend({"px": p["px"], "py": p["py"], "color": info["color"], "label": label} for p in points)
    if not series:
        warnings.append("没有发现可可靠分离的曲线标记；未生成数据点，需人工框选或其他识别方式。")
    elif len({s["marker_count"] for s in series}) > 1:
        warnings.append("各系列可见标记数量不同；重叠、遮挡或浅色标记可能漏检，未插值补点。")
        maximum = max(s["marker_count"] for s in series)
        for item in series:
            if item["marker_count"] < maximum:
                item["warnings"].append("fewer_visible_markers_requires_review")
    return {"series": series, "legend_bbox": legend, "plot_bbox": plot, "warnings": warnings,
            "method": "legend_color_thick_marker_components", "calibration": calibration,
            "overlay": {"plot_bbox": plot, "legend_bbox": legend, "markers": markers_overlay},
            "point_count": sum(s["marker_count"] for s in series), "is_verified": False}


def draw_curve_overlay(image, result):
    """A local preview making both plotted points and excluded legend visible."""
    out = image.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    overlay = result.get("overlay", {})
    if overlay.get("plot_bbox"):
        draw.rectangle(overlay["plot_bbox"], outline=(225, 130, 0), width=2)
    if overlay.get("legend_bbox"):
        draw.rectangle(overlay["legend_bbox"], outline=(220, 30, 120), width=2)
    for point in overlay.get("markers", []):
        x, y = point["px"], point["py"]
        draw.ellipse([x-6, y-6, x+6, y+6], outline=(0, 0, 0), width=2)
        draw.line([x-8, y, x+8, y], fill=(0, 0, 0), width=1)
        draw.line([x, y-8, x, y+8], fill=(0, 0, 0), width=1)
    return out
