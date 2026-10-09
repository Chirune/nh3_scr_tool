"""Chinese article workbench: text, semantic evidence, reviewed images, handoff."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from workbench_paths import data_root, guide_path
import paper_workspace as work
import paper_numeric_view as numbers
import paper_semantic_report as semantics
import paper_reading as reading

KINDS={**semantics.KIND_LABELS,'table_passage':'表格候选段','text_passage':'原文选段',
       'numeric_fact':'正文科学数值','table_value':'表格数值'}
STATES={'unreviewed':'待核对','reviewed':'已核对','draft':'草稿','excluded':'不采用','stale':'已过期'}
SCOPES={'unknown':'来源角色待确认','current_study':'本研究结果','prior_work':'引用他人工作'}
METHODS={'unknown':'类型待确认','experiment':'实验测量','dft':'DFT / 计算'}
OPS={'eq':'明确单值','range':'区间','gt':'大于','ge':'大于等于','lt':'小于','le':'小于等于','unknown':'含义 / 近似程度待确认'}
FEATURE_CHOICES={'unsupported':'无载体','zeolite':'沸石（下方补型号）','SAPO':'磷酸硅铝分子筛','carbon':'碳材料',
    'fly_ash':'粉煤灰','other_reported':'其他已报告类型','impregnation':'浸渍法','coprecipitation':'共沉淀法',
    'sol_gel':'溶胶凝胶法','hydrothermal':'水热法','solvothermal':'溶剂热法',
    'deposition_precipitation':'沉积沉淀法','ion_exchange':'离子交换法','combustion':'燃烧法',
    'physical_mixing':'物理混合法','top':'顶位','bridge':'桥位','hollow':'空位中心（hollow）',
    'fcc_hollow':'fcc 空位中心','hcp_hollow':'hcp 空位中心','metal_cation':'金属阳离子位',
    'oxygen':'氧位','vacancy':'缺陷 / 空位'}


def text_area(parent,height=8):
    frame=ttk.Frame(parent)
    box=tk.Text(frame,wrap='word',height=height,font=('Microsoft YaHei UI',10),undo=False)
    scroll=ttk.Scrollbar(frame,orient='vertical',command=box.yview)
    box.configure(yscrollcommand=scroll.set)
    box.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
    return frame,box


def show_text(box,text):
    box.configure(state='normal');box.delete('1.0','end');box.insert('1.0',text);box.configure(state='disabled')


def table(parent,columns,widths,height=7,selectmode='browse'):
    frame=ttk.Frame(parent)
    tree=ttk.Treeview(frame,columns=[key for key,_ in columns],show='headings',height=height,selectmode=selectmode)
    for (key,title),width in zip(columns,widths):
        tree.heading(key,text=title);tree.column(key,width=width,minwidth=55,stretch=key in ('quote','series','sample','title'))
    sy=ttk.Scrollbar(frame,orient='vertical',command=tree.yview)
    sx=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview)
    tree.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
    frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
    tree.grid(row=0,column=0,sticky='nsew');sy.grid(row=0,column=1,sticky='ns');sx.grid(row=1,column=0,sticky='ew')
    return frame,tree


class RecordDialog(tk.Toplevel):
    def __init__(self,parent,record,reviewer,on_save,evidence_text='',series_count=0):
        super().__init__(parent)
        self.title('统一记录：一个样品 × 一组条件 × 一个指标')
        self.geometry('880x760');self.minsize(720,600)
        self.record=copy.deepcopy(record);self.on_save=on_save;self.vars={}
        self.reviewer=tk.StringVar(value=reviewer);self.confirm=tk.BooleanVar(value=False)
        bottom=ttk.Frame(self,padding=10);bottom.pack(side='bottom',fill='x')
        ttk.Label(bottom,text='审核人 / 组员代号').grid(row=0,column=0,sticky='w')
        ttk.Entry(bottom,textvariable=self.reviewer,width=15).grid(row=0,column=1,padx=8)
        ttk.Checkbutton(bottom,text='已核对本系列的共同样品与条件' if series_count else '已对照证据核实本条内容',variable=self.confirm).grid(row=0,column=2,padx=8)
        ttk.Button(bottom,text='保存记录',command=self.save).grid(row=0,column=3,padx=8)
        outer=ttk.Frame(self);outer.pack(fill='both',expand=True)
        canvas=tk.Canvas(outer,highlightthickness=0);scroll=ttk.Scrollbar(outer,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        form=ttk.Frame(canvas,padding=14);win=canvas.create_window(0,0,window=form,anchor='nw')
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(win,width=e.width))
        form.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        self.bind('<MouseWheel>',lambda e:canvas.yview_scroll(-int(e.delta/120),'units'))
        hint='原句、图点和来源位置会保留。缺少的信息留空；草稿也可以保存。'
        if series_count:hint=f'正在为 {series_count} 个已核验图点填写共同信息。每一点保留自己的原值、单位和横轴条件。'
        if evidence_text:hint+='\n\n证据：'+evidence_text[:1100]
        ttk.Label(form,text=hint,wraplength=750).grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,12))
        fields=[('task_id','这条记录服务哪个预测任务',work.TASKS),('sample_label','样品名称 / 处理状态',None),
                ('sample_mapping_note','图例与样品对应依据（名称不同时必填）',None),
                ('experiment_id','实验 / 测试编号（如 SCR-01）',None),('composition','材料组成 / 表面结构',None),
                ('preparation','制备 / 处理方法（按原文）',None),('characterization','表征量与测量方法（按原文）',None),
                ('metric','指标名称',None),('operator','数值形式',OPS),('value','数值 / 区间下限',None),
                ('value_high','区间上限（仅区间填写）',None),('unit','单位（% / fraction / eV 等）',None),
                ('assertion_scope','这是谁得到的结果',SCOPES),('measurement_type','实验还是计算',METHODS)]
        self.maps={}
        for row,(key,label,mapping) in enumerate(fields,1):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',pady=4,padx=(0,12))
            value=record.get(key)
            if mapping:
                names={k:(v['title'] if isinstance(v,dict) else v) for k,v in mapping.items()}
                self.maps[key]=names;value=names.get(value,next(iter(names.values())))
            variable=tk.StringVar(value='' if value is None else str(value));self.vars[key]=variable
            if mapping:widget=ttk.Combobox(form,textvariable=variable,values=list(names.values()),state='readonly')
            else:widget=ttk.Entry(form,textvariable=variable)
            if series_count and key in ('value','value_high','unit','operator'):widget.configure(state='disabled')
            widget.grid(row=row,column=1,sticky='ew',pady=4)
        form.columnconfigure(1,weight=1)
        offset=len(fields)+1
        ttk.Label(form,text='条件：只填原文明确给出、且属于本次测试的信息',font=('Microsoft YaHei UI',10,'bold')).grid(row=offset,column=0,columnspan=2,sticky='w',pady=(12,6))
        for row,(key,label) in enumerate(work.CONDITION_LABELS.items(),offset+1):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',pady=4,padx=(0,12))
            value=record.get('conditions',{}).get(key,'')
            variable=tk.StringVar(value='' if value is None else str(value));self.vars['condition:'+key]=variable
            ttk.Entry(form,textvariable=variable).grid(row=row,column=1,sticky='ew',pady=4)
        row=offset+len(work.CONDITION_LABELS)+1
        ttk.Label(form,text='核对说明 / 不一致原因').grid(row=row,column=0,sticky='w',pady=5)
        self.vars['notes']=tk.StringVar(value=record.get('notes',''))
        ttk.Entry(form,textvariable=self.vars['notes']).grid(row=row,column=1,sticky='ew')
        row+=1
        ttk.Label(form,text='缺失原因（未报告 / 未找到 / 暂未核对）').grid(row=row,column=0,sticky='w',pady=5)
        self.vars['missing_reason']=tk.StringVar(value=record.get('missing_reason',''))
        ttk.Entry(form,textvariable=self.vars['missing_reason']).grid(row=row,column=1,sticky='ew')
        from paper_encoding import FEATURE_SCHEMA, CHARACTERIZATION_FIELDS
        row+=1
        ttk.Label(form,text='第三板块的材料字段：按证据填写；不知道的留空',font=('Microsoft YaHei UI',10,'bold')).grid(row=row,column=0,columnspan=2,sticky='w',pady=(16,6))
        row+=1
        ttk.Label(form,text='这些是候选输入字段。表征量请在上方注明测量方法及反应前/后时点；只有预测时已知的信息才能进入相应模型。未知留空，不填实验结论或样品代号。',wraplength=740).grid(row=row,column=0,columnspan=2,sticky='w',pady=(0,8))
        for key,spec in FEATURE_SCHEMA.items():
            row+=1;field='feature:'+key;value=record.get('features',{}).get(key)
            label=spec['label']+(('（'+spec['unit']+'）') if spec.get('unit') else '')
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',pady=4,padx=(0,12))
            choices=spec.get('choices')
            if choices and key!='active_metals':
                mapping={'':'未报告 / 暂不填写',**{choice:FEATURE_CHOICES.get(choice,choice) for choice in choices}}
                self.maps[field]=mapping
                var=tk.StringVar(value=mapping.get(value,mapping['']))
                widget=ttk.Combobox(form,textvariable=var,values=list(mapping.values()),state='readonly')
            else:
                if isinstance(value,list):value=', '.join(value)
                var=tk.StringVar(value='' if value is None else str(value));widget=ttk.Entry(form,textvariable=var)
            self.vars[field]=var;widget.grid(row=row,column=1,sticky='ew',pady=4)
            if key in CHARACTERIZATION_FIELDS:
                row+=1;time_key='availability:'+key
                mapping={'unknown':'尚未核对获得时点', 'before_prediction':'预测该性能之前已知', 'after_prediction':'预测之后 / 反应后才获得'}
                self.maps[time_key]=mapping
                current=record.get('feature_availability',{}).get(key,'unknown')
                variable=tk.StringVar(value=mapping.get(current,mapping['unknown']));self.vars[time_key]=variable
                ttk.Label(form,text='该表征量的获得时点（按证据核对）').grid(row=row,column=0,sticky='w',pady=4)
                ttk.Combobox(form,textvariable=variable,values=list(mapping.values()),state='readonly').grid(row=row,column=1,sticky='ew',pady=4)
        self.transient(parent);self.grab_set()

    def save(self):
        try:
            item=copy.deepcopy(self.record)
            for key,var in self.vars.items():
                value=var.get().strip()
                if key in self.maps:value=next(k for k,v in self.maps[key].items() if v==value)
                if key.startswith('condition:'):
                    item.setdefault('conditions',{})[key.split(':',1)[1]]=value or None
                elif key.startswith('availability:'):
                    item.setdefault('feature_availability',{})[key.split(':',1)[1]]=value
                elif key.startswith('feature:'):
                    name=key.split(':',1)[1]
                    if name=='active_metals':
                        import re
                        value=[part.strip() for part in re.split(r'[,，;；\s]+',value) if part.strip()]
                    item.setdefault('features',{})[name]=value or None
                else:item[key]=value
            self.on_save(item,self.reviewer.get(),self.confirm.get())
            self.destroy()
        except Exception as exc:messagebox.showerror('记录尚未保存',str(exc),parent=self)


class PaperWorkbench:
    def __init__(self,root,output_root=None,image_output_root=None):
        # Layout dimensions are pixels; cap high-DPI text expansion so review
        # controls remain reachable on the supported desktop window sizes.
        root.tk.call('tk','scaling',min(float(root.tk.call('tk','scaling')),1.5))
        self.root=root;self.project=None;self.papers=[];self.busy=False;self.events=queue.Queue();self.controls=[]
        base=data_root()
        self.output_root=Path(output_root or base/'论文综合结果')
        self.image_output_root=Path(image_output_root or base/'图片处理结果')
        root.title('第二板块 · 单篇论文证据工作台')
        root.geometry('1320x900');root.minsize(1020,700)
        style=ttk.Style(root)
        if 'clam' in style.theme_names():style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10));style.configure('TButton',padding=(8,5))
        style.configure('Treeview',rowheight=28);style.configure('Accent.TButton',foreground='white',background='#155e75')
        self.status=tk.StringVar(value='从板块1接收论文，或打开已有图片批次。选一篇后开始整理三类证据。')
        self.paper_var=tk.StringVar();self.task_var=tk.StringVar();self.reviewer=tk.StringVar();self.page_var=tk.StringVar(value='1')
        self.semantic_state=tk.StringVar(value='全部状态');self.semantic_kind=tk.StringVar(value='全部类型')
        self.semantic_query=tk.StringVar();self.semantic_count=tk.StringVar()
        self.reading_service=reading.ReadingService();self.reading_after=None
        self.show_reading=tk.BooleanVar(value=True)
        self.root.bind('<Destroy>',lambda event:self.reading_service.close() if event.widget is self.root else None,add='+')
        self.semantic_hint=tk.StringVar(value='读取全文后，比较、比值、计算关系、定性趋势和未来预期都将列为候选，等待核对与采纳。')
        self.numeric_scope=tk.StringVar(value='科学数值（不含背景要求）');self.numeric_summary=tk.StringVar()
        self.numeric_count=tk.StringVar();self.numeric_rows={}
        header=tk.Frame(root,bg='#123f53',padx=16,pady=12);header.pack(fill='x')
        tk.Label(header,text='一篇论文，三路证据，一份可核对的数据包',bg='#123f53',fg='white',font=('Microsoft YaHei UI',17,'bold')).pack(anchor='w')
        tk.Label(header,text='文字 / 表格 → 语义关系 → 图片核验 → 综合审核 → 交给第三板块编码',bg='#123f53',fg='#d5e9f0').pack(anchor='w',pady=(5,0))
        actions=ttk.Frame(root,padding=(12,8));actions.pack(fill='x')
        self.button(actions,'打开板块1记录',self.choose_run).pack(side='left')
        self.button(actions,'打开已有图片批次',self.choose_batch).pack(side='left',padx=6)
        self.button(actions,'打开论文档案',self.choose_project).pack(side='left')
        self.button(actions,'先看项目目的与框架',self.help).pack(side='right')
        selectors=ttk.Frame(root,padding=(12,0));selectors.pack(fill='x')
        ttk.Label(selectors,text='当前论文').grid(row=0,column=0,sticky='w',padx=(0,8))
        self.paper_combo=ttk.Combobox(selectors,textvariable=self.paper_var,state='readonly')
        self.paper_combo.grid(row=0,column=1,columnspan=3,sticky='ew',pady=4)
        self.paper_combo.bind('<<ComboboxSelected>>',self.select_paper)
        ttk.Label(selectors,text='预测任务').grid(row=1,column=0,sticky='w')
        self.task_combo=ttk.Combobox(selectors,textvariable=self.task_var,values=[v['title'] for v in work.TASKS.values()],state='readonly',width=45)
        self.task_combo.grid(row=1,column=1,sticky='ew',pady=4);self.task_combo.bind('<<ComboboxSelected>>',self.change_task)
        ttk.Label(selectors,text='审核人 / 组员代号').grid(row=1,column=2,padx=(20,6))
        ttk.Entry(selectors,textvariable=self.reviewer,width=14).grid(row=1,column=3,sticky='ew')
        selectors.columnconfigure(1,weight=1)
        self.article_info=tk.StringVar(value='目标决定提取字段；论文不是只有一行数据，同一事实可以同时有文字与图片证据。')
        ttk.Label(root,textvariable=self.article_info,wraplength=1200,padding=(12,7)).pack(fill='x')
        tk.Label(root,textvariable=self.status,bg='#e7eff5',fg='#17394a',wraplength=1180,justify='left',anchor='w',padx=12,pady=8).pack(side='bottom',fill='x')
        self.tabs=ttk.Notebook(root);self.tabs.pack(fill='both',expand=True,padx=12,pady=(0,8))
        self.frames=[ttk.Frame(self.tabs,padding=8) for _ in range(4)]
        for frame,title in zip(self.frames,['① 数值 / 文字 / 表格','② 语义关系','③ 图片核验','④ 综合审核 / 编码交接']):self.tabs.add(frame,text=title)
        self.build_text();self.build_semantic();self.build_images();self.build_records()
        self.root.after(100,self.poll)

    def button(self,parent,label,command,accent=False):
        widget=ttk.Button(parent,text=label,command=command,style='Accent.TButton' if accent else 'TButton')
        self.controls.append(widget);return widget

    def build_text(self):
        frame=self.frames[0];bar=ttk.Frame(frame);bar.pack(fill='x',pady=(0,7))
        self.button(bar,'读取全文并提取数值',self.extract,True).pack(side='left')
        self.button(bar,'打开原 PDF 核对',self.open_pdf).pack(side='left',padx=6)
        self.button(bar,'导出本篇数值清单',self.export_numbers).pack(side='left')
        self.text_tabs=ttk.Notebook(frame);self.text_tabs.pack(fill='both',expand=True)
        overview=ttk.Frame(self.text_tabs,padding=6);raw=ttk.Frame(self.text_tabs,padding=6)
        self.text_tabs.add(overview,text='数值清单（先看这里）');self.text_tabs.add(raw,text='原文核对（全文）')
        summary_label=ttk.Label(overview,textvariable=self.numeric_summary,wraplength=1150,foreground='#155e75',justify='left')
        summary_label.pack(fill='x',pady=(0,7))
        overview.bind('<Configure>',lambda event:summary_label.configure(wraplength=max(300,event.width-20)))
        filters=ttk.Frame(overview);filters.pack(fill='x',pady=(0,6))
        choices=['科学数值（不含背景要求）','当前目标性能','实验条件与材料属性','其他结果 / 测量参数','背景与设计要求','全部（含背景）']
        combo=ttk.Combobox(filters,textvariable=self.numeric_scope,values=choices,state='readonly',width=28)
        combo.pack(side='left');combo.bind('<<ComboboxSelected>>',lambda _e:self.refresh_numbers())
        ttk.Label(filters,textvariable=self.numeric_count).pack(side='left',padx=12)
        actions=ttk.Frame(overview);actions.pack(fill='x',pady=(0,6))
        self.button(actions,'回到原文核对',lambda:self.goto_evidence(self.numeric_tree)).pack(side='left')
        self.button(actions,'建立性能记录',lambda:self.use_evidence(self.numeric_tree)).pack(side='left',padx=6)
        self.button(actions,'补充条件 / 特征',self.use_numeric_context).pack(side='left')
        self.button(actions,'确认所选数值',lambda:self.review(self.numeric_tree,'reviewed')).pack(side='left',padx=6)
        panes=ttk.Panedwindow(overview,orient='vertical');panes.pack(fill='both',expand=True)
        box,self.numeric_tree=table(panes,[('role','用途'),('metric','指标'),('value','数值'),('unit','单位'),('sample','归属对象'),('page','页'),('missing','核对事项')],[125,195,110,85,210,45,320],8)
        panes.add(box,weight=3)
        self.numeric_tree.bind('<<TreeviewSelect>>',lambda _e:self.describe_number())
        box,self.numeric_detail=text_area(panes,5);panes.add(box,weight=2)
        show_text(self.numeric_detail,'选择一行，查看原句、单位、用途和条件缺口。只有核对后的样品—条件—性能记录才进入编码。')
        frame=raw;bar=ttk.Frame(frame);bar.pack(fill='x',pady=(0,7))
        self.button(bar,'把选中的原文留作证据',self.capture).pack(side='left')
        ttk.Label(bar,text='页码').pack(side='left',padx=(12,4))
        self.page_combo=ttk.Combobox(bar,textvariable=self.page_var,width=5,state='readonly');self.page_combo.pack(side='left')
        self.page_combo.bind('<<ComboboxSelected>>',lambda e:self.show_page())
        ttk.Label(frame,text='这里是核对用原文。提取结果请看左侧“数值清单”；复杂表格和扫描页仍可能需要人工补充。',wraplength=1150).pack(anchor='w',pady=(0,6))
        box,self.page_text=text_area(frame,14);box.pack(fill='both',expand=True)
        self.page_text.tag_configure('evidence_focus',background='#ffe28a',foreground='#153545')
        self.location_hint=tk.StringVar(value='从语义分支点击“回到原文页”，可直接定位并高亮原句。')
        ttk.Label(frame,textvariable=self.location_hint,foreground='#155e75').pack(anchor='w',pady=(4,0))
        row=ttk.Frame(frame);row.pack(fill='x',pady=6)
        ttk.Label(row,text='已留下的原文 / 表格证据').pack(side='left')
        self.button(row,'确认所选证据',lambda:self.review(self.text_tree,'reviewed')).pack(side='right')
        self.button(row,'用所选证据建立记录',lambda:self.use_evidence(self.text_tree)).pack(side='right',padx=6)
        self.button(row,'补充到已有记录',lambda:self.attach_evidence(self.text_tree)).pack(side='right')
        box,self.text_tree=table(frame,[('page','页'),('kind','类型'),('state','状态'),('quote','原文')],[50,110,90,760],4)
        box.pack(fill='x')

    def build_semantic(self):
        frame=self.frames[1]
        hint=ttk.Label(frame,textvariable=self.semantic_hint,wraplength=1150,justify='left',foreground='#155e75')
        hint.pack(fill='x',pady=(0,7))
        frame.bind('<Configure>',lambda event:hint.configure(wraplength=max(300,event.width-20)))
        bar=ttk.Frame(frame);bar.pack(fill='x',pady=(0,7))
        self.button(bar,'重新扫描全文语义',lambda:self.extract(focus='semantic'),True).pack(side='left')
        self.button(bar,'标注如何采纳',self.adopt_semantics,True).pack(side='left',padx=6)
        self.button(bar,'导出语义清单',self.export_semantics).pack(side='left')
        self.button(bar,'回到原文页',lambda:self.goto_evidence(self.semantic_tree)).pack(side='left',padx=6)
        second=ttk.Frame(frame);second.pack(fill='x',pady=(0,7))
        self.button(second,'确认句子含义',lambda:self.review(self.semantic_tree,'reviewed')).pack(side='left')
        self.button(second,'不采用',lambda:self.adopt_semantics('exclude')).pack(side='left',padx=6)
        self.button(second,'核对样品与对照',self.annotate_semantics).pack(side='left')
        self.button(second,'用明确数值建立记录',lambda:self.use_evidence(self.semantic_tree)).pack(side='left',padx=6)
        self.button(second,'补充到已有记录',lambda:self.attach_evidence(self.semantic_tree)).pack(side='left')
        filters=ttk.Frame(frame);filters.pack(fill='x',pady=(0,7))
        for variable,values,width in ((self.semantic_state,['全部状态',*STATES.values()],12),
                                      (self.semantic_kind,['全部类型',*KINDS.values(),*['科研关系：'+v for v in semantics.FACET_LABELS.values()]],23)):
            combo=ttk.Combobox(filters,textvariable=variable,values=values,state='readonly',width=width)
            combo.pack(side='left',padx=(0,6));combo.bind('<<ComboboxSelected>>',lambda _e:self.refresh_lists())
        ttk.Label(filters,text='原句 / 指标').pack(side='left')
        entry=ttk.Entry(filters,textvariable=self.semantic_query,width=19);entry.pack(side='left',padx=6)
        entry.bind('<Return>',lambda _e:self.refresh_lists())
        self.button(filters,'筛选',self.refresh_lists).pack(side='left')
        self.button(filters,'下一条待核对',self.next_pending_semantic).pack(side='left',padx=6)
        ttk.Label(filters,textvariable=self.semantic_count).pack(side='right')
        self.semantic_panes=tk.PanedWindow(frame,orient='vertical',sashwidth=8,showhandle=True,bd=0)
        self.semantic_panes.pack(fill='both',expand=True)
        box,self.semantic_tree=table(self.semantic_panes,[('facet','科研关系'),('kind','表述类型'),('metric','指标 / 对象'),('state','状态'),('adoption','如何采纳'),('page','页'),('quote','原句')],[165,140,155,75,165,40,380],7)
        self.semantic_panes.add(box,minsize=85,stretch='always')
        self.semantic_tree.bind('<<TreeviewSelect>>',lambda e:self.describe_evidence(self.semantic_tree,self.semantic_detail))
        detail=ttk.Frame(self.semantic_panes)
        reading_bar=ttk.Frame(detail);reading_bar.pack(fill='x',pady=(3,5))
        ttk.Checkbutton(reading_bar,text='原文下显示中文译文与术语',variable=self.show_reading,
            command=lambda:self.describe_evidence(self.semantic_tree,self.semantic_detail)).pack(side='left')
        self.button(reading_bar,'重试中文翻译',self.retry_translation).pack(side='left',padx=8)
        ttk.Label(reading_bar,text='选中自动翻译 · 本机英译中',foreground='#155e75').pack(side='left')
        box,self.semantic_detail=text_area(detail,10);box.pack(fill='both',expand=True)
        self.semantic_panes.add(detail,minsize=130,stretch='always')
        def fit_panes(event):
            if len(self.semantic_panes.panes())==2 and event.height>190:
                self.semantic_panes.sash_place(0,0,max(85,min(event.height-146,int(event.height*.29))))
        self.semantic_panes.bind('<Configure>',fit_panes)

    def build_images(self):
        frame=self.frames[2]
        ttk.Label(frame,text='沿用现有的找图、裁剪和读数工具。请在读图窗口核对点位、坐标、系列名，确认人工审核并重新导出，再回到这里刷新。',wraplength=1150).pack(anchor='w',pady=(0,8))
        bar=ttk.Frame(frame);bar.pack(fill='x',pady=(0,7))
        self.button(bar,'打开本篇图片审核',self.open_images,True).pack(side='left')
        self.button(bar,'刷新图片读数',self.refresh_images).pack(side='left',padx=6)
        self.button(bar,'用所选图点建立记录',lambda:self.use_evidence(self.image_tree)).pack(side='left')
        self.button(bar,'批量整理同一系列图点',self.use_image_series).pack(side='left',padx=6)
        box,self.image_tree=table(frame,[('page','页'),('series','图号 / 系列'),('metric','指标'),('value','数值'),('unit','单位'),('state','读图核验')],[45,400,180,100,80,145],11,'extended')
        box.pack(fill='both',expand=True);self.image_tree.bind('<<TreeviewSelect>>',lambda e:self.describe_evidence(self.image_tree,self.image_detail))
        box,self.image_detail=text_area(frame,7);box.pack(fill='x',pady=(7,0))

    def build_records(self):
        frame=self.frames[3]
        ttk.Label(frame,text='一条记录对应一个样品、一次测试条件和一个指标。同一事实来自正文和图时合并证据；数值冲突先核对，禁止自动平均。',wraplength=1150).pack(anchor='w',pady=(0,7))
        bar=ttk.Frame(frame);bar.pack(fill='x',pady=(0,7))
        self.button(bar,'编辑所选记录',self.edit_record).pack(side='left')
        self.button(bar,'合并相同记录的证据',self.merge_records).pack(side='left',padx=6)
        self.button(bar,'所选记录不采用',self.exclude_record).pack(side='left')
        bar2=ttk.Frame(frame);bar2.pack(fill='x',pady=(0,7))
        self.button(bar2,'交给第三板块：导出待编码包',self.export,True).pack(side='left')
        self.button(bar2,'进入第三板块：数据编码',self.open_encoding,True).pack(side='left',padx=6)
        self.button(bar2,'打开论文结果文件夹',self.open_folder).pack(side='left',padx=6)
        self.summary=tk.StringVar(value='尚未建立统一记录。先从前三个分支选证据，再补样品与条件。')
        ttk.Label(frame,textvariable=self.summary,wraplength=1150).pack(anchor='w',pady=(0,6))
        box,self.record_tree=table(frame,[('sample','样品 / 状态'),('experiment','实验'),('metric','指标'),('value','值 / 单位'),('review','审核'),('issue','交接状态')],[260,110,180,145,90,250],9,'extended')
        box.pack(fill='both',expand=True);self.record_tree.bind('<<TreeviewSelect>>',lambda e:self.describe_record())
        self.record_tree.bind('<Double-1>',lambda e:self.edit_record())
        box,self.record_detail=text_area(frame,8);box.pack(fill='x',pady=(7,0))

    def guard(self):
        if self.busy:return False
        if not self.project:
            self.status.set('先从顶部选择一篇已有 PDF 的论文。');return False
        return True

    def error(self,exc):
        self.status.set('未完成：'+str(exc));messagebox.showerror('未完成',str(exc),parent=self.root)

    def job(self,fn,done,label):
        if self.busy:return
        self.busy=True;self.status.set(label)
        for widget in self.controls:widget.configure(state='disabled')
        self.paper_combo.configure(state='disabled');self.task_combo.configure(state='disabled')
        def worker():
            try:self.events.put(('done',fn(),done))
            except Exception as exc:self.events.put(('error',exc,None))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        try:
            while True:
                kind,value,done=self.events.get_nowait()
                self.busy=False
                for widget in self.controls:widget.configure(state='normal')
                self.paper_combo.configure(state='readonly');self.task_combo.configure(state='readonly')
                if kind=='error':self.error(value)
                else:
                    try:done(value)
                    except Exception as exc:self.error(exc)
        except queue.Empty:pass
        changed=self.reading_service.drain()
        if self.project and self.semantic_tree.selection():
            evidence=self.selected_evidence(self.semantic_tree)
            if reading.request_key(self.project,evidence) in changed:
                self.describe_evidence(self.semantic_tree,self.semantic_detail,keep_scroll=True)
        self.root.after(100,self.poll)

    def choose_run(self,path=None):
        path=path or filedialog.askopenfilename(parent=self.root,title='选择板块1的 run.json',filetypes=[('筛选记录','*.json')])
        if path:self.load_papers(screening_run=path)

    def choose_batch(self,path=None):
        path=path or filedialog.askopenfilename(parent=self.root,title='选择已有图片批次 batch.json',filetypes=[('图片批次','*.json')])
        if path:self.load_papers(batch_path=path)

    def load_papers(self,**kwargs):
        self.job(lambda:work.list_papers(**kwargs),self.set_papers,'正在接收论文清单与已有 PDF……')

    def set_papers(self,result,open_first=True):
        self.papers,waiting=result
        self.project=None;self.paper_var.set('');self.task_var.set('')
        self.reset_semantic_filters()
        for tree in (self.text_tree,self.semantic_tree,self.image_tree,self.record_tree,self.numeric_tree):tree.delete(*tree.get_children())
        self.numeric_rows={};self.numeric_summary.set('请选择一篇已取得 PDF 的论文。');self.numeric_count.set('')
        self.semantic_hint.set('请选择一篇已取得 PDF 的论文，再扫描全文语义。')
        show_text(self.numeric_detail,'请选择当前清单中的论文。')
        self.show_page()
        for box in (self.semantic_detail,self.image_detail,self.record_detail):show_text(box,'请选择当前清单中的论文。')
        self.article_info.set('请选择一篇已取得 PDF 的论文。')
        labels=[f"{i+1}. {p.get('title') or Path(p['local_pdf']).name}" for i,p in enumerate(self.papers)]
        self.paper_combo.configure(values=labels)
        self.status.set(f'可整理 {len(self.papers)} 篇；另有 {len(waiting)} 篇等待取得 PDF 或筛选核对。')
        if labels:
            self.paper_var.set(labels[0])
            if open_first:self.select_paper()

    def select_paper(self,_event=None):
        if self.busy:return
        index=self.paper_combo.current()
        if index>=0:
            self.job(lambda:work.open_paper(self.papers[index],self.output_root),self.set_project,'正在打开本篇论文档案……')

    def choose_project(self):
        path=filedialog.askopenfilename(parent=self.root,title='选择论文档案 paper.json',filetypes=[('论文档案','*.json')])
        if path:self.job(lambda:work.load_project(path),self.set_project,'正在恢复论文档案……')

    def set_project(self,project):
        if not self.project or self.project.get('project_id')!=project.get('project_id') or self.project.get('article',{}).get('source_sha256')!=project['article']['source_sha256']:
            self.reset_semantic_filters()
            self.numeric_scope.set('科学数值（不含背景要求）');self.text_tabs.select(0)
        self.project=project;article=project['article']
        matching=next((i for i,p in enumerate(self.papers) if p.get('source_sha256')==article['source_sha256']),None)
        if matching is not None:self.paper_combo.current(matching)
        else:self.paper_var.set(article.get('title') or Path(article['local_pdf']).name)
        self.task_var.set(work.TASKS[project['task_id']]['title'])
        self.article_info.set((article.get('title') or Path(article['local_pdf']).name)+'\nDOI：'+(article.get('doi') or '待核对'))
        if project.get('workspace_usage')=='workflow_validation':
            self.article_info.set(self.article_info.get()+' · 流程演示档案；正式研究请从板块1进入。')
        self.page_combo.configure(values=[str(p['page']) for p in project['pages']])
        if project['pages'] and self.page_var.get() not in [str(p['page']) for p in project['pages']]:self.page_var.set('1')
        self.show_page();self.refresh_lists()
        self.status.set('已打开本篇。按上方四个步骤整理；已有人工审核与读图文件保留。')

    def change_task(self,_event=None):
        if not self.guard():return
        try:
            key=next(k for k,v in work.TASKS.items() if v['title']==self.task_var.get())
            work.set_task(self.project,key)
            self.refresh_numbers()
            self.status.set('已设置新建记录的预测目标。已有记录保留各自任务，避免静默改变标签定义。')
        except Exception as exc:self.error(exc)

    def extract(self,focus='numbers'):
        if not self.guard():return
        snapshot=copy.deepcopy(self.project)
        def done(value):
            self.set_project(value)
            self.tabs.select(1 if focus=='semantic' else 0);self.text_tabs.select(0)
            report=numbers.numeric_overview(value)
            self.status.set(f"已扫描全文 {len(value['pages'])} 页，得到 {len(report['rows'])} 项数值和 {sum(e['branch']=='semantic' for e in value['evidence'])} 条语义候选；待核对，不等于训练样本。")
        self.job(lambda:work.extract_document(snapshot),done,'正在扫描全文：提取数值，以及比较、比值、计算关系、定性趋势和展望……')

    def show_page(self):
        page=next((p for p in (self.project or {}).get('pages',[]) if str(p['page'])==self.page_var.get()),None)
        show_text(self.page_text,page['text'] if page else '先点击“读取全文并提取数值”。')
        self.page_text.tag_remove('evidence_focus','1.0','end')
        self.location_hint.set('从语义分支点击“回到原文页”，可直接定位并高亮原句。')

    def reset_semantic_filters(self):
        self.semantic_state.set('全部状态');self.semantic_kind.set('全部类型');self.semantic_query.set('')
        self.semantic_count.set('')

    def semantic_visible(self,evidence):
        if self.semantic_state.get()!='全部状态' and STATES.get(evidence.get('review_status'),'待核对')!=self.semantic_state.get():return False
        selected=self.semantic_kind.get()
        if selected.startswith('科研关系：'):
            if selected.split('：',1)[1] not in semantics.facet_labels(evidence):return False
        elif selected!='全部类型' and KINDS.get(evidence.get('kind'),evidence.get('kind'))!=selected:return False
        query=self.semantic_query.get().strip().casefold()
        return not query or query in ' '.join(str(evidence.get(k,'')) for k in ('quote','metric','sample_label','reference_sample')).casefold()

    def next_pending_semantic(self):
        if not self.guard():return
        ids=list(self.semantic_tree.get_children());selected=self.semantic_tree.selection()
        start=ids.index(selected[0])+1 if selected and selected[0] in ids else 0
        evidence={e['evidence_id']:e for e in self.project['evidence']}
        for eid in ids[start:]+ids[:start]:
            item=evidence[eid]
            if item.get('review_status','unreviewed')=='unreviewed' and not item.get('stale'):
                self.semantic_tree.selection_set(eid);self.semantic_tree.focus(eid);self.semantic_tree.see(eid)
                self.describe_evidence(self.semantic_tree,self.semantic_detail)
                self.status.set('已选中下一条待核对原句。先对照原文，再确认含义；此操作不会自动审核。');return
        self.status.set('当前筛选范围内没有待核对句子。可把状态和类型改为“全部”，继续查看其它证据。')

    def capture(self):
        if not self.guard():return
        try:
            start=int(self.page_text.count('1.0','sel.first','chars')[0]);end=int(self.page_text.count('1.0','sel.last','chars')[0])
            work.add_text_evidence(self.project,int(self.page_var.get()),start,end)
            self.refresh_lists();self.status.set('原文选段已留存，带有页码与准确字符位置。可核对后建立统一记录。')
        except tk.TclError:self.status.set('先用鼠标选中原文中的一段连续文字。')
        except Exception as exc:self.error(exc)

    def refresh_lists(self):
        if not self.project:return
        trees=(self.text_tree,self.semantic_tree,self.image_tree,self.record_tree)
        selections={tree:tree.selection() for tree in trees}
        for tree in trees:tree.delete(*tree.get_children())
        total=pending=0
        for e in self.project['evidence']:
            eid=e['evidence_id'];state=STATES.get(e.get('review_status'),e.get('review_status','待核对'))
            if e['branch']=='text':self.text_tree.insert('', 'end',iid=eid,values=(e.get('page'),KINDS.get(e.get('kind'),e.get('kind')),state,e.get('quote','').replace('\n',' ')[:240]))
            elif e['branch']=='semantic':
                total+=1;pending+=e.get('review_status','unreviewed')=='unreviewed' and not e.get('stale')
                if self.semantic_visible(e):self.semantic_tree.insert('','end',iid=eid,values=(' / '.join(semantics.facet_labels(e)) or '通用关系',KINDS.get(e.get('kind'),e.get('kind')),'指标 / 对象待确认' if e.get('metric') in ('unknown','',None) else e['metric'],state,semantics.adoption_label(e),e.get('page'),e.get('quote','').replace('\n',' ')[:300]))
            elif e['branch']=='image':self.image_tree.insert('','end',iid=eid,values=(e.get('page'),e.get('quote',''),e.get('metric'),e.get('value'),e.get('unit'),'可作核对证据' if e.get('usable') else state+' / 待处理'))
        ready=0
        for r in self.project['records']:
            issues=work.record_issues(self.project,r)
            if not issues:ready+=1
            self.record_tree.insert('','end',iid=r['record_id'],values=(r.get('sample_label') or '样品待确认',r.get('experiment_id'),r.get('metric'),str(r.get('value'))+' '+r.get('unit',''),STATES.get(r['review_status'],r['review_status']),f'待处理 {len(issues)} 项' if issues else '可进入标准化'))
        self.summary.set(f"统一记录 {len(self.project['records'])} 条；可进入第三板块标准化 {ready} 条。待补信息、相对关系和冲突会单列，不混成训练数据。")
        self.semantic_count.set(f'显示 {len(self.semantic_tree.get_children())} / {total}；待核对 {pending}')
        self.semantic_hint.set(semantics.semantic_inventory(self.project)['message'])
        for tree in trees:
            keep=[eid for eid in selections[tree] if tree.exists(eid)]
            if keep:tree.selection_set(keep);tree.see(keep[0])
        if not self.semantic_tree.selection():show_text(self.semantic_detail,'选择一条原句查看解释；“回到原文页”会高亮其准确位置。筛选只改变显示，不删除证据。')
        if not self.project['records']:show_text(self.record_detail,'先从前三个分支选一条证据，点击“建立记录”，补充样品、条件和指标。\n倍数、定性解释与展望可保留在语义分支，导出时进入独立关系表。')
        if not self.image_tree.get_children():show_text(self.image_detail,'\n'.join(self.project.get('image_warnings',[])) or '还没有接收本篇图片数值。打开本篇图片审核，完成核对与导出，再回来刷新。')
        self.refresh_numbers()

    def export_semantics(self):
        if not self.guard():return
        if not self.project.get('pages'):
            self.status.set('请先点击“重新扫描全文语义”，再导出清单。');return
        try:
            folder=semantics.export_semantic_inventory(self.project)
            webbrowser.open((folder/'00_本篇语义清单.html').as_uri())
            self.status.set('语义原句、关系、复核状态及采纳决定已导出：'+str(folder))
        except Exception as exc:self.error(exc)

    def adopt_semantics(self,purpose=None):
        if not self.guard():return
        try:
            evidence=self.selected_evidence(self.semantic_tree)
            if evidence.get('stale'):raise ValueError('这是已被替换的旧证据，请选择当前语义候选。')
            old=evidence.get('semantic_adoption',{})
            dialog=tk.Toplevel(self.root);dialog.title('这句话准备如何使用');dialog.geometry('850x620');dialog.minsize(650,460);dialog.transient(self.root)
            box,original=text_area(dialog,11);box.pack(fill='both',expand=True,padx=14,pady=(14,6))
            self.describe_evidence(self.semantic_tree,original)
            form=ttk.Frame(dialog,padding=14);form.pack(fill='x');form.columnconfigure(1,weight=1)
            choice=tk.StringVar(value=work.SEMANTIC_USES.get(purpose or old.get('purpose'),'待决定如何采纳'))
            reviewer=tk.StringVar(value=self.reviewer.get() or old.get('reviewer',''))
            note=tk.StringVar(value=old.get('note',''));confirm=tk.BooleanVar(value=False)
            for row,label in enumerate(('采纳方式','审核人 / 组员代号','采纳依据 / 尚待核对的问题')):
                ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',padx=(0,10),pady=7)
            ttk.Combobox(form,textvariable=choice,values=list(work.SEMANTIC_USES.values()),state='readonly').grid(row=0,column=1,sticky='ew')
            ttk.Entry(form,textvariable=reviewer).grid(row=1,column=1,sticky='ew')
            ttk.Entry(form,textvariable=note).grid(row=2,column=1,sticky='ew')
            ttk.Checkbutton(form,text='已对照原文核对句子含义（未勾选时仍为待核对）',variable=confirm).grid(row=3,column=0,columnspan=2,sticky='w',pady=9)
            ttk.Label(form,text='这里只保存用途和判断依据。定性、未来预期和相对关系不会因此变成实测数值；不明确的幅度继续留空。',wraplength=780).grid(row=4,column=0,columnspan=2,sticky='w',pady=8)
            def save():
                try:
                    selected=next(k for k,v in work.SEMANTIC_USES.items() if v==choice.get())
                    work.set_semantic_adoption(self.project,evidence['evidence_id'],selected,reviewer.get(),note.get(),confirm.get())
                    self.reviewer.set(reviewer.get());dialog.destroy();self.refresh_lists()
                    self.describe_evidence(self.semantic_tree,self.semantic_detail)
                    self.status.set('采纳决定已保存。它将随语义清单及第三板块交接包一起保留。')
                except Exception as exc:messagebox.showerror('尚未保存',str(exc),parent=dialog)
            ttk.Button(form,text='保存采纳决定',command=save).grid(row=5,column=1,sticky='e',pady=7)
            dialog.grab_set()
        except Exception as exc:self.error(exc)

    def refresh_numbers(self):
        if not self.project:return
        report=numbers.numeric_overview(self.project);selected=self.numeric_tree.selection()
        self.numeric_tree.delete(*self.numeric_tree.get_children());self.numeric_rows={}
        scope=self.numeric_scope.get()
        for item in report['rows']:
            role=item['display_role']
            if scope=='科学数值（不含背景要求）' and role=='background':continue
            if scope=='当前目标性能' and role!='target':continue
            if scope=='实验条件与材料属性' and role not in ('condition','preparation','characterization'):continue
            if scope=='其他结果 / 测量参数' and role not in ('other_result','measurement'):continue
            if scope=='背景与设计要求' and role!='background':continue
            eid=item['evidence_id'];self.numeric_rows[eid]=item
            self.numeric_tree.insert('','end',iid=eid,values=(item['role_label'],item['metric_label'],item['value_label'],item.get('unit',''),item['attribution_label'],item.get('page'),'；'.join(item['missing'])))
        c=report['counts']
        self.numeric_summary.set(report['message'])
        self.numeric_count.set(f"显示 {len(self.numeric_rows)} 项 · 目标性能 {c['target']} · 条件/材料 {c['condition']+c['preparation']+c['characterization']} · 其他 {c['other_result']+c['measurement']}")
        keep=[eid for eid in selected if self.numeric_tree.exists(eid)]
        if keep:self.numeric_tree.selection_set(keep);self.describe_number()
        else:show_text(self.numeric_detail,'选择一行查看原文与数值归属。每项都保留来源；条件和材料表征不会直接当成转化率。')

    def describe_number(self):
        selected=self.numeric_tree.selection()
        if not selected or selected[0] not in self.numeric_rows:return
        item=self.numeric_rows[selected[0]]
        self.describe_evidence(self.numeric_tree,self.numeric_detail)
        detail=self.numeric_detail.get('1.0','end').strip()
        show_text(self.numeric_detail,f"用途：{item['role_label']}｜{item['metric_label']} = {item['value_label']} {item.get('unit','')}\n"
                  +f"归属对象：{item['attribution_label']}\n"+'核对事项：'+'；'.join(item['missing'])+'\n\n'+detail)

    def export_numbers(self):
        if not self.guard():return
        if not self.project.get('pages'):
            self.status.set('请先读取全文，再导出本篇数值清单。');return
        try:
            folder=numbers.export_numeric_inventory(self.project)
            webbrowser.open((folder/'00_本篇数值清单.html').as_uri())
            self.status.set('本篇数值、缺失项和原文证据已导出：'+str(folder)+'。清单未经审核，不是训练表。')
        except Exception as exc:self.error(exc)

    def use_numeric_context(self):
        if not self.guard():return
        try:
            evidence=self.selected_evidence(self.numeric_tree)
            records=[r for r in self.project['records'] if r['review_status']!='excluded']
            if not records:raise ValueError('本篇还没有性能记录。先从性能数值或已核对的图点建立记录；这些条件会保留在清单中。')
            dialog=tk.Toplevel(self.root);dialog.title('将一项条件补到同一实验');dialog.geometry('760x260');dialog.transient(self.root)
            ttk.Label(dialog,text='请选择属于同一样品、同一次实验的记录。下一步会预填对应字段，仍由你核对并保存。\n已有不同值不会被覆盖；不把一篇文章的所有条件套到全部实验。',wraplength=710,padding=16).pack(fill='x')
            choice=ttk.Combobox(dialog,values=[f"{r.get('sample_label') or '样品待确认'} / {r.get('experiment_id') or '实验待确认'} / {r.get('metric')} = {r.get('value')}" for r in records],state='readonly')
            choice.pack(fill='x',padx=16,pady=8);choice.current(0)
            def prepare():
                try:
                    updated=numbers.with_numeric_context(self.project,records[choice.current()],evidence)
                    dialog.destroy()
                    RecordDialog(self.root,updated,self.reviewer.get(),self.save_record,evidence_text=f"补充条件来源：第 {evidence.get('page')} 页\n{evidence.get('quote','')}")
                except Exception as exc:messagebox.showerror('尚未补充',str(exc),parent=dialog)
            ttk.Button(dialog,text='预填字段并打开核对表',command=prepare).pack(pady=10);dialog.grab_set()
        except Exception as exc:self.error(exc)

    def selected_evidence(self,tree):
        selected=tree.selection()
        if not selected:raise ValueError('先选中一条证据。')
        return next(e for e in self.project['evidence'] if e['evidence_id']==selected[0])

    def retry_translation(self):
        if not self.guard():return
        if not self.semantic_tree.selection():
            self.status.set('先选中一条语义原句，再翻译。');return
        evidence=self.selected_evidence(self.semantic_tree)
        aid=reading.reading_aid(self.project,evidence)
        if aid['status'] in ('original_chinese','empty'):
            self.status.set(aid['message']);return
        self.show_reading.set(True)
        self.reading_service.request(self.project,evidence,force=True)
        self.describe_evidence(self.semantic_tree,self.semantic_detail,keep_scroll=True)

    def begin_reading(self,key):
        self.reading_after=None
        if not self.project or not self.semantic_tree.selection() or not self.show_reading.get():return
        evidence=self.selected_evidence(self.semantic_tree)
        if reading.request_key(self.project,evidence)!=key:return
        self.reading_service.request(self.project,evidence)
        self.describe_evidence(self.semantic_tree,self.semantic_detail,keep_scroll=True)

    def describe_evidence(self,tree,box,keep_scroll=False):
        if not self.project or not tree.selection():return
        e=self.selected_evidence(tree)
        scroll=box.yview()[0] if keep_scroll else 0
        info=f"第 {e.get('page','?')} 页 · {KINDS.get(e.get('kind'),e.get('kind','图像读数'))}\n"
        if e['branch']=='semantic' and e.get('semantic_facets'):info+='科研关系：'+' / '.join(semantics.facet_labels(e))+'\n'
        info+=f"\n原始证据：\n{e.get('quote','')}\n\n"
        translation_span=None
        if e['branch']=='semantic' and self.show_reading.get():
            key=reading.request_key(self.project,e)
            aid=reading.reading_aid(self.project,e,self.reading_service.states.get(key))
            note=reading.display_reading(aid)+'\n'
            translation_span=(len(info),len(info)+len(note))
            info+=note
            if box is self.semantic_detail:
                if self.reading_after is not None:self.root.after_cancel(self.reading_after);self.reading_after=None
                if aid['status']=='not_translated':self.reading_after=self.root.after(350,lambda:self.begin_reading(key))
        if e['branch']=='semantic' and e.get('relations'):
            info+='语义摘要：'+'；'.join('，'.join(semantics.relation_lines(r)) for r in e['relations'])+'\n'
        if e['branch']=='semantic':info+='\n'.join(semantics.facet_lines(e))+'\n'
        if e.get('value') is not None:info+=f"候选值：{e['value']} {e.get('unit','')}；形式：{e.get('operator','待确认')}\n"
        if e.get('reference_sample'):info+='比较对象：'+str(e['reference_sample'])+'\n'
        if e.get('sample_label'):info+='对应样品：'+str(e['sample_label'])+'\n'
        if e.get('source_table'):info+='表号：'+str(e['source_table'])+'\n'
        if e.get('table_header_evidence'):
            info+='原表头（用于确认列与单位）：'+str(e['table_header_evidence'].get('evidence_quote',''))+'\n'
        info+='结果归属：'+SCOPES.get(e.get('assertion_scope','unknown'),'待核对')+'\n'
        if e.get('conditions'):
            condition_parts=[]
            for key,value in e['conditions'].items():
                label=work.CONDITION_LABELS.get(key,{'temperature':'温度','pressure':'压力'}.get(key,key))
                if isinstance(value,dict):
                    rendered=str(value.get('value','待核对'))
                    if value.get('value_high') is not None:rendered+='～'+str(value['value_high'])
                    operator={'gt':'>','ge':'≥','lt':'<','le':'≤','approx':'约'}.get(value.get('operator'),'')
                    rendered=operator+rendered+' '+str(value.get('unit',''))
                else:rendered=str(value)
                condition_parts.append(label+'：'+rendered.strip())
            info+='已关联条件（仍须对照原文）：'+'；'.join(condition_parts)+'\n'
        if e.get('uncertainty'):info+='测量误差 / 不确定性：'+json.dumps(e['uncertainty'],ensure_ascii=False)+'\n'
        for related in e.get('context_evidence',[]):
            info+='\n上下文证据（仍需核对关联）：\n'+str(related.get('evidence_quote') or related.get('quote',''))+'\n'
        for relation in e.get('relations',[]):
            info+='\n关系候选：'+'\n'.join(semantics.relation_lines(relation))+'\n'
        if e.get('human_annotation'):info+='\n人工补充依据：'+e['human_annotation'].get('note','')+'\n'
        if e['branch']=='semantic':
            info+='\n需核对：\n'+'\n'.join('• '+p for p in semantics.review_prompts(e))+'\n'
            info+='\n如何采纳：'+semantics.adoption_label(e)+'\n'
            if e.get('semantic_adoption'):info+='采纳依据：'+e['semantic_adoption'].get('note','')+'\n'
        if e.get('x_name'):info+=f"图中横轴：{e['x_name']} = {e.get('x_value')} {e.get('x_unit','')}\n"
        warnings=list(e.get('warnings',[]))
        if e.get('kind') in ('numeric_fact','table_value') and e.get('source_role'):
            _,note=numbers.numeric_attribution(e)
            warnings=[note if warning=='本条未明确绑定唯一催化剂样品；需确认其属于哪个实验或装置。' else warning for warning in warnings]
        info+='\n'.join(warnings)
        if e['branch']=='semantic':
            info+='\n\n下一步：'+('可在核对样品、条件、单位和结果来源后建立记录；不是自动通过的训练标签。' if e.get('kind')=='absolute' and e.get('operator')=='eq' else
                 '保留原句与关系；不直接作为绝对性能标签。需要明确的原文数值及对应条件才能另建记录。')
            info+='\n语义确认只表示这句话的含义已核对；不能把倍数、未来设想或作者归因直接作为绝对实测值。'
        show_text(box,info)
        box.tag_configure('chinese_reading',foreground='#105166',background='#eef8f8')
        if translation_span:
            start,end=translation_span
            box.tag_add('chinese_reading',f'1.0+{start}c',f'1.0+{end}c')
        box.yview_moveto(scroll)

    def annotate_semantics(self):
        if not self.guard():return
        try:
            evidence=self.selected_evidence(self.semantic_tree)
            if evidence.get('stale'):raise ValueError('请使用重读后的当前证据；旧证据用于追溯。')
            dialog=tk.Toplevel(self.root);dialog.title('核对语义归属');dialog.geometry('850x650');dialog.transient(self.root)
            box,original=text_area(dialog,12);box.pack(fill='both',expand=True,padx=14,pady=14)
            self.describe_evidence(self.semantic_tree,original)
            form=ttk.Frame(dialog,padding=14);form.pack(fill='x');form.columnconfigure(1,weight=1)
            values={}
            fields=[('sample_label','对应样品（不确定留空）',evidence.get('sample_label','')),
                    ('reference_sample','对照样品（不确定留空）',evidence.get('reference_sample','')),
                    ('reviewer','审核人 / 组员代号',self.reviewer.get()),('note','修改依据（必须写明原文对应关系）','')]
            for row,(key,label,value) in enumerate(fields):
                ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',padx=(0,10),pady=5)
                values[key]=tk.StringVar(value=value)
                ttk.Entry(form,textvariable=values[key]).grid(row=row,column=1,sticky='ew',pady=5)
            ttk.Label(form,text='是谁报告的结果').grid(row=4,column=0,sticky='w',pady=5)
            scope=tk.StringVar(value=SCOPES.get(evidence.get('assertion_scope'),'来源角色待确认'))
            ttk.Combobox(form,textvariable=scope,values=list(SCOPES.values()),state='readonly').grid(row=4,column=1,sticky='ew')
            ttk.Label(form,text='原始数值、单位和倍数不在此处改写；如自动判断不适用，可回到原文选取准确证据。',wraplength=790).grid(row=5,column=0,columnspan=2,sticky='w',pady=10)
            def save():
                try:
                    changes={k:values[k].get() for k in ('sample_label','reference_sample')}
                    changes['assertion_scope']=next(k for k,v in SCOPES.items() if v==scope.get())
                    work.annotate_semantics(self.project,evidence['evidence_id'],changes,values['reviewer'].get(),values['note'].get())
                    self.reviewer.set(values['reviewer'].get());dialog.destroy();self.refresh_lists()
                    self.status.set('归属补充已保存，原始解析仍可追溯。请重新确认句子含义及关联记录。')
                except Exception as exc:messagebox.showerror('尚未保存',str(exc),parent=dialog)
            ttk.Button(form,text='保存归属补充',command=save).grid(row=6,column=1,sticky='e');dialog.grab_set()
        except Exception as exc:self.error(exc)

    def review(self,tree,decision):
        if not self.guard():return
        try:
            work.review_evidence(self.project,self.selected_evidence(tree)['evidence_id'],decision,self.reviewer.get())
            self.refresh_lists();self.status.set('证据核对状态已保存；统一记录仍需核对样品、实验条件和指标归属。')
        except Exception as exc:self.error(exc)

    def goto_evidence(self,tree):
        try:
            e=self.selected_evidence(tree);self.page_var.set(str(e['page']));self.show_page();self.tabs.select(0);self.text_tabs.select(1)
            page=next((p for p in self.project['pages'] if p['page']==e['page']),None)
            text=page['text'] if page else '';quote=e.get('quote','');ref=e.get('source_ref',{})
            start=e.get('start',ref.get('start'));end=e.get('end',ref.get('end'))
            valid=isinstance(start,int) and isinstance(end,int) and 0<=start<end<=len(text) and text[start:end]==quote
            current=not e.get('stale') and ref.get('source_sha256')==self.project['article']['source_sha256']
            if current and not valid and quote and text.count(quote)==1:
                start=text.index(quote);end=start+len(quote);valid=True
            if not current or not valid:
                self.location_hint.set('已打开原文页，但原句位置无法唯一核实；请对照 PDF，必要时重新读取。')
                self.status.set(self.location_hint.get());return
            # Tcl character indexes may differ from Python for non-BMP symbols.
            first=int(self.root.tk.call('string','length',text[:start]))
            last=int(self.root.tk.call('string','length',text[:end]))
            a,b=f'1.0+{first}c',f'1.0+{last}c'
            self.page_text.tag_add('evidence_focus',a,b);self.page_text.see(a)
            self.location_hint.set(f'已高亮第 {e["page"]} 页原句（字符 {start}–{end}）；上下文仍完整保留。')
            self.status.set('正在查看选中证据的原文位置。看完可回到“数值清单”或“② 语义关系”继续核对。')
        except Exception as exc:self.error(exc)

    def use_evidence(self,tree):
        if not self.guard():return
        try:
            e=self.selected_evidence(tree)
            if e['branch']=='semantic' and e.get('kind') not in ('absolute','range'):
                self.status.set('这是一条关系、定性或展望证据。请在语义分支核对；导出时会单独保留，不建立绝对值标签。');return
            item=work.record_from_evidence(self.project,[e['evidence_id']])
            RecordDialog(self.root,item,self.reviewer.get(),self.save_record,evidence_text=f"第 {e.get('page','?')} 页：{e.get('quote','')}")
        except Exception as exc:self.error(exc)

    def use_image_series(self):
        if not self.guard():return
        try:
            ids=list(self.image_tree.selection())
            entries=[e for e in self.project['evidence'] if e['evidence_id'] in ids]
            if not entries or any(not e.get('usable') or e.get('stale') for e in entries):raise ValueError('按 Ctrl / Shift 选择已经核验并导出的图点；待核对的点暂不批量整理。')
            if len({(e.get('source_ref',{}).get('session_id'),e.get('series_label','')) for e in entries})!=1:raise ValueError('一次只选择同一个读图项目、同一系列的点。')
            template=work.record_from_evidence(self.project,[entries[0]['evidence_id']])
            def save(item,reviewer,confirm):
                created=work.put_image_records(self.project,ids,item,reviewer,confirm)
                self.reviewer.set(reviewer);self.refresh_lists();self.tabs.select(3)
                self.status.set(f'已建立 {len(created)} 条记录，每个图点保留自己的原始数值与条件。请查看综合审核中的待处理提示。')
            RecordDialog(self.root,template,self.reviewer.get(),save,evidence_text=entries[0].get('quote',''),series_count=len(entries))
        except Exception as exc:self.error(exc)

    def save_record(self,item,reviewer,confirm):
        work.put_record(self.project,item,reviewer,confirm)
        if reviewer:self.reviewer.set(reviewer)
        self.refresh_lists();self.tabs.select(3)
        self.status.set('记录已保存。交接检查会列出缺少的信息；保存草稿不会自动变成可训练数据。')

    def attach_evidence(self,tree):
        if not self.guard():return
        try:
            evidence=self.selected_evidence(tree)
            records=[r for r in self.project['records'] if r['review_status']!='excluded']
            if not records:raise ValueError('先用数值证据建立一条记录，再把材料、条件或解释补充到它。')
            if not self.reviewer.get().strip():raise ValueError('请在顶部填写审核人 / 组员代号。')
            dialog=tk.Toplevel(self.root);dialog.title('关联到同一次实验');dialog.geometry('760x240');dialog.transient(self.root)
            ttk.Label(dialog,text='所选证据只作为材料、条件或解释的补充，不当作新的性能数值。\n请确认它确实属于这条记录的样品和实验。',wraplength=710,padding=16).pack(fill='x')
            labels=[f"{i+1}. {r.get('sample_label') or '样品待确认'} / {r.get('experiment_id') or '实验待确认'} / {r.get('metric')} = {r.get('value')} {r.get('unit','')}" for i,r in enumerate(records)]
            choice=ttk.Combobox(dialog,values=labels,state='readonly');choice.pack(fill='x',padx=16,pady=8);choice.current(0)
            def save():
                try:
                    work.attach_context(self.project,records[choice.current()]['record_id'],evidence['evidence_id'],self.reviewer.get())
                    dialog.destroy();self.refresh_lists();self.tabs.select(3)
                    self.status.set('补充证据已关联，记录回到草稿；请核对新增的条件或解释。')
                except Exception as exc:messagebox.showerror('尚未关联',str(exc),parent=dialog)
            ttk.Button(dialog,text='确认关联到这条记录',command=save).pack(pady=10);dialog.grab_set()
        except Exception as exc:self.error(exc)

    def describe_record(self):
        if not self.project or not self.record_tree.selection():return
        r=next(r for r in self.project['records'] if r['record_id']==self.record_tree.selection()[0])
        issues=work.record_issues(self.project,r)
        lines=[work.TASKS[r['task_id']]['title'],f"样品：{r.get('sample_label','')}；实验：{r.get('experiment_id','')}",
            f"指标：{r.get('metric','')}；{OPS.get(r.get('operator'),'待确认')}：{r.get('value')} {r.get('unit','')}",
            '条件：'+ '；'.join(work.CONDITION_LABELS.get(k,k)+' = '+str(v) for k,v in r.get('conditions',{}).items() if v is not None and str(v)),
            '\n待处理：'+'；'.join(issues) if issues else '\n已满足本版标准化交接条件。第三板块仍需核对单位、编码字段与数据集划分。','\n关联证据：']
        for eid in r['evidence_ids']:
            e=next(e for e in self.project['evidence'] if e['evidence_id']==eid)
            role='补充条件 / 解释' if r.get('evidence_roles',{}).get(eid)=='context' else '数值来源'
            lines.append(f"第 {e.get('page','?')} 页｜{role}｜{e.get('quote','')}")
        show_text(self.record_detail,'\n'.join(lines))

    def edit_record(self):
        if not self.guard() or not self.record_tree.selection():return
        r=next(r for r in self.project['records'] if r['record_id']==self.record_tree.selection()[0])
        evidence_text='\n'.join(f"第 {e.get('page','?')} 页：{e.get('quote','')}" for e in self.project['evidence'] if e['evidence_id'] in r['evidence_ids'])
        RecordDialog(self.root,r,self.reviewer.get(),self.save_record,evidence_text=evidence_text)

    def merge_records(self):
        if not self.guard():return
        try:
            work.merge_equal_records(self.project,list(self.record_tree.selection()),self.reviewer.get());self.refresh_lists()
            self.status.set('已合并证据，统一记录回到草稿状态；请再次核对。原记录与合并事件均保留。')
        except Exception as exc:self.error(exc)

    def exclude_record(self):
        if not self.guard() or not self.record_tree.selection():return
        try:
            for rid in self.record_tree.selection():work.exclude_record(self.project,rid,self.reviewer.get())
            self.refresh_lists()
        except Exception as exc:self.error(exc)

    def open_pdf(self):
        if self.guard():os.startfile(self.project['article']['local_pdf'])

    def open_images(self):
        if not self.guard():return
        project=copy.deepcopy(self.project)
        def done(batch):
            self.set_project(project)
            runtime=Path(sys.executable).with_name('pythonw.exe')
            if not runtime.is_file():runtime=Path(sys.executable)
            command=[str(runtime),'-B',str(Path(__file__).with_name('app.py')),'--open-batch',str(Path(batch['run_dir'])/'batch.json'),
                     '--paper-id',project['article']['paper_id']]
            subprocess.Popen(command,cwd=Path(__file__).parent,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self.status.set('已打开本篇的图片审核。核验并导出后，回到这里点击“刷新图片读数”。')
        self.job(lambda:work.ensure_image_batch(project,self.image_output_root),done,'正在关联本篇已有图片审核；没有旧批次时自动扫描本篇 PDF……')

    def refresh_images(self):
        if not self.guard():return
        project=copy.deepcopy(self.project)
        def done(value):
            self.set_project(value)
            good=sum(e['branch']=='image' and e.get('usable',False) for e in value['evidence'])
            self.status.set(f'本篇已接收图像证据，其中 {good} 个点已通过读图审核及来源检查。其他点仍显示待处理状态。')
        self.job(lambda:work.refresh_images(project),done,'正在检查图片读数的版本、来源及人工审核状态……')

    def export(self):
        if not self.guard():return
        project=copy.deepcopy(self.project)
        def done(result):
            self.set_project(project)
            note=f"可进入标准化：{result['ready_count']} 条\n待补齐 / 冲突：{result['waiting_count']} 条\n语义关系：{result['relation_count']} 条\n\n这是第三板块的输入包，尚未编码或训练模型。\n\n保存位置：\n{result['directory']}"
            if result['ready_count']==0:note='当前没有满足标准化条件的数值记录；原文关系和待办仍已保存。\n\n'+note
            self.status.set(note.replace('\n',' '));messagebox.showinfo('论文数据包已保存',note,parent=self.root)
        self.job(lambda:work.export_handoff(project),done,'正在核对三路证据、重复记录和缺失条件，生成待编码包……')

    def open_encoding(self):
        if not self.guard():return
        project=copy.deepcopy(self.project)
        def done(result):
            self.set_project(project)
            runtime=Path(sys.executable).with_name('pythonw.exe')
            if not runtime.is_file():runtime=Path(sys.executable)
            packet=Path(result['directory'])/'待编码包.json'
            command=[str(runtime),'-B',str(Path(__file__).with_name('coding_app.py')),'--handoff',str(packet),
                     '--task-id',project['task_id'],'--output-root',str(self.output_root.parent/'编码结果')]
            subprocess.Popen(command,cwd=Path(__file__).parent,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self.status.set('已导出最新证据包并打开第三板块。可继续添加其他论文的数据包，再生成标准化与编码结果。')
        self.job(lambda:work.export_handoff(project),done,'正在保存并核对当前论文，准备进入数据编码……')

    def open_folder(self):
        if self.guard():os.startfile(self.project['run_dir'])

    def help(self):
        path=guide_path()
        if not path.is_file():path=Path(__file__).resolve().parent.parent/'23_语义图表与数据编码_升级说明.html'
        if not path.is_file():path=path.with_name('20_第二板块_文字语义图片与编码.html')
        if path.is_file():webbrowser.open(path.as_uri())
        else:messagebox.showinfo('项目要回答的问题','先确定要预测什么：催化剂组成、制备和工况 → 性能。\n正文、图像、语义围绕同一条实验记录汇总，多份证据不算多次实验。\n第三板块先统一定义、单位、缺失与类别，之后才训练模型。',parent=self.root)


def main(argv=None):
    parser=argparse.ArgumentParser(description='单篇论文的文字、语义、图像证据工作台')
    parser.add_argument('--screening-run');parser.add_argument('--open-batch');parser.add_argument('--open-project')
    parser.add_argument('--start-tab',choices=('numbers','semantic','images','records'),default='numbers')
    parser.add_argument('--output-root');parser.add_argument('--image-output-root');parser.add_argument('--smoke',action='store_true')
    args=parser.parse_args(argv)
    root=tk.Tk()
    if args.smoke:root.withdraw()
    app=PaperWorkbench(root,args.output_root,args.image_output_root)
    app.tabs.select(('numbers','semantic','images','records').index(args.start_tab))
    if not args.smoke:
        if args.screening_run:root.after(100,lambda:app.load_papers(screening_run=args.screening_run))
        elif args.open_batch:root.after(100,lambda:app.load_papers(batch_path=args.open_batch))
        elif args.open_project:root.after(100,lambda:app.job(lambda:work.load_project(args.open_project),app.set_project,'正在打开论文档案……'))
    if args.smoke:
        if args.open_project:app.set_project(work.load_project(args.open_project))
        root.update_idletasks();root.update();root.destroy();return 0
    root.mainloop();return 0


if __name__=='__main__':raise SystemExit(main())
