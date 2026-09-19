"""Small GUI for testing Springer Nature and Elsevier API credentials."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext


ROOT = Path(__file__).resolve().parents[1]


class ApiTestWindow:
    def __init__(self, root):
        self.root = root
        root.title('NH3-SCR 文献接口测试')
        root.geometry('760x560')
        root.minsize(680, 480)

        intro = ('分别粘贴两个 API key，然后点击“开始测试”。\n'
                 '密钥仅在本次测试进程中使用，不会写入文件。')
        tk.Label(root, text=intro, justify='left', anchor='w').pack(fill='x', padx=18, pady=(16, 10))

        form = tk.Frame(root)
        form.pack(fill='x', padx=18)
        tk.Label(form, text='Springer Nature API key', width=25, anchor='w').grid(row=0, column=0, pady=6)
        self.springer = tk.Entry(form, show='●')
        self.springer.grid(row=0, column=1, sticky='ew', pady=6)
        tk.Label(form, text='Elsevier API key', width=25, anchor='w').grid(row=1, column=0, pady=6)
        self.elsevier = tk.Entry(form, show='●')
        self.elsevier.grid(row=1, column=1, sticky='ew', pady=6)
        form.columnconfigure(1, weight=1)

        controls = tk.Frame(root)
        controls.pack(fill='x', padx=18, pady=10)
        self.start = tk.Button(controls, text='开始测试', width=16, command=self.begin)
        self.start.pack(side='left')
        tk.Button(controls, text='打开结果文件夹', width=16,
                  command=lambda: os.startfile(ROOT / 'outputs')).pack(side='left', padx=10)
        self.status = tk.Label(controls, text='等待输入', anchor='w')
        self.status.pack(side='left', padx=8)

        self.log = scrolledtext.ScrolledText(root, wrap='word', height=20, state='disabled')
        self.log.pack(fill='both', expand=True, padx=18, pady=(0, 16))
        self.springer.focus_set()

    def write(self, message):
        self.root.after(0, self._write_now, message)

    def _write_now(self, message):
        self.log.configure(state='normal')
        self.log.insert('end', message + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def set_status(self, message):
        self.root.after(0, self.status.configure, {'text': message})

    def begin(self):
        springer = self.springer.get().strip()
        elsevier = self.elsevier.get().strip()
        if not springer or not elsevier:
            messagebox.showerror('缺少 API key', '两个输入框都需要填写。')
            return
        self.start.configure(state='disabled')
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')
        self.status.configure(text='正在测试')
        threading.Thread(target=self.run_all, args=(springer, elsevier), daemon=True).start()

    def execute_case(self, name, query, sources, output, env):
        self.write(f'[{name}] 开始检索并尝试下载 1 篇论文……')
        command = [sys.executable, '-m', 'scrtool', 'harvest', '--query', query,
                   '--sources', *sources, '--limit', '3', '--max-downloads', '1',
                   '--delay', '1', '-o', str(output)]
        process = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                 text=True, encoding='utf-8', errors='replace', check=False)
        if process.stdout.strip():
            self.write(process.stdout.strip())
        if process.stderr.strip():
            self.write(process.stderr.strip())
        errors_path = output / 'errors.json'
        manifest_path = output / 'download_manifest.json'
        errors = json.loads(errors_path.read_text(encoding='utf-8')) if errors_path.exists() else []
        manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else []
        for error in errors:
            self.write(f"[{name}] {error.get('source')}: {error.get('error')}")
        if manifest:
            row = manifest[0]
            self.write(f"[{name}] PDF 状态：{row.get('status')}")
            if row.get('springer_jats'):
                self.write(f"[{name}] JATS 状态：{row['springer_jats'].get('status')}")
            if row.get('elsevier_xml'):
                self.write(f"[{name}] XML 状态：{row['elsevier_xml'].get('status')}")
            for error in row.get('errors') or []:
                self.write(f'[{name}] {error}')
        ok = process.returncode == 0 and not errors
        self.write(f'[{name}] ' + ('接口检索正常。' if ok else '存在错误，请查看上面的状态。'))
        self.write(f'[{name}] 结果目录：{output}\n')
        return ok

    def run_all(self, springer, elsevier):
        try:
            env = os.environ.copy()
            env['SPRINGER_API_KEY'] = springer
            env['ELSEVIER_API_KEY'] = elsevier
            nature_ok = self.execute_case(
                'Nature', '10.1038/s41467-026-72879-7',
                ['springer', 'crossref'], ROOT / 'outputs' / 'archive' / 'api_tests' / 'test_nature_api', env)
            elsevier_ok = self.execute_case(
                'Elsevier', '10.1016/j.cattod.2012.05.041',
                ['scopus', 'crossref'], ROOT / 'outputs' / 'archive' / 'api_tests' / 'test_elsevier_api', env)
            if nature_ok and elsevier_ok:
                self.write('全部检索接口测试完成。')
                self.set_status('测试完成')
            else:
                self.write('测试完成，但至少一个接口需要检查权限或密钥。')
                self.set_status('完成，有错误')
        except Exception as exc:
            self.write(f'程序错误：{type(exc).__name__}: {exc}')
            self.set_status('程序错误')
        finally:
            self.root.after(0, self.springer.delete, 0, 'end')
            self.root.after(0, self.elsevier.delete, 0, 'end')
            self.root.after(0, self.start.configure, {'state': 'normal'})


def main():
    root = tk.Tk()
    ApiTestWindow(root)
    root.mainloop()


if __name__ == '__main__':
    main()
