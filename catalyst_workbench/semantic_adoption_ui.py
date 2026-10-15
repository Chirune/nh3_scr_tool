"""Simple suggestion-first review; detailed categories are optional."""
import copy
import tkinter as tk
from tkinter import ttk, messagebox

import paper_workspace as work
import semantic_assistant as assistant


class AdoptionDialog:
    def __init__(self, owner, evidence, purpose=None):
        self.owner, self.evidence = owner, evidence
        self.running = False
        self.project_id = owner.project['project_id']
        old = evidence.get('semantic_adoption', {})
        saved = evidence.get('assistant_suggestion', {})
        valid = saved.get('source_quote') == evidence.get('quote') and saved.get('evidence_id') == evidence['evidence_id']
        self.suggestion = saved if valid else assistant.rule_suggestion(evidence)
        selected = purpose or old.get('purpose') or self.suggestion['purpose']
        self.window = tk.Toplevel(owner.root)
        self.window.title('核对程序建议'); self.window.geometry('900x760'); self.window.minsize(700, 650)
        self.window.transient(owner.root)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        outer = ttk.Frame(self.window, padding=14); outer.pack(fill='both', expand=True)
        footer = ttk.Frame(outer); footer.pack(side='bottom', fill='x')
        self.choice = tk.StringVar(value=work.SEMANTIC_USES[selected])
        self.reviewer = tk.StringVar(value=owner.reviewer.get() or old.get('reviewer', ''))
        self.note = tk.StringVar(value=old.get('note') or self.suggestion['reason_zh'])
        self.status = tk.StringVar(value='程序先给建议；你对照原文确认，或展开修改分类。')
        self.summary = tk.StringVar(value=self.suggestion['summary_zh'])
        self.category = tk.StringVar(value='建议用途：' + self.choice.get())
        ttk.Label(outer, textvariable=self.category, font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w')
        summary_label=ttk.Label(outer, textvariable=self.summary, wraplength=830, justify='left')
        box, self.original = owner_text(outer)
        box.pack(fill='both', expand=True, pady=(0, 8))
        page=next((p for p in owner.project.get('pages',[]) if p['page']==evidence.get('page')),{})
        source=page.get('text','');start,end=evidence.get('start'),evidence.get('end')
        context=source[max(0,start-400):min(len(source),end+400)] if isinstance(start,int) and isinstance(end,int) else ''
        display='原文（'+work.evidence_location(evidence)+'）：\n'+evidence.get('quote','')
        if context:display+='\n\n同页上下文（用于核对，未自动补充到数据）：\n'+context
        self.original.insert('1.0',display);self.original.configure(state='disabled')
        summary_label.pack(fill='x', pady=7)
        row = ttk.Frame(outer); row.pack(fill='x'); self.controls_row = row
        ttk.Button(row, text='本机模型判断 / 重试', command=self.ask_model).pack(side='left')
        ttk.Button(row, text='修改分类（展开）', command=self.toggle_details).pack(side='left', padx=8)
        self.details = ttk.Frame(outer)
        ttk.Label(self.details, text='你希望怎么使用这句话').pack(side='left')
        ttk.Combobox(self.details, textvariable=self.choice, values=list(work.SEMANTIC_USES.values()),
                     state='readonly', width=30).pack(side='left', padx=8)
        self.choice.trace_add('write', lambda *_: self.category.set('建议用途 / 当前选择：' + self.choice.get()))
        form = ttk.Frame(outer); form.pack(fill='x', pady=7); form.columnconfigure(1, weight=1)
        ttk.Label(form, text='判断依据（已填写，可修改）').grid(row=0, column=0, sticky='w', padx=(0, 8))
        ttk.Entry(form, textvariable=self.note).grid(row=0, column=1, sticky='ew')
        ttk.Label(form, text='你的名字 / 代号（填一次）').grid(row=1, column=0, sticky='w', pady=7)
        ttk.Entry(form, textvariable=self.reviewer, width=20).grid(row=1, column=1, sticky='w', pady=7)
        status_label=ttk.Label(footer, textvariable=self.status, wraplength=830, justify='left')
        status_label.pack(fill='x', pady=5)
        scope_label=ttk.Label(footer, text='确认只记录这句话的用途；数值、样品和条件仍按原文核对。', wraplength=830)
        scope_label.pack(fill='x')
        def resize(event):
            if event.widget is self.window:
                for label in (summary_label,status_label,scope_label):label.configure(wraplength=max(330,event.width-36))
        self.window.bind('<Configure>',resize)
        buttons = ttk.Frame(footer); buttons.pack(fill='x', pady=(9, 0))
        self.confirm_button = ttk.Button(buttons, text='确认当前建议并保存', command=lambda: self.save(True))
        self.confirm_button.pack(side='left')
        ttk.Button(buttons, text='暂存，稍后核对', command=lambda: self.save(False)).pack(side='left', padx=8)
        ttk.Button(buttons, text='暂不采用', command=self.exclude).pack(side='right')
        self.window.grab_set()
        self.show_questions()
        if purpose is None and not old and not valid and assistant.automatic_enabled():
            self.window.after(200, self.ask_model)

    def alive(self):
        return bool(self.window.winfo_exists())

    def toggle_details(self):
        if self.details.winfo_manager():
            self.details.pack_forget()
        else:
            self.details.pack(fill='x', after=self.controls_row, pady=7)

    def show_questions(self):
        questions = self.suggestion.get('questions', [])
        backend = '本机大模型建议，等待你确认。' if self.suggestion.get('backend') == 'ollama_local' else '规则初步建议；可点击本机模型生成中文解释。'
        self.status.set(backend + ('\n需核对：' + '；'.join(questions) if questions else ''))

    def ask_model(self):
        if not self.alive() or self.running or self.owner.busy:
            return
        snapshot = copy.deepcopy(self.owner.project); evidence = copy.deepcopy(self.evidence)
        self.running = True
        self.status.set('本机模型正在判断这句话；首次加载可能稍慢……')
        def worker():
            try:
                return {'suggestion': assistant.suggest(snapshot, evidence)}
            except assistant.AssistantError as exc:
                return {'error': str(exc)}
        def done(result):
            self.running = False
            if not self.alive():
                return
            if result.get('error'):
                self.status.set(result['error']); return
            if self.owner.project['project_id'] != self.project_id:
                self.status.set('当前论文已改变，请重新选择本篇证据。'); return
            work.save_assistant_suggestion(self.owner.project, evidence['evidence_id'], result['suggestion'])
            self.suggestion = result['suggestion']
            self.choice.set(work.SEMANTIC_USES[self.suggestion['purpose']])
            self.summary.set(self.suggestion['summary_zh']); self.note.set(self.suggestion['reason_zh'])
            self.show_questions()
        self.owner.job(worker, done, '本机模型正在生成分类建议……')

    def save(self, confirm):
        if self.running or self.owner.busy:
            self.status.set('请等待本次模型判断完成。'); return
        try:
            if self.owner.project['project_id'] != self.project_id:
                raise ValueError('当前论文已改变，请重新选择证据。')
            selected = next(k for k, v in work.SEMANTIC_USES.items() if v == self.choice.get())
            work.set_semantic_adoption(self.owner.project, self.evidence['evidence_id'], selected,
                self.reviewer.get(), self.note.get(), confirm)
            self.owner.reviewer.set(self.reviewer.get())
            self.close(); self.owner.refresh_lists()
            self.owner.describe_evidence(self.owner.semantic_tree, self.owner.semantic_detail)
            self.owner.status.set('用途已保存；性能数据仍需核对样品与条件。')
        except Exception as exc:
            messagebox.showerror('尚未保存', str(exc), parent=self.window)

    def exclude(self):
        self.choice.set(work.SEMANTIC_USES['exclude'])
        self.note.set('人工核对后决定不采用本条证据。')
        self.save(False)

    def close(self):
        self.window.destroy()


def owner_text(parent):
    from paper_app import text_area
    return text_area(parent, 9)
