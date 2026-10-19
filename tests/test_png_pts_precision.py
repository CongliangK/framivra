"""Full N1 PNG sampling follows integer frames and half-open clip windows."""
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
    def _assert_full_n1_scope(self):
        # The explicit call shape, not nseg alone, controls the new PNG policy.
        # This same fast contract runs inside the selected real N1 regression.
        for a,b,draft,nframes,wanted in ((0,None,False,None,True),
                (0,40,False,None,False),(20,None,False,None,False),
                (0,None,True,10,False),(0,None,True,None,True)):
            c=SimpleNamespace(fps=30,spf=1/30,dur=40,w=32,h=16,dry=True,
                              cache=Path('.'),gpu=False)
            layer=dict(cl={'dst_in':18,'dur':12},gfx=True,x=3,y=-2,
                       source=PngSequence(Path('.'),'fixture',12,30,32,16))
            plan=RenderPlan(c=c,draft=draft,f=.5 if draft else 1,W=32,H=16,
                            nframes=nframes,nseg=1,layers=[layer])
            commands=[];c.run=lambda cmd:commands.append(cmd)
            plan.composite([layer],Path('unused.mp4'),False,a=a,b=b)
            graph=commands[0][commands[0].index('-filter_complex')+1]
            self.assertEqual('overlay=x=' in graph,wanted,(a,b,draft,nframes))
            self.assertEqual("enable='" in graph,not wanted)
        # Non-PNG callers are byte-identical even if their graph opts in.
        layer=dict(cl={'dst_in':18,'dur':12},gfx=False,x=3,y=-2)
        self.assertEqual(plan._layer_chain(layer,0,'cv',0,40,1),
                         plan._layer_chain(layer,0,'cv',0,40,1,n1_png=True))
        # A two-layer segment still routes to passes, never the N1 clock.
        with mock.patch.object(plan,'_composite_passes',return_value='pass') as passes:
            self.assertEqual(plan.composite([layer,layer],Path('unused.mp4'),False,b=40),'pass')
            passes.assert_called_once()

    def test_only_png_input_timebase_changes(self):
        self._assert_full_n1_scope()
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
        self._assert_full_n1_scope()
        # Full N1, never range or passes. Direct frame-number oracle is derived
        # from dst, not from the old graph or comparison between two wrong runs.
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.dict(os.environ,{'VTX_FILTER_THREADS':'1','VTX_NO_NVENC':'1'}):
            root=Path(td)
            cases=[(d,12,False) for d in (0,1,2,9,18,63,124,125,330)]
            cases += [(d,1,False) for d in (0,1,18)]+[(63,12,True)]
            for dst,n,draft in cases:
                W,H=(16,8) if draft else (32,16)
                frame_bytes=W*H*3
                source=root/f'dst{dst}-n{n}-draft{draft}';source.mkdir()
                for i in range(n):
                    row=bytes(((i+1)*16,32,64,255))*32
                    (source/f'f{i:04d}.png').write_bytes(png([row]*16,32,4))
                total=dst+n+4
                c=SimpleNamespace(fps=30,spf=1/30,dur=total,w=32,h=16,dry=False,cache=root,gpu=False)
                layer=dict(cl={'dst_in':dst,'dur':n},gfx=True,
                           source=PngSequence(source,source.name,n,30,32,16),x=0,y=0)
                plan=RenderPlan(c=c,draft=draft,f=.5 if draft else 1,W=W,H=H,nseg=1,layers=[layer])
                commands=[];c.run=lambda cmd:commands.append(cmd)
                plan.composite([layer],root/'unused.mp4',False)
                cmd=commands[0]
                hashes=[]
                for attempt in range(3):
                    argv=cmd[:cmd.index('-c:v')]+['-c:v','rawvideo','-pix_fmt','rgb24','-f','rawvideo','-']
                    raw=subprocess.check_output(argv,stderr=subprocess.PIPE,timeout=15)
                    self.assertEqual(len(raw),total*frame_bytes)
                    hashes.append(hashlib.sha256(raw).hexdigest())
                    for frame in range(total):
                        # Documented [dst,dst+dur), not the old six-decimal
                        # inclusive endpoint that repeated the last PNG.
                        active=dst <= frame < dst+n
                        expected=frame-dst+1 if active else 0
                        observed=round(raw[frame*frame_bytes]/16)
                        self.assertEqual(observed,expected,(dst,n,attempt,frame))
                        pixel=bytes((expected*16,32,64)) if active else bytes(3)
                        self.assertTrue(raw[frame*frame_bytes:(frame+1)*frame_bytes] == pixel*(W*H),
                                        (dst,n,attempt,frame,'full-frame pixels'))
                self.assertEqual(len(set(hashes)),1)

    @unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'),'set VTX_TEST_FFMPEG')
    def test_real_n1_multilayer_alpha_positions_and_packet_pts(self):
        # Independent RGB source-over oracle, including invisible hidden RGB,
        # cropped negative coordinates, overlap, and mixed opaque RGB/RGBA PNG.
        W,H,total=32,16,16
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.dict(os.environ,{'VTX_FILTER_THREADS':'1','VTX_NO_NVENC':'1'}):
            root=Path(td);layers=[];specs=[];originals={}
            for k,(dst,n,x,y) in enumerate(((0,16,0,0),(2,8,3,-2),(7,6,-3,3),(10,4,3,-2))):
                directory=root/f'layer{k}';directory.mkdir();frames=[]
                for i in range(n):
                    opaque=k==0 or (k==1 and i==3)
                    rgb=k==0 or (k==1 and i==3)
                    pixels=[(16+4*i,24,80,255) if k==0 else
                            (40+16*i,80+px,160-py,255 if opaque else (0,127,255)[(px+py)%3])
                            for py in range(H) for px in range(W)]
                    frames.append(pixels)
                    rows=[bytes(v for pixel in pixels[py*W:(py+1)*W]
                                for v in pixel[:3 if rgb else 4]) for py in range(H)]
                    data=png(rows,W,3 if rgb else 4)
                    path=directory/f'f{i:04d}.png';path.write_bytes(data)
                    originals[path]=hashlib.sha256(data).hexdigest()
                source=PngSequence(directory,f'layer{k}',n,30,W,H)
                layers.append(dict(cl={'dst_in':dst,'dur':n},gfx=True,source=source,x=x,y=y))
                specs.append((dst,n,x,y,frames))
            expected=[]
            for frame in range(total):
                canvas=[(0,0,0)]*(W*H)
                for dst,n,x,y,frames in specs:
                    if not dst <= frame < dst+n: continue
                    pixels=frames[frame-dst]
                    for py in range(max(0,y),min(H,y+H)):
                        for px in range(max(0,x),min(W,x+W)):
                            r,g,b,alpha=pixels[(py-y)*W+px-x]
                            old=canvas[py*W+px]
                            canvas[py*W+px]=tuple((v*alpha+u*(255-alpha)+127)//255
                                                       for u,v in zip(old,(r,g,b)))
                expected.append(bytes(v for pixel in canvas for v in pixel))
            env={k:v for k,v in os.environ.items() if k!='FFREPORT'}
            def run(argv):
                return subprocess.check_output(argv,cwd=root,env=env,stderr=subprocess.PIPE,timeout=15)
            c=SimpleNamespace(fps=30,spf=1/30,dur=total,w=W,h=H,dry=False,cache=root,gpu=False)
            plan=RenderPlan(c=c,draft=False,f=1,W=W,H=H,nseg=1,layers=layers)
            hashes=[]
            for attempt in range(2):
                commands=[];c.run=lambda cmd:commands.append(cmd)
                plan.composite(layers,root/'unused.mp4',False)
                cmd=commands[0];output=root/f'actual-{attempt}.nut'
                argv=cmd[:cmd.index('-c:v')]+['-c:v','ffv1','-threads','1','-pix_fmt','bgra','-f','nut',str(output)]
                run(argv)
                raw=run(['ffmpeg','-v','error','-threads','1','-i',str(output),
                         '-pix_fmt','rgb24','-f','rawvideo','-'])
                self.assertEqual(len(raw),total*W*H*3)
                hashes.append(hashlib.sha256(raw).hexdigest())
                for frame,wanted in enumerate(expected):
                    observed=raw[frame*W*H*3:(frame+1)*W*H*3]
                    if observed != wanted:
                        at=next(i for i,(a,b) in enumerate(zip(observed,wanted)) if a!=b)
                        self.fail((attempt,frame,'pixel',at//3,'channel',at%3,
                                   observed[at],wanted[at]))
                probe=json.loads(run(['ffprobe','-v','error','-select_streams','v:0',
                    '-show_entries','stream=time_base:frame=pts,pkt_dts','-of','json',str(output)]))
                tb=Fraction(probe['streams'][0]['time_base'])
                self.assertEqual([int(f['pts'])*tb for f in probe['frames']],
                                 [Fraction(i,30) for i in range(total)])
                packets=json.loads(run(['ffprobe','-v','error','-select_streams','v:0',
                    '-show_entries','packet=pts,dts,duration','-of','json',str(output)]))['packets']
                self.assertEqual(len(packets),total)
                for i,p in enumerate(packets):
                    self.assertEqual(int(p['pts'])*tb,Fraction(i,30))
                    self.assertEqual(int(p['dts'])*tb,Fraction(i,30))
                    self.assertEqual(int(p['duration'])*tb,Fraction(1,30))
                self.assertEqual({p:hashlib.sha256(p.read_bytes()).hexdigest() for p in originals},originals)
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
