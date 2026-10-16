"""Validate-only failure evidence cannot authorize formal artifacts or reuse.

Byte fixtures and mocked providers only: no media decoding, rendering or network.
"""
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vtx import agent_diagnostics, cache_state, cli, composite, fstxn, hygiene, qc, stages
from vtx.infra import Ctx
from tests.test_agent_diagnostics import timeline


class VerifiedFailureEvidence(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'timeline.json'
        self.source = self.root / 'source.mp4'
        self.tl = timeline()
        self.tl['sources'] = [{'id': 'source', 'path': self.source.name}]
        self.write()
        # Existing formal evidence must survive failure-only reporting untouched.
        seeds = {
            'out/receipt.json': {'schema': cache_state.RECEIPT_SCHEMA,
                                'input_fingerprint': 'old-verified-pin',
                                'stages': {'validate': {'ok': True}, 'qc': {'ok': True}}},
            'out/receipt.prev.json': {'previous': 'keep'},
            'cache/qc_pass_segs.json': {'version': 2, 'checks': {'keep': True}},
            'cache/audio/loudness_receipts.json': {'keep': 'loudness'},
        }
        for name, value in seeds.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))

    def write(self):
        self.path.write_text(json.dumps(self.tl))

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() if path.is_file() else None
                for path in self.root.rglob('*')}

    def context(self):
        return Ctx(self.path, diagnostics=[], allow_unverified_validation=True)

    def assert_unverified(self, context):
        self.assertIsNone(context.input_fingerprint)
        self.assertTrue(context._unverified_validation)
        self.assertEqual(context.source_identities, {})

    def facts(self):
        return {'fps': 30, 'nb_frames': 90, 'has_video': True, 'has_audio': True,
                'w': 160, 'h': 90}

    def invalid_timeline(self):
        self.tl['video_tracks'] = [
            {'name': 'empty', 'kind': 'aroll', 'clips': []},
            {'name': 'gfx', 'kind': 'gfx', 'clips': [
                {'src': 'absent.html', 'dst_in': -1, 'dur': 10}]},
        ]
        self.tl['subtitles'] = [{'in': -1, 'out': 10, 'text': 'bad'}]
        self.tl['audio_tracks'] = [{'name': 'voice', 'events': None}]
        self.write()

    def validate_failure(self, context):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
             patch.object(context, 'run', side_effect=AssertionError('unexpected media process')), \
             patch.object(context, 'probe', return_value=self.facts()), \
             patch('vtx.stages._plugin_hooks.run_probe_source', return_value=None), \
             self.assertRaises(SystemExit) as caught:
            stages.validate(context)
        self.assertEqual(caught.exception.code, 1)
        return output.getvalue()

    def test_missing_source_accumulates_native_failures_without_writes(self):
        self.invalid_timeline()
        before = self.snapshot()
        c = self.context()
        self.assert_unverified(c)
        with patch.object(fstxn, 'atomic_write', side_effect=AssertionError('formal write')) as atomic, \
             patch.object(fstxn, 'locked_update', side_effect=AssertionError('formal transaction')) as locked:
            c.begin_stage('validate')
            output = self.validate_failure(c)
            c.receipt('validate', False, 'failure evidence only')
        atomic.assert_not_called(); locked.assert_not_called()
        expected = ['validate.source_exists', 'validate.track_empty', 'validate.clip_bounds',
                    'validate.gfx_exists', 'validate.subtitle_bounds', 'validate.audio_events_structure']
        found = [record['rule_id'] for record in c.diagnostics if record['rule_id'].startswith('validate.')]
        self.assertEqual(found, expected)
        self.assertIn('校验失败（6 项）', output)
        self.assertNotIn('校验通过', output)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(cache_state.load_receipt(c.out / 'receipt.json', None)['stages'], {})

    def test_cli_and_agent_validate_keep_failure_text_and_artifacts(self):
        self.invalid_timeline()
        before = self.snapshot()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(Ctx, 'run', side_effect=AssertionError('unexpected media process')), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
             patch.object(sys, 'argv', ['vtx', '-t', str(self.path), 'validate']), \
             self.assertRaises(SystemExit) as caught:
            cli.main()
        self.assertEqual(caught.exception.code, 1)
        legacy_output = stdout.getvalue()
        legacy_errors = stderr.getvalue()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(Ctx, 'run', side_effect=AssertionError('unexpected media process')), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = agent_diagnostics.main(['-t', str(self.path), 'validate', '--format', 'agent-json'])
        report = json.loads(stdout.getvalue())
        self.assertEqual(result, 1)
        self.assertEqual(report['exit_code'], 1)
        self.assertEqual(report['legacy_output'], {'stdout': legacy_output, 'stderr': legacy_errors})
        self.assertEqual(stderr.getvalue(), '')
        self.assertGreaterEqual(len({r['rule_id'] for r in report['diagnostics']
                                     if r['rule_id'].startswith('validate.')}), 5)
        self.assertEqual(self.snapshot(), before)

    def test_concrete_source_io_and_nonregular_failures_are_unverified(self):
        self.source.write_bytes(b'source bytes')
        original_stat, original_open = Path.stat, os.open
        def deny_stat(path, *args, **kwargs):
            if path == self.source:
                raise PermissionError('source stat unavailable')
            return original_stat(path, *args, **kwargs)
        def deny_open(path, *args, **kwargs):
            if Path(path) == self.source:
                raise PermissionError('source open unavailable')
            return original_open(path, *args, **kwargs)
        for kind, target, name, replacement in (
                ('stat', Path, 'stat', deny_stat), ('open', os, 'open', deny_open),
                ('read', os, 'read', lambda *args: (_ for _ in ()).throw(OSError('source read unavailable')))):
            before = self.snapshot()
            with self.subTest(kind=kind), patch.object(target, name, replacement):
                self.assert_unverified(self.context())
            self.assertEqual(self.snapshot(), before)
        self.source.unlink(); self.source.mkdir()
        before = self.snapshot()
        self.assert_unverified(self.context())
        self.assertEqual(self.snapshot(), before)

    def test_missing_first_source_does_not_mask_later_native_loader_error(self):
        self.tl['sources'] = [{'id': 'missing', 'path': '0-missing.mp4'},
                              {'id': 'broken', 'path': None}]
        self.write()
        records = []
        before = self.snapshot()
        with self.assertRaises(TypeError) as caught:
            Ctx(self.path, diagnostics=records, allow_unverified_validation=True)
        try:
            self.root / None
        except TypeError as expected:
            self.assertEqual(str(caught.exception), str(expected))
        self.assertEqual(records[-1]['rule_id'], 'load.operation')
        self.assertEqual(records[-1]['json_path'], '$.sources[1].path')
        self.assertTrue(getattr(caught.exception, '_vtx_diagnostic_handled', False))
        self.assertEqual(self.snapshot(), before)

    def test_opaque_identity_callbacks_are_not_converted_to_unavailable(self):
        self.source.write_bytes(b'source bytes')
        for name in ('file_digest', 'verified_media_identity'):
            for error in (PermissionError('opaque callback'), ValueError('opaque callback'),
                          RuntimeError('opaque callback')):
                with self.subTest(name=name, error=type(error).__name__):
                    records = []
                    before = self.snapshot()
                    with patch.object(cache_state, name, side_effect=error) as callback, \
                         self.assertRaises(type(error)) as caught:
                        Ctx(self.path, diagnostics=records, allow_unverified_validation=True)
                    self.assertIs(caught.exception, error)
                    callback.assert_called_once()
                    self.assertFalse(getattr(error, '_vtx_diagnostic_handled', False))
                    self.assertEqual(records, [])
                    self.assertEqual(self.snapshot(), before)

    def test_later_opaque_plugin_failure_is_not_replayed_or_hidden(self):
        error = PermissionError('opaque plugin dependency callback')
        with patch('vtx.pluginlib.hooks.active_plugins_digest', side_effect=error) as callback, \
             self.assertRaises(PermissionError) as caught:
            self.context()
        self.assertIs(caught.exception, error)
        callback.assert_called_once()
        self.assertFalse(getattr(error, '_vtx_diagnostic_handled', False))

    def test_declared_dependency_error_keeps_precedence_over_missing_source(self):
        self.tl['project']['cache_dependencies'] = ['missing-dependency.txt']
        self.write()
        records = []
        with patch.object(cache_state, 'file_digest', side_effect=AssertionError('digest reached')) as digest, \
             self.assertRaises(ValueError):
            Ctx(self.path, diagnostics=records, allow_unverified_validation=True)
        digest.assert_not_called()
        self.assertEqual(records[0]['rule_id'], 'load.cache_dependency')
        self.assertEqual(records[0]['json_path'], '$.project.cache_dependencies[0]')

    def test_external_subtitle_failure_is_still_reached(self):
        self.tl['subtitles_src'] = 'missing-subtitles.srt'
        self.write()
        before = self.snapshot()
        c = self.context()
        self.assert_unverified(c)
        output = self.validate_failure(c)
        self.assertEqual([record['rule_id'] for record in c.diagnostics
                          if record['rule_id'] in ('validate.source_exists', 'subtitle.source_exists')],
                         ['validate.source_exists', 'subtitle.source_exists'])
        self.assertIn('subtitles_src', output)
        self.assertEqual(self.snapshot(), before)

    def test_source_recovery_does_not_promote_existing_context(self):
        c = self.context()
        self.source.write_bytes(b'recovered source')
        before = self.snapshot()
        self.assert_unverified(c)
        # Every native validation predicate now passes, but the context remains
        # failure-only and must reject before printing a successful validation.
        output = io.StringIO()
        with patch.object(c, 'probe', return_value=self.facts()), \
             patch('vtx.stages._plugin_hooks.run_probe_source', return_value=None), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaises((cache_state.InputIdentityError, SystemExit)) as caught:
            stages.validate(c)
        if isinstance(caught.exception, SystemExit):
            self.assertNotEqual(caught.exception.code, 0)
        self.assertNotIn('校验通过', output.getvalue())
        self.assert_unverified(c)
        for action in (lambda: c.receipt('validate', True), lambda: cache_state.receipt_fingerprint(c),
                       lambda: cache_state.verify_input_identity(c), lambda: hygiene.audio_key(c, 'source')):
            with self.assertRaises(cache_state.InputIdentityError):
                action()
        self.assertEqual(self.snapshot(), before)
        fresh = Ctx(self.path)
        self.assertIsInstance(fresh.input_fingerprint, str)
        self.assertEqual(fresh.source_identities[self.source.name]['level'], 'Verified')

    def test_formal_keys_and_lifecycle_reject_unverified_context(self):
        c = self.context()
        clip = {'src': 'source', 'src_in': 0, 'src_out': 30, 'dur': 30}
        before = self.snapshot()
        actions = {
            'fingerprint': lambda: cache_state.receipt_fingerprint(c),
            'verify': lambda: cache_state.verify_input_identity(c),
            'begin audit': lambda: c.begin_stage('audit'),
            'begin qc': lambda: c.begin_stage('qc'),
            'success validate': lambda: c.receipt('validate', True),
            'failure audit': lambda: c.receipt('audit', False),
            'success qc': lambda: c.receipt('qc', True),
            'audio key': lambda: hygiene.audio_key(c, 'source'),
            'segment key': lambda: hygiene.seg_key(c, clip, 0, 0, False, False, 'x264'),
            'gfx key': lambda: hygiene.gfx_key(c, {'src': 'missing.html', 'dur': 30}),
        }
        with patch.object(fstxn, 'locked_update', side_effect=AssertionError('formal transaction')) as locked:
            for label, action in actions.items():
                with self.subTest(label=label), self.assertRaises(cache_state.InputIdentityError):
                    action()
        locked.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_producers_and_qc_memory_reject_before_process_or_writer(self):
        c = self.context()
        before = self.snapshot()
        product = c.out / 'final.mp4'
        actions = {
            'audit': lambda: stages.audit(c), 'prepare': lambda: stages.prepare(c),
            'audio': lambda: stages.build_audio(c), 'mux': lambda: stages.mux(c),
            'layers': lambda: composite.prepare_layers(c),
            'video': lambda: composite.build_video(c), 'gfx': lambda: cli.gfx(c),
            'qc': lambda: qc.qc(c), 'load qc memory': lambda: qc._load_qc_pass_record(c),
            'write qc memory': lambda: qc._write_qc_pass_record(c, 90, []),
            'manifest': lambda: hygiene.manifest_record(c, product, 'mux'),
            'temporary output': lambda: hygiene.tmp_run(c, 'blocked'),
            'versions check': lambda: hygiene.versions_check(c, product),
            'versions record': lambda: hygiene.versions_record(c, product, 'blocked'),
            'history': lambda: hygiene.rotate_history(c, product, 2),
        }
        with contextlib.ExitStack() as stack:
            spies = [stack.enter_context(patch.object(module, name,
                        side_effect=AssertionError('forbidden producer side effect')))
                     for module, name in ((fstxn, 'atomic_write'), (fstxn, 'locked_update'),
                                          (fstxn, 'mkdir'), (qc.loudness_cache, 'lookup'),
                                          (qc.loudness_cache, 'measure_and_store'), (c, 'run'))]
            for label, action in actions.items():
                with self.subTest(label=label), self.assertRaises(cache_state.InputIdentityError):
                    action()
            for spy in spies:
                spy.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_unverified_context_allows_only_probe_process_dispatch(self):
        c = self.context()
        response = SimpleNamespace(returncode=0, stdout='{}', stderr='')
        with patch('vtx.infra.subprocess.run', return_value=response) as run:
            for command in (['ffmpeg', '-version'], [sys.executable, '-c', 'pass']):
                with self.subTest(command=command), self.assertRaises(cache_state.InputIdentityError):
                    c.run(command)
            run.assert_not_called()
            self.assertIs(c.run(['ffprobe', '-version']), response)
            run.assert_called_once()

    def test_audit_and_qc_entrypoints_remain_strict_verified(self):
        before = self.snapshot()
        for command in ('audit', 'qc'):
            with self.subTest(command=command), \
                 patch.object(Ctx, 'run', side_effect=AssertionError('unexpected media process')) as run, \
                 patch.object(agent_diagnostics, command, side_effect=AssertionError('producer reached')) as producer, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                result = agent_diagnostics.main(['-t', str(self.path), command, '--format', 'agent-json'])
                self.assertEqual(result, 1)
                self.assertEqual(json.loads(output.getvalue())['exit_code'], 1)
                run.assert_not_called(); producer.assert_not_called()
            with patch.object(sys, 'argv', ['vtx', '-t', str(self.path), command]), \
                 patch.object(Ctx, 'run', side_effect=AssertionError('unexpected media process')) as run, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit) as caught:
                cli.main()
            self.assertEqual(caught.exception.code, 1)
            run.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_original_seven_action_repair_fixture_with_mocked_audio_probe(self):
        from tests import test_agent_diagnostics_repair as repair
        def invoke(args, **kwargs):
            self.assertEqual(args[:3], [sys.executable, '-m', 'vtx.agent_diagnostics'])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = agent_diagnostics.main(args[3:])
            return SimpleNamespace(returncode=code, stdout=output.getvalue(), stderr='')
        facts = {'fps': None, 'nb_frames': 90, 'has_video': False,
                 'has_audio': True, 'w': 0, 'h': 0}
        with patch.object(repair.subprocess, 'run', side_effect=invoke), \
             patch.object(Ctx, 'run', side_effect=AssertionError('unexpected media process')), \
             patch.object(Ctx, 'probe', return_value=facts), \
             patch('vtx.stages._plugin_hooks.run_probe_source', return_value=None):
            report = repair.run_fixture()
        self.assertEqual(len(report['applied']), 7)
        self.assertEqual(report['before']['exit_code'], 1)
        self.assertEqual(report['after']['exit_code'], 0)
        self.assertIn('validate.source_exists',
                      {item['rule_id'] for item in report['before']['diagnostics']})

    def test_complete_inputs_keep_verified_agent_validation_receipt(self):
        payload = b'complete source bytes'
        self.source.write_bytes(payload)
        with patch.object(Ctx, 'probe', return_value=self.facts()), \
             patch.object(Ctx, 'run', side_effect=AssertionError('unexpected media process')), \
             patch('vtx.stages._plugin_hooks.run_probe_source', return_value=None), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = agent_diagnostics.main(['-t', str(self.path), 'validate', '--format', 'agent-json'])
        report = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(report['exit_code'], 0)
        self.assertIn('校验通过', report['legacy_output']['stdout'])
        receipt = json.loads((self.root / 'out/receipt.json').read_text())
        self.assertTrue(receipt['stages']['validate']['ok'])
        self.assertEqual(receipt['schema'], cache_state.RECEIPT_SCHEMA)
        identity = receipt['source_identities'][self.source.name]
        self.assertEqual(identity['level'], 'Verified')
        self.assertEqual(identity['scope'], 'complete-file')
        self.assertEqual(identity['digest'], hashlib.sha256(payload).hexdigest())
        fresh = self.context()
        self.assertFalse(fresh._unverified_validation)
        self.assertEqual(receipt['input_fingerprint'], fresh.input_fingerprint)
        self.assertIsInstance(hygiene.audio_key(fresh, 'source'), str)

    def test_render_plan_rejects_before_keys_or_seeded_writes(self):
        c = self.context()
        plan = composite.RenderPlan(c, False, 1, 160, 90, nseg=1)
        target = self.root / 'cache' / 'subs.ass'
        target.write_bytes(b'seeded global ASS')
        segment = self.root / 'cache' / 'vseg' / 'subs_000000.ass'
        segment.parent.mkdir()
        segment.write_bytes(b'seeded segment ASS')
        before = self.snapshot()
        operations = [
            lambda: plan.write_global_ass(target),
            lambda: plan.write_seg_ass(target, 0, c.dur),
            lambda: plan._seg_content_key(0, c.dur, [], False, None),
            lambda: plan.composite([], self.root / 'out' / 'video.mp4', True),
            lambda: plan._composite_graph([], self.root / 'out' / 'video.mp4', True),
            lambda: plan._composite_passes([], self.root / 'out' / 'video.mp4', True, 0, c.dur),
            lambda: plan.render_segmented(self.root / 'out' / 'video.mp4'),
        ]
        with patch.object(fstxn, 'atomic_write', side_effect=AssertionError('writer reached')), \
             patch.object(fstxn, 'mkdir', side_effect=AssertionError('mkdir reached')), \
             patch.object(composite, 'segment_content_key', side_effect=AssertionError('key reached')), \
             patch.object(c, 'run', side_effect=AssertionError('process reached')):
            for index, operation in enumerate(operations):
                with self.subTest(entry=index), self.assertRaises(cache_state.InputIdentityError):
                    operation()
                self.assertEqual(self.snapshot(), before)
        self.assert_unverified(c)
        self.assertIsInstance(plan.segment_ass_bytes(0, c.dur), bytes)
        self.assertIsInstance(plan.ass_head(), list)

    def test_render_plan_verified_and_readonly_planning_remain_usable(self):
        self.source.write_bytes(b'tiny complete source')
        c = self.context()
        self.assertFalse(c._unverified_validation)
        plan = composite.RenderPlan(c, False, 1, 160, 90, nseg=1)
        target = self.root / 'cache' / 'subs.ass'
        plan.write_global_ass(target)
        self.assertIn(b'[Events]', target.read_bytes())
        plan.write_seg_ass(target, 0, c.dur)
        self.assertEqual(target.read_bytes(), plan.segment_ass_bytes(0, c.dur))
        self.assertIsInstance(plan._seg_content_key(0, c.dur, [], True, target), str)
        # Empty-layer graph reaches its existing dispatch without a real process.
        with patch.object(c, 'run') as run, patch.object(composite, 'pick_vcodec', return_value=([], 'test')):
            plan.composite([], self.root / 'out' / 'video.mp4', False)
            run.assert_called_once()
        readonly = Ctx(self.path, verify_sources=False)
        readonly_plan = composite.RenderPlan(readonly, False, 1, 160, 90)
        before = self.snapshot()
        self.assertEqual(readonly_plan.segment_ass_bytes(0, readonly.dur), plan.segment_ass_bytes(0, c.dur))
        self.assertIsInstance(readonly_plan._seg_content_key(0, readonly.dur, [], False, None), str)
        self.assertEqual(before, self.snapshot())


if __name__ == '__main__':
    unittest.main()
