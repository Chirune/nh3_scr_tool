"""Build clean source and portable Windows release assets, without uploading.

Run with the project's tested Windows virtual environment. A real CPython
runtime is bundled instead of freezing only the legacy collection window.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
VERSION = '2026.10.15'
FOLDERS = {'browser_extension', 'catalyst_workbench', 'docs', 'examples',
           'modules', 'profiles', 'scripts', 'scrtool', 'tests'}
ROOT_FILES = {'.gitignore', 'CONTRIBUTING.md', 'CURRENT_RELEASE.json', 'README.md',
              'requirements.txt', 'requirements-workbench.txt', 'requirements.lock.txt',
              'start_workbench.py', 'start_workbench.cmd',
              'OPEN_THIS.cmd', 'START_HERE.txt', 'install.ps1', 'run.cmd', 'run.ps1',
              'download_papers.cmd', 'test_apis.cmd', 'reproduce_real_case.ps1'}
EXCLUDED = {'__pycache__', '.git', '.venv', 'runtime_data', 'local_models',
            'node_modules', '.pytest_cache', 'output', 'outputs', 'private'}
FORBIDDEN = {'.pyc', '.pdf', '.docx', '.pptx', '.xlsx', '.xls', '.sqlite',
             '.sqlite3', '.db', '.exe', '.zip', '.7z', '.log', '.argosmodel'}


def public_files():
    for path in sorted(ROOT.rglob('*')):
        rel = path.relative_to(ROOT)
        if not path.is_file() or any(p in EXCLUDED for p in rel.parts): continue
        if rel.parts[0] not in FOLDERS and str(rel) not in ROOT_FILES: continue
        if path.suffix.lower() in FORBIDDEN or path.name.startswith('.env'): continue
        if rel.as_posix() == 'docs/PATENT_DEVELOPMENT_PLAN_20260929.md': continue
        yield path, rel


def scan_public(files):
    """Reject known local credentials without printing their values."""
    import re
    patterns = [re.compile(r'\b[a-fA-F0-9]{32}\b'),
                re.compile(r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', re.I),
                re.compile(r'\bsk-[A-Za-z0-9_-]{16,}\b')]
    for source, rel in files:
        if source.suffix.lower() not in {'.py', '.json', '.md', '.txt', '.js', '.html', '.ps1', '.cmd', '.cs'}: continue
        text = source.read_text(encoding='utf-8-sig')
        # Fixed hashes and synthetic IDs can be legitimate. Only key/token
        # literals are rejected; scan all supplied credentials additionally below.
        for line in text.splitlines():
            if any(word in line.lower() for word in ('api_key', 'apikey', 'token', 'secret')):
                if any(p.search(line) for p in patterns):
                    raise ValueError('Review possible credential in '+str(rel))


def copy_runtime(destination):
    base = Path(sys.base_prefix)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', 'site-packages', 'test', 'tests', 'idlelib')
    for name in ('Lib', 'DLLs', 'tcl'):
        shutil.copytree(base/name, destination/name, ignore=ignore)
    for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll',
                 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        shutil.copy2(base/name, destination/name)
    # This environment contains only the application's runtime dependencies.
    site = Path(sys.prefix)/'Lib/site-packages'
    shutil.copytree(site, destination/'Lib/site-packages',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'direct_url.json'))


def runtime_env(runtime):
    return {**os.environ, 'PYTHONHOME':str(runtime), 'PYTHONPATH':'',
            'PYTHONNOUSERSITE':'1', 'PYTHONUTF8':'1', 'PYTHONDONTWRITEBYTECODE':'1',
            'TCL_LIBRARY':str(runtime/'tcl/tcl8.6'), 'TK_LIBRARY':str(runtime/'tcl/tk8.6')}


def archive(folder, target):
    with ZipFile(target, 'w', ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(folder.rglob('*')):
            if path.is_file(): z.write(path, folder.name+'/'+path.relative_to(folder).as_posix())
    with ZipFile(target) as z:
        if z.testzip(): raise ValueError('Archive integrity check failed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt' or sys.version_info[:2] != (3,12):
        raise SystemExit('Build with the tested Windows CPython 3.12 environment.')
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    stage = ROOT/'artifact_work'/('github_'+VERSION)
    if stage.exists(): raise SystemExit('Build stage exists; use a new build directory or review it first: '+str(stage))
    source = stage/'CatalystWorkbench-All'; source.mkdir(parents=True)
    files = list(public_files()); scan_public(files)
    for path, rel in files:
        dest = source/rel; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path,dest)
    print('Public source files:',len(files),flush=True)
    windows = stage/'CatalystWorkbench-Windows'; shutil.copytree(source, windows)
    runtime = windows/'runtime'; copy_runtime(runtime)
    compiler = Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/codepage:65001', '/target:winexe', '/platform:x64',
                    '/reference:System.Windows.Forms.dll',
                    '/out:'+str(windows/'CatalystWorkbench.exe'),
                    str(ROOT/'scripts/WindowsLauncher.cs')],check=True)
    (windows/'OPEN_THIS.cmd').write_text('@echo off\r\ncd /d "%~dp0"\r\nstart "" "%~dp0CatalystWorkbench.exe" %*\r\n',encoding='ascii')
    (windows/'start_workbench.cmd').write_text('@echo off\r\ncd /d "%~dp0"\r\n"%~dp0CatalystWorkbench.exe" %*\r\n',encoding='ascii')
    instructions = ('催化文献数据工作台 '+VERSION+' · Windows 完整便携版\n\n'
        '1. 完整解压整个压缩包，不要在压缩包内运行。\n'
        '2. 双击 CatalystWorkbench.exe；不需要另装 Python。不要单独复制 EXE。\n'
        '3. 打开1号板块：检索/导入 → 保留论文 → 获取正文：API / 浏览器。\n'
        '4. 浏览器通道需要本机 Chrome；由你完成学校/出版社登录。\n'
        '5. 所有 API Key 由各用户自行填写；本包不含账号、论文或模型。\n'
        '6. Zotero 需自行安装并打开；本地大模型与翻译模型为可选下载。\n'
        '7. 查看更新：docs/workbench/RELEASE_2026.10.15.md。\n'
        '8. 数据保存在 runtime_data；升级前保留原数据目录。\n\n'
        'Nature原版PDF、MDPI可见正文已有真实测试；Elsevier403仍待解决。\n'
        '提取结果需要核对，候选证据不等于可直接训练的样本。\n')
    (windows/'START_HERE.txt').write_text(instructions,encoding='utf-8-sig')
    # Verify the relocated runtime without inheriting the development Python.
    env = runtime_env(runtime)
    subprocess.run([str(runtime/'python.exe'),'-B',str(windows/'start_workbench.py'),'--check'],
                   cwd=windows,env=env,check=True,timeout=60)
    for stage_name in ('all','1','2','3','figures'):
        subprocess.run([str(runtime/'python.exe'),'-B',str(windows/'start_workbench.py'),
                        '--stage',stage_name,'--smoke'],cwd=windows,env=env,check=True,timeout=60)
    subprocess.run([str(runtime/'python.exe'),'-B','-m','unittest',
                    'test_browser_access.BrowserCollectorTests'],
                   cwd=windows/'catalyst_workbench/gateway',env=env,check=True,timeout=120)
    validation_path=Path('docs/workbench/VALIDATION_2026.10.15.md')
    validation=(ROOT/validation_path).read_text(encoding='utf-8')
    validation+='\n## Windows 便携环境\n\n实际复制的独立 Python 运行时通过依赖检查、整合窗口与三个板块及图表窗口的 5 项启动检查。原生 Chrome 的 2 项浏览器测试通过，覆盖登录页等待、正文识别、PDF 请求超时后的正文保存、DOI 关联与来源标记。测试使用合成网页，不代表 Elsevier 已通过。\n'
    for tree in (ROOT,source,windows):
        (tree/validation_path).write_text(validation,encoding='utf-8')
    assets=[]
    for folder,name in ((source,'CatalystWorkbench-All-Latest.zip'),
                        (windows,'CatalystWorkbench-Windows-'+VERSION+'.zip')):
        archive(folder,output/name)
        with (output/name).open('rb') as stream:
            checksum=hashlib.file_digest(stream,'sha256').hexdigest()
        assets.append({'name':name,'bytes':(output/name).stat().st_size,'sha256':checksum})
    (output/'release_manifest.json').write_text(json.dumps({'version':VERSION,'assets':assets,
        'private_materials_included':False,'uploaded':False},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(assets,ensure_ascii=False,indent=2),flush=True)


if __name__ == '__main__': main()
