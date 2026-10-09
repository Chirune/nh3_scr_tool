"""Small, conservative scientific facets layered onto source-backed candidates.

No labels are promoted to training data. A facet describes what a sentence
discusses, independently of whether it is future, negated or hypothetical.
This module neither resolves sample identities nor associates test conditions
with particular samples. It does not mutate the caller's candidate objects.
"""
from __future__ import annotations

import copy
import re

from paper_semantics import MAX_TEXT_LENGTH, NUMBER, _View, _float, _scope, _spans, _without_expectation_aside
from paper_relations import _reference_cutoff

FACET_LABELS = {
    'condition_dependence': '条件依赖',
    'optimum': '最佳区间 / 极值',
    'stability': '稳定性 / 循环',
    'deactivation': '失活 / 性能衰退',
    'recovery': '性能恢复 / 再生',
    'perturbation_tolerance': '水硫等扰动响应',
    'tradeoff': '多指标权衡',
    'uncertainty': '测量不确定性',
    'detection_limit': '检测限 / 未检出',
}
_PERFORMANCE = re.compile(r'\b(?:conversion|selectivity|yield|activity|activities|performance|stability|adsorption\s+capacity|efficiency|concentration|uniformity|signal)\b|转化率|选择性|收率|活性|性能|稳定性|吸附量|吸附容量|效率|浓度|均匀性|信号', re.I)
_MATERIAL = re.compile(r'\b(?:catalyst|material|sample|conversion|selectivity|yield|activity|adsorption\s+capacity)\b|催化剂|材料|样品|转化率|选择性|收率|活性|吸附容量', re.I)
_ENVIRONMENT = re.compile(r'\b(?:temperature|pressure|atmosphere|concentration|space\s+velocity|gas\s+composition|humidity)\b|温度|压力|气氛|浓度|空速|湿度', re.I)
_DEPEND = re.compile(r'\b(?:depend(?:s|ed)?\s+on|dependent\s+on|sensitive\s+to|affected\s+by|varied\s+with|increas\w*\s+with|decreas\w*\s+with|as\s+the\s+temperature)\b|随.{0,18}(?:升高|降低|变化|增加|减少)|依赖|取决于|受.{0,16}影响', re.I)
_PREP = re.compile(r'\b(?:calcin\w*|anneal\w*|dried|drying|synthesi\w*|prepar\w*|pretreat\w*)\b|煅烧|焙烧|干燥|制备|合成|退火|预处理', re.I)
_TEST = re.compile(r'\b(?:tested|measured|evaluated|reaction|exposed|feed|inlet|on[- ]stream)\b|反应|测试|测定|测量|暴露|进料|入口|运行', re.I)
_MODAL = re.compile(r'\b(?:will|would|may|might|could|expected|predicted|anticipated)\b|将(?:会|要)?|有望|预计|预期|预测|可能', re.I)
_NEG = re.compile(r'\b(?:did|does|do|was|were|is|are)\s+not\b(?!\s+only)|\bno\s+(?:significant\s+)?(?:deactivation|change|loss|recovery|effect|improvement)\b|'
                  r'\bno\s+(?:significant\s+)?(?:(?:chemical|sulfur|sulphur|SO2)\s+)?poisoning\b|'
                  r'没有|并未|未能|未(?:发生|观察到|恢复|提高|抑制|毒化|中毒)|无(?:明显|显著)?(?:失活|变化|恢复|影响)', re.I)
_HYP = re.compile(r'\b(?:if|assuming|provided\s+that|hypothetical)\b|假设|假定|如果|若|仅当', re.I)
_ADVICE = re.compile(r'\b(?:should|must|required|recommended)\b|建议|应当|要求', re.I)
_STABLE = re.compile(r'\b(?:stable|stability|maintain\w*|retain\w*|unchanged)\b|稳定|保持|维持|保留|不变', re.I)
_CYCLE = re.compile(rf'(?P<value>{NUMBER})\s*(?:cycles?|次循环|个循环|次(?:重复)?使用)', re.I)
_TIME = re.compile(rf'(?P<value>{NUMBER})\s*(?P<unit>hours?|hrs?|h\b|minutes?|mins?|min\b|seconds?|s\b|days?|d\b|小时|分钟|秒|天)', re.I)
_GAS = re.compile(r'(?<![A-Za-z0-9])(?:H\s*[2₂]O|SO\s*[2₂])(?![A-Za-z0-9])|\b(?:water|steam|sulfur|sulphur|moisture|humidity)\b|水蒸气|水汽|水分|加水|含水|湿度|二氧化硫|耐水|耐硫|硫中毒', re.I)
_RESPONSE = re.compile(r'\b(?:toleran\w*|resistan\w*|poison\w*|inhibit\w*|promot\w*|deactivat\w*|unaffected|suppres\w*|sensitive|maintain\w*|retain\w*)\b|耐受|耐水|耐硫|抗水|抗硫|抵抗|抑制|促进|中毒|不受影响|敏感|保持|维持|降低|下降', re.I)
_UNCERTAINTY = re.compile(r'\b(?:uncertaint\w*|standard\s+(?:deviation|error)|confidence\s+interval|error\s+bars?|measurement\s+errors?)\b|不确定度|标准差|标准误|置信区间|误差棒|测量误差', re.I)
_DETECTION = re.compile(r'\b(?:detection\s+limit|limit\s+of\s+detection|quantification\s+limit|limit\s+of\s+quantification|LOD|LOQ)\b|检测限|检出限|定量限', re.I)


def _perturbation_context(view):
    """Poisoning can be an object of resistance or history of recovery.

    Only an explicit adverse predicate licenses the inhibition direction.
    Polarity stays unresolved when denied or when several effects coexist.
    """
    text = view.text
    tolerance = re.search(r'\b(?:toleran\w*|resistan\w*|unaffected)\b|耐受|耐水|耐硫|抗水|抗硫|抗.{0,5}中毒|不受影响', text, re.I)
    criterion = re.search(r'\b(?:criteria|criterion|requirements?|prerequisites?|design\s+objectives?)\b|评价指标|评价标准|重要指标|重要标准|必要条件|要求', text, re.I)
    recovery = re.search(r'\b(?:recovered|restored|regained|recovery)\b|恢复|再生', text, re.I)
    inhibition = re.search(r'\b(?:inhibit(?:s|ed|ing)?|suppress(?:es|ed|ing)?|deactivat(?:es|ed|ing)|poison(?:ed|s))\b|'
                           r'(?:抑制|降低|削弱).{0,12}(?:活性|转化率|选择性|性能)|'
                           r'(?:活性|转化率|性能).{0,12}(?:下降|降低|损失)|发生(?:了)?硫中毒|被.{0,12}毒化', text, re.I)
    promotion = re.search(r'\b(?:promot(?:es?|ed|ing)|enhanc(?:es?|ed|ing))\b|促进.{0,12}(?:活性|转化率|性能)|'
                          r'(?:活性|转化率|性能).{0,12}(?:提高|增强)', text, re.I)
    denial = (bool(_NEG.search(text)) or bool(re.search(
        r'\bno\s+(?:significant\s+)?(?:(?:chemical|sulfur|sulphur|SO2)\s+)?poisoning\b|'
        r'未(?:被.{0,8})?(?:抑制|毒化)|不(?:具备|具有).{0,8}(?:耐|抗)|不(?:耐|抗)', text, re.I)))
    if tolerance and criterion:
        response, context, trigger = 'unknown', 'evaluation_criterion', tolerance
    elif recovery:
        response, context, trigger = 'unknown', 'recovery_context', recovery
    elif denial:
        response, context, trigger = 'unknown', 'negated_or_denied_effect', inhibition or tolerance
    elif inhibition and (tolerance or promotion):
        response, context, trigger = 'unknown', 'multiple_or_contrasting_effects', inhibition
    elif inhibition:
        response, context, trigger = 'inhibition', 'explicit_adverse_predicate', inhibition
    elif tolerance:
        response, context, trigger = 'reported_tolerance', 'tolerance_property', tolerance
    elif promotion:
        response, context, trigger = 'promotion', 'explicit_beneficial_predicate', promotion
    else:
        response, context, trigger = 'unknown', 'perturbation_mentioned_without_direction', None
    result = dict(response_type=response, response_context=context, tolerance_proven=False)
    if trigger:
        result['direction_evidence'] = view.evidence(trigger.start(), trigger.end())
    return result


def _assertion(text):
    flags = []
    if _NEG.search(text): flags.append('negated')
    if _MODAL.search(_without_expectation_aside(text)): flags.append('future_or_modal')
    if _HYP.search(text): flags.append('hypothetical')
    if _ADVICE.search(text): flags.append('recommended_or_required')
    return flags or ['asserted']


def _condition_role(text, start, end, multiple=False):
    """A nearby explicit action is required; topic words are not actions."""
    if multiple:
        return 'unknown', None
    actions = (
        ('preparation', r'\b(?:calcined|annealed|dried|pretreated|prepared|synthesized|synthesised)\b|焙烧|煅烧|干燥|退火|预处理|制备'),
        ('test', r'\b(?:tested|measured|evaluated|reacted|reaction|exposed|feed|inlet)\b|测试|测定|测量|反应|暴露|进料|入口'),
    )
    found = []
    for role, pattern in actions:
        for match in re.finditer(pattern, text[:start], re.I):
            tail = text[match.end():start]
            if len(tail) > 65:
                continue
            # "prepared a catalyst ... conversion window 300–600°C" is
            # performance context, not the preparation temperature.
            if role == 'preparation' and (_PERFORMANCE.search(tail) or re.search(r'\b(?:window|maintain\w*|exhibit\w*)\b|温窗|表现|保持', tail, re.I)):
                continue
            if re.search(r'\b(?:at|under|to|temperature|pressure|range|window)\b|在|于|温度|压力|至|范围', tail, re.I):
                found.append((match.end(), role, match.start(), match.end()))
        suffix = re.match(r'\s*(?:[)）]\s*)?(?P<action>' + pattern + ')', text[end:], re.I)
        if suffix:
            # Postposed actions are common in Chinese and copied protocols.
            found.append((end + suffix.end(), role, end + suffix.start('action'), end + suffix.end('action')))
    if not found:
        return 'unknown', None
    _, role, a, b = max(found)
    return role, (a, b)


def _role_clauses(text):
    """Split only separately stated preparation/test phases, not value lists."""
    separators = list(re.finditer(r'[,，;；]|\b(?:and|but|whereas|while)\b|\s+\+\s+|然后|随后', text, re.I))
    result, left = [], 0
    for i, split in enumerate(separators):
        next_edge = separators[i + 1].start() if i + 1 < len(separators) else len(text)
        before, after = text[left:split.start()], text[split.end():next_edge]
        if (_PREP.search(before) or _TEST.search(before)) and (_PREP.search(after) or _TEST.search(after)):
            result.append((left, split.start()))
            left = split.end()
    result.append((left, len(text)))
    return result


def _number_mentions(view):
    """Only explicitly unit-bearing experimental conditions, never graph ticks."""
    text, mentions = view.text, []
    patterns = [
        ('temperature', rf'(?P<value>{NUMBER})(?:\s*(?:–|—|-|to|至|到)\s*(?P<high>{NUMBER}))?\s*(?P<unit>°\s*C|℃|K\b)'),
        ('pressure', rf'(?P<value>{NUMBER})(?:\s*(?:–|—|-|to|至|到)\s*(?P<high>{NUMBER}))?\s*(?P<unit>kPa|MPa|bar|atm)\b'),
    ]
    for entity, pattern in patterns:
        for m in re.finditer(pattern, text, re.I):
            # Do not confuse heating rates with temperatures.
            if entity == 'temperature' and re.match(r'\s*/\s*(?:min|s|h)\b', text[m.end():], re.I):
                continue
            prefix = text[max(0, m.start() - 65):m.start()]
            suffix = text[m.end():m.end() + 35]
            if (not re.search(r'\b(?:at|above|below|between|from|to|of|temperature|pressure|range|window|tested|measured|calcined|reaction)\b|在|于|温度|压力|范围|温窗|升至|降至|达到|超过|低于|为', prefix, re.I)
                    and not re.match(r'\s*(?:calcined|annealed|dried|tested|measured|焙烧|煅烧|干燥|测试|测定)', suffix, re.I)):
                continue
            unit = re.sub(r'\s+', '', m['unit']).replace('℃', '°C')
            bound = re.search(r'\b(above|below)\s*$|(?P<cn>超过|低于)\s*$', prefix, re.I)
            bound_text = (bound.group(1) or bound.group('cn')).lower() if bound else ''
            op = 'range' if m.groupdict().get('high') else 'gt' if bound_text in ('above', '超过') else 'lt' if bound else 'eq'
            mentions.append(dict(type='condition_mention', entity=entity, role='unknown', value=_float(m['value']),
                                 value_high=_float(m['high']) if m.groupdict().get('high') else None,
                                 unit=unit, operator=op, binding_status='needs_review', sample_label='',
                                 _normalized_start=m.start(), _normalized_end=m.end(),
                                 **view.evidence(m.start(), m.end())))
    for m in re.finditer(rf'(?P<value>{NUMBER})\s*(?P<unit>ppm|vol\.?\s*%|%)\s*(?P<gas>SO\s*[2₂]|H\s*[2₂]O|NH\s*[3₃]|NO\s*[xₓ]?)(?![A-Za-z0-9])', text, re.I):
        following = text[m.end():m.end() + 65]
        if re.match(r'\s*(?:(?:conversion|selectivity|removal|efficiency|yield|uptake)\b|'
                    r'(?:(?:was|were|is|are)\s+)?(?:converted|removed|reduced)\b|转化率?|选择性|脱除率?|去除率?|效率|收率|吸附量)', following, re.I):
            continue
        gas_context = bool(re.search(r'\b(?:feed|inlet|gas\s+(?:mixture|composition|stream)|contain(?:s|ed|ing)?|consist(?:s|ed|ing)?\s+of|expos(?:ed|ure)\s+to)\b|进料|入口|混合气|气体组成|配气|通入|含有', text, re.I))
        if m['unit'].lower() != 'ppm' and not gas_context and not (m['unit'].lower().startswith('vol') and _TEST.search(text)):
            continue
        mentions.append(dict(type='condition_mention', entity='gas_composition', gas=re.sub(r'\s+', '', m['gas']),
                             role='unknown', value=_float(m['value']), value_high=None, unit=m['unit'], operator='eq',
                             binding_status='needs_review', sample_label='', _normalized_start=m.start(), _normalized_end=m.end(),
                             **view.evidence(m.start(), m.end())))
    windows = _role_clauses(text)
    counts = {}
    for row in mentions:
        window = next(((a, b) for a, b in windows if a <= row['_normalized_start'] < b), (0, len(text)))
        row['_window'] = window
        key = (window, row['entity'])
        counts[key] = counts.get(key, 0) + 1
    for row in mentions:
        # Explicit test/preparation wording is contextual evidence, not a
        # sample assignment. Multiple values within one clause stay unknown.
        a, b = row.pop('_window')
        role, trigger = _condition_role(text[a:b], row['_normalized_start'] - a, row['_normalized_end'] - a,
                                        multiple=counts[((a, b), row['entity'])] > 1)
        row['role'] = role
        row['assertion_status'] = _assertion(text[a:b])
        row['role_evidence'] = view.evidence(a, b)
        if trigger:
            row['role_trigger_evidence'] = view.evidence(a + trigger[0], a + trigger[1])
        row.pop('_normalized_start'); row.pop('_normalized_end')
    return mentions


def _facets(view):
    text, facets = view.text, []
    perf, material = bool(_PERFORMANCE.search(text)), bool(_MATERIAL.search(text))
    mentions = _number_mentions(view) if perf or material or _TEST.search(text) else []
    flags = _assertion(text)

    def add(family, parameters=None, label=None):
        if any(f['type'] == family for f in facets): return
        facets.append(dict(type=family, label=label or FACET_LABELS[family], **view.evidence(),
                           assertion_status=flags, parameters=parameters or {}, usable=False,
                           interpretation_status='needs_review'))

    if perf and _ENVIRONMENT.search(text) and _DEPEND.search(text):
        add('condition_dependence', {'association_status': 'reported_dependence_not_proven_causality'})

    extrema = list(re.finditer(r'\b(?:optimal|optimum|maximum|minimum|highest|lowest|best|peak\s+(?:activity|conversion|selectivity))\b|最优|最佳|最高|最低|最大|最小|活性峰值', text, re.I))
    extremum = extrema[0] if extrema else None
    window = re.search(r'\b(?:operating\s+window|temperature\s+window|performance\s+window)\b|活性温窗|反应温窗|最佳区间', text, re.I)
    if perf and (extremum or window):
        extrema_mentions = []
        for match in extrema:
            form = 'minimum' if re.search(r'minimum|lowest|最低|最小', match.group(), re.I) else 'maximum' if re.search(r'maximum|highest|最高|最大|峰值|peak', match.group(), re.I) else 'unspecified'
            extrema_mentions.append(dict(form=form, **view.evidence(match.start(), match.end())))
        directions = {m['form'] for m in extrema_mentions if m['form'] != 'unspecified'}
        maxmin = 'mixed_or_ambiguous' if len(directions) > 1 else next(iter(directions)) if directions else 'unspecified'
        add('optimum', {'reported_form': 'window' if window else 'extremum', 'extremum_type': maxmin,
                        'extrema_mentions': extrema_mentions,
                        'global_optimality_proven': False})

    durations, preparation_durations = [], []
    time_separators = list(re.finditer(r'(?<!\d),(?!\d)|[，;；]|\b(?:and|but|whereas|while)\b|然后|随后|并(?:且)?', text, re.I))
    for m in _TIME.finditer(text):
        left = max((s.end() for s in time_separators if s.end() <= m.start()), default=0)
        right = min((s.start() for s in time_separators if s.start() >= m.end()), default=len(text))
        timing_clause = text[left:right]
        preparation_only = bool(_PREP.search(timing_clause)) and not _STABLE.search(timing_clause)
        row = dict(value=_float(m['value']), unit=m['unit'], **view.evidence(m.start(), m.end()),
                   timing_context='preparation' if preparation_only else 'stability_statement' if _STABLE.search(timing_clause) and not _PREP.search(timing_clause) else 'unknown',
                   timing_evidence=view.evidence(left, right), assignment_status='needs_review')
        (preparation_durations if preparation_only else durations).append(row)
    cycles = [dict(value=_float(m['value']), unit='cycles', **view.evidence(m.start(), m.end())) for m in _CYCLE.finditer(text)]
    procedure_stability = bool(re.search(r'\b(?:stable\s+readings?|stabilization\s+time|stable\s+engine|stable\s+conditions?)\b|读数稳定|稳定发动机|稳定测试条件', text, re.I))
    if material and _STABLE.search(text) and (durations or cycles) and not procedure_stability:
        add('stability', {'durations': durations, 'cycles': cycles, 'time_zero_defined': False,
                          'excluded_preparation_durations': preparation_durations})

    if material and re.search(r'\b(?:deactivat\w*|activity\s+loss|loss\s+of\s+activity|catalyst\s+degradation)\b|失活|活性损失|活性衰退|催化剂衰减', text, re.I):
        add('deactivation', {'observed_status': 'requires_assertion_review'})
    if material and re.search(r'\b(?:(?:activity|conversion|performance|capacity)\s+(?:(?:was|were|is|can\s+be|could\s+be)\s+)?(?:(?:fully|completely|partially)\s+)?(?:recovered|restored|regained)|regenerat\w*\s+(?:the\s+)?catalyst|recovery\s+of\s+(?:activity|conversion|performance))\b|(?:活性|转化率|性能|容量).{0,12}恢复|再生.{0,8}催化剂|催化剂.{0,8}再生', text, re.I):
        add('recovery', {'recovery_baseline': 'not_assumed'})

    if material and (_GAS.search(text) or re.search(r'\bpoisoning\b|化学中毒', text, re.I)) and _RESPONSE.search(text):
        gases = list(dict.fromkeys(m.group() for m in _GAS.finditer(text)))
        add('perturbation_tolerance', {'perturbations': gases, **_perturbation_context(view)})

    metrics = []
    for pat, name in ((r'conversion|转化率','conversion'), (r'selectivity|选择性','selectivity'),
                      (r'activity|活性','activity'), (r'stability|稳定性','stability'),
                      (r'adsorption\s+capacity|吸附容量','adsorption capacity'), (r'yield|收率','yield')):
        if re.search(pat, text, re.I): metrics.append(name)
    trade = re.search(r'\b(?:trade[- ]?off|at\s+the\s+expense\s+of|compromise)\b|权衡|以.{0,15}为代价|牺牲', text, re.I)
    opposing = (re.search(r'increas|improv|enhanc|higher|提高|增加|增强|升高', text, re.I)
                and re.search(r'decreas|reduc(?:e|ed)|declin|lower|降低|减少|下降', text, re.I)
                and re.search(r'\b(?:but|while|whereas)\b|但是|但|却|然而|而', text, re.I))
    if len(metrics) >= 2 and (trade or opposing):
        add('tradeoff', {'metrics': metrics, 'pairing_status': 'needs_review', 'pareto_optimality_proven': False})

    plusminus = list(re.finditer(rf'(?P<center>{NUMBER})\s*±\s*(?P<error>{NUMBER})(?:\s*(?P<unit>%|ppm|°C|K\b|eV))?', text, re.I))
    if _UNCERTAINTY.search(text) or (plusminus and (perf or re.search(r'\b(?:measured|measurement|analyzer)\b|测量|分析仪', text, re.I))):
        error_type = ('standard_deviation' if re.search(r'standard\s+deviation|标准差', text, re.I)
                      else 'standard_error' if re.search(r'standard\s+error|标准误', text, re.I)
                      else 'confidence_interval' if re.search(r'confidence\s+interval|置信区间', text, re.I) else 'unspecified')
        vals = [dict(center=_float(m['center']), error=_float(m['error']), unit=m['unit'] or '', **view.evidence(m.start(), m.end())) for m in plusminus]
        add('uncertainty', {'uncertainty_type': error_type, 'reported_values': vals,
                            'distribution_assumed': False, 'replicate_count_assumed': False})

    absent = re.search(r'\b(?:not\s+detected|undetectable|below\s+(?:the\s+)?detection)\b|未检出|低于.{0,5}检出限', text, re.I)
    if _DETECTION.search(text) or (absent and re.search(r'\b(?:NO\s*[2₂xₓ]?|NH\s*[3₃]|concentration|signal|analyte)\b|浓度|信号|分析物|氨|氮氧化物', text, re.I)):
        limits = []
        for m in re.finditer(rf'(?:detection\s+limit|limit\s+of\s+detection|LOD|LOQ|检测限|检出限|定量限)(?:\s*(?:was|is|of|=|为|是))?\s*(?P<value>{NUMBER})\s*(?P<unit>ppm|ppb|%|mg/L|μg/L|ug/L)', text, re.I):
            limits.append(dict(value=_float(m['value']), unit=m['unit'], **view.evidence(m.start(), m.end())))
        add('detection_limit', {'detection_status': 'not_detected_or_below_limit' if absent else 'reported_limit',
                               'reported_limits': limits, 'zero_imputed': False})
    return facets, mentions


def enrich_candidates(text: str, candidates) -> list[dict]:
    """Return copied originals plus genuinely new facet-bearing candidates.

    Original IDs, values, kind, sample bindings and review states are unchanged.
    Added metadata never approves a record. New records are unreviewed, unusable
    as labels, and contain no top-level numeric value. Every attached subrecord
    carries its own exact source evidence, including contextual conditions.
    """
    if not isinstance(text, str): raise TypeError('科研关系输入必须是字符串。')
    if len(text) > MAX_TEXT_LENGTH: raise ValueError('单次科研关系筛查最多 200 万字符，请按页处理。')
    result = copy.deepcopy(list(candidates or []))
    original_count = len(result)
    previous = None
    limit = _reference_cutoff(text)
    for start, end, truncated in _spans(text[:limit]):
        raw = text[start:end]
        lead = re.match(r'(?im)^Catalysts\s+\d{4}[^\n]*\n', raw)
        if lead: start += lead.end()
        if start >= end: continue
        view = _View(text, start, end)
        local = view.text
        facets, mentions = _facets(view)
        if not facets and not mentions:
            previous = (start, end)
            continue
        # Existing precise candidates may be shorter than the sentence. These
        # are supplemental contextual mentions, never mutations of conditions.
        owners = [row for row in result[:original_count]
                  if isinstance(row.get('start'), int) and isinstance(row.get('end'), int)
                  and max(start, row['start']) < min(end, row['end'])]
        known_roles = {m['role'] for m in mentions}
        aggregate_role = next(iter(known_roles)) if len(known_roles) == 1 else 'unknown'
        roles = [dict(role='unknown' if f['type'] == 'stability' and aggregate_role == 'preparation' else aggregate_role,
                      facet_type=f['type'], **view.evidence()) for f in facets]
        if owners:
            for row in owners:
                for key, additions in (('semantic_facets', facets), ('semantic_roles', roles), ('conditions_mentions', mentions)):
                    target = row.setdefault(key, [])
                    for addition in additions:
                        signature = (addition.get('type', addition.get('facet_type')), addition['start'], addition['end'], addition.get('entity'), addition.get('value'))
                        if not any((old.get('type', old.get('facet_type')), old.get('start'), old.get('end'), old.get('entity'), old.get('value')) == signature for old in target):
                            target.append(copy.deepcopy(addition))
        elif facets:
            # Repeated wording still has a distinct source location. Keep
            # each occurrence; the review layer marks repeats without
            # inventing another experimental observation.
            flags = _assertion(local)
            scope, warnings = _scope(local)
            warnings += ['科研关系与陈述立场分别标注；本条未经人工核对，未生成可用的实验标签。',
                         '条件仅作为本句提及信息，未自动绑定材料、样品或某个性能数值。']
            if (len([m for m in mentions if m['entity'] == 'temperature']) > 1
                    and any(m['entity'] == 'temperature' and m['role'] == 'unknown' for m in mentions)):
                warnings.append('本句含多个温度，条件角色与对应关系暂记unknown，未自行选择或配对。')
            if truncated: warnings.append('证据段较长且已分段，需结合原页核对。')
            kind = ('negated' if 'negated' in flags else 'outlook' if 'future_or_modal' in flags
                    else 'requirement' if 'recommended_or_required' in flags else 'qualitative')
            item = dict(**view.evidence(), metric='unknown', kind=kind, value=None, value_high=None, unit='', operator='unknown',
                        sample_label='', reference_sample='', conditions={}, assertion_scope=scope,
                        assertion_status=flags, review_status='unreviewed', numeric_eligible=False, usable=False,
                        semantic_id_key=f'{start}:scientific_facets', candidate_origin='scientific_facets_v1',
                        semantic_facets=facets, semantic_roles=roles, conditions_mentions=mentions,
                        relations=[], context_evidence=[], warnings=warnings,
                        confidence_reasons=['由明确科研概念及关系线索提出补充候选，不代表规则已经证明该结论。'])
            if previous and re.search(r'\b(?:it|its|they|these|this)\b|它|其|该样品', local, re.I):
                pv = _View(text, previous[0], previous[1])
                item['context_evidence'].append(dict(type='previous_sentence', **pv.evidence(), binding_status='needs_review'))
            result.append(item)
        previous = (start, end)
    return result
