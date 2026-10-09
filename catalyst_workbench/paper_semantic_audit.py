"""Source-grounded checks and review prompts for scientific statements.

These checks are not a confidence score or a substitute for scientific review.
They make missing links visible without assigning unreported experimental data.
"""
from __future__ import annotations

import re


def assertion_modes(item):
    """Normalize only this claim's assertion, not its surrounding context."""
    mode=item.get('assertion_status') or item.get('assertion_mode')
    if isinstance(mode,dict):mode=mode.get('status') or mode.get('mode')
    return [m for m in (mode if isinstance(mode,list) else [mode]) if isinstance(m,str) and m]


def source_span_issues(value, text):
    issues=[]
    def walk(obj,path):
        if isinstance(obj,dict):
            if 'evidence_quote' in obj:
                start,end,quote=obj.get('start'),obj.get('end'),obj.get('evidence_quote')
                if (not isinstance(start,int) or isinstance(start,bool) or not isinstance(end,int) or isinstance(end,bool)
                        or not isinstance(quote,str) or not quote or not 0<=start<end<=len(text)
                        or text[start:end]!=quote):
                    issues.append(path+'：原文位置或引文不匹配')
            for key,child in obj.items():walk(child,path+'.'+key)
        elif isinstance(obj,list):
            for i,child in enumerate(obj):walk(child,f'{path}[{i}]')
    walk(value,'candidate')
    return issues


def review_prompts(item):
    facets={f.get('type') for f in item.get('semantic_facets',[])}
    prompts=[]
    if not item.get('sample_label'):
        prompts.append('确认这句话作用于哪个样品、装置或体系；不能仅凭同页出现就关联。')
    modes=assertion_modes(item)
    if item.get('kind') in ('outlook','requirement','negated') or set(modes).intersection(('modal','future','future_or_modal','hypothetical','conditional','recommended','required','recommended_or_required','negated')):
        prompts.append('确认它是实测、计算、假设、预期、建议还是否定；不能直接改为已完成实验。')
    if item.get('assertion_scope')=='prior_work':
        prompts.append('属于他人研究的引述；先找到原始来源，避免当成本篇新实验。')
    if item.get('context_evidence'):
        prompts.append('上下文只供消歧；复核指代和比较对象，不自动继承前句条件。')
    family_prompts={
        'condition_dependence':'核对条件与哪个性能对应，区分制备条件和测试条件。',
        'operating_window':'核对区间边界、单位及成立的测试条件；最佳值通常只限于文中比较范围。',
        'optimum':'核对最优的比较范围与条件，不能当作所有材料的全局最优。',
        'stability':'核对持续时长/循环数、起始与终止性能及测试条件。',
        'deactivation':'核对下降的是哪个性能、发生时间及老化/中毒条件。',
        'recovery':'核对恢复前后基准、处理步骤及是否完全恢复。',
        'tolerance':'核对水/硫等扰动的浓度、持续时间及作用后的性能。',
        'perturbation_tolerance':'核对水/硫等扰动的浓度、持续时间及作用后的性能；响应不等于已证明耐受。',
        'tradeoff':'核对两个指标是否针对同一样品和同一组实验条件。',
        'uncertainty':'核对±、误差棒或置信区间的定义及重复次数；“显著”不自动等于统计显著。',
        'detection_limit':'保留未检出和检测限的区别；未检出不填0。',
        'preparation':'核对步骤顺序与样品归属；焙烧温度不填作反应温度。',
        'sample_alias':'核对简称与材料组成的对应范围，不能把相似名字直接合并。',
    }
    for name in sorted(f for f in facets if f):
        if name in family_prompts:prompts.append(family_prompts[name])
    mentions=item.get('conditions_mentions',[])
    if any(c.get('role')=='preparation' for c in mentions):
        prompts.append('本句有制备/处理条件：焙烧温度不填作反应温度，处理时长不填作稳定运行时长。')
    if any(c.get('role')=='unknown' for c in mentions):
        prompts.append('有条件用途尚不清楚；结合分句和原页确认，不将多个条件任意配给性能。')
    if any('formula_status' in r and r['formula_status']=='missing_or_unreadable' for r in item.get('relations',[])):
        prompts.append('公式文字不完整：回到PDF核对分子、分母、上下标和符号。')
    if item.get('kind') in ('comparison','ratio','ambiguous'):
        prompts.append('核对比较基准、方向和口径；缺少基准绝对值时不反推性能。')
    prompts.append('按研究目的决定用途并记录依据；关系或条件线索不会自动成为训练标签。')
    return list(dict.fromkeys(prompts))


def repeated_quote_groups(evidence):
    """Mark exact repeated statements; do not merge values, reviews or sources."""
    groups={}
    for item in evidence:
        if item.get('branch')!='semantic' or item.get('stale'):continue
        text=re.sub(r'\s+',' ',item.get('quote','')).strip().casefold()
        if not text:continue
        key=(text,item.get('kind'),item.get('metric'),item.get('operator'))
        groups.setdefault(key,[]).append(item)
    out={}
    for group in groups.values():
        if len(group)<2:continue
        refs=[{'evidence_id':e['evidence_id'],'page':e.get('page'),'start':e.get('start'),'end':e.get('end')}
              for e in group]
        for item in group:out[item['evidence_id']]=refs
    return out
