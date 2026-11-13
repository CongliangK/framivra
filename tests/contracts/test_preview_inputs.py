"""Preview-local input adaptation, no browser or external renderer required."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from vtx.cache.policy import CachePolicy
from vtx.render.preview_inputs import PreviewInputs, _run_tasks
from vtx.runtime import fstxn
from tests.gfx._png import write_png


class PreviewInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []
        self.clip = {'src': 'page.html', 'dst_in': 3, 'dur': 5,
                     'scale': 0.5, 'opacity': 0.7, 'fade_in_frames': 3}
        self.c = SimpleNamespace(root=self.root, cache=self.root / 'cache',
            fps=30, w=16, h=16, dry=False, cache_policy=CachePolicy('core'),
            tl={'video_tracks': [{'kind': 'gfx', 'clips': [self.clip]}]},
            verify_inputs=lambda stage: self.calls.append(('verify', stage)),
            run=lambda command: self.calls.append(('run', command)))
        png = self.root / 'source.png'
        write_png(png, width=16, height=16)
        self.data = png.read_bytes()

    def test_only_visible_graphics_requested_and_clip_clock_is_not_rebased(self):
        invisible = {'src': 'cold.html', 'dst_in': 10, 'dur': 2}
        self.c.tl['video_tracks'][0]['clips'].append(invisible)
        scratch = self.root / 'cache' / 'preview' / 'request' / 'inputs'
        fstxn.mkdir(scratch, root=self.root)
        with patch('vtx.render.gfx_packed_reader.read_frame', return_value=self.data) as read:
            sources = PreviewInputs(self.c, 5)
            identity = sources.identity()
            layers = sources.materialize(scratch)
        self.assertEqual([call.args[2] for call in read.call_args_list], [2, 2])
        self.assertTrue(all(call.args[1] is self.clip for call in read.call_args_list))
        self.assertEqual(identity[0]['asset']['png_sha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(len(layers), 1)
        self.assertIs(layers[0]['cl'], self.clip)
        self.assertEqual(layers[0]['source_stop'], 3)
        self.assertEqual(layers[0]['source'].input_args(2, 3)[-1], str(scratch / 'gfx-0' / 'f%04d.png'))
        self.assertEqual((scratch / 'gfx-0' / 'f0002.png').read_bytes(), self.data)
        self.assertFalse((scratch / 'gfx-0' / 'f0000.png').exists())
        self.assertFalse((scratch / 'seg').exists())

    def test_staged_graphics_digest_remains_bound_to_authenticated_bytes(self):
        scratch = self.root / 'cache' / 'preview' / 'request' / 'inputs'
        fstxn.mkdir(scratch, root=self.root)
        with patch('vtx.render.gfx_packed_reader.read_frame', return_value=self.data):
            sources = PreviewInputs(self.c, 5)
            layer = sources.materialize(scratch)[0]
            sources.identity()
            write_png(layer['source'].frame_path(2), width=16, height=16, frame=7)
            with self.assertRaisesRegex(ValueError, 'digest changed'):
                sources.identity()

    def test_off_never_calls_graphics_reader_and_invisible_gfx_does_not_block(self):
        self.c.cache_policy = CachePolicy('off')
        with patch('vtx.render.gfx_packed_reader.read_frame', side_effect=AssertionError('old cache')):
            with self.assertRaisesRegex(ValueError, 'off mode'):
                PreviewInputs(self.c, 5)
            empty = PreviewInputs(self.c, 9)
            self.assertEqual(empty.identity(), [])
            self.assertEqual(empty.materialize(self.root / 'unused'), [])
        self.assertFalse(self.c.cache.exists())

    def test_changed_authenticated_gfx_bytes_are_not_blessed_after_render(self):
        with patch('vtx.render.gfx_packed_reader.read_frame', side_effect=[self.data, self.data + b'x']):
            sources = PreviewInputs(self.c, 5)
            with self.assertRaisesRegex(ValueError, 'changed'):
                sources.identity()

    def test_native_preparation_uses_original_executor_pin_and_only_visible_clips(self):
        visible = {'src': 's', 'src_in': 0, 'src_out': 5, 'dst_in': 3, 'dur': 5}
        invisible = {'src': 's', 'src_in': 0, 'src_out': 9, 'dst_in': 9, 'dur': 9}
        self.c.tl['video_tracks'] = [
            {'kind': 'aroll', 'clips': [invisible]},
            {'kind': 'pip', 'clips': [invisible, visible]}]
        source = self.root / 'original.mp4'
        source.write_bytes(b'only a source identity fixture')
        self.c.src_path = lambda _: source
        self.c.probe = lambda _: {'has_alpha': False}
        scratch = self.root / 'cache' / 'preview' / 'request' / 'inputs'
        layer = {'ti': 1, 'cl': visible, 'seg': scratch / 'seg' / 'native.mp4', 'gfx': False}
        with patch('vtx.media.subs._layer_ready', side_effect=AssertionError('no old media')), \
                patch('vtx.media.encoders.seg_encoder_kind', return_value='x264') as encoder:
            sources = PreviewInputs(self.c, 5)
            identity = sources.identity()
            self.assertEqual(len(identity), 1)
            self.assertIn('seg_key', identity[0]['asset'])
            encoder.return_value = 'nvenc'
            self.assertNotEqual(sources.identity(), identity, 'cold preview key must bind the production encoder')
            encoder.return_value = 'x264'
            with patch('vtx.pipeline.composite.prepare_layers', return_value=[layer]) as prepare, \
                    patch('vtx.render.preview_inputs.media_cache_valid', return_value=True), \
                    patch('vtx.render.preview_inputs._file', return_value={'fixture': 'native-seg'}):
                self.assertEqual(sources.materialize(scratch), [layer])
        local = prepare.call_args.args[0]
        self.assertIsNot(local, self.c)
        self.assertEqual(local.cache, scratch)
        self.assertEqual(local.gfx_cache, self.c.cache)
        self.assertEqual(local.cache_store, self.c.cache)
        self.assertIs(local.run, self.c.run)
        self.assertEqual([track['clips'] for track in local.tl['video_tracks']], [[], [visible]])
        self.assertEqual(self.c.tl['video_tracks'][1]['clips'], [invisible, visible])
        local.verify_inputs('video')
        self.assertEqual(self.calls, [('verify', None)])
        self.assertIs(prepare.call_args.kwargs['run_tasks'], _run_tasks)

    def test_encoder_executor_routes_every_task_through_namespace_guard(self):
        _run_tasks(self.c, [('native', ['ffmpeg', 'preview.mp4'], False)], 'preview')
        self.assertEqual(self.calls, [('run', ['ffmpeg', 'preview.mp4'])])


if __name__ == '__main__':
    unittest.main()
