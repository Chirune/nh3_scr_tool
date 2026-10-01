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
from tkinter import filedialog, messagebox, scrolledtext, ttk


ROOT = Path(__file__).resolve().parents[1]


class DownloadWindow:
    def __init__(self, root):
        self.root = root
        self.output = None
        root.title('NH3-SCR 论文检索与下载')
        root.geometry('900x860')
        root.minsize(780, 760)

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

        screening = tk.LabelFrame(root, text='摘要筛选：先判断论文研究的反应和催化剂，再下载全文')
        screening.pack(fill='x', padx=18, pady=8)
        self.engine = ttk.Combobox(screening, state='readonly', values=[
            '规则初筛（无需AI密钥）', 'DeepSeek 摘要语义筛选', 'Ollama 本机摘要语义筛选'])
        self.engine.current(0)
        self.engine.grid(row=0, column=1, sticky='ew', pady=5)
        tk.Label(screening, text='筛选方式', width=29, anchor='w').grid(row=0, column=0, sticky='w')
        self.ai_key = self.row(screening, 1, 'DeepSeek key', show='●')
        self.ai_model = self.row(screening, 2, 'AI 模型名称', show=None)
        self.ai_model.insert(0, 'deepseek-flash')
        self.ai_key.configure(state='disabled'); self.ai_model.configure(state='disabled')
        self.engine.bind('<<ComboboxSelected>>', self.change_engine)
        self.keep_review = tk.BooleanVar(value=True)
        self.discovery_only = tk.BooleanVar(value=False)
        tk.Checkbutton(screening, text='保留待核对论文的下载机会（缺摘要不直接排除）', variable=self.keep_review).grid(row=3, column=0, columnspan=2, sticky='w')
        tk.Checkbutton(screening, text='本次只检索与筛选，先核对清单，暂不下载', variable=self.discovery_only).grid(row=4, column=0, columnspan=2, sticky='w')
        self.records_path = tk.StringVar()
        self.decisions_path = tk.StringVar()
        replay = tk.Frame(screening)
        replay.grid(row=5, column=0, columnspan=2, sticky='ew', pady=5)
        tk.Button(replay, text='使用已有题录', command=self.choose_records).pack(side='left')
        tk.Button(replay, text='载入人工核对表', command=self.choose_decisions).pack(side='left', padx=8)
        tk.Button(replay, text='清除载入', command=self.clear_replay).pack(side='left')
        self.replay_label = tk.Label(screening, text='新检索；未载入人工核对表', anchor='w', wraplength=790)
        self.replay_label.grid(row=6, column=0, columnspan=2, sticky='ew')
        tk.Label(screening, text='DeepSeek 按用量计费。Ollama 需要本机已安装并运行相应模型。', anchor='w').grid(row=7, column=0, columnspan=2, sticky='w')
        screening.columnconfigure(1, weight=1)

        controls = tk.Frame(root)
        controls.pack(fill='x', padx=18, pady=10)
        self.start = tk.Button(controls, text='开始筛选与下载', width=15, command=self.begin)
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

    def change_engine(self, event=None):
        engine = self.engine.current()
        self.ai_key.configure(state='normal' if engine == 1 else 'disabled')
        self.ai_model.configure(state='normal' if engine else 'disabled')
        if engine:
            self.ai_model.delete(0, 'end')
            self.ai_model.insert(0, 'deepseek-flash' if engine == 1 else '填写本机已安装的模型名')

    def choose_records(self):
        path = filedialog.askopenfilename(title='选择 records.json 或 records.csv', filetypes=[('题录清单', '*.json *.csv')])
        if path:
            self.records_path.set(path)
            self.refresh_replay()

    def choose_decisions(self):
        path = filedialog.askopenfilename(title='选择填写后的 manual_review_template.csv', filetypes=[('核对表', '*.csv')])
        if path:
            self.decisions_path.set(path)
            self.refresh_replay()

    def clear_replay(self):
        self.records_path.set(''); self.decisions_path.set('')
        self.refresh_replay()

    def refresh_replay(self):
        records = Path(self.records_path.get()).name if self.records_path.get() else '新检索'
        decisions = Path(self.decisions_path.get()).name if self.decisions_path.get() else '未载入人工核对表'
        self.replay_label.configure(text=records + '；' + decisions)

    def begin(self):
        query = self.query.get().strip()
        if not query and not self.records_path.get():
            messagebox.showerror('缺少检索词', '请输入检索词或论文 DOI。')
            return
        engine = self.engine.current()
        ai_model = self.ai_model.get().strip()
        if engine and (not ai_model or ai_model.startswith('填写')):
            messagebox.showerror('缺少模型', '请填写服务支持的模型名称。')
            return
        if engine == 1 and not (self.ai_key.get().strip() or os.getenv('DEEPSEEK_API_KEY')):
            messagebox.showerror('缺少 DeepSeek key', '请在此界面输入 DeepSeek key，或先选择无需密钥的规则初筛。')
            return
        if self.decisions_path.get() and not self.records_path.get():
            messagebox.showerror('需要原题录', '载入人工核对表时，请同时选择对应的 records.json，保证核对决定对应原论文。')
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
            'engine': engine, 'ai_key': self.ai_key.get().strip(), 'ai_model': ai_model,
            'keep_review': self.keep_review.get(), 'discovery_only': self.discovery_only.get(),
            'records': self.records_path.get(), 'decisions': self.decisions_path.get(),
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
            self.output.mkdir(parents=True, exist_ok=True)
            self.write('启用来源：' + '、'.join(sources))
            if values['springer']:
                self.write('Springer Nature key：用于已找到 DOI 的开放全文/JATS 获取')
            self.write('输出目录：' + str(self.output))
            command = [sys.executable, '-m', 'scrtool', 'harvest',
                       '--query', values['query'], '--sources', *sources,
                       '--limit', str(values['limit']), '--max-downloads', str(values['maximum']),
                       '--library-dir', str(ROOT / 'outputs' / 'paper_library'),
                       '--delay', '1', '-o', str(self.output)]
            if values['engine']:
                if values['engine'] == 1:
                    if values['ai_key']:
                        env['DEEPSEEK_API_KEY'] = values['ai_key']
                    endpoint, key_env = 'https://api.deepseek.com', 'DEEPSEEK_API_KEY'
                else:
                    endpoint, key_env = 'http://localhost:11434/v1', 'OLLAMA_API_KEY'
                config_path = self.output / 'screen_config.json'
                config_path.write_text(json.dumps({'base_url': endpoint, 'model': values['ai_model'],
                                                   'api_key_env': key_env}, ensure_ascii=False, indent=2), encoding='utf-8')
                command.extend(['--screen-engine', 'llm', '--screen-config', str(config_path)])
            if values['records']:
                command.extend(['--records', values['records']])
            if values['decisions']:
                command.extend(['--review-decisions', values['decisions']])
            if not values['keep_review']:
                command.append('--no-download-review')
            if values['discovery_only']:
                command.append('--no-download')
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            for line in process.stdout:
                self.write(self.progress_text(line.rstrip()))
            code = process.wait()
            summary_path = self.output / 'summary.json'
            statuses = {}
            if summary_path.exists():
                summary = json.loads(summary_path.read_text(encoding='utf-8'))
                self.write('\n检索记录：' + str(summary.get('records', 0)))
                self.write('来源错误：' + str(summary.get('search_errors', 0)))
                public_abstracts = summary.get('public_abstracts') or {}
                self.write('公开来源补摘要：新增 ' + str(public_abstracts.get('retrieved', 0)) +
                           '；缓存 ' + str(public_abstracts.get('cached', 0)))
                abstracts = summary.get('abstract_status') or {}
                self.write('摘要：已有 ' + str(abstracts.get('available', 0)) +
                           '；缺失 ' + str(abstracts.get('missing', 0)) +
                           '（权限不足 ' + str(abstracts.get('access_denied', 0)) +
                           '；请求失败 ' + str(abstracts.get('request_failed', 0)) + '）')
                self.write('共享论文库：' + str(summary.get('paper_library', '')))
                statuses = summary.get('download_status') or {}
                screened = summary.get('screening') or {}
                self.write('摘要筛选：' + '、'.join(f'{label} {screened.get("counts", {}).get(key, 0)}' for key, label in
                                                [('target', '相关'), ('review', '待核对'), ('non_target', '不相关')]))
                self.write('实际AI完成：' + str(screened.get('ai_completed', 0)) + '；AI错误：' + str(screened.get('ai_errors', 0)))
                self.write('核对清单：screening_records.csv；需要修正决定时填写 manual_review_template.csv')
                for status, count in statuses.items():
                    self.write(f'{status}: {count}')
            downloaded = sum(count for status, count in statuses.items()
                             if status in {'downloaded_pdf', 'cached_pdf', 'existing', 'downloaded_xml_only',
                                           'downloaded_jats_only'})
            if values['discovery_only']:
                self.write('\n筛选完成。本次未下载；可先核对清单，再载入题录与人工核对表继续下载。')
            elif downloaded:
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
            for entry in [self.springer, self.elsevier, self.openalex, self.ai_key]:
                self.root.after(0, self.clear_sensitive, entry)
            self.root.after(0, self.start.configure, {'state': 'normal'})

    @staticmethod
    def clear_sensitive(entry):
        state = entry.cget('state')
        entry.configure(state='normal')
        entry.delete(0, 'end')
        entry.configure(state=state)

    @staticmethod
    def progress_text(line):
        try:
            item = json.loads(line)
        except (ValueError, TypeError):
            return line
        if 'created_at' in item:
            return ''
        if 'screening_progress' in item:
            return f'筛选 {item["screening_progress"]}/{item["total"]}：{item.get("doi") or "无DOI"} → {item["decision"]}'
        if 'download_progress' in item:
            return f'获取全文 {item["download_progress"]}：{item.get("doi") or "无DOI"} → {item["status"]}'
        if 'public_abstract_batch' in item:
            return f'补摘要 {item["public_abstract_batch"]}：已查询 {item["processed"]}/{item["total"]}，累计新增 {item["retrieved"]}'
        if 'public_abstract_wait' in item:
            return f'公开摘要接口限流，等待 {item["seconds"]} 秒后重试一次。'
        if 'public_abstract_progress' in item:
            return f'补摘要：{item.get("doi") or "无DOI"} → {item["status"]}'
        if 'public_abstracts' in item:
            report = item['public_abstracts']
            return f'公开摘要获取完成：新增 {report["retrieved"]}，缓存 {report["cached"]}，仍需补充 {report["remaining"]}'
        return line

    def open_output(self):
        if self.output and self.output.exists():
            os.startfile(self.output)


def main():
    root = tk.Tk()
    DownloadWindow(root)
    root.mainloop()


if __name__ == '__main__':
    main()
