"""Local, evidence-bearing automatic plot calibration.

The module reads visible PDF glyphs before accepting OCR observations supplied
by the caller. It never obtains tick values from DOI, page number, or caption.
At least three independent printed ticks must support each axis. Ambiguous
linear/logarithmic fits and non-performance diagrams remain explicit failures.
Coordinates in the result are always original crop-image pixels.
"""
from __future__ import annotations

import ctypes
import hashlib
import math
from pathlib import Path
import re
import unicodedata

import numpy as np
from PIL import Image
import pypdfium2 as pdfium
from pypdf import PdfReader

try:
    from .sources import PDFIUM_LOCK
    from .digitize import validate_calibration
except ImportError:
    from sources import PDFIUM_LOCK
    from digitize import validate_calibration


NUMBER = re.compile(r"^[+−–-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+−–-]?\d+)?$")
NON_PERFORMANCE = re.compile(
    r"(?:measuring\s+probe\s+positions|probe\s+positions|schematic\s+(?:diagram|illustration)|"
    r"experimental\s+(?:setup|apparatus)|reactor\s+(?:schematic|configuration)|"
    r"outlet\s+face|flow\s+(?:diagram|chart)|reaction\s+(?:scheme|pathway|mechanism))", re.I)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _crop_text(lines, offset_x, offset_y, scale_x, scale_y, width, height):
    result = []
    for line in lines:
        box = line["bbox"]
        mapped = [(box[0]-offset_x)/scale_x, (box[1]-offset_y)/scale_y,
                  (box[2]-offset_x)/scale_x, (box[3]-offset_y)/scale_y]
        cx, cy = (mapped[0]+mapped[2])/2, (mapped[1]+mapped[3])/2
        if 0 <= cx <= width and 0 <= cy <= height:
            result.append({"text": line["text"], "bbox": mapped,
                           "source": line.get("source", "pdf_visible_text")})
    return result


def visible_pdf_text(session, image_size):
    """Read visible glyph text and rotated PDF text objects, under one lock."""
    meta = session.get("source_metadata", {})
    source = Path(meta.get("source_path", ""))
    if source.suffix.lower() != ".pdf" or not source.is_file():
        return [], ["原图没有可读取的 PDF 文字层。"]
    if meta.get("source_sha256") and _sha256(source) != meta["source_sha256"]:
        return [], ["源 PDF 已改变，自动识别已停止。"]
    # Imports stay lazy so this module is usable independently in the digitizer.
    try:
        from locate import (_page_geometry, _text_lines, _device_box, _intersection,
                            _area, _union, RENDER_MAX_DIMENSION, MAX_RENDER_PIXELS)
    except ImportError:
        import sys
        parent = str(Path(__file__).resolve().parent.parent)
        if parent not in sys.path:
            sys.path.append(parent)
        from locate import (_page_geometry, _text_lines, _device_box, _intersection,
                            _area, _union, RENDER_MAX_DIMENSION, MAX_RENDER_PIXELS)
    context = meta.get("figure_context", {})
    transform = context.get("crop_to_page", {})
    page_index = int(meta.get("page", 1))-1
    reader = PdfReader(str(source))
    if not 0 <= page_index < len(reader.pages):
        return [], ["来源 PDF 页码无效。"]
    with PDFIUM_LOCK:
        document = pdfium.PdfDocument(str(source))
        page = None
        try:
            page = document[page_index]
            pw, ph = page.get_size()
            if context:
                scale = min(RENDER_MAX_DIMENSION/max(pw, ph),
                            math.sqrt(MAX_RENDER_PIXELS/(pw*ph)))
                width = int(context.get("page_width") or math.ceil(pw*scale))
                height = int(context.get("page_height") or math.ceil(ph*scale))
            else:
                width, height = image_size
                scale = width/pw
            *_, clips = _page_geometry(page, reader.pages[page_index], reader, width, height, scale)
            lines, flags = _text_lines(page, width, height, scale, clips)
            # Horizontal row segmentation cannot reconstruct vertical axis
            # names. The PDF text-object order retains those visible words.
            textpage = page.get_textpage()
            groups = {}
            upright_glyphs = []
            try:
                for index in range(min(textpage.count_chars(), 60000)):
                    cp = pdfium.raw.FPDFText_GetUnicode(textpage, index)
                    if cp <= 0 or cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
                        continue
                    char = chr(cp)
                    raw = pdfium.raw.FPDFText_GetTextObject(textpage, index)
                    if not raw or pdfium.raw.FPDFTextObj_GetTextRenderMode(raw) == 3:
                        continue
                    address = ctypes.cast(raw, ctypes.c_void_p).value
                    box = _device_box(page, textpage.get_charbox(index), width, height)
                    if address in clips:
                        clip = clips[address]
                        overlap = _intersection(box, clip) if clip else None
                        if overlap is None or _area(overlap) < _area(box)*.45:
                            continue
                    groups.setdefault(address, []).append((char, box))
                    angle = float(pdfium.raw.FPDFText_GetCharAngle(textpage, index))
                    if min(abs(angle), abs(angle-2*math.pi)) < .02:
                        upright_glyphs.append((char, box))
            finally:
                textpage.close()
            for glyphs in groups.values():
                text = "".join(g[0] for g in glyphs).strip()
                if not text:
                    continue
                box = _union([g[1] for g in glyphs])
                if box[3]-box[1] > 2*(box[2]-box[0]) and len(text) >= 4:
                    lines.append({"text": text, "bbox": box, "source": "pdf_rotated_text_object"})
            # A PDF row can contain many ticks and parts of a vertical title.
            # Keep actual glyph boxes, so "100 150 200" is three observations.
            for word in _pdf_words(upright_glyphs):
                lines.append({**word, "source": "pdf_visible_word"})
        finally:
            if page is not None:
                page.close()
            document.close()
    return _crop_text(lines, float(transform.get("offset_x", 0)),
                      float(transform.get("offset_y", 0)), float(transform.get("scale_x", 1)),
                      float(transform.get("scale_y", 1)), *image_size), flags


def _number(text):
    # Internal whitespace may separate distinct tick labels (e.g. "0 20").
    # Joining them would manufacture a number that was never printed.
    text = str(text).strip().translate(str.maketrans("０１２３４５６７８９．，＋－", "0123456789.,+-")).replace("−", "-").replace("–", "-")
    # OCR may tokenize the visibly printed decimal separator. Join only
    # digit–separator–digit sequences, never two whitespace-separated numbers.
    text = re.sub(r"(?<=\d)\s*[.,、]\s*(?=\d)", ".", text)
    if not NUMBER.fullmatch(text):
        return None
    try:
        value = float(text.replace(",", "."))
        return value if math.isfinite(value) else None
    except ValueError:
        return None


def _pdf_words(glyphs):
    bands = []
    for char, box in sorted(glyphs, key=lambda item: (item[1][3], item[1][0])):
        if not char.strip():
            continue
        height = max(3, box[3]-box[1])
        band = next((b for b in bands if abs(box[3]-np.median([x[1][3] for x in b])) <= max(2, height*.18)), None)
        if band is None:
            bands.append([(char,box)])
        else:
            band.append((char,box))
    result = []
    for band in bands:
        chunks = []
        for glyph in sorted(band, key=lambda item:item[1][0]):
            char, box = glyph
            if chunks:
                previous = chunks[-1][-1]
                if char == previous[0] and max(abs(a-b) for a,b in zip(box,previous[1])) < 1:
                    continue
                height = max(box[3]-box[1], previous[1][3]-previous[1][1])
                if box[0]-previous[1][2] <= max(2.5,height*.45):
                    chunks[-1].append(glyph)
                    continue
            chunks.append([glyph])
        for chunk in chunks:
            result.append({"text":"".join(c for c,b in chunk),"bbox":[min(b[0] for c,b in chunk),min(b[1] for c,b in chunk),max(b[2] for c,b in chunk),max(b[3] for c,b in chunk)]})
    return result


def _deduplicate(lines):
    result = []
    for raw in lines:
        try:
            line = {**raw, "text": str(raw["text"]).strip(), "bbox": list(map(float, raw["bbox"])),
                    "source": str(raw.get("source", "ocr"))}
        except (KeyError, TypeError, ValueError):
            continue
        if not line["text"] or len(line["bbox"]) != 4 or not all(math.isfinite(v) for v in line["bbox"]):
            continue
        a, b, c, d = line["bbox"]
        if c <= a or d <= b:
            continue
        if any(line["text"] == other["text"] and
               abs((a+c-other["bbox"][0]-other["bbox"][2])/2) < 4 and
               abs((b+d-other["bbox"][1]-other["bbox"][3])/2) < 4 for other in result):
            continue
        result.append(line)
    return result


def _runs(mask, gap=2):
    # Bridge only tiny rasterization gaps, never a substantial missing axis.
    a = np.asarray(mask, dtype=bool).copy()
    locations = np.flatnonzero(a)
    for left, right in zip(locations, locations[1:]):
        if right-left <= gap+1:
            a[left:right+1] = True
    diff = np.diff(np.r_[False, a, False].astype(np.int8))
    return list(zip(np.flatnonzero(diff == 1), np.flatnonzero(diff == -1)-1))


def _long_lines(image, minimum_fraction=.25):
    rgb = np.asarray(image.convert("RGB"))
    # Black/grey axes remain, coloured curves do not form candidate frames.
    mask = (rgb.max(axis=2) < 125) & (rgb.max(axis=2)-rgb.min(axis=2) < 35)
    height, width = mask.shape
    horizontal, vertical = [], []
    for y in np.flatnonzero(mask.sum(axis=1) >= max(40,width*minimum_fraction*.88)):
        for x0, x1 in _runs(mask[y]):
            if x1-x0 >= max(45,width*minimum_fraction):
                horizontal.append((int(y), int(x0), int(x1)))
    for x in np.flatnonzero(mask.sum(axis=0) >= max(35,height*minimum_fraction*.88)):
        for y0, y1 in _runs(mask[:, x]):
            if y1-y0 >= max(40,height*minimum_fraction):
                vertical.append((int(x), int(y0), int(y1)))
    # Collapse the adjacent pixels making a single thick printed stroke.
    def collapse(lines):
        groups = []
        for line in lines:
            matching = next((g for g in groups if abs(line[0]-np.mean([v[0] for v in g])) <= 3 and
                             abs(line[1]-g[0][1]) <= 8 and abs(line[2]-g[0][2]) <= 8), None)
            if matching is None:
                groups.append([line])
            else:
                matching.append(line)
        return [tuple(float(np.median([v[i] for v in g])) for i in range(3)) for g in groups]
    return collapse(horizontal), collapse(vertical)


def find_plot_frames(image):
    horizontal, vertical = _long_lines(image, minimum_fraction=.12)
    frames = []
    for hy,hx0,hx1 in horizontal:
        for vx,vy0,vy1 in vertical:
            if abs(vx-hx0)>12 or abs(hy-vy1)>12:
                continue
            box = [vx,vy0,hx1,hy]
            if hx1-vx < max(70,image.width*.12) or hy-vy0 < max(65,image.height*.12):
                continue
            if not any(max(abs(a-b) for a,b in zip(box,old))<12 for old in frames):
                frames.append(box)
    return sorted(frames,key=lambda b:(b[1],b[0]))


def _strict_axis_fit(ticks, axis, axis_length):
    """Fit >=3 ticks; report linear/log ambiguity instead of guessing."""
    coordinate = 0 if axis == "x" else 1
    ticks = sorted(ticks, key=lambda t: t["pixel"][coordinate])
    unique = []
    for tick in ticks:
        if any(abs(tick["pixel"][coordinate]-t["pixel"][coordinate]) < 3 for t in unique):
            continue
        unique.append(tick)
    if len(unique) < 3:
        return None, "printed_ticks_less_than_three"
    pixels = np.array([t["pixel"][coordinate] for t in unique], dtype=float)
    values = np.array([t["value"] for t in unique], dtype=float)
    differences = np.diff(values)
    if not (np.all(differences > 0) or np.all(differences < 0)):
        return None, "printed_ticks_not_monotonic"
    if np.ptp(pixels) < axis_length*.4:
        return None, "printed_ticks_span_too_small"
    fits = []
    for kind in ("linear", "log10"):
        if kind == "log10" and np.any(values <= 0):
            continue
        target = np.log10(values) if kind == "log10" else values
        slope, intercept = np.polyfit(target, pixels, 1)
        if abs(slope) < 1e-12:
            continue
        predicted = slope*target+intercept
        residual = float(np.max(np.abs(predicted-pixels)))
        rms = float(np.sqrt(np.mean((predicted-pixels)**2)))
        threshold = max(2.5, axis_length*.007)
        if residual <= threshold:
            fits.append({"scale": kind, "slope": float(slope), "intercept": float(intercept),
                         "max_residual_pixels": residual, "rms_residual_pixels": rms})
    if not fits:
        return None, "printed_ticks_do_not_fit_linear_or_log"
    if len(fits) > 1:
        return None, "linear_log_ambiguous"
    fit = fits[0]
    fit["ticks"] = unique
    return fit, ""


def _axis_fit(ticks, axis, axis_length):
    """Keep complete fits first; tolerate a minority of demonstrable OCR outliers.

    Only observed numeric labels are used. No missing digits or ticks are
    filled in. A consensus needs four anchors, a majority of the observations,
    and broad pixel coverage. Competing scales/transforms stay unresolved.
    """
    fit, reason = _strict_axis_fit(ticks, axis, axis_length)
    if fit or reason == "linear_log_ambiguous":
        return fit, reason
    coordinate = 0 if axis == "x" else 1
    unique = []
    for tick in sorted(ticks, key=lambda t: t["pixel"][coordinate]):
        if not any(abs(tick["pixel"][coordinate]-t["pixel"][coordinate]) < 3 for t in unique):
            unique.append(tick)
    if len(unique) < 6 or not all("ocr" in t.get("source", "").lower() for t in unique):
        return None, reason
    pixels = np.array([t["pixel"][coordinate] for t in unique], dtype=float)
    values = np.array([t["value"] for t in unique], dtype=float)
    differences = np.diff(values)
    if np.all(differences > 0) or np.all(differences < 0):
        # A coherent but nonuniform scale may be broken/piecewise. Do not
        # turn that into one global transform by dropping a whole segment.
        return None, reason
    threshold = max(2.5, axis_length*.007)
    minimum = max(4, math.ceil(len(unique)*.60))
    options = {}
    for scale in ("linear", "log10"):
        eligible = np.isfinite(values) & ((values > 0) if scale == "log10" else True)
        target = np.full_like(values, np.nan)
        target[eligible] = np.log10(values[eligible]) if scale == "log10" else values[eligible]
        for i in range(len(unique)):
            for j in range(i+1, len(unique)):
                if not (eligible[i] and eligible[j]) or target[j] == target[i]:
                    continue
                slope = (pixels[j]-pixels[i])/(target[j]-target[i])
                intercept = pixels[i]-slope*target[i]
                residual = np.abs(slope*target+intercept-pixels)
                indexes = tuple(np.flatnonzero(eligible & (residual <= threshold)).tolist())
                if len(indexes) < minimum or np.ptp(pixels[list(indexes)]) < axis_length*.60:
                    continue
                accepted = [unique[k] for k in indexes]
                candidate, failure = _strict_axis_fit(accepted, axis, axis_length)
                if failure or candidate["scale"] != scale:
                    continue
                # An outlier just outside tolerance may be a distorted/broken
                # axis, not a lost OCR digit. Do not silently discard it.
                rejected = [k for k in range(len(unique)) if k not in indexes]
                fitted = candidate["slope"]*target+candidate["intercept"]
                if any(eligible[k] and abs(fitted[k]-pixels[k]) < threshold*3 for k in rejected):
                    continue
                candidate.update(fit_method="ocr_tick_consensus", rejected_ticks=[unique[k] for k in rejected])
                options[(scale, indexes)] = candidate
    ordered = sorted(options.values(), key=lambda f: (-len(f["ticks"]), f["rms_residual_pixels"]))
    if not ordered:
        return None, reason
    best = ordered[0]
    for other in ordered[1:]:
        if len(other["ticks"]) < len(best["ticks"])-1:
            continue
        shared_scale = best["scale"] == other["scale"]
        samples = np.linspace(pixels.min(), pixels.max(), 5)
        predicted_other = other["slope"]*((samples-best["intercept"])/best["slope"])+other["intercept"]
        if not shared_scale or np.max(np.abs(samples-predicted_other)) > threshold*2:
            return None, "competing_ocr_tick_consensus"
    return best, ""


def _bands(tokens, axis):
    groups = []
    coordinate = 1 if axis == "x" else 0
    for token in sorted(tokens, key=lambda t: t["pixel"][coordinate]):
        box = token["bbox"]
        position = token["pixel"][1] if axis == "x" else box[2]
        tolerance = max(4, (box[3]-box[1])*.8) if axis == "x" else max(5, (box[3]-box[1])*.7)
        group = next((g for g in groups if abs(position-np.median([v["pixel"][1] if axis == "x" else v["bbox"][2] for v in g])) <= tolerance), None)
        if group is None:
            groups.append([token])
        else:
            group.append(token)
    return [g for g in groups if len(g) >= 3]


def _axis_name(lines, bbox, axis, tick_band):
    x0, y0, x1, y1 = bbox
    names = []
    for line in lines:
        if _number(line["text"]) is not None:
            continue
        b = line["bbox"]
        cx, cy = (b[0]+b[2])/2, (b[1]+b[3])/2
        if axis == "x" and x0 <= cx <= x1 and cy > tick_band and cy <= y1 + (y1-y0)*.25:
            names.append(line)
        if axis == "y" and b[2] < tick_band and y0 <= cy <= y1:
            names.append(line)
    if not names:
        return "", "", None
    # A rotated vertical label may be long in y and narrow in x; OCR callers
    # should provide it as one token after rotating the left strip.
    def title_score(line):
        text = line["text"]
        useful = len(re.findall(r"conversion|selectivity|temperature|pressure|intensity|signal|desorption|adsorption|rate|storage|surface|energy|percentage|absorbance|current|voltage|time|yield|quantity|adsorbed|asdorbed|mmol", text, re.I))
        unit = bool(re.search(r"\((?:ppm|%|a\.?\s*u\.?|[°℃]?[CK]|eV|V|mA)\)", text, re.I))
        return useful*20 + unit*10 + (3 if line.get("source", "").startswith("pdf") else 0) + min(len(text),70)*.04
    chosen = max(names, key=title_score)
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", chosen["text"])).strip()
    match = re.search(r"[\[(]\s*([^\])]+)\s*[\])]\s*$", text)
    unit = match.group(1).strip() if match else ("%" if "%" in text else "")
    name = text[:match.start()].strip() if match else text.replace("%", "").strip()
    for canonical in ("Temperature", "Conversion", "Selectivity", "Pressure", "Time"):
        if name.replace(" ", "").casefold() == canonical.casefold():
            name = canonical
    unit = unit.replace("° C", "°C").replace("℃", "°C")
    if re.search('temperature',name,re.I) and re.fullmatch(r'[oO0]\s*C',unit):
        chosen={**chosen,'unit_normalized_from':unit}
        unit='°C'
    return name, unit, chosen


def calibrate_from_text(image, text_lines, caption=""):
    """Calibrate an upright Cartesian plot from visible text observations."""
    lines = _deduplicate(text_lines)
    result = {"status": "needs_review", "chart_type": "unknown", "plot_bbox": None,
              "calibration": None, "visible_text_lines": lines, "ticks": {"x": [], "y": []},
              "axis_evidence": {}, "reasons": [], "method": "visible_text_tick_regression"}
    combined = " ".join([caption]+[l["text"] for l in lines])
    if NON_PERFORMANCE.search(caption):
        result.update(status="refuse_numeric", chart_type="non_performance",
                      reasons=["图注表明这是装置、测点或机理示意图；不自动生成性能读数。"])
        return result
    if re.search(r"X\s*coordinates?\s*[\[(]mm", combined, re.I) and re.search(r"probe|outlet\s+face", combined, re.I):
        result.update(status="refuse_numeric", chart_type="non_performance",
                      reasons=["图中毫米坐标描述测点布局；不是催化性能曲线。"])
        return result
    tokens = []
    for line in lines:
        if line.get("orientation", 0) != 0:
            # Rotated OCR is useful for axis names, not sideways glyphs mistaken
            # for tick numbers.
            continue
        # When a whole line is one number (e.g. '0 ． 2'), its component
        # glyphs '0' and '2' must not become two contradictory ticks.
        observations = [{"text": line["text"], "bbox": line["bbox"]}]
        if _number(line["text"]) is None:
            observations += list(line.get("words", []))
        for observation in observations:
            value = _number(observation["text"])
            if value is not None:
                box = list(map(float, observation["bbox"]))
                token = {"text": observation["text"], "bbox": box, "source": line["source"],
                         "value": value, "pixel": [(box[0]+box[2])/2, (box[1]+box[3])/2]}
                if not any(abs(token["pixel"][0]-t["pixel"][0]) < 2 and abs(token["pixel"][1]-t["pixel"][1]) < 2 for t in tokens):
                    tokens.append(token)
    horizontals, verticals = _long_lines(image)
    candidates = []
    partial_x = []
    failures = set()
    for hy, hx0, hx1 in horizontals:
        for vx, vy0, vy1 in verticals:
            # Printed conventional bottom and left axes meet at this corner.
            if abs(vx-hx0) > 12 or abs(hy-vy1) > 12:
                continue
            width, height = hx1-vx, hy-vy0
            if width < image.width*.25 or height < image.height*.25:
                continue
            bbox = [vx, vy0, hx1, hy]
            x_tokens, y_tokens = [], []
            for token in tokens:
                px, py = token["pixel"]
                b = token["bbox"]
                font_height = max(5, b[3]-b[1])
                if vx-8 <= px <= hx1+8 and hy+1 <= py <= hy+font_height*4.5:
                    x_tokens.append(token)
                if vy0-8 <= py <= hy+8 and vx-font_height*5 <= b[2] <= vx-1:
                    y_tokens.append(token)
            for x_band in _bands(x_tokens, "x"):
                xf, reason = _axis_fit(x_band, "x", width)
                if reason:
                    failures.add(reason)
                    continue
                partial_x.append({"bbox": bbox, "x": xf, "y_token_count": len(y_tokens)})
                for y_band in _bands(y_tokens, "y"):
                    yf, reason = _axis_fit(y_band, "y", height)
                    if reason:
                        failures.add(reason)
                        continue
                    score = len(xf["ticks"])+len(yf["ticks"])-(xf["rms_residual_pixels"]+yf["rms_residual_pixels"])/3
                    candidates.append({"bbox": bbox, "x": xf, "y": yf, "score": score})
    if not candidates:
        if partial_x:
            best_partial = max(partial_x, key=lambda item: len(item["x"]["ticks"])-item["x"]["rms_residual_pixels"])
            fit, box = best_partial["x"], best_partial["bbox"]
            band = float(np.median([tick["pixel"][1] for tick in fit["ticks"]]))
            name, unit, evidence = _axis_name(lines, box, "x", band)
            result["ticks"]["x"] = fit["ticks"]
            result["partial_axes"] = {"x": {"status": "recognized", "name": name, "unit": unit,
                "fit_scale": fit["scale"], "tick_count": len(fit["ticks"]), "ticks": fit["ticks"], "name_evidence": evidence},
                "y": {"status": "not_calibrated", "nearby_numeric_token_count": best_partial["y_token_count"]}}
            result["reason_codes"] = ["y_numeric_ticks_not_recognized"]
            result["reasons"] = [f"横轴已识别 {len(fit['ticks'])} 个刻度；纵轴未获得可靠的数字刻度标定，不能生成完整 X/Y 数值。"]
            if re.search(r"normaliz\w*\s.{0,100}max\w*.{0,80}min\w*", caption, re.I):
                result["reason_codes"].append("normalized_profile_without_numeric_y")
                result["reasons"].append("图注说明曲线按最大和最小值归一化。相对曲线形状可另行记录，但不能据此恢复原始相位值。")
            result["next_action"] = "核对原图纵轴刻度或查询补充原始数据；若没有刻度，只能另行处理相对趋势，不能手动猜测 Y 轴数值。"
            return result
        if len(tokens) < 6:
            if re.search(r"normaliz\w*\s.{0,100}max\w*.{0,80}min\w*", caption, re.I):
                result["reasons"] = ["图注说明这是按最大、最小值归一化的线剖面；尚未获得纵轴数字刻度，不能恢复原始相位数值。",
                                      "图中横轴位置刻度的自动标定也尚未完成，不能将示意图中的尺寸文字当成轴刻度。"]
                result["reason_codes"] = ["normalized_profile_without_numeric_y", "insufficient_visible_tick_text"]
                result["next_action"] = "原图纵轴若未给数字刻度，请查询补充原始数据；归一化相对形状需要独立的曲线读取方式，本版尚未自动导出。不能猜填 Y 值。"
                return result
            result["reasons"] = ["图内可识别的数字不足，尚不能建立横、纵轴标定。"]
            result["reason_codes"] = ["insufficient_visible_tick_text"]
            return result
        result["reasons"] = ["未找到同时受至少三个横轴刻度和三个纵轴刻度支持的绘图区。"]
        result["reason_codes"] = sorted(failures) or ["axis_geometry_or_tick_alignment_missing"]
        return result
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    distinct = [c for c in candidates[1:] if max(abs(a-b) for a,b in zip(c["bbox"],best["bbox"])) > 20]
    if distinct and distinct[0]["score"] >= best["score"]-1:
        result["reasons"] = ["图片包含多个同样可靠的绘图区，请先裁出一个子图后自动读取。"]
        result["reason_codes"] = ["multiple_plot_regions"]
        return result
    box = best["bbox"]
    calibration = {}
    for axis in ("x", "y"):
        fit = best[axis]
        first, last = fit["ticks"][0], fit["ticks"][-1]
        coordinate = 0 if axis == "x" else 1
        values = [first["value"], last["value"]]
        locations = [fit["slope"]*(math.log10(v) if fit["scale"] == "log10" else v)+fit["intercept"] for v in values]
        band = float(np.median([t["pixel"][1] for t in fit["ticks"]])) if axis == "x" else float(min(t["bbox"][0] for t in fit["ticks"]))
        name, unit, evidence = _axis_name(lines, box, axis, band)
        fixed = box[3] if axis == "x" else box[0]
        p1 = [locations[0], fixed] if axis == "x" else [fixed, locations[0]]
        p2 = [locations[1], fixed] if axis == "x" else [fixed, locations[1]]
        calibration[axis] = {"p1": p1, "p2": p2, "v1": values[0], "v2": values[1],
                             "scale": fit["scale"], "name": name, "unit": unit}
        result["ticks"][axis] = fit["ticks"]
        result["axis_evidence"][axis] = {"tick_count": len(fit["ticks"]),
            "max_residual_pixels": fit["max_residual_pixels"], "rms_residual_pixels": fit["rms_residual_pixels"],
            "fit_scale": fit["scale"], "name_evidence": evidence,
            "fit_method": fit.get("fit_method", "all_printed_ticks"),
            "rejected_ticks": fit.get("rejected_ticks", [])}
    result.update(status="ready", chart_type="xy", plot_bbox=box,
                  calibration=validate_calibration(calibration), reasons=[])
    result["axis_metadata_warnings"] = []
    for axis in ("x", "y"):
        rejected = result['axis_evidence'][axis].get('rejected_ticks', [])
        if rejected:
            result['axis_metadata_warnings'].append(
                f"{axis.upper()}轴已由 {len(result['ticks'][axis])} 个实际识别的刻度交叉标定；"
                f"另有 {len(rejected)} 个不一致的 OCR 数字未采用，原始识别记录已保留，未补写刻度。")
        if not calibration[axis]["name"] or not calibration[axis]["unit"]:
            result["axis_metadata_warnings"].append(f"{axis.upper()}轴名称或单位没有可靠识别，须核对原图。")
        if calibration[axis]["unit"] in ("OC", "0C", "O C"):
            result["axis_metadata_warnings"].append(f"{axis.upper()}轴单位 OCR 可能把度数符号识别为字母，须核对原图。")
        if (result['axis_evidence'][axis].get('name_evidence') or {}).get('unit_normalized_from'):
            result['axis_metadata_warnings'].append(f"{axis.upper()}轴温度单位中的 o/O/0 已按度数符号规范为 °C；原始文字已保存供核对。")
    return result


def auto_calibrate(session, image=None, extra_text_lines=None, use_local_ocr=True):
    """Public session API; OCR observations may supplement PDF visible text."""
    if image is None:
        with Image.open(session["image_path"]) as opened:
            image = opened.convert("RGB").copy()
    try:
        lines, flags = visible_pdf_text(session, image.size)
    except Exception as exc:
        lines, flags = [], ["PDF文字读取失败："+str(exc)]
    context = session.get("source_metadata", {}).get("figure_context", {})
    caption = context.get("caption", "")
    result = calibrate_from_text(image, lines+list(extra_text_lines or []), caption)
    if "源 PDF 已改变，自动识别已停止。" in flags:
        result.update(status="needs_review", calibration=None, plot_bbox=None,
                      reasons=["源 PDF 已改变，不能沿用旧图关联自动读取。"],
                      reason_codes=["source_pdf_identity_changed"])
        result["text_source_flags"] = flags
        return result
    if result["status"] == "needs_review" and extra_text_lines is None and use_local_ocr:
        try:
            from .native_ocr import recognize_image
        except ImportError:
            from native_ocr import recognize_image
        try:
            observations = recognize_image(session["image_path"], include_rotated=True)
            result = calibrate_from_text(image, lines+observations.get("lines", []), caption)
            result["ocr_language"] = observations.get("language")
            result["ocr_warnings"] = observations.get("warnings", [])
        except Exception as exc:
            result["reasons"].append("本地 OCR 没有完成："+str(exc))
    result["text_source_flags"] = flags
    result["text_sources"] = sorted(set(l.get("source", "ocr") for l in result["visible_text_lines"]))
    return result


# Descriptive alias for integrations that already use an inspect naming style.
recognize_axes = auto_calibrate
