"""Run isolated suites so the two tools' existing flat imports never collide."""
import argparse
from pathlib import Path
import json
import os
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--without-team',action='store_true')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args(argv)
    suites=['catalyst_workbench','catalyst_workbench/gateway','catalyst_workbench/digitizer']
    if not args.without_team:suites.append('tests')
    result=[]
    for suite in suites:
        start=time.monotonic()
        env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'}
        run=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/run_workbench_suite.py'),suite],
            cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf8',errors='replace')
        print(suite+'\n'+run.stdout+run.stderr,flush=True)
        result.append({'suite':suite,'exit_code':run.returncode,'seconds':round(time.monotonic()-start,2),
                       'output':run.stdout+run.stderr})
    for stage in ['all','1','2','3','figures']:
        run=subprocess.run([sys.executable,'-B',str(ROOT/'start_workbench.py'),'--stage',stage,'--smoke'],
            cwd=ROOT,capture_output=True,text=True,encoding='utf8',errors='replace')
        result.append({'suite':'hidden_gui_'+stage,'exit_code':run.returncode,'output':run.stdout+run.stderr})
        print('hidden_gui_'+stage+': '+str(run.returncode),flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return int(any(r['exit_code'] for r in result))


if __name__=='__main__':raise SystemExit(main())
