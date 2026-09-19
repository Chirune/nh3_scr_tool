"""One-command interactive smoke test for publisher APIs."""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_case(name, query, sources, output):
    print(f"\n[{name}] 正在检索并尝试下载 1 篇论文，请稍候……")
    command = [
        sys.executable, '-m', 'scrtool', 'harvest', '--query', query,
        '--sources', *sources, '--limit', '3', '--max-downloads', '1',
        '--delay', '1', '-o', str(output),
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    summary_path = output / 'summary.json'
    errors_path = output / 'errors.json'
    manifest_path = output / 'download_manifest.json'
    summary = json.loads(summary_path.read_text(encoding='utf-8')) if summary_path.exists() else {}
    errors = json.loads(errors_path.read_text(encoding='utf-8')) if errors_path.exists() else []
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else []
    print(f"[{name}] 检索到 {summary.get('records', 0)} 条；检索错误 {len(errors)} 条。")
    for error in errors:
        print(f"  - {error.get('source')}: {error.get('error')}")
    for row in manifest[:1]:
        print(f"  - 全文状态: {row.get('status')}")
        if row.get('elsevier_xml'):
            print(f"  - Elsevier XML: {row['elsevier_xml'].get('status')}")
        if row.get('springer_jats'):
            print(f"  - Springer JATS: {row['springer_jats'].get('status')}")
        for error in row.get('errors') or []:
            print(f"  - 下载信息: {error}")
    print(f"[{name}] 详细结果: {output}")
    return completed.returncode, errors


def main():
    print('NH3-SCR 文献接口一键测试')
    print('输入内容不会显示，也不会保存到文件。粘贴后按回车。')
    springer = getpass.getpass('1/2 Springer Nature API key: ').strip()
    elsevier = getpass.getpass('2/2 Elsevier API key: ').strip()
    if not springer or not elsevier:
        print('ERROR: 两个 key 都必须输入。')
        return 2
    os.environ['SPRINGER_API_KEY'] = springer
    os.environ['ELSEVIER_API_KEY'] = elsevier

    nature_output = ROOT / 'outputs' / 'archive' / 'api_tests' / 'test_nature_api'
    elsevier_output = ROOT / 'outputs' / 'archive' / 'api_tests' / 'test_elsevier_api'
    nature_code, nature_errors = run_case(
        'Nature',
        '10.1038/s41467-026-72879-7',
        ['springer', 'crossref'], nature_output,
    )
    elsevier_code, elsevier_errors = run_case(
        'Elsevier',
        '10.1016/j.cattod.2012.05.041',
        ['scopus', 'crossref'], elsevier_output,
    )

    print('\n测试结束。')
    print('Nature API：' + ('需要查看上面的错误' if nature_errors else '检索接口正常'))
    print('Elsevier API：' + ('需要查看上面的错误' if elsevier_errors else '检索接口正常'))
    os.environ.pop('SPRINGER_API_KEY', None)
    os.environ.pop('ELSEVIER_API_KEY', None)
    return 0 if nature_code == 0 and elsevier_code == 0 and not nature_errors and not elsevier_errors else 2


if __name__ == '__main__':
    raise SystemExit(main())
