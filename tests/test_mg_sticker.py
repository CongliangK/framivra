"""Sourced evidence semantics, camera transforms and real sticker PNG tests."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('mg_sticker',ROOT/'templates/mg-styles/render_sticker.py')
sticker=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(sticker)
READY=bool(importlib.util.find_spec('PIL')) and Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc').is_file()


def example(name='phone'):
    return json.loads((ROOT/f'templates/mg-styles/examples/sticker/{name}.json').read_text())


@unittest.skipUnless(READY,'optional Pillow and separately installed Noto CJK required')
class StickerExplainer(unittest.TestCase):
    def test_camera_moves_with_sentences_then_pulls_out(self):
        p=sticker._prepare(example(),240)
        first=sticker._camera(p,0)
        decompose=sticker._camera(p,90)
        proof=sticker._camera(p,155)
        final=sticker._camera(p,239)
        self.assertEqual(first[:3],(720,540,1.16))
        self.assertAlmostEqual(decompose[0],2160)
        self.assertAlmostEqual(proof[0],3600)
        self.assertAlmostEqual(final[0],2880)
        self.assertAlmostEqual(final[2],1/3)
        self.assertGreater(sticker._camera(p,61)[0],first[0])
        self.assertLess(sticker._camera(p,61)[0],decompose[0])

    def test_explicit_reading_budgets_hold_actual_chart_and_overview_pixels(self):
        for name in ('phone-readable','water-readable'):
            data=example(name);p=sticker._prepare(data,360)
            proof=p['beats'][2];done=proof['in']+proof['move_frames']+proof['reveal_frames']
            self.assertEqual(sticker._evidence_progress(proof,done),1)
            self.assertEqual(proof['out']-done,proof['closeup_hold_frames'])
            self.assertEqual(sticker._render(p,done).tobytes(),sticker._render(p,proof['out']-1).tobytes())
            ending=p['beats'][3]
            overview=ending['out']-ending['overview_hold_frames']
            self.assertAlmostEqual(sticker._camera(p,overview)[2],1/3)
            self.assertEqual(sticker._camera(p,overview)[:3],sticker._camera(p,359)[:3])
            self.assertEqual(sticker._render(p,overview).tobytes(),sticker._render(p,359).tobytes())
        for change in ({'move_frames':True},{'closeup_hold_frames':0},{'overview_hold_frames':100}):
            data=example('phone-readable');data['beats'][3].update(change)
            with tempfile.TemporaryDirectory() as tmp:
                target=Path(tmp)/'not-created'
                with self.assertRaises(ValueError):sticker.render_sticker_frames(data,360,target)
                self.assertFalse(target.exists())

    def test_schema_sources_units_relations_and_fonts_fail_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for kind in ['missing_source','mixed_units','nan','bad_reference','unreachable','bad_shape','float_window','overflow','seed_bool']:
                data=example()
                if kind=='missing_source':data['evidence'][0]['source']=''
                if kind=='mixed_units':data['evidence'][0]['unit']='kg'
                if kind=='nan':data['evidence'][0]['value']=float('nan')
                if kind=='bad_reference':data['relations'][0]['to']='unseen'
                if kind=='unreachable':data['relations']=data['relations'][1:]
                if kind=='bad_shape':data['parts'][0]['shape']='photograph'
                if kind=='float_window':data['beats'][0]['out']=60.0
                if kind=='overflow':data['evidence'][0]['source']='非常长的来源记录'*15
                if kind=='seed_bool':data['seed']=True
                target=root/kind
                with self.assertRaises(ValueError):sticker.render_sticker_frames(data,240,target)
                self.assertFalse(target.exists())
            latin=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
            if latin.exists():
                with self.assertRaisesRegex(ValueError,'lacks glyph'):
                    sticker.validate_sticker_content(example(),240,latin)
            self.assertEqual(list(root.iterdir()),[])

    def test_metadata_preserves_sources_and_rejects_photographic_claim(self):
        data=example();before=copy.deepcopy(data)
        receipt=sticker.validate_sticker_content(data,240)
        self.assertEqual(data,before)
        self.assertEqual(receipt['evidence'][0],{'value':35,'unit':'g','source':'教学示意数据，非真实测量'})
        self.assertIn('no photographs',receipt['artwork'])
        self.assertEqual(receipt['world_size'],[5760,1080])

    def test_bar_heights_and_negative_baseline_are_actual_pixels(self):
        from PIL import Image
        data=example('water');p=sticker._prepare(data,240)
        world=sticker._world(p,179)
        accent=Image.new('RGB',(1,1),p['palette'][2]).getpixel((0,0))
        # Pure data geometry uses one common scale and a signed zero baseline.
        for i,expected in [(0,200),(1,round(80/120*200))]:
            x=round(p['evidence'][i]['center'][0]);ys=[]
            for y in range(390,801):
                if world.getpixel((x,y))==accent:ys.append(y)
            self.assertGreaterEqual(min(ys),590)
            self.assertLessEqual(abs(max(ys)-min(ys)-expected),1)
        positive=sticker._prepare(example(),240);world=sticker._world(positive,179)
        accent=Image.new('RGB',(1,1),positive['palette'][2]).getpixel((0,0))
        x=round(positive['evidence'][1]['center'][0])
        self.assertEqual(world.getpixel((x,390)),accent)
        self.assertNotEqual(world.getpixel((x,600)),accent)

    def test_real_png_seek_and_changed_icon_and_value_affect_geometry(self):
        from PIL import Image,ImageChops
        data=example();p=sticker._prepare(data,240)
        for f in [0,59,60,155,180,239]:
            before=sticker._render(p,f)
            sticker._render(p,239-f)
            self.assertEqual(before.mode,'RGB');self.assertEqual(before.size,(1920,1080))
            self.assertEqual(before.tobytes(),sticker._render(p,f).tobytes())
        changed=copy.deepcopy(data);changed['question']['shape']='bottle'
        a=sticker.render_sticker_frame(data,0,240).crop((650,300,1270,900))
        b=sticker.render_sticker_frame(changed,0,240).crop((650,300,1270,900))
        self.assertIsNotNone(ImageChops.difference(a,b).getbbox())
        changed=copy.deepcopy(data);changed['evidence'][0]['value']=5
        a=sticker.render_sticker_frame(data,155,240).crop((450,390,670,600))
        b=sticker.render_sticker_frame(changed,155,240).crop((450,390,670,600))
        self.assertIsNotNone(ImageChops.difference(a,b).getbbox())
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'actual.png';before.save(target)
            with Image.open(target) as reopened:self.assertEqual(before.tobytes(),reopened.tobytes())

    def test_ring_only_content_and_procedural_white_outline(self):
        from PIL import Image,ImageChops
        data=example()
        for i,item in enumerate(data['evidence']):
            item.update(chart='ring',unit='%',value=25*(i+1))
        image=sticker.render_sticker_frame(data,155,240)
        self.assertEqual(image.size,(1920,1080))
        data['evidence'][0]['value']=101
        with self.assertRaises(ValueError):sticker.validate_sticker_content(data,240)
        icon=sticker._icon('chip',105,'#e77c50','#263b40')
        raw=icon.tobytes()
        pixels=list(zip(raw[0::4],raw[1::4],raw[2::4],raw[3::4]))
        self.assertTrue(any(p[:3]==(255,255,255) and p[3]>0 for p in pixels))
        self.assertTrue(any(p[3]==0 for p in pixels))
        different=sticker._icon('battery',105,'#e77c50','#263b40')
        self.assertIsNotNone(ImageChops.difference(icon,different).getbbox())


if __name__=='__main__':unittest.main()
