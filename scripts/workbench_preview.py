"""Evidence text and source-page preview, without browser or Python installation."""
import queue
import re
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageTk
from scrtool.core import uid


def render_page(source_file, locator, cache):
    match = re.search(r'page:(\d+)', locator)
    path = Path(source_file)
    if path.suffix.lower() != '.pdf' or not match:
        return None
    import pdfplumber
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / (uid(str(path), path.stat().st_mtime_ns, match.group(1)) + '.png')
    if not dest.exists():
        with pdfplumber.open(path) as pdf:
            image = pdf.pages[int(match.group(1))-1].to_image(resolution=130).original
            image.save(dest)
    return dest


class SourcePreview:
    def __init__(self, parent, open_source):
        self.frame = ttk.Frame(parent)
        self.events = queue.Queue()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.token = 0
        self.image_path = None
        self.source_file = None
        self.location = tk.StringVar(value='选择一篇论文或一条记录查看原文')
        bar = ttk.Frame(self.frame)
        bar.pack(fill='x')
        ttk.Label(bar, textvariable=self.location, wraplength=550).pack(side='left', fill='x', expand=True)
        ttk.Button(bar, text='打开原文文件', command=lambda:open_source(self.source_file)).pack(side='right')
        prompt_frame = ttk.Frame(self.frame)
        prompt_frame.pack(fill='x', pady=6)
        self.prompt = tk.Text(prompt_frame, height=7, wrap='word', background='#fff5d6', font=('Microsoft YaHei UI',10))
        prompt_scroll = ttk.Scrollbar(prompt_frame, command=self.prompt.yview)
        self.prompt.configure(yscrollcommand=prompt_scroll.set)
        self.prompt.pack(side='left', fill='both', expand=True)
        prompt_scroll.pack(side='right', fill='y')
        tabs = ttk.Notebook(self.frame)
        tabs.pack(fill='both', expand=True)
        word_frame = ttk.Frame(tabs)
        tabs.add(word_frame, text='对应原文（黄色为证据）')
        self.text = tk.Text(word_frame, wrap='word', font=('Microsoft YaHei UI',10))
        scroll = ttk.Scrollbar(word_frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.text.tag_configure('evidence', background='#ffe99c')
        self.text.tag_configure('condition', background='#bce8d4')
        page_frame = ttk.Frame(tabs)
        tabs.add(page_frame, text='PDF 原始页面')
        self.page_status = tk.StringVar(value='PDF 记录显示原始页；其他格式显示文字原文。')
        ttk.Label(page_frame, textvariable=self.page_status, wraplength=550).pack(fill='x')
        self.picture = ttk.Label(page_frame, anchor='center')
        self.picture.pack(fill='both', expand=True)
        self.picture.bind('<Double-Button-1>', self.zoom)
        self.frame.after(100, self.poll)

    @staticmethod
    def fill(widget, value):
        widget.configure(state='normal')
        widget.delete('1.0','end')
        widget.insert('1.0',value)
        widget.configure(state='disabled')

    def show(self, source, prompt, evidence='', condition_evidence='', cache=None):
        self.token += 1
        self.source_file = source.get('source_file')
        self.location.set((Path(self.source_file).name + ' · ' if self.source_file else '') + source.get('locator','摘要'))
        self.fill(self.prompt,prompt)
        self.fill(self.text,source.get('text',''))
        for excerpt,tag in [(evidence,'evidence'),(condition_evidence,'condition')]:
            if excerpt:
                index = self.text.search(excerpt,'1.0',stopindex='end',exact=True)
                if index:
                    self.text.tag_add(tag,index,f'{index}+{len(excerpt)}c')
                    self.text.see(index)
        self.image_path = None
        self.picture.configure(image='',text='')
        if self.source_file and Path(self.source_file).suffix.lower()=='.pdf' and cache:
            self.page_status.set('正在载入对应 PDF 原始页……')
            token = self.token
            self.pool.submit(self.render,token,self.source_file,source.get('locator',''),cache)
        else:
            self.page_status.set('这条来源没有 PDF 页；请查看“对应原文”页签。')

    def render(self,token,path,locator,cache):
        if token != self.token:
            return
        try:
            result = render_page(path,locator,cache)
            self.events.put((token,result,None))
        except Exception as exc:
            self.events.put((token,None,str(exc)))

    def poll(self):
        try:
            while True:
                token,path,error = self.events.get_nowait()
                if token != self.token:
                    continue
                if error:
                    self.page_status.set('原始页预览未载入：'+error+'。可以点击“打开原文文件”。')
                elif path:
                    self.image_path = path
                    with Image.open(path) as original:
                        scaled = original.copy()
                    scaled.thumbnail((max(self.frame.winfo_width()-25,350),560))
                    self.photo = ImageTk.PhotoImage(scaled)
                    self.picture.configure(image=self.photo)
                    self.page_status.set('原始页面已载入。双击图片，可在工具内放大查看。')
                else:
                    self.page_status.set('未定位 PDF 页。可以点击“打开原文文件”。')
        except queue.Empty:
            pass
        self.frame.after(100,self.poll)

    def zoom(self,_event=None):
        if not self.image_path:
            return
        window = tk.Toplevel(self.frame)
        window.title(self.location.get())
        window.geometry('1000x780')
        canvas = tk.Canvas(window)
        ys=ttk.Scrollbar(window,orient='vertical',command=canvas.yview)
        xs=ttk.Scrollbar(window,orient='horizontal',command=canvas.xview)
        canvas.configure(yscrollcommand=ys.set,xscrollcommand=xs.set)
        ys.pack(side='right',fill='y'); xs.pack(side='bottom',fill='x')
        canvas.pack(fill='both',expand=True)
        window.photo = ImageTk.PhotoImage(file=str(self.image_path))
        canvas.create_image(0,0,image=window.photo,anchor='nw')
        canvas.configure(scrollregion=(0,0,window.photo.width(),window.photo.height()))

    def close(self):
        self.pool.shutdown(wait=False,cancel_futures=True)
