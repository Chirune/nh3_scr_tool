"""DOI-matched public abstracts, with provenance and a reusable success cache."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .harvest import invert_abstract, normalize_doi, normalize_title, safe_error


def needs_abstract(record):
    return not record.get('abstract') or record.get('abstract_is_full') is False


def publisher_abstract(html, doi, title):
    """Only extract an identified article's explicitly labelled abstract."""
    soup = BeautifulSoup(html, 'html.parser')
    meta = {}
    for node in soup.find_all('meta'):
        key = (node.get('name') or node.get('property') or '').lower()
        if key and node.get('content'):
            meta[key] = node['content']
    page_doi = meta.get('citation_doi') or meta.get('dc.identifier') or meta.get('dc.identifier.doi')
    if page_doi:
        if normalize_doi(page_doi) != normalize_doi(doi):
            return None
    elif not title or normalize_title(meta.get('citation_title')) != normalize_title(title):
        return None
    value = meta.get('citation_abstract') or meta.get('dc.description') or meta.get('dcterms.abstract')
    if not value:
        section = soup.select_one('#abstracts, #abstract, section.abstract, div.abstract, section[data-title="Abstract"]')
        if section:
            for node in section.select('h2, h3, .section-title'):
                node.decompose()
            value = section.get_text(' ', strip=True)
    if value:
        value = re.sub(r'\s+', ' ', BeautifulSoup(value, 'html.parser').get_text()).strip()
        if len(value) >= 80 and not value.endswith(('...', '…')):
            return value
    return None


def enrich_public_abstracts(records, session, *, cache_dir=None, timeout=20,
                            openalex_key=None, email=None, publisher_limit=20):
    """Use public metadata before publisher pages; never replace a complete abstract."""
    timeout = min(timeout, 20)
    report = {'requested': sum(needs_abstract(r) for r in records), 'cached': 0,
              'retrieved': 0, 'by_source': {}, 'requests': {}, 'publisher_pages_attempted': 0}
    cache = Path(cache_dir) if cache_dir else None

    def cache_path(doi):
        return cache / (hashlib.sha256(doi.encode('utf-8')).hexdigest() + '.json')

    def attempt(record, source, status, **details):
        record.setdefault('abstract_attempts', []).append({'source': source, 'status': status, **details})

    def attach(record, text, source, url, fetched_at=None, cached=False):
        if not isinstance(text, str) or not text.strip():
            return False
        record.update(abstract=text.strip(), abstract_source=source, abstract_url=url,
                      abstract_is_full=True, abstract_status='cached' if cached else 'retrieved',
                      abstract_fetched_at=fetched_at or datetime.now(timezone.utc).isoformat())
        report['cached' if cached else 'retrieved'] += 1
        report['by_source'][source] = report['by_source'].get(source, 0) + 1
        if cache and not cached:
            cache.mkdir(parents=True, exist_ok=True)
            payload = {k: record.get(k) for k in ('doi', 'abstract', 'abstract_source', 'abstract_url', 'abstract_fetched_at')}
            cache_path(normalize_doi(record['doi'])).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return True

    pending = [r for r in records if needs_abstract(r) and r.get('doi')]
    for record in pending:
        doi = normalize_doi(record['doi'])
        if cache and cache_path(doi).exists():
            try:
                payload = json.loads(cache_path(doi).read_text(encoding='utf-8'))
                if normalize_doi(payload.get('doi')) == doi and payload.get('abstract_source') and payload.get('abstract_url'):
                    attach(record, payload.get('abstract'), payload['abstract_source'], payload['abstract_url'],
                           payload.get('abstract_fetched_at'), cached=True)
            except (ValueError, OSError):
                pass

    def remaining():
        return [r for r in pending if needs_abstract(r)]

    def request(source, method, url, **kwargs):
        report['requests'][source] = report['requests'].get(source, 0) + 1
        response = getattr(session, method)(url, timeout=timeout, **kwargs)
        if source == 'semantic_scholar' and response.status_code == 429:
            retry_after = response.headers.get('Retry-After', '30')
            try:
                wait_seconds = max(1.1, float(retry_after))
            except (TypeError, ValueError):
                wait_seconds = 30
            if wait_seconds <= 30:
                print(json.dumps({'public_abstract_wait': source, 'seconds': wait_seconds},
                                 ensure_ascii=False), flush=True)
                time.sleep(wait_seconds)
                report['requests'][source] += 1
                response = getattr(session, method)(url, timeout=timeout, **kwargs)
        response.raise_for_status()
        return response

    def failed(batch, source, exc):
        response = getattr(exc, 'response', None)
        code = response.status_code if response is not None else None
        for record in batch:
            attempt(record, source, 'rate_limited' if code == 429 else 'request_failed',
                    http_status=code, error=safe_error(exc))
        return code in {401, 403, 429}

    # Batching avoids one network request per DOI. All matches are checked by DOI.
    batch_rows = remaining()
    for start in range(0, len(batch_rows), 50):
        batch = batch_rows[start:start + 50]
        params = {'filter': 'doi:' + '|'.join('https://doi.org/' + normalize_doi(r['doi']) for r in batch),
                  'per-page': 100, 'select': 'id,doi,title,abstract_inverted_index'}
        if openalex_key:
            params['api_key'] = openalex_key
        elif email:
            params['mailto'] = email
        try:
            data = request('openalex', 'get', 'https://api.openalex.org/works', params=params).json()
            matches = {normalize_doi(item.get('doi')): item for item in data.get('results') or []}
            for record in batch:
                item = matches.get(normalize_doi(record['doi'])) or {}
                text = invert_abstract(item.get('abstract_inverted_index'))
                if not attach(record, text, 'openalex', item.get('id') or 'https://api.openalex.org/works'):
                    attempt(record, 'openalex', 'missing_in_response' if item else 'not_found')
        except Exception as exc:
            if failed(batch_rows[start:], 'openalex', exc):
                break
        print(json.dumps({'public_abstract_batch': 'openalex', 'processed': min(start + 50, len(batch_rows)),
                          'total': len(batch_rows), 'retrieved': report['retrieved']}, ensure_ascii=False), flush=True)

    batch_rows = remaining()
    for start in range(0, len(batch_rows), 50):
        batch = batch_rows[start:start + 50]
        try:
            if start:
                time.sleep(1.1)
            data = request('semantic_scholar', 'post', 'https://api.semanticscholar.org/graph/v1/paper/batch',
                           params={'fields': 'title,abstract,externalIds,url'},
                           json={'ids': ['DOI:' + normalize_doi(r['doi']) for r in batch]}).json()
            if not isinstance(data, list):
                raise ValueError('Semantic Scholar batch response is not an array')
            matches = {normalize_doi((item.get('externalIds') or {}).get('DOI')): item
                       for item in data if isinstance(item, dict)}
            for record in batch:
                item = matches.get(normalize_doi(record['doi'])) or {}
                if not attach(record, item.get('abstract'), 'semantic_scholar',
                              item.get('url') or 'https://www.semanticscholar.org/paper/' + str(item.get('paperId', ''))):
                    attempt(record, 'semantic_scholar', 'missing_in_response' if item else 'not_found')
        except Exception as exc:
            if failed(batch_rows[start:], 'semantic_scholar', exc):
                break
        print(json.dumps({'public_abstract_batch': 'semantic_scholar', 'processed': min(start + 50, len(batch_rows)),
                          'total': len(batch_rows), 'retrieved': report['retrieved']}, ensure_ascii=False), flush=True)

    # A Crossref search already asks for abstracts; re-query only other-source DOIs.
    for record in remaining():
        crossref_already_checked = any(a.get('source') == 'crossref' and a.get('status') in
                                      {'missing_in_response', 'missing_in_search_response'}
                                      for a in record.get('abstract_attempts', []))
        if 'crossref' in (record.get('source_names') or []) or crossref_already_checked:
            attempt(record, 'crossref', 'missing_in_search_response')
            continue
        url = 'https://api.crossref.org/works/' + quote(normalize_doi(record['doi']), safe='')
        try:
            item = request('crossref', 'get', url).json().get('message') or {}
            text = item.get('abstract') if normalize_doi(item.get('DOI')) == normalize_doi(record['doi']) else None
            if text:
                text = re.sub(r'\s+', ' ', BeautifulSoup(text, 'html.parser').get_text()).strip()
            if not attach(record, text, 'crossref', 'https://doi.org/' + normalize_doi(record['doi'])):
                attempt(record, 'crossref', 'missing_in_response')
        except Exception as exc:
            if failed([record], 'crossref', exc):
                break
        print(json.dumps({'public_abstract_progress': 'crossref', 'doi': record.get('doi'),
                          'status': record.get('abstract_status', 'missing_in_response')}, ensure_ascii=False), flush=True)

    blocked_hosts = set()
    for record in remaining():
        if report['publisher_pages_attempted'] >= publisher_limit:
            attempt(record, 'publisher', 'page_limit')
            continue
        record_doi = normalize_doi(record['doi'])
        host = 'www.sciencedirect.com' if record_doi.startswith(('10.1016/', '10.1006/')) else None
        if host in blocked_hosts:
            attempt(record, 'publisher', 'host_unavailable')
            continue
        report['publisher_pages_attempted'] += 1
        url = 'https://doi.org/' + record_doi
        try:
            response = request('publisher', 'get', url, headers={'Accept': 'text/html'})
            # DOI hubs sometimes return a public HTML refresh rather than an HTTP redirect.
            soup = BeautifulSoup(response.text, 'html.parser')
            refresh = soup.find('meta', attrs={'http-equiv': re.compile('^refresh$', re.I)})
            if refresh:
                match = re.search(r'url\s*=\s*[\'\"]?(.+?)[\'\"]?\s*$', refresh.get('content', ''), re.I)
                if match:
                    target = urljoin(response.url, match.group(1))
                    if urlsplit(target).scheme in {'http', 'https'}:
                        response = request('publisher', 'get', target, headers={'Accept': 'text/html'})
            text = publisher_abstract(response.text, record_doi, record.get('title'))
            if not attach(record, text, 'publisher', 'https://doi.org/' + record_doi):
                attempt(record, 'publisher', 'no_identified_abstract')
        except Exception as exc:
            response = getattr(exc, 'response', None)
            code = response.status_code if response is not None else None
            failed([record], 'publisher', exc)
            if code in {401, 403, 429}:
                failed_host = urlsplit(response.url).hostname if response is not None else host
                if failed_host:
                    blocked_hosts.add(failed_host)
                if host:
                    blocked_hosts.add(host)
        print(json.dumps({'public_abstract_progress': report['publisher_pages_attempted'],
                          'doi': record_doi, 'status': record.get('abstract_status', 'missing_in_response')},
                         ensure_ascii=False), flush=True)

    for record in pending:
        if needs_abstract(record):
            record['abstract_status'] = 'summary_only' if record.get('abstract') else 'missing_in_response'
    report['remaining'] = sum(needs_abstract(r) for r in records)
    print(json.dumps({'public_abstracts': report}, ensure_ascii=False), flush=True)
    return report
