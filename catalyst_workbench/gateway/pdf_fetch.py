"""Fetch publicly accessible article PDFs and bind verified files to screening IDs.

Public retrieval does not use browser cookies or upload private documents.
Optional publisher clients can retrieve authorized PDFs or resolve OA PDF links.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib, ipaddress, io, json, re, socket, time, unicodedata
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit, urldefrag
from urllib.request import Request, HTTPRedirectHandler, build_opener
from pypdf import PdfReader

MAX_PDF_BYTES=40*1024*1024
MAX_META_BYTES=4*1024*1024
STATUS_LABELS={'available':'已有 PDF','downloaded':'PDF 已获取','not_found':'未找到开放 PDF',
 'access_required':'下载受限','network_error':'网络未完成','identity_unverified':'PDF 身份待核对',
 'source_changed':'原 PDF 已变化','cancelled':'已停止','queued':'等待获取','fetching':'正在获取',
 'missing_doi':'缺少 DOI','api_authentication':'API 认证未通过','api_permission':'API 全文权限待确认',
 'api_rate_limit':'API 限流等待','api_not_found':'API 入口返回404','api_network':'API 连接未完成',
 'api_response':'API 未返回可用 PDF','api_not_supported':'非当前 API 出版社',
 'api_oa_not_found':'开放获取库未收录','api_pdf_unavailable':'API 有响应 / PDF 未取得',
 'api_subscription_required':'订阅全文 / 请用学校权限','api_access_unknown':'开放状态待核对',
 'api_lookup_unresolved':'API 查询原因待核对'}


def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Stopped(Exception):pass
class FetchError(Exception):
    def __init__(self,status,message):super().__init__(message);self.status=status


def selected_records(run):
    return [r for r in run.get('records',[]) if (r.get('manual_decision') or r.get('effective_decision'))=='target']


def status_label(record):
    state=record.get('pdf_acquisition',{}).get('status')
    if state:return STATUS_LABELS.get(state,state)
    path=Path(record.get('local_path') or '')
    return '已有 PDF' if path.suffix.lower()=='.pdf' and path.is_file() else '待自动获取'


def public_url(url,resolve=True):
    p=urlsplit(str(url))
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.port not in (None,80,443):
        raise FetchError('not_found','不是支持的公开 HTTP(S) 下载链接。')
    host=p.hostname.lower()
    if host=='localhost' or host.endswith(('.localhost','.local','.internal')):
        raise FetchError('not_found','链接不是公开论文站点。')
    try:addresses=[ipaddress.ip_address(host)]
    except ValueError:
        addresses=[ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host,p.port or 443,type=socket.SOCK_STREAM)] if resolve else []
    if any(not a.is_global for a in addresses):raise FetchError('not_found','链接未指向公开论文站点。')
    return str(url)


class PublicRedirect(HTTPRedirectHandler):
    max_redirections=6
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        public_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def request_bytes(url,limit=MAX_META_BYTES,cancel_event=None):
    if cancel_event is not None and cancel_event.is_set():raise Stopped()
    public_url(url)
    opener=build_opener(PublicRedirect())
    request=Request(url,headers={'User-Agent':'CuZn-LiteratureGateway/0.2 (public literature research)',
                                 'Accept':'application/pdf,application/json,text/html;q=0.7'})
    started=time.monotonic()
    try:
        with opener.open(request,timeout=15) as response:
            if int(response.headers.get('Content-Length') or 0)>limit:raise FetchError('not_found','响应超过本次大小上限。')
            chunks=[];total=0
            while True:
                if cancel_event is not None and cancel_event.is_set():raise Stopped()
                if time.monotonic()-started>35:raise FetchError('network_error','下载超过单链接时间上限，已停止。')
                data=response.read(min(128*1024,limit+1-total))
                if not data:break
                chunks.append(data);total+=len(data)
                if total>limit:raise FetchError('not_found','响应超过本次大小上限。')
            return b''.join(chunks),response.geturl(),response.headers.get('Content-Type','')
    except HTTPError as exc:
        kind='access_required' if exc.code in (401,403) else 'network_error' if exc.code in (429,500,502,503,504) else 'not_found'
        message=f'HTTP {exc.code}：'+('网站要求授权或限制自动访问。' if kind=='access_required' else '该入口暂不可用。')
        raise FetchError(kind,message) from exc
    except (URLError,TimeoutError,socket.timeout,OSError) as exc:
        raise FetchError('network_error','连接失败或超时：'+str(exc)[:160]) from exc


def _doi(value):
    text=str(value or '').strip().lower()
    text=re.sub(r'^https?://(?:dx\.)?doi\.org/','',text)
    return text if re.fullmatch(r'10\.\d{4,9}/[^\s<>]+',text) else ''


def _compact(text):return re.sub(r'[^a-z0-9]+','',unicodedata.normalize('NFKC',str(text)).lower())


def _labelled_pii(text):
    """Read an explicit serial-article PII label, never a bare similar number.

    Older Elsevier PDFs may print a spaced PII instead of their full DOI.
    Keep each line separate so adjacent dates/page numbers cannot be absorbed.
    """
    normalized=unicodedata.normalize('NFKC',str(text)).lower()
    normalized=normalized.translate(str.maketrans({'‐':'-','‑':'-','–':'-','−':'-'}))
    found=set()
    for line in normalized.splitlines():
        for match in re.finditer(r'\bpii\s*[:：]\s*(s[\d x()\t-]+)',line):
            compact=re.sub(r'[\s()\-]','',match.group(1))
            if re.fullmatch(r's\d{15}[\dx]',compact):found.add(compact)
    return found


def _legacy_elsevier_pii(doi):
    """Only the explicit historical serial-PII DOI format has this fallback."""
    suffix=doi.removeprefix('10.1016/') if doi.startswith('10.1016/') else ''
    if not re.fullmatch(r's\d{4}-?\d{4}\(\d{2}\)\d{5}-?[\dx]',suffix):return ''
    return re.sub(r'[()\-]','',suffix)


def inspect_pdf(data,record):
    if not data.lstrip().startswith(b'%PDF-'):raise FetchError('not_found','返回的是网页或登录提示，没有保存成 PDF。')
    try:
        reader=PdfReader(io.BytesIO(data),strict=False)
        if reader.is_encrypted and not reader.decrypt(''):raise FetchError('access_required','PDF 需要密码。')
        pages=len(reader.pages)
        if not pages:raise FetchError('not_found','PDF 没有页面。')
        first_pages=[p.extract_text() or '' for p in reader.pages[:min(2,pages)]]
        first='\n'.join(first_pages)
        metadata=str(reader.metadata or {})
    except FetchError:raise
    except Exception as exc:raise FetchError('not_found','PDF 结构无法读取。') from exc
    expected=_doi(record.get('doi'))
    compact=re.sub(r'\s+','',first.lower())
    matched=bool(expected and (expected in compact or expected in re.sub(r'\s+','',metadata.lower())))
    words={w for w in re.findall(r'[a-z0-9]{3,}',unicodedata.normalize('NFKC',str(record.get('title',''))).lower()) if w not in {'the','and','for','with','from','into','doi','www','https'}}
    comparable=len(words)>=4 and not str(record.get('title','')).startswith('DOI 查询未完成')
    title_ratio=sum(_compact(w) in _compact(first+' '+metadata) for w in words)/max(1,len(words))
    identity_check='doi_in_first_two_pages_or_metadata_and_title_coverage'
    identifier_type,identifier_value='doi',expected
    if not matched:
        expected_pii=_legacy_elsevier_pii(expected)
        # Require title evidence on the first page for the alternative path.
        # An article citation on a later page must not prove identity.
        pii_title_ratio=sum(_compact(w) in _compact(first_pages[0]+' '+metadata) for w in words)/max(1,len(words))
        detected=_labelled_pii(first_pages[0]) | _labelled_pii(metadata)
        if expected_pii and detected=={expected_pii} and comparable and pii_title_ratio>=.8:
            identity_check='elsevier_legacy_doi_suffix_matches_explicit_first_page_or_metadata_pii_and_title'
            identifier_type,identifier_value='pii',expected_pii.upper()
            title_ratio=pii_title_ratio
        elif expected_pii and detected and detected!={expected_pii}:
            raise FetchError('identity_unverified','PDF 中的 PII 文章编号与本篇 DOI 对应编号不一致或出现冲突，没有绑定附件。')
        elif expected_pii and detected=={expected_pii}:
            raise FetchError('identity_unverified','PII 文章编号一致，但首页题名证据不足，请核对附件是否为正文。')
        else:
            raise FetchError('identity_unverified','PDF 文字中未找到本篇完整 DOI，也没有可交叉核验的历史 PII 文章编号；附件已找到，但尚未确认论文身份。')
    elif comparable and title_ratio<.6:
        raise FetchError('identity_unverified','PDF 中找到了本篇 DOI，但题名不匹配；可能是引用了该论文的其他文件，没有绑定附件。')
    return {'pages':pages,'identity_check':identity_check,
            'identifier_type':identifier_type,'identifier_value':identifier_value,'doi_printed_in_pdf':matched,
            'doi_verified':expected,'title_coverage':title_ratio if comparable else None,
            'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}


class PDFLinks(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.links=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='meta' and (a.get('name') or a.get('property') or '').lower() in ('citation_pdf_url','wkhealth_pdf_url'):
            self.links.append(a.get('content',''))
        if tag=='a':
            href=a.get('href','')
            if re.search(r'\.pdf(?:[?#]|$)|/pdf(?:[/?#]|$)|[?&]pdf=render',href,re.I) and not re.search(r'supp|support|/si/',href,re.I):self.links.append(href)


def html_pdf_links(data,base):
    parser=PDFLinks();parser.feed(data.decode('utf8',errors='replace'))
    return list(dict.fromkeys(urljoin(base,u) for u in parser.links if u))[:5]


def mdpi_asset(url,record):
    p=urlsplit(url)
    if p.hostname not in ('www.mdpi.com','mdpi.com'):return ''
    parts=p.path.strip('/').split('/')
    if len(parts)<4 or not re.fullmatch(r'\d{4}-\d{4}',parts[0]) or not all(v.isdigit() for v in parts[1:4]):return ''
    # These journal routes were verified against downloaded PDF identities.
    # Other publishers use
    # advertised PDF links and OA metadata, not guessed CDN names.
    journal={'2073-4344':'catalysts','1660-4601':'ijerph','2077-0375':'membranes','2071-1050':'sustainability'}.get(parts[0])
    if not journal:return ''
    filename=f'{journal}-{int(parts[1]):02d}-{int(parts[3]):05d}'
    return f'https://mdpi-res.com/d_attachment/{journal}/{filename}/article_deploy/{filename}.pdf'


def existing_file(record,base):
    raw=record.get('local_path')
    if not raw:return None
    path=Path(raw)
    if not path.is_absolute():path=Path(base)/path
    if not path.is_file() or path.suffix.lower()!='.pdf':return None
    data=path.read_bytes();actual=hashlib.sha256(data).hexdigest()
    expected=record.get('local_sha256')
    if expected and expected!=actual:raise FetchError('source_changed','原 PDF 与筛选时的文件不一致，未覆盖旧文件；请核对来源。')
    if not data.lstrip().startswith(b'%PDF-'):raise FetchError('source_changed','原文件已经不是有效 PDF。')
    reader=PdfReader(io.BytesIO(data),strict=False)
    if not len(reader.pages):raise FetchError('source_changed','原 PDF 没有页面。')
    return {'local_path':str(path.resolve()),'local_sha256':actual,'pages':len(reader.pages),'status':'available'}


def fetch_record(record,run_dir,cancel_event=None,progress=None,publisher_client=None):
    attempts=[];seen=set();queue=[];landing=[];expected=_doi(record.get('doi'));start=now()
    def emit(text):
        if progress:progress(text)
    def result(status,message,**extra):
        return {'status':status,'message':message,'record_id':record.get('id'),'doi':expected,
                'started_at':start,'finished_at':now(),'attempts':attempts,**extra}
    try:
        local=existing_file(record,run_dir)
        if local:
            # Preserve the verified origin when a Zotero or public download is
            # reused. A cached file must not lose its evidence on stage handoff.
            previous=record.get('pdf_acquisition',{})
            provenance={}
            if previous.get('local_sha256')==local['local_sha256']:
                provenance={key:previous[key] for key in ('source_url','source_path','access_basis','identity_check',
                    'doi_verified','title_coverage','identifier_type','identifier_value','doi_printed_in_pdf',
                    'acquisition_method','zotero_item_key','zotero_attachment_key','quota',
                    'metadata_source_url','publisher_api_provider','open_access_verified') if key in previous}
            return result('available','已核对本地 PDF，直接交给图片扫描。',**provenance,**{k:v for k,v in local.items() if k!='status'})
    except Exception as exc:return result(getattr(exc,'status','source_changed'),str(exc))
    if not expected:return result('missing_doi','没有 DOI，暂不能自动核对下载文件与论文的关系。')
    api_failure=None
    if publisher_client is not None:
        from elsevier_api import APIError
        from publisher_clients import select_client
        active_client=select_client(publisher_client,record)
        if active_client is not None:
            try:
                info=active_client.fetch_pdf(record,run_dir,cancel_event,progress)
                return result(info['status'],info['message'],**{k:v for k,v in info.items()
                    if k not in ('status','message','record_id','doi','attempts','started_at','finished_at')})
            except Stopped:return result('cancelled','已停止获取，已完成的论文保留。')
            except FetchError as exc:
                api_failure={'status':exc.status,'message':str(exc)}
                if isinstance(exc,APIError):api_failure.update(exc.public_details())
                attempts.append({'provider':getattr(active_client,'provider_label','Publisher API'),**api_failure})
    def add(url,basis,pdf=False):
        if not isinstance(url,str):return
        try:public_url(url,resolve=False)
        except (ValueError,FetchError):return
        url=urldefrag(url)[0]
        asset=mdpi_asset(url,record)
        if asset and asset not in {q[0] for q in queue}:queue.insert(0,(asset,'publisher_public_asset'))
        if pdf or re.search(r'\.pdf(?:[?#]|$)|/pdf(?:[/?#]|$)|[?&]pdf=render',url,re.I):queue.append((url,basis))
        else:landing.append((url,basis))
    for c in record.get('fulltext_candidates',[]):
        if isinstance(c,dict) and 'xml' not in str(c.get('format','')).lower():add(c.get('url',''),c.get('provider','metadata'), 'pdf' in str(c.get('format','')).lower())
    add((record.get('route') or {}).get('official_entry') or record.get('url') or 'https://doi.org/'+expected,'publisher_landing')
    pmcid=(record.get('source_ids') or {}).get('pmcid','')
    if re.fullmatch(r'PMC\d+',str(pmcid)):add('https://europepmc.org/articles/'+pmcid+'?pdf=render','Europe PMC',True)

    def consume(maximum):
        tried=0
        while queue and tried<maximum:
            url,basis=queue.pop(0)
            if url in seen:continue
            seen.add(url);tried+=1
            emit('获取 PDF：'+expected+' · '+str(urlsplit(url).hostname))
            try:
                data,final,ctype=request_bytes(url,MAX_PDF_BYTES,cancel_event)
                if not data.lstrip().startswith(b'%PDF-'):
                    # Some publisher download endpoints return an article page.
                    for link in html_pdf_links(data[:MAX_META_BYTES],final):add(link,'publisher_advertised_pdf',True)
                checked=inspect_pdf(data,record)
                folder=Path(run_dir)/'自动获取PDF';folder.mkdir(exist_ok=True)
                name=hashlib.sha256(expected.encode()).hexdigest()[:18]+'.pdf'
                target=folder/name
                if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=checked['sha256']:
                    target=folder/(Path(name).stem+'_'+checked['sha256'][:8]+'.pdf')
                temporary=target.with_suffix('.part');temporary.write_bytes(data);temporary.replace(target)
                attempts.append({'url':url,'final_url':final,'status':'downloaded','basis':basis})
                return result('downloaded','PDF 已自动获取并通过论文身份核验。',local_path=str(target.resolve()),
                              local_sha256=checked['sha256'],source_url=final,requested_url=url,access_basis=basis,**checked)
            except Stopped:raise
            except Exception as exc:attempts.append({'url':url,'status':getattr(exc,'status','network_error'),'message':str(exc)[:300]})
        return None
    try:
        ready=consume(3)
        if ready:return ready
        # OpenAlex's OA locations can point to public institutional manuscripts
        # even when the publisher's own download requires an account.
        meta_url='https://api.openalex.org/works/https://doi.org/'+quote(expected,safe='/')
        try:
            data,_,_=request_bytes(meta_url,MAX_META_BYTES,cancel_event);meta=json.loads(data)
            for loc in [meta.get('best_oa_location'),*meta.get('locations',[])]:
                if loc and loc.get('is_oa'):
                    if loc.get('pdf_url'):add(loc['pdf_url'],'OpenAlex_OA_location',True)
                    elif loc.get('landing_page_url'):add(loc['landing_page_url'],'OpenAlex_OA_landing')
            attempts.append({'url':meta_url,'status':'metadata_received'})
        except Stopped:raise
        except Exception as exc:attempts.append({'url':meta_url,'status':getattr(exc,'status','network_error'),'message':str(exc)[:200]})
        ready=consume(4)
        if ready:return ready
        # Prefer explicit OA landing pages over DOI resolver pages.
        landing.sort(key=lambda q:0 if q[1]=='OpenAlex_OA_landing' else 1)
        for url,basis in landing[:2]:
            if url in seen:continue
            seen.add(url)
            try:
                data,final,_=request_bytes(url,MAX_META_BYTES,cancel_event)
                if data.lstrip().startswith(b'%PDF-'):
                    # A direct landing redirect may itself be a small PDF.
                    seen.discard(final);add(final,basis,True)
                else:
                    for link in html_pdf_links(data,final):add(link,'publisher_advertised_pdf',True)
                attempts.append({'url':url,'status':'landing_checked','final_url':final})
                ready=consume(3)
                if ready:return ready
            except Stopped:raise
            except Exception as exc:attempts.append({'url':url,'status':getattr(exc,'status','network_error'),'message':str(exc)[:200]})
        kinds={a['status'] for a in attempts}
        if api_failure:
            return result(api_failure['status'],api_failure['message']+' 本次也未取得可核验的开放 PDF。',
                          publisher_api_check=api_failure)
        kind=next((s for s in ('identity_unverified','network_error','access_required') if s in kinds),'not_found')
        messages={'identity_unverified':'取得的文件未通过论文身份核验，没有送入图片扫描。',
                  'access_required':'已检查公开入口；网站限制访问，且未取得可核验的开放副本。',
                  'network_error':'部分入口连接失败或限流，尚未取得 PDF；可以稍后重试。',
                  'not_found':'本次未找到可直接取得且身份一致的开放 PDF。'}
        return result(kind,messages[kind])
    except Stopped:return result('cancelled','已停止获取，已完成的论文保留。')


def acquire_retained(run,progress=None,cancel_event=None,persist=None,publisher_client=None):
    records=selected_records(run)
    def emit(message):
        if progress:progress(message)
    pending={};completed=0;results=[];iterator=iter(records)
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='public-pdf') as pool:
        def schedule():
            if cancel_event is not None and cancel_event.is_set():return False
            try:record=next(iterator)
            except StopIteration:return False
            args=(dict(record),run['run_dir'],cancel_event,progress)
            pending[pool.submit(fetch_record,*args,**({'publisher_client':publisher_client} if publisher_client is not None else {}))]=record
            return True
        schedule();schedule()
        while pending:
            done,_=wait(pending,timeout=.2,return_when=FIRST_COMPLETED)
            for future in done:
                record=pending.pop(future)
                try:info=future.result()
                except Exception as exc:info={'status':'network_error','message':'本篇获取未完成：'+str(exc)[:200],'attempts':[]}
                previous=record.get('pdf_acquisition')
                if previous:record.setdefault('pdf_acquisition_history',[]).append(previous)
                record['pdf_acquisition']=info
                if info['status'] in ('downloaded','available'):
                    record.update(local_path=info['local_path'],local_sha256=info['local_sha256'])
                completed+=1;results.append(info)
                emit(f'PDF 获取进度 {completed}/{len(records)}：{record.get("doi","")} · {STATUS_LABELS.get(info["status"],info["status"])}')
                if persist:persist(run)
                schedule()
    cancelled=bool(cancel_event is not None and cancel_event.is_set())
    summary={'selected':len(records),'completed':completed,'downloaded':sum(r['status']=='downloaded' for r in results),
             'available':sum(r['status']=='available' for r in results),
             'failed':sum(r['status'] not in ('downloaded','available','cancelled') for r in results),
             'cancelled':cancelled,'unprocessed':len(records)-completed,'at':now()}
    summary['ready']=summary['downloaded']+summary['available']
    run['pdf_acquisition_summary']=summary
    root=Path(run['run_dir']);root.mkdir(parents=True,exist_ok=True)
    (root/'PDF获取记录.json').write_text(json.dumps({'summary':summary,'records':results},ensure_ascii=False,indent=2),encoding='utf8')
    if persist:persist(run)
    return run
