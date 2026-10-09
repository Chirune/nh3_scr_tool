"""Small local Zotero handoff window for the literature gateway."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser

import engine
from pdf_fetch import selected_records
from zotero_bridge import LocalZotero, BridgeError, sync_run


class ZoteroWindow:
    def __init__(self, parent):
        self.parent = parent
        self.window = tk.Toplevel(parent.root)
        self.window.title('学校全文 → Zotero → 自动接收 PDF')
        self.window.geometry('900x720')
        self.window.minsize(710, 590)
        self.window.transient(parent.root)
        self.closed = False
        self.timer = None
        self.job_active = False
        self.watch = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='点击“检测 Zotero 连接”。程序只连接本机，不需要学校密码。')
        outer = ttk.Frame(self.window, padding=18)
        outer.pack(fill='both', expand=True)
        bottom = ttk.Frame(outer)
        bottom.pack(side='bottom', fill='x')
        ttk.Label(outer, text='把有权限阅读的论文接到图片识别', style='Title.TLabel').pack(anchor='w')
        steps = ('板块 1 保留论文 → 浏览器学校登录 → 点 Zotero 保存正文 → 本窗口接收。\n'
                 '请保存到“我的文库”，确认条目下有 PDF；具体步骤见底部“详细操作说明”。')
        ttk.Label(outer, text=steps, wraplength=780, justify='left').pack(fill='x', pady=12)
        row = ttk.Frame(outer)
        row.pack(fill='x', pady=4)
        for text, command in [('检测 Zotero 连接', self.check), ('打开选中论文网页', parent._open_article)]:
            ttk.Button(row, text=text, command=command).pack(side='left', padx=(0, 7))
        self.receive_button = ttk.Button(outer, text='接收保留论文的 PDF', command=self.sync)
        self.receive_button.pack(anchor='w', pady=4)
        ttk.Checkbutton(outer, text='自动接收（每 8 秒检查当前保留论文；关闭此窗口即停止）',
                        variable=self.watch, command=self.toggle_watch).pack(anchor='w', pady=8)
        ttk.Label(outer, textvariable=self.status, wraplength=780, justify='left').pack(fill='x', pady=6)
        frame = ttk.Frame(outer)
        frame.pack(fill='both', expand=True, pady=5)
        self.results = tk.Text(frame, wrap='word', height=7, font=('Microsoft YaHei UI', 10), state='disabled')
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.results.yview)
        self.results.configure(yscrollcommand=scroll.set)
        self.results.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        row = ttk.Frame(bottom)
        row.pack(fill='x', pady=(8, 0))
        self.enter_button = ttk.Button(row, text='进入第二板块（已接收的 PDF）', command=self.enter_figures,
                                      style='Figure.TButton')
        self.enter_button.pack(side='left')
        ttk.Button(row, text='详细操作说明', command=self.guide).pack(side='right')
        ttk.Label(bottom, text='本版接收“我的文库”中已保存到本机的正文 PDF。它不会代替学校登录，也不会自动批量访问出版社。',
                  style='Hint.TLabel', wraplength=780).pack(fill='x', pady=(8, 0))
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()

    def _finish(self, result, done):
        self.job_active = False
        if self.closed:
            return
        if result.get('error'):
            self.watch.set(False)
            self.status.set(result['error'])
        else:
            done(result['value'])

    def _job(self, label, worker, done):
        if self.closed:
            return
        if self.parent.busy:
            self.status.set('板块 1 正在处理其他任务，完成后再接收。')
            return
        self.job_active = True
        self.status.set(label)
        def work():
            try:
                return {'value': worker()}
            except (BridgeError, OSError, ValueError) as exc:
                return {'error': str(exc)}
            except Exception as exc:
                return {'error': f'接收未完成（{type(exc).__name__}），已保存的文件保留。请重新检测连接。'}
        self.parent._start_job(label, work, lambda result: self._finish(result, done))

    def check(self):
        self._job('正在检测本机 Zotero……', lambda: LocalZotero().check(),
                  lambda info: self.status.set(info['message']))

    def sync(self):
        run = self.parent.run
        if not run or not selected_records(run):
            self.status.set('先在板块 1 检索或导入 DOI，并把需要的论文判定为“保留”。')
            return
        selected = self.parent.tree.selection()
        selected_id = selected[0] if selected else None
        def done(updated):
            self.parent._show_run(updated, preserve_selection=selected_id)
            summary = updated['zotero_sync_summary']
            self.status.set(f"本轮新接收 {summary['received']} 篇；保留论文中可用 PDF {summary['ready']} 篇；待处理 {summary['waiting']} 篇。")
            if summary['cancelled']:
                self.watch.set(False)
                self.status.set(self.status.get() + ' 已停止自动接收。')
            self.parent.status_var.set(self.status.get())
            self.refresh()
        self._job('正在从本机 Zotero 匹配保留论文及 PDF……',
                  lambda: sync_run(run, cancel_event=self.parent.cancel_event,
                                   progress=self.parent._progress, persist=engine._save), done)

    def refresh(self):
        lines = []
        for record in selected_records(self.parent.run or {}):
            info = record.get('zotero_transfer', {})
            state = '已有 PDF' if record.get('local_path') and Path(record['local_path']).is_file() else info.get('label', '等待检查')
            lines.append(f"{state}｜{record.get('doi') or '缺少 DOI'}\n{record.get('title', '')}\n{info.get('message', '')}")
        self.results.configure(state='normal')
        self.results.delete('1.0', 'end')
        self.results.insert('1.0', '\n\n'.join(lines) or '当前没有保留论文。先在板块 1 检索，或导入刚刚保存那篇论文的 DOI。')
        self.results.configure(state='disabled')

    def toggle_watch(self):
        if self.timer is not None:
            self.window.after_cancel(self.timer)
            self.timer = None
        if self.watch.get():
            self._tick()

    def _tick(self):
        self.timer = None
        if self.closed or not self.watch.get():
            return
        if not self.parent.busy:
            self.sync()
        self.timer = self.window.after(8000, self._tick)

    def enter_figures(self):
        if self.parent.busy:
            self.status.set('本轮接收完成后，再进入第二板块。')
            return
        ready = [r for r in selected_records(self.parent.run or {}) if r.get('local_path') and Path(r['local_path']).is_file()]
        if not ready:
            self.status.set('还没有收到可扫描 PDF；请先检查 Zotero 条目下面是否有正文附件，再点击接收。')
            return
        self.watch.set(False)
        self.toggle_watch()
        self.parent._launch_figures()

    def guide(self):
        path = Path(__file__).resolve().parents[2] / 'docs/workbench/tutorial.html'
        if path.is_file():
            webbrowser.open(path.as_uri())
        else:
            messagebox.showinfo('连接方式', '在 Zotero 设置 → 高级中允许其他本机程序通信。浏览器保存论文后，本窗口按 DOI 接收已下载的正文 PDF。', parent=self.window)

    def close(self):
        self.closed = True
        self.watch.set(False)
        if self.timer is not None:
            self.window.after_cancel(self.timer)
        if self.job_active:
            self.parent.cancel_event.set()
        self.window.destroy()
