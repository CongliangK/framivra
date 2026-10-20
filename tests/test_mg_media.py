"""Actual RGBA geometry checks for the independent reaction video asset."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]/'templates/mg-styles'
spec=importlib.util.spec_from_file_location('mg_build_media',ROOT/'build_media.py')
media=importlib.util.module_from_spec(spec)
sys.path.insert(0,str(ROOT))
try: spec.loader.exec_module(media)
finally: sys.path.pop(0)

class MediaGeometry(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'optional Pillow is unavailable')
    def test_alpha_union_crop_repositions_to_identical_pixels(self):
        from PIL import Image,ImageDraw
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'full';source.mkdir()
            for f,box in enumerate([None,(333,280,700,380),(600,390,900,470)]):
                image=Image.new('RGBA',(1920,1080),(0,0,0,0))
                if box: ImageDraw.Draw(image).rectangle(box,fill=(50,180,220,233))
                image.save(source/f'f{f:04d}.png')
            cropped=root/'asset';left,top,right,bottom=media.crop_caption_asset(source,cropped)
            self.assertTrue(all(v%2==0 for v in (left,top,right,bottom)))
            self.assertLess(right-left,1920);self.assertLess(bottom-top,1080)
            for f in range(3):
                with Image.open(cropped/f'f{f:04d}.png') as cell:
                    reconstructed=Image.new('RGBA',(1920,1080),(0,0,0,0))
                    reconstructed.paste(cell,(left,top))
                with Image.open(source/f'f{f:04d}.png') as original:
                    self.assertEqual(original.tobytes(),reconstructed.tobytes())

    def test_invalid_samples_fail_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'new'
            for samples in (0,257,True,1.5):
                with self.assertRaises(ValueError):
                    media.assemble('soft-3d',output,{},30,samples=samples,dry=True)
                self.assertFalse(output.exists())

if __name__=='__main__':unittest.main()
