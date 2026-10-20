import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]/'templates/mg-styles'
spec=importlib.util.spec_from_file_location('mg_synthwave',ROOT/'render_synthwave.py');s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
READY=importlib.util.find_spec('PIL') and importlib.util.find_spec('numpy') and Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc').is_file()
@unittest.skipUnless(READY,'optional Pillow/NumPy/CJK font required')
class SynthJourney(unittest.TestCase):
    def load(self,name):return json.loads((ROOT/'examples'/('synthwave-'+name+'.json')).read_text())
    def test_two_journeys_real_pixels_seek_and_arrival_hold(self):
        from PIL import ImageChops
        for name,frames in [('night',180),('coast',240)]:
            data=self.load(name);end=s.render_synthwave_frame(data,frames-1,frames)
            for f in [frames-11,frames-1]:self.assertIsNone(ImageChops.difference(end,s.render_synthwave_frame(data,f,frames)).getbbox())
            self.assertIsNotNone(ImageChops.difference(end,s.render_synthwave_frame(data,50,frames)).getbbox())
            images={f:s.render_synthwave_frame(data,f,frames).tobytes() for f in [15,65,90]}
            for f in [90,15,65]:self.assertEqual(images[f],s.render_synthwave_frame(data,f,frames).tobytes())
            self.assertEqual(end.size,(1920,1080))
    def test_tracking_changes_band_pixels_only_during_declared_event(self):
        from PIL import ImageChops
        data=self.load('night');plain=dict(data,tracking=[])
        self.assertIsNotNone(ImageChops.difference(s.render_synthwave_frame(data,66,180),s.render_synthwave_frame(plain,66,180)).getbbox())
        self.assertIsNone(ImageChops.difference(s.render_synthwave_frame(data,70,180),s.render_synthwave_frame(plain,70,180)).getbbox())
        norm=s.validate_synthwave_content(data,180)
        self.assertLess(s.journey_state(norm,30,180)['travel'],s.journey_state(norm,100,180)['travel'])
    def test_terrain_samples_remain_continuous_across_old_modulo_boundary(self):
        # Same world vertices retain heights and move by the real camera delta,
        # even as the leftmost offscreen sampling index changes.
        a=s.terrain_points(133.2,1,21);b=s.terrain_points(133.5,1,21)
        matched=0
        for x,y in a:
            same=[(xx,yy) for xx,yy in b if abs(yy-y)<1e-9]
            if same:
                self.assertAlmostEqual(x-same[0][0],.18,places=8);matched+=1
        self.assertGreater(matched,25)

    def test_invalid_content_font_windows_and_values_rejected_before_write(self):
        import copy
        data=self.load('night');bad=[]
        for key,value in [('seed',float('inf')),('title','\U0010ffff'),('destination','字'*100),('tracking',[{'in':160,'out':170,'strength':2}])]:
            item=copy.deepcopy(data);item[key]=value;bad.append(item)
        item=copy.deepcopy(data);item['stages'][2]['in']=119;bad.append(item)
        item=copy.deepcopy(data);item['waypoints'][0]['frame']=True;bad.append(item)
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'not-created'
            for item in bad:
                with self.assertRaises(ValueError):s.render_synthwave_frames(item,180,out)
                self.assertFalse(out.exists())
if __name__=='__main__':unittest.main()
