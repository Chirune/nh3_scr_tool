"""Read local Zotero attachments for retained papers; never log in or download online.

Only GET requests to the fixed loopback API are issued. Zotero's database,
attachments and preferences are never modified. Copies are verified before
being bound to a screening record and passed to the figure intake.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener, url2pathname
import uuid

from engine import canonical_doi
from pdf_fetch import MAX_PDF_BYTES, FetchError, Stopped, existing_file, inspect_pdf, now, selected_records

BASE = 'http://127.0.0.1:23119/api/'
SETUP = '请打开 Zotero 桌面版，在“设置 → 高级”勾选“允许此计算机上的其他应用程序与 Zotero 通信”。'
LABELS = {'received': 'Zotero 已接收', 'available': '已有 PDF', 'not_saved': '等待 Zotero 保存',
          'no_pdf': 'Zotero 只有条目', 'file_missing': '附件未在本机',
          'identity_unverified': 'PDF 身份待核对', 'ambiguous': '多个 PDF 待核对',
          'source_changed': '原 PDF 已变化', 'missing_doi': '缺少 DOI', 'supplement_only': '仅有补充材料',
          'cancelled': '已停止', 'error': '接收未完成'}


class BridgeError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _key(value):
    value = str(value or '')
    if not re.fullmatch(r'[A-Z0-9]{8}', value):
        raise BridgeError('Zotero 返回的条目编号无效，未读取附件。')
    return value


def local_file_url(value):
    parsed = urlsplit(str(value).strip())
    if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost') or parsed.query or parsed.fragment:
        raise BridgeError('附件不是本机文件；请先在 Zotero 中打开附件，使文件下载到本机。')
    raw = url2pathname(parsed.path)
    if '\x00' in raw or raw.startswith(('\\\\', '//')):
        raise BridgeError('附件指向网络共享或无效路径，未接收。')
    path = Path(raw)
    if not path.is_absolute():
        raise BridgeError('附件路径不是完整的本机路径。')
    path = path.resolve()
    if str(path).startswith(('\\\\', '//')) or path.suffix.lower() != '.pdf':
        raise BridgeError('附件不是本机 PDF。')
    return path


class LocalZotero:
    def __init__(self):
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, params=None):
        if path.startswith('/') or '..' in path or '?' in path or '://' in path:
            raise BridgeError('无效的本地接口路径。')
        url = BASE + path + ('?' + urlencode(params) if params else '')
        req = Request(url, headers={'Zotero-API-Version': '3', 'Accept': 'application/json'})
        try:
            with self.opener.open(req, timeout=5) as response:
                body = response.read(4 * 1024 * 1024 + 1)
                if len(body) > 4 * 1024 * 1024:
                    raise BridgeError('本地接口响应过大，请缩小待接收论文范围。')
                return body, dict(response.headers)
        except HTTPError as exc:
            if exc.code == 403:
                raise BridgeError('Zotero 尚未允许本机程序读取。' + SETUP) from exc
            if exc.code in (404, 501):
                raise BridgeError('Zotero 本地接口或附件不可用，请检查桌面软件版本及附件是否已下载。') from exc
            raise BridgeError(f'Zotero 本地接口返回 HTTP {exc.code}；未访问出版社。') from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise BridgeError('尚未连接 Zotero。' + SETUP) from exc

    def check(self):
        body, headers = self.request('users/0/items/top', {'format': 'keys', 'limit': 1})
        api_version = next((v for k, v in headers.items() if k.lower() == 'zotero-api-version'), '')
        if api_version != '3':
            raise BridgeError('本机端口没有返回预期的 Zotero 接口，未读取文库。')
        return {'connected': True, 'api_version': api_version,
                'message': '已连接本机 Zotero。学校登录和 PDF 保存仍在浏览器中完成。'}

    def items(self, path, params=None, cancel_event=None):
        params = dict(params or {})
        output, seen = [], set()
        for start in range(0, 5000, 100):
            if cancel_event is not None and cancel_event.is_set():
                raise Stopped()
            body, _ = self.request(path, {**params, 'format': 'json', 'limit': 100, 'start': start})
            try:
                rows = json.loads(body)
            except (ValueError, UnicodeError) as exc:
                raise BridgeError('Zotero 返回的文献信息无法读取。') from exc
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise BridgeError('Zotero 返回了不支持的文献信息格式。')
            for row in rows:
                key = _key(row.get('key'))
                if key in seen:
                    raise BridgeError('Zotero 分页返回了重复内容，本轮停止，请重试。')
                seen.add(key)
                output.append(row)
            if len(rows) < 100:
                return output
        raise BridgeError('Zotero 匹配结果过多，本轮停止；未把未检查文件交给图片识别。')

    def find_papers(self, doi, cancel_event=None):
        # Quicksearch can include references and standalone attachments. Exact
        # parent DOI filtering is mandatory, even when the query matches.
        rows = self.items('users/0/items/top', {'q': doi, 'qmode': 'everything'}, cancel_event)
        return [row for row in rows if isinstance(row.get('data'), dict)
                and row['data'].get('itemType') not in ('attachment', 'note', 'annotation')
                and canonical_doi(row['data'].get('DOI')) == doi]

    def attachments(self, item_key, cancel_event=None):
        rows = self.items(f'users/0/items/{_key(item_key)}/children', cancel_event=cancel_event)
        return [row for row in rows if row.get('data', {}).get('itemType') == 'attachment'
                and row['data'].get('contentType', '').split(';')[0] == 'application/pdf']

    def attachment_path(self, item_key):
        body, _ = self.request(f'users/0/items/{_key(item_key)}/file/view/url')
        return local_file_url(body.decode('utf-8').strip())


def _supplement(attachment):
    data = attachment.get('data', {})
    text = str(data.get('title', '')) + ' ' + str(data.get('filename', ''))
    return bool(re.search(r'supporting\s+information|supplement(?:ary|al)|补充材料|(?:^|[_. -])(?:si|supp)(?:[_. -]|$)', text, re.I))


def receive_record(record, run_dir, client, cancel_event=None):
    doi = canonical_doi(record.get('doi'))
    def result(status, message, **extra):
        return {'status': status, 'label': LABELS.get(status, status), 'message': message,
                'doi': doi, 'record_id': record.get('id'), 'checked_at': now(), **extra}
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise Stopped()
        local = existing_file(record, run_dir)
        if local:
            return result('available', '已有 PDF，通过文件一致性检查，无需重新接收。')
        if record.get('local_path'):
            return result('source_changed', '原绑定文件已丢失；请核对旧文件，本轮未自动替换。')
        if not doi:
            return result('missing_doi', '请补全论文 DOI 后再匹配 Zotero，未按标题猜测附件。')
        parents = client.find_papers(doi, cancel_event)
        if not parents:
            return result('not_saved', '在浏览器论文页面点 Zotero 保存；请确认条目 DOI 与本篇一致，并保存在“我的文库”。')
        candidates, seen, failures = [], set(), []
        attachment_count = supplements = 0
        for parent in parents:
            for attachment in client.attachments(parent['key'], cancel_event):
                attachment_count += 1
                if _supplement(attachment):
                    supplements += 1
                    continue
                if attachment['key'] in seen:
                    continue
                seen.add(attachment['key'])
                try:
                    path = client.attachment_path(attachment['key'])
                    if not path.is_file():
                        raise FileNotFoundError()
                    before = path.stat()
                    if before.st_size > MAX_PDF_BYTES:
                        raise BridgeError('附件超过当前 40 MB 接收上限。')
                    with path.open('rb') as stream:
                        data = stream.read(MAX_PDF_BYTES + 1)
                    after = path.stat()
                    if len(data) > MAX_PDF_BYTES or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise BridgeError('附件仍在保存或大小变化，下轮再检查。')
                    checked = inspect_pdf(data, record)
                    candidates.append((parent['key'], attachment['key'], path, data, checked))
                except FetchError as exc:
                    failures.append(('identity_unverified', str(exc)))
                except (BridgeError, OSError) as exc:
                    failures.append(('file_missing', str(exc) if isinstance(exc, BridgeError) else '附件未下载到本机，或暂时无法读取。请在 Zotero 中打开该 PDF。'))
        unique = {candidate[4]['sha256']: candidate for candidate in candidates}
        if len(unique) > 1:
            return result('ambiguous', '有多个不同的 PDF 同时通过身份检查，请在 Zotero 中核对正文版本后再接收。',
                          candidate_attachment_keys=[c[1] for c in unique.values()])
        if not unique:
            if failures:
                status = 'identity_unverified' if any(x[0] == 'identity_unverified' for x in failures) else 'file_missing'
                return result(status, '；'.join(dict.fromkeys(x[1] for x in failures))[:600])
            if supplements:
                return result('supplement_only', '仅检测到补充材料，请保存论文正文 PDF。补充材料暂不作为正文自动接收。')
            return result('no_pdf', '已找到 DOI 一致的条目，但没有 PDF 附件；请在有全文权限的网页上用 Zotero 保存正文。')
        if cancel_event is not None and cancel_event.is_set():
            raise Stopped()
        parent_key, attachment_key, original, data, checked = next(iter(unique.values()))
        folder = Path(run_dir).resolve() / 'Zotero接收PDF'
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (hashlib.sha256(doi.encode()).hexdigest()[:18] + '_' + checked['sha256'][:12] + '.pdf')
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != checked['sha256']:
                return result('source_changed', '已有同名接收文件内容变化，未覆盖。请核对文件。')
        else:
            temporary = folder / (uuid.uuid4().hex + '.part')
            with temporary.open('xb') as stream:
                stream.write(data)
            temporary.replace(target)
        verified_message=('已从本机 Zotero 接收正文 PDF，通过 PII 文章编号与首页题名交叉核验（此 PDF 未印完整 DOI），可进入第二板块。'
                          if checked.get('identifier_type')=='pii' else
                          '已从本机 Zotero 接收正文 PDF，通过 DOI 与题名检查，可进入第二板块。')
        return result('received', verified_message,
                      local_path=str(target), local_sha256=checked['sha256'],
                      source_path=str(original), source_url='https://doi.org/' + doi,
                      zotero_item_key=parent_key, zotero_attachment_key=attachment_key,
                      access_basis='local_zotero_attachment_not_an_open_access_claim', **checked)
    except Stopped:
        return result('cancelled', '已停止接收，已取得文件保留。')
    except FetchError as exc:
        return result(getattr(exc, 'status', 'error'), str(exc))


def sync_run(run, client=None, cancel_event=None, progress=None, persist=None):
    client = client or LocalZotero()
    client.check()
    records = selected_records(run)
    results = []
    for record in records:
        if cancel_event is not None and cancel_event.is_set():
            break
        info = receive_record(record, run['run_dir'], client, cancel_event)
        results.append(info)
        if info['status'] == 'available':
            continue
        record['zotero_transfer'] = info
        if info['status'] == 'received':
            previous = record.get('pdf_acquisition')
            if previous:
                record.setdefault('pdf_acquisition_history', []).append(previous)
            record['pdf_acquisition'] = {**info, 'status': 'available', 'acquisition_method': 'zotero_local'}
            record.update(local_path=info['local_path'], local_sha256=info['local_sha256'])
            if persist:
                persist(run)
        if progress:
            progress(f'Zotero 接收 {len(results)}/{len(records)}：{record.get("doi", "")} · {info["label"]}')
    summary = {'selected': len(records), 'checked': len(results),
               'received': sum(x['status'] == 'received' for x in results),
               'ready': sum(x['status'] in ('received', 'available') for x in results),
               'waiting': sum(x['status'] not in ('received', 'available') for x in results),
               'cancelled': bool(cancel_event is not None and cancel_event.is_set()), 'at': now()}
    run['zotero_sync_summary'] = summary
    root = Path(run['run_dir'])
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / 'Zotero接收记录.json.tmp'
    temporary.write_text(json.dumps({'summary': summary, 'records': results}, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(root / 'Zotero接收记录.json')
    if persist:
        persist(run)
    return run
