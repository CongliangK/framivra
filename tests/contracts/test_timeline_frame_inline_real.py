"""Opt-in *new* 16-frame AV mechanism fixture (not the retired 12-frame/900f baseline).

VTX_TEST_INLINE16=1 authorizes official validate/prepare/prepare_layers writes only
inside a new external temporary fixture. Frames themselves may write only preview.
VTX_INLINE_FIXTURE_ROOT optionally names an absent owned directory to retain proof.
No gfx/Chrome/Inkscape, final, QC, plugins, models or full-project performance claim.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from vtx.api import frame_timeline
from tests.contracts.test_timeline_frame_inline import decode_inline, REPO


def fence(root):
    return {str(p.relative_to(root)): (p.stat().st_mode, p.stat().st_size, p.stat().st_mtime_ns,
            p.stat().st_ctime_ns, hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
            for p in root.rglob('*') if 'preview' not in p.relative_to(root).parts and p.name != 'cache'}


@unittest.skipUnless(os.environ.get('VTX_TEST_INLINE16') == '1' and shutil.which('ffmpeg'),
                     'set VTX_TEST_INLINE16=1 to authorize new external warm16 AV preparation')
class WarmInline16(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {'VTX_NO_NVENC': '1', 'VTX_SEG_NVENC': '0',
                                                'VTX_FILTER_THREADS': '1', 'VTX_IO_WORKERS': '1'})
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        if os.environ.get('VTX_INLINE_FIXTURE_ROOT'):
            cls.root = Path(os.environ['VTX_INLINE_FIXTURE_ROOT'])
            cls.root.mkdir(exist_ok=False)
        else:
            cls.temp = tempfile.TemporaryDirectory(prefix='inline-warm16-')
            cls.addClassCleanup(cls.temp.cleanup)
            cls.root = Path(cls.temp.name)
        cls.path = cls.root / 'timeline.json'
        command = ['ffmpeg', '-v', 'error', '-y', '-filter_threads', '1', '-f', 'lavfi', '-i',
                   'color=c=blue:s=1920x1080:r=30:d=0.5333333333333333', '-f', 'lavfi', '-i',
                   'sine=frequency=440:sample_rate=48000:duration=0.5333333333333333',
                   '-frames:v', '16', '-shortest', '-c:v', 'ffv1', '-level', '3', '-g', '1',
                   '-threads', '1', '-pix_fmt', 'yuv420p', '-c:a', 'pcm_s16le', str(cls.root / 'source.mkv')]
        p = subprocess.run(command, capture_output=True, text=True)
        if p.returncode:
            raise AssertionError(p.stderr)
        red_command = [arg.replace('color=c=blue:', 'color=c=red:') for arg in command]
        red_command[-1] = str(cls.root / 'red.mkv')
        p = subprocess.run(red_command, capture_output=True, text=True)
        if p.returncode:
            raise AssertionError(p.stderr)
        cls.doc = {'version': 2, 'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 16},
            'sources': [{'id': 's', 'path': 'source.mkv'}, {'id': 'red', 'path': 'red.mkv'}],
            'video_tracks': [
                {'name': 'base', 'kind': 'aroll', 'clips': [
                    {'src': 's', 'src_in': 0, 'src_out': 12, 'dst_in': 2, 'dur': 12}]},
                {'name': 'overlap', 'kind': 'pip', 'clips': [
                    {'src': 'red', 'src_in': 0, 'src_out': 4, 'dst_in': 6, 'dur': 4,
                     'scale': 0.25, 'x': 100, 'y': 80, 'opacity': 0.5}]}],
            'audio_tracks': [{'name': 'voice', 'events': [
                {'src': 's', 'src_in': 0, 'in': 0, 'out': 16, 'volume': 1.0}]}],
            'subtitles': [{'in': 7, 'out': 8, 'text': 'INLINE FRAME'}]}
        # Preserve numeric/escaped token spellings, including >53-bit integer.
        cls.raw = (json.dumps(cls.doc, ensure_ascii=False)[:-1] +
            ',"unknown":{"big":9007199254740993,"precise":1.0000000000000001,"escaped":"\\u4e2d\\n"}}\n')
        cls.path.write_text(cls.raw, encoding='utf-8')
        from vtx.pipeline.infra import Ctx
        from vtx.pipeline.stages import validate, prepare
        from vtx.pipeline.composite import prepare_layers
        log = io.StringIO()
        try:
            with contextlib.redirect_stdout(log):
                validate(Ctx(cls.path))
                prepare(Ctx(cls.path))
                prepare_layers(Ctx(cls.path))
        except SystemExit as error:
            raise AssertionError('official warm16 preparation failed:\n' + log.getvalue()) from error
        finally:
            print('WARM16 FIXTURE', cls.root, '\nWRITE AUTHORIZATION: validate receipts, prepare audio, prepare_layers seg; frames preview only.\n', log.getvalue())
        cls.prepared = fence(cls.root)

    def render(self, frame, **kwargs):
        result = frame_timeline(self.path, frame=frame, render=True, include_png=True, **kwargs)
        self.assertTrue(result['ok'], result['diagnostics'])
        self.assertEqual(result['reference']['document']['raw'], self.path.read_text())
        self.assertEqual(result['content_version'], 'sha256:' + hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(result['image']['content_version'], result['content_version'])
        self.assertEqual((result['image']['width'], result['image']['height'], result['image']['frame']), (1920, 1080, frame))
        self.assertEqual(result['image']['fps'], {'num': 30, 'den': 1})
        self.assertNotIn('source_frame', json.dumps(result))
        return result

    def test_01_first_last_halfopen_overlap_subtitles_and_cache(self):
        results, pixels = {}, {}
        for f in (0, 2, 6, 7, 8, 10, 14, 15):
            result = self.render(f)
            results[f] = result
            data, rgb = decode_inline(result['image'])
            self.assertEqual(data, (self.root / result['image']['path']).read_bytes())
            pixels[f] = rgb
        self.assertEqual(set(pixels[0]), {0})
        self.assertEqual(pixels[0], pixels[14])
        self.assertEqual(pixels[0], pixels[15])
        def pixel(f, x, y):
            i = (y * 1920 + x) * 3
            return tuple(pixels[f][i:i+3])
        self.assertTrue(pixel(2, 30, 30)[2] > 240, pixel(2, 30, 30))
        self.assertLess(max(pixel(2, 30, 30)[:2]), 8)
        self.assertEqual(pixels[2], pixels[10])
        self.assertEqual(pixels[6], pixels[8])
        purple = pixel(6, 200, 150)
        self.assertTrue(110 <= purple[0] <= 145 and 110 <= purple[2] <= 145 and purple[1] < 8, purple)
        self.assertEqual(pixel(6, 30, 30), pixel(2, 30, 30))
        self.assertNotEqual(pixels[7], pixels[8])
        # Overlap is explicit in the reference; half-open subtitle ends at 8.
        self.assertEqual(len(results[7]['reference']['selection']['blocks']), 4)
        hit = self.render(7)
        self.assertTrue(hit['image']['cache_hit'])
        self.assertEqual(hit['image']['inline'], results[7]['image']['inline'])
        self.assertEqual(fence(self.root), self.prepared)

    def test_02_marker_corruption_gc_fd_binding_and_saved_revision_CAS(self):
        first = self.render(5)
        path = self.root / first['image']['path']
        marker = path.parent / 'complete.json'
        path.write_bytes(path.read_bytes() + b'tail')
        repaired = self.render(5)
        self.assertFalse(repaired['image']['cache_hit'])
        record = json.loads(marker.read_text())
        record['sha256'] = '0'*64
        marker.write_text(json.dumps(record))
        self.assertFalse(self.render(5)['image']['cache_hit'])
        # GC during anchored read cannot publish data under a no-longer-present name.
        from vtx.render import png_artifact
        original = png_artifact.os.read
        removed = False
        def gc(fd, size):
            nonlocal removed
            if not removed and os.readlink('/proc/self/fd/' + str(fd)) == str(path):
                removed = True
                path.unlink()
            return original(fd, size)
        from vtx.pipeline.composite import RenderPlan
        with patch('vtx.render.png_artifact.os.read', side_effect=gc), \
                patch.object(RenderPlan, 'composite', side_effect=RuntimeError('GC forced rebuild denied')):
            failed = frame_timeline(self.path, frame=5, render=True, include_png=True)
        self.assertTrue(removed, failed['diagnostics'])
        self.assertFalse(failed['ok'])
        self.assertIsNone(failed['image'])
        self.assertFalse(self.render(5)['image']['cache_hit'])
        before = fence(self.root)
        self.path.write_text(self.raw + ' ')
        stale = frame_timeline(self.path, frame=5, render=True, include_png=True,
                               expected_content_version=first['content_version'])
        self.assertFalse(stale['ok'])
        self.assertIsNone(stale['image'])
        changed = self.render(5)
        self.assertNotEqual(changed['image']['render_key'], first['image']['render_key'])
        self.path.write_text(self.raw)
        # Actual stat identity of timeline changes deliberately; all other non-preview files frozen.
        after = fence(self.root)
        before.pop('timeline.json'); after.pop('timeline.json')
        self.assertEqual(before, after)

    def test_03_inflight_raw_change_has_no_success_marker(self):
        from vtx.pipeline.composite import RenderPlan
        original = RenderPlan.composite
        def mutate(plan, *args, **kwargs):
            value = original(plan, *args, **kwargs)
            self.path.write_text(self.raw + '\n')
            return value
        markers = set((self.root / 'cache' / 'preview').glob('*/complete.json'))
        with patch.object(RenderPlan, 'composite', mutate):
            failed = frame_timeline(self.path, frame=4, render=True, include_png=True)
        self.path.write_text(self.raw)
        self.assertFalse(failed['ok'])
        self.assertIsNone(failed['image'])
        # Existing verify_input_identity runs before document_current; preserve its error semantics.
        self.assertEqual(failed['diagnostics'][0]['code'], 'render.failed', failed['diagnostics'])
        self.assertIn('输入在渲染期间被修改', failed['diagnostics'][0]['message'])
        self.assertEqual(set((self.root / 'cache' / 'preview').glob('*/complete.json')), markers)

    def test_04_real_ffmpeg_cancel_reap_inline_api_and_CLI(self):
        script = '''import json, pathlib, subprocess, sys
from vtx.api import frame_timeline
from vtx.pipeline.composite import RenderPlan
real = subprocess.Popen
pidpath = pathlib.Path(sys.argv[2])
def tracked(*args, **kwargs):
    p = real(*args, **kwargs)
    if '-re' in args[0]: pidpath.write_text(str(p.pid))
    return p
subprocess.Popen = tracked
def slow(plan, layers, path, **kwargs):
    return plan.c.run(['ffmpeg','-v','error','-y','-re','-f','lavfi','-i','color=c=black:s=1920x1080:r=30','-t','60','-c:v','png','-threads','1','-f','image2','-update','1',str(path)])
RenderPlan.composite = slow
if sys.argv[3] == 'cli':
    sys.argv = ['vtx','-t',sys.argv[1],'timeline','frame','--frame','11','--render','--include-png']
    from vtx.cli.commands import main
    raise SystemExit(main())
print(json.dumps(frame_timeline(sys.argv[1],frame=11,render=True,include_png=True)))
'''
        for mode, sig in (('api', signal.SIGINT), ('cli', signal.SIGINT), ('cli', signal.SIGTERM)):
            # Control proof files outside the project frame write fence.
            with tempfile.TemporaryDirectory(prefix='inline-cancel-') as control:
                pidpath = Path(control) / 'child.pid'
                process = subprocess.Popen([sys.executable, '-B', '-c', script, str(self.path), str(pidpath), mode],
                    cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic()+30
                    while not pidpath.exists() and process.poll() is None and time.monotonic()<deadline:
                        time.sleep(0.02)
                    if not pidpath.exists():
                        out, err = process.communicate(timeout=10)
                        self.fail('owned ffmpeg did not start: ' + repr((out, err)))
                    process.send_signal(sig)
                    out, err = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 1 if mode == 'cli' else 0, err)
                    failed = json.loads(out)
                    self.assertFalse(failed['ok'])
                    self.assertIsNone(failed['image'])
                    self.assertEqual(failed['diagnostics'][0]['code'], 'render.cancelled')
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(pidpath.read_text()), 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=10)
