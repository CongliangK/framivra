"""Operation-local PNG facts: ordinary content and lifecycle contracts only."""
import contextlib
import copy
import dataclasses
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest import mock

from vtx import composite, frame_source, gfx_svg
from vtx.frame_source import PngSequence, png_source, png_consumer_scope
from vtx.infra import Ctx
from tests.test_frame_source import write_png


class PngConsumerScopeContract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.make_source('frames')
        self.layers = [self.layer(self.source)]

    def make_source(self, name):
        directory = self.root / name
        directory.mkdir()
        for frame in range(3):
            write_png(directory / f'f{frame:04d}.png', frame=frame)
        return PngSequence(directory, name, 3, 30, 16, 16)

    @staticmethod
    def layer(source):
        return dict(source=source, gfx=True, ti=0, x=0, y=0, w2=None, h2=None,
                    cl=dict(src='page.html', dst_in=0, dur=source.frame_count))

    @staticmethod
    def fingerprints(source):
        return [hashlib.sha256(source.frame_path(i).read_bytes()).hexdigest()
                for i in range(source.frame_count)]

    def test_first_parse_then_full_hash_reuse(self):
        # A successful check outside the operation must not seed its facts.
        self.source.require_ready()
        reads = []
        real_open = open

        class TrackedReader:
            def __init__(self, path, mode):
                self.stream = real_open(path, mode)
                self.record = [Path(path), [], 0]
                reads.append(self.record)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def read(self, size):
                self.record[1].append(size)
                data = self.stream.read(size)
                self.record[2] += len(data)
                return data

        with png_consumer_scope(self.layers) as layers, \
             mock.patch.object(frame_source, 'png_invalid_reason',
                               wraps=frame_source.png_invalid_reason) as parses:
            source = layers[0]['source']
            first = source.input_args(options=['-t', '0.100'])
            self.assertEqual(parses.call_count, 3)
            for frame, (fingerprint, size) in source._png_facts.facts.items():
                data = source.frame_path(frame).read_bytes()
                self.assertEqual((fingerprint, size), (hashlib.sha256(data).hexdigest(), len(data)))
            with mock.patch.object(frame_source, 'open', TrackedReader, create=True):
                second = source.input_args(options=['-t', '0.100'])
            self.assertEqual(first, second)
            self.assertEqual(parses.call_count, 3)
        self.assertEqual(len(reads), 3)
        for path, requests, count in reads:
            self.assertEqual(count, len(path.read_bytes()))
            self.assertTrue(all(0 < size <= 65536 for size in requests))
            self.assertEqual(requests[-1], 1)  # EOF check after the complete bytes.

    def test_same_length_content_change_rejected(self):
        path = self.source.frame_path(0)
        original = path.read_bytes()
        changed = bytearray(original)
        changed[-5] ^= 1
        with png_consumer_scope(self.layers) as layers, \
             mock.patch.object(frame_source, 'png_invalid_reason',
                               wraps=frame_source.png_invalid_reason) as parses:
            source = layers[0]['source']
            source.require_ready()
            path.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, 'digest changed'):
                source.require_ready()
            self.assertEqual(parses.call_count, 3)
            self.assertFalse(source._png_facts.scope.active)
            self.assertFalse(source._png_facts.facts)
            path.write_bytes(original)
            source.require_ready()
            self.assertEqual(parses.call_count, 6)
            self.assertFalse(source._png_facts.facts)

    def test_truncated_and_appended_bytes_rejected(self):
        path = self.source.frame_path(0)
        original = path.read_bytes()
        for changed in (original[:-1], original + b'x' * 100000):
            with self.subTest(length=len(changed)):
                path.write_bytes(original)
                with png_consumer_scope(self.layers) as layers:
                    source = layers[0]['source']
                    source.require_ready()
                    path.write_bytes(changed)
                    with mock.patch.object(frame_source, '_current_png_fingerprint',
                                           wraps=frame_source._current_png_fingerprint) as hashes:
                        with self.assertRaisesRegex(ValueError, 'digest changed'):
                            source.require_ready()
                    self.assertEqual(hashes.call_args.args, (path, len(original)))
                    self.assertFalse(source._png_facts.facts)

    def test_each_read_keeps_dependency_gates(self):
        expected = self.fingerprints(self.source)
        for field in ('interval_check', 'environment_check'):
            with self.subTest(gate=field):
                gate = mock.Mock(return_value=expected)
                original = dataclasses.replace(self.source, **{field: gate})
                with png_consumer_scope([self.layer(original)]) as layers, \
                     mock.patch.object(frame_source, 'png_invalid_reason',
                                       wraps=frame_source.png_invalid_reason) as parses:
                    source = layers[0]['source']
                    for _ in range(3):
                        source.input_args()
                    self.assertEqual(gate.call_count, 3)
                    self.assertEqual(parses.call_count, 3)
                    gate.return_value = ['0' * 64] * 3
                    with self.assertRaisesRegex(ValueError, 'digest changed'):
                        source.input_args()
                    self.assertEqual(gate.call_count, 4)
                    self.assertFalse(source._png_facts.facts)

    def context(self):
        return SimpleNamespace(root=self.root, cache=self.root / 'cache', out=self.root / 'out',
                               fps=30, w=16, h=16, dry=False, draft=False,
                               tl={'video_tracks': [{'kind': 'gfx', 'clips': [self.layers[0]['cl']]}]},
                               verify_inputs=mock.Mock(), receipt=mock.Mock())

    @contextlib.contextmanager
    def mock_render(self, *, failure=None):
        calls = []

        def consume(plan, layers, output, with_subs, **kwargs):
            sources = [layer['source'] for layer in layers if layer['gfx']]
            args = [source.input_args() for source in sources]
            calls.append((sources, Path(output), with_subs, args))
            if failure is not None:
                raise failure
            return 'mock encoder'

        with mock.patch.dict(os.environ, {'VTX_VIDEO_SEGMENTS': '1'}), \
             mock.patch.object(composite, 'budget_gate'), \
             mock.patch.object(composite.RenderPlan, 'write_global_ass'), \
             mock.patch.object(composite.RenderPlan, 'composite', new=consume), \
             contextlib.redirect_stdout(io.StringIO()):
            yield calls

    def test_prepared_source_is_bound_only_inside_build(self):
        c = self.context()
        with mock.patch.object(composite, 'png_source', return_value=self.source), \
             mock.patch.object(composite, 'seg_encoder_kind', return_value='x264'), \
             mock.patch.object(composite, 'run_parallel'), \
             mock.patch.object(frame_source, 'png_invalid_reason',
                               wraps=frame_source.png_invalid_reason) as parses:
            layers = composite.prepare_layers(c)
            self.assertEqual(parses.call_count, 0)
            self.assertIs(layers[0]['source'], self.source)
            with self.mock_render() as calls:
                composite.build_video(c, layers)
            self.assertEqual(parses.call_count, 3)
        bound = calls[0][0][0]
        self.assertIsNot(bound, self.source)
        self.assertIs(layers[0]['source'], self.source)
        self.assertIsNone(self.source._png_facts)
        self.assertFalse(bound._png_facts.scope.active)
        self.assertFalse(bound._png_facts.facts)

    def test_scope_exit_and_cancel_discard_facts(self):
        for failure in (None, RuntimeError('ordinary failed render'), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), \
                 mock.patch.object(frame_source, 'png_invalid_reason',
                                   wraps=frame_source.png_invalid_reason) as parses:
                c = self.context()
                with self.mock_render(failure=failure) as calls:
                    if failure is None:
                        composite.build_video(c, self.layers)
                    else:
                        with self.assertRaises(type(failure)):
                            composite.build_video(c, self.layers)
                        c.receipt.assert_not_called()
                source = calls[0][0][0]
                self.assertFalse(source._png_facts.scope.active)
                self.assertFalse(source._png_facts.facts)
                source.require_ready()
                self.assertEqual(parses.call_count, 6)

    def test_escaped_or_replaced_instance_revalidates(self):
        with mock.patch.object(frame_source, 'png_invalid_reason',
                               wraps=frame_source.png_invalid_reason) as parses:
            with png_consumer_scope(self.layers) as layers:
                source = layers[0]['source']
                source.require_ready()
                copied = dataclasses.replace(source)
                copied.require_ready()
                self.assertEqual(parses.call_count, 6)
                source.require_ready()
                self.assertEqual(parses.call_count, 6)
            source.require_ready()
            self.assertEqual(parses.call_count, 9)

    def test_failed_gate_does_not_leave_reusable_facts(self):
        other = self.make_source('other-frames')
        for failure in ('gate', 'parse', 'cancel', 'interval'):
            with self.subTest(failure=failure):
                gate = mock.Mock(return_value=self.fingerprints(self.source))
                original = dataclasses.replace(self.source, interval_check=gate)
                with png_consumer_scope([self.layer(original), self.layer(other)]) as layers:
                    first, second = [layer['source'] for layer in layers]
                    # Seed a different source to check operation-wide revocation.
                    second.require_ready()
                    if failure == 'parse':
                        path = first.frame_path(1)
                        saved = path.read_bytes()
                        path.write_bytes(b'ordinary truncated PNG')
                    else:
                        first.require_ready()
                    if failure == 'gate':
                        gate.side_effect = ValueError('ordinary input gate failure')
                    if failure == 'cancel':
                        gate.side_effect = KeyboardInterrupt()
                    error = KeyboardInterrupt if failure == 'cancel' else ValueError
                    with self.assertRaises(error):
                        first.input_args(-1 if failure == 'interval' else 0)
                    self.assertFalse(first._png_facts.scope.active)
                    self.assertFalse(first._png_facts.facts)
                    self.assertFalse(second._png_facts.facts)
                    if failure == 'parse':
                        path.write_bytes(saved)
                    gate.side_effect = None
                    with mock.patch.object(frame_source, 'png_invalid_reason',
                                           wraps=frame_source.png_invalid_reason) as parses:
                        first.require_ready()
                        second.require_ready()
                        self.assertEqual(parses.call_count, 6)
                    self.assertFalse(first._png_facts.facts)
                    self.assertFalse(second._png_facts.facts)

    def test_parallel_consumers_share_authoritative_parse(self):
        for fail_second in (False, True):
            with self.subTest(fail_second=fail_second):
                expected = self.fingerprints(self.source)
                gate = mock.Mock(side_effect=[expected, ValueError('ordinary parallel gate failure')]
                                 if fail_second else None, return_value=expected)
                original = dataclasses.replace(self.source, interval_check=gate)
                with png_consumer_scope([self.layer(original)]) as layers, \
                     mock.patch.object(frame_source, 'png_invalid_reason',
                                       wraps=frame_source.png_invalid_reason) as parses:
                    source = layers[0]['source']
                    barrier = threading.Barrier(2)

                    def consume():
                        barrier.wait(timeout=10)
                        return source.input_args()

                    with ThreadPoolExecutor(2) as pool:
                        futures = [pool.submit(consume) for _ in range(2)]
                        failures = [future.exception(timeout=10) for future in futures]
                    self.assertEqual(parses.call_count, 3)
                    self.assertEqual(gate.call_count, 2)
                    if fail_second:
                        self.assertEqual(sum(isinstance(error, ValueError) for error in failures), 1)
                        self.assertFalse(source._png_facts.scope.active)
                        self.assertFalse(source._png_facts.facts)
                    else:
                        self.assertEqual(failures, [None, None])
                        self.assertEqual(futures[0].result(), futures[1].result())

    def test_build_main_and_qc_share_one_scope(self):
        c = self.context()
        with self.mock_render() as calls, \
             mock.patch.object(frame_source, 'png_invalid_reason',
                               wraps=frame_source.png_invalid_reason) as parses, \
             mock.patch.object(frame_source, '_current_png_fingerprint',
                               wraps=frame_source._current_png_fingerprint) as hashes:
            composite.build_video(c, self.layers)
        self.assertEqual(parses.call_count, 3)
        self.assertEqual(hashes.call_count, 3)
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0][0][0], calls[1][0][0])
        self.assertEqual(calls[0][3], calls[1][3])
        self.assertEqual([(call[1].name, call[2]) for call in calls],
                         [('video_noaudio.mp4', True), ('qc_clean.mp4', False)])
        c.receipt.assert_called_once()

    def test_next_build_starts_with_full_parse(self):
        c = self.context()
        with self.mock_render() as calls, \
             mock.patch.object(frame_source, 'png_invalid_reason',
                               wraps=frame_source.png_invalid_reason) as parses:
            composite.build_video(c, self.layers)
            composite.build_video(c, self.layers)
        self.assertEqual(parses.call_count, 6)
        self.assertEqual(len(calls), 4)
        self.assertIsNot(calls[0][0][0], calls[2][0][0])
        self.assertTrue(all(not call[0][0]._png_facts.scope.active for call in calls))
        self.assertEqual(c.receipt.call_count, 2)


class SvgPngConsumerGateContract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.engine = dict(name='inkscape', version='ordinary mock version',
                           executable='/test/inkscape', sha256='e' * 64)
        for name, value in (
            ('engine_identity', self.engine),
            ('fontconfig_identity', dict(executable='/test/fc-scan', version='mock', sha256='f' * 64)),
        ):
            patch = mock.patch.object(gfx_svg, name, return_value=value)
            patch.start()
            self.addCleanup(patch.stop)
        self.svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
                    'viewBox="0 0 16 16"><rect width="16" height="16" fill="red"/></svg>')
        for name in ('f0.svg', 'f1.svg'):
            (self.root / name).write_text(self.svg)
        (self.root / 'generator.js').write_text('// ordinary generator')
        self.clip = dict(format='svg-sequence', src='manifest.json', dst_in=0, dur=2)
        self.manifest = dict(format=gfx_svg.FORMAT, fps=30, width=16, height=16,
                             frame_count=2, frames=['f0.svg', 'f1.svg'], generator='generator.js')
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest))
        timeline = dict(version=2, project=dict(fps=30, width=16, height=16, duration_frames=2),
                        sources=[], video_tracks=[dict(name='graphics', kind='gfx', clips=[self.clip])],
                        subtitles=[], audio_tracks=[])
        (self.root / 'timeline.json').write_text(json.dumps(timeline))
        self.c = Ctx(self.root / 'timeline.json')
        self.source = png_source(self.c, self.clip)
        self.assertIsNone(self.source.interval_check.__self__.scope)
        self.source.directory.mkdir(parents=True)
        for frame in range(2):
            write_png(self.source.frame_path(frame), frame=frame)
        record = gfx_svg.resolve(self.c, self.clip)
        self.marker = self.source.directory / gfx_svg.COMPLETE_FILE
        self.marker.write_text(json.dumps(dict(format='framivra-svg-completion/v1',
            key=gfx_svg.record_key(record), input=record,
            sha256=PngConsumerScopeContract.fingerprints(self.source))))

    def assert_edit_rejected(self, edit):
        with png_consumer_scope([PngConsumerScopeContract.layer(self.source)]) as layers, \
             mock.patch.object(gfx_svg, 'resolve', wraps=gfx_svg.resolve) as resolves, \
             mock.patch.object(frame_source, 'png_invalid_reason',
                               wraps=frame_source.png_invalid_reason) as parses:
            source = layers[0]['source']
            self.assertIsNone(source.interval_check.__self__.scope)
            source.require_ready(0, 1)
            source.require_ready(0, 1)
            self.assertEqual(resolves.call_count, 2)
            self.assertEqual(parses.call_count, 1)
            edit()
            with self.assertRaises(ValueError):
                source.input_args(0, 1)
            self.assertEqual(resolves.call_count, 3)
            self.assertEqual(parses.call_count, 1)
            self.assertFalse(source._png_facts.scope.active)
            self.assertFalse(source._png_facts.facts)
        self.assertFalse(hasattr(self.c, '_svg_consumer_sources'))

    def test_scoped_source_rechecks_manifest(self):
        changed = copy.deepcopy(self.manifest)
        changed['_meta'] = 'ordinary legitimate edit'
        self.assert_edit_rejected(lambda: (self.root / 'manifest.json').write_text(json.dumps(changed)))

    def test_scoped_source_rechecks_shared_generator(self):
        self.assert_edit_rejected(lambda: (self.root / 'generator.js').write_text('// changed generator'))

    def test_scoped_source_rechecks_unrequested_frame(self):
        self.assert_edit_rejected(lambda: (self.root / 'f1.svg').write_text(self.svg.replace('red', 'blue')))

    def test_scoped_source_rechecks_engine_environment(self):
        self.assert_edit_rejected(lambda: self.engine.update(version='ordinary next engine version'))

    def test_scoped_source_rechecks_completion_record(self):
        def edit():
            record = json.loads(self.marker.read_text())
            record['sha256'][0] = '0' * 64
            self.marker.write_text(json.dumps(record))
        self.assert_edit_rejected(edit)

    def test_existing_svg_snapshot_scope_keeps_full_input_gates(self):
        with gfx_svg.consumer_scope(self.c):
            original = png_source(self.c, self.clip)
            self.assertIsNotNone(original.interval_check.__self__.scope)
            with png_consumer_scope([PngConsumerScopeContract.layer(original)]) as layers, \
                 mock.patch.object(gfx_svg, 'resolve', wraps=gfx_svg.resolve) as resolves, \
                 mock.patch.object(frame_source, 'png_invalid_reason',
                                   wraps=frame_source.png_invalid_reason) as parses:
                source = layers[0]['source']
                self.assertIsNone(source.interval_check.__self__.scope)
                source.require_ready(0, 1)
                source.require_ready(0, 1)
                (self.root / 'generator.js').write_text('// changed shared generator')
                with self.assertRaises(ValueError):
                    source.require_ready(0, 1)
                self.assertEqual(resolves.call_count, 3)
                self.assertEqual(parses.call_count, 1)
                self.assertFalse(source._png_facts.scope.active)
                self.assertFalse(source._png_facts.facts)
            self.assertIsNotNone(original.interval_check.__self__.scope)
        self.assertFalse(hasattr(self.c, '_svg_consumer_sources'))

    def assert_custom_interval_gate_preserved(self, snapshot_type, *, alternate=False):
        with gfx_svg.consumer_scope(self.c):
            snapshot = snapshot_type(self.c, self.clip, self.source.directory,
                                     self.source.interval_check.__self__.record,
                                     scope=self.c._svg_consumer_sources)
            snapshot.extra_gate = mock.Mock()
            if alternate:
                def extra_checked_interval(snapshot, start, stop):
                    snapshot.extra_gate()
                    return snapshot.check_interval(start, stop)
                check = MethodType(extra_checked_interval, snapshot)
            else:
                check = snapshot.check_interval
            original = dataclasses.replace(self.source, interval_check=check)
            with png_consumer_scope([PngConsumerScopeContract.layer(original)]) as layers:
                source = layers[0]['source']
                self.assertIs(source.interval_check, check)
                source.require_ready(0, 1)
                self.assertTrue(source._png_facts.facts)
                snapshot.extra_gate.side_effect = ValueError('ordinary custom interval gate failure')
                with self.assertRaisesRegex(ValueError, 'custom interval gate failure'):
                    source.require_ready(0, 1)
                self.assertEqual(snapshot.extra_gate.call_count, 2)
                self.assertFalse(source._png_facts.scope.active)
                self.assertFalse(source._png_facts.facts)

    def test_subclass_interval_gate_is_preserved(self):
        class GuardedSnapshot(gfx_svg.SequenceSnapshot):
            def check_interval(self, start, stop):
                self.extra_gate()
                return super().check_interval(start, stop)
        self.assert_custom_interval_gate_preserved(GuardedSnapshot)

    def test_alternate_bound_interval_gate_is_preserved(self):
        self.assert_custom_interval_gate_preserved(gfx_svg.SequenceSnapshot, alternate=True)
