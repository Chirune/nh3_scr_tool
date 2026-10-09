"""Source-aligned offline scientific statement candidates; never approvals.

Rules support explicit constructions, not arbitrary language understanding.
No table reconstruction, inferred units, cross-sentence identity or approval.
"""
from __future__ import annotations
import math
import re

MAX_TEXT_LENGTH = 2_000_000
MAX_EVIDENCE_LENGTH = 1_200
NUMBER = r"[+−–-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?:[eE][+−-]?\d+)?"
COUNT = rf"(?:{NUMBER}|one|two|three|four|five|six|seven|eight|nine|ten|hundred|[一二三四五六七八九十百两]+)"
_NUMBER_WORDS = dict(zip(('one','two','three','four','five','six','seven','eight','nine','ten','hundred'), (1,2,3,4,5,6,7,8,9,10,100)))
_NUMBER_WORDS.update(dict(zip('一二两三四五六七八九十百', (1,2,2,3,4,5,6,7,8,9,10,100))))
_METRICS = [
    ('NOx conversion', r'\bNO\s*(?:x|ₓ)\s*conversion\b|NO\s*(?:x|ₓ)\s*转化率|氮氧化物转化率'),
    ('NO conversion', r'\bNO\s+conversion\b|NO\s*转化率|一氧化氮转化率'),
    ('NH3 conversion', r'\bNH\s*[3₃]\s*conversion\b|NH[3₃]\s*转化率|氨转化率'),
    ('CO2 conversion', r'\bCO\s*[2₂]\s*conversion\b|CO[2₂]\s*转化率|二氧化碳转化率'),
    ('CO conversion', r'\bCO\s+conversion\b|CO\s*转化率|一氧化碳转化率'),
    ('N2 selectivity', r'\bN\s*[2₂]\s*selectivity\b|N[2₂]\s*选择性|氮气选择性'),
    ('CO selectivity', r'\bCO\s+selectivity\b|CO\s*选择性|一氧化碳选择性'),
    ('methanol selectivity', r'\bmethanol\s+selectivity\b|甲醇选择性'),
    ('methanol yield', r'\bmethanol\s+yield\b|甲醇收率|甲醇产率'),
    ('NH3 adsorption energy', r'\b(?:NH\s*[3₃]|ammonia)\s+adsorption\s+energ(?:y|ies)\b|\badsorption\s+energ(?:y|ies)\s+of\s+(?:NH\s*[3₃]|ammonia)\b|(?:NH[3₃]|氨)\s*吸附能'),
    ('adsorption energy', r'\badsorption\s+energ(?:y|ies)\b|吸附能'),
    ('adsorption capacity', r'\badsorption\s+capacit(?:y|ies)\b|吸附容量|吸附量'),
    ('BET surface area', r'\b(?:BET\s+)?(?:specific\s+)?surface\s+areas?\b|BET\s*比表面积|比表面积'),
    ('pore volume', r'\b(?:total\s+)?pore\s+volumes?\b|孔容'),
    ('reaction temperature', r'\breaction\s+temperatures?\b|反应温度'),
]
_METRIC_PATTERNS = [(n,re.compile(p,re.I)) for n,p in _METRICS]
_UNIT = (r'percentage\s+points?|百分点|%|％|percent\b|'
         r'(?:kJ|kcal)\s*(?:/\s*mol|[·⋅]?\s*mol\s*(?:\^?[-−]1|⁻¹))|eV\b|'
         r'(?:cm²|cm\^?2|m²|m\^?2|cm³|cm\^?3)\s*(?:/\s*g|[·⋅]?\s*g\s*(?:\^?[-−]1|⁻¹))|'
         r'(?:mmol|μmol|µmol|umol|mol|mg)\s*(?:/\s*(?:kg|g)|[·⋅]?\s*(?:kg|g)\s*(?:\^?[-−]1|⁻¹))|'
         r'°\s*C|℃|K\b|fraction\b|dimensionless\b|无量纲')
_UNIT_RE = re.compile(_UNIT,re.I)
_NUMBER_RE = re.compile(NUMBER)
_RANGE_SEPARATOR = r'(?:[–—−~～至到]|-(?=\s*\d)|\bto\b)'
_BOUND_WORDS = (r'>=|<=|≥|≤|>|<|no\s+less\s+than|not\s+less\s+than|no\s+more\s+than|not\s+more\s+than|'
                r'(?:did|does|do)\s+not\s+exceed|(?:did|does|do)\s+not\s+fall\s+below|'
                r'at\s+least|at\s+most|more\s+than|less\s+than|greater\s+than|over|above|below|up\s+to|'
                r'不少于|不低于|不超过|至少|至多|高于|低于|超过|大于|小于')
_BOUND = re.compile(rf'\s*(?P<bound>{_BOUND_WORDS})?\s*',re.I)
_APPROX = re.compile(r'(?:about|approximately|approx\.?|around|ca\.?|~|≈|约|大约|近)\s*',re.I)
_SUFFIX = re.compile(r'\s*(?:(?:efficiency|efficiencies|rate|rates)\b\s*)?',re.I)
_LINK = re.compile(r'\s*(?:(?:measured\s+to\s+be|found\s+to\s+be|was|were|is|are|of|reached|reaches|achieved|achieves|measured|determined|'
                   r'increased\s+to|decreased\s+to|ranged\s+from|ranges\s+from|ranged\s+between|ranges\s+between|between|'
                   r'only|very\s+low,|very\s+high,|as\s+high\s+as|as\s+low\s+as)\b\s*|'
                   r'(?:为|是|约为|达到|达到了|提高到|提升至|降低至|介于|范围为)\s*)',re.I)
_OUTLOOK = re.compile(r'\b(?:may|might|could|potential(?:ly)?|expected|expect|will|would|future|prospect(?:s|ive)?)\b|'
                      r'有望|预计|预期|可能|展望|将会|未来|理论上可|潜力',re.I)
_NEGATED = re.compile(
    r'\b(?-i:no|No)\s+(?:(?:significant|measurable)\s+)?(?:improvement|increase|enhancement|change|conversion|adsorption)\b|'
    r'\b(?:did|does|do|was|were|is|are|has|had)\s+not\s+(?:(?:significantly|measurably)\s+)?'
    r'(?:improv\w*|increas\w*|decreas\w*|reduc\w*|eliminat\w*|enhanc\w*|reach\w*|achiev\w*|observ\w*|show\w*|exhibit\w*|\d+)\b|'
    r'\b(?:neither|never)\b|\b(?:had|has|have)\s+no\s+(?:significant\s+)?effects?\b|'
    r'(?:未|没有|并未|不)(?:显著)?(?:提高|提升|增加|改善|达到|观察到)|无(?:明显|显著)?(?:改善|提升|增加)',re.I)
_QUALITATIVE = re.compile(r'\b(?:high|higher|highest|low|lower|lowest|better|superior|enhanced|improved|excellent|promising|stable|stability)\b|'
                          r'较高|较低|提高|提升|改善|优异|优于|稳定|增强',re.I)
_PREDICTED = re.compile(r'\b(?:predicted|simulated|model[- ]estimated|forecast(?:ed)?)\b|预测(?:的|得到|结果|值)|模拟(?:的|得到|结果|值)',re.I)
_REQUIREMENT = re.compile(r'\b(?:conversion|selectivity|yield)\b[^.;]{0,100}\b(?:is|are|was|were)\s+(?:demanded|required)\b|'
                          r'\b(?:required|target|minimum\s+acceptable)\s+(?:[A-Za-z0-9₀-₉]+\s+){0,3}(?:conversion|selectivity|yield)\b|'
                          r'(?:转化率|选择性|收率)[^。；]{0,30}(?:要求|目标)|(?:要求|目标)[^。；]{0,30}(?:转化率|选择性|收率)',re.I)
_PRIOR = re.compile(r'\b(?:previous\s+(?:work|study|studies)|previously\s+reported|prior\s+(?:work|study|studies)|'
                    r'reported\s+by|according\s+to|literature|et\s+al)\b|前人|已有研究|据报道|文献报道|文献中|综述指出|他人研究',re.I)
_CURRENT = re.compile(r'\b(?:in\s+(?:this|the\s+present|our)\s+(?:study|work|paper|experiment)|'
                      r'we\s+(?:measured|observed|found|obtained|determined|achieved|tested|prepared)|'
                      r'our\s+(?:study|work|measurements|experiment))\b|本研究|本工作|我们(?:测得|测量|发现|获得|观察|制备|测试)',re.I)
_PREPARATION = re.compile(r'\b(?:calcin\w*|dri(?:ed|ng)|drying|anneal\w*|pretreat\w*|pre-treat\w*|degass\w*|synthesi\w*|preparation|oxidization|nitrification)\b|焙烧|煅烧|干燥|制备|预处理|退火',re.I)
_TEST = re.compile(r'\b(?:tested|measured|evaluated|reaction|test)\b|反应|测试|测定',re.I)
_TOKEN = r'(?:\d+(?:\.\d+)?%?[-]?)?[A-Za-z][A-Za-z0-9₀-₉/@+_.%−-]{0,79}'
_TOKEN_RE = re.compile(_TOKEN)
_SAMPLE_WORD = re.compile(r'(?:\b(?:samples?|catalysts?)\s+(?:(?:labeled|labelled|named)\s+(?:as\s+)?)?|样品\s*|催化剂\s*)',re.I)
_STOP_LABELS = set('the a an this that these those with without containing prepared were was is are had has have showed show shows at for of and or in on as gave give reported tested calcined support activity conversion efficiency stability surface acid particles temperature properties reaction respectively'.split())


def _float(raw):
    word = raw.strip().lower()
    if word in _NUMBER_WORDS: return float(_NUMBER_WORDS[word])
    if re.fullmatch(r'[一二三四五六七八九]?十[一二三四五六七八九]?',word):
        left,right = word.split('十')
        return float(_NUMBER_WORDS.get(left,1)*10+_NUMBER_WORDS.get(right,0))
    try:
        value = float(word.replace('−','-').replace('–','-').replace(',',''))
        return value if math.isfinite(value) else None
    except ValueError: return None


class _View:
    """Whitespace/ligature/dehyphenation view with exact source coordinates."""
    def __init__(self, source, start, end):
        self.source, self.start, self.end = source, start, end
        chars, mapping = [], []
        raw = source[start:end]
        skip = set()
        for m in re.finditer(r'(?<=[A-Za-z])[-\u00ad‐‑]\s*\n\s*(?=[a-z])',raw):
            skip.update(range(m.start(),m.end()))
        # A wrapped material suffix retains its hyphen: SAPO-\n34 -> SAPO-34.
        # Only remove whitespace here, unlike ordinary word dehyphenation.
        for m in re.finditer(r'(?<=[A-Za-z0-9])[-‐‑](?P<space>\s*\n\s*)(?=\d)',raw):
            skip.update(range(m.start('space'),m.end('space')))
        for m in re.finditer(r'\b(?:[A-Z][a-z]?\d*)+\s+(?=[xyz\d]+/)',raw):
            whitespace = re.search(r'\s+$',m.group())
            skip.update(range(m.start()+whitespace.start(),m.end()))
        replacements = {'ﬁ':'fi','ﬂ':'fl','ﬀ':'ff','ﬃ':'ffi','ﬄ':'ffl','◦':'°','º':'°','‐':'-','‑':'-'}
        for i,char in enumerate(raw):
            if i in skip: continue
            replacement = ' ' if char.isspace() else replacements.get(char,char)
            if replacement == ' ' and chars and chars[-1] == ' ':
                mapping[-1] = (mapping[-1][0],start+i+1)
                continue
            for value in replacement:
                chars.append(value); mapping.append((start+i,start+i+1))
        self.text, self.mapping = ''.join(chars), mapping

    def evidence(self, a=0, b=None):
        b = len(self.text) if b is None else b
        if not self.mapping or b <= a: return {'start':self.start,'end':self.start,'evidence_quote':''}
        first,last = self.mapping[max(0,a)][0],self.mapping[min(len(self.mapping),b)-1][1]
        return {'start':first,'end':last,'evidence_quote':self.source[first:last]}


def _spans(text):
    refs = re.search(r'(?im)^\s*(?:References|Bibliography|参考文献)\s*$',text)
    limit = refs.start() if refs else len(text)
    boundaries = []
    for m in re.finditer(r'[。！？!?；;]|\n\s*\n|\.(?=\s|[A-Z]|$)',text[:limit]):
        if m.group() == '.':
            before = text[max(0,m.start()-12):m.start()+1]
            if re.search(r'\b(?:al|fig|eq|dr|mr|mrs|prof|vs|ca|approx)\.$',before,re.I): continue
            # A chemical symbol or one-letter sample at the end of a sentence
            # is not automatically an author's initial ("sample A. Its ...").
            # Only retain a clear dotted-initial sequence here.
            if re.search(r'\b[A-Z]\.$',before) and re.match(r'\s*[A-Z]\.',text[m.end():]): continue
            if re.search(r'\b(?:e\.g|i\.e)\.$',before,re.I): continue
        boundaries.append(m.end())
    boundaries.append(limit)
    last = 0
    for boundary in boundaries:
        begin,end = last,boundary
        last = boundary
        while begin < end and text[begin].isspace(): begin += 1
        while end > begin and text[end-1].isspace(): end -= 1
        truncated = end-begin > MAX_EVIDENCE_LENGTH
        while begin < end:
            cut = min(end,begin+MAX_EVIDENCE_LENGTH)
            if cut < end:
                gap = text.rfind(' ',begin+MAX_EVIDENCE_LENGTH//2,cut)
                if gap > begin: cut = gap
            yield begin,cut,truncated
            begin = cut
            while begin < end and text[begin].isspace(): begin += 1


def _metrics(quote):
    matches = sorted(((m.start(),m.end(),name) for name,p in _METRIC_PATTERNS for m in p.finditer(quote)),key=lambda x:(x[0],-(x[1]-x[0])))
    result = []
    for match in matches:
        if not result or match[0] >= result[-1][1]: result.append(match)
    return result


def _unit(raw):
    value = raw.strip()
    if re.fullmatch(r'%|％|percent',value,re.I): return '%'
    if re.fullmatch(r'percentage\s+points?|百分点',value,re.I): return 'percentage points'
    if re.fullmatch(r'°\s*C|℃',value,re.I): return '°C'
    if value.lower() in ('fraction','dimensionless') or value == '无量纲': return 'dimensionless'
    return value


def _unit_matches(metric,unit):
    if not unit: return False
    if any(x in metric for x in ('conversion','selectivity','yield')): return unit in ('%','dimensionless')
    if metric.endswith('adsorption energy'): return bool(re.match(r'(?:eV|kJ|kcal)',unit,re.I))
    if metric == 'adsorption capacity': return bool(re.match(r'(?:mmol|μmol|µmol|umol|mol|mg)',unit,re.I))
    if metric == 'BET surface area': return bool(re.match(r'(?:cm²|cm\^?2|m²|m\^?2)',unit,re.I))
    if metric == 'pore volume': return bool(re.match(r'(?:cm³|cm\^?3)',unit,re.I))
    if metric == 'reaction temperature': return unit in ('°C','K')
    return False


def _operator(raw):
    raw = re.sub(r'\s+',' ',raw.strip().lower())
    if raw in ('>','more than','greater than','over','above','高于','超过','大于'): return 'gt'
    if raw in ('>=','≥','at least','no less than','not less than','至少','不少于','不低于') or re.fullmatch(r'(?:did|does|do) not fall below',raw): return 'ge'
    if raw in ('<','less than','below','低于','小于'): return 'lt'
    return 'le' if raw else 'eq'


def _lead(text,pos=0):
    pos = _SUFFIX.match(text,pos).end()
    context_unit = ''
    m = re.match(r'\s*\(([^()]{1,24})\)\s*',text[pos:])
    if m and _UNIT_RE.fullmatch(m.group(1).strip()):
        context_unit = _unit(m.group(1)); pos += m.end()
    begin = pos
    for _ in range(5):
        m = _LINK.match(text,pos)
        if not m: break
        pos = m.end()
    descriptor = re.match(r'\s*(?:very\s+)?(?:low|high)\s*,\s*',text[pos:],re.I)
    if descriptor: pos += descriptor.end()
    return pos,context_unit,text[begin:pos]


def _quantity(text,pos=0,allow_range=True):
    """Read one complete expression; never silently truncate invalid numbers."""
    start = pos
    bound = _BOUND.match(text,pos); pos = bound.end()
    approx = _APPROX.match(text,pos)
    if approx: pos = approx.end()
    number = _NUMBER_RE.match(text,pos)
    if not number: return None
    value,pos = _float(number.group()),number.end()
    result = dict(value=value,value_high=None,unit='',operator=_operator(bound.group('bound') or ''),
                  approximate=bool(approx),start=start,number_start=number.start(),end=pos,invalid=value is None)
    if re.match(r',\d|[eE](?![+−-]?\d)|\s*[×·]\s*10',text[pos:]):
        result.update(value=None,operator='unknown',invalid=True); return result
    unit_at = pos+len(text[pos:])-len(text[pos:].lstrip())
    first_unit = _UNIT_RE.match(text,unit_at)
    if first_unit: result['unit'] = _unit(first_unit.group()); pos = first_unit.end()
    # Preserve explicit error, never infer SD/SE/CI or manufacture endpoints.
    error = re.match(rf'\s*(?:\(\s*)?(?:±|\+/-)\s*(?P<value>{NUMBER})\s*(?P<unit>{_UNIT})?\s*\)?',text[pos:],re.I)
    if error:
        err_unit = _unit(error.group('unit') or '')
        if not result['unit']: result['unit'] = err_unit
        result['uncertainty'] = dict(value=_float(error.group('value')),unit=err_unit or result['unit'],type='unspecified',start=pos,end=pos+error.end())
        compatible_error = result['unit']=='%' and err_unit=='percentage points'
        if (err_unit and result['unit'] != err_unit and not compatible_error) or result['uncertainty']['value'] is None or result['uncertainty']['value'] < 0:
            result.update(value=None,invalid=True)
        pos += error.end(); result['operator'] = 'unknown'
    elif allow_range:
        high = re.match(rf'\s*{_RANGE_SEPARATOR}\s*(?P<value>{NUMBER})',text[pos:],re.I)
        if high:
            result['value_high'] = _float(high.group('value')); pos += high.end()
            unit_at = pos+len(text[pos:])-len(text[pos:].lstrip())
            unit2 = _UNIT_RE.match(text,unit_at)
            if unit2:
                normalized = _unit(unit2.group()); pos = unit2.end()
                if result['unit'] and normalized != result['unit']: result['invalid'] = True
                result['unit'] = normalized
            if result['value_high'] is None: result['invalid'] = True
            result['operator'] = 'range' if not bound.group('bound') else 'unknown'
    if approx: result['operator'] = 'unknown'
    result['end'] = pos
    if result['invalid']: result.update(value=None,value_high=None,operator='unknown')
    return result


def _measurement(tail):
    pos,context_unit,lead = _lead(tail)
    binder = re.match(rf'\s*(?:(?:for|over|using|of)\s+)?(?:(?:sample|catalyst)\s+)?(?P<label>{_TOKEN})\s+(?:was|were|is|are|of)\s+',tail[pos:],re.I)
    if binder and _valid_label(binder.group('label'),True): pos += binder.end()
    temp = re.match(rf'\s*at\s+(?:about\s+)?{NUMBER}\s*(?:°\s*C|℃|K\b)\s+(?:was|were|is|are|of)\s+',tail[pos:],re.I)
    if temp: pos += temp.end()
    result = _quantity(tail,pos)
    if result is None: return None
    if not result['unit']: result['unit'] = context_unit
    if not result['unit'] and not lead.strip() and not binder and not temp:
        # Bare heading/table numbers after a metric label are not prose
        # measurements. "conversion was 0.9" remains a unit-missing candidate.
        return None
    if '约' in lead: result.update(approximate=True,operator='unknown')
    if re.search(r'\bbetween\b|介于',lead,re.I):
        high = re.match(rf'\s+(?:and|与)\s+(?P<high>{NUMBER})\s*(?P<unit>{_UNIT})?',tail[result['end']:],re.I)
        if high:
            result['value_high'] = _float(high.group('high'))
            result['end'] += high.end()
            if high.group('unit'): result['unit'] = _unit(high.group('unit'))
            result['operator'] = 'unknown' if result['approximate'] else 'range'
            if result['value_high'] is None: result.update(value=None,invalid=True,operator='unknown')
    return result


def _preceding_measurement(prefix):
    # Only a directly attached unit-bearing value. Loadings, citations and
    # sample suffixes are never assigned by approximate textual proximity.
    m = re.search(rf'(?:(?:{_BOUND_WORDS})\s+)?(?:(?:about|approximately|around|~|≈)\s*)?{NUMBER}\s*(?:{_UNIT})\s*$',prefix,re.I)
    if not m: return None
    result = _quantity(prefix,m.start())
    if result: result['end'] = len(prefix.rstrip())
    return result


def _valid_label(label,explicit=False):
    label = label.rstrip('.')
    if explicit and re.fullmatch(r'[A-Z]',label): return True
    if not label or label.lower() in _STOP_LABELS: return False
    if label in ('NH3','CO2','NO','NOx','N2','O2','H2','CO'): return False
    if re.fullmatch(r'(?:[munpµμ]?L|cm3|mm3|m3|mol|mmol|umol|mg|kg|g|kJ|kcal|m2|cm2)/(?:min|s|h|g|kg|mol|L|mL)(?:/(?:g|kg))?',label,re.I):return False
    if '/' in label or '@' in label: return True
    if explicit: return bool(re.fullmatch(_TOKEN,label) and (any(c.isupper() for c in label) or any(c.isdigit() for c in label)))
    return bool(re.fullmatch(r'[A-Z][A-Za-z]*\d+(?:[-_.][A-Za-z0-9]+)*',label))


def _samples(text):
    found = []
    for marker in _SAMPLE_WORD.finditer(text):
        pos = marker.end()
        for _ in range(12):
            m = _TOKEN_RE.match(text,pos)
            if not m or not _valid_label(m.group(),True): break
            found.append((m.start(),m.start()+len(m.group().rstrip('.')),m.group().rstrip('.')))
            sep = re.match(r'\s*(?:,\s*(?:and\s+)?|and\s+|和|及|、)\s*',text[m.end():],re.I)
            if not sep: break
            pos = m.end()+sep.end()
    for m in _TOKEN_RE.finditer(text):
        label = m.group().rstrip('.')
        if '/' in label or '@' in label:
            if _valid_label(label) and not re.match(r'https?|\d*/',label,re.I) and not label.lower().startswith(('mol/','mg/','mmol/','m2/','cm3/','m/','kcal/','kj/')):
                found.append((m.start(),m.start()+len(label),label))
        elif _valid_label(label,True) and re.match(r'\s+(?:(?:molecular\s+sieve)\s+)?catalysts?\b',text[m.end():],re.I):
            found.append((m.start(),m.start()+len(label),label))
        elif _valid_label(label) and (re.search(r'(?:\bfor|\bover|\busing|\bof)\s*$',text[:m.start()],re.I) or
                                          re.match(r'\s+(?:showed|gave|achieved|had|exhibited)\b',text[m.end():],re.I)):
            found.append((m.start(),m.start()+len(label),label))
    result = []
    for a,b,label in sorted(set(found)):
        if re.search(r'\b(?:without|excluding|except|not)\s+(?:(?:the\s+)?(?:sample|catalyst)\s+)?$',text[:a],re.I):
            continue
        if re.search(r'\bseries\s+of\s*$',text[:a],re.I) or re.search(r'\bcatalysts\s+with\s+different\b',text[b:b+80],re.I):
            continue  # a material family does not identify one preparation
        modifier = re.match(r'\s+(?:catalyst\s+)?with\s+\d+(?:\.\d+)?\s*%\s*[A-Z][a-z]?(?:\s+loading)?',text[b:])
        if modifier: b += modifier.end(); label = text[a:b]
        if not result or a >= result[-1][1]: result.append((a,b,label))
    return result


def _scope(local,global_text=''):
    prior,current = bool(_PRIOR.search(local)),bool(_CURRENT.search(local))
    if prior and current: return 'unknown',['同一分句混有本研究与文献引述，需人工确定归属。']
    if prior: return 'prior_work',['属于文献引述候选，不能自动作为本研究实验记录。']
    if current: return 'current_study',[]
    if global_text and _CURRENT.search(global_text) and not _PRIOR.search(global_text): return 'current_study',[]
    warning = '未能从本句确定属于本研究还是文献引述。'
    if re.search(r'\[\s*\d[\d,; –-]*\]',local): warning += ' 引用编号本身不足以确定结果归属。'
    return 'unknown',[warning]


def _clauses(text):
    """Split independent predicates, keeping shared-metric claims together."""
    start = 0
    for d in re.finditer(r'\s*[,，]\s*|\s+\b(?:but|whereas|while|and)\b\s+|[，,]?\s*(?:但是|然而|而)\s*',text,re.I):
        left,right = text[start:d.start()],text[d.end():]
        lm,rm = _metrics(left),_metrics(right)
        if not lm or not rm: continue
        word = d.group().strip(' ,，').lower()
        contrast = word in ('but','whereas','while','但是','然而','而') and not re.search(r'\bnot\s+only\b',left,re.I)
        last = lm[-1]
        measure = _measurement(left[last[1]:]) or _preceding_measurement(left[:last[0]])
        if measure and not measure.get('unit'): measure = None
        if contrast or measure or _NEGATED.search(left):
            yield start,d.start()
            start = d.end()
    yield start,len(text)


def _conditions(view,a,b,metric_pos=None):
    text = view.text[a:b]
    # A coordinated temperature list is not one shared condition. In particular,
    # a trailing unit in "at 300 and 400 °C" must not license only the last value.
    # Pairing sample/value/temperature lists is a separate, multi-axis problem.
    temp_list = re.search(rf'(?:\bat\s+(?:(?:reaction\s+)?temperatures?\s+(?:of\s+)?)?|在|温度(?:为|是)?)'
                          rf'\s*{NUMBER}\s*(?:°\s*C|℃|K\b)?\s*(?:,\s*(?:and|or)?\s*|\b(?:and|or)\b\s*|和|及|或|、)'
                          rf'{NUMBER}\s*(?:°\s*C|℃|K\b)',text,re.I)
    if temp_list:
        return {},True
    found = []
    for m in re.finditer(rf'(?:{_BOUND_WORDS})\s+{NUMBER}|(?:about|approximately|around)\s+{NUMBER}|{NUMBER}',text,re.I):
        if m.start() and (text[m.start()-1].isalnum() or text[m.start()-1] in '/_'): continue
        q = _quantity(text,m.start())
        if not q or q['unit'] not in ('°C','K') or q['value'] is None: continue
        if re.match(r'\s*(?:/\s*(?:min|s|h)|(?:min|s|h)\s*(?:\^?[-−]1|⁻¹))\b',text[q['end']:],re.I): continue
        prefix = text[:m.start()]
        anchor = re.search(r'(?:\bat\s+|(?:temperature(?:s)?|温度)\s*(?:(?:range|interval)\s*)?(?:(?:is|was|of|from|为|是)\s*)?)\(?\s*$',prefix,re.I)
        if not anchor: continue
        prep,tests,metrics = list(_PREPARATION.finditer(prefix)),list(_TEST.finditer(prefix)),_metrics(prefix)
        last_prep = prep[-1].start() if prep else -1
        last_test = tests[-1].start() if tests else -1
        last_metric = metrics[-1][0] if metrics else -1
        if last_prep > max(last_test,last_metric): continue
        if max(last_test,last_metric) < 0: continue
        if any(a+q['start'] < prior['end'] for prior in found): continue
        q.update(start=a+q['start'],end=a+q['end'])
        found.append(q)
    if len(found) != 1: return {},bool(found)
    q = found[0]
    return {'temperature':{k:q[k] for k in ('value','value_high','unit','operator')} | view.evidence(q['start'],q['end'])},False


def _base(view,a,b,metric,truncated,metric_pos,role='claim'):
    local = view.text[a:b]
    scope,warnings = _scope(local,view.text[:a])
    samples = list(dict.fromkeys(s[2] for s in _samples(local)))
    subject_evidence = []
    if not samples and a:
        antecedents = _samples(view.text[:a])
        antecedent_labels = list(dict.fromkeys(s[2] for s in antecedents))
        prefix_scope,_ = _scope(view.text[:a])
        if len(antecedent_labels)==1 and prefix_scope==scope and not re.search(r'\b(?:whereas|while|but)\b',local,re.I):
            samples = antecedent_labels
            s = antecedents[-1]
            subject_evidence = [dict(type='subject_binding',sample_label=s[2],**view.evidence(s[0],s[1]))]
    conditions,ambiguous_conditions = _conditions(view,a,b,metric_pos)
    if truncated: warnings.append('长证据段已分段筛查，需回看前后文，不能直接编码。')
    if ambiguous_conditions: warnings.append('分句含多个测试温度，未自动把其中一个温度分配给数值。')
    metric_evidence = view.evidence(metric_pos,metric_pos+1)
    return dict(kind='ambiguous',**view.evidence(a,b),metric=metric,value=None,value_high=None,unit='',operator='unknown',
                sample_label=samples[0] if len(samples)==1 else '',reference_sample='',conditions=conditions,
                assertion_scope=scope,review_status='unreviewed',warnings=warnings,numeric_eligible=False,
                semantic_id_key=f"{metric}:{metric_evidence['start']}:{role}",relations=subject_evidence,context_evidence=[],confidence_reasons=[])


def _relation(view,kind,a,b,**extra):
    return dict(type=kind,**view.evidence(a,b),**extra)


def _comparison(view,a,b,metric_end,metric):
    local = view.text[metric_end:b]
    suffix = _SUFFIX.match(local).end()
    tail,offset = local[suffix:],metric_end+suffix
    binder = re.match(rf'\s*(?:of|for|over)\s+(?:(?:sample|catalyst)\s+)?(?P<label>{_TOKEN})\s+',tail,re.I)
    if binder and _valid_label(binder.group('label'),True):
        offset += binder.end(); tail = tail[binder.end():]
    # Only a change governed by this metric. Earlier Cu-loading changes are
    # not re-labelled as conversion changes merely because they share a sentence.
    change = re.match(rf'\s*(?:(?:was|is|were|are)\s+)?(?:(?:increas\w*|decreas\w*|rose|fell|changed)\s+)?from\s+(?P<lo>{NUMBER})\s*(?P<unit1>{_UNIT})?(?P<middle>\s*(?:(?:for|over|on)\s+(?:(?:sample|catalyst)\s+)?{_TOKEN}\s*|\(\s*{_TOKEN}\s*\)\s*)?)\s*to\s+(?P<hi>{NUMBER})\s*(?P<unit2>{_UNIT})?',tail,re.I)
    if not change:
        change = re.match(rf'\s*从\s*(?P<lo>{NUMBER})\s*(?P<unit1>{_UNIT})?\s*(?:提高|提升|增加|降低|下降)?[至到]\s*(?P<hi>{NUMBER})\s*(?P<unit2>{_UNIT})?',tail,re.I)
    if change:
        unit1,unit2 = _unit(change.group('unit1') or ''),_unit(change.group('unit2') or '')
        unit = unit2 or unit1
        lo,hi = _float(change.group('lo')),_float(change.group('hi'))
        if (unit1 and unit2 and unit1 != unit2) or (unit and not _unit_matches(metric,unit)): return None
        source_sample,target_sample = '',''
        source_matches = _samples(change.groupdict().get('middle') or '')
        if len(source_matches)==1: source_sample = source_matches[0][2]
        parenthetic_source = re.fullmatch(rf'\s*\(\s*(?P<label>{_TOKEN})\s*\)\s*',change.groupdict().get('middle') or '')
        if parenthetic_source and _valid_label(parenthetic_source.group('label'),True): source_sample=parenthetic_source.group('label')
        after = tail[change.end():]
        target_match = re.match(rf'\s*(?:for|over|on)\s+(?:(?:sample|catalyst)\s+)?(?P<label>{_TOKEN})',after,re.I)
        if target_match is None:
            target_match = re.match(rf'\s*\(\s*(?P<label>{_TOKEN})\s*\)',after,re.I)
        extra_end = change.end()
        if target_match and _valid_label(target_match.group('label'),True):
            target_sample = target_match.group('label').rstrip('.'); extra_end += target_match.end()
        rel = _relation(view,'change',offset+change.start(),offset+extra_end,
                        source=dict(value=lo,unit=unit1 or unit,sample_label=source_sample),
                        target=dict(value=hi,unit=unit2 or unit,sample_label=target_sample),
                        direction='decrease' if re.search(r'decreas|fell|降低|下降',change.group(),re.I) else 'increase' if re.search(r'increas|rose|提高|提升|增加',change.group(),re.I) else 'unspecified')
        return dict(kind='comparison',value=None,unit='',operator='unknown',relation=rel,
                    warning='分别保留起点和终点；未换算差值，也未把变化误作单条绝对性能标签。',sample=target_sample,reference=source_sample)
    patterns = [
        ('delta','percentage points',rf'(?P<value>{NUMBER})\s*(?:percentage\s+points?|百分点)(?:\s+(?:higher|lower|greater|less)\s+than\b)?'),
        ('ratio','%',rf'(?:\b(?:increased?|improved?|enhanced?|decreased?|reduced?|higher|lower)\s+by\s+|(?:提高|提升|增加|降低|下降)(?:了)?)\s*(?P<value>{NUMBER})\s*(?:[%％]|percent\b)'),
        ('ratio','%',rf'(?P<value>{NUMBER})\s*(?:[%％]|percent\b)\s+(?P<direction>higher|lower|greater|larger|smaller|less|more)\s+than\b'),
        ('ratio','fold',rf'(?P<value>{COUNT})\s*(?:times|fold)\s+as\s+(?:high|large|much|great|strong)\s+as\b'),
        ('ratio','fold',rf'(?:提高到|提高至|提升到|提升至|增加到|增加至|增至|变为|达到)\s*(?P<value>{COUNT})\s*倍'),
        ('ratio','fold',rf'(?:是|为)[^，,。;；]{{0,35}}?的\s*(?P<value>{COUNT})\s*倍'),
        ('ratio','fold',rf'(?:\breduced?|decreased?)\s+by\s+(?:a\s+)?factor\s+of\s+(?P<value>{COUNT})\b'),
        ('unknown','fold',rf'\b(?:increased?|improved?|enhanced?)\s+(?:by\s+)?(?P<value>{COUNT})\s*[- ]?(?:fold|times)\b'),
        ('unknown','fold',rf'(?P<value>{COUNT})\s*[- ]?(?:fold|times)\s+(?:higher|greater|larger|increase)\b'),
        ('unknown','fold',rf'(?P<value>{COUNT})\s*[- ]?(?:fold|times)\s+(?:lower|smaller|less)\b'),
        ('unknown','fold',rf'(?:提高|提升|增加|增强)(?:了)?\s*(?P<value>{COUNT})\s*倍'),
    ]
    for op,unit,pattern in patterns:
        match = re.search(pattern,tail,re.I)
        if not match: continue
        if re.search(r'(?:±|\+/-)\s*$',tail[:match.start()]): continue
        # Do not reinterpret the upper endpoint of "2–3 times" as −3 times.
        prefix_range = re.search(rf'(?P<low>{NUMBER})\s*{_RANGE_SEPARATOR}\s*$',tail[:match.start()],re.I)
        low = None
        if prefix_range:
            low = _float(prefix_range.group('low'))
        elif match.start() and re.search(r'\d\s*$',tail[:match.start()]) and re.match(r'[−–-]',match.group()):
            before = re.search(rf'(?P<low>{NUMBER})\s*$',tail[:match.start()])
            low = _float(before.group('low')) if before else None
        if re.search(r'\b(?:loading|content|concentration|temperature|cost|time)\b',tail[:match.start()],re.I): continue
        reference = ''
        rest = tail[match.end():]
        ref = re.match(rf'\s*(?:(?:than|compared\s+(?:with|to))\s+)?(?:for\s+)?(?:(?:the\s+)?(?:sample|catalyst)\s+)?(?P<label>{_TOKEN})',rest,re.I)
        if ref and _valid_label(ref.group('label'),True): reference = ref.group('label').rstrip('.')
        embedded_ref = re.search(rf'(?:是|为)(?:样品|催化剂)?\s*(?P<label>{_TOKEN})\s*的',match.group())
        if embedded_ref and _valid_label(embedded_ref.group('label'),True): reference = embedded_ref.group('label').rstrip('.')
        unnamed_reference = re.match(r'\s*(?:(?:than|compared\s+(?:with|to))\s+)?(?P<label>(?:the\s+)?(?:control|reference\s+catalyst))\b',rest,re.I)
        reference_description = unnamed_reference.group('label') if unnamed_reference else ''
        relation_end = match.end()+(ref.end() if ref and reference and not embedded_ref else unnamed_reference.end() if unnamed_reference else 0)
        relation = _relation(view,'relative_change',offset+match.start(),offset+relation_end,
                             value=_float(match.group('value')),unit=unit,operator=op,reference_sample=reference,
                             reference_description=reference_description)
        if low is not None:
            high = abs(_float(match.group('value'))) if _float(match.group('value')) is not None else None
            range_start = prefix_range.start() if prefix_range else before.start()
            relation.update(view.evidence(offset+range_start,offset+relation_end),value=low,value_high=high,operator='unknown')
            return dict(kind='ambiguous',value=None,unit='',operator='unknown',relation=relation,
                        warning='原文为倍数/变化幅度区间，完整保留两端；未把上界截成单个倍数，也未推算绝对性能。',sample='',reference=reference)
        claim_prefix = tail[:match.end()]
        relation['direction'] = ('decrease' if re.search(r'\b(?:decreas\w*|reduc\w*|lower|smaller|less|fell)\b|降低|下降|减少',claim_prefix,re.I)
                                 else 'increase' if re.search(r'\b(?:increas\w*|improv\w*|enhanc\w*|higher|greater|larger|more|rose)\b|提高|提升|增加|增强',claim_prefix,re.I)
                                 else 'unspecified')
        if 'factor' in match.group().lower(): relation['ratio_definition'] = 'reference_divided_by_target'
        elif unit=='fold' and op=='ratio': relation['ratio_definition'] = 'target_divided_by_reference'
        elif unit=='%': relation['ratio_definition'] = 'relative_change_percent'
        elif op=='delta': relation['ratio_definition'] = 'difference_in_percentage_points'
        else: relation['ratio_definition'] = 'unresolved'
        warning = ('百分点差值不是绝对转化率，也不同于相对百分比变化。' if op=='delta' else
                   '倍数基准存在歧义，未自动按最终倍数或增加倍数换算。' if op=='unknown' else
                   '这是相对变化；未从相对值计算绝对性能，未补参考样品的基准数值。')
        return dict(kind='ambiguous' if op=='unknown' else 'comparison',value=_float(match.group('value')),unit=unit,operator=op,
                    relation=relation,warning=warning,sample='',reference=reference)
    return None


def _fill_measurement(item,q,view,offset=0,truncated=False):
    item.update(kind='range' if q['value_high'] is not None else 'absolute',**{k:q[k] for k in ('value','value_high','unit','operator')})
    if q['invalid']:
        item.update(kind='ambiguous',value=None,value_high=None,operator='unknown')
        item['warnings'].append('数值格式无效、单位冲突或超出有限数值范围，未截取较短的数字。')
    elif q['unit'] and not _unit_matches(item['metric'],q['unit']):
        item.update(kind='ambiguous',value=None,value_high=None,operator='unknown')
        item['warnings'].append('数值单位与该指标不匹配，未把温度等条件误作性能。')
    if not q['unit']: item['warnings'].append('未明确给出单位/无量纲约定，未自动补百分比。')
    if q['value_high'] is not None: item['warnings'].append('这是区间，不是精确单点；须保留上下界。')
    if q['approximate']: item['warnings'].append('原文为近似值，保留近似含义，不能当精确等式。')
    if q['operator'] in ('gt','ge','lt','le'): item['warnings'].append('这是边界值，不能当作精确实测值。')
    if q.get('uncertainty'):
        error = q['uncertainty']
        item['uncertainty'] = {k:error[k] for k in ('value','unit','type')} | view.evidence(offset+error['start'],offset+error['end'])
        item['warnings'].append('原文含±不确定度；未假定其为标准差、标准误或置信区间，未当作精确值。')
    if not item['sample_label']: item['warnings'].append('未能明确对应到唯一的样品，需要人工绑定。')
    if not item['conditions']: item['warnings'].append('本分句未得到可明确绑定的测试条件，需补充证据。')
    values = [v for v in (q['value'],q['value_high']) if v is not None]
    plausible = not (any(x in item['metric'] for x in ('conversion','selectivity','yield')) and q['unit'] in ('%','dimensionless') and
                     any(v<0 or v>(100 if q['unit']=='%' else 1) for v in values))
    ordered = q['value_high'] is None or q['value'] is not None and q['value_high'] >= q['value']
    if not plausible: item['warnings'].append('转化率/选择性/收率超出该单位的常见物理范围，保留原值并要求核验。')
    if not ordered: item['warnings'].append('上下界顺序异常，未自动排序或修正原文。')
    precise_conditions = all(c.get('operator','eq') in ('eq','range') and
                             (c.get('value_high') is None or c['value_high']>=c['value'])
                             for c in item['conditions'].values())
    item['numeric_eligible'] = bool(item['kind'] in ('absolute','range') and item['value'] is not None and
                                    item['assertion_scope']=='current_study' and item['sample_label'] and item['conditions'] and
                                    _unit_matches(item['metric'],q['unit']) and q['operator'] in ('eq','range') and
                                    not q['approximate'] and not q.get('uncertainty') and not truncated and plausible and ordered and precise_conditions)
    ev = view.evidence(offset+q['start'],offset+q['end'])
    item['semantic_id_key'] += f":{ev['start']}:{ev['end']}"
    relation_type = 'measurement' if item['value'] is not None and _unit_matches(item['metric'],q['unit']) else 'unresolved_quantity'
    item['relations'].append(dict(type=relation_type,**ev,value=q['value'],value_high=q['value_high'],unit=q['unit'],operator=q['operator'],sample_label=item['sample_label']))
    item['confidence_reasons'].append('数值表达式与指标具有明确的同分句语法连接；仍需人工核对。')


def _respectively(view,metrics,truncated):
    """Only explicit one-dimensional ordered pairing, never a guessed grid."""
    text = view.text
    if not re.search(r'\brespectively\b|分别',text,re.I): return None
    labels = list(dict.fromkeys(s[2] for s in _samples(text)))
    last = metrics[-1]
    tail = text[last[1]:]
    pos,context_unit,_ = _lead(tail)
    values = []
    for _ in range(20):
        q = _quantity(tail,pos,allow_range=False)
        if not q: break
        values.append(q); pos = q['end']
        sep = re.match(r'\s*(?:,\s*(?:and\s+)?|and\s+|和|、)\s*',tail[pos:],re.I)
        if not sep: break
        pos += sep.end()
    if len(values)<2: return None
    units = set(q['unit'] for q in values if q['unit'])
    common_unit = next(iter(units)) if len(units)==1 else context_unit if not units else ''
    valid = (len(metrics)==1 and len(labels)==len(values)) or (len(labels)<=1 and len(metrics)==len(values))
    valid = valid and not any(q['invalid'] or q.get('uncertainty') for q in values)
    if not valid:
        item = _base(view,0,len(text),metrics[0][2] if len(metrics)==1 else 'unknown',truncated,metrics[0][0],'unresolved_pairing')
        item['warnings'].append('分别对应的样品/指标/数值数量不一致或存在多个配对维度，未猜测顺序。')
        item['relations'].append(_relation(view,'unresolved_pairing',0,len(text),samples=labels,values=[q['value'] for q in values]))
        return [item]
    result = []
    for i,q in enumerate(values):
        metric = metrics[0] if len(metrics)==1 else metrics[i]
        item = _base(view,0,len(text),metric[2],truncated,metric[0],f'respectively_{i}')
        if len(labels)>1: item['sample_label'] = labels[i]
        if not q['unit']: q['unit'] = common_unit
        _fill_measurement(item,q,view,last[1],truncated)
        item['relations'].append(_relation(view,'respectively',0,len(text),position=i,sample_label=item['sample_label'],metric=metric[2],value=q['value']))
        item['confidence_reasons'].append('原文明确使用respectively/分别，样品或指标与数值列表数量一致；测试条件仍须单独核对。')
        result.append(item)
    return result


def _without_expectation_aside(text):
    """A retrospective 'as expected' aside does not make a result a forecast.

    This is used only for assertion checks. Original quotations and offsets
    are never changed. Other modal words in the claim remain in place.
    """
    pattern=r'\bas\s+(?:one\s+(?:would\s+)?expect(?:s)?|expected)\b|正如预期(?:所示)?|符合预期'
    return re.sub(pattern,lambda m:' '*len(m.group()),text,flags=re.I)


def _claim_flags(text):
    assertion = re.split(r',\s*(?:which|possibly|probably)|\bbecause\b|\bdue\s+to\b',text,maxsplit=1,flags=re.I)[0]
    return bool(_NEGATED.search(assertion)),bool(_OUTLOOK.search(_without_expectation_aside(assertion)))


def semantic_candidates(text: str) -> list[dict]:
    """Exact-offset unreviewed local candidates; maximum two million chars.

    Additive fields: semantic_id_key, relations, context_evidence,
    confidence_reasons, optional uncertainty. All quotations refer to the same
    original input. Context does not change scope, sample, conditions or review.
    """
    if not isinstance(text,str): raise TypeError('语义候选输入必须是字符串。')
    if len(text)>MAX_TEXT_LENGTH: raise ValueError('单次语义筛查文本过长，请按页或按章节处理（最多 200 万字符）。')
    candidates,previous = [],None
    for start,end,truncated in _spans(text):
        view = _View(text,start,end)
        metrics = _metrics(view.text)
        sentence_items = []
        paired = _respectively(view,metrics,truncated) if metrics else None
        if paired is not None:
            sentence_items.extend(paired)
        else:
            for a,b in _clauses(view.text):
                local = view.text[a:b]
                matches = [(a+x,a+y,n) for x,y,n in _metrics(local)]
                if not matches: continue
                if len(set(m[2] for m in matches))>1:
                    shared = _comparison(view,a,b,matches[-1][1],matches[-1][2])
                    if shared and not any(_measurement(view.text[m[1]:matches[i+1][0]]) or
                                          _comparison(view,a,matches[i+1][0],m[1],m[2])
                                          for i,m in enumerate(matches[:-1])):
                        item = _base(view,a,b,'unknown',truncated,matches[0][0],'shared_comparison')
                        item.update(**{k:shared[k] for k in ('kind','value','unit','operator')})
                        item['relations'].append(shared['relation'])
                        item['warnings'].extend([shared['warning'],'多个指标共享相对描述，未把同一数值任意分配给某个指标。'])
                        sentence_items.append(item)
                        continue
                    coordinated = all(re.fullmatch(r'\s*(?:and|和|与|、|,)\s*',view.text[m[1]:matches[i+1][0]],re.I)
                                      for i,m in enumerate(matches[:-1]))
                    if coordinated and _measurement(view.text[matches[-1][1]:b]):
                        item = _base(view,a,b,'unknown',truncated,matches[0][0],'unresolved_metrics')
                        item['warnings'].append('多个指标共用数值表达，但未明确分别对应，未把数值只分配给最后一个指标。')
                        sentence_items.append(item)
                        continue
                for i,(ms,me,metric) in enumerate(matches):
                    right = matches[i+1][0] if i+1<len(matches) else b
                    left = matches[i-1][1] if i else a
                    tail = view.text[me:right]
                    comparison = _comparison(view,a,right,me,metric)
                    q = _measurement(tail)
                    offset = me
                    if q is None:
                        q = _preceding_measurement(view.text[left:ms]); offset = left
                    negated,outlook = _claim_flags(view.text[a:right])
                    item = _base(view,a,b,metric,truncated,ms)
                    if comparison:
                        item.update(**{k:comparison[k] for k in ('kind','value','unit','operator')})
                        item['relations'].append(comparison['relation'])
                        item['warnings'].append(comparison['warning'])
                        if comparison['sample']: item['sample_label'] = comparison['sample']
                        item['reference_sample'] = comparison['reference']
                        if item['reference_sample']:
                            options = [s[2] for s in _samples(view.text[a:b]) if s[2]!=item['reference_sample']]
                            if not comparison['sample']:
                                item['sample_label'] = options[0] if len(set(options))==1 else ''
                    elif q:
                        if re.match(r'\s*(?:and\s+|,\s*)\d',tail[q['end']:] if offset==me else '',re.I) and q['value_high'] is None:
                            item['warnings'].append('同一指标出现多个未明确配对的数值，未只选第一个数值。')
                        else: _fill_measurement(item,q,view,offset,truncated)
                    elif _QUALITATIVE.search(local):
                        item['kind'] = 'qualitative'
                        item['warnings'].append('定性评价，没有可直接编码的绝对测量值。')
                    elif not (negated or outlook): continue
                    if outlook or negated:
                        item.update(kind='negated' if negated else 'outlook',value=None,value_high=None,operator='unknown',unit='',numeric_eligible=False)
                        item['warnings'].append('被否定的是陈述/改善；未观察到不等于测得零。' if negated else '含预期或可能性措辞，不能作为已测得结果。')
                        for relation in item['relations']: relation['assertion'] = 'negated' if negated else 'modal'
                    sentence_items.append(item)
        if not metrics:
            m = re.search(rf'(?<![A-Za-z0-9−+^/-])(?P<lo>{NUMBER})\s*{_RANGE_SEPARATOR}\s*(?P<hi>{NUMBER})\s*(?P<unit>°\s*C|℃|K\b)',view.text,re.I)
            if m and not _PREPARATION.search(view.text):
                item = _base(view,0,len(view.text),'unknown',truncated,m.start())
                item.update(kind='range',value=_float(m.group('lo')),value_high=_float(m.group('hi')),unit=_unit(m.group('unit')),operator='range',conditions={})
                item['warnings'].append('温度范围尚未绑定具体指标/步骤，不能当作性能数值。')
                sentence_items.append(item)
        for item in sentence_items:
            # All paths, including respectively and shared comparisons, obey
            # assertion scope. A denied list is not a list of observations.
            negated,outlook = _claim_flags(_View(text,item['start'],item['end']).text)
            if negated or outlook:
                item.update(kind='negated' if negated else 'outlook',value=None,value_high=None,operator='unknown',unit='',numeric_eligible=False)
                for relation in item['relations']: relation['assertion'] = 'negated' if negated else 'modal'
                warning = '被否定的是陈述/改善；未观察到不等于测得零。' if negated else '含预期或可能性措辞，不能作为已测得结果。'
                if warning not in item['warnings']: item['warnings'].append(warning)
            # A reported model output is a real statement but not an observed
            # experimental target. Keep its original number inside the linked
            # relation, never let the point-label path silently promote it.
            claim_text = _View(text,item['start'],item['end']).text
            if _REQUIREMENT.search(claim_text):
                item.update(kind='outlook',value=None,value_high=None,operator='unknown',unit='',numeric_eligible=False,
                            quantity_role='background',source_role='requirement')
                item['warnings'].append('本句给出性能要求或设计目标，不是本研究测得的结果；原要求值保留在关系证据中。')
                for relation in item['relations']:
                    if relation['type']=='measurement': relation['type']='requirement'
                    relation['assertion']='required_not_measured'
            if _PREDICTED.search(claim_text) and item['kind'] in ('absolute','range'):
                item.update(kind='ambiguous',value=None,value_high=None,operator='unknown',unit='',numeric_eligible=False)
                item['warnings'].append('本句含预测/模拟结果措辞；原数值保留在关系证据中，未混入实验实测标签，需核对计算方法与结果类型。')
                for relation in item['relations']:
                    if relation['type']=='measurement':
                        relation['type']='model_output'
                        relation['assertion']='predicted_or_simulated'
            if previous and not truncated and not item['sample_label']:
                previous_view = _View(text,previous[0],previous[1])
                prior_samples = _samples(previous_view.text)
                same_metric = item['metric'] in [m[2] for m in _metrics(previous_view.text)]
                if prior_samples or same_metric:
                    item['context_evidence'].append(dict(type='previous_sentence',**previous_view.evidence(),suggested_samples=list(dict.fromkeys(s[2] for s in prior_samples)),binding_status='needs_review'))
                    item['warnings'].append('附上相邻前句供人工消歧；未自动继承样品、条件或研究归属。')
            if re.search(r'\b\d+(?:\.\d+)?\s+8C\b',item['evidence_quote']):
                item['warnings'].append('文字层含8C等可能的温度字符错误，未擅自替换为°C；请回看PDF。')
            candidates.append(item)
        previous = (start,end)
    return candidates
