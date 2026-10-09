"""Local stage-three workbench: inspect explicit fields, encode, retain sources."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from workbench_paths import data_root
import paper_encoding as encoding
import paper_workspace as work
from scoring_ui import ScoringPanel
from ml_preparation_ui import ModelPreparationPanel
from paper_app import table, text_area, show_text

SPLIT_LABELS={'preview':'仅预览','train':'训练部分','test':'预留测试部分'}
ROLES={'X':'模型输入','y':'预测目标','audit_only':'仅作追溯'}


class CodingWorkbench:
    def __init__(self,root,output_root=None):
        # The layout uses pixel dimensions; high-DPI Tk point scaling otherwise
        # makes controls consume the whole fixed-size workbench on Windows.
        root.tk.call('tk', 'scaling', min(float(root.tk.call('tk', 'scaling')), 1.5))
        self.root=root;self.paths=[];self.result=None;self.exported=None;self.busy=False
        self.events=queue.Queue();self.buttons=[]
        self.output_root=Path(output_root or data_root()/'编码结果')
        root.title('第三板块 · 数据编码与双评分');root.geometry('1320x900');root.minsize(1000,680)
        style=ttk.Style(root)
        if 'clam' in style.theme_names():style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10));style.configure('TButton',padding=(8,5))
        style.configure('Treeview',rowheight=27)
        header=tk.Frame(root,bg='#164955',padx=16,pady=12);header.pack(fill='x')
        tk.Label(header,text='把核验后的事实，整理成 X 与 y',font=('Microsoft YaHei UI',18,'bold'),bg='#164955',fg='white').pack(anchor='w')
        tk.Label(header,text='X：材料和条件　y：本次任务的性能　来源：每一行都能回到论文与证据',bg='#164955',fg='#d9eded').pack(anchor='w',pady=(5,0))
        self.status=tk.StringVar(value='从第二板块进入，或添加一篇／多篇论文的待编码包。');self.task=tk.StringVar()
        controls=ttk.Frame(root,padding=12);controls.pack(fill='x')
        self.button(controls,'添加论文待编码包',self.choose_files).pack(side='left')
        self.button(controls,'移除所选',self.remove_file).pack(side='left',padx=6)
        ttk.Label(controls,text='预测任务').pack(side='left',padx=(10,6))
        self.task_combo=ttk.Combobox(controls,textvariable=self.task,values=[v['title'] for k,v in work.TASKS.items() if k!='pending'],state='readonly',width=51)
        self.task_combo.pack(side='left',fill='x',expand=True);self.task_combo.bind('<<ComboboxSelected>>',lambda e:self.invalidate())
        box,self.files=table(root,[('title','论文'),('ready','已核验记录'),('waiting','待核验记录'),('file','数据包')],[450,110,110,500],3)
        box.pack(fill='x',padx=12)
        actions=ttk.Frame(root,padding=(12,9));actions.pack(fill='x')
        self.button(actions,'检查并生成编码预览',self.build).pack(side='left')
        self.button(actions,'导出 X、y 与来源',self.export).pack(side='left',padx=6)
        self.button(actions,'保存检查报告',self.save_review).pack(side='left')
        self.button(actions,'打开编码结果文件夹',self.open_folder).pack(side='left')
        self.button(actions,'回到所选论文核对',self.open_paper).pack(side='right')
        self.summary=tk.StringVar(value='先选择一个任务。不同性能、实验与 DFT 分开编码。')
        ttk.Label(root,textvariable=self.summary,padding=(12,4),wraplength=1200).pack(fill='x')
        ttk.Label(root,text='这里先核对事实与编码。X预览不直接代替交叉验证；第⑥页按训练折准备输入。未知或预测后表征不会直接进入X。',padding=(12,3)).pack(fill='x')
        tk.Label(root,textvariable=self.status,bg='#e9f1f4',fg='#234251',wraplength=1200,justify='left',anchor='w',padx=12,pady=8).pack(side='bottom',fill='x')
        self.tabs=ttk.Notebook(root);self.tabs.pack(fill='both',expand=True,padx=12,pady=8)
        self.frames=[ttk.Frame(self.tabs,padding=8) for _ in range(6)]
        for frame,label in zip(self.frames,['① 标准化记录','② 编码检查预览 X / y','③ 未纳入与缺失','④ 字段说明与划分','⑤ 质量分 / 性能分','⑥ 机器学习准备']):self.tabs.add(frame,text=label)
        box,self.observations=table(self.frames[0],[('sample','样品 / 实验'),('raw','原值'),('target','统一单位后的 y'),('doi','论文 DOI'),('split','用途'),('approx','图像近似')],[240,130,160,320,130,90],8)
        box.pack(fill='both',expand=True);self.observations.bind('<<TreeviewSelect>>',lambda e:self.describe_observation())
        box,self.observation_detail=text_area(self.frames[0],7);box.pack(fill='x',pady=(7,0))
        box,self.matrix=table(self.frames[1],[('empty','编码后在这里查看输入列与目标列')],[1100],15)
        box.pack(fill='both',expand=True)
        box,self.excluded=table(self.frames[2],[('sample','样品'),('reason','未纳入原因')],[200,920],8)
        box.pack(fill='both',expand=True);self.excluded.bind('<<TreeviewSelect>>',lambda e:self.describe_excluded())
        box,self.excluded_detail=text_area(self.frames[2],8);box.pack(fill='x',pady=(7,0))
        box,self.dictionary=table(self.frames[3],[('label','字段含义'),('role','用途'),('unit','单位'),('encoding','处理方式')],[370,140,130,460],9)
        box.pack(fill='both',expand=True)
        box,self.audit_detail=text_area(self.frames[3],7);box.pack(fill='x',pady=(7,0))
        self.scoring=ScoringPanel(self,self.frames[4])
        self.model_preparation=ModelPreparationPanel(self,self.frames[5])
        self.root.after(100,self.poll)

    def button(self,parent,label,command):
        widget=ttk.Button(parent,text=label,command=command);self.buttons.append(widget);return widget

    def invalidate(self):
        self.result=None;self.exported=None
        for tree in (self.observations,self.excluded,self.dictionary,self.matrix):tree.delete(*tree.get_children())
        for box in (self.observation_detail,self.excluded_detail,self.audit_detail):show_text(box,'数据包或任务已改变，请重新生成预览。')
        self.summary.set('尚未生成当前任务的编码预览。')
        self.scoring.clear()
        self.model_preparation.clear()

    def add_paths(self,paths):
        for candidate in paths:
            path=str(Path(candidate).resolve())
            if path in self.paths:continue
            try:
                data=encoding.load_handoff(path)
                self.paths.append(path)
                self.files.insert('','end',iid=path,values=(data['article'].get('title') or data['article'].get('doi','论文'),len(data['records_ready_for_standardization']),len(data['records_waiting_for_review']),Path(path).parent.name))
                if not self.task.get():
                    tasks=[r.get('task_id') for r in data['records_ready_for_standardization']+data['records_waiting_for_review'] if r.get('task_id') in work.TASKS and r.get('task_id')!='pending']
                    if tasks:self.task.set(work.TASKS[tasks[0]]['title'])
            except Exception as exc:messagebox.showerror('没有添加这个数据包',str(exc),parent=self.root)
        self.invalidate();self.status.set(f'已添加 {len(self.paths)} 个论文数据包；点击“检查并生成编码预览”。')

    def choose_files(self):
        if self.busy:return
        paths=filedialog.askopenfilenames(parent=self.root,title='选择各论文的待编码包.json',filetypes=[('待编码包','*.json')])
        if paths:self.add_paths(paths)

    def remove_file(self):
        if self.busy:return
        for path in self.files.selection():self.paths.remove(path);self.files.delete(path)
        self.invalidate()

    def job(self,fn,done,message):
        if self.busy:return
        self.busy=True;self.status.set(message)
        for button in self.buttons:button.configure(state='disabled')
        self.task_combo.configure(state='disabled')
        self.scoring.set_busy(True)
        self.model_preparation.set_busy(True)
        def worker():
            try:self.events.put(('done',fn(),done))
            except Exception as exc:self.events.put(('error',exc,None))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        try:
            while True:
                kind,value,done=self.events.get_nowait();self.busy=False
                for button in self.buttons:button.configure(state='normal')
                self.task_combo.configure(state='readonly')
                self.scoring.set_busy(False)
                self.model_preparation.set_busy(False)
                if kind=='error':self.status.set('本次未完成：'+str(value));messagebox.showerror('编码未完成',str(value),parent=self.root)
                else:
                    try:done(value)
                    except Exception as exc:self.status.set(str(exc));messagebox.showerror('显示结果未完成',str(exc),parent=self.root)
        except queue.Empty:pass
        self.root.after(100,self.poll)

    def build(self):
        if self.busy:return
        task=next((k for k,v in work.TASKS.items() if v['title']==self.task.get() and k!='pending'),None)
        if not task or not self.paths:
            self.status.set('请先添加论文待编码包，并选择一个明确预测任务。');return
        paths=list(self.paths)
        self.job(lambda:encoding.build_encoding(paths,task),self.show_result,'正在复核来源、统一单位、检查重复，并按论文组生成编码……')

    def show_result(self,result):
        self.result=result;self.exported=None;s=result['summary']
        for tree in (self.observations,self.excluded,self.dictionary,self.matrix):tree.delete(*tree.get_children())
        for i,row in enumerate(result['observations']):
            self.observations.insert('','end',iid=str(i),values=(row['sample_label']+' / '+row['experiment_id'],str(row['raw_value'])+' '+row['raw_unit'],str(row['y'])+' '+row['y_unit'],row['doi'],SPLIT_LABELS.get(row['split'],row['split']),'是' if row['approximate'] else '否'))
        for i,row in enumerate(result['excluded']):
            self.excluded.insert('','end',iid=str(i),values=(row.get('sample_label') or '整个数据包','；'.join(row['reasons'])))
        transformations={'numeric_preserved':'保留数值＋缺失标记','multi_hot':'多项类别分别标记','one_hot':'类别分别标记','excluded_from_X':'不作模型输入','task_unit_conversion':'统一任务单位'}
        for row in result['field_dictionary']:
            self.dictionary.insert('','end',values=(row.get('label') or row['field'],ROLES.get(row.get('role'),row.get('role')),row.get('unit',''),transformations.get(row.get('encoding'),row.get('encoding',''))))
        columns=['row','y',*result['encoder']['columns']]
        self.matrix.configure(columns=columns)
        for column in columns:
            self.matrix.heading(column,text='行序' if column=='row' else '目标 y' if column=='y' else column)
            self.matrix.column(column,width=110 if column in ('row','y') else 180,minwidth=70,stretch=False)
        for i,row in enumerate(result['encoded_rows']):
            self.matrix.insert('','end',values=[i+1,row['y'],*['缺失' if row['X'][c] is None else row['X'][c] for c in result['encoder']['columns']]])
        self.summary.set(f"标准化记录 {s['observation_count']} 条 / {s['paper_count']} 篇论文；未纳入 {s['excluded_count']} 条；输入列 {s['column_count']} 列；训练部分 {s['train_count']} 条，预留测试 {s['test_count']} 条，仅预览 {s['preview_count']} 条。")
        show_text(self.audit_detail,'\n\n'.join(result['warnings']))
        self.scoring.refresh()
        self.model_preparation.refresh()
        missing='\n'.join((encoding.FEATURE_SCHEMA.get(k) or encoding.CONDITION_SCHEMA.get(k) or {}).get('label',k)+f'：缺失 {v} 条' for k,v in s['missing_counts'].items() if v)
        show_text(self.excluded_detail,'字段缺失统计：\n'+(missing or '当前无已标准化记录中的缺失统计。')+'\n\n点击上方一条可查看未纳入的具体原因。')
        self.status.set(s['reason'])
        if not result['observations']:
            self.tabs.select(2);self.status.set('当前没有可编码的核验记录。请在“未纳入与缺失”查看原因，回到第二板块补齐；未生成空白 X/y 文件。')
        elif self.observations.get_children():
            self.observations.selection_set('0');self.describe_observation()

    def describe_observation(self):
        if not self.result or not self.observations.selection():return
        row=self.result['observations'][int(self.observations.selection()[0])]
        specs={**encoding.FEATURE_SCHEMA,**encoding.CONDITION_SCHEMA}
        lines=[f"样品：{row['sample_label']}　实验：{row['experiment_id']}",
            f"原值 {row['raw_value']} {row['raw_unit']} → {row['y']} {row['y_unit']}",row.get('approximation_note','')]
        mapping=row.get('raw_record',{}).get('sample_mapping_note')
        if mapping:lines.append('样品对应依据：'+mapping)
        source_names=list(dict.fromkeys(str(e.get('sample_label') or e.get('series_label'))
            for e in row['evidence'] if e.get('sample_label') or e.get('series_label')))
        if source_names:lines.append('原句 / 图例中的样品：'+'；'.join(source_names))
        _, unavailable=encoding.prediction_available_facts(row)
        masked={item['field'] for item in unavailable}
        lines.append('核验后的事实字段（获得时点未知/预测后的表征保留原值，X预览中暂作缺失）：')
        for name,value in row['X_raw'].items():lines.append(specs[name]['label']+'：'+('未报告 / 待补充' if value is None else str(value))+
            ('　【暂不进入X：预测前可用性待核对】' if name in masked else ''))
        lines.append('\n对应证据：')
        for evidence in row['evidence']:lines.append(f"第 {evidence.get('page','?')} 页：{evidence.get('quote','')}")
        show_text(self.observation_detail,'\n'.join(lines))

    def describe_excluded(self):
        if not self.result or not self.excluded.selection():return
        row=self.result['excluded'][int(self.excluded.selection()[0])]
        show_text(self.excluded_detail,'\n'.join(row['reasons'])+'\n\n所属数据包：\n'+row.get('packet_path',''))

    def export(self):
        if self.busy:return
        if not self.result or not self.result['observations']:
            self.status.set('先生成包含已核验记录的编码预览；没有合格记录时会显示原因。');return
        def done(report):
            self.exported=report;self.status.set('编码文件已保存：'+report['directory'])
            messagebox.showinfo('编码文件已保存',f"已编码 {report['observation_count']} 条记录。\n先打开“00_先看这里_编码结果.html”，或查看“人工核对_逐行摘要.csv”。\nX/y、双评分、完整事实与模型编码方案均已保存。尚未训练或评价模型。\n\n{report['directory']}",parent=self.root)
        import copy
        config=copy.deepcopy(self.scoring.config);annotations=copy.deepcopy(self.scoring.annotations)
        self.job(lambda:encoding.export_encoding(self.result,self.output_root,scoring_config=config,scoring_annotations=annotations),done,'正在复核来源，并导出编码、两类分数与逐项依据……')

    def open_folder(self):
        if self.exported:os.startfile(self.exported['directory'])
        else:self.status.set('本次尚未导出编码文件。')

    def save_review(self):
        if self.busy:return
        if not self.result:self.status.set('先点击“检查并生成编码预览”。');return
        try:
            import uuid
            folder=self.output_root/('编码检查_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:4])
            folder.mkdir(parents=True,exist_ok=False)
            report={key:self.result[key] for key in ('task_id','task_title','input_packets','summary','excluded','audit','warnings')}
            if self.scoring.report:
                report['decision_scores']=self.scoring.report
            (folder/'编码检查.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
            work._csv(folder/'未纳入原因.csv',self.result['excluded'],['sample_label','task_id','record_id','reasons','packet_path'])
            self.exported={'directory':str(folder)};self.status.set('检查报告已保存：'+str(folder)+'；没有把未通过记录写成 X/y。')
        except Exception as exc:messagebox.showerror('检查报告未保存',str(exc),parent=self.root)

    def open_paper(self):
        if self.busy:return
        path=next(iter(self.files.selection()),None)
        if not path and self.paths:path=self.paths[0]
        if not path:return
        try:
            packet=encoding.load_handoff(path);project=(packet.get('source_workspace') or {}).get('path')
            if not project or not Path(project).is_file():raise ValueError('旧数据包缺少论文档案入口，请在第二板块重新导出。')
            runtime=Path(sys.executable).with_name('pythonw.exe')
            if not runtime.is_file():runtime=Path(sys.executable)
            subprocess.Popen([str(runtime),'-B',str(Path(__file__).with_name('paper_app.py')),'--open-project',project],cwd=Path(__file__).parent,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except Exception as exc:messagebox.showerror('暂时无法返回论文',str(exc),parent=self.root)


def load_session(path):
    data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or set(data)!={'schema_version','handoffs','task_id','score_settings','show_scores'} or data['schema_version']!='coding-workbench-session/1.0':
        raise ValueError('不是第三板块的已保存演示入口。')
    if (not isinstance(data['handoffs'],list) or not data['handoffs'] or
        any(not isinstance(p,str) or not Path(p).is_file() for p in data['handoffs']) or
        data['task_id'] not in work.TASKS or data['task_id']=='pending' or
        not isinstance(data['score_settings'],str) or not Path(data['score_settings']).is_file() or
        not isinstance(data['show_scores'],bool)):
        raise ValueError('演示入口中的任务或本地文件已不存在。')
    return data


def main(argv=None):
    parser=argparse.ArgumentParser(description='第三板块：标准化与数据编码')
    parser.add_argument('--handoff',action='append',default=[]);parser.add_argument('--task-id');parser.add_argument('--output-root');parser.add_argument('--smoke',action='store_true');parser.add_argument('--session')
    parser.add_argument('--model-preparation',action='store_true',help='打开机器学习准备页；不训练模型')
    args=parser.parse_args(argv)
    session=load_session(args.session) if args.session else None
    if session:args.handoff=session['handoffs'];args.task_id=session['task_id']
    root=tk.Tk()
    if args.smoke:root.withdraw()
    app=CodingWorkbench(root,args.output_root)
    if args.task_id in work.TASKS and args.task_id!='pending':app.task.set(work.TASKS[args.task_id]['title'])
    if args.smoke:root.update_idletasks();root.destroy();return 0
    def receive():
        if session:app.scoring.read_settings(session['score_settings'])
        app.add_paths(args.handoff)
        if session and session['show_scores']:app.tabs.select(4)
        if args.model_preparation:app.tabs.select(5)
        if app.task.get():app.build()
    if args.handoff:root.after(100,receive)
    root.mainloop();return 0


if __name__=='__main__':raise SystemExit(main())
