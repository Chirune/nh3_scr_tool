"""Acquire a selected paper's PDF through the existing DOI resolvers."""
from __future__ import annotations

from pathlib import Path

from .harvest import Harvester, is_valid_pdf, normalize_doi, safe_error


def acquire_pdf(record, destination, *, email=None, timeout=20, harvester=None):
    """Return a source-labelled result; never treat a web page as a PDF."""
    doi = normalize_doi(record.get('doi'))
    if not doi:
        return {'status': 'missing_doi', 'path': None, 'source': None, 'url': None,
                'errors': ['这篇题录没有 DOI，无法自动定位正文。']}
    destination = Path(destination)
    if is_valid_pdf(destination):
        return {'status': 'cached_pdf', 'path': str(destination.resolve()),
                'source': 'project_cache', 'url': None, 'errors': []}

    client = harvester or Harvester(email=email, timeout=timeout)
    errors = []
    sources = [
        ('题录中的 PDF 链接', lambda: record.get('pdf_urls') or []),
        ('OpenAlex', lambda: client.resolve_openalex_doi(doi)),
        ('Semantic Scholar', lambda: client.resolve_semantic_scholar_doi(doi)),
    ]
    if client.email:
        sources.append(('Unpaywall', lambda: client.resolve_unpaywall(doi)))
    sources.extend([
        ('DOI 出版社页面', lambda: client.resolve_landing_page_pdf(
            doi, record.get('landing_page_url') or None)),
        ('HAL 开放存档', lambda: client.resolve_hal_doi(doi)),
    ])
    tried = set()
    for source, resolve in sources:
        try:
            urls = resolve()
            if isinstance(urls, str):
                urls = [urls]
            urls = [url for url in urls if isinstance(url, str) and url not in tried]
            tried.update(urls)
            if not urls:
                continue
            result = client.download_pdf(urls, destination)
            if result.get('path') and is_valid_pdf(destination):
                return {**result, 'source': source, 'errors': errors + result.get('errors', [])}
            errors.extend(f'{source}：{item}' for item in result.get('errors', []))
        except Exception as exc:
            errors.append(f'{source}：{safe_error(exc)}')
    return {'status': 'no_pdf', 'path': None, 'source': None, 'url': None,
            'errors': errors or ['各来源都未提供可下载的 PDF。']}
