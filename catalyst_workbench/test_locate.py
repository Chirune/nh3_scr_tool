"""Meaningful geometric tests using SYNTHETIC, NON-EXPERIMENTAL PDFs only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import zlib

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from locate import locate_pdf, _safe_pdf_characters, _pdf_form_tree, _captions, _candidate_boxes


WIDTH, HEIGHT = 600, 800


def make_fixture(path):
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    font_ref = writer._add_object(font)
    images = {}

    def text(x,y,value,size=10):
        value=value.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        return f'0 0 0 rg BT /F1 {size} Tf {x} {HEIGHT-y} Td ({value}) Tj ET\n'

    def line(x1,y1,x2,y2,color='0 0 0',thick=1):
        return f'{color} RG {thick} w {x1} {HEIGHT-y1} m {x2} {HEIGHT-y2} l S\n'

    def chart(left,top,right,bottom,color='1 0 0'):
        out=line(left,top,left,bottom)+line(left,bottom,right,bottom)
        out+=line(left+10,bottom-20,right-10,top+20,color,2)
        out+=text(left-12,bottom+12,'0')+text(right-8,bottom+12,'100')
        out+=text(left-23,top+5,'100')+text(left+30,bottom+27,'Temperature (a.u.)')
        return out

    def image_ref(name,pil):
        data=DecodedStreamObject()
        data.set_data(pil.convert('RGB').tobytes())
        data.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Image'),NameObject('/Width'):NumberObject(pil.width),NameObject('/Height'):NumberObject(pil.height),NameObject('/ColorSpace'):NameObject('/DeviceRGB'),NameObject('/BitsPerComponent'):NumberObject(8)})
        images[name]=writer._add_object(data.flate_encode())

    def place(name,x,y,w,h):
        return f'q {w} 0 0 {h} {x} {HEIGHT-y-h} cm /{name} Do Q\n'

    def add(commands):
        page=writer.add_blank_page(WIDTH,HEIGHT)
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font_ref}),NameObject('/XObject'):DictionaryObject({NameObject('/'+name):ref for name,ref in images.items()})})
        stream=DecodedStreamObject();stream.set_data(commands.encode('ascii'))
        page[NameObject('/Contents')]=writer._add_object(stream)
        return page

    bitmap=Image.new('RGB',(440,280),'white');draw=ImageDraw.Draw(bitmap)
    draw.line((15,10,15,265,420,265),fill='black',width=3)
    draw.line((25,240,400,30),fill='blue',width=5)
    image_ref('ChartBitmap',bitmap)
    notice=text(35,30,'SYNTHETIC TEST ONLY - NOT EXPERIMENTAL RESULTS',11)
    commands=notice+chart(60,100,265,240)+place('ChartBitmap',330,100,220,140)
    commands+=text(38,285,'Figure 1. Red conversion curve.',10)
    commands+=text(325,285,'Figure 2. Blue selectivity curve.',10)
    commands+=text(38,310,'Separate left-column explanation.',10)+text(325,310,'Separate right-column explanation.',10)
    add(commands)

    commands=notice+line(65,120,65,350)+line(65,350,330,350)
    for x,y in [(100,280),(180,220),(260,160)]:
        commands+=f'0.1 0.3 0.8 rg {x} {HEIGHT-350} 42 {350-y} re f\n'
    commands+=text(45,395,'Figure 3. Bar chart of synthetic yields.',10)
    add(commands)

    add(notice+chart(75,125,350,310)+text(70,390,'An uncaptioned vector graphic for review.',10))

    scan=Image.new('RGB',(600,800),'white');sd=ImageDraw.Draw(scan)
    sd.text((35,25),'SYNTHETIC SCANNED PAGE - NOT EXPERIMENTAL',fill='black')
    sd.line((60,100,60,320,450,320),fill='black',width=3)
    sd.line((80,290,400,130),fill='red',width=3)
    sd.text((60,355),'Figure 4. Image-only caption has no PDF text layer.',fill='black')
    image_ref('Scan',scan)
    add(place('Scan',0,0,600,800))

    add(notice+text(45,100,'This is a plain text page with no figure or graphics.',12)+text(45,125,'The locator should not invent a precise figure box.',12))
    add(notice+text(45,120,'Figure 5. Caption-only page; figure boundary unavailable.',11))

    commands=notice+chart(65,140,260,290)+chart(340,140,550,290,'0 0 1')
    commands+=text(45,340,'Figure 6. (a) Conversion curve and (b) selectivity curve.',10)
    add(commands)

    with Path(path).open('wb') as handle:
        writer.write(handle)
    bitmap.close();scan.close()
    return {'notice':'人工构造的非实验测试 PDF；不含真实论文或实验结果。',
            'pages':{'1':'two columns: vector Figure 1 and bitmap Figure 2', '2':'vector bar Figure 3',
                     '3':'uncaptioned vector graph', '4':'scanned full-page raster with no text layer',
                     '5':'plain text, no graphics', '6':'caption only; must not invent an exact figure boundary',
                     '7':'two vector panels with one shared caption; keep grouped, not allegedly split'},
            'page_points':[WIDTH,HEIGHT]}


class LocateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.folder=Path(cls.temp.name)
        cls.pdf=cls.folder/'synthetic.pdf';make_fixture(cls.pdf)
        cls.result=locate_pdf(cls.pdf,cls.folder/'located')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_all_pages_rendered_and_source_identity(self):
        self.assertEqual(len(self.result['pages']),7)
        self.assertEqual(self.result['source_sha256'],hashlib.sha256(self.pdf.read_bytes()).hexdigest())
        self.assertFalse(self.result['cancelled'])
        for page in self.result['pages']:
            self.assertTrue(Path(page['image_path']).is_file())
            self.assertGreater(page['width'],0)
            self.assertGreater(page['height'],0)

    def test_two_column_graphics_caption_pairing_and_vector_support(self):
        figures=[f for f in self.result['figures'] if f['page']==1]
        matched={f['figure_label']:f for f in figures if f['locator_status']=='graphics_caption_candidate'}
        self.assertIn('Figure 1',matched)
        self.assertIn('Figure 2',matched)
        left,right=matched['Figure 1'],matched['Figure 2']
        self.assertIn('vector_path',left['graphics_kinds'])
        self.assertIn('image',right['graphics_kinds'])
        self.assertLess(left['bbox'][2],right['bbox'][0])
        self.assertIn('Red conversion',left['caption'])
        self.assertNotIn('Blue selectivity',left['caption'])
        self.assertIn('Blue selectivity',right['caption'])
        self.assertLess(left['bbox'][3],left['caption_bbox'][1])
        self.assertEqual(left['chart_type'],'xy')

    def test_bar_hint_is_not_a_confirmed_type(self):
        figure=next(f for f in self.result['figures'] if f['page']==2 and f['figure_label']=='Figure 3')
        self.assertEqual(figure['chart_type'],'bar')
        self.assertIn('chart_type_hint_requires_review',figure['quality_flags'])
        self.assertEqual(figure['review_status'],'unreviewed')
        self.assertFalse(figure['is_numeric_data_extracted'])

    def test_no_caption_graphics_remain_reviewable(self):
        figures=[f for f in self.result['figures'] if f['page']==3]
        self.assertTrue(figures)
        self.assertTrue(any(f['locator_status']=='graphics_without_caption' for f in figures))
        self.assertTrue(all('caption_missing' in f['quality_flags'] for f in figures))

    def test_scan_and_caption_only_have_honest_full_page_fallback(self):
        for page_number in (4,6):
            figures=[f for f in self.result['figures'] if f['page']==page_number]
            self.assertTrue(figures)
            for figure in figures:
                self.assertEqual(figure['locator_status'],'page_fallback')
                self.assertEqual(figure['bbox'],[0,0,figure['page_width'],figure['page_height']])
                self.assertEqual(figure['bbox_precision'],'not_located')
                self.assertIsNone(figure['graphic_bbox'])
        self.assertEqual(self.result['pages'][3]['text_status'],'no_text_layer')

    def test_text_only_is_not_an_invented_figure(self):
        self.assertEqual([f for f in self.result['figures'] if f['page']==5],[])
        self.assertEqual(self.result['pages'][4]['locator_status'],'no_candidate')

    def test_shared_caption_keeps_multiple_panels_grouped(self):
        figures=[f for f in self.result['figures'] if f['page']==7]
        figure=next(f for f in figures if f['figure_label']=='Figure 6')
        self.assertEqual(figure['locator_status'],'graphics_caption_candidate')
        self.assertIn('multiple_graphics_grouped_under_one_caption',figure['quality_flags'])
        self.assertIn('subfigure_not_separated',figure['quality_flags'])
        self.assertLess(figure['bbox'][0],figure['page_width']*0.2)
        self.assertGreater(figure['bbox'][2],figure['page_width']*0.9)
        self.assertEqual(len(figures),1)

    def test_bboxes_crops_and_evidence_bounds(self):
        for figure in self.result['figures']:
            box=figure['bbox'];width,height=figure['page_width'],figure['page_height']
            self.assertTrue(all(isinstance(v,int) for v in box))
            self.assertTrue(0<=box[0]<box[2]<=width)
            self.assertTrue(0<=box[1]<box[3]<=height)
            with Image.open(figure['crop_path']) as crop:
                self.assertEqual(crop.size,(box[2]-box[0],box[3]-box[1]))
            self.assertEqual(figure['bbox_coordinate_system'],'rendered_page_pixels')
            self.assertEqual(figure['source_sha256'],self.result['source_sha256'])

    def test_cancellation_and_invalid_input(self):
        event=threading.Event();event.set()
        result=locate_pdf(self.pdf,self.folder/'cancelled',cancel_event=event)
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['pages'],[])
        with self.assertRaisesRegex(ValueError,'本地'):
            locate_pdf(self.folder/'missing.pdf',self.folder/'bad')
        bad=self.folder/'not_really.pdf';bad.write_bytes(b'not a PDF')
        with self.assertRaisesRegex(ValueError,'无法打开'):
            locate_pdf(bad,self.folder/'bad')

    def test_pdf_unicode_surrogates_are_repaired_before_ui_or_json(self):
        # Real PDFium boundaries can contain UTF-16 units, isolated surrogates,
        # zero mappings or even invalid values. No surrogate may reach Tk/UTF-8.
        codes = [ord('F'),0xD83D,0xDE00,ord(' '),0xD800,ord('X'),0xDC00,0,0x110000,-1,0x1D6FC]
        characters,spans,flags = _safe_pdf_characters(codes)
        text = ''.join(characters)
        self.assertEqual(characters[1],'\U0001f600')
        self.assertEqual(characters[2],'')
        self.assertEqual(spans[1:3],[2,0])
        self.assertEqual(text.count('\ufffd'),5)
        self.assertEqual(characters[-1],'\U0001d6fc')
        self.assertIn('text_utf16_surrogate_pair_normalized',flags)
        self.assertIn('text_unicode_unmapped_replaced',flags)
        self.assertFalse(any(0xD800<=ord(char)<=0xDFFF for char in text))
        self.assertTrue(text.encode('utf-8'))
        self.assertTrue(json.dumps({'caption':text},ensure_ascii=False).encode('utf-8'))


def make_clipped_form_fixture(path):
    """Two visible plots with hidden embedded old-page text, never real data."""
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')}))
    fonts = DictionaryObject({NameObject('/F1'):font})

    def text(x, y, value):
        return f'BT /F1 11 Tf {x} {y} Td ({value}) Tj ET\n'

    def form(commands, xobjects=None):
        obj = DecodedStreamObject()
        obj.set_data(commands.encode('ascii'))
        obj.update({NameObject('/Type'):NameObject('/XObject'), NameObject('/Subtype'):NameObject('/Form'),
            NameObject('/BBox'):ArrayObject([NumberObject(v) for v in (0,0,600,800)]),
            NameObject('/Resources'):DictionaryObject({NameObject('/Font'):fonts,
                NameObject('/XObject'):DictionaryObject(xobjects or {})})})
        return writer._add_object(obj)

    plot = '0 0 0 RG 100 600 m 100 350 l 450 350 l S\n1 0 0 RG 110 370 m 430 580 l S\n'
    plot += text(105, 625, 'Legend remains visible inside the plotted region')
    plot += text(85, 352, '0') + text(430, 330, '100') + text(180, 310, 'Temperature')
    hidden = text(50, 730, 'HIDDEN OLD PAGE TEXT SHOULD NOT BECOME A CAPTION OR BOUNDARY')
    hidden += text(50, 710, 'Figure 99. Hidden caption from an earlier embedded page.')
    inner = form(hidden + plot)
    # The second rectangle is larger; sequential clipping MUST intersect,
    # never replace the first rectangle, whose height is deliberately negative.
    clips = '50 650 450 -350 re W* n\n0 0 600 800 re W n\n'
    outer = form('q 0.8 0 0 0.8 80 60 cm\n' + clips + '/Inner Do Q\n', {NameObject('/Inner'):inner})
    direct = form('q\n' + clips + hidden + plot + 'Q\n')
    for number, object_ref, caption_y in [(1,outer,275),(2,direct,275)]:
        page = writer.add_blank_page(WIDTH, HEIGHT)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):fonts,
            NameObject('/XObject'):DictionaryObject({NameObject('/Plot'):object_ref})})
        stream = DecodedStreamObject()
        stream.set_data(('/Plot Do\n' + text(90, caption_y, f'Figure {number}. Synthetic conversion curve.')).encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    with Path(path).open('wb') as output:
        writer.write(output)


class BoundaryRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        cls.pdf = cls.folder/'synthetic_clipped_forms.pdf'
        make_clipped_form_fixture(cls.pdf)
        cls.result = locate_pdf(cls.pdf, cls.folder/'located')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_nested_negative_height_clips_intersect_after_parent_transform(self):
        reader = PdfReader(self.pdf)
        try:
            records, _, flags = _pdf_form_tree(reader.pages[0], reader)
            self.assertEqual(flags, [])
            # Expected intersection in page points: 0.8 * [50,300,500,650]
            # followed by translation [80,60]. The larger second clip must
            # not restore the hidden old-page heading and caption.
            self.assertEqual(records[(0,0)]['clip'], [120.0,300.0,480.0,580.0])
        finally:
            reader.close()
        figure = next(f for f in self.result['figures'] if f['figure_label']=='Figure 1')
        self.assertEqual(figure['locator_status'], 'graphics_caption_candidate')
        scale = figure['render_scale']
        self.assertAlmostEqual(figure['graphic_bbox'][1]/scale, HEIGHT-580, delta=1)
        self.assertAlmostEqual(figure['graphic_bbox'][3]/scale, HEIGHT-300, delta=1)

    def test_clip_inside_form_filters_hidden_text_but_keeps_plot_legend(self):
        for page in self.result['pages']:
            self.assertNotIn('HIDDEN', page['text_excerpt'])
            self.assertNotIn('Figure 99', page['text_excerpt'])
            self.assertIn('Legend remains visible', page['text_excerpt'])
            self.assertIn('Temperature', page['text_excerpt'])
            self.assertIn('clipped_form_text_filtered', page['quality_flags'])
        for page_number in (1,2):
            figures = [f for f in self.result['figures'] if f['page']==page_number]
            self.assertEqual(len(figures), 1)
            self.assertEqual(figures[0]['figure_label'], f'Figure {page_number}')
            self.assertNotEqual(figures[0]['locator_status'], 'page_fallback')

    def test_figure_references_in_body_are_not_captions(self):
        lines = [
            {'text':'Figure 2 shows the catalytic activity measured at various temperatures.', 'bbox':[50,80,540,92]},
            {'text':'Figure 3 is discussed in the next section.', 'bbox':[50,110,430,122]},
            {'text':'Figure 4. NO conversion of the different samples.', 'bbox':[50,450,490,462]},
        ]
        self.assertEqual([caption['label'] for caption in _captions(lines,600,1)], ['Figure 4'])

    def test_body_boundary_stops_padding_without_cutting_long_plot_legend(self):
        graph = [80,100,500,350]
        groups = [{'bbox':graph,'objects':[{'bbox':graph,'kind':'vector_path'}]}]
        caption = {'label':'Figure 1','text':'Figure 1. Conversion curves.', 'bbox':[60,395,510,408]}
        lines = [
            {'text':'This is a complete body paragraph line preceding the plotted figure.', 'bbox':[45,79,555,96]},
            {'text':'This exceptionally long legend describes the temperature dependent curve measurements.', 'bbox':[105,110,490,123]},
            {'text':'Temperature (degrees Celsius)', 'bbox':[170,359,420,372]},
        ]
        figure = _candidate_boxes(groups,[caption],600,800,1,lines)[0]
        self.assertGreater(figure['bbox'][1], 96)
        self.assertLessEqual(figure['bbox'][1], graph[1])
        self.assertGreaterEqual(figure['bbox'][3], 372)
        self.assertIn('top_boundary_stopped_at_body_paragraph', figure['flags'])


if __name__=='__main__':
    if '--make-fixtures' in sys.argv:
        folder=Path(__file__).parent/'fixtures';folder.mkdir(exist_ok=True)
        expected=make_fixture(folder/'synthetic_layouts_not_experimental.pdf')
        (folder/'expected_layouts.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2),encoding='utf-8')
        (folder/'README.txt').write_text('本目录仅包含人工构造的定位测试材料，不是真实论文，不含实验结果。\n用途：测试矢量图、位图、两栏图注配对、无图注、扫描页、仅图注页和多子图共用图注。\n',encoding='utf-8')
        print('Synthetic non-experimental fixtures written.')
    else:
        unittest.main()
