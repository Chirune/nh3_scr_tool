"""Build four current source bundles from Git-tracked files only."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile

ROOT=Path(__file__).resolve().parents[1]
FORBIDDEN={'.pdf','.pptx','.ppt','.docx','.7z','.exe','.argosmodel','.sqlite','.sqlite3','.db','.pyc'}


def build(destination):
    destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=True)
    raw=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT)
    files=[name.decode('utf8') for name in raw.split(b'\0') if name]
    for name in files:
        path=Path(name)
        if path.suffix.lower() in FORBIDDEN or path.parts[0] in {'.venv','runtime_data','local_models','output','outputs','private'}:
            raise ValueError('Unexpected non-source tracked file: '+name)
        if not (ROOT/name).is_file():raise ValueError('Missing tracked file: '+name)
    result=[]
    for mode in ['All','Stage1','Stage2','Stage3']:
        filename=f'CatalystWorkbench-{mode}-Latest.zip'
        prefix=f'CatalystWorkbench-{mode}'
        stage='all' if mode=='All' else mode[-1]
        with ZipFile(destination/filename,'w',ZIP_DEFLATED,compresslevel=7) as z:
            for name in files:z.write(ROOT/name,prefix+'/'+name)
            z.writestr(prefix+'/OPEN_THIS.cmd',f'@echo off\r\ncall "%~dp0start_workbench.cmd" --stage {stage} %*\r\n')
            z.writestr(prefix+'/START_HERE.txt',
                f'Current shared workbench: 2026.10.09\nDefault entry: {mode}\n\n'
                '1. Fully extract this archive.\n2. Install 64-bit Python 3.11 or 3.12 if needed.\n'
                '3. Double-click OPEN_THIS.cmd. Initial dependency installation needs internet.\n'
                '4. Tutorial: docs/workbench/tutorial.html\n\n'
                'All four bundles use identical current shared code; only their default entry differs.\n'
                'Private papers, projects, PPT, patents, credentials and model weights are not included.\n')
        with ZipFile(destination/filename) as z:assert z.testzip() is None
        data=(destination/filename).read_bytes()
        result.append({'name':filename,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'source_files':len(files)})
    (destination/'SHA256SUMS.txt').write_text('\n'.join(f"{r['sha256']}  {r['name']}" for r in result)+'\n',encoding='ascii')
    (destination/'release_manifest.json').write_text(json.dumps({'version':'2026.10.09','assets':result},indent=2),encoding='utf8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'release/current')
    print(json.dumps(build(parser.parse_args().output),indent=2))
