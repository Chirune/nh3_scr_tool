"""Conservative, source-aligned scientific quantities for a paper inventory.

These are review candidates, never training labels. Only explicit field/unit
constructions and two small table layouts are supported; numbers in plots,
references and sample codes are not observations. No cross-sentence binding,
unit repair, model call or derived concentration is performed here.
"""
from __future__ import annotations

import re

from paper_semantics import MAX_TEXT_LENGTH, NUMBER, _View, _float, _spans

_N = NUMBER
_TEMP = r"(?:°\s*C|℃|K\b|8C\b)"
_FLOW = r"(?:m[lL]\s*/\s*min|m[lL]\s*min\s*[-−⁻]1)"
_REFS = re.compile(r"(?im)^\s*(?:References|Bibliography|参考文献)\s*$")
_MODAL = re.compile(r"\b(?:may|might|could|would|expected|predicted|simulated|for\s+instance)\b|预计|预测|模拟|可能", re.I)
_REQUIRED = re.compile(r"\b(?:demanded|required|requirements?|targets?|must|shall|able\s+to\s+achieve)\b|要求|目标", re.I)
_PRIOR = re.compile(r"\b(?:previous\s+(?:work|studies|study)|reported\s+by|according\s+to|et\s+al)\b|前人|据报道", re.I)
_CURRENT = re.compile(r"\b(?:in\s+(?:this|our)\s+(?:study|work|experiment)|we\s+(?:measured|found|observed|prepared))\b|本研究|本工作", re.I)


def _normal(raw):
    return re.sub(r"\s+", " ", _View(raw, 0, len(raw)).text).strip().casefold()


def _unit(raw):
    raw = re.sub(r"\s+", "", raw)
    if re.fullmatch(r'm[lL]min[-−⁻]1', raw):
        return 'mL/min'
    return {"℃": "°C", "ml/min": "mL/min", "mL/min": "mL/min",
            "ml": "mL", "ml/g": "mL/g", "m2/g": "m²/g", "m²/g": "m²/g",
            "cm3/g": "cm³/g", "cm³/g": "cm³/g", "Nm": "N·m"}.get(raw, raw)


def numeric_facts(text: str) -> list[dict]:
    """Return an unreviewed inventory, with offsets into the untouched input.

    Extra provenance keys: source_role, context_evidence, uncertainty,
    table_header_evidence, duplicate_evidence and suggested_feature_key.
    ``field_key`` suggests a condition field, but does not attach it to a
    sample. ``numeric_eligible`` is false even for fully explicit table rows.
    """
    if not isinstance(text, str):
        raise TypeError('数值候选输入必须是字符串。')
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError('单次数值提取文本过长，请按页处理（最多 200 万字符）。')
    refs = _REFS.search(text)
    limit = refs.start() if refs else len(text)
    view = _View(text, 0, limit)
    normal = view.text
    facts = []
    sentences = list(_spans(text[:limit]))

    def context(start, end):
        for a, b, _ in sentences:
            if a <= start < b:
                # Context is evidence for review, never inherited identity.
                return dict(start=a, end=b, evidence_quote=text[a:b])
        return dict(start=start, end=end, evidence_quote=text[start:end])

    def add(ev, metric, label, value, unit, role, *, high=None, operator='eq',
            sample='', field='', source_role='reported_quantity', warnings=None,
            header=None, feature='', uncertainty=None):
        if value is None or (high is not None and not isinstance(high, (float, int))):
            return
        ctx = context(ev['start'], ev['end'])
        # A table row's scope cannot come from a neighboring prose paragraph.
        ctxt = _View(text, ev['start'], ev['end']).text if header else _View(text, ctx['start'], ctx['end']).text
        scope = 'current_study' if _CURRENT.search(ctxt) else 'unknown'
        warn = list(warnings or [])
        if _PRIOR.search(ctxt):
            scope, role, source_role = 'prior_work', 'background', 'prior_work'
            warn.append('本句引用前人工作，未当成本研究的实测结果。')
        # Required duration of a preparation step is not a performance target.
        if role in ('other_result', 'measurement') and _REQUIRED.search(ctxt):
            role, source_role = 'background', 'requirement_or_general_claim'
            warn.append('这是目标、要求或一般能力描述，不是本研究的实测性能。')
        elif _MODAL.search(ctxt) and role != 'preparation':
            role, source_role = 'background', 'modal_or_model_statement'
            warn.append('本句含推测、示例或模型措辞，保留原值供核对，不当作实测结果。')
        if unit == '8C':
            field = ''
            warn.append('PDF文字层把温度单位写成8C；保留原文，必须查看原页确认，未自动改为°C。')
        if operator != 'eq':
            warn.append('保留近似值、范围或边界含义，不能按精确单点使用。')
        if not sample:
            warn.append('本条未明确绑定唯一催化剂样品；需确认其属于哪个实验或装置。')
        if high is not None and high < value:
            warn.append('上下界顺序异常，未自动交换数值。')
        item = dict(**ev, kind='table_value' if header else 'numeric_fact',
                    metric=metric, metric_label=label, value=float(value),
                    value_high=float(high) if high is not None else None,
                    unit=unit, operator=operator, quantity_role=role,
                    field_key=field, sample_label=sample, conditions={},
                    assertion_scope=scope, review_status='unreviewed',
                    numeric_eligible=False, source_role=source_role,
                    warnings=warn, context_evidence=[ctx], source_table='')
        if header:
            item['table_header_evidence'] = header
            captions = list(re.finditer(r'\bTable\s+\d+[A-Za-z]?\b', text[max(0, header['start'] - 240):header['end']], re.I))
            if captions:
                item['source_table'] = captions[-1].group()
        if feature:
            item['suggested_feature_key'] = feature
        if uncertainty is not None:
            item['uncertainty'] = uncertainty
            item['warnings'].append('保留原文±不确定度，未假定为标准差、标准误或置信区间。')
        facts.append(item)

    def emit(m, metric, label, role, *, unit=None, **kwargs):
        groups = m.groupdict()
        uncertainty = None
        if groups.get('error') is not None:
            uncertainty = dict(value=_float(groups['error']), unit=unit or _unit(groups.get('unit', '')),
                               type='unspecified', **view.evidence(m.start('error'), m.end('error')))
        add(view.evidence(m.start(), m.end()), metric, label, _float(groups['value']),
            unit or _unit(groups.get('unit', '')), role,
            high=_float(groups['high']) if groups.get('high') else None,
            uncertainty=uncertainty, **kwargs)

    # A label/value/unit parameter table. Headers are retained independently;
    # matches remain useful if a table is copied without its caption.
    parameter_specs = [
        (r'Engine speed', 'engine speed', '发动机转速', r'rpm', '', 'condition'),
        (r'Engine torque', 'engine torque', '发动机扭矩', r'Nm|N[·⋅]?m', '', 'condition'),
        (r'NO\s*x concentration at engine outlet', 'engine-out NOx concentration', '发动机出口NOx浓度', r'ppm', '', 'condition'),
        (r'Exhaust mass flow rate', 'exhaust mass flow rate', '排气质量流量', r'kg/h', '', 'condition'),
        (r'Exhaust gas temperature at SCR inlet', 'SCR inlet temperature', 'SCR入口温度', _TEMP, 'temperature_C', 'condition'),
    ]
    line_offset = 0
    for line in text[:limit].splitlines(keepends=True):
        clean = line.rstrip('\r\n')
        lv = _View(text, line_offset, line_offset + len(clean))
        for prefix, metric, label, units, field, role in parameter_specs:
            m = re.fullmatch(rf'\s*{prefix}\s+(?P<value>{_N})\s*(?P<unit>{units})\s*', lv.text, re.I)
            if m:
                prior = text[max(0, line_offset - 650):line_offset]
                hm = list(re.finditer(r'(?im)^\s*(?:Table\s+\d+\.?[^\n]*|Engine Parameter Setpoint Value)\s*$', prior))
                header = None
                if hm:
                    h = hm[-1]
                    a = max(0, line_offset - 650) + h.start()
                    b = max(0, line_offset - 650) + h.end()
                    header = dict(start=a, end=b, evidence_quote=text[a:b])
                ev = lv.evidence(m.start(), m.end())
                add(ev, metric, label, _float(m['value']), _unit(m['unit']), role,
                    field=field if _unit(m['unit']) == '°C' else '', header=header,
                    source_role='apparatus_operating_condition')
        line_offset += len(line)

    # Strict two-column textural-property table; arbitrary chemical tables and
    # flattened multi-column layouts are deliberately not guessed.
    header_re = re.compile(r'Sample\s+BET\s+(?:specific\s+)?surface\s+area\s*\(\s*m[²2]/g\s*\)\s+Total\s+pore\s+volume\s*\(\s*cm[³3]/g\s*\)', re.I)
    for hm in header_re.finditer(normal):
        header = view.evidence(hm.start(), hm.end())
        pos = header['end']
        tail = text[pos:limit]
        for raw in tail.splitlines(keepends=True):
            clean = raw.strip()
            if not clean:
                pos += len(raw)
                continue
            rv = _View(text, pos, pos + len(raw.rstrip('\r\n')))
            m = re.fullmatch(rf'\s*(?P<sample>.+?)\s+(?P<bet>{_N})\s+(?P<pore>{_N})\s*', rv.text)
            valid = m and re.search(r'[A-Za-z]', m['sample']) and not re.match(r'(?:Fig(?:ure)?|Table|Equation)\b', m['sample'], re.I)
            if not valid:
                break
            ev = rv.evidence()
            sample = m['sample'].strip()
            for key, metric, label, unit, feature in (
                ('bet', 'BET surface area', 'BET比表面积', 'm²/g', 'BET_surface_area_m2_g'),
                ('pore', 'pore volume', '总孔容', 'cm³/g', 'pore_volume_cm3_g'),
            ):
                add(ev, metric, label, _float(m[key]), unit, 'characterization', sample=sample,
                    header=header, feature=feature, source_role='table_characterization',
                    warnings=['样品名按表格原文保留；未把样品编号中的数字自动解释为制备条件。'])
            pos += len(raw)

    # Dimensionless distribution metrics have a different scientific purpose
    # from catalytic conversion. UI 0.934 is never NO conversion 93.4%.
    ui = re.compile(rf'(?P<prefix>uniformity\s+index(?:\s*\(UI\))?(?:\s+value)?|UI\s+mean\s+value)'
                    rf'(?P<bridge>(?:(?!\.|;).){{0,100}}?)\s+(?:equal\s+to|of|was|is|=|greater\s+than)\s*'
                    rf'(?P<value>{_N})(?:\s*±\s*(?P<error>{_N}))?', re.I)
    for m in ui.finditer(normal):
        fragment = m.group()
        if (re.search(r'\b(?:formula|equation|defined\s+as)\b', fragment, re.I)
                or re.match(r'\s*[−+*/∑]', normal[m.end():])
                or re.match(r'\s*(?:times?\b|fold\b|%\s*(?:higher|lower)|(?:higher|lower)\s+than)', normal[m.end():], re.I)):
            continue
        mean = 'mean' in m['prefix'].lower()
        emit(m, 'NH3 uniformity index', '氨分布均匀性指数（均值）' if mean else '氨分布均匀性指数',
             'other_result', unit='dimensionless', source_role='reported_summary' if mean else 'distribution_result',
             operator='gt' if re.search(r'greater\s+than', fragment, re.I) else 'eq',
             warnings=['UI衡量氨在截面上的分布均匀程度，不是NO或NOx转化率。'] +
                      (['这是作者报告的汇总值；未根据重复实验自行重算或修正。'] if mean else []))
    for m in re.finditer(rf'(?:mean\s+calculated\s+α\s+value|α\s*mean)\s*(?:was|is|of|=)\s*(?P<value>{_N})', normal, re.I):
        emit(m, 'mean NH3/NOx stoichiometric ratio', '平均氨氮当量比α', 'other_result', unit='dimensionless',
             source_role='author_calculated_result', warnings=['这是作者由浓度分布计算的α均值，不是转化率，也不是模型预测实验标签。'])
    ratio = rf'NO\s*[2₂]\s*/\s*NO\s*[xₓ]\s+ratio\s+(?:at\s+the\s+SCR\s+inlet\s+)?(?:was|is|=)\s*(?P<value>{_N})\s*(?P<unit>%)'
    for m in re.finditer(ratio, normal, re.I):
        emit(m, 'NO2/NOx inlet ratio', '入口NO₂/NOx比例', 'condition', source_role='gas_composition_ratio')
    requirement = (rf'(?P<gas>NO\s*[xₓ]|NO)\s+conversion\s+(?:efficiency\s+)?'
                   rf'(?P<bound>above|greater\s+than|at\s+least)?\s*(?P<value>{_N})\s*(?P<unit>%)'
                   rf'\s+(?:is|was)\s+(?:demanded|required)')
    for m in re.finditer(requirement, normal, re.I):
        gas = re.sub(r'\s+', '', m['gas']).replace('ₓ', 'x')
        emit(m, gas + ' conversion', gas + '转化率设计要求', 'background', source_role='requirement',
             operator='ge' if m['bound'] == 'at least' else 'gt' if m['bound'] else 'eq',
             warnings=['这是原文要求达到的转化率，不是本研究已测得的转化率。'])
    for m in re.finditer(rf'NO\s*x\s+emissions\s+downstream\s+of\s+the\s+SCR\s+are\s*(?P<value>{_N})\s*(?P<unit>ppm)\s+or\s+lower', normal, re.I):
        emit(m, 'local downstream NOx concentration', '局部SCR出口NOx浓度上界', 'measurement', operator='le',
             source_role='local_concentration', warnings=['原文只涉及局部过量NH₃区域，不能代表全截面平均值或整体NOx转化率。'])

    # Explicit concentrations distinguish final inlet gas from stock/premix.
    for m in re.finditer(rf'inlet\s+NO\s+concentration\s+(?:was\s+)?(?:kept\s+in\s+a\s+fixed\s+value,\s*namely|of|was|is|=)\s*(?P<value>{_N})\s*(?P<unit>ppm)', normal, re.I):
        emit(m, 'inlet NO concentration', '入口NO浓度', 'condition', field='feed_NO_ppm', source_role='explicit_inlet_concentration')
    premix_re = re.compile(rf'(?P<gas>NH\s*[3₃]|NO)\s*\(\s*(?P<value>{_N})\s*(?P<unit>ppm)\s*,\s*(?P<flow>{_N})\s*{_FLOW}\s*,\s*N\s*[2₂]\s+as\s+the\s+gas\s+carrier\s*\)', re.I)
    gas_streams = []
    for m in premix_re.finditer(normal):
        gas = re.sub(r'\s+', '', m['gas']).replace('₃', '3').upper()
        ev = view.evidence(m.start(), m.end())
        warning = ['这是与载气配制的单路气体浓度，不是混合后反应器入口浓度；未自动填入进料浓度字段。']
        add(ev, f'{gas} premix concentration', f'{gas}预混气浓度', _float(m['value']), 'ppm', 'condition', source_role='premix_stream', warnings=warning)
        add(ev, f'{gas} stream flow rate', f'{gas}气路流量', _float(m['flow']), 'mL/min', 'condition', source_role='premix_stream', warnings=warning)
        gas_streams.append((_float(m['flow']), ev))
    for m in re.finditer(rf'O\s*[2₂]\s*\(\s*(?P<value>{_N})\s*(?P<unit>{_FLOW})\s*\)', normal, re.I):
        emit(m, 'O2 stream flow rate', '氧气支路流量', 'condition', source_role='premix_stream')
        gas_streams.append((_float(m['value']), view.evidence(m.start(), m.end())))
    total_flow_re = rf'(?:total\s+(?:mixture\s+)?(?:flow\s+gas|gas\s+flow(?:\s+rate)?|flow\s+rate))\s*(?:was|is|of|=)\s*(?P<value>{_N})\s*(?P<unit>{_FLOW})'
    for m in re.finditer(total_flow_re, normal, re.I):
        warning = []
        if gas_streams and len(gas_streams) >= 2:
            # Check only adjacent, explicitly enumerated streams; never derive
            # concentrations or silently repair a missing digit in the paper.
            ev = view.evidence(m.start(), m.end())
            close = [(v, e) for v, e in gas_streams if 0 <= ev['start'] - e['end'] <= 500]
            if len(close) >= 2 and abs(sum(v for v, _ in close) - _float(m['value'])) > 1e-6:
                warning.append('相邻已列支路流量之和与原文报告的总流量不一致；保留原值，请查原图或实验段落，未推算进料浓度。')
        emit(m, 'total gas flow rate', '反应混合气总流量', 'condition', field='flow_rate_ml_min', warnings=warning)

    for m in re.finditer(rf'UWS\s+flow\s+rate\s+of\s*(?P<value>{_N})\s*(?P<unit>mg/s)', normal, re.I):
        emit(m, 'UWS mass flow rate', '尿素水溶液质量流量', 'condition', source_role='urea_solution_feed')
    for m in re.finditer(rf'(?P<value>{_N})\s*(?P<unit>m[lL])\s+of\s+catalyst\s+was\s+placed', normal, re.I):
        emit(m, 'catalyst bed volume', '装填催化剂体积', 'condition', warnings=['体积不是质量；未用假定密度换算成催化剂质量。'])
    for m in re.finditer(rf'(?:fixed-bed\s+reactor\s+with\s+a\s+diameter\s+of|SCR\s+(?:substrate\s+)?(?:of\s+)?diameter\s+of)\s*(?P<value>{_N})\s*(?P<unit>mm)', normal, re.I):
        emit(m, 'reactor diameter', '反应器或SCR基体直径', 'condition', source_role='apparatus_geometry')

    # Preparation values are kept apart from reaction conditions. Lists are
    # retained as separate candidates with a pairing warning, never expanded
    # into an invented grid of samples, metals and preparation temperatures.
    for m in re.finditer(rf'acid\s*/\s*ash\s*(?:=|¼)\s*(?P<value>{_N})\s*(?P<unit>m[lL]/g)', normal, re.I):
        emit(m, 'acid/ash liquid-solid ratio', '酸液/粉煤灰用量比', 'preparation')
    prep_specs = [
        (r'(?:dried|drying)\s+at\s+(?:temperatures?\s+of\s+)?', 'drying temperature', '干燥温度', 'drying_temperature_C'),
        (r'(?:calcined|calcination)\s+(?:at\s+)?(?:temperatures?\s+of\s+)?', 'calcination temperature', '煅烧温度', 'calcination_temperature_C'),
        (r'(?:suspensions?\s+(?:was|were)\s+heated\s+at)\s*', 'preparation heating temperature', '制备加热温度', ''),
        (r'(?:treated\s+with\s+concentrated\s+nitric\s+acid.{0,50}?at\s+a\s+temperature\s+of)\s*', 'acid treatment temperature', '硝酸处理温度', ''),
        (r'(?:steam\s+activation\s+at)\s*', 'steam activation temperature', '蒸汽活化温度', ''),
    ]
    listnum = rf'{_N}(?:\s*(?:,\s*(?:and\s+)?|and\s+){_N})*'
    for prefix, metric, label, feature in prep_specs:
        pattern = rf'{prefix}(?P<approx>about\s+|approximately\s+)?(?P<values>{listnum})\s*(?P<unit>{_TEMP})(?:\s+for\s+(?P<duration>{_N})\s*h\b)?'
        for m in re.finditer(pattern, normal, re.I):
            ev = view.evidence(m.start(), m.end())
            nums = list(re.finditer(_N, m['values']))
            warn = ['原文同时列出多个制备值；未推断其与样品或金属种类的一一对应。'] if len(nums) > 1 else []
            for n in nums:
                unit = _unit(m['unit'])
                add(ev, metric, label, _float(n.group()), unit, 'preparation',
                    feature=feature if unit == '°C' else '',
                    operator='approx' if m['approx'] else 'eq', warnings=warn)
            if m['duration']:
                add(ev, metric.replace('temperature', 'duration'), label.replace('温度', '时间'),
                    _float(m['duration']), 'h', 'preparation', warnings=warn)
    for m in re.finditer(rf'high\s+pressure\s+steam\s*\(\s*(?P<value>{_N})\s*(?P<unit>atm)\.?\s*\)', normal, re.I):
        emit(m, 'steam preparation pressure', '制备蒸汽压力', 'preparation', warnings=['这是制备工序压力，不是催化反应压力。'])

    # Repeated PDF text layers and literally repeated table rows must not
    # multiply the inventory. Equal values with different evidence are kept.
    unique = {}
    for item in sorted(facts, key=lambda row: (row['start'], row['metric'])):
        key = (item['metric'], item['sample_label'], item['value'], item['value_high'], item['unit'],
               item['operator'], item['quantity_role'], _normal(item['evidence_quote']))
        old = unique.get(key)
        if old is None:
            unique[key] = item
        else:
            old.setdefault('duplicate_evidence', []).append({k: item[k] for k in ('start', 'end', 'evidence_quote')})
    return list(unique.values())
