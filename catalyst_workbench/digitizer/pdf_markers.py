"""Recover separate printed vector symbols inside a user-specified PDF plot.

Axes and legend assignments must be supplied after checking the page. This
does not infer experimental identity, trace connecting lines or approve data.
Raster/scanned figures require the existing image digitizer instead.
"""
from __future__ import annotations

import math


def vector_markers(page, plot_bbox, series, exclude_boxes=(), size_range=(4, 10)):
    """Return PDF-point centres, shape and object index for each named series.

Each series specifies label, shape (circle/square/diamond) and RGB colour.
Coordinates have a top-left origin, matching pdfplumber's top/bottom fields.
Filled and stroked copies of a symbol are deduplicated. Legend areas must be
excluded explicitly. Ambiguous colour/shape assignments raise an error.
"""
    l, t, r, b = map(float, plot_bbox)
    low, high = map(float, size_range)
    if not all(math.isfinite(x) for x in (l, t, r, b, low, high)) or not (l < r and t < b and 0 < low < high):
        raise ValueError('Invalid plot bounds or symbol sizes.')
    result = {s['label']: [] for s in series}
    if len(result) != len(series):
        raise ValueError('Series labels must be unique.')
    for s in series:
        if s['shape'] not in ('circle', 'square', 'diamond') or len(s['color']) != 3:
            raise ValueError('Specify a supported symbol shape and RGB colour.')
    for object_type, objects in (('curve', page.curves), ('rect', page.rects)):
        for index, obj in enumerate(objects):
            w, h = obj['x1'] - obj['x0'], obj['bottom'] - obj['top']
            x, y = (obj['x0'] + obj['x1']) / 2, (obj['top'] + obj['bottom']) / 2
            if not (low <= w <= high and low <= h <= high and abs(w - h) < .3):
                continue
            if not (l <= x <= r and t <= y <= b):
                continue
            if any(a <= x <= c and z <= y <= d for a, z, c, d in exclude_boxes):
                continue
            path = obj.get('path', [])
            commands = [v[0] for v in path]
            points = {tuple(v) for v in obj.get('pts', [])}
            shape = None
            if object_type == 'rect':
                shape = 'square'
            elif commands.count('c') == 4 and 'h' in commands:
                shape = 'circle'
            elif ('c' not in commands and len(points) == 4 and
                  ('h' in commands or obj.get('pts', [None])[0] == obj.get('pts', [None])[-1])):
                # A diamond's vertices lie on the bounding box midlines.
                if all(abs(px - x) < .1 or abs(py - y) < .1 for px, py in points):
                    shape = 'diamond'
            if not shape:
                continue
            colors = []
            for enabled, key in ((obj.get('fill'), 'non_stroking_color'), (obj.get('stroke'), 'stroking_color')):
                color = obj.get(key)
                if enabled and isinstance(color, (tuple, list)) and len(color) == 3:
                    colors.append(color)
            matches = [s for s in series if s['shape'] == shape and any(
                max(abs(float(a) - float(c)) for a, c in zip(color, s['color'])) < .02 for color in colors)]
            if len(matches) > 1:
                raise ValueError('Multiple series match one symbol; check legend assignments.')
            if not matches:
                continue
            points_out = result[matches[0]['label']]
            if any(math.hypot(x - p['x_pdf'], y - p['y_pdf']) < .15 for p in points_out):
                continue
            points_out.append({'x_pdf': x, 'y_pdf': y, 'shape': shape,
                               'pdf_object_type': object_type, 'pdf_object_index': index,
                               'symbol_bbox_pdf': [obj['x0'], obj['top'], obj['x1'], obj['bottom']],
                               'review_status': 'unreviewed', 'method': 'pdf_vector_symbol_center'})
    return {label: sorted(points, key=lambda p: p['x_pdf']) for label, points in result.items()}
