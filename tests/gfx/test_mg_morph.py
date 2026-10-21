"""Semantic contour geometry and real PNG checks for frame-driven shape morphs."""
from tests.gfx._mg import load_renderer
import json
import math
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
morph = load_renderer('mg_morph',ROOT/'templates/mg-styles/styles/morph/scripts/render_morph.py')
try:
    from PIL import Image,ImageChops
except ImportError:
    Image=ImageChops=None


def example(name):
    return json.loads((ROOT/f'templates/mg-styles/styles/morph/assets/examples/morph-{name}.json').read_text())


@unittest.skipIf(Image is None,'optional Pillow required')
class SemanticMorph(unittest.TestCase):
    def test_semantic_outlines_equal_topology_and_continuous_boundaries(self):
        contours=[morph.shape_contour(name) for name in morph.SHAPES]
        self.assertTrue(all(len(points)==128 for points in contours))
        self.assertEqual(len({tuple(points) for points in contours}),len(morph.SHAPES))
        for points in contours:
            self.assertTrue(all(math.isfinite(v) for p in points for v in p))
            area=sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points,points[1:]+points[:1]))/2
            self.assertGreater(area,.2)
        for name,frames in (('growth',240),('navigation',180)):
            data=morph.validate_morph_content(example(name),frames)
            for stage in data['shapes'][1:]:
                before=morph.morph_state(data,stage['in']-1,frames)
                at=morph.morph_state(data,stage['in'],frames)
                self.assertEqual(before['points'],at['points'])
                self.assertEqual(before['color'],at['color'])
                settled=morph.morph_state(data,stage['in']+stage['transition'],frames)
                held=morph.morph_state(data,stage['out']-1,frames)
                self.assertEqual(settled['points'],held['points'])
                self.assertEqual(settled['color'],held['color'])
                self.assertNotEqual(at['points'],settled['points'])

    def test_actual_png_two_semantic_stories_and_reverse_seek(self):
        for name,frames in (('growth',240),('navigation',180)):
            data=example(name)
            first=morph.render_morph_frame(data,20,frames)
            last=morph.render_morph_frame(data,frames-1,frames)
            # Labels are outside this crop: the changed pixels prove changed shapes.
            delta=ImageChops.difference(first.crop((450,280,1480,820)),last.crop((450,280,1480,820)))
            self.assertIsNotNone(delta.getbbox())
            morph.render_morph_frame(data,frames//2,frames)
            self.assertEqual(first.tobytes(),morph.render_morph_frame(data,20,frames).tobytes())
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'frame.png';last.save(path)
                with Image.open(path) as read:self.assertEqual(read.tobytes(),last.tobytes())
            self.assertEqual(first.mode,'RGB');self.assertEqual(first.size,(1920,1080))

    def test_reject_before_output_bad_fonts_windows_finite_and_text_fit(self):
        for edit in ({'x':float('nan')},{'y':400,'scale':1.1},{'scale':True},{'shape':'anything'},{'transition':True},{'out':59}):
            data=example('growth');data['shapes'][1].update(edit)
            with self.subTest(edit=edit),self.assertRaises(ValueError):morph.validate_morph_content(data,240)
        data=example('growth');data['shapes'][0]['label']='满'*100;data['shapes'][1]['label']='满'*100
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'not-created'
            with self.assertRaises(ValueError):morph.render_morph_frames(data,240,target)
            self.assertFalse(target.exists())
        missing=example('growth');missing['title']='缺字'+chr(0x10ffff)
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'no-tofu'
            with self.assertRaisesRegex(ValueError,'lacks required glyph'):
                morph.render_morph_frames(missing,240,output)
            self.assertFalse(output.exists())
        dejavu=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
        if dejavu.exists():
            with self.assertRaises(ValueError):morph.validate_morph_content(example('growth'),240,dejavu)
        with self.assertRaises(ValueError):morph.render_morph_frame(example('growth'),True,240)
        with self.assertRaises(ValueError):morph.validate_morph_content(example('growth'),True)


if __name__=='__main__':unittest.main()
