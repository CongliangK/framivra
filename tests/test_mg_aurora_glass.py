"""Numerical optics and real PNG tests for analytic refractive aurora cards."""
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('mg_glass',ROOT/'templates/mg-styles/render_aurora_glass.py')
glass=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(glass)
try:
    import numpy as np
    from PIL import Image,ImageChops
except ImportError:
    np=Image=ImageChops=None


def example(name):return json.loads((ROOT/f'templates/mg-styles/examples/aurora-glass-{name}.json').read_text())


@unittest.skipIf(np is None or Image is None,'optional NumPy and Pillow required')
class AuroraOptics(unittest.TestCase):
    def test_nonuniform_refractive_sampling_dispersion_and_true_perspective(self):
        yy,xx=np.mgrid[0:180,0:320];pattern=(((xx//8+yy//8)%2)*180+30).astype('uint8')
        image=Image.fromarray(np.stack([pattern]*3,axis=2))
        box,coords,mask,_=glass.lens_coordinates(320,180,(160,90),72,.25,.8,.4)
        x0,y0,x1,y1=box;grid_y,grid_x=np.mgrid[y0:y1,x0:x1]
        displacement=coords[1][0]-grid_x
        self.assertGreater(float(np.std(displacement[mask>.9])),2)
        self.assertGreater(float(np.max(np.abs(coords[0][0]-coords[2][0]))),.5)
        result=glass.apply_lens(image,(160,90),72,.25,.8,.4)
        self.assertNotEqual(image.tobytes(),result.tobytes())
        pixels=np.asarray(result);self.assertTrue(np.any(pixels[:,:,0]!=pixels[:,:,2]))
        no_dispersion=np.asarray(glass.apply_lens(image,(160,90),72,.25,0,.4))
        self.assertTrue(np.array_equal(no_dispersion[:,:,0],no_dispersion[:,:,2]))
        q=glass.card_corners(960,570,700,440,.18,.1)
        a=(q[1][0]-q[0][0],q[1][1]-q[0][1]);b=(q[2][0]-q[3][0],q[2][1]-q[3][1])
        self.assertGreater(abs(a[0]*b[1]-a[1]*b[0]),1)
        far=glass.card_corners(960,570,700,440,.18,.1,200)
        self.assertLess(math.dist(far[0],far[1]),math.dist(q[0],q[1]))

    def test_real_png_two_semantic_stories_and_reverse_seek(self):
        for name,frames in (('ideas',240),('reflection',180)):
            data=example(name);first=glass.render_aurora_glass_frame(data,25,frames)
            last=glass.render_aurora_glass_frame(data,frames-20,frames)
            self.assertEqual(first.mode,'RGB');self.assertEqual(first.size,(1920,1080))
            self.assertIsNotNone(ImageChops.difference(first.crop((250,300,1700,850)),last.crop((250,300,1700,850))).getbbox())
            self.assertEqual(first.tobytes(),glass.render_aurora_glass_frame(data,25,frames).tobytes())
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'frame.png';last.save(path)
                with Image.open(path) as read:self.assertEqual(last.tobytes(),read.tobytes())

    def test_fractional_values_in_real_ui_draws_and_shortest_hold(self):
        from PIL import ImageDraw
        from unittest.mock import patch
        data=example('ideas')
        for i,chapter in enumerate(data['chapters']):
            chapter.update({'in':i*30,'out':(i+1)*30,'mode':('chat','voice','focus')[i],
                            'value':36.5,'unit':'单位'})
        original=ImageDraw.ImageDraw.text
        calls=[]
        def record(draw,xy,text,*args,**kwargs):
            calls.append(str(text))
            return original(draw,xy,text,*args,**kwargs)
        for i in range(3):
            calls.clear()
            with patch.object(ImageDraw.ImageDraw,'text',record):
                image=glass.render_aurora_glass_frame(data,i*30+29,90)
            self.assertIn('36.5 单位',calls)
            self.assertEqual(image.mode,'RGB')
            self.assertIsNotNone(image.crop((650,480,1300,790)).getbbox())
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'fractional-ui.png';image.save(path)
                with Image.open(path) as read:self.assertEqual(read.tobytes(),image.tobytes())
        self.assertEqual(glass.micro_ui_state(data['chapters'][0],23)['displayed'],36.5)
        self.assertEqual(glass.micro_ui_state(data['chapters'][0],29)['displayed'],36.5)
        data['chapters'][0].update(value=1.25e-8,mode='voice')
        calls.clear()
        with patch.object(ImageDraw.ImageDraw,'text',record):
            glass.render_aurora_glass_frame(data,29,90)
        self.assertIn('1.25e-08 单位',calls)

    def test_schema_glyph_fit_optics_and_window_rejection_before_writes(self):
        for edit in ({'seed':True},{'seed':2**31},{'lens':{'strength':float('nan')}},{'lens':{'radius':True}}):
            data=example('ideas');data.update(edit)
            with self.subTest(edit=edit),self.assertRaises(ValueError):glass.validate_aurora_glass_content(data,240)
        for edit in ({'in':True},{'out':20},{'value':float('inf')},{'mode':'blur'}):
            data=example('ideas');data['chapters'][0].update(edit)
            with self.subTest(edit=edit),self.assertRaises(ValueError):glass.validate_aurora_glass_content(data,240)
        for text in ('满'*70,chr(0x10ffff)):
            data=example('ideas');data['chapters'][0]['detail']=text
            with tempfile.TemporaryDirectory() as tmp:
                target=Path(tmp)/'not-created'
                with self.assertRaises(ValueError):glass.render_aurora_glass_frames(data,240,target)
                self.assertFalse(target.exists())
        shortest=example('ideas')
        for i,c in enumerate(shortest['chapters']):
            c['in']=i*30;c['out']=(i+1)*30;c['value']=100
        shortest['chapters'][0]['mode']='chat';shortest['chapters'][1]['mode']='focus'
        self.assertEqual(glass.micro_ui_state(shortest['chapters'][0],29)['displayed'],100)
        self.assertEqual(glass.micro_ui_state(shortest['chapters'][0],23)['displayed'],100)
        self.assertTrue(all(glass.micro_ui_state(shortest['chapters'][1],59)['enabled']))
        self.assertTrue(all(glass.micro_ui_state(shortest['chapters'][1],53)['enabled']))
        # Real PNGs for legal 30-frame chapters: neither cue ends with an unfinished UI.
        chat_png=glass.render_aurora_glass_frame(shortest,29,90)
        focus_png=glass.render_aurora_glass_frame(shortest,59,90)
        self.assertIsNotNone(ImageChops.difference(chat_png.crop((650,480,1300,790)),focus_png.crop((650,480,1300,790))).getbbox())
        with self.assertRaises(ValueError):glass.render_aurora_glass_frame(example('ideas'),True,240)
        data=example('ideas');base=glass.render_aurora_glass_frame(data,115,240)
        data['chapters'][1]['mode']='voice'
        altered=glass.render_aurora_glass_frame(data,115,240)
        self.assertIsNotNone(ImageChops.difference(base.crop((650,480,1300,790)),altered.crop((650,480,1300,790))).getbbox())


if __name__=='__main__':unittest.main()
