"""Desktop front end for the NH3-SCR literature harvester."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, scrolledtext


ROOT = Path(__file__).resolve().parents[1]


class DownloadWindow:
    def __init__(self, root):
        self.root = root
        self.output = None
        root.title('NH3-SCR 论文检索与下载')
        root.geometry('820x680')
        root.minsize(720, 580)

        tk.Label(root, text='输入检索词与需要的数量。API key 只在本次运行中使用，不会保存。',
                 anchor='w').pack(fill='x', padx=18, pady=(16, 8))
        form = tk.Frame(root)
        form.pack(fill='x', padx=18)

        self.query = self.row(form, 0, '检索词', show=None)
        self.query.insert(0, 'NH3-SCR catalyst')
        self.springer = self.row(form, 1, 'Springer Nature key（可选）', show='●')
        self.elsevier = self.row(form, 2, 'Elsevier key（可选）', show='●')
        self.openalex = self.row(form, 3, 'OpenAlex key（可选）', show='●')
        self.email = self.row(form, 4, '邮箱（用于 Unpaywall，可选）', show=None)

        numbers = tk.Frame(form)
        numbers.grid(row=5, column=0, columnspan=2, sticky='w', pady=8)
        tk.Label(numbers, text='每个来源最多检索').pack(side='left')
        self.limit = tk.Spinbox(numbers, from_=1, to=1000, width=7)
        self.limit.delete(0, 'end'); self.limit.insert(0, '50')
        self.limit.pack(side='left', padx=(6, 18))
        tk.Label(numbers, text='最多下载').pack(side='left')
        self.max_downloads = tk.Spinbox(numbers, from_=0, to=1000, width=7)
        self.max_downloads.delete(0, 'end'); self.max_downloads.insert(0, '20')
        self.max_downloads.pack(side='left', padx=6)
        form.columnconfigure(1, weight=1)

        controls = tk.Frame(root)
        controls.pack(fill='x', padx=18, pady=10)
        self.start = tk.Button(controls, text='开始下载', width=15, command=self.begin)
        self.start.pack(side='left')
        self.open_button = tk.Button(controls, text='打开结果文件夹', width=16,
                                     state='disabled', command=self.open_output)
        self.open_button.pack(side='left', padx=10)
        self.status = tk.Label(controls, text='等待开始', anchor='w')
        self.status.pack(side='left', padx=8)

        self.log = scrolledtext.ScrolledText(root, wrap='word', state='disabled')
        self.log.pack(fill='both', expand=True, padx=18, pady=(0, 16))
        self.query.focus_set()

    def row(self, parent, row, label, show):
        tk.Label(parent, text=label, width=29, anchor='w').grid(row=row, column=0, sticky='w', pady=5)
        entry = tk.Entry(parent, show=show)
        entry.grid(row=row, column=1, sticky='ew', pady=5)
        return entry

    def write(self, text):
        self.root.after(0, self._write, text)

    def _write(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def begin(self):
        query = self.query.get().strip()
        if not query:
            messagebox.showerror('缺少检索词', '请输入检索词或论文 DOI。')
            return
        try:
            limit = int(self.limit.get())
            maximum = int(self.max_downloads.get())
            if not 1 <= limit <= 1000 or not 0 <= maximum <= 1000:
                raise ValueError
        except ValueError:
            messagebox.showerror('数量不正确', '检索数应为 1–1000，下载数应为 0–1000。')
            return
        values = {
            'query': query, 'limit': limit, 'maximum': maximum,
            'springer': self.springer.get().strip(),
            'elsevier': self.elsevier.get().strip(),
            'openalex': self.openalex.get().strip(),
            'email': self.email.get().strip(),
        }
        self.start.configure(state='disabled')
        self.open_button.configure(state='disabled')
        self.log.configure(state='normal'); self.log.delete('1.0', 'end'); self.log.configure(state='disabled')
        self.status.configure(text='正在检索和下载')
        threading.Thread(target=self.run, args=(values,), daemon=True).start()

    def run(self, values):
        try:
            env = os.environ.copy()
            sources = ['crossref', 'nature']
            if values['springer']:
                # The key may allow DOI-specific JATS retrieval while denying
                # Springer keyword search. Crossref supplies discovery; the key
                # is still used automatically for each matching DOI.
                env['SPRINGER_API_KEY'] = values['springer']
            if values['elsevier']:
                env['ELSEVIER_API_KEY'] = values['elsevier']; sources.append('scopus')
            if values['openalex']:
                env['OPENALEX_API_KEY'] = values['openalex']; sources.append('openalex')
            if values['email']:
                env['UNPAYWALL_EMAIL'] = values['email']
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            self.output = ROOT / 'outputs' / 'runs' / f'download_{stamp}'
            self.write('启用来源：' + '、'.join(sources))
            if values['springer']:
                self.write('Springer Nature key：用于已找到 DOI 的开放全文/JATS 获取')
            self.write('输出目录：' + str(self.output))
            command = [sys.executable, '-m', 'scrtool', 'harvest',
                       '--query', values['query'], '--sources', *sources,
                       '--limit', str(values['limit']), '--max-downloads', str(values['maximum']),
                       '--library-dir', str(ROOT / 'outputs' / 'paper_library'),
                       '--delay', '1', '-o', str(self.output)]
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            for line in process.stdout:
                self.write(line.rstrip())
            code = process.wait()
            summary_path = self.output / 'summary.json'
            statuses = {}
            if summary_path.exists():
                summary = json.loads(summary_path.read_text(encoding='utf-8'))
                self.write('\n检索记录：' + str(summary.get('records', 0)))
                self.write('来源错误：' + str(summary.get('search_errors', 0)))
                self.write('共享论文库：' + str(summary.get('paper_library', '')))
                statuses = summary.get('download_status') or {}
                for status, count in statuses.items():
                    self.write(f'{status}: {count}')
            downloaded = sum(count for status, count in statuses.items()
                             if status in {'downloaded_pdf', 'cached_pdf', 'existing', 'downloaded_xml_only',
                                           'downloaded_jats_only'})
            if downloaded:
                self.write(f'\n完成。可用全文 {downloaded} 个（包含共享库缓存），位于结果目录的 files 文件夹中。')
            else:
                self.write('\n检索已完成，但没有获得可下载的开放全文；files 文件夹为空。')
            self.root.after(0, self.status.configure,
                            {'text': '下载完成' if code == 0 else '完成，有错误'})
            self.root.after(0, self.open_button.configure, {'state': 'normal'})
        except Exception as exc:
            self.write(f'程序错误：{type(exc).__name__}: {exc}')
            self.root.after(0, self.status.configure, {'text': '程序错误'})
        finally:
            for entry in [self.springer, self.elsevier, self.openalex]:
                self.root.after(0, entry.delete, 0, 'end')
            self.root.after(0, self.start.configure, {'state': 'normal'})

    def open_output(self):
        if self.output and self.output.exists():
            os.startfile(self.output)


def main():
    root = tk.Tk()
    DownloadWindow(root)
    root.mainloop()


if __name__ == '__main__':
    main()
