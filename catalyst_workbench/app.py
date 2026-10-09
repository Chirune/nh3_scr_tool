"""Chinese local GUI: screened papers -> figure candidates -> reviewed readings."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk
from candidate_policy import selection_report, split_candidates
from digitizer.chart_catalog import describe_chart

from pipeline import (
    build_batch, load_batch, update_figure, open_digitizer,
    save_batch, collect_readings, create_subfigure,
)


TYPE_LABELS = {
    "xy": "曲线 / 散点 / 谱线", "bar": "竖柱 / 并列分组柱", "bar_horizontal": "横向条形图",
    "unknown": "待判断", "non_numeric": "本版不读数",
}
REVIEW_LABELS = {"unreviewed": "未核对", "keep": "保留", "exclude": "排除", "review": "待复核"}
FILTER_VALUES = ("全部候选", "未核对", "保留", "排除", "待复核")
SCOPE_VALUES = ("有图号的图片（默认）", "未识别图号（查漏）", "全部原始候选")
FLAG_LABELS = {
    "caption_association_requires_review": "图注与图片的对应关系需要核对",
    "chart_type_hint_requires_review": "图型仅为文字线索推测，需要核对",
    "caption_missing": "未找到可靠图注",
    "figure_boundary_not_found": "未找到可靠图像边界",
    "full_page_manual_location_required": "目前保留整页，需要人工框选目标图",
    "graphic_may_be_table_or_decoration": "候选也可能是表格或装饰元素",
    "heuristic_bbox_requires_review": "自动定位框需要人工核对",
    "mixed_panel_types_possible": "组合图可能包含不同类型的子图",
    "multiple_graphics_grouped_under_one_caption": "同一图注下可能有多个图形",
    "multiple_nearby_graphics_candidates": "附近存在多个候选图形",
    "no_ocr_performed": "本次没有执行 OCR 文字识别",
    "no_text_layer": "页面缺少可读取文字层",
    "subfigure_not_separated": "子图尚未分别拆分，请复制为独立子图后框选",
    "whole_page_image_or_form_present": "页面含整页图像或整体图形对象，边界需核实",
    "graphics_bounds_read_failed": "部分图形边界读取失败",
    "graphics_object_limit_reached": "图形对象较多，本次达到读取上限",
    "text_char_limit_reached": "页面文字较多，本次达到读取上限",
    "text_layer_read_failed": "页面文字层读取失败",
    "unreadable_graphics_bounds": "部分图形没有可读边界",
}


class ImageView(ttk.Frame):
    """Image display with exact mapping to saved image pixel coordinates."""

    def __init__(self, parent, on_box=None):
        super().__init__(parent)
        self.original = None
        self.photo = None
        self.box = None
        self.drag_start = None
        self.drag_box = None
        self.on_box = on_box
        self.scale = 1.0
        self.scale_x = self.scale_y = 1.0
        self.fit_mode = True
        self.render_key = None
        self.resize_after = None
        self.empty_message = "选择左侧候选图后，在这里核对原页与定位框。"
        self.empty_action = None
        self.empty_button = None
        self.canvas = tk.Canvas(self, bg="#dfe7ef", highlightthickness=0, cursor="crosshair" if on_box else "arrow")
        sx = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        sy = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        self.canvas.bind("<Configure>", self.on_resize)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Control-MouseWheel>", self.on_zoom_wheel)
        if on_box:
            self.canvas.bind("<ButtonPress-1>", self.on_press)
            self.canvas.bind("<B1-Motion>", self.on_drag)
            self.canvas.bind("<ButtonRelease-1>", self.on_release)

    def set_image(self, image, box=None, reset=True):
        self.original = image.convert("RGB") if image is not None else None
        self.box = list(box) if box is not None else None
        self.drag_box = self.drag_start = None
        self.render_key = None
        if reset:
            self.fit_mode = True
        self.draw()
        if reset:
            # Reset after replacing the scroll region; the previous empty-state
            # text may have had a nonzero origin.
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)

    def set_empty_message(self, message, action=None):
        self.empty_message = message
        self.empty_action = action
        if action is not None and self.empty_button is None:
            self.empty_button = ttk.Button(self.canvas, text="选择已下载 PDF 文件夹", command=lambda: self.empty_action() if self.empty_action else None)
        self.draw()

    def on_resize(self, _event=None):
        if self.resize_after:
            self.after_cancel(self.resize_after)
        self.resize_after = self.after(100, self._finish_resize)

    def _finish_resize(self):
        self.resize_after = None
        self.draw()

    def destroy(self):
        if self.resize_after is not None:
            try:
                self.after_cancel(self.resize_after)
            except tk.TclError:
                pass
            self.resize_after = None
        super().destroy()

    def fit(self):
        self.fit_mode = True
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.draw()

    def actual_size(self):
        self.fit_mode = False
        self.scale = 1.0
        self.draw()

    def zoom(self, factor):
        self.fit_mode = False
        self.scale = max(0.025, min(4.0, self.scale * factor))
        self.draw()

    def on_wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def on_zoom_wheel(self, event):
        self.zoom(1.2 if event.delta > 0 else 1 / 1.2)
        return "break"

    def to_original(self, event, clamp=False):
        if self.original is None:
            return None
        x = (self.canvas.canvasx(event.x) - 10) / self.scale_x
        y = (self.canvas.canvasy(event.y) - 10) / self.scale_y
        if clamp:
            return min(max(x, 0), self.original.width), min(max(y, 0), self.original.height)
        if 0 <= x <= self.original.width and 0 <= y <= self.original.height:
            return x, y
        return None

    def on_press(self, event):
        point = self.to_original(event)
        if point is not None:
            self.drag_start = point
            self.drag_box = [*point, *point]

    def on_drag(self, event):
        if self.drag_start is None:
            return
        point = self.to_original(event, clamp=True)
        self.drag_box = [*self.drag_start, *point]
        self.draw()

    def on_release(self, event):
        if self.drag_start is None:
            return
        x1, y1 = self.drag_start
        x2, y2 = self.to_original(event, clamp=True)
        self.drag_start = self.drag_box = None
        bbox = [max(0, round(min(x1, x2))), max(0, round(min(y1, y2))),
                min(self.original.width, round(max(x1, x2))), min(self.original.height, round(max(y1, y2)))]
        if bbox[2] - bbox[0] >= 10 and bbox[3] - bbox[1] >= 10:
            self.on_box(bbox)
        self.draw()

    def draw(self):
        self.canvas.delete("all")
        if self.original is None:
            text_item = self.canvas.create_text(28, 30, text=self.empty_message, anchor="nw", fill="#294c65", width=max(180, self.canvas.winfo_width() - 56), font=("Microsoft YaHei UI", 13))
            if self.empty_action is not None and self.empty_button is not None:
                bounds = self.canvas.bbox(text_item)
                self.canvas.create_window(28, bounds[3] + 16, window=self.empty_button, anchor="nw")
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            return
        if self.fit_mode:
            w = max(100, self.canvas.winfo_width() - 24)
            h = max(100, self.canvas.winfo_height() - 24)
            self.scale = min(w / self.original.width, h / self.original.height, 1.0)
        dw = max(1, round(self.original.width * self.scale))
        dh = max(1, round(self.original.height * self.scale))
        self.scale_x, self.scale_y = dw / self.original.width, dh / self.original.height
        key = id(self.original), dw, dh
        if key != self.render_key:
            self.photo = ImageTk.PhotoImage(self.original.resize((dw, dh), Image.Resampling.LANCZOS))
            self.render_key = key
        self.canvas.create_image(10, 10, image=self.photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, dw + 20, dh + 20))
        box = self.drag_box or self.box
        if box is not None:
            left, top, right, bottom = box
            self.canvas.create_rectangle(10 + left * self.scale_x, 10 + top * self.scale_y,
                                         10 + right * self.scale_x, 10 + bottom * self.scale_y,
                                         outline="#ed8612", width=3, dash=(8, 3))


class CropDialog(tk.Toplevel):
    """Stage a crop locally; only an explicit confirmation commits it."""

    def __init__(self, parent, image, initial_box, on_confirm, show=True):
        super().__init__(parent)
        self.withdraw()
        self.title("手动裁剪图片 · 确认后才保存")
        self.source_image = image.convert("RGB").copy()
        self.pending_bbox = list(initial_box) if initial_box else None
        self.on_confirm = on_confirm
        self.result = None
        width = min(1280, max(820, self.winfo_screenwidth() - 100))
        height = min(860, max(560, self.winfo_screenheight() - 130))
        self.geometry(f"{width}x{height}")
        self.minsize(760, 480)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        ttk.Label(self, text="在左侧整页上拖框，右侧预览裁剪结果", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", padx=12, pady=(10, 5))
        ttk.Label(self, text="请把坐标轴、刻度、单位和图例一起框入。整页原图会保留；点击“确认裁剪”前不会修改候选或读数记录。", wraplength=1100).pack(anchor="w", padx=12, pady=(0, 6))
        footer = ttk.Frame(self, padding=10)
        footer.pack(side="bottom", fill="x")
        self.confirm_button = ttk.Button(footer, text="确认裁剪并保存", style="Accent.TButton", command=self.confirm)
        self.confirm_button.pack(side="right")
        ttk.Button(footer, text="取消，不修改", command=self.cancel).pack(side="right", padx=8)
        self.crop_status = tk.StringVar(value="拖动鼠标重新框选；确认前可反复调整。")
        ttk.Label(footer, textvariable=self.crop_status, wraplength=650).pack(side="left", fill="x", expand=True)
        toolbar = ttk.Frame(self, padding=(12, 0, 12, 5))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="适合窗口", command=lambda: self.page_view.fit()).pack(side="left")
        ttk.Button(toolbar, text="原图 100%", command=lambda: self.page_view.actual_size()).pack(side="left", padx=5)
        ttk.Button(toolbar, text="放大 ＋", command=lambda: self.page_view.zoom(1.3)).pack(side="left")
        ttk.Button(toolbar, text="缩小 −", command=lambda: self.page_view.zoom(1 / 1.3)).pack(side="left", padx=5)
        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=12, pady=(0, 5))
        self.page_view = ImageView(panes, on_box=self.stage_box)
        self.preview_view = ImageView(panes)
        panes.add(self.page_view, weight=3)
        panes.add(self.preview_view, weight=2)
        self.page_view.set_image(self.source_image, box=self.pending_bbox)
        if self.pending_bbox:
            self.stage_box(self.pending_bbox)
        if show:
            self.transient(parent)
            self.deiconify()
            self.grab_set()

    def stage_box(self, bbox):
        left, top, right, bottom = [int(value) for value in bbox]
        if not (0 <= left < right <= self.source_image.width and 0 <= top < bottom <= self.source_image.height):
            raise ValueError("裁剪框必须位于整页图片内。")
        self.pending_bbox = [left, top, right, bottom]
        self.page_view.box = list(self.pending_bbox)
        self.page_view.draw()
        self.preview_view.set_image(self.source_image.crop(tuple(self.pending_bbox)))
        self.crop_status.set(f"预览：{right-left} × {bottom-top} 像素。请确认坐标轴与图例完整。")

    def confirm(self):
        if not self.pending_bbox:
            messagebox.showinfo("先框选图片", "请先在左侧整页上拖出要保留的范围。", parent=self)
            return
        try:
            if self.on_confirm(list(self.pending_bbox)) is False:
                return
        except Exception as exc:
            messagebox.showerror("裁剪尚未保存", str(exc), parent=self)
            return
        self.result = list(self.pending_bbox)
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class FigurePipelineApp:
    def __init__(self, root, output_root=None):
        root.tk.call('tk','scaling',min(float(root.tk.call('tk','scaling')),1.5))
        self.root = root
        self.output_root = output_root
        self.batch = None
        self.current_id = None
        self.current_page_path = None
        self.loading_details = False
        self.refreshing_list = False
        self.busy = False
        self.cancel_event = None
        self.close_when_done = False
        self.events = queue.Queue()
        self.kind_var = tk.StringVar(value="screening")
        self.input_var = tk.StringVar()
        self.include_var = tk.BooleanVar(value=False)
        self.filter_var = tk.StringVar(value=FILTER_VALUES[0])
        self.scope_var = tk.StringVar(value=SCOPE_VALUES[0])
        self.label_var = tk.StringVar()
        self.type_var = tk.StringVar(value=TYPE_LABELS["unknown"])
        self.summary_var = tk.StringVar(value="尚未载入批次。已有筛选记录时选择 run.json；已有论文时选择 PDF 文件夹。")
        self.selection_var = tk.StringVar(value="尚未选择候选图")
        self.status_var = tk.StringVar(value="第一步：选择文献来源，然后开始本地扫描。")
        self.count_var = tk.StringVar(value="候选图 0 张")

        root.title("论文筛选 → 图片候选 → 数值读数 · 本地工作台")
        width = min(1450, max(1020, root.winfo_screenwidth() - 70))
        height = min(900, max(620, root.winfo_screenheight() - 100))
        root.geometry(f"{width}x{height}")
        root.minsize(980, 620)
        root.configure(bg="#f3f6fa")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", padding=(8, 5))
        style.configure("Treeview", rowheight=28)
        style.configure("Accent.TButton", background="#145d73", foreground="white")
        style.map("Accent.TButton", background=[("active", "#10738f")])
        style.configure("Muted.TLabel", foreground="#52677a")
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))

        header = tk.Frame(root, bg="#123f53", padx=16, pady=11)
        header.pack(fill="x")
        self.source_toggle_button = ttk.Button(header, text="收起扫描设置", command=self.toggle_input_panel)
        self.source_toggle_button.pack(side="right", padx=(10, 0))
        tk.Label(header, text="把筛选后的论文，接到图片读数", bg="#123f53", fg="white", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        tk.Label(header, text="① 接收筛选后的 PDF  →  ② 自动找图和裁图  →  ③ 自动生成候选数值  →  ④ 核对与导出", bg="#123f53", fg="#d7eaf0").pack(anchor="w", pady=(3, 0))
        tk.Label(root, text="选择曲线、散点或柱状图，点“自动识别并生成数值表”。新增竖柱、分组柱、横条读取；数值与标签需核对。图表用途可在右侧查看。", bg="#fff5d9", fg="#785413", anchor="w", padx=14, pady=7).pack(fill="x")

        inputs = ttk.LabelFrame(root, text="1  文献来源", padding=8)
        self.inputs_frame = inputs
        self.inputs_visible = True
        inputs.pack(fill="x", padx=12, pady=(8, 5))
        radio = ttk.Frame(inputs)
        radio.pack(fill="x")
        ttk.Radiobutton(radio, text="第一板块的筛选记录（run.json）", variable=self.kind_var, value="screening").pack(side="left")
        ttk.Radiobutton(radio, text="已经下载的 PDF 文件夹", variable=self.kind_var, value="folder").pack(side="left", padx=15)
        ttk.Checkbutton(radio, text="筛选记录中同时包含待复核论文", variable=self.include_var).pack(side="left", padx=12)
        row = ttk.Frame(inputs)
        row.pack(fill="x", pady=(5, 0))
        ttk.Entry(row, textvariable=self.input_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="选择…", command=self.choose_input).pack(side="left", padx=6)
        self.start_button = ttk.Button(row, text="开始批量扫描", command=self.start_scan, style="Accent.TButton")
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(row, text="取消扫描", command=self.cancel_scan, state="disabled")
        self.cancel_button.pack(side="left", padx=6)
        ttk.Button(row, text="打开已保存批次", command=self.choose_batch).pack(side="left")
        progress_row = ttk.Frame(inputs)
        progress_row.pack(fill="x", pady=(7, 0))
        self.progress = ttk.Progressbar(progress_row, mode="indeterminate", length=220)
        self.progress.pack(side="left", padx=(0, 10))
        ttk.Label(progress_row, textvariable=self.summary_var, wraplength=1120, style="Muted.TLabel").pack(side="left", fill="x", expand=True)

        self.status_label = tk.Label(root, textvariable=self.status_var, wraplength=1250, height=2, justify="left", anchor="w", bg="#e7eff5", fg="#17394a", padx=12, pady=5)
        self.status_label.pack(side="bottom", fill="x")
        body = ttk.Panedwindow(root, orient="horizontal")
        self.body = body
        body.pack(fill="both", expand=True, padx=12, pady=6)
        left, right = ttk.Frame(body, width=380), ttk.Frame(body)
        left.pack_propagate(False)
        body.add(left, weight=0)
        body.add(right, weight=1)
        self._build_candidate_list(left)
        self._build_preview(right)
        self.preview_frame = right

    def toggle_input_panel(self, visible=None):
        if not hasattr(self, "inputs_frame"):
            return
        show = not self.inputs_visible if visible is None else bool(visible)
        if show:
            self.inputs_frame.pack(fill="x", padx=12, pady=(8, 5), before=self.body)
        else:
            self.inputs_frame.pack_forget()
        self.inputs_visible = show
        self.source_toggle_button.configure(text="收起扫描设置" if show else "展开扫描设置")

    def _build_candidate_list(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="2  候选图列表", font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        combo = ttk.Combobox(row, textvariable=self.filter_var, values=FILTER_VALUES, width=12, state="readonly")
        combo.pack(side="right", padx=5)
        combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_list())
        scope_row = ttk.Frame(parent)
        scope_row.pack(fill="x", pady=(0, 4))
        ttk.Label(scope_row, text="显示范围").pack(side="left")
        scope = ttk.Combobox(scope_row, textvariable=self.scope_var, values=SCOPE_VALUES, width=23, state="readonly")
        scope.pack(side="left", fill="x", expand=True, padx=(6, 5))
        scope.bind("<<ComboboxSelected>>", lambda _e: self.refresh_list())
        ttk.Label(parent, textvariable=self.count_var, style="Muted.TLabel").pack(anchor="w", pady=(0, 3))
        table = ttk.Frame(parent)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("paper", "page", "label", "type", "located", "review"), show="headings", selectmode="extended")
        for key, title, width in (("paper", "论文", 175), ("page", "页", 40), ("label", "图号", 65), ("type", "候选类型", 115), ("located", "定位状态", 110), ("review", "人工状态", 75)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=40, stretch=key == "paper")
        sy = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        sx = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.tag_configure("keep", background="#e5f3ec")
        self.tree.tag_configure("exclude", foreground="#85919c")
        self.tree.tag_configure("review", background="#fff3d9")
        ttk.Label(parent, text="无图号区域暂存查漏，不需逐张审核。\n按 Ctrl 或 Shift 可多选；人工决定会逐张保存。", style="Muted.TLabel").pack(anchor="w", pady=(5, 3))
        decisions = ttk.Frame(parent)
        decisions.pack(fill="x")
        for title, value in (("保留所选", "keep"), ("排除所选", "exclude"), ("所选待复核", "review")):
            ttk.Button(decisions, text=title, command=lambda v=value: self.set_decision(v)).pack(side="left", padx=(0, 5))
        tools = ttk.LabelFrame(parent, text="批次文件", padding=7)
        tools.pack(fill="x", pady=(9, 0))
        ttk.Button(tools, text="查看待获取论文 / 扫描问题", command=self.show_issues).pack(fill="x")
        bottom = ttk.Frame(tools)
        bottom.pack(fill="x", pady=(5, 0))
        ttk.Button(bottom, text="汇总已导出数值", command=self.collect).pack(side="left", padx=(0, 5))
        ttk.Button(bottom, text="打开结果文件夹", command=self.open_results).pack(side="left")
        ttk.Label(tools, text="先选图并自动生成数值表；这里合并已经生成或重新导出的表格。",
                  wraplength=480, style="Muted.TLabel").pack(anchor="w", pady=(5, 0))

    def _build_preview(self, parent):
        ttk.Label(parent, text="3  核对原页与候选裁图", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(0, 4))
        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(0, 5))
        self.crop_button = ttk.Button(actions, text="手动裁剪图片", command=self.open_crop_dialog, style="Accent.TButton")
        self.crop_button.pack(side="left", padx=(0, 8))
        self.read_button = ttk.Button(actions, text="自动识别并生成数值表", command=self.launch_automatic_reading, style="Accent.TButton")
        self.read_button.pack(side="left")
        second_actions = ttk.Frame(parent)
        second_actions.pack(fill="x", pady=(0, 5))
        self.restore_crop_button = ttk.Button(second_actions, text="恢复原自动框", command=self.restore_original_box)
        self.restore_crop_button.pack(side="left", padx=(0, 6))
        ttk.Button(second_actions, text="复制为独立子图", command=self.duplicate_subfigure).pack(side="left")
        self.selection_label = ttk.Label(parent, textvariable=self.selection_var, style="Muted.TLabel", wraplength=720)
        self.selection_label.pack(anchor="w", fill="x", pady=(0, 4))
        self.selection_label.bind("<Configure>", lambda event: self.selection_label.configure(wraplength=max(180, event.width - 8)))
        controls = ttk.Frame(parent)
        controls.pack(fill="x", pady=(0, 4))
        ttk.Button(controls, text="适合窗口", command=lambda: self.active_view().fit()).pack(side="left")
        ttk.Button(controls, text="原图 100%", command=lambda: self.active_view().actual_size()).pack(side="left", padx=4)
        ttk.Button(controls, text="放大 ＋", command=lambda: self.active_view().zoom(1.3)).pack(side="left")
        ttk.Button(controls, text="缩小 −", command=lambda: self.active_view().zoom(1 / 1.3)).pack(side="left", padx=4)
        ttk.Button(controls,text='图表类型与用途',command=self.show_chart_catalog).pack(side='left',padx=6)
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill="both", expand=True)
        self.page_view = ImageView(self.notebook, on_box=self.open_crop_dialog)
        self.crop_view = ImageView(self.notebook)
        self.notebook.add(self.page_view, text="整页与定位框")
        self.notebook.add(self.crop_view, text="候选裁图")
        details = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(details, text="图注与图信息")
        ttk.Label(details, text="自动识别可尝试拆分组合图。裁剪时请包含坐标轴、类别和图例；改变范围后需重新取数。", wraplength=700, style="Muted.TLabel").pack(anchor="w", pady=(0, 7))
        ttk.Button(details, text="哪些图对预测有用？查看图表类型说明", command=self.show_chart_catalog).pack(anchor='w',pady=(0,7))
        edits = ttk.Frame(details)
        edits.pack(fill="x", pady=(0, 7))
        ttk.Label(edits, text="图号 / 子图").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(edits, textvariable=self.label_var, width=18).grid(row=0, column=1, sticky="w", padx=5)
        ttk.Label(edits, text="图型").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Combobox(edits, textvariable=self.type_var, values=list(TYPE_LABELS.values()), state="readonly", width=18).grid(row=1, column=1, sticky="w", padx=5)
        ttk.Button(edits, text="保存图号 / 图型", command=self.save_edits).grid(row=2, column=0, columnspan=2, sticky="w", pady=(5, 0))
        text_frame = ttk.Frame(details)
        text_frame.pack(fill="both", expand=True)
        self.caption_text = tk.Text(text_frame, height=4, wrap="word", relief="flat", bg="#f7f9fc", font=("Microsoft YaHei UI", 10))
        ys = ttk.Scrollbar(text_frame, orient="vertical", command=self.caption_text.yview)
        self.caption_text.configure(yscrollcommand=ys.set)
        self.caption_text.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.caption_text.configure(state="disabled")

    def active_view(self):
        return self.crop_view if self.notebook.index("current") == 1 else self.page_view

    def show_chart_catalog(self):
        guide=Path(__file__).resolve().parent.parent/'15_图表类型与预测用途.html'
        if guide.is_file():os.startfile(str(guide))
        else:
            from digitizer.chart_catalog import CATALOG
            window=tk.Toplevel(self.root);window.title('图表类型与预测用途');window.geometry('860x620')
            box=tk.Text(window,wrap='word',padx=15,pady=15,font=('Microsoft YaHei UI',11));box.pack(fill='both',expand=True)
            for row in CATALOG:box.insert('end',row[0]+'\n示例：'+row[1]+'\n用途：'+row[2]+'\n本版：'+row[3]+'\n需核对：'+row[4]+'\n\n')
            box.configure(state='disabled')

    def set_status(self, text):
        self.status_var.set(str(text))

    def show_error(self, error):
        self.set_status("未完成：" + str(error))
        messagebox.showerror("处理未完成", str(error), parent=self.root)

    def choose_input(self):
        if self.kind_var.get() == "folder":
            path = filedialog.askdirectory(parent=self.root, title="选择已下载论文的 PDF 文件夹")
        else:
            path = filedialog.askopenfilename(parent=self.root, title="选择第一板块的 run.json", filetypes=[("筛选记录", "*.json"), ("所有文件", "*.*")])
        if path:
            self.input_var.set(path)

    def choose_downloaded_folder(self):
        if not self.busy:
            self.toggle_input_panel(True)
            self.kind_var.set("folder")
            self.choose_input()
            self.set_status("选择文件夹后点击“开始批量扫描”。直接导入 PDF 只代表文件可读取，不代表论文已通过相关性筛选。")

    def choose_batch(self, path=None):
        if self.busy:
            self.set_status("正在扫描，请先等待完成或取消扫描。")
            return
        if path is None:
            path = filedialog.askopenfilename(parent=self.root, title="打开本工具保存的批次 JSON", filetypes=[("批次记录", "*.json"), ("所有文件", "*.*")])
        if path:
            try:
                self.set_batch(load_batch(str(path)))
            except Exception as exc:
                self.show_error(exc)

    def start_scan(self):
        if self.busy:
            return
        path = self.input_var.get().strip()
        if not path or not Path(path).exists():
            messagebox.showinfo("选择文献来源", "请先选择现有的筛选记录 run.json，或存有 PDF 的文件夹。", parent=self.root)
            return
        self.save_edits(silent=True)
        self.busy = True
        self.cancel_event = threading.Event()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.set_status("正在扫描本地论文；候选图和图注都需要后续人工核对。")
        kind, include = self.kind_var.get(), bool(self.include_var.get())
        def report(*args, **kwargs):
            value = args[0] if len(args) == 1 and not kwargs else {"args": list(args), **kwargs}
            self.events.put(("progress", value))
        def worker():
            try:
                result = build_batch(path, output_root=self.output_root, input_kind=kind, include_review=include, progress=report, cancel_event=self.cancel_event)
                self.events.put(("done", result))
            except Exception as exc:
                self.events.put(("error", exc))
        threading.Thread(target=worker, daemon=True).start()
        self.root.after(80, self.poll_events)

    def poll_events(self):
        finished = False
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                self.show_progress(payload)
            else:
                finished = True
                self.busy = False
                self.progress.stop()
                self.start_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
                if kind == "done":
                    was_cancelled = self.cancel_event is not None and self.cancel_event.is_set()
                    self.set_batch(payload)
                    if was_cancelled:
                        self.set_status("扫描已取消，已完成部分已载入。可核对现有候选，或另开新批次继续扫描。")
                elif not self.close_when_done:
                    self.show_error(payload)
        if self.close_when_done and finished:
            self.root.destroy()
        elif self.busy:
            self.root.after(80, self.poll_events)

    def show_progress(self, payload):
        if isinstance(payload, dict):
            text = payload.get("message") or payload.get("status") or payload.get("stage")
            current = payload.get("current", payload.get("completed", payload.get("done")))
            total = payload.get("total")
            if text is None:
                text = "；".join(f"{key}: {value}" for key, value in payload.items() if value is not None)
            if isinstance(current, (int, float)) and isinstance(total, (int, float)) and total > 0:
                self.progress.stop()
                self.progress.configure(mode="determinate", maximum=total, value=current)
                text = f"{current:g} / {total:g} · {text}"
        else:
            text = str(payload)
        self.summary_var.set(str(text))

    def cancel_scan(self):
        if self.cancel_event is not None and self.busy:
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.set_status("已请求取消。正在结束当前操作并保存已完成部分，请稍候。")

    def on_close(self):
        if self.busy:
            self.close_when_done = True
            self.cancel_scan()
        else:
            self.save_edits(silent=True)
            self.root.destroy()

    def set_batch(self, batch):
        self.batch = batch
        self.toggle_input_panel(False)
        self.current_id = None
        self.current_page_path = None
        self.filter_var.set(FILTER_VALUES[0])
        self.scope_var.set(SCOPE_VALUES[0])
        self.page_view.set_image(None)
        self.crop_view.set_image(None)
        self.set_caption("")
        self.label_var.set("")
        self.type_var.set(TYPE_LABELS["unknown"])
        self.selection_var.set("尚未选择候选图")
        papers = len(batch.get("papers", []))
        numbered, unmatched = split_candidates(batch)
        counts = self.waiting_counts(batch)
        errors = len(batch.get("errors", []))
        self.summary_var.set(f"入选待获取 PDF {counts['missing_pdf']} 篇 · 实际可扫描 PDF {papers} 篇 · 筛选待复核 {counts['review']} 篇 · 其他待处理 {counts['other']} 项 · 有图号 {len(numbered)} 张 · 无图号暂存 {len(unmatched)} 张 · 扫描问题 {errors} 项")
        self.refresh_list()
        if numbered:
            self.set_status("默认只显示有编号图注的图片。选择曲线、散点或柱状图，可一键尝试自动读数。")
        elif unmatched:
            self.set_status("未识别到编号图注，原始候选仍保留。可将“显示范围”切换到“未识别图号（查漏）”检查原页。")
        else:
            self.set_status(self.empty_batch_message().replace("\n", " "))

    @staticmethod
    def waiting_counts(batch):
        counts = {"missing_pdf": 0, "review": 0, "other": 0}
        for item in batch.get("waiting", []):
            reason = item.get("reason_code", "") if isinstance(item, dict) else ""
            key = "missing_pdf" if reason in ("pdf_not_downloaded", "fulltext_not_local_pdf") else "review" if reason in ("screening_review_required", "unknown_manual_decision", "unknown_screening_decision") else "other"
            counts[key] += 1
        return counts

    def empty_batch_message(self):
        batch = self.batch or {}
        papers = batch.get("papers", [])
        counts = self.waiting_counts(batch)
        if batch.get("status") == "cancelled":
            return "扫描已取消，目前没有候选图。\n\n已完成部分仍保留；可检查扫描问题后重新开始。"
        if not papers:
            if counts["missing_pdf"]:
                return (f"尚无已下载 PDF 可扫描\n\n{counts['missing_pdf']} 篇入选论文尚未关联本地 PDF；另有 {counts['review']} 篇仍待筛选复核。\n"
                        "保留论文或获得 DOI，并不等于已下载全文。\n请在第一板块关联真实下载的 PDF，或选择已下载文件夹。直接导入文件夹不代表筛选通过。")
            if counts["review"]:
                return f"论文仍待筛选复核，尚无可扫描 PDF\n\n当前 {counts['review']} 篇需先核对筛选决定。请查看“待获取论文 / 扫描问题”，再决定哪些论文进入图片处理。"
            return "本批次没有可扫描 PDF\n\n请查看“待获取论文 / 扫描问题”中的路径、文件类型或筛选状态，再关联可读取的 PDF。"
        scanned = sum(p.get("scan_status") == "scanned" for p in papers)
        if batch.get("errors") and not scanned:
            return "扫描失败，暂时没有候选图\n\n请查看“待获取论文 / 扫描问题”中的具体原因；没有候选不代表原文没有图片。"
        return "已扫描，但未找到候选图\n\n请人工检查 PDF 原页与图注，并查看扫描问题。自动定位未找到候选，不代表论文没有可用图片或数据。"

    def find_figure(self, figure_id=None):
        wanted = figure_id or self.current_id
        if not self.batch or not wanted:
            return None
        return next((f for f in self.batch.get("figures", []) if str(f.get("figure_id")) == str(wanted)), None)

    @staticmethod
    def locator_label(value):
        return {
            "manual": "人工框选", "manual_corrected": "人工修正", "caption": "图注辅助定位",
            "caption_guided": "图注辅助定位", "embedded_image": "嵌入图像候选",
            "image_block": "嵌入图像候选", "page_fallback": "整页候选 / 待定位",
            "fallback": "待定位", "located": "自动定位 / 待核对",
            "graphics_caption_candidate": "图形与图注候选",
            "graphics_without_caption": "图形候选 / 缺图注",
        }.get(str(value), str(value or "待定位"))

    def refresh_list(self):
        if not self.batch:
            return
        selected = list(self.tree.selection())
        self.refreshing_list = True
        self.tree.delete(*self.tree.get_children())
        wanted = self.filter_var.get()
        all_figures = self.batch.get("figures", [])
        numbered, unmatched = split_candidates(self.batch)
        if getattr(self,'paper_scope_id',None):
            all_figures=[f for f in all_figures if f.get('paper_id')==self.paper_scope_id]
            numbered=[f for f in numbered if f.get('paper_id')==self.paper_scope_id]
            unmatched=[f for f in unmatched if f.get('paper_id')==self.paper_scope_id]
        scope = self.scope_var.get()
        figures = numbered if scope == SCOPE_VALUES[0] else unmatched if scope == SCOPE_VALUES[1] else all_figures
        displayed = []
        for figure in figures:
            state = figure.get("review_status", "unreviewed")
            if wanted != FILTER_VALUES[0] and REVIEW_LABELS.get(state, state) != wanted:
                continue
            identifier = str(figure["figure_id"])
            displayed.append(identifier)
            title = figure.get("title") or figure.get("doi") or figure.get("paper_id", "未命名论文")
            self.tree.insert("", "end", iid=identifier, values=(title, figure.get("page", ""), figure.get("figure_label", "") or "待确认", TYPE_LABELS.get(figure.get("chart_type"), TYPE_LABELS["unknown"]), self.locator_label(figure.get("locator_status")), REVIEW_LABELS.get(state, state)), tags=(state,))
        self.count_var.set(f"显示 {len(displayed)} 张 · 有图号 {len(numbered)} 张 · 暂存查漏 {len(unmatched)} 张")
        valid = [item for item in selected if item in displayed]
        if self.current_id in displayed and self.current_id not in valid:
            valid.insert(0, self.current_id)
        if not valid and displayed:
            valid = [displayed[0]]
        if valid:
            self.tree.selection_set(valid)
            self.tree.focus(self.current_id if self.current_id in valid else valid[0])
        self.refreshing_list = False
        if valid:
            self.on_select()
        else:
            self.current_id = None
            self.page_view.set_image(None)
            self.crop_view.set_image(None)
            if all_figures:
                if scope == SCOPE_VALUES[0] and not numbered:
                    message = "未识别到编号图注\n\n无图号区域已暂存，可能包括扫描页或未识别到的图注。\n将左侧“显示范围”切换到“未识别图号（查漏）”可查看；不需要重新导入 PDF。"
                elif scope == SCOPE_VALUES[1] and not unmatched:
                    message = "没有需要查漏的无图号区域。\n\n将左侧“显示范围”切回“有图号的图片（默认）”即可继续。"
                else:
                    message = "当前显示条件下没有候选图。\n\n可将人工状态改为“全部候选”，或调整左侧“显示范围”。"
            else:
                message = self.empty_batch_message()
            self.page_view.set_empty_message(message, action=self.choose_downloaded_folder if not all_figures else None)
            self.crop_view.set_empty_message(message)
            self.selection_var.set("当前显示条件下没有候选图" if all_figures else "尚无候选图，请先查看下方原因")
            self.set_caption("")

    def on_select(self, _event=None):
        if self.refreshing_list or not self.batch:
            return
        selected = self.tree.selection()
        if not selected:
            return
        focus = self.tree.focus()
        new_id = focus if focus in selected else selected[0]
        if self.current_id != new_id:
            self.save_edits(silent=True, refresh=False)
        self.current_id = new_id
        self.load_current_preview()

    def load_current_preview(self, preserve_zoom=False):
        figure = self.find_figure()
        if not figure:
            return
        self.loading_details = True
        self.label_var.set(figure.get("figure_label", ""))
        self.type_var.set(TYPE_LABELS.get(figure.get("chart_type"), TYPE_LABELS["unknown"]))
        doi = figure.get("doi") or "DOI 待确认"
        title = str(figure.get('title') or figure.get('paper_id', ''))
        title = title if len(title) <= 75 else title[:72] + "…"
        self.selection_var.set(f"{title}\n{doi} · 第 {figure.get('page', '?')} 页 · {REVIEW_LABELS.get(figure.get('review_status'), '未核对')}")
        try:
            page_path = figure.get("page_image_path")
            if page_path and Path(page_path).is_file():
                if page_path != self.current_page_path or self.page_view.original is None:
                    with Image.open(page_path) as source:
                        page_image = source.convert("RGB").copy()
                    self.page_view.set_image(page_image, box=figure.get("bbox"), reset=not preserve_zoom)
                    self.current_page_path = page_path
                else:
                    self.page_view.box = figure.get("bbox")
                    self.page_view.draw()
            else:
                self.page_view.set_image(None)
                self.current_page_path = None
            crop_path = figure.get("crop_path")
            if crop_path and Path(crop_path).is_file():
                with Image.open(crop_path) as source:
                    crop_image = source.convert("RGB").copy()
                self.crop_view.set_image(crop_image)
            elif self.page_view.original is not None and figure.get("bbox"):
                self.crop_view.set_image(self.page_view.original.crop(tuple(figure["bbox"])))
            else:
                self.crop_view.set_image(None)
        except Exception as exc:
            self.set_status("候选记录已载入，但预览图片无法打开：" + str(exc))
        flags = figure.get("quality_flags", [])
        caption = figure.get("caption") or "未识别到可靠图注，请对照原页核实。"
        text = describe_chart(caption)+f"\n\n定位：{self.locator_label(figure.get('locator_status'))}\n图注：{caption}"
        if flags:
            text += "\n\n待核实：\n" + "\n".join("• " + FLAG_LABELS.get(str(flag), str(flag)) for flag in flags)
        self.set_caption(text)
        self.loading_details = False

    def set_caption(self, text):
        self.caption_text.configure(state="normal")
        self.caption_text.delete("1.0", "end")
        self.caption_text.insert("1.0", text)
        self.caption_text.configure(state="disabled")

    def correct_box(self, bbox):
        if not self.current_id or self.busy:
            return False
        try:
            self.batch = update_figure(self.batch, self.current_id, decision="review", bbox=bbox,
                                       figure_label=self.label_var.get().strip(), chart_type=self.current_type())
            if self.filter_var.get() not in (FILTER_VALUES[0], REVIEW_LABELS["review"]):
                self.filter_var.set(FILTER_VALUES[0])
            self.load_current_preview(preserve_zoom=True)
            self.refresh_list()
            self.notebook.select(self.crop_view)
            self.set_status("新定位框已保存，候选已标为待复核。若此前已有读数，旧框对应读数将视为过期；请按新框重新取数。")
            return True
        except Exception as exc:
            self.show_error(exc)
            return False

    def open_crop_dialog(self, initial_bbox=None):
        if self.busy:
            return None
        figure = self.find_figure()
        if not figure:
            messagebox.showinfo("先选择候选图", "请先在左侧列表选择一张候选图，再手动裁剪。", parent=self.root)
            return None
        try:
            with Image.open(figure["page_image_path"]) as source:
                page_image = source.convert("RGB").copy()
            return CropDialog(self.root, page_image, initial_bbox or figure.get("bbox"), self.correct_box)
        except Exception as exc:
            self.show_error(exc)
            return None

    def restore_original_box(self):
        figure = self.find_figure()
        if not figure or self.busy:
            return False
        original = figure.get("original_bbox")
        if not original:
            messagebox.showinfo("没有原自动框记录", "此候选没有保存原自动框。整页原图仍可在“手动裁剪图片”中重新框选。", parent=self.root)
            return False
        if self.correct_box(list(original)):
            self.set_status("已恢复原自动定位框，整页原图仍保留。请重新核对裁图；先前其他范围的读数不会自动恢复为当前读数。")
            return True
        return False

    def duplicate_subfigure(self):
        if self.busy or not self.current_id:
            return
        self.save_edits(silent=True, refresh=False)
        try:
            new_id = create_subfigure(self.batch, self.current_id)
            self.tree.selection_remove(self.tree.selection())
            self.current_id = str(new_id)
            self.filter_var.set(FILTER_VALUES[0])
            self.refresh_list()
            self.tree.see(self.current_id)
            self.set_status("已创建独立子图候选。请拖框仅保留目标子图，并修改图号，如 Fig. 1(a)。其他子图请从原组合图再次复制；各子图读数分别保存。")
        except Exception as exc:
            self.show_error(exc)

    def current_type(self):
        return next((key for key, label in TYPE_LABELS.items() if label == self.type_var.get()), "unknown")

    def save_edits(self, silent=False, refresh=True):
        if self.loading_details or self.busy:
            return
        figure = self.find_figure()
        if not figure:
            return
        label, chart_type = self.label_var.get().strip(), self.current_type()
        if label == (figure.get("figure_label") or "") and chart_type == figure.get("chart_type", "unknown"):
            return
        try:
            self.batch = update_figure(self.batch, self.current_id, decision="review", figure_label=label, chart_type=chart_type)
            if refresh:
                self.refresh_list()
            if not silent:
                self.set_status("图号 / 图型已保存，并标为待复核。核对后可重新标记保留。")
        except Exception as exc:
            if not silent:
                self.show_error(exc)
            else:
                self.set_status("图号 / 图型尚未保存：" + str(exc))

    def set_decision(self, decision):
        if self.busy or not self.batch:
            return
        ids = list(self.tree.selection())
        if not ids:
            messagebox.showinfo("先选择候选图", "请在列表中选中一张或多张候选图。", parent=self.root)
            return
        self.save_edits(silent=True, refresh=False)
        try:
            for identifier in ids:
                self.batch = update_figure(self.batch, identifier, decision=decision)
            self.refresh_list()
            self.set_status(f"已把 {len(ids)} 张候选图标为“{REVIEW_LABELS[decision]}”。人工决定已保存。")
        except Exception as exc:
            self.show_error(exc)

    def launch_digitizer(self):
        if self.busy:
            return
        figure = self.find_figure()
        if not figure:
            messagebox.showinfo("先选择候选图", "请先选择一张候选图，并核对整页、定位框及图注。", parent=self.root)
            return
        self.save_edits(silent=True, refresh=False)
        figure = self.find_figure()
        if figure.get("review_status") == "exclude" or figure.get("chart_type") == "non_numeric":
            messagebox.showinfo("当前候选不进入读数", "已排除的图和“本版不读数”图型不能进入数值恢复。若判断有误，请先修改图型及人工状态。", parent=self.root)
            return
        try:
            path = open_digitizer(self.batch, self.current_id)
            self.set_status(f"已打开本地读数工具。先标定坐标轴再取点；读取后请导出。读数项目：{path}")
        except Exception as exc:
            self.show_error(exc)

    def launch_automatic_reading(self):
        if self.busy:
            return
        figure = self.find_figure()
        if not figure:
            messagebox.showinfo("先选择图片", "请先选择一张候选图，核对裁图范围。", parent=self.root)
            return
        self.save_edits(silent=True, refresh=False)
        figure = self.find_figure()
        if figure.get("review_status") == "exclude" or figure.get("chart_type") == "non_numeric":
            messagebox.showinfo("这张图不进入自动取数", "当前图已排除或标为本版不读数。请选择有数值含义的曲线、散点或柱状图；图号和图型可在图信息页签中核对。", parent=self.root)
            return
        try:
            path = open_digitizer(self.batch, self.current_id, auto_read=True)
            self.set_status(f"已进入自动读数：程序会识别坐标、图例和数据点，再生成待核对数值表。结果在新窗口显示。项目：{path}")
        except Exception as exc:
            self.show_error(exc)

    def open_results(self):
        if not self.batch:
            messagebox.showinfo("尚无批次", "请先扫描论文或打开已保存批次。", parent=self.root)
            return
        try:
            os.startfile(str(Path(self.batch["run_dir"])))
        except Exception as exc:
            self.show_error(exc)

    def collect(self):
        if not self.batch or self.busy:
            return
        self.save_edits(silent=True, refresh=False)
        if not self.batch.get("reading_sessions"):
            self.set_status("当前还没有图上的数值。先选择曲线、散点或柱状图，点击右侧上方“自动识别并生成数值表”。")
            messagebox.showinfo("还没有可汇总的数值", "这个按钮汇总已经导出的读数。当前还没有读数项目。\n\n"
                                "请按顺序操作：\n1. 选择一张曲线、散点或柱状图。\n2. 点右侧上方“自动识别并生成数值表”。\n"
                                "3. 等待自动读取坐标、图例和数据点。成功后会生成 CSV。\n"
                                "4. 在读数窗口点“打开导出文件夹”，并核对结果。\n5. 如需合并多张图，再回这里汇总。", parent=self.root)
            return
        exported = False
        read_problems = []
        for ref in self.batch["reading_sessions"]:
            try:
                session = json.loads(Path(ref["session_path"]).read_text(encoding="utf-8-sig"))
                exported = exported or bool(session.get("exports"))
            except Exception as exc:
                read_problems.append(str(exc))
        if not exported:
            self.set_status("读数项目尚未生成数值表。请查看自动识别进度或失败原因；成功后再汇总。")
            detail = "\n\n部分项目无法读取：\n" + "\n".join(read_problems[:3]) if read_problems else ""
            messagebox.showinfo("先在读数窗口导出", "已打开读数项目，但还没有已导出的数值表。\n\n"
                                "请先在读数窗口点击“一键识别并生成数值表”。若识别失败，查看提示并核对图型及裁图范围。\n"
                                "自动生成后可直接汇总；人工修正的结果需重新点击“导出数值表与证据”。" + detail, parent=self.root)
            return
        try:
            path = collect_readings(self.batch)
            summary = json.loads((Path(path).parent / "汇总说明.json").read_text(encoding="utf-8"))
            count = summary.get("row_count", 0)
            if not count:
                reasons = list(dict.fromkeys(item.get("reason", "") for item in summary.get("skipped", []) if item.get("reason")))
                self.set_status("没有可汇总的有效数值。请核对读数是否已经导出，以及裁剪范围是否在导出后发生变化。")
                messagebox.showinfo("没有可汇总的有效数值", "本次为 0 条数值，尚未得到有效汇总。\n\n"
                                    + ("原因：\n" + "\n".join(reasons[:5]) if reasons else "请先在读数窗口取点并导出。")
                                    + "\n\n检查记录：\n" + str(Path(path).parent / "汇总说明.json"), parent=self.root)
                return
            self.set_status(f"已汇总 {count} 条数值：{path}")
            messagebox.showinfo("汇总完成", f"已汇总 {count} 条数值，可用 Excel 打开：\n\n{path}\n\n"
                                "采用各读数项目最近一次主动导出的内容。未纳入原因记录在同目录的汇总说明文件中。", parent=self.root)
        except Exception as exc:
            self.show_error(exc)

    def show_issues(self):
        if not self.batch:
            return
        window = tk.Toplevel(self.root)
        window.title("待获取论文与扫描问题")
        window.geometry("920x670")
        ttk.Label(window, text="没有本地 PDF 的条目需要先取得全文。扫描失败或未找到候选图，不等于论文中没有可用数据。", wraplength=870).pack(anchor="w", padx=12, pady=10)
        area = tk.Text(window, wrap="word", font=("Microsoft YaHei UI", 10))
        area.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        def describe(item):
            if isinstance(item, dict):
                return "\n".join(f"{key}: {value}" for key, value in item.items())
            return str(item)
        text = "待获取 / 待处理论文\n\n"
        waiting = self.batch.get("waiting", [])
        text += "\n\n".join(describe(item) for item in waiting) if waiting else "无记录"
        text += "\n\n————————\n扫描问题\n\n"
        errors = self.batch.get("errors", [])
        text += "\n\n".join(describe(item) for item in errors) if errors else "无记录"
        text += "\n\n————————\n输入统计\n\n" + describe(self.batch.get("input_summary", {}))
        report = selection_report(self.batch)
        text += (f"\n\n————————\n图号筛选\n\n有编号图注 {report['numbered_candidate_count']} 张；"
                 f"无图号暂存 {report['unmatched_candidate_count']} 张。\n"
                 "主列表只显示有编号图注的图片。其余区域可在左侧“显示范围 → 未识别图号（查漏）”查看；人工判定和已有读数均保留。")
        missing = [p for p in report["papers"] if not p["numbered_candidate_count"] and p["scan_status"] == "scanned"]
        if missing:
            text += "\n\n以下论文尚未识别到编号图注，不代表原文没有图片：\n" + "\n".join(p["title"] or p["doi"] or p["paper_id"] for p in missing)
        area.insert("1.0", text)
        area.configure(state="disabled")


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地论文图片候选与读数衔接工具")
    parser.add_argument("--output-root")
    parser.add_argument("--paper-id",help="只显示该论文，保留整个批次与既有审核记录")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--open-batch")
    sources.add_argument("--screening-run")
    sources.add_argument("--pdf-folder")
    parser.add_argument("--auto-scan", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    root = tk.Tk()
    if args.smoke:
        root.withdraw()
    app = FigurePipelineApp(root, output_root=args.output_root)
    app.paper_scope_id=args.paper_id
    if args.screening_run:
        app.kind_var.set("screening")
        app.input_var.set(args.screening_run)
    elif args.pdf_folder:
        app.kind_var.set("folder")
        app.input_var.set(args.pdf_folder)
    if args.open_batch:
        if args.smoke:
            app.set_batch(load_batch(args.open_batch))
        else:
            root.after(100, lambda: app.choose_batch(args.open_batch))
    elif args.auto_scan and not args.smoke and (args.screening_run or args.pdf_folder):
        root.after(150, app.start_scan)
    if args.smoke:
        root.update_idletasks()
        root.update()
        root.destroy()
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
