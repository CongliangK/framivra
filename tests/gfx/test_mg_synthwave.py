from tests.gfx._mg import load_renderer
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]/'templates/mg-styles'
s = load_renderer('mg_synthwave',ROOT/'styles/synthwave/scripts/render_synthwave.py')
READY=importlib.util.find_spec('PIL') and importlib.util.find_spec('numpy') and Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc').is_file()
@unittest.skipUnless(READY,'optional Pillow/NumPy/CJK font required')
class SynthJourney(unittest.TestCase):
    def load(self,name):return json.loads((ROOT/'styles/synthwave/assets/examples'/('synthwave-'+name+'.json')).read_text())
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

    def test_waypoint_leaves_by_native_geometry_not_old_distance_or_label_gate(self):
        from PIL import ImageChops
        from unittest.mock import patch
        data=self.load('night');font=s._font(data);project=s.waypoint_projection
        # Suppress only one real scene element to isolate its native decoded ROI.
        def difference(frame,index):
            normal=s.render_synthwave_frame(data,frame,180)
            def omit(point,travel,font_path,horizon=580):
                return None if point is data['waypoints'][index] else project(point,travel,font_path,horizon)
            with patch.object(s,'waypoint_projection',side_effect=omit):
                absent=s.render_synthwave_frame(data,frame,180)
            return ImageChops.difference(normal,absent)
        for frame,index,region in ((35,0,(0,650,500,1080)),(36,0,(0,650,500,1080)),
                                   (83,1,(1420,650,1920,1080)),(84,1,(1420,650,1920,1080))):
            with self.subTest(frame=frame):
                delta=difference(frame,index).crop(region)
                self.assertIsNotNone(delta.getbbox())
                self.assertGreater(sum(count for count,color in delta.getcolors(delta.width*delta.height) if max(color)>0),100)
        # At f46 the first marker still has visible edge pixels; at f47 its
        # entire line AND label have left, so culling can no longer pop the art.
        self.assertIsNotNone(difference(46,0).getbbox())
        self.assertIsNone(difference(47,0).getbbox())
        label=s.waypoint_projection(data['waypoints'][0],32,font)
        self.assertLess(label['x'],210)  # Old label gate already hid readable ink.
        box=label['bounds'];self.assertGreater(box[2],0)
        self.assertIsNotNone(difference(32,0).crop((0,650,350,760)).getbbox())
        # Far projected objects are finite and remain drawable; the near-plane
        # singularity is reached only after lateral departure.
        far=project(dict(frame=500,label='远方',side='left'),0,font)
        self.assertIsNotNone(far)
        self.assertTrue(all(__import__('math').isfinite(far[k]) for k in ('x','bottom','width','height','scale')))
        self.assertIsNone(project(data['waypoints'][0],70,font))


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
