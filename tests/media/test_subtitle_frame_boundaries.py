"""Issue178: integer-frame intent versus real libass and production CLI output.

Pure tests always run. Set VTX_TEST_FFMPEG to enable CPU integrations. Optional
VTX_SUBTITLE_EVIDENCE_DIR retains inputs, ASS, commands, PTS and frame evidence;
otherwise fixtures live in temporary directories. No browser/GPU is needed.
"""
import csv
from fractions import Fraction
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from vtx.composite import RenderPlan, _graph_code_digest
from vtx.constraints import ALLOWED_FPS
from vtx.infra import Ctx
from vtx.media.subs import _active_subs

FPS = 30


def plan(subtitles, width=160, height=90, draft=False):
    c = SimpleNamespace(fps=FPS, tl={'subtitles': subtitles}, gpu=False, root=None)
    return RenderPlan(c=c, draft=draft, f=width / 1920, W=width, H=height)


def parse_ass(data):
    def seconds(text):
        h, m, s = text.split(':')
        return 3600 * int(h) + 60 * int(m) + Fraction(s)
    return [(seconds(p[1]), seconds(p[2]), p[9])
            for line in data.decode().splitlines() if line.startswith('Dialogue:')
            for p in [line.split(',', 9)]]


def emitted_showinfo_pts(log, count):
    """showinfo can observe encoder lookahead beyond -frames:v output."""
    observed = [(int(a), int(b)) for a, b in re.findall(
        r'\bn:\s*(\d+)\s+pts:\s*(-?\d+)', log)]
    # Raw byte length independently fixes actual emitted frame count. Preserve
    # every PTS check for that range; never mistake filter lookahead for output.
    return [(n, pts) for n, pts in observed if n < count]


class ShowinfoOutputRange(unittest.TestCase):
    def test_lookahead_is_not_an_emitted_frame(self):
        log = 'n: 0 pts: 0\nn: 1 pts: 1\n'
        self.assertEqual(emitted_showinfo_pts(log, 1), [(0, 0)])

    def test_actual_emitted_pts_error_is_preserved(self):
        log = 'n: 0 pts: 1\nn: 1 pts: 1\n'
        self.assertNotEqual(emitted_showinfo_pts(log, 1), [(0, 0)])
        self.assertNotEqual(emitted_showinfo_pts('n: 1 pts: 1', 1), [(0, 0)])


def intended(subtitles, frame):
    # Independent oracle: never call production conversion or active-set code.
    return [s['text'] for s in subtitles if s['in'] <= frame < s['out']]


def cases():
    yield 'reported_global', [{'in': 310, 'out': 329, 'text': 'TEST'}], 0, 340
    yield 'reported_segment309', [{'in': 310, 'out': 329, 'text': 'TEST'}], 309, 340
    yield 'positive_grid', [{'in': 309, 'out': 330, 'text': 'TEST'}], 0, 340
    yield 'empty', [], 0, 340
    # Cartesian product: every start/end/origin remainder modulo three.
    for origin in (309, 310, 311):
        for start in (312, 313, 314):
            for end in (321, 322, 323):
                yield f'mod3_{origin}_{start}_{end}', [
                    {'in': start, 'out': end, 'text': 'TEST'}], origin, 327
    for origin in (0, 309, 310, 311):
        for start in (312, 313, 314):
            yield f'single_{origin}_{start}', [
                {'in': start, 'out': start + 1, 'text': 'TEST'}], origin, 318
    for origin in (309, 310, 311):
        yield f'adjacent_{origin}', [
            {'in': 312, 'out': 314, 'text': 'LEFT'},
            {'in': 314, 'out': 317, 'text': 'RIGHT'}], origin, 321
        yield f'clipped_{origin}', [
            {'in': 307, 'out': 313, 'text': 'LEFT'},
            {'in': 313, 'out': 321, 'text': 'RIGHT'},
            {'in': 321, 'out': 324, 'text': 'OUTSIDE'}], origin, 318
        # Opposite sides of the same real split, including exact boundaries.
        for a, b in ((origin - 3, origin), (origin, origin + 6)):
            yield f'crosscut_{a}_{b}', [
                {'in': origin - 1, 'out': origin + 2, 'text': 'TEST'}], a, b
    # FFmpeg's double PTS -> integer libass milliseconds can underflow by 1ms
    # even at mathematically exact centisecond boundaries. Cover both edges.
    for origin in (0, 309):
        for local in (111, 207, 399):
            for start, end in ((local - 2, local), (local, local + 2), (local, local + 1)):
                yield f'millisecond_{origin}_{start}_{end}', [
                    {'in': origin + start, 'out': origin + end, 'text': 'TEST'}], origin, origin + end + 2
    for frame in (0, 1, 2):
        yield f'zero_single_{frame}', [{'in': frame, 'out': frame + 1, 'text': 'TEST'}], 0, 6


def ass_for(p, path, origin, stop):
    if origin == 0:
        p.write_global_ass(path)
    else:
        p.write_seg_ass(path, origin, stop)
    return path.read_bytes()


class FrameBoundaryContracts(unittest.TestCase):
    def test_supported_fps_matrix_is_explicit(self):
        self.assertEqual(ALLOWED_FPS, (FPS,))

    def test_quantized_edges_and_active_set_match_frame_intent(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'subs.ass'
            for name, subs, origin, stop in cases():
                with self.subTest(name=name):
                    p = plan(subs)
                    before = json.dumps(subs, sort_keys=True)
                    events = parse_ass(ass_for(p, target, origin, stop))
                    for frame in range(origin, stop):
                        expected = intended(subs, frame)
                        measured_clock = Fraction((frame - origin) * 1000 // FPS, 1000)
                        predicted = [text for start, end, text in events
                                     if start <= measured_clock < end]
                        self.assertEqual(predicted, expected, (name, frame))
                        if frame > origin:
                            # A 1ms early clock also models exact-ms floating underflow.
                            early = measured_clock - Fraction(1, 1000)
                            self.assertEqual([text for start, end, text in events
                                              if start <= early < end], expected)
                        self.assertEqual([s['text'] for s in _active_subs(p.c, frame)], expected)
                    self.assertEqual(json.dumps(subs, sort_keys=True), before)

    def test_impact_analysis_uses_same_bytes_and_accepts_30_point_zero(self):
        from vtx.inspection.timeline_diff import _seg_dialogue
        subs = [{'in': 310, 'out': 329, 'text': 'TEST'}]
        for fps in (30, 30.0):
            p = plan(subs); p.c.fps = fps
            expected = "\n".join(line for line in p.segment_ass_bytes(309, 340).decode().splitlines()
                                 if line.startswith('Dialogue:'))
            self.assertEqual(_seg_dialogue(subs, fps, 309, 340), expected)
        self.assertEqual(_seg_dialogue(subs, 30, 309, 320),
                         _seg_dialogue([dict(subs[0], out=330)], 30, 309, 320))

    def test_mathematical_integer_frames_are_not_type_restricted(self):
        with tempfile.TemporaryDirectory() as temp:
            for name, subs, origin, stop in cases():
                integer = plan(subs)
                equivalent = plan([dict(s, **{'in': float(s['in']), 'out': float(s['out'])})
                                   for s in subs])
                original = json.dumps(equivalent.c.tl, sort_keys=True)
                self.assertEqual(integer.segment_ass_bytes(origin, stop),
                                 equivalent.segment_ass_bytes(origin, stop), name)
                a, b = Path(temp) / 'integer.ass', Path(temp) / 'equivalent.ass'
                integer.write_global_ass(a); equivalent.write_global_ass(b)
                self.assertEqual(a.read_bytes(), b.read_bytes(), name)
                self.assertEqual(json.dumps(equivalent.c.tl, sort_keys=True), original)

    def test_fractional_or_non_numeric_frames_are_never_truncated(self):
        from vtx.inspection.timeline_diff import _seg_dialogue
        for value in (310.5, -1, True, '310', None, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                plan([])._fc(value)
        for field in ('in', 'out'):
            sub = {'in': 310, 'out': 329, 'text': 'TEST'}
            sub[field] += 0.5
            with self.assertRaises(ValueError):
                plan([sub]).segment_ass_bytes(309, 340)
            with self.assertRaises(ValueError):
                _seg_dialogue([sub], 30, 309, 340)
            with self.assertRaises(ValueError):
                plan([sub]).segment_ass_bytes(315, 320)  # clipping cannot conceal invalid input
            with self.assertRaises(ValueError):
                _active_subs(plan([sub]).c, 315)

    def test_long_timecode_is_integer_exact(self):
        self.assertEqual(plan([])._fc(30 * 3600 + 2), '1:00:00.05')
        self.assertEqual(plan([])._fc(30 * 3600 * 100000 + 2), '100000:00:00.05')

    def test_changed_ass_invalidates_only_affected_segment_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            p = plan([{'in': 310, 'out': 329, 'text': 'TEST'}])
            f = Path(temp) / 'segment.ass'
            new = p.segment_ass_bytes(309, 340)
            old = new.replace(b'0:00:00.01', b'0:00:00.03').replace(b'0:00:00.65', b'0:00:00.67')
            self.assertNotEqual(new, old)
            f.write_bytes(old)
            old_key = p._seg_content_key(309, 340, [], True, f)
            clean_key = p._seg_content_key(309, 340, [], False, f)
            f.write_bytes(new)
            self.assertNotEqual(old_key, p._seg_content_key(309, 340, [], True, f))
            self.assertEqual(clean_key, p._seg_content_key(309, 340, [], False, f))
            before = p.segment_ass_bytes(0, 300)
            p.c.tl['subtitles'][0]['out'] += 1
            self.assertEqual(before, p.segment_ass_bytes(0, 300))
            # Endpoint-only changes must not invalidate expensive no-sub passes.
            self.assertRegex(_graph_code_digest(), r'^[0-9a-f]{12}$')


@unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'), 'set VTX_TEST_FFMPEG for actual CPU burn-in')
class SubtitleFrameIntegration(unittest.TestCase):
    def setUp(self):
        keep = os.environ.get('VTX_SUBTITLE_EVIDENCE_DIR')
        if keep:
            self.root = Path(keep) / self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            self.temp = tempfile.TemporaryDirectory(prefix='vtx-subtitle-edges-')
            self.addCleanup(self.temp.cleanup)
            self.root = Path(self.temp.name)
        self.ff = os.environ['VTX_TEST_FFMPEG']
        self.records = []
        self.env = dict(os.environ, VTX_NO_NVENC='1', VTX_SEG_NVENC='0')
        self.run_tool([self.ff, '-version'], 'ffmpeg-version')
        font = self.run_tool(['fc-match', 'Noto Sans CJK SC', '-f', '%{file}'], 'font')
        font_path = Path(font.decode())
        (self.root / 'font-identity.json').write_text(json.dumps({
            'path': str(font_path), 'sha256': hashlib.sha256(font_path.read_bytes()).hexdigest()}))
        self.addCleanup(self.freeze_manifest)

    def freeze_manifest(self):
        files = {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(self.root.rglob('*')) if p.is_file()}
        (self.root / 'manifest.json').write_text(json.dumps(files, indent=2))

    def run_tool(self, cmd, name, env=None, success=True):
        result = subprocess.run(list(map(str, cmd)), capture_output=True,
                                env=env or self.env, timeout=180)
        (self.root / (name + '.stdout')).write_bytes(result.stdout)
        (self.root / (name + '.stderr')).write_bytes(result.stderr)
        self.records.append({'name': name, 'argv': list(map(str, cmd)),
                             'returncode': result.returncode})
        (self.root / 'commands.json').write_text(json.dumps(self.records, indent=2))
        if success:
            self.assertEqual(result.returncode, 0, (name, result.stderr[-3000:], result.stdout[-3000:]))
        else:
            self.assertNotEqual(result.returncode, 0, (name, 'invalid input was accepted'))
        return result.stdout

    def burn_ass(self, path, count, name):
        raw = self.run_tool([self.ff, '-v', 'info', '-f', 'lavfi', '-i',
            f'color=black:s=160x90:r=30:d={count / FPS + 1}',
            '-vf', f'subtitles=filename={path},format=gray,showinfo',
            '-frames:v', str(count), '-threads', '1', '-pix_fmt', 'gray',
            '-f', 'rawvideo', 'pipe:1'], name)
        self.assertEqual(len(raw), count * 160 * 90)
        log = (self.root / (name + '.stderr')).read_text()
        self.assertIn('time_base: 1/30', log)
        observed_pts = emitted_showinfo_pts(log, count)
        self.assertEqual(observed_pts, [(n, n) for n in range(count)])
        return [raw[i * 14400:(i + 1) * 14400] for i in range(count)]

    def test_libass_cartesian_boundary_matrix(self):
        references = {}
        for word in ('TEST', 'LEFT', 'RIGHT'):
            p = self.root / f'reference_{word}.ass'
            plan([{'in': 0, 'out': 30, 'text': word}]).write_global_ass(p)
            references[word] = self.burn_ass(p, 1, f'reference_{word}')[0]
            self.assertTrue(any(references[word]), 'font/render failure must not pass as empty')
        references[None] = bytes(14400)
        rows = []
        for name, subs, origin, stop in cases():
            (self.root / (name + '.input.json')).write_text(json.dumps({
                'subtitles': subs, 'origin': origin, 'stop': stop, 'fps': FPS}))
            p = self.root / (name + '.ass')
            events = parse_ass(ass_for(plan(subs), p, origin, stop))
            frames = self.burn_ass(p, stop - origin, name)
            if name in ('reported_global', 'reported_segment309', 'positive_grid', 'empty'):
                from PIL import Image
                for global_frame in (309, 310, 328, 329, 330):
                    n = global_frame - origin
                    if 0 <= n < len(frames):
                        Image.frombytes('L', (160, 90), frames[n]).save(
                            self.root / f'{name}-global{global_frame}.png')
            for n, image in enumerate(frames):
                want = intended(subs, n + origin)
                predicted = [text for start, end, text in events
                             if start <= Fraction(n * 1000 // FPS, 1000) < end]
                rows.append({'case': name, 'local_frame': n, 'global_frame': n + origin,
                    'pts': n, 'time_base': '1/30', 'intent': want, 'prediction': predicted,
                    'nonblack_pixels': sum(v > 0 for v in image),
                    'pixel_sha256': hashlib.sha256(image).hexdigest()})
                self.assertEqual(hashlib.sha256(image).digest(),
                                 hashlib.sha256(references[want[0] if want else None]).digest(),
                                 (name, n + origin, want))
            with (self.root / 'per-frame.csv').open('w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)

    def cli(self, timeline, args, name, segments=2, success=True):
        env = dict(self.env, VTX_VIDEO_SEGMENTS=str(segments))
        return self.run_tool([sys.executable, str(REPO / 'vtx/vtx.py'),
                             '-t', str(timeline), *args], name, env, success=success)

    def check_movie(self, movie, subs, origin, count, name):
        meta = json.loads(self.run_tool(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_streams', '-show_frames', '-of', 'json', movie], name + '-pts'))
        stream = meta['streams'][0]
        self.assertEqual(Fraction(stream['r_frame_rate']), FPS)
        pts = [Fraction(f['pts']) * Fraction(stream['time_base']) for f in meta['frames']]
        self.assertEqual(pts, [Fraction(n, FPS) for n in range(count)])
        raw = self.run_tool([self.ff, '-v', 'error', '-i', movie, '-vf',
            'scale=160:90,format=gray', '-threads', '1', '-pix_fmt', 'gray',
            '-f', 'rawvideo', 'pipe:1'], name + '-pixels')
        self.assertEqual(len(raw), count * 14400)
        rows = []
        for n in range(count):
            image = raw[n * 14400:(n + 1) * 14400]
            expected = bool(intended(subs, origin + n))
            # Black canvas is zero; foreground text is near-white. Encoding noise
            # below 100 cannot invent a subtitle, and positive controls must pass.
            observed = max(image) > 100
            rows.append({'local_frame': n, 'global_frame': origin + n,
                         'pts': str(pts[n]), 'expected': expected, 'observed': observed,
                         'max': max(image), 'pixel_sha256': hashlib.sha256(image).hexdigest()})
        (self.root / (name + '-frames.json')).write_text(json.dumps(rows, indent=2))
        self.assertEqual([r['global_frame'] for r in rows if r['observed']],
                         [r['global_frame'] for r in rows if r['expected']], name)

    def test_production_final_range_point_and_warm_cache(self):
        project = self.root / 'project'; project.mkdir()
        audio = project / 'source.mp4'
        self.run_tool([self.ff, '-v', 'error', '-f', 'lavfi', '-i',
            'color=black:s=320x180:r=30:d=20.6', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000:duration=20.6',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', audio], 'audio-source')
        subs = [{'in': 310, 'out': 329, 'text': 'TEST'},
                {'in': 345, 'out': 366, 'text': 'GRID'},
                {'in': 380, 'out': 381, 'text': 'ONE'},
                {'in': 382, 'out': 383, 'text': 'TWO'},
                {'in': 384, 'out': 385, 'text': 'THREE'},
                {'in': 405, 'out': 407, 'text': 'LEFT'},
                {'in': 407, 'out': 410, 'text': 'RIGHT'},
                {'in': 109, 'out': 111, 'text': 'END111'},
                {'in': 111, 'out': 112, 'text': 'START111'},
                {'in': 205, 'out': 207, 'text': 'END207'},
                {'in': 207, 'out': 208, 'text': 'START207'},
                {'in': 397, 'out': 399, 'text': 'END399'},
                {'in': 399, 'out': 400, 'text': 'START399'},
                {'in': 418, 'out': 420, 'text': 'LOCALEND111'},
                {'in': 420, 'out': 421, 'text': 'LOCALSTART111'}]
        # Legal JSON mathematical integers must survive every real entry point.
        subs = [dict(s, **{'in': float(s['in']), 'out': float(s['out'])}) for s in subs]
        tl = {'version': 2, 'project': {'fps': FPS, 'width': 1920, 'height': 1080,
             'duration_frames': 618}, 'sources': [{'id': 'tone', 'path': 'source.mp4'}],
             'video_tracks': [{'name': 'base', 'kind': 'aroll', 'clips': [
                 {'src': 'tone', 'src_in': 0, 'src_out': 618, 'dst_in': 0, 'dur': 618}]}], 'subtitles': subs,
             'audio_tracks': [{'name': 'voice', 'events': [
                 {'src': 'tone', 'in': 0, 'out': 618, 'volume': 1}]}]}
        timeline = project / 'timeline.json'; timeline.write_text(json.dumps(tl))
        original = timeline.read_bytes()
        self.cli(timeline, ['all', '--until', 'mux'], 'formal-global', segments=1)
        self.check_movie(project / 'out/final.mp4', subs, 0, 618, 'formal-global')
        self.cli(timeline, ['all', '--until', 'mux'], 'formal-segmented')
        self.check_movie(project / 'out/final.mp4', subs, 0, 618, 'formal-segmented')
        self.cli(timeline, ['qc', '--strict'], 'formal-qc')
        cache = project / 'cache/vseg'
        before = {p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                  for p in cache.glob('*.mp4')}
        self.assertTrue(before, 'segmented run must publish vseg media before the warm-cache check')
        self.cli(timeline, ['video'], 'formal-warm')
        after = {p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                 for p in cache.glob('*.mp4')}
        self.assertEqual(before, after)
        output_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (project / 'out').iterdir() if p.is_file()}
        for a, b in ((309, 425), (310, 340), (311, 340), (308, 340), (328, 330)):
            self.cli(timeline, ['video', '--draft', '--range', f'{a}f', f'{b}f'], f'range-{a}-{b}')
            movie = project / 'cache/preview' / f'range_{a / FPS:.3f}_{b / FPS:.3f}.mp4'
            self.check_movie(movie, subs, a, b - a, f'range-{a}-{b}')
        output_after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in (project / 'out').iterdir() if p.is_file()}
        self.assertEqual(output_before, output_after, 'range must not rewrite formal outputs/receipts')
        self.cli(timeline, ['subs', 'preview', 'f309', 'f310', 'f328', 'f329', 'f330'], 'point-preview')
        from PIL import Image
        for frame in (309, 310, 328, 329, 330):
            p = project / 'out' / f'subs_preview_f{frame}.png'
            self.assertEqual(Image.open(p).convert('L').getextrema()[1] > 100,
                             bool(intended(subs, frame)), frame)
        self.assertEqual(timeline.read_bytes(), original, 'never rewrite timeline to fit output')
        (self.root / 'cache-warm.json').write_text(json.dumps({'before': before, 'after': after}, indent=2))
        self.cli(timeline, ['subs', 'render'], 'integral-subs-render')
        integer_timeline = project / 'integer-timeline.json'
        integer_tl = dict(tl, subtitles=[dict(s, **{'in': int(s['in']), 'out': int(s['out'])})
                                           for s in subs])
        integer_timeline.write_text(json.dumps(integer_tl))
        diff_path = self.root / 'integral-diff.json'
        self.cli(timeline, ['diff', str(timeline), str(integer_timeline),
                            '--json', str(diff_path)], 'integral-diff')
        diff = json.loads(diff_path.read_text())
        self.assertEqual(diff['n_entries'], 0)
        self.assertEqual(diff['impact']['vseg_invalid']['count'], 0)
        for field in ('in', 'out'):
            invalid = json.loads(original)
            invalid['subtitles'][0][field] += 0.5
            invalid_timeline = project / f'fractional-{field}.json'
            invalid_timeline.write_text(json.dumps(invalid))
            for mode, args in (
                    ('global', ['subs', 'render']),
                    ('video', ['video', '--draft', '--frames', '12']),
                    ('segment', ['video', '--draft', '--range', '309f', '340f']),
                    ('diff', ['diff', str(invalid_timeline), str(integer_timeline)])):
                name = f'reject-fractional-{field}-{mode}'
                self.cli(invalid_timeline, args, name, success=False)
                self.assertIn('subtitle frame boundary must be a non-negative integer',
                              (self.root / (name + '.stderr')).read_text()
                              + (self.root / (name + '.stdout')).read_text())
        self.assertEqual(timeline.read_bytes(), original)


    def test_production_cross_cut_modulo_three(self):
        tone = self.root / 'tone.wav'
        self.run_tool([self.ff, '-v', 'error', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000:duration=22',
            '-c:a', 'pcm_s16le', tone], 'cross-cut-audio')
        for cut in (318, 319, 320):
            project = self.root / f'cut-{cut}'; project.mkdir()
            subs = [{'in': 310.0, 'out': 329.0, 'text': 'TEST'}]
            tl = {'version': 2, 'project': {'fps': 30, 'width': 1920, 'height': 1080,
                  'duration_frames': cut * 2}, 'sources': [{'id': 'tone', 'path': str(tone)}],
                  'video_tracks': [], 'audio_tracks': [{'name': 'voice', 'events': [
                      {'src': 'tone', 'in': 0, 'out': cut * 2, 'volume': 1}]}], 'subtitles': subs}
            timeline = project / 'timeline.json'; timeline.write_text(json.dumps(tl))
            before = timeline.read_bytes()
            log = self.cli(timeline, ['video'], f'cut-{cut}-formal').decode()
            self.assertIn(f'切点 [{cut}]', log)
            self.check_movie(project / 'out/video_noaudio.mp4', subs, 0, cut * 2,
                             f'cut-{cut}-formal')
            log = self.cli(timeline, ['video', '--draft', '--range', '309f', '340f'],
                           f'cut-{cut}-range').decode()
            self.assertIn(f'全片切点 [{cut}]', log)
            self.assertIn(f'[309,{cut})', log)
            self.assertIn(f'[{cut},340)', log)
            self.check_movie(project / 'cache/preview/range_10.300_11.333.mp4',
                             subs, 309, 31, f'cut-{cut}-range')
            self.assertEqual(timeline.read_bytes(), before)

    def test_two_layer_pass_clock(self):
        # Real FFV1 intermediate input: its 1/1000 clock is different from the
        # direct lavfi 1/30 clock, so exercise the production pass route too.
        source = self.root / 'black.mp4'
        self.run_tool([self.ff, '-v', 'error', '-f', 'lavfi', '-i',
            'color=black:s=320x180:r=30:d=25', '-c:v', 'libx264', '-preset',
            'ultrafast', '-pix_fmt', 'yuv420p', source], 'pass-source')
        subs = [{'in': 310, 'out': 329, 'text': 'TEST'},
                {'in': 420, 'out': 421, 'text': 'START111'},
                {'in': 514, 'out': 516, 'text': 'END207'},
                {'in': 706, 'out': 708, 'text': 'END399'}]
        # Use the production writer: intermediate admission requires the
        # completion certificate published by Ctx.run's owned transaction.
        timeline = self.root / 'timeline.json'
        timeline.write_text(json.dumps({
            'project': {'fps': 30, 'duration_frames': 750, 'width': 320, 'height': 180},
            'sources': [{'id': 'black', 'path': str(source)}],
            'video_tracks': [], 'audio_tracks': [], 'subtitles': subs,
        }))
        c = Ctx(timeline)
        c.cache.mkdir()
        run_process = subprocess.run
        def record_process(cmd, *args, **kwargs):
            # Record the real subprocess result before Ctx.run applies failure
            # handling, including staged output/inspection argv and full streams.
            result = run_process(cmd, *args, **kwargs)
            name = f'pass-command-{len(self.records)}'
            for stream in ('stdout', 'stderr'):
                data = getattr(result, stream) or b''
                (self.root / (name + '.' + stream)).write_bytes(
                    data.encode() if isinstance(data, str) else data)
            self.records.append({'name': name, 'argv': list(map(str, cmd)),
                                 'returncode': result.returncode})
            (self.root / 'commands.json').write_text(json.dumps(self.records, indent=2))
            return result
        p = RenderPlan(c=c, draft=False, f=1.0, W=320, H=180)
        layers = [dict(gfx=False, seg=source, x=0, y=0, w2=None, h2=None,
                       cl={'src': 'black', 'src_in': 0, 'src_out': 750,
                           'dst_in': 0, 'dur': 750}) for _ in range(2)]
        ass = self.root / 'pass.ass'; p.write_seg_ass(ass, 309, 720)
        movie = self.root / 'pass.mp4'
        with patch.object(subprocess, 'run', side_effect=record_process):
            p.composite(layers, movie, True, a=309, b=720, sub_file=ass)
        intermediates = list((c.cache / 'vseg_pass').glob('*.mkv'))
        self.assertEqual(len(intermediates), 1, 'must actually enter multi-layer pass path')
        self.check_movie(movie, subs, 309, 411, 'two-layer-pass')


if __name__ == '__main__':
    unittest.main(verbosity=2)
