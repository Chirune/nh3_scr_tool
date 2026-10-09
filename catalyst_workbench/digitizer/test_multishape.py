"""Known-centre and false-assignment checks for four monochrome series."""
import unittest
from PIL import Image, ImageDraw
from digitizer.auto_multishape import find_multishape_legend, extract_multishape_series


def fixture(overlap=False, duplicate=False):
    image=Image.new('RGB',(740,570),'white')
    draw=ImageDraw.Draw(image)
    plot=[65,155,680,515]
    draw.line((65,155,65,515,680,515),fill='black',width=2)
    shapes=['square','circle','triangle_up','triangle_down']
    colors=[5,50,120,15]
    lines=[];truth={}
    def marker(shape,x,y,r,color):
        color=(color,color,color)
        if shape=='square':draw.rectangle((x-r,y-r,x+r,y+r),fill=color)
        elif shape=='circle':draw.ellipse((x-r,y-r,x+r,y+r),fill=color)
        elif shape=='triangle_up':draw.polygon([(x,y-r),(x-r,y+r),(x+r,y+r)],fill=color)
        else:draw.polygon([(x-r,y-r),(x+r,y-r),(x,y+r)],fill=color)
    for i,(shape,color) in enumerate(zip(shapes,colors)):
        y=35+i*25
        draw.line((432,y,478,y),fill=(color,color,color),width=1)
        marker('circle' if duplicate and i==0 else shape,455,y,6,color)
        lines.append({'text':'Sample '+str(i+1),'bbox':[488,y-7,575,y+7]})
        draw.text((488,y-7),'Sample '+str(i+1),fill='black')
        truth[shape]=[]
        for j,x in enumerate((160,275,390,505)):
            yy=200+i*65+j*4
            if overlap and j==2 and i<2:yy=260
            marker(shape,x,yy,5,color)
            if not (overlap and j==2 and i<2):truth[shape].append((x,yy))
    return image,plot,lines,truth


class MultishapeTests(unittest.TestCase):
    def test_external_legend_and_four_shapes_have_correct_centres(self):
        image,plot,lines,truth=fixture()
        legend=find_multishape_legend(image,[plot],lines)
        self.assertIsNotNone(legend)
        self.assertEqual({v['shape'] for v in legend['entries']},set(truth))
        result=extract_multishape_series(image,plot,legend,lines)
        self.assertEqual(len(result['series']),4)
        for series in result['series']:
            self.assertEqual(len(series['points_px']),4)
            for point,(x,y) in zip(series['points_px'],truth[series['marker_style']]):
                self.assertLessEqual(abs(point['px']-x),1.5)
                self.assertLessEqual(abs(point['py']-y),1.5)
                self.assertGreater(point['py'],plot[1])

    def test_same_shape_twice_is_not_assigned_arbitrarily(self):
        image,plot,lines,truth=fixture(duplicate=True)
        self.assertIsNone(find_multishape_legend(image,[plot],lines))

    def test_missing_legend_text_does_not_invent_sample_names(self):
        image,plot,lines,truth=fixture()
        self.assertIsNone(find_multishape_legend(image,[plot],[]))

    def test_overpainted_symbol_is_not_reconstructed_as_two_points(self):
        image,plot,lines,truth=fixture(overlap=True)
        legend=find_multishape_legend(image,[plot],lines)
        result=extract_multishape_series(image,plot,legend,lines)
        overlap_points=[(s['marker_style'],p) for s in result['series'] for p in s['points_px']
                        if abs(p['px']-390)<6 and abs(p['py']-260)<6]
        self.assertLessEqual(len(overlap_points),1)
        self.assertFalse(any(shape=='square' for shape,p in overlap_points))


if __name__=='__main__':unittest.main()
