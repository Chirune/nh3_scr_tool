"""Readable, local controls for separate evidence and performance scores."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import paper_scoring as scoring
from paper_app import table, text_area, show_text


class ScoringPanel:
    def __init__(self, owner, frame):
        self.owner, self.frame = owner, frame
        self.config = scoring.default_config()
        self.annotations = {}
        self.report = None
        self.buttons = []
        ttk.Label(frame, text='质量：资料与核验有多完整　｜　性能：当前指标离既定目标有多近',
                  font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='两种分数分别显示。未知条件会列为待补；未核验记录不评分。分数不会进入 X 或替代 y。',
                  wraplength=940).pack(anchor='w', pady=5)
        actions = ttk.Frame(frame); actions.pack(fill='x', pady=5)
        for label, action in (('调整评分模板', self.configure), ('补充所选记录的比较依据', self.annotate),
                              ('读取已保存评分方案', self.load_settings)):
            button = ttk.Button(actions, text=label, command=action)
            button.pack(side='left', padx=(0, 8)); self.buttons.append(button)
        self.summary = tk.StringVar(value='生成编码预览后，这里会同步显示双评分。')
        ttk.Label(frame, textvariable=self.summary, wraplength=940).pack(anchor='w', pady=4)
        box, self.tree = table(frame, [('sample', '样品 / 实验'), ('quality', '质量证据分'),
            ('performance', '单项性能分'), ('raw', '统一单位原值'), ('comparison', '比较条件')], [210, 100, 100, 140, 400], 5)
        box.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda event: self.describe())
        box, self.detail = text_area(frame, 11); box.pack(fill='both', expand=True, pady=(6, 0))

    def set_busy(self, busy):
        for button in self.buttons:
            button.configure(state='disabled' if busy else 'normal')

    def clear(self):
        self.report = None
        self.tree.delete(*self.tree.get_children())
        self.summary.set('数据或任务已改变，请重新生成编码预览。')
        show_text(self.detail, '补充信息保留在本次窗口中；记录内容变化后，旧补充不会自动生效。')

    def refresh(self):
        result = self.owner.result
        if result is None:
            self.clear(); return
        selected = next(iter(self.tree.selection()), None)
        self.report = scoring.score_encoding(result, self.config, self.annotations)
        self.tree.delete(*self.tree.get_children())
        for i, card in enumerate(self.report['cards']):
            comp, perf = card['comparison'], card['performance']
            comparison = ('待补 ' + str(len(comp['missing'])) + ' 项；暂不能横向比较') if comp['missing'] else (
                f"同条件 {comp['group_samples']} 个样品 / {comp['group_papers']} 篇；组 {comp['group_id'][:6]}")
            self.tree.insert('', 'end', iid=str(i), values=(card['sample_label'] + ' / ' + card['experiment_id'],
                card['quality']['score'], '未定义目标' if perf['score'] is None else perf['score'],
                f"{perf['value']:g} {perf['unit']}", comparison))
        s = self.report['summary']
        self.summary.set(f"质量评分 {s['quality_scored']} 条；性能评分 {s['performance_scored']} 条；"
            f"具有多个样品的严格同条件组 {s['groups_with_multiple_samples']} 个；未通过检查、不评分 {s['excluded_not_scored']} 条。"
            + (' 有失效补充，请查看所选记录。' if self.report['annotation_issues'] else '')
            + (' 保存方案中另有不属于当前预览的补充，未使用。' if self.report['unused_annotation_ids'] else ''))
        if self.report['cards']:
            self.tree.selection_set(selected if selected and self.tree.exists(selected) else '0')
            self.describe()
        else:
            show_text(self.detail, '没有通过编码检查的记录，未生成分数。\n\n' + '\n'.join(
                item.get('sample_label', '') + '：' + '；'.join(item['reasons']) for item in self.report['excluded']))

    def describe(self):
        if self.report and self.tree.selection():
            card = self.report['cards'][int(self.tree.selection()[0])]
            show_text(self.detail, scoring.describe_card(card, self.report))

    def configure(self):
        if self.owner.busy:
            return
        task = self.owner.result['task_id'] if self.owner.result else next((k for k, v in
            __import__('paper_workspace').TASKS.items() if v['title'] == self.owner.task.get() and k != 'pending'), 'scr_conversion')
        def saved(config):
            self.config = config; self.owner.exported = None; self.refresh()
            self.owner.status.set('新模板已应用；导出时会一起保存规则、依据和版本指纹。')
        return ConfigDialog(self.owner.root, self.config, task, saved)

    def annotate(self):
        if self.owner.busy or not self.owner.result or not self.tree.selection():
            self.owner.status.set('先生成编码预览，再在双评分表中选择一条记录。'); return
        row = self.owner.result['observations'][int(self.tree.selection()[0])]
        def saved(annotation):
            self.annotations[row['observation_id']] = annotation
            self.owner.exported = None; self.refresh()
            self.owner.status.set('比较补充已用于当前预览，尚未写入原论文档案；请导出编码结果保存这份评分方案。')
        return AnnotationDialog(self.owner.root, row, self.annotations.get(row['observation_id']), saved)

    def load_settings(self):
        if self.owner.busy:
            return
        path = filedialog.askopenfilename(parent=self.owner.root, title='选择评分模板.json或评分方案与补充.json',
                                          filetypes=[('评分方案', '*.json')])
        if not path:
            return
        try:
            self.read_settings(path)
        except (ValueError, OSError, TypeError, KeyError) as exc:
            messagebox.showerror('方案未读取', str(exc), parent=self.owner.root)

    def read_settings(self, path):
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('评分方案的结构不正确。')
        if 'config' in data:
            if set(data) != {'schema_version', 'config', 'annotations'} or data['schema_version'] != scoring.VERSION or not isinstance(data['annotations'], dict):
                raise ValueError('不是本工具导出的评分方案与补充。')
            config = scoring.validate_config(data['config'])
            annotations = data['annotations']
        else:
            config = scoring.validate_config(data)
            annotations = copy.deepcopy(self.annotations)
        if self.owner.result:
            scoring.score_encoding(self.owner.result, config, annotations)
        self.config, self.annotations = config, annotations
        self.owner.exported = None; self.refresh()
        self.owner.status.set('已读取评分方案；缺少匹配证据或对应旧记录的补充会显示为失效。')


class ConfigDialog(tk.Toplevel):
    def __init__(self, parent, config, task, saved):
        super().__init__(parent)
        self.config_data = copy.deepcopy(config); self.task = task; self.saved = saved
        self.title('评分模板：质量与性能分别配置'); self.geometry('870x730'); self.minsize(790, 690)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        area = ttk.Frame(self, padding=15); area.pack(fill='both', expand=True)
        ttk.Label(area, text='质量权重可调整，来源核验等纳入条件始终保留。阈值请依据研究目标预先确定。', wraplength=820).pack(anchor='w')
        self.name = tk.StringVar(value=config['name'])
        ttk.Entry(area, textvariable=self.name).pack(fill='x', pady=8)
        quality = ttk.LabelFrame(area, text='数据质量：六维度权重（自动换算为合计100%）', padding=10); quality.pack(fill='x')
        self.weights = {}
        for i, (key, (label, _)) in enumerate(scoring.DIMENSIONS.items()):
            self.weights[key] = tk.StringVar(value=str(config['quality_weights'][key]))
            ttk.Label(quality, text=label).grid(row=i // 2, column=(i % 2) * 2, sticky='w', padx=5, pady=5)
            ttk.Entry(quality, textvariable=self.weights[key], width=10).grid(row=i // 2, column=(i % 2) * 2 + 1, padx=(5, 45))
        profile = config['profiles'][task]
        import paper_workspace as work
        performance = ttk.LabelFrame(area, text=work.TASKS[task]['title'] + ' · 单项性能规则', padding=10)
        performance.pack(fill='x', pady=12)
        self.mode = tk.StringVar(value=scoring.MODES[profile['mode']])
        ttk.Combobox(performance, textvariable=self.mode, values=list(scoring.MODES.values()), state='readonly', width=30).grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(performance, text='阈值单位：' + profile['unit'] + '（比例填0至1）').grid(row=0, column=2, columnspan=2, sticky='w')
        self.values = {}
        for i, (key, label) in enumerate((('anchor_low', '下边界'), ('anchor_high', '上边界'), ('target_low', '目标区间下限'), ('target_high', '目标区间上限'))):
            self.values[key] = tk.StringVar(value='' if profile[key] is None else str(profile[key]))
            ttk.Label(performance, text=label).grid(row=i // 2 + 1, column=(i % 2) * 2, sticky='w', pady=5)
            ttk.Entry(performance, textvariable=self.values[key], width=18).grid(row=i // 2 + 1, column=(i % 2) * 2 + 1, padx=7)
        ttk.Label(performance, text='越高越好：下边界0分、上边界100分；越低越好相反。区间模式：两侧边界0分、目标区间100分。', wraplength=770).grid(row=3, column=0, columnspan=4, sticky='w', pady=5)
        self.goal = tk.StringVar(value=profile['goal']); self.rationale = tk.StringVar(value=profile['rationale'])
        for label, variable in (('评价目标', self.goal), ('阈值依据（请写明来源或研究理由）', self.rationale)):
            ttk.Label(area, text=label).pack(anchor='w'); ttk.Entry(area, textvariable=variable).pack(fill='x', pady=(3, 8))
        ttk.Label(area, text='默认权重是待校准的起点。STY、TOF、CO选择性和吸附能先保留原值；需要时再明确目标。\n本操作只改变评分，不改变已核验数据与X/y。', wraplength=810).pack(anchor='w', pady=5)
        actions = ttk.Frame(area); actions.pack(fill='x', side='bottom')
        ttk.Button(actions, text='应用并重新评分', command=self.save).pack(side='right')
        ttk.Button(actions, text='取消', command=self.destroy).pack(side='right', padx=8)

    def save(self):
        try:
            config = copy.deepcopy(self.config_data); config['name'] = self.name.get().strip()
            config['quality_weights'] = {key: float(value.get()) for key, value in self.weights.items()}
            profile = config['profiles'][self.task]
            profile['mode'] = next(key for key, value in scoring.MODES.items() if value == self.mode.get())
            for key, value in self.values.items():
                profile[key] = float(value.get()) if value.get().strip() else None
            profile.update(goal=self.goal.get().strip(), rationale=self.rationale.get().strip())
            config = scoring.validate_config(config)
            self.saved(config); self.destroy()
        except (ValueError, StopIteration) as exc:
            messagebox.showerror('模板尚未应用', str(exc), parent=self)

    def destroy(self):
        super().destroy()
        # Release Tk variables here on the UI thread, including dialogs closed
        # before a later background PDF job triggers Python's cyclic collector.
        self.weights.clear(); self.values.clear()
        self.name = self.mode = self.goal = self.rationale = None
        self.saved = None


class AnnotationDialog(tk.Toplevel):
    def __init__(self, parent, row, previous, saved):
        super().__init__(parent)
        self.row = row; self.saved = saved; self.fields = {}; self.sources = {}
        self.title('核对比较依据：' + row['sample_label']); self.geometry('1050x760'); self.minsize(910, 600)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        top = ttk.Frame(self, padding=12); top.pack(fill='x')
        ttk.Label(top, text='只填原文支持的内容，未知留空。每一项选择对应证据；同组条件须完整且含义一致。', wraplength=990).pack(anchor='w')
        ttk.Label(top, text='找不到对应方法段落时，请回板块2添加并核验该段证据、关联本条记录，再重新交接。', wraplength=990).pack(anchor='w', pady=4)
        old, errors = scoring.checked_annotation(row, previous)
        if errors:
            ttk.Label(top, text='旧补充已失效，请依据当前证据重新填写。', foreground='#9a4a08').pack(anchor='w')
        self.reviewer = tk.StringVar(value=previous.get('reviewer', '') if previous and not errors else '')
        self.note = tk.StringVar(value=previous.get('note', '') if previous and not errors else '')
        self.confirm = tk.BooleanVar(value=False)
        bottom = ttk.Frame(self, padding=12); bottom.pack(side='bottom', fill='x')
        ttk.Label(bottom, text='核验人').grid(row=0, column=0, sticky='w')
        ttk.Entry(bottom, textvariable=self.reviewer, width=25).grid(row=0, column=1, sticky='w', padx=8)
        ttk.Label(bottom, text='核对说明').grid(row=1, column=0, sticky='w', pady=6)
        ttk.Entry(bottom, textvariable=self.note).grid(row=1, column=1, sticky='ew', padx=8)
        bottom.columnconfigure(1, weight=1)
        ttk.Checkbutton(bottom, text='我已逐项对照所选证据，确认未把未知信息或推测当作已报告事实。', variable=self.confirm).grid(row=2, column=0, columnspan=2, sticky='w', pady=5)
        ttk.Button(bottom, text='应用人工核对补充', command=self.save).grid(row=3, column=1, sticky='e')
        holder = ttk.Frame(self); holder.pack(fill='both', expand=True)
        canvas = tk.Canvas(holder, highlightthickness=0); scroll = ttk.Scrollbar(holder, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set); scroll.pack(side='right', fill='y'); canvas.pack(side='left', fill='both', expand=True)
        inner = ttk.Frame(canvas, padding=12); window = canvas.create_window(0, 0, anchor='nw', window=inner)
        inner.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
        self.evidence_options = {'': ''}
        for evidence in row['evidence']:
            label = f"第{evidence.get('page','?')}页 · {evidence['evidence_id']} · {evidence.get('quote','')[:64]}"
            self.evidence_options[label] = evidence['evidence_id']
        for i, (key, (label, kind, _)) in enumerate(scoring.context_fields(row).items()):
            ttk.Label(inner, text=label).grid(row=i * 3, column=0, sticky='w', pady=(9, 1))
            self.fields[key] = tk.StringVar(value=str(old[key]['value']) if key in old else '')
            ttk.Entry(inner, textvariable=self.fields[key]).grid(row=i * 3 + 1, column=0, sticky='ew')
            selected = next((name for name, eid in self.evidence_options.items() if key in old and eid == old[key]['evidence_id']), '')
            self.sources[key] = tk.StringVar(value=selected)
            ttk.Combobox(inner, textvariable=self.sources[key], values=list(self.evidence_options), state='readonly').grid(row=i * 3 + 2, column=0, sticky='ew', pady=(1, 3))
        inner.columnconfigure(0, weight=1)
        box, self.evidence_text = text_area(inner, 7); box.grid(row=len(self.fields) * 3, column=0, sticky='ew', pady=12)
        show_text(self.evidence_text, '\n\n'.join(f"第{e.get('page','?')}页 · {e['evidence_id']}\n{e.get('quote','')}" for e in row['evidence']))

    def save(self):
        try:
            if not self.confirm.get():
                raise ValueError('请先逐项核对证据，再勾选确认。')
            entries = {key: {'value': variable.get().strip(), 'evidence_id': self.evidence_options.get(self.sources[key].get(), '')}
                       for key, variable in self.fields.items() if variable.get().strip()}
            annotation = scoring.make_annotation(self.row, entries, self.reviewer.get(), self.note.get())
            self.saved(annotation); self.destroy()
        except ValueError as exc:
            messagebox.showerror('核对补充未应用', str(exc), parent=self)

    def destroy(self):
        super().destroy()
        self.fields.clear(); self.sources.clear()
        self.reviewer = self.note = self.confirm = None
        self.saved = None
