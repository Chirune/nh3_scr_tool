"""Shared launcher. All stages run against the same local data directory."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
CODE = ROOT / 'catalyst_workbench'
sys.path.insert(0, str(CODE))
from workbench_paths import data_root, guide_path, translation_root

STAGES = {'1': '板块1 · 文献获取与筛选', '2': '板块2 · 单篇论文证据',
          '3': '板块3 · 编码与模型准备', 'figures': '图表扫描与核验',
          'team': '组员采集工作台'}


def command_for(stage, input_path=None, smoke=False):
    data = data_root()
    if stage == '1':
        command = [CODE/'gateway/app.py', '--output-root', data/'筛选结果',
            '--paper-output-root', data/'论文综合结果', '--image-output-root', data/'图片处理结果']
        if input_path:
            command += ['--pdf-folder' if Path(input_path).is_dir() else '--load-run', input_path]
    elif stage == '2':
        command = [CODE/'paper_app.py', '--output-root', data/'论文综合结果',
                   '--image-output-root', data/'图片处理结果']
        if input_path:
            name = Path(input_path).name
            flag = '--open-project' if name == 'paper.json' else '--open-batch' if name == 'batch.json' else '--screening-run'
            command += [flag, input_path]
    elif stage == '3':
        command = [CODE/'coding_app.py', '--output-root', data/'编码结果']
        if input_path:
            value = json.loads(Path(input_path).read_text(encoding='utf-8-sig'))
            flag = '--session' if value.get('schema_version') == 'coding-workbench-session/1.0' else '--handoff'
            command += [flag, input_path]
    elif stage == 'figures':
        command = [CODE/'app.py', '--output-root', data/'图片处理结果']
        if input_path:
            command += ['--open-batch', input_path]
    elif stage == 'team':
        command = [ROOT/'scripts/workbench_gui.py']
    else:
        raise ValueError('Unknown stage')
    if smoke:
        if stage == 'team':
            raise ValueError('Team-tool smoke checks use its original test suite.')
        command.append('--smoke')
    return [sys.executable, '-B', '-X', 'utf8', *map(str, command)]


def missing_packages(team=False):
    pairs = [('PIL','Pillow'),('numpy','numpy'),('pypdf','pypdf'),('pypdfium2','pypdfium2'),('tkinter','tkinter')]
    if team:
        pairs += [('requests','requests'),('bs4','beautifulsoup4'),('openpyxl','openpyxl'),('pdfplumber','pdfplumber'),('fontTools','fonttools')]
    return [name for module,name in pairs if importlib.util.find_spec(module) is None]


def main(argv=None):
    parser = argparse.ArgumentParser(description='催化文献数据工作台 · 三板块整合版')
    parser.add_argument('--stage',choices=['all',*STAGES],default='all')
    parser.add_argument('--input',help='existing screening run, paper, or handoff JSON')
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--check',action='store_true')
    args = parser.parse_args(argv)
    missing = missing_packages(args.stage=='team')
    if args.check:
        print(json.dumps({'ready':not missing,'missing':missing,'version':'2026.10.09'},ensure_ascii=False))
        return bool(missing)
    if missing:
        print('Missing: '+', '.join(missing)+'\nRun: py scripts/setup_workbench.py --team-tools')
        return 1
    if args.stage != 'all':
        return subprocess.run(command_for(args.stage,args.input,args.smoke)).returncode
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import webbrowser
    root = tk.Tk()
    if args.smoke:
        root.withdraw()
    root.title('催化文献数据工作台 · 最新整合版 2026.10.09')
    root.geometry('1120x780');root.minsize(950,690)
    root.tk.call('tk','scaling',min(float(root.tk.call('tk','scaling')),1.5))
    style = ttk.Style(root)
    if 'clam' in style.theme_names():style.theme_use('clam')
    style.configure('.',font=('Microsoft YaHei UI',11))
    style.configure('TButton',padding=(14,9))
    top=tk.Frame(root,bg='#153e50',padx=26,pady=22);top.pack(fill='x')
    tk.Label(top,text='把论文证据，整理成可核对的模型输入',font=('Microsoft YaHei UI',21,'bold'),fg='white',bg='#153e50').pack(anchor='w')
    tk.Label(top,text='文献筛选 → 文字 · 语义 · 图像 → 统一记录 → 编码与模型准备',fg='#c7e4eb',bg='#153e50',font=('Microsoft YaHei UI',12)).pack(anchor='w',pady=(10,0))
    body=ttk.Frame(root,padding=22);body.pack(fill='both',expand=True)
    status=tk.StringVar(value='从板块1开始。三个板块共用本机的数据目录，审核过的数据可继续使用。')

    def launch(stage,path=None):
        if missing_packages(stage=='team'):
            messagebox.showinfo('需要安装依赖','请运行 py scripts/setup_workbench.py --team-tools',parent=root);return
        logdir=data_root()/'日志';logdir.mkdir(parents=True,exist_ok=True)
        from datetime import datetime
        logfile=logdir/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+stage+'.log')
        with logfile.open('ab') as stream:
            child=subprocess.Popen(command_for(stage,path),cwd=ROOT,stdout=stream,stderr=stream,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        status.set(f'已打开{STAGES[stage]}。如果窗口启动失败，可查看数据目录中的“日志”。')
        def check_start():
            if child.poll() not in (None,0):
                status.set('窗口未启动，请查看：'+str(logfile))
                messagebox.showerror('窗口未启动','错误详情保存在：\n'+str(logfile),parent=root)
        root.after(2000,check_start)

    def choose(stage):
        path=filedialog.askopenfilename(title='选择已保存的 JSON 文件',filetypes=[('工作数据','*.json')],parent=root)
        if path:launch(stage,path)

    for col,(stage,description) in enumerate([
        ('1','检索、DOI、本地 PDF 或学校全文\n人工保留后直接进入第二板块'),
        ('2','数值 / 表格、语义、图片核验\n原句下方提供中文阅读备注'),
        ('3','统一单位与 X/y、独立双评分\n按论文分组准备模型输入')]):
        panel=ttk.LabelFrame(body,text=STAGES[stage],padding=16)
        panel.grid(row=0,column=col,sticky='nsew',padx=(0,12),pady=(0,18))
        ttk.Label(panel,text=description,wraplength=275).pack(anchor='w',pady=(0,14))
        ttk.Button(panel,text='打开'+stage+'号板块',command=lambda s=stage:launch(s)).pack(anchor='w')
        ttk.Button(panel,text='继续已保存的数据',command=lambda s=stage:choose(s)).pack(anchor='w',pady=(8,0))
        body.columnconfigure(col,weight=1)

    def demo():
        try:
            from shared_demo import create_demo
            launch('1',create_demo(data_root()/'合成演示'))
        except Exception as exc:messagebox.showerror('演示未打开',str(exc),parent=root)

    def import_team():
        path=filedialog.askopenfilename(title='选择组员采集工作台的 project.json',filetypes=[('组员项目','project.json')],parent=root)
        if not path:return
        try:
            from team_bridge import import_project
            result=import_project(path,data_root()/'筛选结果')
            messagebox.showinfo('已导入论文与筛选决定',
                '已保留 DOI、人工筛选决定及有效的唯一正文 PDF。\n原工具中的数值审核结果保留在原项目，本次不自动转为新板块的已审核标签。',parent=root)
            launch('1',result)
        except Exception as exc:messagebox.showerror('项目未导入',str(exc),parent=root)

    def install_translation():
        if not messagebox.askyesno('安装本地中文翻译',
            '首次安装需要联网下载公开翻译模型及依赖，保存在本项目中。\n安装完成后，翻译在本机执行，不发送论文原文。\n现在安装？',parent=root):return
        folder=data_root()/'日志';folder.mkdir(parents=True,exist_ok=True)
        log=folder/'安装中文翻译.log'
        with log.open('wb') as stream:
            child=subprocess.Popen([sys.executable,str(ROOT/'scripts/setup_translation.py')],cwd=ROOT,stdout=stream,stderr=stream,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        status.set('正在安装中文翻译，进度见“日志 / 安装中文翻译.log”；完成后重开第二板块。')
        def check_install():
            outcome=child.poll()
            if outcome is None:root.after(1000,check_install)
            elif outcome==0:status.set('中文翻译安装完成。请重开第二板块，选择一条英文语义查看。')
            else:
                status.set('中文翻译安装未完成，请查看安装日志。')
                messagebox.showerror('安装未完成','请查看：\n'+str(log),parent=root)
        root.after(1000,check_install)

    tools=ttk.LabelFrame(body,text='开始试用与团队衔接',padding=15);tools.grid(row=1,column=0,columnspan=3,sticky='ew',pady=(0,18))
    for col,(label,fn) in enumerate([('无论文也能试：合成演示',demo),('导入组员采集项目',import_team),('组员采集与浏览器工具',lambda:launch('team'))]):
        ttk.Button(tools,text=label,command=fn).grid(row=0,column=col,padx=(0,10),sticky='w')
    ttk.Label(tools,text='演示数据均为人工构造，只用于学操作；正式研究请使用自己的论文并逐项核验。',wraplength=970).grid(row=1,column=0,columnspan=3,sticky='w',pady=(13,0))
    lower=ttk.Frame(body);lower.grid(row=2,column=0,columnspan=3,sticky='ew')
    for label,fn in [('查看最新版使用教程',lambda:webbrowser.open(guide_path().as_uri())),
        ('安装中文翻译',install_translation),('打开数据文件夹',lambda:(data_root().mkdir(parents=True,exist_ok=True),os.startfile(str(data_root())))),
        ('单独打开图表核验',lambda:launch('figures'))]:
        ttk.Button(lower,text=label,command=fn).pack(side='left',padx=(0,8))
    ttk.Label(body,text='数据保存位置：'+str(data_root()),wraplength=980).grid(row=3,column=0,columnspan=3,sticky='w',pady=(20,0))
    ttk.Label(body,text='当前提供数据提取、审核和模型输入准备；尚未训练新预测模型。',wraplength=980).grid(row=4,column=0,columnspan=3,sticky='w',pady=(8,0))
    tk.Label(root,textvariable=status,anchor='w',wraplength=1030,padx=22,pady=14,bg='#e9f1f4',fg='#153e50').pack(side='bottom',fill='x')
    if args.smoke:root.update_idletasks();root.update();root.destroy();return 0
    root.mainloop();return 0


if __name__ == '__main__':
    raise SystemExit(main())
