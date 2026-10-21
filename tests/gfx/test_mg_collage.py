"""Actual Pillow paper-puppet pixels, held exposures and source-schema checks."""
import copy
from tests.gfx._mg import load_renderer
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
collage = load_renderer('mg_collage',ROOT/'templates/mg-styles/styles/collage/scripts/render_collage.py')
FONT=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
try:
    from PIL import Image,ImageChops
except ImportError:
    Image=None


def content():
    def part(id,shape,parent,x,y,scale,pivot,hinge,drift):
        return dict(id=id,shape=shape,parent=parent,x=x,y=y,scale=scale,angle=0,pivot=pivot,motion=dict(hinge=hinge,drift=drift))
    return {'title':'让旧习惯换个角度','subtitle':'原创程序纸偶，不是照片拼贴','exposure':3,'seed':-7,
            'parts':[part('head','head',None,.45,.5,140,[0,0],15,[0,0]),
                     part('jaw','jaw','head',0,.6,60,[-.8,0],28,[0,0]),
                     part('rocket','rocket',None,.7,.6,60,[0,0],35,[-80,-40])],
            'beats':[{'mode':'awaken','in':0,'out':24,'text':'提出疑问','active':['head','jaw']},
                     {'mode':'release','in':24,'out':48,'text':'想法跳出框架','active':['rocket']},
                     {'mode':'reframe','in':48,'out':72,'text':'撕开旧叙事','active':['rocket']},
                     {'mode':'resolve','in':72,'out':96,'text':'重新组合成新答案','active':[]}],
            'evidence':[{'label':'样例','value':'一组','source':'人工虚构演示'}]}


@unittest.skipUnless(Image is not None and FONT.is_file(),'Pillow and separately licensed CJK font required')
class CollagePuppet(unittest.TestCase):
    def frame(self,data,frame):return collage.render_collage_frame(data,frame,96,FONT)

    def test_whole_frame_exposure_and_seek_order(self):
        data=content();held=self.frame(data,6)
        self.assertEqual(held.mode,'RGB');self.assertEqual(held.size,(1920,1080))
        self.assertEqual(held.tobytes(),self.frame(data,7).tobytes())
        self.assertEqual(held.tobytes(),self.frame(data,8).tobytes())
        self.assertNotEqual(held.tobytes(),self.frame(data,9).tobytes())
        for frame in (95,0,48,27):self.frame(data,frame)
        self.assertEqual(held.tobytes(),self.frame(data,6).tobytes())

    def test_hinges_shape_parent_and_story_change_real_scene(self):
        data=content();original=self.frame(data,9)
        changed=copy.deepcopy(data);changed['parts'][1]['pivot']=[.8,0]
        self.assertIsNotNone(ImageChops.difference(original.crop((500,350,1250,850)),self.frame(changed,9).crop((500,350,1250,850))).getbbox())
        changed=copy.deepcopy(data);changed['parts'][2]['shape']='leaf'
        self.assertNotEqual(self.frame(data,36).tobytes(),self.frame(changed,36).tobytes())
        changed=copy.deepcopy(data);changed['beats'][3]['text']='用中文讲另一个结局'
        self.assertNotEqual(self.frame(data,95).tobytes(),self.frame(changed,95).tobytes())
        prepared=collage._prepare(data,96,FONT)
        # Moving the parent must carry a child's local coordinate system.
        a=collage._state(prepared,6)[3]['jaw'];b=collage._state(prepared,9)[3]['jaw']
        self.assertNotEqual(a[:3],b[:3])

    def test_parent_rigid_rotation_cannot_translate_a_zero_relative_angle_child(self):
        for angle in (90,-90,35):
            data=content()
            parent,child=data['parts'][:2]
            parent['angle']=angle;parent['motion']['hinge']=0
            child.update(scale=100,angle=0,pivot=[.5,0])
            child['motion']['hinge']=0
            prepared=collage._prepare(data,96,FONT)
            transform=collage._state(prepared,9)[3]
            px,py,pa,ps=transform['head']
            dx,dy=collage._rot(child['x']*ps,child['y']*ps,pa)
            with self.subTest(parent_angle=angle):
                self.assertAlmostEqual(transform['jaw'][0],px+dx,places=9)
                self.assertAlmostEqual(transform['jaw'][1],py+dy,places=9)
                # With no relative rotation the pivot is visually irrelevant.
                zero_pivot=copy.deepcopy(data);zero_pivot['parts'][1]['pivot']=[0,0]
                self.assertIsNone(ImageChops.difference(self.frame(data,9),self.frame(zero_pivot,9)).getbbox())

    def test_parent_rotation_rigidly_transports_child_geometry_with_relative_hinge(self):
        data=content()
        data['parts'][0]['motion']['hinge']=0
        data['parts'][1].update(scale=100,angle=30,pivot=[.5,0])
        data['parts'][1]['motion']['hinge']=0
        base=collage._prepare(data,96,FONT)
        transforms=collage._state(base,95)[3]
        child=base['parts'][1]
        unrotated=collage._points(child,transforms['jaw'])
        cx,cy=transforms['head'][:2]
        for angle in (90,-90,35):
            rotated=copy.deepcopy(data);rotated['parts'][0]['angle']=angle
            prepared=collage._prepare(rotated,96,FONT)
            actual=collage._points(prepared['parts'][1],collage._state(prepared,95)[3]['jaw'])
            for (x,y),(ax,ay) in zip(unrotated,actual):
                dx,dy=collage._rot(x-cx,y-cy,angle)
                self.assertAlmostEqual(ax,cx+dx,places=8)
                self.assertAlmostEqual(ay,cy+dy,places=8)

    def test_rigid_parent_rotation_propagates_through_nonzero_grandchild_pivots(self):
        data=content()
        data['parts'][0]['motion']['hinge']=0
        data['parts'][1].update(scale=100,angle=0,pivot=[.5,0]);data['parts'][1]['motion']['hinge']=0
        grandchild=data['parts'][2]
        grandchild.update(parent='jaw',x=.1,y=.4,scale=40,angle=0,pivot=[.5,0])
        grandchild['motion']={'hinge':0,'drift':[0,0]}
        base=collage._prepare(data,96,FONT);initial=collage._state(base,95)[3]
        points=collage._points(grandchild,initial['rocket']);cx,cy=initial['head'][:2]
        data['parts'][0]['angle']=90
        prepared=collage._prepare(data,96,FONT)
        actual=collage._points(grandchild,collage._state(prepared,95)[3]['rocket'])
        for (x,y),(ax,ay) in zip(points,actual):
            dx,dy=collage._rot(x-cx,y-cy,90)
            self.assertAlmostEqual(ax,cx+dx,places=8);self.assertAlmostEqual(ay,cy+dy,places=8)

    def test_transition_is_actual_torn_paper_cover_not_text_change(self):
        data=content();covered=self.frame(data,54);later=self.frame(data,69)
        self.assertNotEqual(covered.crop((200,320,1750,930)).tobytes(),later.crop((200,320,1750,930)).tobytes())
        self.assertGreater(sum(count for count,color in covered.crop((700,350,1200,850)).getcolors(250000) if color==(255,252,239)),10000)

    def test_invalid_sources_geometry_text_and_exposure_write_nothing(self):
        invalid=[]
        for seed in (True,2**31,-2**31-1):
            data=content();data['seed']=seed;invalid.append(data)
        data=content();data['parts'][0].update(x=.9,scale=220);invalid.append(data)
        data=content();data['parts'][0]['motion']['hinge']=float('nan');invalid.append(data)
        data=content();data['parts'][0]['parent']='jaw';invalid.append(data)
        data=content();data['beats'][1]['in']=25;invalid.append(data)
        data=content();data['beats'][0]['active']=['missing'];invalid.append(data)
        data=content();data['evidence'][0]['source']='';invalid.append(data)
        data=content();data['title']='坏\n标题';invalid.append(data)
        data=content();data['title']='\U0010ffff';invalid.append(data)
        data=content();data['beats'][0]['text']='界'*100;invalid.append(data)
        data=content();data['exposure']=True;invalid.append(data)
        with tempfile.TemporaryDirectory() as temp:
            for i,data in enumerate(invalid):
                with self.subTest(i=i):
                    output=Path(temp)/str(i)
                    with self.assertRaises(ValueError):collage.render_collage_frames(data,96,output,FONT)
                    self.assertFalse(output.exists())

    def test_sequence_evidence_matches_single_frame_and_no_overwrite(self):
        data=content()
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'collage'
            report=collage.render_collage_frames(data,96,output,FONT)
            self.assertEqual(len(list(output.glob('f*.png'))),96)
            self.assertEqual(report['source_type'],'original_procedural_cutouts_not_photographs')
            self.assertEqual(report,json.loads((output/'evidence.json').read_text()))
            self.assertEqual(report['frame_sha256'][6:9],[report['frame_sha256'][6]]*3)
            for frame in (0,24,48,95):
                with Image.open(output/f'f{frame:04d}.png') as actual:
                    self.assertEqual(actual.tobytes(),self.frame(data,frame).tobytes())
            with self.assertRaises(ValueError):collage.render_collage_frames(data,96,output,FONT)

    def test_two_authored_examples_replace_ratio_and_choreography(self):
        images=[]
        for name,frames in (('collage-curiosity.json',240),('collage-perspective.json',180)):
            data=json.loads((ROOT/'templates/mg-styles/styles/collage/assets/examples'/name).read_text())
            collage.validate_collage_content(data,frames,FONT)
            images.append(collage.render_collage_frame(data,frames-1,frames,FONT).tobytes())
        self.assertNotEqual(*images)


if __name__=='__main__':unittest.main()
