"""Recover visible continuous curve samples, separately from marker observations."""
from __future__ import annotations
import re
import unicodedata
import colorsys
import numpy as np
try:
    from .auto_curves import _prototypes, _color_masks, _components, _find_legend, _text_rows, _erode
    from .auto_axes import find_plot_frames
except ImportError:
    from auto_curves import _prototypes, _color_masks, _components, _find_legend, _text_rows, _erode
    from auto_axes import find_plot_frames


def normalize_legend(text):
    text = unicodedata.normalize('NFKC', str(text))
    text = re.sub(r'(?<=\d)\s*[.,]\s*(?=\d)', '.', text)
    return text.strip()


def _stroke_legend(infos, rows, plot):
    l,t,r,b=plot
    width=r-l
    candidates=[]
    for row in rows:
        x0,y0,x1,y1=row['bbox']
        if not (l<x0<x1<r and t<y0<y1<b): continue
        cy=(y0+y1)/2
        # A small swatch immediately left of the visible label, not the whole
        # connected curve component, associates thin lines and hollow symbols.
        left=max(l+2,int(x0-width*.19));right=int(x0-3)
        top=max(t+2,int(cy-max(3,(y1-y0)*.32)))
        bottom=min(b-2,int(cy+max(3,(y1-y0)*.32))+1)
        if right<=left or bottom<=top: continue
        options=[]
        for index,info in enumerate(infos):
            mask=info['mask'][top:bottom,left:right]
            cols=np.flatnonzero(mask.any(axis=0))
            if len(cols)<max(7,width*.035): continue
            # Swatches span a short horizontal distance; a steep real curve
            # typically touches very few columns in this thin band.
            options.append((len(cols),index,[left+int(cols[0]),top,left+int(cols[-1])+1,bottom]))
        if options:
            _,index,swatch=max(options)
            candidates.append({'index':index,'label':row['text'],'bbox':row['bbox'],'swatch_bbox':swatch})
    groups=[]
    for item in candidates:
        found=next((g for g in groups if abs(item['bbox'][0]-np.median([v['bbox'][0] for v in g]))<width*.045 and abs(item['swatch_bbox'][0]-np.median([v['swatch_bbox'][0] for v in g]))<width*.07),None)
        if found is None: groups.append([item])
        else: found.append(item)
    groups=[g for g in groups if len(g)>=2 and len({i['index'] for i in g})>=2]
    if not groups: return None,{}
    group=max(groups,key=len)
    labels={}
    for item in group:
        index=item['index']
        if index not in labels: labels[index]={k:item[k] for k in ('label','bbox','swatch_bbox')} | {'alternatives':[],'entries':[]}
        labels[index]['alternatives'].append(item['label'])
        labels[index]['entries'].append(item)
    box=[min(v['swatch_bbox'][0] for v in group)-3,min(min(v['bbox'][1],v['swatch_bbox'][1]) for v in group)-3,
         max(v['bbox'][2] for v in group)+3,max(max(v['bbox'][3],v['swatch_bbox'][3]) for v in group)+3]
    return box,labels


def _typed_markers(mask, plot):
    """Local filled cores and bounded background holes; no curve interpolation."""
    l,t,r,b=map(int,plot)
    region=mask[t:b,l:r]
    maximum=max(9,(r-l)*.045)
    result={'filled':[],'hollow':[]}
    for style,comps in [('filled',_components(_erode(region,1),3)),('hollow',_components(~region,2))]:
        for comp in comps:
            x0,y0,x1,y1=comp['bbox'];w,h=x1-x0,y1-y0
            if min(w,h)<2 or max(w,h)>maximum or max(w,h)>min(w,h)*2: continue
            if style=='hollow' and (x0==0 or y0==0 or x1==r-l or y1==b-t): continue
            if style=='hollow' and comp['area'] < 3: continue
            x,y=comp['center']
            result[style].append({'px':x+l,'py':y+t,'marker_bbox':[x0+l,y0+t,x1+l,y1+t],
                                  'method':'automatic_'+style+'_marker','review_status':'unreviewed'})
    return result


def trace_series(image, plot_bbox, text_lines, calibration, exclude_boxes=None):
    rgb = np.asarray(image.convert('RGB'))
    l,t,r,b = [int(round(x)) for x in plot_bbox]
    width,height = r-l,b-t
    roi = np.zeros(rgb.shape[:2],dtype=bool)
    roi[t+2:b-2,l+2:r-2] = True
    exclusions = list(exclude_boxes or [])
    for box in find_plot_frames(image):
        x0,y0,x1,y1 = box
        if x0 > l+12 and y0 > t+12 and x1 < r-12 and y1 < b-12:
            w,h=x1-x0,y1-y0
            exclusions.append([max(l,x0-w*.38),max(t,y0-10),min(r,x1+20),min(b,y1+h*.30)])
    infos=[]
    palette = _prototypes(rgb,[l,t,r,b])
    # Include neutral black strokes; text and frame regions are removed below.
    palette.append([25,25,25])
    for color in palette:
        broad,strong = _color_masks(rgb,color)
        if np.ptp(color)<25:
            mean=rgb.mean(axis=2)
            broad=(rgb.max(axis=2)-rgb.min(axis=2)<20)&(mean<175)
            strong=broad
        broad &= roi
        comps=_components(broad,3)
        infos.append({'color':color,'components':comps,'cores':comps,'mask':broad})
    # The same neutral ink must not appear as separate grey/black series.
    neutral=[i for i,v in enumerate(infos) if np.ptp(v['color'])<25]
    if len(neutral)>1:
        infos=[v for i,v in enumerate(infos) if i not in neutral[:-1]]
    rows=_text_rows(text_lines)
    for row in rows:
        row['text']=normalize_legend(row['text'])
    legend,labels=_stroke_legend(infos,rows,[l,t,r,b])
    if legend is None:
        legend,labels=_find_legend(infos,rows,[l,t,r,b],allow_numeric=True)
    # Antialiasing/printing may give a labelled stroke an extra dark shade.
    # Only fold an unlabelled nearby hue into a labelled series; two labelled
    # swatches remain independent even if their colours are similar.
    folded=set()
    for index,info in enumerate(infos):
        if index in labels or np.ptp(info['color'])<25:
            continue
        hue=colorsys.rgb_to_hsv(*(v/255 for v in info['color']))[0]
        options=[]
        for j in labels:
            if np.ptp(infos[j]['color'])<25: continue
            other=colorsys.rgb_to_hsv(*(v/255 for v in infos[j]['color']))[0]
            distance=min(abs(hue-other),1-abs(hue-other))
            if distance<.055: options.append((distance,j))
        if options:
            j=min(options)[1]
            infos[j]['mask'] |= info['mask']
            folded.add(index)
    if legend:
        exclusions.append(legend)
    # Words inside the plot are annotations, never curves. Keep legend strokes
    # excluded as a group, and leave axis titles outside this region alone.
    for row in rows:
        x0,y0,x1,y1=row['bbox']
        if l < x0 < x1 < r and t < y0 < y1 < b:
            exclusions.append([x0-2,y0-2,x1+2,y1+2])
    for row in text_lines:
        if row.get('orientation',0)!=0: continue
        x0,y0,x1,y1=row['bbox']
        if l < x0 < x1 < r and t < y0 < y1 < b:
            exclusions.append([x0-2,y0-2,x1+2,y1+2])
    for x0,y0,x1,y1 in exclusions:
        roi[max(t,int(y0)):min(b,int(y1)+1),max(l,int(x0)):min(r,int(x1)+1)] = False
    start,end=l+3,r-3
    isothermal=any(re.search(r'isothermal',line['text'],re.I) for line in text_lines)
    if isothermal:
        start=max(start,int(min(calibration['x']['p1'][0],calibration['x']['p2'][0])))
        end=min(end,int(max(calibration['x']['p1'][0],calibration['x']['p2'][0])))
    step=max(2,int(round(width/180)))
    result=[]
    skipped=[]
    for index,info in enumerate(infos):
        if index in folded: continue
        if len(labels.get(index,{}).get('alternatives',[]))>1:
            typed=_typed_markers(info['mask'],[l,t,r,b])
            assignments={}
            for entry in labels[index].get('entries',[]):
                sx0,sy0,sx1,sy1=entry['swatch_bbox']
                hollow=any(sx0<=p['px']<=sx1 and sy0-3<=p['py']<=sy1+3 for p in typed['hollow'])
                style='hollow' if hollow else 'filled'
                assignments.setdefault(style,[]).append(entry)
            recovered=[]
            for style,entries in assignments.items():
                if len(entries)!=1: continue
                points=[p for p in typed[style] if roi[int(p['py']),int(p['px'])]]
                if style=='filled':
                    # A partially covered solid marker leaves a crescent/core
                    # beside a hollow marker. Its centroid is not a data point.
                    points=[p for p in points if min(p['marker_bbox'][2]-p['marker_bbox'][0],p['marker_bbox'][3]-p['marker_bbox'][1])>=3
                            and not any(np.hypot(p['px']-q['px'],p['py']-q['py']) < max(p['marker_bbox'][2]-p['marker_bbox'][0],p['marker_bbox'][3]-p['marker_bbox'][1])+2 for q in typed['hollow'])]
                points.sort(key=lambda p:p['px'])
                if len(points)<3: continue
                if any(abs(a['px']-z['px'])<3 and abs(a['py']-z['py'])>10 for a,z in zip(points,points[1:])):continue
                entry=entries[0]
                recovered.append({'label':entry['label'],'label_raw':entry['label'],'color':info['color'],
                    'points_px':points,'method':'automatic_'+style+'_markers','label_requires_review':True,
                    'label_basis':'visible_legend_text_and_'+style+'_symbol','legend_text_bbox':entry['bbox'],
                    'sample_count':len(points),'marker_style':style,'value_origin':'image_digitized_approximate',
                    'warnings':['空心/实心标记按可见形状区分；重叠或遮挡点未补齐。']})
            result.extend(recovered)
            missing=[v for v in labels[index]['alternatives'] if v not in {s['label'] for s in recovered}]
            if missing: skipped.append({'color':info['color'],'reason':'same_colour_overlapping_markers','labels':missing})
            continue
        mask=info['mask'] & roi
        components=_components(mask,12)
        if legend:
            # A thin black legend frame may sit just outside the text/swatch
            # rectangle. Exclude the enclosing frame instead of sampling it.
            lx,ly,rx,by=legend
            for c in components:
                a,y,z,d=c['bbox'];area=(z-a)*(d-y)
                close=max(abs(a-lx),abs(z-rx))<max(20,width*.07) and max(abs(y-ly),abs(d-by))<max(20,height*.07)
                if close and a<(lx+rx)/2<z and y<(ly+by)/2<d and z-a>=(rx-lx)*.75 and d-y>=(by-ly)*.75 and c['area']/max(1,area)<.15:
                    mask[max(t,y-1):min(b,d+2),max(l,a-1):min(r,z+2)]=False
        components=[c for c in _components(mask,12) if c['area']>=max(35,width*height*.00025)]
        # Isolated filled scatter markers must yield one centre per marker,
        # not many columns of samples across each circular disc.
        small=[c for c in components if 4<=c['bbox'][2]-c['bbox'][0]<=max(25,width*.045) and
               4<=c['bbox'][3]-c['bbox'][1]<=max(25,height*.065) and
               c['area']/max(1,(c['bbox'][2]-c['bbox'][0])*(c['bbox'][3]-c['bbox'][1]))>=.5]
        if index in labels and len(small)>=3 and len(small)==len(components):
            label_info=labels[index];raw=label_info.get('label','散点系列（待核对）')
            points=[{'px':float(c['center'][0]),'py':float(c['center'][1]),'marker_bbox':c['bbox'],
                     'method':'automatic_isolated_scatter_marker','review_status':'unreviewed'} for c in small]
            points.sort(key=lambda p:(p['px'],p['py']))
            result.append({'label':normalize_legend(raw),'label_raw':raw,'color':info['color'],'points_px':points,
                           'method':'automatic_isolated_scatter_markers','label_requires_review':True,
                           'label_basis':'visible_legend_text','legend_text_bbox':label_info.get('bbox'),
                           'sample_count':len(points),'marker_style':'isolated','value_origin':'image_digitized_approximate',
                           'warnings':['独立散点按标记中心读取，一枚标记对应一行；重叠点未推算。']})
            continue
        # Remove isolated text/antialias speckles by retaining components with
        # horizontal extent. A partly hidden curve can have several segments.
        valid=np.zeros_like(mask)
        for comp in _components(mask,5):
            x0,y0,x1,y1=comp['bbox']
            if max(x1-x0,y1-y0) >= max(8,width*.018):
                valid[y0:y1,x0:x1] |= mask[y0:y1,x0:x1]
        points=[]
        ambiguous=0
        missing=0
        segment=0
        previous=None
        for x in range(start,end+1,step):
            ys=np.flatnonzero(valid[:,x])
            if not len(ys):
                missing+=1
                previous=None
                continue
            groups=np.split(ys,np.flatnonzero(np.diff(ys)>2)+1)
            # A thick same-colour branch is evidence of ambiguity, too.
            # Filtering it out before counting runs would falsely promote a
            # remaining thin run to an unambiguous curve sample.
            if len(groups)!=1 or groups[0][-1]-groups[0][0] > max(14,height*.06):
                ambiguous+=1
                previous=None
                continue
            y=float((groups[0][0]+groups[0][-1])/2)
            if previous is None:
                segment+=1
            points.append({'px':float(x),'py':y,'method':'automatic_curve_sample',
                           'trace_id':f'segment_{segment}','review_status':'unreviewed'})
            previous=y
        if len(points)<max(8,int((end-start)/step*.12)):
            continue
        label_info=labels.get(index,{})
        raw=label_info.get('label',f'颜色系列 {len(result)+1}（图例待核对）')
        result.append({'label':normalize_legend(raw),'label_raw':raw,'color':info['color'],
                       'points_px':points,'method':'automatic_continuous_curve_samples',
                       'label_requires_review':True,'label_basis':'visible_legend_text' if label_info else 'unresolved',
                       'legend_text_bbox':label_info.get('bbox'),'sample_count':len(points),
                       'missing_columns':missing,'ambiguous_columns':ambiguous,
                       'value_origin':'image_curve_samples_approximate',
                       'warnings':['曲线采样点不是作者原始实验点；未补齐遮挡或同色分支。']})
    warnings=['连续曲线按图像位置采样；点数由采样间隔决定，不代表实验次数。',
              '断开、重叠或多个同色分支处跳过；须核对每条曲线与图例。']
    if result and all(s.get('marker_style') for s in result):
        warnings=['按图例区分空心、实心标记，输出可辨认标记的近似位置；遮挡及相交处可能漏检，未补点。']
        if all(s.get('marker_style')=='isolated' for s in result):
            warnings=['独立散点按标记中心读取，一枚可见标记对应一行；未将标记宽度采样为多个实验点。图例、样品和条件仍需核对。']
    if isothermal:
        warnings.append('检测到 isothermal 等温阶段；仅在已识别温度刻度区间采样，未把后段位置当作更高温度。')
    if legend is None:
        warnings.append('图例尚未可靠定位，颜色系列名称需要人工核对。')
    if skipped:
        warnings.append('部分同色实心/空心标记互相遮挡，未输出的系列：'+'；'.join(', '.join(v['labels']) for v in skipped)+'。没有补齐或合并这些系列。')
    return {'series':result,'point_count':sum(len(s['points_px']) for s in result),
            'legend_bbox':legend,'exclude_boxes':exclusions,'warnings':warnings,
            'method':'continuous_visible_stroke_sampling','plot_bbox':plot_bbox,'is_verified':False,'skipped_series':skipped}
