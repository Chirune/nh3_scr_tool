"""Known-value fixtures verify geometry, categorical semantics and safeguards."""
from pathlib import Path
import csv,json,tempfile,unittest
from unittest.mock import patch
from PIL import Image,ImageDraw,ImageFont
from .auto_bars import bar_geometry,read_bars
from .auto_extract import auto_extract_session
from .session import new_session,save_session,load_session
from .chart_catalog import classify_chart
from .auto_trace import trace_series

def fixture(horizontal=False,grouped=False,negative=False,error=False,stacked=False):
    im=Image.new('RGB',(760,590),'white');d=ImageDraw.Draw(im)
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',18);lines=[]
    def text(x,y,s):
        d.text((x,y),s,font=font,fill='black');box=d.textbbox((x,y),s,font=font)
        lines.append({'text':s,'bbox':list(box),'source':'known_fixture_visible_text'})
    box=[140,70,690,460];d.line((140,70,140,460,690,460),fill='black',width=2)
    if horizontal:
        for v in (0,20,40,60,80,100):
            x=140+5.5*v;d.line((x,460,x,466),fill='black',width=2);text(x-9,472,str(v))
        text(350,515,'Conversion (%)')
        for i,v in enumerate([20,55,85]):
            y=135+i*125;text(35,y-11,chr(65+i));d.rectangle((141,y-20,140+5.5*v,y+20),fill=(35,105,180))
    else:
        values=(-40,-20,0,20,40,60) if negative else (0,20,40,60,80,100)
        def yp(v):return 460-(v-values[0])*3.9
        for v in values:
            y=yp(v);d.line((134,y,140,y),fill='black',width=2);text(94,y-10,str(v))
        # Text evidence includes the correct rotated axis title.
        title=Image.new('RGB',(185,24),'white');ImageDraw.Draw(title).text((0,0),'Conversion (%)',font=font,fill='black');im.paste(title.rotate(90,expand=True),(45,200))
        lines.append({'text':'Conversion (%)','bbox':[45,200,69,385],'source':'known_fixture_rotated','orientation':90})
        for i,v in enumerate([-20,25,50] if negative else [20,55,85]):
            x=235+i*170;text(x-6,474,chr(65+i))
            series=[(-23,v,(35,105,180)),(23,v+5,(220,70,50))] if grouped else [(0,v,(35,105,180))]
            for offset,val,color in series:
                a=x+offset-16;c=x+offset+16;y=yp(val);base=yp(0)
                d.rectangle((a,min(y,base),c,max(y,base)-1),fill=color)
                if error:
                    d.line((x+offset,y-15,x+offset,y+15),fill='black',width=2);d.line((x+offset-9,y-15,x+offset+9,y-15),fill='black',width=2)
                if stacked:d.rectangle((a,y-32,c,y-1),fill=(220,70,50))
        if grouped:
            for i,(label,color) in enumerate([('Fresh',(35,105,180)),('Aged',(220,70,50))]):
                y=84+i*31;d.rectangle((160,y,180,y+16),fill=color);text(192,y-3,label)
    return im,lines,box

class BarTests(unittest.TestCase):
    def result(self,**kwargs):
        im,lines,_=fixture(**kwargs);result=read_bars(im,lines,'Comparison of catalyst conversion');json.dumps(result,allow_nan=False);return result,im,lines
    def values(self,result):
        from .digitize import pixel_to_data
        return sorted(pixel_to_data(p['px'],p['py'],result['calibration'])[result['numeric_axis']] for s in result['series'] for p in s['points_px'])
    def test_vertical_known_values(self):
        r,_,_=self.result();self.assertEqual(r['status'],'ready',r);self.assertEqual(r['chart_type'],'bar')
        for a,b in zip(self.values(r),[20,55,85]):self.assertAlmostEqual(a,b,delta=.6)
        self.assertEqual({p['category'] for s in r['series'] for p in s['points_px']},{'A','B','C'})
    def test_grouped_and_error_caps(self):
        r,_,_=self.result(grouped=True,error=True);self.assertEqual(r['status'],'ready',r)
        self.assertEqual(len([p for s in r['series'] for p in s['points_px']]),6)
        self.assertEqual({s['label'] for s in r['series']},{'Fresh','Aged'})
        for a,b in zip(self.values(r),[20,25,55,60,85,90]):self.assertAlmostEqual(a,b,delta=.8)
    def test_horizontal(self):
        r,_,_=self.result(horizontal=True);self.assertEqual(r['status'],'ready',r);self.assertEqual(r['chart_type'],'bar_horizontal')
        for a,b in zip(self.values(r),[20,55,85]):self.assertAlmostEqual(a,b,delta=.6)
    def test_negative_bar(self):
        r,_,_=self.result(negative=True);self.assertEqual(r['status'],'ready',r)
        for a,b in zip(self.values(r),[-20,25,50]):self.assertAlmostEqual(a,b,delta=.6)
    def test_stacked_not_silently_lower_segment(self):
        r,_,_=self.result(stacked=True);self.assertEqual(r['status'],'needs_review');self.assertIn('stacked_rectangles',r['reason_codes'])
    def test_histogram_not_categorical_bars(self):
        im,lines,_=fixture();r=read_bars(im,lines,'Particle size distribution histogram');self.assertNotEqual(r['status'],'ready')
    def test_missing_categories_no_guessed_sample(self):
        im,lines,_=fixture();r=read_bars(im,[v for v in lines if v['text'] not in ('A','B','C')]);self.assertEqual(r['status'],'ready')
        self.assertEqual(r['missing_category_count'],3);self.assertTrue(r['requires_category_review'])
        self.assertTrue(all(not p['category'] for s in r['series'] for p in s['points_px']))
    def test_insufficient_ticks(self):
        im,lines,_=fixture();r=read_bars(im,[v for v in lines if v['text'] not in ('0','20','40','60')]);self.assertNotEqual(r['status'],'ready')
    def test_dual_axes_blocked(self):
        im,lines,_=fixture()
        lines+=[{'text':str(v),'bbox':[696,450-v*3.9,727,470-v*3.9]} for v in (0,20,40,60,80,100)]
        r=read_bars(im,lines);self.assertNotEqual(r['status'],'ready');self.assertIn('second_numeric_axis',r['reason_codes'])
    def test_horizontal_csv_never_exports_category_pixels(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);im,lines,_=fixture(horizontal=True);im.save(td/'horizontal.png')
            session,_=new_session(td/'horizontal.png',output_root=td/'readings');path=save_session(session)
            with patch('digitizer.auto_extract.recognize_image',return_value={'lines':lines}):result=auto_extract_session(path)
            self.assertEqual(result['status'],'success',result)
            with (Path(result['export_dir'])/'读数数据.csv').open(encoding='utf-8-sig') as stream:data=list(csv.DictReader(stream))
            self.assertEqual(len(data),3);self.assertTrue(all(not r['y_value'] and not r['y_unit'] and not r['y_scale'] for r in data))
            self.assertTrue(all(r['value_axis']=='x' and r['category'] in 'ABC' and r['review_status']=='unreviewed' for r in data))
            saved,_=load_session(result['session_path']);self.assertTrue(all(p['bar_bbox'] and p['category_evidence'] for p in saved['points']))
    def test_role_separate_from_geometry(self):
        self.assertIn('model_evaluation',[v['code'] for v in classify_chart('Predicted versus measured NO conversion')['roles']])
        self.assertIn('spectrum',[v['code'] for v in classify_chart('NH3-TPD spectra')['roles']])

    def test_isolated_discs_are_one_measurement_each(self):
        im=Image.new('RGB',(620,430),'white');draw=ImageDraw.Draw(im);plot=[70,40,580,370];rows=[]
        draw.rectangle(plot,outline='black',width=2)
        for i,color in enumerate([(30,110,210),(240,100,20),(170,170,170)]):
            cy=70+25*i;draw.ellipse((400,cy-7,414,cy+7),fill=color)
            rows.append({'text':str(1500+i*500)+' rpm','bbox':[426,cy-9,562,cy+9]})
            for j in range(4):
                x=140+100*j;y=210+36*i-12*j;draw.ellipse((x-8,y-8,x+8,y+8),fill=color)
        cal={'x':{'p1':[70,370],'p2':[580,370],'v1':0,'v2':100,'name':'Temperature','unit':'C','scale':'linear'},
             'y':{'p1':[70,370],'p2':[70,40],'v1':0,'v2':1,'name':'Rate','unit':'mg/s','scale':'linear'}}
        result=trace_series(im,plot,rows,cal)
        self.assertEqual(result['point_count'],12)
        self.assertTrue(all(s.get('marker_style')=='isolated' for s in result['series']))

    def test_gradient_fill_not_several_bars(self):
        im,lines,box=fixture();draw=ImageDraw.Draw(im)
        for i,v in enumerate([20,55,85]):
            center=235+i*170;y=460-v*3.9
            for x in range(center-16,center+17):
                level=45+round(120*(1-abs(x-center)/16))
                draw.line((x,y,x,459),fill=(level,level,level))
        result=read_bars(im,lines)
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(sum(len(s['points_px']) for s in result['series']),3)
        for a,b in zip(self.values(result),[20,55,85]):self.assertAlmostEqual(a,b,delta=.6)

if __name__=='__main__':unittest.main()
