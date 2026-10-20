"""Implicit-surface topology and actual PNG tests for the liquid template."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location('mg_liquid',Path(__file__).resolve().parents[1]/'templates/mg-styles/render_liquid.py')
liquid=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(liquid)
try:
    import numpy as np
    from PIL import Image
except ImportError:
    np=Image=None


def components(mask):
    """Four-connected pixel components; discard contour specks smaller than 5px."""
    a=np.array(mask,dtype=bool); sizes=[]
    for y,x in zip(*np.where(a)):
        if not a[y,x]: continue
        pending=[(y,x)];a[y,x]=False;count=0
        while pending:
            yy,xx=pending.pop();count+=1
            for dy,dx in ((1,0),(-1,0),(0,1),(0,-1)):
                ny,nx=yy+dy,xx+dx
                if 0<=ny<a.shape[0] and 0<=nx<a.shape[1] and a[ny,nx]:
                    a[ny,nx]=False;pending.append((ny,nx))
        if count>4:sizes.append(count)
    return sorted(sizes,reverse=True)


def second_content():
    edges=[0,24,66,108,144]
    return {'title':'汇聚成新的可能','subtitle':'先相遇，再连接，最后各自出发',
            'palette':['#edf8fa','#192c37','#277e9b','#92dbcf'],
            'stages':[{'in':edges[i],'out':edges[i+1],'mode':mode,
                       'label':['相遇','连接','分离','一起向前'][i],'amount':1.1}
                      for i,mode in enumerate(liquid.MODES)]}


@unittest.skipIf(np is None or Image is None,'optional NumPy and Pillow required')
class LiquidTemplate(unittest.TestCase):
    def test_neck_topology_and_delayed_droplets_for_two_timings(self):
        for data,frames in (({},120),(second_content(),144)):
            with self.subTest(frames=frames):
                normalized=liquid._normalize(data,frames)
                merge=normalized['stages'][1];split=normalized['stages'][2]
                start=merge['in'];neck=start+round((merge['out']-start-1)*.4)
                self.assertEqual(len(components(liquid.liquid_mask(data,start,frames))),2)
                mask=liquid.liquid_mask(data,neck,frames)
                self.assertEqual(len(components(mask)),1)
                self.assertGreater(np.array(mask)[:,240].sum(),0)
                # No individual circle reaches the centre: this is an implicit bridge.
                _,stage,u=liquid._state(data,neck,frames)
                self.assertTrue(all(abs(x-960)>r for x,y,r in liquid._balls(stage,u,0)))
                pinch=split['in']+round((split['out']-split['in']-1)*.54)
                droplets=split['in']+round((split['out']-split['in']-1)*.8)
                self.assertEqual(len(components(liquid.liquid_mask(data,pinch,frames))),2)
                self.assertGreaterEqual(len(components(liquid.liquid_mask(data,droplets,frames))),3)
                self.assertEqual(len(components(liquid.liquid_mask(data,frames-1,frames))),1)

    def test_real_png_seek_and_masked_resolution(self):
        for data,frames in (({},120),(second_content(),144)):
            image=liquid.render_liquid_frame(data,frames-1,frames)
            liquid.render_liquid_frame(data,2,frames)
            self.assertEqual(image.mode,'RGB')
            self.assertEqual(image.size,(1920,1080))
            self.assertEqual(image.tobytes(),liquid.render_liquid_frame(data,frames-1,frames).tobytes())
            changed=dict(data);base=liquid._normalize(data,frames)
            changed['stages']=[dict(stage) for stage in base['stages']]
            changed['stages'][-1]['label']=''
            no_label=liquid.render_liquid_frame(changed,frames-1,frames)
            # The resolve label is revealed inside the liquid, not only in the footer.
            self.assertNotEqual(image.crop((600,420,1320,660)).tobytes(),no_label.crop((600,420,1320,660)).tobytes())
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'frame.png';image.save(path)
                with Image.open(path) as read:self.assertEqual(read.tobytes(),image.tobytes())

    def test_small_amount_resolve_fits_full_glyph_rectangle_inside_real_surface(self):
        from PIL import ImageDraw,ImageChops
        data=liquid._normalize({},120)
        for stage in data['stages']:stage['amount']=.65
        data['stages'][-1]['label']='让每一个小小的想法都汇聚成我们共同期待的美好未来'
        font_file=liquid._font_file(data)
        old_font=liquid._fit_font(data['stages'][-1]['label'],670,72,font_file)
        font=liquid._resolve_font(data,120,font_file)
        draw=ImageDraw.Draw(Image.new('L',(1920,1080)))
        box=draw.textbbox((960,570),data['stages'][-1]['label'],font=font,anchor='mm')
        padded=(box[0]-10,box[1]-10,box[2]+10,box[3]+10)
        surface=liquid._surface_mask(data,119,120)
        self.assertGreaterEqual(surface.crop(padded).getextrema()[0],250)
        self.assertLess(font.size,old_font.size)
        # The rendered letter pixels are complete, rather than merely passing a width check.
        glyph=Image.new('L',(1920,1080))
        ImageDraw.Draw(glyph).text((960,570),data['stages'][-1]['label'],font=font,fill=255,anchor='mm')
        glyph_pixels=np.array(glyph)>0
        self.assertGreater(glyph_pixels.sum(),0)
        self.assertTrue(np.all(np.array(surface)[glyph_pixels]>=250))
        actual=liquid.render_liquid_frame(data,119,120)
        expected=liquid.render_liquid_frame(data,118,120)
        liquid.render_liquid_frame(data,0,120)
        self.assertEqual(actual.tobytes(),liquid.render_liquid_frame(data,119,120).tobytes())
        self.assertEqual(actual.size,(1920,1080))
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'frame.png';actual.save(target)
            with Image.open(target) as reopened:self.assertIsNone(ImageChops.difference(actual,reopened).getbbox())
        data['stages'][-1]['label']='满'*80
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'not-created'
            with self.assertRaisesRegex(ValueError,'final liquid mask'):
                liquid.render_liquid_frames(data,120,output)
            self.assertFalse(output.exists())

    def test_schema_rejects_invalid_windows_and_output(self):
        for data,frames in (({},True),({},59),({'stages':[]},120),({'seed':False},120)):
            with self.subTest(data=data,frames=frames),self.assertRaises(ValueError):
                liquid.render_liquid_frame(data,0,frames)
        for frame in (-1,120,1.5):
            with self.assertRaises(ValueError):liquid.render_liquid_frame({},frame,120)
        mixed=liquid._normalize({},120)
        mixed['stages'][1]['amount']=1.1
        with self.assertRaises(ValueError):liquid.validate_liquid_content(mixed,120)
        first=np.array(liquid.liquid_mask({'seed':3},45,120))
        second=np.array(liquid.liquid_mask({'seed':7},45,120))
        self.assertFalse(np.array_equal(first,second))
        maximum=liquid._normalize({},120)
        for stage in maximum['stages']:stage['amount']=1.25
        # At the largest supported amount, the contour stays below the subtitle band.
        self.assertGreaterEqual(liquid.liquid_mask(maximum,119,120).getbbox()[1]*4,250)
        dejavu=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
        if dejavu.is_file():
            with self.assertRaises(ValueError):liquid.validate_liquid_content(second_content(),144,dejavu)
        overflowing=second_content()
        overflowing['stages'][-1]['label']='满'*80
        with self.assertRaises(ValueError):liquid.validate_liquid_content(overflowing,144)
        with self.assertRaises(ValueError):liquid.render_liquid_frame(overflowing,0,144)
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'not-created'
            with self.assertRaises(ValueError):liquid.render_liquid_frames(overflowing,144,target)
            self.assertFalse(target.exists())
        with tempfile.TemporaryDirectory() as tmp,self.assertRaises(ValueError):
            liquid.render_liquid_frames({},120,tmp)


if __name__=='__main__':unittest.main()
