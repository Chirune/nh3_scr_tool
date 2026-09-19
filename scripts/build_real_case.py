"""Reproduce the evidence-curated case study, separate from automatic extraction.

Run from the tool directory after downloading the linked supplement.
The curator's decisions are explicit here, not disguised as NLP inference.
"""
import argparse
import collections
import html
import json
import re
from pathlib import Path
from scrtool.core import read_json, write_json, write_csv
from scrtool.ingest import blocks
from scrtool.annotations import import_annotations
from scrtool.cli import run_export
from scrtool.features import build_features

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/real_paper'
PAPER = next((ROOT.parent / '论文').glob('*.pdf'))
DOI = '10.1038/s41467-026-72879-7'
source = {b['locator']: b for b in blocks(PAPER, DOI)}
annotations = {'paper_id': DOI, 'author': 'Codex agent; evidence-curated, not automatic extraction', 'records': []}


def span(page, start, end):
    text = source[f'page:{page}']['text']
    first = re.search(r'\s+'.join(re.escape(w) for w in start.split()), text)
    if not first:
        raise ValueError(f'Page {page}: missing start {start}')
    last = re.search(r'\s+'.join(re.escape(w) for w in end.split()), text[first.start():])
    if not last:
        raise ValueError(f'Page {page}: missing end {end}')
    return text[first.start():first.start()+last.end()]


def add(page, catalyst, prop, value, unit, evidence, conditions=None, note=''):
    annotations['records'].append(dict(locator=f'page:{page}', catalyst=catalyst, property=prop, raw_value=str(value), unit=unit, evidence=evidence, conditions=conditions or {}, note=note))


composition = span(2, 'To investigate its mechanistic origin', 'respectively.')
for cat, val in [('Cu-0.5', '.48'), ('Cu-1.4', '1.38'), ('Cu-2.5', '2.48')]:
    add(2, cat, 'cu_content', val, 'wt%', composition, note='Explicit respectively-list; conflicting other locations retained.')

calcination = span(11, 'The obtained Cu-SSZ-13 solid', '550 °C for 5 h.')
for cat in ['Cu-0.5', 'Cu-1.4', 'Cu-2.5']:
    add(11, cat, 'calcination_temperature', 550, 'degC', calcination, note='Shared final preparation step of the three model catalysts; not parent-support calcination.')
    add(11, cat, 'calcination_time', 5, 'h', calcination)

hydrothermal = span(11, 'The hydrothermal synthesis was carried out', 'under constant stirring.')
add(11, 'Na-SSZ-13', 'hydrothermal_temperature', 165, 'degC', hydrothermal, note='Parent support preparation')
add(11, 'Na-SSZ-13', 'hydrothermal_time', 96, 'h', hydrothermal, note='Parent support preparation')

critical = span(2, 'critical ANR', 'at 220 °C).')
for cat, temp, val in [('Cu-1.4', 220, 1.25), ('Cu-1.4', 200, 1.1), ('Cu-1.4', 160, 0.6), ('Cu-0.5', 220, 0.4)]:
    add(2, cat, 'critical_anr', val, '1', critical, {'temperature': dict(raw_value=temp, unit='degC', evidence=critical)}, note='Explicit text examples; distinct target from NOx conversion')

write_json(OUT / 'curated_annotations.json', annotations)
curated = import_annotations(PAPER, annotations, OUT / 'curated')
groups = ['main_extracted', 'supplement_extracted', 'fig1a', 'fig1b', 'curated']
all_records = []
for group in groups:
    all_records.extend(read_json(OUT / group / 'candidates.json'))

# Identify composition discrepancies without choosing a preferred publication location.
by_sample = collections.defaultdict(list)
for r in all_records:
    if r['property'] == 'cu_content' and r['value'] is not None:
        by_sample[(r['catalyst'], r['property'])].append(r)
conflicts = []
for (cat, prop), rs in by_sample.items():
    values = sorted(set(r['value'] for r in rs))
    if len(values) > 1:
        conflicts.append(dict(catalyst=cat, property=prop, values=values, records=[r['record_id'] for r in rs], reason='Published locations disagree; no automatic resolution'))
        for r in rs:
            r['issues'].append('cross_source_conflict')

# These decisions follow visual inspection of Table S1 and both plotted overlays.
# They are agent review, not human/domain-expert validation.
for r in all_records:
    if r['issues']:
        continue
    if r['method'] in ['pdf_vector_markers', 'curated_annotation', 'explicit_pair'] or r['source_kind'] == 'pdf_table':
        r['review_status'] = 'approved'
        r['reviewer'] = 'Codex agent visual/evidence audit (not human expert validation)'
        r['review_level'] = 'agent_checked'
        r['review_note'] = 'Source/axes/legend/table inspected in this case study. Recheck before scientific use.'

write_json(OUT / 'combined_reviewed.json', all_records)
write_csv(OUT / 'all_observations.csv', all_records)
write_json(OUT / 'conflicts.json', conflicts)
write_csv(OUT / 'needs_review.csv', [r for r in all_records if r['review_status'] != 'approved'])
run_export(argparse.Namespace(input=str(OUT / 'combined_reviewed.json'), output=str(OUT / 'training')))
build_features(all_records, OUT / 'training')

# Independent visual anchor checks, coarse readings from the original Fig.1a.
checks = []
anchors = [('Cu-0.5', 200, 8), ('Cu-0.5', 300, 25), ('Cu-0.5', 400, 85),
           ('Cu-1.4', 200, 73), ('Cu-1.4', 300, 87), ('Cu-1.4', 400, 88),
           ('Cu-2.5', 200, 92), ('Cu-2.5', 300, 95), ('Cu-2.5', 400, 90)]
for cat, temp, expected in anchors:
    candidates = [r for r in all_records if r.get('figure') == 'Fig.1a' and r['catalyst'] == cat]
    r = min(candidates, key=lambda r: abs(r['conditions']['temperature']['value']-temp))
    error = abs(r['value']-expected)
    checks.append(dict(catalyst=cat, temperature_expected=temp, x_extracted=r['conditions']['temperature']['value'], y_visual=expected, y_extracted=r['value'], tolerance_percentage_points=3, pass_check=error<=3 and abs(r['conditions']['temperature']['value']-temp)<4))
write_json(OUT / 'visual_anchor_checks.json', checks)
if not all(c['pass_check'] for c in checks):
    raise RuntimeError('Visual anchor check failed')

summary = dict(paper_id=DOI, original_pages=14, supplementary_pages=39,
               fig1a_points=46, fig1b_points=55, total_observations=len(all_records),
               approved_by_agent=sum(r['review_status']=='approved' for r in all_records),
               pending=sum(r['review_status']!='approved' for r in all_records),
               conflicts=conflicts, visual_anchor_checks_passed=len(checks),
               evaluation_scope='One paper; not an extraction precision/recall benchmark; vector profiles and curated annotations are case-specific',
               raw_data_status='Authors state data available on request; no author raw experimental dataset obtained',
               supplementary_url='https://media.springernature.com/original/springer-static/esm/art%3A10.1038%2Fs41467-026-72879-7/MediaObjects/41467_2026_72879_MOESM1_ESM.pdf')
write_json(OUT / 'case_report.json', summary)

rows = ''.join('<tr>'+''.join('<td>'+html.escape(str(r.get(k,'')))+'</td>' for k in ['catalyst','property','value','unit','locator','method','review_status','issues'])+'</tr>' for r in all_records)
report = '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>NH3-SCR 真实论文测试</title>
<style>body{font:16px system-ui;max-width:1250px;margin:40px auto;padding:0 20px;color:#183344}h1{font-size:30px}table{border-collapse:collapse;font-size:13px;width:100%}td,th{border:1px solid #ccd7df;padding:6px;text-align:left}th{background:#e6f2f5;position:sticky;top:0}img{max-width:48%}input{padding:10px;width:70%}.note{background:#fff1d4;padding:18px;line-height:1.6}a{color:#006a89}</style>
<h1>NH₃-SCR 真实论文提取测试</h1>
<p>Insights into the mechanisms of NH₃ inhibition on Cu-CHA SCR catalysts · DOI: 10.1038/s41467-026-72879-7</p>
<p>主文 14 页 + 补充材料 39 页。Fig.1a：46 点；Fig.1b：55 点。组成表、制备和正文临界 ANR 已提取。</p>
<div class="note">这是单篇论文的可复现测试，含代理视觉核验及人工式证据整理，不代表全自动提取准确率。曲线点为图中读数。Cu-0.5 的 0.47/0.48 wt% 和 Cu-1.4 的 1.38/1.39 wt% 冲突保留待核验。作者原始实验数据尚未取得。其他机理图、谱图及补充性能曲线尚未数字化。</div>
<p><a href="all_observations.csv">全部观测 CSV</a> · <a href="training/ml_long.csv">性能长表 CSV</a> · <a href="needs_review.csv">待核验 CSV</a> · <a href="case_report.json">测试统计 JSON</a></p>
<h2>绿色框对应从 PDF 读取的离散标记</h2><a href="fig1a/overlay.png"><img src="fig1a/overlay.png"></a><a href="fig1b/overlay.png"><img src="fig1b/overlay.png"></a>
<h2>观测记录</h2><input id="filter" placeholder="筛选催化剂、字段、问题或来源"><table><thead><tr><th>催化剂</th><th>属性</th><th>标准值</th><th>单位</th><th>位置</th><th>提取方式</th><th>核验状态</th><th>问题</th></tr></thead><tbody>'''+rows+'''</tbody></table><script>document.querySelector('#filter').oninput=e=>{for(const r of document.querySelectorAll('tbody tr'))r.hidden=!r.textContent.toLowerCase().includes(e.target.value.toLowerCase())}</script></html>'''
(OUT / 'report.html').write_text(report, encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False))
