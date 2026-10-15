"""Compare the official Wiley client with the workbench, without saving tokens."""
import getpass
import importlib.metadata
import json
import logging
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

import requests
from wiley_tdm import TDMClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'catalyst_workbench/gateway'))
from publisher_clients import WileyClient
from elsevier_api import APIError

logging.disable(logging.CRITICAL)
OUT = ROOT / 'runtime_data/wiley_diagnostic_20261013'
OUT.mkdir(parents=True, exist_ok=True)
token = getpass.getpass('Wiley TDM Token (memory only): ').strip()
if not token:
    raise SystemExit('Missing token.')

def safe_message(value):
    value = str(value or '').replace(token, '[credential removed]')
    value = re.sub(r'https?://\S+', '[URL removed]', value)
    value = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[IP removed]', value)
    value = re.sub(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}', '[UUID removed]', value)
    return value[:700]

class DiagnosticSession(requests.Session):
    def rebuild_auth(self, prepared_request, response):
        super().rebuild_auth(prepared_request, response)
        if urlsplit(prepared_request.url).hostname != 'api.wiley.com':
            prepared_request.headers.pop('Wiley-TDM-Client-Token', None)

rows = []
last_started = 0.0
def pace():
    global last_started
    delay = 10.1 - (time.monotonic() - last_started)
    if delay > 0:
        time.sleep(delay)
    last_started = time.monotonic()

def official(doi, mode):
    row = {'client': 'official_wiley_tdm', 'version': importlib.metadata.version('wiley-tdm'),
           'doi': doi, 'network': mode, 'responses': []}
    client = TDMClient(api_token=token, download_dir=OUT / ('official_' + mode))
    original = client._api_session
    session = DiagnosticSession()
    session.headers.update(original.headers)
    session.trust_env = mode != 'direct'
    original.close()
    def observed(response, *args, **kwargs):
        item = {'host': urlsplit(response.url).hostname, 'http_status': response.status_code,
                'content_type': response.headers.get('Content-Type', '')}
        if response.status_code >= 400:
            item['server_message'] = safe_message(response.text)
        row['responses'].append(item)
        return response
    session.hooks['response'] = [observed]
    client._api_session = session
    pace()
    result = client.download_pdf(doi)
    row.update(status=result.status.name, http_status=int(result.api_status) if result.api_status else None,
               duration_seconds=round(result.duration or 0, 2), message=safe_message(result.comment))
    if result.path and result.path.is_file():
        with result.path.open('rb') as f:
            row['pdf_signature_ok'] = f.read(5) == b'%PDF-'
        row['path'] = str(result.path)
        row['bytes'] = result.path.stat().st_size
    session.close()
    rows.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row

oa = '10.1002/cctc.202301214'
experimental = '10.1002/cctc.202500194'
verify_workbench = '--verify-workbench' in sys.argv
first = {} if verify_workbench else official(oa, 'system_proxy')
if not verify_workbench:
    if first.get('http_status') not in (400, 401, 403, 429):
        official(experimental, 'system_proxy')
    if first.get('http_status') != 429:
        official(oa, 'direct')

# The application has its own file/identity checks; test the same DOI through it.
papers = [(oa, 'Recent Progress in the Application of Transition-Metal Containing MFI topologies for NH3-SCR-DeNOx and NH3 oxidation')]
if verify_workbench:
    papers.append((experimental, 'Environmental-Benign Catalysts for Selective Catalytic Reduction with Ammonia (NH3-SCR) Supported on Natural Clinoptilolite Prepared by Solution Combustion Synthesis Method'))
client = WileyClient(token)
for doi, title in papers if first.get('http_status') != 429 else []:
    pace()
    record = {'doi': doi, 'title': title,
              'id': 'wiley-diagnostic-' + doi.split('/')[-1], 'route': {'publisher_group': 'Wiley'}}
    row = {'client': 'workbench', 'doi': doi, 'network': 'direct', 'title': title}
    try:
        result = client.fetch_pdf(record, OUT / 'workbench')
        row.update({name: result.get(name) for name in ('status', 'local_path', 'doi_verified', 'pages', 'title_coverage', 'bytes')})
    except APIError as error:
        row.update(status=error.status, http_status=error.http_status, message=safe_message(error))
    except Exception as error:
        row.update(status=type(error).__name__, message=safe_message(error))
    rows.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    if row.get('http_status') in (400, 401, 403, 429):
        break

report = {'tested_date': '2026-10-13', 'credentials_saved': False,
          'official_client_source': 'D:/数据检索/tool/tdm-client-1.2.0',
          'credential_redirect_policy': 'Token sent only to api.wiley.com; signed redirect URLs are not logged.',
          'tests': rows}
serialized = json.dumps(report, ensure_ascii=False, indent=2)
assert token not in serialized
report_path = OUT / ('workbench_verified.json' if verify_workbench else 'diagnostic.json')
report_path.write_text(serialized, encoding='utf-8')
token = ''
print('Report saved: ' + str(report_path), flush=True)
