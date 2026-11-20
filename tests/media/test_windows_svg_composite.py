"""The native SVG route uses the shared graph with Windows-owned media."""
import hashlib
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from vtx.cache import recovery
from vtx.pipeline import composite
from vtx.render.frame_source import PngSequence


class WindowsSvgComposite(unittest.TestCase):
    def test_unqualified_subtitles_cannot_silently_render_without_captions(self):
        c = SimpleNamespace(_windows_svg_media=object(), tl={'subtitles': [{'in': 0, 'out': 2, 'text': 'Example'}]})
        with self.assertRaisesRegex(ValueError, 'qualified caption profile'):
            composite.build_video(c)

    def test_native_concat_names_are_the_names_the_owner_will_hold(self):
        path = PureWindowsPath("D:/Project #1/L’artiste's clip.mp4")
        self.assertEqual(
            composite._concat_manifest([(path, 0, 3)], 30, resolve_paths=False),
            "file 'D:/Project #1/L’artiste'\\''s clip.mp4'\nduration 0.100000\n",
        )

    def test_prepared_ordinary_segment_reuse_calls_native_certificate_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'plain.mp4'
            source.write_bytes(b'fixture source bytes')
            media = SimpleNamespace(valid=mock.Mock(return_value=True))
            clip = {'src': 'plain', 'src_in': 0, 'src_out': 6, 'dst_in': 0, 'dur': 6}
            c = SimpleNamespace(root=root, cache=root / 'cache', fps=30, spf=1 / 30, w=64, h=64,
                                tl={'video_tracks': [{'clips': [clip]}]}, _windows_svg_media=media,
                                src_path=lambda sid: source, crop169=lambda sid: 'crop=64:36',
                                probe=lambda path: {'has_alpha': False}, verify_inputs=mock.Mock())
            run_tasks = mock.Mock()
            layers = composite.prepare_layers(c, run_tasks=run_tasks)
            self.assertEqual(len(layers), 1)
            self.assertFalse(layers[0]['gfx'])
            media.valid.assert_called_once_with(c, layers[0]['seg'], 6, allow_short=True)
            run_tasks.assert_called_once_with(c, [], 'seg 预编码')

    def test_qc_clean_matches_native_bottom_source_semantics(self):
        media = object()
        for track_kind, alpha, effects, expected in (
                ('gfx', False, {}, True), ('video', False, {}, False),
                ('video', True, {}, True), ('video', False, {'opacity': 0.5}, True),
                ('video', False, {'fade_in_frames': 3}, True),
                ('video', False, {'x': 4, 'scale': 0.5}, True)):
            with self.subTest(track_kind=track_kind, alpha=alpha, effects=effects):
                clip = {'src': 'clip', **effects}
                c = SimpleNamespace(_windows_svg_media=media, src_path=Path,
                                    probe=lambda path: {'has_alpha': alpha},
                                    tl={'video_tracks': [{'kind': track_kind, 'clips': [clip]}]})
                self.assertEqual(composite.needs_qc_clean(c), expected)

    @unittest.skipUnless(sys.platform == 'win32', 'native QC input pin')
    def test_fresh_qc_source_pipe_bytes_are_pinned_through_shared_graph(self):
        from tests.gfx._png import png

        with tempfile.TemporaryDirectory() as temp:
            scratch = Path(temp) / 'scratch'
            private = scratch / 'reference-1'
            private.mkdir(parents=True)
            source = Path(temp) / 'original.mp4'
            payload = png(16, 16, 128)
            media = SimpleNamespace(scratch=scratch)
            c = SimpleNamespace(_windows_svg_media=media, src_path=lambda sid: source,
                                cache=Path(temp) / 'cache', fps=30, spf=1 / 30, dur=6,
                                dry=False, w=64, h=64,
                                probe=lambda path: {'w': 32, 'h': 32})
            clip = {'src': 'clip', 'src_in': 2, 'dur': 6, 'x': 4, 'scale': 0.5, 'opacity': 0.5}

            def decode(argv, *, text):
                self.assertFalse(text)
                self.assertEqual(argv[-5:], ['-c:v', 'png', '-f', 'image2pipe', '-'])
                self.assertIn(str(source), argv)
                self.assertIn('colorchannelmixer=aa=0.5', argv[argv.index('-vf') + 1])
                return subprocess.CompletedProcess(argv, 0, payload, b'')

            def encode(argv):
                frame = private / 'f0000.png'
                self.assertEqual(frame.read_bytes(), payload)
                with self.assertRaises(PermissionError):
                    with frame.open('r+b'):
                        pass
                self.assertIn(str(private / 'f%04d.png'), argv)
                self.assertIn('),4,NAN)', argv[argv.index('-filter_complex') + 1])
                self.assertEqual(Path(argv[-1]), private / 'reference.mp4')

            media.execute = mock.Mock(side_effect=decode)
            c.run = mock.Mock(side_effect=encode)
            self.assertEqual(composite.render_source_frame(c, clip, 3, private), private / 'reference.mp4')
            media.execute.assert_called_once()
            c.run.assert_called_once()

    @unittest.skipUnless(sys.platform == 'win32', 'native cache publication and sharing modes')
    def test_multipass_requires_certified_held_predecessor_not_sidecar(self):
        from vtx.render.gfx_svg_win_media import WindowsSvgMedia
        from vtx.render.gfx_svg_win_publish import anchored

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project, scratch = root / 'project', root / 'scratch'
            project.mkdir()
            scratch.mkdir()
            media = WindowsSvgMedia.__new__(WindowsSvgMedia)
            media.project, media.scratch = project, scratch
            media.operation = SimpleNamespace(keep=False)
            media.identity = {'profile': 'fixture'}
            media.published = {}
            c = SimpleNamespace(root=project, cache=project / 'cache', _windows_svg_media=media,
                                fps=30, spf=1 / 30, dur=6, dry=False, pass_cache='none',
                                input_fingerprint='fixture-input')
            plan = composite.RenderPlan(c, False, 1, 64, 64)
            prepared = c.cache / 'seg' / 'source.mp4'
            # The ordinary input is handled by execute's existing command scope;
            # the extra predecessor hold tested here spans its replacement run.
            layers = [dict(cl={'dst_in': 0, 'dur': 6}, seg=prepared, gfx=False,
                           x=0, y=0, w2=None, h2=None)]
            for index in (1, 2):
                source = PngSequence(scratch / f'png{index}', f'svg-{index}', 6, 30, 64, 64)
                layers.append(dict(cl={'dst_in': 0, 'dur': 6}, source=source, gfx=True,
                                   x=index * 4, y=2, w2=None, h2=None))
            encoded = []

            def encode(argv):
                target = Path(argv[-1])
                self.assertEqual(target.parent, scratch)
                for option, value in zip(argv, argv[1:]):
                    if option == '-i' and value.endswith('.mkv'):
                        with self.assertRaises(PermissionError):
                            with Path(value).open('r+b'):
                                pass
                encoded.append((target.suffix, argv))
                target.write_bytes(f'pass {len(encoded)}'.encode())
                return subprocess.CompletedProcess(argv, 0, '', '')

            def inspect(path, suffix, *, execute):
                return {'frames': 6, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

            def no_legacy(*args, **kwargs):
                self.fail('native pass touched legacy filesystem operations')

            c.run = lambda argv: recovery.atomic_ffmpeg(c, argv, encode)
            with anchored(scratch) as media.directory, \
                 mock.patch.object(recovery, '_inspect', side_effect=inspect), \
                 mock.patch('vtx.cache.cache_state.verify_input_identity'), \
                 mock.patch.object(PngSequence, 'require_ready'), \
                 mock.patch.multiple(composite.fstxn, mkdir=no_legacy, file_lock=no_legacy,
                                     cleanup_identity=no_legacy, remove_pinned=no_legacy, atomic_write=no_legacy):
                plan.composite(layers, project / 'out' / 'cold.mp4', False, 0, 6)
                passes = sorted((c.cache / 'vseg_pass').glob('*.mkv'))
                self.assertEqual(len(passes), 2)
                self.assertEqual([suffix for suffix, _ in encoded], ['.mkv', '.mkv', '.mp4'])
                self.assertIn(str(prepared), encoded[0][1])
                self.assertIn(str(passes[0]), encoded[1][1])
                self.assertIn(str(passes[1]), encoded[2][1])
                self.assertTrue(all(media.valid(c, path, 6) for path in passes))
                self.assertFalse(list((c.cache / 'vseg_pass').glob('*.n')))

                encoded.clear()
                plan.composite(layers, project / 'out' / 'warm.mp4', False, 0, 6)
                self.assertEqual([suffix for suffix, _ in encoded], ['.mp4'])
                self.assertIn(str(passes[1]), encoded[0][1])

                passes[1].write_bytes(b'uncertified replacement')
                passes[1].with_suffix('.mkv.n').write_text('6', encoding='ascii')
                encoded.clear()
                plan.composite(layers, project / 'out' / 'recovered.mp4', False, 0, 6)
                self.assertEqual([suffix for suffix, _ in encoded], ['.mkv', '.mp4'])
                self.assertIn(str(passes[0]), encoded[0][1])
                self.assertTrue(media.valid(c, passes[1], 6))
