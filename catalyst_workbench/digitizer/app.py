"""Local Chinese desktop interface for auditable figure digitisation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import threading
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from digitize import validate_calibration, trace_color_curve
from session import (
    new_session, load_session, set_calibration, add_points,
    delete_last_point, export_session, save_session,
)


class DigitizerApp:
    def __init__(self, root: tk.Tk, output_root: str | None = None, auto_read_on_load: bool = False):
        self.root = root
        # Keep the complete workbench usable on Windows displays reporting
        # very large Tk point scaling; scrollable controls remain available.
        root.tk.call('tk', 'scaling', min(float(root.tk.call('tk', 'scaling')), 1.5))
        self.output_root = output_root
        self.session = None
        self.image = None
        self.tk_image = None
        self.cached_render = None
        self.cached_render_key = None
        self.scale = 1.0
        self.render_scale_x = 1.0
        self.render_scale_y = 1.0
        self.fit_view = True
        self.mouse_mode = "point"
        self.axis_marks = {}
        self.preview_points = []
        self.preview_warnings = []
        self.preview_trace_run = None
        self.trace_generation = 0
        self.color = None
        self.drag_start = None
        self.drag_box = None
        self.busy = False
        self.job_queue = queue.Queue()
        self.configure_after = None
        self.loading_controls = True
        self.auto_read_on_load = bool(auto_read_on_load)

        root.title("论文图片自动读数 · 本地工作台")
        root.geometry("1440x930")
        root.minsize(1100, 750)
        root.configure(bg="#f3f6fa")
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", padding=(8, 5))
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Accent.TButton", foreground="white", background="#155e75")
        style.map("Accent.TButton", background=[("active", "#0e7490")])
        style.configure("Treeview", rowheight=26)
        style.configure("Muted.TLabel", foreground="#546477")

        self.page_var = tk.IntVar(value=1)
        self.mode_var = tk.StringVar(value="xy")
        self.x_name = tk.StringVar(value="横轴指标")
        self.x_unit = tk.StringVar(value="")
        self.y_name = tk.StringVar(value="纵轴指标")
        self.y_unit = tk.StringVar(value="")
        self.x_scale = tk.StringVar(value="线性")
        self.y_scale = tk.StringVar(value="线性")
        self.series_var = tk.StringVar(value="系列1（待确认）")
        self.sample_var = tk.StringVar(value="")
        self.category_var = tk.StringVar(value="")
        self.figure_var = tk.StringVar(value="")
        self.doi_var = tk.StringVar(value="")
        self.reviewed_var = tk.BooleanVar(value=False)
        self.tolerance_var = tk.IntVar(value=45)
        self.step_var = tk.IntVar(value=5)
        self.status_var = tk.StringVar(value="先打开图片或 PDF。PDF 请选择论文中的实际页码。")
        self.file_var = tk.StringVar(value="尚未导入图片")
        self.cal_status = tk.StringVar(value="先点击上方“一键识别并生成数值表”。需要修正时可手动标定。")
        self.color_var = tk.StringVar(value="尚未选色")
        self.point_count = tk.StringVar(value="尚无读数")
        self.mark_texts = {key: tk.StringVar(value="未选") for key in ("X1", "X2", "Y1", "Y2")}

        header = tk.Frame(root, bg="#123f53", padx=18, pady=12)
        header.pack(fill="x")
        tk.Label(header, text="论文图片 → 可核对的数值表", fg="white", bg="#123f53",
                 font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        tk.Label(header, text="① 导入图页  →  ② 自动识别坐标和数据  →  ③ 对照原图核对  →  ④ 查看数值表",
                 fg="#d5e9f0", bg="#123f53", font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(4, 0))
        tk.Label(root, text="图像读数是近似值，原文数据优先。全程本地处理；支持尝试曲线、散点、竖柱、分组柱、横条及组合图自动拆分。",
                 bg="#fff7df", fg="#775315", anchor="w", padx=15, pady=7).pack(fill="x")

        body = ttk.Panedwindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=8)
        left_outer = ttk.Frame(body, width=385)
        right = ttk.Frame(body)
        body.add(left_outer, weight=0)
        body.add(right, weight=1)

        controls_canvas = tk.Canvas(left_outer, width=380, highlightthickness=0, bg="#f3f6fa")
        controls_scroll = ttk.Scrollbar(left_outer, orient="vertical", command=controls_canvas.yview)
        controls_canvas.configure(yscrollcommand=controls_scroll.set)
        controls_scroll.pack(side="right", fill="y")
        controls_canvas.pack(side="left", fill="both", expand=True)
        left = ttk.Frame(controls_canvas, padding=(0, 0, 8, 0))
        controls_window = controls_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>", lambda _e: controls_canvas.configure(scrollregion=controls_canvas.bbox("all")))
        controls_canvas.bind("<Configure>", lambda e: controls_canvas.itemconfigure(controls_window, width=e.width))

        self._make_controls(left)

        tools_row = ttk.Frame(right)
        tools_row.pack(fill="x", pady=(0, 5))
        ttk.Button(tools_row, text="适合窗口", command=self.fit).pack(side="left")
        ttk.Button(tools_row, text="原图 100%", command=self.actual_size).pack(side="left", padx=4)
        ttk.Button(tools_row, text="放大 ＋", command=lambda: self.zoom(1.3)).pack(side="left", padx=4)
        ttk.Button(tools_row, text="缩小 −", command=lambda: self.zoom(1 / 1.3)).pack(side="left")
        ttk.Button(tools_row, text="查看本页文字", command=self.show_page_text).pack(side="right")
        ttk.Label(right, textvariable=self.file_var, style="Muted.TLabel", wraplength=930).pack(anchor="w", pady=(0, 4))

        automatic_row = ttk.Frame(right)
        automatic_row.pack(fill="x", pady=(0, 6))
        self.auto_button = ttk.Button(automatic_row, text="一键识别并生成数值表", command=self.auto_read,
                                      style="Accent.TButton")
        self.auto_button.pack(side="left")
        ttk.Label(automatic_row, text="自动读轴、提取候选点；完成后请核对。", style="Muted.TLabel",
                  wraplength=420).pack(side="left", padx=8)

        export_row = ttk.Frame(right)
        export_row.pack(fill="x", pady=(0, 6))
        ttk.Button(export_row, text="导出数值表与证据", command=self.export,
                   style="Accent.TButton").pack(side="left")
        ttk.Button(export_row, text="打开导出文件夹", command=self.open_output).pack(side="left", padx=6)
        ttk.Button(export_row, text="查看各子图结果", command=self.open_panel_results).pack(side="left", padx=6)
        review_row = ttk.Frame(right)
        review_row.pack(fill="x", pady=(0, 6))
        self.series_review_button = ttk.Button(review_row, text="逐系列核对：原图与数值", command=self.open_series_review)
        self.series_review_button.pack(side="left")
        ttk.Label(review_row, text="识别成功后可按系列核对；修正后请再次导出。", style="Muted.TLabel", wraplength=380).pack(side="left", padx=8)

        display = ttk.Frame(right)
        display.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(display, bg="#dfe6ee", highlightthickness=0, cursor="crosshair")
        yscroll = ttk.Scrollbar(display, orient="vertical", command=self.canvas.yview)
        xscroll = ttk.Scrollbar(display, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
        display.columnconfigure(0, weight=1)
        display.rowconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Configure>", self.on_canvas_configure)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-int(e.delta / 120), "units"))
        self.canvas.bind("<Control-MouseWheel>", lambda e: self.zoom(1.15 if e.delta > 0 else 1 / 1.15))

        table_top = ttk.Frame(right)
        table_top.pack(fill="x", pady=(8, 3))
        ttk.Label(table_top, textvariable=self.point_count).pack(side="left")
        ttk.Button(table_top, text="删除最后一个点", command=self.remove_point).pack(side="right")
        table_frame = ttk.Frame(right)
        table_frame.pack(fill="x")
        columns = ("number", "x", "y", "series", "category", "method")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=6)
        for key, title, width in (
            ("number", "序号", 45), ("x", "X / 柱位置", 105), ("y", "Y", 105),
            ("series", "系列", 175), ("category", "类别", 110), ("method", "取点方式", 100),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=40, stretch=key == "series")
        table_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=table_scroll.set)
        self.tree.pack(side="left", fill="x", expand=True)
        table_scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.focus_selected_point)

        status = tk.Label(root, textvariable=self.status_var, anchor="w", justify="left", wraplength=1350,
                          bg="#e7eff5", fg="#17394a", padx=12, pady=8)
        status.pack(fill="x")
        root.bind("<Escape>", lambda _e: self.set_mouse_mode("point"))
        for var in (self.x_name, self.x_unit, self.y_name, self.y_unit, self.x_scale, self.y_scale):
            var.trace_add("write", self.axis_field_changed)
        for var in (self.figure_var, self.doi_var, self.series_var, self.sample_var, self.category_var):
            var.trace_add("write", self.context_changed)
        for var in (self.tolerance_var, self.step_var):
            var.trace_add("write", self.trace_settings_changed)
        self.notes_box.bind("<<Modified>>", self.notes_changed)
        self.loading_controls = False
        self.draw()

    def _make_controls(self, parent):
        f = ttk.LabelFrame(parent, text="1  导入", padding=8)
        f.pack(fill="x", pady=(0, 7))
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Button(row, text="打开图片 / PDF", command=self.open_source, style="Accent.TButton").pack(side="left")
        ttk.Label(row, text="PDF 页码").pack(side="left", padx=(8, 3))
        ttk.Spinbox(row, from_=1, to=10000, textvariable=self.page_var, width=5).pack(side="left")
        ttk.Button(f, text="打开已保存的读数项目", command=self.open_saved).pack(fill="x", pady=(6, 0))

        f = ttk.LabelFrame(parent, text="需要修正时：手动标定坐标", padding=8)
        f.pack(fill="x", pady=(0, 7))
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(0, 6))
        ttk.Radiobutton(row, text="曲线 / 散点", variable=self.mode_var, value="xy", command=self.change_chart_type).pack(side="left")
        ttk.Radiobutton(row, text="竖柱 / 分组柱", variable=self.mode_var, value="bar", command=self.change_chart_type).pack(side="left", padx=8)
        ttk.Radiobutton(row, text="横条", variable=self.mode_var, value="bar_horizontal", command=self.change_chart_type).pack(side="left")
        grid = ttk.Frame(f)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        self.mark_buttons = {}
        for i, key in enumerate(("X1", "X2", "Y1", "Y2")):
            button = ttk.Button(grid, text=f"点选 {key}", command=lambda k=key: self.set_mouse_mode(k))
            button.grid(row=i, column=0, sticky="w", pady=2)
            self.mark_buttons[key] = button
            ttk.Label(grid, textvariable=self.mark_texts[key], width=27).grid(row=i, column=1, sticky="w", padx=7)
        axes = ttk.Frame(f)
        axes.pack(fill="x", pady=(7, 4))
        ttk.Label(axes, text="轴").grid(row=0, column=0)
        ttk.Label(axes, text="指标名称").grid(row=0, column=1)
        ttk.Label(axes, text="单位").grid(row=0, column=2)
        ttk.Label(axes, text="刻度类型").grid(row=0, column=3)
        for i, axis in enumerate(("x", "y"), 1):
            ttk.Label(axes, text=axis.upper()).grid(row=i, column=0, padx=(0, 5))
            ttk.Entry(axes, textvariable=getattr(self, axis + "_name"), width=11).grid(row=i, column=1, padx=2, pady=3)
            ttk.Entry(axes, textvariable=getattr(self, axis + "_unit"), width=6).grid(row=i, column=2, padx=2)
            ttk.Combobox(axes, textvariable=getattr(self, axis + "_scale"), values=("线性", "对数（log10）"), state="readonly", width=10).grid(row=i, column=3, padx=2)
        ttk.Label(f, text="对数轴请填实际刻度值，如 1、10、100；不是填其对数。", wraplength=340, style="Muted.TLabel").pack(anchor="w")
        ttk.Button(f, text="应用坐标标定", command=self.apply_calibration, style="Accent.TButton").pack(fill="x", pady=(6, 4))
        ttk.Label(f, textvariable=self.cal_status, wraplength=340, style="Muted.TLabel").pack(anchor="w")

        f = ttk.LabelFrame(parent, text="需要补点时：人工 / 颜色辅助", padding=8)
        f.pack(fill="x", pady=(0, 7))
        form = ttk.Frame(f)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        for i, (title, var) in enumerate((("论文 DOI", self.doi_var), ("图号 / 子图", self.figure_var), ("系列 / 图例", self.series_var), ("样品名称", self.sample_var), ("柱图类别", self.category_var))):
            ttk.Label(form, text=title).grid(row=i, column=0, sticky="w", pady=3)
            ttk.Entry(form, textvariable=var).grid(row=i, column=1, sticky="ew", padx=(7, 0))
        ttk.Button(f, text="人工点选数据点", command=lambda: self.set_mouse_mode("point"), style="Accent.TButton").pack(fill="x", pady=(6, 4))
        ttk.Label(f, text="柱图：填好类别，再点柱顶中心；仅支持普通垂直柱，暂不支持堆叠、水平或浮动柱。", wraplength=340, style="Muted.TLabel").pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(7, 3))
        ttk.Button(row, text="框选绘图区", command=lambda: self.set_mouse_mode("roi")).pack(side="left")
        ttk.Button(row, text="点选曲线颜色", command=lambda: self.set_mouse_mode("color")).pack(side="left", padx=5)
        ttk.Label(f, textvariable=self.color_var, style="Muted.TLabel").pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(4, 3))
        ttk.Label(row, text="颜色容差").pack(side="left")
        ttk.Spinbox(row, from_=1, to=150, textvariable=self.tolerance_var, width=5).pack(side="left", padx=4)
        ttk.Label(row, text="横向间隔（像素）").pack(side="left")
        ttk.Spinbox(row, from_=1, to=100, textvariable=self.step_var, width=4).pack(side="left", padx=4)
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(3, 0))
        ttk.Button(row, text="预览颜色取点", command=self.preview_trace).pack(side="left")
        self.accept_button = ttk.Button(row, text="接受预览点", command=self.accept_preview, state="disabled")
        self.accept_button.pack(side="left", padx=5)
        ttk.Button(f, text="清除预览（保留已取点）", command=self.clear_preview).pack(fill="x", pady=(5, 0))
        ttk.Label(f, text="颜色辅助只用于清晰单值曲线；散点、重叠曲线和误差棒优先人工点选。", wraplength=340, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        f = ttk.LabelFrame(parent, text="核对自动结果与导出", padding=8)
        f.pack(fill="x", pady=(0, 7))
        ttk.Label(f, text="备注：条件、读图歧义、待核实内容", style="Muted.TLabel").pack(anchor="w")
        self.notes_box = tk.Text(f, height=2, wrap="word", font=("Microsoft YaHei UI", 10), relief="solid", borderwidth=1)
        self.notes_box.pack(fill="x", pady=4)
        ttk.Checkbutton(f, text="我已对照原图核对这些读数", variable=self.reviewed_var).pack(anchor="w")
        ttk.Label(f, text="此标记是人工核对记录，不是测量精度认证。", style="Muted.TLabel", wraplength=340).pack(anchor="w", pady=(1, 5))
        ttk.Button(f, text="导出数值表与证据", command=self.export, style="Accent.TButton").pack(fill="x")
        ttk.Button(f, text="打开导出文件夹", command=self.open_output).pack(fill="x", pady=(5, 0))

    def set_status(self, text):
        self.status_var.set(text)

    def require_image(self):
        if self.image is None or self.session is None:
            messagebox.showinfo("先导入图片", "请先打开论文图片，或选择 PDF 中包含目标图的那一页。", parent=self.root)
            return False
        return True

    def require_calibration(self):
        if not self.require_image():
            return False
        if not self.session.get("calibration"):
            messagebox.showinfo("先识别坐标", "请先点击上方“一键识别并生成数值表”。\n\n自动识别无法完成时，可在左侧“需要修正时：手动标定坐标”中修正刻度。", parent=self.root)
            return False
        return True

    def run_job(self, function, on_success, label):
        if self.busy:
            self.set_status("上一项处理尚未完成，请稍候。")
            return
        self.busy = True
        self.auto_button.configure(state="disabled")
        self.set_status(label)
        self.root.configure(cursor="watch")
        def worker():
            try:
                self.job_queue.put((True, function()))
            except Exception as exc:
                self.job_queue.put((False, exc))
        threading.Thread(target=worker, daemon=True).start()
        def poll():
            try:
                ok, result = self.job_queue.get_nowait()
            except queue.Empty:
                self.root.after(80, poll)
                return
            self.busy = False
            self.auto_button.configure(state="normal")
            self.root.configure(cursor="")
            if ok:
                try:
                    on_success(result)
                except Exception as exc:
                    self.show_error(exc)
            else:
                self.show_error(result)
        self.root.after(80, poll)

    def show_error(self, exc):
        text = str(exc) or type(exc).__name__
        self.set_status("未完成：" + text)
        messagebox.showerror("处理未完成", text, parent=self.root)

    def auto_read(self):
        """Run automatic extraction without requesting manual tick values."""
        if self.busy or not self.require_image():
            return
        try:
            # Preserve edited context before the worker loads the on-disk session.
            # The extractor owns isolation of existing points / exports.
            self.sync_metadata()
            session_path = save_session(self.session)
        except Exception as exc:
            self.show_error(exc)
            return

        def worker():
            from auto_extract import auto_extract_session
            return auto_extract_session(str(session_path))

        self.run_job(worker, self.complete_auto_read,
                     "正在自动识别图型、坐标刻度和数据……完成后会生成待核对的数值表。")

    def complete_auto_read(self, result):
        if not isinstance(result, dict):
            raise ValueError("自动识别没有返回有效的结果记录，请保留原图后重试。")
        count = int(result.get("count", 0) or 0)
        status = str(result.get("status", "")).lower()
        if status not in ("success", "partial_success", "ok", "completed") or count <= 0:
            reason = str(result.get("message") or "本图未能可靠识别坐标和数据，尚未生成数值表。")
            report_path = result.get("report_path")
            if report_path:
                reason += "\n\n识别记录：\n" + str(report_path)
            self.set_status("自动识别未生成数值：" + reason)
            advice = result.get("next_action") or "可以重新裁剪到单个图，或使用左侧手动修正。"
            messagebox.showinfo("自动识别结果", reason + "\n\n" + str(advice), parent=self.root)
            return
        if not result.get("session_path"):
            raise ValueError("自动识别返回了点数，但缺少可打开的读数项目。")
        self.set_session(load_session(str(result["session_path"])))
        self.cal_status.set("自动坐标识别已完成。请核对轴名称、单位、刻度和图中标记。需要修改时使用左侧手动标定。")
        self.reviewed_var.set(False)
        export_dir = result.get("export_dir")
        message = f"已自动生成 {count} 个候选读数，均待核对。"
        if result.get('panel_count'):
            message=result['message']+'\n当前窗口显示其中一个子图；点击“查看各子图结果”可查看全部子图、核对图和数值表。'
        if status=='partial_success':
            if not result.get('panel_count') and result.get('message'):message+='\n'+str(result['message'])
            message+='\n部分内容未完整识别：缺失字段留空，未可靠分离的数据未输出。具体情况见下方提示及读取记录。'
        if export_dir:
            message += f"\n\n数值表：\n{Path(export_dir) / '读数数据.csv'}"
        warnings = result.get("warnings") or []
        if warnings:
            message += "\n\n需要核对：\n" + "\n".join(str(item) for item in warnings[:4])
        message += "\n\n请对照图中标记和下方表格核对。点击“打开导出文件夹”查看本次结果；修正后可再次导出。"
        self.set_status(f"自动识别完成：{count} 个候选读数，待核对。点击“打开导出文件夹”查看数值表。")
        messagebox.showinfo("自动读取完成 · 待核对", message, parent=self.root)

    def open_source(self, path=None, page=None):
        if self.busy:
            return
        if path is None:
            path = filedialog.askopenfilename(parent=self.root, title="选择论文图片或 PDF", filetypes=[("图片与 PDF", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            page = int(page if page is not None else self.page_var.get())
            if page < 1:
                raise ValueError("PDF 页码从 1 开始。")
        except Exception as exc:
            self.show_error(exc)
            return
        self.run_job(lambda: new_session(str(path), page=page, output_root=self.output_root), self.set_session, "正在本地读取图像，请稍候……")

    def open_saved(self, path=None):
        if self.busy:
            return
        if path is None:
            path = filedialog.askopenfilename(parent=self.root, title="选择读数项目 JSON", filetypes=[("读数项目", "*.json"), ("所有文件", "*.*")])
        if path:
            self.run_job(lambda: load_session(str(path)), self.set_session, "正在恢复读数项目……")

    def set_session(self, result):
        self.loading_controls = True
        self.session, self.image = result
        self.image = self.image.convert("RGB")
        self.cached_render_key = None
        self.axis_marks = {}
        self.preview_points = []
        self.preview_warnings = []
        self.preview_trace_run = None
        self.color = None
        self.color_var.set("尚未选色")
        self.accept_button.configure(state="disabled")
        self.reviewed_var.set(False)
        self.mode_var.set(self.session.get("chart_type", "xy"))
        self.figure_var.set(self.session.get("figure_label", ""))
        self.doi_var.set(self.session.get("doi", ""))
        self.notes_box.delete("1.0", "end")
        self.notes_box.insert("1.0", self.session.get("notes", ""))
        self.notes_box.edit_modified(False)
        self.series_var.set("系列1（待确认）")
        self.sample_var.set("")
        self.category_var.set("")
        meta = self.session.get("source_metadata", {})
        self.page_var.set(meta.get("page") or 1)
        self.file_var.set(f"{Path(meta.get('source_path', self.session.get('image_path', ''))).name}  ·  第 {meta.get('page') or 1} 页  ·  原图 {self.image.width} × {self.image.height} 像素")
        cal = self.session.get("calibration")
        if cal:
            for axis in ("x", "y"):
                item = cal[axis]
                getattr(self, axis + "_name").set(item.get("name", axis.upper()))
                getattr(self, axis + "_unit").set(item.get("unit", ""))
                getattr(self, axis + "_scale").set("对数（log10）" if item.get("scale") == "log10" else "线性")
                for n in (1, 2):
                    self.axis_marks[axis.upper() + str(n)] = {"p": list(item[f"p{n}"]), "v": item[f"v{n}"]}
            self.cal_status.set("已恢复标定。修改刻度后，请重新点击“应用坐标标定”。")
        else:
            self.x_name.set("横轴指标")
            self.y_name.set("纵轴指标")
            self.x_unit.set("")
            self.y_unit.set("")
            self.x_scale.set("线性")
            self.y_scale.set("线性")
            self.cal_status.set("尚未识别坐标。先点击上方“一键识别并生成数值表”，需要修正时再手动标定。")
        self.update_mark_labels()
        self.update_chart_controls()
        self.mouse_mode = "point"
        self.fit_view = True
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.refresh_table()
        self.draw()
        self.loading_controls = False
        self.set_status("图片已就绪。点击上方“一键识别并生成数值表”；可自动判断柱图方向，并尝试分别读取组合图的子图。")
        if self.auto_read_on_load:
            self.auto_read_on_load = False
            self.root.after(100, self.auto_read)

    def invalidate_review(self):
        self.reviewed_var.set(False)
        if self.session:
            self.session["reviewed"] = False
            for point in self.session.get("points", []):
                point["review_status"] = "unreviewed"

    def context_changed(self, *_args):
        if self.loading_controls or self.session is None:
            return
        self.invalidate_review()
        self.sync_metadata()
        save_session(self.session)

    def notes_changed(self, _event=None):
        if not self.notes_box.edit_modified():
            return
        self.notes_box.edit_modified(False)
        self.context_changed()

    def trace_settings_changed(self, *_args):
        if not self.loading_controls:
            self.clear_preview(silent=True)

    def axis_field_changed(self, *_args):
        if self.loading_controls or self.session is None:
            return
        self.invalidate_review()
        self.session["calibration"] = None
        self.cal_status.set("轴名称、单位或刻度类型已修改。请重新点击“应用坐标标定”。")
        self.clear_preview(silent=True)
        save_session(self.session)

    def update_chart_controls(self):
        category_axis='X' if self.mode_var.get()=='bar' else 'Y' if self.mode_var.get()=='bar_horizontal' else ''
        for key in ('X1','X2','Y1','Y2'):
            self.mark_buttons[key].configure(state='disabled' if key.startswith(category_axis) and category_axis else 'normal')

    def change_chart_type(self):
        self.update_chart_controls()
        if self.session:
            old = self.session.get("chart_type", "xy")
            if old == self.mode_var.get():
                return
            if self.session.get("points"):
                self.mode_var.set(old)
                self.update_chart_controls()
                messagebox.showinfo("为另一种图型新建项目", "当前已有读数，不能把曲线数据改成柱图数据。请先导出，再重新打开原图，为另一种图型建立独立项目。", parent=self.root)
                return
            self.session["chart_type"] = self.mode_var.get()
            self.session["calibration"] = None
            self.reviewed_var.set(False)
            self.preview_points = []
            self.accept_button.configure(state="disabled")
            self.cal_status.set("图型已改变。必须重新应用标定，已有点随后会按新标定重新计算。")
            save_session(self.session)
            self.draw()
        if self.mode_var.get() == "bar":
            self.set_status("普通柱状图只需标定 Y1、Y2；每次填好类别后，点柱顶中心。X 仅为图片位置，不是科学变量。")
        elif self.mode_var.get()=='bar_horizontal':
            self.set_status('先尝试一键自动读数；手动修正时只需 X1、X2，填写类别并点横条端部中心。Y 位置不作为科学变量。')

    def set_mouse_mode(self, mode):
        if not self.require_image() or self.busy:
            return
        self.mouse_mode = mode
        if mode in ("X1", "X2", "Y1", "Y2"):
            self.set_status(f"请点击 {mode[0]} 轴上的第 {mode[1]} 个已知刻度位置；接着输入刻度值。两个刻度尽量相距较远。")
        elif mode == "roi":
            self.set_status("按住鼠标左键框选绘图区：尽量避开文字和图例。松开鼠标完成；原图不会被裁切。")
        elif mode == "color":
            self.set_status("请点击目标曲线内部颜色清晰的位置，避开抗锯齿边缘和重叠处。")
        else:
            self.set_status("人工取点模式：先核对系列、样品和类别，再点数据点中心或柱顶中心。按 Esc 可返回此模式。")

    def canvas_to_image(self, event, clamp=False):
        if self.image is None:
            return None
        x = (self.canvas.canvasx(event.x) - 10) / self.render_scale_x
        y = (self.canvas.canvasy(event.y) - 10) / self.render_scale_y
        if clamp:
            return (min(max(x, 0), self.image.width - 1), min(max(y, 0), self.image.height - 1))
        if 0 <= x < self.image.width and 0 <= y < self.image.height:
            return x, y
        return None

    def on_press(self, event):
        if self.busy or self.image is None:
            return
        pos = self.canvas_to_image(event)
        if pos is None:
            return
        px, py = pos
        if self.mouse_mode == "roi":
            self.drag_start = pos
            self.drag_box = [px, py, px, py]
            return
        if self.mouse_mode in ("X1", "X2", "Y1", "Y2"):
            key = self.mouse_mode
            old = self.axis_marks.get(key, {}).get("v")
            value = simpledialog.askfloat("输入刻度数值", f"{key} 的刻度是多少？\n请填实际数值；对数轴也填刻度本身，例如 0.1、1、10。", initialvalue=old, parent=self.root)
            if value is not None:
                self.axis_marks[key] = {"p": [px, py], "v": value}
                self.update_mark_labels()
                self.reviewed_var.set(False)
                # Staged marks must not silently leave an older calibration active.
                self.session["calibration"] = None
                self.invalidate_review()
                save_session(self.session)
                self.cal_status.set("刻度已修改。请完成其他刻度后点击“应用坐标标定”。")
                self.clear_preview(silent=True)
                self.mouse_mode = "point"
                self.draw()
                self.set_status(f"已记录 {key} = {value:g}；继续点选其他刻度，最后应用坐标标定。")
            return
        if self.mouse_mode == "color":
            self.color = list(self.image.getpixel((int(px), int(py))))[:3]
            self.color_var.set(f"所选颜色 RGB：{tuple(self.color)}；请先框选绘图区")
            self.clear_preview(silent=True)
            self.mouse_mode = "point"
            self.set_status("颜色已选定。框选绘图区后点击“预览颜色取点”；候选点不会自动写入数据表。")
            return
        if not self.require_calibration():
            return
        if self.mode_var.get() in ("bar","bar_horizontal") and not self.category_var.get().strip():
            messagebox.showinfo("填写柱图类别", "请先填写当前柱对应的类别，例如温度、样品编号或处理方式。", parent=self.root)
            return
        try:
            self.sync_metadata()
            self.session = add_points(self.session, [{"px": px, "py": py, "method": "manual"}], self.series_var.get().strip() or "系列1（待确认）", sample_label=self.sample_var.get().strip(), category=self.category_var.get().strip(), method="manual")
            self.reviewed_var.set(False)
            self.refresh_table()
            self.draw()
            self.set_status("已添加人工读数。请对照原图核对；取错可删除最后一个点。")
        except Exception as exc:
            self.show_error(exc)

    def on_drag(self, event):
        if self.mouse_mode != "roi" or self.drag_start is None:
            return
        pos = self.canvas_to_image(event, clamp=True)
        self.drag_box = [*self.drag_start, *pos]
        self.draw()

    def on_release(self, event):
        if self.mouse_mode != "roi" or self.drag_start is None:
            return
        pos = self.canvas_to_image(event, clamp=True)
        x1, y1 = self.drag_start
        x2, y2 = pos
        self.drag_start = None
        self.drag_box = None
        if abs(x2 - x1) < 8 or abs(y2 - y1) < 8:
            self.set_status("绘图区太小，请重新拖动框选。")
            self.draw()
            return
        self.session["roi"] = [int(min(x1, x2)), int(min(y1, y2)), int(max(x1, x2)) + 1, int(max(y1, y2)) + 1]
        self.invalidate_review()
        save_session(self.session)
        self.clear_preview(silent=True)
        self.mouse_mode = "point"
        self.draw()
        self.set_status("已框选绘图区。选择曲线颜色后可预览颜色取点；后续人工数据点也应落在橙框内。")

    def update_mark_labels(self):
        for key, var in self.mark_texts.items():
            mark = self.axis_marks.get(key)
            category=(key.startswith('X') and self.mode_var.get()=='bar') or (key.startswith('Y') and self.mode_var.get()=='bar_horizontal')
            var.set('类别轴：无需数字刻度' if category else f"{mark['v']:g}  ·  已选刻度" if mark else "未选")

    def build_calibration(self):
        cal = {}
        for axis in ("x", "y"):
            if axis == "x" and self.mode_var.get() == "bar":
                cal[axis] = {"p1": [0, 0], "v1": 0, "p2": [self.image.width, 0], "v2": self.image.width, "scale": "linear", "name": "柱位置", "unit": "pixel"}
                continue
            if axis=='y' and self.mode_var.get()=='bar_horizontal':
                cal[axis]={'p1':[0,0],'v1':0,'p2':[0,self.image.height],'v2':self.image.height,'scale':'linear','name':'类别位置（仅像素）','unit':'pixel'}
                continue
            keys = (axis.upper() + "1", axis.upper() + "2")
            if any(key not in self.axis_marks for key in keys):
                raise ValueError(f"请先点选 {keys[0]} 和 {keys[1]} 两个刻度。")
            a, b = (self.axis_marks[key] for key in keys)
            cal[axis] = {"p1": a["p"], "v1": a["v"], "p2": b["p"], "v2": b["v"], "scale": "log10" if getattr(self, axis + "_scale").get().startswith("对数") else "linear", "name": getattr(self, axis + "_name").get().strip() or axis.upper(), "unit": getattr(self, axis + "_unit").get().strip()}
        return cal

    def apply_calibration(self):
        if not self.require_image() or self.busy:
            return
        try:
            cal = self.build_calibration()
            validate_calibration(cal)
            self.sync_metadata()
            self.session = set_calibration(self.session, cal)
            self.reviewed_var.set(False)
            self.clear_preview(silent=True)
            self.cal_status.set("标定已应用。已有点已重新计算；请核对轴类型、单位和读数。")
            self.mouse_mode = "point"
            self.refresh_table()
            self.draw()
            self.set_status("现在可以人工点选数据点。每换一个系列，请先修改“系列 / 图例”和样品名称。")
        except Exception as exc:
            self.show_error(exc)

    def preview_trace(self):
        if not self.require_calibration() or self.busy:
            return
        if self.mode_var.get() != "xy":
            messagebox.showinfo("柱图自动读取", "柱图请优先点击上方“一键识别并生成数值表”。需要修正时，可填写类别并点柱体端部中心。", parent=self.root)
            return
        if not self.session.get("roi") or self.color is None:
            messagebox.showinfo("准备颜色取点", "请先框选绘图区，再点选目标曲线颜色。", parent=self.root)
            return
        try:
            tolerance, step = int(self.tolerance_var.get()), int(self.step_var.get())
            if not 1 <= tolerance <= 150 or not 1 <= step <= 100:
                raise ValueError("颜色容差应为 1–150，横向间隔应为 1–100 像素。")
        except Exception as exc:
            self.show_error(exc)
            return
        image, roi, color = self.image, list(self.session["roi"]), list(self.color)
        self.clear_preview(silent=True)
        request_generation = self.trace_generation
        def complete(result):
            if request_generation != self.trace_generation:
                self.set_status("取点设置已变更，旧候选已作废。请按新设置重新预览。")
                return
            trace_id = uuid.uuid4().hex[:12]
            self.preview_points = [dict(point, trace_id=trace_id) for point in result.get("points", [])]
            self.preview_warnings = result.get("warnings", [])
            self.preview_trace_run = {"trace_id": trace_id, "roi": roi, "color_rgb": color, "tolerance": tolerance, "step": step, "max_thickness": 20, "stats": result.get("stats", {}), "warnings": self.preview_warnings, "candidate_count": len(self.preview_points)}
            self.accept_button.configure(state="normal" if self.preview_points else "disabled")
            self.draw()
            warnings = "；".join(map(str, self.preview_warnings))
            self.set_status(f"黄色圈为 {len(self.preview_points)} 个候选点，尚未入表。请放大检查后再接受。" + (" 注意：" + warnings if warnings else ""))
            if warnings:
                messagebox.showwarning("颜色取点需要核对", warnings + "\n\n候选点仅显示为黄色圈。确认它们都属于目标曲线后再接受；有误可缩小框选范围或改为人工取点。", parent=self.root)
        self.run_job(lambda: trace_color_curve(image, roi, color, tolerance=tolerance, step=step, max_thickness=20), complete, "正在本地生成颜色候选点……")

    def accept_preview(self):
        if not self.preview_points or self.busy or not self.require_calibration():
            return
        try:
            self.sync_metadata()
            self.session = add_points(self.session, self.preview_points, self.series_var.get().strip() or "系列1（待确认）", sample_label=self.sample_var.get().strip(), category=self.category_var.get().strip(), method="color_trace")
            if self.preview_trace_run:
                self.session.setdefault("trace_runs", []).append(dict(self.preview_trace_run))
                save_session(self.session)
            self.reviewed_var.set(False)
            count = len(self.preview_points)
            self.clear_preview(silent=True)
            self.refresh_table()
            self.draw()
            self.set_status(f"已接受 {count} 个颜色候选点。颜色追踪是近似恢复；请再核对样品、坐标和曲线对应关系。")
        except Exception as exc:
            self.show_error(exc)

    def clear_preview(self, silent=False):
        self.trace_generation += 1
        self.preview_points = []
        self.preview_warnings = []
        self.preview_trace_run = None
        self.accept_button.configure(state="disabled")
        self.draw()
        if not silent:
            self.set_status("预览已清除；已接受的数据点未改变。")

    def remove_point(self):
        if not self.require_image() or self.busy:
            return
        if not self.session.get("points"):
            self.set_status("目前没有可删除的数据点。")
            return
        try:
            self.session = delete_last_point(self.session)
            self.reviewed_var.set(False)
            self.refresh_table()
            self.draw()
            self.set_status("已删除最后一个点。")
        except Exception as exc:
            self.show_error(exc)

    @staticmethod
    def format_value(value):
        if value is None:
            return "—"
        try:
            return f"{float(value):.8g}"
        except (ValueError, TypeError):
            return str(value)

    def refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        points = self.session.get("points", []) if self.session else []
        for i, point in enumerate(points, 1):
            method = {"manual": "人工点选", "color_trace": "颜色辅助", "automatic": "自动候选", "auto_curve": "自动曲线", "auto_scatter": "自动散点", "auto_bar": "自动柱图", "automatic_curve_sample":"曲线图像采样", "automatic_hollow_marker":"自动空心标记", "automatic_filled_marker":"自动实心标记", "automatic_marker_candidate":"自动标记候选"}.get(point.get("method"), point.get("method", ""))
            self.tree.insert("", "end", iid=str(i - 1), values=(i, self.format_value(None if self.mode_var.get()=='bar' else point.get("x")), self.format_value(None if self.mode_var.get()=='bar_horizontal' else point.get("y")), point.get("series_label", ""), point.get("category", ""), method))
        unit='根柱体' if self.mode_var.get() in ('bar','bar_horizontal') else '个点'
        hint='类别轴显示 —；具体名称见“类别”，数值见另一轴。' if unit=='根柱体' else '表中显示值便于核对，导出保留原始计算值'
        self.point_count.set(f"已取 {len(points)} {unit} · {hint}")

    def sync_metadata(self):
        if self.session is None:
            return
        self.session["figure_label"] = self.figure_var.get().strip()
        self.session["doi"] = self.doi_var.get().strip()
        self.session["chart_type"] = self.mode_var.get()
        self.session["notes"] = self.notes_box.get("1.0", "end-1c").strip()

    def export(self):
        if not self.require_calibration() or self.busy:
            return
        if not self.session.get("points"):
            messagebox.showinfo("还没有读数", "请先点击“一键识别并生成数值表”。需要补点时，也可使用左侧人工取点。", parent=self.root)
            return
        try:
            self.sync_metadata()
            output = export_session(self.session, metadata={"reviewed": bool(self.reviewed_var.get()), "doi": self.doi_var.get().strip(), "figure_label": self.figure_var.get().strip(), "chart_type": self.mode_var.get(), "notes": self.notes_box.get("1.0", "end-1c").strip()})
            self.refresh_table()
            count = len(self.session["points"])
            csv_path = Path(self.session['run_dir']) / self.session['exports'][-1]['directory'] / "读数数据.csv"
            self.set_status(f"已导出 {count} 个点：{csv_path}。点击“打开导出文件夹”可直接查看。")
            messagebox.showinfo("导出完成", f"已导出 {count} 个点。用 Excel 打开“读数数据.csv”。\n\n表格位置：\n{csv_path}\n\n点击“逐系列核对：原图与数值”可按系列查看像素与数值对应；“打开导出文件夹”可找到表格和原图证据。", parent=self.root)
        except Exception as exc:
            self.show_error(exc)

    def open_output(self):
        if not self.require_image():
            return
        exports = self.session.get("exports", [])
        if not exports:
            messagebox.showinfo("还没有导出的数值", "请先点击“一键识别并生成数值表”，程序会识别坐标轴、提取候选数据并导出。\n\n自动识别未完成时，可手动标定坐标轴和补点，再点击“导出数值表与证据”。\n\n完成后，这个按钮会直接打开包含“读数数据.csv”的文件夹。", parent=self.root)
            return
        try:
            run_dir = Path(self.session["run_dir"]).resolve()
            bundle=self.session.get('automatic_bundle',{}).get('export_dir')
            if self.session.get('automatic_bundle',{}).get('session_export_directory')!=exports[-1]['directory']:
                bundle=None
            path = Path(bundle).resolve() if bundle else (run_dir / exports[-1]["directory"]).resolve()
            allowed=run_dir.parent if bundle else run_dir
            if path == allowed or not path.is_relative_to(allowed):
                raise ValueError("导出目录不在当前读数项目中，请重新导出。")
            if not path.is_dir() or not (path / "读数数据.csv").is_file():
                raise ValueError("尚未找到本次导出的数值表，请在读数窗口重新导出。")
            os.startfile(str(path))
        except Exception as exc:
            self.show_error(exc)

    def open_series_review(self):
        if not self.require_image():
            return
        try:
            exports = self.session.get("exports") or []
            if not exports:
                messagebox.showinfo("尚未生成数值", "请先自动识别并生成数值，完成后可逐系列核对原图、点号与数值。", parent=self.root)
                return
            root = Path(self.session['run_dir']).resolve()
            folder = (root / exports[-1]['directory']).resolve()
            if folder.parent != root:
                raise ValueError("导出目录不在当前读数项目中，请重新导出。")
            target = folder / "逐系列核对.html"
            if not target.is_file():
                messagebox.showinfo("请更新本次导出", "这是旧版导出记录。点击“导出数值表与证据”即可生成逐系列核对页。", parent=self.root)
                return
            snapshot = json.loads((folder / '读数与溯源.json').read_text(encoding='utf-8-sig'))
            relevant = ('points','calibration','calibration_id','doi','figure_label','chart_type','notes','roi','reviewed','source_metadata','image_sha256')
            if any(snapshot.get(key) != self.session.get(key) for key in relevant):
                messagebox.showinfo("核对页需要更新", "当前读数、标定或审核状态已修改。请先点击“导出数值表与证据”，再查看新的逐系列核对页。旧快照继续保留用于追溯。", parent=self.root)
                return
            os.startfile(str(target))
        except Exception as exc:
            self.show_error(exc)

    def open_panel_results(self):
        if not self.require_image(): return
        bundle=self.session.get('automatic_bundle',{}).get('export_dir')
        if not bundle:
            messagebox.showinfo('子图结果','当前结果只有一个绘图区。组合图自动识别后，这里会显示所有子图与各自的数值表。',parent=self.root)
            return
        try:
            path=Path(bundle).resolve()/'查看子图与数值.html'
            if not path.is_relative_to(Path(self.session['run_dir']).resolve().parent) or not path.is_file():
                raise ValueError('没有找到本次子图结果，请重新自动识别。')
            exports=self.session.get('exports',[])
            if exports and self.session.get('automatic_bundle',{}).get('session_export_directory')!=exports[-1]['directory']:
                messagebox.showinfo('子图汇总快照','当前子图已重新导出。这里显示自动拆图时的快照；最新数据请点“打开导出文件夹”，或回板块 2 重新汇总。',parent=self.root)
            os.startfile(str(path))
        except Exception as exc: self.show_error(exc)

    def show_page_text(self):
        if not self.require_image():
            return
        meta = self.session.get("source_metadata", {})
        text = meta.get("page_text", "") or "本页没有可读取的文字层，或导入的是普通图片。此功能不执行 OCR，也不自动识别图例、坐标值或图中数值。"
        window = tk.Toplevel(self.root)
        window.title("PDF 本页文字层 · 用于人工核对图注")
        window.geometry("860x650")
        ttk.Label(window, text="文字层顺序可能不完整；请回到原图核对。这里不是自动读图或 OCR 结果。", wraplength=800).pack(anchor="w", padx=12, pady=10)
        content = tk.Text(window, wrap="word", font=("Microsoft YaHei UI", 11))
        content.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        candidates = meta.get("figure_caption_candidates", [])
        if candidates:
            text = "可能的图注（待人工核对）\n" + "\n".join(map(str, candidates)) + "\n\n———— 本页文字 ————\n\n" + text
        content.insert("1.0", text)
        content.configure(state="disabled")

    def on_canvas_configure(self, _event):
        if self.configure_after:
            self.root.after_cancel(self.configure_after)
        self.configure_after = self.root.after(120, self._resize_draw)

    def _resize_draw(self):
        self.configure_after = None
        self.draw()

    def fit(self):
        self.fit_view = True
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.draw()

    def actual_size(self):
        if self.image is None:
            return
        self.fit_view = False
        self.scale = 1.0
        self.draw()

    def zoom(self, factor):
        if self.image is None:
            return "break"
        self.fit_view = False
        self.scale = min(4.0, max(0.04, self.scale * factor))
        self.draw()
        return "break"

    def draw(self):
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("all")
        if self.image is None:
            self.canvas.create_text(45, 70, text="从左侧打开论文图片或 PDF\n\n点击上方“一键识别并生成数值表”，再对照原图核对候选数据。", anchor="nw", fill="#476174", font=("Microsoft YaHei UI", 14), width=750)
            return
        if self.fit_view:
            width, height = max(100, self.canvas.winfo_width() - 24), max(100, self.canvas.winfo_height() - 24)
            self.scale = min(width / self.image.width, height / self.image.height, 1.0)
        dw, dh = max(1, round(self.image.width * self.scale)), max(1, round(self.image.height * self.scale))
        self.render_scale_x = dw / self.image.width
        self.render_scale_y = dh / self.image.height
        cache_key = (id(self.image), dw, dh)
        if cache_key != self.cached_render_key:
            resized = self.image.resize((dw, dh), Image.Resampling.LANCZOS)
            self.tk_image = ImageTk.PhotoImage(resized)
            self.cached_render_key = cache_key
        self.canvas.create_image(10, 10, image=self.tk_image, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, dw + 20, dh + 20))
        def xy(px, py):
            return 10 + px * self.render_scale_x, 10 + py * self.render_scale_y
        for key, mark in self.axis_marks.items():
            if (self.mode_var.get() == "bar" and key.startswith("X")) or (self.mode_var.get()=='bar_horizontal' and key.startswith('Y')):
                continue
            x, y = xy(*mark["p"])
            self.canvas.create_line(x - 8, y, x + 8, y, fill="#0074d9", width=2)
            self.canvas.create_line(x, y - 8, x, y + 8, fill="#0074d9", width=2)
            self.canvas.create_text(x + 9, y - 9, text=key, fill="#005da9", anchor="sw", font=("Segoe UI", 10, "bold"))
        roi = self.drag_box or (self.session.get("roi") if self.session else None)
        if roi:
            self.canvas.create_rectangle(*xy(roi[0], roi[1]), *xy(roi[2], roi[3]), outline="#ed8c17", width=2, dash=(6, 3))
        for point in self.session.get("points", []) if self.session else []:
            x, y = xy(float(point["px"]), float(point["py"]))
            self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#00a7a0", outline="white", width=1)
        for point in self.preview_points:
            x, y = xy(float(point["px"]), float(point["py"]))
            self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4, outline="#eab308", width=2)

    def focus_selected_point(self, _event=None):
        selected = self.tree.selection()
        if not selected or not self.session:
            return
        try:
            point = self.session["points"][int(selected[0])]
            x, y = 10 + point["px"] * self.render_scale_x, 10 + point["py"] * self.render_scale_y
            self.canvas.delete("selection_ring")
            self.canvas.create_oval(x - 9, y - 9, x + 9, y + 9, outline="#e11d48", width=3, tags="selection_ring")
            if self.mode_var.get() in ('bar','bar_horizontal'):
                axis='y' if self.mode_var.get()=='bar' else 'x'
                self.set_status(f"选中柱体：类别={point.get('category') or '待核对'}；数值={self.format_value(point.get(axis))}；系列={point.get('series_label','')}。红圈标示柱体端点。")
            else:
                self.set_status(f"选中点：X={self.format_value(point.get('x'))}，Y={self.format_value(point.get('y'))}；系列：{point.get('series_label', '')}；样品：{point.get('sample_label', '') or '未填写'}。红圈标示原图位置。")
        except (ValueError, IndexError, KeyError):
            return


def main(argv=None):
    parser = argparse.ArgumentParser(description="论文图片本地读数工作台")
    parser.add_argument("--output-root")
    parser.add_argument("--open", dest="source")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--load-session")
    parser.add_argument("--auto-read", action="store_true", help="图像载入后自动识别坐标和数据")
    parser.add_argument("--smoke", action="store_true", help="创建界面后退出，仅用于本地可用性检查")
    args = parser.parse_args(argv)
    root = tk.Tk()
    if args.smoke:
        root.withdraw()
    app = DigitizerApp(root, output_root=args.output_root, auto_read_on_load=args.auto_read and not args.smoke)
    if args.smoke:
        if args.load_session:
            app.set_session(load_session(args.load_session))
        elif args.source:
            app.set_session(new_session(args.source, page=args.page, output_root=args.output_root))
        root.update_idletasks()
        root.update()
        root.destroy()
        return 0
    if args.load_session:
        root.after(150, lambda: app.open_saved(args.load_session))
    elif args.source:
        root.after(150, lambda: app.open_source(args.source, args.page))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
