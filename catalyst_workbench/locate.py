"""Conservative local PDF figure candidates; no OCR or scientific digitisation.

Graphics geometry includes vector paths, image objects and top-level form objects.
Caption proximity is a heuristic association, never a claim of exact segmentation.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
from pathlib import Path
import re
import threading

from PIL import Image
import pypdfium2 as pdfium
from pypdf import PdfReader
from pypdf.generic import ContentStream

try:
    # Share PDFium's process-wide lock with the existing digitiser when imported.
    from digitizer.sources import PDFIUM_LOCK
except ImportError:
    try:
        from sources import PDFIUM_LOCK
    except ImportError:
        PDFIUM_LOCK = threading.RLock()

RENDER_MAX_DIMENSION = 2200
MAX_RENDER_PIXELS = 8_000_000
MAX_TEXT_CHARS = 60_000
MAX_GRAPHICS_OBJECTS = 12_000
CAPTION_RE = re.compile(r"^(?P<label>(?:Fig(?:ure)?\.?\s*[A-Za-z]?\d+[A-Za-z]?|图\s*\d+))(?=\b|\s|[.:(：])", re.I)
REFERENCE_VERBS_RE = re.compile(r"^\s*(?:shows?|presents?|illustrates?|demonstrates?|depicts?|indicates?|compares?|reveals?|displays?|summari[sz]es?|provides?|is\b|are\b|was\b|were\b|can\b|has\b|have\b|also\b|above\b|below\b)",re.I)


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def _area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _clamp(box, width, height):
    return [max(0, min(width, int(math.floor(box[0])))), max(0, min(height, int(math.floor(box[1])))),
            max(0, min(width, int(math.ceil(box[2])))), max(0, min(height, int(math.ceil(box[3]))))]


def _device_box(page, box, width, height):
    """PDF page coordinates to the rendered page, respecting rotation/cropbox."""
    points = []
    left, bottom, right, top = box
    for x, y in ((left, bottom), (left, top), (right, bottom), (right, top)):
        dx, dy = ctypes.c_int(), ctypes.c_int()
        ok = pdfium.raw.FPDF_PageToDevice(page, 0, 0, width, height, 0, float(x), float(y), ctypes.byref(dx), ctypes.byref(dy))
        if not ok:
            raise ValueError("无法转换 PDF 页面坐标。")
        points.append((dx.value, dy.value))
    return _clamp([min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)], width, height)


def _matrix_product(outer,inner):
    a,b,c,d,e,f=outer;g,h,i,j,k,l=inner
    return (a*g+c*h,b*g+d*h,a*i+c*j,b*i+d*j,a*k+c*l+e,b*k+d*l+f)


def _transform_box(box,matrix):
    a,b,c,d,e,f=matrix
    points=[(a*x+c*y+e,b*x+d*y+f) for x,y in ((box[0],box[1]),(box[0],box[3]),(box[2],box[1]),(box[2],box[3]))]
    return [min(x for x,y in points),min(y for x,y in points),max(x for x,y in points),max(y for x,y in points)]


def _intersection(a,b):
    box=[max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])]
    return box if box[2]>box[0] and box[3]>box[1] else None


def _object_rectangle_clip(obj, parent_matrix, inherited_clip):
    """Intersect rectangular clip paths attached to an individual PDF object.

    The enclosing Form BBox does not include clipping operators executed inside
    that Form. PDFium exposes those per-object clip paths in the parent space.
    Nonrectangular paths are intentionally left conservative, not treated as
    exact rectangular cuts (which could discard visible plot content).
    """
    if inherited_clip is None:
        return None
    result = list(inherited_clip)
    clip_path = pdfium.raw.FPDFPageObj_GetClipPath(obj.raw)
    if not clip_path:
        return result
    for path_index in range(pdfium.raw.FPDFClipPath_CountPaths(clip_path)):
        points = []
        rectangular = True
        for segment_index in range(pdfium.raw.FPDFClipPath_CountPathSegments(clip_path, path_index)):
            segment = pdfium.raw.FPDFClipPath_GetPathSegment(clip_path, path_index, segment_index)
            if pdfium.raw.FPDFPathSegment_GetType(segment) == pdfium.raw.FPDF_SEGMENT_BEZIERTO:
                rectangular = False
                break
            x, y = ctypes.c_float(), ctypes.c_float()
            if not pdfium.raw.FPDFPathSegment_GetPoint(segment, ctypes.byref(x), ctypes.byref(y)):
                rectangular = False
                break
            points.append((round(x.value, 4), round(y.value, 4)))
        xs, ys = {p[0] for p in points}, {p[1] for p in points}
        if rectangular and len(xs) == 2 and len(ys) == 2 and len(set(points)) == 4:
            rect = _transform_box([min(xs), min(ys), max(xs), max(ys)], parent_matrix)
            result = _intersection(result, rect)
            if result is None:
                return None
    return result


def _pdf_form_tree(pdf_page,reader):
    """Resolve visible Form BBoxes, including every parent matrix and clip.

    PDFium object bounds and text extraction include content outside a Form's
    BBox. Publisher PDFs often embed an old whole page inside a figure-sized
    Form BBox; these discarded paragraphs must not become figure boundaries.
    """
    identity=(1.,0.,0.,1.,0.,0.)
    root_clip=list(map(float,pdf_page.cropbox))
    records={}
    flags=[]
    def walk(container,matrix,clip,prefix,depth):
        if depth>12:
            flags.append('form_depth_limit_reached');return
        resources=container.get('/Resources',{})
        if hasattr(resources,'get_object'):resources=resources.get_object()
        xobjects=resources.get('/XObject',{})
        if hasattr(xobjects,'get_object'):xobjects=xobjects.get_object()
        stream=container.get_contents() if hasattr(container,'get_contents') else container
        if stream is None:return
        content=ContentStream(stream,reader)
        current=matrix;current_clip=clip;stack=[];rect=None;pending_clip=False;ordinal=0
        for operands,operator in content.operations:
            if operator==b'q':stack.append((current,current_clip))
            elif operator==b'Q':
                if stack:current,current_clip=stack.pop()
            elif operator==b'cm' and len(operands)==6:
                current=_matrix_product(current,tuple(map(float,operands)))
            elif operator==b're' and len(operands)==4:
                x,y,w,h=map(float,operands);rect=_transform_box([x,y,x+w,y+h],current)
            elif operator in (b'W',b'W*'):pending_clip=True
            elif operator in (b'n',b'S',b's',b'f',b'f*',b'B',b'B*'):
                if pending_clip and rect is not None:
                    current_clip=_intersection(current_clip,rect) if current_clip else None
                rect=None;pending_clip=False
            elif operator==b'Do' and operands:
                reference=xobjects.get(operands[0])
                if reference is None:continue
                obj=reference.get_object()
                if obj.get('/Subtype')!='/Form':continue
                key=prefix+(ordinal,);ordinal+=1
                child_matrix=_matrix_product(current,tuple(map(float,obj.get('/Matrix',identity))))
                bbox=obj.get('/BBox')
                child_clip=_intersection(current_clip,_transform_box(list(map(float,bbox)),child_matrix)) if bbox is not None and current_clip else None
                records[key]={'matrix':child_matrix,'clip':child_clip,'bbox_source':'PDF_Form_BBox_with_parent_clip'}
                if child_clip:walk(obj,child_matrix,child_clip,key,depth+1)
    walk(pdf_page,identity,root_clip,(),0)
    return records,root_clip,flags


def _page_geometry(page,pdf_page,reader,width,height,scale):
    records,root_clip,flags=_pdf_form_tree(pdf_page,reader)
    objects=[];whole=[];text_clips={};visited=0
    def visit(form,prefix,parent_matrix,clip,collect):
        nonlocal visited
        ordinal=0
        for obj in page.get_objects(max_depth=1,form=form):
            visited+=1
            if visited>MAX_GRAPHICS_OBJECTS:
                flags.append('graphics_object_limit_reached');return
            address=ctypes.cast(obj.raw,ctypes.c_void_p).value
            object_clip = _object_rectangle_clip(obj, parent_matrix, clip)
            if obj.type==pdfium.raw.FPDF_PAGEOBJ_TEXT:
                text_clips[address]=_device_box(page,object_clip,width,height) if object_clip else None
                continue
            if obj.type==pdfium.raw.FPDF_PAGEOBJ_FORM:
                key=prefix+(ordinal,);ordinal+=1
                record=records.get(key)
                if record is None:
                    flags.append('form_clip_mapping_unavailable')
                    continue
                child_clip=_intersection(record['clip'], object_clip) if record['clip'] and object_clip else None
                if child_clip is None:
                    visit(obj,key,record['matrix'],None,False)
                    continue
                box=_device_box(page,child_clip,width,height)
                w,h=box[2]-box[0],box[3]-box[1]
                bounded=_area(box)<width*height*0.57 and w>30*scale and h>25*scale
                if collect and bounded:
                    objects.append({'bbox':box,'kind':'form','bounds_basis':'form_bbox_clipped','form_path':list(key)})
                elif collect:
                    whole.append({'bbox':box,'kind':'form'})
                visit(obj,key,record['matrix'],child_clip,collect and not bounded)
                continue
            if not collect or object_clip is None or obj.type not in (pdfium.raw.FPDF_PAGEOBJ_PATH,pdfium.raw.FPDF_PAGEOBJ_IMAGE):continue
            try:
                world=_transform_box(obj.get_bounds(),parent_matrix)
                visible=_intersection(world,object_clip)
                if visible is None:continue
                box=_device_box(page,visible,width,height)
            except Exception:
                flags.append('unreadable_graphics_bounds');continue
            kind='image' if obj.type==pdfium.raw.FPDF_PAGEOBJ_IMAGE else 'vector_path'
            w,h=box[2]-box[0],box[3]-box[1]
            if w>=width*.92 and h>=height*.92:
                if kind=='image':whole.append({'bbox':box,'kind':kind})
                continue
            if kind=='vector_path' and ((w<2*scale and h<2*scale) or (w>width*.8 and h<2*scale) or (h>height*.85 and w<2*scale)):continue
            objects.append({'bbox':box,'kind':kind,'bounds_basis':'parent_matrix_and_clip'})
    visit(None,(),(1.,0.,0.,1.,0.,0.),root_clip,True)
    return objects,whole,list(dict.fromkeys(flags)),text_clips


def _safe_pdf_characters(codepoints):
    """Return Unicode scalar strings with PDFium indices preserved.

    Some PDFs expose a UTF-16 surrogate pair as two character entries. A valid
    pair becomes one scalar at the first index and an empty continuation slot.
    Isolated surrogates, NULL/unmapped entries and invalid codepoints become the
    replacement character, before strings reach Tk, matching or UTF-8 output.
    """
    characters = [""] * len(codepoints)
    spans = [1] * len(codepoints)
    flags = []
    index = 0
    while index < len(codepoints):
        value = codepoints[index]
        if isinstance(value,int) and 0xD800 <= value <= 0xDBFF and index+1 < len(codepoints):
            following = codepoints[index+1]
            if isinstance(following,int) and 0xDC00 <= following <= 0xDFFF:
                characters[index] = chr(0x10000+((value-0xD800)<<10)+(following-0xDC00))
                spans[index] = 2
                spans[index+1] = 0
                flags.append("text_utf16_surrogate_pair_normalized")
                index += 2
                continue
        if not isinstance(value,int) or value <= 0 or value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
            characters[index] = "\ufffd"
            flags.append("text_unicode_unmapped_replaced")
        else:
            characters[index] = chr(value)
        index += 1
    return characters, spans, list(dict.fromkeys(flags))


def _text_lines(page, width, height, scale, text_clips=None):
    """Build spatial lines from visible glyph bounds, avoiding column joins."""
    textpage = page.get_textpage()
    flags = []
    glyphs = []
    hidden_count=0
    try:
        count = textpage.count_chars()
        if count > MAX_TEXT_CHARS:
            flags.append("text_char_limit_reached")
        codepoints = [pdfium.raw.FPDFText_GetUnicode(textpage,index) for index in range(min(count,MAX_TEXT_CHARS))]
        characters,spans,unicode_flags = _safe_pdf_characters(codepoints)
        flags.extend(unicode_flags)
        for index,char in enumerate(characters):
            if not char:
                continue
            value = ord(char)
            if not value or value in (10, 13):
                continue
            try:
                if char.isspace():
                    continue
                boxes = [_device_box(page,textpage.get_charbox(index+offset),width,height) for offset in range(spans[index])]
                box = _union([b for b in boxes if b[2]>b[0] and b[3]>b[1]] or boxes)
                if text_clips is not None:
                    raw_obj=pdfium.raw.FPDFText_GetTextObject(textpage,index)
                    address=ctypes.cast(raw_obj,ctypes.c_void_p).value if raw_obj else None
                    if address in text_clips:
                        clip=text_clips[address]
                        overlap=_intersection(box,clip) if clip else None
                        if overlap is None or _area(overlap)<_area(box)*0.45:
                            hidden_count+=1;continue
                    if raw_obj and pdfium.raw.FPDFTextObj_GetTextRenderMode(raw_obj)==3:continue
            except Exception:
                continue
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            glyphs.append({"char": char, "bbox": box, "index": index})
    finally:
        textpage.close()
    if hidden_count:flags.append('clipped_form_text_filtered')
    if not glyphs:
        return [], flags
    rows = []
    for glyph in sorted(glyphs, key=lambda g: ((g["bbox"][1]+g["bbox"][3])/2, g["bbox"][0])):
        box = glyph["bbox"]
        center = (box[1] + box[3]) / 2
        tolerance = max(4.5 * scale, (box[3] - box[1]) * 0.40)
        eligible = [r for r in rows[-5:] if abs(r["center"] - center) <= tolerance]
        if eligible:
            row = min(eligible, key=lambda r: abs(r["center"] - center))
            row["glyphs"].append(glyph)
            row["center"] = sum((g["bbox"][1]+g["bbox"][3])/2 for g in row["glyphs"]) / len(row["glyphs"])
        else:
            rows.append({"center": center, "glyphs": [glyph]})
    lines = []
    for row in rows:
        ordered = sorted(row["glyphs"], key=lambda g: g["bbox"][0])
        segments, current = [], []
        for glyph in ordered:
            if current and glyph["bbox"][0] - current[-1]["bbox"][2] > max(18 * scale, 1.8 * (glyph["bbox"][3]-glyph["bbox"][1])):
                segments.append(current)
                current = []
            current.append(glyph)
        if current:
            segments.append(current)
        for segment in segments:
            text = ""
            previous = None
            for glyph in segment:
                if previous:
                    gap = glyph["bbox"][0] - previous["bbox"][2]
                    between = characters[previous["index"]+1:glyph["index"]] if glyph["index"] > previous["index"] else []
                    source_space = any(char.isspace() for char in between)
                    if source_space or gap > max(5*scale, (glyph["bbox"][3]-glyph["bbox"][1])*0.7):
                        text += " "
                text += glyph["char"]
                previous = glyph
            lines.append({"text": text.strip(), "bbox": _union([g["bbox"] for g in segment])})
    return sorted(lines, key=lambda line: (line["bbox"][1], line["bbox"][0])), flags


def _captions(lines, width, scale):
    captions = []
    for index, line in enumerate(lines):
        match = CAPTION_RE.match(line["text"])
        if not match:
            continue
        remainder=line['text'][match.end():].lstrip().lstrip('.:：').lstrip()
        if REFERENCE_VERBS_RE.match(remainder):
            continue
        box = line["bbox"]
        parts, boxes = [line["text"]], [box]
        previous_bottom = box[3]
        column_right = width * 0.51 if box[0] < width * 0.45 and box[2] < width * 0.52 else width
        for following in lines[index+1:]:
            other = following["bbox"]
            if other[1] > previous_bottom + 13 * scale:
                break
            if other[1] < previous_bottom - 3 * scale:
                continue
            if abs(other[0] - box[0]) > 18 * scale or other[2] > column_right + 10 * scale:
                continue
            if CAPTION_RE.match(following["text"]) or len(parts) >= 5:
                break
            parts.append(following["text"])
            boxes.append(other)
            previous_bottom = other[3]
        label=re.sub(r'^(Fig(?:ure)?\.?)\s*([A-Za-z]?\d+)',r'\1 \2',match.group('label'),flags=re.I)
        item={"label":label,"text":" ".join(parts)[:2000],"bbox":_union(boxes)}
        duplicate=next((c for c in captions if c['label']==label and abs(c['bbox'][1]-item['bbox'][1])<6*scale),None)
        if duplicate:
            if len(item['text'])>len(duplicate['text']):duplicate.update(item)
        else:captions.append(item)
    return captions


def _graphics(page, width, height, scale):
    objects, flags = [], []
    raw_types = [pdfium.raw.FPDF_PAGEOBJ_PATH, pdfium.raw.FPDF_PAGEOBJ_IMAGE, pdfium.raw.FPDF_PAGEOBJ_FORM]
    names = {raw_types[0]: "vector_path", raw_types[1]: "image", raw_types[2]: "form"}
    full_page_graphics = []
    for index, obj in enumerate(page.get_objects(filter=raw_types, max_depth=1)):
        if index >= MAX_GRAPHICS_OBJECTS:
            flags.append("graphics_object_limit_reached")
            break
        try:
            box = _device_box(page, obj.get_bounds(), width, height)
        except Exception:
            flags.append("unreadable_graphics_bounds")
            continue
        w, h = box[2]-box[0], box[3]-box[1]
        if w <= 0 and h <= 0:
            continue
        kind = names.get(obj.type, "unknown")
        if w >= width * 0.92 and h >= height * 0.92:
            if kind != "vector_path":
                full_page_graphics.append({"bbox": box, "kind": kind})
            # A filled page background cannot locate a figure.
            continue
        if kind == "vector_path" and (w < 2 * scale and h < 2 * scale):
            continue
        # Ignore page separators that have virtually no height or width.
        if (w > width * 0.8 and h < 2 * scale) or (h > height * 0.85 and w < 2 * scale):
            continue
        objects.append({"bbox": box, "kind": kind})
    return objects, full_page_graphics, list(dict.fromkeys(flags))


def _clusters(objects, width, height, scale):
    """Merge touching/near geometry; text is added later as bounded padding."""
    clusters = []
    margin = 7 * scale
    for item in sorted(objects, key=lambda obj: _area(obj["bbox"]), reverse=True):
        box = list(item["bbox"])
        members = [item]
        merged = True
        while merged:
            merged = False
            for group in list(clusters):
                other = group["bbox"]
                if box[0] <= other[2]+margin and box[2] >= other[0]-margin and box[1] <= other[3]+margin and box[3] >= other[1]-margin:
                    box = _union([box, other])
                    members.extend(group["objects"])
                    clusters.remove(group)
                    merged = True
        clusters.append({"bbox": box, "objects": members})
    return [g for g in clusters if (g["bbox"][2]-g["bbox"][0] >= 30*scale and
            g["bbox"][3]-g["bbox"][1] >= 25*scale and _area(g["bbox"]) >= width*height*0.0015)]


def _chart_hint(caption):
    text = caption.lower()
    numeric = bool(re.search(r"conversion|selectivity|yield|performance|activity|uptake|isotherm|curve|scatter|temperature|spectr|\bxrd\b|\btpd\b|\btpr\b|转化率|选择性|曲线|谱图", text))
    non_numeric = bool(re.search(r"\b(?:sem|tem|hrtem|microscopy|micrograph|photograph|scheme|mechanism)\b|显微|机理|示意图", text))
    if numeric and non_numeric:
        return "unknown", ["mixed_panel_types_possible"]
    if non_numeric:
        return "non_numeric", []
    if re.search(r'horizontal\s+bar|横向条形|横条图',text):
        return 'bar_horizontal',[]
    if re.search(r"\bbar\s+(?:chart|graph|plot)|\bcolumn chart\b|柱状", text):
        return "bar", []
    if numeric:
        return "xy", []
    return "unknown", []


def _candidate_boxes(groups, captions, width, height, scale, lines=None):
    figures, used = [], set()
    for caption in captions:
        cb = caption["bbox"]
        options = []
        for index, group in enumerate(groups):
            if index in used:
                continue
            gb = group["bbox"]
            overlap = max(0, min(gb[2], cb[2])-max(gb[0], cb[0]))
            overlap_ratio = overlap / max(1, min(gb[2]-gb[0], cb[2]-cb[0]))
            gap = cb[1] - gb[3]
            if overlap_ratio >= 0.30 and -3*scale <= gap <= 125*scale:
                score = max(0, gap) + abs((gb[0]+gb[2])-(cb[0]+cb[2]))*0.04
                options.append((score, index, "caption_below_graphics", gap))
        if not options:
            # Above-figure captions have a much tighter window.
            for index, group in enumerate(groups):
                if index in used:
                    continue
                gb = group["bbox"]
                overlap = max(0, min(gb[2], cb[2])-max(gb[0], cb[0]))
                gap = gb[1] - cb[3]
                if overlap / max(1, min(gb[2]-gb[0], cb[2]-cb[0])) >= 0.40 and 0 <= gap <= 45*scale:
                    options.append((gap, index, "caption_above_graphics", gap))
        if not options:
            figures.append({"caption": caption, "bbox": [0,0,width,height], "graphic_bbox": None,
                             "locator_status": "page_fallback", "association": "caption_without_locatable_graphics",
                             "flags": ["figure_boundary_not_found", "full_page_manual_location_required"]})
            continue
        options.sort()
        _, index, association, gap = options[0]
        selected = [index]
        group = groups[index]
        gb = group["bbox"]
        # A single caption may describe several side-by-side panels. Retain the
        # group rather than claiming that one panel is the complete figure.
        multi_panel_caption = len(re.findall(r"\([a-z]\)", caption["text"], re.I)) >= 2
        if association == "caption_below_graphics" and (multi_panel_caption or cb[2]-cb[0] > width*0.62):
            for _,other_index,other_association,other_gap in options[1:]:
                other = groups[other_index]["bbox"]
                vertical_overlap = max(0,min(gb[3],other[3])-max(gb[1],other[1]))
                if other_association == association and abs(other_gap-gap) < 35*scale and vertical_overlap > min(gb[3]-gb[1],other[3]-other[1])*0.5:
                    selected.append(other_index)
            if multi_panel_caption:
                for other_index,other_group in enumerate(groups):
                    if other_index in used or other_index in selected:
                        continue
                    other = other_group["bbox"]
                    vertical_overlap = max(0,min(gb[3],other[3])-max(gb[1],other[1]))
                    same_row = abs(gb[3]-other[3]) < 25*scale and vertical_overlap > min(gb[3]-gb[1],other[3]-other[1])*0.6
                    competing_caption = any(c is not caption and abs(c["bbox"][1]-cb[1]) < 50*scale and
                                            min(c["bbox"][2],other[2])-max(c["bbox"][0],other[0]) > 20*scale for c in captions)
                    if same_row and not competing_caption:
                        selected.append(other_index)
            if len(selected) > 1:
                group = {"bbox":_union([groups[i]["bbox"] for i in selected]),"objects":[o for i in selected for o in groups[i]["objects"]]}
                gb = group["bbox"]
        used.update(selected)
        # Padding keeps tick labels/axis titles with paths. Caption below clamps
        # the lower edge so that the candidate need not absorb body text.
        padded = [gb[0]-27*scale, gb[1]-14*scale, gb[2]+17*scale, gb[3]+28*scale]
        if association == "caption_below_graphics":
            padded[3] = min(padded[3], cb[1]-2*scale)
        else:
            padded[1] = max(padded[1], cb[3]+2*scale)
        body_boundary=None
        for line in lines or []:
            lb=line['bbox'];words=re.findall(r'\b[A-Za-z]{2,}\b',line['text'])
            # Only substantial paragraph lines OUTSIDE the graphical region
            # define a stop boundary. Legends, ticks and axis titles inside the
            # graph must never cut off the plot merely because they are text.
            paragraph=len(words)>=9 and len(line['text'])>=55 and lb[2]-lb[0]>width*.32
            overlaps=min(lb[2],gb[2])-max(lb[0],gb[0])>min(lb[2]-lb[0],gb[2]-gb[0])*.25
            if paragraph and overlaps and lb[3]<=gb[1] and lb[3]>=padded[1]-5*scale:
                padded[1]=max(padded[1],lb[3]+3*scale)
                body_boundary=lb
        flags = ["heuristic_bbox_requires_review", "caption_association_requires_review", "subfigure_not_separated"]
        if len(selected) > 1:
            flags.append("multiple_graphics_grouped_under_one_caption")
        if body_boundary:flags.append('top_boundary_stopped_at_body_paragraph')
        if len(options) > 1 and options[1][0]-options[0][0] < 18*scale:
            flags.append("multiple_nearby_graphics_candidates")
        figures.append({"caption": caption, "bbox": _clamp(padded,width,height), "graphic_bbox": gb,
                         "locator_status": "graphics_caption_candidate", "association": association,
                         "body_boundary_bbox":body_boundary,
                         "association_gap_pixels": gap, "graphics_kinds": sorted({x["kind"] for x in group["objects"]}),
                         "graphics_object_count": len(group["objects"]), "flags": flags})
    for index, group in enumerate(groups):
        if index in used:
            continue
        gb = group["bbox"]
        figures.append({"caption": None, "bbox": _clamp([gb[0]-27*scale,gb[1]-14*scale,gb[2]+17*scale,gb[3]+28*scale],width,height),
                         "graphic_bbox": gb, "locator_status": "graphics_without_caption", "association": "no_caption_found",
                         "graphics_kinds": sorted({x["kind"] for x in group["objects"]}), "graphics_object_count":len(group["objects"]),
                         "flags": ["caption_missing", "heuristic_bbox_requires_review", "graphic_may_be_table_or_decoration", "subfigure_not_separated"]})
    return figures


def locate_pdf(pdf_path, output_dir, progress=None, cancel_event=None):
    """Scan all pages and return auditable, unreviewed figure candidates.

    A page fallback is explicitly NOT an accurately located figure. This method
    never reads numerical axes, digitises a curve, or decides ML eligibility.
    """
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError("请选择已获取到本地的 PDF 文件。")
    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    source_hash = _hash_file(path)
    source_reader=None
    result = {"schema_version":"figure-candidates/1.0", "source_path":str(path), "source_sha256":source_hash,
              "pages":[], "figures":[], "warnings":[], "cancelled":False,
              "disclosure":"候选图框、图注关联和图型提示均需人工核对；未执行 OCR、坐标识别或数值提取。"}
    try:
        source_reader=PdfReader(path,strict=False)
        with PDFIUM_LOCK:
            document = pdfium.PdfDocument(str(path))
            try:
                result["page_count"] = len(document)
                for page_index in range(len(document)):
                    if cancel_event is not None and cancel_event.is_set():
                        result["cancelled"] = True
                        result["warnings"].append("用户停止扫描；保留已完成页面和候选。")
                        break
                    page_number = page_index + 1
                    if progress:
                        progress(f"定位候选图：{path.name}，第 {page_number}/{len(document)} 页")
                    page = document[page_index]
                    image = None
                    try:
                        pw, ph = page.get_size()
                        if pw <= 0 or ph <= 0:
                            raise ValueError("页面尺寸无效")
                        scale = min(RENDER_MAX_DIMENSION/max(pw,ph), math.sqrt(MAX_RENDER_PIXELS/(pw*ph)))
                        bitmap = page.render(scale=scale)
                        try:
                            borrowed = bitmap.to_pil()
                            try:
                                image = borrowed.convert("RGB").copy()
                            finally:
                                borrowed.close()
                        finally:
                            bitmap.close()
                        width, height = image.size
                        image_path = target / f"page_{page_number:04d}_{source_hash[:10]}.png"
                        image.save(image_path)
                        try:
                            objects,whole_graphics,graphics_flags,text_clips=_page_geometry(page,source_reader.pages[page_index],source_reader,width,height,scale)
                        except Exception as geometry_error:
                            objects,whole_graphics,graphics_flags=_graphics(page,width,height,scale)
                            graphics_flags.append('form_clip_analysis_failed')
                            text_clips=None
                        try:
                            lines, text_flags = _text_lines(page,width,height,scale,text_clips)
                            text_status = "text_layer" if lines else "no_text_layer"
                        except Exception:
                            lines,text_flags,text_status = [],["text_layer_read_failed"],"failed"
                        captions = _captions(lines,width,scale)
                        groups = _clusters(objects,width,height,scale)
                        candidates = _candidate_boxes(groups,captions,width,height,scale,lines)
                        if not candidates and (whole_graphics or text_status != "text_layer" or graphics_flags):
                            candidates = [{"caption":None,"bbox":[0,0,width,height],"graphic_bbox":None,
                                           "locator_status":"page_fallback","association":"no_reliable_local_figure_boundary",
                                           "flags":["full_page_manual_location_required","no_ocr_performed"]}]
                        page_info = {"page":page_number,"width":width,"height":height,"image_path":str(image_path),
                                     "pdf_width_points":pw,"pdf_height_points":ph,"render_scale":scale,
                                     "text_status":text_status,"caption_count":len(captions),
                                     "graphics_object_count":len(objects),"whole_page_graphics_count":len(whole_graphics),
                                     "locator_status":"candidates" if candidates else "no_candidate",
                                     "quality_flags":text_flags+graphics_flags,
                                     "text_excerpt":"\n".join(x["text"] for x in lines)[:12000]}
                        result["pages"].append(page_info)
                        for candidate_index,candidate in enumerate(candidates,1):
                            caption = candidate["caption"]
                            caption_text = caption["text"] if caption else ""
                            chart_type,hint_flags = _chart_hint(caption_text)
                            figure_id = f"{source_hash[:10]}_p{page_number:04d}_c{candidate_index:03d}"
                            crop_path = target / (figure_id+".png")
                            box = candidate["bbox"]
                            if box[2] <= box[0] or box[3] <= box[1]:
                                continue
                            crop = image.crop(tuple(box))
                            try:
                                crop.save(crop_path)
                            finally:
                                crop.close()
                            flags = candidate["flags"]+text_flags+graphics_flags+hint_flags+["chart_type_hint_requires_review"]
                            if whole_graphics:
                                flags.append("whole_page_image_or_form_present")
                            figure = {"figure_id":figure_id,"page":page_number,"figure_label":caption["label"] if caption else "",
                                      "caption":caption_text,"caption_bbox":caption["bbox"] if caption else None,
                                      "bbox":box,"bbox_coordinate_system":"rendered_page_pixels",
                                      "page_width":width,"page_height":height,"page_image_path":str(image_path),"crop_path":str(crop_path),
                                      "chart_type":chart_type,"chart_type_basis":"caption_keywords_only" if caption else "no_caption_unknown",
                                      "quality_flags":list(dict.fromkeys(flags)),"review_status":"unreviewed",
                                      "locator_status":candidate["locator_status"],"bbox_method":"visible_graphics_form_clip_caption_body_boundary_v2" if candidate["graphic_bbox"] else "full_page_fallback",
                                      "body_boundary_bbox":candidate.get('body_boundary_bbox'),
                                      "bbox_precision":"candidate_requires_review" if candidate["graphic_bbox"] else "not_located",
                                      "graphic_bbox":candidate["graphic_bbox"],"caption_association":candidate["association"],
                                      "graphics_kinds":candidate.get("graphics_kinds",[]),"source_sha256":source_hash,
                                      "source_path":str(path),"render_scale":scale,"is_numeric_data_extracted":False}
                            result["figures"].append(figure)
                    except Exception as exc:
                        result["warnings"].append(f"第 {page_number} 页读取未完成：{exc}")
                        result["pages"].append({"page":page_number,"locator_status":"failed","text_status":"unknown","error":str(exc)})
                    finally:
                        if image is not None:
                            image.close()
                        page.close()
            finally:
                document.close()
    except Exception as exc:
        raise ValueError(f"无法打开或扫描 PDF，请检查密码、文件完整性和权限：{exc}") from exc
    finally:
        if source_reader is not None:
            source_reader.close()
    result["warnings"].append("自动结果是候选图位置与图注关联，不代表精准分割、完整检出或数值提取；请逐页核对漏检。")
    (target/"figure_candidates.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result
