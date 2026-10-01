"""Translate calibrated figure snapshots into the existing observation schema."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from PIL import Image

from .core import FIELDS, make_record, read_json, uid, write_csv, write_json
from .figure_digitizer.digitize import pixel_to_data, validate_calibration
from .literature import normalized_doi


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot_image(path, snapshot):
    relative = snapshot.get('image_file')
    candidates = [Path(path).parent / relative] if relative else []
    if snapshot.get('image_path'):
        candidates.append(Path(snapshot['image_path']))
    image = next((p.resolve() for p in candidates if p.is_file()), None)
    if image is None or file_hash(image) != snapshot.get('image_sha256'):
        raise ValueError('找不到与标定一致的原图，或原图已变化。请连同 source.png 保存导出目录。')
    return image


def build_figure_records(path, paper_id, y_property, x_property=None, *, source_path=None,
                         context=None, conditions=None, document_type='unknown'):
    path = Path(path).resolve()
    snapshot = read_json(path)
    if snapshot.get('schema_version') != 'figure-digitizer/1.0':
        raise ValueError('请选择图片读数工具导出的“读数与溯源.json”。')
    if not paper_id or y_property not in FIELDS:
        raise ValueError('必须指定论文归属和有效的纵轴指标。')
    if x_property is not None and (x_property not in FIELDS or FIELDS[x_property][0] != 'condition'):
        raise ValueError('横轴只能映射为明确的反应条件；其他类型请保留读数后单独关联。')
    if snapshot.get('doi') and normalized_doi(snapshot['doi']) != normalized_doi(paper_id) and normalized_doi(snapshot['doi']) != normalized_doi((context or {}).get('doi')):
        raise ValueError('读数快照 DOI 与所选论文不一致。')
    if snapshot.get('chart_type') not in {'xy', 'bar'}:
        raise ValueError('暂不支持此图型。')
    if snapshot['chart_type'] == 'bar' and x_property:
        raise ValueError('柱图横向位置是像素位置，不能当作数值条件。')
    if not snapshot.get('figure_label', '').strip():
        raise ValueError('请先填写图号和子图号，再导出。')
    points = snapshot.get('points')
    if not isinstance(points, list) or not points:
        raise ValueError('快照没有读数点。')
    cal = validate_calibration(snapshot['calibration'])
    image = snapshot_image(path, snapshot)
    with Image.open(image) as opened:
        width, height = opened.size
    metadata = snapshot['source_metadata']
    source = Path(source_path or metadata['source_path'])
    if not source.is_file() or file_hash(source) != metadata.get('source_sha256'):
        # A saved image itself is a valid source when its hash matches the imported original image.
        if metadata.get('source_kind') == 'image' and file_hash(image) == metadata.get('source_sha256'):
            source = image
        else:
            raise ValueError('原始论文文件缺失或内容已变化，请使用同一份论文重新关联。')
    context = context or {}
    conditions = conditions or {}
    if conditions and not context.get('text'):
        raise ValueError('固定反应条件需要对应原文内容块作为依据。')
    if x_property in conditions:
        raise ValueError('同一条件不能同时来自横轴和固定条件。')
    if context.get('document_type') == 'review':
        document_type = 'review'
    blocks, records, ids = [], [], set()
    for point in points:
        point_id = point.get('point_id')
        if not point_id or point_id in ids:
            raise ValueError('图片读数点标识缺失或重复。')
        ids.add(point_id)
        if point.get('calibration_id') != snapshot.get('calibration_id'):
            raise ValueError('读数点与当前坐标轴标定不一致。')
        if not (0 <= point['px'] < width and 0 <= point['py'] < height):
            raise ValueError('读数点超出保存的原图。')
        if snapshot.get('roi'):
            left, top, right, bottom = snapshot['roi']
            if not (min(left, right) <= point['px'] <= max(left, right) and min(top, bottom) <= point['py'] <= max(top, bottom)):
                raise ValueError('读数点超出绘图区，请回原图核对。')
        calculated = pixel_to_data(point['px'], point['py'], cal)
        for axis in ('x', 'y'):
            cached = point.get(axis)
            if isinstance(cached, bool) or not isinstance(cached, (int, float)) or not math.isclose(cached, calculated[axis], rel_tol=1e-9, abs_tol=1e-8):
                raise ValueError('保存的读数与像素标定结果不一致，请重新导出。')
        evidence = json.dumps({'figure': snapshot['figure_label'], 'point_id': point_id,
                               'sample': point.get('sample_label', ''), 'series': point.get('series_label', ''),
                               'category': point.get('category', ''), 'pixel': [point['px'], point['py']],
                               'x': {'value': calculated['x'], 'unit': cal['x']['unit']},
                               'y': {'value': calculated['y'], 'unit': cal['y']['unit']}}, ensure_ascii=False)
        text = evidence + ('\n' + context['text'] if context.get('text') else '')
        locator = f"page:{metadata.get('page', 1)};figure:{snapshot['figure_label']};point:{point_id}"
        block = dict(paper_id=paper_id, source_id=metadata['source_sha256'], source_file=str(source.resolve()),
                     kind='digitized_curve', text=text, locator=locator,
                     block_id=uid(metadata['source_sha256'], locator, evidence, context.get('block_id')),
                     image_path=str(image), document_type=document_type,
                     training_eligible=document_type in {'research_article', 'supplementary_information'})
        specs = {}
        for prop, spec in conditions.items():
            quote = spec.get('evidence', '')
            if not quote or quote not in context['text'] or str(spec.get('raw_value', '')) not in quote:
                raise ValueError('固定条件必须复制包含数值的原文证据：' + prop)
            specs[prop] = dict(spec, source_locator=context.get('locator'), origin='reported')
        if x_property:
            specs[x_property] = {'raw_value': str(calculated['x']), 'unit': cal['x']['unit'],
                                 'evidence': evidence, 'origin': 'image_digitized_approximate', 'source_locator': locator}
        record = make_record(block, point.get('sample_label'), y_property, str(calculated['y']),
                             cal['y']['unit'], evidence, specs, method='mapped_table',
                             experiment_id=uid(paper_id, metadata['source_sha256'], snapshot['figure_label'], point.get('series_label')))
        record.update(method='image_digitizer', figure=snapshot['figure_label'],
                      estimated=True, value_origin='image_digitized_approximate',
                      image_review_status=point.get('review_status'), review_level='pending_scientific_review',
                      digitization_group=record['experiment_id'],
                      provenance={'snapshot_path': str(path), 'snapshot_sha256': file_hash(path),
                                  'point_id': point_id, 'calibration': cal, 'calibration_id': snapshot['calibration_id'],
                                  'image_path': str(image), 'image_sha256': snapshot['image_sha256'],
                                  'pixel_resolution': {axis: calculated[axis + '_pixel_resolution'] for axis in ('x', 'y')},
                                  'point_method': point.get('method'), 'trace_id': point.get('trace_id')})
        record['record_id'] = uid('figure-observation-v1', paper_id, block['source_id'], point_id,
                                  cal, y_property, x_property, record['value'], record['catalyst'], record['conditions'])
        blocks.append(block)
        records.append(record)
    return records, blocks


def import_figure(path, mapping, output):
    context = mapping.get('context')
    records, sources = build_figure_records(path, mapping['paper_id'], mapping['y_property'], mapping.get('x_property'),
        source_path=mapping.get('source_path'), context=context, conditions=mapping.get('conditions'),
        document_type=mapping.get('document_type', 'unknown'))
    output = Path(output)
    write_json(output / 'candidates.json', records)
    write_csv(output / 'candidates.csv', records)
    write_json(output / 'sources.json', sources)
    write_csv(output / 'decisions_template.csv', [{'record_id': r['record_id'], 'decision': '', 'reviewer': '', 'note': ''} for r in records])
    return {'candidates': len(records), 'pending': len(records), 'approved': 0}
