"""Official Article Retrieval API; credentials stay in process memory.

Reference: https://dev.elsevier.com/documentation/ArticleRetrievalAPI.wadl
This adapter requests PDF/FULL explicitly. Metadata or login pages must never
be mistaken for a full-text PDF. Only a verified PDF reaches the screening row.
"""
from __future__ import annotations
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from http.client import IncompleteRead
import math
from pathlib import Path
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import uuid

from pdf_fetch import MAX_PDF_BYTES, FetchError, Stopped, _doi, inspect_pdf, public_url

REGISTER_URL='https://dev.elsevier.com/apikey/manage'
AUTH_URL='https://dev.elsevier.com/tecdoc_api_authentication.html'
ARTICLE_DOCS='https://dev.elsevier.com/documentation/ArticleRetrievalAPI.wadl'
ACS_TDM_URL='https://solutions.acs.org/solutions/text-and-data-mining/'
API_ORIGIN='https://api.elsevier.com'


class APIError(FetchError):
    def __init__(self,status,message,http_status=None,retry_after_seconds=None):
        super().__init__(status,message)
        self.http_status=http_status;self.retry_after_seconds=retry_after_seconds

    def public_details(self):
        return {'status':self.status,'message':str(self),'http_status':self.http_status,
                'retry_after_seconds':self.retry_after_seconds}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def supports(record):
    doi=_doi(record.get('doi'))
    return bool(doi) and (doi.startswith('10.1016/') or
        (record.get('route') or {}).get('publisher_group')=='Elsevier')


def _credential(value,label,required=False):
    value=str(value or '').strip()
    if required and not value:raise ValueError('请在本机填写 '+label+'。')
    if len(value)>4096 or any(ord(c)<33 or ord(c)>126 for c in value):
        raise ValueError(label+' 中含空格、换行或不支持的字符，请重新复制；不要填写学校密码。')
    return value


def _retry_delay(headers):
    delays=[60.0]
    raw=headers.get('Retry-After','')
    try:delays.append(float(raw))
    except (TypeError,ValueError):
        try:delays.append(parsedate_to_datetime(raw).timestamp()-time.time())
        except (TypeError,ValueError,OverflowError):pass
    try:delays.append(float(headers.get('X-RateLimit-Reset',''))-time.time())
    except (TypeError,ValueError,OverflowError):pass
    return max(d for d in delays if math.isfinite(d) and d>=0)


def _quota(headers):
    result={}
    for key in ('X-RateLimit-Limit','X-RateLimit-Remaining','X-RateLimit-Reset'):
        value=str(headers.get(key,''))
        if value.isdigit() and len(value)<20:result[key]=int(value)
    return result


class ElsevierClient:
    """An explicitly enabled, per-application session. Not serializable config."""
    provider_id='elsevier'
    provider_label='Elsevier official API'
    def __init__(self,api_key,insttoken='',*,opener=None,minimum_interval=1.0):
        self._api_key=_credential(api_key,'Elsevier API Key',True)
        self._insttoken=_credential(insttoken,'Institutional Token')
        self._opener=opener or build_opener(NoRedirect())
        self._lock=threading.Lock();self._last_started=0.0
        self._minimum_interval=max(0.0,float(minimum_interval))
        self._blocked_until=0.0;self._invalid=False

    def __repr__(self):return '<ElsevierClient: credentials hidden, memory only>'

    def _headers(self,url):
        headers={'Accept':'application/pdf','User-Agent':'CuZn-LiteratureGateway/0.3 (academic literature retrieval)'}
        if urlsplit(url).hostname=='api.elsevier.com':
            headers['X-ELS-APIKey']=self._api_key
            if self._insttoken:headers['X-ELS-Insttoken']=self._insttoken
        return headers

    def _validate_redirect(self,url):
        parsed=urlsplit(url);host=(parsed.hostname or '').lower()
        trusted=(host in ('api.elsevier.com','reader.elsevier.com','www.sciencedirect.com','sciencedirect.com')
                 or host.endswith('.els-cdn.com') or host.endswith('.sciencedirectassets.com'))
        if parsed.scheme!='https' or not trusted:
            raise APIError('api_response','API 返回了尚未接入的下载站点；未携带密钥访问，请使用学校全文 / Zotero 接收核对。')
        if self._api_key in url or (self._insttoken and self._insttoken in url):
            raise APIError('api_response','重定向地址异常，已停止；密钥没有通过链接继续发送。')
        public_url(url)

    def _http_error(self,code,headers):
        if code==401:
            self._invalid=True
            return APIError('api_authentication','API 认证未通过（401）。请检查 API Key；本次暂停使用该密钥，重新填写后再试。',code)
        if code==403:
            return APIError('api_permission','API 或这篇全文的权限未通过（403）。学校网页能登录不代表 API 已获授权；请核对密钥权限、学校网络出口或官方机构 Token。',code)
        if code==429:
            delay=_retry_delay(headers);self._blocked_until=time.monotonic()+delay
            return APIError('api_rate_limit','Elsevier API 已限流或配额不足（429），已暂停该通道，不会连续重试。',code,delay)
        if code==404:return APIError('api_not_found','API 未找到这篇文章（404），请核对 DOI 及出版平台。',code)
        if code in (400,406):return APIError('api_response','API 未接受本次 PDF 全文请求，请核对 DOI、文章格式和接口权限。',code)
        return APIError('api_network',f'Elsevier API 本次返回 HTTP {code}，暂未取得 PDF。',code)

    def _request_pdf(self,url,cancel_event=None):
        # Serialize requests for this session, including requests made by the
        # existing two-worker public-download queue.
        with self._lock:
            if cancel_event is not None and cancel_event.is_set():raise Stopped()
            if self._invalid:raise APIError('api_authentication','本次密钥之前未通过认证，请在 API 窗口重新填写后重试。')
            remaining=self._blocked_until-time.monotonic()
            if remaining>0:raise APIError('api_rate_limit','Elsevier API 仍在限流等待期，未发送新请求。',429,remaining)
            delay=self._minimum_interval-(time.monotonic()-self._last_started)
            while delay>0:
                if cancel_event is not None and cancel_event.wait(min(delay,.1)):raise Stopped()
                if cancel_event is None:time.sleep(min(delay,.1))
                delay=self._minimum_interval-(time.monotonic()-self._last_started)
            self._last_started=time.monotonic();started=time.monotonic();quota={}
            current=url
            for hop in range(6):
                if cancel_event is not None and cancel_event.is_set():raise Stopped()
                if time.monotonic()-started>45:raise APIError('api_network','全文下载超时，本次已停止。')
                try:
                    response=self._opener.open(Request(current,headers=self._headers(current)),timeout=15)
                except HTTPError as exc:
                    if exc.code in (301,302,303,307,308):
                        quota.update(_quota(exc.headers))
                        location=exc.headers.get('Location','');exc.close()
                        if not location:raise APIError('api_response','全文重定向缺少下载地址。') from None
                        redirected=urljoin(current,location)
                        self._validate_redirect(redirected);current=redirected;continue
                    error=self._http_error(exc.code,exc.headers);exc.close();raise error from None
                except (URLError,TimeoutError,socket.timeout,OSError):
                    raise APIError('api_network','无法连接 Elsevier API，可能是网络、代理或超时；尚不能据此判断密钥和学校权限。') from None
                with response:
                    quota.update(_quota(response.headers))
                    try:length=int(response.headers.get('Content-Length','0'))
                    except (TypeError,ValueError):length=0
                    if length>MAX_PDF_BYTES:raise APIError('api_response','全文超过本次 40 MB 下载上限。')
                    chunks=[];size=0
                    while True:
                        if cancel_event is not None and cancel_event.is_set():raise Stopped()
                        if time.monotonic()-started>45:raise APIError('api_network','全文下载超时，本次已停止。')
                        try:chunk=response.read(min(128*1024,MAX_PDF_BYTES+1-size))
                        except (OSError,TimeoutError,IncompleteRead):raise APIError('api_network','下载正文时连接中断，未保存不完整 PDF。') from None
                        if not chunk:break
                        chunks.append(chunk);size+=len(chunk)
                        if size>MAX_PDF_BYTES:raise APIError('api_response','全文超过本次 40 MB 下载上限。')
                    if length>0 and size!=length:raise APIError('api_network','收到的文件长度与服务器声明不一致，未保存不完整 PDF。')
                    return b''.join(chunks),quota
            raise APIError('api_response','全文重定向次数过多，未继续请求。')

    def fetch_pdf(self,record,run_dir,cancel_event=None,progress=None):
        if not supports(record):raise APIError('api_not_supported','这篇论文未识别为 Elsevier；当前 API 入口不用于 ACS 或其他出版社。')
        doi=_doi(record.get('doi'))
        url=API_ORIGIN+'/content/article/doi/'+quote(doi,safe='/')+'?view=FULL'
        if progress:progress('Elsevier API：请求所选论文的 PDF 全文 · '+doi)
        data,quota=self._request_pdf(url,cancel_event)
        if not data.lstrip().startswith(b'%PDF-'):
            raise APIError('api_response','接口返回了非 PDF 内容（可能是元数据、XML 或登录提示），没有将其冒充全文 PDF。')
        checked=inspect_pdf(data,record)
        if cancel_event is not None and cancel_event.is_set():raise Stopped()
        folder=Path(run_dir)/'Elsevier_API全文';folder.mkdir(parents=True,exist_ok=True)
        filename=hashlib.sha256(doi.encode()).hexdigest()[:18]+'_'+checked['sha256'][:12]+'.pdf'
        target=folder/filename
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=checked['sha256']:
            raise APIError('source_changed','同名目标文件已有不同内容，未覆盖，请核对结果文件夹。')
        if not target.exists():
            temporary=folder/(filename+'.'+uuid.uuid4().hex[:8]+'.part')
            temporary.write_bytes(data);temporary.replace(target)
        return {'status':'downloaded','message':'已通过 Elsevier 官方 API 取得 PDF，并核对论文身份。',
            'record_id':record.get('id'),'doi':doi,'local_path':str(target.resolve()),'local_sha256':checked['sha256'],
            'source_url':url,'requested_url':url,'access_basis':'elsevier_article_retrieval_api',
            'acquisition_method':'official_elsevier_api','quota':quota,'attempts':[],
            'finished_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),**checked}
