"""One official XML/FULL request; key stays in memory and errors are allowlisted."""
import getpass
import argparse
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import sys
from urllib.request import build_opener, ProxyHandler

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'catalyst_workbench/gateway'))
from elsevier_api import ElsevierClient,APIError,NoRedirect

parser=argparse.ArgumentParser();parser.add_argument('--output');args=parser.parse_args()
tested_date=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
key=getpass.getpass('Elsevier Key (memory only): ').strip()
out=Path(args.output).resolve() if args.output else ROOT/('runtime_data/elsevier_xml_'+tested_date.replace('-',''))
out.mkdir(parents=True,exist_ok=True)
paper={'id':'live-xml-control','doi':'10.1016/j.apsusc.2019.145153',
       'title':'The effect of SO2 on NH3-SCO and SCR properties over Cu/SCR catalyst',
       'route':{'publisher_group':'Elsevier'}}
client=ElsevierClient(key,opener=build_opener(ProxyHandler({}),NoRedirect()))
try:
    result=client.fetch_xml(paper,out,progress=print)
except APIError as error:
    result=error.public_details()
except Exception as error:
    result={'status':type(error).__name__,'message':'XML 请求或解析未完成；未保存未经核对的正文。'}
report={'tested_date':tested_date,'network':'direct','doi':paper['doi'],
        'credentials_saved':False,'official_xml_result':result}
payload=json.dumps(report,ensure_ascii=False,indent=2)
assert key not in payload
(out/'live_result.json').write_text(payload,encoding='utf-8')
print(payload,flush=True)
key=''
