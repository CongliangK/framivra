"""Real held-drawing PNG, smear timing, content and preflight tests."""
import copy
import importlib.util
from pathlib import Path
import json
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('mg_cel', ROOT / 'templates/mg-styles/render_cel.py')
cel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cel)
READY = bool(importlib.util.find_spec('PIL')) and Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc').is_file()


def example(name='garden'):
    return json.loads((ROOT / f'templates/mg-styles/examples/cel/{name}.json').read_text())


@unittest.skipUnless(READY, 'optional Pillow and external Noto CJK required')
class CelDrawings(unittest.TestCase):
    def test_actual_pngs_hold_on_twos_and_threes_but_action_is_on_ones(self):
        data = example()
        # Initial anticipation is twos; post-action hold starts at frame30.
        self.assertEqual(cel.render_cel_frame(data, 0, 240).tobytes(), cel.render_cel_frame(data, 1, 240).tobytes())
        self.assertEqual(cel.render_cel_frame(data, 30, 240).tobytes(), cel.render_cel_frame(data, 31, 240).tobytes())
        self.assertEqual(cel.render_cel_frame(data, 31, 240).tobytes(), cel.render_cel_frame(data, 32, 240).tobytes())
        self.assertNotEqual(cel.render_cel_frame(data, 12, 240).tobytes(), cel.render_cel_frame(data, 13, 240).tobytes())
        self.assertNotEqual(cel.render_cel_frame(data, 32, 240).tobytes(), cel.render_cel_frame(data, 33, 240).tobytes())

    def test_smear_is_two_action_drawings_and_has_real_hatch_pixels(self):
        from PIL import Image, ImageDraw, ImageChops
        prepared = cel._prepare(example(), 240)
        actor = prepared['cast'][0]
        found = [f for f in range(60) if cel._pose(prepared, actor, cel._sample(prepared, f)[0])['smear']]
        self.assertEqual(found, [12, 13])
        pose = cel._pose(prepared, actor, 12)
        with_smear = Image.new('RGB', cel.SIZE, prepared['palette'][0])
        no_smear = with_smear.copy()
        cel._draw_actor(ImageDraw.Draw(with_smear), actor, pose, prepared['seed'], 12, prepared['palette'])
        cel._draw_actor(ImageDraw.Draw(no_smear), actor, {**pose, 'smear':False}, prepared['seed'], 12, prepared['palette'])
        self.assertIsNotNone(ImageChops.difference(with_smear, no_smear).getbbox())

    def test_anticipation_squash_and_hop_are_semantic_pose_changes(self):
        prepared = cel._prepare(example(), 240)
        bud, cat = prepared['cast']
        compressed = cel._pose(prepared, bud, 9)
        self.assertLess(compressed['sx'], .5)
        self.assertLess(compressed['sy'], .5)
        hop = cel._pose(prepared, cat, 81)
        self.assertLess(hop['y'], -90)
        self.assertGreater(hop['x'], 20)

    def test_out_of_order_seek_returns_identical_actual_pixels(self):
        data = example()
        for f in [0,9,10,12,29,30,59,60,119,120,179,180,239]:
            before = cel.render_cel_frame(data, f, 240)
            cel.render_cel_frame(data, 239-f, 240)
            self.assertEqual(before.tobytes(), cel.render_cel_frame(data, f, 240).tobytes())

    def test_same_text_different_shape_and_seed_change_drawing_pixels(self):
        from PIL import ImageChops
        a = example()
        b = copy.deepcopy(a)
        b['characters'][0]['shape'] = 'star'
        im_a = cel.render_cel_frame(a, 34, 240).crop((300,350,820,780))
        im_b = cel.render_cel_frame(b, 34, 240).crop((300,350,820,780))
        self.assertIsNotNone(ImageChops.difference(im_a, im_b).getbbox())
        b = copy.deepcopy(a)
        b['seed'] += 1
        im_seed = cel.render_cel_frame(b, 34, 240).crop((400,430,740,750))
        self.assertIsNotNone(ImageChops.difference(im_a.crop((100,80,440,400)), im_seed).getbbox())

    def test_preflight_rejects_bad_seed_cues_font_and_overflow_without_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ['bool_seed','large_seed','overlap','bad_shape','bad_emotion','overflow','fractional_motion']:
                data = example()
                if name == 'bool_seed': data['seed'] = True
                if name == 'large_seed': data['seed'] = 2**31
                if name == 'overlap': data['beats'][1]['in'] = 59
                if name == 'bad_shape': data['characters'][0]['shape'] = 'photograph'
                if name == 'bad_emotion': data['characters'][0]['emotion'] = '???'
                if name == 'overflow': data['characters'][0]['label'] = '很长的中文内容' * 18
                if name == 'fractional_motion': data['beats'][0]['motion'] = 20.5
                output = root / name
                with self.assertRaises(ValueError):
                    cel.render_cel_frames(data,240,output)
                self.assertFalse(output.exists())
            with self.assertRaises(ValueError):
                cel.validate_cel_content(example(),240,root/'missing.ttf')
            latin = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
            if latin.exists():
                with self.assertRaisesRegex(ValueError,'lacks glyph'):
                    cel.render_cel_frame(example(),0,240,latin)
            self.assertEqual(list(root.iterdir()),[])

    def test_two_signed_seed_extremes_and_custom_exposures_are_accepted(self):
        for seed in [-(2**31),2**31-1]:
            data = example(); data['seed'] = seed
            data['exposures'] = {'action':2,'body':3,'hold':1}
            before = copy.deepcopy(data)
            metadata = cel.validate_cel_content(data,240)
            self.assertEqual(data,before)
            self.assertEqual(metadata['exposures'],data['exposures'])

    def test_real_sequence_has_current_file_hashes_and_exact_size(self):
        with tempfile.TemporaryDirectory() as temp:
            data = example()
            data['beats'] = [{'actor':'bud','action':'pop','in':0,'out':12,'anticipation':2,'motion':4},
                             {'actor':'cat','action':'hop','in':12,'out':24,'anticipation':2,'motion':4,'distance':20}]
            receipt = cel.render_cel_frames(data,24,Path(temp)/'frames')
            self.assertEqual(len(list((Path(temp)/'frames').glob('f*.png'))),24)
            self.assertEqual(receipt['size'],[1920,1080])
            self.assertEqual(receipt['frame_sha256'][0],receipt['frame_sha256'][1])
            self.assertEqual(json.loads((Path(temp)/'frames/evidence.json').read_text()),receipt)


if __name__ == '__main__':
    unittest.main()
