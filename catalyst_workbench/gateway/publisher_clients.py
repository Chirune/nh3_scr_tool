"""Publisher-specific full-text routes. Credentials remain in this process only.

Wiley: official TDM PDF endpoint; token AND institutional IP entitlement.
Springer: OA JSON lookup followed by the PDF link advertised for that DOI.
Neither metadata nor a login page is a successful PDF retrieval.
"""
from __future__ import annotations

import hashlib
from http.client import IncompleteRead
import json
from pathlib import Path
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, build_opener
import uuid

from elsevier_api import (APIError, ElsevierClient, NoRedirect, _credential,
                          _quota, _retry_delay, supports as elsevier_supports)
from pdf_fetch import (MAX_META_BYTES, MAX_PDF_BYTES, Stopped, FetchError,
                       _doi, inspect_pdf, html_pdf_links, public_url, now)

SPRINGER_REGISTER = 'https://dev.springernature.com/'
SPRINGER_DOCS = 'https://dev.springernature.com/docs/api-endpoints/open-access/'
SPRINGER_META_DOCS = 'https://dev.springernature.com/docs/api-endpoints/meta-api/'
# Official documentation uses this public OA article as a worked example.
# A connectivity probe never saves it as a screened paper or downloads its PDF.
SPRINGER_PROBE_DOI = '10.1038/s41598-020-79929-0'
WILEY_REGISTER = 'https://static.wiley.com/tdm/'
WILEY_DOCS = 'https://onlinelibrary.wiley.com/library-info/resources/text-and-datamining'


def provider_for(record):
    doi = _doi(record.get('doi'))
    if not doi:
        return None
    # Prefer a recognized, current publisher route over historical DOI prefixes.
    group = (record.get('route') or {}).get('publisher_group')
    groups = {'Elsevier': 'elsevier', 'Springer Nature': 'springer', 'Wiley': 'wiley'}
    if group in groups:
        return groups[group]
    if group and group != '其他 / 未识别':
        return None
    if doi.startswith(('10.1007/', '10.1038/', '10.1186/')):
        return 'springer'
    if doi.startswith(('10.1002/', '10.1111/')):
        return 'wiley'
    if elsevier_supports(record):
        return 'elsevier'
    return None


def select_client(configured, record):
    if isinstance(configured, PublisherClients):
        return configured.for_record(record)
    provider = getattr(configured, 'provider_id', 'elsevier')
    return configured if configured is not None and provider_for(record) == provider else None


class PublisherClients:
    def __init__(self, previous=None):
        self.clients = {}
        if isinstance(previous, PublisherClients):
            self.clients.update(previous.clients)
        elif previous is not None:
            self.clients[getattr(previous, 'provider_id', 'elsevier')] = previous

    def __repr__(self):
        return '<PublisherClients: credentials hidden, memory only>'

    def for_record(self, record):
        return self.clients.get(provider_for(record))


def same_credentials(client, key, token=''):
    if client is None:
        return False
    saved = getattr(client, '_secret', getattr(client, '_api_key', None))
    return saved == key.strip() and getattr(client, '_insttoken', '') == token.strip()


class PublisherTransport:
    """Bounded, cancellable HTTPS requests with per-provider pacing and no logs."""
    provider_id = ''
    provider_label = ''
    api_host = ''
    domains = ()

    def __init__(self, secret, *, opener=None, minimum_interval=1.0):
        self._secret = _credential(secret, self.provider_label + ' Key / Token', True)
        self._opener = opener or build_opener(NoRedirect())
        self._lock = threading.Lock()
        self._minimum_interval = max(0., float(minimum_interval))
        self._last_started = 0.
        self._blocked_until = 0.
        self._invalid = False

    def __repr__(self):
        return '<' + self.__class__.__name__ + ': credentials hidden, memory only>'

    def _validate_url(self, url, *, credential_query=False):
        try:
            p = urlsplit(url)
            host = (p.hostname or '').lower()
            allowed = any(host == d or host.endswith('.' + d) for d in self.domains)
            if p.scheme != 'https' or not allowed or p.username or p.password or p.port not in (None, 443):
                raise ValueError()
            if self._secret in unquote(url) and not credential_query:
                raise ValueError()
        except ValueError:
            raise APIError('api_response', self.provider_label + ' 返回了尚未接入或不安全的地址，未继续请求。') from None
        try:
            public_url(url)
        except FetchError:
            raise APIError('api_response', self.provider_label + ' 返回的地址不是公开论文站点。') from None
        except OSError:
            raise APIError('api_network', self.provider_label + ' 下载站点解析失败，请检查网络连接。') from None
        return url

    def _headers(self, url, accept):
        headers = {'Accept': accept, 'User-Agent': 'CuZn-LiteratureGateway/0.4 TextDataMining'}
        if self.provider_id == 'wiley' and urlsplit(url).hostname == self.api_host:
            headers['Wiley-TDM-Client-Token'] = self._secret
        return headers

    def _http_error(self, code, headers, api_request):
        label = self.provider_label
        if code == 429:
            delay = _retry_delay(headers)
            self._blocked_until = time.monotonic() + delay
            return APIError('api_rate_limit', label + ' 已限流或配额用尽，暂停该通道；不会连续重试。', code, delay)
        if api_request and (code == 401 or (self.provider_id == 'wiley' and code in (400, 403))):
            self._invalid = True
            return APIError('api_authentication', label + f' 认证未通过（{code}），请检查对应出版社的 Key / Token；本次暂停该凭据。', code)
        if api_request and self.provider_id == 'wiley' and code == 404:
            return APIError('api_permission', 'Wiley 返回 404：可能没有该篇订阅权限、未从机构网络访问，或文章不可用；不能仅据此认定 DOI 不存在。', code)
        if code in (401, 403):
            return APIError('api_permission', label + f' 未允许这次访问（{code}），请核对接口方案、文章权限及下载入口。', code)
        if code == 404:
            return APIError('api_not_found', label + ' 本次入口未找到所请求内容（404）。', code)
        return APIError('api_network' if code >= 500 else 'api_response', label + f' 本次返回 HTTP {code}，尚未取得 PDF。', code)

    def _request(self, url, accept, limit, cancel_event=None, *, api_request=False):
        def cancelled():
            if cancel_event is not None and cancel_event.is_set():
                raise Stopped()
        cancelled()
        while not self._lock.acquire(timeout=.1):
            cancelled()
        try:
            cancelled()
            if self._invalid:
                raise APIError('api_authentication', self.provider_label + ' 本次凭据之前未通过认证，请重新填写后再试。')
            remaining = self._blocked_until - time.monotonic()
            if remaining > 0:
                raise APIError('api_rate_limit', self.provider_label + ' 仍在限流等待期，未发送新请求。', 429, remaining)
            delay = self._minimum_interval - (time.monotonic() - self._last_started)
            while delay > 0:
                cancelled()
                if cancel_event is not None:
                    cancel_event.wait(min(.1, delay))
                else:
                    time.sleep(min(.1, delay))
                delay = self._minimum_interval - (time.monotonic() - self._last_started)
            self._last_started = time.monotonic()
            started = time.monotonic()
            current = url
            quota = {}
            for hop in range(6):
                cancelled()
                if time.monotonic() - started > 45:
                    raise APIError('api_network', self.provider_label + ' 本次请求超时。')
                # Springer requires api_key in its initial HTTPS query. It is
                # never forwarded to an asset or persisted in a result/log.
                self._validate_url(current, credential_query=(hop == 0 and api_request and self.provider_id == 'springer'))
                try:
                    response = self._opener.open(Request(current, headers=self._headers(current, accept)), timeout=15)
                except HTTPError as exc:
                    if exc.code in (301, 302, 303, 307, 308):
                        location = exc.headers.get('Location', '')
                        exc.close()
                        if not location:
                            raise APIError('api_response', '下载重定向缺少地址。') from None
                        current = urljoin(current, location)
                        self._validate_url(current)
                        continue
                    error = self._http_error(exc.code, exc.headers, api_request and urlsplit(current).hostname == self.api_host)
                    exc.close()
                    raise error from None
                except (URLError, TimeoutError, socket.timeout, OSError):
                    raise APIError('api_network', self.provider_label + ' 连接失败或超时；尚不能判断凭据和文章权限。') from None
                with response:
                    quota.update(_quota(response.headers))
                    try:
                        length = int(response.headers.get('Content-Length', '0'))
                    except (TypeError, ValueError):
                        length = 0
                    if length > limit:
                        raise APIError('api_response', '响应超过本次大小上限，未保存。')
                    chunks, size = [], 0
                    while True:
                        cancelled()
                        if time.monotonic() - started > 45:
                            raise APIError('api_network', '下载超时，未保存不完整文件。')
                        try:
                            chunk = response.read(min(128 * 1024, limit + 1 - size))
                        except (OSError, TimeoutError, IncompleteRead):
                            raise APIError('api_network', '连接中断，未保存不完整文件。') from None
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > limit:
                            raise APIError('api_response', '响应超过本次大小上限，未保存。')
                    if length > 0 and size != length:
                        raise APIError('api_network', '下载文件长度不完整，未保存。')
                    return b''.join(chunks), current, quota
            raise APIError('api_response', '下载重定向次数过多，未继续请求。')
        finally:
            self._lock.release()

    def _save(self, data, record, run_dir, source_url, access_basis, cancel_event=None, **extra):
        if not data.lstrip().startswith(b'%PDF-'):
            raise APIError('api_pdf_unavailable', '返回内容不是 PDF，未将网页、XML 或元数据写成全文 PDF。')
        checked = inspect_pdf(data, record)
        if cancel_event is not None and cancel_event.is_set():
            raise Stopped()
        doi = _doi(record.get('doi'))
        folder = Path(run_dir) / (self.provider_id + '_API全文')
        folder.mkdir(parents=True, exist_ok=True)
        filename = hashlib.sha256(doi.encode()).hexdigest()[:18] + '_' + checked['sha256'][:12] + '.pdf'
        target = folder / filename
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != checked['sha256']:
            raise APIError('source_changed', '目标文件已有不同内容，未覆盖。')
        if not target.exists():
            temporary = folder / (filename + '.' + uuid.uuid4().hex[:8] + '.part')
            temporary.write_bytes(data)
            temporary.replace(target)
        return {'status': 'downloaded', 'message': self.provider_label + ' 已取得 PDF，并核对 DOI 与论文身份。',
                'record_id': record.get('id'), 'doi': doi, 'local_path': str(target.resolve()),
                'local_sha256': checked['sha256'], 'source_url': source_url,
                'requested_url': source_url, 'access_basis': access_basis,
                'acquisition_method': access_basis, 'publisher_api_provider': self.provider_id,
                'attempts': [], 'finished_at': now(), **checked, **extra}


class WileyClient(PublisherTransport):
    provider_id = 'wiley'
    provider_label = 'Wiley TDM'
    api_host = 'api.wiley.com'
    domains = ('wiley.com',)

    def __init__(self, token, *, opener=None, minimum_interval=10.1):
        super().__init__(token, opener=opener, minimum_interval=minimum_interval)

    def fetch_pdf(self, record, run_dir, cancel_event=None, progress=None):
        if provider_for(record) != self.provider_id:
            raise APIError('api_not_supported', '请选择 Wiley 论文；此 Token 不能用于其他出版社。')
        doi = _doi(record.get('doi'))
        url = 'https://api.wiley.com/onlinelibrary/tdm/v1/articles/' + quote(doi, safe='')
        if progress:
            progress('Wiley TDM：请求 PDF · ' + doi + '；连续请求至少间隔约 10 秒。')
        data, _, quota = self._request(url, 'application/pdf', MAX_PDF_BYTES, cancel_event, api_request=True)
        return self._save(data, record, run_dir, url, 'wiley_tdm_api', cancel_event, quota=quota)


class SpringerLookupError(APIError):
    """Only allowlisted diagnostic fields are persisted; never response bodies."""
    def __init__(self, status, message, *, stage, key_check='not_determined',
                 article_access='not_determined', http_status=None, retry_after_seconds=None):
        super().__init__(status, message, http_status, retry_after_seconds)
        self.stage = stage
        self.key_check = key_check
        self.article_access = article_access

    def public_details(self):
        return {**super().public_details(), 'lookup_stage': self.stage,
                'key_check': self.key_check, 'article_access': self.article_access}


class SpringerOAClient(PublisherTransport):
    provider_id = 'springer'
    provider_label = 'Springer Nature 开放获取'
    api_host = 'api.springernature.com'
    domains = ('springernature.com', 'springer.com', 'nature.com', 'biomedcentral.com', 'springeropen.com')

    def __init__(self, key, *, opener=None, minimum_interval=.7):
        super().__init__(key, opener=opener, minimum_interval=minimum_interval)

    def _asset_url(self, raw):
        if not isinstance(raw, str):
            raise APIError('api_response', '开放获取记录中的下载地址格式异常。')
        try:
            parsed = urlsplit(raw)
        except ValueError:
            raise APIError('api_response', '开放获取记录中的下载地址格式异常。') from None
        # Official OA records sometimes advertise old http links; use HTTPS.
        url = urlunsplit(('https' if parsed.scheme == 'http' else parsed.scheme, parsed.netloc,
                          parsed.path, parsed.query, ''))
        return self._validate_url(url)

    def _lookup(self, doi, collection, cancel_event=None):
        public_url = 'https://api.springernature.com/' + collection + '/json?' + urlencode({'q': 'doi:' + doi, 'p': 5})
        url = public_url + '&' + urlencode({'api_key': self._secret})
        data, _, quota = self._request(url, 'application/json', MAX_META_BYTES, cancel_event, api_request=True)
        try:
            payload = json.loads(data)
            records = payload['records']
            if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise SpringerLookupError('api_response', '接口未返回可解析的文章列表；尚不能确认文章开放状态，未生成 PDF。',
                                      stage=collection) from None
        # Do not retain the body: some responses echo apiKey/nextPage with keys.
        return records, public_url, quota

    @staticmethod
    def _matching(records, doi):
        matched = [row for row in records if _doi(str(row.get('doi') or row.get('identifier') or '').removeprefix('doi:')) == doi]
        if records and not matched:
            raise SpringerLookupError('identity_unverified', '接口返回的 DOI 与所选论文不一致，未继续下载。', stage='doi_check')
        return matched

    @staticmethod
    def _open_access(row, oa_collection=False):
        values = [str(row[key]).lower().strip() for key in ('openaccess', 'openAccess') if key in row]
        if not values:
            return True if oa_collection else None
        if all(value in ('true', '1') for value in values):
            return True
        if all(value in ('false', '0') for value in values):
            return False
        return None

    def _metadata_after_404(self, doi, cancel_event=None, progress=None):
        if progress:
            progress('开放获取查询返回404；正在核对同一 DOI 的官方元数据与开放状态……')
        try:
            return self._lookup(doi, 'meta/v2', cancel_event)
        except APIError as exc:
            status = exc.status if exc.status in ('api_rate_limit', 'api_authentication', 'api_permission') else 'api_lookup_unresolved'
            raise SpringerLookupError(status,
                '开放获取查询返回404；随后元数据核对也未完成。\n' + str(exc)
                + '\n当前不能据此认定论文不存在、Key有效或学校未购买。可在本窗口检查 Key / 通道，或使用学校全文 / Zotero 接收。',
                stage='metadata_after_oa_404', http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds) from None

    def check_access(self, cancel_event=None, progress=None):
        """Explicit OA-route check, independent of the selected subscription paper."""
        if progress:
            progress('检查 Springer Key 与开放获取查询通道；不下载 PDF，不修改筛选记录……')
        try:
            records, _, _ = self._lookup(SPRINGER_PROBE_DOI, 'openaccess', cancel_event)
            self._matching(records, SPRINGER_PROBE_DOI)
            return {'status': 'api_probe_ok', 'lookup_stage': 'openaccess', 'key_check': 'oa_lookup_responded',
                'pdf_downloaded': False, 'probe_doi': SPRINGER_PROBE_DOI,
                'message': '开放获取查询通道已响应，Key可用于本次查询。\n这不代表所选订阅论文可以下载；本次未下载PDF，也未添加测试论文。'
                    + ('\n测试 DOI 当前未在该开放库返回记录。' if not records else '')}
        except APIError as exc:
            if exc.status != 'api_not_found':
                raise
        records, _, _ = self._metadata_after_404(SPRINGER_PROBE_DOI, cancel_event, progress)
        self._matching(records, SPRINGER_PROBE_DOI)
        return {'status': 'api_metadata_only', 'lookup_stage': 'metadata_after_oa_404',
            'key_check': 'metadata_lookup_responded', 'pdf_downloaded': False, 'probe_doi': SPRINGER_PROBE_DOI,
            'message': '元数据查询接口已响应，Key可用于本次元数据查询；开放获取查询仍返回404。\n'
                '这尚不能验证开放全文通道。请在Springer开发者后台核对该Key启用的接口方案，或查看官方接口说明。\n'
                '本次未下载PDF，也未添加测试论文。'}

    def fetch_pdf(self, record, run_dir, cancel_event=None, progress=None):
        if provider_for(record) != self.provider_id:
            raise APIError('api_not_supported', '请选择 Springer Nature 论文；这个入口仅查询开放获取范围。')
        doi = _doi(record.get('doi'))
        if progress:
            progress('Springer Nature：查询这篇 DOI 的开放获取记录 · ' + doi)
        collection = 'openaccess'
        try:
            records, public_query, quota = self._lookup(doi, collection, cancel_event)
        except APIError as exc:
            if exc.status != 'api_not_found':
                raise
            collection = 'meta/v2'
            records, public_query, quota = self._metadata_after_404(doi, cancel_event, progress)
        key_check = 'oa_lookup_responded' if collection == 'openaccess' else 'metadata_lookup_responded'
        if not records:
            raise SpringerLookupError('api_oa_not_found',
                ('开放获取接口已响应，但开放获取库未找到这篇 DOI。' if collection == 'openaccess'
                 else '开放获取查询返回404；元数据接口已响应，但也未找到这篇 DOI。')
                + '\n这不代表论文不存在，也不说明学校没有购买。可用学校全文 / Zotero 接收，或单独检查 Key / 通道。',
                stage=collection, key_check=key_check)
        matched = self._matching(records, doi)
        flags = [self._open_access(row, collection == 'openaccess') for row in matched]
        if any(flag is False for flag in flags) and any(flag is True for flag in flags):
            raise SpringerLookupError('api_access_unknown', '同一DOI的开放状态记录相互矛盾，未自动下载；请核对论文网页。',
                                      stage=collection, key_check=key_check)
        if not any(flag is True for flag in flags):
            is_subscription = all(flag is False for flag in flags)
            raise SpringerLookupError('api_subscription_required' if is_subscription else 'api_access_unknown',
                ('已找到这篇论文；官方记录标记为非开放获取 / 订阅内容。\n当前“开放获取”通道不能提供这篇订阅全文。'
                 if is_subscription else '已找到这篇论文的元数据，但没有明确的开放获取标记，未将元数据当成全文权限。')
                + '\nKey仅已通过本次' + ('开放获取' if collection == 'openaccess' else '元数据')
                + '查询；这不证明具备订阅全文API权限。\n下一步：学校登录后用Zotero保存全文，本程序自动接收；'
                '批量订阅全文需另向Springer确认Full Text / TDM API授权。',
                stage=collection, key_check=key_check,
                article_access='subscription' if is_subscription else 'unknown')
        pdfs, pages = [], []
        for row, flag in zip(matched, flags):
            if flag is not True:
                continue
            links = row.get('url', [])
            if not isinstance(links, list):
                continue
            for link in links:
                if not isinstance(link, dict):
                    continue
                try:
                    target = self._asset_url(link.get('value', ''))
                except APIError:
                    continue
                if str(link.get('format', '')).lower() == 'pdf':
                    pdfs.append(target)
                elif str(link.get('format', '')).lower() == 'html':
                    pages.append(target)
        last_error = None
        # Only follow links advertised by the exact DOI's OA record/page.
        if not pdfs:
            for page in list(dict.fromkeys(pages))[:2]:
                try:
                    html, final, _ = self._request(page, 'text/html', MAX_META_BYTES, cancel_event)
                    for link in html_pdf_links(html, final):
                        try:
                            pdfs.append(self._asset_url(link))
                        except APIError:
                            pass
                except APIError as exc:
                    if exc.status in ('api_rate_limit', 'api_authentication'):
                        raise
                    last_error = exc
        for candidate in list(dict.fromkeys(pdfs))[:3]:
            try:
                body, _, _ = self._request(candidate, 'application/pdf', MAX_PDF_BYTES, cancel_event)
                # Persist an unsigned source identifier; never persist API keys
                # or temporary signed download queries received from a server.
                p = urlsplit(candidate)
                source = urlunsplit((p.scheme, p.netloc, p.path, '', ''))
                return self._save(body, record, run_dir, source, 'springer_oa_api_pdf_link', cancel_event,
                                  metadata_source_url=public_query, quota=quota, open_access_verified=True,
                                  lookup_stage=collection, key_check=key_check)
            except FetchError as exc:
                if exc.status in ('api_rate_limit', 'api_authentication'):
                    raise
                last_error = exc
        if last_error:
            if last_error.status == 'api_not_found':
                raise SpringerLookupError('api_pdf_unavailable', '已找到开放论文记录，但随后访问的全文页面或PDF链接返回404。\n'
                    '这是下载链接未取得内容，不表示文章不存在；可打开论文网页或使用学校全文 / Zotero 接收。',
                    stage='public_pdf_or_page', key_check=key_check, article_access='open_access', http_status=404) from None
            raise last_error
        raise APIError('api_pdf_unavailable', '已找到开放获取记录，但未取得可用 PDF 链接；记录或 XML 不等于图片模块所需的 PDF。')


FACTORIES = {'elsevier': ElsevierClient, 'springer': SpringerOAClient, 'wiley': WileyClient}
