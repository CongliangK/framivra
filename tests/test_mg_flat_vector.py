"""Real-Pillow verification of flat-vector cause graphs and content reordering."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('mg_flat_vector',ROOT/'templates/mg-styles/render_flat_vector.py')
flat=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(flat)
FONT=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
try:
    from PIL import Image,ImageChops
except ImportError:
    Image=None


def content():
    return {'title':'从观察到行动','subtitle':'纯色图形解释因果，而非装饰数据',
            'origin':{'label':'发现问题','detail':'提出一个可验证的假设'},
            'cards':[{'id':'learn','label':'先观察','value':12,'detail':'收集真实反馈'},
                     {'id':'make','label':'再行动','value':3,'detail':'用原型验证想法'}],
            'links':[{'from':'origin','to':'learn'},{'from':'learn','to':'make'}],
            'final_order':['make','learn'],
            'stages':[{'mode':'question','in':0,'out':14,'text':'什么问题值得解决？'},
                      {'mode':'build','in':14,'out':58,'text':'证据推动行动'},
                      {'mode':'resolve','in':58,'out':96,'text':'把结果整理成下一步'}], 'seed':-19}


@unittest.skipUnless(Image is not None and FONT.is_file(),'Pillow and separately licensed CJK font required')
class FlatVector(unittest.TestCase):
    def frame(self,data,frame):return flat.render_flat_vector_frame(data,frame,96,FONT)

    def test_semantic_links_and_reorder_change_real_pixels(self):
        data=content();base=self.frame(data,50)
        changed=copy.deepcopy(data);changed['links'][1]['from']='origin'
        arrows=self.frame(changed,50)
        self.assertIsNotNone(ImageChops.difference(base.crop((400,320,1500,900)),arrows.crop((400,320,1500,900))).getbbox())
        changed=copy.deepcopy(data);changed['final_order'].reverse()
        final=self.frame(data,89);reordered=self.frame(changed,89)
        self.assertNotEqual(final.tobytes(),reordered.tobytes())
        changed=copy.deepcopy(data);changed['cards'][0]['label']='新的中文内容'
        self.assertNotEqual(final.tobytes(),self.frame(changed,89).tobytes())
        self.assertEqual(final.mode,'RGB');self.assertEqual(final.size,(1920,1080))

    def test_anticipation_and_wipe_have_pure_seekable_geometry(self):
        data=content();first=self.frame(data,4).tobytes()
        self.frame(data,89);self.frame(data,0)
        self.assertEqual(first,self.frame(data,4).tobytes())
        self.assertNotEqual(self.frame(data,2).tobytes(),self.frame(data,8).tobytes())
        # The carrier shape actually covers the old scene, then reveals the new one.
        covered=self.frame(data,63)
        self.assertEqual(covered.getpixel((1800,100)),tuple(bytes.fromhex(flat.PALETTE[2][1:])))
        self.assertNotEqual(covered.tobytes(),self.frame(data,89).tobytes())

    def test_invalid_semantics_font_and_bounds_rejected_before_output(self):
        invalid=[]
        for seed in (True,2**31,-2**31-1,1.5):
            data=content();data['seed']=seed;invalid.append(data)
        for value in (float('inf'),float('nan'),True):
            data=content();data['cards'][0]['value']=value;invalid.append(data)
        data=content();data['final_order']=['learn','learn'];invalid.append(data)
        data=content();data['links'][0]['from']='make';invalid.append(data)
        data=content();data['links']=data['links'][:1];invalid.append(data)
        data=content();data['stages'][1]['in']=15;invalid.append(data)
        data=content();data['stages'][2]['out']=89;invalid.append(data)
        data=content();data['title']='坏\n标题';invalid.append(data)
        data=content();data['title']='\U0010ffff';invalid.append(data)
        data=content();data['cards'][0]['detail']='界'*80;invalid.append(data)
        with tempfile.TemporaryDirectory() as temp:
            for i,data in enumerate(invalid):
                with self.subTest(i=i):
                    output=Path(temp)/str(i)
                    with self.assertRaises(ValueError):flat.render_flat_vector_frames(data,96,output,FONT)
                    self.assertFalse(output.exists())
        for seed in (-(2**31),2**31-1):
            data=content();data['seed']=seed;flat.validate_flat_vector_content(data,96,FONT)
        with self.assertRaises(ValueError):self.frame(content(),True)
        with self.assertRaises(ValueError):flat.validate_flat_vector_content(content(),96,'/tmp/nonexistent-flat-font.ttf')

    def test_sequence_matches_direct_seek_and_evidence(self):
        data=content()
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'frames'
            report=flat.render_flat_vector_frames(data,96,output,FONT)
            self.assertEqual(len(list(output.glob('f*.png'))),96)
            self.assertEqual(report['final_order'],data['final_order'])
            self.assertEqual(report,json.loads((output/'evidence.json').read_text()))
            for frame in (0,14,52,89):
                with Image.open(output/f'f{frame:04d}.png') as actual:
                    self.assertEqual(actual.tobytes(),self.frame(data,frame).tobytes())
            with self.assertRaises(ValueError):flat.render_flat_vector_frames(data,96,output,FONT)

    def test_shortest_build_settles_last_card_and_renders_its_text(self):
        data=json.loads((ROOT/'templates/mg-styles/examples/flat-vector-energy.json').read_text())
        # Six cards settle at local 63; locals 63..68 are six full readable frames.
        data['stages']=[{'mode':'question','in':0,'out':14,'text':'提出问题'},
                        {'mode':'build','in':14,'out':83,'text':'逐项建立因果'},
                        {'mode':'resolve','in':83,'out':121,'text':'完整呈现每项结果'}]
        bad=copy.deepcopy(data);bad['stages'][1]['out']=82;bad['stages'][2]['in']=82
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'too-short'
            with self.assertRaises(ValueError):flat.render_flat_vector_frames(bad,121,output,FONT)
            self.assertFalse(output.exists())
        flat.validate_flat_vector_content(data,121,FONT)
        changed=copy.deepcopy(data);changed['cards'][-1]['label']='终态文字在这里'
        for frame in (77,82,120):
            with self.subTest(frame=frame):
                first=flat.render_flat_vector_frame(data,frame,121,FONT)
                second=flat.render_flat_vector_frame(changed,frame,121,FONT)
                # Only the final card label changed; verify ink in its actual
                # lower-right caption region at build end AND resolved final.
                region=(1280,670,1820,745)
                self.assertIsNotNone(ImageChops.difference(first.crop(region),second.crop(region)).getbbox())
        for local in range(63,69):
            self.assertEqual(flat._build_progress(local,5),1)
        for local in (63,68):
            for field,replacement,region in (
                    ('value',987,(1280,720,1820,805)),
                    ('detail','完整细节可见',(1280,810,1820,875))):
                changed=copy.deepcopy(data);changed['cards'][-1][field]=replacement
                first=flat.render_flat_vector_frame(data,14+local,121,FONT)
                second=flat.render_flat_vector_frame(changed,14+local,121,FONT)
                self.assertIsNotNone(ImageChops.difference(first.crop(region),second.crop(region)).getbbox())
        # A settled scene remains byte-identical over the full pre-transition hold.
        self.assertEqual(flat.render_flat_vector_frame(data,77,121,FONT).tobytes(),
                         flat.render_flat_vector_frame(data,82,121,FONT).tobytes())
        # Already rendered examples have longer windows and remain valid.
        for name,frames in (('flat-vector-ideas.json',180),('flat-vector-energy.json',240)):
            example=json.loads((ROOT/'templates/mg-styles/examples'/name).read_text())
            flat.validate_flat_vector_content(example,frames,FONT)


    def test_two_examples_replace_causal_structure_and_card_count(self):
        images=[]
        for name,frames,count in (('flat-vector-ideas.json',180,3),('flat-vector-energy.json',240,6)):
            data=json.loads((ROOT/'templates/mg-styles/examples'/name).read_text())
            checked=flat.validate_flat_vector_content(data,frames,FONT)
            self.assertEqual(len(checked['cards']),count)
            images.append(flat.render_flat_vector_frame(data,frames-1,frames,FONT).tobytes())
        self.assertNotEqual(*images)


if __name__=='__main__':unittest.main()
