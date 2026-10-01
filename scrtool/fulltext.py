"""Acquire a selected paper's PDF through the existing DOI resolvers."""
from __future__ import annotations

from pathlib import Path

from .harvest import Harvester, is_valid_pdf, normalize_doi, safe_error


def guidance(record, result):
    """Turn provider diagnostics into a next step a reader can act on."""
    if result.get('status') == 'missing_doi':
        return '题录缺少 DOI。请先核对 DOI，或从浏览器下载论文后添加本地文件。'
    if result.get('path'):
        return '已获取 PDF，请打开核对标题和 DOI，再开始提取。'
    publisher = str(record.get('publisher') or '').lower()
    doi = normalize_doi(record.get('doi'))
    if 'elsevier' in publisher or doi.startswith(('10.1016/', '10.1006/')):
        message = '未找到可直接下载的开放 PDF。此论文可能需要机构访问：请打开论文网页，在浏览器下载 PDF 后添加本地文件。'
    else:
        message = '未找到可直接下载的 PDF。请打开论文网页核对获取方式，下载后添加本地文件。'
    if any('429' in str(error) for error in result.get('errors', [])):
        message += ' Semantic Scholar 暂时限流；这不代表论文被排除。'
    if any('Elsevier 全文 API' in str(error) and any(code in str(error) for code in ('401', '403'))
           for error in result.get('errors', [])):
        message += ' Elsevier 全文接口已尝试，但当前 API 凭证没有取得该论文全文。'
    return message


def publisher_route(record):
    """Route by publisher metadata, with DOI prefix as a fallback."""
    publisher = str(record.get('publisher') or '').lower()
    doi = normalize_doi(record.get('doi'))
    if 'elsevier' in publisher or doi.startswith(('10.1016/', '10.1006/')):
        return 'elsevier'
    if any(name in publisher for name in ('springer', 'nature')) or doi.startswith(
            ('10.1007/', '10.1038/', '10.1186/')):
        return 'springer_nature'
    return 'open_sources'


def acquire_pdf(record, destination, *, email=None, openalex_key=None, elsevier_key=None,
                elsevier_insttoken=None, springer_key=None, timeout=20, harvester=None):
    """Return a source-labelled result; never treat a web page as a PDF."""
    doi = normalize_doi(record.get('doi'))
    if not doi:
        result = {'status': 'missing_doi', 'path': None, 'source': None, 'url': None,
                  'errors': ['这篇题录没有 DOI，无法自动定位正文。']}
        return {**result, 'message': guidance(record, result)}
    destination = Path(destination)
    if is_valid_pdf(destination):
        result = {'status': 'cached_pdf', 'path': str(destination.resolve()),
                  'source': 'project_cache', 'url': None, 'errors': []}
        return {**result, 'message': guidance(record, result)}

    client = harvester or Harvester(email=email, openalex_key=openalex_key,
                                   elsevier_key=elsevier_key, elsevier_insttoken=elsevier_insttoken,
                                   springer_key=springer_key, timeout=timeout)
    errors = []
    route = publisher_route(record)
    if route == 'elsevier' and client.elsevier_key:
        try:
            result = client.download_elsevier_pdf(doi, destination)
            if result and result.get('path') and is_valid_pdf(destination):
                success = {**result, 'source': 'Elsevier 全文 API', 'errors': result.get('errors', [])}
                return {**success, 'message': guidance(record, success)}
            if result:
                errors.extend('Elsevier 全文 API：' + item for item in result.get('errors', []))
        except Exception as exc:
            errors.append('Elsevier 全文 API：' + safe_error(exc))

    def springer_pdf_urls():
        matches = client.search_springer(doi, 1)
        return [url for row in matches if normalize_doi(row.get('doi')) == doi
                for url in row.get('pdf_urls') or []]

    sources = [
        ('题录中的 PDF 链接', lambda: record.get('pdf_urls') or []),
        ('OpenAlex', lambda: client.resolve_openalex_doi(doi)),
        ('Semantic Scholar', lambda: client.resolve_semantic_scholar_doi(doi)),
    ]
    if route == 'springer_nature' and client.springer_key:
        sources.insert(1, ('Springer Nature 接口', springer_pdf_urls))
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
                success = {**result, 'source': source, 'errors': errors + result.get('errors', [])}
                return {**success, 'message': guidance(record, success)}
            errors.extend(f'{source}：{item}' for item in result.get('errors', []))
        except Exception as exc:
            errors.append(f'{source}：{safe_error(exc)}')
    result = {'status': 'no_pdf', 'path': None, 'source': None, 'url': None,
              'errors': errors or ['各来源都未提供可下载的 PDF。']}
    return {**result, 'message': guidance(record, result)}
