"""A guided desktop workflow with evidence visible beside each review item."""
import argparse
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

if not getattr(sys,'frozen',False):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from scrtool.core import FIELDS
from scrtool.workbench import Project, PROPERTIES, issue_label, questions, stamp
from workbench_preview import SourcePreview


def bundled_sample():
    if getattr(sys,'frozen',False):
        directory = Path(sys._MEIPASS) / 'data'
        real = directory / 'records_with_abstracts.json'
        return real if real.exists() else directory / 'demo_abstracts.json'
    root = Path(__file__).resolve().parents[1]
    real = root / 'evaluation/abstract_collector_exe_20260929/records_with_abstracts.json'
    return real if real.exists() else root / 'examples/demo_abstracts.json'


def tree(parent, columns, widths):
    frame=ttk.Frame(parent)
    table=ttk.Treeview(frame,columns=tuple(columns),show='headings',selectmode='extended')
    for key,label in columns.items():
        table.heading(key,text=label); table.column(key,width=widths.get(key,120),minwidth=60)
    ys=ttk.Scrollbar(frame,command=table.yview)
    xs=ttk.Scrollbar(frame,orient='horizontal',command=table.xview)
    table.configure(yscrollcommand=ys.set,xscrollcommand=xs.set)
    frame.rowconfigure(0,weight=1); frame.columnconfigure(0,weight=1)
    table.grid(row=0,column=0,sticky='nsew'); ys.grid(row=0,column=1,sticky='ns'); xs.grid(row=1,column=0,sticky='ew')
    return frame,table


class Workbench:
    def __init__(self,root):
        self.root=root; self.project=None; self.busy=False; self.events=queue.Queue()
        self.api_credentials={'email':'','openalex_key':'','elsevier_key':'',
                              'elsevier_insttoken':'','springer_key':''}
        self.batch_stop=threading.Event(); self.batch_running=False
        self.current_source=None; self.current_record=None; self.current_claim=None; self.records=[]; self.missing=[]; self.claims=[]
        root.title('NH₃-SCR 文献数据工作台 0.8')
        root.geometry('1280x850'); root.minsize(1020,720)
        style=ttk.Style(root)
        style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10),background='#F5F7FB',foreground='#19324A')
        style.configure('TFrame',background='#F5F7FB')
        style.configure('TLabel',background='#F5F7FB',foreground='#19324A')
        style.configure('TButton',padding=(11,7),background='#E6EDF5',borderwidth=0)
        style.map('TButton',background=[('active','#D4E4F4')])
        style.configure('Accent.TButton',padding=(13,8),background='#1665A3',foreground='white',
                        font=('Microsoft YaHei UI',10,'bold'))
        style.map('Accent.TButton',background=[('active','#0E4D80')],foreground=[('active','white')])
        style.configure('Treeview',rowheight=30,background='white',fieldbackground='white',foreground='#19324A')
        style.map('Treeview',background=[('selected','#D8EAFB')],foreground=[('selected','#19324A')])
        style.configure('TNotebook.Tab',padding=(18,10),font=('Microsoft YaHei UI',10,'bold'))
        header=tk.Frame(root,bg='#173B5E'); header.pack(fill='x')
        tk.Label(header,text='NH₃-SCR 文献数据工作台',font=('Microsoft YaHei UI',18,'bold'),
                 bg='#173B5E',fg='white').pack(anchor='w',padx=18,pady=(12,0))
        tk.Label(header,text='统一检索  →  核对论文  →  按出版社获取正文  →  对照原文审核  →  导出',
                 font=('Microsoft YaHei UI',10),bg='#173B5E',fg='#D9E9F7').pack(anchor='w',padx=18,pady=(2,12))
        bar=ttk.Frame(root); bar.pack(fill='x',padx=15,pady=(10,0))
        for label,fn in [('新建项目',self.new_project),('继续以前项目',self.open_project),('接口设置',self.api_settings),('打开项目文件夹',lambda:self.open_file(self.project.folder if self.project else None)),('简易操作说明',self.help)]:
            ttk.Button(bar,text=label,command=fn).pack(side='left',padx=(0,6))
        ttk.Label(bar,text='核对人：').pack(side='left',padx=(15,0))
        self.reviewer=tk.StringVar()
        ttk.Entry(bar,textvariable=self.reviewer,width=15).pack(side='left')
        self.project_label=tk.StringVar(value='还没有选择项目。可以在线检索，或导入已有摘要开始。')
        ttk.Label(root,textvariable=self.project_label,wraplength=1180).pack(fill='x',padx=15,pady=6)
        self.status=tk.StringVar(value='先收集摘要；人工确认后才进入正文提取。所有操作自动保存在项目中。')
        ttk.Label(root,textvariable=self.status,wraplength=1180).pack(fill='x',padx=15,pady=(0,7))
        self.tabs=ttk.Notebook(root); self.tabs.pack(fill='both',expand=True,padx=15,pady=(0,12))
        self.pages=[ttk.Frame(self.tabs) for _ in range(5)]
        for page,title in zip(self.pages,['1 收集摘要','2 核对论文','3 正文与提取','4 对照原文审核','5 导出结果']):
            self.tabs.add(page,text=title)
        self.build_import(); self.build_papers(); self.build_materials(); self.build_review(); self.build_export()
        root.after(100,self.poll)
        root.protocol('WM_DELETE_WINDOW',self.close)

    def guarded(self):
        if self.busy:
            messagebox.showinfo('正在处理','请等当前处理完成，再进行下一步。'); return False
        return True

    def ensure_project(self):
        if not self.project:
            self.project=Project(Path.home()/'Documents'/'NH3-SCR 工作项目'/('项目_'+stamp()))
        return self.project

    def new_project(self):
        if not self.guarded(): return
        folder=filedialog.askdirectory(title='选择项目保存位置（会新建独立项目文件夹）')
        if folder:
            project=self.safe(lambda:Project(Path(folder)/('NH3SCR_项目_'+stamp())))
            if project:
                self.project=project; self.refresh(); self.tabs.select(0)

    def open_project(self):
        if not self.guarded(): return
        path=filedialog.askopenfilename(title='选择以前项目中的 project.json',filetypes=[('工作项目','project.json')])
        if path:
            self.safe(lambda:setattr(self,'project',Project(Path(path).parent))); self.refresh()

    def safe(self,fn):
        try: return fn()
        except Exception as exc: messagebox.showerror('需要处理',str(exc)); return None

    def act(self,fn,message):
        try: fn()
        except Exception as exc:
            messagebox.showerror('需要处理',str(exc)); return False
        self.refresh(); self.status.set(message); return True

    def run(self,fn,done):
        if not self.guarded(): return
        self.busy=True; self.status.set('正在处理，请稍等……可以查看原文，先不要修改项目。')
        def worker():
            try: self.events.put(('done',done,fn()))
            except Exception as exc: self.events.put(('error',None,str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        try:
            kind,done,result=self.events.get_nowait()
            if kind=='progress':
                self.status.set(result)
            elif kind=='error':
                self.busy=False
                self.batch_running=False
                self.status.set('本次操作未完成，已保存的结果仍在。'); messagebox.showerror('需要处理',result)
            else:
                self.busy=False
                self.refresh(); done(result)
        except queue.Empty: pass
        self.root.after(100,self.poll)

    def open_file(self,path):
        if not path: return
        self.safe(lambda:os.startfile(str(path)))

    def api_settings(self):
        if not self.guarded():return
        dialog=tk.Toplevel(self.root); dialog.title('文献接口设置'); dialog.geometry('560x390')
        dialog.transient(self.root)
        body=ttk.Frame(dialog,padding=18); body.pack(fill='both',expand=True)
        ttk.Label(body,text='统一检索与正文获取使用同一组接口设置',
                  font=('Microsoft YaHei UI',13,'bold')).grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,14))
        fields=[('elsevier_key','Elsevier API key',True),
                ('elsevier_insttoken','Elsevier InstToken（如学校提供）',True),
                ('springer_key','Springer Nature key',True),
                ('openalex_key','OpenAlex key',True),
                ('email','联系邮箱（Unpaywall）',False)]
        values={}
        for index,(key,label,secret) in enumerate(fields,1):
            ttk.Label(body,text=label).grid(row=index,column=0,sticky='w',pady=7)
            values[key]=tk.StringVar(value=self.api_credentials[key])
            ttk.Entry(body,textvariable=values[key],show='●' if secret else '',width=34).grid(
                row=index,column=1,sticky='ew',padx=(12,0),pady=7)
        body.columnconfigure(1,weight=1)
        ttk.Label(body,text='只保存在本次程序会话中，不写入项目或 GitHub。API key 不等于全文访问权限。',
                  wraplength=500).grid(row=6,column=0,columnspan=2,sticky='w',pady=(16,8))
        def save():
            self.api_credentials={key:value.get().strip() for key,value in values.items()}
            self.status.set('接口设置已用于本次会话。现在可以在线检索，或对已保留论文获取正文。')
            dialog.destroy()
        ttk.Button(body,text='保存接口设置',command=save,style='Accent.TButton').grid(
            row=7,column=0,columnspan=2,sticky='e',pady=8)
        dialog.grab_set()

    def build_import(self):
        page=self.pages[0]
        ttk.Label(page,text='直接在线检索，或导入浏览器、Zotero 和本地文件中的摘要。检索结果先核对，再获取正文。',wraplength=1100).pack(anchor='w',padx=18,pady=(16,8))
        search=ttk.Frame(page); search.pack(fill='x',padx=18,pady=(0,10))
        ttk.Label(search,text='检索词').pack(side='left')
        self.search_query=tk.StringVar(value='NH3-SCR catalyst')
        ttk.Entry(search,textvariable=self.search_query,width=45).pack(side='left',padx=(8,12),fill='x',expand=True)
        ttk.Label(search,text='每个来源最多').pack(side='left')
        self.search_limit=tk.IntVar(value=20)
        ttk.Spinbox(search,from_=1,to=100,textvariable=self.search_limit,width=5).pack(side='left',padx=(5,12))
        ttk.Button(search,text='在线检索并加入清单',command=self.online_search,
                   style='Accent.TButton').pack(side='left')
        ttk.Label(page,text='默认使用 OpenAlex 和 Crossref；在上方“接口设置”输入密钥后，自动加入对应出版商接口。',
                  wraplength=1100).pack(anchor='w',padx=18,pady=(0,12))
        self.sample_label = ('使用现有 80 篇摘要开始测试' if bundled_sample().name == 'records_with_abstracts.json'
                             else '使用合成演示摘要开始测试')
        for label,fn in [(self.sample_label,lambda:self.import_paths([bundled_sample()])),('添加自己的摘要文件',self.choose_abstracts),('浏览器采集按钮怎么安装',self.browser_help),('下一步：核对论文',lambda:self.tabs.select(1))]:
            ttk.Button(page,text=label,command=fn).pack(anchor='w',padx=18,pady=7)
        self.summary=tk.StringVar(value='导入后会显示论文数量和待核对数量。')
        ttk.Label(page,textvariable=self.summary,font=('Microsoft YaHei UI',12),wraplength=1100).pack(anchor='w',padx=18,pady=20)
        ttk.Label(page,text='初筛只用于挑选论文。每篇论文需要在第 2 步人工确认，才会出现在正文提取列表中。',wraplength=1100).pack(anchor='w',padx=18)

    def choose_abstracts(self):
        if not self.guarded(): return
        paths=filedialog.askopenfilenames(title='选择摘要文件',filetypes=[('摘要文件','*.json *.html *.htm *.ris *.csv'),('所有文件','*.*')])
        if paths:self.import_paths(paths)

    def online_search(self):
        if not self.guarded():return
        try:
            limit=int(self.search_limit.get())
            if not 1<=limit<=100:raise ValueError()
        except (ValueError,tk.TclError):
            return messagebox.showerror('检索数量','每个来源请填 1–100。')
        project=self.ensure_project()
        credentials=dict(self.api_credentials)
        sources=['openalex','crossref']
        if credentials['elsevier_key']:sources.append('scopus')
        if credentials['springer_key']:sources.append('springer')
        def done(result):
            self.status.set(f"本次找到 {result['found']} 篇；清单共 {result['total']} 篇。"
                            +(f" 有 {len(result['errors'])} 个来源提示，记录在项目文件夹。" if result['errors'] else ' 请到第 2 步核对。'))
            self.tabs.select(1)
        self.run(lambda:project.search_online(self.search_query.get(),sources,limit=limit,
                     email=credentials['email'] or None,openalex_key=credentials['openalex_key'] or None,
                     elsevier_key=credentials['elsevier_key'] or None,
                     elsevier_insttoken=credentials['elsevier_insttoken'] or None,
                     springer_key=credentials['springer_key'] or None),done)

    def import_paths(self,paths):
        if not self.guarded(): return
        project=self.safe(self.ensure_project)
        if not project:return
        self.run(lambda:project.import_abstracts(paths),lambda result:(self.status.set(f'已整理 {result[0]} 篇论文；无法读取的文件 {len(result[1])} 个。现在核对论文。'),self.tabs.select(1)))

    def build_papers(self):
        page=self.pages[1]
        ttk.Label(page,text='看右侧原文摘要，确认实际研究 NH₃-SCR、催化剂为研究对象、且有作者自己的实验数据。',wraplength=1150).pack(anchor='w',pady=8)
        bar=ttk.Frame(page); bar.pack(fill='x')
        self.paper_filter=tk.StringVar(value='尚未确认')
        combo=ttk.Combobox(bar,textvariable=self.paper_filter,values=['尚未确认','已保留','全部'],state='readonly',width=12)
        combo.pack(side='left'); combo.bind('<<ComboboxSelected>>',lambda e:self.refresh_papers())
        for label,decision in [('保留：原始实验研究','target'),('不相关 / 非原始实验','non_target'),('暂时待定','review')]:
            ttk.Button(bar,text=label,command=lambda d=decision:self.decide_papers(d)).pack(side='left',padx=3)
        ttk.Button(bar,text='下一步：准备正文',command=lambda:self.tabs.select(2)).pack(side='right')
        ttk.Button(bar,text='评分权重',command=self.score_weights).pack(side='left',padx=5)
        prefetch=ttk.Frame(page); prefetch.pack(fill='x',pady=(8,0))
        ttk.Label(prefetch,text='先下载初筛相关论文，每批').pack(side='left')
        self.prefetch_limit=tk.IntVar(value=15)
        ttk.Spinbox(prefetch,from_=1,to=100,textvariable=self.prefetch_limit,width=5).pack(side='left',padx=5)
        ttk.Label(prefetch,text='篇，下载后仍需人工核对').pack(side='left')
        ttk.Button(prefetch,text='预取下一批',command=self.prefetch_batch,style='Accent.TButton').pack(side='left',padx=8)
        ttk.Button(prefetch,text='停止批量',command=self.stop_batch).pack(side='left',padx=3)
        ttk.Button(prefetch,text='打开预取结果',command=self.open_prefetch_report).pack(side='left',padx=3)
        ttk.Button(prefetch,text='打开所选预取PDF',command=self.open_prefetched_pdf).pack(side='left',padx=3)
        panes=ttk.Panedwindow(page,orient='horizontal'); panes.pack(fill='both',expand=True,pady=8)
        frame,self.paper_table=tree(panes,{'auto':'规则初筛','human':'人工判断','score':'阅读评分','title':'论文题目'},{'auto':75,'human':85,'score':80,'title':420})
        panes.add(frame,weight=1)
        self.paper_preview=SourcePreview(panes,self.open_file); panes.add(self.paper_preview.frame,weight=1)
        self.paper_table.bind('<<TreeviewSelect>>',self.preview_paper)
        self.paper_note=tk.StringVar()
        note=ttk.Frame(page); note.pack(fill='x')
        ttk.Label(note,text='核对理由（可选）：').pack(side='left')
        ttk.Entry(note,textvariable=self.paper_note).pack(side='left',fill='x',expand=True)
        ttk.Button(note,text='打开所选论文网页',command=self.open_paper_url).pack(side='left',padx=5)

    def refresh_papers(self):
        selected=self.paper_table.selection()
        self.paper_table.delete(*self.paper_table.get_children())
        self.paper_preview.show({},'选择论文后，这里显示具体核对问题与原文摘要。')
        if not self.project:return
        for row in sorted(self.project.state['papers'],key=lambda r:-r.get('priority_score',0)):
            human=row.get('human_decision')
            if self.paper_filter.get()=='尚未确认' and human in {'target','non_target'}:continue
            if self.paper_filter.get()=='已保留' and human!='target':continue
            self.paper_table.insert('', 'end',iid=row['record_id'],values=(row['decision_label'],{'target':'已保留','non_target':'已排除','review':'待定'}.get(human,'未确认'),row.get('priority_score','未评分'),row['title']))
        available=self.paper_table.get_children()
        if available:
            choice=next((identity for identity in selected if identity in available),available[0])
            self.paper_table.selection_set(choice); self.preview_paper()

    def preview_paper(self,_event=None):
        ids=self.paper_table.selection()
        if not ids or not self.project:return
        row=self.project.paper(ids[0])
        prompt='需要核对：\n① 实际反应是否为 NH₃-SCR？\n② 催化剂是否为研究对象？\n③ 是否包含作者自己的实验数据？\n初筛原因：'+row['reason']
        if row.get('score_components'):
            prompt+='\n阅读评分：'+str(row['priority_score'])+' / 100；用于排序，未校准。'
            prompt+='\n'+'；'.join(c['label']+' '+str(c['contribution']) for c in row['score_components'].values())
            if row.get('score_basis')=='title_keywords_only':prompt+='\n缺少完整摘要，本次评分仅依据题名和关键词。'
        prefetched=self.project.state.get('prefetch',{}).get(ids[0])
        if prefetched:
            prompt+='\n批量预取：'+prefetched['status']+'。下载成功也仍需人工核对和保留。'
        self.paper_preview.show({'text':row['title']+'\nDOI：'+row['doi']+'\n\n'+(row['abstract'] or '未取得摘要。请打开论文网页核对。'),'locator':'原文摘要'},prompt)

    def decide_papers(self,decision):
        if not self.guarded() or not self.project:return
        ids=self.paper_table.selection()
        if not ids:return messagebox.showinfo('选择论文','先选择要核对的论文。可按 Ctrl 多选。')
        self.act(lambda:self.project.decide_papers(ids,decision,self.reviewer.get(),self.paper_note.get()),
                 '人工论文决定已保存。已保留的论文可在第 3 步添加正文。')

    def open_paper_url(self):
        ids=self.paper_table.selection()
        if ids:
            row=self.project.paper(ids[0]); url=('https://doi.org/'+row['doi']) if row['doi'] else row.get('abstract_url','')
            if url.startswith(('https://','http://')):webbrowser.open(url)

    def prefetch_batch(self):
        if not self.guarded() or not self.project:return
        try:
            limit=int(self.prefetch_limit.get())
            if not 1<=limit<=100:raise ValueError()
        except (ValueError,tk.TclError):
            return messagebox.showerror('预取数量','每批请填 1–100。')
        project=self.project
        credentials={key:value or None for key,value in self.api_credentials.items()}
        self.batch_stop.clear(); self.batch_running=True
        def progress(index,total,title,state):
            self.events.put(('progress',None,f'预取 {index}/{total}：{state} · {title[:75]}'))
        def done(result):
            self.batch_running=False
            label='已停止' if result['stopped'] else '已完成'
            self.status.set(f"预取{label}：取得 {result['downloaded']} 篇，未取得 {result['failed']} 篇。下载成功的论文仍需你在第 2 步核对保留。点“打开预取结果”看明细。")
        self.run(lambda:project.prefetch_screened(limit=limit,progress=progress,
                 should_stop=self.batch_stop.is_set,**credentials),done)

    def open_prefetch_report(self):
        if not self.project:return
        path=self.project.folder/'批量预取_最近一次.csv'
        if path.exists():self.open_file(path)
        else:messagebox.showinfo('还没有结果','先点“预取下一批”。')

    def open_prefetched_pdf(self):
        if not self.project:return
        ids=self.paper_table.selection()
        if len(ids)!=1:return messagebox.showinfo('选择一篇论文','请只选择一篇论文。')
        item=self.project.state.get('prefetch',{}).get(ids[0],{})
        path=self.project.folder/item['path'] if item.get('path') else None
        if path and path.exists():self.open_file(path)
        else:messagebox.showinfo('没有预取文件','这篇论文尚未预取到 PDF。')

    def build_materials(self):
        page=self.pages[2]
        ttk.Label(page,text='已保留论文可逐篇获取，也可批量获取尚无正文的论文。成功后自动归档；失败可在结果表中查看，再用浏览器或插件补充。',wraplength=1150).pack(anchor='w',pady=9)
        bar=ttk.Frame(page); bar.pack(fill='x')
        ttk.Button(bar,text='自动获取所选论文正文',command=self.acquire_material,
                   style='Accent.TButton').pack(side='left',padx=(0,8))
        ttk.Button(bar,text='批量获取待下载正文',command=self.acquire_batch,
                   style='Accent.TButton').pack(side='left',padx=(0,8))
        ttk.Button(bar,text='停止批量',command=self.stop_batch).pack(side='left',padx=(0,8))
        ttk.Button(bar,text='打开论文网页',command=self.open_material_page).pack(side='left',padx=5)
        ttk.Button(bar,text='提取所选论文',command=self.extract).pack(side='left',padx=5)
        ttk.Button(bar,text='下一步：看原文审核',command=lambda:self.tabs.select(3)).pack(side='right')
        options=ttk.Frame(page); options.pack(fill='x',pady=(8,0))
        ttk.Label(options,text='已有文件 / 补充材料：').pack(side='left')
        self.role=tk.StringVar(value='正文')
        ttk.Combobox(options,textvariable=self.role,values=['正文','补充材料','原始数据表'],
                     state='readonly',width=14).pack(side='left')
        ttk.Button(options,text='手动添加文件',command=self.add_materials).pack(side='left',padx=5)
        ttk.Button(options,text='使用浏览器采集全文',command=self.attach_browser_capture).pack(side='left',padx=5)
        ttk.Button(options,text='打开已添加正文',command=self.open_primary).pack(side='left',padx=5)
        ttk.Button(options,text='打开批量结果',command=self.open_batch_report).pack(side='left',padx=5)
        ttk.Label(page,text='按期刊/出版社信息选择接口：Elsevier API、Springer Nature API 或开放来源。密钥在上方“接口设置”输入。',
                  wraplength=1150).pack(anchor='w',pady=(7,0))
        frame,self.material_table=tree(page,{'title':'已保留论文','files':'材料数','status':'提取状态'},{'title':780,'files':80,'status':230})
        frame.pack(fill='both',expand=True,pady=8)
        self.material_detail=tk.StringVar(value='选择论文查看已绑定的文件。')
        ttk.Label(page,textvariable=self.material_detail,wraplength=1150).pack(fill='x',pady=6)
        self.material_table.bind('<<TreeviewSelect>>',self.material_selected)
        ttk.Label(page,text='此步使用已有规则提取。扫描图、复杂曲线或规则不覆盖的内容会列入“未提取页”，需要继续补提。',wraplength=1150).pack(anchor='w',pady=7)

    def material_selected(self,_event=None):
        ids=self.material_table.selection()
        if ids:
            items=self.project.state['attachments'].get(ids[0],[])
            from scrtool.fulltext import publisher_route
            route={'elsevier':'Elsevier','springer_nature':'Springer Nature',
                   'open_sources':'开放来源'}[publisher_route(self.project.paper(ids[0]))]
            details=[''.join({'primary':'正文','supplement':'补充材料','data':'数据表'}[x['role']]+'：'+Path(x['path']).name) for x in items]
            details.insert(0,'识别来源：'+route+'；期刊：'+str(self.project.paper(ids[0]).get('journal') or '待核对'))
            if self.project.state.get('captures',{}).get(ids[0]):
                details.append('已有浏览器采集正文；可点“使用浏览器采集全文”添加为正文。')
            result=self.project.state.get('acquisition',{}).get(ids[0])
            if result:
                details.append('自动获取：'+('已取得 PDF' if result.get('status') in {'downloaded_pdf','cached_pdf'} else '暂未取得 PDF'))
                if result.get('source'):details.append('来源：'+result['source'])
                if result.get('message'):
                    details.append(result['message'])
                elif result.get('errors'):
                    from scrtool.fulltext import guidance
                    details.append(guidance(self.project.paper(ids[0]),result))
            self.material_detail.set('\n'.join(details) or '尚未添加材料。')

    def acquire_material(self):
        if not self.guarded() or not self.project:return
        ids=self.material_table.selection()
        if len(ids)!=1:return messagebox.showinfo('选择一篇论文','请只选择一篇已保留论文。')
        identity=ids[0]; project=self.project
        def done(result):
            self.material_table.selection_set(identity)
            self.material_selected()
            if result['status']=='already_attached':
                self.status.set('这篇论文已有正文文件，可以直接点“提取所选论文”。')
            elif result.get('path'):
                self.status.set('正文已自动添加；来源：'+str(result.get('source'))+'。请核对论文身份，再点“提取所选论文”。')
            else:
                self.status.set(result.get('message') or '暂未取得 PDF。请打开论文网页，下载后添加本地文件。')
        credentials=dict(self.api_credentials)
        self.run(lambda:project.acquire_primary(identity,
                     email=credentials['email'] or None,
                     openalex_key=credentials['openalex_key'] or None,
                     elsevier_key=credentials['elsevier_key'] or None,
                     elsevier_insttoken=credentials['elsevier_insttoken'] or None,
                     springer_key=credentials['springer_key'] or None),done)

    def acquire_batch(self):
        if not self.guarded() or not self.project:return
        project=self.project
        identities=[r['record_id'] for r in project.state['papers']
                    if r.get('human_decision')=='target' and not any(
                        item['role']=='primary' for item in project.state['attachments'].get(r['record_id'],[]))]
        if not identities:
            return messagebox.showinfo('没有待下载论文','已保留论文中没有缺少正文的条目。')
        if not messagebox.askyesno('批量获取正文',f'将依次尝试 {len(identities)} 篇已保留论文。失败不会停止整批，结果保存在项目文件夹。现在开始吗？'):
            return
        credentials={key:value or None for key,value in self.api_credentials.items()}
        self.batch_stop.clear()
        self.batch_running=True
        def progress(index,total,title,state):
            self.events.put(('progress',None,f'正文获取 {index}/{total}：{state} · {title[:75]}'))
        def done(result):
            self.batch_running=False
            label='已停止' if result['stopped'] else '已完成'
            self.status.set(f"批量{label}：新获取 {result['downloaded']} 篇，已有正文 {result['existing']} 篇，未获取 {result['failed']} 篇。点“打开批量结果”查看每篇原因。")
        self.run(lambda:project.acquire_many(identities,progress=progress,
                 should_stop=self.batch_stop.is_set,**credentials),done)

    def stop_batch(self):
        if self.batch_running:
            self.batch_stop.set()
            self.status.set('已请求停止；当前这篇处理结束后停止，已保存的正文不会丢失。')

    def open_batch_report(self):
        if not self.project:return
        path=self.project.folder/'批量正文获取_最近一次.csv'
        if path.exists():self.open_file(path)
        else:messagebox.showinfo('还没有结果','先点“批量获取待下载正文”。')

    def attach_browser_capture(self):
        if not self.guarded() or not self.project:return
        ids=self.material_table.selection()
        if len(ids)!=1:return messagebox.showinfo('选择一篇论文','请只选择一篇已保留论文。')
        self.act(lambda:self.project.attach_browser_capture(ids[0]),
                 '浏览器采集的材料已添加。请打开核对论文身份、文件内容和归属，再点“提取所选论文”。')

    def open_material_page(self):
        if not self.project:return
        ids=self.material_table.selection()
        if len(ids)!=1:return messagebox.showinfo('选择一篇论文','请先选择一篇论文。')
        paper=self.project.paper(ids[0])
        url=('https://doi.org/'+paper['doi']) if paper.get('doi') else paper.get('landing_page_url')
        if url and url.startswith(('https://','http://')):webbrowser.open(url)
        else:messagebox.showinfo('缺少链接','这篇题录没有 DOI 或论文网址，请核对题录。')

    def open_primary(self):
        if not self.project:return
        ids=self.material_table.selection()
        if len(ids)!=1:return messagebox.showinfo('选择一篇论文','请先选择一篇论文。')
        item=next((x for x in self.project.state['attachments'].get(ids[0],[]) if x['role']=='primary'),None)
        if item:self.open_file(self.project.folder/item['path'])
        else:messagebox.showinfo('尚无正文','请先尝试自动获取，或手动添加这篇论文的正文文件。')

    def add_materials(self):
        if not self.guarded() or not self.project:return
        ids=self.material_table.selection()
        if len(ids)!=1:return messagebox.showinfo('选择一篇论文','添加材料时请只选择一篇，避免归属混淆。')
        paths=filedialog.askopenfilenames(title='选择这篇论文对应的材料',filetypes=[('论文或数据','*.pdf *.html *.htm *.md *.txt *.csv *.tsv *.xlsx *.json'),('所有文件','*.*')])
        if paths:self.act(lambda:self.project.attach(ids[0],paths,{'正文':'primary','补充材料':'supplement','原始数据表':'data'}[self.role.get()]),'材料已绑定。选择论文后点击“提取所选论文”。')

    def extract(self):
        if not self.guarded() or not self.project:return
        ids=self.material_table.selection()
        if not ids:return messagebox.showinfo('选择论文','先选择要提取的论文。第一轮建议一次 1–3 篇。')
        project=self.project
        def done(reports):
            self.status.set(f"提取完成：候选 {sum(r['candidates'] for r in reports)} 条，未自动提取内容块 {sum(r['unresolved_blocks'] for r in reports)} 个，读取错误 {sum(r['errors'] for r in reports)} 个。请对照原文审核。")
            self.tabs.select(3)
        self.run(lambda:project.extract(ids),done)

    def build_review(self):
        page=self.pages[3]
        ttk.Label(page,text='选择左侧记录，右侧直接显示核对问题、原文证据和 PDF 原始页。通过前请检查样品、数值、单位、条件。',wraplength=1150).pack(anchor='w',pady=7)
        bar=ttk.Frame(page); bar.pack(fill='x')
        self.record_filter=tk.StringVar(value='未审核')
        box=ttk.Combobox(bar,textvariable=self.record_filter,values=['未审核','有问题','已通过','全部'],state='readonly',width=10)
        box.pack(side='left'); box.bind('<<ComboboxSelected>>',lambda e:self.refresh_records())
        for label,fn in [('通过所选',lambda:self.review('approve')),('排除所选',lambda:self.review('reject')),('修正 / 补录一条',self.edit_record),('确认原始研究类型',self.confirm_document)]:
            ttk.Button(bar,text=label,command=fn).pack(side='left',padx=3)
        ttk.Button(bar,text='下一步：导出',command=lambda:self.tabs.select(4)).pack(side='right')
        figure_bar=ttk.Frame(page); figure_bar.pack(fill='x',pady=3)
        from workbench_figures import open_reader, choose_snapshot
        ttk.Button(figure_bar,text='打开当前页图片读数',command=lambda:open_reader(self)).pack(side='left')
        ttk.Button(figure_bar,text='导入已有图片读数',command=lambda:choose_snapshot(self)).pack(side='left',padx=5)
        panes=ttk.Panedwindow(page,orient='horizontal'); panes.pack(fill='both',expand=True,pady=6)
        left=ttk.Notebook(panes); self.review_tabs=left; panes.add(left,weight=1)
        frame,self.record_table=tree(left,{'sample':'催化剂','property':'指标','value':'数值','state':'审核状态','problem':'问题'},{'sample':115,'property':125,'value':100,'state':80,'problem':230})
        left.add(frame,text='提取的数值')
        frame2,self.missing_table=tree(left,{'file':'原文文件','location':'原文位置','reason':'需要查看什么'},{'file':180,'location':100,'reason':270})
        left.add(frame2,text='未提取页（看原文补提）')
        frame3,self.claim_table=tree(left,{'type':'表达类型','change':'报告变化','state':'审核状态','quote':'原文语句'}, {'type':145,'change':95,'state':85,'quote':350})
        left.add(frame3,text='语义候选')
        self.claim_table.bind('<<TreeviewSelect>>',self.preview_claim)
        self.original=SourcePreview(panes,self.open_file); panes.add(self.original.frame,weight=1)
        self.record_table.bind('<<TreeviewSelect>>',self.preview_record)
        self.missing_table.bind('<<TreeviewSelect>>',self.preview_missing)
        note=ttk.Frame(page); note.pack(fill='x')
        self.record_note=tk.StringVar()
        ttk.Label(note,text='审核理由 / 补提备注：').pack(side='left')
        ttk.Entry(note,textvariable=self.record_note).pack(side='left',fill='x',expand=True)
        ttk.Button(note,text='记录这页待补提内容',command=self.note_missing).pack(side='left',padx=3)

    def refresh_records(self):
        self.current_record=None; self.current_source=None; self.current_claim=None
        self.original.show({},'选择左侧记录，查看需要核对的问题和对应原文。')
        self.record_table.delete(*self.record_table.get_children()); self.missing_table.delete(*self.missing_table.get_children())
        self.claim_table.delete(*self.claim_table.get_children())
        self.records=self.project.candidates() if self.project else []
        self.missing=self.project.missing_blocks() if self.project else []
        self.claims=self.project.claims() if self.project else []
        for index,row in enumerate(self.claims):
            change=row.get('reported_change') or {}
            label={'prospective_claim':'预期或潜力','negated_statement':'否定表达','mechanistic_interpretation':'机理解释','reported_comparison':'比较关系','qualitative_observation':'定性变化'}.get(row['statement_type'],row['statement_type'])
            self.claim_table.insert('','end',iid=str(index),values=(label,str(change.get('amount',''))+' '+change.get('unit',''),{'pending':'未审核','approved':'已核对','rejected':'已排除'}[row['review_status']],row['evidence']))
        for index,row in enumerate(self.records):
            status=row['review_status']; choice=self.record_filter.get()
            if choice=='未审核' and status!='pending':continue
            if choice=='有问题' and not row['issues']:continue
            if choice=='已通过' and status!='approved':continue
            issue='；'.join(issue_label(x) for x in row['issues'])
            if row['category']=='performance' and row['property'] not in {'t50','t90'} and row['conditions'].get('temperature',{}).get('value') is None:
                issue=(issue+'；' if issue else '')+'缺反应温度，暂不能导出性能行'
            self.record_table.insert('','end',iid=str(index),values=(row['catalyst'] or '未找到',PROPERTIES.get(row['property'],row['property']),f"{row['value']} {row['unit']}",{'pending':'未审核','approved':'已通过','rejected':'已排除'}[status],issue))
        for index,row in enumerate(self.missing):
            reason='检查是否有性能、结构或条件数据' if row['reason']=='no_measurements_extracted' else '检查扫描内容 / 曲线，需另行取点'
            note=self.project.state.get('block_notes',{}).get(row['paper_id']+':'+row['block_id'],{})
            self.missing_table.insert('','end',iid=str(index),values=(Path(row['file']).name,row['locator'],('已记备注；' if note else '')+reason))

    def preview_record(self,_event=None):
        ids=self.record_table.selection()
        if not ids:return
        self.current_claim=None; self.current_record=self.records[int(ids[0])]
        self.current_source=self.project.source(self.current_record['paper_id'],self.current_record['block_id'])
        temp=self.current_record.get('conditions',{}).get('temperature',{})
        self.original.show(self.current_source,questions(self.current_record),self.current_record['evidence'],temp.get('evidence',''),self.project.folder/'previews')

    def preview_missing(self,_event=None):
        ids=self.missing_table.selection()
        if not ids:return
        row=self.missing[int(ids[0])]; self.current_record=None; self.current_claim=None
        self.current_source=self.project.source(row['paper_id'],row['block_id'])
        prompt='这页未自动提取到数值，请查看原文：\n① 是否有催化剂性能、结构表征或反应条件？\n② 若有，可补录文字数值；曲线需要经过取点后导入数据表。\n③ 若只是引言、参考文献等，可备注“没有目标数据”。'
        self.original.show(self.current_source,prompt,cache=self.project.folder/'previews')

    def review(self,decision):
        if not self.guarded() or not self.project:return
        if self.review_tabs.index('current')==2:
            ids=[self.claims[int(x)]['claim_id'] for x in self.claim_table.selection()]
            if not ids:return messagebox.showinfo('选择语句','先选择语义候选。')
            return self.act(lambda:self.project.review_claims(ids,decision,self.reviewer.get(),self.record_note.get()),'语义审核已保存。比较表达保存在语义表中，仍需补充科学关联。')
        ids=[self.records[int(x)]['record_id'] for x in self.record_table.selection()]
        if not ids:return messagebox.showinfo('选择记录','在“提取的数值”列表选择要审核的记录。')
        self.act(lambda:self.project.review(ids,decision,self.reviewer.get(),self.record_note.get()),'审核决定已保存。通过的数据可进入第 5 步导出；缺温度的性能值会保留不导出。')

    def note_missing(self):
        if not self.guarded() or not self.current_source:return
        source=self.current_source
        self.act(lambda:self.project.note_missing(source['paper_id'],source['block_id'],self.reviewer.get(),self.record_note.get()),'原文页面备注已保存。')

    def confirm_document(self):
        if not self.guarded() or not self.current_source:return
        source=self.current_source
        if source.get('document_type')!='unknown':return messagebox.showinfo('文档类型','这份来源已经有文档类型。此按钮用于“尚未确认文档为原始研究”的记录。')
        evidence=simpledialog.askstring('确认原始研究','请从右侧原文复制能证明作者做了实验的文字（至少 12 字符）。确认后仍需逐条审核数值。',parent=self.root)
        if evidence:self.act(lambda:self.project.confirm_primary_document(source['paper_id'],source['source_file'],self.reviewer.get(),evidence),'原始研究类型已由你确认，记录仍需逐条审核。')

    def edit_record(self):
        if not self.guarded() or not self.current_source:return messagebox.showinfo('选择原文','先选择一条数值或一个未提取页面。')
        if self.current_claim:return messagebox.showinfo('语义表达','请先核对并用备注记录比较对象、指标和条件；只有原文明确给出绝对数值时，才切到未提取页补录数值。')
        source=self.current_source; record=self.current_record
        dialog=tk.Toplevel(self.root); dialog.title('照原文修正 / 补录一条数据'); dialog.geometry('760x680')
        ttk.Label(dialog,text='只填写右侧原文能直接支持的数据。保存后仍需点击“通过”。温度留空会保留原记录已有条件。',wraplength=720).pack(fill='x',padx=15,pady=10)
        fields=ttk.Frame(dialog); fields.pack(fill='x',padx=15)
        catalyst=tk.StringVar(value=(record or {}).get('catalyst') or '')
        choices={PROPERTIES.get(key,key)+' ['+unit+']':key for key,(_,unit) in FIELDS.items()}
        initial=(record or {}).get('property','nox_conversion')
        prop=tk.StringVar(value=next(label for label,key in choices.items() if key==initial))
        raw=tk.StringVar(value=(record or {}).get('raw_value',''))
        unit=tk.StringVar(value=(record or {}).get('raw_unit',FIELDS[initial][1]))
        temperature=tk.StringVar(); temperature_unit=tk.StringVar(value='degC')
        entries=[('催化剂（原文名称）',catalyst),('原文数值 / 类型',raw),('数值单位（按原文）',unit),('补充反应温度（原文数值，可留空）',temperature)]
        for index,(label,var) in enumerate(entries):
            ttk.Label(fields,text=label).grid(row=index,column=0,sticky='w',pady=4)
            ttk.Entry(fields,textvariable=var,width=47).grid(row=index,column=1,sticky='ew',pady=4)
        ttk.Label(fields,text='指标').grid(row=4,column=0,sticky='w',pady=4)
        combo=ttk.Combobox(fields,textvariable=prop,values=list(choices),state='readonly',width=47)
        combo.grid(row=4,column=1,sticky='ew')
        combo.bind('<<ComboboxSelected>>',lambda e:unit.set(FIELDS[choices[prop.get()]][1]))
        ttk.Label(fields,text='温度单位').grid(row=5,column=0,sticky='w',pady=4)
        ttk.Combobox(fields,textvariable=temperature_unit,values=['degC','K'],state='readonly',width=12).grid(row=5,column=1,sticky='w')
        ttk.Label(dialog,text='数值证据：从右侧原文复制，必须含所填数值。').pack(anchor='w',padx=15,pady=(12,2))
        evidence=tk.Text(dialog,height=7,wrap='word'); evidence.pack(fill='both',expand=True,padx=15)
        if record:evidence.insert('1.0',record['evidence'])
        ttk.Label(dialog,text='温度证据：仅在上面补充温度时填写，从同一原文页复制。').pack(anchor='w',padx=15,pady=(9,2))
        temp_evidence=tk.Text(dialog,height=4,wrap='word'); temp_evidence.pack(fill='x',padx=15)
        def save():
            success=self.act(lambda:self.project.manual_record(source['paper_id'],source['block_id'],catalyst.get(),choices[prop.get()],raw.get(),unit.get(),evidence.get('1.0','end').strip(),self.reviewer.get(),temperature.get(),temp_evidence.get('1.0','end').strip(),temperature_unit.get(),record['record_id'] if record else None),
                             '补录 / 修正已保存，原记录保留在审核历史中。请重新选中这条记录，核对后通过。')
            if success:dialog.destroy()
        ttk.Button(dialog,text='保存为待审核数据',command=save).pack(pady=12)

    def build_export(self):
        page=self.pages[4]
        ttk.Label(page,text='只导出已经人工通过的数据。缺反应温度的性能记录、未解决的冲突会保留在报告中。',wraplength=1100).pack(anchor='w',padx=18,pady=18)
        self.export_summary=tk.StringVar(value='先在第 4 步完成原文核对。')
        ttk.Label(page,textvariable=self.export_summary,font=('Microsoft YaHei UI',12),wraplength=1100).pack(anchor='w',padx=18,pady=12)
        ttk.Button(page,text='保存审核结果并导出数据',command=self.export).pack(anchor='w',padx=18,pady=8)
        ttk.Button(page,text='打开本次导出文件夹',command=self.open_export).pack(anchor='w',padx=18,pady=8)
        ttk.Button(page,text='单独导出语义候选',command=self.export_claims).pack(anchor='w',padx=18,pady=8)
        ttk.Label(page,text='输出：已审核观测表、性能长表、样品特征表、暂缓导出记录和质量报告。\n能导出数据不代表跨论文质量已经达标；本版用于推进小批测试，没有训练模型。',wraplength=1100).pack(anchor='w',padx=18,pady=16)

    def export(self):
        if not self.project:return
        project=self.project
        def done(result):
            folder,report=result
            self.status.set(f"已导出：审核通过 {report['approved']} 条，性能表 {report['ml_rows']} 行，缺温度暂缓 {report['withheld']} 条，仍待审核 {report['pending']} 条。")
        self.run(project.export,done)

    def open_export(self):
        if self.project and self.project.state.get('last_export'):
            self.open_file(self.project.folder/self.project.state['last_export'])

    def export_claims(self):
        if self.guarded() and self.project:
            folder=self.safe(self.project.export_claims)
            if folder:self.status.set('语义候选已保存：'+str(folder)); self.open_file(folder)

    def preview_claim(self,_event=None):
        ids=self.claim_table.selection()
        if not ids:return
        row=self.claims[int(ids[0])]; self.current_claim=row; self.current_record=None
        self.current_source=self.project.source(row['paper_id'],row['block_id'])
        prompt='核对原句：\n① 这句话报告实验结果、比较关系，还是未来预期？\n② 改善的指标、样品和比较对象是什么？\n③ 比较是否在相同条件下？基准值是否明确？\n报告变化：'+str(row.get('reported_change') or '未量化')+'\n原始表达保留，绝对值未推算。'
        self.original.show(self.current_source,prompt,row['evidence'],cache=self.project.folder/'previews')

    def score_weights(self):
        if not self.guarded() or not self.project:return
        from scrtool.scoring import DEFAULT_WEIGHTS,LABELS
        dialog=tk.Toplevel(self.root); dialog.title('文献阅读评分权重')
        ttk.Label(dialog,text='权重用于安排阅读顺序；不会替代论文的人工决定。',wraplength=460).grid(row=0,column=0,columnspan=2,padx=15,pady=10)
        values={}; configured=self.project.state.get('score_config',{}).get('weights',DEFAULT_WEIGHTS)
        for index,key in enumerate(DEFAULT_WEIGHTS,1):
            ttk.Label(dialog,text=LABELS[key]).grid(row=index,column=0,sticky='w',padx=15,pady=5)
            values[key]=tk.StringVar(value=str(configured[key])); ttk.Entry(dialog,textvariable=values[key],width=12).grid(row=index,column=1,padx=15)
        def save():
            if self.act(lambda:self.project.score_papers({'weights':{k:float(v.get()) for k,v in values.items()}}),'评分已更新；排序清单保存在项目文件夹。'):dialog.destroy()
        ttk.Button(dialog,text='保存并重新评分',command=save).grid(row=8,column=0,columnspan=2,pady=15)

    def refresh(self):
        self.refresh_papers(); self.refresh_records()
        self.material_table.delete(*self.material_table.get_children())
        if not self.project:return
        rows=self.project.state['papers']; kept=[r for r in rows if r.get('human_decision')=='target']
        for row in kept:
            identity=row['record_id']; count=len(self.project.state['attachments'].get(identity,[]))
            status='未提取' if count else '待获取正文'
            acquisition=self.project.state.get('acquisition',{}).get(identity)
            if not count and acquisition:
                status='需浏览器获取正文'
            if identity in self.project.state['runs']:
                report=__import__('json').loads((self.project.run_path(identity)/'run_report.json').read_text(encoding='utf-8'))
                status=f"候选 {report['candidates']} 条；未提取块 {report['unresolved_blocks']} 个"
            self.material_table.insert('','end',iid=identity,values=(row['title'],count,status))
        confirmed=sum(r.get('human_decision') in {'target','non_target'} for r in rows)
        prefetched=sum(bool(item.get('path')) for item in self.project.state.get('prefetch',{}).values())
        self.summary.set(f'摘要清单 {len(rows)} 篇，人工已确认 {confirmed} 篇，其中保留 {len(kept)} 篇；已预取 {prefetched} 篇待核对。\n目前待审核数值 {sum(r["review_status"]=="pending" for r in self.records)} 条；已通过 {sum(r["review_status"]=="approved" for r in self.records)} 条。')
        self.project_label.set('当前项目：'+str(self.project.folder))
        exported=self.project.state.get('last_export')
        self.export_summary.set('本次导出位置：'+str(self.project.folder/exported) if exported else '新增提取或审核后，请重新导出，以保存当前结果。')

    def help(self):
        messagebox.showinfo('简易操作',
            '1. 在第 1 步在线检索，或导入自己的摘要；需要出版社接口时，先点上方“接口设置”。\n2. 填写核对人，选择论文，看右侧摘要，点“保留：原始实验研究”。\n3. 在第 3 步可点“批量获取待下载正文”，或逐篇获取；需要时用已加载的 Chrome 插件保存采集包 JSON，导入第 1 步，再点“使用浏览器采集全文”。\n4. 在第 4 步选数据，右侧看原文证据和 PDF 页，通过或修正。\n5. 在第 5 步导出。\n\n先用少量已保留论文试跑。合成演示条目不能用于科研；项目决定会自动保存。')

    def browser_help(self):
        messagebox.showinfo('浏览器采集按钮',
            'Chrome 打开 chrome://extensions，对已加载的 NH3-SCR 插件点“重新加载”。第一次使用则开启开发者模式，点“加载已解压的扩展程序”，选择交付包中的 browser_extension 文件夹。\n\n在论文网页展开正文后点插件“读取本页”，再点“下载文件并保存采集包”。等 Chrome 完成下载并保存 JSON，在本工具第 1 步导入。人工保留论文后，第 3 步点“自动获取所选论文正文”会优先使用插件下载的 PDF；点“使用浏览器采集全文”可使用可见网页正文。请核对文件。\n\n采集页面不是双击 popup.html；Zotero 的 CSL JSON / RIS 也可导入摘要。')

    def close(self):
        if self.busy:
            messagebox.showinfo('正在保存处理结果','请等当前提取或导出完成后再关闭，以保留这一轮结果。'); return
        self.paper_preview.close(); self.original.close(); self.root.destroy()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--smoke-test',action='store_true')
    parser.add_argument('--verify',help='Run noninteractive checks in this output folder')
    parser.add_argument('--verify-pdf',help='Optional local real PDF for page-preview acceptance')
    parser.add_argument('--project',help='Open a saved local project')
    args=parser.parse_args()
    if args.verify:
        from scrtool.workbench_check import verify
        verify(args.verify,bundled_sample(),args.verify_pdf,app_factory=Workbench)
        return
    root=tk.Tk()
    if args.smoke_test:root.withdraw()
    app=Workbench(root)
    if args.project:
        project=app.safe(lambda:Project(args.project))
        if project:
            app.project=project; app.refresh(); app.tabs.select(1)
    if args.smoke_test:root.after(600,app.close)
    root.mainloop()


if __name__=='__main__':main()
