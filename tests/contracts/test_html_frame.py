"""Current-composite producer authorization and sparse native contract."""
import hashlib
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vtx.api import frame_timeline, timeline_capabilities
from vtx.timeline_frame import _render_inner, _FrameError
from vtx.render.preview_gfx import Producer

REPO = Path(__file__).resolve().parents[2]


class HtmlFrame(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / 'timeline.json'
        self.clip = {'src': 'page.html', 'dst_in': 20, 'dur': 40}
        self.doc = {'version': 2, 'project': {'fps': 30, 'width': 1920, 'height': 1080,
                    'duration_frames': 80}, 'sources': [], 'video_tracks': [
                    {'name': 'page', 'kind': 'gfx', 'clips': [self.clip]}], 'audio_tracks': [], 'subtitles': []}
        self.path.write_text(json.dumps(self.doc))
        self.version = 'sha256:' + hashlib.sha256(self.path.read_bytes()).hexdigest()

    def call(self, **kwargs):
        args = dict(frame=27, render=True, prepare_gfx=True, trust_html=True,
                    include_png=True, expected_content_version=self.version)
        args.update(kwargs)
        return frame_timeline(self.path, **args)

    def test_zero_authority_and_wrong_flags_never_construct_context(self):
        with patch('vtx.timeline_frame._render', side_effect=AssertionError('renderer')), \
             patch('vtx.pipeline.infra.Ctx.__init__', side_effect=AssertionError('Ctx')):
            for kwargs in ({'render': False}, {'prepare_gfx': False}, {'include_png': False},
                           {'prepare_gfx': 'yes'}, {'trust_html': 'yes'},
                           {'expected_content_version': None}, {'expected_content_version': 'stale'},
                           {'frame': 80}, {'max_png_bytes': 0}):
                self.assertFalse(self.call(**kwargs)['ok'], kwargs)
        self.assertFalse((self.root / 'cache').exists())

    def test_visible_static_html_requires_trust_before_context(self):
        reference = {'document': {'raw': json.dumps(self.doc)}}
        with patch('vtx.timeline_frame.sys.platform', 'linux'), \
             patch('vtx.runtime.fstxn.write_capability', return_value='fd-anchored'), \
             patch('vtx.pluginlib.hooks.active_plugins', return_value=[]), \
             patch('vtx.pipeline.infra.Ctx.__init__', side_effect=AssertionError('Ctx')):
            with self.assertRaises(_FrameError) as caught:
                _render_inner(self.path, reference, 27, 8000, prepare_gfx=True,
                              trust_html=False, request=[])
        self.assertEqual(caught.exception.code, 'html.not_trusted')
        self.assertFalse((self.root / 'cache').exists())

    def test_plugins_rejected_before_context(self):
        reference = {'document': {'raw': json.dumps(self.doc)}}
        with patch('vtx.timeline_frame.sys.platform', 'linux'), \
             patch('vtx.runtime.fstxn.write_capability', return_value='fd-anchored'), \
             patch('vtx.pluginlib.hooks.active_plugins', return_value=[{'name': 'unsafe'}]), \
             patch('vtx.pipeline.infra.Ctx.__init__', side_effect=AssertionError('Ctx')):
            with self.assertRaises(_FrameError) as caught:
                _render_inner(self.path, reference, 27, 8000, prepare_gfx=True,
                              trust_html=True, request=[])
        self.assertEqual(caught.exception.code, 'plugins.unsupported')

    def test_native_html_original_page_duration_local_and_no_cache_read(self):
        c = SimpleNamespace(root=self.root, cache=self.root / 'cache', w=1920, h=1080,
                            fps=30, tl=self.doc, frame_root_identity='pin',
                            _gfx_environment=(None, None, '/chrome', None))
        certificate = {'axes': {'backend': 'cdp'}}
        record = {'policy': 'strict', 'closure': 'static-subset-v1'}
        with patch('vtx.runtime.proc_supervisor.supervision_available', return_value=True), \
             patch('vtx.render.gfx_environment.certificate', return_value=certificate), \
             patch('vtx.render.gfx_environment.recheck') as recheck, \
             patch('vtx.render.gfx_dependencies.resolve', return_value=record), \
             patch('vtx.render.gfx_render.render_page', return_value={'failed': []}) as renderer, \
             patch('vtx.runtime.fstxn.mkdir'), \
             patch('vtx.render.png_artifact.read_png', return_value=({}, b'immutable PNG')):
            producer = Producer(c, self.clip, 7)
            self.assertEqual(producer.render(self.root / 'preview'), b'immutable PNG')
            args, kwargs = renderer.call_args
            self.assertEqual(args[0], str(self.root / 'page.html'))
            self.assertEqual(kwargs['frames'], [7])
            self.assertEqual(kwargs['n_frames'], 40)
            self.assertEqual(kwargs['tabs'], 1)
            self.assertEqual(kwargs['dependency_record'], record)
            self.assertEqual(kwargs['ctx'].cache, self.root / 'preview')
            self.assertGreaterEqual(recheck.call_count, 3)
            with patch('vtx.render.gfx_dependencies.resolve', return_value={**record, 'changed': True}):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    producer.check()

    def test_svg_only_requested_original_local_frame_and_declared_fonts(self):
        clip = {**self.clip, 'format': 'svg-sequence'}
        c = SimpleNamespace(root=self.root, cache=self.root / 'cache', w=1920, h=1080,
                            fps=30, tl={**self.doc, 'video_tracks': [{'kind': 'gfx', 'clips': [clip]}]},
                            frame_root_identity='pin')
        record = {'frames': ['other.svg'] * 7 + ['requested.svg'] + ['other.svg'] * 32,
                  'files': {'requested.svg': hashlib.sha256(b'SVG').hexdigest(),
                            'font.ttf': hashlib.sha256(b'FONT').hexdigest()},
                  'fonts': ['font.ttf'], 'engine': {'executable': '/inkscape'}}
        def read(_c, name):
            self.assertIn(name, ('requested.svg', 'font.ttf'))
            return b'SVG' if name == 'requested.svg' else b'FONT'
        with patch('vtx.render.gfx_svg.resolve', return_value=record), \
             patch('vtx.render.gfx_svg.read_input', side_effect=read), \
             patch('vtx.runtime.fstxn.mkdir'), patch('vtx.runtime.fstxn.atomic_write') as write, \
             patch('vtx.render.gfx_svg_stage.run_shell') as shell, \
             patch('vtx.render.png_artifact.read_png', return_value=({}, b'PNG')):
            producer = Producer(c, clip, 7)
            self.assertEqual(producer.render(self.root / 'preview'), b'PNG')
        self.assertEqual([call.args[0].name for call in write.call_args_list], ['f0007.svg', 'font0.ttf'])
        self.assertEqual(shell.call_args.kwargs, dict(count=1, width=1920, height=1080,
                                                     root=self.root, indices=[7]))

    def test_off_mode_fresh_adapter_keeps_clip_order_params_duration_and_sparse_stop(self):
        from vtx.render.preview_inputs import PreviewInputs
        clip = {**self.clip, 'x': 100, 'y': 80, 'opacity': 0.5, 'scale': 0.5}
        c = SimpleNamespace(root=self.root, cache=self.root / 'cache', w=1920, h=1080,
                            fps=30, tl={**self.doc, 'video_tracks': [{'kind': 'gfx', 'clips': [clip]}]},
                            run=lambda *a: None)
        policy = SimpleNamespace(reuse_graphics=False, reuse_media=False)
        with patch('vtx.render.preview_inputs.policy_for', return_value=policy), \
             patch('vtx.render.gfx_packed_reader.read_frame', side_effect=AssertionError('old gfx')), \
             patch('vtx.render.preview_gfx.Producer') as producer, \
             patch('vtx.runtime.fstxn.mkdir'), patch('vtx.runtime.fstxn.atomic_write'), \
             patch('vtx.render.preview_inputs.PngSequence') as sequence:
            producer.return_value.render.return_value = b'PNG'
            producer.return_value.record = {'full': 'identity'}
            sources = PreviewInputs(c, 27, prepare_gfx=True)
            before = sources.identity()
            sources.produce(self.root / 'request')
            after = sources.identity()
            layers = sources.materialize(self.root / 'scratch')
            producer.assert_called_once_with(c, clip, 7)
            self.assertNotEqual(before, after)  # final key must include produced bytes
            self.assertEqual(after[0]['asset']['png_sha256'], hashlib.sha256(b'PNG').hexdigest())
            self.assertIs(layers[0]['cl'], clip)
            self.assertEqual(layers[0]['ti'], 0)
            self.assertEqual(layers[0]['source_stop'], 8)
            self.assertEqual(sequence.call_args.args[2], 40)  # never narrow clip.dur
            self.assertEqual((layers[0]['x'], layers[0]['y']), (100, 80))

    def test_supervision_required_before_identity_or_writes(self):
        c = SimpleNamespace(tl=self.doc)
        with patch('vtx.runtime.proc_supervisor.supervision_available', return_value=False), \
             patch('vtx.render.gfx_environment.certificate', side_effect=AssertionError('certificate')):
            with self.assertRaisesRegex(ValueError, 'supervision'):
                Producer(c, self.clip, 7)
        self.assertFalse((self.root / 'cache').exists())

    def test_scoped_recovery_preserves_live_unknown_and_unsettled_requests(self):
        from vtx.render.preview_gfx import recover_requests, REQUEST_SCHEMA
        c = SimpleNamespace(root=self.root, cache=self.root / 'cache', frame_root_identity='pin')
        request = c.cache / 'preview' / ('request_' + 'a' * 32)
        ledger = request / 'producers' / 'producer-0' / 'tmp' / 'worker-run' / '.worker_pgid.json'
        ledger.parent.mkdir(parents=True)
        ledger.write_text('{}')
        marker = json.dumps({'schema': REQUEST_SCHEMA, 'project': str(c.root)}).encode()
        with patch('vtx.render.png_artifact.read_artifact', return_value=marker), \
             patch('vtx.runtime.fstxn.cleanup_identity', return_value='pin'), \
             patch('vtx.runtime.fstxn.remove_pinned') as remove:
            for status in ('live_host', 'broken_ledger', 'kill_failed', 'lineage_denied', 'pid_reuse'):
                def noisy_reaper(_directory):
                    print('native recovery diagnostic')
                    return status
                wire, diagnostic = io.StringIO(), io.StringIO()
                with redirect_stdout(wire), redirect_stderr(diagnostic), \
                     patch('vtx.runtime.proc_supervisor.reap_run_dir', side_effect=noisy_reaper):
                    with self.assertRaisesRegex(ValueError, 'preserved'):
                        recover_requests(c)
                self.assertEqual(wire.getvalue(), '')
                self.assertIn('native recovery diagnostic', diagnostic.getvalue())
                remove.assert_not_called()
            with patch('vtx.runtime.proc_supervisor.reap_run_dir', return_value='already_dead') as reap:
                recover_requests(c)
                reap.assert_called_once_with(ledger.parent)
                remove.assert_called_once()
        with patch('vtx.render.png_artifact.read_artifact', return_value=b'{}'), \
             patch('vtx.runtime.fstxn.cleanup_identity', return_value='pin'), \
             patch('vtx.runtime.proc_supervisor.reap_run_dir', side_effect=AssertionError('unknown request')):
            recover_requests(c)

    def test_request_cleanup_before_success_and_unsettled_failure(self):
        from vtx.timeline_frame import _render
        request = self.root / 'request'
        request.mkdir()
        def inner(*args, **kwargs):
            kwargs['request'].append((request, 'identity'))
            return ({'kind': 'current-composite'}, b'PNG')
        with patch('vtx.timeline_frame._render_inner', side_effect=inner), \
             patch('vtx.runtime.fstxn.remove_pinned') as remove:
            self.assertEqual(_render(self.path, {}, 27, 8000, prepare_gfx=True)[1], b'PNG')
            remove.assert_called_once()
        (request / '.worker_pgid.json').write_text('{}')
        with patch('vtx.timeline_frame._render_inner', side_effect=inner), \
             patch('vtx.runtime.fstxn.remove_pinned') as remove:
            with self.assertRaises(_FrameError):
                _render(self.path, {}, 27, 8000, prepare_gfx=True)
            remove.assert_not_called()

    def test_inline_complete_composite_contract(self):
        image = dict(kind='current-composite', approximate=False, path=None,
                     delivery='inline-only', cache_hit=False)
        with patch('vtx.timeline_frame._render', return_value=(image, b'PNG')) as renderer:
            result = self.call()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['image']['inline']['data'], 'UE5H')
        self.assertIsNone(result['image']['path'])
        self.assertNotIn('layer', result['image'])
        self.assertTrue(renderer.call_args.kwargs['prepare_gfx'])

    def test_capability_and_cli_reject_foreign_and_obsolete_flags(self):
        capability = timeline_capabilities()['capabilities']['frame']
        self.assertNotIn('html_layer', capability)
        self.assertEqual(capability['prepare_gfx'], dict(flag='--prepare-gfx', trust_flag='--trust-html',
                         kind='current-composite', preview_only=True, inline_required=True))
        base = [sys.executable, '-m', 'vtx', '-t', str(self.path)]
        for command, flags in [('inspect', ['--prepare-gfx']), ('validate', ['--trust-html']),
                               ('timeline', ['frame', '--trust-html']),
                               ('timeline', ['frame', '--prepare-gfx', '--render']),
                               ('timeline', ['frame', '--html-layer', '/video_tracks/0/clips/0'])]:
            proc = subprocess.run(base + [command] + flags, cwd=REPO, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertFalse((self.root / 'cache').exists())


if __name__ == '__main__':
    unittest.main()
