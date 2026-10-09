"""Task-oriented display and export of source-backed numbers, never ML approval."""
from __future__ import annotations

import copy
import csv
from datetime import datetime
import html
import json
from pathlib import Path
import re
import unicodedata
import uuid

ROLE_LABELS = {
    'target': '当前目标性能', 'condition': '实验条件',
    'preparation': '材料制备', 'characterization': '材料表征',
    'other_result': '其他结果', 'measurement': '装置 / 测量参数',
    'background': '背景 / 设计要求',
}
METRIC_LABELS = {
    'NO conversion': 'NO 转化率', 'NOx conversion': 'NOₓ 转化率',
    'NH3 conversion': 'NH₃ 转化率', 'N2 selectivity': 'N₂ 选择性',
    'CO2 conversion': 'CO₂ 转化率', 'CO conversion': 'CO 转化率',
    'CO selectivity': 'CO 选择性', 'methanol selectivity': '甲醇选择性',
    'methanol yield': '甲醇收率', 'methanol STY': '甲醇时空收率',
    'NH3 adsorption energy': 'NH₃ 吸附能', 'adsorption energy': '吸附能',
    'adsorption capacity': '吸附容量', 'BET surface area': 'BET 比表面积',
    'pore volume': '孔体积', 'reaction temperature': '反应温度',
}
_SYMBOLS = {'eq': '', 'gt': '>', 'ge': '≥', 'lt': '<', 'le': '≤', 'approx': '约 ', 'unknown': '约 / 待核对 '}


def _normal(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text))).casefold()


def _number(value):
    if value is None:
        return ''
    if isinstance(value, (float, int)):
        return format(value, '.10g')
    return str(value)


def value_label(item):
    label = _SYMBOLS.get(item.get('operator', 'eq'), '') + _number(item.get('value'))
    if item.get('value_high') is not None:
        label += '～' + _number(item['value_high'])
    uncertainty = item.get('uncertainty')
    if uncertainty:
        if isinstance(uncertainty, dict):
            amount = uncertainty.get('value', uncertainty.get('absolute', uncertainty.get('magnitude')))
            label += ' ± ' + _number(amount) if amount is not None else '（附不确定度）'
        else:
            label += '（附不确定度）'
    return label


def _role(item, target):
    role = item.get('quantity_role')
    if role == 'background' or item.get('assertion_scope') == 'prior_work':
        return 'background'
    if item.get('metric') == target and item.get('kind') in ('absolute', 'range'):
        return 'target'
    if role in ROLE_LABELS:
        return role
    if item.get('metric') == 'reaction temperature':
        return 'condition'
    if item.get('metric') in ('BET surface area', 'pore volume'):
        return 'characterization'
    return 'other_result'


def numeric_attribution(item, role=None):
    """Describe the reported object without inventing a sample or experiment ID."""
    role = role or _role(item, '')
    if role == 'background':
        return '背景 / 要求', '保留背景依据，不绑定为本研究的材料性能记录'
    if item.get('sample_label'):
        return item['sample_label'], '原文已给出样品名；与性能记录关联时仍需核对实验条件'
    source = item.get('source_role', '')
    if source == 'apparatus_operating_condition':
        table = str(item.get('source_table') or '').strip()
        return ((table + ' · 测试工况') if table else '装置测试工况',
                '这是装置工况，无需虚构催化剂样品名；应用于性能记录时核对适用实验')
    descriptions = {
        'apparatus_geometry': ('反应器 / 装置尺寸', '保存为装置信息，无需绑定催化剂样品'),
        'gas_composition_ratio': ('入口气体组成', '已识别气体条件，应用于性能记录时核对适用实验'),
        'explicit_inlet_concentration': ('反应入口气体条件', '已识别入口条件，应用于性能记录时核对适用实验'),
        'premix_stream': ('预混气支路', '支路条件独立保留；不能直接当成最终混合后的进料条件'),
        'urea_solution_feed': ('尿素供给工况', '已识别供给条件，应用于性能记录时核对适用实验'),
        'local_concentration': ('局部测点', '保留局部测量含义，不能当作全截面平均值'),
        'distribution_result': ('装置氨分布结果', '分布均匀性结果，不是催化剂转化率'),
        'reported_summary': ('作者报告的汇总结果', '保留汇总值与误差含义，不改成单次催化剂性能'),
        'author_calculated_result': ('作者计算结果', '保留作者计算的定义，不改成实测转化率'),
    }
    if source in descriptions:
        return descriptions[source]
    if role == 'condition':
        return '适用实验待确认', '原文尚未明确唯一实验；使用前核对条件适用范围'
    if role == 'preparation':
        return '制备对象待确认', '需要确认对应哪批材料及哪一步制备操作'
    if role == 'characterization':
        return '材料样品待确认', '需要确认被测材料，不能按同篇论文直接合并到任一样品'
    return '对应样品 / 实验待确认', '需要确认该结果对应的样品和实验'


def numeric_overview(project):
    from paper_workspace import TASKS, CONDITION_LABELS
    target = TASKS[project['task_id']]['metric']
    rows, keys = [], {}
    for item in project.get('evidence', []):
        if item.get('branch') == 'image' or item.get('stale') or item.get('review_status') in ('stale', 'excluded'):
            continue
        if item.get('value') is None or item.get('kind') not in ('absolute', 'range', 'numeric_fact', 'table_value'):
            continue
        role = _role(item, target)
        key = (item.get('metric'), item.get('value'), item.get('value_high'), item.get('unit'),
               item.get('operator'), item.get('sample_label'), role, _normal(item.get('quote', '')))
        occurrence = {'evidence_id': item['evidence_id'], 'page': item.get('page'),
                      'start': item.get('start'), 'end': item.get('end')}
        if key in keys:
            keys[key]['occurrences'].append(occurrence)
            continue
        attribution, attribution_note = numeric_attribution(item, role)
        missing = []
        if role == 'target':
            if not item.get('sample_label'):
                missing.append('样品未绑定')
            conditions = item.get('conditions', {})
            for field in TASKS[project['task_id']]['conditions']:
                present = conditions.get(field) is not None and str(conditions.get(field)).strip() != ''
                if field == 'temperature_C':
                    present = present or bool(conditions.get('temperature'))
                if not present:
                    missing.append(CONDITION_LABELS.get(field, field) + '待关联')
            if item.get('assertion_scope') != 'current_study':
                missing.append('本研究结果归属待确认')
            if item.get('operator') != 'eq' or item.get('value_high') is not None:
                missing.append('区间 / 边界不作精确单点')
        elif role in ('condition', 'preparation', 'characterization'):
            if item.get('sample_label'):
                missing.append('样品已识别；用于性能记录时核对实验条件')
            else:
                missing.append(attribution_note)
        elif role == 'background':
            missing.append('不能作为本研究实测结果')
        else:
            missing.append('不是当前预测目标')
        if item.get('review_status') != 'reviewed':
            missing.append('待人工核对')
        row = {**copy.deepcopy(item), 'display_role': role, 'role_label': ROLE_LABELS[role],
               'metric_label': item.get('metric_label') or METRIC_LABELS.get(item.get('metric'), item.get('metric', '待确认指标')),
               'attribution_label': attribution, 'attribution_note': attribution_note,
               'value_label': value_label(item), 'missing': missing,
               'occurrences': [occurrence]}
        keys[key] = row
        rows.append(row)
    order = {role: i for i, role in enumerate(ROLE_LABELS)}
    rows.sort(key=lambda row: (order[row['display_role']], row.get('page', 0), row.get('start', 0)))
    counts = {role: sum(row['display_role'] == role for row in rows) for role in ROLE_LABELS}
    total = len(rows)
    if not project.get('pages'):
        message = '先读取全文。这里将列出指标、数值、单位、来源和缺失条件。'
    elif not counts['target']:
        message = f'本次提取到 {total} 项数值，当前目标“{METRIC_LABELS.get(target, target) or "待确定"}”候选为 0。'
        title = project.get('article', {}).get('title', '')
        if re.search(r'uniformity|concentration distribution|均匀性|浓度分布', title, re.I):
            message += ' 本篇侧重浓度分布 / 均匀性，应先复核与催化剂性能预测任务的适配性。'
        message += ' 请查看其他结果及图表；未提取到不等于原文未报告。'
    else:
        message = f'提取到 {total} 项数值，其中当前目标性能 {counts["target"]} 项。先核对样品和条件，再建立实验记录。'
    if project.get('pages') and not project.get('numeric_extraction_version'):
        message += ' 此档案尚未运行新版数值提取，请点击“读取全文并提取数值”。'
    return {'rows': rows, 'counts': counts, 'message': message, 'target_metric': target}


def export_numeric_inventory(project):
    """Export pending scientific facts without calling them training observations."""
    from paper_workspace import verify_source, _csv
    verify_source(project)
    report = numeric_overview(project)
    folder = Path(project['run_dir']) / ('数值提取清单_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:4])
    folder.mkdir(parents=True)
    rows = []
    for item in report['rows']:
        rows.append({'用途': item['role_label'], '指标': item['metric_label'], '原值': item['value'],
                     '区间上限': item.get('value_high'), '形式': item.get('operator'), '单位': item.get('unit'),
                     '样品': item.get('sample_label', ''), '归属对象': item['attribution_label'],
                     '归属说明': item['attribution_note'], '已有条件': item.get('conditions', {}),
                     '缺失或限制': '；'.join(item['missing']), '页码': item.get('page'),
                     '表号': item.get('source_table', ''), '原句': item.get('quote', ''),
                     '不确定度': item.get('uncertainty', {}), '审核状态': item.get('review_status', 'unreviewed'),
                     '证据ID': item['evidence_id'], '其他出处': item['occurrences'],
                     'DOI': project['article'].get('doi', ''), '源PDF指纹': project['article']['source_sha256'],
                     '可直接用于训练': False})
    fields = list(rows[0]) if rows else ['用途', '指标', '原值', '单位', '页码', '原句', '可直接用于训练']
    _csv(folder / '本篇科学数值_待核对.csv', rows, fields)
    payload = {'schema_version': 'paper-numeric-inventory/1.0', 'article': project['article'],
               'task_id': project['task_id'], 'report': report,
               'scope': '原文数值与上下文清单；未经样品、实验条件及来源归属审核，不是机器学习训练表。'}
    (folder / '数值与原文证据.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
    escape = lambda value: html.escape(str(value if value is not None else ''))
    tr = []
    for item in report['rows']:
        tr.append('<tr>' + ''.join('<td>' + escape(value) + '</td>' for value in (
            item['role_label'], item['metric_label'], item['value_label'], item.get('unit'),
            item['attribution_label'], '；'.join(item['missing']), item.get('page'), item.get('quote'))) + '</tr>')
    (folder / '00_本篇数值清单.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>本篇数值提取清单</title>
<style>body{font:16px "Microsoft YaHei",sans-serif;background:#f3f7f9;color:#183c4a;margin:32px}h1{font-size:26px}table{border-collapse:collapse;background:white;width:100%}th,td{padding:12px;border:1px solid #cfdae0;text-align:left;vertical-align:top}th{background:#155e75;color:white}p{line-height:1.7}.note{background:#fff1cc;padding:16px}td:last-child{min-width:300px;white-space:pre-wrap}a{color:#126b7d}</style>'''
        + '<h1>' + escape(project['article'].get('title')) + '</h1><p>' + escape(report['message'])
        + '</p><p class="note">这是待核对数值清单，不是训练数据。条件、表征和均匀性等其他结果分别列出；背景要求不能作为本研究实测值。</p>'
        + '<p><a href="本篇科学数值_待核对.csv">打开CSV</a> · <a href="数值与原文证据.json">完整来源记录</a></p>'
        + '<table><tr>' + ''.join('<th>' + title + '</th>' for title in ('用途', '指标', '数值', '单位', '归属对象', '核对事项', '页', '原文证据'))
        + '</tr>' + ''.join(tr) + '</table></html>', encoding='utf8')
    return folder


def with_numeric_context(project, record, evidence):
    """Prepare one explicit field mapping; the user must save and review it."""
    from paper_encoding import FEATURE_SCHEMA, CONDITION_SCHEMA
    if evidence.get('stale') or evidence.get('review_status') == 'excluded':
        raise ValueError('这条数值证据已过期或不采用。')
    if evidence.get('quantity_role') == 'background' or evidence.get('assertion_scope') == 'prior_work':
        raise ValueError('背景或他人研究数值不能补为本实验条件。')
    if evidence.get('operator', 'eq') != 'eq' or evidence.get('value_high') is not None or evidence.get('uncertainty'):
        raise ValueError('这条是边界、区间或含误差的数值，请保留原始形式并在记录里人工核对，不能自动填成单值。')
    field = evidence.get('field_key') or evidence.get('suggested_feature_key')
    if field in CONDITION_SCHEMA:
        group, spec = 'conditions', CONDITION_SCHEMA[field]
    elif field in FEATURE_SCHEMA and FEATURE_SCHEMA[field]['kind'] == 'numeric':
        group, spec = 'features', FEATURE_SCHEMA[field]
    else:
        raise ValueError('此指标尚无直接对应的编码字段，可在原文核对页“补充到已有记录”保留证据，不自动改填其他指标。')
    expected = spec.get('unit', '')
    normal_unit = lambda value: _normal(value).replace('^', '').replace('−', '-')
    if normal_unit(evidence.get('unit')) != normal_unit(expected):
        raise ValueError(f'原单位 {evidence.get("unit")} 与字段单位 {expected} 不同，请先核对换算，不自动填入。')
    item = copy.deepcopy(record)
    source_sample, target_sample = evidence.get('sample_label'), item.get('sample_label')
    if source_sample and target_sample and _normal(source_sample) != _normal(target_sample):
        raise ValueError('数值证据的样品与记录样品不同，请先核对样品对应关系。')
    prior = item.setdefault(group, {}).get(field)
    if prior is not None and str(prior).strip() != '' and prior != evidence['value']:
        raise ValueError('这条记录已有不同的字段值，请在原记录核对冲突；本次未覆盖。')
    item[group][field] = evidence['value']
    eid = evidence['evidence_id']
    if eid not in item['evidence_ids']:
        item['evidence_ids'].append(eid)
    item.setdefault('evidence_roles', {})[eid] = 'context'
    item.update(review_status='draft', reviewer='')
    return item
