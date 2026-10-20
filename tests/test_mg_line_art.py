"""Semantic path geometry and real Pillow seek checks for line-art assets."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mg_semantic_line_art', ROOT / 'templates/mg-styles/render_line_art.py')
line = importlib.util.module_from_spec(spec)
spec.loader.exec_module(line)
FONT = Path('/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc')
READY = bool(importlib.util.find_spec('PIL')) and FONT.is_file()


def story_a():
    return {'title': '从一粒种子到共同的家', 'subtitle': '生长，阅读，共建。',
            'story': [{'object': 'seed', 'label': '一粒种子', 'in': 0, 'out': 45, 'hold': 12},
                      {'object': 'tree', 'label': '长成一棵树', 'in': 45, 'out': 90, 'hold': 12},
                      {'object': 'book', 'label': '知识被传递', 'in': 90, 'out': 135, 'hold': 12},
                      {'object': 'house', 'label': '一起建成家', 'in': 135, 'out': 180, 'hold': 15}],
            'drawing_speed': 1}


class PathGeometry(unittest.TestCase):
    def test_arc_length_tip_advances_by_requested_distance(self):
        points = [(0, 0), (30, 0), (30, 40)]
        lengths = line._lengths(points)
        self.assertEqual(lengths, [0, 30, 70])
        self.assertEqual(line._partial(points, lengths, 20)[1], (20, 0))
        self.assertEqual(line._partial(points, lengths, 50)[1], (30, 20))
        self.assertEqual(line._partial(points, lengths, 99)[1], (30, 40))

    def test_semantic_routes_differ_and_have_finite_coordinates(self):
        routes = [line._route(name)[0] for name in line.OBJECTS]
        self.assertEqual(len({tuple(points) for points in routes}), len(line.OBJECTS))
        for points in routes:
            self.assertTrue(all(math.isfinite(x) and math.isfinite(y) for x, y in points))
            self.assertGreater(line._lengths(points)[-1], 1)

    @unittest.skipUnless(READY, 'optional Pillow and separately installed Noto Serif CJK required')
    def test_public_validation_has_no_writes_and_rejects_uncovered_cjk(self):
        data = story_a()
        before = copy.deepcopy(data)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = line.validate_line_art_content(data, 180)
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(data, before)
            self.assertEqual(metadata['font_license'], 'SIL OFL 1.1')
            self.assertTrue(metadata['font_sha256'])
            self.assertEqual(metadata['semantic_objects'], ['seed', 'tree', 'book', 'house'])
            latin = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
            if latin.is_file():
                with self.assertRaisesRegex(ValueError, 'lacks glyph'):
                    line.validate_line_art_content(data, 180, latin)
                with self.assertRaisesRegex(ValueError, 'lacks glyph'):
                    line.render_line_art_frame(data, 0, 180, latin)

    @unittest.skipUnless(READY, 'optional Pillow and separately installed Noto Serif CJK required')
    def test_all_handoffs_share_exact_pen_tip_and_seek_boundaries(self):
        from PIL import ImageChops
        data = story_a()
        prepared = line._prepare(data, 180)
        for a, b in zip(prepared['scenes'], prepared['scenes'][1:]):
            self.assertEqual(a['path'][-1], b['path'][0])
        for frame in [0, 32, 44, 45, 89, 90, 134, 135, 179]:
            expected = line.render_line_art_frame(data, frame, 180)
            line.render_line_art_frame(data, 179 - frame, 180)
            actual = line.render_line_art_frame(data, frame, 180)
            self.assertEqual(expected.size, (1920, 1080))
            self.assertIsNone(ImageChops.difference(expected, actual).getbbox())

    @unittest.skipUnless(READY, 'optional Pillow and separately installed Noto Serif CJK required')
    def test_content_changes_geometry_even_with_identical_text(self):
        from PIL import ImageChops
        a = story_a()
        b = copy.deepcopy(a)
        b['story'][0]['object'] = 'network'
        im_a = line.render_line_art_frame(a, 32, 180)
        im_b = line.render_line_art_frame(b, 32, 180)
        # Compare semantic scene only, excluding all title and caption pixels.
        self.assertIsNotNone(ImageChops.difference(im_a.crop((140, 300, 560, 760)), im_b.crop((140, 300, 560, 760))).getbbox())

    @unittest.skipUnless(READY, 'optional Pillow and separately installed Noto Serif CJK required')
    def test_speed_changes_drawing_and_final_fill_keeps_same_contour(self):
        from PIL import ImageChops
        a, b = story_a(), story_a()
        b['drawing_speed'] = 2
        self.assertIsNotNone(ImageChops.difference(line.render_line_art_frame(a, 12, 180), line.render_line_art_frame(b, 12, 180)).getbbox())
        before = line.render_line_art_frame(a, 165, 180)
        after = line.render_line_art_frame(a, 179, 180)
        self.assertIsNotNone(ImageChops.difference(before.crop((1400, 300, 1780, 760)), after.crop((1400, 300, 1780, 760))).getbbox())

    @unittest.skipUnless(READY, 'optional Pillow and separately installed Noto Serif CJK required')
    def test_single_hold_frame_really_displays_final_fill_at_all_valid_speeds(self):
        # Exercise the actual raster result, not just an opacity helper or DOM state.
        for span in (2, 3, 8):
            for speed in (1, 2, 8):
                for kind in ('seed', 'tree', 'book', 'house', 'network'):
                    data={'title':'Boundary', 'drawing_speed':speed, 'story':[
                        {'object':'seed','label':'Start','in':0,'out':2,'hold':1},
                        {'object':kind,'label':'Finish','in':2,'out':2+span,'hold':1}]}
                    prepared=line._prepare(data,2+span)
                    scene=prepared['scenes'][-1]
                    final=line._render(prepared,1+span)
                    expected_fill=line._mix(prepared['palette'][0],prepared['palette'][3],.9)
                    # Only the hero polygons can create this exact interior colour.
                    pixels=final.crop((960,300,1840,760))
                    filled=sum(count for count,color in pixels.getcolors(pixels.width*pixels.height) if color==expected_fill)
                    with self.subTest(span=span,speed=speed,object=kind):
                        self.assertGreater(filled,100)
                        self.assertEqual(line._hero_opacity(scene,1+span),1)
                        reveal=scene['in']+scene['drawing_frames']
                        before=line._render(prepared,reveal-1).crop((960,300,1840,760))
                        self.assertNotIn(expected_fill,[color for _,color in before.getcolors(before.width*before.height)])

    @unittest.skipUnless(READY, 'optional Pillow and separately installed Noto Serif CJK required')
    def test_reveal_finishes_inside_exclusive_window_and_then_holds(self):
        data=story_a()
        data['drawing_speed']=2
        prepared=line._prepare(data,180)
        scene=prepared['scenes'][-1]
        start=scene['in']+scene['drawing_frames']
        opacity=[line._hero_opacity(scene,frame) for frame in range(start,scene['out'])]
        self.assertGreater(opacity[0],0)
        self.assertEqual(opacity,sorted(opacity))
        self.assertEqual(opacity[-1],1)
        self.assertGreaterEqual(opacity.count(1),2)
        from PIL import ImageChops
        first_full=start+opacity.index(1)
        self.assertIsNone(ImageChops.difference(line._render(prepared,first_full),line._render(prepared,179)).getbbox())

    @unittest.skipUnless(READY, 'optional Pillow and separately installed Noto Serif CJK required')
    def test_bad_cues_do_not_create_output_and_sequence_receipt_is_real(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for mutation in ['overlap', 'gap', 'float', 'object', 'hold', 'bool_speed']:
                data = story_a()
                if mutation == 'overlap': data['story'][1]['in'] = 44
                if mutation == 'gap': data['story'][1]['in'] = 46
                if mutation == 'float': data['story'][0]['in'] = 0.0
                if mutation == 'object': data['story'][0]['object'] = 'unknown'
                if mutation == 'hold': data['story'][0]['hold'] = 45
                if mutation == 'bool_speed': data['drawing_speed'] = True
                output = root / mutation
                with self.assertRaises(ValueError):
                    line.render_line_art_frames(data, 180, output)
                self.assertFalse(output.exists())
            tiny = {'title': 'Seed to tree', 'story': [
                {'object': 'seed', 'label': 'Seed', 'in': 0, 'out': 3, 'hold': 1},
                {'object': 'tree', 'label': 'Tree', 'in': 3, 'out': 6, 'hold': 1}]}
            receipt = line.render_line_art_frames(tiny, 6, root / 'actual')
            self.assertEqual(len(list((root / 'actual').glob('f*.png'))), 6)
            self.assertTrue(receipt['routes_continuous'])
            self.assertEqual(receipt['frames'], 6)
            self.assertEqual(json.loads((root / 'actual/evidence.json').read_text()), receipt)


if __name__ == '__main__':
    unittest.main()
