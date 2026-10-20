"""Real PNG checks for the optional media reaction-caption renderer."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('mg_captions', Path(__file__).resolve().parents[1] / 'templates/mg-styles/captions.py')
captions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(captions)
try:
    from PIL import Image
except ImportError:
    Image = None


@unittest.skipIf(Image is None, 'optional Pillow is not installed')
class ReactionCaptions(unittest.TestCase):
    def setUp(self):
        self.cues = [{'in': 2, 'out': 16, 'text': '真的成功了！ <yes> & 好', 'emotion': 'joy', 'punchFrame': 10}]
        self.content = {'subject_rect': [.25, .15, .75, .74]}
        self.font = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
        if not self.font.is_file():
            self.skipTest('CJK font fixture unavailable on this host')

    def frame(self, number):
        return captions.render_caption_frame(self.content, self.cues, number, 18, font_path=self.font)

    def test_cjk_seek_boundaries_and_protected_subject(self):
        self.assertIsNone(self.frame(1).getbbox())
        self.assertIsNone(self.frame(16).getbbox())
        frame = self.frame(10)
        self.assertEqual(frame.mode, 'RGBA')
        self.assertIsNotNone(frame.getbbox())
        self.assertIsNone(frame.crop((480, 162, 1440, 799)).getbbox())
        self.frame(15)
        self.frame(2)
        self.assertEqual(frame.tobytes(), self.frame(10).tobytes())
        # A semantic punch changes rendered pixels even after entry has settled.
        normal = [dict(self.cues[0], punchFrame=2)]
        normal_frame = captions.render_caption_frame(self.content, normal, 10, 18, font_path=self.font)
        self.assertNotEqual(frame.tobytes(), normal_frame.tobytes())
        staggered = [{'in': 0, 'out': 30, 'text': '成功', 'emotion': 'joy', 'stagger': 2}]
        early = captions.render_caption_frame({}, staggered, 3, 30, font_path=self.font)
        later = captions.render_caption_frame({}, staggered, 12, 30, font_path=self.font)
        # Early frames contain the leading reaction glyph; later frames reveal Chinese glyphs.
        eb, lb = early.getbbox(), later.getbbox()
        self.assertIsNotNone(eb)
        self.assertGreater(lb[2]-lb[0], eb[2]-eb[0])
        self.assertNotEqual(early.tobytes(), later.tobytes())
        captions.render_caption_frame({}, staggered, 25, 30, font_path=self.font)
        self.assertEqual(early.tobytes(), captions.render_caption_frame({}, staggered, 3, 30, font_path=self.font).tobytes())
        upper_content = {'caption_band': 'upper', 'subject_rect': [.3, .6, .7, .95]}
        upper = captions.render_caption_frame(upper_content, staggered, 12, 30, font_path=self.font)
        self.assertIsNone(upper.crop((576, 648, 1344, 1026)).getbbox())
        self.assertIsNotNone(upper.crop((0, 291, 1920, 540)).getbbox())
        with self.assertRaises(ValueError):
            captions.render_caption_frame({'caption_band':'upper','subject_rect':[.3,.2,.7,.4]}, staggered, 12, 30, font_path=self.font)

    def test_sequence_matches_direct_seek_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'sequence'
            report = captions.render_caption_frames(self.content, self.cues, 18, output, font_path=self.font)
            self.assertEqual(report['caption_frames'], 14)
            self.assertEqual(len(list(output.glob('f*.png'))), 18)
            with Image.open(output / 'f0010.png') as image:
                self.assertEqual(image.tobytes(), self.frame(10).tobytes())
            with self.assertRaises(ValueError):
                captions.render_caption_frames(self.content, self.cues, 18, output, font_path=self.font)

    def test_invalid_cue_and_subject_geometry(self):
        for change in ({'in': True}, {'out': 19}, {'punchFrame': 16}, {'emotion': []}, {'stagger': True}, {'stagger': 6}, {'stagger': 2}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                captions.render_caption_frame({}, [dict(self.cues[0], **change)], 3, 18, font_path=self.font)
        with self.assertRaises(ValueError):
            captions.render_caption_frame({}, self.cues * 2, 3, 18, font_path=self.font)
        with self.assertRaises(ValueError):
            captions.render_caption_frame({'subject_rect': [0, 0, 1, .9]}, self.cues, 3, 18, font_path=self.font)

    def test_preflight_rejects_newline_overflow_before_output(self):
        for text in ('\n'*100+'X', 'one\ntwo\nthree\nfour', '界'*120):
            cues = [{'in':0, 'out':180, 'text':text, 'emotion':'surprise'}]
            with self.subTest(text=text), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)/'frames'
                with self.assertRaises(ValueError):
                    captions.render_caption_frames({}, cues, 180, output,
                                                    width=320, height=320, font_path=self.font)
                self.assertFalse(output.exists())

    def test_prepared_prefix_controls_stagger_and_hold_boundary(self):
        # surprise prepares !? + two spaces + A: five glyphs, not three.
        cue = {'in':0, 'out':25, 'text':'A', 'emotion':'surprise', 'stagger':5}
        with self.assertRaises(ValueError):
            captions.validate_caption_content({}, [cue], 30, font_path=self.font)
        cue['out'] = 29
        report = captions.validate_caption_content({}, [cue], 30, font_path=self.font)
        self.assertEqual(report['cues'][0]['glyph_count'], 5)
        self.assertEqual(report['cues'][0]['minimum_frames'], 29)
        # Full last-glyph reveal occurs at local 23, leaving frames 23..28.
        full = captions.render_caption_frame({}, [cue], 23, 30, font_path=self.font)
        end = captions.render_caption_frame({}, [cue], 28, 30, font_path=self.font)
        self.assertEqual(full.tobytes(), end.tobytes())

    def test_nonstagger_attack_and_readable_hold_boundary(self):
        cue = {'in':0, 'out':13, 'text':'成功', 'emotion':'joy', 'stagger':0}
        with self.assertRaises(ValueError):
            captions.validate_caption_content({}, [cue], 14, font_path=self.font)
        cue['out'] = 14
        report = captions.validate_caption_content({}, [cue], 14, font_path=self.font)
        self.assertEqual(report['cues'][0]['minimum_frames'], 14)
        start = captions.render_caption_frame({}, [cue], 8, 14, font_path=self.font)
        end = captions.render_caption_frame({}, [cue], 13, 14, font_path=self.font)
        self.assertEqual(start.tobytes(), end.tobytes())

    def test_individual_glyphs_and_wrapped_text_have_unclipped_bounds(self):
        cues = [{'in':0, 'out':120, 'text':'jÅ界\n测试 italic W',
                 'emotion':'surprise', 'stagger':2}]
        _, _, layers, _, _ = captions._prepare({}, cues, 120, font_path=self.font)
        for _, layer, glyphs in layers:
            self.assertIsNotNone(layer.getbbox())
            for glyph, x, y in glyphs:
                box = glyph.getbbox()
                if box:
                    self.assertGreater(box[0], 0)
                    self.assertGreater(box[1], 0)
                    self.assertLess(box[2], glyph.width)
                    self.assertLess(box[3], glyph.height)
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x+glyph.width, layer.width)
                self.assertLessEqual(y+glyph.height+12, layer.height)
        image = captions.render_caption_frame({}, cues, 110, 120, font_path=self.font)
        self.assertIsNotNone(image.getbbox())


if __name__ == '__main__':
    unittest.main()
