"""Publisher-specific onboarding; multiple independent in-memory credentials."""
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

import engine
from elsevier_api import REGISTER_URL, AUTH_URL
from elsevier_api import APIError
from pdf_fetch import Stopped
from publisher_clients import (FACTORIES, PublisherClients, provider_for, same_credentials,
    SPRINGER_REGISTER, SPRINGER_DOCS, WILEY_REGISTER, WILEY_DOCS)


PROVIDERS = {
    'springer': {'label': 'Springer Nature：开放获取', 'field': 'Springer API Key',
        'register': SPRINGER_REGISTER, 'docs': SPRINGER_DOCS,
        'note': '先申请 Open Access API。程序按 DOI 查询开放论文，再接收其公开 PDF；此入口不解锁订阅文章。'},
    'wiley': {'label': 'Wiley：TDM 全文 PDF', 'field': 'Wiley TDM Token',
        'register': WILEY_REGISTER, 'docs': WILEY_DOCS,
        'note': '需 Wiley TDM Token；订阅全文还需机构 IP 权限。浏览器学校登录不会自动传给程序，连续请求约每 10 秒一篇。'},
    'elsevier': {'label': 'Elsevier：全文 PDF', 'field': 'Elsevier API Key',
        'register': REGISTER_URL, 'docs': AUTH_URL,
        'note': '使用 Elsevier 官方全文 API。学校网页登录状态不会自动传给程序；机构 Token 仅在官方提供后填写。'},
}


class PublisherAPIWindow:
    def __init__(self, owner):
        self.owner = owner
        self.closed = False
        self.drafts = {}
        self.provider_id = provider_for(owner._selected() or {}) or 'springer'
        self.window = tk.Toplevel(owner.root)
        self.window.title('出版社 API：选择出版社、申请与单篇验证')
        self.window.geometry('980x760')
        self.window.minsize(840, 650)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.key = tk.StringVar()
        self.token = tk.StringVar()
        self.selected = tk.StringVar()
        self.state = tk.StringVar()
        self.provider = tk.StringVar(value=PROVIDERS[self.provider_id]['label'])
        self.note = tk.StringVar()
        self.field = tk.StringVar()
        header = ttk.Frame(self.window, padding=(18, 10))
        header.pack(fill='x')
        ttk.Label(header, text='选择出版社，先验证一篇', font=('Microsoft YaHei UI', 16, 'bold')).pack(anchor='w')
        choice = ttk.Frame(header)
        choice.pack(fill='x', pady=(6, 0))
        ttk.Label(choice, text='出版社 / 接口').pack(side='left', padx=(0, 12))
        self.provider_box = ttk.Combobox(choice, textvariable=self.provider, state='readonly',
            values=[p['label'] for p in PROVIDERS.values()], width=38)
        self.provider_box.pack(side='left')
        self.provider_box.bind('<<ComboboxSelected>>', self.change_provider)
        ttk.Label(header, textvariable=self.note, wraplength=790).pack(anchor='w', pady=(6, 0))
        links = ttk.Frame(header)
        links.pack(fill='x', pady=(7, 0))
        ttk.Button(links, text='① 打开该出版社申请入口', command=lambda: self.open_link('register')).pack(side='left')
        ttk.Button(links, text='查看该接口说明', command=lambda: self.open_link('docs')).pack(side='left', padx=8)
        ttk.Button(links, text='ACS / RSC 等接入方式', command=self.help).pack(side='left')
        form = ttk.LabelFrame(self.window, text='② 在这台电脑填写', padding=10)
        form.pack(fill='x', padx=18)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, textvariable=self.field).grid(row=0, column=0, sticky='w', padx=(0, 10), pady=4)
        ttk.Entry(form, textvariable=self.key, show='*', width=40).grid(row=0, column=1, sticky='ew', pady=4)
        self.token_label = ttk.Label(form, text='机构 Token（有才填）')
        self.token_label.grid(row=1, column=0, sticky='w', padx=(0, 10), pady=4)
        self.token_entry = ttk.Entry(form, textvariable=self.token, show='*', width=40)
        self.token_entry.grid(row=1, column=1, sticky='ew', pady=4)
        ttk.Label(form, text='各出版社分别申请凭据，只在本次运行中使用；重新打开程序后需重新填写。',
                  wraplength=770).grid(row=2, column=0, columnspan=2, sticky='w', pady=(4, 0))
        controls = ttk.Frame(form)
        controls.grid(row=3, column=0, columnspan=2, sticky='w', pady=(7, 0))
        self.apply_button = ttk.Button(controls, text='应用密钥（仅本次）', command=self.apply)
        self.apply_button.pack(side='left')
        self.disable_button = ttk.Button(controls, text='停用当前出版社 API', command=self.disable)
        self.disable_button.pack(side='left', padx=8)
        self.probe_button = ttk.Button(controls, text='检查 Key / 通道', command=self.check_access)
        ttk.Label(form, textvariable=self.state, wraplength=770).grid(row=4, column=0, columnspan=2, sticky='w', pady=(5, 0))
        check = ttk.LabelFrame(self.window, text='③ 验证板块1当前选中的论文', padding=10)
        check.pack(fill='x', padx=18, pady=8)
        ttk.Label(check, textvariable=self.selected, wraplength=780).pack(anchor='w')
        actions = ttk.Frame(check)
        actions.pack(fill='x', pady=(7, 0))
        self.test_button = ttk.Button(actions, text='验证并接收这篇 PDF', command=self.test_selected)
        self.test_button.pack(side='left')
        ttk.Button(actions, text='刷新当前论文', command=self.refresh).pack(side='left', padx=8)
        self.article_button = ttk.Button(actions, text='打开论文网页', command=self.owner._open_article)
        self.article_button.pack(side='left')
        ttk.Label(check, text='在板块1选中对应出版社的论文并判定“保留”；验证会实际请求该接口。',
                  wraplength=780).pack(anchor='w', pady=(5, 0))
        footer = ttk.Frame(self.window, padding=(18, 4, 18, 10))
        footer.pack(side='bottom', fill='x')
        ttk.Label(footer, text='成功后回到板块1，点击“自动获取 PDF → 进入第二板块”，程序按出版社选择已启用的通道。',
                  wraplength=780).pack(anchor='w')
        footer_actions = ttk.Frame(footer); footer_actions.pack(anchor='w', pady=(5, 0))
        ttk.Button(footer_actions, text='查看本机多出版社操作说明', command=self.help).pack(side='left')
        self.school_button = ttk.Button(footer_actions, text='学校全文 / Zotero 接收', command=self.open_school)
        self.school_button.pack(side='left', padx=8)
        panel = ttk.Frame(self.window, padding=(18, 0))
        panel.pack(fill='both', expand=True)
        self.result = tk.Text(panel, height=5, wrap='word', font=('Microsoft YaHei UI', 10), state='disabled', padx=8, pady=8)
        scroll = ttk.Scrollbar(panel, orient='vertical', command=self.result.yview)
        self.result.configure(yscrollcommand=scroll.set)
        self.result.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.update_provider_fields()
        self.refresh()
        self.show('选择出版社后申请并填写对应凭据，再选择一篇保留论文进行验证。\n\nSpringer：先验证开放论文；Wiley：TDM Token + 机构 IP 权限。\n只有实际取得且身份核对通过的 PDF 才会交给板块2。')

    def registry(self):
        return PublisherClients(getattr(self.owner, 'publisher_client', None))

    def update_provider_fields(self):
        settings = PROVIDERS[self.provider_id]
        self.note.set(settings['note'])
        self.field.set(settings['field'])
        if self.provider_id == 'springer':
            self.probe_button.pack(side='left')
        else:
            self.probe_button.pack_forget()
        for widget in (self.token_label, self.token_entry):
            if self.provider_id == 'elsevier':
                widget.grid()
            else:
                widget.grid_remove()

    def change_provider(self, event=None):
        if self.owner.busy:
            self.provider.set(PROVIDERS[self.provider_id]['label'])
            return
        self.drafts[self.provider_id] = (self.key.get(), self.token.get())
        self.provider_id = next(k for k, v in PROVIDERS.items() if v['label'] == self.provider.get())
        key, token = self.drafts.get(self.provider_id, ('', ''))
        self.key.set(key)
        self.token.set(token)
        self.update_provider_fields()
        self.refresh()
        self.show(PROVIDERS[self.provider_id]['note'] + '\n\n请刷新当前论文，再进行单篇验证。')

    def open_link(self, kind):
        webbrowser.open(PROVIDERS[self.provider_id][kind])

    def show(self, text):
        if self.closed:
            return
        self.result.configure(state='normal')
        self.result.delete('1.0', 'end')
        self.result.insert('1.0', text)
        self.result.configure(state='disabled')

    def refresh(self):
        if self.closed:
            return
        record = self.owner._selected()
        if record:
            value = (record.get('title') or '未命名论文') + '\nDOI：' + record.get('doi', '待核对')
            match = provider_for(record)
            if match != self.provider_id:
                value += '\n请在上方选择 ' + (PROVIDERS[match]['label'] if match in PROVIDERS else '对应通道；该出版方暂无已接入的 Key 接口')
            self.selected.set(value)
        else:
            self.selected.set('当前未选择论文。可先填写凭据，再回到板块1选中论文。')
        configured = self.registry().clients
        self.state.set('本次已启用：' + '、'.join(PROVIDERS[k]['label'].split('：')[0] for k in configured)
                       + '；全文权限以实际验证为准。' if configured else '本次尚未启用出版社 API。')
        busy = self.owner.busy
        self.provider_box.configure(state='disabled' if busy else 'readonly')
        self.apply_button.configure(state='disabled' if busy else 'normal')
        self.disable_button.configure(state='disabled' if busy else 'normal')
        self.probe_button.configure(state='disabled' if busy else 'normal')
        self.article_button.configure(state='normal' if record and not busy else 'disabled')
        self.school_button.configure(state='disabled' if busy else 'normal')
        self.test_button.configure(state='normal' if not busy and record and provider_for(record) == self.provider_id else 'disabled')

    def apply(self):
        if self.owner.busy:
            return False
        key = self.key.get()
        token = self.token.get() if self.provider_id == 'elsevier' else ''
        registry = self.registry()
        existing = registry.clients.get(self.provider_id)
        # Repeated validation must retain the provider's rate-limit state.
        if same_credentials(existing, key, token):
            self.refresh()
            return True
        try:
            factory = FACTORIES[self.provider_id]
            client = factory(key, token) if self.provider_id == 'elsevier' else factory(key)
        except ValueError as exc:
            messagebox.showinfo('检查填写内容', str(exc), parent=self.window)
            return False
        registry.clients[self.provider_id] = client
        self.owner.publisher_client = registry
        self.refresh()
        self.owner.status_var.set('已启用 ' + PROVIDERS[self.provider_id]['label'] + '，请验证一篇对应论文。')
        return True

    def disable(self):
        if self.owner.busy:
            return
        registry = self.registry()
        registry.clients.pop(self.provider_id, None)
        self.owner.publisher_client = registry if registry.clients else None
        self.key.set('')
        self.token.set('')
        self.drafts.pop(self.provider_id, None)
        self.refresh()
        self.show('已停用当前出版社 API。其他已启用的出版社通道仍可使用。')

    def open_school(self):
        if not self.owner.busy:
            self.owner._open_zotero()

    def check_access(self):
        if self.owner.busy or self.provider_id != 'springer':
            return
        if self.key.get() and not self.apply():
            return
        client = self.registry().clients.get('springer')
        if client is None:
            self.show('请先填写 Springer Key 并应用，再检查通道。')
            return
        self.show('正在检查 Key 与开放获取查询通道……\n使用一篇公开测试文献；不会下载PDF、添加论文或改变当前筛选记录。')
        def run_check():
            try:
                return client.check_access(self.owner.cancel_event, self.owner._progress)
            except Stopped:
                return {'status': 'cancelled', 'message': '已停止通道检查。'}
            except APIError as exc:
                return exc.public_details()
        def done(result):
            message = result['message']
            if result.get('retry_after_seconds'):
                message += '\n请至少等待约 ' + str(round(result['retry_after_seconds'])) + ' 秒后再试。'
            self.show(message)
            self.owner.status_var.set(result['message'])
            if not self.closed:
                self.window.after(200, self.refresh)
        self.owner._start_job('正在检查 Springer Key / 查询通道……', run_check, done)
        self.refresh()

    def test_selected(self):
        if self.owner.busy:
            return
        record = self.owner._selected()
        if not record or not self.owner.run or provider_for(record) != self.provider_id:
            self.show('请在板块1选中与上方出版社对应的论文，再点击“刷新当前论文”。')
            return
        if (record.get('manual_decision') or record.get('effective_decision')) != 'target':
            self.show('请先回到板块1将这篇论文判定为“保留”。')
            return
        if self.key.get() and not self.apply():
            return
        client = self.registry().clients.get(self.provider_id)
        if client is None:
            self.show('请填写这个出版社的 Key / Token，再点击“应用密钥（仅本次）”。')
            return
        run, record_id = self.owner.run, record['id']
        self.show('正在验证 ' + PROVIDERS[self.provider_id]['label'] + ' 并请求 PDF……')

        def done(payload):
            self.owner._show_run(payload['run'], preserve_selection=record_id)
            result = payload['check']
            message = result['message']
            if result.get('local_path'):
                message += '\n\n已接收至：\n' + result['local_path']
            if result.get('retry_after_seconds'):
                message += '\n\n请至少等待约 ' + str(round(result['retry_after_seconds'])) + ' 秒后再试。'
            if result['status'] == 'downloaded':
                message += '\n\n可以回到板块1进入第二板块。'
            self.show(message)
            self.owner.status_var.set(result['message'])
            if not self.closed:
                self.window.after(200, self.refresh)

        self.owner._start_job('正在验证出版社全文访问……',
            lambda: engine.check_publisher_pdf(run, record_id, client, progress=self.owner._progress,
                                              cancel_event=self.owner.cancel_event), done)
        self.refresh()

    def help(self):
        path = Path(__file__).resolve().parents[2] / 'docs/workbench/tutorial.html'
        if not path.is_file():
            path = Path(__file__).resolve().parents[1] / 'delivery_docs' / path.name
        webbrowser.open(path.as_uri() if path.is_file() else PROVIDERS[self.provider_id]['docs'])

    def close(self):
        self.key.set('')
        self.token.set('')
        self.drafts.clear()
        self.closed = True
        self.window.destroy()
