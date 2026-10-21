"""Real CPU sampling: authored luma/rectangles define truth before encoding."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
from tests.qc._support import _FakeContext, _run_qc
from vtx import qc, stages
from vtx.infra import Ctx

REPO = Path(__file__).resolve().parents[2]


def _sample(c, path, pre_crop=''):
    # Exercise the exact nested helper, not an imitation of its filter.
    tree = ast.parse(Path(qc.__file__).read_text())
    helper = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == 'gray_frame')
    module = ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[]))
    env = dict(qc.__dict__, c=c)
    exec(compile(module, 'production-gray-frame', 'exec'), env)
    return env['gray_frame'](path, 0, pre_crop=pre_crop)


class SamplingCacheContract(unittest.TestCase):
    def test_schema_and_filter_changes_reprobe_current_contract_reuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            c = _FakeContext(Path(temporary), enabled=True)
            with mock.patch.object(qc.loudness_cache, 'lookup',
                                   return_value={'i_lufs': -14, 'tp_dbfs': -2}):
                code, _, _ = _run_qc(c)
                self.assertEqual(code, 0)
                path = c.cache / qc._QC_SEG_RECORD
                current = json.loads(path.read_text())
                self.assertEqual(current['version'], 2)
                self.assertEqual(current['probe_filter'], qc._QC_GRAY_FILTER)
                # The previous reader requires version == 1 before any reuse.
                self.assertNotEqual(current['version'], 1)
                for version, value in ((1, None), (1, qc._QC_GRAY_FILTER),
                        (2, None), (2, 'scale=48:27,format=gray'),
                        (2, 'other-filter'), (3, qc._QC_GRAY_FILTER)):
                    old = dict(current, version=version)
                    if value is None:
                        old.pop('probe_filter')
                    else:
                        old['probe_filter'] = value
                    path.write_text(json.dumps(old)); c.commands.clear()
                    self.assertIsNone(qc._load_qc_pass_record(c))
                    code, _, report = _run_qc(c)
                    self.assertEqual(code, 0)
                    self.assertEqual(report['skipped_probes'], [])
                    self.assertTrue(any('rawvideo' in x for x in c.commands))
                c.commands.clear()
                code, _, report = _run_qc(c)
                self.assertEqual(code, 0)
                self.assertTrue(report['skipped_probes'])
                self.assertFalse(any('rawvideo' in x for x in c.commands))
                c.strict = True; c.commands.clear()
                code, _, report = _run_qc(c)
                self.assertEqual(code, 0)
                self.assertEqual(report['skipped_probes'], [])
                self.assertTrue(any('rawvideo' in x for x in c.commands))


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires CPU FFmpeg tools')
class SamplingMediaContract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vtx_qc_sampling_')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / 'cache'; self.cache.mkdir()
        self.c = types.SimpleNamespace(cache=self.cache, fps=30, run=self.command)
        self.serial = 0

    def command(self, args, **kwargs):
        result = subprocess.run(list(map(str, args)), capture_output=True,
                                timeout=180, **kwargs)
        self.assertEqual(result.returncode, 0,
                         (result.stdout + result.stderr).decode(errors='replace')[-4000:])
        return result

    def encoded_plane(self, w, h, range_, y, patch=None):
        self.serial += 1
        lum = bytearray([y]) * (w * h)
        if patch:
            x, top, pw, ph, py, u, v = patch
            for row in range(top, top + ph):
                lum[row*w+x:row*w+x+pw] = bytes([py]) * pw
        chroma = []
        for level in ((patch[5], patch[6]) if patch else (128, 128)):
            plane = bytearray([128]) * (w * h // 4)
            if patch:
                for row in range(top//2, (top+ph)//2):
                    plane[row*w//2+x//2:row*w//2+(x+pw)//2] = bytes([level]) * (pw//2)
            chroma.append(plane)
        raw = bytes(lum) + bytes(chroma[0]) + bytes(chroma[1])
        source = self.root / f'{self.serial}.yuv'; source.write_bytes(raw)
        video = self.root / f'{self.serial}.mp4'
        self.command(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'yuv420p',
                      '-video_size', f'{w}x{h}', '-framerate', '30', '-color_range', range_,
                      '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                      '-i', source, '-frames:v', '1', '-c:v', 'libx264', '-threads', '1',
                      '-preset', 'ultrafast', '-crf', '0', '-color_range', range_,
                      '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', video])
        probe = json.loads(self.command(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                         '-show_entries', 'stream=color_range,pix_fmt', '-of', 'json', video]).stdout)
        stream = probe['streams'][0]
        self.assertEqual(stream['color_range'], range_)
        decoded = self.command(['ffmpeg', '-v', 'error', '-i', video, '-frames:v', '1',
                                '-pix_fmt', stream['pix_fmt'], '-f', 'rawvideo', '-']).stdout
        self.assertEqual(decoded, raw, 'encoded fixture must preserve authored planes')
        return video

    def test_constant_black_white_and_near_black_for_both_ranges(self):
        for w, h in ((1920, 1080), (320, 180)):
            for range_, y, expected in (('tv', 16, 0), ('pc', 0, 0),
                                        ('tv', 235, 255), ('pc', 255, 255),
                                        ('tv', 20, 5), ('pc', 4, 4),
                                        ('tv', 17, 1), ('pc', 1, 1)):
                with self.subTest(size=(w, h), range=range_, y=y):
                    video = self.encoded_plane(w, h, range_, y)
                    sample = _sample(self.c, video)
                    self.assertEqual(len(sample), 48 * 27)
                    self.assertEqual(set(sample), {expected})
                    if expected >= 4:
                        self.assertGreaterEqual(sum(sample)/len(sample), qc.QC_LEAK_MEAN_MAX)
                    else:
                        self.assertLess(sum(sample)/len(sample), qc.QC_LEAK_MEAN_MAX)
                    if expected == 0:
                        self.assertEqual(_sample(self.c, video,
                            f'crop={w}:{h},scale=1920:1080:flags=lanczos,crop=960:540:240:120'),
                            bytes(48 * 27))

    def test_central_edge_and_colored_leaks_remain_visible(self):
        w, h = 1920, 1080
        # Authored 1/25-canvas rectangles, above the unchanged coverage gate.
        for x, y, value, u, v in ((768, 432, 235, 128, 128), (0, 0, 235, 128, 128),
                                  (1536, 864, 81, 90, 240)):
            with self.subTest(patch=(x, y, value, u, v)):
                video = self.encoded_plane(w, h, 'tv', 16, (x, y, 384, 216, value, u, v))
                sample = _sample(self.c, video)
                mean = sum(sample)/len(sample)
                cover = sum(p > qc.QC_PIXEL_DIFF for p in sample)/len(sample)
                self.assertFalse(mean < qc.QC_LEAK_MEAN_MAX and cover < qc.QC_LEAK_COVER_MAX)
                crop = _sample(self.c, video, f'crop=384:216:{x}:{y}')
                self.assertGreater(sum(crop)/len(crop), qc.QC_WINDOW_MEAN_MIN)

    def test_formal_bottom_gap_pipeline_and_real_leak_rejection(self):
        source = self.root / 'source.mp4'
        self.command(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                      'color=c=0x708090:s=320x180:r=30:d=2', '-f', 'lavfi', '-i',
                      'sine=frequency=1000:sample_rate=48000:duration=2', '-c:v', 'libx264',
                      '-threads', '1', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                      '-c:a', 'aac', '-ac', '2', '-shortest', source])
        window = self.root / 'window.mp4'
        self.command(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                      'color=c=white:s=320x180:r=30:d=1', '-f', 'lavfi', '-i',
                      'sine=frequency=800:sample_rate=48000:duration=1', '-c:v', 'libx264',
                      '-threads', '1', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                      '-c:a', 'aac', '-ac', '2', '-shortest', window])
        timeline = self.root / 'timeline.json'
        timeline.write_text(json.dumps({
            'version': 2, 'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 60},
            'sources': [{'id': 'source', 'path': 'source.mp4'},
                        {'id': 'window', 'path': 'window.mp4'}],
            'video_tracks': [{'name': 'base', 'kind': 'aroll', 'clips': [
                {'src': 'source', 'src_in': 0, 'src_out': 30, 'dst_in': 0, 'dur': 30}]},
                {'name': 'window', 'kind': 'aroll', 'clips': [
                    {'src': 'window', 'src_in': 0, 'src_out': 10, 'dst_in': 10,
                     'dur': 10, 'scale': 1.0, 'x': 400, 'y': 200}]}],
            'subtitles': [], 'audio_tracks': [{'name': 'tone', 'events': [
                {'src': 'source', 'in': 0, 'out': 60, 'volume': 1.0}]}]}))
        env = dict(os.environ, VTX_PIPELINE='0', VTX_VIDEO_SEGMENTS='1', VTX_SEG_NVENC='0')
        self.command([sys.executable, REPO/'vtx/vtx.py', '-t', timeline, 'all', '--until', 'mux'],
                     env=env, cwd=REPO)
        c = Ctx(timeline, diagnostics=[]); c.strict = True
        code, _, report = _run_qc(c)
        self.assertEqual(code, 0, report)
        self.assertTrue(all(x['pass'] for x in report['checks']))
        self.assertEqual(next(d['status'] for d in c.diagnostics
                              if d['rule_id'] == 'qc.clip_window'), 'pass')
        self.assertEqual(next(d['status'] for d in c.diagnostics
                              if d['rule_id'] == 'qc.overlay_leak'), 'pass')
        final = self.root/'out/final.mp4'; good = self.root/'out/good.mp4'
        shutil.copyfile(final, good)
        for effect in ("drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='gte(n,30)'",
                       "drawbox=x=0:y=0:w=384:h=216:color=white:t=fill:enable='gte(n,30)'",
                       "drawbox=x=0:y=0:w=iw:h=ih:color=0x040404:t=fill:enable='gte(n,30)'"):
            damaged = self.root/'out/damaged.mp4'
            self.command(['ffmpeg', '-v', 'error', '-i', good, '-vf', effect,
                          '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast',
                          '-crf', '0', '-c:a', 'copy', damaged])
            os.replace(damaged, final)
            c = Ctx(timeline, diagnostics=[]); c.strict = True
            code, _, report = _run_qc(c)
            self.assertEqual(code, 1, report)
            failed = [x for x in report['checks'] if not x['pass']]
            self.assertEqual(len(failed), 1, failed)
            self.assertTrue(failed[0]['name'].startswith('空窗无叠加泄漏'))
            self.assertEqual(next(d['status'] for d in c.diagnostics
                                  if d['rule_id'] == 'qc.overlay_leak'), 'fail')
            self.assertFalse(json.loads((self.root/'out/receipt.json').read_text())['stages']['qc']['ok'])
        # Removing a real cropped window over a non-black source must still
        # fail its existing effectiveness gate, independently of black gaps.
        self.command(['ffmpeg', '-v', 'error', '-i', good, '-vf',
                      "drawbox=x=400:y=200:w=320:h=180:color=0x708090:t=fill:enable='between(n,10,19)'",
                      '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast',
                      '-crf', '0', '-c:a', 'copy', damaged])
        os.replace(damaged, final)
        c = Ctx(timeline, diagnostics=[]); c.strict = True
        code, _, report = _run_qc(c)
        self.assertEqual(code, 1, report)
        failed = [x for x in report['checks'] if not x['pass']]
        self.assertEqual(len(failed), 1, failed)
        self.assertTrue(failed[0]['name'].startswith('窗口 clip'))


class EmptyVideoTracksQC(unittest.TestCase):
    def context(self, *, tracks=None, base=0, final=None, strict=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        c = _FakeContext(Path(temporary.name), enabled=True, strict=strict)
        c.tl['video_tracks'] = [] if tracks is None else tracks
        original = c.run

        def run(cmd, **kwargs):
            if 'rawvideo' not in cmd:
                return original(cmd, **kwargs)
            c.commands.append(tuple(cmd))
            frame = round(float(cmd[cmd.index('-ss') + 1]) * c.fps)
            source = cmd[cmd.index('-i') + 1]
            intensity = final(frame) if final and source.endswith('final.mp4') else base
            Path(cmd[-1]).write_bytes(bytes([intensity]) * (48 * 27))
            return subprocess.CompletedProcess(cmd, 0, '', '')

        c.run = run
        return c

    def run_qc(self, c):
        with mock.patch.object(qc.loudness_cache, 'lookup', return_value={
                'i_lufs': -14, 'tp_dbfs': -2}):
            return _run_qc(c)

    def leak(self, report):
        return [x for x in report['checks'] if x['name'].startswith('空窗无叠加泄漏')]

    def test_zero_tracks_keeps_black_canvas_probe_and_all_global_checks(self):
        for strict in (False, True):
            with self.subTest(strict=strict):
                c = self.context(strict=strict)
                code, _, report = self.run_qc(c)
                self.assertEqual(code, 0)
                self.assertEqual(len(report['checks']), 7)
                self.assertEqual(self.leak(report)[0]['name'], '空窗无叠加泄漏 @frame 23')
                self.assertTrue(self.leak(report)[0]['pass'])
                self.assertEqual(report['skipped_probes'], [])
                probes = [cmd for cmd in c.commands if 'rawvideo' in cmd]
                self.assertEqual(len(probes), 1)
                self.assertEqual(probes[0][probes[0].index('-i') + 1], str(c.out / 'final.mp4'))
                self.assertEqual(c.receipts[-1][0][:2], ('qc', True))
                self.assertFalse(any(d['rule_id'] == 'qc.required_field' for d in c.diagnostics))

    def test_zero_tracks_still_rejects_visible_leak(self):
        c = self.context(final=lambda frame: 100)
        code, _, report = self.run_qc(c)
        self.assertEqual(code, 1)
        self.assertFalse(self.leak(report)[0]['pass'])
        self.assertEqual(c.receipts[-1][0][:2], ('qc', False))

    def test_subtitle_coverage_still_controls_probe_selection(self):
        c = self.context()
        c.tl['subtitles'] = [{'in': 10, 'out': 24, 'text': 'covered'}]
        code, _, report = self.run_qc(c)
        self.assertEqual(code, 0)
        self.assertEqual(self.leak(report)[0]['name'], '空窗无叠加泄漏 @frame 9')

    def test_fully_covered_zero_tracks_retains_unknown_leak_diagnostic(self):
        c = self.context()
        c.tl['subtitles'] = [{'in': 0, 'out': 24, 'text': 'covered'}]
        code, _, report = self.run_qc(c)
        self.assertEqual(code, 0)
        self.assertEqual(self.leak(report), [])
        self.assertEqual(len(report['checks']), 6)
        leak = next(d for d in c.diagnostics if d['rule_id'] == 'qc.overlay_leak')
        self.assertEqual(leak['status'], 'unknown')
        self.assertFalse(any('rawvideo' in cmd for cmd in c.commands))

    def test_empty_clips_direct_qc_uses_black_without_changing_validation(self):
        # Direct QC can inspect existing media; this does not make this track
        # valid for the all pipeline. The validation boundary is asserted below.
        c = self.context(tracks=[{'name': 'empty', 'kind': 'aroll', 'clips': []}])
        code, _, report = self.run_qc(c)
        self.assertEqual(code, 0)
        self.assertTrue(self.leak(report)[0]['pass'])

    def test_normal_bottom_fade_is_avoided_with_one_or_multiple_tracks(self):
        bottom = {'name': 'base', 'kind': 'aroll', 'clips': [
            {'src': 'a', 'src_in': 0, 'dst_in': 0, 'dur': 24,
             'fade_in_frames': 2, 'fade_out_frames': 5}]}
        upper = {'name': 'over', 'kind': 'aroll', 'clips': [
            {'src': 'b', 'src_in': 0, 'dst_in': 0, 'dur': 12}]}
        for multiple in (False, True):
            with self.subTest(multiple=multiple):
                c = self.context(tracks=[bottom, upper] if multiple else [bottom],
                                 base=100, final=lambda frame: 200 if multiple and frame < 12 else 100)
                code, _, report = self.run_qc(c)
                self.assertEqual(code, 0)
                self.assertEqual(self.leak(report)[0]['name'], '空窗无叠加泄漏 @frame 18')
                self.assertEqual(sum(x['name'].startswith('底轨切点') for x in report['checks']), 1)
                self.assertEqual(sum(x['name'].startswith('全屏 clip') for x in report['checks']), int(multiple))

    def test_runtime_validator_accepts_zero_tracks_but_rejects_an_empty_track(self):
        # Isolate the runtime track rule: this reduced, sourceless fixture is
        # not evidence of whole-input formal schema conformance.
        with tempfile.TemporaryDirectory() as temporary:
            timeline = Path(temporary) / 'timeline.json'
            value = {'version': 2,
                     'project': {'fps': 30, 'width': 320, 'height': 180, 'duration_frames': 60},
                     'sources': [], 'video_tracks': [], 'subtitles': [], 'audio_tracks': []}
            timeline.write_text(json.dumps(value))
            c = Ctx(timeline, diagnostics=[])
            with contextlib.redirect_stdout(io.StringIO()):
                stages.validate(c)
            value['video_tracks'] = [{'name': 'empty', 'kind': 'aroll', 'clips': []}]
            timeline.write_text(json.dumps(value))
            c = Ctx(timeline, diagnostics=[])
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
                stages.validate(c)
            self.assertEqual(caught.exception.code, 1)
            self.assertTrue(any(d['rule_id'] == 'validate.track_empty' for d in c.diagnostics))

if __name__ == '__main__':
    unittest.main()
