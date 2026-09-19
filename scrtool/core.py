"""Small, explicit data model. Missing values never become zero."""
import csv
import hashlib
import json
import math
import re
from pathlib import Path

# Canonical property -> (category, unit). Feed concentrations remain separate.
FIELDS = {
    'si_content': ('composition', 'wt%'), 'al_content': ('composition', 'wt%'),
    'cu_content': ('composition', 'wt%'), 'isolated_cu_content': ('composition', 'wt%'),
    'si_al_ratio': ('composition', '1'), 'cu_al_ratio': ('composition', '1'),
    'cu_per_cage': ('composition', '1'),
    'isolated_cu_al_ratio': ('composition', '1'),
    'isolated_cu_per_cage': ('composition', '1'),
    'anr': ('condition', '1'),
    'critical_anr': ('performance', '1'),
    'hydrothermal_temperature': ('synthesis', 'degC'),
    'hydrothermal_time': ('synthesis', 'h'),
    'temperature': ('condition', 'degC'),
    'pressure': ('condition', 'bar'),
    'ghsv': ('condition', 'h^-1'),
    'no_inlet': ('condition', 'ppm'), 'no2_inlet': ('condition', 'ppm'),
    'nh3_inlet': ('condition', 'ppm'), 'o2_inlet': ('condition', '%'),
    'h2o_inlet': ('condition', '%'), 'so2_inlet': ('condition', 'ppm'),
    'time_on_stream': ('condition', 'h'),
    'no_conversion': ('performance', '%'), 'nox_conversion': ('performance', '%'),
    'nh3_conversion': ('performance', '%'), 'n2_selectivity': ('performance', '%'),
    'n2o_concentration': ('performance', 'ppm'),
    't50': ('performance', 'degC'), 't90': ('performance', 'degC'),
    'bet_surface_area': ('characterization', 'm2/g'),
    'pore_volume': ('characterization', 'cm3/g'),
    'pore_diameter': ('characterization', 'nm'),
    'crystallite_size': ('characterization', 'nm'),
    'acid_amount': ('characterization', 'mmol/g'),
    'activation_energy': ('characterization', 'kJ/mol'),
    'apparent_activation_energy': ('characterization', 'kJ/mol'),
    'active_site_distance': ('characterization', 'Å'),
    'active_site_type': ('characterization', 'text'),
    'acid_site_type': ('characterization', 'text'),
    'mo_oxidation_state': ('characterization', '1'),
    'coordination_number': ('characterization', '1'),
    'bond_distance': ('characterization', 'Å'),
    'turnover_frequency': ('performance', 's^-1'),
    'calcination_temperature': ('synthesis', 'degC'),
    'calcination_time': ('synthesis', 'h'), 'loading': ('composition', 'wt%'),
}
ALIASES = {
    'si_content': ['si content'], 'al_content': ['al content'], 'cu_content': ['cu content'],
    'si_al_ratio': ['si/al ratio'], 'cu_al_ratio': ['cu/al ratio'], 'cu_per_cage': ['cu-ion per cha cage'],
    'temperature': ['temperature', 'reaction temperature', '温度', '反应温度'],
    'ghsv': ['ghsv', '空速'],
    'no_conversion': ['no conversion', 'no转化率'],
    'nox_conversion': ['nox conversion', 'nox转化率'],
    'nh3_conversion': ['nh3 conversion'],
    'n2_selectivity': ['n2 selectivity', 'n2选择性'],
    'pressure': ['pressure', 'reaction pressure', '反应压力'],
    'bet_surface_area': ['bet surface area', 'surface area', 'sbet', '比表面积'],
    'pore_volume': ['pore volume', '孔容'], 'pore_diameter': ['pore diameter', '孔径'],
    'coordination_number': ['cn', 'coordination number'],
    'bond_distance': ['r', 'bond distance', 'interatomic distance'],
    'active_site_distance': ['active site distance'],
    'apparent_activation_energy': ['apparent activation energy'],
    'turnover_frequency': ['tof', 'tofs', 'turnover frequency'],
}
TRANS = str.maketrans('₀₁₂₃₄₅₆₇₈₉²³−⁻', '012345678923--')


def norm(s):
    return str(s).translate(TRANS).strip()


def uid(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def write_csv(path, rows, columns=None):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    columns = columns or sorted({k for r in rows for k in r}) or ['record_id']
    with Path(path).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in r.items()})


def quantity(raw, unit, prop):
    """Only exact scalar numbers normalize; inequalities/ranges stay in raw_value."""
    s = norm(raw)
    if FIELDS[prop][1] == 'text':
        return (s, None) if s else (None, 'non_scalar_or_missing')
    s = re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))', '', s)
    # Crystallographic notation such as 1.88(7) reports a central value with
    # uncertainty in the last digits. Keep the raw form and normalize its center.
    crystallographic = re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))\(\d+\)', s)
    if crystallographic:
        s = crystallographic.group(1)
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', s):
        return None, 'non_scalar_or_missing'
    value = float(s)
    u = norm(unit).lower().replace(' ', '').replace('°', 'deg')
    target = FIELDS[prop][1]
    accepted = {
        'degC': ['degc', 'c', '℃'], 'h^-1': ['h^-1', 'h-1', '1/h'],
        '%': ['%', 'percent', 'vol%'], 'ppm': ['ppm', 'ppmv'],
        'm2/g': ['m2/g', 'm2g-1'], 'cm3/g': ['cm3/g', 'cm3g-1'],
        'nm': ['nm'], 'mmol/g': ['mmol/g'], 'kJ/mol': ['kj/mol'],
        'h': ['h', 'hr', 'hours'], 'wt%': ['wt%', 'wt.%'], '1': ['1', 'ratio', 'dimensionless'],
        'bar': ['bar'], 'Å': ['å', 'angstrom'], 's^-1': ['s^-1', 's-1', '1/s'],
        'text': ['text'],
    }
    if target == 'degC' and u == 'k':
        value -= 273.15
    elif target == 'h' and u == 'min':
        value /= 60
    elif target == 'mmol/g' and u in ['umol/g', 'μmol/g', 'µmol/g']:
        value /= 1000
    elif u not in accepted[target]:
        return None, 'unknown_unit'
    if not math.isfinite(value):
        return None, 'non_finite'
    if target in ['%', 'wt%'] and not 0 <= value <= 100:
        return None, 'out_of_range'
    if target != 'degC' and value < 0:
        return None, 'out_of_range'
    if target == 'degC' and value < -273.15:
        return None, 'out_of_range'
    return value, None


def make_record(block, catalyst, prop, raw, unit, evidence, conditions=None, method='rules', experiment_id=None):
    if prop not in FIELDS:
        raise ValueError('Unknown property: ' + str(prop))
    if not isinstance(evidence, str) or not evidence or evidence not in block['text']:
        raise ValueError('Evidence must be an exact substring of its source block')
    value, issue = quantity(raw, unit, prop)
    issues = [issue] if issue else []
    if not catalyst:
        issues.append('missing_catalyst')
    if catalyst and norm(catalyst) not in norm(block['text']) and method not in {'mapped_table', 'nh3_scr_domain_rule'}:
        issues.append('catalyst_not_in_block')
    document_type = block.get('document_type')
    if document_type == 'review':
        issues.append('secondary_source_document')
    elif document_type == 'unknown':
        issues.append('unclassified_document')
    conditions = conditions or {}
    clean_conditions = {}
    for key, q in conditions.items():
        if key not in FIELDS or FIELDS[key][0] != 'condition' or not isinstance(q, dict):
            raise ValueError('Invalid condition: ' + str(key))
        cv, ci = quantity(q.get('raw_value', ''), q.get('unit', ''), key)
        ce = q.get('evidence', '')
        if not ce or ce not in block['text']:
            raise ValueError('Condition evidence is absent from source')
        clean_conditions[key] = {'value': cv, 'unit': FIELDS[key][1], 'raw_value': q.get('raw_value'), 'evidence': ce,
                                 'origin': q.get('origin', 'reported'), 'source_locator': q.get('source_locator', block['locator'])}
        if ci:
            issues.append('condition_' + key + '_' + ci)
    record = {
        'paper_id': block['paper_id'], 'source_id': block['source_id'],
        'source_file': block['source_file'], 'source_kind': block['kind'],
        'locator': block['locator'], 'block_id': block['block_id'],
        'catalyst': catalyst or None, 'property': prop, 'category': FIELDS[prop][0],
        'raw_value': str(raw), 'raw_unit': str(unit), 'value': value,
        'unit': FIELDS[prop][1], 'evidence': evidence, 'conditions': clean_conditions,
        'experiment_id': experiment_id, 'method': method, 'issues': issues,
        'estimated': block['kind'] == 'digitized_curve',
        'document_type': document_type,
        'document_type_confidence': block.get('document_type_confidence'),
        'training_eligible': block.get('training_eligible'),
        'source_row': block.get('row'),
        'review_status': 'pending',
    }
    record['record_id'] = uid(record)
    return record


def infer_header(header):
    text = re.sub(r'\s+', ' ', norm(header).lower())
    text = re.sub(r'(?<=[)])\s+[a-zªᵃ]$', '', text)
    text = re.sub(r'\s+[a-zªᵃ]\s*(?=[(\[])', ' ', text)
    text = re.sub(r'[ªᵃ]$', '', text).strip()
    # Delimit units explicitly to avoid silently assuming percent or Celsius.
    match = re.fullmatch(r'(.+?)\s*[\[(](.+?)[\])]', text)
    name, unit = (match.group(1).strip(), match.group(2)) if match else (text, '')
    for prop in FIELDS:
        if name in [prop, prop.replace('_', ' ')] + ALIASES.get(prop, []):
            return prop, unit or ('1' if FIELDS[prop][1] == '1' else '')
    return None, unit
