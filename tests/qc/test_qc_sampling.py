"""Real CPU sampling: authored luma/rectangles define truth before encoding."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
from tests.qc._support import _FakeContext, _run_qc
from vtx.inspection import qc
from vtx.pipeline import stages
from vtx.pipeline.infra import Ctx

REPO = Path(__file__).resolve().parents[2]


def _sample(c, path, pre_crop='', frame=0):
    # Exercise the exact nested helper, not an imitation of its filter.
    tree = ast.parse(Path(qc.__file__).read_text())
    helper = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == 'gray_frame')
    module = ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[]))
    env = dict(qc.__dict__, c=c, native=qc._QCOperations(c).native)
    exec(compile(module, 'production-gray-frame', 'exec'), env)
    return env['gray_frame'](path, frame, pre_crop=pre_crop)


class SamplingCacheContract(unittest.TestCase):
    def test_witness_capture_bounds_both_pipes_and_time(self):
        from time import monotonic
        from vtx.media._display_witness import capture_witness, witnessed_frames

        good = types.SimpleNamespace(returncode=0, stderr=b'', stdout=b'frame=1\r\nprogress=end\r\n')
        self.assertEqual(witnessed_frames(good), 1)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            witnessed_frames(types.SimpleNamespace(**(vars(good) | {'stdout': good.stdout[:-1]})))
        cli = [sys.executable, '-B', '-c']
        ok = capture_witness([*cli, 'import sys; sys.stdout.write("ok"); sys.stderr.write("err")'],
                             deadline=monotonic() + 5, limit=5)
        self.assertEqual((ok.returncode, ok.stdout, ok.stderr), (0, b'ok', b'err'))
        with self.assertRaisesRegex(ValueError, 'output budget'):
            capture_witness([*cli, 'import sys; print("a" * 2000); print("b" * 1000, file=sys.stderr)'],
                            deadline=monotonic() + 5, limit=2100)
        for child in ('import time; time.sleep(20)',
                      'import os,time; os.close(1); os.close(2); time.sleep(20)'):
            with self.subTest(child=child), self.assertRaisesRegex(TimeoutError, 'time budget'):
                capture_witness([*cli, child], deadline=monotonic() + 0.5)

    def test_schema_and_filter_changes_reprobe_current_contract_reuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            c = _FakeContext(Path(temporary), enabled=True)
            with mock.patch.object(qc.loudness_cache, 'lookup',
                                   return_value={'i_lufs': -14, 'tp_dbfs': -2}):
                code, _, _ = _run_qc(c)
                self.assertEqual(code, 0)
                path = c.cache / qc._QC_SEG_RECORD
                current = json.loads(path.read_text())
                self.assertEqual(current['version'], 7)
                self.assertEqual(current['probe_filter'], qc._QC_GRAY_FILTER)
                # Previous readers require their exact version before any reuse.
                for previous in (1, 2, 3, 4, 5, 6):
                    self.assertNotEqual(current['version'], previous)
                for version, value in ((1, None), (1, qc._QC_GRAY_FILTER),
                        (2, None), (2, 'scale=48:27,format=gray'),
                        (2, 'other-filter'), (2, qc._QC_GRAY_FILTER),
                        (3, None), (3, 'other-filter'), (3, qc._QC_GRAY_FILTER),
                        (4, None), (4, 'other-filter'), (4, qc._QC_GRAY_FILTER),
                        (5, None), (5, 'other-filter'), (5, qc._QC_GRAY_FILTER),
                         (6, None), (6, qc._QC_GRAY_FILTER),
                          (7, None), (7, 'other-filter'), (8, qc._QC_GRAY_FILTER)):
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

    def assert_common_matrix_compares_equivalent_colored_moving_frames(self):
        # RGB is authored independently of either YUV matrix. Lossless 4:4:4
        # encodes avoid making chroma subsampling the definition of equivalence.
        w, h, frames = 320, 180, 30
        palette = ((230, 24, 24), (24, 230, 24), (24, 24, 230),
                   (230, 230, 24), (24, 230, 230), (230, 24, 230), (100, 100, 100))
        raw = bytearray()
        for frame in range(frames):
            row = b''.join(bytes(palette[((x + frame * 3) // 40) % len(palette)])
                           for x in range(w))
            raw.extend(row * h)
        source = self.root / 'authored-moving.rgb'
        source.write_bytes(raw)
        videos = []
        for matrix, tag in (('bt601', 'smpte170m'), ('bt709', 'bt709')):
            video = self.root / f'moving-{matrix}.mp4'
            self.command(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                          '-video_size', f'{w}x{h}', '-framerate', '30', '-i', source,
                          '-vf', f'scale=out_color_matrix={matrix}:out_range=limited,format=yuv444p',
                          '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast', '-crf', '0',
                          '-colorspace', tag, '-color_range', 'tv', video])
            videos.append(video)
        for frame in (0, 17, 29):
            with self.subTest(frame=frame):
                decoded = []
                for video in videos:
                    decoded.append(self.command(['ffmpeg', '-v', 'error', '-i', video,
                        '-vf', f'select=eq(n\\,{frame})', '-frames:v', '1', '-pix_fmt', 'rgb24',
                        '-threads', '1', '-f', 'rawvideo', '-']).stdout)
                authored = raw[frame*w*h*3:(frame+1)*w*h*3]
                for rgb in decoded:
                    self.assertEqual(len(rgb), len(authored))
                    self.assertLess(sum(abs(a-b) for a,b in zip(rgb, authored))/len(rgb), 2)
                samples = []
                for video in videos:
                    sample = _sample(self.c, video, frame=frame)
                    exact = self.command(['ffmpeg', '-v', 'error', '-i', video,
                        '-vf', f'select=eq(n\\,{frame}),' + qc._QC_GRAY_FILTER,
                        '-frames:v', '1', '-f', 'rawvideo', '-']).stdout
                    self.assertEqual(sample, exact, 'input seek must select the authored integer frame')
                    samples.append(sample)
                a, b = samples
                self.assertLess(sum(abs(x-y) for x,y in zip(a,b))/len(a), qc.QC_LEAK_MEAN_MAX)
                self.assertLess(sum(abs(x-y)>qc.QC_PIXEL_DIFF for x,y in zip(a,b))/len(a),
                                qc.QC_LEAK_COVER_MAX)
        # Unknown matrix tags retain FFmpeg's existing auto fallback. For this
        # SD fixture it is BT.601; this does not certify arbitrary untagged media.
        untagged = self.root / 'moving-untagged.mp4'
        self.command(['ffmpeg', '-v', 'error', '-i', videos[0], '-map', '0:v:0',
                      '-c:v', 'copy', '-bsf:v', 'h264_metadata=matrix_coefficients=2',
                      '-colorspace', 'unknown', untagged])
        info = json.loads(self.command(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=color_space', '-of', 'json', untagged]).stdout)
        self.assertNotIn(info['streams'][0].get('color_space'), ('bt709', 'smpte170m'))
        self.assertEqual(_sample(self.c, untagged, frame=29), _sample(self.c, videos[0], frame=29))
        # A real chromatic change must remain a failure, not get normalized away.
        a = _sample(self.c, videos[0], frame=0)
        b = _sample(self.c, videos[1], frame=17)
        self.assertGreater(sum(abs(x-y) for x,y in zip(a,b))/len(a), qc.QC_LEAK_MEAN_MAX)

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

    def test_mixed_pattern_truth_and_untagged_range_defaults_to_limited(self):
        # Non-constant truth: left 3/4 limited black, right 1/4 limited white.
        # Interior probes must land exactly on 0/255 and the transition must
        # stay at the geometric boundary after native-format matrix/range
        # normalization (chroma site / matrix cannot move luma edges).
        w, h = 1920, 1080
        self.serial += 1
        split = w * 3 // 4
        lum = b''.join(bytes([16]) * split + bytes([235]) * (w - split)
                       for _ in range(h))
        raw = lum + bytes([128]) * (w * h // 4) * 2
        source = self.root / f'{self.serial}.yuv'; source.write_bytes(raw)
        tagged = self.root / f'{self.serial}.mp4'
        for target, args in ((tagged, ['-color_range', 'tv']),
                             (self.root/'untagged.mp4', [])):
            # The untagged encode carries no range on either side; its samples
            # must still resolve via in_range=auto to the limited mapping.
            common = ['-f', 'rawvideo', '-pixel_format', 'yuv420p',
                      '-video_size', f'{w}x{h}', '-framerate', '30']
            if args:
                common += args
            self.command(['ffmpeg', '-v', 'error', *common,
                          '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                          '-i', source, '-frames:v', '1', '-c:v', 'libx264', '-threads', '1',
                          '-preset', 'ultrafast', '-crf', '0',
                          '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                          *args, target])
        sample = _sample(self.c, tagged)
        self.assertEqual(len(sample), 48 * 27)
        rows = [sample[i*48:(i+1)*48] for i in range(27)]
        for row in rows:
            self.assertEqual(set(row[:32]), {0}, row[:32])
            self.assertEqual(set(row[40:]), {255}, row[40:])
        for a, b in zip(rows, rows[1:]):
            self.assertEqual(a[32:40], b[32:40])
        # An untagged stream resolves in_range=auto to limited, same samples.
        self.assertEqual(_sample(self.c, self.root/'untagged.mp4'), sample)
        # Extend this existing pattern-truth case without changing strict-suite
        # selectors/counts: matrices, moving frame identity and unknown fallback.
        self.assert_common_matrix_compares_equivalent_colored_moving_frames()

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
        cli = [sys.executable, '-B', '-X', 'utf8', REPO/'vtx/vtx.py', '-t', timeline]
        self.command([*cli, 'all', '--until', 'mux'], env=env, cwd=REPO)
        from vtx.cache._formal_video import proof_name
        out = self.root / 'out'
        video_proof, final_proof = (out / proof_name(leaf) for leaf in ('video_noaudio.mp4', 'final.mp4'))
        final = out / 'final.mp4'
        before = final.read_bytes()
        final_proof.write_bytes(b'null')  # Malformed is not a missing legacy proof.
        rejected = subprocess.run(list(map(str, [*cli, 'qc', '--strict'])), cwd=REPO,
                                  env=env, capture_output=True, timeout=180)
        self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
        self.assertIn('当前视频产物缺少有效几何证明', rejected.stdout.decode('utf-8'))
        final_proof.unlink()
        video_proof.unlink()
        self.command([*cli, 'qc', '--strict'], env=env, cwd=REPO)
        self.assertEqual(final.read_bytes(), before)
        self.assertFalse(final_proof.exists(), 'do not adopt the legacy artifact')
        self.command([*cli, 'mux'], env=env, cwd=REPO)
        self.assertTrue(final_proof.is_file())
        self.assertFalse(video_proof.exists(), 'mux did not rerender or adopt video')
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
        self.assert_out_of_gamut_bottom_and_real_leak(env)

    def test_same_decoder_exposes_hidden_and_late_display_geometry(self):
        from time import monotonic
        from vtx.media._display_witness import verify_source

        c = types.SimpleNamespace()

        clear = self.root / 'clear.h264'
        oriented = self.root / 'oriented.h264'
        self.command(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=640x360:r=30:d=2',
                      '-c:v', 'libx264', '-threads', '1', '-bf', '0',
                      '-x264-params', 'keyint=30:min-keyint=30:scenecut=0', clear])
        self.command(['ffmpeg', '-v', 'error', '-i', clear, '-c', 'copy',
                      '-bsf:v', 'h264_metadata=display_orientation=insert:rotate=90', oriented])
        late = self.root / 'late.h264'
        late.write_bytes(clear.read_bytes() + oriented.read_bytes())
        for raw, name, counts in (
            (clear, 'normal.mkv', (60, None)),  # An ordinary container without a declared nb_frames.
            (oriented, 'hidden.mp4', (None, 60)),
            (late, 'late.mp4', (None, None)),
        ):
            with self.subTest(name=name):
                source = self.root / name
                self.command(['ffmpeg', '-v', 'error', '-fflags', '+genpts', '-r', '30', '-i', raw,
                              '-map', '0:v:0', '-c', 'copy', source])
                stream = json.loads(self.command(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                                   '-show_streams', '-of', 'json', source]).stdout)['streams'][0]
                self.assertEqual((stream['width'], stream['height']), (640, 360))
                self.assertFalse(stream.get('side_data_list'))
                if name == 'normal.mkv':
                    self.assertNotIn('nb_frames', stream)
                for expected, count in zip(((640, 360), (360, 640)), counts):
                    deadline = monotonic() + 30
                    if count is None:
                        with self.assertRaisesRegex(ValueError, 'geometry could not be confirmed'):
                            verify_source(c, source, *expected, deadline=deadline)
                    else:
                        self.assertEqual(verify_source(c, source, *expected, deadline=deadline), count)

    def test_quarter_turn_windows_match_decoded_geometry_and_strict_qc(self):
        for color in ('white', 'gray'):
            self.command(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                          f'color={color}:s=640x360:r=30:d=1', '-f', 'lavfi', '-i',
                          'sine=440:48000:duration=1', '-c:v', 'libx264', '-threads', '1',
                          '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', self.root / f'{color}.mp4'])
        # Write a known 89.5° MP4 display matrix, including on older FFmpeg
        # builds without the -display_rotation authoring option.
        movie = bytearray((self.root / 'white.mp4').read_bytes())

        def children(start, end, kind):
            while start < end:
                size, tag = struct.unpack_from('>I4s', movie, start)
                self.assertTrue(8 <= size <= end - start, (size, tag))
                if tag == kind:
                    yield start + 8, start + size
                start += size

        moov, = children(0, len(movie), b'moov')
        video = []
        for track in children(*moov, b'trak'):
            mdia, = children(*track, b'mdia')
            (handler, _), = children(*mdia, b'hdlr')
            if movie[handler + 8:handler + 12] == b'vide':
                video.extend(children(*track, b'tkhd'))
        (header, end), = video
        self.assertIn(movie[header], (0, 1))
        matrix = header + (40 if movie[header] == 0 else 52)
        self.assertLessEqual(matrix + 36, end)
        struct.pack_into('>9i', movie, matrix, 571, -65533, 0, 65533, 571, 0, 0, 0, 1 << 30)
        rotated = self.root / 'rotated.mp4'
        rotated.write_bytes(movie)
        from vtx.media.encoders import display_video_size, probe_video_alpha
        self.assertIs(probe_video_alpha(rotated).get('quarter_turn'), True)

        cfg = self.root / 'config.json'
        cfg.write_text('{"cache":{"mode":"aggressive"}}', encoding='utf-8')
        env = dict(os.environ, VTX_CONFIG=str(cfg), VTX_PIPELINE='0', VTX_VIDEO_SEGMENTS='1',
                   VTX_SEG_NVENC='0', VTX_NO_NVENC='1')
        for upper, scale, visible in ((False, None, (640, 360)), (False, .5, (180, 320)),
                                      (True, .9, (324, 360))):
            with self.subTest(upper=upper, scale=scale):
                project = self.root / f'{upper}-{scale}'
                project.mkdir()
                shutil.copyfile(rotated, project / '素材.mp4')
                shutil.copyfile(self.root / 'gray.mp4', project / 'background.mp4')
                clip = dict(src='turned', src_in=0, src_out=30, dst_in=0, dur=30)
                if scale is not None:
                    clip.update(scale=scale, x=0, y=0)
                tracks = [{'name': 'turned', 'kind': 'aroll', 'clips': [clip]}]
                if upper:
                    tracks.insert(0, {'name': 'base', 'kind': 'aroll',
                                     'clips': [dict(clip, src='background')]})
                    for key in ('scale', 'x', 'y'):
                        del tracks[0]['clips'][0][key]
                timeline = project / 'timeline.json'
                timeline.write_text(json.dumps({
                    'version': 2, 'project': {'fps': 30, 'width': 640, 'height': 360, 'duration_frames': 30},
                    'sources': [{'id': 'turned', 'path': '素材.mp4'},
                                {'id': 'background', 'path': 'background.mp4'}],
                    'video_tracks': tracks, 'subtitles': [],
                    'audio_tracks': [{'name': 'voice', 'events': [
                        {'src': 'background', 'in': 0, 'out': 30, 'volume': 1.0}]}],
                }, ensure_ascii=False), encoding='utf-8')
                cli = [sys.executable, '-B', '-X', 'utf8', REPO/'vtx/vtx.py', '-t', timeline]
                if not upper and scale is None:
                    out = project / 'out'
                    redirected = project / 'real-output'
                    redirected.mkdir()
                    if os.name == 'nt':
                        self.command(['cmd.exe', '/d', '/c', 'mklink', '/J', out, redirected])
                    else:
                        out.mkdir()
                        (redirected / 'alternate.mp4').touch()
                        (out / 'video_noaudio.mp4').symlink_to(redirected / 'alternate.mp4')
                self.command([*cli, 'all', '--until', 'mux'], cwd=REPO, env=env)
                if not upper and scale is not None:
                    from vtx.cache._formal_video import proof_name
                    out = project / 'out'
                    ledger = out / 'receipt.json'
                    published = [ledger, out / 'video_noaudio.mp4', out / 'final.mp4']
                    before_gate = [path.read_bytes() for path in published]
                    for stage, file, stale in (('mux', 'video_noaudio.mp4', None),
                                                ('qc', 'final.mp4', b'{broken')):
                        proof = out / proof_name(file)
                        current = proof.read_bytes()
                        if stale is None:
                            proof.unlink()  # Simulate a complete pre-fix handoff.
                            from vtx.api import atomic_media
                            media = out / file
                            payload = media.read_bytes()
                            atomic_media(Ctx(timeline), media, lambda path: path.write_bytes(payload))
                            self.assertFalse(proof.exists(), 'a generic publisher cannot qualify a formal handoff')
                        else:
                            proof.write_bytes(stale)
                        result = subprocess.run(list(map(str, [*cli, stage])), cwd=REPO, env=env,
                                                capture_output=True, timeout=180)
                        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                        self.assertIn('当前输入要求先成功完成阶段', result.stdout.decode('utf-8'))
                        self.assertEqual([path.read_bytes() for path in published], before_gate)
                        proof.write_bytes(current)
                    reference = project / 'cache/qc_clean.mp4'
                    final = project / 'out/final.mp4'
                    before = final.read_bytes()
                    self.assertTrue(reference.is_file())
                    self.command([*cli, 'gc'], cwd=REPO, env=env)
                    self.assertFalse(reference.exists())
                    self.command([*cli, 'qc', '--strict'], cwd=REPO, env=env)
                    self.assertEqual(final.read_bytes(), before)
                    self.assertTrue(reference.is_file())
                    self.command([*cli, 'gc'], cwd=REPO, env=env)
                    self.assertFalse(reference.exists())
                    diagnostic = self.command([sys.executable, '-B', '-X', 'utf8', '-m',
                        'vtx.agent_diagnostics', '-t', timeline, 'qc', '--strict',
                        '--format', 'agent-json'], cwd=REPO, env=env)
                    self.assertEqual(json.loads(diagnostic.stdout)['exit_code'], 0)
                    self.assertEqual(final.read_bytes(), before)
                    self.assertTrue(reference.is_file())
                else:
                    self.command([*cli, 'qc', '--strict'], cwd=REPO, env=env)
                c = Ctx(timeline)
                self.assertEqual(display_video_size(c.probe(project / '素材.mp4')), (360, 640))
                report = json.loads((project / 'out/qc_report.json').read_bytes())
                self.assertEqual(report['skipped_probes'], [])
                self.assertTrue(all(row['pass'] for row in report['checks']), report)
                pixels = self.command(['ffmpeg', '-v', 'error', '-i', project/'out/final.mp4',
                                       '-frames:v', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', '-']).stdout
                self.assertEqual(len(pixels), 640 * 360)
                rows = [sum(value > 200 for value in pixels[n:n + 640]) for n in range(0, len(pixels), 640)]
                self.assertEqual(rows, [visible[0]] * visible[1] + [0] * (360 - visible[1]))
                if upper:
                    # Another visible layer changes the canvas; it must not
                    # make an entirely offscreen rotated layer pass QC.
                    doc = json.loads(timeline.read_bytes())
                    doc['video_tracks'].append({'name': 'offscreen', 'kind': 'aroll',
                                               'clips': [dict(clip, x=640)]})
                    timeline.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
                    self.command([*cli, 'all', '--until', 'mux'], cwd=REPO, env=env)
                    result = subprocess.run(list(map(str, [*cli, 'qc', '--strict'])),
                                            cwd=REPO, env=env, capture_output=True, timeout=180)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    report = json.loads((project / 'out/qc_report.json').read_bytes())
                    failed = [row['name'] for row in report['checks'] if not row['pass']]
                    self.assertEqual(failed, ['窗口 clip offscreen @frame 15 生效'], report)

    def assert_out_of_gamut_bottom_and_real_leak(self, env):
        project = self.root / 'gamut'; project.mkdir()
        shutil.copyfile(self.root/'window.mp4', project/'window.mp4')
        source = project / 'source.mp4'
        # Legal limited-range YUV codes but R would be negative before RGB
        # clipping. Keep the authored color in YUV up to the actual compositor.
        self.command(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                      'nullsrc=s=320x180:r=30:d=2,geq=lum=81:cb=115:cr=16',
                      '-f', 'lavfi', '-i', 'sine=frequency=1000:sample_rate=48000:duration=2',
                      '-c:v', 'libx264', '-threads', '1', '-crf', '0', '-pix_fmt', 'yuv420p',
                      '-colorspace', 'bt709', '-color_range', 'tv',
                      '-c:a', 'aac', '-ac', '2', '-shortest', source])
        timeline = project / 'timeline.json'
        timeline.write_text(json.dumps({
            'version': 2, 'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 60},
            'sources': [{'id': 'source', 'path': 'source.mp4'},
                        {'id': 'window', 'path': 'window.mp4'}],
            'video_tracks': [{'name': 'base', 'kind': 'aroll', 'clips': [
                {'src': 'source', 'src_in': 0, 'src_out': 60, 'dst_in': 0, 'dur': 60}]},
                {'name': 'window', 'kind': 'aroll', 'clips': [
                    {'src': 'window', 'src_in': 0, 'src_out': 10, 'dst_in': 40,
                     'dur': 10, 'scale': 1.0, 'x': 400, 'y': 200}]}],
            'subtitles': [{'in': 5, 'out': 25, 'text': 'Subtitle before the empty window'}],
            'audio_tracks': [{'name': 'tone', 'events': [
                {'src': 'source', 'in': 0, 'out': 60, 'volume': 1.0}]}]}))
        # The later segment has two passes even though frame 59 is unobscured.
        self.command([sys.executable, REPO/'vtx/vtx.py', '-t', timeline, 'all', '--until', 'mux'],
                     env=dict(env, VTX_VIDEO_SEGMENTS='2'), cwd=REPO)
        final = project/'out/final.mp4'
        for injected in (False, True):
            if injected:
                damaged = final.with_name('damaged.mp4')
                self.command(['ffmpeg', '-v', 'error', '-i', final, '-vf',
                              "drawbox=x=0:y=0:w=384:h=216:color=white:t=fill:enable='gte(n,30)'",
                              '-c:v', 'libx264', '-threads', '1', '-crf', '0', '-c:a', 'copy', damaged])
                os.replace(damaged, final)
            c = Ctx(timeline, diagnostics=[]); c.strict = True
            code, _, report = _run_qc(c)
            failures = [x['name'] for x in report['checks'] if not x['pass']]
            self.assertEqual(failures, ['空窗无叠加泄漏 @frame 59'] if injected else [], report)
            self.assertEqual(code, int(injected))


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
