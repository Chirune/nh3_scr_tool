"""Known-value checks for continuous sampling, inset rejection and panel provenance."""
from pathlib import Path
import csv,json,sys,tempfile,unittest
from unittest.mock import patch
import numpy as np
from PIL import Image,ImageDraw
sys.path.append(str(Path(__file__).resolve().parent.parent))
from digitizer.auto_trace import trace_series, normalize_legend
from digitizer.auto_panels import independent_frames,panel_specs,create_panel_session,read_panels
from digitizer.session import new_session,load_session,save_session,set_calibration,add_points,export_session
from digitizer.digitize import pixel_to_data
from digitizer.auto_axes import _pdf_words


def fixture(inset=False):
    image=Image.new('RGB',(520,440),'white');d=ImageDraw.Draw(image)
    plot=[60,30,480,350];d.rectangle(plot,outline='black',width=2)
    functions={'0.27':lambda x:170+.12*(x-90),'0.45':lambda x:270-.10*(x-90)}
    rows=[]
    for i,(label,fn) in enumerate(functions.items()):
        colour=('blue','red')[i]
        d.line([(x,round(fn(x))) for x in range(90,440)],fill=colour,width=2)
        y=290+22*i;d.line([355,y,390,y],fill=colour,width=2)
        rows.append({'text':label,'bbox':[399,y-7,445,y+7]})
    if inset:
        d.rectangle([320,60,450,150],outline='black',width=2)
        d.line([325,80,445,135],fill='blue',width=3)
    cal={'x':{'p1':[60,350],'p2':[480,350],'v1':0,'v2':10,'scale':'linear','name':'Temperature','unit':'°C'},
         'y':{'p1':[60,350],'p2':[60,30],'v1':0,'v2':100,'scale':'linear','name':'Conversion','unit':'%'}}
    return image,plot,rows,cal,functions


class ContinuousPanelTests(unittest.TestCase):
    def test_line_sampling_matches_known_values_and_excludes_numeric_legend(self):
        image,plot,rows,cal,truth=fixture()
        result=trace_series(image,plot,rows,cal)
        self.assertEqual({s['label'] for s in result['series']},set(truth))
        for series in result['series']:
            self.assertGreater(len(series['points_px']),80)
            for p in series['points_px']:
                self.assertLess(abs(p['py']-truth[series['label']](p['px'])),1.5)
                self.assertEqual(p['method'],'automatic_curve_sample')
        self.assertFalse(result['is_verified'])

    def test_inset_is_not_a_separate_panel_or_main_curve_samples(self):
        image,plot,rows,cal,truth=fixture(True)
        self.assertEqual(len(independent_frames(image)),1)
        result=trace_series(image,plot,rows,cal)
        for series in result['series']:
            for p in series['points_px']:
                self.assertLess(abs(p['py']-truth[series['label']](p['px'])),1.5)
                self.assertFalse(320<p['px']<450 and 60<p['py']<150)

    def test_isothermal_stops_at_printed_temperature_anchors(self):
        image,plot,rows,cal,truth=fixture()
        rows.append({'text':'isothermal','bbox':[400,360,500,385]})
        cal['x']['p1']=[120,350];cal['x']['p2']=[300,350]
        result=trace_series(image,plot,rows,cal)
        self.assertTrue(result['series'])
        self.assertTrue(all(120<=p['px']<=300 for s in result['series'] for p in s['points_px']))
        self.assertTrue(any('等温' in w for w in result['warnings']))

    def test_thick_same_colour_branch_does_not_leave_a_false_unique_sample(self):
        image,plot,rows,cal,truth=fixture()
        # One thin stroke and one thick same-colour region coexist in these
        # columns. Discarding the thick region first would falsely make the
        # thin stroke look like the only possible branch.
        ImageDraw.Draw(image).rectangle([185,65,225,110],fill='blue')
        result=trace_series(image,plot,rows,cal)
        blue=next(s for s in result['series'] if s['label']=='0.27')
        self.assertFalse(any(185<=p['px']<=225 for p in blue['points_px']))
        self.assertGreater(blue['ambiguous_columns'],0)
        before=[p for p in blue['points_px'] if p['px']<185][-1]
        after=[p for p in blue['points_px'] if p['px']>225][0]
        self.assertNotEqual(before['trace_id'],after['trace_id'])

    def test_panel_labels_do_not_use_letters_inside_axis_title(self):
        image=Image.new('RGB',(900,700),'white')
        frames=[[100,30,350,250],[530,30,780,250]]
        lines=[{'text':'p','bbox':[225,280,237,299]}, {'text':'(a)','bbox':[219,310,244,330]},
               {'text':'p','bbox':[655,280,667,299]}, {'text':'(b)','bbox':[649,310,674,330]}]
        specs=panel_specs(image,frames,lines)
        self.assertEqual([s['label'] for s in specs],['(a)','(b)'])

    def test_panel_row_order_and_titles_survive_small_axis_height_offsets(self):
        image=Image.new('RGB',(900,720),'white')
        draw=ImageDraw.Draw(image)
        for box in ([100,70,380,290],[530,72,810,290],[100,430,380,670],[530,428,810,670]):
            draw.rectangle(box,outline='black',width=2)
        frames=independent_frames(image)
        self.assertEqual([b[0]<400 for b in frames],[True,False,True,False])
        specs=panel_specs(image,frames,[{'text':'Sample A','bbox':[170,44,260,62]},
                                       {'text':'Sample B','bbox':[600,44,690,62]}])
        self.assertLess(specs[0]['bbox'][1],44)
        self.assertEqual([s['sample_label'] for s in specs[:2]],['Sample A','Sample B'])

    def test_child_crop_preserves_page_transform_and_sample_export_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);image,plot,rows,cal,truth=fixture();image.save(folder/'input.png')
            parent,original=new_session(folder/'input.png',output_root=folder/'readings');original.close()
            parent['figure_label']='Figure 1'
            parent['source_metadata']['figure_context']={'crop_to_page':{'offset_x':100,'offset_y':200,'scale_x':.5,'scale_y':.75}}
            child,path=create_panel_session(parent,image,{'label':'(b)','bbox':[20,10,500,400]})
            child['roi']=[0,0,480,390];set_calibration(child,cal)
            add_points(child,[{'px':100,'py':150,'method':'automatic_curve_sample'}],'test')
            export_session(child)
            with (Path(child['last_export_dir'])/'读数数据.csv').open(encoding='utf-8-sig',newline='') as stream:
                data=list(csv.DictReader(stream))
            self.assertEqual(data[0]['figure_label'],'Figure 1(b)')
            self.assertAlmostEqual(float(data[0]['page_pixel_x']),160)
            self.assertAlmostEqual(float(data[0]['page_pixel_y']),320)
            self.assertEqual(data[0]['value_origin'],'image_curve_samples_approximate')
            self.assertEqual(data[0]['review_status'],'unreviewed')

    def test_pdf_ticks_remain_separate_but_narrow_one_is_joined(self):
        glyphs=[('1',[0,0,4,12]),('0',[8,0,16,12]),('0',[18,0,26,12]),
                ('2',[36,0,44,12]),('0',[46,0,54,12]),('0',[56,0,64,12])]
        self.assertEqual([w['text'] for w in _pdf_words(glyphs)],['100','200'])

    def test_decimal_cleanup_preserves_non_numeric_punctuation(self):
        self.assertEqual(normalize_legend('0 ， 27'),'0.27')
        self.assertEqual(normalize_legend('NOx, Cu/SSZ-13'),'NOx, Cu/SSZ-13')

if __name__=='__main__':unittest.main()
