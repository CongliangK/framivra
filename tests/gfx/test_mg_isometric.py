"""True-isometric projection, assembly causality and real PNG checks."""
from tests.gfx._mg import load_renderer
import json
import math
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]
iso = load_renderer('mg_iso',ROOT/'templates/mg-styles/styles/isometric/scripts/render_isometric.py')
try:
    from PIL import Image,ImageChops
except ImportError:
    Image=ImageChops=None


def example(name):return json.loads((ROOT/f'templates/mg-styles/styles/isometric/assets/examples/isometric-{name}.json').read_text())


@unittest.skipIf(Image is None,'optional Pillow required')
class IsometricAssembly(unittest.TestCase):
    def test_last_frame_windows_are_really_visible(self):
        from unittest.mock import patch
        node = dict(id='late',label='Late',kind='home',x=1,y=1,height=1.8,base=70,wall=76,roof=82,activate=88,depends_on=[])
        data = dict(title='Lights',subtitle='',grid=[2,2],seed=2,nodes=[dict(node,id='early',x=0,y=0,base=0,wall=6,roof=12,activate=18),node],flows=[])
        with self.assertRaises(ValueError):iso.validate_isometric_content(data,90)
        node.update(base=66,wall=72,roof=78,activate=84)
        data['nodes'][-1]=node
        data = iso.validate_isometric_content(data,90)
        for seed in (-2147483648,-1,0,2,2147483647):
            for height in (.35,1.8):
                for level in range(max(1,int(height/.38))):
                    for column in range(2):
                        self.assertLessEqual(iso.window_on_frame(node,level,column,90,seed),84)
                        states=[f>=iso.window_on_frame(node,level,column,90,seed) for f in range(84,90)]
                        self.assertEqual(states,[True]*6)
        actual=iso.render_isometric_frame(data,89,90)
        with patch.object(iso,'window_on_frame',return_value=999):
            dark=iso.render_isometric_frame(data,89,90)
        self.assertIsNotNone(ImageChops.difference(actual,dark).getbbox())

    def test_true_equal_axes_causality_delayed_caps_and_depth_order(self):
        axes=[iso.project_iso(1,0,0),iso.project_iso(0,1,0),iso.project_iso(0,0,1)]
        for axis in axes:self.assertAlmostEqual(math.hypot(*axis),120)
        self.assertAlmostEqual(axes[0][1]/axes[0][0],math.tan(math.pi/6))
        for name,frames in (('neighborhood',240),('campus',210)):
            data=iso.validate_isometric_content(example(name),frames)
            node=data['nodes'][1]
            state={n['id']:n for n in iso.isometric_state(data,node['roof'],frames)}[node['id']]
            self.assertEqual(state['wall_progress'],1)
            self.assertEqual(state['roof_progress'],0)
            after={n['id']:n for n in iso.isometric_state(data,node['roof']+5,frames)}[node['id']]
            self.assertEqual(after['roof_progress'],1)
            final=iso.isometric_state(data,frames-1,frames)
            self.assertTrue(all(n['active'] for n in final))
            self.assertEqual([(n['x']+n['y'],n['x'],n['id']) for n in final],sorted((n['x']+n['y'],n['x'],n['id']) for n in final))
            ids={n['id']:n for n in data['nodes']}
            self.assertTrue(all(ids[d]['activate']<=n['base'] for n in data['nodes'] for d in n['depends_on']))

    def test_actual_png_two_worlds_reverse_seek_and_input_order_independence(self):
        for name,frames in (('neighborhood',240),('campus',210)):
            data=example(name);final=iso.render_isometric_frame(data,frames-1,frames)
            early=iso.render_isometric_frame(data,0,frames)
            self.assertIsNotNone(ImageChops.difference(early.crop((400,260,1520,900)),final.crop((400,260,1520,900))).getbbox())
            iso.render_isometric_frame(data,frames//2,frames)
            self.assertEqual(final.tobytes(),iso.render_isometric_frame(data,frames-1,frames).tobytes())
            shuffled=dict(data,nodes=list(reversed(data['nodes'])))
            self.assertEqual(final.tobytes(),iso.render_isometric_frame(shuffled,frames-1,frames).tobytes())
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'frame.png';final.save(path)
                with Image.open(path) as read:self.assertEqual(read.tobytes(),final.tobytes())
            changed=dict(data,seed=data.get('seed',0)+1)
            self.assertNotEqual(final.crop((400,260,1520,900)).tobytes(),iso.render_isometric_frame(changed,frames-1,frames).crop((400,260,1520,900)).tobytes())

    def test_preflight_rejects_causal_numeric_font_and_layout_errors(self):
        for mutation in ({'height':float('nan')},{'x':True},{'base':True},{'activate':31},{'depends_on':['missing']}):
            data=example('neighborhood');data['nodes'][0].update(mutation)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):iso.validate_isometric_content(data,240)
        for seed in (True,2**31,-2**31-1):
            data=example('neighborhood');data['seed']=seed
            with self.assertRaises(ValueError):iso.validate_isometric_content(data,240)
        data=example('neighborhood');data['nodes'][0]['label']='满'*40
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'not-created'
            with self.assertRaises(ValueError):iso.render_isometric_frames(data,240,output)
            self.assertFalse(output.exists())
        missing=example('neighborhood');missing['title']='\U0010ffff'
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'not-created'
            with self.assertRaises(ValueError):iso.render_isometric_frames(missing,240,output)
            self.assertFalse(output.exists())
        dejavu=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
        if dejavu.is_file():
            with self.assertRaises(ValueError):iso.validate_isometric_content(example('neighborhood'),240,dejavu)
        with self.assertRaises(ValueError):iso.render_isometric_frame(example('neighborhood'),True,240)


if __name__=='__main__':unittest.main()
