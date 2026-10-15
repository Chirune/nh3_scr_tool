"""Validate the installed first-stage path; credentials stay in this process."""
import getpass
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'catalyst_workbench/gateway'))
import engine

key=getpass.getpass('OpenAlex key (not saved): ').strip()
if not key:raise SystemExit('Missing OpenAlex key.')
out=ROOT/'runtime_data'/'openalex_live_20261013'
run=engine.run_search('scr_ammonia',['NH3-SCR catalyst'],limit=5,providers=['Crossref','OpenAlex'],
                      output_root=out/'search',openalex_api_key=key,progress=print)
# Reuse the existing saved task to demonstrate DOI enrichment and stable IDs.
prior=ROOT/'runtime_data/stage1_openalex_20261012/live_summary.json'
updated=None
historical_error_count=0
if prior.is_file():
    old=engine.load_run(json.loads(prior.read_text(encoding='utf-8'))['run_path'])
    historical_error_count=len(old.get('errors') or [])
    # Work on a local copy so the previous test remains independently readable.
    copy_dir=out/'previous_result_enriched';copy_dir.mkdir(parents=True,exist_ok=True)
    old['run_dir']=str(copy_dir.resolve())
    updated=engine.enrich_existing_run(old,openalex_api_key=key,progress=print)
    assert [r['id'] for r in old['records']]==[r['id'] for r in updated['records']]

def overview(value):
    return {'run_path':str(Path(value['run_dir'])/'run.json'),'summary':value['summary'],
            'requests':value['requests'],'errors':value['errors'],'enrichment':value.get('abstract_enrichment'),
            'records':[{'id':r['id'],'doi':r.get('doi'),'title':r.get('title'),'sources':r.get('sources'),
                        'abstract_chars':len(r.get('abstract') or ''),'abstract_status':r.get('abstract_status'),
                        'abstract_origin':r.get('abstract_origin'),'abstract_source':r.get('abstract_source'),
                        'decision':r['effective_decision'],'oa_candidates':len(r.get('fulltext_candidates') or [])} for r in value['records']]}
report={'tested_date':'2026-10-13','key_persisted':False,'keyword_search':overview(run),
        'saved_result_enrichment':overview(updated) if updated else None}
if updated:
    report['saved_result_enrichment']['historical_error_count']=historical_error_count
    report['saved_result_enrichment']['new_errors']=updated['errors'][historical_error_count:]
serialized=json.dumps(report,ensure_ascii=False,indent=2)
assert key not in serialized
for path in out.rglob('*'):
    if path.is_file():assert key not in path.read_text(encoding='utf-8-sig')
out.mkdir(parents=True,exist_ok=True);(out/'validation.json').write_text(serialized,encoding='utf-8')
print(json.dumps({'search':run['summary'],'search_errors':run['errors'],
                  'enrichment':updated.get('abstract_enrichment') if updated else None,
                  'enrichment_errors':updated['errors'][historical_error_count:] if updated else [],'report':str(out/'validation.json')},ensure_ascii=False),flush=True)
key=''
