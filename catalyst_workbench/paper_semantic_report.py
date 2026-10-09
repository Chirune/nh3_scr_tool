"""Human-readable inventory of semantic evidence, separate from target values."""
from __future__ import annotations

import copy
from collections import Counter
from datetime import datetime
import html
import json
from pathlib import Path
import uuid

from paper_semantic_facets import FACET_LABELS
from paper_semantic_audit import review_prompts, repeated_quote_groups, assertion_modes

EXTRACTION_VERSION = 'scientific-relations/2.0'

KIND_LABELS = {'absolute': '明示数值', 'range': '区间', 'comparison': '相对比较 / 倍数',
    'ratio': '比值 / 比例', 'calculation': '计算关系 / 定义', 'qualitative': '定性描述 / 解释',
    'outlook': '展望 / 可能性', 'negated': '否定', 'requirement': '要求 / 目标',
    'ambiguous': '含义待确认'}
STATE_LABELS = {'unreviewed': '待核对', 'reviewed': '已核对', 'excluded': '不采用', 'stale': '已过期'}
RELATION_LABELS = {'relative_change': '相对变化', 'difference': '差值', 'ratio': '比值',
    'ambiguous_fold_change': '倍数含义有歧义', 'from_to': '从原值变为新值', 'change': '从原值变为新值',
    'calculation': '计算依据', 'constraint': '公式 / 定义约束', 'explanation': '作者解释 / 归因',
    'correlation': '相关关系', 'qualitative_comparison': '定性比较', 'trend': '变化趋势',
    'negated_statement': '否定表述', 'requirement': '要求 / 目标'}
ASSERTION_LABELS = {'observed_or_reported': '原文报告（实测或计算仍需核对）',
    'reported': '原文报告（来源仍需核对）', 'outlook': '展望 / 可能性', 'future': '未来预期',
    'hypothetical': '假设 / 预期', 'negated': '否定', 'requirement': '要求 / 目标',
    'ambiguous': '含义待确认', 'unknown': '陈述角色待核对', 'asserted': '原文陈述（实测或解释仍需核对）',
    'modal': '预期 / 可能 / 推测', 'required': '要求 / 目标', 'conditional': '依赖假设或条件',
    'recommended':'作者建议（尚非实测结论）','future_or_modal':'未来预期 / 可能性',
    'recommended_or_required':'建议 / 要求（尚非实测结论）'}


def facet_labels(item):
    return list(dict.fromkeys(FACET_LABELS.get(f.get('type'),f.get('type','待核对')) for f in item.get('semantic_facets',[])))


def facet_lines(item):
    lines=[]
    if item.get('semantic_facets'):lines.append('科研关系：'+' / '.join(facet_labels(item)))
    roles={'preparation':'制备 / 处理条件','test':'测试条件','measurement':'测试条件','unknown':'条件用途待确认',
           'exposure':'扰动 / 暴露条件','duration':'时间 / 循环线索'}
    for c in item.get('conditions_mentions',[]):
        role=c.get('role','unknown')
        lines.append(roles.get(role,role)+'：'+str(c.get('evidence_quote') or c.get('quote','')))
    modes=assertion_modes(item)
    if modes:lines.append('陈述性质：'+'；'.join(ASSERTION_LABELS.get(m,m) for m in modes))
    metric_names={'conversion':'转化率','selectivity':'选择性','activity':'活性','stability':'稳定性',
                  'adsorption capacity':'吸附容量','yield':'收率'}
    for facet in item.get('semantic_facets',[]):
        kind=facet.get('type');p=facet.get('parameters',{})
        scoped_modes=[m for m in assertion_modes(facet) if m!='asserted' and m not in modes]
        if scoped_modes:lines.append('相关原句的限定：'+'；'.join(ASSERTION_LABELS.get(m,m) for m in scoped_modes))
        if kind=='stability':
            for value in p.get('durations',[]):lines.append('时长线索：'+value.get('evidence_quote',''))
            for value in p.get('cycles',[]):lines.append('循环线索：'+value.get('evidence_quote',''))
            lines.append('仍需核对起始与终止性能；未假定时间零点。')
        elif kind=='optimum':
            form={'window':'文中温度 / 操作窗口','extremum':'文中极值'}.get(p.get('reported_form'),'最佳范围待核对')
            direction={'maximum':'最高 / 最大','minimum':'最低 / 最小','mixed_or_ambiguous':'同时提到多个极值，需分别核对','unspecified':'极值方向未细分'}.get(p.get('extremum_type'),'')
            lines.append(form+'：'+direction+'；只限于原文比较范围。')
        elif kind=='perturbation_tolerance':
            response={'inhibition':'抑制 / 中毒 / 下降','promotion':'促进','reported_tolerance':'文中报告耐受','unknown':'响应方向待确认'}.get(p.get('response_type'),'待确认')
            lines.append('扰动：'+'、'.join(p.get('perturbations',[]))+'；响应：'+response+'；仍需核对是否实测。')
            context={'evaluation_criterion':'耐受性的评价标准或要求，尚非本次测试结果',
                'recovery_context':'中毒后的恢复语境，不能仅凭中毒一词确定当前下降',
                'negated_or_denied_effect':'作用被否定或未观察到，不能改成正向作用',
                'multiple_or_contrasting_effects':'同时出现多种或相反作用，需逐一配对',
                'tolerance_property':'报告耐受性质，适用浓度与持续时间仍需核对',
                'explicit_adverse_predicate':'原句有抑制或下降谓词，样品和条件仍需核对',
                'explicit_beneficial_predicate':'原句有促进谓词，样品和条件仍需核对',
                'perturbation_mentioned_without_direction':'仅提及扰动，作用方向未明确'}.get(p.get('response_context'))
            if context:lines.append('响应语境：'+context)
        elif kind=='tradeoff':
            lines.append('涉及指标：'+'、'.join(metric_names.get(m,m) for m in p.get('metrics',[]))+'；是否同组实验待核对。')
        elif kind=='uncertainty':
            error_type={'standard_deviation':'标准差','standard_error':'标准误','confidence_interval':'置信区间','unspecified':'误差定义待确认'}.get(p.get('uncertainty_type'),'误差定义待确认')
            lines.append('误差类型：'+error_type)
            for value in p.get('reported_values',[]):lines.append('原文误差表述：'+value.get('evidence_quote',''))
        elif kind=='detection_limit':
            lines.append('检出状态：'+('未检出 / 低于检测限（不能填0）' if p.get('detection_status')=='not_detected_or_below_limit' else '检测 / 定量限相关说明'))
            if not p.get('reported_limits'):lines.append('具体限值：本句未识别到，不能自行补入。')
            for value in p.get('reported_limits',[]):lines.append('限值原句：'+value.get('evidence_quote',''))
        elif kind=='recovery':lines.append('恢复前后基准未自动补齐；需核对处理过程与恢复程度。')
    return lines


def adoption_label(item):
    from paper_workspace import SEMANTIC_USES
    if item.get('stale'):return '旧判断，仅供追溯'
    if item.get('review_status') == 'excluded':return SEMANTIC_USES['exclude']
    return SEMANTIC_USES.get(item.get('semantic_adoption', {}).get('purpose'), SEMANTIC_USES['pending'])


def relation_lines(relation):
    """Display operands as relationship values, never as absolute performance."""
    lines = [RELATION_LABELS.get(relation.get('type'), str(relation.get('type', '关系')))]
    labels = [('target', '比较主体'), ('reference', '参照对象'), ('numerator', '分子'),
              ('denominator', '分母'), ('value', '关系中的量'), ('value_high', '关系量上限'),
              ('before', '变化前'), ('after', '变化后'), ('from_value', '变化前'), ('to_value', '变化后'),
              ('source_value', '变化前'), ('target_value', '变化后'),
              ('strength', '原文程度词'), ('formula', '原文公式'), ('expression', '原文表达式')]
    for key, label in labels:
        value = relation.get(key)
        if value is not None and value != '':
            unit = (' ' + str(relation.get('unit', ''))) if key in ('value','value_high','before','after','from_value','to_value','source_value','target_value') else ''
            lines.append(f'{label}：{value}{unit}')
    if relation.get('direction'):
        lines.append('方向：' + {'increase':'增加','decrease':'减少','positive':'正相关',
            'negative':'负相关','unknown':'待核对','unspecified':'待核对'}.get(relation['direction'],str(relation['direction'])))
    if relation.get('ratio_definition'):
        lines.append('定义：' + {'target_divided_by_reference':'目标 ÷ 对照',
            'reference_divided_by_target':'对照 ÷ 目标','relative_change_percent':'相对对照的变化百分比',
            'difference_in_percentage_points':'百分点差值','unresolved':'需核对原文定义',
            'unresolved_increase_by_fold':'“增加了几倍”的基准有歧义，暂不计算'}.get(relation['ratio_definition'],str(relation['ratio_definition'])))
    if relation.get('operator'):
        lines.append('关系量的形式：'+{'eq':'等于','gt':'大于','ge':'大于等于','lt':'小于','le':'小于等于'}.get(relation['operator'],str(relation['operator'])))
    if relation.get('assertion'):
        lines.append('陈述性质：' + ASSERTION_LABELS.get(relation['assertion'],str(relation['assertion'])))
    if relation.get('magnitude_known') is False:lines.append('变化量：未明确给出，不补造数值')
    if relation.get('formula_references'):lines.append('原文公式编号：' + ', '.join(map(str,relation['formula_references'])))
    if relation.get('formula_status')=='missing_or_unreadable':lines.append('公式：文字层缺失或不可辨，需对照原页')
    return lines


def semantic_inventory(project):
    rows = []
    repeated=repeated_quote_groups(project.get('evidence',[]))
    for evidence in project.get('evidence', []):
        if evidence.get('branch') != 'semantic':continue
        row = copy.deepcopy(evidence)
        row['kind_label'] = KIND_LABELS.get(row.get('kind'), row.get('kind', '待确认'))
        if not row.get('metric') or row.get('metric')=='unknown':row['metric']='指标 / 对象待确认'
        row['state_label'] = STATE_LABELS.get('stale' if row.get('stale') else row.get('review_status','unreviewed'), '待核对')
        row['adoption_label'] = adoption_label(row)
        row['relationship_summary'] = '\n\n'.join('\n'.join(relation_lines(r)) for r in row.get('relations', []))
        row['facet_labels']=facet_labels(row)
        row['scientific_summary']='\n'.join(facet_lines(row))
        row['review_prompts']=review_prompts(row)
        row['repeated_statement_sources']=repeated.get(row['evidence_id'],[])
        rows.append(row)
    rows.sort(key=lambda r:(bool(r.get('stale')),r.get('page',0),r.get('start',0),r.get('kind','')))
    current = [r for r in rows if not r.get('stale')]
    counts = dict(Counter(r['kind_label'] for r in current))
    facet_counts=dict(Counter(label for r in current for label in r['facet_labels']))
    pending = sum(r.get('review_status','unreviewed') == 'unreviewed' for r in current)
    message = f'本篇 {len(current)} 条当前语义候选，{pending} 条待核对。可按条件依赖、稳定性、失活恢复等科研关系筛选；一句可含多种关系，句子数不等于实验数。'
    if project.get('semantic_extraction_version')!=EXTRACTION_VERSION:
        message += ' 此档案还未运行扩展提取，请点击“重新扫描全文语义”。'
    return {'rows':rows,'counts':counts,'facet_counts':facet_counts,'current_count':len(current),'pending_count':pending,'message':message}


def export_semantic_inventory(project):
    """Snapshot all decisions and exact source evidence without changing review."""
    from paper_workspace import verify_source, _csv
    from paper_reading import reading_aid
    verify_source(project)
    report = semantic_inventory(project)
    folder = Path(project['run_dir']) / ('语义提取清单_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:4])
    folder.mkdir(parents=True)
    rows = []
    for e in report['rows']:
        adopted = e.get('semantic_adoption', {})
        aid=reading_aid(project,e)
        e['reading_aid']=aid
        rows.append({'科研关系':' / '.join(e['facet_labels']),'语义类型':e['kind_label'],'指标或对象':e.get('metric'),'原句':e.get('quote',''),
            '中文参考译文（非原始证据）':aid['translation'],'翻译状态':aid['message'],
            '术语解释':'；'.join(t['term']+'：'+t['zh']+'。'+t['explanation'] for t in aid['terms']),
            '阅读核对提示':'；'.join(aid['hints']),'翻译模型':aid['model'],'译文原句指纹':aid['source_key'],
            '页码':e.get('page'),'起始字符':e.get('start'),'结束字符':e.get('end'),
            '关系说明':e['relationship_summary'],'关系原始字段':e.get('relations',[]),
            '科研关系字段':e.get('semantic_facets',[]),'条件及角色':e.get('conditions_mentions',[]),
            '陈述性质':e.get('assertion_status',''),'复核提示':e['review_prompts'],'重复原句出处':e['repeated_statement_sources'],
            '上下文证据':e.get('context_evidence',[]),'样品':e.get('sample_label',''),
            '比较对象':e.get('reference_sample',''),'结果归属':e.get('assertion_scope','unknown'),
            '核对状态':e['state_label'],'如何采纳':e['adoption_label'],'采纳说明':adopted.get('note',''),
            '审核人':adopted.get('reviewer') or e.get('reviewer',''),'核对事项':'；'.join(e.get('warnings',[])),
            '证据ID':e['evidence_id'],'DOI':project['article'].get('doi',''),
            '源PDF指纹':project['article']['source_sha256'],'可直接用于训练':False})
    _csv(folder/'语义关系与采纳决定.csv',rows,list(rows[0]) if rows else ['语义类型','原句','页码','核对状态','如何采纳'])
    payload={'schema_version':'paper-semantic-inventory/1.1','article':project['article'],
        'extraction_version':project.get('semantic_extraction_version','legacy'), 'report':report,
        'scope':'语义候选及人工决定；展望、定性、比值和计算关系不会自动变成绝对实测性能。reading_aid 为本机中文阅读备注，不是原始证据或训练标签；仅导出已生成的译文，不在导出时自动翻译全文。'}
    (folder/'语义与原文证据.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    esc=lambda value:html.escape(str(value if value is not None else ''))
    cards=[]
    for index,e in enumerate(report['rows'],1):
        contexts='\n'.join(c.get('evidence_quote') or c.get('quote','') for c in e.get('context_evidence',[]))
        note=e.get('semantic_adoption',{}).get('note','')
        aid=e['reading_aid']
        reading_note='<aside class="note"><b>中文参考译文（阅读备注）</b><p>'+esc(aid['translation'] or ('原句已是中文。' if aid['status']=='original_chinese' else '尚未生成译文：在程序中选中这条原句即可自动翻译。'))+'</p>'
        if aid['translation']:reading_note+='<p class="muted">本机机器翻译，未作人工翻译审校。科学含义请对照英文原文；译文不用于训练。</p>'
        if aid['terms']:reading_note+='<p><b>本句术语</b></p><ul>'+''.join('<li>'+esc(t['term']+'：'+t['zh']+'。'+t['explanation'])+'</li>' for t in aid['terms'])+'</ul>'
        if aid['hints']:reading_note+='<p><b>对照时注意</b></p><ul>'+''.join('<li>'+esc(h)+'</li>' for h in aid['hints'])+'</ul>'
        reading_note+='</aside>'
        cards.append('<article class="item"><header><b>'+str(index)+'. '+esc(' / '.join(e['facet_labels']) or e['kind_label'])+'</b><span>'+esc(e['kind_label'])+' · 第 '+esc(e.get('page'))+' 页 · '+esc(e['state_label'])+'</span></header>'
            +'<h3>'+esc(e.get('metric'))+'</h3><blockquote>'+esc(e.get('quote',''))+'</blockquote>'
            +reading_note
            +('<pre>'+esc(e['scientific_summary'])+'</pre>' if e['scientific_summary'] else '')
            +('<pre>'+esc(e['relationship_summary'])+'</pre>' if e['relationship_summary'] else '')
            +('<details><summary>上下文证据（关联仍须核对）</summary><blockquote>'+esc(contexts)+'</blockquote></details>' if contexts else '')
            +'<p class="decision">采纳方式：'+esc(e['adoption_label'])+('；'+esc(note) if note else '')+'</p>'
            +'<details><summary>本条需要核对什么</summary><ul>'+''.join('<li>'+esc(p)+'</li>' for p in e['review_prompts'])+'</ul></details>'
            +('<p class="muted">本篇另有相同原句：'+esc('；'.join('第 '+str(p['page'])+' 页' for p in e['repeated_statement_sources']))+'。仍保留各处证据，不算独立实验。</p>' if e['repeated_statement_sources'] else '')
            +'<p class="muted">'+esc('；'.join(e.get('warnings',[])))+'</p></article>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>全文语义关系与复核清单</title>
<style>body{font:16px/1.7 "Microsoft YaHei",sans-serif;max-width:1180px;margin:36px auto;padding:0 24px;background:#f2f6f8;color:#193e4c}h1{font-size:28px}header{display:flex;justify-content:space-between;gap:20px}header b{color:#17637b}span,.muted{color:#58707c}.item{background:white;border:1px solid #cedee5;border-radius:10px;padding:20px 24px;margin:18px 0}.decision,.note{background:#e7f3f3;padding:12px}.note{border-left:4px solid #177080}blockquote{white-space:pre-wrap;margin:12px 0;background:#f8fafb;padding:14px;border-left:3px solid #b4c7ce}pre{white-space:pre-wrap;font:inherit}input{font:inherit;padding:10px;width:min(700px,90%);border:1px solid #a8c0cc;border-radius:5px}a{color:#126b7d}</style>'''
    page+='<h1>全文语义关系与复核清单</h1><p>'+esc(project['article'].get('title'))+'<br>DOI：'+esc(project['article'].get('doi',''))+'</p><p>'+esc(report['message'])+'</p>'
    page+='<p class="note">先核对原句，再决定保留为比较关系、计算约束、定性结论、机理解释或背景展望。“将会大幅增长”保留方向与预期性质，不猜测增幅。本页为导出快照；请在程序的“标注如何采纳”中保存决定。</p>'
    page+='<p><a href="语义关系与采纳决定.csv">打开 CSV 清单</a> · <a href="语义与原文证据.json">完整证据记录</a></p><input id="query" placeholder="筛选类型、原句、指标或采纳方式"><p id="shown"></p>'
    page+=''.join(cards)+'<script>const q=document.getElementById("query"),items=[...document.querySelectorAll(".item")];function filter(){let n=0;for(const el of items){el.hidden=!el.textContent.toLowerCase().includes(q.value.toLowerCase());if(!el.hidden)n++;}document.getElementById("shown").textContent="当前显示 "+n+" 条";}q.addEventListener("input",filter);filter();</script></html>'
    (folder/'00_本篇语义清单.html').write_text(page,encoding='utf8')
    return folder
