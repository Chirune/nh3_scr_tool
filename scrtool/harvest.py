"""Reproducible literature discovery and lawful full-text acquisition.

The implementation combines the useful, lightweight parts of the source projects
kept in ``../article source``: multi-source discovery/deduplication (findpapers),
OpenAlex discovery (pyalex), OA resolution (unpywall), publisher fallbacks
(paperscraper), and optional Elsevier XML retrieval (pybliometrics).

Only public API endpoints and URLs returned by those APIs are used.  A missing
full text remains an explicit status; it is never silently treated as a paper.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .core import write_csv, write_json


DEFAULT_QUERIES = [
    'NH3-SCR catalyst',
    'ammonia selective catalytic reduction catalyst',
    'selective catalytic reduction of NO with NH3',
]
ALLOWED_SOURCES = {'openalex', 'crossref', 'scopus', 'springer', 'nature'}
USER_AGENT = 'NH3SCRDataTool/0.2 (literature acquisition; contact={email})'


class _PdfLinkParser(HTMLParser):
    """Extract publisher-declared PDF links from a DOI landing page."""

    META_NAMES = {
        'citation_pdf_url', 'eprints.document_url', 'wkhealth_pdf_url',
        'dc.format.pdf', 'pdf_url',
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []

    def handle_starttag(self, tag, attrs):
        values = {str(key).lower(): value for key, value in attrs if value is not None}
        if tag.lower() == 'meta':
            name = (values.get('name') or values.get('property') or '').lower()
            content = values.get('content')
            if content and (name in self.META_NAMES or ('pdf' in name and content.startswith(('http://', 'https://', '/')))):
                self.urls.append(content)
        elif tag.lower() == 'link':
            href = values.get('href')
            mime = (values.get('type') or '').lower()
            if href and ('application/pdf' in mime or 'pdf' in (values.get('title') or '').lower()):
                self.urls.append(href)


def parse_nature_search(content, query):
    """Parse Nature's current public search cards into normalized records."""
    soup = BeautifulSoup(content, 'html.parser')
    records = []
    for article in soup.find_all('article'):
        link = article.find('a', attrs={'data-track-action': 'view article'})
        title_node = article.find('h3', class_='c-card__title')
        if not link or not title_node or not link.get('href'):
            continue
        href = link['href']
        match = re.search(r'/articles/([^/?#]+)', href)
        doi = f'10.1038/{match.group(1)}' if match else None
        date_node = article.find('time', attrs={'itemprop': 'datePublished'})
        date = date_node.get('datetime', '') if date_node else ''
        year_match = re.match(r'^(\d{4})', date)
        abstract_node = article.find(attrs={'itemprop': 'description'})
        journal_node = article.find(attrs={'data-test': 'journal-title-and-link'})
        type_node = article.find(attrs={'data-test': 'article.type'})
        authors = [node.get_text(' ', strip=True) for node in
                   article.select('li[itemprop="creator"] span[itemprop="name"]')]
        is_oa = article.find(attrs={'data-test': 'open-access'}) is not None
        records.append(_base_record(
            doi=doi, title=title_node.get_text(' ', strip=True),
            abstract=abstract_node.get_text(' ', strip=True) if abstract_node else None,
            year=int(year_match.group(1)) if year_match else None,
            journal=journal_node.get_text(' ', strip=True) if journal_node else None,
            publisher='Springer Nature', authors=authors,
            document_type=type_node.get_text(' ', strip=True) if type_node else None,
            is_oa=is_oa, oa_status='open' if is_oa else None,
            landing_page_url=urljoin('https://www.nature.com', href),
            source_names=['nature'], source_ids={'nature': href}, query_matches=[query],
        ))
    return records


def normalize_doi(value):
    value = (value or '').strip()
    value = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', value, flags=re.I)
    value = re.sub(r'^doi:\s*', '', value, flags=re.I)
    return value.strip().lower().rstrip('.,;)')


def normalize_title(value):
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').lower()).strip()


def _year_from_parts(parts):
    try:
        return int(parts[0][0])
    except (TypeError, ValueError, IndexError):
        return None


def _record_id(doi, title, year):
    key = normalize_doi(doi) or f'{normalize_title(title)}|{year or ""}'
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]


def file_fingerprint(path):
    path = Path(path)
    hasher = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            hasher.update(chunk)
    return {'sha256': hasher.hexdigest(), 'size_bytes': path.stat().st_size}


def is_valid_pdf(path):
    path = Path(path)
    if not path.exists() or path.stat().st_size <= 4:
        return False
    with path.open('rb') as handle:
        return handle.read(4) == b'%PDF'


def materialize_library_file(source, destination):
    """Expose a cached file in one run using a hard link, without copying bytes."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        return str(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return str(destination)
    try:
        os.link(source, destination)
        return str(destination)
    except OSError:
        # Cross-volume and restricted filesystems may not support hard links.
        # Point the manifest at the central copy instead of duplicating it.
        return str(source)


def safe_error(exc):
    text = f'{type(exc).__name__}: {exc}'
    return re.sub(r'([?&](?:api_key|apikey|email)=)[^&\s]+', r'\1[redacted]', text, flags=re.I)


def _base_record(**values):
    doi = normalize_doi(values.get('doi'))
    title = (values.get('title') or '').strip()
    year = values.get('year')
    return {
        'record_id': _record_id(doi, title, year),
        'doi': doi or None,
        'title': title or None,
        'abstract': values.get('abstract'),
        'year': year,
        'journal': values.get('journal'),
        'volume': values.get('volume'),
        'issue': values.get('issue'),
        'article_number': values.get('article_number'),
        'publisher': values.get('publisher'),
        'authors': list(dict.fromkeys(values.get('authors') or [])),
        'document_type': values.get('document_type'),
        'cited_by_count': values.get('cited_by_count'),
        'is_oa': values.get('is_oa'),
        'oa_status': values.get('oa_status'),
        'landing_page_url': values.get('landing_page_url'),
        'pdf_urls': list(dict.fromkeys(u for u in values.get('pdf_urls') or [] if u)),
        'source_names': list(dict.fromkeys(values.get('source_names') or [])),
        'source_ids': values.get('source_ids') or {},
        'query_matches': list(dict.fromkeys(values.get('query_matches') or [])),
    }


def parse_openalex(item, query):
    primary = item.get('primary_location') or {}
    best = item.get('best_oa_location') or {}
    locations = item.get('locations') or []
    pdf_urls = [best.get('pdf_url'), primary.get('pdf_url')]
    pdf_urls.extend((loc or {}).get('pdf_url') for loc in locations)
    if item.get('has_fulltext') and item.get('id'):
        work_id = item['id'].rstrip('/').split('/')[-1]
        pdf_urls.append(f'https://content.openalex.org/works/{work_id}.pdf')
    source = primary.get('source') or {}
    authors = []
    for authorship in item.get('authorships') or []:
        name = ((authorship or {}).get('author') or {}).get('display_name')
        if name:
            authors.append(name)
    return _base_record(
        doi=((item.get('ids') or {}).get('doi') or item.get('doi')),
        title=item.get('title') or item.get('display_name'),
        abstract=invert_abstract(item.get('abstract_inverted_index')),
        year=item.get('publication_year'), journal=source.get('display_name'),
        publisher=source.get('host_organization_name'), authors=authors,
        document_type=item.get('type'), cited_by_count=item.get('cited_by_count'),
        is_oa=(item.get('open_access') or {}).get('is_oa'),
        oa_status=(item.get('open_access') or {}).get('oa_status'),
        landing_page_url=primary.get('landing_page_url') or item.get('doi'),
        pdf_urls=pdf_urls, source_names=['openalex'],
        source_ids={'openalex': item.get('id')}, query_matches=[query],
    )


def invert_abstract(index):
    if not index:
        return None
    positioned = [(position, word) for word, positions in index.items() for position in positions]
    return ' '.join(word for _, word in sorted(positioned))


def parse_crossref(item, query):
    title = '; '.join(item.get('title') or [])
    journal = '; '.join(item.get('container-title') or []) or None
    authors = []
    for author in item.get('author') or []:
        name = ' '.join(x for x in [author.get('given'), author.get('family')] if x)
        if name:
            authors.append(name)
    issued = (item.get('published-print') or item.get('published-online') or item.get('issued') or {}).get('date-parts')
    pdf_urls = [link.get('URL') for link in item.get('link') or []
                if 'pdf' in (link.get('content-type') or '').lower()]
    return _base_record(
        doi=item.get('DOI'), title=title, abstract=item.get('abstract'),
        year=_year_from_parts(issued), journal=journal, publisher=item.get('publisher'),
        volume=item.get('volume'), issue=item.get('issue'),
        article_number=item.get('article-number') or item.get('page'),
        authors=authors, document_type=item.get('type'),
        cited_by_count=item.get('is-referenced-by-count'),
        landing_page_url=item.get('URL'), pdf_urls=pdf_urls,
        source_names=['crossref'], source_ids={'crossref': item.get('DOI')},
        query_matches=[query],
    )


def parse_scopus(item, query):
    date = item.get('prism:coverDate') or ''
    year = int(date[:4]) if re.match(r'^\d{4}', date) else None
    return _base_record(
        doi=item.get('prism:doi'), title=item.get('dc:title'),
        year=year, journal=item.get('prism:publicationName'),
        authors=[item['dc:creator']] if item.get('dc:creator') else [],
        document_type=item.get('subtypeDescription'),
        cited_by_count=int(item['citedby-count']) if str(item.get('citedby-count', '')).isdigit() else None,
        landing_page_url=item.get('prism:url'), source_names=['scopus'],
        source_ids={'scopus': item.get('dc:identifier')}, query_matches=[query],
    )


def parse_springer(item, query):
    date = item.get('publicationDate') or item.get('onlineDate') or ''
    year_match = re.search(r'\b(19|20)\d{2}\b', str(date))
    year = int(year_match.group(0)) if year_match else None
    authors = []
    for creator in item.get('creators') or []:
        name = creator.get('creator') if isinstance(creator, dict) else str(creator)
        if name:
            authors.append(name)
    urls, pdf_urls = [], []
    for link in item.get('url') or []:
        if isinstance(link, str):
            value, fmt = link, ''
        else:
            value, fmt = link.get('value'), link.get('format', '')
        if value:
            urls.append(value)
            if 'pdf' in fmt.lower() or value.lower().endswith('.pdf') or '/pdf' in value.lower():
                pdf_urls.append(value)
    doi = item.get('doi') or item.get('identifier')
    open_access = item.get('openaccess', item.get('openAccess'))
    if isinstance(open_access, str):
        open_access = open_access.lower() == 'true'
    return _base_record(
        doi=doi, title=item.get('title'), abstract=item.get('abstract'), year=year,
        journal=item.get('publicationName'), publisher=item.get('publisher'),
        authors=authors, document_type=item.get('contentType') or item.get('genre'),
        is_oa=open_access, oa_status='open' if open_access else None,
        landing_page_url=next((u for u in urls if 'doi.org/' in u), None) or (urls[0] if urls else None),
        pdf_urls=pdf_urls, source_names=['springer'],
        source_ids={'springer': item.get('identifier') or doi}, query_matches=[query],
    )


def _xml_text(element):
    return ' '.join(''.join(element.itertext()).split()) if element is not None else None


def parse_springer_jats(content, query):
    root = ET.fromstring(content)
    records = []
    for article in root.findall('.//article'):
        doi_node = article.find(".//article-id[@pub-id-type='doi']")
        title_node = article.find('.//article-title')
        date_year = article.find('.//pub-date/year')
        authors = []
        for contributor in article.findall(".//contrib[@contrib-type='author']"):
            surname = _xml_text(contributor.find('./name/surname'))
            given = _xml_text(contributor.find('./name/given-names'))
            name = ' '.join(x for x in [given, surname] if x)
            if name:
                authors.append(name)
        doi = _xml_text(doi_node)
        records.append(_base_record(
            doi=doi, title=_xml_text(title_node),
            abstract=_xml_text(article.find('.//abstract')),
            year=int(date_year.text) if date_year is not None and (date_year.text or '').isdigit() else None,
            journal=_xml_text(article.find('.//journal-title')),
            publisher=_xml_text(article.find('.//publisher-name')),
            authors=authors, document_type=article.get('article-type'),
            is_oa=True, oa_status='open',
            landing_page_url=f'https://doi.org/{doi}' if doi else None,
            source_names=['springer'], source_ids={'springer': doi}, query_matches=[query],
        ))
    return records


def merge_records(records: Iterable[dict]):
    merged = {}
    title_index = {}
    for incoming in records:
        doi = normalize_doi(incoming.get('doi'))
        title_key = (normalize_title(incoming.get('title')), incoming.get('year'))
        key = ('doi', doi) if doi else ('title',) + title_key
        if doi and title_key[0] and title_key in title_index:
            candidate_key = title_index[title_key]
            candidate_doi = normalize_doi(merged[candidate_key].get('doi'))
            if not candidate_doi or candidate_doi == doi:
                key = candidate_key
        if key not in merged:
            merged[key] = dict(incoming)
        else:
            current = merged[key]
            for field in ['doi', 'title', 'abstract', 'year', 'journal', 'volume', 'issue',
                          'article_number', 'publisher',
                          'document_type', 'cited_by_count', 'is_oa', 'oa_status',
                          'landing_page_url']:
                if current.get(field) in (None, '') and incoming.get(field) not in (None, ''):
                    current[field] = incoming[field]
            for field in ['authors', 'pdf_urls', 'source_names', 'query_matches']:
                current[field] = list(dict.fromkeys((current.get(field) or []) + (incoming.get(field) or [])))
            current['source_ids'] = {**(current.get('source_ids') or {}), **(incoming.get('source_ids') or {})}
            current['record_id'] = _record_id(current.get('doi'), current.get('title'), current.get('year'))
        if title_key[0]:
            title_index[title_key] = key
    # API results arrive in relevance order.  Preserve that order so an exact
    # title/DOI match is not displaced by a newer but weaker match.
    return list(merged.values())


class Harvester:
    def __init__(self, email=None, openalex_key=None, elsevier_key=None, springer_key=None, timeout=60):
        self.email = email or os.getenv('UNPAYWALL_EMAIL') or os.getenv('OPENALEX_EMAIL')
        self.openalex_key = openalex_key or os.getenv('OPENALEX_API_KEY')
        self.elsevier_key = elsevier_key or os.getenv('ELSEVIER_API_KEY') or os.getenv('ELSEVIER_TDM_API_KEY')
        self.elsevier_insttoken = os.getenv('ELSEVIER_INSTTOKEN')
        self.springer_key = springer_key or os.getenv('SPRINGER_API_KEY')
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT.format(email=self.email or 'not-provided')})
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods={'GET'}, respect_retry_after_header=True)
        self.session.mount('https://', HTTPAdapter(max_retries=retry))

    def _get_json(self, url, request_timeout=None, **kwargs):
        response = self.session.get(url, timeout=request_timeout or self.timeout, **kwargs)
        response.raise_for_status()
        return response.json()

    def search_openalex(self, query, limit, from_year=None, to_year=None):
        results, cursor = [], '*'
        while len(results) < limit:
            params = {'search': query, 'per-page': min(200, limit - len(results)), 'cursor': cursor}
            filters = []
            if from_year:
                filters.append(f'from_publication_date:{from_year}-01-01')
            if to_year:
                filters.append(f'to_publication_date:{to_year}-12-31')
            if filters:
                params['filter'] = ','.join(filters)
            if self.openalex_key:
                params['api_key'] = self.openalex_key
            elif self.email:
                params['mailto'] = self.email
            data = self._get_json('https://api.openalex.org/works', params=params)
            page = data.get('results') or []
            results.extend(parse_openalex(item, query) for item in page)
            cursor = (data.get('meta') or {}).get('next_cursor')
            if not page or not cursor:
                break
        return results[:limit]

    def search_crossref(self, query, limit, from_year=None, to_year=None):
        query_doi = normalize_doi(query)
        if query_doi.startswith('10.') and ' ' not in query_doi:
            data = self._get_json(f'https://api.crossref.org/works/{quote(query_doi, safe="")}')
            item = data.get('message') or {}
            return [parse_crossref(item, query)] if item else []
        params = {'query.bibliographic': query, 'rows': min(limit, 1000),
                  'select': 'DOI,title,abstract,author,publisher,published-print,published-online,issued,container-title,volume,issue,article-number,page,type,is-referenced-by-count,URL,link'}
        filters = ['type:journal-article']
        if from_year:
            filters.append(f'from-pub-date:{from_year}-01-01')
        if to_year:
            filters.append(f'until-pub-date:{to_year}-12-31')
        if filters:
            params['filter'] = ','.join(filters)
        data = self._get_json('https://api.crossref.org/works', params=params)
        return [parse_crossref(item, query) for item in (data.get('message') or {}).get('items', [])]

    def search_scopus(self, query, limit, from_year=None, to_year=None):
        if not self.elsevier_key:
            raise ValueError('Scopus requires ELSEVIER_API_KEY')
        query_doi = normalize_doi(query)
        expression = f'DOI({query_doi})' if query_doi.startswith('10.') and ' ' not in query_doi else f'TITLE-ABS-KEY({query})'
        if from_year:
            expression += f' AND PUBYEAR AFT {from_year - 1}'
        if to_year:
            expression += f' AND PUBYEAR BEF {to_year + 1}'
        results = []
        for start in range(0, limit, 25):
            data = self._get_json(
                'https://api.elsevier.com/content/search/scopus',
                params={'query': expression, 'start': start, 'count': min(25, limit - start)},
                headers={'X-ELS-APIKey': self.elsevier_key, 'Accept': 'application/json',
                         'User-Agent': self.session.headers['User-Agent']},
            )
            page = (data.get('search-results') or {}).get('entry') or []
            results.extend(parse_scopus(item, query) for item in page if 'error' not in item)
            if len(page) < 25:
                break
        return results[:limit]

    def search_springer(self, query, limit, from_year=None, to_year=None):
        if not self.springer_key:
            raise ValueError('Springer Nature search requires SPRINGER_API_KEY')
        results = []
        for start in range(1, limit + 1, 100):
            page_size = min(100, limit - len(results))
            query_doi = normalize_doi(query)
            springer_query = f'doi:"{query_doi}"' if query_doi.startswith('10.') and ' ' not in query_doi else f'keyword:"{query}"'
            params = {'q': springer_query, 'p': page_size, 's': start,
                      'api_key': self.springer_key}
            try:
                data = self._get_json('https://api.springernature.com/meta/v2/json', params=params)
                page = data.get('records') or []
                parsed = [parse_springer(item, query) for item in page]
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status not in {401, 403, 404}:
                    raise
                # This user's key is authorized for the OA JATS endpoint even
                # though Meta v2 is unavailable. JATS supports the same q/p/s query.
                response = self.session.get(
                    'https://api.springernature.com/openaccess/jats', params=params,
                    headers={'Accept': 'application/xml', 'User-Agent': self.session.headers['User-Agent']},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                parsed = parse_springer_jats(response.content, query)
                page = parsed
            if from_year:
                parsed = [item for item in parsed if item.get('year') and item['year'] >= from_year]
            if to_year:
                parsed = [item for item in parsed if item.get('year') and item['year'] <= to_year]
            results.extend(parsed)
            if len(page) < page_size:
                break
        return results[:limit]

    def search_nature(self, query, limit, from_year=None, to_year=None):
        """Discover Nature-family articles from the current public search page."""
        results = []
        for page_number in range(1, 101):
            params = {'q': query, 'order': 'relevance', 'page': page_number}
            if from_year or to_year:
                start = from_year or 1848
                end = to_year or datetime.now(timezone.utc).year
                params['date_range'] = f'{start}-{end}'
            response = self.session.get(
                'https://www.nature.com/search', params=params,
                headers={'Accept': 'text/html,application/xhtml+xml',
                         'Accept-Language': 'en-US,en;q=0.8',
                         'User-Agent': self.session.headers['User-Agent']},
                timeout=self.timeout, allow_redirects=True,
            )
            response.raise_for_status()
            page = parse_nature_search(response.content, query)
            if from_year:
                page = [item for item in page if item.get('year') and item['year'] >= from_year]
            if to_year:
                page = [item for item in page if item.get('year') and item['year'] <= to_year]
            results.extend(page)
            if len(results) >= limit or not page:
                break
        return results[:limit]

    def resolve_unpaywall(self, doi):
        if not self.email or not doi:
            return []
        data = self._get_json(f'https://api.unpaywall.org/v2/{quote(doi, safe="")}', params={'email': self.email})
        locations = [data.get('best_oa_location')] + (data.get('oa_locations') or [])
        return list(dict.fromkeys(loc.get('url_for_pdf') for loc in locations if loc and loc.get('url_for_pdf')))

    def resolve_openalex_doi(self, doi):
        """Resolve OA copies through the free OpenAlex singleton endpoint."""
        if not doi:
            return []
        params = {'api_key': self.openalex_key} if self.openalex_key else {}
        if self.email and not self.openalex_key:
            params['mailto'] = self.email
        try:
            data = self._get_json(
                f'https://api.openalex.org/works/https://doi.org/{quote(doi, safe="/().-_")}',
                params=params, request_timeout=(10, min(self.timeout, 20)),
            )
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return []
            raise
        locations = [data.get('best_oa_location'), data.get('primary_location')]
        locations.extend(data.get('locations') or [])
        urls = [loc.get('pdf_url') for loc in locations if loc and loc.get('pdf_url')]
        # OpenAlex's content service requires an API key; metadata lookups do not.
        if self.openalex_key and data.get('has_fulltext') and data.get('id'):
            work_id = data['id'].rstrip('/').split('/')[-1]
            urls.append(f'https://content.openalex.org/works/{work_id}.pdf')
        return list(dict.fromkeys(urls))

    def resolve_semantic_scholar_doi(self, doi):
        """Resolve an openly hosted PDF through Semantic Scholar metadata."""
        if not doi:
            return []
        try:
            data = self._get_json(
                f'https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi, safe="")}',
                params={'fields': 'openAccessPdf'}, request_timeout=(10, min(self.timeout, 20)),
            )
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in {404, 429}:
                return []
            raise
        pdf = data.get('openAccessPdf') or {}
        return [pdf['url']] if pdf.get('url') else []

    def resolve_hal_doi(self, doi):
        """Resolve author-deposited copies from the French HAL repository."""
        if not doi:
            return []
        data = self._get_json(
            'https://api.archives-ouvertes.fr/search/',
            params={'q': f'doiId_s:{doi}', 'fl': 'fileMain_s', 'rows': 5, 'wt': 'json'},
            request_timeout=(10, min(self.timeout, 20)),
        )
        docs = (data.get('response') or {}).get('docs') or []
        return list(dict.fromkeys(item.get('fileMain_s') for item in docs if item.get('fileMain_s')))

    def resolve_landing_page_pdf(self, doi, landing_page_url=None):
        """Follow a DOI and read standard citation metadata for its PDF URL."""
        target = landing_page_url or (f'https://doi.org/{doi}' if doi else None)
        if not target or not target.startswith(('http://', 'https://')):
            return []
        response = self.session.get(
            target, timeout=(10, min(self.timeout, 20)), allow_redirects=True,
            headers={'Accept': 'text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.1',
                     'User-Agent': self.session.headers['User-Agent']},
        )
        response.raise_for_status()
        content_type = (response.headers.get('Content-Type') or '').lower()
        if 'application/pdf' in content_type or response.content[:4] == b'%PDF':
            return [response.url]
        if 'html' not in content_type and not response.content.lstrip().lower().startswith((b'<!doctype html', b'<html')):
            return []
        parser = _PdfLinkParser()
        parser.feed(response.text[:5_000_000])
        return list(dict.fromkeys(urljoin(response.url, value) for value in parser.urls))

    def download_pdf(self, urls, destination, max_bytes=100 * 1024 * 1024):
        destination = Path(destination)
        if is_valid_pdf(destination):
            return {'status': 'cached_pdf', 'path': str(destination.resolve()), 'url': None,
                    **file_fingerprint(destination)}
        errors = []
        for url in list(dict.fromkeys(u for u in urls if isinstance(u, str) and u.startswith(('http://', 'https://')))):
            try:
                with self.session.get(url, timeout=self.timeout, stream=True, allow_redirects=True,
                                      headers={'Accept': 'application/pdf,application/octet-stream;q=0.9,*/*;q=0.1',
                                               'User-Agent': self.session.headers['User-Agent']}) as response:
                    response.raise_for_status()
                    total = 0
                    first = True
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temp = destination.with_suffix(destination.suffix + '.part')
                    with temp.open('wb') as handle:
                        for chunk in response.iter_content(1024 * 128):
                            if not chunk:
                                continue
                            if first and not chunk.startswith(b'%PDF'):
                                raise ValueError('response is not a PDF')
                            first = False
                            total += len(chunk)
                            if total > max_bytes:
                                raise ValueError(f'PDF exceeds {max_bytes} bytes')
                            handle.write(chunk)
                    if first:
                        raise ValueError('empty response')
                    temp.replace(destination)
                    return {'status': 'downloaded_pdf', 'path': str(destination.resolve()), 'url': url,
                            **file_fingerprint(destination)}
            except Exception as exc:
                part = destination.with_suffix(destination.suffix + '.part')
                if part.exists():
                    part.unlink()
                errors.append(f'{url}: {safe_error(exc)}')
        return {'status': 'no_pdf', 'path': None, 'url': None, 'errors': errors}

    def download_elsevier_xml(self, doi, destination):
        if not self.elsevier_key or not doi:
            return None
        destination = Path(destination)
        if destination.exists() and destination.stat().st_size > 20:
            return {'status': 'existing_xml', 'path': str(destination.resolve()), **file_fingerprint(destination)}
        url = f'https://api.elsevier.com/content/article/doi/{quote(doi, safe="")}'
        headers = {'Accept': 'application/xml', 'X-ELS-APIKey': self.elsevier_key,
                   'User-Agent': self.session.headers['User-Agent']}
        if self.elsevier_insttoken:
            headers['X-ELS-Insttoken'] = self.elsevier_insttoken
        response = self.session.get(url, params={'httpAccept': 'text/xml'}, headers=headers,
                                    timeout=self.timeout)
        response.raise_for_status()
        content = response.content.lstrip()
        if not content.startswith(b'<?xml') and not content.startswith(b'<'):
            raise ValueError('Elsevier response is not XML')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return {'status': 'downloaded_xml', 'path': str(destination.resolve()), 'url': url,
                **file_fingerprint(destination)}

    def download_springer_jats(self, doi, destination):
        if not self.springer_key or not doi:
            return None
        destination = Path(destination)
        if destination.exists() and destination.stat().st_size > 20:
            return {'status': 'existing_jats', 'path': str(destination.resolve()),
                    **file_fingerprint(destination)}
        url = 'https://api.springernature.com/openaccess/jats'
        response = self.session.get(
            url, params={'q': f'doi:"{doi}"', 'api_key': self.springer_key},
            headers={'Accept': 'application/xml', 'User-Agent': self.session.headers['User-Agent']},
            timeout=self.timeout,
        )
        response.raise_for_status()
        content = response.content.lstrip()
        if not content.startswith(b'<'):
            raise ValueError('Springer Nature response is not XML')
        text_head = content[:2000].lower()
        if b'<records>0</records>' in text_head or b'<total>0</total>' in text_head:
            return {'status': 'no_open_jats', 'path': None, 'url': url}
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return {'status': 'downloaded_jats', 'path': str(destination.resolve()), 'url': url,
                **file_fingerprint(destination)}


def safe_stem(record):
    value = record.get('doi') or record.get('title') or record['record_id']
    value = re.sub(r'[^A-Za-z0-9._-]+', '_', value).strip('._')
    return (value[:140] or record['record_id']) + '_' + record['record_id'][:8]


def mdpi_static_pdf_urls(record):
    """Build MDPI's public static-asset PDF URL when metadata is sufficient."""
    doi = (record.get('doi') or '').lower()
    if not doi.startswith('10.3390/'):
        return []
    suffix = doi.split('/', 1)[1]
    match = re.match(r'([a-z]+)(\d+)$', suffix)
    if not match:
        return []
    prefix, digits = match.groups()
    journal_map = {'catal': 'catalysts'}
    journal_slug = journal_map.get(prefix, prefix)
    volume_text = str(record.get('volume') or '').strip()
    article_text = str(record.get('article_number') or '').strip()
    if not volume_text or not article_text:
        return []
    volume_match = re.search(r'\d+', volume_text)
    article_match = re.search(r'\d+', article_text)
    if not volume_match or not article_match:
        return []
    volume = int(volume_match.group())
    article = int(article_match.group())
    stem = f'{journal_slug}-{volume:02d}-{article:05d}'
    return [f'https://mdpi-res.com/d_attachment/{journal_slug}/{stem}/article_deploy/{stem}.pdf']


def inventory_local(paths):
    rows = []
    supported = {'.pdf', '.xml', '.html', '.htm', '.md', '.txt'}
    for raw in paths or []:
        root = Path(raw).resolve()
        files = [root] if root.is_file() else sorted(p for p in root.rglob('*') if p.is_file())
        for path in files:
            if path.suffix.lower() not in supported:
                continue
            fingerprint = file_fingerprint(path)
            rows.append({'path': str(path), 'relative_path': path.name if root.is_file() else path.relative_to(root).as_posix(),
                         'extension': path.suffix.lower(), **fingerprint, 'status': 'local_existing'})
    return rows


def run_harvest(args):
    if args.limit < 1 or args.limit > 1000:
        raise ValueError('--limit must be between 1 and 1000')
    if args.max_downloads < 0 or args.max_file_mb < 1 or args.timeout < 1 or args.delay < 0:
        raise ValueError('Download limits, timeout, and delay must be non-negative and valid')
    if args.from_year and args.to_year and args.from_year > args.to_year:
        raise ValueError('--from-year cannot be later than --to-year')
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    queries = args.query or DEFAULT_QUERIES
    source_tokens = args.sources if isinstance(args.sources, list) else [args.sources]
    source_parts = [part.strip().lower() for token in source_tokens for part in token.split(',') if part.strip()]
    sources = set() if source_parts == ['none'] else set(source_parts)
    unknown = sources - ALLOWED_SOURCES
    if unknown:
        raise ValueError('Unknown sources: ' + ', '.join(sorted(unknown)))
    harvester = Harvester(email=args.email, openalex_key=args.openalex_key,
                          elsevier_key=args.elsevier_key, springer_key=args.springer_key,
                          timeout=args.timeout)
    records, errors = [], []
    for query in queries:
        for source in sorted(sources):
            try:
                method = getattr(harvester, 'search_' + source)
                records.extend(method(query, args.limit, args.from_year, args.to_year))
            except Exception as exc:
                errors.append({'stage': 'search', 'source': source, 'query': query,
                               'error': safe_error(exc)})
    records = merge_records(records)
    write_json(output / 'records.json', records)
    write_csv(output / 'records.csv', records)

    local_rows = inventory_local(args.local_papers)
    write_json(output / 'local_documents.json', local_rows)
    write_csv(output / 'local_documents.csv', local_rows)

    default_library_parent = output.parent.parent if output.parent.name.lower() == 'runs' else output.parent
    library_dir = (Path(args.library_dir).resolve() if getattr(args, 'library_dir', None)
                   else default_library_parent / 'paper_library')
    manifest = []
    if args.download:
        attempted = 0
        files_dir = output / 'files'
        library_dir.mkdir(parents=True, exist_ok=True)
        legacy_files = {}
        for candidate in output.parent.glob('*/files/*'):
            if candidate.is_file():
                legacy_files.setdefault(candidate.name, candidate)
        # Prefer records with API-declared or commonly open-access full text so
        # a short limit is not consumed entirely by paywalled search results.
        oa_prefixes = ('10.3390/', '10.3389/', '10.1371/', '10.1186/', '10.7717/')
        download_order = sorted(
            enumerate(records),
            key=lambda pair: (
                0 if (pair[1].get('doi') or '').startswith(oa_prefixes) else 1,
                0 if pair[1].get('is_oa') else 1,
                0 if pair[1].get('pdf_urls') else 1,
                pair[0],
            ),
        )
        for _, record in download_order:
            row = {'record_id': record['record_id'], 'doi': record.get('doi'), 'title': record.get('title'),
                   'status': 'not_attempted', 'path': None, 'url': None, 'errors': []}
            if attempted >= args.max_downloads:
                row['status'] = 'download_limit'
                manifest.append(row)
                continue
            attempted += 1
            stem = safe_stem(record)
            cached_pdf = library_dir / (stem + '.pdf')
            legacy_pdf = legacy_files.get(cached_pdf.name)
            if not is_valid_pdf(cached_pdf) and legacy_pdf and is_valid_pdf(legacy_pdf):
                materialize_library_file(legacy_pdf, cached_pdf)
            urls = []
            if not is_valid_pdf(cached_pdf):
                urls = list(record.get('pdf_urls') or [])
                urls.extend(mdpi_static_pdf_urls(record))
                if record.get('doi'):
                    try:
                        urls.extend(harvester.resolve_openalex_doi(record['doi']))
                    except Exception as exc:
                        row['errors'].append('OpenAlex DOI resolver: ' + safe_error(exc))
                    try:
                        urls.extend(harvester.resolve_semantic_scholar_doi(record['doi']))
                    except Exception as exc:
                        row['errors'].append('Semantic Scholar DOI resolver: ' + safe_error(exc))
                if record.get('doi') and harvester.email:
                    try:
                        urls.extend(harvester.resolve_unpaywall(record['doi']))
                    except Exception as exc:
                        row['errors'].append('Unpaywall: ' + safe_error(exc))
            result = harvester.download_pdf(urls, cached_pdf, args.max_file_mb * 1024 * 1024)
            # Publisher links are sometimes blocked to scripts even for OA
            # papers. Only after that failure, try the DOI page and HAL copy.
            if result.get('status') == 'no_pdf':
                fallback_urls = []
                try:
                    fallback_urls.extend(harvester.resolve_landing_page_pdf(
                        record.get('doi'), record.get('landing_page_url')))
                except Exception as exc:
                    row['errors'].append('DOI landing page: ' + safe_error(exc))
                if record.get('doi'):
                    try:
                        fallback_urls.extend(harvester.resolve_hal_doi(record['doi']))
                    except Exception as exc:
                        row['errors'].append('HAL DOI resolver: ' + safe_error(exc))
                if fallback_urls:
                    fallback_result = harvester.download_pdf(
                        fallback_urls, cached_pdf, args.max_file_mb * 1024 * 1024)
                    if fallback_result.get('status') != 'no_pdf':
                        fallback_result['errors'] = (result.get('errors') or []) + (fallback_result.get('errors') or [])
                        result = fallback_result
                    else:
                        result['errors'] = (result.get('errors') or []) + (fallback_result.get('errors') or [])
            row.update(result)
            if result.get('errors'):
                row['errors'] = row['errors'] + result['errors']
            if result.get('path') and result.get('status') in {'downloaded_pdf', 'cached_pdf'}:
                row['library_path'] = str(cached_pdf.resolve())
                row['path'] = materialize_library_file(cached_pdf, files_dir / (stem + '.pdf'))
            publisher_text = (record.get('publisher') or '').lower()
            doi_text = (record.get('doi') or '').lower()
            elsevier_record = 'elsevier' in publisher_text or doi_text.startswith(('10.1016/', '10.1006/'))
            if record.get('doi') and harvester.elsevier_key and elsevier_record:
                try:
                    cached_xml = library_dir / (stem + '.xml')
                    legacy_xml = legacy_files.get(cached_xml.name)
                    if not cached_xml.exists() and legacy_xml:
                        materialize_library_file(legacy_xml, cached_xml)
                    xml_result = harvester.download_elsevier_xml(record['doi'], cached_xml)
                    if xml_result:
                        if xml_result.get('path'):
                            xml_result['library_path'] = str(cached_xml.resolve())
                            xml_result['path'] = materialize_library_file(
                                cached_xml, files_dir / (stem + '.xml'))
                        row['elsevier_xml'] = xml_result
                        if row['status'] == 'no_pdf' and xml_result.get('path'):
                            row['status'] = 'downloaded_xml_only'
                            row['path'] = xml_result['path']
                except Exception as exc:
                    row['errors'].append('Elsevier: ' + safe_error(exc))
            springer_record = 'springer' in (record.get('source_names') or []) or any(
                marker in publisher_text for marker in ['springer', 'nature'])
            if record.get('doi') and harvester.springer_key and springer_record:
                try:
                    cached_jats = library_dir / (stem + '.springer.jats.xml')
                    legacy_jats = legacy_files.get(cached_jats.name)
                    if not cached_jats.exists() and legacy_jats:
                        materialize_library_file(legacy_jats, cached_jats)
                    jats_result = harvester.download_springer_jats(record['doi'], cached_jats)
                    if jats_result:
                        if jats_result.get('path'):
                            jats_result['library_path'] = str(cached_jats.resolve())
                            jats_result['path'] = materialize_library_file(
                                cached_jats, files_dir / (stem + '.springer.jats.xml'))
                        row['springer_jats'] = jats_result
                        if row['status'] == 'no_pdf' and jats_result.get('path'):
                            row['status'] = 'downloaded_jats_only'
                            row['path'] = jats_result['path']
                except Exception as exc:
                    row['errors'].append('Springer Nature: ' + safe_error(exc))
            manifest.append(row)
            print(json.dumps({'download_progress': attempted,
                              'doi': record.get('doi'), 'status': row['status']},
                             ensure_ascii=False), flush=True)
            if args.delay:
                time.sleep(args.delay)
    write_json(output / 'download_manifest.json', manifest)
    write_csv(output / 'download_manifest.csv', manifest)
    write_json(output / 'errors.json', errors)
    counts = {}
    for row in manifest:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    summary = {'created_at': datetime.now(timezone.utc).isoformat(),
               'queries': queries, 'sources': sorted(sources), 'records': len(records),
               'local_documents': len(local_rows), 'search_errors': len(errors),
               'download_status': counts, 'output': str(output),
               'paper_library': str(library_dir)}
    write_json(output / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 2 if sources and not records and errors else 0
