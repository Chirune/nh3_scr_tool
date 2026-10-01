"""Figure reading and explicit scientific field mapping inside the workbench."""
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from scrtool.core import FIELDS, infer_header, read_json
from scrtool.workbench import PROPERTIES
from scrtool.figure_digitizer.sources import SUPPORTED_IMAGE_SUFFIXES


def paper_selection(workbench):
    if workbench.current_source:
        return workbench.current_source['paper_id']
    ids = workbench.material_table.selection()
    return ids[0] if len(ids) == 1 else None


def open_reader(workbench):
    if not workbench.guarded() or not workbench.project:
        return
    identity = paper_selection(workbench)
    if not identity or identity not in workbench.project.state['runs']:
        return messagebox.showinfo('选择论文', '先提取论文，并在审核页选择对应原文页。')
    source = workbench.current_source or {}
    path = source.get('source_file')
    if not path or Path(path).suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES | {'.pdf'}:
        paths = [workbench.project.folder / a['path'] for a in workbench.project.state['attachments'].get(identity, [])
                 if Path(a['path']).suffix.lower() in SUPPORTED_IMAGE_SUFFIXES | {'.pdf'}]
        path = paths[0] if paths else None
    if not path:
        return messagebox.showinfo('添加论文', '请先给这篇论文添加原始 PDF 文件。')
    from scrtool.figure_digitizer.app import DigitizerApp
    from scrtool.figure_digitizer.session import new_session
    page = re.search(r'page:(\d+)', source.get('locator', ''))
    result = workbench.safe(lambda: new_session(path, int(page.group(1)) if page else 1,
                                                workbench.project.folder / 'figure_sessions' / identity))
    if not result:
        return
    session, _ = result
    session['doi'] = workbench.project.paper(identity).get('doi', '')
    window = tk.Toplevel(workbench.root)
    app = DigitizerApp(window, output_root=workbench.project.folder / 'figure_sessions' / identity,
                       on_export=lambda snapshot: import_dialog(workbench, snapshot, identity, source.get('block_id')))
    app.set_session(result)
    window._figure_reader = app


def choose_snapshot(workbench):
    if not workbench.guarded() or not workbench.project:
        return
    identity = paper_selection(workbench)
    if not identity or identity not in workbench.project.state['runs']:
        return messagebox.showinfo('选择论文', '先提取论文，再选中这篇论文的原文页。')
    path = filedialog.askopenfilename(title='选择图片读数的“读数与溯源.json”', filetypes=[('读数快照', '*.json')])
    if path:
        import_dialog(workbench, path, identity, (workbench.current_source or {}).get('block_id'))


def import_dialog(workbench, snapshot_path, identity, block_id=None):
    snapshot = workbench.safe(lambda: read_json(snapshot_path))
    if not snapshot:
        return
    if snapshot.get('schema_version') != 'figure-digitizer/1.0':
        return messagebox.showerror('文件类型', '请选择图片读数工具导出的“读数与溯源.json”。')
    window = tk.Toplevel(workbench.root)
    window.title('将图片读数关联到论文数据'); window.geometry('880x740')
    ttk.Label(window, text=workbench.project.paper(identity)['title'], wraplength=840).pack(anchor='w', padx=15, pady=8)
    ttk.Label(window, text='确认物理量含义和固定反应条件。导入后进入待审核列表。', wraplength=840).pack(anchor='w', padx=15)
    tabs = ttk.Notebook(window); tabs.pack(fill='both', expand=True, padx=15, pady=10)
    mapping = ttk.Frame(tabs); fixed = ttk.Frame(tabs)
    tabs.add(mapping, text='指标和来源'); tabs.add(fixed, text='固定反应条件（可留空）')
    choices = {PROPERTIES.get(p, p) + ' [' + p + ']': p for p in FIELDS}
    condition_choices = {'不作为数值条件': None, **{k: v for k, v in choices.items() if FIELDS[v][0] == 'condition'}}
    cal = snapshot.get('calibration') or {}
    def detected(axis):
        spec = cal.get(axis, {})
        prop, _ = infer_header(str(spec.get('name', '')) + ' [' + str(spec.get('unit', '')) + ']')
        if not prop:
            prop = next((p for p, label in PROPERTIES.items() if label == spec.get('name')), None)
        return prop
    yp = detected('y'); xp = detected('x')
    yvar = tk.StringVar(value=next((k for k, v in choices.items() if v == yp), ''))
    xvar = tk.StringVar(value=next((k for k, v in condition_choices.items() if v == xp), '不作为数值条件'))
    for index, (label, variable, values) in enumerate([('纵轴物理量', yvar, choices), ('横轴条件', xvar, condition_choices)]):
        ttk.Label(mapping, text=label).grid(row=index, column=0, sticky='w', padx=8, pady=10)
        box = ttk.Combobox(mapping, textvariable=variable, values=list(values), state='readonly', width=55)
        box.grid(row=index, column=1, sticky='ew', padx=8)
        if index == 1 and snapshot.get('chart_type') == 'bar':
            xvar.set('不作为数值条件'); box.configure(state='disabled')
    mapping.columnconfigure(1, weight=1)
    ttk.Label(mapping, text=f"图号：{snapshot.get('figure_label', '')}；读数点：{len(snapshot.get('points', []))}\n纵轴原始单位：{cal.get('y', {}).get('unit', '')}；横轴原始单位：{cal.get('x', {}).get('unit', '')}", wraplength=810).grid(row=2, column=0, columnspan=2, sticky='w', padx=8, pady=12)
    sources = [b for b in read_json(workbench.project.run_path(identity) / 'workflow_sources.json') if b.get('kind') != 'digitized_curve']
    source_choices = {f"{i+1}. {Path(b['source_file']).name} · {b['locator']}": b for i, b in enumerate(sources)}
    chosen = next((k for k, b in source_choices.items() if b['block_id'] == block_id), next(iter(source_choices), ''))
    source_var = tk.StringVar(value=chosen)
    ttk.Label(mapping, text='固定条件的原文位置').grid(row=3, column=0, sticky='w', padx=8)
    source_box = ttk.Combobox(mapping, textvariable=source_var, values=list(source_choices), state='readonly', width=55)
    source_box.grid(row=3, column=1, sticky='ew', padx=8)
    original = tk.Text(mapping, wrap='word', height=16)
    original.grid(row=4, column=0, columnspan=2, sticky='nsew', padx=8, pady=10); mapping.rowconfigure(4, weight=1)
    def show_source(_event=None):
        original.configure(state='normal'); original.delete('1.0', 'end')
        original.insert('1.0', source_choices.get(source_var.get(), {}).get('text', ''))
        original.configure(state='disabled')
    source_box.bind('<<ComboboxSelected>>', show_source); show_source()
    ttk.Label(fixed, text='只填同一实验中原文明确给出的条件；证据需从左侧页签复制，且包含所填数值。', wraplength=800).grid(row=0, column=0, columnspan=4, padx=8, pady=12, sticky='w')
    for column, label in enumerate(['条件', '原文数值', '原文单位', '包含数值的原文证据']):
        ttk.Label(fixed, text=label).grid(row=1, column=column, sticky='w', padx=6)
    specs = {}
    for row, prop in enumerate([p for p in FIELDS if FIELDS[p][0] == 'condition'], 2):
        raw, unit, evidence = tk.StringVar(), tk.StringVar(value=FIELDS[prop][1]), tk.StringVar()
        specs[prop] = raw, unit, evidence
        ttk.Label(fixed, text=PROPERTIES.get(prop, prop)).grid(row=row, column=0, sticky='w', padx=6, pady=5)
        for column, (variable, width) in enumerate([(raw, 12), (unit, 9), (evidence, 47)], 1):
            ttk.Entry(fixed, textvariable=variable, width=width).grid(row=row, column=column, sticky='ew', padx=6, pady=5)
    fixed.columnconfigure(3, weight=1)
    def save():
        if not workbench.guarded():
            return
        if yvar.get() not in choices:
            return messagebox.showinfo('选择物理量', '请确认纵轴物理量。', parent=window)
        values = {p: {'raw_value': raw.get().strip(), 'unit': unit.get().strip(), 'evidence': evidence.get().strip()}
                  for p, (raw, unit, evidence) in specs.items() if raw.get().strip()}
        context = source_choices.get(source_var.get(), {})
        try:
            count = workbench.project.import_figure(identity, snapshot_path, choices[yvar.get()], condition_choices[xvar.get()],
                context_block_id=context.get('block_id'), conditions=values)
        except Exception as exc:
            return messagebox.showerror('需要处理', str(exc), parent=window)
        workbench.refresh(); workbench.tabs.select(3)
        workbench.status.set(f'图片读数导入 {count} 条；重复记录已跳过。请核对样品、单位、条件后审核。')
        window.destroy()
    ttk.Button(window, text='导入为待审核数据', command=save).pack(pady=12)
