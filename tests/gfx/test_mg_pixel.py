"""Real Pillow pixels for the quest path; these do not certify a finished film."""
import copy
from tests.gfx._mg import load_renderer
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PIXEL = load_renderer('mg_pixel', ROOT / 'templates/mg-styles/styles/pixel/scripts/render_pixel.py')
FONT = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
try:
    from PIL import Image, ImageChops
except ImportError:
    Image = None


def story():
    return {'mission': '收集研究证据', 'steps': [
        {'label': '跨过问题', 'in': 0, 'out': 8, 'obstacle': 'jump'},
        {'label': '收集线索', 'in': 8, 'out': 16, 'obstacle': 'collect'}],
        'reward': {'text': '任务完成', 'in': 17}, 'seed': 7}


@unittest.skipUnless(Image is not None and FONT.is_file(), 'Pillow and separately licensed Noto CJK font required')
class PixelQuest(unittest.TestCase):
    def frame(self, data, frame):
        return PIXEL.render_pixel_frame(data, frame, 20)

    def test_native_lattice_is_exact_six_by_six_including_cjk(self):
        for frame in (0, 2, 5, 9, 11):
            with self.subTest(frame=frame):
                image = self.frame(story(), frame)
                self.assertEqual(image.size, (1920, 1080))
                self.assertEqual(image.mode, 'RGB')
                small = image.resize((320, 180), Image.Resampling.NEAREST)
                expanded = small.resize(image.size, Image.Resampling.NEAREST)
                self.assertIsNone(ImageChops.difference(image, expanded).getbbox())
                self.assertGreater(len(image.getcolors(image.width * image.height)), 2)

    def test_cjk_mission_steps_and_reward_change_their_visible_regions(self):
        original = story()
        for field, frame, region in (
                ('mission', 2, (0, 0, 1920, 192)),
                ('steps', 11, (0, 900, 1920, 1080)),
                ('reward', 18, (150, 294, 1770, 762))):
            changed = copy.deepcopy(original)
            if field == 'mission':
                changed['mission'] = '制作可用模板'
            elif field == 'steps':
                changed['steps'][1]['label'] = '验证不同内容'
            else:
                changed['reward']['text'] = '新的发现'
            with self.subTest(field=field):
                a, b = self.frame(original, frame), self.frame(changed, frame)
                self.assertIsNotNone(ImageChops.difference(a.crop(region), b.crop(region)).getbbox())
                # Chinese strings must create distinct masks, not repeated missing-glyph boxes.
                self.assertNotEqual(a.crop(region).tobytes(), b.crop(region).tobytes())

    def test_obstacle_semantics_change_playfield_not_only_caption(self):
        original = story()
        changed = copy.deepcopy(original)
        changed['steps'][0]['obstacle'] = 'duck'
        region = (0, 192, 1920, 894)
        a, b = self.frame(original, 2), self.frame(changed, 2)
        self.assertIsNotNone(ImageChops.difference(a.crop(region), b.crop(region)).getbbox())

    def test_seek_order_and_seed_are_pixel_deterministic(self):
        data = story()
        expected = self.frame(data, 5).tobytes()
        for frame in (11, 0, 9, 2, 5):
            self.frame(data, frame)
        self.assertEqual(expected, self.frame(data, 5).tobytes())
        changed = copy.deepcopy(data)
        changed['seed'] += 1
        self.assertNotEqual(expected, self.frame(changed, 5).tobytes())

    def test_sequence_matches_single_frames_and_evidence(self):
        data = story()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'frames'
            report = PIXEL.render_pixel_frames(data, 20, output)
            self.assertEqual(len(list(output.glob('f*.png'))), 20)
            self.assertEqual(report['native_grid'], [320, 180])
            self.assertEqual(report['size'], [1920, 1080])
            self.assertEqual(report['sampling'], 'nearest')
            self.assertEqual(report, json.loads((output / 'evidence.json').read_text()))
            for frame in (0, 5, 11):
                with Image.open(output / f'f{frame:04d}.png') as actual:
                    self.assertEqual(actual.tobytes(), self.frame(data, frame).tobytes())
            with self.assertRaises(FileExistsError):
                PIXEL.render_pixel_frames(data, 20, output)

    def test_minimum_action_has_real_middle_pose_and_no_whole_obstacle_teleport(self):
        for kind in PIXEL.OBSTACLES:
            data={'mission':'动作采样','steps':[{'label':'跨越','in':0,'out':7,'obstacle':kind}],
                  'reward':{'text':'完成','in':7},'seed':7}
            prepared=PIXEL._prepare(data,9,None)
            poses=[PIXEL.pixel_state(prepared,f) for f in range(7)]
            self.assertLessEqual(max(b['world_x']-a['world_x'] for a,b in zip(poses,poses[1:])),30)
            if kind=='duck':
                self.assertFalse(poses[0]['crouch']);self.assertTrue(poses[3]['crouch']);self.assertFalse(poses[-1]['crouch'])
                self.assertEqual(poses[3]['player_y'],PIXEL.GROUND-8)
            else:
                self.assertEqual(poses[0]['jump'],0);self.assertGreater(poses[3]['jump'],0);self.assertEqual(poses[-1]['jump'],0)
                self.assertLess(poses[3]['player_y'],PIXEL.GROUND-12)
            # Actual native-grid sprite pixels occupy the computed intermediate pose.
            image=PIXEL._render(prepared,3).resize((320,180),Image.Resampling.NEAREST)
            x,y=poses[3]['player_x'],poses[3]['player_y']
            self.assertEqual(image.getpixel((x+3,y)),tuple(int(prepared['palette'][2][i:i+2],16) for i in (1,3,5)))
            for span in (2,3,6):
                invalid=copy.deepcopy(data);invalid['steps'][0]['out']=span
                with tempfile.TemporaryDirectory() as temp:
                    target=Path(temp)/'bad'
                    with self.assertRaises(ValueError):PIXEL.render_pixel_frames(invalid,9,target)
                    self.assertFalse(target.exists())

    def test_invalid_cues_and_seeds_fail_before_sequence_output(self):
        invalid = []
        for seed in (True, -(2**31)-1, 2**31, 1.2, '7'):
            data = story(); data['seed'] = seed; invalid.append(data)
        for field, value in (('in', True), ('in', -1), ('out', 0), ('out', 20), ('obstacle', 'fly')):
            data = story(); data['steps'][0][field] = value; invalid.append(data)
        data = story(); data['steps'][1]['in'] = 7; invalid.append(data)
        data = story(); data['reward']['in'] = 15; invalid.append(data)
        data = story(); data['mission'] = ''; invalid.append(data)
        data = story(); data['steps'] = []; invalid.append(data)
        data = story(); data['reward'] = {}; invalid.append(data)
        with tempfile.TemporaryDirectory() as temp:
            for i, data in enumerate(invalid):
                with self.subTest(data=data):
                    output = Path(temp) / str(i)
                    with self.assertRaises(ValueError):
                        PIXEL.render_pixel_frames(data, 20, output)
                    self.assertFalse(output.exists())
        for frame in (-1, 20, True, 1.5):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                self.frame(story(), frame)


class SkylineContinuity(unittest.TestCase):
    @unittest.skipUnless(Image is not None, 'Pillow required')
    def test_native_skyline_pixels_translate_across_modulo_boundary(self):
        from PIL import ImageDraw
        def skyline(camera):
            image=Image.new('1',(320,180))
            draw=ImageDraw.Draw(image)
            for identity,x in PIXEL.skyline_columns(camera):
                height=9+(identity*11+7)%31
                draw.rectangle((x,PIXEL.GROUND-height,x+35,PIXEL.GROUND),fill=1)
            return image
        for camera in (94,188,282,376):
            before,after=skyline(camera-1),skyline(camera)
            self.assertIsNone(ImageChops.difference(before.crop((1,0,320,180)),after.crop((0,0,319,180))).getbbox())

    def test_world_identity_survives_every_scroll_boundary(self):
        for camera in range(90, 1000):
            before = dict(PIXEL.skyline_columns(camera - 1))
            after = dict(PIXEL.skyline_columns(camera))
            for identity in before.keys() & after.keys():
                self.assertEqual(after[identity] - before[identity], -((camera // 2) - ((camera - 1) // 2)))


if __name__ == '__main__':
    unittest.main()
