"""Evidence-backed decision support, independent of X/y and reading priority.

The default rubric is a proposed completeness rubric, not a calibrated estimate
of scientific truth. Performance is a single-metric utility with fixed anchors.
No data-dependent scaling, automatic admission, or model fitting occurs here.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

VERSION = 'evidence-performance-score/1.0'
ANNOTATION_VERSION = 'score-context-review/1.0'
REFERENCE = {
    'repository': 'https://github.com/Chirune/nh3_scr_tool',
    'commit': 'da1e7a8ba8d48c9a9ca19f161842118cf4343e58',
    'borrowed_concepts': ['逐项证据', '可配置权重', '规则版本与指纹', '阅读优先级不改变纳入判定'],
    'original_score_role': '文献阅读优先级；不是这里的数据质量分或性能分',
}
DIMENSIONS = {
    'traceability': ('来源可追溯', 20), 'review': ('核验状态与数值规范', 20),
    'identity': ('样品与实验归属', 15), 'conditions': ('条件与定义完整度', 20),
    'material': ('材料描述完整度', 15), 'uncertainty': ('误差与重复性证据', 10),
}
# These are separate, manually checked comparison annotations, not ML inputs.
CONTEXT_FIELDS = {
    'metric_definition': ('指标定义及分母口径', 'text', 'all'),
    'feed_composition': ('完整进料：浓度、平衡气、水/硫状态', 'text', 'experiment'),
    'contact_time': ('空速/接触时间：数值、单位、基准', 'text', 'experiment'),
    'reactor_protocol': ('反应器、稳态/扫描方式、运行时间', 'text', 'experiment'),
    'material_state': ('新鲜/老化状态及测试前处理', 'text', 'experiment'),
    'energy_reference': ('能量参考态、符号及修正定义', 'text', 'dft'),
    'calculation_protocol': ('泛函、U、色散、自旋、截断及k点设置', 'text', 'dft'),
    'geometry_protocol': ('建模与收敛方案、覆盖度定义', 'text', 'dft'),
    'uncertainty_value': ('不确定度数值（使用当前 y 的统一单位）', 'number', 'all'),
    'uncertainty_definition': ('不确定度含义：SD/SE/区间等及置信水平', 'text', 'all'),
    'repeat_count': ('独立实验次数（不是曲线上的点数）', 'integer', 'experiment'),
    'digitization_error': ('读图误差评估方法与结果', 'text', 'all'),
    'convergence_check': ('DFT数值收敛验证', 'text', 'dft'),
}
MODES = {'disabled': '只展示原值，暂不评分', 'higher': '数值越高越接近目标',
         'lower': '数值越低越接近目标', 'target_range': '接近指定目标区间'}
SNAPSHOT_FIELDS = ('input_packets', 'observations', 'excluded', 'split', 'encoder', 'encoded_rows')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def present(value):
    return value is not None and value != '' and value != [] and value != {}


def default_config():
    import paper_workspace as work
    profiles = {}
    for key, task in work.TASKS.items():
        if key == 'pending':
            continue
        fraction = '%' in task['units']
        unit = 'fraction' if fraction else 'eV' if key == 'nh3_adsorption_energy' else task['units'][0]
        enabled = fraction and key != 'cuzn_co_selectivity'
        disabled_reasons = {
            'nh3_adsorption_energy': '吸附能不是越负越好；须依据反应目标和机理预先定义适用的目标区间。',
            'cuzn_methanol_sty': '时空收率没有通用上限；先统一归一化基准，再给出外部目标或参考范围。',
            'cuzn_tof': 'TOF没有通用上限；先核对活性位点计数与速率定义，再确定参考范围。',
            'cuzn_co_selectivity': '先确认目标产物；若目标为甲醇，可明确设置CO选择性越低越接近目标。',
        }
        profiles[key] = {'mode': 'higher' if enabled else 'disabled', 'unit': unit,
            'anchor_low': 0.0 if fraction else None, 'anchor_high': 1.0 if fraction else None,
            'target_low': None, 'target_high': None,
            'goal': ('提高该单项指标；其他性能须另行查看' if enabled else
                     '先明确产物偏好或目标范围，再设评分尺度'),
            'rationale': ('按比例固有的0至1范围换算；不使用本批数据最大最小值' if enabled else disabled_reasons[key])}
    return {'version': VERSION, 'name': '科研证据与单项性能双评分 v1',
        'note': '建议起始模板，尚未用团队人工标注集校准。质量与性能不相加、不相乘。',
        'quality_weights': {key: value[1] for key, value in DIMENSIONS.items()},
        'profiles': profiles}


def validate_config(config):
    defaults = default_config()
    if not isinstance(config, dict) or set(config) != set(defaults) or config['version'] != VERSION:
        raise ValueError('评分模板版本或字段不符，请从本界面导出的模板开始修改。')
    for key in ('name', 'note'):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ValueError('模板名称与说明不能为空。')
    weights = config['quality_weights']
    if not isinstance(weights, dict) or set(weights) != set(DIMENSIONS):
        raise ValueError('质量权重须包含完整的六个维度。')
    if any(number(v) is None or float(v) < 0 for v in weights.values()) or not math.isfinite(sum(float(v) for v in weights.values())) or sum(float(v) for v in weights.values()) <= 0:
        raise ValueError('权重须是有限的非负数，且总和大于0。')
    profiles = config['profiles']
    if not isinstance(profiles, dict) or set(profiles) != set(defaults['profiles']):
        raise ValueError('须保留全部已支持任务的评分配置。')
    for task, profile in profiles.items():
        original = defaults['profiles'][task]
        if not isinstance(profile, dict) or set(profile) != set(original):
            raise ValueError('性能规则字段不完整：' + task)
        if profile['mode'] not in MODES or profile['unit'] != original['unit']:
            raise ValueError('性能方向或统一单位不符：' + task)
        if not all(isinstance(profile[k], str) and profile[k].strip() for k in ('goal', 'rationale')):
            raise ValueError('请写明该任务的目标，以及采用这个评分尺度的依据。')
        for key in ('anchor_low', 'anchor_high', 'target_low', 'target_high'):
            if profile[key] is not None and number(profile[key]) is None:
                raise ValueError('评分边界须为有限数值。')
        if profile['mode'] != 'disabled':
            low, high = number(profile['anchor_low']), number(profile['anchor_high'])
            if low is None or high is None or low >= high or not math.isfinite(high - low):
                raise ValueError('启用评分需要“下边界 < 上边界”，并填写实际统一单位的值。')
            if profile['mode'] == 'target_range':
                left, right = number(profile['target_low']), number(profile['target_high'])
                if left is None or right is None or not low < left <= right < high:
                    raise ValueError('目标区间须满足：下边界 < 目标下限 ≤ 目标上限 < 上边界。')
    result = copy.deepcopy(config)
    result['quality_weights'] = {key: float(value) for key, value in weights.items()}
    for profile in result['profiles'].values():
        for key in ('anchor_low', 'anchor_high', 'target_low', 'target_high'):
            profile[key] = number(profile[key])
    return result


def observation_fingerprint(row):
    return fingerprint({k: row[k] for k in ('observation_id', 'source_sha256', 'task_id',
        'raw_record', 'evidence', 'X_raw', 'y', 'y_unit')})


def _annotation_fingerprint_matches(row, expected):
    if expected == observation_fingerprint(row):
        return True
    # Schema additions with empty values do not change previously reviewed
    # evidence. Any new nonempty value, changed record or evidence still expires it.
    legacy = {'active_metals', 'support', 'support_detail', 'preparation_method',
        'metal_loading_wt_pct', 'cu_zn_atomic_ratio', 'calcination_temperature_C',
        'calcination_time_h', 'reduction_temperature_C', 'BET_surface_area_m2_g',
        'surface_facet', 'adsorption_site', 'dft_functional', 'coverage_ml',
        'temperature_C', 'pressure_kPa', 'space_velocity_h_inv', 'flow_rate_ml_min', 'catalyst_mass_g'}
    if any(present(v) for k, v in row['X_raw'].items() if k not in legacy):
        return False
    previous = {**row, 'X_raw': {k: v for k, v in row['X_raw'].items() if k in legacy}}
    return expected == observation_fingerprint(previous)


def context_fields(row):
    method = 'dft' if row['task_id'] == 'nh3_adsorption_energy' else 'experiment'
    return {k: v for k, v in CONTEXT_FIELDS.items() if v[2] in ('all', method)
            and (k != 'digitization_error' or row.get('approximate'))}


def make_annotation(row, entries, reviewer, note):
    annotation = {'schema_version': ANNOTATION_VERSION, 'observation_id': row['observation_id'],
        'observation_fingerprint': observation_fingerprint(row), 'reviewer': reviewer.strip(),
        'note': note.strip(), 'reviewed_at': datetime.now(timezone.utc).isoformat(),
        'fields': copy.deepcopy(entries)}
    _, errors = checked_annotation(row, annotation)
    if errors:
        raise ValueError('；'.join(errors))
    return annotation


def checked_annotation(row, annotation):
    if not annotation:
        return {}, []
    if not isinstance(annotation, dict):
        return {}, ['比较补充的结构无效，未用于评分。']
    if (annotation.get('schema_version') != ANNOTATION_VERSION or
        annotation.get('observation_id') != row['observation_id'] or
        not _annotation_fingerprint_matches(row, annotation.get('observation_fingerprint'))):
        return {}, ['比较补充对应旧版记录或另一条观察，请重新核对；旧补充未用于评分。']
    if not all(isinstance(annotation.get(k), str) and annotation[k].strip() for k in ('reviewer', 'note', 'reviewed_at')):
        return {}, ['比较补充缺少核验人、核对说明或时间；未用于评分。']
    fields = annotation.get('fields')
    allowed = context_fields(row)
    if not isinstance(fields, dict) or set(fields) - set(allowed):
        return {}, ['比较补充含本任务不支持的字段。']
    evidence = {e['evidence_id']: e for e in row['evidence']}
    result, errors = {}, []
    for key, entry in fields.items():
        label, kind, _ = allowed[key]
        if not isinstance(entry, dict) or set(entry) != {'value', 'evidence_id'}:
            errors.append(label + '缺少数值或证据编号'); continue
        source = evidence.get(entry['evidence_id']) if isinstance(entry['evidence_id'], str) else None
        if not source:
            errors.append(label + '引用的证据未关联到本条记录'); continue
        value = entry['value']
        if kind == 'text':
            if not isinstance(value, str) or not value.strip():
                errors.append(label + '不能为空'); continue
            value = value.strip()
            if re.fullmatch(r'(?i)(?:unknown|n/?a|none|unspecified|未报告|不详|未知|待定|待补|无)', value):
                errors.append(label + '应留空，未知不能充当已知的比较条件'); continue
        else:
            value = number(value)
            if value is None or value < 0 or (kind == 'integer' and (value < 2 or value != int(value))):
                errors.append(label + '无效；重复次数须为至少2的整数'); continue
            if kind == 'integer':
                value = int(value)
        result[key] = {'value': value, 'evidence_id': entry['evidence_id'],
            'page': source.get('page'), 'quote': source.get('quote', ''), 'reviewer': annotation['reviewer']}
    # A partially malformed supplement must not enable a comparison.
    return ({}, errors) if errors else (result, [])


def _quality(row, context, config):
    raw, x = row['raw_record'], row['X_raw']
    conditions = raw.get('conditions', {})
    groups = {key: [] for key in DIMENSIONS}
    def add(group, field, label, value, *, applicable=True, sources=None):
        groups[group].append({'field': field, 'label': label,
            'status': 'not_applicable' if not applicable else 'documented' if present(value) else 'unknown',
            'value': value, 'source': sources or field,
            'evidence_ids': [e['evidence_id'] for e in row['evidence']]})
    def ctx(group, field, applicable=True):
        item = context.get(field, {})
        add(group, field, CONTEXT_FIELDS[field][0], item.get('value'), applicable=applicable,
            sources=('人工补充；证据 ' + item['evidence_id']) if item else '尚无带证据的核对补充')
    add('traceability', 'source_sha256', '原PDF已校验且可定位', row['source_sha256'])
    add('traceability', 'evidence', '原句/图像点、页码与证据编号',
        True if row['evidence'] and all(e.get('page') and e.get('quote') for e in row['evidence']) else None)
    add('review', 'reviewer', '本条观察已由人工核验', row.get('reviewer'))
    add('review', 'conversion', '原值、单位及换算记录齐全', row.get('conversion'))
    add('review', 'integrity_gate', '通过当前来源、一致性、冲突和重复检查', True)
    for key, label in (('sample_label', '样品身份'), ('experiment_id', '实验编号'), ('measurement_type', '实验/计算归属')):
        add('identity', key, label, row.get(key))
    dft = row['task_id'] == 'nh3_adsorption_energy'
    condition_keys = (('surface_site', '表面与位点'), ('calculation_method', '计算方法说明')) if dft else (
        ('temperature_C', '反应温度'), ('pressure_kPa', '绝对压力'), ('feed_description', '进料原文'))
    for key, label in condition_keys:
        add('conditions', 'conditions.' + key, label, conditions.get(key))
    if not dft:
        add('conditions', 'contact_amount', '空速或流量数值', x.get('space_velocity_h_inv') or x.get('flow_rate_ml_min'))
    for field in (('energy_reference', 'calculation_protocol', 'geometry_protocol') if dft else
                  ('metric_definition', 'feed_composition', 'contact_time', 'reactor_protocol', 'material_state')):
        ctx('conditions', field)
    material_keys = ('active_metals', 'support', 'surface_facet', 'adsorption_site', 'dft_functional', 'coverage_ml') if dft else (
        'active_metals', 'support', 'preparation_method', 'metal_loading_wt_pct')
    import paper_encoding as encoding
    for key in material_keys:
        add('material', 'features.' + key, encoding.FEATURE_SCHEMA[key]['label'], x.get(key),
            applicable=not (key == 'metal_loading_wt_pct' and x.get('support') == 'unsupported'))
    add('material', 'composition', '组成/配方原文', raw.get('composition'))
    # A value without an error definition (or vice versa) earns no error credit.
    error_complete = 'uncertainty_value' in context and 'uncertainty_definition' in context
    add('uncertainty', 'uncertainty_pair', '不确定度数值与含义成对保留',
        {k: context[k] for k in ('uncertainty_value', 'uncertainty_definition')} if error_complete else None)
    ctx('uncertainty', 'convergence_check' if dft else 'repeat_count')
    ctx('uncertainty', 'digitization_error', applicable=bool(row.get('approximate')))
    components, missing = {}, []
    total_weight = sum(config['quality_weights'].values())
    for key, items in groups.items():
        applicable = [item for item in items if item['status'] != 'not_applicable']
        documented = sum(item['status'] == 'documented' for item in applicable)
        ratio = documented / len(applicable) if applicable else 0
        weight = config['quality_weights'][key] / total_weight * 100
        components[key] = {'label': DIMENSIONS[key][0], 'weight_percent': weight,
            'documented': documented, 'applicable': len(applicable), 'ratio': ratio,
            'contribution': weight * ratio, 'checks': items}
        missing.extend(item['label'] for item in applicable if item['status'] == 'unknown')
    return {'score': round(sum(item['contribution'] for item in components.values()), 2),
        'status': 'evidence_completeness_not_truth_probability', 'components': components,
        'missing': missing, 'gate': 'passed_encoding_checks',
        'note': '未知项不贡献证据分，表示尚无足够资料，不表示实验做得差。质量分不是测量正确率。'}


def _performance(row, profile):
    value = row['y']
    result = {'score': None, 'value': value, 'unit': row['y_unit'], 'mode': profile['mode'],
        'goal': profile['goal'], 'rationale': profile['rationale'], 'clipped': False,
        'status': 'objective_not_defined', 'formula': '',
        'note': '仅评价当前单项指标，不代表催化剂综合优劣，也不是机器学习预测分。'}
    if profile['mode'] == 'disabled':
        return result
    low, high = profile['anchor_low'], profile['anchor_high']
    if profile['mode'] == 'higher':
        utility = (value - low) / (high - low)
        formula = f'100 × (y − {low:g}) / ({high:g} − {low:g})'
    elif profile['mode'] == 'lower':
        utility = (high - value) / (high - low)
        formula = f'100 × ({high:g} − y) / ({high:g} − {low:g})'
    else:
        left, right = profile['target_low'], profile['target_high']
        utility = (value - low) / (left - low) if value < left else (high - value) / (high - right) if value > right else 1
        formula = f'目标区间 [{left:g}, {right:g}] 内100分，向两侧边界 {low:g}/{high:g} 线性降至0分'
    result.update(score=round(min(1, max(0, utility)) * 100, 2), clipped=not 0 <= utility <= 1,
        status='single_metric_scaled', formula=formula + '；结果限制在0–100，原值完整保留')
    return result


def _comparison(row, context):
    dft = row['task_id'] == 'nh3_adsorption_energy'
    fields = ('energy_reference', 'calculation_protocol', 'geometry_protocol') if dft else (
        'metric_definition', 'feed_composition', 'contact_time', 'reactor_protocol', 'material_state')
    missing = [CONTEXT_FIELDS[key][0] for key in fields if key not in context]
    raw_conditions = row['raw_record'].get('conditions', {})
    numeric = ('coverage_ml',) if dft else ('temperature_C', 'pressure_kPa')
    for key in numeric:
        if row['X_raw'].get(key) is None:
            missing.append(key)
    # Preserve ALL known operating conditions; no rounding, tolerance, unknown=0,
    # fuzzy matching, or unrecorded interpolation to make groups larger.
    signature = {'task_id': row['task_id'], 'unit': row['y_unit'], 'measurement_type': row['measurement_type'],
        'reviewed_context': {key: context[key]['value'] for key in fields if key in context},
        'numeric': {key: row['X_raw'].get(key) for key in numeric},
        'other_reported_conditions': {key: value for key, value in raw_conditions.items()
            if present(value) and key not in ('surface_site', 'calculation_method')},
    }
    return {'status': 'missing_context' if missing else 'eligible_for_exact_comparison',
        'missing': missing, 'group_id': None if missing else fingerprint(signature)[:16],
        'signature': signature, 'group_observations': 0, 'group_papers': 0, 'group_samples': 0,
        'rank': None, 'note': '只列出完整已核对条件严格相同的比较组；不自动排名，不据小分差宣称显著优越。'}


def score_encoding(result, config=None, annotations=None):
    import paper_encoding as encoding
    # Match the encoder's canonical serialization, not our configuration hash.
    if result.get('schema_version') != encoding.SCHEMA or encoding._fingerprint(
        {key: result[key] for key in SNAPSHOT_FIELDS}) != result.get('snapshot_fingerprint'):
        raise ValueError('编码预览已改变，请重新生成，再进行评分。')
    config = validate_config(default_config() if config is None else config)
    annotations = {} if annotations is None else annotations
    if not isinstance(annotations, dict):
        raise ValueError('评分补充须按观察编号保存。')
    cards, issues, groups = [], [], {}
    active_ids = {row['observation_id'] for row in result['observations']}
    for row in result['observations']:
        context, errors = checked_annotation(row, annotations.get(row['observation_id']))
        issues.extend({'observation_id': row['observation_id'], 'message': error} for error in errors)
        card = {'observation_id': row['observation_id'], 'record_id': row['record_id'],
            'doi': row['doi'], 'sample_label': row['sample_label'], 'experiment_id': row['experiment_id'],
            'task_id': row['task_id'], 'source_sha256': row['source_sha256'],
            'quality': _quality(row, context, config),
            'performance': _performance(row, config['profiles'][row['task_id']]),
            'comparison': _comparison(row, context), 'annotation_issues': errors,
            'reviewed_context': context, 'approximate': row['approximate'],
            'evidence': copy.deepcopy(row['evidence'])}
        cards.append(card)
        group = card['comparison']['group_id']
        if group:
            groups.setdefault(group, []).append(card)
    for members in groups.values():
        papers = {card['doi'] or card['source_sha256'] for card in members}
        samples = {(card['doi'] or card['source_sha256'], card['sample_label']) for card in members}
        for card in members:
            card['comparison'].update(group_observations=len(members), group_papers=len(papers), group_samples=len(samples))
    excluded = [{**{key: item.get(key, '') for key in ('record_id', 'sample_label', 'task_id', 'packet_path', 'code')},
        'quality_score': None, 'performance_score': None, 'status': 'gate_not_passed', 'reasons': item['reasons']}
        for item in result['excluded']]
    return {'schema_version': VERSION, 'config': config, 'config_id': fingerprint(config),
        'encoding_snapshot': result['snapshot_fingerprint'], 'cards': cards, 'excluded': excluded,
        'annotations': copy.deepcopy(annotations), 'annotation_issues': issues,
        'unused_annotation_ids': sorted(set(annotations) - active_ids),
        'summary': {'quality_scored': len(cards), 'performance_scored': sum(c['performance']['score'] is not None for c in cards),
            'comparison_groups': len(groups), 'groups_with_multiple_samples': sum(len({(c['doi'] or c['source_sha256'], c['sample_label']) for c in group}) > 1 for group in groups.values()),
            'excluded_not_scored': len(excluded)},
        'reference': REFERENCE, 'calibration_status': 'proposed_rubric_not_empirically_calibrated',
        'model_trained': False, 'scientific_evaluation_performed': False,
        'policy': ['质量与性能分开，未生成综合总分。', '评分未加入X，未替换y，也不按分数自动筛选训练数据。',
            '性能阈值固定在模板中，未用训练/测试数据的极值拟合。',
            '同一来源的文字、语义与图像证据合并追溯，不当作三次独立实验加分。',
            '高质量的低性能数据仍是有价值的训练样本。', '跨图、跨条件、跨论文的指标不自动拼成多目标综合分。']}


def write_score_files(folder, report):
    from paper_encoding import _write_json, _write_csv
    folder = Path(folder)
    _write_json(folder / '双评分明细.json', report)
    _write_json(folder / '评分模板.json', report['config'])
    _write_json(folder / '评分方案与补充.json', {'schema_version': VERSION,
        'config': report['config'], 'annotations': report['annotations']})
    quality, performance, checks = [], [], []
    for card in report['cards']:
        common = {k: card[k] for k in ('observation_id', 'doi', 'sample_label', 'experiment_id', 'task_id')}
        quality.append({**common, 'quality_score': card['quality']['score'], 'gate': card['quality']['gate'],
            'missing': card['quality']['missing'], 'config_id': report['config_id']})
        performance.append({**common, 'performance_score': card['performance']['score'],
            **{key: card['performance'][key] for key in ('value', 'unit', 'mode', 'status', 'formula', 'clipped')},
            **{'comparison_' + key: card['comparison'][key] for key in ('status', 'group_id', 'missing', 'group_observations', 'group_papers', 'group_samples')},
            'config_id': report['config_id']})
        for key, dimension in card['quality']['components'].items():
            for item in dimension['checks']:
                checks.append({**common, 'dimension': dimension['label'], 'dimension_contribution': dimension['contribution'], **item})
    _write_csv(folder / '数据质量分.csv', quality, ['observation_id', 'doi', 'sample_label', 'experiment_id', 'task_id', 'quality_score', 'gate', 'missing', 'config_id'])
    _write_csv(folder / '单项性能分.csv', performance, ['observation_id', 'doi', 'sample_label', 'experiment_id', 'task_id', 'performance_score',
        'value', 'unit', 'mode', 'status', 'formula', 'clipped', 'comparison_status', 'comparison_group_id', 'comparison_missing',
        'comparison_group_observations', 'comparison_group_papers', 'comparison_group_samples', 'config_id'])
    _write_csv(folder / '质量逐项依据.csv', checks, ['observation_id', 'doi', 'sample_label', 'experiment_id', 'task_id',
        'dimension', 'dimension_contribution', 'field', 'label', 'status', 'value', 'source', 'evidence_ids'])
    _write_csv(folder / '未评分原因.csv', report['excluded'], ['record_id', 'sample_label', 'task_id', 'packet_path', 'code', 'status', 'reasons'])


def describe_card(card, report):
    q, p, comp = card['quality'], card['performance'], card['comparison']
    lines = [f"{card['sample_label']} / {card['experiment_id']}　{card['doi']}",
        f"数据质量证据分：{q['score']:g}/100；{q['note']}"]
    for item in q['components'].values():
        lines.append(f"  {item['label']}：已记录 {item['documented']}/{item['applicable']} 项，贡献 {item['contribution']:.2f}/{item['weight_percent']:g} 分")
        for check in item['checks']:
            state = {'documented': '有资料', 'unknown': '待补', 'not_applicable': '不适用'}[check['status']]
            lines.append('    ' + state + ' · ' + check['label'])
    lines += ['', f"单项性能：{p['value']:g} {p['unit']}；性能分：" + ('暂不评分' if p['score'] is None else f"{p['score']:g}/100"),
        '目标：' + p['goal'], '公式：' + (p['formula'] or p['rationale']), p['note']]
    if comp['missing']:
        lines.append('暂不能作同条件比较，缺少：' + '；'.join(comp['missing']))
    else:
        lines.append(f"严格同条件组 {comp['group_id']}：{comp['group_observations']} 条观察，{comp['group_samples']} 个论文内样品，{comp['group_papers']} 篇论文。")
    lines += [comp['note'], '', '人工补充状态：' + ('；'.join(card['annotation_issues']) or '无失效补充'),
        '模板：' + report['config']['name'] + '；版本指纹 ' + report['config_id'][:16], '原文证据：']
    lines.extend(f"  {e['evidence_id']} · 第{e.get('page', '?')}页 · {e.get('quote', '')}" for e in card['evidence'])
    return '\n'.join(lines)
