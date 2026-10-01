"""Configurable, evidence-linked literature priority scores, never acceptance decisions."""
from __future__ import annotations

import math
import re

from .core import uid

VERSION = 'nh3scr-priority-v1'
LABELS = {
    'reaction': 'NH3-SCR主题', 'material': '催化剂研究线索',
    'experimental': '实验研究线索', 'performance': '量化性能线索',
    'conditions': '反应条件线索', 'characterization': '结构表征线索',
}
DEFAULT_WEIGHTS = dict(reaction=40, material=15, experimental=15,
                       performance=10, conditions=10, characterization=10)
PATTERNS = {
    'reaction': r'\bNH\s*[3₃]\s*[-– ]?SCR\b|\bammonia[- ]SCR\b|selective\s+catalytic\s+reduction.{0,140}(?:NH\s*[3₃]|ammonia)|(?:NH\s*[3₃]|ammonia).{0,140}selective\s+catalytic\s+reduction|氨选择性催化还原|NH[3₃].{0,40}选择性催化还原',
    'material': r'\bcatalysts?\b|\bzeolites?\b|\bCu[-–/ ]CHA\b|催化剂|分子筛',
    'experimental': r'\b(?:experimentally|experimental|measured|measurements?|synthesi[sz]ed|prepared|tested|evaluated)\b|实验测量|制备了|合成了|测试了',
    'performance': r'(?:conversion|selectivity|转化率|选择性).{0,65}\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*%.{0,45}(?:conversion|selectivity|转化率|选择性)|\b(?:TOF|turnover frequency)\b.{0,65}\d',
    'conditions': r'\d+(?:\.\d+)?\s*(?:°\s*C|℃|ppm\b|h\s*[-⁻^]?\s*1\b)|\bGHSV\b|space velocity|\bANR\b|空速|进料比',
    'characterization': r'\b(?:EXAFS|XANES|XPS|XRD|EPR|BET|DRIFTS|FTIR|TEM|SEM)\b|electron\s+paramagnetic\s+resonance|X[- ]ray\s+(?:diffraction|photoelectron\s+spectroscopy|absorption)|(?:transmission|scanning)\s+electron\s+microscopy|active\s+sites?|coordination\s+number|acid\s+sites?|活性中心|配位数|酸位',
}


def validate_config(config=None):
    config = config or {'weights': DEFAULT_WEIGHTS}
    if not isinstance(config, dict) or set(config) - {'weights', 'version', 'note'}:
        raise ValueError('评分配置只支持 weights、version 和 note。')
    weights = config.get('weights', DEFAULT_WEIGHTS)
    if not isinstance(weights, dict) or set(weights) != set(DEFAULT_WEIGHTS):
        raise ValueError('评分权重必须包含：' + ', '.join(DEFAULT_WEIGHTS))
    clean = {}
    for key, value in weights.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('评分权重必须是有限的非负数：' + key)
        clean[key] = float(value)
    if sum(clean.values()) <= 0:
        raise ValueError('评分权重总和必须大于 0。')
    return {'version': VERSION, 'weights': clean}


def score_record(record, config=None):
    config = validate_config(config)
    fields = {k: str(record.get(k) or '') for k in ('title', 'abstract', 'keywords')}
    complete = bool(fields['abstract'].strip()) and record.get('abstract_is_full') is not False
    if record.get('abstract_extraction_status') in {'failed', 'fallback_first_pages'} or record.get('needs_ocr'):
        complete = False
    # Search-page snippets and PDF first-page fallbacks are not abstract evidence.
    if not complete:
        fields['abstract'] = ''
    components = {}
    total = sum(config['weights'].values())
    for key, pattern in PATTERNS.items():
        evidence = []
        for name, text in fields.items():
            match = re.search(pattern, text, re.I | re.S)
            if match:
                evidence.append({'field': name, 'quote': text[match.start():match.end()]})
        weight = config['weights'][key]
        components[key] = {'label': LABELS[key], 'weight': weight,
                           'matched': bool(evidence), 'evidence': evidence,
                           'contribution': round(100 * weight / total if evidence else 0, 4)}
    return {'priority_score': round(sum(c['contribution'] for c in components.values()), 2),
            'score_components': components, 'score_config': config,
            'score_config_id': uid(config), 'score_basis': 'title_abstract_keywords' if complete else 'title_keywords_only',
            'score_status': 'heuristic_not_calibrated',
            'score_note': '用于阅读顺序；关键词线索不证明实验归属，评分未经过人工标注集校准。'}
