"""Small, read-only public metadata adapters; no credentials or full-text downloads.

Crossref: https://www.crossref.org/documentation/retrieve-metadata/rest-api/
Europe PMC: https://europepmc.org/RestfulWebService
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
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, unquote
from urllib.request import Request, urlopen


CROSSREF_BASE = "https://api.crossref.org/works"
EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class AdapterError(RuntimeError):
    """A readable error that the caller can log without losing other sources."""


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


def _get_json(base: str, params: dict[str, Any], provider: str, contact: str, timeout: float) -> dict[str, Any]:
    url = base + ("?" + urlencode(params) if params else "")
    agent = "CuZn-LiteratureGateway/0.1 (metadata-only research prototype)"
    if contact:
        agent += f" (mailto:{contact})"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": agent})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise AdapterError(f"{provider} 返回内容超过 8 MB，已停止读取；请减小检索条数。")
            data = json.loads(payload.decode("utf-8-sig"))
            if not isinstance(data, dict):
                raise AdapterError(f"{provider} 返回的 JSON 格式与预期不符。")
            return data
        except HTTPError as exc:
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
            raise AdapterError(f"{provider} HTTP {exc.code}：{hints.get(exc.code, '服务异常，请稍后重试。')}") from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise AdapterError(f"{provider} 连接失败或超过 {timeout:g} 秒：请检查网络后重试。") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AdapterError(f"{provider} 未返回有效 JSON；可能是服务或网络代理异常。") from exc
    raise AdapterError(f"{provider} 请求失败。")


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
            "fulltext_candidates": candidates, "warnings": warnings}


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
            "fulltext_candidates": candidates, "warnings": warnings}


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
