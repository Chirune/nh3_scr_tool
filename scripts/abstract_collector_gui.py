"""Standalone stage-one desktop app; all inputs are local user-supplied files."""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrtool.abstract_import import SUPPORTED, export_collection


class CollectorWindow:
    def __init__(self, root):
        self.root, self.paths, self.rows = root, [], []
        self.events = queue.Queue()
        self.output = None
        root.title('NH₃-SCR 摘要采集与论文初筛 0.2')
        root.geometry('1020x750')
        root.minsize(830, 620)
        ttk.Label(root, text='第一步：收集摘要并筛选论文', font=('Microsoft YaHei UI', 17)).pack(anchor='w', padx=20, pady=(16, 6))
        ttk.Label(root, text='浏览器按钮采集 JSON / Zotero 导出 CSL JSON、RIS / 保存的 HTML → 导入 → 初筛清单',
                  wraplength=950).pack(anchor='w', padx=20)
        tools = ttk.Frame(root)
        tools.pack(fill='x', padx=20, pady=12)
        ttk.Button(tools, text='添加文件', command=self.add_files).pack(side='left')
        ttk.Button(tools, text='添加文件夹', command=self.add_folder).pack(side='left', padx=8)
        ttk.Button(tools, text='清空输入', command=self.clear).pack(side='left')
        self.run_button = ttk.Button(tools, text='整理摘要并初筛', command=self.begin)
        self.run_button.pack(side='right')
        self.inputs = tk.Listbox(root, height=4)
        self.inputs.pack(fill='x', padx=20)
        dest = ttk.Frame(root)
        dest.pack(fill='x', padx=20, pady=10)
        ttk.Label(dest, text='输出位置：').pack(side='left')
        self.dest = tk.StringVar(value=str(Path.home() / 'Documents' / 'NH3-SCR 摘要结果'))
        ttk.Entry(dest, textvariable=self.dest).pack(side='left', fill='x', expand=True)
        ttk.Button(dest, text='选择', command=self.choose_output).pack(side='left', padx=8)
        self.open_button = ttk.Button(dest, text='打开结果', command=self.open_result, state='disabled')
        self.open_button.pack(side='left')
        self.status = tk.StringVar(value='等待添加文件。程序无需 Elsevier API，规则初筛结果需要人工核对。')
        ttk.Label(root, textvariable=self.status, wraplength=950).pack(fill='x', padx=20, pady=(0, 8))
        frame = ttk.Frame(root)
        frame.pack(fill='both', expand=True, padx=20)
        self.table = ttk.Treeview(frame, columns=('decision', 'doi', 'title', 'length'), show='headings', height=10)
        for key, label, width in [('decision', '初筛', 85), ('doi', 'DOI', 230), ('title', '论文标题', 530), ('length', '摘要字符', 85)]:
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=70)
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        self.table.bind('<<TreeviewSelect>>', self.preview)
        self.detail = tk.Text(root, height=9, wrap='word', state='disabled')
        self.detail.pack(fill='x', padx=20, pady=10)
        ttk.Label(root, text='输出包含原始摘要、来源、初筛原因及人工核对表。缺摘要不会直接排除；此版本不提取性能数据。').pack(anchor='w', padx=20, pady=(0, 12))
        root.after(100, self.poll)

    def add_files(self):
        paths = filedialog.askopenfilenames(title='选择摘要采集文件或 Zotero 导出文件',
                  filetypes=[('支持的文件', '*.html *.htm *.json *.ris *.csv'), ('所有文件', '*.*')])
        self.add(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title='选择包含 HTML 或摘要导出文件的文件夹')
        if folder:
            self.add(str(p) for p in sorted(Path(folder).rglob('*')) if p.is_file() and p.suffix.lower() in SUPPORTED)

    def add(self, paths):
        for path in paths:
            if path not in self.paths:
                self.paths.append(path)
                self.inputs.insert('end', path)
        self.status.set(f'已选择 {len(self.paths)} 个文件。')

    def clear(self):
        self.paths.clear()
        self.inputs.delete(0, 'end')
        self.status.set('输入已清空。')

    def choose_output(self):
        folder = filedialog.askdirectory(title='选择结果保存位置')
        if folder:
            self.dest.set(folder)

    def begin(self):
        if not self.paths:
            messagebox.showinfo('先添加文件', '先添加浏览器采集 JSON、Zotero 导出文件或保存的 HTML。')
            return
        if not self.dest.get().strip():
            messagebox.showinfo('选择输出位置', '请填写结果保存位置。')
            return
        output = Path(self.dest.get()) / ('摘要采集_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        self.run_button.configure(state='disabled')
        self.status.set('正在读取摘要、核对重复 DOI 并进行规则初筛……')
        threading.Thread(target=self.run, args=(list(self.paths), output), daemon=True).start()

    def run(self, paths, output):
        try:
            rows, report = export_collection(paths, output)
            self.events.put(('done', (rows, report, output)))
        except Exception as exc:
            self.events.put(('error', str(exc)))

    def poll(self):
        try:
            kind, value = self.events.get_nowait()
        except queue.Empty:
            self.root.after(100, self.poll)
            return
        self.run_button.configure(state='normal')
        if kind == 'error':
            self.status.set('读取失败，请检查输入。')
            messagebox.showerror('未完成整理', value)
        else:
            self.rows, report, self.output = value
            for child in self.table.get_children():
                self.table.delete(child)
            for i, row in enumerate(self.rows):
                self.table.insert('', 'end', iid=str(i), values=(row['decision_label'], row['doi'], row['title'], len(row['abstract'])))
            counts = report['counts']
            self.status.set(f"完成：{report['records']} 篇，{report['abstracts']} 篇有摘要；相关 {counts['target']}、待核对 {counts['review']}、不相关 {counts['non_target']}；文件错误 {report['import_errors']}。")
            self.open_button.configure(state='normal')
        self.root.after(100, self.poll)

    def preview(self, _event=None):
        selected = self.table.selection()
        if not selected:
            return
        row = self.rows[int(selected[0])]
        value = f"{row['title']}\nDOI：{row['doi']}\n来源：{row['abstract_source']}\n初筛原因：{row['reason']}\n\n{row['abstract'] or '未取得摘要，保留待核对。'}"
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', value)
        self.detail.configure(state='disabled')

    def open_result(self):
        if self.output:
            os.startfile(self.output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', nargs='+', help='local input files, for repeatable verification')
    parser.add_argument('--output')
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    if args.batch:
        if not args.output:
            parser.error('--batch requires --output')
        _rows, report = export_collection(args.batch, args.output)
        if sys.stdout:
            print(json.dumps(report, ensure_ascii=False))
        return
    root = tk.Tk()
    if args.smoke_test:
        root.withdraw()
    CollectorWindow(root)
    if args.smoke_test:
        root.after(500, root.destroy)
    root.mainloop()


if __name__ == '__main__':
    main()
