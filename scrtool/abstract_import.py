"""Offline abstract collection from saved pages, Zotero and browser exports."""
from __future__ import annotations

import contextlib
import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

from .core import uid, write_csv, write_json
from .literature import screen_records, write_screening

SUPPORTED = {'.html', '.htm', '.json', '.ris', '.csv'}


def text(value):
    value = str(value or '')
    if '<' in value and '>' in value:
        value = BeautifulSoup(value, 'html.parser').get_text()
    return re.sub(r'\s+', ' ', value).strip()


def doi(value):
    value = unquote(str(value or '')).strip()
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value, flags=re.I)
    return value.lower().rstrip('.,;') if re.fullmatch(r'10\.\d{4,9}/\S+', value, re.I) else ''


def html_record(raw):
    """Extract a labelled Abstract section; never merge Highlights into it."""
    soup = BeautifulSoup(raw, 'html.parser')
    meta = {}
    for node in soup.find_all('meta'):
        key = (node.get('name') or node.get('property') or '').lower()
        if key and node.get('content'):
            meta.setdefault(key, []).append(node['content'])

    def first(*keys):
        return next((meta[k][0] for k in keys if k in meta), '')

    paper_doi = doi(first('citation_doi', 'dc.identifier.doi', 'dc.identifier', 'prism.doi'))
    title = text(first('citation_title', 'dc.title', 'og:title'))
    if not title:
        heading = soup.find('h1')
        title = text(heading.get_text() if heading else '')
    canonical = soup.find('link', rel='canonical')
    url = first('citation_public_url', 'citation_abstract_html_url', 'og:url')
    if not url and canonical:
        url = canonical.get('href', '')
    if not paper_doi:
        paper_doi = doi(url)
    abstract, locator = '', ''
    for heading in soup.find_all(re.compile(r'^h[1-6]$')):
        if text(heading.get_text()).casefold() not in {'abstract', '摘要'}:
            continue
        parent = heading.parent
        for _ in range(3):
            if parent is None or parent.name in {'body', 'html'}:
                break
            clone = BeautifulSoup(str(parent), 'html.parser')
            other = [text(h.get_text()).casefold() for h in clone.find_all(re.compile(r'^h[1-6]$'))]
            if any(h in {'highlights', 'graphical abstract', 'keywords', 'introduction'} for h in other):
                break
            for h in clone.find_all(re.compile(r'^h[1-6]$')):
                if text(h.get_text()).casefold() in {'abstract', '摘要'}:
                    h.decompose()
            for node in clone.select('script, style, nav, button'):
                node.decompose()
            for node in clone.select('p'):
                node.append(' ')
            candidate = text(clone.get_text())
            if len(candidate) >= 80:
                abstract = candidate
                locator = ('#' + parent['id'] if parent.get('id') else parent.name) + ' > heading:Abstract'
                break
            parent = parent.parent
        if abstract:
            break
    if not abstract:
        for key in ('citation_abstract', 'dcterms.abstract', 'dc.description'):
            candidate = text(first(key))
            if len(candidate) >= 80:
                abstract, locator = candidate, 'meta:' + key
                break
    date = first('citation_publication_date', 'dc.date', 'prism.publicationdate')
    year = re.search(r'\b(?:19|20)\d{2}\b', date)
    return {'doi': paper_doi, 'title': title, 'abstract': abstract,
            'year': int(year.group()) if year else None,
            'journal': text(first('citation_journal_title', 'prism.publicationname')),
            'authors': meta.get('citation_author', []), 'keywords': first('citation_keywords', 'keywords'),
            'landing_page_url': url, 'abstract_url': url, 'abstract_locator': locator,
            'document_type': first('citation_article_type', 'dc.type'),
            'abstract_source': 'saved_html'}


def metadata_record(item, source):
    """Handle CSL JSON, Zotero API item data, and our browser/harvest JSON."""
    if not isinstance(item, dict):
        raise ValueError('题录项必须是 JSON 对象')
    if isinstance(item.get('data'), dict):
        item = {**item['data'], 'zotero_key': item.get('key', '')}
    if item.get('itemType') in {'attachment', 'note', 'annotation'}:
        return None
    if not any(k in item for k in ('title', 'doi', 'DOI', 'abstract', 'abstractNote')):
        raise ValueError('未识别为论文题录，请使用 CSL JSON、RIS 或浏览器采集文件')
    row = dict(item)
    issues = item.get('import_issues') or []
    if isinstance(issues, str):
        try:
            issues = json.loads(issues)
        except ValueError:
            issues = [issues]
    row['import_issues'] = issues if isinstance(issues, list) else [str(issues)]
    row['doi'] = doi(item.get('doi') or item.get('DOI'))
    row['title'] = text(item.get('title'))
    row['abstract'] = text(item.get('abstract') or item.get('abstractNote'))
    row['abstract_source'] = item.get('abstract_source') or source
    row['landing_page_url'] = item.get('landing_page_url') or item.get('URL') or item.get('url') or ''
    row['abstract_url'] = item.get('abstract_url') or row['landing_page_url']
    row['journal'] = item.get('journal') or item.get('container-title') or item.get('publicationTitle') or ''
    row['keywords'] = item.get('keywords') or item.get('keyword') or ''
    row['document_type'] = item.get('document_type') or item.get('type') or item.get('itemType') or ''
    if not row.get('year'):
        parts = (item.get('issued') or {}).get('date-parts') or []
        match = re.search(r'\b(?:19|20)\d{2}\b', str(item.get('date') or ''))
        row['year'] = parts[0][0] if parts and parts[0] else int(match.group()) if match else None
    full = item.get('abstract_is_full')
    if isinstance(full, str):
        full = full.strip().lower() not in {'false', '0', 'no'} if full.strip() else None
    row['abstract_is_full'] = full
    if item.get('abstract_origin') == 'PDF 前两页文字；尚未单独定位摘要':
        row.update(abstract_is_full=False, abstract_extraction_status='fallback_first_pages')
    return row


def ris_records(value):
    entries, fields, key = [], {}, None
    mapping = {'DO': 'doi', 'TI': 'title', 'T1': 'title', 'AB': 'abstract', 'N2': 'abstract',
               'JO': 'journal', 'JF': 'journal', 'T2': 'journal', 'TY': 'document_type',
               'PY': 'date', 'Y1': 'date', 'UR': 'url'}
    for line in value.splitlines():
        match = re.match(r'^([A-Z0-9]{2})\s{2}-\s?(.*)$', line)
        if match:
            tag, payload = match.groups()
            if tag == 'TY' and fields:
                entries.append(fields)
                fields = {}
            if tag == 'ER':
                entries.append(fields)
                fields, key = {}, None
            elif tag in mapping:
                key = mapping[tag]
                fields[key] = payload
            elif tag in {'AU', 'A1', 'KW'}:
                key = 'authors' if tag != 'KW' else 'keywords'
                fields.setdefault(key, []).append(payload)
            else:
                key = None
        elif line.strip() and key and isinstance(fields.get(key), str):
            fields[key] += '\n' + line.strip()
    if fields:
        entries.append(fields)
    return [metadata_record(r, 'zotero_ris') for r in entries if r]


def read_abstract_file(path):
    path = Path(path)
    if path.suffix.lower() in {'.html', '.htm'}:
        rows = [html_record(path.read_bytes())]
    elif path.suffix.lower() == '.ris':
        rows = ris_records(path.read_text(encoding='utf-8-sig'))
    elif path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as handle:
            rows = [metadata_record(r, 'imported_csv') for r in csv.DictReader(handle)]
    elif path.suffix.lower() == '.json':
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
        if isinstance(payload, dict) and isinstance(payload.get('items'), list):
            payload = payload['items']
        elif isinstance(payload, dict) and isinstance(payload.get('records'), list):
            payload = payload['records']
        elif isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            raise ValueError('JSON 应为题录数组或包含 items 的采集文件')
        rows = [metadata_record(r, 'zotero_csl_json') for r in payload]
    else:
        raise ValueError('请选择 HTML、CSL JSON、RIS、CSV 或浏览器采集 JSON')
    output = []
    for row in rows:
        if row is None:
            continue
        row['source_inputs'] = [str(path.resolve())]
        row['title'], row['abstract'] = text(row.get('title')), text(row.get('abstract'))
        if not row['title'] and not row.get('doi'):
            raise ValueError('没有论文标题或 DOI，可能保存的是登录页、跳转页或错误页')
        flags = list(row.get('import_issues') or [])
        if not row.get('doi'):
            flags.append('missing_doi')
        if not row['abstract']:
            flags.append('missing_abstract')
        elif len(row['abstract']) < 80 or row['abstract'].endswith(('...', '…')):
            flags.append('short_or_truncated_abstract')
        full = row.get('abstract_is_full')
        row['abstract_is_full'] = bool(row['abstract']) and full is not False and 'short_or_truncated_abstract' not in flags
        row['abstract_status'] = 'imported' if row['abstract_is_full'] else 'summary_only' if row['abstract'] else 'missing_in_response'
        row['import_issues'] = sorted(set(flags))
        row['record_id'] = row.get('record_id') or uid(row.get('doi'), row['title'], '' if row.get('doi') else str(path))
        output.append(row)
    return output


def collect_files(paths):
    records, errors, by_doi = [], [], {}
    for path in dict.fromkeys(map(str, paths)):
        try:
            imported = read_abstract_file(path)
        except Exception as exc:
            errors.append({'source_file': str(path), 'error': str(exc)})
            continue
        for row in imported:
            previous = by_doi.get(row.get('doi'))
            if previous is None:
                records.append(row)
                if row.get('doi'):
                    by_doi[row['doi']] = row
                continue
            sources = sorted(set(previous['source_inputs'] + row['source_inputs']))
            if not previous['abstract'] and row['abstract']:
                previous.update(row)
            elif previous['abstract'] and row['abstract'] and previous['abstract'] != row['abstract']:
                previous.setdefault('abstract_alternatives', []).append({k: row.get(k) for k in
                    ('abstract', 'abstract_source', 'abstract_url', 'source_inputs')})
                previous['import_issues'] = sorted(set(previous.get('import_issues', []) + ['abstract_conflict']))
            previous['source_inputs'] = sources
    with contextlib.redirect_stdout(io.StringIO()):
        rows = screen_records(records)
    for row in rows:
        if row.get('import_issues'):
            row.update(auto_decision='review', effective_decision='review', decision_label='待核对',
                       reason='导入数据需核对：' + '、'.join(row['import_issues']))
    return rows, errors


def export_collection(paths, output):
    rows, errors = collect_files(paths)
    if not rows:
        raise ValueError('未取得可整理的题录。' + ('；'.join(e['error'] for e in errors[:3]) if errors else '请检查输入文件。'))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'records.json', rows)
    write_csv(output / 'records.csv', rows, ['record_id', 'doi', 'title', 'year', 'journal', 'document_type', 'keywords', 'abstract',
               'abstract_source', 'abstract_url', 'abstract_is_full', 'import_issues', 'source_inputs',
               'abstract_extraction_status', 'needs_ocr',
               'auto_decision', 'decision_label', 'reason'])
    report = write_screening(output, rows)
    write_csv(output / 'import_errors.csv', errors, ['source_file', 'error'])
    report.update(abstracts=sum(bool(r['abstract']) for r in rows),
                  complete_abstracts=sum(bool(r['abstract_is_full']) for r in rows),
                  files=len(set(map(str, paths))), import_errors=len(errors),
                  created_at=datetime.now(timezone.utc).isoformat(), stage='abstract_collection_and_rules_screening',
                  machine_learning_ready=False)
    write_json(output / 'summary.json', report)
    return rows, report
