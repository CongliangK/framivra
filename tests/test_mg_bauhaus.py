"""Real raster and analytic trajectory checks for modular Bauhaus choreography."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]/'templates'/'mg-styles'
SPEC=importlib.util.spec_from_file_location('mg_bauhaus',ROOT/'render_bauhaus.py')
bauhaus=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bauhaus)
FONT=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
READY=bool(importlib.util.find_spec('PIL')) and FONT.is_file()


def example(name='a'):
    return json.loads((ROOT/'examples'/('bauhaus-'+name+'.json')).read_text(encoding='utf-8'))


@unittest.skipUnless(READY,'optional Pillow and separately installed Noto Sans CJK required')
class ModularBauhaus(unittest.TestCase):
    def test_both_examples_validate_without_mutation_or_writes(self):
        for name in ('a','b'):
            data=example(name);original=copy.deepcopy(data)
            with tempfile.TemporaryDirectory() as temp:
                receipt=bauhaus.validate_bauhaus_content(data,180)
                self.assertEqual(list(Path(temp).iterdir()),[])
                self.assertEqual(data,original)
                self.assertEqual(receipt['size'],[1920,1080])
                self.assertEqual(receipt['font_license'],'SIL OFL 1.1')
                self.assertTrue(receipt['trajectory_bounds_verified'])
                self.assertTrue(all(frame%receipt['tick_frames']==0 for frame in receipt['cue_frames']))

    def test_analytic_rotation_bounds_enclose_all_intermediate_vertices(self):
        prepared=bauhaus._prepare(example(),180)
        before=prepared['layouts']['focus'];after=prepared['layouts']['rhythm']
        module=prepared['modules'][2]
        for axis in ('center','corner','frame'):
            bounds=bauhaus._motion_bounds(prepared,module,before[module['id']],after[module['id']],axis)
            for step in range(101):
                for x,y in bauhaus._points(prepared,module,before[module['id']],after[module['id']],axis,step/100):
                    self.assertGreaterEqual(x,bounds[0]-1e-8);self.assertLessEqual(x,bounds[2]+1e-8)
                    self.assertGreaterEqual(y,bounds[1]-1e-8);self.assertLessEqual(y,bounds[3]+1e-8)

    def test_grid_landings_and_shared_cascade_schedule(self):
        prepared=bauhaus._prepare(example(),180)
        for beat in prepared['beats']:
            for rank,module in enumerate(prepared['modules']):
                landing=beat['in']+rank*beat['cascade_frames']+beat['move_frames']
                _,states=bauhaus._state(prepared,landing)
                self.assertEqual(states[rank]['progress'],1)
                placement=prepared['layouts'][beat['target']][module['id']]
                center=bauhaus._center(prepared,module,placement)
                goal=[bauhaus._rotate(point,placement['turn']*math.pi/2) for point in module['polygon']]
                for actual,relative in zip(states[rank]['points'],goal):
                    self.assertAlmostEqual(actual[0],center[0]+relative[0],places=8)
                    self.assertAlmostEqual(actual[1],center[1]+relative[1],places=8)
        _,mid=bauhaus._state(prepared,125)
        self.assertGreater(mid[0]['progress'],0)
        self.assertEqual(mid[-1]['progress'],0)
        self.assertEqual([s['axis'] for s in mid],['center','corner','frame','center','corner','frame'])

    def test_final_poster_has_real_readable_hold_and_visible_primaries(self):
        from PIL import ImageChops
        prepared=bauhaus._prepare(example(),180)
        first=bauhaus._render(prepared,155).crop((96,220,1824,880))
        last=bauhaus._render(prepared,179).crop((96,220,1824,880))
        self.assertIsNone(ImageChops.difference(first,last).getbbox())
        histogram=dict((color,count) for count,color in last.getcolors(last.width*last.height))
        for color in prepared['palette'][1:]:
            rgb=tuple(int(color[i:i+2],16) for i in (1,3,5))
            self.assertGreater(histogram.get(rgb,0),1000)
        opening=bauhaus._render(prepared,0).crop((96,220,1824,880))
        self.assertIsNotNone(ImageChops.difference(first,opening).getbbox())

    def test_out_of_order_calls_are_pixel_identical(self):
        from PIL import ImageChops
        data=example()
        for frame in (0,25,60,75,125,145,179):
            expected=bauhaus.render_bauhaus_frame(data,frame,180)
            bauhaus.render_bauhaus_frame(data,179-frame,180)
            actual=bauhaus.render_bauhaus_frame(data,frame,180)
            self.assertIsNone(ImageChops.difference(expected,actual).getbbox())
            self.assertEqual(actual.size,(1920,1080))

    def test_different_geometry_changes_pixels_with_same_text_and_colors(self):
        from PIL import ImageChops
        a=example();b=copy.deepcopy(a)
        b['modules'][0]['shape']='square'
        left=bauhaus.render_bauhaus_frame(a,179,180).crop((96,220,1824,880))
        right=bauhaus.render_bauhaus_frame(b,179,180).crop((96,220,1824,880))
        self.assertIsNotNone(ImageChops.difference(left,right).getbbox())

    def test_invalid_schema_and_finite_values_fail_before_output(self):
        with tempfile.TemporaryDirectory() as temp:
            for mutation in ('nan','bool_span','float_cell','missing_module','bad_tick','short_hold','unsigned_seed','bool_seed','axis','trajectory'):
                data=example()
                if mutation=='nan':data['modules'][0]['span']=float('nan')
                if mutation=='bool_span':data['modules'][0]['span']=True
                if mutation=='float_cell':data['layouts']['focus'][0]['cell'][0]=3.0
                if mutation=='missing_module':data['layouts']['focus'].pop()
                if mutation=='bad_tick':data['tick_frames']=7
                if mutation=='short_hold':data['beats'][0]['cascade_frames']=10
                if mutation=='unsigned_seed':data['seed']=2**31
                if mutation=='bool_seed':data['seed']=False
                if mutation=='axis':data['beats'][0]['axis']='bounce'
                if mutation=='trajectory':
                    data['layouts']['rhythm'][2]['cell']=[10,0]
                    data['beats'][2]['axis']='frame'
                output=Path(temp)/mutation
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                    bauhaus.render_bauhaus_frames(data,180,output)
                self.assertFalse(output.exists())

    def test_cjk_fit_and_missing_glyphs_are_preflighted(self):
        for text in ('长'*120,'\U0010ffff'):
            data=example();data['title']=text
            with self.assertRaises(ValueError):bauhaus.validate_bauhaus_content(data,180)
        latin=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
        if latin.is_file():
            with self.assertRaisesRegex(ValueError,'lacks glyph'):
                bauhaus.validate_bauhaus_content(example(),180,latin)
        data=example();data['font_path']=str(FONT)
        self.assertEqual(bauhaus.validate_bauhaus_content(data,180)['font_license'],'verify separately')

    def test_valid_one_frame_settled_boundary_and_real_sequence_receipt(self):
        import hashlib
        data=example()
        data['modules']=data['modules'][:2]
        for name in data['layouts']:data['layouts'][name]=data['layouts'][name][:2]
        data['tick_frames']=1
        data['beats']=[dict(**{'in':0,'out':15},label='先对齐',target='focus',axis='center',move_frames=14,cascade_frames=0),
                       dict(**{'in':15,'out':30},label='再构成',target='poster',axis='center',move_frames=14,cascade_frames=0)]
        prepared=bauhaus._prepare(data,30)
        self.assertTrue(all(state['progress']==1 for state in bauhaus._state(prepared,29)[1]))
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'actual'
            receipt=bauhaus.render_bauhaus_frames(data,30,output)
            self.assertEqual(len(list(output.glob('f*.png'))),30)
            for frame,expected in enumerate(receipt['frame_sha256']):self.assertEqual(hashlib.sha256((output/f'f{frame:04d}.png').read_bytes()).hexdigest(),expected)
            self.assertEqual(json.loads((output/'evidence.json').read_text()),receipt)
            with self.assertRaises(FileExistsError):bauhaus.render_bauhaus_frames(data,30,output)


if __name__=='__main__':unittest.main()
