"""Credential-safe validation of official Springer clients and existing route."""
import getpass
import json
from pathlib import Path
import sys
from urllib.request import ProxyHandler,build_opener

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'catalyst_workbench/gateway'))
from official_sdks import springer_search
from publisher_clients import SpringerOAClient,SPRINGER_PROBE_DOI
from elsevier_api import NoRedirect,APIError

key=getpass.getpass('Springer Key (memory only): ').strip()
out=ROOT/'runtime_data/platform_expansion_20261013/springer';out.mkdir(parents=True,exist_ok=True)
rows=[]
requests_to_test=[('meta','doi:10.1038/s41467-026-72879-7')] if '--meta' in sys.argv else ([('tdm','doi:10.1038/s41467-026-72879-7')] if '--tdm' in sys.argv else [('openaccess','doi:'+SPRINGER_PROBE_DOI),('openaccess','NH3 SCR catalyst')])
for kind,query in requests_to_test:
    row={'client':'springernature-api-client 0.0.10','kind':kind,'query':query}
    try:
        result=springer_search(key,query,kind=kind,limit=3)
        if kind=='tdm':
            row.update(status='xml_response_received',xml_chars=len(result),fulltext_identity_verified=False)
            if key in result:raise ValueError('credential echo')
            (out/'tdm_unverified_response.xml').write_text(result,encoding='utf-8')
        else:
            records=result.get('records',[])
            row.update(status='ok',count=len(records),records=[{**{k:r.get(k) for k in ('doi','title','openaccess','url')},'abstract_chars':len(r.get('abstract') or '')} for r in records])
    except Exception as error:
        cause=error.__cause__;response=getattr(cause,'response',None)
        row.update(status='not_completed',http_status=getattr(response,'status_code',None),error_type=type(error).__name__)
    rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    if row.get('http_status') in (401,403,429):break
try:
    if '--meta' in sys.argv:raise RuntimeError('Meta-only test; do not send this key to OpenAccess.')
    client=SpringerOAClient(key)
    result=client.check_access()
    row={'client':'workbench','status':result['status'],'details':result}
except APIError as error:row={'client':'workbench',**error.public_details()}
except RuntimeError:row={'client':'workbench','status':'not_tested_oa_with_meta_key'}
rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
report={'tested_date':'2026-10-13','credentials_saved':False,'tests':rows,'tdm_api_metric_provided':False}
payload=json.dumps(report,ensure_ascii=False,indent=2)
assert key not in payload
(out/('meta_validation.json' if '--meta' in sys.argv else ('tdm_validation.json' if '--tdm' in sys.argv else 'validation.json'))).write_text(payload,encoding='utf-8')
key=''
