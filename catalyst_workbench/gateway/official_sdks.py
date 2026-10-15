"""Small integration layer around publisher-supplied Python clients."""
import importlib
import logging
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from types import ModuleType

def springer_module(name):
    # Official 0.0.10 config logs request params (including api_key) to
    # api_client.log on import. Supply a quiet logging configuration instead.
    config='springernature_api_client.logging_config'
    if config not in sys.modules:
        module=ModuleType(config)
        logger=logging.getLogger('workbench.springer_official')
        logger.disabled=True;logger.propagate=False
        module.logger=logger
        sys.modules[config]=module
    else:
        sys.modules[config].logger.disabled=True
    return importlib.import_module('springernature_api_client.'+name)


def springer_search(api_key,query,*,kind='openaccess',limit=5):
    module=springer_module(kind)
    factory={'openaccess':'OpenAccessAPI','meta':'MetaAPI','tdm':'TDMAPI'}[kind]
    client=getattr(module,factory)(api_key=api_key,max_retries=0)
    if client.BASE_URL != 'https://api.springernature.com/':
        raise ValueError('Springer 官方客户端使用的地址不是当前已验证接口，请更新官方客户端。')
    return client.search(q=query,p=limit,s=1,fetch_all=False,is_premium=False)


def wiley_download(token,doi,directory,*,validate_url,cancel_event=None,use_system_proxy=False,max_bytes=40*1024*1024):
    import requests
    from wiley_tdm import TDMClient
    from pdf_fetch import Stopped
    for name in ('wiley_tdm.tdm_client','wiley_tdm.file_utils','wiley_tdm.ip_utils'):
        logging.getLogger(name).disabled=True
    class Session(requests.Session):
        response_headers={}
        def rebuild_auth(self,prepared,response):
            super().rebuild_auth(prepared,response)
            validate_url(prepared.url)
            if urlsplit(prepared.url).hostname!='api.wiley.com':prepared.headers.pop('Wiley-TDM-Client-Token',None)
        def get(self,url,**kwargs):
            validate_url(url)
            if cancel_event is not None and cancel_event.is_set():raise Stopped()
            response=super().get(url,**kwargs)
            self.response_headers={name:response.headers[name] for name in ('Retry-After','X-RateLimit-Limit','X-RateLimit-Remaining','X-RateLimit-Reset') if name in response.headers}
            original=response.iter_content
            started=time.monotonic()
            def chunks(*args,**options):
                size=0
                for chunk in original(*args,**options):
                    if cancel_event is not None and cancel_event.is_set():raise Stopped()
                    size+=len(chunk)
                    if size>max_bytes:raise requests.RequestException('Wiley PDF exceeded size limit.')
                    if time.monotonic()-started>45:raise requests.RequestException('Wiley PDF exceeded time limit.')
                    yield chunk
            response.iter_content=chunks
            return response
    Path(directory).mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wiley_sdk_',dir=directory) as temporary:
        client=TDMClient(api_token=token,download_dir=temporary)
        session=Session();session.headers.update(client._api_session.headers)
        session.trust_env=bool(use_system_proxy)
        client._api_session.close();client._api_session=session
        try:
            result=client.download_pdf(doi)
            code=int(result.api_status) if result.api_status else None
            data=Path(result.path).read_bytes() if result.path and result.path.is_file() else None
            return {'status':result.status.name,'http_status':code,'data':data,'headers':session.response_headers}
        finally:session.close()
