"""Show model preparation and chemistry coverage before any model is trained."""
import json
import tkinter as tk
from tkinter import ttk

import ml_preparation as preparation
from paper_app import table, text_area, show_text


class ModelPreparationPanel:
    def __init__(self, owner, frame):
        self.owner = owner; self.plan = None; self.preview = None
        self.heading = ttk.Label(frame, text='同一份化学事实 → 各模型适用的编码 → 后续公平比较',
            font=('Microsoft YaHei UI', 12, 'bold')); self.heading.pack(anchor='w')
        bar = ttk.Frame(frame); self.controls = bar; bar.pack(fill='x', pady=4)
        ttk.Label(bar, text='特征场景').pack(side='left')
        self.profile = tk.StringVar()
        self.profile_box = ttk.Combobox(bar, textvariable=self.profile, state='readonly', width=40)
        self.profile_box.pack(side='left', padx=8)
        self.profile_box.bind('<<ComboboxSelected>>', lambda event: self.describe())
        self.preview_button = ttk.Button(bar, text='预览首个训练折的编码', command=self.prepare_preview)
        self.preview_button.pack(side='left')
        self.export_button = ttk.Button(bar, text='导出该训练折', command=self.export_preview)
        self.export_button.pack(side='left', padx=(8, 0))
        self.summary = tk.StringVar(value='生成编码预览后，查看建模任务、字段覆盖和候选编码方式。')
        self.summary_label = ttk.Label(frame, textvariable=self.summary, wraplength=940)
        self.summary_label.pack(anchor='w', pady=5)
        self.views = ttk.Notebook(frame); self.views.pack(fill='both', expand=True, pady=7)
        self.explanation = ttk.Frame(self.views); self.preview_frame = ttk.Frame(self.views)
        self.views.add(self.explanation, text='方案与字段覆盖'); self.views.add(self.preview_frame, text='本训练折输入表')
        box, self.recipes = table(self.explanation, [('model', '候选模型'), ('missing', '数值缺失'),
            ('scale', '数值尺度'), ('categories', '类别处理')], [300, 230, 160, 230], 4)
        box.pack(fill='x')
        self.recipes.bind('<<TreeviewSelect>>', lambda event: self.describe())
        box, self.detail = text_area(self.explanation, 16); box.pack(fill='both', expand=True)
        self.preview_summary = tk.StringVar(value='选择方案后点击“预览首个训练折的编码”。')
        ttk.Label(self.preview_frame, textvariable=self.preview_summary, wraplength=940).pack(anchor='w', pady=5)
        box, self.matrix = table(self.preview_frame, [('empty', '此处显示真实编码变换结果；没有模型预测值')], [920], 9)
        box.pack(fill='both', expand=True)
        self.compact = False
        frame.bind('<Configure>', self.adapt_height)

    def adapt_height(self, event):
        compact = event.height < 340
        if compact == self.compact:
            return
        self.compact = compact
        if compact:
            # The parent workbench already shows task/counts and model status.
            # Free the duplicated headings so an actual result row stays visible.
            self.heading.pack_forget(); self.summary_label.pack_forget()
        else:
            self.heading.pack(anchor='w', before=self.controls)
            self.summary_label.pack(anchor='w', pady=5, before=self.views)

    def set_busy(self, busy):
        self.profile_box.configure(state='disabled' if busy else 'readonly')
        self.preview_button.configure(state='disabled' if busy else 'normal')
        self.export_button.configure(state='disabled' if busy else 'normal')

    def clear(self):
        self.plan = self.preview = None
        self.recipes.delete(*self.recipes.get_children())
        self.matrix.delete(*self.matrix.get_children())
        self.preview_summary.set('数据已改变，旧训练折输入已清空。')
        self.summary.set('数据或任务已改变，请重新生成编码预览。')
        show_text(self.detail, '填补、类别表与缩放都将在具体训练折内学习。旧的整批编码预览不直接代替交叉验证预处理。')

    def refresh(self):
        self.plan = preparation.build_plan(self.owner.result); self.preview = None
        self.recipes.delete(*self.recipes.get_children())
        self.matrix.delete(*self.matrix.get_children())
        self.preview_summary.set('选择编码方案后，生成本训练折输入表。')
        self.profile_box.configure(values=[v['label'] for v in self.plan['profiles'].values()])
        self.profile.set(self.plan['profiles'][self.plan['default_profile']]['label'])
        for key, item in self.plan['recipes'].items():
            self.recipes.insert('', 'end', iid=key, values=(item['label'],
                '本训练折中位数' if item['numeric_missing'] == 'training_fold_median' else '保留缺失',
                '本训练折标准化' if item['scaling'] != 'none' else '不缩放',
                '保留类别，多元素拆列' if item['categorical'] == 'native_strings' else '本训练折独热 / 多热'))
        groups = len({r['split_group'] for r in self.plan['rows']})
        self.summary.set(f"{len(self.plan['rows'])} 条观察 / {groups} 个论文组；"
            f"开发验证 {len(self.plan['folds'])} 折，预留测试 {len(self.plan['outer_test_groups'])} 个论文组。模型尚未比较。")
        self.recipes.selection_set('tree_one_hot'); self.describe()

    def profile_id(self):
        return next(k for k, v in self.plan['profiles'].items() if v['label'] == self.profile.get())

    def describe(self):
        if not self.plan:
            return
        profile_id = self.profile_id()
        recipe_id = next(iter(self.recipes.selection()), 'tree_one_hot')
        profile = self.plan['profiles'][profile_id]
        recipe = self.plan['recipes'][recipe_id]
        if self.preview and (self.preview['state']['profile_id'] != profile_id or self.preview['state']['recipe_id'] != recipe_id):
            self.preview = None; self.matrix.delete(*self.matrix.get_children())
            self.preview_summary.set('场景或方案已改变，请重新生成训练折输入。')
            self.views.select(self.explanation)
        lines = [profile['label'], profile['note'], '', recipe['label'], recipe['note'], '', '当前字段覆盖：']
        allowed = set(profile['fields'])
        for field in self.plan['coverage']:
            if field['field'] in allowed:
                lines.append(f"  {field['label']}：原事实有值 {field['reported']}/{field['total']}；预测前可用 {field['available_before_prediction']}/{field['total']}"
                    + (f"；{field['reported_but_unavailable']}条表征时点未确认/反应后获得，暂不进入X" if field['reported_but_unavailable'] else ''))
        lines += ['', '表征字段即使有值，也需确认获得时点后才能用于“表征后预测”。', '', '后续模型怎么选：']
        lines.extend(self.plan['selection_protocol'].values())
        lines += ['', 'DFT衔接：', self.plan['dft_handoff']['note']]
        if not self.plan['folds']:
            lines.append('\n当前训练论文组不足3个，或整体仍是预览状态：只保留事实表，未拟合任何预处理。')
        show_text(self.detail, '\n'.join(lines))

    def prepare_preview(self):
        if self.owner.busy or not self.plan:
            return
        if not self.plan['folds']:
            self.owner.status.set('目前论文分组不足，只保留事实表；不会用整批数据拟合来冒充训练折。'); return
        recipe = next(iter(self.recipes.selection()), 'tree_one_hot')
        try:
            self.preview = preparation.prepare_fold(self.plan, recipe, self.plan['folds'][0]['fold_id'], self.profile_id())
            state = self.preview['state']
            summary = f"仅预处理演示；没有训练预测模型。\n训练 {len(self.preview['train'])} 条，验证 {len(self.preview['validation'])} 条；预留测试未读取。\n"
            summary += '数值填补与缩放参数：\n' + json.dumps(state['numeric'], ensure_ascii=False, indent=2)
            summary += '\n本训练折全部缺失而未采用的字段：\n' + json.dumps(state['dropped_fields'], ensure_ascii=False, indent=2)
            summary += '\n首条训练行：\n' + json.dumps(self.preview['train'][:1], ensure_ascii=False, indent=2)
            show_text(self.detail, summary)
            columns = ['partition', 'sample', 'y', *state['columns']]
            self.matrix.configure(columns=columns)
            for column in columns:
                self.matrix.heading(column, text={'partition': '用途', 'sample': '样品（仅追溯）', 'y': '已知目标 y'}.get(column, column))
                self.matrix.column(column, width=130 if column in ('partition', 'sample', 'y') else 180, minwidth=80, stretch=False)
            self.matrix.delete(*self.matrix.get_children())
            lookup = {r['observation_id']: r for r in self.plan['rows']}
            for partition, label in (('train', '训练'), ('validation', '验证')):
                for row in self.preview[partition]:
                    self.matrix.insert('', 'end', values=[label, lookup[row['observation_id']]['sample_label'], row['y'],
                        *['缺失' if row['X'][c] is None else f"{row['X'][c]:.5g}" if isinstance(row['X'][c], float) else row['X'][c] for c in state['columns']]])
            self.preview_summary.set(f"训练 {len(self.preview['train'])} 条 / 验证 {len(self.preview['validation'])} 条 · y是已知标签，不是预测值。仅做输入变换，未训练模型。")
            self.views.select(self.preview_frame)
            self.owner.status.set('已生成该训练折的输入预览；没有模型训练结果，也没有据此选出最佳模型。')
        except (ValueError, OverflowError) as exc:
            self.owner.status.set('本次编码预览未完成：' + str(exc))

    def export_preview(self):
        if self.owner.busy or not self.plan:
            return
        if not self.plan['folds']:
            self.owner.status.set('训练论文组不足，尚不能导出训练折。可先导出完整事实与待补字段。'); return
        recipe = next(iter(self.recipes.selection()), 'tree_one_hot')
        profile = self.profile_id(); fold = self.plan['folds'][0]['fold_id']; result = self.owner.result
        def done(report):
            self.owner.exported = report
            self.owner.status.set(f"已导出训练 {report['train_count']} 条 / 验证 {report['validation_count']} 条的编码；未训练模型。打开编码结果文件夹查看：" + report['directory'])
        self.owner.job(lambda: preparation.export_fold(result, self.owner.output_root, recipe, fold, profile),
            done, '正在复核论文来源，并分别导出本训练折和验证折的输入……')
