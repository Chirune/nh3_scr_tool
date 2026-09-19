"""Recover discrete vector markers, never sample a fitted connecting line.

The profile (axes, region, legend and series selectors) needs visual validation.
Coordinates are PDF points with origin at the top left, not rendered pixels.
"""
import hashlib
import io
import json
import math
from pathlib import Path
from .core import make_record, uid, write_json, write_csv, FIELDS


def axis_value(point, axis):
    from .cli import calibrate
    if axis.get('segments'):
        for segment in axis['segments']:
            lo = min(segment['pixel1'], segment['pixel2'])
            hi = max(segment['pixel1'], segment['pixel2'])
            if lo <= point <= hi:
                return calibrate(point, segment['pixel1'], segment['pixel2'],
                                 segment['value1'], segment['value2'],
                                 segment.get('scale', axis.get('scale', 'linear')))
        raise ValueError(f'Point {point} falls in a broken-axis gap')
    return calibrate(point, axis['pixel1'], axis['pixel2'], axis['value1'], axis['value2'], axis.get('scale', 'linear'))


def inside(x, y, box):
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def marker_centers(page, profile, selector):
    objects = getattr(page, selector.get('object', 'curves'))
    centers = []
    for obj in objects:
        x, y = (obj['x0'] + obj['x1']) / 2, (obj['top'] + obj['bottom']) / 2
        exclusions = [*profile.get('exclude', []), *selector.get('exclude', [])]
        if not inside(x, y, selector.get('region', profile['region'])) or any(inside(x, y, box) for box in exclusions):
            continue
        color = obj.get(selector.get('color_key', 'non_stroking_color'))
        expected_color = selector['color']
        color = list(color) if isinstance(color, (tuple, list)) else [color]
        expected_color = list(expected_color) if isinstance(expected_color, (tuple, list)) else [expected_color]
        if color[0] is None or len(color) != len(expected_color):
            continue
        if max(abs(a-b) for a, b in zip(color, expected_color)) > selector.get('color_tolerance', 0.01):
            continue
        if not selector['width'][0] <= obj['width'] <= selector['width'][1] or not selector['height'][0] <= obj['height'] <= selector['height'][1]:
            continue
        if selector.get('points') is not None and len(obj.get('pts', [])) != selector['points']:
            continue
        if any(math.hypot(x-c['x'], y-c['y']) < 0.03 for c in centers):
            continue
        centers.append(dict(x=x, y=y, bbox=[obj['x0'], obj['top'], obj['x1'], obj['bottom']]))
    centers.sort(key=lambda c: c['x'])
    if selector.get('expected_count') is not None and len(centers) != selector['expected_count']:
        raise ValueError(f"{selector['catalyst']}: expected {selector['expected_count']} markers, got {len(centers)}; inspect selector")
    return centers


def extract_vectors(pdf_path, profile, out):
    import pdfplumber
    from PIL import ImageDraw
    import pypdfium2 as pdfium
    path, out = Path(pdf_path).resolve(), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    sid = hashlib.sha256(path.read_bytes()).hexdigest()
    if profile.get('source_sha256') and sid != profile['source_sha256'].lower():
        raise ValueError('PDF hash differs from the visually calibrated profile')
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[profile['page'] - 1]
        text = page.extract_text() or ''
        # Caption crop provides the exact source for experiment conditions.
        evidence = page.crop(tuple(profile['caption_bbox'])).extract_text() or ''
        if not evidence:
            raise ValueError('Caption crop contains no text')
        block = dict(source_id=sid, paper_id=profile['paper_id'], source_file=str(path),
                     kind='digitized_curve', locator=f"page:{profile['page']};{profile['figure']}",
                     text=evidence, block_id=uid(sid, profile), bbox=profile['region'])
        records, point_rows = [], []
        renderer = pdfium.PdfDocument(path)
        image = renderer[profile['page'] - 1].render(scale=2).to_pil()
        draw = ImageDraw.Draw(image)
        for selector in profile['series']:
            for number, point in enumerate(marker_centers(page, profile, selector), 1):
                x_axis = selector.get('x', profile['x'])
                x = axis_value(point['x'], x_axis)
                y_axis = selector.get('y', profile['y'])
                y = axis_value(point['y'], y_axis)
                conditions = {}
                for key, q in {**profile.get('conditions', {}), **selector.get('conditions', {})}.items():
                    if q.get('quote') and q['quote'] not in evidence:
                        raise ValueError('Condition quote missing from caption: ' + key)
                    conditions[key] = dict(raw_value=q['value'], unit=q['unit'], evidence=q.get('quote', evidence), origin=q.get('origin', 'caption'))
                xprop = x_axis.get('property', profile['x']['property'])
                conditions[xprop] = dict(raw_value=round(x, 4), unit=x_axis.get('unit', profile['x']['unit']), evidence=evidence, origin='vector_axis_calibration')
                r = make_record(block, selector['catalyst'], y_axis.get('property', profile['y']['property']), round(y, 4), y_axis.get('unit', profile['y']['unit']), evidence, conditions, 'pdf_vector_markers', uid(block['block_id'], selector['catalyst'], point))
                r['vector_point'] = point
                r['figure'] = profile['figure']
                r['calibration'] = {'x': x_axis, 'y': y_axis}
                r['selector'] = selector
                r['estimated'] = True
                r['review_note'] = 'Extracted from plotted marker geometry; not author raw data. Axes and legend require visual review.'
                records.append(r)
                point_rows.append(dict(
                    catalyst=selector['catalyst'],
                    x_property=xprop,
                    x=round(x, 4),
                    x_unit=x_axis.get('unit', profile['x']['unit']),
                    y_property=y_axis.get('property', profile['y']['property']),
                    y=round(y, 4),
                    y_unit=y_axis.get('unit', profile['y']['unit']),
                    pdf_x=point['x'],
                    pdf_y=point['y'],
                    figure=profile['figure'],
                    estimated=True,
                ))
                box = [2*v for v in point['bbox']]
                draw.rectangle(box, outline=(0, 160, 0), width=1)
                draw.text((box[2]+1, box[1]), str(number), fill=(0, 120, 0))
        overlay = io.BytesIO()
        image.save(overlay, format='PNG')
        (out / 'overlay.png').write_bytes(overlay.getvalue())
    write_json(out / 'candidates.json', records)
    write_json(out / 'sources.json', [block])
    write_json(out / 'profile.json', profile)
    write_csv(out / 'candidates.csv', records)
    write_csv(out / 'points.csv', point_rows)
    write_csv(out / 'decisions_template.csv', [dict(record_id=r['record_id'], decision='', reviewer='', note='') for r in records])
    print(json.dumps({'figure': profile['figure'], 'markers': len(records), 'issues': sum(bool(r['issues']) for r in records)}))
    return records
