"""Real Pillow pixels and semantic-state checks for the optional CPU HUD path."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('mg_hud',ROOT/'templates/mg-styles/render_hud.py')
hud=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(hud)
FONT=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
try:
    from PIL import Image,ImageChops
except ImportError:
    Image=None


def content():
    return {'title':'搜寻到确认','subtitle':'可换中文状态与轨迹',
            'target':{'name':'信标七号','points':[{'frame':0,'x':.35,'y':.4},{'frame':30,'x':.6,'y':.6},{'frame':59,'x':.5,'y':.5}]},
            'stages':[{'mode':'search','in':0,'out':20,'text':'搜索信号'},
                      {'mode':'detect','in':20,'out':40,'text':'发现目标'},
                      {'mode':'confirm','in':40,'out':60,'text':'位置确认'}],
            'telemetry':[{'label':'高度','value':2345,'unit':'m'},
                         {'label':'信号','value':-67.5,'unit':'dBm'},
                         {'label':'精度','value':99.2,'unit':'%'}], 'seed':-7}


@unittest.skipUnless(Image is not None and FONT.is_file(),'Pillow and separately licensed CJK font required')
class SemanticHUD(unittest.TestCase):
    def test_stages_trajectory_and_final_values_converge(self):
        data=content(); prepared=hud._prepare(data,60,FONT)
        for frame,mode in ((0,'search'),(19,'search'),(20,'detect'),(39,'detect'),(40,'confirm'),(59,'confirm')):
            with self.subTest(frame=frame):
                self.assertEqual(hud._state(prepared,frame)[0]['mode'],mode)
        self.assertEqual(hud._state(prepared,30)[2],(.6*1920,.6*1080))
        # Every final datum is the supplied value, not a deterministic random number.
        expected=['2345','-67.5','99.2']
        self.assertEqual(hud._state(prepared,40)[3],expected)
        self.assertEqual(hud._state(prepared,59)[3],expected)
        self.assertNotEqual(hud._state(prepared,38)[3],expected)

    def test_real_pixels_change_for_trajectory_state_and_cjk_content(self):
        data=content(); original=hud.render_hud_frame(data,30,60,FONT)
        self.assertEqual(original.mode,'RGB');self.assertEqual(original.size,(1920,1080))
        changed=copy.deepcopy(data)
        changed['target']['points'][1].update(x=.4,y=.4)
        moved=hud.render_hud_frame(changed,30,60,FONT)
        center=(500,270,1420,880)
        self.assertIsNotNone(ImageChops.difference(original.crop(center),moved.crop(center)).getbbox())
        changed=copy.deepcopy(data);changed['stages'][1]['text']='检测管线'
        status=hud.render_hud_frame(changed,30,60,FONT)
        self.assertIsNotNone(ImageChops.difference(original.crop((0,950,1920,1060)),status.crop((0,950,1920,1060))).getbbox())
        confirmed=hud.render_hud_frame(data,59,60,FONT)
        changed=copy.deepcopy(data);changed['telemetry'][1]['value']=-43.2
        value=hud.render_hud_frame(changed,59,60,FONT)
        self.assertIsNotNone(ImageChops.difference(confirmed.crop((1420,380,1880,500)),value.crop((1420,380,1880,500))).getbbox())

    def test_seek_order_is_real_pixel_deterministic(self):
        data=content(); first=hud.render_hud_frame(data,28,60,FONT).tobytes()
        for frame in (59,0,39,40):hud.render_hud_frame(data,frame,60,FONT)
        self.assertEqual(first,hud.render_hud_frame(data,28,60,FONT).tobytes())

    def test_preflight_rejects_invalid_windows_coordinates_seed_and_fonts(self):
        invalid=[]
        for seed in (True,2**31,-2**31-1,1.5):
            data=content();data['seed']=seed;invalid.append(data)
        for value in (float('nan'),float('inf'),.99,True):
            data=content();data['target']['points'][1]['x']=value;invalid.append(data)
        data=content();data['target']['points'][1]['frame']=0;invalid.append(data)
        data=content();data['stages'][1]['in']=21;invalid.append(data)
        data=content();data['stages'][2]['out']=59;invalid.append(data)
        data=content();data['telemetry'][0]['value']=float('nan');invalid.append(data)
        data=content();data['telemetry']=[];invalid.append(data)
        data=content();data['title']='坏\n标题';invalid.append(data)
        data=content();data['title']='\U0010ffff';invalid.append(data)
        data=content();data['telemetry'][0]['label']='界'*60;invalid.append(data)
        with tempfile.TemporaryDirectory() as temp:
            for i,data in enumerate(invalid):
                with self.subTest(i=i):
                    output=Path(temp)/str(i)
                    with self.assertRaises(ValueError):hud.render_hud_frames(data,60,output,FONT)
                    self.assertFalse(output.exists())
        for seed in (-(2**31),2**31-1):
            data=content();data['seed']=seed
            hud.validate_hud_content(data,60,FONT)
        with self.assertRaises(ValueError):hud.validate_hud_content(content(),60,'/tmp/missing-hud-font.ttf')

    def test_sequence_evidence_and_direct_seek_match(self):
        data=content()
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'hud'
            report=hud.render_hud_frames(data,60,output,FONT)
            self.assertEqual(len(list(output.glob('f*.png'))),60)
            self.assertEqual(report['final_values'],['2345','-67.5','99.2'])
            self.assertEqual(report,json.loads((output/'evidence.json').read_text()))
            for frame in (0,20,40,59):
                with Image.open(output/f'f{frame:04d}.png') as actual:
                    self.assertEqual(actual.tobytes(),hud.render_hud_frame(data,frame,60,FONT).tobytes())
            with self.assertRaises(ValueError):hud.render_hud_frames(data,60,output,FONT)

    def test_both_examples_have_different_semantic_inputs(self):
        examples=[]
        for name,frames in (('hud-rescue.json',180),('hud-water.json',240)):
            data=json.loads((ROOT/'templates/mg-styles/examples'/name).read_text())
            hud.validate_hud_content(data,frames,FONT)
            prepared=hud._prepare(data,frames,FONT)
            self.assertEqual(hud._state(prepared,frames-1)[3],
                             [hud._number(item['value']) for item in data['telemetry']])
            examples.append(hud.render_hud_frame(data,frames-1,frames,FONT).tobytes())
        self.assertNotEqual(*examples)


if __name__=='__main__':unittest.main()
