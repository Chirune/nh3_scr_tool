"""Local filled-bar extraction. Category coordinates are never scientific values.

Only separately bounded solid rectangles on one calibrated numeric axis are
supported. Stacks, histogram bins, mixed/dual axes and ambiguous baselines fail
explicitly. Thin error caps are not used as bar endpoints.
"""
from __future__ import annotations
import math
import re
import tempfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter
try:
    from .auto_axes import _number, _deduplicate, _bands, _axis_fit, _axis_name, find_plot_frames, _runs
    from .digitize import validate_calibration
    from .native_ocr import recognize_image
    from .auto_curves import _text_rows
except ImportError:
    from auto_axes import _number, _deduplicate, _bands, _axis_fit, _axis_name, find_plot_frames, _runs
    from digitize import validate_calibration
    from native_ocr import recognize_image
    from auto_curves import _text_rows


def magnified_bar_text(image):
    """Retry local OCR at readable size, returning exact original coordinates."""
    scale=min(2.0,1900/max(image.size))
    if scale<=1.1:return {'lines':[],'scale':scale,'warnings':['图像已较大，不再放大。']}
    size=(round(image.width*scale),round(image.height*scale));sx=size[0]/image.width;sy=size[1]/image.height
    with tempfile.TemporaryDirectory(prefix='bar_label_ocr_') as temporary:
        path=Path(temporary)/'magnified.png'
        enlarged=image.resize(size,Image.Resampling.LANCZOS);enlarged.save(path);enlarged.close()
        result=recognize_image(path,include_rotated=True)
    def mapped(box):return [float(box[0]/sx),float(box[1]/sy),float(box[2]/sx),float(box[3]/sy)]
    for line in result['lines']:
        line['bbox']=mapped(line['bbox']);line['source']='windows_magnified_chart_ocr'
        for word in line.get('words',[]):word['bbox']=mapped(word['bbox'])
    result.update(width=image.width,height=image.height,scale_x=sx,scale_y=sy)
    return result


def _components(mask):
    """Run-length connected components; cost scales with runs, not filled area."""
    groups=[]; parents=[]; previous=[]
    def root(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]]; i=parents[i]
        return i
    for y,row in enumerate(mask):
        current=[]
        changes=np.diff(np.r_[False,row,False].astype(np.int8))
        for left,right in zip(np.flatnonzero(changes==1),np.flatnonzero(changes==-1)-1):
            touching={root(i) for a,b,i in previous if a<=right+1 and b>=left-1}
            if touching:
                i=min(touching)
                for j in touching: parents[j]=i
            else:
                i=len(groups);groups.append([]);parents.append(i)
            groups[i].append((left,y,right+1,y+1));current.append((left,right,i))
        previous=current
    merged={}
    for i,runs in enumerate(groups):merged.setdefault(root(i),[]).extend(runs)
    return [[min(v[0] for v in g),min(v[1] for v in g),max(v[2] for v in g),max(v[3] for v in g)] for g in merged.values()]


def filled_rectangles(image, frame):
    l,t,r,b=map(lambda v:int(round(v)),frame)
    # Omit axes themselves. Their thickness must not join filled black bars.
    l=max(0,l+3);t=max(0,t+2);r=min(image.width,r-2);b=min(image.height,b-2)
    if r-l<60 or b-t<60:return []
    rgb=np.asarray(image.convert('RGB'))[t:b,l:r,:].astype(np.int16)
    eligible=rgb.max(axis=2)<238
    eligible|=(rgb.max(axis=2)-rgb.min(axis=2)>45)&(rgb.min(axis=2)<210)
    pixels=rgb[eligible]
    if not len(pixels):return []
    quant=pixels//32
    colors,counts=np.unique(quant,axis=0,return_counts=True)
    prototypes=[];rects=[]
    masks=[(eligible,None)]
    for index in np.argsort(counts)[::-1][:18]:
        if counts[index]<max(90,rgb.shape[0]*rgb.shape[1]*.001):continue
        color=np.median(pixels[np.all(quant==colors[index],axis=1)],axis=0)
        if any(np.linalg.norm(color-c)<40 for c in prototypes):continue
        prototypes.append(color)
        masks.append(((np.linalg.norm(rgb-color,axis=2)<33)&eligible,color))
    for mask,color in masks:
        eroded=np.asarray(Image.fromarray((mask*255).astype('uint8')).filter(ImageFilter.MinFilter(5)))>0
        for a,y,c,d in _components(eroded):
            if c-a<3 or d-y<3:continue
            a=max(0,a-2);y=max(0,y-2);c=min(r-l,c+2);d=min(b-t,d+2)
            cut=mask[y:d,a:c]
            # Reject tails of a thick error stem: retain the broad rectangle.
            row_counts=cut.sum(axis=1); col_counts=cut.sum(axis=0)
            ys=np.flatnonzero(row_counts>=.80*max(row_counts))
            xs=np.flatnonzero(col_counts>=.80*max(col_counts))
            if not len(ys) or not len(xs):continue
            if (d-y)>(c-a)*1.3:y,d=y+int(ys[0]),y+int(ys[-1])+1
            elif (c-a)>(d-y)*1.3:a,c=a+int(xs[0]),a+int(xs[-1])+1
            bw,bh=c-a,d-y
            if bw<5 or bh<5 or bw*bh<max(80,(r-l)*(b-t)*.0006):continue
            fill=float(mask[y:d,a:c].mean())
            if fill<.88:continue
            box=[int(l+a),int(t+y),int(l+c),int(t+d)]
            if any(max(abs(u-v) for u,v in zip(box,old['bbox']))<4 for old in rects):continue
            chosen=np.median(rgb[y:d,a:c].reshape(-1,3),axis=0) if color is None else color
            rects.append({'bbox':box,'color':chosen.astype(int).tolist(),'fill_fraction':fill,'whole_foreground':color is None})
    # A transverse gradient can produce many colour stripes in one bar.
    # Remove only full-length contained stripes; preserve shorter stacked
    # segments so the downstream stack check can refuse them.
    kept=[]
    for v in rects:
        a,y,c,d=v['bbox']
        contained=False
        if not v['whole_foreground']:
            for u in rects:
                aa,yy,cc,dd=u['bbox']
                if u['whole_foreground'] and aa-2<=a<c<=cc+2 and yy-2<=y<d<=dd+2:
                    if (abs(y-yy)<4 and abs(d-dd)<4) or (abs(a-aa)<4 and abs(c-cc)<4):
                        # Exact full-height slices are gradient shades. Stacks
                        # sharing full width but not full height stay visible.
                        if ((d-y)>=(dd-yy)*.90 and (c-a)<(cc-aa)*.9) or ((c-a)>=(cc-aa)*.90 and (d-y)<(dd-yy)*.9 and cc-aa>dd-yy):
                            contained=True;break
        if not contained:kept.append(v)
    return kept


def bar_geometry(image, frames=None):
    candidates=[]
    for frame in frames if frames is not None else find_plot_frames(image):
        rects=filled_rectangles(image,frame)
        l,t,r,b=frame
        for orientation in ('vertical','horizontal'):
            across=0 if orientation=='vertical' else 1
            length=(r-l) if across==0 else (b-t)
            along_length=(b-t) if across==0 else (r-l)
            bodies=[v for v in rects if 5<=v['bbox'][across+2]-v['bbox'][across]<=length*.36 and
                    v['bbox'][3-across]-v['bbox'][1-across]>=max(16,along_length*.08)]
            # At least two broad bars sharing a baseline; numerical fit follows.
            ends=[(v['bbox'][1],v['bbox'][3]) if across==0 else (v['bbox'][0],v['bbox'][2]) for v in bodies]
            supports=max((sum(min(abs(e-p),abs(f-p))<=6 for e,f in ends) for pair in ends for p in pair),default=0)
            if supports>=2:candidates.append({'plot_bbox':frame,'orientation':orientation,'rectangles':rects,'geometric_support':supports})
    return candidates


def _tokens(lines):
    tokens=[]
    for line in lines:
        if line.get('orientation',0)!=0:continue
        observations=[line] if _number(line['text']) is not None else list(line.get('words',[]))
        for v in observations:
            value=_number(v['text'])
            if value is None:continue
            a,b,c,d=map(float,v['bbox']);p=[(a+c)/2,(b+d)/2]
            if any(abs(p[0]-u['pixel'][0])<2 and abs(p[1]-u['pixel'][1])<2 for u in tokens):continue
            tokens.append({'text':v['text'],'value':value,'bbox':[a,b,c,d],'pixel':p,'source':line.get('source','ocr')})
    return tokens


def _label_candidates(lines,frame,orientation):
    l,t,r,b=frame;answer=[]
    for line in lines:
        if line.get('orientation',0)!=0:continue
        a,y,c,d=line['bbox'];cx,cy=(a+c)/2,(y+d)/2
        if orientation=='vertical':valid=l<=cx<=r and b+1<=cy<=b+min(95,(b-t)*.32)
        else:valid=t<=cy<=b and l-min(200,(r-l)*.40)<=a<c<l-2
        if not valid or not line['text'].strip():continue
        if re.match(r'^(?:Fig(?:ure)?\.?\s*\d|\([a-z]\))',line['text'],re.I):continue
        answer.append(line)
    # Prefer detailed PDF words to full rows that combine several categories.
    chosen=[]
    for line in sorted(answer,key=lambda v:((v['bbox'][2]-v['bbox'][0])*(v['bbox'][3]-v['bbox'][1]))):
        a,y,c,d=line['bbox']
        if any(a-1<=u['bbox'][0] and y-1<=u['bbox'][1] and c+1>=u['bbox'][2] and d+1>=u['bbox'][3] for u in chosen):continue
        chosen.append(line)
    if orientation=='vertical' and chosen:
        nearest=min(v['bbox'][1] for v in chosen)
        first=[v for v in chosen if v['bbox'][1]<=nearest+max(5,(v['bbox'][3]-v['bbox'][1])*.7)]
        lower=[v for v in chosen if v not in first]
        merged=[]
        for anchor in first:
            cx=(anchor['bbox'][0]+anchor['bbox'][2])/2
            parts=[anchor]+[v for v in lower if v['bbox'][2]-v['bbox'][0]<(r-l)*.32 and
                abs((v['bbox'][0]+v['bbox'][2])/2-cx)<(r-l)*.13 and
                anchor is min(first,key=lambda u:abs((u['bbox'][0]+u['bbox'][2]-v['bbox'][0]-v['bbox'][2])/2))]
            merged.append({**anchor,'text':' '.join(v['text'] for v in sorted(parts,key=lambda v:v['bbox'][1])),
                           'parts':parts,'bbox':[min(v['bbox'][0] for v in parts),min(v['bbox'][1] for v in parts),max(v['bbox'][2] for v in parts),max(v['bbox'][3] for v in parts)]})
        chosen=merged
    return chosen


def _legend_label(image,color,lines,frame,bodies):
    rgb=np.asarray(image.convert('RGB')).astype(np.int16);matches=[]
    for line in lines:
        a,y,c,d=line['bbox'];cy=(y+d)/2
        if line.get('orientation',0)!=0 or not re.search('[A-Za-z\u4e00-\u9fff]',line['text']):continue
        if any(v['bbox'][0]-3<=a<=v['bbox'][2]+3 and v['bbox'][1]-3<=cy<=v['bbox'][3]+3 for v in bodies):continue
        left=max(0,int(a-min(65,(frame[2]-frame[0])*.15)));right=max(0,int(a-3))
        top=max(0,int(y)-2);bottom=min(image.height,int(d)+2)
        if right-left<5 or bottom-top<3:continue
        mask=np.linalg.norm(rgb[top:bottom,left:right]-np.asarray(color),axis=2)<33
        ys,xs=np.where(mask)
        if len(xs)<25:continue
        bw,bh=int(xs.max()-xs.min()+1),int(ys.max()-ys.min()+1)
        if bw<6 or bh<4 or bh>max(25,(d-y)*1.6) or len(xs)/(bw*bh)<.60:continue
        # Require swatch separate from plot bars and beyond neither image edge.
        sw=[left+int(xs.min()),top+int(ys.min()),left+int(xs.max())+1,top+int(ys.max())+1]
        if any(max(sw[0],v['bbox'][0])<min(sw[2],v['bbox'][2]) and max(sw[1],v['bbox'][1])<min(sw[3],v['bbox'][3]) for v in bodies):continue
        matches.append({'text':line['text'],'bbox':line['bbox'],'swatch_bbox':sw,'source':line.get('source','ocr')})
    if not matches:return None
    # Duplicate word/line observations are one label only if they overlap.
    first=max(matches,key=lambda v:len(v['text']))
    for other in matches:
        if other is first:continue
        if max(first['bbox'][1],other['bbox'][1])>min(first['bbox'][3],other['bbox'][3])+3:return None
    return first


def read_bars(image,text_lines,caption='',geometry=None):
    lines=_deduplicate(text_lines);tokens=_tokens(lines)
    geometry=geometry if geometry is not None else bar_geometry(image)
    result={'status':'not_bar' if not geometry else 'needs_review','series':[],
            'reasons':[],'reason_codes':[],'visible_text_lines':lines,'warnings':[]}
    if not geometry:return result
    unsupported=re.search(r'histogram|particle\s+size\s+distribution|stacked\s+bar|box\s*plot|violin|heat\s*map|contour|直方图|堆叠柱|箱线|热图',caption,re.I)
    if unsupported:
        result.update(reasons=['图注提示分布、堆叠或其他专用图型，本次不按普通柱体输出。'],reason_codes=['specialized_bar_like_chart'])
        return result
    options=[]
    for geometry_item in geometry:
        box=geometry_item['plot_bbox'];l,t,r,b=box;vertical=geometry_item['orientation']=='vertical'
        axis='y' if vertical else 'x';numeric=[];opposite=[]
        for token in tokens:
            px,py=token['pixel'];a,y,c,d=token['bbox'];h=max(5,d-y)
            if vertical:
                if t-6<=py<=b+6 and l-h*5<=c<=l-1:numeric.append(token)
                if t-6<=py<=b+6 and r+1<=a<=r+h*4:opposite.append(token)
            elif l-6<=px<=r+6 and b+1<=py<=b+h*4.5:numeric.append(token)
        for band in _bands(numeric,axis):
            fit,reason=_axis_fit(band,axis,b-t if vertical else r-l)
            if not fit:continue
            if fit['scale']!='linear':
                result['reason_codes'].append('log_bar_not_supported');continue
            if vertical and any(_axis_fit(g,'y',b-t)[0] for g in _bands(opposite,'y')):
                # Mirrored right ticks do not prove all bars use the left scale.
                result['reason_codes'].append('second_numeric_axis');continue
            baseline=fit['intercept']
            if vertical and not t-6<=baseline<=b+6:baseline=b
            if not vertical and not l-6<=baseline<=r+6:baseline=l
            bodies=[];floating=[]
            for v in geometry_item['rectangles']:
                a,y,c,d=v['bbox'];across=c-a if vertical else d-y
                if not 5<=across<=(r-l if vertical else b-t)*.36:continue
                ends=(y,d) if vertical else (a,c)
                if min(abs(e-baseline) for e in ends)<=7:
                    end=max(ends,key=lambda p:abs(p-baseline))
                    if abs(end-baseline)<6:continue
                    bodies.append({**v,'endpoint':end,'center':(a+c)/2 if vertical else (y+d)/2})
                else:floating.append(v)
            if len(bodies)<2:continue
            stacked=False
            for body in bodies:
                a,y,c,d=body['bbox']
                for v in floating:
                    aa,yy,cc,dd=v['bbox']
                    overlap=max(0,min(c,cc)-max(a,aa)) if vertical else max(0,min(d,dd)-max(y,yy))
                    denom=min(c-a,cc-aa) if vertical else min(d-y,dd-yy)
                    close=min(abs(y-dd),abs(d-yy)) if vertical else min(abs(a-cc),abs(c-aa))
                    if overlap/max(1,denom)>.75 and close<7:stacked=True
            if stacked:
                result['reason_codes'].append('stacked_rectangles');continue
            labels=_label_candidates(lines,box,geometry_item['orientation'])
            def catpos(v):return (v['bbox'][0]+v['bbox'][2])/2 if vertical else (v['bbox'][1]+v['bbox'][3])/2
            labels.sort(key=catpos)
            # Categories are associated by their nearest visible label, with
            # half-gap regions and a width bound for single-category plots.
            points=[];unassigned=[]
            for body in sorted(bodies,key=lambda v:v['center']):
                if not labels:
                    points.append({**body,'category':'','category_evidence':None});unassigned.append(body);continue
                label=min(labels,key=lambda v:abs(catpos(v)-body['center']))
                distance=abs(catpos(label)-body['center'])
                limit=(r-l if vertical else b-t)*.2
                if distance>limit:
                    points.append({**body,'category':'','category_evidence':None});unassigned.append(body);continue
                points.append({**body,'category':label['text'].strip(),'category_evidence':label})
            if len(points)<2:continue
            options.append({'fit':fit,'numeric_axis':axis,'plot_bbox':box,'points':points,'labels':labels,
                            'orientation':geometry_item['orientation'],'baseline_pixel':baseline,'unassigned':unassigned})
    if not options:
        result['reasons']=['未找到同时满足可分离柱体、类别标签和至少三个数值刻度的单轴柱图。']
        if 'second_numeric_axis' in result['reason_codes']:result['reasons']=['检测到右侧数值刻度，双轴对应关系未确认，未生成柱图数值。']
        if 'stacked_rectangles' in result['reason_codes']:result['reasons']=['检测到上下 / 左右堆叠的色块，不能把部分柱段当完整数值输出。']
        return result
    best=max(options,key=lambda v:(len(v['points']),len(v['fit']['ticks'])))
    axis=best['numeric_axis'];vertical=axis=='y';box=best['plot_bbox'];fit=best['fit'];l,t,r,b=box
    band=float(min(v['bbox'][0] for v in fit['ticks'])) if vertical else float(np.median([v['pixel'][1] for v in fit['ticks']]))
    name,unit,evidence=_axis_name(lines,box,axis,band)
    first,last=fit['ticks'][0],fit['ticks'][-1]
    pos=[fit['slope']*v['value']+fit['intercept'] for v in (first,last)]
    numeric={'p1':[l,pos[0]] if vertical else [pos[0],b],'p2':[l,pos[1]] if vertical else [pos[1],b],
             'v1':first['value'],'v2':last['value'],'scale':'linear','name':name,'unit':unit}
    # Legacy calibration stores this axis in pixels for UI editing only.
    # Export and tables suppress it and use category (never an invented number).
    category={'p1':[l,b] if vertical else [l,t],'p2':[r,b] if vertical else [l,b],
              'v1':l if vertical else t,'v2':r if vertical else b,'scale':'linear','name':'类别位置（仅像素）','unit':'pixel'}
    cal=validate_calibration({'y':numeric,'x':category} if vertical else {'x':numeric,'y':category})
    color_groups=[]
    for point in best['points']:
        group=next((g for g in color_groups if np.linalg.norm(np.array(g['color'])-point['color'])<40),None)
        if group is None:group={'color':point['color'],'points':[]};color_groups.append(group)
        group['points'].append(point)
    series=[];warnings=['只读取柱体端点；未提取误差线数值、SD / SE / CI 含义或重复次数。','类别与图例来自可见文字，样品与实验条件仍需核对。']
    for i,g in enumerate(color_groups,1):
        legend=_legend_label(image,g['color'],_text_rows(lines),box,best['points'])
        label=legend['text'] if legend else '柱系列'+str(i)+'（待核对）'
        if not legend:warnings.append(f'柱系列{i} 未确认图例名称，保留颜色和类别证据。')
        pts=[]
        for p in g['points']:
            pts.append({'px':p['center'] if vertical else p['endpoint'],'py':p['endpoint'] if vertical else p['center'],
                        'category':p['category'],'bar_bbox':p['bbox'],'bar_color':g['color'],'category_evidence':p['category_evidence']})
        series.append({'label':label,'color':g['color'],'label_evidence':legend,'method':'auto_bar',
                       'points_px':pts,'label_basis':'visible_legend_text' if legend else 'unresolved'})
    categories=[p['category'] for s in series for p in s['points_px']]
    if all(categories) and len(set(categories))==len(categories) and all(not s.get('label_evidence') for s in series):
        series=[{'label':'单组柱（类别分别保存）','method':'auto_bar','label_basis':'one_bar_per_visible_category',
                 'points_px':sorted([p for s in series for p in s['points_px']],key=lambda p:p['px'] if vertical else p['py'])}]
        warnings=[v for v in warnings if '未确认图例名称' not in v]
        warnings.append('每个可见类别对应一根柱；颜色未解释为独立实验系列。')
    if not name or not unit:warnings.append('数值轴名称或单位未完整识别，请核对原图。')
    if best['unassigned']:warnings.append('部分柱体类别名称未识别，category 留空；数值及柱体位置已保存。需补齐类别、样品与条件后再用于预测。')
    result.update(status='ready',chart_type='bar' if vertical else 'bar_horizontal',calibration=cal,plot_bbox=box,
                  category_axis='x' if vertical else 'y',numeric_axis=axis,bar_orientation=best['orientation'],
                  baseline_pixel=best['baseline_pixel'],series=series,warnings=warnings,axis_metadata_warnings=warnings,
                  axis_evidence={axis:{'tick_count':len(fit['ticks']),'ticks':fit['ticks'],'fit_scale':fit['scale'],
                  'max_residual_pixels':fit['max_residual_pixels'],'name_evidence':evidence}},
                  method='filled_rectangle_endpoint_single_numeric_axis',reasons=[],reason_codes=[],
                  missing_category_count=len(best['unassigned']),requires_category_review=bool(best['unassigned']))
    return result
