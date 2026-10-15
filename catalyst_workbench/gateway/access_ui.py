"""Two explicit acquisition routes and their actual error state."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox
import uuid

from access_status import failure_text
import engine
from browser_acquire import receive_result, web_url


class AccessWindow:
    def __init__(self,owner):
        self.owner=owner;self.closed=False;self.timer=None;self.process=None;self.folder=None;self.record_id=None
        self.window=tk.Toplevel(owner.root);self.window.title('获取正文：API 或浏览器')
        self.window.geometry('880x710');self.window.minsize(740,640)
        self.text=tk.StringVar();self.state=tk.StringVar(value='选择一种获取方式；API 出错后可以改用浏览器。')
        frame=ttk.Frame(self.window,padding=18);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='选择正文获取方式',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
        self.details=tk.Text(frame,height=6,wrap='word',font=('Microsoft YaHei UI',10),state='disabled')
        self.details.pack(fill='x',pady=10)
        def update_text(*_):
            self.details.configure(state='normal');self.details.delete('1.0','end')
            self.details.insert('1.0',self.text.get());self.details.configure(state='disabled')
        self.text.trace_add('write',update_text)
        api=ttk.LabelFrame(frame,text='① API 通道',padding=12);api.pack(fill='x',pady=6)
        ttk.Label(api,text='使用对应出版社 Key；失败时显示 HTTP 错误码、原始原因和下一步提示。',wraplength=760).pack(anchor='w')
        row=ttk.Frame(api);row.pack(fill='x',pady=7)
        self.api_button=ttk.Button(row,text='填写 / 验证出版社 API',command=owner._open_api);self.api_button.pack(side='left')
        self.auto_button=ttk.Button(row,text='用已配置通道获取保留论文',command=owner._open_figures);self.auto_button.pack(side='left',padx=8)
        browser=ttk.LabelFrame(frame,text='② 浏览器通道',padding=12);browser.pack(fill='x',pady=6)
        ttk.Label(browser,text='打开专用 Chrome，由你完成账户或学校登录。授权后程序自动检查所选论文，接收 PDF 或保存当前可见正文。登录页不会作为正文采集。',wraplength=760,justify='left').pack(anchor='w')
        self.browser_button=ttk.Button(browser,text='打开浏览器并授权本次采集',command=self.start_browser)
        self.browser_button.pack(anchor='w',pady=8)
        row=ttk.Frame(browser);row.pack(fill='x')
        ttk.Button(row,text='已登录，返回论文',command=self.resume).pack(side='left',padx=(0,8))
        ttk.Button(row,text='改用已有浏览器 + Zotero',command=owner._open_zotero).pack(side='left')
        ttk.Button(row,text='停止本次浏览器采集',command=self.stop).pack(side='left',padx=8)
        ttk.Label(frame,textvariable=self.state,wraplength=800,justify='left',foreground='#155e75').pack(fill='x',pady=12)
        row=ttk.Frame(frame);row.pack(side='bottom',fill='x',pady=(8,0))
        ttk.Button(row,text='刷新论文 / 错误信息',command=self.refresh).pack(side='left')
        ttk.Button(row,text='已接收正文 → 第二板块',command=self.enter).pack(side='right')
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        def destroyed(event):
            if event.widget is self.window and not self.closed:
                self.stop();self.closed=True
        self.window.bind('<Destroy>',destroyed)
        self.refresh()
        self.timer=self.window.after(1000,self.poll)

    def refresh(self):
        if self.closed:return
        record=self.owner._selected()
        if record is None:
            record=next((r for r in (self.owner.run or {}).get('records',[]) if r.get('effective_decision')=='target'),None)
        self.record=record
        if not record:self.text.set('请先在板块1选择一篇论文，并判定“保留”。')
        else:
            self.text.set(record.get('title','')+'\nDOI：'+record.get('doi','待核对')+
                          ('\n\n'+failure_text(record) if failure_text(record) else '\n尚无 API HTTP 错误记录；可直接选择获取方式。'))
        active=not self.owner.busy and record is not None
        self.browser_button.configure(state='normal' if active and not self.process else 'disabled')
        for b in (self.api_button,self.auto_button):b.configure(state='disabled' if self.owner.busy else 'normal')

    def start_browser(self):
        if self.owner.busy or self.process or not self.record:return
        record=self.record
        if not record.get('doi') or (record.get('manual_decision') or record.get('effective_decision'))!='target':
            self.state.set('请先核对 DOI 并将这篇论文判定为“保留”。');return
        if importlib.util.find_spec('playwright') is None:
            self.state.set('缺少浏览器组件；请重新运行工作台依赖安装。也可以使用 Zotero 入口。');return
        try:
            url=web_url((record.get('route') or {}).get('official_entry') or record.get('url') or 'https://doi.org/'+record['doi'])
            self.folder=Path(self.owner.run['run_dir'])/'浏览器采集'/uuid.uuid4().hex[:12];self.folder.mkdir(parents=True)
            self.record_id=record['id']
            job={'authorized':True,'url':url,'record':{k:record.get(k) for k in ('id','doi','title')}}
            path=self.folder/'job.json';path.write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
            script=Path(__file__).with_name('browser_acquire.py')
            with (self.folder/'worker.log').open('ab') as log:
                self.process=subprocess.Popen([sys.executable,'-B',str(script),'--job',str(path)],cwd=script.parent,
                    stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self.state.set('已授权本次所选论文采集。请在新打开的专用 Chrome 中登录；此窗口会自动接收结果。')
            self.refresh()
        except (OSError,ValueError) as exc:self.state.set(str(exc))

    def poll(self):
        if self.closed:return
        if self.folder:
            path=self.folder/'status.json'
            try:
                if path.is_file():
                    status=json.loads(path.read_text(encoding='utf-8'));self.state.set(status['message'])
                if (self.folder/'result.json').is_file() and self.process:
                    if self.owner.busy:
                        self.state.set('浏览器已完成；等待当前工作台任务结束后自动关联。')
                    else:
                        message=receive_result(self.owner.run,self.record_id,self.folder)
                        self.owner._show_run(self.owner.run,preserve_selection=self.record_id)
                        self.owner.status_var.set(message);self.state.set(message)
                        self.process=None;self.folder=None;self.refresh()
                elif self.process and self.process.poll() is not None:
                    self.process=None;self.refresh()
            except (OSError,ValueError,KeyError) as exc:
                self.state.set('结果需要核对：'+str(exc));self.process=None
        self.timer=self.window.after(1000,self.poll)

    def stop(self):
        if self.folder:(self.folder/'stop').touch()
        self.state.set('已请求停止本次浏览器采集；正在进行的请求结束后关闭专用浏览器。')

    def resume(self):
        if self.folder and self.process:
            (self.folder/'resume').touch()
            self.state.set('已请求返回所选论文；程序继续自动检查可见正文或 PDF。')
        else:self.state.set('请先点击“打开浏览器并授权本次采集”。')

    def enter(self):
        from pdf_fetch import selected_records,existing_file,existing_xml
        if self.owner.busy:return
        run=self.owner.run or {}
        try:
            if not any(existing_file(r,run['run_dir']) or existing_xml(r,run['run_dir']) for r in selected_records(run)):
                self.state.set('尚未接收到可核验正文；请先完成 API 获取或浏览器采集。');return
            self.owner._launch_figures()
        except (OSError,ValueError) as exc:self.state.set(str(exc))

    def close(self):
        self.stop();self.closed=True
        if self.timer:self.window.after_cancel(self.timer)
        self.window.destroy()
