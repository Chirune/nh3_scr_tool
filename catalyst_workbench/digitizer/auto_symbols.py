"""Separate same-ink filled circles/squares using actual visible legend samples."""
from __future__ import annotations
import re
import numpy as np
from PIL import Image
try:
    from .auto_curves import _components, _erode, _text_rows
except ImportError:
    from auto_curves import _components, _erode, _text_rows


def _ink(image):
    rgb=np.asarray(image.convert('RGB')).astype(np.int16)
    return (rgb.max(axis=2)-rgb.min(axis=2)<30)&(rgb.mean(axis=2)<140)


def find_symbol_legend(image, plots, lines):
    ink=_ink(image)
    cores=_components(_erode(ink,1),6)
    rows=_text_rows(lines)
    candidates=[]
    for plot in plots:
        l,t,r,b=plot;w=r-l
        for row in rows:
            x0,y0,x1,y1=row['bbox'];cy=(y0+y1)/2
            if not(l<x0<x1<r and t<y0<y1<b and re.search('[A-Za-z]{3}',row['text'])):continue
            options=[]
            for core in cores:
                a,z,c,d=core['bbox'];ww,hh=c-a,d-z;cx,yy=core['center']
                if not(5<=min(ww,hh)<=w*.045 and max(ww,hh)/min(ww,hh)<1.22):continue
                if not(8<x0-cx<w*.18 and abs(yy-cy)<max(4,(y1-y0)*.45)):continue
                fill=core['area']/(ww*hh)
                shape='circle' if .52<=fill<=.83 else 'square' if fill>=.91 else None
                if shape:options.append((abs(yy-cy),x0-cx,core,shape))
            if not options:continue
            _,_,core,shape=min(options,key=lambda v:(v[0],v[1]))
            a,z,c,d=core['bbox'];bb=[a-1,z-1,c+1,d+1]
            patch=ink[bb[1]:bb[3],bb[0]:bb[2]]
            marker_size=max(patch.shape)
            # A perfectly filled square needs a background rim for rejecting
            # oversized blobs; antialiased symbols already provide that rim.
            if not (~patch).any():
                bb=[a-2,z-2,c+2,d+2]
                patch=ink[bb[1]:bb[3],bb[0]:bb[2]]
            candidates.append({'label':row['text'],'label_bbox':row['bbox'],'shape':shape,
                               'symbol_bbox':bb,'template':patch.astype(int).tolist(),
                               'marker_size':marker_size,'center':core['center'],'plot_bbox':plot})
    for first in candidates:
        group=[v for v in candidates if v['plot_bbox']==first['plot_bbox'] and
               abs(v['center'][0]-first['center'][0])<8 and abs(v['label_bbox'][0]-first['label_bbox'][0])<22]
        if len(group)!=2 or {v['shape'] for v in group}!={'circle','square'}:continue
        if abs(group[0]['center'][1]-group[1]['center'][1])<12:continue
        return {'entries':group,'basis':'visible_same_ink_legend_symbols',
                'legend_bbox':[min(v['symbol_bbox'][0] for v in group)-30,min(v['symbol_bbox'][1] for v in group)-8,
                               max(v['label_bbox'][2] for v in group)+8,max(v['symbol_bbox'][3] for v in group)+8]}
    return None


def _separate_touching_pair(ink, core, templates, size):
    """Fit two visible touching symbols jointly, with a resolved centre gap.

    A single-centre or strongly overlapping blob cannot meet the separation
    and independent visible-shape requirements and is left unassigned.
    """
    a,z,c,d=core['bbox']
    options=[]
    for top_entry,top_mask,_ in templates:
        for bottom_entry,bottom_mask,_ in templates:
            if top_entry['shape']==bottom_entry['shape']:continue
            ht,wt=top_mask.shape;hb,wb=bottom_mask.shape
            for dx in (-1,0,1):
                cx=(a+c-1)/2+dx
                for dt in (-1,0,1):
                    ty=z-1+dt;tx=round(cx-(wt-1)/2)
                    for db in (-1,0,1):
                        by=d+1-hb+db;bx=round(cx-(wb-1)/2)
                        gap=by+(hb-1)/2-ty-(ht-1)/2
                        if gap<size*.75 or gap>size*2.3:continue
                        x0,y0,x1,y1=min(tx,bx),ty,max(tx+wt,bx+wb),by+hb
                        if x0<0 or y0<0 or x1>ink.shape[1] or y1>ink.shape[0] or y1<=y0:continue
                        expected=np.zeros((y1-y0,x1-x0),dtype=bool)
                        expected[:ht,tx-x0:tx-x0+wt]|=top_mask
                        expected[by-y0:by-y0+hb,bx-x0:bx-x0+wb]|=bottom_mask
                        observed=ink[y0:y1,x0:x1]
                        miss=1-float(observed[expected].mean())
                        extra=float(observed[~expected].mean()) if (~expected).any() else 1
                        score=miss+.45*extra
                        if miss>.12 or extra>.32 or score>.18:continue
                        options.append((score,top_entry['shape'],[
                            {'px':tx+(wt-1)/2,'py':ty+(ht-1)/2,'score':score,'shape':top_entry['shape'],
                             'label':top_entry['label'],'marker_bbox':[tx,ty,tx+wt,ty+ht],'joint_symbol_fit':True},
                            {'px':bx+(wb-1)/2,'py':by+(hb-1)/2,'score':score,'shape':bottom_entry['shape'],
                             'label':bottom_entry['label'],'marker_bbox':[bx,by,bx+wb,by+hb],'joint_symbol_fit':True}]))
    if not options:return []
    options.sort(key=lambda v:v[0]);best=options[0]
    other=[v for v in options if v[1]!=best[1]]
    if other and other[0][0]-best[0]<.035:return []
    return best[2]


def extract_symbol_series(image, plot, legend, local_legend_bbox=None, text_lines=None):
    """Output separable printed marker centres; ambiguous overlaps stay unassigned."""
    ink=_ink(image);l,t,r,b=map(int,map(round,plot))
    roi=np.zeros_like(ink);roi[t+2:b-2,l+2:r-2]=True
    excluded=[]
    if local_legend_bbox: excluded.append(local_legend_bbox)
    for row in _text_rows(text_lines or []):
        a,z,c,d=row['bbox']
        if l<a<c<r and t<z<d<b:excluded.append([a-2,z-2,c+2,d+2])
    for a,z,c,d in excluded:
        roi[max(0,int(z)):int(d)+1,max(0,int(a)):int(c)+1]=False
    masked=ink&roi
    cores=_components(_erode(masked,1),6)
    templates=[]
    for entry in legend['entries']:
        template=np.array(entry['template'],dtype=bool)
        for scale in (.90,1.0,1.1):
            h,w=template.shape
            tw,th=round(w*scale),round(h*scale)
            variant=np.array(Image.fromarray(template.astype('uint8')*255).resize((tw,th),Image.Resampling.NEAREST))>0
            # Ignore the middle rows at either side, where line samples join
            # the legend marker and plotted connecting lines join data points.
            valid=np.ones_like(variant)
            valid[max(0,th//2-1):th//2+2,:2]=False
            valid[max(0,th//2-1):th//2+2,-2:]=False
            templates.append((entry,variant,valid))
    size=float(np.median([e.get('marker_size',max(np.array(e['template']).shape)) for e in legend['entries']]))
    candidates=[];ambiguous_regions=0
    for core in cores:
        a,z,c,d=core['bbox'];ww,hh=c-a,d-z
        if not(size*.45<=ww<=size*1.6 and size*.45<=hh<=size*3.5):continue
        if hh>size*1.25:
            pair=_separate_touching_pair(ink,core,templates,size)
            if pair:
                candidates.extend(pair)
                continue
        hits=[]
        for entry,template,valid in templates:
            th,tw=template.shape;foreground=template&valid;background=(~template)&valid
            if not foreground.any() or not background.any():continue
            for yy in range(max(t+2,z-2),min(b-2,d+2)):
                for xx in range(max(l+2,int((a+c)/2)-3),min(r-2,int((a+c)/2)+4)):
                    x0,y0=xx-tw//2,yy-th//2
                    if x0<0 or y0<0 or x0+tw>image.width or y0+th>image.height:continue
                    patch=ink[y0:y0+th,x0:x0+tw]
                    missing=1-float(patch[foreground].mean())
                    extra=float(patch[background].mean())
                    score=missing+.45*extra
                    if missing<=.12 and extra<=.32 and score<=.18:
                        hits.append({'px':x0+(tw-1)/2,'py':y0+(th-1)/2,'score':score,'shape':entry['shape'],
                                     'label':entry['label'],'marker_bbox':[x0,y0,x0+tw,y0+th]})
        chosen=[]
        for hit in sorted(hits,key=lambda p:p['score']):
            if any(np.hypot(hit['px']-p['px'],hit['py']-p['py'])<size*.65 for p in chosen):continue
            alternatives=[p for p in hits if p['shape']!=hit['shape'] and np.hypot(p['px']-hit['px'],p['py']-hit['py'])<size*.45]
            if alternatives and min(p['score'] for p in alternatives)-hit['score']<.045:
                continue
            chosen.append(hit)
        if not chosen or (hh>size*1.25 and len(chosen)<2):
            ambiguous_regions+=1
        # Long merged components require two independently matched symbols;
        # their middle must not become a fabricated marker centre.
        if hh>size*1.25 and len({p['shape'] for p in chosen})<2:continue
        candidates.extend(chosen)
    series=[]
    for entry in legend['entries']:
        points=[]
        for p in sorted((p for p in candidates if p['shape']==entry['shape']),key=lambda v:(v['px'],v['py'])):
            if any(np.hypot(p['px']-q['px'],p['py']-q['py'])<size*.7 for q in points):continue
            points.append(dict(p,method='automatic_legend_'+entry['shape']+'_marker',review_status='unreviewed'))
        if len(points)<3:continue
        series.append({'label':entry['label'],'color':[20,20,20],'points_px':points,
                       'label_basis':'visible_legend_text_and_'+entry['shape']+'_symbol',
                       'method':'automatic_legend_'+entry['shape']+'_markers','marker_style':entry['shape'],
                       'legend_text_bbox':entry['label_bbox'],'sample_count':len(points),
                       'label_requires_review':True,'value_origin':'image_digitized_approximate'})
    missing=[e['label'] for e in legend['entries'] if e['label'] not in {s['label'] for s in series}]
    skipped=[{'reason':'overlapping_same_ink_symbols','labels':missing}] if missing else []
    if ambiguous_regions:skipped.append({'reason':'unresolved_marker_overlap','labels':[], 'region_count':ambiguous_regions})
    return {'series':series,'legend_bbox':local_legend_bbox,'method':'visible_legend_symbol_matching',
            'skipped_series':skipped,'ambiguous_marker_regions':ambiguous_regions,'is_verified':False,
            'warnings':['按图例的圆点、方块分别定位；只导出可分离的可见标记，未插值或补齐重叠点。',
                        f'仍有 {ambiguous_regions} 处标记区域不能可靠区分，未作为完整双系列读数输出。']}
