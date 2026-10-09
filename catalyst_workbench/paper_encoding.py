"""Auditable, task-specific encoding of reviewed article handoff packets.

This module reads local sources, never edits a review, and never trains or
scores a model. Test fixtures are not demonstration scientific datasets.
"""
from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import html
import json
import math
from pathlib import Path
import re
import unicodedata
import uuid

from pypdf.errors import PyPdfError


HANDOFF_SCHEMA = 'stage3-evidence-handoff/1.0'
SCHEMA = 'stage3-task-encoding/1.0'
ELEMENTS = ('Li Be Na Mg Al K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Rb Sr Y Zr Nb Mo '
            'Tc Ru Rh Pd Ag Cd In Sn Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm '
            'Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po Fr Ra Ac Th Pa U Np Pu '
            'Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv').split()


def _field(label, kind, unit='', choices=None, minimum=None, maximum=None):
    value = {'label': label, 'kind': kind, 'unit': unit}
    if choices is not None:
        value['choices'] = list(choices)
    if minimum is not None:
        value['minimum'] = minimum
    if maximum is not None:
        value['maximum'] = maximum
    return value


FEATURE_SCHEMA = {
    'active_metals': _field('活性金属（元素符号列表）', 'multilabel', choices=ELEMENTS),
    'support': _field('载体类型', 'category', choices=[
        'unsupported', 'Al2O3', 'SiO2', 'TiO2', 'CeO2', 'ZrO2', 'CeO2-ZrO2',
        'Fe2O3', 'WO3', 'MnOx', 'zeolite', 'SAPO', 'carbon', 'fly_ash', 'other_reported']),
    'support_detail': _field('载体规范短标签（如 SSZ-13）', 'category'),
    'preparation_method': _field('制备方法', 'category', choices=[
        'impregnation', 'coprecipitation', 'sol_gel', 'hydrothermal', 'solvothermal',
        'deposition_precipitation', 'ion_exchange', 'combustion', 'physical_mixing',
        'other_reported']),
    'metal_loading_wt_pct': _field('总活性金属负载量', 'numeric', 'wt.%', minimum=0, maximum=100),
    'cu_zn_atomic_ratio': _field('Cu/Zn 原子比', 'numeric', 'Cu/Zn', minimum=0),
    'calcination_temperature_C': _field('焙烧温度', 'numeric', '°C', minimum=-273.15),
    'calcination_time_h': _field('焙烧时间', 'numeric', 'h', minimum=0),
    'reduction_temperature_C': _field('还原温度', 'numeric', '°C', minimum=-273.15),
    'BET_surface_area_m2_g': _field('BET 比表面积', 'numeric', 'm²/g', minimum=0),
    'surface_facet': _field('表面晶面（Miller 指数）', 'category'),
    'adsorption_site': _field('吸附位点', 'category', choices=[
        'top', 'bridge', 'hollow', 'fcc_hollow', 'hcp_hollow', 'metal_cation',
        'oxygen', 'vacancy', 'other_reported']),
    'dft_functional': _field('DFT 泛函', 'category', choices=[
        'PBE', 'PBE+U', 'PBE-D3', 'RPBE', 'PW91', 'BLYP', 'B3LYP',
        'HSE06', 'SCAN', 'r2SCAN', 'other_reported']),
    'coverage_ml': _field('吸附覆盖度', 'numeric', 'ML', minimum=0),
    'pd_loading_wt_pct': _field('Pd质量分数（相对完整催化剂）', 'numeric', 'wt.%', minimum=0, maximum=100),
    'ru_loading_wt_pct': _field('Ru质量分数（相对完整催化剂）', 'numeric', 'wt.%', minimum=0, maximum=100),
    'reduction_time_h': _field('还原时间', 'numeric', 'h', minimum=0),
    'metal_particle_size_nm': _field('活性金属粒径（保留测量方法与时点）', 'numeric', 'nm', minimum=0),
    'metal_dispersion_pct': _field('金属分散度（保留测量方法与时点）', 'numeric', '%', minimum=0, maximum=100),
    'ce3_fraction_pct': _field('Ce³⁺占总Ce比例（保留测量方法与时点）', 'numeric', '%', minimum=0, maximum=100),
    'zeolite_si_al_atomic_ratio': _field('分子筛Si/Al原子比', 'numeric', 'Si/Al', minimum=0),
    'pore_volume_cm3_g': _field('孔体积（保留测量方法与时点）', 'numeric', 'cm³/g', minimum=0),
}
CONDITION_SCHEMA = {
    'temperature_C': _field('反应温度', 'numeric', '°C', minimum=-273.15),
    'pressure_kPa': _field('压力（保留原文定义）', 'numeric', 'kPa', minimum=0),
    'space_velocity_h_inv': _field('空速（保持已核对定义）', 'numeric', 'h⁻¹', minimum=0),
    'flow_rate_ml_min': _field('气体流量', 'numeric', 'mL/min', minimum=0),
    'catalyst_mass_g': _field('催化剂用量', 'numeric', 'g', minimum=0),
    'feed_NO_ppm': _field('进料NO浓度', 'numeric', 'ppm', minimum=0, maximum=1e6),
    'feed_NO2_ppm': _field('进料NO₂浓度', 'numeric', 'ppm', minimum=0, maximum=1e6),
    'feed_NH3_ppm': _field('进料NH₃浓度', 'numeric', 'ppm', minimum=0, maximum=1e6),
    'feed_O2_vol_pct': _field('进料O₂体积分数', 'numeric', 'vol.%', minimum=0, maximum=100),
    'feed_H2O_vol_pct': _field('进料水蒸气体积分数', 'numeric', 'vol.%', minimum=0, maximum=100),
    'feed_SO2_ppm': _field('进料SO₂浓度', 'numeric', 'ppm', minimum=0, maximum=1e6),
    'h2_co2_molar_ratio': _field('进料H₂/CO₂摩尔比', 'numeric', 'H₂/CO₂', minimum=0),
    'reaction_time_h': _field('本性能点对应的反应/运行时间', 'numeric', 'h', minimum=0),
}
DFT_FIELDS = {'surface_facet', 'adsorption_site', 'dft_functional', 'coverage_ml'}
CHARACTERIZATION_FIELDS = {'BET_surface_area_m2_g', 'metal_particle_size_nm',
    'metal_dispersion_pct', 'ce3_fraction_pct', 'pore_volume_cm3_g'}
_ADDED_FIELDS = {'issues', 'doi', 'source_sha256', 'split_group', 'stage3_status', 'ml_ready'}
_IGNORED_TEXT = ['composition', 'preparation', 'characterization', 'sample_label',
                 'experiment_id', 'doi', 'notes', 'feed_description', 'surface_site',
                 'calculation_method', 'other']
_NIST_SI = 'https://www.nist.gov/pml/special-publication-330/sp-330-section-2'
# Both SI defining constants are exact; division by 1000 converts J to kJ.
EV_TO_KJ_MOL = float(Decimal('1.602176634e-19') * Decimal('6.02214076e23') / 1000)
UNIT_CONVERSIONS = {
    'percent_to_fraction': {'formula': 'y = raw_value / 100', 'factor': 0.01,
                            'basis': 'percent means one part per hundred'},
    'kJ_mol_to_eV': {'formula': 'y = raw_value / 96.4853321233100184',
                     'factor': 1 / EV_TO_KJ_MOL,
                     'e_C_exact': '1.602176634e-19', 'N_A_mol_inv_exact': '6.02214076e23',
                     'source': _NIST_SI,
                     'note': '仅换算单位；不更改吸附能正负号或推断能量定义。'},
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'))


def _fingerprint(value):
    return hashlib.sha256(_canonical(value).encode('utf8')).hexdigest()


def _digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _read_json(path):
    def invalid_constant(value):
        raise ValueError('JSON 不允许 NaN 或无穷大：' + value)
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'),
                       parse_constant=invalid_constant, parse_float=_number)
    if not isinstance(value, dict):
        raise ValueError('JSON 顶层必须为对象。')
    return value


def _number(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError('必须为有限数字，不能用布尔值、数组或对象代替。')
    if isinstance(value, str) and not value.strip():
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('必须为有限数字；缺失请留空。')
    return number


def _blank(value):
    return value is None or value == '' or value == []


def _short_identity(value):
    return re.sub(r'\s+', ' ', str(value or '').strip()).casefold()


def normalize_doi(value):
    """A DOI is a group identifier only, never an input feature."""
    if not value:
        return ''
    if not isinstance(value, str):
        raise ValueError('DOI 必须为文本。')
    doi = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)', '', value.strip(),
                 flags=re.I).strip().casefold()
    if not re.fullmatch(r'10\.\d{4,9}/[^\s]+', doi):
        raise ValueError('DOI 格式无效；请回到论文工作台核对，不能以任意标签分组。')
    return doi


def _canonical_feature(name, value, record):
    spec = FEATURE_SCHEMA.get(name) or CONDITION_SCHEMA[name]
    if value is None or value == '' or (spec['kind'] == 'multilabel' and value == []):
        return None
    if spec['kind'] == 'numeric':
        number = _number(value)
        if number is None:
            return None
        if 'minimum' in spec and number < spec['minimum']:
            raise ValueError('数值小于允许下限 ' + str(spec['minimum']))
        if 'maximum' in spec and number > spec['maximum']:
            raise ValueError('数值大于允许上限 ' + str(spec['maximum']))
        if name in {'pressure_kPa', 'space_velocity_h_inv', 'flow_rate_ml_min', 'catalyst_mass_g'} and number <= 0:
            raise ValueError('已报告的压力、空速、流量或质量必须大于 0；未报告请留空。')
        return number
    if spec['kind'] == 'multilabel':
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError('填写元素符号列表，不能填材料说明段落。')
        tokens = [v.strip() for v in value]
        if any(v not in spec['choices'] for v in tokens):
            raise ValueError('列表仅接受明确的金属元素符号，例如 Cu、Zn、Fe。')
        return sorted(set(tokens)) or None
    if not isinstance(value, str):
        raise ValueError('类别必须为规范短文本。')
    token = value.strip()
    if not token:
        return None
    if spec.get('choices') and token not in spec['choices']:
        raise ValueError('请选择已定义类别；未报告请留空。')
    if not spec.get('choices'):
        if name == 'support_detail' and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_+./()\-]{0,31}', token):
            raise ValueError('载体标签限 32 个字母/数字/符号，不接受空格、段落或 DOI。')
        if name == 'support_detail':
            known_material = bool(re.search(r'^(?:H-|Na-|Cu-|Fe-)?(?:SSZ|ZSM|SAPO|SBA|MCM|AlPO|KIT)[-]?\d', token, re.I)
                                  or token in {'CHA', 'BEA', 'FER', 'MOR', 'Beta', 'H-Beta', 'Y', 'HY', 'USY', 'NaY', 'carbon', 'fly_ash'}
                                  or ('O' in token and re.fullmatch(r'(?:[A-Z][a-z]?\d*)+(?:[-/](?:[A-Z][a-z]?\d*)+)*', token)))
            identity_tokens = {_short_identity(record.get(k)) for k in ('sample_label', 'experiment_id', 'doi')}
            if (re.fullmatch(r'(?:S|sample|cat|catalyst|exp|test)[_-]?\d+', token, re.I)
                    or re.search(r'conversion|selectivity|yield|performance|activity|^TOF$|^STY$', token, re.I)
                    or (_short_identity(token) in identity_tokens and not known_material)):
                raise ValueError('实验/样品编号、DOI、性能描述不能当载体类别；请填写规范材料标签。')
        if name == 'surface_facet':
            inner = token.strip('()[]{}').strip()
            if not re.fullmatch(r'-?\d(?:\s*-?\d){2,3}', inner):
                raise ValueError('晶面仅填写三位或四位 Miller 指数，例如 111、(1 1 1)。')
            token = re.sub(r'\s+', '', inner)
    return token


def feature_issues(record):
    """Validate explicit fields; missing values are reported separately, not invented."""
    issues = []
    availability = record.get('feature_availability', {})
    if (not isinstance(availability, dict) or set(availability) - CHARACTERIZATION_FIELDS or
        any(value not in ('unknown', 'before_prediction', 'after_prediction') for value in availability.values())):
        issues.append('表征获得时点必须逐项标为未知、预测前已知或预测后获得。')
    features = record.get('features')
    if features is None:
        features = {}
    if not isinstance(features, dict):
        return ['结构化材料特征 features 必须为对象。']
    for name in features:
        if name not in FEATURE_SCHEMA:
            issues.append('未定义的材料特征不会编码：' + str(name))
            continue
        try:
            _canonical_feature(name, features[name], record)
        except (ValueError, TypeError, OverflowError) as exc:
            issues.append(FEATURE_SCHEMA[name]['label'] + '：' + str(exc))
    conditions = record.get('conditions', {})
    if not isinstance(conditions, dict):
        issues.append('条件 conditions 必须为对象。')
    else:
        for name in CONDITION_SCHEMA:
            try:
                _canonical_feature(name, conditions.get(name), record)
            except (ValueError, TypeError, OverflowError) as exc:
                issues.append(CONDITION_SCHEMA[name]['label'] + '：' + str(exc))
        if 'x_value' in conditions:
            try:
                _number(conditions['x_value'])
            except (ValueError, TypeError, OverflowError) as exc:
                issues.append('保留横轴条件 x_value 必须为有限数字：' + str(exc))
    # These fields explicitly share a denominator. A individually valid value
    # can still make an impossible composition; do not silently renormalize it.
    def finite_values(mapping, names):
        try:
            return [_number(mapping.get(name)) for name in names]
        except (ValueError, TypeError, OverflowError):
            return None
    loadings = finite_values(features, ('pd_loading_wt_pct', 'ru_loading_wt_pct', 'metal_loading_wt_pct'))
    if loadings:
        pd, ru, total = loadings
        reported_sum = sum(v for v in (pd, ru) if v is not None)
        if reported_sum > 100 + 1e-9:
            issues.append('Pd与Ru的质量分数之和超过100%；请核对它们是否相对完整催化剂，未自动归一化。')
        if total is not None and reported_sum > total + 1e-9:
            issues.append('已报告的Pd/Ru质量分数之和大于总活性金属负载量；请核对共同分母与单位。')
    if isinstance(conditions, dict):
        volumes = finite_values(conditions, ('feed_O2_vol_pct', 'feed_H2O_vol_pct'))
        if volumes and sum(v for v in volumes if v is not None) > 100 + 1e-9:
            issues.append('同一进料的O₂与水蒸气体积分数之和超过100%；请核对条件，未自动缩放。')
    return issues


def prediction_available_facts(row):
    """Keep source facts intact while masking unconfirmed/post-outcome assays."""
    values = copy.deepcopy(row['X_raw'])
    unavailable = []
    availability = row.get('raw_record', {}).get('feature_availability', {})
    for name in CHARACTERIZATION_FIELDS & set(values):
        if values[name] is not None and availability.get(name) != 'before_prediction':
            unavailable.append({'field': name, 'value_preserved_in_facts': values[name],
                'availability': availability.get(name, 'unknown'),
                'reason': '尚未确认预测前可得；原事实保留，X预览中暂作缺失。'})
            values[name] = None
    return values, sorted(unavailable, key=lambda item: item['field'])


def _feature_specs(task_id):
    if task_id == 'nh3_adsorption_energy':
        return {k: v for k, v in FEATURE_SCHEMA.items()}
    conditions = {k: v for k, v in CONDITION_SCHEMA.items()
                  if not (task_id.startswith('scr_') and k == 'h2_co2_molar_ratio')
                  and not (task_id.startswith('cuzn_') and k.startswith('feed_'))}
    return {**{k: v for k, v in FEATURE_SCHEMA.items() if k not in DFT_FIELDS},
            **conditions}


def load_handoff(path):
    """Load one schema-checked packet; readiness is revalidated by build_encoding."""
    file = Path(path)
    if file.is_dir():
        file = file / '待编码包.json'
    packet = _read_json(file)
    if packet.get('schema_version') != HANDOFF_SCHEMA:
        raise ValueError('请选择 stage3-evidence-handoff/1.0 的待编码包.json。')
    if not isinstance(packet.get('article'), dict):
        raise ValueError('交接包缺少 article 来源对象。')
    for key in ('records_ready_for_standardization', 'records_waiting_for_review', 'evidence'):
        if not isinstance(packet.get(key), list) or not all(isinstance(v, dict) for v in packet[key]):
            raise ValueError('交接包字段 ' + key + ' 必须为对象列表。')
    if not isinstance(packet.get('review_events'), list) or not all(isinstance(v, dict) for v in packet['review_events']):
        raise ValueError('交接包缺少原始审核事件列表。')
    for row in packet['records_ready_for_standardization'] + packet['records_waiting_for_review']:
        if not isinstance(row.get('issues'), list) or not all(isinstance(v, str) for v in row['issues']):
            raise ValueError('统一记录 issues 必须保留为原因文本列表。')
        if not isinstance(row.get('conditions'), dict) or not isinstance(row.get('evidence_roles', {}), dict):
            raise ValueError('统一记录条件与证据用途必须为对象。')
        if not isinstance(row.get('evidence_ids'), list) or not all(isinstance(v, str) for v in row['evidence_ids']):
            raise ValueError('统一记录 evidence_ids 必须为编号列表。')
    if any(not isinstance(e.get('source_ref'), dict) for e in packet['evidence']):
        raise ValueError('原始证据 source_ref 必须保留为来源对象。')
    return packet


def _unique(items, key, label):
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError(label + '必须为对象列表。')
    values = {}
    for item in items:
        identity = item.get(key)
        if not isinstance(identity, str) or not identity:
            raise ValueError(label + '缺少唯一编号。')
        if identity in values:
            raise ValueError(label + '存在重复编号，不能选取最后一条覆盖前一条。')
        values[identity] = item
    return values


def _project_for_packet(packet, file):
    import paper_workspace as workspace
    pointer = packet.get('source_workspace')
    if pointer is not None and (not isinstance(pointer, dict) or not pointer.get('path')):
        raise ValueError('交接包的来源工作台指针无效。')
    path = Path(pointer['path']) if pointer else file.parent.parent / 'paper.json'
    if not path.is_absolute():
        path = file.parent / path
    project = workspace.load_project(path.resolve())
    if pointer and pointer.get('project_id') != project.get('project_id'):
        raise ValueError('交接包与当前论文工作台编号不匹配。')
    article = packet['article']
    actual = project.get('article', {})
    if article.get('source_sha256') != actual.get('source_sha256'):
        raise ValueError('交接包来源 PDF 与当前工作台不一致。')
    if normalize_doi(article.get('doi')) != normalize_doi(actual.get('doi')):
        raise ValueError('论文 DOI 已修改，请重新导出交接包。')
    source_hash = article.get('source_sha256', '')
    if not re.fullmatch(r'[a-fA-F0-9]{64}', str(source_hash)):
        raise ValueError('论文来源缺少有效 SHA256。')
    source_file = Path(article.get('local_pdf') or '')
    if not source_file.is_absolute():
        source_file = file.parent / source_file
    if _digest(source_file) != source_hash:
        raise ValueError('交接包关联 PDF 已改变，请重新核验并导出。')
    records = _unique(project.get('records', []), 'record_id', '当前统一记录')
    evidence = _unique(project.get('evidence', []), 'evidence_id', '当前证据')
    frozen = _unique(packet['evidence'], 'evidence_id', '交接包证据')
    _unique(packet['records_ready_for_standardization'] + packet['records_waiting_for_review'],
            'record_id', '交接包统一记录')
    return project, records, evidence, frozen, str(path.resolve())


def _image_index(project):
    from paper_image_bridge import read_image_evidence
    article = project['article']
    if not article.get('batch_path') or not article.get('paper_id'):
        return {}, ['图片证据缺少当前图片批次，无法验证来源是否过期。']
    result = read_image_evidence(article['batch_path'], article['paper_id'])
    return _unique(result['items'], 'evidence_id', '当前图片证据'), result['warnings']


def _metric_key(value):
    value = unicodedata.normalize('NFKC', str(value or '')).casefold()
    value = re.sub(r'\((?:%|fraction|ev|kj/mol|h\^-1|g/kgcat/h)\)', '', value)
    return re.sub(r'[^a-z0-9]', '', value)


def _image_metric_matches(entry, row):
    source, target = _metric_key(entry.get('metric')), _metric_key(row.get('metric'))
    if source == target:
        return True
    # Only explicit species-qualified aliases. An axis called simply
    # "conversion" cannot be resolved to NO, NOx or CO2 by the record form.
    aliases = {'noconversion': {'noconversionrate', 'conversionofno'},
               'noxconversion': {'noxconversionrate', 'conversionofnox'},
               'co2conversion': {'co2conversionrate', 'conversionofco2'},
               'n2selectivity': {'selectivityton2'},
               'methanolselectivity': {'ch3ohselectivity', 'selectivitytomethanol'},
               'methanolyield': {'ch3ohyield'},
               'methanolsty': {'ch3ohsty', 'methanolspacetimeyield'},
               'nh3adsorptionenergy': {'adsorptionenergyofnh3'}}
    return source in aliases.get(target, set())


def _source_page_text(project, number, cache):
    from pypdf import PdfReader
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ValueError('原文页码无效。')
    if 'reader' not in cache:
        cache['reader'] = PdfReader(project['article']['local_pdf'])
    if number > len(cache['reader'].pages):
        raise ValueError('原文页码超出 PDF。')
    if number not in cache:
        cache[number] = (cache['reader'].pages[number - 1].extract_text() or '')[:300000]
    return cache[number]


def _semantic_temperature_reasons(entry, row):
    """A reviewed label does not turn a bounded/approximate condition into a point."""
    conditions = entry.get('conditions') or {}
    if not isinstance(conditions, dict):
        return ['语义数值来源的温度条件格式无效。']
    if 'temperature' not in conditions:
        return []
    temperature = conditions['temperature']
    if not isinstance(temperature, dict):
        return ['语义数值来源的温度条件格式无效，不能推断精确摄氏温度。']
    # Legacy candidates stored only value/unit. Absence of the operator is
    # compatible with equality; an explicit unknown operator is not.
    if temperature.get('operator', 'eq') != 'eq' or temperature.get('value_high') is not None:
        return ['语义数值来源温度是范围、不等式或近似，不能改写为精确温度进入 X；'
                '需另行核对独立精确条件证据，本版不自动跨证据推导。']
    if temperature.get('unit') not in ('°C', '℃'):
        return ['语义数值来源尚不是明确的摄氏单点温度；需另行核对独立精确条件证据，'
                '本版不从未统一单位的温度自动推导。']
    try:
        original = _number(temperature.get('value'))
        recorded = _number(row.get('conditions', {}).get('temperature_C'))
        if original is None or recorded is None or not math.isclose(original, recorded, rel_tol=1e-12, abs_tol=1e-12):
            return ['语义数值来源的明确单点温度与记录温度不一致，不能仅在记录表改写实验条件。']
    except (ValueError, TypeError, OverflowError):
        return ['语义数值来源温度或统一记录温度不是有限数字。']
    return []


def _record_reasons(project, row, current, live_evidence, frozen, image_state, source_text_cache):
    import paper_workspace as workspace
    reasons = []
    if row.get('stage3_status') != 'ready_for_standardization' or row.get('issues') != []:
        reasons.append('交接包未将这条记录核验为可标准化。')
    if row.get('review_status') != 'reviewed' or not str(row.get('reviewer') or '').strip():
        reasons.append('缺少统一记录的人工确认或审核人。')
    if not current:
        reasons.append('当前工作台已不存在这条记录。')
    elif {k: v for k, v in row.items() if k not in _ADDED_FIELDS} != current:
        reasons.append('统一记录在导出后已改变，请重新核验并导出；旧包不进入编码。')
    if row.get('source_sha256') != project['article']['source_sha256']:
        reasons.append('统一记录的来源校验值与本篇不一致。')
    if normalize_doi(row.get('doi')) != normalize_doi(project['article'].get('doi')):
        reasons.append('统一记录 DOI 与本篇不一致。')
    if row.get('value_high') is not None:
        reasons.append('单点标签仍带有区间上限，需回到工作台核对。')
    try:
        if _number(row.get('value')) is None:
            reasons.append('标签缺少有限数值。')
        # Re-run the shared scientific and review rules; never trust ready flags.
        reasons.extend(workspace.record_issues(project, row))
        reasons.extend(feature_issues(row))
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        reasons.append('记录字段不完整或类型无效：' + str(exc))
    evidence_ids = row.get('evidence_ids')
    if not isinstance(evidence_ids, list) or not evidence_ids or not all(isinstance(e, str) for e in evidence_ids):
        return list(dict.fromkeys(reasons + ['记录缺少有效证据编号列表。']))
    if len(evidence_ids) != len(set(evidence_ids)):
        reasons.append('同一记录重复关联同一证据编号，请先整理。')
    roles = row.get('evidence_roles', {})
    if not isinstance(roles, dict) or any(v not in ('value', 'context') for v in roles.values()):
        reasons.append('证据用途无效，不能判断哪些是标签来源。')
        roles = {}
    for eid in evidence_ids:
        entry, live = frozen.get(eid), live_evidence.get(eid)
        if not entry or not live:
            reasons.append('交接包或当前工作台缺少关联证据。')
            continue
        if entry != live:
            reasons.append('关联证据在导出后已修改或重新审核，请重新导出。')
        if entry.get('review_status') != 'reviewed':
            reasons.append('原始证据未审核，不能凭交接包 ready 状态直接编码。')
        if entry.get('stale') or entry.get('source_ref', {}).get('source_sha256') != project['article']['source_sha256']:
            reasons.append('关联证据已过期或并非当前 PDF。')
        if entry.get('branch') not in ('text', 'semantic', 'image'):
            reasons.append('未知证据分支，未建立可追溯的审核规则。')
        if not str(entry.get('quote') or '').strip() or not entry.get('page'):
            reasons.append('证据缺少原文/读图定位或页码。')
        if entry.get('branch') == 'image':
            fresh = image_state[0].get(eid)
            if not fresh or fresh != entry or fresh.get('usable') is not True:
                reasons.append('图像来源、标定、点或导出已过期/不可用，需重新核验导出。')
        elif entry.get('branch') in ('text', 'semantic'):
            # Existing text evidence has exact extraction offsets. Validate them
            # against the current retained page; no paragraph is a model input.
            page = next((p for p in project.get('pages', []) if p.get('page') == entry.get('page')), None)
            start, end = entry.get('start'), entry.get('end')
            text = page.get('text', '') if page else ''
            if (not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int)
                    or isinstance(end, bool) or not 0 <= start < end <= len(text)
                    or text[start:end] != entry.get('quote')):
                reasons.append('原文证据与当前页文字及位置不一致，不能验证其来源。')
            else:
                try:
                    original = _source_page_text(project, entry['page'], source_text_cache)
                    if original[start:end] != entry['quote']:
                        reasons.append('原文证据与实际源 PDF 文字不一致，不能仅凭工作台缓存编码。')
                except (OSError, ValueError, KeyError, TypeError, IndexError, PyPdfError) as exc:
                    reasons.append('无法复验实际 PDF 的原文位置：' + str(exc))
            if not str(entry.get('reviewer') or '').strip():
                reasons.append('原文证据缺少审核人。')
        if roles.get(eid, 'value') == 'value' and entry.get('branch') in ('semantic', 'image'):
            # Quantitative semantic evidence cannot be rewritten into a different
            # target by changing the form alone. Context evidence stays context.
            if entry.get('branch') == 'semantic':
                reasons.extend(_semantic_temperature_reasons(entry, row))
            if ((entry.get('branch') == 'semantic' and entry.get('metric') != row.get('metric'))
                    or (entry.get('branch') == 'image' and not _image_metric_matches(entry, row))):
                reasons.append('数值来源指标与标签不同或无法明确等价，不能混用 NO/NOx 等任务。')
            source_sample = entry.get('sample_label') or (entry.get('series_label') if entry.get('branch') == 'image' else '')
            mapping_note = row.get('sample_mapping_note')
            if (source_sample and _short_identity(source_sample) != _short_identity(row.get('sample_label'))
                    and (not isinstance(mapping_note, str) or not mapping_note.strip())):
                reasons.append('原句/图例的样品标识与统一样品名不同；请填写样品映射依据并重新人工确认，不能静默改归属。')
            if entry.get('kind') == 'absolute' and entry.get('operator') == 'eq':
                try:
                    raw_y, _, _ = standardize_target(row['task_id'], entry.get('value'), entry.get('unit'))
                    y, _, _ = standardize_target(row['task_id'], row.get('value'), row.get('unit'))
                    if not math.isclose(raw_y, y, rel_tol=1e-12, abs_tol=1e-12):
                        reasons.append('原句/图像点的数值与标签不一致，请核对值与单位；未自动覆盖。')
                except (ValueError, TypeError, KeyError):
                    reasons.append('原句/图像点的单位或数值不能支持当前标签。')
            if entry.get('branch') == 'image' and entry.get('x_unit') in ('°C', '℃') and re.search(r'temperature|温度', str(entry.get('x_name', '')), re.I):
                try:
                    x = _number(entry.get('x_value'))
                    recorded = _number(row.get('conditions', {}).get('temperature_C'))
                    if x is None or recorded is None or not math.isclose(x, recorded, rel_tol=1e-12, abs_tol=1e-12):
                        reasons.append('图像横轴温度与统一记录温度不一致，需核对来源。')
                except (ValueError, TypeError):
                    reasons.append('图像横轴温度或统一条件无效。')
    return list(dict.fromkeys(reasons))


def standardize_target(task_id, value, unit):
    """Return (value, canonical_unit, audit); never infer a missing unit."""
    import paper_workspace as workspace
    task = workspace.TASKS.get(task_id)
    if not task or task_id == 'pending':
        raise ValueError('编码必须选择一个明确预测任务。')
    y = _number(value)
    if y is None or unit not in task['units']:
        raise ValueError('标签数值或单位不符合所选任务。')
    method = 'identity'
    factor = 1.0
    target_unit = unit
    if unit in ('%', 'fraction'):
        maximum = 100 if unit == '%' else 1
        if not 0 <= y <= maximum:
            raise ValueError('比例超出原单位有效范围，未截断或猜测单位。')
        target_unit = 'fraction'
        if unit == '%':
            factor, method = 0.01, 'percent_to_fraction'
    elif task_id == 'nh3_adsorption_energy':
        target_unit = 'eV'
        if unit == 'kJ/mol':
            factor, method = 1 / EV_TO_KJ_MOL, 'kJ_mol_to_eV'
    elif y < 0:
        raise ValueError('时空收率或 TOF 不能为负数，请核对原文。')
    return y * factor, target_unit, {'raw_value': value, 'raw_unit': unit,
                                    'target_unit': target_unit, 'factor': factor,
                                    'method': method}


def _normalize_observation(row, packet, file, evidence, task_id):
    y, unit, conversion = standardize_target(task_id, row['value'], row['unit'])
    specs = _feature_specs(task_id)
    features = row.get('features') or {}
    conditions = row.get('conditions') or {}
    values = {name: _canonical_feature(name, (features if name in FEATURE_SCHEMA else conditions).get(name), row)
              for name in specs}
    linked = [copy.deepcopy(evidence[e]) for e in row['evidence_ids']]
    approximate = any(e.get('branch') == 'image' or e.get('value_origin') == 'image_digitized_approximate'
                      for e in linked if row.get('evidence_roles', {}).get(e['evidence_id'], 'value') == 'value')
    source_hash = packet['article']['source_sha256'].lower()
    doi = normalize_doi(packet['article'].get('doi'))
    observation_id = _fingerprint([source_hash, row['record_id'], task_id])[:24]
    missing = [name for name, value in values.items() if value is None]
    return {
        'observation_id': observation_id, 'row_id': observation_id, 'record_id': row['record_id'], 'doi': doi,
        'source_sha256': source_hash, 'split_group': 'doi:' + doi if doi else 'sha256:' + source_hash,
        'task_id': task_id, 'metric': row['metric'], 'measurement_type': row['measurement_type'],
        'sample_label': row['sample_label'], 'experiment_id': row['experiment_id'],
        'X_raw': values, 'feature_values': copy.deepcopy(values), 'y': y, 'y_value': y,
        'y_unit': unit, 'target_unit': unit, 'raw_value': row['value'], 'raw_unit': row['unit'],
        'conversion': conversion,
        'approximate': approximate,
        'approximation_note': '图像读数近似，保留像素与标定来源；不是作者原始数据。' if approximate else '',
        'missing_fields': missing, 'missing_reason': row.get('missing_reason', ''),
        'raw_record': copy.deepcopy(row), 'evidence': linked,
        'packet_paths': [str(file)], 'reviewer': row['reviewer'],
    }


def _experiment_key(row):
    raw_conditions = row['raw_record'].get('conditions', {})
    conditions = {}
    redundant_temperature_axis = (raw_conditions.get('x_unit') in ('°C', '℃')
        and re.search(r'temperature|温度', str(raw_conditions.get('x_name', '')), re.I)
        and _number(raw_conditions.get('x_value')) is not None
        and _number(raw_conditions.get('temperature_C')) == _number(raw_conditions.get('x_value')))
    for key, value in raw_conditions.items():
        if (redundant_temperature_axis and key in ('x_name', 'x_value', 'x_unit')) or _blank(value):
            continue
        if key in CONDITION_SCHEMA:
            value = _canonical_feature(key, value, row['raw_record'])
        elif isinstance(value, str):
            value = _short_identity(value)
        if not _blank(value):
            conditions[key] = value
    return _canonical([row['split_group'], row['task_id'],
                       _short_identity(row['sample_label']),
                       _short_identity(row['experiment_id']), conditions])


def _excluded(path, row, code, reasons):
    return {'packet_path': str(path), 'record_id': row.get('record_id', ''),
            'task_id': row.get('task_id', ''), 'sample_label': row.get('sample_label', ''),
            'code': code, 'reasons': list(dict.fromkeys(reasons)), 'raw_record': copy.deepcopy(row)}


def _deduplicate(observations, excluded, audit):
    by_source = {}
    for row in observations:
        by_source.setdefault(row['source_sha256'], set()).add(row['doi'])
    blocked = set()
    for row in observations:
        dois = by_source[row['source_sha256']] - {''}
        if len(dois) > 1:
            blocked.add(row['observation_id'])
            excluded.append(_excluded(row['packet_paths'][0], row['raw_record'], 'conflicting_source_doi',
                                      ['同一个 PDF 来源被归入不同 DOI，先核对论文身份，防止跨划分泄漏。']))
        elif dois:
            row['split_group'] = 'doi:' + next(iter(dois))
    unique = {}
    for row in observations:
        if row['observation_id'] in blocked:
            continue
        key = row['observation_id']
        previous = unique.get(key)
        if previous:
            if previous['raw_record'] == row['raw_record'] and previous['evidence'] == row['evidence']:
                previous['packet_paths'] = sorted(set(previous['packet_paths'] + row['packet_paths']))
                audit.append({'code': 'repeated_record_ignored', 'observation_id': key,
                              'detail': '多个交接包包含同一已核验原记录，只计一条观察。'})
            else:
                blocked.add(key)
                for item in (previous, row):
                    excluded.append(_excluded(item['packet_paths'][0], item['raw_record'], 'conflicting_record_copy',
                                              ['同一个来源与记录编号对应不同内容，请核对不同工作台副本。']))
            continue
        unique[key] = row
    candidates = [r for key, r in unique.items() if key not in blocked]
    experiments, point_uses = {}, {}
    for row in candidates:
        experiments.setdefault(_experiment_key(row), []).append(row)
        for evidence in row['evidence']:
            if evidence.get('branch') not in ('semantic', 'image'):
                continue
            if row['raw_record'].get('evidence_roles', {}).get(evidence['evidence_id'], 'value') != 'value':
                continue
            point_uses.setdefault((row['source_sha256'], evidence['evidence_id']), []).append(row)
    reasons_by_id = {}
    for rows in experiments.values():
        if len(rows) < 2:
            continue
        same = (all(math.isclose(r['y'], rows[0]['y'], rel_tol=1e-12, abs_tol=1e-12) for r in rows)
                and all(r['X_raw'] == rows[0]['X_raw'] for r in rows))
        code = 'duplicate_experiment' if same else 'conflicting_experiment'
        detail = ('同一论文、样品、实验和条件重复；请在工作台合并证据并重审，未增加样本量。' if same else
                  '同一论文、样品、实验和条件对应冲突标签/结构化特征；未自动平均或挑选。')
        for row in rows:
            reasons_by_id.setdefault(row['observation_id'], []).append((code, detail))
    for rows in point_uses.values():
        if len(rows) > 1:
            for row in rows:
                reasons_by_id.setdefault(row['observation_id'], []).append((
                    'reused_value_evidence', '同一语义绝对值或图像点被赋给多条观察，不能靠改样品名或实验号复制样本。'))
    result = []
    for row in candidates:
        problems = reasons_by_id.get(row['observation_id'], [])
        if problems:
            excluded.append(_excluded(row['packet_paths'][0], row['raw_record'], problems[0][0], [v[1] for v in problems]))
        else:
            result.append(row)
    return sorted(result, key=lambda row: (row['split_group'], row['observation_id']))


def _split(observations, test_fraction, min_papers):
    groups = sorted({r['split_group'] for r in observations},
                    key=lambda group: _fingerprint(['stage3-fixed-group-split-v1', group]))
    if len(groups) < min_papers:
        assignments = {g: 'preview' for g in groups}
        status = 'preview_only'
        reason = f'只有 {len(groups)} 个独立论文组，少于本次划分门槛 {min_papers}；只生成编码预览，不构造测试集。'
    else:
        count = max(1, min(len(groups) - 1, math.ceil(len(groups) * test_fraction)))
        assignments = {g: 'test' if i < count else 'train' for i, g in enumerate(groups)}
        status = 'holdout_prepared'
        reason = '已按论文分组预留测试集；这只是划分操作门槛，不代表论文量或数据覆盖足以可靠评估。'
    for row in observations:
        row['split'] = assignments[row['split_group']]
    return {'status': status, 'reason': reason, 'group_count': len(groups),
            'group_assignments': assignments, 'test_fraction_requested': test_fraction,
            'min_papers': min_papers, 'strategy': 'deterministic_DOI_or_SHA256_groups',
            'train_groups': [g for g in groups if assignments[g] == 'train'],
            'test_groups': [g for g in groups if assignments[g] == 'test'],
            'preview_groups': [g for g in groups if assignments[g] == 'preview'],
            'model_trained': False, 'evaluation_performed': False}


def _encode(observations, task_id, split):
    specs = _feature_specs(task_id)
    fitting = [r for r in observations if r['split'] == 'train'] if split['status'] == 'holdout_prepared' else observations
    vocabulary, columns = {}, []
    for name, spec in specs.items():
        if spec['kind'] == 'numeric':
            columns.extend([name, name + '__missing'])
            continue
        tokens = set()
        for row in fitting:
            value = row['X_raw'][name]
            if value is not None:
                tokens.update(value if isinstance(value, list) else [value])
        vocabulary[name] = sorted(tokens)
        columns.extend(name + '=' + v for v in vocabulary[name])
        columns.extend([name + '__missing', name + '__unknown'])
    encoded = []
    for row in observations:
        values, unknown = {}, {}
        available, unavailable = prediction_available_facts(row)
        for name, spec in specs.items():
            value = available[name]
            missing = value is None
            if spec['kind'] == 'numeric':
                values[name] = value
                values[name + '__missing'] = int(missing)
                continue
            tokens = set(value if isinstance(value, list) else [] if missing else [value])
            for token in vocabulary[name]:
                values[name + '=' + token] = int(token in tokens)
            outside = sorted(tokens - set(vocabulary[name]))
            values[name + '__missing'] = int(missing)
            values[name + '__unknown'] = int(bool(outside))
            if outside:
                unknown[name] = outside
        encoded.append({'observation_id': row['observation_id'], 'split': row['split'],
                        'split_group': row['split_group'], 'X': values, 'y': row['y'],
                        'y_unit': row['y_unit'], 'unknown_categories': unknown,
                        'missing_fields': [name for name, value in available.items() if value is None],
                        'unavailable_characterization': unavailable})
    encoder = {'kind': 'one_hot_and_multi_hot', 'fit_scope': 'train_only' if split['status'] == 'holdout_prepared' else 'preview_only',
               'fit_observation_ids': [r['observation_id'] for r in fitting],
               'vocabulary': vocabulary, 'columns': columns,
               'missing_policy': 'numeric_null_preserved_with_indicator; category_missing_indicator',
               'unknown_policy': 'training_unseen_category_indicator_and_raw_token_audit',
               'imputation': 'none', 'scaling': 'none', 'ordinal_category_codes': False,
               'availability_policy': 'characterization_requires_explicit_before_prediction; raw_facts_preserved',
               'usage': 'inspection_preview_not_cross_validation_input',
               'warning': 'X为检查预览：数值缺失保留null，未确认预测前可得的表征值也暂作缺失。原值完整保存在事实表；正式比较请用第⑥页逐训练折编码。'}
    dictionary = []
    for name, spec in specs.items():
        dictionary.append({'field': name, **copy.deepcopy(spec), 'role': 'X',
                           'source': 'record.features' if name in FEATURE_SCHEMA else 'record.conditions',
                           'encoding': 'numeric_preserved' if spec['kind'] == 'numeric' else 'multi_hot' if spec['kind'] == 'multilabel' else 'one_hot',
                           'missing': 'null + missing indicator',
                           'fitted_categories': vocabulary.get(name, [])})
    dictionary.append({'field': 'y', 'role': 'y', 'label': task_id, 'kind': 'numeric',
                       'unit': observations[0]['y_unit'] if observations else '',
                       'source': 'record.value and record.unit', 'encoding': 'task_unit_conversion'})
    for field in _IGNORED_TEXT:
        dictionary.append({'field': field, 'role': 'audit_only', 'encoding': 'excluded_from_X',
                           'reason': '原文、标识、性能解释或尚未结构化条件，仅作来源与核验信息。'})
    return encoder, encoded, dictionary


def build_encoding(handoff_paths, task_id, *, test_fraction=0.2, min_papers=5):
    """Read current sources, standardize one task and fit encodings after grouping."""
    import paper_workspace as workspace
    if task_id not in workspace.TASKS or task_id == 'pending':
        raise ValueError('请选择明确预测任务；不同目标与实验/DFT 分开编码。')
    if isinstance(handoff_paths, (str, Path)):
        handoff_paths = [handoff_paths]
    if not 0 < test_fraction < 1 or isinstance(min_papers, bool) or not isinstance(min_papers, int) or min_papers < 2:
        raise ValueError('测试比例须在 0 与 1 之间，分组门槛须为至少 2 的整数。')
    observations, excluded, audit, inputs = [], [], [], []
    seen_files, seen_content = set(), set()
    for item in handoff_paths:
        file = Path(item)
        if file.is_dir():
            file = file / '待编码包.json'
        file = file.resolve()
        if str(file).casefold() in seen_files:
            audit.append({'code': 'repeated_packet_path_ignored', 'packet_path': str(file)})
            continue
        seen_files.add(str(file).casefold())
        packet = None
        observation_start, exclusion_start = len(observations), len(excluded)
        input_item = {'path': str(file), 'sha256': None}
        inputs.append(input_item)
        try:
            checksum = _digest(file)
            input_item['sha256'] = checksum
            packet = load_handoff(file)
            if checksum in seen_content:
                audit.append({'code': 'repeated_packet_content_ignored', 'packet_path': str(file)})
                continue
            project, records, live_evidence, frozen, project_path = _project_for_packet(packet, file)
            seen_content.add(checksum)
            image_state = _image_index(project) if any(e.get('branch') == 'image' for e in packet['evidence']) else ({}, [])
            source_text_cache = {}
            audit.append({'code': 'source_revalidated', 'packet_path': str(file),
                          'source_workspace': project_path, 'source_sha256': project['article']['source_sha256'],
                          'image_warnings': image_state[1], 'writes_to_source': False})
            if not packet['records_ready_for_standardization'] and not packet['records_waiting_for_review']:
                pending = _excluded(file, {}, 'no_unified_records', [
                    f"本包含 {len(packet['evidence'])} 条证据候选，但尚无统一记录。"
                    '请返回板块2，从所选证据建立统一记录，核验样品、数值单位和实验条件后重新导出。'])
                pending['entity_type'] = 'packet'
                excluded.append(pending)
            for row in packet['records_waiting_for_review']:
                excluded.append(_excluded(file, row, 'waiting_for_review',
                                          ['原交接包列为待核对，本模块不会自动提升审核状态。'] + list(row.get('issues') or [])))
            for row in packet['records_ready_for_standardization']:
                if row.get('task_id') != task_id:
                    excluded.append(_excluded(file, row, 'different_task', ['与本次任务不同，另行编码；不混指标或实验/DFT。']))
                    continue
                reasons = _record_reasons(project, row, records.get(row.get('record_id')), live_evidence, frozen, image_state, source_text_cache)
                if reasons:
                    excluded.append(_excluded(file, row, 'record_not_eligible', reasons))
                    continue
                try:
                    observations.append(_normalize_observation(row, packet, file, frozen, task_id))
                except (ValueError, KeyError, TypeError, OverflowError) as exc:
                    excluded.append(_excluded(file, row, 'normalization_failed', [str(exc)]))
        except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, PyPdfError) as exc:
            del observations[observation_start:]
            del excluded[exclusion_start:]
            rows = ((packet.get('records_ready_for_standardization', []) + packet.get('records_waiting_for_review', []))
                    if isinstance(packet, dict) else [])
            for row in rows or [{}]:
                excluded.append(_excluded(file, row, 'packet_source_invalid', [str(exc)]))
            audit.append({'code': 'packet_rejected', 'packet_path': str(file), 'reason': str(exc)})
    observations = _deduplicate(observations, excluded, audit)
    split = _split(observations, test_fraction, min_papers)
    encoder, encoded, dictionary = _encode(observations, task_id, split)
    missing_counts = {name: sum(row['X_raw'][name] is None for row in observations) for name in _feature_specs(task_id)}
    summary = {'observation_count': len(observations), 'excluded_count': len(excluded),
               'paper_count': split['group_count'], 'approximate_count': sum(r['approximate'] for r in observations),
               'train_count': sum(r['split'] == 'train' for r in observations),
               'test_count': sum(r['split'] == 'test' for r in observations),
               'preview_count': sum(r['split'] == 'preview' for r in observations),
               'column_count': len(encoder['columns']), 'unknown_row_count': sum(bool(r['unknown_categories']) for r in encoded),
               'missing_counts': missing_counts,
               'unavailable_characterization_count': sum(len(r['unavailable_characterization']) for r in encoded),
               'status': 'empty' if not observations else split['status'], 'model_trained': False,
               'evaluation_performed': False, 'reason': split['reason']}
    warnings = [split['reason'], encoder['warning'],
                '已编码字段不代表完整配方或计算描述；进料原文、能量定义及其他条件保留审核，未解析为类别。',
                '本模块不训练、不计算 R²，也不以测试夹具或演示数据冒充科研结果。']
    all_missing = [name for name, count in missing_counts.items() if observations and count == len(observations)]
    if all_missing:
        warnings.append('以下字段全部缺失：' + '、'.join(all_missing) + '；未从段落或性能结论推测。')
    result = {'schema_version': SCHEMA, 'created_at': datetime.now(timezone.utc).isoformat(),
              'task_id': task_id, 'task_title': workspace.TASKS[task_id]['title'],
              'input_packets': inputs, 'observations': observations, 'excluded': excluded,
              'field_dictionary': dictionary, 'split': split, 'encoder': encoder,
              'encoded_rows': encoded, 'audit': audit, 'summary': summary,
              'unit_conversions': copy.deepcopy(UNIT_CONVERSIONS), 'warnings': warnings,
              'scientific_evaluation': {'performed': False, 'scores': None}}
    result['snapshot_fingerprint'] = _fingerprint({k: result[k] for k in
        ('input_packets', 'observations', 'excluded', 'split', 'encoder', 'encoded_rows')})
    return result


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')


def _write_csv(path, rows, fields):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            clean = {}
            for key, value in row.items():
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False, allow_nan=False)
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                    value = "'" + value
                clean[key] = value
            writer.writerow(clean)


def write_export_overview(folder, result, scores, preparation):
    """A local, readable index over exported facts; never an accuracy report."""
    folder = Path(folder)
    cards = {card['observation_id']: card for card in scores['cards']}
    records = []
    for index, row in enumerate(result['observations'], 1):
        card = cards[row['observation_id']]
        raw = row['X_raw']
        _, masked = prediction_available_facts(row)
        records.append({'行号': index, '样品': row['sample_label'], '实验': row['experiment_id'],
            '目标指标': row['metric'], '原值': row['raw_value'], '原单位': row['raw_unit'],
            '统一值y': row['y'], '统一单位': row['y_unit'],
            '活性金属': raw.get('active_metals'), '载体': raw.get('support'),
            '温度_C': raw.get('temperature_C'), '压力_kPa': raw.get('pressure_kPa'),
            '空速_h_1': raw.get('space_velocity_h_inv'),
            '用途': {'train': '训练开发', 'test': '预留测试', 'preview': '仅检查预览'}[row['split']],
            '图像近似': '是' if row['approximate'] else '否',
            '质量证据分': card['quality']['score'], '单项性能分': card['performance']['score'],
            '同条件比较': '条件完整' if card['comparison']['group_id'] else '条件待补，暂不比较',
            '暂不进入X的表征': [FEATURE_SCHEMA[item['field']]['label'] for item in masked],
            '论文DOI': row['doi'], '页码': sorted({e['page'] for e in row['evidence']}),
            '证据编号': [e['evidence_id'] for e in row['evidence']], '观察编号': row['observation_id']})
    _write_csv(folder / '人工核对_逐行摘要.csv', records, list(records[0]))
    esc = lambda value: html.escape(str(value))
    def display(value):
        if value is None or value == []:
            return '<span class="muted">未报告 / 待补</span>'
        if isinstance(value, list):
            return esc('、'.join(str(v) for v in value))
        return esc(value)
    visible = ('行号', '样品', '原值', '原单位', '统一值y', '统一单位', '温度_C', '用途', '质量证据分', '单项性能分', '页码')
    table = '<table><thead><tr>' + ''.join('<th>' + esc(k) + '</th>' for k in visible) + '</tr></thead><tbody>'
    table += ''.join('<tr>' + ''.join('<td>' + display(row[k]) + '</td>' for k in visible) + '</tr>' for row in records)
    table += '</tbody></table>'
    links = [
        ('人工核对_逐行摘要.csv', '先看这一张', '样品、原值、统一值、条件、分数和来源放在同一行；含追溯列，不直接当作X。'),
        ('建模事实表_尚未拟合填补缩放.csv', '完整事实', '保留全部已核验值及获得时点；含DOI、目标与表征原值，正式建模需先按方案选列。'),
        ('机器学习准备包.json', '后续建模入口', '原事实、论文分组和模型编码方案；在每个训练折内学习填补、缩放与类别表。'),
        ('X.csv', '输入检查预览', '暂未填补/缩放。未确认预测前可得的表征值被屏蔽；不要用整批类别表直接做交叉验证。'),
        ('y.csv', '目标值', '一行对应一个统一单位后的实验/计算观察，不是预测值。'),
        ('行与来源对应.csv', '回到论文', '与X/y严格同序；提供DOI、来源哈希、样品和观察编号。'),
        ('数据质量分.csv', '质量证据分', '证据完整程度，既不是测量正确率，也不是模型准确率。'),
        ('单项性能分.csv', '单项性能分', '只按模板尺度描述这一项指标；条件不全不横向比较，不进入模型X。'),
        ('排除原因.csv', '尚未纳入的数据', '保留具体原因；回到第二板块核对后重新生成，未丢弃原审核记录。'),
    ]
    navigation = ''.join(f'<tr><td><a href="{esc(name)}">{esc(title)}</a></td><td>{esc(explanation)}</td></tr>'
                         for name, title, explanation in links)
    s = result['summary']
    tutorial = all(re.search(r'合成教学|synthetic.*fixture|fixture.*synthetic',
        str(row['raw_record'].get('notes', '')), re.I) for row in result['observations'])
    tutorial_notice = '<p class="note"><b>合成教学数据，仅演示操作，不是科研结果或模型效果证据。</b></p>' if tutorial else ''
    data = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>编码结果 · 先看这里</title>
<style>body{{font:16px/1.7 "Microsoft YaHei",sans-serif;color:#183d49;background:#eef4f6;margin:0}}main{{max-width:1280px;margin:auto;padding:32px}}h1{{font-size:30px}}h2{{font-size:22px}}section{{background:white;border-radius:12px;padding:22px;margin:20px 0}}.note{{background:#fff5d9;border-left:5px solid #df9a2a;padding:12px}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:10px;border-bottom:1px solid #dce7eb;text-align:left;vertical-align:top}}th{{background:#e5eff3}}a{{color:#00768d}}.muted{{color:#7a8489}}.scroll{{overflow:auto}}</style>
<main><h1>这一批数据整理到了哪一步</h1>{tutorial_notice}<p>{esc(result['task_title'])}</p>
<div class="note">已整理 {s['observation_count']} 条观察 / {s['paper_count']} 个论文组；未纳入 {s['excluded_count']} 条。<b>尚未训练模型，没有新的准确率或预测结果。</b>本页直接由本次导出生成。</div>
<section><h2>先核对一行，再理解三类文件</h2><p>材料与实验条件形成输入 X，统一单位后的性能形成目标 y，论文和证据负责追溯。同一来源的文字、语义和图像证据不当作三次实验。</p><p>百分数例如 90% 编码为 0.90 fraction，含义不变。CSV里的空格是未知，不是0。X预览屏蔽了 {s['unavailable_characterization_count']} 个获得时点未确认或反应后才得到的表征值；原值仍在事实表。</p><div class="scroll">{table}</div><p class="muted">本表供核对。质量分和性能分并排显示，二者未进入X，也未替代y。</p></section>
<section><h2>按用途打开文件</h2><table><thead><tr><th>文件用途</th><th>如何使用</th></tr></thead><tbody>{navigation}</tbody></table></section>
<section><h2>下一步：数据足够后才比较模型</h2><p>当前开发验证 {len(preparation['folds'])} 折，预留测试 {len(preparation['outer_test_groups'])} 个论文组。分组数量达到操作门槛不代表已经具备可靠的科学验证能力。</p><p>第三板块 → ⑥机器学习准备 → 选择预测场景和编码方案 → 预览 / 导出首个训练折。这里只做输入变换；随机森林、XGBoost等还没有训练。真实模型选择将比较相同论文分组下的误差；预留测试不参加选择。</p><p>DFT用于检验候选的微观假设。单独的吸附能不能代替对宏观转化率、选择性的实验验证。</p></section>
<p class="muted">编码快照：{esc(result['snapshot_fingerprint'])}<br>生成时间（UTC）：{esc(result['created_at'])}</p></main></html>'''
    (folder / '00_先看这里_编码结果.html').write_text(data, encoding='utf8')


def revalidate_snapshot(result):
    """Refuse stale previews before exporting facts or fitted fold transforms."""
    if result.get('schema_version') != SCHEMA:
        raise ValueError('不是本模块生成的编码结果。')
    if not result.get('observations'):
        raise ValueError('没有通过核验的观察，不能导出空白 X/y 冒充编码数据集。')
    refreshed = build_encoding([item['path'] for item in result['input_packets']], result['task_id'],
                               test_fraction=result['split']['test_fraction_requested'],
                               min_papers=result['split']['min_papers'])
    fingerprint = _fingerprint({k: result[k] for k in
        ('input_packets', 'observations', 'excluded', 'split', 'encoder', 'encoded_rows')})
    if fingerprint != result.get('snapshot_fingerprint') or refreshed['snapshot_fingerprint'] != fingerprint:
        raise ValueError('编码预览、交接包或来源在预览后已改变，请重新读取编码；未导出旧结果。')
    return refreshed


def export_encoding(result, output_root, *, scoring_config=None, scoring_annotations=None):
    """Export aligned X/y and provenance; recheck freshness before writing files."""
    refreshed = revalidate_snapshot(result)
    import paper_scoring
    scores = paper_scoring.score_encoding(refreshed, scoring_config, scoring_annotations)
    import ml_preparation
    preparation = ml_preparation.build_plan(refreshed)
    folder = Path(output_root).resolve() / ('编码_' + result['task_id'] + '_' +
              datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    folder.mkdir(parents=True, exist_ok=False)
    _write_json(folder / '编码结果.json', {**result, 'decision_scores': scores})
    paper_scoring.write_score_files(folder, scores)
    ml_preparation.write_preparation_files(folder, preparation)
    columns = result['encoder']['columns']
    _write_csv(folder / 'X.csv', [r['X'] for r in result['encoded_rows']], columns)
    _write_csv(folder / 'y.csv', [{'y': r['y']} for r in result['encoded_rows']], ['y'])
    mapping = [{'row_index_0_based': i, **{k: row[k] for k in
        ('observation_id', 'record_id', 'doi', 'source_sha256', 'sample_label', 'experiment_id', 'split_group', 'split', 'approximate')}}
        for i, row in enumerate(result['observations'])]
    _write_csv(folder / '行与来源对应.csv', mapping, list(mapping[0]))
    for subset in ('train', 'test', 'preview'):
        rows = [r for r in result['encoded_rows'] if r['split'] == subset]
        if not rows:
            continue
        _write_csv(folder / ('X_' + subset + '.csv'), [r['X'] for r in rows], columns)
        _write_csv(folder / ('y_' + subset + '.csv'), [{'y': r['y']} for r in rows], ['y'])
        indices = [{'subset_row_index_0_based': i, 'observation_id': r['observation_id']} for i, r in enumerate(rows)]
        _write_csv(folder / ('行对应_' + subset + '.csv'), indices, ['subset_row_index_0_based', 'observation_id'])
    normalized = [{**{k: v for k, v in row.items() if k != 'X_raw'}, **row['X_raw']} for row in result['observations']]
    _write_csv(folder / '标准化观察表.csv', normalized, list(normalized[0]))
    _write_csv(folder / '排除原因.csv', result['excluded'],
               ['packet_path', 'record_id', 'task_id', 'sample_label', 'code', 'reasons', 'raw_record'])
    dictionary_fields = ['field', 'role', 'label', 'kind', 'unit', 'source', 'encoding',
                         'choices', 'fitted_categories', 'minimum', 'maximum', 'missing', 'reason']
    _write_csv(folder / '字段字典.csv', result['field_dictionary'], dictionary_fields)
    _write_json(folder / '编码审计.json', {k: result[k] for k in
        ('schema_version', 'created_at', 'input_packets', 'snapshot_fingerprint', 'summary',
         'split', 'encoder', 'audit', 'unit_conversions', 'warnings', 'scientific_evaluation')})
    note = [result['task_title'], *result['warnings'], '',
            'X.csv 只有输入特征，y.csv 只有本任务统一单位的标签；两文件与行与来源对应.csv 的行序严格一致。',
            '数值空白表示 null；不是零，未进行填补。类别只用独热/多热，missing 与 unknown 独立标记。',
            'train/test 文件只在论文组达到本次划分门槛时生成；列定义只在 train 拟合。',
            'preview 文件只用于检查编码，使用全部预览行生成列定义，没有独立测试集。',
            '原值、单位、图像近似标记、原始记录、原文/像素来源和排除原因保存在编码结果.json。',
            '请在建模前核对配方、进料、空速定义、DFT 能量定义与论文覆盖，避免研究间不可比。',
            '数据质量分.csv 与单项性能分.csv 分开保存；这两类分数没有进入 X，也没有替代 y。',
            '质量分是当前证据完整度的建议量表，未进行真实人工标注集校准；不是测量正确率。',
            '性能分只按模板规定的单项指标和固定尺度计算；比较条件不全时不能横向排名。',
            '评分模板.json 与评分方案与补充.json 可在“质量分 / 性能分”页重新读取。',
            '后续模型比较请从机器学习准备包.json的事实层出发，在每个训练折内拟合类别表、填补与缩放；不要把当前整批X预览直接拿去交叉验证。',
            '默认配方与条件场景排除需测量的表征值；表征后预测仅使用明确在预测前已知的值。',
            'eV 与 kJ/mol 常数依据：' + _NIST_SI]
    (folder / '数据说明.txt').write_text('\n'.join(note) + '\n', encoding='utf8')
    write_export_overview(folder, result, scores, preparation)
    return {'directory': str(folder), 'path': str(folder / '编码结果.json'),
            'observation_count': len(result['observations']), 'excluded_count': len(result['excluded']),
            'status': result['summary']['status'], 'paper_count': result['split']['group_count'],
            'files': sorted(p.name for p in folder.iterdir())}
