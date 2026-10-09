"""Known-centre checks for same-colour symbols and corrected OCR provenance."""
import unittest
from PIL import Image, ImageDraw
from digitizer.axis_ocr import replace_tick_observations
from digitizer.auto_symbols import find_symbol_legend, extract_symbol_series


def figure_fixture(touching=False):
    image=Image.new('RGB',(540,430),'white');draw=ImageDraw.Draw(image)
    plot=[55,30,510,350];draw.rectangle(plot,outline='black',width=2)
    lines=[{'text':'Adsorption','bbox':[380,270,480,289]},
           {'text':'Desorption','bbox':[380,300,480,319]}]
    draw.line([322,280,369,280],fill='black',width=1)
    draw.ellipse([340,274,352,286],fill='black')
    draw.line([322,310,369,310],fill='black',width=1)
    draw.rectangle([340,304,352,316],fill='black')
    truth={'circle':[],'square':[]}
    for x,y in [(140,120),(230,160),(320,200),(410,230)]:
        gap=13 if touching else 40
        draw.ellipse([x-6,y-6,x+6,y+6],fill='black')
        draw.rectangle([x-6,y-gap-6,x+6,y-gap+6],fill='black')
        truth['circle'].append((x,y));truth['square'].append((x,y-gap))
    return image,plot,lines,truth


class AxisAndSymbolTests(unittest.TestCase):
    def test_tile_retry_replaces_misread_tick_but_keeps_legend_and_vertical_title(self):
        lines=[{'text':'02','bbox':[120,350,140,370]},
               {'text':'Adsorption','bbox':[200,180,290,200]},
               {'text':'Quantity','bbox':[25,90,43,220],'orientation':90}]
        corrected={'lines':[{'text':'0 ． 2','bbox':[120,350,140,370],'source':'windows_axis_tile_ocr'}],
                   'strips':{'x':[50,340,500,380],'y':[10,10,50,340]}}
        result=replace_tick_observations(lines,corrected)
        self.assertEqual([v['text'] for v in result],['Adsorption','Quantity','0 ． 2'])
        self.assertEqual(result[-1]['source'],'windows_axis_tile_ocr')

    def test_broad_x_tick_strip_does_not_delete_axis_title(self):
        title={'text':'Reaction temperature(OC)','bbox':[220,400,420,421]}
        observations=[{'text':'2','bbox':[100,380,110,390]},title]
        refined={'strips':{'x':[50,370,500,440]},'lines':[{'text':'200','bbox':[100,380,124,390]}]}
        result=replace_tick_observations(observations,refined)
        self.assertIn(title,result)
        self.assertNotIn(observations[0],result)

    def check_symbols(self,touching):
        image,plot,lines,truth=figure_fixture(touching)
        legend=find_symbol_legend(image,[plot],lines)
        self.assertIsNotNone(legend)
        self.assertEqual({v['shape'] for v in legend['entries']},{'circle','square'})
        result=extract_symbol_series(image,plot,legend,legend['legend_bbox'],lines)
        self.assertEqual(len(result['series']),2)
        for series in result['series']:
            expected=truth[series['marker_style']]
            self.assertEqual(len(series['points_px']),len(expected))
            for p,(x,y) in zip(series['points_px'],expected):
                self.assertLessEqual(abs(p['px']-x),1.5)
                self.assertLessEqual(abs(p['py']-y),1.5)
                self.assertEqual(p['review_status'],'unreviewed')
                self.assertFalse(320<p['px']<480 and 265<p['py']<325)

    def test_shared_black_ink_does_not_merge_circle_and_square_series(self):
        self.check_symbols(False)

    def test_touching_symbols_are_located_from_two_visible_shapes(self):
        self.check_symbols(True)

    def test_unlabelled_black_strokes_do_not_invent_legend_assignments(self):
        image,plot,lines,truth=figure_fixture()
        self.assertIsNone(find_symbol_legend(image,[plot],[]))


if __name__=='__main__':unittest.main()
