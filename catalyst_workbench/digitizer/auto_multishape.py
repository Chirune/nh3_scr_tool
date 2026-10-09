"""Read monochrome multi-series markers from their visible legend templates.

This fallback is for three/four distinct filled shapes, including triangles,
with a vertically aligned legend inside or just outside the plot. It does not
infer curve identity from proximity, interpolation or catalyst names.
"""
from __future__ import annotations

import re
import numpy as np
from PIL import Image

try:
    from .auto_curves import _components, _erode, _text_rows
except ImportError:
    from auto_curves import _components, _erode, _text_rows


SHAPE_NAMES = {"square": "方块", "circle": "圆点", "triangle_up": "上三角", "triangle_down": "下三角"}


def _shape(core_mask):
    widths = core_mask.sum(axis=1)
    widths = widths[widths > 0]
    if len(widths) < 3:
        return None
    third = max(1, len(widths)//3)
    top, bottom = float(np.mean(widths[:third])), float(np.mean(widths[-third:]))
    if bottom > top*1.8:
        return "triangle_up"
    if top > bottom*1.8:
        return "triangle_down"
    fill = core_mask.mean()
    if fill >= .84:
        return "square"
    if .53 <= fill <= .80:
        return "circle"
    return None


def find_multishape_legend(image, plots, text_lines):
    gray = np.asarray(image.convert("L"))
    rgb = np.asarray(image.convert("RGB")).astype(np.int16)
    ink = (gray < 185) & (rgb.max(axis=2)-rgb.min(axis=2) < 30)
    stroke = (gray < 245) & (rgb.max(axis=2)-rgb.min(axis=2) < 30)
    eroded = _erode(ink, 1)
    cores = _components(eroded, 4)
    candidates = []
    for plot in plots:
        l, t, r, b = plot
        width, height = r-l, b-t
        for row in _text_rows(text_lines):
            x0, y0, x1, y1 = row['bbox']
            cy = (y0+y1)/2
            if not (l < x0 < x1 <= image.width and max(0, t-height*.50) < y0 < y1 < b):
                continue
            if not re.search(r"[A-Za-z]{2}", row['text']):
                continue
            options = []
            for core in cores:
                a, z, c, d = core['bbox']
                ww, hh = c-a, d-z
                cx, my = (a+c-1)/2, (z+d-1)/2
                if not (3 <= min(ww, hh) and max(ww, hh) <= width*.055 and .6 <= ww/hh <= 1.7):
                    continue
                if not (8 < x0-cx < width*.16 and abs(my-cy) < max(5, (y1-y0)*.55)):
                    continue
                shape = _shape(eroded[z:d, a:c])
                if not shape:
                    continue
                # A real horizontal legend stroke must extend on both sides
                # of the compact symbol. Text glyphs do not satisfy this.
                left, right = max(0, a-18), min(image.width, c+18, int(x0)-2)
                row_scores = []
                for yy in range(max(0, int(cy)-4), min(image.height, int(cy)+5)):
                    ls, rs = stroke[yy, left:a-2].sum(), stroke[yy, c+2:right].sum()
                    if min(ls, rs) >= 5:
                        row_scores.append((int(ls+rs), yy))
                if not row_scores:
                    continue
                anchor_y = float(max(row_scores)[1])
                # Keep a white rim and a fixed visible swatch anchor. The
                # triangle centroid alone would shift the reported Y value.
                margin = 3
                bb = [a-margin, z-margin, c+margin, d+margin]
                if bb[0] < 0 or bb[1] < 0 or bb[2] > image.width or bb[3] > image.height:
                    continue
                patch = ink[bb[1]:bb[3], bb[0]:bb[2]]
                candidates.append({"label": row['text'], "label_bbox": row['bbox'],
                    "shape": shape, "symbol_bbox": bb, "template": patch.astype(int).tolist(),
                    "anchor": [cx-bb[0], anchor_y-bb[1]], "center": [cx, anchor_y],
                    "marker_size": max(ww, hh)+2,
                    "ink_gray": float(np.median(gray[z:d,a:c][eroded[z:d,a:c]])),
                    "plot_bbox": list(plot)})
    for first in candidates:
        group = [v for v in candidates if v['plot_bbox'] == first['plot_bbox'] and
                 abs(v['center'][0]-first['center'][0]) < 7 and
                 abs(v['label_bbox'][0]-first['label_bbox'][0]) < 35]
        group.sort(key=lambda v: v['center'][1])
        if not (3 <= len(group) <= 4) or len({v['shape'] for v in group}) != len(group):
            continue
        gaps = np.diff([v['center'][1] for v in group])
        if min(gaps) < 9 or max(gaps) > min(gaps)*1.7:
            continue
        return {'entries': group, 'basis': 'visible_multishape_legend_templates',
                'legend_bbox': [min(v['symbol_bbox'][0] for v in group)-20,
                    min(v['symbol_bbox'][1] for v in group)-7,
                    max(v['label_bbox'][2] for v in group)+7,
                    max(v['symbol_bbox'][3] for v in group)+7]}
    return None


def extract_multishape_series(image, plot, legend, text_lines=None):
    """Match real marker patches; unresolved overlaps are excluded and logged."""
    gray = np.asarray(image.convert('L'))
    rgb = np.asarray(image.convert('RGB')).astype(np.int16)
    ink = (gray < 185) & (rgb.max(axis=2)-rgb.min(axis=2) < 30)
    l, t, r, b = map(lambda v: int(round(v)), plot)
    roi = np.zeros_like(ink)
    roi[t+3:b-3, l+3:r-3] = True
    exclusions = [legend['legend_bbox']]
    for row in _text_rows(text_lines or []):
        a, z, c, d = row['bbox']
        if l < a < c < r and t < z < d < b:
            exclusions.append([a-2, z-2, c+2, d+2])
    for a, z, c, d in exclusions:
        roi[max(0,int(z)):max(0,min(image.height,int(d)+1)),
            max(0,int(a)):max(0,min(image.width,int(c)+1))] = False
    eroded = _erode(ink & roi, 1)
    cores = _components(eroded, 4)
    size = float(np.median([e['marker_size'] for e in legend['entries']]))
    templates = []
    for entry in legend['entries']:
        original = np.array(entry['template'], dtype=bool)
        h, w = original.shape
        for scale in (.78, .86, .94, 1.0, 1.08, 1.16):
            tw, th = round(w*scale), round(h*scale)
            resized = Image.fromarray(original.astype('uint8')*255).resize((tw,th),Image.Resampling.NEAREST)
            template = np.asarray(resized)>0
            anchor = [(entry['anchor'][0]+.5)*tw/w-.5,(entry['anchor'][1]+.5)*th/h-.5]
            variant = dict(entry,anchor=anchor)
            valid = np.ones_like(template)
            ay = round(anchor[1])
            rim = max(2,round(3*scale))
            valid[max(0,ay-1):ay+2, :rim] = False
            valid[max(0,ay-1):ay+2, -rim:] = False
            templates.append((variant, template & valid, (~template) & valid))
    candidates, unresolved = [], []
    for core in cores:
        a, z, c, d = core['bbox']
        ww, hh = c-a, d-z
        if min(ww,hh) < size*.25 or max(ww,hh) > size*4.5:
            continue
        core_shape = _shape(eroded[z:d,a:c])
        if not core_shape or max(ww,hh)>size*1.35:
            unresolved.append(core['bbox'])
            continue
        hits = []
        core_gray = float(np.median(gray[z:d,a:c][eroded[z:d,a:c]]))
        for entry, foreground, background in templates:
            if entry['shape'] != core_shape or abs(core_gray-entry['ink_gray']) > 32:
                continue
            th, tw = foreground.shape
            if not foreground.any() or not background.any():
                continue
            for yy in range(max(t+3, z-2), min(b-3, d+2)):
                for xx in range(max(l+3, a-2), min(r-3, c+2)):
                    x0, y0 = round(xx-entry['anchor'][0]), round(yy-entry['anchor'][1])
                    if x0 < 0 or y0 < 0 or x0+tw > image.width or y0+th > image.height:
                        continue
                    patch = ink[y0:y0+th,x0:x0+tw]
                    missing = 1-float(patch[foreground].mean())
                    extra = float(patch[background].mean())
                    score = missing+.65*extra
                    if missing <= .20 and extra <= .25 and score <= .23:
                        hits.append({'px': x0+entry['anchor'][0], 'py': y0+entry['anchor'][1],
                            'shape':entry['shape'],'score':score,'marker_bbox':[x0,y0,x0+tw,y0+th]})
        chosen = []
        for hit in sorted(hits,key=lambda p:p['score']):
            # A small square can superficially fit a larger circle template.
            # Require its independent filled-core outline to agree as well.
            if hit['shape'] != core_shape:
                continue
            if any(np.hypot(hit['px']-q['px'],hit['py']-q['py']) < size*.65 for q in chosen):
                continue
            hit['outline_basis'] = 'eroded_component_shape_and_legend_gray'
            hit['ink_gray'] = core_gray
            chosen.append(hit)
        if not chosen or max(ww,hh) > size*1.5:
            unresolved.append(core['bbox'])
        candidates.extend(chosen)
    series=[]
    for entry in legend['entries']:
        points=[]
        for point in sorted((v for v in candidates if v['shape']==entry['shape']), key=lambda v:(v['px'],v['py'])):
            if any(np.hypot(point['px']-q['px'],point['py']-q['py']) < size*.65 for q in points):
                continue
            points.append(dict(point,method='automatic_legend_shape_template',review_status='unreviewed'))
        if len(points) >= 3:
            series.append({'label':SHAPE_NAMES[entry['shape']]+' · '+entry['label'],
                'label_raw':entry['label'], 'points_px':points, 'color':[50,50,50],
                'marker_style':entry['shape'], 'marker_style_name':SHAPE_NAMES[entry['shape']],
                'method':'automatic_legend_shape_template', 'label_requires_review':True,
                'label_basis':'visible_legend_text_and_'+entry['shape']+'_symbol',
                'legend_text_bbox':entry['label_bbox'], 'sample_count':len(points),
                'value_origin':'image_digitized_approximate'})
    missing=[e['label'] for e in legend['entries'] if e['shape'] not in {s['marker_style'] for s in series}]
    skipped=[]
    if missing:skipped.append({'reason':'unresolved_symbol_series','labels':missing})
    if unresolved:skipped.append({'reason':'unresolved_marker_overlap_or_shape','region_count':len(unresolved)})
    return {'series':series, 'method':'visible_multishape_legend_templates','legend_bbox':legend['legend_bbox'],
        'legend_evidence':legend, 'skipped_series':skipped, 'unresolved_regions':unresolved,
        'warnings':['按照可见图例的方块、圆点、上三角、下三角定位；不按曲线位置猜测系列，不补齐重叠点。',
                    '图例文字来自本机 OCR，催化剂缩写和字母可能识别错误；请依据点形和原图核对系列名称。',
                    f'有 {len(unresolved)} 处候选标记区域未完整分离；输出为可分离点的近似读数。']}
