"""Half-frame lead must not truncate to a whole-frame PNG sampling error."""
import contextlib
import io
import json
import hashlib
import os
import re
import time
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


def _timing_failure_matrix(root, budget=30.0):
    """Bounded failure-only diagnostics; observations never replace the oracle."""
    root.mkdir()
    deadline = time.monotonic() + budget
    env = {k: v for k, v in os.environ.items() if k != 'FFREPORT'}
    cases = ([(d, 12, 0) for d in (0, 1, 2, 18, 330)]
             + [(d, 1, 0) for d in (0, 1, 18)] + [(18, 12, 20), (18, 12, 16)])
    variants = ('current-half', 'parent-whole-tb', 'integer-clone', 'integer-transparent',
                'main-t-x-gating')
    completed = 0
    timeouts = 0
    print('PNG_TIMING_DIAGNOSTIC_BEGIN ' + json.dumps(
        dict(budget_seconds=budget, subprocess_timeout_seconds=3, attempts_per_case=2,
             cases=cases, variants=variants)), flush=True)
    for dst, n, a in cases:
        if time.monotonic() >= deadline:
            break
        source = root / f'dst{dst}-n{n}-a{a}'
        source.mkdir()
        inputs = []
        for i in range(n):
            row = bytes(((i+1)*16, 32, 64, 255))*32
            data = png([row]*16, 32, 4)
            name = f'f{i:04d}.png'
            (source/name).write_bytes(data)
            inputs.append(dict(name=name, source_id=i+1, sha256=hashlib.sha256(data).hexdigest()))
        print('PNG_TIMING_DIAGNOSTIC_INPUT ' + json.dumps(
            dict(case=[dst, n, a], frames=inputs)), flush=True)
        total = dst+n+4
        c = SimpleNamespace(fps=30, spf=1/30, dur=total, w=32, h=16,
                            dry=False, cache=root, gpu=False)
        layer = dict(cl={'dst_in': dst, 'dur': n}, gfx=True,
                     source=PngSequence(source, source.name, n, 30, 32, 16), x=0, y=0)
        plan = RenderPlan(c=c, draft=False, f=1, W=32, H=16, nseg=1, layers=[layer])
        commands = []
        c.run = lambda cmd: commands.append(cmd)
        plan.composite([layer], root/'unused.mp4', False, a=a)
        command = commands[0]
        graph = command[command.index('-filter_complex')+1]
        rel = dst-a
        eff = max(rel, 0)
        head = int(eff > 0)
        stop = max(1, total-dst-n+2)
        m = ((dst+n)*10**6) % 30
        endpoint = ((rel+n+(0.25 if m == 0 or 2*m > 30 else -0.5))/30
                    if a else (dst+n)/30)
        end = Fraction(f'{endpoint:.6f}')
        expected = [min(frame-rel, n-1)+1
                    if frame >= max(rel, 0) and Fraction(frame, 30) <= end else 0
                    for frame in range(total-a)]
        for variant in variants:
            alternate = graph
            if variant == 'parent-whole-tb':
                alternate = graph.replace('settb=1/60,', '')
            elif variant.startswith('integer-'):
                # Force actual alpha before an added transparent head, including
                # uniform RGB PNG inputs. All variants retain the same endpoint.
                start = (f'start={head}:start_mode='
                         + ('add:color=black@0:' if variant.endswith('transparent') else 'clone:')) if head else ''
                slave = (f'[1:v]format=rgba,settb=1/30,tpad={start}stop={stop}:stop_mode=clone,'
                         f'setpts=PTS+{eff-head}[L0T]')
                alternate = re.sub(r'\[1:v\].*?\[L0T\]', slave, graph, count=1)
            elif variant == 'main-t-x-gating':
                window = re.search(r"enable='([^']+)'", graph).group(1)
                alternate = graph.replace('overlay=0:0:', f"overlay=x='if({window},0,NAN)':y=0:")
                alternate = alternate.replace(f"enable='{window}':", '')
            # showinfo is observational and reports graph PTS before output CFR.
            alternate = alternate.replace('[L0]null[vout]', '[L0]showinfo@timing[vout]')
            argv = command[:command.index('-c:v')]
            argv[argv.index('-v')+1] = 'info'
            argv[argv.index('-filter_complex')+1] = alternate
            argv += ['-c:v', 'rawvideo', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-']
            for attempt in range(2):
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    print('PNG_TIMING_DIAGNOSTIC_END ' + json.dumps(
                        dict(completed=completed, timeouts=timeouts, status='budget-exhausted')), flush=True)
                    return
                record = dict(case=[dst, n, a], variant=variant, attempt=attempt)
                if attempt == 0:
                    record['argv'] = argv
                try:
                    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            cwd=root, env=env, timeout=min(3.0, remaining))
                    raw = result.stdout
                    observed = [round(raw[k]/16) for k in range(0, len(raw), 32*16*3)]
                    stderr = result.stderr.decode('utf-8', 'replace')
                    tb = re.search(r'showinfo@timing[^\n]*config in time_base: (\d+)/(\d+)', stderr)
                    pts = [int(x) for x in re.findall(
                        r'showinfo@timing[^\n]*n:\s*\d+\s+pts:\s*(-?\d+)', stderr)]
                    timebase = Fraction(int(tb[1]), int(tb[2])) if tb else None
                    grid = (timebase is not None and len(pts) >= total-a and
                            all(p*timebase == Fraction(i, 30) for i, p in enumerate(pts[:total-a])))
                    wrong = [(i, wanted, observed[i] if i < len(observed) else None)
                             for i, wanted in enumerate(expected)
                             if i >= len(observed) or observed[i] != wanted]
                    record.update(exit=result.returncode, bytes=len(raw), frames=len(observed),
                                  expected_frames=total-a, wrong_count=len(wrong), first_wrong=wrong[:8],
                                  sha256=hashlib.sha256(raw).hexdigest(), observed_ids=observed, expected_ids=expected,
                                  graph_time_base=str(timebase), graph_pts=pts, graph_pts_on_grid=grid)
                    if result.returncode:
                        record['stderr_tail'] = result.stderr[-2000:].decode('utf-8', 'replace')
                except subprocess.TimeoutExpired:
                    timeouts += 1
                    record['status'] = 'subprocess-timeout'
                completed += 1
                print('PNG_TIMING_DIAGNOSTIC_ATTEMPT ' + json.dumps(record), flush=True)
    print('PNG_TIMING_DIAGNOSTIC_END ' + json.dumps(
        dict(completed=completed, timeouts=timeouts,
             status=('budget-exhausted' if completed < len(cases)*len(variants)*2
                     else 'incomplete-timeouts' if timeouts else 'complete'))), flush=True)


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
                        try:
                            self.assertEqual(observed,expected,(dst,n,attempt,frame))
                        except AssertionError:
                            try:
                                _timing_failure_matrix(root/'timing-diagnostics')
                            except Exception as diagnostic_error:
                                try:
                                    print('PNG_TIMING_DIAGNOSTIC_ERROR ' + repr(diagnostic_error), flush=True)
                                except Exception:
                                    pass  # Best-effort logging must not replace the oracle failure.
                            raise  # Preserve the exact original oracle failure.
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
