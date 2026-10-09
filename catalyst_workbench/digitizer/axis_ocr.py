"""Read small printed ticks in magnified tiles; keep exact original coordinates.

The local OCR engine often drops isolated digits in a vertical column. Put the
actual printed labels side by side for OCR; never synthesize numeric labels.
"""
from __future__ import annotations
from pathlib import Path
import re
import tempfile

import numpy as np
from PIL import Image

try:
    from .auto_axes import _runs, _number
    from .native_ocr import recognize_image
except ImportError:
    from auto_axes import _runs, _number
    from native_ocr import recognize_image


def tick_tiles(image, plot, wide=False):
    l,t,r,b=map(float,plot)
    w,h=r-l,b-t
    # Exclude the axis stroke and keep tick labels, rather than the axis title.
    strips={
        'x':[max(0,int(l-.06*w)),max(0,int(b+.035*h)),min(image.width,int(r+.06*w)),min(image.height,int(b+.16*h))],
        'y':[max(0,int(l-(.18 if wide else .115)*w)),max(0,int(t-.06*h)),max(0,int(l-.025*w)),min(image.height,int(b+.06*h))],
    }
    gray=np.asarray(image.convert('L'))
    tiles=[]
    for axis,box in strips.items():
        x0,y0,x1,y1=box
        ink=gray[y0:y1,x0:x1]<150
        if not ink.size: continue
        if axis=='x':
            # The second text row is often the axis title. It must not connect
            # adjacent tick labels when projecting the strip horizontally.
            bands=[(a,z) for a,z in _runs(ink.sum(axis=1)>=2,gap=2) if z-a>=5]
            if bands:
                first,last=bands[0]
                ink=ink[first:last+1,:];y0+=int(first)
        projection=ink.sum(axis=0 if axis=='x' else 1)
        for a,z in _runs(projection>= (1 if axis=='x' else 2),gap=max(2,round(h*.016)) if axis=='x' else 2):
            region=ink[:,a:z+1] if axis=='x' else ink[a:z+1,:]
            offset=0
            if axis=='y':
                # A vertical title may intrude at the far left of this strip.
                # Printed Y tick labels are the rightmost aligned text group.
                groups=_runs(region.sum(axis=0)>=1,gap=max(4,round((z-a)*.6)) if wide else 4)
                if groups:
                    first,last=groups[-1];offset=int(first)
                    region=region[:,first:last+1]
            yy,xx=np.nonzero(region)
            if not len(xx): continue
            bb=[x0+(a if axis=='x' else offset)+int(xx.min()),y0+(a if axis=='y' else 0)+int(yy.min()),
                x0+(a if axis=='x' else offset)+int(xx.max())+1,y0+(a if axis=='y' else 0)+int(yy.max())+1]
            ww,hh=bb[2]-bb[0],bb[3]-bb[1]
            if not (4<=hh<=max(32,h*.12) and 2<=ww<=max(60,w*.15)): continue
            tiles.append({'axis':axis,'original_bbox':list(map(int,bb))})
    return tiles,strips


def recognize_axis_ticks(image_path, plot, wide=False):
    with Image.open(image_path) as source:
        image=source.convert('RGB')
    tiles,strips=tick_tiles(image,plot,wide=wide)
    if not tiles:
        image.close()
        return {'lines':[],'tiles':[],'strips':strips,'warnings':['未能分离刻度文字。']}
    # Bounded sheets stay below the Windows OCR image-size limit.
    scale=3.0
    x,y,row_height=24,20,0
    for tile in tiles:
        a,b,c,d=tile['original_bbox']
        tw,th=int((c-a)*scale),int((d-b)*scale)
        if x+tw>2200:
            x,y,row_height=24,y+row_height+55,0
        tile['sheet_bbox']=[x,y,x+tw,y+th]
        tile['scale']=scale
        x+=tw+60;row_height=max(row_height,th)
    sheet=Image.new('RGB',(max(t['sheet_bbox'][2] for t in tiles)+24,y+row_height+24),'white')
    for tile in tiles:
        box=tile['sheet_bbox']
        crop=image.crop(tile['original_bbox'])
        enlarged=crop.resize((box[2]-box[0],box[3]-box[1]),Image.Resampling.LANCZOS)
        sheet.paste(enlarged,box[:2]);crop.close();enlarged.close()
    image.close()
    with tempfile.TemporaryDirectory(prefix='axis_tick_ocr_') as temporary:
        path=Path(temporary)/'printed_tick_tiles.png'
        sheet.save(path);sheet.close()
        observed=recognize_image(path,include_rotated=False)
    lines=[]
    for tile in tiles:
        a,b,c,d=tile['sheet_bbox'];words=[]
        for line in observed['lines']:
            for word in line.get('words',[]):
                l,t,r,z=word['bbox']
                if a-2<=l and r<=c+2 and b-2<=t and z<=d+2:
                    words.append(word)
        words.sort(key=lambda v:v['bbox'][0])
        text=' '.join(word['text'] for word in words)
        value=_number(text)
        tile['recognized_text']=text
        if value is None: continue
        # Use OCR's actual glyph box, not the padded tile or a guessed tick.
        bb=[min(v['bbox'][0] for v in words),min(v['bbox'][1] for v in words),
            max(v['bbox'][2] for v in words),max(v['bbox'][3] for v in words)]
        ox,oy=tile['original_bbox'][:2]
        original=[ox+(bb[0]-a)/scale,oy+(bb[1]-b)/scale,ox+(bb[2]-a)/scale,oy+(bb[3]-b)/scale]
        lines.append({'text':text,'bbox':original,'source':'windows_axis_tile_ocr','orientation':0,
                      'requires_review':True,'axis_tile':tile['axis'],'recognized_value':value})
    return {'lines':lines,'tiles':tiles,'strips':strips,'language':observed.get('language'),
            'warnings':['刻度采用原图文字分块放大识别；未按等距关系补写数字。']}


def replace_tick_observations(lines, refined):
    """Keep titles/legends; replace old tick observations inside known strips."""
    if not refined.get('lines'): return list(lines)
    def inside(box):
        cx,cy=(box[0]+box[2])/2,(box[1]+box[3])/2
        return any(a<=cx<=c and b<=cy<=d for a,b,c,d in refined['strips'].values())
    kept=[]
    for line in lines:
        if line.get('orientation',0)!=0:
            kept.append(line);continue
        # The broad numeric strip can also cover an axis title below the
        # first row. Replacing tick OCR must not erase "Temperature (°C)".
        # Such labels are not numeric observations; retain their own boxes.
        if re.search(r'[A-Za-z]{3}', line.get('text','')):
            kept.append(line);continue
        words=[w for w in line.get('words',[]) if not inside(w['bbox'])]
        if inside(line['bbox']): continue
        kept.append(dict(line,words=words))
    return kept+refined['lines']


def recognize_axis_titles(image_path, plot, ticks):
    """Retry the vertical title in isolation, in both text orientations.

    This only contributes text/units, never calibration values. Crowded full
    pages can make local OCR read an upside-down title while missing its
    correctly oriented version.
    """
    with Image.open(image_path) as source:
        image=source.convert('RGB')
    l,t,r,b=plot
    left=max(0,int(l-(r-l)*.30))
    right=int(min((v['bbox'][0] for v in ticks.get('y',[])),default=l-10)-5)
    box=[left,max(0,int(t)),min(image.width,right),min(image.height,int(b)+1)]
    if box[2]-box[0]<8 or box[3]-box[1]<40:
        image.close()
        return {'lines':[]}
    crop=image.crop(box);image.close()
    scale=min(3.0,1900/max(crop.size))
    size=(round(crop.width*scale),round(crop.height*scale))
    sx,sy=size[0]/crop.width,size[1]/crop.height
    with tempfile.TemporaryDirectory(prefix='axis_title_ocr_') as temporary:
        path=Path(temporary)/'axis_title.png'
        resized=crop.resize(size,Image.Resampling.LANCZOS)
        resized.save(path);resized.close();crop.close()
        observed=recognize_image(path,include_rotated=True)
    def mapped(bb):return [box[0]+bb[0]/sx,box[1]+bb[1]/sy,box[0]+bb[2]/sx,box[1]+bb[3]/sy]
    lines=[]
    for line in observed.get('lines',[]):
        if line.get('orientation') not in (-90,90):continue
        item=dict(line,bbox=mapped(line['bbox']),source='windows_axis_title_ocr')
        item['words']=[dict(w,bbox=mapped(w['bbox'])) for w in line.get('words',[])]
        lines.append(item)
    return {'lines':lines,'crop_bbox':box,'scale_x':sx,'scale_y':sy,'language':observed.get('language')}
