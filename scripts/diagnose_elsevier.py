"""Small live permission/format/network comparison. No credentials persisted."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote
import xml.etree.ElementTree as ET
import requests

ORIGIN='https://api.elsevier.com'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('--key-stdin',action='store_true')
    parser.add_argument('--supplement',action='store_true',help='Append two metadata/authentication checks; do not repeat the main matrix.')
    args=parser.parse_args()
    print('Waiting for API key (not saved).',flush=True)
    key=(sys.stdin.readline() if args.key_stdin else getpass.getpass('Elsevier key: ')).strip()
    if not key or any(ord(c)<33 or ord(c)>126 for c in key):
        raise SystemExit('Missing or invalid credential input.')
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    report={'tested_at':datetime.now(timezone.utc).isoformat(),'institution_token_supplied':False,
            'credential_saved':False,'cases':[],'articles':[],
            'scope':'Live bounded official API tests; no account or proxy settings changed.'}
    parsed={}
    if args.supplement:
        report=json.loads((output/'diagnostic.json').read_text(encoding='utf-8'))
        report['supplement_tested_at']=datetime.now(timezone.utc).isoformat()
    def call(mode,name,path,accept='application/json',params=None,payload=None):
        session=requests.Session();session.trust_env=(mode=='system_proxy')
        proxy_active=bool(session.merge_environment_settings(ORIGIN+path,{},None,None,None)['proxies'])
        row={'network_mode':mode,'proxy_configured_for_request':proxy_active,'test':name,
             'path':path,'method':'PUT' if payload is not None else 'GET','accept':accept,'params':params or {}}
        start=time.monotonic()
        try:
            response=session.request(row['method'],ORIGIN+path,headers={'X-ELS-APIKey':key,'Accept':accept,
                'User-Agent':'NH3SCR-API-Diagnostic/1.0'},params=params,json=payload,
                timeout=(8,20),allow_redirects=False)
            row.update(http_status=response.status_code,content_type=response.headers.get('Content-Type',''),
                       response_bytes=len(response.content),request_id=response.headers.get('X-ELS-ReqId',''),
                       quota={k:response.headers[k] for k in ('X-RateLimit-Limit','X-RateLimit-Remaining','X-RateLimit-Reset') if k in response.headers})
            data=None
            try:data=response.json()
            except ValueError:pass
            if isinstance(data,dict):
                parsed[(mode,name)]=data
                err=data.get('service-error',{}).get('status',{})
                other=data.get('error-response',{})
                if err:row.update(error_code=err.get('statusCode',''),error_message=err.get('statusText',''))
                if other:row.update(error_code=other.get('error-code',other.get('error','')),error_message=other.get('error-message',other.get('message','')))
                search=data.get('search-results',{})
                if search:row['total_results']=search.get('opensearch:totalResults');row['entries_received']=len(search.get('entry',[]))
                row['abstract_characters']=len(str(data.get('full-text-retrieval-response',{}).get('coredata',{}).get('dc:description') or ''))
            row['pdf_signature_valid']=response.content.startswith(b'%PDF-')
            if 200<=response.status_code<300 and row['pdf_signature_valid']:
                # Inspect without saving an article or exposing response content.
                from io import BytesIO
                from pypdf import PdfReader
                try:row['pdf_pages']=len(PdfReader(BytesIO(response.content)).pages)
                except Exception:row['pdf_parse_failed']=True
            if 'xml' in row['content_type'] or response.content.lstrip().startswith(b'<?xml'):
                try:
                    tree=ET.fromstring(response.content);tags=[e.tag.split('}')[-1] for e in tree.iter()]
                    row['xml_root']=tree.tag.split('}')[-1]
                    row['xml_paragraphs']=tags.count('para');row['xml_sections']=tags.count('section')
                    row['xml_fulltext_present']=bool(tags.count('originalText') and (tags.count('para') or tags.count('doc')))
                    for e in tree.iter():
                        local=e.tag.split('}')[-1]
                        if local in ('statusCode','error-code'):row['error_code']=(e.text or '')[:120]
                        if local in ('statusText','error-message'):row['error_message']=(e.text or '')[:800]
                except ET.ParseError:row['xml_parse_failed']=True
        except requests.RequestException as exc:
            row['network_error_type']=type(exc).__name__
        finally:session.close()
        row['seconds']=round(time.monotonic()-start,2)
        # Only selected error fields are saved; no auth bodies, headers or tokens.
        for field in ('error_code','error_message'):
            if field in row:row[field]=str(row[field]).replace(key,'[redacted]')[:800]
        report['cases'].append(row)
        serialized=json.dumps(report,ensure_ascii=False,indent=2)
        if key in serialized:raise RuntimeError('Credential persistence refused.')
        (output/'diagnostic.json').write_text(serialized,encoding='utf-8')
        print(json.dumps(row,ensure_ascii=False),flush=True)
        time.sleep(1.0)
        return row

    if args.supplement:
        call('direct','scopus_previous_control_metadata','/content/search/scopus',params={
            'query':'DOI(10.1016/j.apsusc.2019.145153)','count':1})
        entries=parsed.get(('direct','scopus_previous_control_metadata'),{}).get('search-results',{}).get('entry',[])
        found=next((e for e in entries if str(e.get('prism:doi','')).lower()=='10.1016/j.apsusc.2019.145153'),None)
        if found:
            control=next(a for a in report['articles'] if a['label']=='previously_failed_control')
            control.update(title=found.get('dc:title',''),journal=found.get('prism:publicationName',''),
                openaccess=found.get('openaccess'),access_status='Scopus openaccess='+str(found.get('openaccess'))+'; institution subscription not independently verified')
        call('direct','authenticate_scopus','/authenticate',params={'platform':'SCOPUS'})
        (output/'diagnostic.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Supplement completed; no credentials or authentication bodies saved.',flush=True)
        return

    for mode in ('system_proxy','direct'):
        call(mode,'scopus_search','/content/search/scopus',params={'query':'TITLE-ABS-KEY("NH3-SCR")','count':3})
        call(mode,'sciencedirect_search_put','/content/search/sciencedirect',payload={'qs':'"NH3-SCR"','display':{'show':10}})
        call(mode,'authenticate_scidir','/authenticate',params={'platform':'SCIDIR'})

    # Select an OA control from Elsevier's own Scopus index; do not assume that
    # an arbitrary paper with a repository copy is publisher open access.
    usable=next((m for m in ('direct','system_proxy') if parsed.get((m,'scopus_search'),{}).get('search-results',{}).get('entry')),None)
    oa=None
    if usable:
        call(usable,'scopus_oa_control','/content/search/scopus',params={
            'query':'SRCTITLE(Heliyon) AND TITLE-ABS-KEY(catalyst) AND OPENACCESS(1)','count':3})
        entries=parsed.get((usable,'scopus_oa_control'),{}).get('search-results',{}).get('entry',[])
        oa=next((e for e in entries if str(e.get('prism:doi','')).startswith('10.1016/') and str(e.get('openaccess'))=='1'),None)
    articles=[{'label':'previously_failed_control','doi':'10.1016/j.apsusc.2019.145153','access_status':'not_yet_confirmed'}]
    if oa:
        articles.insert(0,{'label':'oa_control','doi':oa['prism:doi'],'title':oa.get('dc:title',''),
                          'journal':oa.get('prism:publicationName',''),'access_status':'Scopus openaccess=1; Heliyon',
                          'openaccess':oa.get('openaccess')})
    report['articles']=articles
    for mode in ('system_proxy','direct'):
        for article in articles:
            path='/content/article/doi/'+quote(article['doi'],safe='/')
            for view,accept,label in [('META_ABS','application/json','abstract'),('FULL','text/xml','xml_full'),
                                      ('FULL','application/pdf','pdf_full')]:
                call(mode,article['label']+'_'+label,path,accept,{'view':view})
    print('Finished. Report: '+str(output/'diagnostic.json'),flush=True)
    key=''

if __name__=='__main__':main()
