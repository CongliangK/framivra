"""Real Pillow pixels for the quest path; these do not certify a finished film."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('mg_pixel', ROOT / 'templates/mg-styles/render_pixel.py')
PIXEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIXEL)
FONT = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
try:
    from PIL import Image, ImageChops
except ImportError:
    Image = None


def story():
    return {'mission': '收集研究证据', 'steps': [
        {'label': '跨过问题', 'in': 0, 'out': 4, 'obstacle': 'jump'},
        {'label': '收集线索', 'in': 4, 'out': 8, 'obstacle': 'collect'}],
        'reward': {'text': '任务完成', 'in': 9}, 'seed': 7}


@unittest.skipUnless(Image is not None and FONT.is_file(), 'Pillow and separately licensed Noto CJK font required')
class PixelQuest(unittest.TestCase):
    def frame(self, data, frame):
        return PIXEL.render_pixel_frame(data, frame, 12)

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
                ('steps', 5, (0, 900, 1920, 1080)),
                ('reward', 10, (150, 294, 1770, 762))):
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
            report = PIXEL.render_pixel_frames(data, 12, output)
            self.assertEqual(len(list(output.glob('f*.png'))), 12)
            self.assertEqual(report['native_grid'], [320, 180])
            self.assertEqual(report['size'], [1920, 1080])
            self.assertEqual(report['sampling'], 'nearest')
            self.assertEqual(report, json.loads((output / 'evidence.json').read_text()))
            for frame in (0, 5, 11):
                with Image.open(output / f'f{frame:04d}.png') as actual:
                    self.assertEqual(actual.tobytes(), self.frame(data, frame).tobytes())
            with self.assertRaises(FileExistsError):
                PIXEL.render_pixel_frames(data, 12, output)

    def test_invalid_cues_and_seeds_fail_before_sequence_output(self):
        invalid = []
        for seed in (True, -(2**31)-1, 2**31, 1.2, '7'):
            data = story(); data['seed'] = seed; invalid.append(data)
        for field, value in (('in', True), ('in', -1), ('out', 0), ('out', 12), ('obstacle', 'fly')):
            data = story(); data['steps'][0][field] = value; invalid.append(data)
        data = story(); data['steps'][1]['in'] = 3; invalid.append(data)
        data = story(); data['reward']['in'] = 7; invalid.append(data)
        data = story(); data['mission'] = ''; invalid.append(data)
        data = story(); data['steps'] = []; invalid.append(data)
        data = story(); data['reward'] = {}; invalid.append(data)
        with tempfile.TemporaryDirectory() as temp:
            for i, data in enumerate(invalid):
                with self.subTest(data=data):
                    output = Path(temp) / str(i)
                    with self.assertRaises(ValueError):
                        PIXEL.render_pixel_frames(data, 12, output)
                    self.assertFalse(output.exists())
        for frame in (-1, 12, True, 1.5):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                self.frame(story(), frame)


if __name__ == '__main__':
    unittest.main()
