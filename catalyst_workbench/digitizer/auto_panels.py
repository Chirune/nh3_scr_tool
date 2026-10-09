"""Separate independent plot frames and preserve their original-page mapping."""
from __future__ import annotations
import copy, csv, hashlib, json, re, uuid
from datetime import datetime
from pathlib import Path
try:
    from .auto_axes import find_plot_frames, visible_pdf_text
    from .session import save_session
    from .native_ocr import recognize_image
    from .auto_symbols import find_symbol_legend
except ImportError:
    from auto_axes import find_plot_frames, visible_pdf_text
    from session import save_session
    from native_ocr import recognize_image
    from auto_symbols import find_symbol_legend


def independent_frames(image):
    frames=find_plot_frames(image)
    frames=[b for b in frames if not any(a!=b and a[0]<=b[0] and a[1]<=b[1] and a[2]>=b[2] and a[3]>=b[3] for a in frames)]
    rows=[]
    for box in sorted(frames,key=lambda b:(b[1],b[0])):
        row=next((r for r in rows if abs(box[1]-r[0][1])<min(box[3]-box[1],r[0][3]-r[0][1])*.15),None)
        if row is None: rows.append([box])
        else: row.append(box)
    return [box for row in rows for box in sorted(row,key=lambda b:b[0])]


def panel_specs(image, frames, lines):
    result=[]
    for index,f in enumerate(frames):
        w,h=f[2]-f[0],f[3]-f[1]
        bounds=[max(0,int(f[0]-.28*w)),max(0,int(f[1]-.16*h)),min(image.width,int(f[2]+.12*w)),min(image.height,int(f[3]+.38*h))]
        # Stop before the next panel's plot; preserve title/ticks in the gutter.
        for other in frames:
            if other==f: continue
            if other[1]<f[3] and other[3]>f[1]:
                if other[0]>f[2]: bounds[2]=min(bounds[2],int(other[0]-5))
                if other[2]<f[0]: bounds[0]=max(bounds[0],int(other[2]+5))
            if other[0]<f[2] and other[2]>f[0]:
                if other[1]>f[3]: bounds[3]=min(bounds[3],int(other[1]-5))
                if other[3]<f[1]: bounds[1]=max(bounds[1],int(other[3]+5))
        options=[]
        for line in lines:
            # A lone PDF glyph in an axis title (e.g. the p in Temperature)
            # must never become the subfigure identifier.
            match=re.fullmatch(r'\(([a-zA-Z])\)',line['text'].strip())
            b=line['bbox'];cx,cy=(b[0]+b[2])/2,(b[1]+b[3])/2
            if match and f[0]<=cx<=f[2] and f[3]<cy<bounds[3]:
                options.append((abs(cx-(f[0]+f[2])/2),match.group(1)))
        label='('+min(options)[1]+')' if options else '子图'+str(index+1)
        titles=[line for line in lines if line.get('orientation',0)==0 and
                re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 /+_.-]{1,30}',line['text'].strip()) and
                re.search('[A-Za-z]',line['text']) and
                f[0]<(line['bbox'][0]+line['bbox'][2])/2<f[2] and
                bounds[1]<=line['bbox'][1]<line['bbox'][3]<f[1] and
                line['bbox'][2]-line['bbox'][0]<w*.7]
        title=min(titles,key=lambda line:abs((line['bbox'][0]+line['bbox'][2]-f[0]-f[2])/2)) if titles else None
        result.append({'label':label,'bbox':bounds,'plot_bbox_in_parent':f,
                       'sample_label':title['text'].strip() if title else '',
                       'sample_label_evidence':title,'sample_label_requires_review':bool(title)})
    return result


def create_panel_session(original,image,spec):
    name='panel_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
    folder=Path(original['run_dir']).parent/name
    folder.mkdir()
    cropped=image.crop(spec['bbox'])
    cropped.save(folder/'source.png');cropped.close()
    child=copy.deepcopy(original)
    child.update(session_id=name,run_dir=str(folder),image_path=str(folder/'source.png'),image_file='source.png',
                 image_sha256=hashlib.sha256((folder/'source.png').read_bytes()).hexdigest(),
                 points=[],exports=[],calibration=None,calibration_id=None,calibration_history=[],roi=None,reviewed=False,
                 figure_label=original.get('figure_label','')+spec['label'])
    child.pop('automatic_extraction',None)
    child.pop('automatic_bundle',None)
    child.pop('last_export_dir',None)
    metadata=child['source_metadata']
    context=metadata.setdefault('figure_context',{})
    transform=context.get('crop_to_page',{'offset_x':0,'offset_y':0,'scale_x':1,'scale_y':1}).copy()
    transform['offset_x']=transform.get('offset_x',0)+spec['bbox'][0]*transform.get('scale_x',1)
    transform['offset_y']=transform.get('offset_y',0)+spec['bbox'][1]*transform.get('scale_y',1)
    context['crop_to_page']=transform
    context['automatic_subfigure']={'parent_session':str(Path(original['run_dir'])/'session.json'),**spec}
    child['notes']=original.get('notes','')+'\n独立子图自动标定；自动拆分标签及样品关联待核对。'
    return child,save_session(child)


def read_panels(original,image,session_path,frames,extractor,register):
    lines,flags=visible_pdf_text(original,image.size)
    shared=find_symbol_legend(image,frames,lines)
    # Raster plots have no readable caption/title/legend text in the PDF.
    # OCR the complete figure once to retain its shared legend and panel titles.
    if shared is None and len([v for v in lines if len(v['text'])>3])<6:
        try:
            observed=recognize_image(original['image_path'],include_rotated=False)
            lines+=observed['lines']
            shared=find_symbol_legend(image,frames,lines)
        except Exception as exc:
            flags.append('组合图图例文字识别未完成：'+str(exc))
    specs=panel_specs(image,frames,lines)
    bundle=Path(original['run_dir'])/('自动拆图结果_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:4])
    bundle.mkdir()
    results=[]
    for spec in specs:
        child,path=create_panel_session(original,image,spec)
        if shared:
            context=child['source_metadata']['figure_context']
            context['shared_symbol_legend']=copy.deepcopy(shared)
            context['shared_symbol_legend']['source_session']=str(session_path)
            # Only the panel which contains the legend needs a local mask.
            a,b,c,d=shared['legend_bbox'];x,y,r,z=spec['bbox']
            context['shared_symbol_legend']['local_legend_bbox']=[a-x,b-y,c-x,d-y] if x<=a<c<=r and y<=b<d<=z else None
            save_session(child)
        result=extractor(path,split_panels=False)
        result['panel_label']=spec['label'];result['crop_bbox']=spec['bbox']
        result['sample_label']=spec.get('sample_label','')
        results.append(result)
    successful=[r for r in results if r.get('count',0)>0 and r.get('export_dir')]
    rows=[];columns=[]
    for item in successful:
        with (Path(item['export_dir'])/'读数数据.csv').open(encoding='utf-8-sig',newline='') as stream:
            reader=csv.DictReader(stream);columns=reader.fieldnames;rows.extend(reader)
    if rows:
        with (bundle/'读数数据.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader();writer.writerows(rows)
    warnings=['各子图独立标定；曲线采样行数不代表独立实验数。所有自动读数均待核对。']
    if shared: warnings.append('圆点和方块的名称取自整张图的共用图例；对子图的适用关系和样品标题仍需核对。')
    failed=[r for r in results if not r.get('count')]
    for result in results:
        warnings.extend(result['panel_label']+'：'+v for v in result.get('warnings',[]) if any(term in v for term in ('未输出','等温','重叠','不能可靠区分')))
    if failed: warnings.append('尚未读出的子图：'+', '.join(r['panel_label'] for r in failed))
    payload={'status':('partial_success' if failed or any(r.get('status')=='partial_success' for r in results) else 'success') if rows else 'needs_review',
             'count':len(rows),'panel_count':len(specs),'successful_panel_count':len(successful),
             'panels':results,'export_dir':str(bundle),'bundle_export_dir':str(bundle),
             'session_path':successful[0]['session_path'] if successful else str(session_path),
             'message':f'已自动拆分 {len(specs)} 个子图；{len(successful)} 个子图生成数值表，共 {len(rows)} 行。',
             'warnings':warnings,'report_path':str(bundle/'拆图读取记录.json')}
    (bundle/'拆图读取记录.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf8')
    (bundle/'请先阅读.txt').write_text(payload['message']+'\n'+'\n'.join(warnings)+'\n\n各子图结果：\n'+'\n'.join(r['panel_label']+'：'+r.get('export_dir',r.get('message','')) for r in results),encoding='utf-8-sig')
    # An offline index makes every panel/overlay/table directly reviewable.
    from html import escape
    links=[]
    for item in results:
        if item.get('count'):
            dest=Path(item['export_dir'])
            links.append('<section><h2>'+escape(item['panel_label'])+'</h2><p>'+str(item['count'])+' 行待核对读数 · <a href="'+(dest/'读数数据.csv').as_uri()+'">打开子图数值表</a> · <a href="'+(dest/'逐系列核对.html').as_uri()+'">逐系列核对原图与数值</a></p><img src="'+(dest/'原图与读数标记.png').as_uri()+'"><p>'+escape('；'.join(item.get('warnings',[])))+'</p></section>')
        else: links.append('<section><h2>'+escape(item['panel_label'])+'</h2><p>'+escape(item.get('message','未读出'))+'</p></section>')
    html='<meta charset="utf-8"><title>自动拆图读取结果</title><style>body{font-family:Microsoft YaHei, sans-serif;max-width:1050px;margin:36px auto;padding:0 24px;line-height:1.7;background:#f3f6f8}section{padding:20px;background:white;border-radius:12px;margin:20px 0}img{max-width:100%}a{color:#12647a}</style><h1>'+escape(payload['message'])+'</h1><p>图中恢复的近似数值；不是作者原始数据。未将采样点当作独立实验。</p><a href="读数数据.csv">打开全部子图的汇总数值表</a>'+''.join(links)
    (bundle/'查看子图与数值.html').write_text(html,encoding='utf8')
    for item in successful:
        path=Path(item['session_path']);child=json.loads(path.read_text(encoding='utf8'))
        child['automatic_bundle']={'export_dir':str(bundle),'session_export_directory':child['exports'][-1]['directory'],
                                   'panels':[{k:r.get(k) for k in ('panel_label','session_path','count','status')} for r in results]}
        save_session(child)
    return payload
