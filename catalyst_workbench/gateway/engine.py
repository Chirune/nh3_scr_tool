"""Local, auditable literature discovery. No language-model calls or credentials."""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import unquote, urlsplit, quote
from urllib.request import Request, urlopen
import uuid
import xml.etree.ElementTree as ET

from adapters import search_crossref, search_europe_pmc, search_openalex, search_core, search_semantic_scholar, lookup_doi, lookup_openalex_doi, AdapterError, _openalex_key, provider_key
from rules import PROFILE_INFO, screen_record

VERSION = '0.4.0'
DECISIONS = {'target': '保留候选', 'review': '待复核', 'non_target': '暂不相关'}
DOI_RE = re.compile(r'10\.\d{4,9}/[^\s<>"\x00-\x1f]+', re.I)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def canonical_doi(value):
    value = unquote(str(value or '')).strip()
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value, flags=re.I)
    value = value.rstrip('.,;，。；')
    return value.lower() if re.fullmatch(r'10\.\d{4,9}/\S+', value) else ''


def valid_web_url(value):
    try:
        parsed = urlsplit(str(value or ''))
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username
    except ValueError:
        return False


def source_route(record):
    """Identify a likely publisher without claiming that its download API is connected."""
    mapping = [
        ('Elsevier', ['sciencedirect.com', 'elsevier.com'], ['elsevier']),
        ('ACS', ['acs.org'], ['american chemical society']),
        ('RSC', ['rsc.org'], ['royal society of chemistry']),
        ('Springer Nature', ['springer.com', 'springernature.com', 'nature.com'], ['springer', 'nature']),
        ('Wiley', ['wiley.com'], ['wiley']),
        ('MDPI', ['mdpi.com'], ['mdpi']),
        ('Taylor & Francis', ['tandfonline.com'], ['taylor', 'informa']),
        ('Frontiers', ['frontiersin.org'], ['frontiers']),
    ]
    candidates = record.get('fulltext_candidates') or []
    urls = [record.get('url', '')] + [c.get('url', '') for c in candidates]
    hosts = [urlsplit(u).hostname or '' for u in urls if valid_web_url(u)]
    publisher = str(record.get('publisher') or '').lower()
    group, identified_by = '其他 / 未识别', '元数据不足'
    for label, domains, names in mapping:
        if any(h == d or h.endswith('.' + d) for h in hosts for d in domains):
            group, identified_by = label, '链接域名'
            break
    if group == '其他 / 未识别':
        for label, domains, names in mapping:
            if any(n in publisher for n in names):
                group, identified_by = label, '出版方元数据（未验证最终跳转）'
                break
    if group=='其他 / 未识别':
        doi=canonical_doi(record.get('doi'))
        prefixes={'10.1016/':'Elsevier','10.1006/':'Elsevier','10.1021/':'ACS','10.1039/':'RSC',
                  '10.1007/':'Springer Nature','10.1038/':'Springer Nature','10.1186/':'Springer Nature',
                  '10.1002/':'Wiley','10.1111/':'Wiley','10.3390/':'MDPI','10.1080/':'Taylor & Francis'}
        for prefix,label in prefixes.items():
            if doi.startswith(prefix):
                group,identified_by=label,'DOI前缀推断（需核对当前平台）'
                break
    open_pmc = any(is_open_pmc_candidate(c) for c in candidates)
    if open_pmc:
        hint = '有 Europe PMC 开放全文 XML 入口，可尝试获取；以实际响应为准'
    elif candidates:
        hint = '已有全文候选链接；访问权限和可用性尚未验证'
    else:
        hint = '目前只有论文页面入口，全文需进一步查找'
    entry = record.get('url', '')
    if not valid_web_url(entry):
        doi = canonical_doi(record.get('doi'))
        entry = 'https://doi.org/' + quote(doi, safe='/():;') if doi else ''
    return {'publisher_group': group, 'identified_by': identified_by,
            'access_hint': hint, 'official_entry': entry, 'open_xml_available': open_pmc}


def is_open_pmc_candidate(candidate):
    url = candidate.get('url', '')
    if not valid_web_url(url):
        return False
    p = urlsplit(url)
    return p.hostname == 'www.ebi.ac.uk' and bool(re.fullmatch(
        r'/europepmc/webservices/rest/PMC\d+/fullTextXML', p.path)) and str(
        candidate.get('access_status', '')).lower() in {'open', 'open_access', 'oa', 'open_access_confirmed', 'open_access_reported'}


def _emit(progress, message):
    if progress:
        progress(message)


def _cancelled(event):
    return event is not None and event.is_set()


def _new_run(profile, output_root, mode):
    if profile not in PROFILE_INFO:
        raise ValueError('请选择已有研究方向。')
    root = Path(output_root or Path(__file__).parent / 'runs').resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + profile + '_' + uuid.uuid4().hex[:6])
    path.mkdir()
    return {'version': VERSION, 'created_at': now(), 'profile': profile,
            'profile_title': PROFILE_INFO[profile]['title'], 'mode': mode,
            'run_dir': str(path), 'records': [], 'errors': [], 'requests': [], 'summary': {},
            'scope_note': '有限数量候选检索和关键词初筛，不是穷尽检索、全文审查或实验数据提取。'}


def _record_key(record):
    # A DOI merely read from a local PDF is not verified: do not merge it with an API record.
    if record.get('local_sha256'):
        return 'file:' + record['local_sha256']
    doi = canonical_doi(record.get('doi'))
    if doi:
        return 'doi:' + doi
    sid = json.dumps(record.get('source_ids', {}), ensure_ascii=False, sort_keys=True)
    if sid != '{}':
        return 'source:' + str(record.get('sources')) + ':' + sid
    return 'unresolved:' + hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def merge_records(raw_records):
    merged = {}
    for source in raw_records:
        original = copy.deepcopy(source)
        _abstract_metadata(original)
        key = _record_key(original)
        original['doi'] = canonical_doi(original.get('doi'))
        if key not in merged:
            record = copy.deepcopy(original)
            record.update(id=hashlib.sha256(key.encode()).hexdigest()[:18],
                          source_records=[original], manual_decision='', manual_note='')
            merged[key] = record
            continue
        record = merged[key]
        record['source_records'].append(original)
        for field in ('sources', 'warnings', 'fulltext_candidates'):
            values = record.setdefault(field, [])
            for value in original.get(field) or []:
                if value not in values:
                    values.append(value)
        record.setdefault('source_ids', {}).update(original.get('source_ids') or {})
        for field in ('title', 'publisher', 'journal', 'url', 'year', 'document_type'):
            if not record.get(field) and original.get(field):
                record[field] = original[field]
        # Keep one source's abstract intact. Never concatenate unrelated snippets as if one abstract.
        if len(original.get('abstract') or '') > len(record.get('abstract') or ''):
            record['abstract'] = original['abstract']
            record['abstract_origin'] = original.get('abstract_origin','')
            record['abstract_source'] = copy.deepcopy(original.get('abstract_source',{}))
        if record.get('metadata_lookup_failed') and original.get('title') and not original.get('metadata_lookup_failed'):
            record['title']=original['title'];record.pop('metadata_lookup_failed',None)
        _abstract_metadata(record)
    return list(merged.values())


def _abstract_metadata(record):
    text=str(record.get('abstract') or '').strip()
    if text.lower() in {'no abstract available','abstract unavailable','n/a','none','null','无摘要'}:
        text='';record['abstract']=''
    local=bool(record.get('extraction_status'))
    record['abstract_status']='local_excerpt' if text and local else 'available' if text else 'missing'
    if text:
        if not record.get('abstract_origin'):
            record['abstract_origin']='本地PDF提取片段' if local else ' / '.join(record.get('sources',[]))+'摘要'
        if not isinstance(record.get('abstract_source'),dict) or not record['abstract_source']:
            record['abstract_source']={'provider':' / '.join(record.get('sources',[])),
                                       'source_id':record.get('doi',''),'source_url':record.get('url','')}
        record['abstract_source'].setdefault('retrieved_at',record.get('retrieved_at') or now())
        record['warnings']=[w for w in record.get('warnings',[]) if not str(w).startswith(('来源未提供摘要','来源未提供有效摘要','DOI 元数据未取得'))]
    return record


def _classify_record(record, profile):
    from difflib import SequenceMatcher
    _abstract_metadata(record)
    record['screening'] = screen_record(record, profile)
    if str(record.get('document_type', '')).lower() in {'component', 'dataset'}:
        record['screening']['decision'] = 'review'
        record['screening']['reason'] += '；此记录可能是补充材料或数据集，需关联主论文后使用。'
    titles = [re.sub(r'\W+', '', r.get('title', '').lower()) for r in record.get('source_records',[]) if r.get('title') and not r.get('metadata_lookup_failed')]
    if titles and any(SequenceMatcher(None, titles[0], t).ratio() < .60 for t in titles[1:]):
        record['screening']['decision'] = 'review'
        record['screening']['reason'] += '；同 DOI 的来源题名存在明显差异，需核对身份。'
        if '多来源题名差异，已转人工复核' not in record.setdefault('warnings',[]):
            record['warnings'].append('多来源题名差异，已转人工复核')
    if record.get('extraction_status') in ('failed', 'empty'):
        record['screening']['decision'] = 'review'
        record['screening']['reason'] = 'PDF 文字提取失败或为空；可能是扫描版，不能据此排除。'
    elif record['abstract_status']=='missing':
        record['screening']['decision']='review'
        record['screening']['reason'] += '；尚无有效摘要，保留待复核，不由标题直接放行或排除。'
    record['effective_decision'] = record.get('manual_decision') or record['screening']['decision']
    record['route'] = source_route(record)


def _screen(run, raw_records):
    records = merge_records(raw_records)
    for record in records:
        _classify_record(record,run['profile'])
    records.sort(key=lambda r: ({'target': 0, 'review': 1, 'non_target': 2}.get(r['effective_decision'], 3),
                               not r['screening'].get('priority_focus', False),
                               -float(r['screening'].get('coverage', 0)), str(r.get('title', ''))))
    run['records'] = records
    run['summary']['raw_count'] = len(raw_records)
    run['summary']['duplicates_merged'] = len(raw_records) - len(records)
    _save(run)
    return run


def _csv_safe(value):
    text = str('' if value is None else value)
    if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r')):
        return "'" + text
    return text


def _save(run):
    root = Path(run['run_dir'])
    root.mkdir(parents=True, exist_ok=True)
    counts = {d: sum(r.get('effective_decision') == d for r in run['records']) for d in DECISIONS}
    run['summary'].update(total=len(run['records']), **counts)
    run['summary']['with_abstract']=sum(r.get('abstract_status')=='available' for r in run['records'])
    run['summary']['missing_abstract']=sum(r.get('abstract_status')=='missing' for r in run['records'])
    run['updated_at'] = now()
    tmp = root / 'run.json.tmp'
    tmp.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(root / 'run.json')
    fields = ['id', 'decision', 'decision_zh', 'auto_decision', 'manual_decision', 'manual_note',
              'title', 'doi', 'year', 'journal', 'publisher', 'sources', 'publisher_group',
              'topic_branch', 'priority_focus', 'group_coverage_not_confidence', 'reason',
              'matched_groups', 'missing_groups', 'abstract', 'abstract_status','abstract_origin','abstract_source','url', 'access_hint',
              'fulltext_candidates', 'local_path', 'local_xml', 'xml_sha256', 'xml_source', 'xml_message',
              'pdf_status', 'pdf_source', 'pdf_message', 'warnings']
    with (root / 'screening_results.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        for record in run['records']:
            s, route = record['screening'], record['route']
            row = {k: record.get(k, '') for k in fields}
            row.update(xml_source=record.get('xml_acquisition',{}).get('source_url',''),
                       xml_message=record.get('xml_acquisition',{}).get('message',''))
            row.update(decision=record['effective_decision'], decision_zh=DECISIONS[record['effective_decision']],
                       auto_decision=s['decision'], sources='; '.join(record.get('sources', [])),
                       publisher_group=route['publisher_group'], access_hint=route['access_hint'],
                       topic_branch=s.get('topic_branch', ''), priority_focus=s.get('priority_focus', False),
                       group_coverage_not_confidence=s.get('coverage', 0), reason=s.get('reason', ''),
                       matched_groups=json.dumps(s.get('matched_groups', {}), ensure_ascii=False),
                       missing_groups='; '.join(s.get('missing_groups', [])),
                       fulltext_candidates=json.dumps(record.get('fulltext_candidates', []), ensure_ascii=False),
                       warnings='; '.join(record.get('warnings', [])))
            row['abstract_source']=json.dumps(record.get('abstract_source',{}),ensure_ascii=False)
            acquisition=record.get('pdf_acquisition',{})
            row.update(pdf_status=acquisition.get('status',''),pdf_source=acquisition.get('source_url',''),pdf_message=acquisition.get('message',''))
            writer.writerow({k: _csv_safe(v) for k, v in row.items()})
    for decision, filename in [('target', '保留候选_DOI.txt'), ('review', '待复核_DOI.txt')]:
        dois = sorted({r['doi'] for r in run['records'] if r.get('doi') and r['effective_decision'] == decision})
        (root / filename).write_text('\n'.join(dois) + ('\n' if dois else ''), encoding='utf-8')
    _write_html(run)


def _write_html(run):
    e = lambda value: html.escape(str(value or ''))
    cards = []
    for r in run['records']:
        s = r['screening']
        url = r['route']['official_entry']
        link = f'<a href="{e(url)}">打开论文页面</a>' if valid_web_url(url) else ''
        cards.append(f'<article><span class="{r["effective_decision"]}">{e(DECISIONS[r["effective_decision"]])}</span> '
                     f'<small>{e(s.get("topic_branch", ""))} {"｜重点关注" if s.get("priority_focus") else ""}</small>'
                     f'<h2>{e(r.get("title"))}</h2><p>DOI：{e(r.get("doi") or "未提供 / 待确认")} ｜ 来源：{e(", ".join(r.get("sources", [])))}</p>'
                     f'<p>{e(s.get("reason"))}</p><p>{e(r["route"]["publisher_group"])}：{e(r["route"]["access_hint"])} {link}</p>'
                     f'<p>PDF 获取：{e(r.get("pdf_acquisition",{}).get("message","保留后可在软件中自动获取"))}</p>'
                     f'<p>摘要状态：{e(r.get("abstract_status"))} ｜ 摘要来源：{e(r.get("abstract_origin") or "未取得")}</p>'
                     f'<details><summary>查看摘要、关键词与人工复核</summary><p>{e(r.get("abstract") or "缺少摘要，需看原文")}</p>'
                     f'<p>关键词组：{e(json.dumps(s.get("matched_groups", {}), ensure_ascii=False))}</p>'
                     f'<p>人工判断：{e(r.get("manual_decision"))}　{e(r.get("manual_note"))}</p></details></article>')
    summary = run['summary']
    errors = ''.join('<li>' + e(x) + '</li>' for x in run['errors'])
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>文献筛选结果</title><style>body{font-family:"Microsoft YaHei",sans-serif;line-height:1.7;background:#f4f6f9;color:#1e293b;margin:0}main{max-width:1050px;margin:30px auto;padding:0 24px}article{background:white;padding:22px;border:1px solid #dbe3ed;border-radius:10px;margin:16px 0}h1{font-size:28px}h2{font-size:19px;margin:12px 0}p{overflow-wrap:anywhere}.target{color:#16754b}.review{color:#996300}.non_target{color:#6b7280}span{font-weight:bold}small{color:#576579}a{color:#175ea3}details p{white-space:pre-wrap}summary{cursor:pointer}</style><main>'
    document += f'<h1>{e(run["profile_title"])} · 初筛结果</h1><p>{e(run["created_at"])}（UTC）</p>'
    document += f'<p>去重后 {summary["total"]} 篇：保留候选 {summary["target"]}，待复核 {summary["review"]}，暂不相关 {summary["non_target"]}。</p>'
    document += '<p>这是关键词初筛；“保留候选”不代表已读完全文或已取得可靠实验数据。关键词覆盖比例不是准确率。人工修改请在软件中完成，本页面用于查看。</p>'
    if errors:
        document += '<p>本次部分操作未成功，结果可能不完整：</p><ul>' + errors + '</ul>'
    document += ''.join(cards) + '</main></html>'
    (Path(run['run_dir']) / '筛选结果_可直接打开.html').write_text(document, encoding='utf-8')


def _enrich_missing_abstracts(run, raw, cache_root, api_key='', enabled=True, progress=None, cancel_event=None, maximum=50,recheck_openalex=False,blocked_status=None):
    if not enabled:return []
    candidates=[r for r in merge_records(raw) if r.get('abstract_status')=='missing' and r.get('doi')
                and not r.get('extraction_status') and (recheck_openalex or not r.get('source_ids',{}).get('OpenAlex'))]
    cache_root=Path(cache_root);cache_root.mkdir(parents=True,exist_ok=True)
    additions=[]
    run['abstract_enrichment']={'enabled':True,'maximum':maximum,'eligible':len(candidates),'attempted':0,'filled':0,
                               'deferred_by_limit':max(0,len(candidates)-maximum)}
    if blocked_status:
        run['abstract_enrichment']['stopped_reason']=f'本批OpenAlex已返回HTTP {blocked_status}，未再次请求补全；其他来源结果保留'
        return []
    for index,record in enumerate(candidates[:maximum],1):
        if _cancelled(cancel_event):
            run['errors'].append('用户停止摘要补全；保留已取得的信息。');break
        doi=record['doi'];started=now()
        _emit(progress,f'OpenAlex补摘要 {index}/{min(len(candidates),maximum)}：{doi}')
        run['abstract_enrichment']['attempted']+=1
        try:
            cache=cache_root/(hashlib.sha256(('openalex-doi:'+VERSION+':'+doi).encode()).hexdigest()+'.json')
            cached=cache.exists() and time.time()-cache.stat().st_mtime<86400
            enriched=json.loads(cache.read_text(encoding='utf-8')) if cached else lookup_openalex_doi(doi,timeout=15,api_key=api_key)
            if canonical_doi(enriched.get('doi'))!=doi:raise AdapterError('补全响应DOI不一致，未合并摘要。')
            enriched['retrieved_at']=started
            _abstract_metadata(enriched)
            if not cached:cache.write_text(json.dumps(enriched,ensure_ascii=False),encoding='utf-8')
            additions.append(enriched)
            filled=enriched['abstract_status']=='available'
            run['abstract_enrichment']['filled']+=int(filled)
            run['requests'].append({'provider':'OpenAlex','operation':'abstract_enrichment','doi':doi,
                'status':'filled' if filled else 'abstract_missing','started_at':started,'cached_24h':cached})
        except Exception as exc:
            run['errors'].append(f'OpenAlex补摘要未完成（{doi}）：{exc}')
            run['requests'].append({'provider':'OpenAlex','operation':'abstract_enrichment','doi':doi,'status':'error',
                'error':str(exc),'http_status':getattr(exc,'http_status',None),'started_at':started})
            if getattr(exc,'http_status',None) in (401,403,429):
                run['abstract_enrichment']['stopped_reason']='授权或额度限制，未继续请求剩余DOI'
                break
    return additions


def run_search(profile, queries=None, limit=15, providers=('Crossref', 'Europe PMC'), output_root=None, progress=None, cancel_event=None,
               enrich_abstracts=True, openalex_api_key='',search_api_keys=None):
    openalex_api_key=_openalex_key(openalex_api_key)
    search_api_keys={p:provider_key(v,p) for p,v in (search_api_keys or {}).items() if p in ('CORE','Semantic Scholar')}
    run = _new_run(profile, output_root, 'search')
    if isinstance(queries, str):
        queries = queries.splitlines()
    queries = [str(q).strip() for q in (queries if queries is not None else PROFILE_INFO[profile]['queries']) if str(q).strip()]
    if not queries or len(queries) > 10:
        raise ValueError('请填写 1–10 行检索词，每行作为一次独立检索。')
    limit = int(limit)
    if not 1 <= limit <= 100:
        raise ValueError('每个检索词、每个来源的候选上限应为 1–100。')
    functions = {'Crossref': search_crossref, 'Europe PMC': search_europe_pmc,'OpenAlex':search_openalex,
                 'CORE':search_core,'Semantic Scholar':search_semantic_scholar}
    providers = list(providers)
    if not providers or any(p not in functions for p in providers):
        raise ValueError('至少选择一个有效文献来源。')
    run.update(queries=queries, providers=providers, per_query_per_source_limit=limit)
    raw = []
    blocked_providers={}
    cache_root = Path(run['run_dir']).parent / '_cache'
    cache_root.mkdir(exist_ok=True)
    for query in queries:
        for provider in providers:
            if _cancelled(cancel_event):
                run['errors'].append('用户停止检索；保留已完成的部分结果。')
                return _screen(run, raw)
            _emit(progress, f'{provider}：正在检索 {query}（最多 {limit} 篇）')
            started = now()
            try:
                key = hashlib.sha256(json.dumps([VERSION, provider, query, limit]).encode()).hexdigest()
                cache = cache_root / (key + '.json')
                cached = cache.exists() and time.time() - cache.stat().st_mtime < 86400
                if cached:
                    records = json.loads(cache.read_text(encoding='utf-8'))
                else:
                    if provider in blocked_providers:
                        run['requests'].append({'provider':provider,'query':query,'status':'deferred',
                            'http_status':blocked_providers[provider],'reason':'本批来源已受授权/额度限制，未重复请求'})
                        continue
                    options={'limit':limit,'timeout':15}
                    if provider=='OpenAlex':options['api_key']=openalex_api_key
                    elif provider in search_api_keys:options['api_key']=search_api_keys[provider]
                    records = functions[provider](query, **options)
                    cache.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
                for record in records:
                    record['retrieval_query'] = query
                    record['retrieved_at'] = started
                raw.extend(records)
                run['requests'].append({'provider': provider, 'query': query, 'count': len(records), 'started_at': started, 'cached_24h': cached, 'status': 'ok'})
                _emit(progress, f'{provider} 返回 {len(records)} 篇' + ('（24 小时缓存）' if cached else ''))
            except Exception as exc:
                message = f'{provider} 检索失败：{type(exc).__name__}: {exc}'
                run['errors'].append(message)
                status=getattr(exc,'http_status',None)
                run['requests'].append({'provider': provider, 'query': query, 'started_at': started, 'status': 'error', 'error': str(exc),'http_status':status})
                if provider in ('OpenAlex','CORE','Semantic Scholar') and status in (401,403,429):blocked_providers[provider]=status
                _emit(progress, message)
    raw.extend(_enrich_missing_abstracts(run,raw,cache_root,openalex_api_key,enrich_abstracts,progress,cancel_event,
                                       blocked_status=blocked_providers.get('OpenAlex')))
    _emit(progress, '正在按 DOI 去重、筛选并保存结果……')
    return _screen(run, raw)


def run_dois(profile, text, output_root=None, progress=None, cancel_event=None,enrich_abstracts=True,openalex_api_key=''):
    openalex_api_key=_openalex_key(openalex_api_key)
    dois = list(dict.fromkeys(canonical_doi(d) for d in DOI_RE.findall(unquote(text))))
    dois = [d for d in dois if d]
    if not dois:
        raise ValueError('没有识别到 DOI。请一行粘贴一个 DOI 或 doi.org 链接。')
    if len(dois) > 100:
        raise ValueError('第一版每次最多查询 100 个 DOI，请分批导入。')
    run = _new_run(profile, output_root, 'doi_lookup')
    run['input_dois'] = dois
    raw = []
    for i, doi in enumerate(dois, 1):
        if _cancelled(cancel_event):
            run['errors'].append('用户停止 DOI 查询；保留已完成的部分结果。')
            break
        _emit(progress, f'查询 DOI {i}/{len(dois)}：{doi}')
        try:
            record = lookup_doi(doi, timeout=15)
            record['retrieved_at'] = now()
            raw.append(record)
            run['requests'].append({'doi': doi, 'provider': 'Crossref', 'status': 'ok'})
        except Exception as exc:
            run['errors'].append(f'{doi}：{exc}（查询失败，不代表论文不相关）')
            raw.append({'doi': doi, 'title': 'DOI 查询未完成：' + doi, 'abstract': '',
                        'url': 'https://doi.org/' + doi, 'sources': ['用户 DOI'],
                        'source_ids': {}, 'warnings': ['DOI 元数据未取得'], 'fulltext_candidates': [],'metadata_lookup_failed':True})
    raw.extend(_enrich_missing_abstracts(run,raw,Path(run['run_dir']).parent/'_cache',openalex_api_key,
                                       enrich_abstracts,progress,cancel_event))
    return _screen(run, raw)


def enrich_existing_run(run,openalex_api_key='',progress=None,cancel_event=None):
    """Update metadata only. IDs, decisions and local fulltext links are retained."""
    updated=copy.deepcopy(run)
    openalex_api_key=_openalex_key(openalex_api_key)
    additions=_enrich_missing_abstracts(updated,updated['records'],Path(updated['run_dir']).parent/'_cache',
                                       openalex_api_key,True,progress,cancel_event,recheck_openalex=True)
    changed=[]
    for addition in additions:
        record=next((r for r in updated['records'] if canonical_doi(r.get('doi'))==addition['doi'] and not r.get('extraction_status')),None)
        if not record:continue
        seed=copy.deepcopy(record)
        # A downloaded PDF hash identifies a file, not a new metadata DOI.
        # Remove it only from this temporary metadata merge, never the record.
        seed.pop('local_sha256',None)
        selected=merge_records([seed,addition])[0]
        # Copy only metadata, not identity, file links, review fields or audit.
        fields=('title','abstract','publisher','journal','url','year','document_type','sources','source_ids',
                'warnings','fulltext_candidates','abstract_origin','abstract_source','abstract_status')
        for field in fields:
            if field in selected:record[field]=copy.deepcopy(selected[field])
        record.setdefault('source_records',[]).append(copy.deepcopy(addition))
        if addition.get('title'):record.pop('metadata_lookup_failed',None)
        _classify_record(record,updated['profile'])
        changed.append(record['id'])
    for record in updated['records']:
        _classify_record(record,updated['profile'])
    updated.setdefault('metadata_history',[]).append({'at':now(),'operation':'openalex_abstract_enrichment','record_ids':changed,
                                                   'manual_reviews_preserved':True})
    _save(updated)
    return updated


def run_local_pdfs(profile, folder, output_root=None, progress=None, cancel_event=None):
    from pypdf import PdfReader
    directory = Path(folder)
    if not directory.is_dir():
        raise ValueError('请选择存在的 PDF 文件夹。')
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == '.pdf')
    if not files:
        raise ValueError('文件夹直接下一层没有 PDF；第一版不递归读取子文件夹。')
    run = _new_run(profile, output_root, 'local_pdf_offline')
    run['input_folder'] = str(directory.resolve())
    raw = []
    for i, path in enumerate(files, 1):
        if _cancelled(cancel_event):
            run['errors'].append('用户停止本地导入；保留已完成结果。')
            break
        _emit(progress, f'本地读取 {i}/{len(files)}：{path.name}')
        record = {'doi': '', 'title': path.stem, 'abstract': '', 'year': '', 'publisher': '', 'journal': '',
                  'url': '', 'sources': ['本地 PDF'], 'source_ids': {'local': str(path.resolve())},
                  'document_type': '', 'fulltext_candidates': [], 'warnings': [],
                  'local_path': str(path.resolve()), 'extraction_status': 'ok', 'retrieved_at': now()}
        try:
            with path.open('rb') as f:
                digest = hashlib.file_digest(f, 'sha256').hexdigest()
            record['local_sha256'] = digest
            reader = PdfReader(path)
            texts = [(p.extract_text() or '') for p in reader.pages[:2]]
            text = '\n'.join(texts).strip()
            if not text:
                record['extraction_status'] = 'empty'
                record['warnings'].append('文字为空，尚未进行 OCR')
            else:
                title = str((reader.metadata or {}).get('/Title') or '').strip()
                if title and len(title) > 8:
                    record['title'] = title[:500]
                else:
                    first = [line.strip() for line in text.splitlines() if len(line.strip()) > 12]
                    record['title'] = ' '.join(first[:2])[:400] or path.stem
                record['abstract'] = text[:16000]
                record['abstract_origin'] = 'PDF 前两页文字；尚未单独定位摘要'
                found = list(dict.fromkeys(canonical_doi(d) for d in DOI_RE.findall(texts[0] if texts else '')))
                found = [d for d in found if d]
                if len(found) == 1:
                    record['doi'] = found[0]
                    record['url'] = 'https://doi.org/' + found[0]
                    record['warnings'].append('DOI 从首页识别，尚未联网核对题名；使用前请核实')
                elif len(found) > 1:
                    record['warnings'].append('首页有多个 DOI，未自动选取：' + '; '.join(found))
                else:
                    record['warnings'].append('未在首页识别到 DOI，可在软件中单独导入 DOI 查询')
        except Exception as exc:
            record['extraction_status'] = 'failed'
            record['warnings'].append('PDF 读取失败：' + str(exc))
        raw.append(record)
    return _screen(run, raw)


def save_review(run, record_id, decision, note=''):
    if decision not in DECISIONS:
        raise ValueError('人工判断必须是保留、待复核或排除。')
    record = next((r for r in run['records'] if r['id'] == record_id), None)
    if record is None:
        raise ValueError('未找到对应记录。')
    event = {'at': now(), 'record_id': record_id, 'previous': record['effective_decision'], 'decision': decision, 'note': note}
    record.update(manual_decision=decision, manual_note=str(note), effective_decision=decision)
    run.setdefault('review_history', []).append(event)
    _save(run)
    return run


def load_run(path):
    file = Path(path)
    if file.is_dir():
        file = file / 'run.json'
    value = json.loads(file.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or not isinstance(value.get('records'), list) or value.get('profile') not in PROFILE_INFO:
        raise ValueError('这不是本工具保存的 run.json 结果文件。')
    value['run_dir'] = str(file.resolve().parent)
    for record in value['records']:_abstract_metadata(record)
    return value


def download_open_fulltext(run, record_id, progress=None):
    record = next((r for r in run['records'] if r['id'] == record_id), None)
    if not record:
        raise ValueError('请先选择一篇论文。')
    candidates = [c for c in record.get('fulltext_candidates', []) if is_open_pmc_candidate(c)]
    if not candidates:
        raise ValueError('XML 下载目前仅接入 Europe PMC 开放全文，不是通用 PDF 下载。这篇论文暂无该入口，请打开论文网页，取得 PDF 后导入。')
    url = candidates[0]['url']
    pmcid = re.search(r'/((?:PMC)\d+)/', url).group(1)
    directory = Path(run['run_dir']) / 'fulltext'
    directory.mkdir(exist_ok=True)
    target = directory / (pmcid + '.xml')
    if target.exists():
        return target
    _emit(progress, '正在获取开放全文 XML：' + pmcid)
    request = Request(url, headers={'User-Agent': 'LocalLiteratureGateway/' + VERSION, 'Accept': 'application/xml'})
    with urlopen(request, timeout=25) as response:
        final_url = urlsplit(response.geturl())
        if final_url.hostname != 'www.ebi.ac.uk':
            raise ValueError('全文接口跳转到未接入的站点，已停止自动获取。')
        data = response.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise ValueError('全文超过本版 20 MB 单文件上限，请人工获取。')
    if b'<!ENTITY' in data.upper():
        raise ValueError('XML 含不支持的实体定义，需人工处理。')
    tree = ET.fromstring(data)
    if tree.tag.split('}')[-1] != 'article':
        raise ValueError('接口没有返回有效文章 XML，未将错误页面保存为全文。')
    doi_values = [canonical_doi(''.join(n.itertext())) for n in tree.iter()
                  if n.tag.split('}')[-1] == 'article-id' and n.attrib.get('pub-id-type') == 'doi']
    expected = canonical_doi(record.get('doi'))
    if expected and doi_values and expected not in doi_values:
        raise ValueError('全文 DOI 与候选论文不一致，已停止保存。')
    target.write_bytes(data)
    (directory / (pmcid + '_获取记录.json')).write_text(json.dumps({'at': now(), 'doi': expected,
        'pmcid': pmcid, 'source_url': url, 'sha256': hashlib.sha256(data).hexdigest(),
        'identity_check': 'doi_match' if expected and expected in doi_values else 'pmcid_endpoint_only',
        'note': '仅取得全文 XML；尚未进行实验数据提取。'}, ensure_ascii=False, indent=2), encoding='utf-8')
    record['downloaded_fulltext'] = str(target)
    _save(run)
    return target


def acquire_pdfs(run, progress=None, cancel_event=None, publisher_client=None):
    """Acquire kept papers before the existing local figure pipeline starts."""
    from pdf_fetch import acquire_retained
    return acquire_retained(run,progress=progress,cancel_event=cancel_event,persist=_save,
                            **({'publisher_client':publisher_client} if publisher_client is not None else {}))


def check_publisher_pdf(run,record_id,client,progress=None,cancel_event=None,*,format='pdf'):
    """Explicit single-paper API check; never replace an existing PDF on failure."""
    from pdf_fetch import FetchError, Stopped, now
    from elsevier_api import APIError
    record=next(r for r in run['records'] if r['id']==record_id)
    if (record.get('manual_decision') or record.get('effective_decision'))!='target':
        raise ValueError('请先将当前论文判定为保留，再进行 API 全文验证。')
    try:
        fetch=client.fetch_xml if format=='xml' else client.fetch_pdf
        info=fetch(dict(record),run['run_dir'],cancel_event,progress)
    except Stopped:info={'status':'cancelled','message':'已停止本次 API 验证。'}
    except FetchError as exc:
        info=exc.public_details() if isinstance(exc,APIError) else {'status':exc.status,'message':str(exc)}
    info={**info,'checked_at':now()}
    record['publisher_api_check']={key:value for key,value in info.items() if key!='attempts'}
    if info['status']=='downloaded_xml':
        previous=record.get('xml_acquisition')
        if previous:record.setdefault('xml_acquisition_history',[]).append(previous)
        record.update(local_xml=info['local_xml'],xml_sha256=info['xml_sha256'],xml_acquisition=info)
        if not record.get('local_path'):record['pdf_acquisition']=info
    elif info['status']=='downloaded':
        previous=record.get('pdf_acquisition')
        if previous:record.setdefault('pdf_acquisition_history',[]).append(previous)
        record['pdf_acquisition']=info
        record.update(local_path=info['local_path'],local_sha256=info['local_sha256'])
    elif not record.get('local_path'):
        record['pdf_acquisition']=info
    _save(run)
    return {'run':run,'check':info,'record_id':record_id}


def check_elsevier_pdf(run,record_id,client,progress=None,cancel_event=None):
    """Compatibility for previous launchers and tests."""
    return check_publisher_pdf(run,record_id,client,progress,cancel_event)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=list(PROFILE_INFO), default='scr_ammonia')
    parser.add_argument('--query', action='append')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--output-root')
    parser.add_argument('--provider',action='append',choices=['Crossref','Europe PMC','OpenAlex','CORE','Semantic Scholar'])
    parser.add_argument('--no-enrich-abstracts',action='store_true')
    args = parser.parse_args()
    result = run_search(args.profile, args.query, args.limit, output_root=args.output_root, progress=print,
                        providers=args.provider or ['Crossref','OpenAlex','Europe PMC','CORE','Semantic Scholar'],enrich_abstracts=not args.no_enrich_abstracts,
                        openalex_api_key=os.environ.get('OPENALEX_API_KEY',''),
                        search_api_keys={'CORE':os.environ.get('CORE_API_KEY',''),'Semantic Scholar':os.environ.get('SEMANTIC_SCHOLAR_API_KEY','')})
    print(json.dumps({'run_dir': result['run_dir'], 'summary': result['summary'], 'errors': result['errors']}, ensure_ascii=False))


if __name__ == '__main__':
    import sys
    if sys.stdout:
        sys.stdout.reconfigure(encoding='utf-8')
    main()
