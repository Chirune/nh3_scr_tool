"""Review-only relations beyond a fixed vocabulary of performance measurements.

This rule-based inventory preserves comparisons, definitions and trends. It
does not resolve pronouns, evaluate equations, infer causal truth or create
absolute training labels. Evidence coordinates always address the input text.
"""
from __future__ import annotations

import re

from paper_semantics import MAX_TEXT_LENGTH, NUMBER, COUNT, _View, _float, _metrics, _samples, _scope, _spans, _without_expectation_aside

_UP = r'increas\w*|enhanc\w*|improv\w*|grow\w*|growth|rose|rises?|higher|greater|提升|提高|增加|增长|增大|上升|增强|改善|升高'
_DOWN = r'decreas\w*|reduc(?:e|es|ed|ing)\b|declin\w*|diminish\w*|fell|falls?|lower|降低|下降|减少|减小|衰减|减弱'
_TREND = re.compile(rf'\b(?:{_UP}|{_DOWN}|unchanged|remained\s+stable|correlat\w*|proportional)\b|{_UP}|{_DOWN}|不变|保持稳定|相关|正比|反比', re.I)
_MODAL = re.compile(r'\b(?:will|would|may|might|could|expected|expect|anticipated|predicted|simulated|potentially)\b|将(?:会|要)?|未来|有望|预计|预期|可能|预测|模拟', re.I)
_NEGATED = re.compile(r'\b(?:did|does|do|is|are|was|were|has|have)\s+not\b(?!\s+only)|'
                      r'\bno\s+(?:(?:significant|measurable|obvious)\s+)?(?:effect|change|difference|improvement|increase|decrease|correlation|NO\s*x?\s*conversion)\b|'
                      r'\bneither\b|没有|并未|未能|未(?:显著)?(?:提高|增加|降低|观察|变化)|无(?:明显|显著)?(?:变化|影响|相关)', re.I)
_CALC = re.compile(r'\b(?:quotient|divid(?:ed|ing)\s+by|calculated?\s+(?:as|according\s+to|using|from|by)|'
                   r'comput(?:ed|ing)\s+(?:from|using|by)|defined\s+as|formula|equation)\b|'
                   r'根据.{0,25}(?:计算|公式)|(?:计算|求得|算出).{0,20}(?:公式|比值)|公式|除以|之商|定义为', re.I)
_CAUSE = re.compile(r'\b(?:because|due\s+to|owing\s+to|attributed\s+to|leads?\s+to|results?\s+(?:in|from)|(?:are|is|were|was)\s+the\s+results?\s+of|'
                    r'can\s+be\s+explained\s+by|implies|affects?|limited\s+by|depends?\s+on|'
                    r'caus(?:es?|ed)|therefore|consequently)\b|由于|因为|因此|导致|归因于|源于|取决于|受.{0,12}限制|影响|说明', re.I)
_REQUIRED = re.compile(r'\b(?:is|are|was|were)\s+(?:demanded|required)\b|\b(?:target|required)\s+(?:conversion|selectivity|efficiency)\b|'
                       r'\bmust\s+(?:achieve|reach|exceed)\b|要求.{0,20}(?:达到|提高|降低)|目标.{0,12}(?:达到|为)', re.I)
_RECOMMENDED = re.compile(r'\b(?:should|recommended|advisable)\b|建议|应当|应予|需要.{0,12}(?:核验|核对|检查|调查)', re.I)
_CONDITIONAL = re.compile(r'\b(?:assuming|if|provided\s+that|when)\b|假定|假设|若|如果|仅当|当.{0,25}时', re.I)
_PRONOUN = re.compile(r'\b(?:it|its|this|these|they|them|that|those|such)\b|它|其|这(?:种|一|些|个)|上述|该(?:材料|体系|样品)', re.I)
_STRENGTH = re.compile(r'\b(?:significant(?:ly)?|substantial(?:ly)?|dramatic(?:ally)?|greatly|slightly|marked(?:ly)?)\b|显著|大幅(?:度)?|明显|大大|略微|稍微|轻微', re.I)
_REL_UNIT = r'%|％|percentage\s+points?|百分点|个百分点|percent\b'
_END_REF = r'[^,.;，。；]{1,130}'


def _clean(value):
    return re.sub(r'\s+', ' ', str(value)).strip(' ,，:：')


def _direction(text):
    if re.search(rf'{_DOWN}|低于|小于|更少|比.{{1,35}}(?:低|少)|\b(?:less|fewer)\b', text, re.I):
        return 'decrease'
    if re.search(rf'{_UP}|高于|大于|更多|比.{{1,35}}(?:高|多)|\bmore\b', text, re.I):
        return 'increase'
    if re.search(r'unchanged|stable|不变|稳定', text, re.I):
        return 'unchanged'
    return 'unknown'


def _metric(text):
    found = _metrics(text)
    if found:
        names = list(dict.fromkeys(m[2] for m in found))
        return names[0] if len(names) == 1 else ' / '.join(names)
    for pattern, metric in (
        (r'NO\s*[2₂]\s*/\s*NO\s*[xₓ]', 'NO2/NOx ratio'),
        (r'α|stoichiometric\s+ratio|当量比', 'NH3/NOx stoichiometric ratio'),
        (r'uniformity\s+index|\bUI\s+values?\b|均匀性|均匀度', 'NH3 uniformity index'),
        (r'(?:NH\s*[3₃]|ammonia)\s+concentration|氨浓度', 'NH3 concentration'),
        (r'NO\s*[xₓ]?\s+(?:concentration|emissions?|reduction)|氮氧化物浓度', 'NOx concentration / emission'),
        (r'ammonia\s+(?:maldistribution|distribution)|氨分布', 'NH3 distribution'),
        (r'mass\s+fraction|质量分数', 'mass fraction'),
        (r'flow\s+rate|mass\s+flow|流量', 'flow rate'),
        (r'reaction\s+rate|kinetic\s+factors|反应速率|动力学', 'reaction kinetics'),
        (r'catalytic\s+activit|催化活性', 'catalytic activity'),
        (r'efficiency|效率', 'efficiency'),
        (r'concentration|浓度', 'concentration'),
    ):
        if re.search(pattern, text, re.I):
            return metric
    return 'unknown'


def _quantity_unit(raw):
    return 'percentage points' if re.search(r'point|百分点|个百分点', raw, re.I) else '%'


def _reference_cutoff(text):
    """Recognize a bibliographic continuation page, not an in-text citation."""
    refs = re.search(r'(?im)^\s*(?:References|Bibliography|参考文献)\s*$', text)
    if refs:
        return refs.start()
    entries = list(re.finditer(r'(?m)^\s*\d{1,3}\.\s+[A-Z][A-Za-z´’\'-]+,\s*[A-Z]\.', text))
    if len(entries) >= 2 and entries[0].start() < 350 and re.search(r'\[CrossRef\]|\b(?:19|20)\d{2}\b', text):
        return entries[0].start()
    return len(text)


def _duplicate(candidate, existing):
    cstart, cend = candidate['start'], candidate['end']
    family = {'comparison', 'ratio', 'qualitative', 'negated', 'outlook', 'requirement', 'ambiguous', 'calculation'}
    for old in existing:
        if old.get('kind') not in family:
            continue
        a, b = old.get('start'), old.get('end')
        if not isinstance(a, int) or not isinstance(b, int):
            continue
        overlap = max(0, min(cend, b) - max(cstart, a))
        covers = overlap / max(1, cend - cstart) >= .85 and overlap / max(1, b - a) >= .85
        old_kind = 'requirement' if old.get('source_role') == 'requirement' else old.get('kind')
        same_kind = old_kind == candidate['kind'] or {old_kind, candidate['kind']} <= {'ratio', 'comparison'}
        if covers and same_kind:
            # A calculation/causal explanation added beside an old comparison
            # is new information, even if its sentence happens to coincide.
            new_types = {r['type'] for r in candidate['relations']}
            if not new_types.intersection({'calculation', 'explanation', 'constraint', 'correlation'}):
                return True
            old_types = {r.get('type') for r in old.get('relations', [])}
            if new_types <= old_types:
                return True
    return False


def relation_candidates(text: str, existing=None) -> list[dict]:
    """Return broader scientific relations, always pending human interpretation.

    Numerical amounts live inside ``relations`` only. The top-level value and
    value_high remain None for every kind, including explicitly stated ratios.
    Existing source-overlapping relations are not duplicated; existing absolute
    measurements do not suppress a distinct explanatory relation.
    """
    if not isinstance(text, str):
        raise TypeError('关系候选输入必须是字符串。')
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError('单次关系筛查最多 200 万字符，请按页处理。')
    existing = list(existing or [])
    results, seen, previous = [], {}, None
    limit = _reference_cutoff(text)
    for start, end, truncated in _spans(text[:limit]):
        # Drop only leading layout headers, retaining exact source offsets.
        raw = text[start:end]
        lead = re.match(r'(?im)^(?:Catalysts\s+\d{4}[^\n]*\n|[^\n]{0,70}FOR PEER REVIEW[^\n]*\n)', raw)
        if lead:
            start += lead.end()
        if start >= end:
            continue
        # Some PDFs place plot tick arrays between a calculation cue and the
        # equation. Keep the cue, not a thousand pixels' worth of tick text.
        chunk = text[start:end]
        tick = re.search(r'(?m)^\s*[-−]?\d+(?:\.\d+)?(?:\s+[-−]?\d+(?:\.\d+)?){4,}\s*$', chunk)
        if tick and _CALC.search(chunk[:tick.start()]):
            end = start + tick.start()
        view = _View(text, start, end)
        local = view.text.strip()
        if not local or not re.search(r'[A-Za-z\u4e00-\u9fff]', local):
            continue
        if re.match(r'^(?:Figure|Fig\.|Table|References|©|Copyright)\s', local, re.I):
            previous = (start, end)
            continue
        relations = []

        def rel(kind, m=None, **extra):
            ev = view.evidence(m.start(), m.end()) if m else view.evidence()
            relations.append(dict(type=kind, **ev, **extra))

        # Explicit "A exceeds B by ..." and percentage-point differences.
        cn = re.compile(rf'(?P<target>[^，。；\s比]{{1,35}})比(?P<reference>[^，。；\s高低多少]{{1,35}})(?P<direction>高|低|多|少)'
                        rf'(?:出|了)?\s*(?P<value>{COUNT})\s*(?P<unit>{_REL_UNIT})', re.I)
        for m in cn.finditer(local):
            unit = _quantity_unit(m['unit'])
            rel('relative_change' if unit == '%' else 'difference', m,
                target=_clean(m['target']), reference=_clean(m['reference']), value=_float(m['value']), unit=unit,
                direction='increase' if m['direction'] in ('高', '多') else 'decrease',
                ratio_definition='(target-reference)/reference' if unit == '%' else 'target-reference')
        en = re.compile(rf'(?P<value>{COUNT})\s*(?P<unit>{_REL_UNIT})\s*(?P<direction>higher|lower|greater|less|more|fewer)\s+than\s+(?P<reference>{_END_REF})', re.I)
        for m in en.finditer(local):
            prefix = re.split(r'[,;，；]', local[:m.start()])[-1]
            target = re.sub(r'\s+(?:is|was|were|are|of)\s*$', '', prefix, flags=re.I)
            unit = _quantity_unit(m['unit'])
            rel('relative_change' if unit == '%' else 'difference', m,
                target=_clean(target[-160:]), reference=_clean(m['reference']), value=_float(m['value']), unit=unit,
                direction='decrease' if m['direction'].lower() in ('lower', 'less', 'fewer') else 'increase',
                ratio_definition='(target-reference)/reference' if unit == '%' else 'target-reference')
        by = re.compile(rf'(?P<direction>increas\w*|decreas\w*|reduc\w*|rose|fell|提高|增加|下降|降低|减少)(?:\s+by|了)?\s*(?P<value>{COUNT})\s*(?P<unit>{_REL_UNIT})', re.I)
        for m in by.finditer(local):
            unit = _quantity_unit(m['unit'])
            rel('relative_change' if unit == '%' else 'difference', m,
                target='', reference='', value=_float(m['value']), unit=unit, direction=_direction(m['direction']),
                ratio_definition='(final-initial)/initial' if unit == '%' else 'final-initial')

        # Ratio direction and denominator are explicit; increased BY N-fold is
        # kept ambiguous instead of choosing N or N+1 as the final ratio.
        for m in re.finditer(rf'(?P<target>[^，。；\s是]{{1,35}})是(?P<reference>[^，。；\s的]{{1,35}})的\s*(?P<value>{COUNT})\s*倍', local):
            rel('ratio', m, target=m['target'], reference=m['reference'], value=_float(m['value']), unit='fold', ratio_definition='target/reference')
        for m in re.finditer(rf'(?P<target>[A-Za-z][\w/-]*)\s+(?:is|was|were|are)\s+(?P<value>{COUNT})\s+times\s+(?:as\s+(?:high|large|great)\s+as|that\s+of)\s+(?P<reference>{_END_REF})', local, re.I):
            rel('ratio', m, target=m['target'], reference=_clean(m['reference']), value=_float(m['value']), unit='fold', ratio_definition='target/reference')
        for m in re.finditer(rf'(?:提高|增加|增长|提升)(?P<link>了|到|至)\s*(?P<value>{COUNT})\s*倍|(?:increased|increases|higher)\s+(?P<enlink>by|to)\s*(?P<envalue>{COUNT})\s*(?:-?fold|times)', local, re.I):
            link = m['link'] or m['enlink']
            ambiguous = link in ('了', 'by')
            rel('ambiguous_fold_change' if ambiguous else 'ratio', m, target='', reference='',
                value=_float(m['value'] or m['envalue']), unit='fold', direction='increase',
                ratio_definition='unresolved_increase_by_fold' if ambiguous else 'final/initial')
        for m in re.finditer(rf'(?P<object>[A-Za-z][A-Za-z0-9₀-₉]*(?:\s+[0-9xₓ])?\s*/\s*[A-Za-z][A-Za-z0-9₀-₉]*(?:\s+[0-9xₓ])?)\s+(?:ratio|shares?)\s*(?:at\s+the\s+SCR\s+inlet\s+)?(?P<link>was|is|=|over|above|exceeds?|below)\s*(?P<value>{NUMBER})\s*(?P<unit>%)', local, re.I):
            label = re.sub(r'\s+', '', m['object'])
            sides = label.split('/', 1)
            bound = m['link'].lower()
            rel('ratio', m, target=_clean(sides[0]), reference=_clean(sides[1]), value=_float(m['value']), unit='%',
                ratio_definition='target/reference', operator='gt' if bound in ('over', 'above', 'exceed', 'exceeds') else 'lt' if bound == 'below' else 'eq')

        # A numeric before/after relation retains both values, without creating
        # one experimental row or guessing sample identities.
        for m in re.finditer(rf'(?P<direction>increas\w*|decreas\w*|changed|rose|fell)\s+from\s+(?P<before>{NUMBER})\s*(?P<unit1>%|ppm|°C)?\s+to\s+(?P<after>{NUMBER})\s*(?P<unit2>%|ppm|°C)', local, re.I):
            rel('change', m, source_value=_float(m['before']), target_value=_float(m['after']),
                unit=m['unit2'], source_unit=m['unit1'] or m['unit2'], direction=_direction(m['direction']), target='', reference='')

        calculation = bool(_CALC.search(local))
        formula_status = ''
        if calculation:
            # A bare reference such as "results shown in Fig.4" is not a
            # calculation rule; an explicit equation/definition is retained.
            meaningful = re.search(r'quotient|divid|defined|calculated\s+(?:as|according|using|from|by)|comput|=|公式|除以|定义|之商', local, re.I)
            if meaningful:
                # Presence of an expression is not proof of a complete or
                # correct formula, especially with a damaged PDF text layer.
                if re.search(r'\S\s*=\s*\S', local):
                    formula_status = 'expression_present_requires_review'
                elif re.search(r'quotient\s+of|divid(?:ed|ing)\s+by|除以|之商', local, re.I):
                    formula_status = 'textual_definition_requires_review'
                else:
                    formula_status = 'missing_or_unreadable'
                rel('calculation', expression=view.evidence()['evidence_quote'], evaluation_status='not_evaluated',
                    formula_status=formula_status,
                    formula_references=re.findall(r'(?:formula|equation|公式|式)\s*\(?\s*(\d{1,3})\s*\)?', local, re.I))
            else:
                calculation = False

        causal = _CAUSE.search(local)
        if causal:
            kind = 'constraint' if re.search(r'limited\s+by|取决于|受.{0,12}限制', local, re.I) else 'explanation'
            rel(kind, trigger=causal.group(), causal_status='author_statement_not_established_causality')
        correlation = re.search(r'\b(?:correlat\w*|proportional)\b|相关|正比|反比', local, re.I)
        if correlation:
            rel('correlation', trigger=correlation.group(), direction='negative' if re.search(r'negative|inverse|负相关|反比', local, re.I) else 'positive' if re.search(r'positive|正相关|正比', local, re.I) else 'unknown')
        qualitative_comparison = re.search(r'\b(?:higher|lower|greater|less|better|superior)\s+than\s+([^.;]{1,100})|比[^，。；]{1,45}(?:更|较)?(?:高|低|多|少|好)', local, re.I)
        if qualitative_comparison and not any(r['type'] in ('relative_change', 'difference', 'ratio') for r in relations):
            reference = qualitative_comparison.group(1) or ''
            # Re-read from the original comparison tail so decimal points are
            # part of the number, not mistaken for sentence punctuation.
            tail_start = qualitative_comparison.start(1) if qualitative_comparison.group(1) is not None else -1
            threshold = re.match(rf'(?P<value>{NUMBER})(?:\s*(?P<unit>%|ppm|°C|K\b))?(?!\w|\.\d)', local[tail_start:]) if tail_start >= 0 else None
            if threshold:
                metric = _metric(local)
                unit = threshold['unit'] or ('dimensionless' if metric in ('NH3 uniformity index', 'NH3/NOx stoichiometric ratio', 'NO2/NOx ratio') else '')
                comparison_ev = view.evidence(qualitative_comparison.start(), tail_start + threshold.end())
                relations.append(dict(type='constraint', subtype='numeric_threshold', **comparison_ev,
                                      value=_float(threshold['value']), unit=unit,
                                      operator='lt' if re.match(r'lower|less', qualitative_comparison.group(), re.I) else 'gt',
                                      target='', reference='', threshold_variable=metric))
            else:
                reference = re.split(r'\s+\b(?:should|must|needs?\s+to)\b', reference, maxsplit=1, flags=re.I)[0]
                rel('qualitative_comparison', qualitative_comparison, direction=_direction(qualitative_comparison.group()),
                    target='', reference=_clean(reference), value=None)
        trend_matches = [m for m in _TREND.finditer(local)
                         if not (m.group().lower() == 'reducing' and re.match(r'\s+agent\b', local[m.end():], re.I))]
        if trend_matches and not relations:
            strength = _STRENGTH.search(local)
            rel('trend', direction=_direction(local), strength=strength.group() if strength else '', magnitude_known=False)

        negated = bool(_NEGATED.search(local))
        modal = bool(_MODAL.search(_without_expectation_aside(local)))
        required = bool(_REQUIRED.search(local))
        recommended = bool(_RECOMMENDED.search(local))
        conditional = bool(_CONDITIONAL.search(local))
        clear_no_change = re.search(r'\bno\s+(?:significant\s+)?(?:effect|change|difference|correlation)\b|没有变化|无(?:明显)?变化', local, re.I)
        if not relations and negated and (_metric(local) != 'unknown' or clear_no_change):
            rel('negated_statement', magnitude_known=False)
        if not relations and required and _metric(local) != 'unknown':
            rel('requirement', magnitude_known=False)
        if not relations:
            previous = (start, end)
            continue

        types = {r['type'] for r in relations}
        kind = ('negated' if negated else 'requirement' if required or recommended else 'outlook' if modal else
                'ambiguous' if 'ambiguous_fold_change' in types else 'calculation' if calculation else
                'ratio' if types == {'ratio'} else 'comparison' if types.intersection({'ratio', 'relative_change', 'difference', 'change', 'qualitative_comparison'}) else 'qualitative')
        # "according to the formula" is a computation cue, not attribution
        # to another paper. Other attribution remains conservative.
        scope_text = re.sub(r'\baccording\s+to\s+(?:the\s+)?(?:formula|equation)\b', 'using the formula', local, flags=re.I)
        scope, warnings = _scope(scope_text)
        warnings.append('这是一条关系或趋势候选，未生成绝对实验数值，需人工确认含义与适用对象。')
        if 'relative_change' in types:
            warnings.append('相对百分比使用比较基准作分母；没有基准绝对值时不能反推绝对性能。')
        if 'difference' in types:
            warnings.append('百分点表示绝对差，与相对百分比不同；未假定基准数值。')
        if 'ambiguous_fold_change' in types:
            warnings.append('“提高了几倍/by N-fold”可有不同口径，未选择最终为N倍或N+1倍。')
        if calculation:
            warnings.append('保留计算关系及公式文字层；未求值，公式字符、分母和适用假设须对照原页。')
            if formula_status == 'missing_or_unreadable':
                warnings.append('本段只有计算或公式线索，未找到可辨认的公式表达式；公式可能缺失、位于图片或文字层截断，须回原页核对。')
        if recommended:
            warnings.append('本句属于建议或应当开展的核验，不是已完成的实测比较或测量结果。')
        if any(r.get('subtype') == 'numeric_threshold' for r in relations):
            warnings.append('比较对象是数值阈值，不是另一个样品；未把阈值当成实际测得值。')
        if negated:
            warnings.append('否定变化或假定无转化不等于测得零，未填零值。')
        if modal:
            warnings.append('含未来、预期、推测或模型措辞，不能当作已完成的实验结果。')
        if conditional:
            warnings.append('陈述依赖假设或条件，不能脱离条件泛化。')
        if truncated:
            warnings.append('长段已分段，计算关系或指代可能跨段，需查看完整原文。')
        metric = _metric(local)
        labels = list(dict.fromkeys(s[2] for s in _samples(local)))
        claim = 'negated' if negated else 'recommended' if recommended else 'required' if required else 'modal' if modal else 'conditional' if conditional else 'asserted'
        for r in relations:
            r['assertion'] = claim
        item = dict(**view.evidence(), metric=metric, kind=kind, value=None, value_high=None, unit='', operator='unknown',
                    sample_label=labels[0] if len(labels) == 1 else '', reference_sample='', conditions={},
                    assertion_scope=scope, review_status='unreviewed', numeric_eligible=False,
                    semantic_id_key=f'{metric}:{start}:broad_relation', relations=relations, context_evidence=[],
                    warnings=warnings, confidence_reasons=['原文具有明确的比较、计算、趋势或解释线索；规则提取仍需人工审核。'],
                    assertion_mode=claim, candidate_origin='relation_rules_v1')
        if previous and (_PRONOUN.search(local) or not item['sample_label']):
            pv = _View(text, previous[0], previous[1])
            item['context_evidence'].append(dict(type='previous_sentence', **pv.evidence(), binding_status='needs_review',
                                                 suggested_samples=list(dict.fromkeys(s[2] for s in _samples(pv.text)))))
            item['warnings'].append('前句仅供核对指代；未自动继承样品、条件或研究归属。')
        if _PRONOUN.search(local) and not item['sample_label']:
            item['unresolved_subject'] = True
        key = (kind, re.sub(r'\s+', '', local).casefold())
        if key in seen:
            seen[key].setdefault('duplicate_evidence', []).append(view.evidence())
        elif not _duplicate(item, existing):
            seen[key] = item
            results.append(item)
        previous = (start, end)
    return results
