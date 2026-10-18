"""Half-frame lead must not truncate to a whole-frame PNG sampling error."""
import contextlib
import io
import json
import hashlib
import os
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vtx.composite import RenderPlan
from vtx.constraints import fps_error
from vtx.frame_source import PngSequence
from tests.test_png_format_stability import png


class TestPngTiming(unittest.TestCase):
    def test_only_png_input_timebase_changes(self):
        c=SimpleNamespace(fps=30,spf=1/30)
        plan=RenderPlan(c=c,draft=False,f=1,W=32,H=16)
        base=dict(cl={'dst_in':18,'dur':12},x=0,y=0)
        png_slave,png_overlay,_=plan._layer_chain(dict(base,gfx=True),0,'cv',0,40,1)
        video_slave,video_overlay,_=plan._layer_chain(dict(base,gfx=False),0,'cv',0,40,1)
        self.assertEqual(png_slave.replace('settb=1/60,',''),video_slave)
        self.assertEqual(png_overlay,video_overlay)
        for invalid in (29.97,30.5,60,True): self.assertIsNotNone(fps_error(invalid))
        for valid in (30,30.0): self.assertIsNone(fps_error(valid))

    def test_negative_global_destination_remains_rejected(self):
        from vtx.infra import Ctx
        from vtx.stages import validate
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'page.html').write_text('<html></html>')
            timeline=dict(version=2,project=dict(fps=30,width=1920,height=1080,duration_frames=30),
                          sources=[],subtitles=[],audio_tracks=[],video_tracks=[dict(name='gfx',kind='gfx',
                          clips=[dict(src='page.html',dst_in=-1,dur=12)])])
            path=root/'timeline.json';path.write_text(json.dumps(timeline))
            output=io.StringIO()
            with contextlib.redirect_stdout(output),self.assertRaises(SystemExit):validate(Ctx(path))
            self.assertIn('dst_in=-1',output.getvalue())

    @unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'),'set VTX_TEST_FFMPEG')
    def test_real_n1_first_interior_tail_and_repeat_are_timeline_correct(self):
        # Full N1, never range or passes. Direct frame-number oracle is derived
        # from dst, not from the old graph or comparison between two wrong runs.
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.dict(os.environ,{'VTX_FILTER_THREADS':'1','VTX_NO_NVENC':'1'}):
            root=Path(td)
            for dst,n in [(d,12) for d in (0,1,2,18,330)]+[(d,1) for d in (0,1,18)]:
                source=root/f'dst{dst}-n{n}';source.mkdir()
                for i in range(n):
                    row=bytes(((i+1)*16,32,64,255))*32
                    (source/f'f{i:04d}.png').write_bytes(png([row]*16,32,4))
                total=dst+n+4
                c=SimpleNamespace(fps=30,spf=1/30,dur=total,w=32,h=16,dry=False,cache=root,gpu=False)
                layer=dict(cl={'dst_in':dst,'dur':n},gfx=True,
                           source=PngSequence(source,source.name,n,30,32,16),x=0,y=0)
                plan=RenderPlan(c=c,draft=False,f=1,W=32,H=16,nseg=1,layers=[layer])
                commands=[];c.run=lambda cmd:commands.append(cmd)
                plan.composite([layer],root/'unused.mp4',False)
                cmd=commands[0]
                hashes=[]
                for attempt in range(3):
                    argv=cmd[:cmd.index('-c:v')]+['-c:v','rawvideo','-pix_fmt','rgb24','-f','rawvideo','-']
                    raw=subprocess.check_output(argv,stderr=subprocess.PIPE,timeout=15)
                    self.assertEqual(len(raw),total*32*16*3)
                    hashes.append(hashlib.sha256(raw).hexdigest())
                    for frame in range(total):
                        # Preserve the pre-existing six-decimal enable endpoint:
                        # the task fixes selection, not inclusive-end policy.
                        active=frame>=dst and Fraction(frame,30)<=Fraction(f'{(dst+n)/30:.6f}')
                        expected=min(frame-dst,n-1)+1 if active else 0
                        observed=round(raw[frame*32*16*3]/16)
                        self.assertEqual(observed,expected,(dst,n,attempt,frame))
                self.assertEqual(len(set(hashes)),1)

    @unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'),'set VTX_TEST_FFMPEG')
    def test_negative_relative_seek_uses_current_then_next_source_frame(self):
        # A negative global dst is invalid. This is the shared helper's legal
        # cross-segment condition dst=18,a=20, not a substitute N1/range test.
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.dict(os.environ,{'VTX_FILTER_THREADS':'1','VTX_NO_NVENC':'1'}):
            root=Path(td);source=root/'seq';source.mkdir()
            for i in range(12):
                row=bytes(((i+1)*16,32,64,255))*32
                (source/f'f{i:04d}.png').write_bytes(png([row]*16,32,4))
            c=SimpleNamespace(fps=30,spf=1/30,dur=34,w=32,h=16,dry=False,cache=root,gpu=False)
            layer=dict(cl={'dst_in':18,'dur':12},gfx=True,
                       source=PngSequence(source,'seq',12,30,32,16),x=0,y=0)
            plan=RenderPlan(c=c,draft=False,f=1,W=32,H=16,nseg=1,layers=[layer])
            cmds=[];c.run=lambda cmd:cmds.append(cmd)
            plan.composite([layer],root/'unused.mp4',False,a=20)
            cmd=cmds[0];argv=cmd[:cmd.index('-c:v')]+['-c:v','rawvideo','-pix_fmt','rgb24','-f','rawvideo','-']
            raw=subprocess.check_output(argv,stderr=subprocess.PIPE,timeout=15)
            self.assertEqual([round(raw[i*32*16*3]/16) for i in range(10)],list(range(3,13)))

if __name__=='__main__':unittest.main()
