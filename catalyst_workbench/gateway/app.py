"""Chinese desktop interface for the local literature acquisition workspace."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench_paths import data_root
import engine
from rules import PROFILE_INFO


DECISIONS = {"target": "保留", "review": "待复核", "non_target": "排除"}


def readable(value):
    if value is None:
        return ""
    if isinstance(value, dict):
        return "\n".join(f"{key}：{readable(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return "；".join(readable(item) for item in value)
    return str(value)


def has_open_xml(record):
    return bool(record) and any(isinstance(candidate, dict) and engine.is_open_pmc_candidate(candidate)
                                for candidate in record.get("fulltext_candidates", []) or [])


def fulltext_access_hint(record):
    if not record:
        return "先选择论文，查看全文获取方式。XML 按钮仅用于已接入的 Europe PMC 开放全文。"
    local = Path(record.get("local_path") or "")
    if local.suffix.lower() == ".pdf" and local.is_file():
        return "已有本地 PDF，可完成筛选后进入第二板块扫描图片，无需再次下载 XML。"
    acquisition=record.get('pdf_acquisition',{})
    if acquisition.get('message'):
        return acquisition['message']+' 可点击顶部“自动获取 PDF → 进入第二板块”重试。'
    if has_open_xml(record):
        return "顶部按钮会自动尝试获取 PDF 并交给图片扫描；也可单独获取 Europe PMC XML。"
    platform = (record.get("route") or {}).get("publisher_group") or "该论文"
    if platform == "Elsevier":
        return "可自动查找 Elsevier 的公开 PDF，也可在顶部“出版社 API 设置 / 验证”填写官方密钥。API 全文是否可取需实际验证学校权限；学校全文 / Zotero 接收仍可使用。"
    return "保留论文后，点击顶部“自动获取 PDF → 进入第二板块”。程序会自动查找公开 PDF，保存并传给图片扫描。"


class LiteratureApp:
    def __init__(self, root: tk.Tk, output_root: Path, profile: str | None = None, *, paper_output_root=None, image_output_root=None):
        root.tk.call('tk','scaling',min(float(root.tk.call('tk','scaling')),1.5))
        self.root = root
        self.output_root = Path(output_root).resolve()
        base=data_root()
        self.paper_output_root=Path(paper_output_root or base/'论文综合结果').resolve()
        self.image_output_root=Path(image_output_root or base/'图片处理结果').resolve()
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.cancellable = True
        self.run = None
        self.records = {}
        self.job_done = None
        self.job_number = 0
        self.close_requested = False
        self.zotero_window = None
        self.api_window = None
        self.publisher_client = None
        self.controls = []
        self.record_controls = []
        self.profile_keys = list(PROFILE_INFO)
        self.profile_titles = [PROFILE_INFO[key]["title"] for key in self.profile_keys]
        self.profile_var = tk.StringVar()
        self.limit_var = tk.StringVar(value="10")
        self.source_vars = {
            "Crossref": tk.BooleanVar(value=True),
            "Europe PMC": tk.BooleanVar(value=True),
        }
        self.status_var = tk.StringVar(value="就绪。选择研究方向，或导入已有 DOI / 本地 PDF。")
        self.summary_var = tk.StringVar(value="尚未生成结果")
        self.description_var = tk.StringVar()
        self.fulltext_hint_var = tk.StringVar(value=fulltext_access_hint(None))
        self._build()
        preferred = profile or ("scr_ammonia" if "scr_ammonia" in PROFILE_INFO else self.profile_keys[0])
        if preferred not in PROFILE_INFO:
            raise ValueError("请选择已有研究方向。")
        self.profile_var.set(PROFILE_INFO[preferred]["title"])
        self._change_profile()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll)

    def _build(self):
        self.root.title("板块 1：文献获取与筛选 · 出版社 API / Zotero 接收")
        width = min(1260, max(900, self.root.winfo_screenwidth() - 60))
        height = min(920, max(650, self.root.winfo_screenheight() - 90))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(900, 650)
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=29, font=("Microsoft YaHei UI", 10))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(8, 5))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("Hint.TLabel", foreground="#546575")
        style.configure("Figure.TButton", foreground="white", background="#155e75", padding=(12, 7))
        style.map("Figure.TButton", background=[("active", "#0e7490"), ("disabled", "#d8e1e7")],
                  foreground=[("disabled", "#52616b")])

        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="板块 1：联网筛选 → 自动获取 PDF", style="Title.TLabel").pack(anchor="w")
        top_actions = ttk.Frame(outer)
        top_actions.pack(anchor="w", pady=(6, 4))
        self.figure_button = ttk.Button(top_actions, text="自动获取 PDF → 进入第二板块", command=self._open_figures,
                                       state="disabled", style="Figure.TButton")
        self.figure_button.pack(anchor="w")
        access_row=ttk.Frame(top_actions);access_row.pack(anchor='w',pady=(4,0))
        self.zotero_button = ttk.Button(access_row, text="学校全文 / Zotero 接收", command=self._open_zotero)
        self.zotero_button.pack(side='left')
        self.api_button=ttk.Button(access_row,text='出版社 API 设置 / 验证',command=self._open_api)
        self.api_button.pack(side='left',padx=(8,0));self.controls.append((self.api_button,'normal'))
        ttk.Label(
            outer,
            text="检索后保留论文，点击上方按钮：自动查找可获取的 PDF，下载成功后直接扫描图片。每篇获取状态会显示在列表中。",
            style="Hint.TLabel",
            wraplength=1150,
        ).pack(anchor="w", pady=(3, 9))

        search = ttk.LabelFrame(outer, text="1. 选择方向与来源", padding=10)
        search.pack(fill="x")
        row = ttk.Frame(search)
        row.pack(fill="x")
        ttk.Label(row, text="研究方向").pack(side="left")
        self.profile_combo = ttk.Combobox(
            row, textvariable=self.profile_var, values=self.profile_titles,
            state="readonly", width=45,
        )
        self.profile_combo.pack(side="left", padx=(8, 18))
        self.profile_combo.bind("<<ComboboxSelected>>", self._change_profile)
        self.controls.append((self.profile_combo, "readonly"))
        ttk.Label(row, text="每条检索词 / 每个来源最多").pack(side="left")
        self.limit_spin = ttk.Spinbox(row, from_=1, to=100, width=5, textvariable=self.limit_var)
        self.limit_spin.pack(side="left", padx=6)
        ttk.Label(row, text="篇（1–100）").pack(side="left")
        self.controls.append((self.limit_spin, "normal"))
        ttk.Label(search, textvariable=self.description_var, style="Hint.TLabel", wraplength=1130).pack(
            anchor="w", pady=(6, 5)
        )
        query_row = ttk.Frame(search)
        query_row.pack(fill="x")
        ttk.Label(query_row, text="检索词\n一行一条").pack(side="left", anchor="n", padx=(0, 10))
        self.query_text = tk.Text(query_row, height=3, wrap="word", font=("Microsoft YaHei UI", 10), undo=True)
        self.query_text.pack(side="left", fill="x", expand=True)
        self.controls.append((self.query_text, "normal"))
        source_row = ttk.Frame(search)
        source_row.pack(fill="x", pady=(7, 0))
        ttk.Label(source_row, text="联网检索来源：").pack(side="left")
        for name, variable in self.source_vars.items():
            check = ttk.Checkbutton(source_row, text=name, variable=variable)
            check.pack(side="left", padx=(0, 12))
            self.controls.append((check, "normal"))
        ttk.Label(source_row, text="来源表示检索入口；结果会另外识别出版平台。", style="Hint.TLabel").pack(side="left")
        action_row = ttk.Frame(search)
        action_row.pack(fill="x", pady=(8, 0))
        for title, command in (
            ("联网检索并初筛", self._search),
            ("导入 DOI", self._doi_dialog),
            ("导入本地 PDF 文件夹", self._local_pdfs),
            ("打开已保存结果", self._load_dialog),
        ):
            button = ttk.Button(action_row, text=title, command=command)
            button.pack(side="left", padx=(0, 7))
            self.controls.append((button, "normal"))
        self.cancel_button = ttk.Button(action_row, text="停止当前任务", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="right")

        ttk.Label(outer, textvariable=self.summary_var).pack(anchor="w", pady=(10, 5))
        panes = ttk.Panedwindow(outer, orient="vertical")
        panes.pack(fill="both", expand=True)
        result_frame = ttk.LabelFrame(panes, text="2. 选择一篇，查看筛选依据", padding=5)
        panes.add(result_frame, weight=3)
        columns = ("decision", "pdf_status", "focus", "branch", "title", "year", "publisher", "doi")
        self.tree = ttk.Treeview(result_frame, columns=columns, show="headings", height=8, selectmode="browse")
        specs = (
            ("decision", "判定", 76, False), ("focus", "重点关注", 94, False),
            ("pdf_status", "PDF 获取", 120, False),
            ("branch", "主题分支", 120, False), ("title", "论文标题", 430, True),
            ("year", "年份", 55, False), ("publisher", "出版平台", 120, False),
            ("doi", "DOI", 210, True),
        )
        for key, title, width, stretch in specs:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=45, stretch=stretch)
        yscroll = ttk.Scrollbar(result_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(result_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)
        self.tree.tag_configure("target", foreground="#176238")
        self.tree.tag_configure("review", foreground="#885a06")
        self.tree.tag_configure("non_target", foreground="#6f7378")
        self.tree.bind("<<TreeviewSelect>>", self._select_record)
        self.tree.bind("<Double-1>", lambda _event: self._open_article())

        detail_frame = ttk.LabelFrame(panes, text="3. 读依据，必要时人工修改判定", padding=7)
        panes.add(detail_frame, weight=3)
        text_frame = ttk.Frame(detail_frame)
        self.detail_text = tk.Text(
            text_frame, height=9, wrap="word", state="disabled",
            font=("Microsoft YaHei UI", 10), padx=7, pady=5,
        )
        detail_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.detail_text.yview)
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        self.detail_text.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        note_row = ttk.Frame(detail_frame)
        ttk.Label(note_row, text="人工备注").pack(side="left", padx=(0, 8))
        self.note_text = tk.Text(note_row, height=2, wrap="word", font=("Microsoft YaHei UI", 10), state="disabled")
        self.note_text.pack(side="left", fill="x", expand=True)
        button_row = ttk.Frame(detail_frame)
        # Reserve controls before giving the remaining height to evidence text.
        button_row.pack(side="bottom", fill="x", pady=(4, 0))
        for title, decision in (("人工：保留", "target"), ("人工：待复核", "review"), ("人工：排除", "non_target")):
            button = ttk.Button(button_row, text=title, command=lambda d=decision: self._save_review(d), state="disabled")
            button.pack(side="left", padx=(0, 5))
            self.record_controls.append(button)
        for title, command in (("打开论文网页", self._open_article), ("获取 PMC XML", self._download)):
            button = ttk.Button(button_row, text=title, command=command, state="disabled")
            button.pack(side="left", padx=(0, 5))
            self.record_controls.append(button)
            if command == self._download:
                self.xml_button = button
        self.folder_button = ttk.Button(button_row, text="打开结果文件夹", command=self._open_folder, state="disabled")
        self.folder_button.pack(side="right")
        ttk.Label(detail_frame, textvariable=self.fulltext_hint_var, style="Hint.TLabel",
                  wraplength=1120).pack(side="bottom", fill="x", pady=(4, 2))
        note_row.pack(side="bottom", fill="x", pady=6)
        text_frame.pack(fill="both", expand=True)
        ttk.Separator(outer).pack(fill="x", pady=(8, 5))
        ttk.Label(outer, textvariable=self.status_var, wraplength=1160, style="Hint.TLabel").pack(anchor="w")

    def _profile(self):
        title = self.profile_var.get()
        return next((key for key, info in PROFILE_INFO.items() if info["title"] == title), self.profile_keys[0])

    def _change_profile(self, _event=None):
        info = PROFILE_INFO[self._profile()]
        self.description_var.set(info.get("description", ""))
        self.query_text.delete("1.0", "end")
        queries = info.get("queries", [])
        self.query_text.insert("1.0", "\n".join(queries) if not isinstance(queries, str) else queries)

    def _progress(self, *args, **kwargs):
        parts = [readable(arg) for arg in args]
        if kwargs:
            parts.append(readable(kwargs))
        self.events.put(("progress", " ".join(parts)))

    def _set_busy(self, busy, cancellable=True):
        self.busy = busy
        self.cancellable = cancellable
        for control, idle_state in self.controls:
            control.configure(state="disabled" if busy else idle_state)
        self.cancel_button.configure(state="normal" if busy and cancellable else "disabled")
        self.tree.configure(selectmode="none" if busy else "browse")
        self._enable_record_controls()

    def _enable_record_controls(self):
        record = self._selected()
        available = bool(record) and not self.busy
        for button in self.record_controls:
            button.configure(state="normal" if available else "disabled")
        self.xml_button.configure(state="normal" if available and has_open_xml(record) else "disabled")
        self.fulltext_hint_var.set(fulltext_access_hint(record))
        self.note_text.configure(state="normal" if available else "disabled")
        self.folder_button.configure(state="normal" if self.run and not self.busy else "disabled")
        self.figure_button.configure(state="normal" if self.run and not self.busy else "disabled")

    def _open_figures(self):
        if not self.run or self.busy:
            return
        from pdf_fetch import selected_records
        if not selected_records(self.run):
            messagebox.showinfo('先选择保留论文','当前没有判定为“保留”的论文。请先完成检索和筛选，再自动获取 PDF。',parent=self.root)
            return
        def done(updated):
            self._show_run(updated)
            summary=updated.get('pdf_acquisition_summary',{})
            message=f"保留 {summary.get('selected',0)} 篇：新获取 {summary.get('downloaded',0)} 篇，已有 {summary.get('available',0)} 篇，未取得 {summary.get('failed',0)} 篇。"
            if summary.get('cancelled'):
                self.status_var.set(message+' 已停止；已获取的 PDF 会保留。再次点击可继续。')
            elif summary.get('ready',0):
                self._launch_figures()
                self.status_var.set(message+' 已进入第二板块单篇论文工作台；未取得的论文可在列表查看原因。')
            else:
                self.status_var.set(message+' 暂无可扫描 PDF。')
                messagebox.showinfo('本次尚未取得 PDF',message+'\n\n每篇原因见列表“PDF 获取”及下方详情。未打开空白图片工作台；可以更换论文或稍后重试。',parent=self.root)
        client=getattr(self,'publisher_client',None)
        options={'publisher_client':client} if client is not None else {}
        self._start_job('正在自动获取保留论文的 PDF；完成后进入文字、语义和图片工作台……',
                        lambda:engine.acquire_pdfs(self.run,progress=self._progress,cancel_event=self.cancel_event,**options),done)

    def _launch_figures(self):
        try:
            run_path = Path(self.run["run_dir"]) / "run.json"
            if not run_path.is_file():
                raise ValueError("尚未找到保存的筛选记录，请先完成一次筛选。")
            workspace = Path(__file__).resolve().parent.parent
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            runtime = pythonw if pythonw.is_file() else Path(sys.executable)
            args = [str(runtime), "-B", str(workspace / "paper_app.py"), "--screening-run", str(run_path),
                    "--output-root", str(self.paper_output_root),
                    "--image-output-root", str(self.image_output_root)]
            kwargs = {"cwd": str(workspace)}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.Popen(args, **kwargs)
            self.status_var.set("已进入第二板块：先选一篇论文，再整理文字、语义和图片证据。")
        except Exception as exc:
            messagebox.showerror("暂时无法进入论文证据工作台", str(exc), parent=self.root)

    def _start_job(self, description, function, on_done, cancellable=True):
        if self.busy:
            return
        self.cancel_event = threading.Event()
        self.job_done = on_done
        self.job_number += 1
        self.status_var.set(description)
        self._set_busy(True, cancellable)

        def worker():
            try:
                result = function()
                self.events.put(("result", result))
            except Exception as exc:
                self.events.put(("error", (str(exc), traceback.format_exc())))
            finally:
                self.events.put(("finished", None))

        threading.Thread(target=worker, name=f"literature-job-{self.job_number}", daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self.status_var.set(payload[:700])
                    for record_id,record in self.records.items():
                        if self.tree.exists(record_id):self.tree.set(record_id,'pdf_status',self._pdf_status(record))
                elif kind == "result":
                    if self.job_done and not self.close_requested:
                        try:
                            self.job_done(payload)
                        except Exception as exc:
                            self.status_var.set("结果已返回，但界面显示失败。请查看本次结果文件夹。")
                            messagebox.showerror("显示结果失败", str(exc), parent=self.root)
                elif kind == "error":
                    if not self.close_requested:
                        self._handle_error(*payload)
                elif kind == "finished":
                    if self.close_requested:
                        self.root.destroy()
                        return
                    self._set_busy(False)
                    self.job_done = None
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _handle_error(self, message, details):
        log_path = None
        try:
            self.output_root.mkdir(parents=True, exist_ok=True)
            log_path = self.output_root / f"界面错误_{datetime.now():%Y%m%d_%H%M%S}.txt"
            log_path.write_text(details, encoding="utf-8")
        except OSError:
            pass
        self.status_var.set("本次操作未完成；已有结果仍保留。")
        footer = f"\n\n本地错误记录：{log_path}" if log_path else ""
        messagebox.showerror("操作未完成", message + footer, parent=self.root)

    def _search(self):
        try:
            limit = int(self.limit_var.get())
            if not 1 <= limit <= 100:
                raise ValueError
        except ValueError:
            messagebox.showinfo("检查数量", "每条检索词、每个来源的数量请输入 1 到 100 的整数。", parent=self.root)
            return
        queries = [line.strip() for line in self.query_text.get("1.0", "end").splitlines() if line.strip()]
        providers = tuple(name for name, variable in self.source_vars.items() if variable.get())
        if not queries or not providers:
            messagebox.showinfo("补充检索条件", "请至少填写一条检索词，并选择一个检索来源。", parent=self.root)
            return
        if len(queries) > 10:
            messagebox.showinfo("检索词过多", "第一版每次支持 1–10 行检索词，请分批检索。", parent=self.root)
            return
        profile = self._profile()
        self._start_job(
            "正在联网检索；每次任务都会保存到独立结果文件夹……",
            lambda: engine.run_search(
                profile, queries=queries, limit=limit, providers=providers,
                output_root=self.output_root, progress=self._progress, cancel_event=self.cancel_event,
            ),
            self._show_run,
        )

    def _doi_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("导入已有 DOI")
        dialog.geometry("720x380")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="粘贴 DOI 或 DOI 链接，每行一条。系统将联网补充信息后按当前方向初筛。", wraplength=680).pack(
            anchor="w", padx=16, pady=(16, 8)
        )
        text = tk.Text(dialog, height=11, font=("Microsoft YaHei UI", 11), wrap="word")
        text.pack(fill="both", expand=True, padx=16)
        text.focus_set()
        row = ttk.Frame(dialog, padding=12)
        row.pack(fill="x")

        def submit():
            doi_text = text.get("1.0", "end").strip()
            if not doi_text:
                messagebox.showinfo("尚未填写", "请粘贴至少一个 DOI。", parent=dialog)
                return
            profile = self._profile()
            dialog.destroy()
            self._start_job(
                "正在识别 DOI 并获取文献信息……",
                lambda: engine.run_dois(
                    profile, doi_text, output_root=self.output_root,
                    progress=self._progress, cancel_event=self.cancel_event,
                ),
                self._show_run,
            )

        ttk.Button(row, text="开始导入并初筛", command=submit).pack(side="right")
        ttk.Button(row, text="取消", command=dialog.destroy).pack(side="right", padx=8)

    def _local_pdfs(self, folder=None):
        if self.busy:
            return
        if folder is None:
            folder = filedialog.askdirectory(title="选择放有论文 PDF 的文件夹（只在本地读取）", parent=self.root)
        if not folder:
            return
        profile = self._profile()
        self._start_job(
            "正在本地读取 PDF 并初筛；不会上传文件……",
            lambda: engine.run_local_pdfs(
                profile, folder, output_root=self.output_root,
                progress=self._progress, cancel_event=self.cancel_event,
            ),
            self._show_run,
        )

    def _load_dialog(self):
        path = filedialog.askdirectory(
            title="选择某一次任务的结果文件夹", parent=self.root,
            initialdir=str(self.output_root) if self.output_root.exists() else None,
        )
        if path:
            self.load_path(path)

    def load_path(self, path):
        self._start_job("正在读取已保存的结果……", lambda: engine.load_run(path), self._show_run, cancellable=False)

    def _show_run(self, run, preserve_selection=None):
        if not isinstance(run, dict):
            raise ValueError("返回结果不是预期的文献结果结构。")
        self.run = run
        self.records = {}
        self.tree.delete(*self.tree.get_children())
        counts = {"target": 0, "review": 0, "non_target": 0}
        for index, record in enumerate(run.get("records", [])):
            item_id = str(record.get("id", index))
            while item_id in self.records:
                item_id += "_"
            self.records[item_id] = record
            screen = record.get("screening") or {}
            route = record.get("route") or {}
            decision = record.get("manual_decision") or record.get("effective_decision") or screen.get("decision", "review")
            counts[decision] = counts.get(decision, 0) + 1
            focus = screen.get("priority_focus")
            if isinstance(focus, bool):
                focus = "优先关注" if focus else ""
            values = (
                DECISIONS.get(decision, decision), self._pdf_status(record), readable(focus), readable(screen.get("topic_branch")),
                record.get("title") or "（未取得标题）", record.get("year") or "",
                route.get("publisher_group") or record.get("publisher") or "待识别", record.get("doi") or "",
            )
            self.tree.insert("", "end", iid=item_id, values=values, tags=(decision,))
        self.summary_var.set(
            f"{run.get('profile_title') or PROFILE_INFO.get(run.get('profile'), {}).get('title', '当前结果')}"
            f"　｜　共 {len(self.records)} 篇　保留 {counts['target']}　待复核 {counts['review']}　排除 {counts['non_target']}"
            +(f"　｜　可扫描 PDF {sum(bool(r.get('local_path')) and Path(r['local_path']).is_file() for r in run['records'] if (r.get('manual_decision') or r.get('effective_decision')) == 'target')} 篇"
              if run.get('pdf_acquisition_summary') or run.get('zotero_sync_summary') else '')
        )
        chosen = preserve_selection if preserve_selection in self.records else next(iter(self.records), None)
        if chosen is not None:
            self.tree.selection_set(chosen)
            self.tree.focus(chosen)
            self.tree.see(chosen)
        self._select_record()
        errors = run.get("errors") or []
        cancelled = self.cancel_event.is_set()
        if cancelled:
            status = "任务已停止，已取得的结果已显示。"
        elif errors:
            status = f"已完成并保存结果；有 {len(errors)} 条来源或文献处理提示，请注意检索覆盖范围。"
        else:
            status = "已完成并保存结果。选择一篇查看依据，再决定是否需要人工修改。"
        self.status_var.set(status)
        if errors:
            messagebox.showwarning(
                "部分信息未取得",
                "已有结果仍可核对；部分来源或文献未能完成：\n\n"
                + "\n\n".join(readable(item) for item in errors[:6])[:2500]
                + ("\n\n更多提示保存在本次结果文件夹中。" if len(errors) > 6 else ""),
                parent=self.root,
            )

    def _selected(self):
        selected = self.tree.selection()
        return self.records.get(selected[0]) if selected else None

    @staticmethod
    def _pdf_status(record):
        from pdf_fetch import status_label
        info = record.get('zotero_transfer', {})
        if info and not record.get('local_path'):
            return info.get('label') or status_label(record)
        return status_label(record)

    def _select_record(self, _event=None):
        record = self._selected()
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.note_text.configure(state="normal")
        self.note_text.delete("1.0", "end")
        if record:
            screen = record.get("screening") or {}
            route = record.get("route") or {}
            automatic = screen.get("decision", "review")
            manual = record.get("manual_decision")
            sections = [
                ("标题", record.get("title")),
                ("DOI", record.get("doi") or "未取得 DOI；不要仅因此排除论文"),
                ("PDF 获取状态", self._pdf_status(record)),
                ("PDF 获取说明", record.get('pdf_acquisition',{}).get('message')),
                ("PDF 获取来源", record.get('pdf_acquisition',{}).get('source_url')),
                ("Zotero 接收说明", record.get('zotero_transfer',{}).get('message')),
                ("自动保存位置", record.get('local_path')),
                ("自动初筛", DECISIONS.get(automatic, automatic)),
                ("人工判定", DECISIONS.get(manual, manual) if manual else "尚未人工判定"),
                ("判断理由", screen.get("reason")),
                ("命中关键词 / 概念组", screen.get("matched_groups")),
                ("待补充的概念组", screen.get("missing_groups")),
                ("概念组覆盖比例（不是准确率）", screen.get("coverage")),
                ("文章类型", screen.get("article_type")),
                ("主题分支", screen.get("topic_branch")),
                ("优先关注", screen.get("priority_focus")),
                ("筛选证据", screen.get("evidence")),
                ("摘要 / 本地提取内容", record.get("abstract") or "未取得摘要；需要查看原文。"),
                ("文字来源", record.get("abstract_origin")),
                ("需要注意", record.get("warnings")),
                ("检索来源", record.get("sources")),
                ("期刊", record.get("journal")),
                ("出版平台", route.get("publisher_group") or record.get("publisher")),
                ("访问说明", route.get("access_hint")),
                ("论文入口", route.get("official_entry")),
            ]
            content = "\n\n".join(f"【{label}】\n{readable(value)}" for label, value in sections if value not in (None, "", [], {}))
            self.detail_text.insert("1.0", content)
            self.note_text.insert("1.0", record.get("manual_note") or "")
        else:
            self.detail_text.insert("1.0", "完成检索或导入后，点击上方的一篇论文，即可查看摘要、筛选依据和访问方式。")
        self.detail_text.configure(state="disabled")
        self._enable_record_controls()

    def _save_review(self, decision):
        record = self._selected()
        if not record or not self.run or self.busy:
            return
        run = self.run
        record_id = record["id"]
        selected_id = self.tree.selection()[0]
        note = self.note_text.get("1.0", "end").strip()

        def save():
            result = engine.save_review(run, record_id, decision, note=note)
            return result if isinstance(result, dict) and "records" in result else run

        def done(updated_run):
            self._show_run(updated_run, preserve_selection=selected_id)
            self.status_var.set(f"已保存人工判定：{DECISIONS[decision]}。对应导出结果已更新。")

        self._start_job("正在保存人工判定……", save, done, cancellable=False)

    def _open_zotero(self):
        from zotero_ui import ZoteroWindow
        previous = getattr(self, 'zotero_window', None)
        if previous is not None and not previous.closed:
            previous.refresh()
            previous.window.lift()
        else:
            self.zotero_window = ZoteroWindow(self)

    def _open_api(self):
        if self.busy:return
        from publisher_api_ui import PublisherAPIWindow
        previous=getattr(self,'api_window',None)
        if previous is not None and not previous.closed:
            previous.refresh();previous.window.lift()
        else:self.api_window=PublisherAPIWindow(self)

    def _open_article(self):
        if self.busy:
            return
        record = self._selected()
        if not record:
            return
        url = (record.get("route") or {}).get("official_entry") or ""
        if not url and record.get("doi"):
            url = "https://doi.org/" + record["doi"]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            messagebox.showinfo("尚无论文网页", "这篇文献暂时没有可用的网页入口，请根据标题查找原文。", parent=self.root)
            return
        try:
            webbrowser.open(url)
            self.status_var.set("已请求浏览器打开论文网页。全文访问取决于该平台的开放状态和您的机构权限。")
        except Exception as exc:
            messagebox.showerror("无法打开浏览器", str(exc), parent=self.root)

    def _download(self):
        if self.busy:
            return
        record = self._selected()
        if not record or not self.run:
            return
        if not has_open_xml(record):
            message = fulltext_access_hint(record)
            self.status_var.set(message)
            messagebox.showinfo("当前全文获取方式", message, parent=self.root)
            return
        run, record_id = self.run, record["id"]

        def done(path):
            self.status_var.set(f"开放全文已保存：{path}")
            messagebox.showinfo("开放全文已保存", f"保存位置：\n{path}\n\n这是文献全文文件，尚未进行实验数据提取。", parent=self.root)

        self._start_job(
            "正在检查开放全文入口并获取 XML……",
            lambda: engine.download_open_fulltext(run, record_id, progress=self._progress),
            done, cancellable=False,
        )

    def _open_folder(self):
        if not self.run:
            return
        folder = Path(self.run.get("run_dir") or self.output_root)
        if not folder.is_dir():
            messagebox.showinfo("结果文件夹不存在", str(folder), parent=self.root)
            return
        try:
            os.startfile(str(folder))
        except OSError as exc:
            messagebox.showerror("无法打开文件夹", str(exc), parent=self.root)

    def _cancel(self):
        if self.busy and self.cancellable:
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.status_var.set("已请求停止。当前网络请求结束或超时后，将保留已取得的结果。")

    def _close(self):
        if self.busy:
            self.close_requested = True
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.status_var.set("正在结束当前操作并保存已有结果，完成后会自动关闭。")
        else:
            self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="多来源文献获取与筛选")
    parser.add_argument("--output-root", type=Path, default=data_root() / "筛选结果")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--load-run", type=Path)
    inputs.add_argument("--pdf-folder", type=Path, help="打开后只在本地导入该文件夹中的 PDF 并筛选")
    parser.add_argument("--profile", choices=list(PROFILE_INFO), default="scr_ammonia", help="研究方向")
    parser.add_argument('--paper-output-root',type=Path,help='本次流程的单篇论文输出目录')
    parser.add_argument('--image-output-root',type=Path,help='本次流程的图片输出目录')
    parser.add_argument("--smoke", action="store_true", help="创建并隐藏界面，完成本地启动检查后退出")
    args = parser.parse_args(argv)
    root = tk.Tk()
    if args.smoke:
        root.withdraw()
    app = LiteratureApp(root, args.output_root, profile=args.profile, paper_output_root=args.paper_output_root, image_output_root=args.image_output_root)
    if args.pdf_folder:
        for variable in app.source_vars.values():
            variable.set(False)
    if args.smoke:
        result = {"gui": "ok", "profile": app._profile(), "network_called": False}
        try:
            if args.pdf_folder:
                run = engine.run_local_pdfs(app._profile(), args.pdf_folder, output_root=args.output_root)
                app._show_run(run)
                verified = 0
                for record in run.get("records", []):
                    path = Path(record["local_path"])
                    with path.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    if digest != record.get("local_sha256"):
                        raise ValueError("本地导入的 PDF 哈希不匹配：" + path.name)
                    verified += 1
                result.update(mode=run.get("mode"), records=len(app.records), local_pdfs_verified=verified,
                              run_path=str(Path(run["run_dir"]) / "run.json"))
            elif args.load_run:
                app._show_run(engine.load_run(args.load_run))
                result.update(records=len(app.records), run_path=str(Path(app.run["run_dir"]) / "run.json"))
            app._set_busy(False)
            root.update_idletasks()
            root.update()
        finally:
            root.destroy()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.load_run:
        root.after(150, lambda: app.load_path(args.load_run))
    elif args.pdf_folder:
        root.after(150, lambda: app._local_pdfs(args.pdf_folder))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
