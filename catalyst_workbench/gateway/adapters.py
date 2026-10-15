"""Small, read-only metadata adapters; no full-text downloads.

Crossref: https://www.crossref.org/documentation/retrieve-metadata/rest-api/
Europe PMC: https://europepmc.org/RestfulWebService
OpenAlex: https://help.openalex.org/data/works/
An optional OpenAlex key is used only in the Authorization header.
Only explicit query / DOI / optional contact values are sent to these services.
"""
from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import json
import math
import re
import socket
import time
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, unquote
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler


CROSSREF_BASE = "https://api.crossref.org/works"
EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
OPENALEX_BASE = "https://api.openalex.org/works"
CORE_BASE = 'https://api.core.ac.uk/v3/search/works'
SEMANTIC_BASE = 'https://api.semanticscholar.org/graph/v1/paper/search'
CREDENTIALS = {'OpenAlex': ('api.openalex.org','Authorization','Bearer '),
               'CORE': ('api.core.ac.uk','Authorization','Bearer '),
               'Semantic Scholar': ('api.semanticscholar.org','x-api-key','')}
_PACE_LOCK = threading.Lock()
_LAST_STARTED = {}
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class AdapterError(RuntimeError):
    """A readable error that the caller can log without losing other sources."""
    def __init__(self, message, http_status=None, retry_after_seconds=None):
        super().__init__(message)
        self.http_status=http_status
        self.retry_after_seconds=retry_after_seconds


class _OpenAlexRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed=urlsplit(newurl)
        if parsed.scheme!='https' or parsed.hostname!='api.openalex.org':
            return None
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class _CredentialRedirect(HTTPRedirectHandler):
    def __init__(self,host):super().__init__();self.host=host
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        p=urlsplit(newurl)
        if p.scheme!='https' or p.hostname!=self.host:return None
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def provider_key(value,provider):
    value=str(value or '').strip()
    if value and (len(value)>4096 or any(ord(c)<33 or ord(c)>126 for c in value)):
        raise ValueError(provider+' Key 中含空格、换行或不支持的字符。')
    return value


class _PlainTextParser(HTMLParser):
    BLOCKS = {"p", "title", "sec", "section", "div", "br", "li", "tr", "td", "h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        tag = tag.rsplit(":", 1)[-1].lower()
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in self.BLOCKS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.rsplit(":", 1)[-1].lower()
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in self.BLOCKS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: Any) -> str:
    """Strip HTML/JATS, separate block text, and preserve inline chemical formulae."""
    if value is None:
        return ""
    # Some services escape their markup (e.g. &lt;sub&gt;2&lt;/sub&gt;).
    source = unescape(str(value))
    source = re.sub(r"\s*<(?:[\w-]+:)?(sub|sup)\b[^>]*>\s*", r"<\1>", source, flags=re.I)
    source = re.sub(r"\s*</(?:[\w-]+:)?(sub|sup)\s*>", r"</\1>", source, flags=re.I)
    parser = _PlainTextParser()
    try:
        parser.feed(source)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]*>", " ", source)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def normalize_doi(value: Any) -> str:
    """Accept a DOI or DOI resolver URL without stripping legal DOI suffixes."""
    value = str(value or "").strip()
    if value.lower().startswith(("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")):
        value = unquote(urlsplit(value).path.lstrip("/"))
    value = re.sub(r"^doi\s*:\s*", "", value, flags=re.I).strip()
    return value.lower() if re.fullmatch(r"10\.\d{4,9}/\S+", value, re.I) else ""


def _validate(query: str, limit: int, timeout: float) -> tuple[str, int, float]:
    query = str(query).strip()
    if not query:
        raise ValueError("检索词不能为空。")
    if len(query) > 2000:
        raise ValueError("检索词请控制在 2000 个字符以内。")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("每个来源的条数 limit 必须是 1–100 的整数。")
    timeout = float(timeout)
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("请求超时必须在 0–60 秒之间。")
    return query, limit, timeout


def _contact(contact: str) -> str:
    contact = str(contact or "").strip()
    if contact and (len(contact) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", contact)):
        raise ValueError("联系邮箱格式不正确；也可以留空。")
    return contact


def _get_json(base: str, params: dict[str, Any], provider: str, contact: str, timeout: float, api_key: str = '') -> dict[str, Any]:
    url = base + ("?" + urlencode(params) if params else "")
    agent = "CuZn-LiteratureGateway/0.1 (metadata-only research prototype)"
    if contact:
        agent += f" (mailto:{contact})"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": agent})
    if api_key:
        auth=CREDENTIALS.get(provider)
        if not auth or urlsplit(base).scheme!='https' or urlsplit(base).hostname!=auth[0]:
            raise ValueError(provider+' Key 仅用于该平台官方 HTTPS 接口。')
        request.add_header(auth[1],auth[2]+provider_key(api_key,provider))
    opener=build_opener(_OpenAlexRedirect() if provider=='OpenAlex' else _CredentialRedirect(CREDENTIALS[provider][0])).open if api_key else urlopen
    interval={'CORE':2.1,'Semantic Scholar':1.1}.get(provider,0)
    if interval:
        with _PACE_LOCK:
            delay=interval-(time.monotonic()-_LAST_STARTED.get(provider,0))
            if delay>0:time.sleep(delay)
            _LAST_STARTED[provider]=time.monotonic()
    for attempt in range(3):
        try:
            with opener(request, timeout=timeout) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise AdapterError(f"{provider} 返回内容超过 8 MB，已停止读取；请减小检索条数。")
            data = json.loads(payload.decode("utf-8-sig"))
            if not isinstance(data, dict):
                raise AdapterError(f"{provider} 返回的 JSON 格式与预期不符。")
            return data
        except HTTPError as exc:
            if provider=='OpenAlex' and exc.code in (401,403,429):
                retry=exc.headers.get('Retry-After','') if exc.headers else ''
                try:delay=float(retry)
                except ValueError:delay=None
                hint=('当前网络的匿名共享额度不足或已限流；可填写自己的OpenAlex Key，或稍后重试。'
                      if exc.code==429 and not api_key else
                      '本次OpenAlex Key的额度不足或请求受限；未继续重试。' if exc.code==429 else
                      'OpenAlex授权未通过，请核对OpenAlex Key；不要填Elsevier Key。')
                raise AdapterError(f'OpenAlex HTTP {exc.code}：{hint}',exc.code,delay) from None
            if provider in ('CORE','Semantic Scholar') and exc.code in (401,403,429):
                delay=exc.headers.get('Retry-After','') if exc.headers else ''
                try:delay=float(delay)
                except ValueError:delay=None
                hint='请求限流；可填本平台 Key 或稍后重试，本批暂停该来源。' if exc.code==429 else '访问被拒绝；请核对本平台 Key 和访问方案，本批暂停该来源。'
                raise AdapterError(f'{provider} HTTP {exc.code}：{hint}',exc.code,delay) from None
            if exc.code == 429 and attempt < 2:
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                try:
                    delay = float(retry_after) if retry_after else 2 ** attempt
                except ValueError:
                    delay = 6.0  # HTTP-date: defer rather than retry before its deadline.
                if 0 <= delay <= 5:
                    time.sleep(delay)
                    continue
            hints = {
                400: "检索表达式或参数无效，请检查输入。",
                401: "该入口要求授权，当前公开模式未提供凭据。",
                403: "服务暂时拒绝访问，请稍后重试或使用其他来源。",
                404: "未找到此记录或接口；DOI 可能不由 Crossref 登记。",
                429: "请求频率受限；已停止自动重试，请稍后再试。",
            }
            raise AdapterError(f"{provider} HTTP {exc.code}：{hints.get(exc.code, '服务异常，请稍后重试。')}",exc.code) from None
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise AdapterError(f"{provider} 连接失败或超过 {timeout:g} 秒：请检查网络后重试。") from None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AdapterError(f"{provider} 未返回有效 JSON；可能是服务或网络代理异常。") from exc
    raise AdapterError(f"{provider} 请求失败。")


def _discovery_record(provider,sid,title,abstract,doi_value,year,publisher='',journal='',url='',document_type='',candidates=None,**extra):
    canonical=normalize_doi(doi_value)
    abstract=plain_text(abstract)
    warnings=[]
    if not abstract:warnings.append('来源未提供摘要；不能仅因缺少摘要判定论文无关。')
    if not canonical:warnings.append('来源未提供可规范化的 DOI。')
    if candidates:warnings.append('全文链接是来源报告的候选；尚未实际下载并核对论文身份。')
    return {'doi':canonical,'title':plain_text(title),'abstract':abstract,'year':year,
            'publisher':plain_text(publisher),'journal':plain_text(journal),'url':url or ('https://doi.org/'+canonical if canonical else ''),
            'sources':[provider],'source_ids':{provider:str(sid)},'document_type':plain_text(document_type),
            'fulltext_candidates':candidates or [],'warnings':warnings,
            'abstract_origin':provider+' 来源摘要' if abstract else '',
            'abstract_source':{'provider':provider,'source_id':str(sid),'source_url':url,'method':'reported_abstract'} if abstract else {},**extra}


def _core_record(item):
    sid=str(item.get('id') or '')
    identifiers=item.get('identifiers') or []
    d=item.get('doi') or next((i.get('identifier') for i in identifiers if isinstance(i,dict) and i.get('type')=='doi'),'')
    candidates=[]
    urls=[item.get('downloadUrl'),*(item.get('sourceFulltextUrls') or [])]
    for raw in urls:
        url=_web_url(raw)
        if url and not any(c['url']==url for c in candidates):
            candidates.append({'url':url,'format':'application/pdf' if '.pdf' in urlsplit(url).path.lower() else 'unknown',
                               'provider':'CORE','access_status':'open_access_reported'})
    journals=item.get('journals') or []
    journal=next((j.get('title') or j.get('name','') for j in journals if isinstance(j,dict)),'')
    publisher=item.get('publisher') or ''
    if isinstance(publisher,dict):publisher=publisher.get('name','')
    if isinstance(publisher,list):publisher='; '.join(str(p) for p in publisher)
    row=_discovery_record('CORE',sid,item.get('title'),item.get('abstract'),d,item.get('yearPublished'),publisher,journal,
                             'https://core.ac.uk/works/'+sid,item.get('documentType') or '',candidates,
                             repository_providers=item.get('dataProviders') or [])
    if not item.get('documentType'):row['warnings'].append('CORE未报告文献类型，请核对期刊论文、预印本或学位论文。')
    return row


def search_core(query,limit=20,contact='',timeout=15,api_key=''):
    query,limit,timeout=_validate(query,limit,timeout)
    data=_get_json(CORE_BASE,{'q':query,'limit':limit},'CORE',_contact(contact),timeout,api_key=api_key)
    if not isinstance(data.get('results'),list):raise AdapterError('CORE 响应缺少论文列表。')
    return [_core_record(r) for r in data['results'][:limit] if isinstance(r,dict)]


def _semantic_record(item):
    sid=str(item.get('paperId') or '')
    pdf=item.get('openAccessPdf') or {}
    url=_web_url(pdf.get('url')) if isinstance(pdf,dict) else ''
    candidates=[{'url':url,'format':'application/pdf','provider':'Semantic Scholar','access_status':'open_access_reported'}] if url else []
    journal=item.get('journal') or {}
    return _discovery_record('Semantic Scholar',sid,item.get('title'),item.get('abstract'),
                            (item.get('externalIds') or {}).get('DOI'),item.get('year'),journal=journal.get('name',''),
                            url='https://www.semanticscholar.org/paper/'+sid,
                            document_type='; '.join(item.get('publicationTypes') or []),candidates=candidates)


def search_semantic_scholar(query,limit=20,contact='',timeout=15,api_key=''):
    query,limit,timeout=_validate(query,limit,timeout)
    # Official relevance endpoint does not support hyphenated query terms.
    effective=re.sub(r'(?<=\w)[\-–‑](?=\w)',' ',query)
    fields='title,abstract,externalIds,journal,year,openAccessPdf,publicationTypes'
    data=_get_json(SEMANTIC_BASE,{'query':effective,'limit':limit,'fields':fields},'Semantic Scholar',_contact(contact),timeout,api_key=api_key)
    if not isinstance(data.get('data'),list):raise AdapterError('Semantic Scholar 响应缺少论文列表。')
    return [dict(_semantic_record(r),provider_query=effective) for r in data['data'][:limit] if isinstance(r,dict)]


def _web_url(value: Any) -> str:
    value = str(value or "").strip()
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme.lower() in {"https", "http"} and parsed.hostname else ""
    except ValueError:
        return ""


def _first(value: Any) -> str:
    return plain_text(value[0] if isinstance(value, list) and value else value or "")


def _year(item: dict[str, Any]) -> int | None:
    for name in ("published", "issued", "published-print", "published-online"):
        try:
            return int(item[name]["date-parts"][0][0])
        except (KeyError, TypeError, IndexError, ValueError):
            pass
    return None


def _crossref_record(item: dict[str, Any]) -> dict[str, Any]:
    doi = normalize_doi(item.get("DOI"))
    abstract = plain_text(item.get("abstract"))
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in item.get("link", []) or []:
        if not isinstance(link, dict):
            continue
        url = _web_url(link.get("URL"))
        if url and url not in seen:
            candidates.append({"url": url, "format": str(link.get("content-type") or "unknown"),
                               "access_status": "unknown", "provider": "Crossref"})
            seen.add(url)
    warnings = []
    if not abstract:
        warnings.append("来源未提供摘要；不能仅因缺少摘要判定论文无关。")
    if not doi:
        warnings.append("来源未提供可规范化的 DOI。")
    if candidates:
        warnings.append("全文候选链接的访问权限尚未核实；链接存在不代表开放获取。")
    return {"doi": doi, "title": _first(item.get("title")), "abstract": abstract,
            "year": _year(item), "publisher": plain_text(item.get("publisher")),
            "journal": _first(item.get("container-title")),
            "url": _web_url(item.get("URL")) or (f"https://doi.org/{doi}" if doi else ""),
            "sources": ["Crossref"], "source_ids": {"Crossref": doi},
            "document_type": plain_text(item.get("type")),
            "fulltext_candidates": candidates, "warnings": warnings,
            "abstract_origin": "Crossref登记摘要" if abstract else "",
            "abstract_source": {"provider":"Crossref","source_id":doi,
                                "source_url":CROSSREF_BASE+'/'+quote(doi,safe='')} if abstract else {}}


def search_crossref(query: str, limit: int = 20, contact: str = "", timeout: float = 15) -> list[dict[str, Any]]:
    """Search public Crossref metadata; its relevance search is NOT a Boolean filter."""
    query, limit, timeout = _validate(query, limit, timeout)
    contact = _contact(contact)
    # The bounded prototype fits in one page (Crossref permits up to 1000 rows).
    # Use explicit relevance ranking; do not mix cursor pagination with a
    # small, relevance-ranked search after Crossref's August 2026 changes.
    params: dict[str, Any] = {"query": query, "rows": limit, "sort": "score", "order": "desc", "filter": "type:journal-article",
        "select": "DOI,title,abstract,published,publisher,container-title,type,URL,link"}
    if contact:
        params["mailto"] = contact
    response = _get_json(CROSSREF_BASE, params, "Crossref", contact, timeout)
    message = response.get("message", {})
    items = message.get("items") if isinstance(message, dict) else None
    if not isinstance(items, list):
        raise AdapterError("Crossref 响应缺少文献列表；请稍后重试。")
    return [_crossref_record(item) for item in items if isinstance(item, dict)][:limit]


def lookup_doi(doi: str, contact: str = "", timeout: float = 15) -> dict[str, Any]:
    """Look up one DOI in Crossref. Non-Crossref DOIs may legitimately be absent."""
    normalized = normalize_doi(doi)
    if not normalized:
        raise ValueError("请输入完整 DOI 或 doi.org 链接；DOI 应以 10. 开头，并包含斜杠。")
    _, _, timeout = _validate(normalized, 1, timeout)
    contact = _contact(contact)
    params = {"mailto": contact} if contact else {}
    response = _get_json(CROSSREF_BASE + "/" + quote(normalized, safe=""), params, "Crossref", contact, timeout)
    message = response.get("message")
    if not isinstance(message, dict):
        raise AdapterError("Crossref 未返回 DOI 元数据对象。")
    return _crossref_record(message)


def _europe_pmc_record(item: dict[str, Any]) -> dict[str, Any]:
    doi = normalize_doi(item.get("doi"))
    source, source_id = str(item.get("source") or ""), str(item.get("id") or "")
    pmcid = str(item.get("pmcid") or "").strip()
    if not re.fullmatch(r"PMC\d+", pmcid):
        pmcid = ""
    source_ids = {"Europe PMC": f"{source}:{source_id}"}
    if pmcid:
        source_ids["pmcid"] = pmcid
    abstract = plain_text(item.get("abstractText"))
    candidates = []
    seen = set()
    for link in (item.get("fullTextUrlList") or {}).get("fullTextUrl", []) or []:
        if not isinstance(link, dict):
            continue
        url = _web_url(link.get("url"))
        if not url or url in seen:
            continue
        # Europe PMC's per-link availabilityCode says free/subscription access,
        # not a redistribution licence. Preserve it conservatively.
        availability = str(link.get("availabilityCode") or "").upper()
        access = "free_to_read_reported" if availability == "OA" else ("subscription_reported" if availability == "S" else "unknown")
        candidates.append({"url": url, "format": str(link.get("documentStyle") or "unknown"),
                           "access_status": access, "provider": str(link.get("site") or "Europe PMC")})
        seen.add(url)
    if pmcid and item.get("isOpenAccess") == "Y":
        # Only a provider-reported OA article with a strictly validated PMCID
        # gets this official XML endpoint. The caller must still validate the
        # downloaded article identity and respect its reuse licence.
        xml_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
        candidates = [candidate for candidate in candidates if candidate["url"] != xml_url]
        candidates.append({"url": xml_url, "format": "application/xml",
                           "access_status": "open_access_reported", "provider": "Europe PMC"})
    warnings = []
    if not abstract:
        warnings.append("来源未提供摘要；不能仅因缺少摘要判定论文无关。")
    if not doi:
        warnings.append("来源未提供可规范化的 DOI。")
    if candidates:
        warnings.append("全文访问状态来自元数据，尚未实际验证；免费阅读不等于可任意再分发。")
    try:
        year = int(item.get("pubYear", ""))
    except (TypeError, ValueError):
        year = None
    journal_info = item.get("journalInfo") or {}
    journal = journal_info.get("journal") or {}
    pub_types = (item.get("pubTypeList") or {}).get("pubType", []) or []
    return {"doi": doi, "title": plain_text(item.get("title")), "abstract": abstract,
            "year": year, "publisher": plain_text(item.get("publisher")),
            "journal": plain_text(journal.get("title")),
            "url": f"https://europepmc.org/article/{quote(source, safe='')}/{quote(source_id, safe='')}" if source and source_id else (f"https://doi.org/{doi}" if doi else ""),
            "sources": ["Europe PMC"], "source_ids": source_ids,
            "document_type": "; ".join(plain_text(value) for value in pub_types),
            "fulltext_candidates": candidates, "warnings": warnings,
            "abstract_origin": "Europe PMC摘要" if abstract else "",
            "abstract_source": {"provider":"Europe PMC","source_id":f'{source}:{source_id}',
                                "source_url":f'https://europepmc.org/article/{quote(source,safe="")}/{quote(source_id,safe="")}'} if abstract else {}}


def search_europe_pmc(query: str, limit: int = 20, contact: str = "", timeout: float = 15) -> list[dict[str, Any]]:
    """Search Europe PMC (primarily life sciences) using its documented syntax."""
    query, limit, timeout = _validate(query, limit, timeout)
    contact = _contact(contact)
    size = min(limit, 50)
    params: dict[str, Any] = {"query": query, "format": "json", "resultType": "core",
                              "pageSize": size, "cursorMark": "*"}
    if contact:
        params["email"] = contact
    records = []
    for _ in range(math.ceil(limit / size)):
        response = _get_json(EUROPE_PMC_BASE, params, "Europe PMC", contact, timeout)
        items = (response.get("resultList") or {}).get("result")
        if not isinstance(items, list):
            raise AdapterError("Europe PMC 响应缺少文献列表；请检查检索表达式后重试。")
        records.extend(_europe_pmc_record(item) for item in items if isinstance(item, dict))
        cursor = response.get("nextCursorMark")
        if len(items) < size or not cursor or cursor == params["cursorMark"] or len(records) >= limit:
            break
        params["cursorMark"] = cursor
    return records[:limit]


def _openalex_key(value):
    value=str(value or '').strip()
    if len(value)>4096 or any(ord(c)<33 or ord(c)>126 for c in value):
        raise ValueError('OpenAlex Key包含空格或不支持的字符，请重新复制。')
    return value


def decode_openalex_abstract(index):
    """Reconstruct a complete positional index; collisions/gaps are not repaired."""
    if not index:
        return ''
    if not isinstance(index,dict) or len(index)>50000:
        raise ValueError('摘要倒排索引格式无效。')
    positions={}
    for word, offsets in index.items():
        if not isinstance(word,str) or not word.strip() or not isinstance(offsets,list) or not offsets:
            raise ValueError('摘要词或位置列表无效。')
        for offset in offsets:
            if type(offset) is not int or not 0<=offset<50000:
                raise ValueError('摘要位置不是有效整数。')
            if offset in positions and positions[offset]!=word:
                raise ValueError('摘要位置冲突，未拼接不确定的摘要。')
            positions[offset]=word
    if not positions or len(positions)!=max(positions)+1:
        raise ValueError('摘要位置有缺口，未将不完整内容当成完整摘要。')
    return ' '.join(positions[i] for i in range(len(positions)))


def _openalex_record(item):
    doi=normalize_doi(item.get('doi'))
    work_id=str(item.get('id') or '')
    warnings=[]
    try:abstract=decode_openalex_abstract(item.get('abstract_inverted_index'))
    except ValueError as exc:
        abstract='';warnings.append('OpenAlex '+str(exc))
    if not abstract:warnings.append('来源未提供有效摘要；需要补充原文后复核。')
    if not doi:warnings.append('OpenAlex未提供可规范化的DOI。')
    primary=item.get('primary_location') or {}
    if not isinstance(primary,dict):primary={}
    source=primary.get('source') or {}
    if not isinstance(source,dict):source={}
    locations=[item.get('best_oa_location'),primary]+(item.get('locations') or [])
    candidates=[];seen=set()
    for loc in locations:
        if not isinstance(loc,dict) or loc.get('is_oa') is not True:continue
        for field,format_ in (('pdf_url','application/pdf'),('landing_page_url','text/html')):
            url=_web_url(loc.get(field))
            if not url or url in seen:continue
            seen.add(url)
            candidates.append({'url':url,'format':format_,'provider':'OpenAlex',
                               'access_status':'open_access_reported'})
    if candidates:warnings.append('开放获取状态由OpenAlex报告；尚未实际下载验证，也不代表可任意再分发。')
    try:year=int(item.get('publication_year'))
    except (ValueError,TypeError):year=None
    return {'doi':doi,'title':plain_text(item.get('title') or item.get('display_name')),'abstract':abstract,
            'year':year,'publisher':plain_text(source.get('host_organization_name')),
            'journal':plain_text(source.get('display_name')),
            'url':_web_url(primary.get('landing_page_url')) or ('https://doi.org/'+doi if doi else _web_url(work_id)),
            'sources':['OpenAlex'],'source_ids':{'OpenAlex':work_id},'document_type':plain_text(item.get('type')),
            'fulltext_candidates':candidates,'warnings':warnings,
            'abstract_origin':'OpenAlex倒排索引还原摘要' if abstract else '',
            'abstract_source':{'provider':'OpenAlex','source_id':work_id,'source_url':_web_url(work_id),
                               'method':'abstract_inverted_index'} if abstract else {}}


def search_openalex(query, limit=20, contact='', timeout=15, api_key=''):
    """Search title/abstract metadata, keeping provider-reported OA locations."""
    query,limit,timeout=_validate(query,limit,timeout)
    contact=_contact(contact);api_key=_openalex_key(api_key)
    params={'search':query,'per-page':limit,
            'select':'id,doi,title,publication_year,abstract_inverted_index,primary_location,best_oa_location,locations,type'}
    if contact:params['mailto']=contact
    data=_get_json(OPENALEX_BASE,params,'OpenAlex',contact,timeout,api_key=api_key)
    items=data.get('results')
    if not isinstance(items,list):raise AdapterError('OpenAlex响应缺少论文列表。')
    return [_openalex_record(v) for v in items if isinstance(v,dict)][:limit]


def lookup_openalex_doi(doi, contact='', timeout=15, api_key=''):
    expected=normalize_doi(doi)
    if not expected:raise ValueError('请输入完整DOI或doi.org链接。')
    _,_,timeout=_validate(expected,1,timeout)
    contact=_contact(contact);api_key=_openalex_key(api_key)
    data=_get_json(OPENALEX_BASE+'/'+quote('https://doi.org/'+expected,safe=':/'),
                   {'mailto':contact} if contact else {},'OpenAlex',contact,timeout,api_key=api_key)
    record=_openalex_record(data)
    if record['doi']!=expected:raise AdapterError('OpenAlex返回的DOI与请求不一致，未合并摘要。')
    return record
