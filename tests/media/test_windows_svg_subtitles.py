"""Windows SVG captions: bound source, owned renderer and real pixel timing."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vtx.render import gfx_svg
from vtx.render import gfx_svg_win_subtitles as subs
from vtx.timeline_frame import frame_timeline
from vtx.timeline_reference import reference_timeline


class WindowsCaptionInputs(unittest.TestCase):
    def test_frame_clipping_does_not_retime_cue_or_expand_user_text(self):
        text = "100% [sale]:\\NIt's ready, and {literal}."
        cues = subs._cues([{'in': 3, 'out': 6, 'text': text}], 8)
        profile = subs.Captions(cues, 'face.ttf', b'', 'unused')
        self.assertEqual(profile.window(0, 3), [])
        self.assertEqual(profile.window(4, 5), [(0, 1, text.replace('\\N', '\n'))])
        self.assertEqual(profile.window(6, 8), [])

    def test_rejects_unsafe_or_unsupported_cues(self):
        base = {'in': 0, 'out': 3, 'text': 'captions'}
        for cue in ({**base, 'text': r'{\move(0,0,3,5)}styled'},
                    {**base, 'text': 'first\x00injected'}, {**base, 'text': '\u200b'},
                    {**base, 'text': 'injected\u202eframe'}, {**base, 'text': '\u034f'},
                    {**base, 'in': True}, {**base, 'in': 0.5}, {**base, 'out': 8}):
            with self.subTest(cue=cue), self.assertRaises(ValueError):
                subs._cues([cue], 6)
        with self.assertRaisesRegex(ValueError, 'must not overlap'):
            subs._cues([base, {**base, 'in': 2, 'out': 5}], 6)

    def test_external_source_is_parsed_from_exact_snapshot_or_rejected(self):
        path = Path('project')
        raw = b'[{"in":1,"out":4,"text":"example"}]'
        c = SimpleNamespace(root=path, dur=10, fps=30, tl={'subtitles_src': 'text/captions.json'}, _svg_all=False)
        with patch.object(subs, 'read', side_effect=(raw, raw, b'[]')) as read:
            subs.prepare(c)
            self.assertEqual(c.tl['subtitles'], [{'in': 1, 'out': 4, 'text': 'example'}])
            self.assertEqual(c._subtitles_input_digest(), hashlib.sha256(raw).hexdigest())
            with self.assertRaisesRegex(ValueError, 'changed after parsing'):
                c._subtitles_input_digest()
            read.assert_called_with(path, 'text/captions.json', limit=subs._SOURCE_LIMIT,
                                    expected_path=path / 'text' / 'captions.json')
        with patch.object(subs, 'read') as read:
            for name in ('../captions.srt', 'cache/captions.json', 'out/captions.srt', 'captions.ass', 'C:/captions.srt'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    subs.prepare(SimpleNamespace(tl={'subtitles_src': name}))
            read.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32' and os.environ.get('VTX_WINDOWS_SVG_MEDIA_BIN'),
                         'requires the pinned native Windows media runtime')
    def test_owned_clock_fills_pts_hole_before_caption_and_never_uses_fallback_font(self):
        from contextlib import ExitStack
        from vtx.render.gfx_svg_win_media import WindowsSvgMedia, _STANDARD_TOOLS

        with tempfile.TemporaryDirectory() as root, ExitStack() as pins:
            root = Path(root).resolve()
            scratch = root / 'scratch'
            scratch.mkdir()
            media = WindowsSvgMedia(pins, root, scratch, SimpleNamespace(keep=False),
                                    Path(os.environ['VTX_WINDOWS_SVG_MEDIA_BIN']), _STANDARD_TOOLS)
            cues = ((3, 4, 'X'),)
            name, payload = subs._font(cues)
            c = SimpleNamespace(_windows_svg_media=media, dry=False, fps=120, h=64,
                                _windows_subtitles=subs.Captions(cues, name, payload, subs._sha(payload)),
                                run=lambda argv: media.execute(argv, text=False))
            command = ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:s=64x64:r=120',
                       '-filter_complex', r'[0:v]select=not(eq(n\,2))[base];[base]null[vout]', '-map', '[vout]',
                       '-frames:v', '6', '-fps_mode', 'cfr', '-r', '120', '-pix_fmt', 'gray', '-f', 'rawvideo', '-']
            result = subs.run(c, command, a=0, b=6, label='base')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(result.stdout), 6 * 64 * 64)
            self.assertEqual([f for f in range(6) if max(result.stdout[f * 4096:(f + 1) * 4096]) > 100], [3])
            self.assertEqual(list(scratch.iterdir()), [])

            # A failed explicit font load must never accept an ambient replacement.
            c._windows_subtitles = subs.Captions(cues, name, b'bad font', 'unused')
            self.assertNotEqual(subs.run(c, command, a=0, b=6, label='base').returncode, 0)

    @unittest.skipUnless(sys.platform == 'win32', 'native no-follow subtitle input')
    def test_native_external_hard_link_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root).resolve()
            (path / 'subtitle.srt').write_bytes(b'')
            os.link(path / 'subtitle.srt', path / 'alias.srt')
            with self.assertRaisesRegex(ValueError, 'one hard link'):
                subs.prepare(SimpleNamespace(root=path, tl={'subtitles_src': 'alias.srt'}))


@unittest.skipUnless(sys.platform == 'win32' and all(os.environ.get(key) for key in (
    'VTX_WINDOWS_SVG_PORTABLE', 'VTX_WINDOWS_SVG_SCRATCH', 'VTX_WINDOWS_SVG_MEDIA_BIN')),
    'requires the pinned native Windows SVG and media runtimes')
class WindowsCaptionPixels(unittest.TestCase):
    def test_external_segmented_delivery_and_inline_sparse_frame(self):
        exe = str(Path(os.environ['VTX_WINDOWS_SVG_MEDIA_BIN']) / 'ffmpeg.exe')
        env = {**os.environ, 'VTX_VIDEO_SEGMENTS': '2', 'VTX_CACHE_MODE': 'core'}

        def run(*args, data=None, segments=2, ok=True):
            result = subprocess.run(args, input=data, capture_output=True, timeout=360,
                                    env={**env, 'VTX_VIDEO_SEGMENTS': str(segments)})
            self.assertEqual(result.returncode == 0, ok, (result.stdout + result.stderr).decode('utf-8', 'replace'))
            return result.stdout

        with tempfile.TemporaryDirectory(prefix='vtx-cap-') as directory:
            root = Path(directory).resolve()
            timeline = root / 'timeline.json'
            cli = (sys.executable, '-X', 'utf8', '-m', 'vtx', '-t', str(timeline))
            run(exe, '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:s=1920x1080:r=30',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '3.6', '-c:v',
                'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(root / 'base.mp4'))
            (root / 'frame.svg').write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">'
                '<rect x="600" y="240" width="720" height="360" fill="red"/></svg>', encoding='utf-8')
            (root / 'clip.json').write_text(json.dumps(dict(format=gfx_svg.FORMAT, fps=30, width=1920, height=1080,
                frame_count=8, frames=['frame.svg'] * 8, fonts=[])), encoding='utf-8')
            text = "字幕 100%: 'ready'"
            doc = dict(version=2, project=dict(fps=30, width=1920, height=1080, duration_frames=108),
                       sources=[dict(id='base', path='base.mp4')], video_tracks=[
                       dict(name='video', kind='aroll', clips=[dict(src='base', src_in=0, src_out=108, dst_in=0, dur=108)]),
                       dict(name='graphics', kind='gfx', clips=[dict(format='svg-sequence', src='clip.json', dst_in=8, dur=8)]),
                       ], subtitles=[{'in': 10, 'out': 11, 'text': '字幕'}], audio_tracks=[dict(name='music', duck=False,
                       events=[{'src': 'base', 'src_in': 0, 'in': 0, 'out': 108, 'volume': 1.0}])])
            timeline.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')

            def bright(data):
                self.assertEqual(len(data), 1200 * 240 * 3)
                return sum(min(data[i:i+3]) > 180 for i in range(0, len(data), 3))

            crop = 'crop=1200:240:360:820'

            frame = 10
            version = reference_timeline(timeline, global_frame=frame)['content_version']
            result = frame_timeline(timeline, frame=frame, expected_content_version=version,
                                    render=True, prepare_gfx=True, include_png=True)
            self.assertTrue(result['ok'], result['diagnostics'])
            png = base64.b64decode(result['image']['inline']['data'])
            png_args = (exe, '-v', 'error', '-f', 'png_pipe', '-i', '-', '-vf')
            rgb_args = ('-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-')
            self.assertGreater(bright(run(*png_args, crop, *rgb_args, data=png)), 20)
            r, g, b = run(*png_args, 'crop=1:1:960:450', *rgb_args, data=png)
            self.assertGreater(r - max(g, b), 150, 'the visible SVG is below the subtitle')
            self.assertFalse((root / 'cache').exists() or (root / 'out').exists())

            # External SRT with CRLF; at 30fps the cue straddles the cut at 54.
            (root / 'captions.srt').write_bytes(f'1\r\n00:00:01,667 --> 00:00:01,967\r\n{text}\r\n'.encode('utf-8'))
            doc.update(subtitles=[], subtitles_src='captions.srt')
            timeline.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
            output = run(*cli, 'all').decode('utf-8', 'replace')
            self.assertIn('切点 [54]', output)
            run(*cli, 'qc', '--strict')
            movie = root / 'out' / 'final.mp4'
            receipt = json.loads((root / 'out' / 'receipt.json').read_bytes())
            self.assertTrue(receipt['stages']['qc']['ok'])
            self.assertEqual(receipt['stages']['qc']['media_sha256']['final.mp4'], hashlib.sha256(movie.read_bytes()).hexdigest())
            self.assertFalse(list((root / 'cache').rglob('*.ass')))
            self.assertFalse(list((root / 'cache').rglob('*.graph')))
            def check_movie(frames):
                for frame, on in frames:
                    pixels = run(exe, '-v', 'error', '-i', str(movie), '-vf', f'select=eq(n\\,{frame}),{crop}',
                                 *rgb_args)
                    self.assertEqual(bright(pixels) > 20, on, frame)

            check_movie(((49, False), (50, True), (53, True), (54, True), (58, True), (59, False)))
            (root / 'captions.srt').write_text(f'1\n00:00:01,633 --> 00:00:01,933\n{text}\n', encoding='utf-8')
            run(*cli, 'qc', '--strict', ok=False)
            run(*cli, 'all', segments=1)
            latest = json.loads((root / 'out' / 'receipt.json').read_bytes())
            self.assertNotEqual(receipt['input_fingerprint'], latest['input_fingerprint'])
            self.assertTrue(latest['stages']['qc']['ok'])
            check_movie(((48, False), (49, True), (57, True), (58, False)))
