"""Storage wire contract, bounded reads and existing cleanup safety integration."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from vtx import storage
from vtx.cache.hygiene import CleanupPlan
from vtx.timeline import timeline_capabilities


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.timeline = self.root / 'timeline.json'
        self.timeline.write_text(json.dumps(dict(version=2, project=dict(fps=30, width=1920,
            height=1080, duration_frames=30), sources=[], video_tracks=[], audio_tracks=[], subtitles=[])))

    def put(self, name, data=b'abc'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def call(self, operation, request=None):
        # Existing GC may probe encoder availability; make tests deterministic.
        with patch('vtx.cache.hygiene.seg_encoder_kind', return_value='h264'):
            return storage.storage_operation(self.timeline, operation, request)

    def request(self, plan):
        return dict(plan_id=plan['plan']['id'], content_version=plan['content_version'], confirm=storage.CONFIRM)

    def test_inspect_logical_counts_links_and_hardlinks_zero_write_without_ctx(self):
        self.put('cache/a.bin', b'12345')
        self.put('out/final.mp4', b'12')
        self.put('original.mov', b'123')
        os.link(self.root / 'original.mov', self.root / 'hard.mov')
        (self.root / 'alias').symlink_to(self.root / 'original.mov')
        (self.root / 'linked-dir').symlink_to(self.root / 'cache', target_is_directory=True)
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        with patch('vtx.pipeline.infra.Ctx', side_effect=AssertionError('Ctx forbidden')), \
             patch('vtx.cache.policy.read_policy') as policy:
            policy.return_value.mode = 'aggressive'
            result = self.call('inspect')
        self.assertTrue(result['ok'], result)
        size = self.timeline.stat().st_size
        self.assertEqual(result['usage'], dict(project_bytes=size + 13, cache_bytes=5,
            out_bytes=2, other_bytes=size + 6, file_count=5, symlinks_skipped=2))
        self.assertEqual(result['cache_mode'], 'aggressive')
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))
        self.assertFalse((self.root / 'cache/.cache-policy.lock').exists())
        self.assertFalse((self.root / 'cache/.cache-policy.lock.lock').exists())

    @unittest.skipUnless(os.name == 'posix' and getattr(os, 'O_NOFOLLOW', 0), 'POSIX no-follow pinning')
    def test_timeline_ancestor_substitution_is_refused_before_content_read(self):
        from vtx.runtime import fstxn
        identity = fstxn.cleanup_identity(self.timeline)
        moved = self.root.with_name(self.root.name + '-moved')
        self.assertFalse(moved.exists())
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside).resolve()
            (external / 'timeline.json').write_bytes(b'secret external bytes')
            def replace_ancestor(_):
                self.root.rename(moved)
                self.root.symlink_to(external, target_is_directory=True)
                return identity
            try:
                with patch.object(fstxn, 'cleanup_identity', side_effect=replace_ancestor), \
                     patch('vtx.storage.os.read', wraps=os.read) as read:
                    with self.assertRaises(OSError):
                        storage._version(self.timeline)
                    read.assert_not_called()
            finally:
                if self.root.is_symlink():
                    self.root.unlink()
                if moved.exists():
                    moved.rename(self.root)

    def test_inspect_and_plan_bind_exact_lf_and_crlf_timeline_bytes(self):
        candidate = self.put('cache/old.bin', b'obsolete')
        lf = json.dumps(json.loads(self.timeline.read_bytes()), indent=2).encode('utf-8') + b'\n'
        plans = []
        for raw in (lf, lf.replace(b'\n', b'\r\n')):
            with self.subTest(newline=raw[-2:]):
                self.timeline.write_bytes(raw)
                version = 'sha256:' + hashlib.sha256(raw).hexdigest()
                for op in ('inspect', 'plan'):
                    result = self.call(op)
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['content_version'], version)
                    if op == 'plan':
                        self.assertIn('cache/old.bin', [e['path'] for e in result['plan']['entries']])
                        plans.append(result['plan']['id'])
                self.assertEqual(self.timeline.read_bytes(), raw)
                self.assertEqual(list(candidate.parent.iterdir()), [candidate])
                self.assertEqual(candidate.read_bytes(), b'obsolete')
                self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['cache', 'timeline.json'])
        if len(plans) == 2:
            self.assertNotEqual(*plans, 'line-ending edits must invalidate the previous plan')

    def test_incomplete_scan_has_no_totals(self):
        self.put('a', b'1')
        for kwargs in ({'MAX_ENTRIES': 1}, {'SCAN_SECONDS': -1}):
            with patch.multiple(storage, **kwargs):
                result = self.call('inspect')
            self.assertFalse(result['ok'])
            self.assertEqual(result['diagnostics'][0]['code'], 'scan.limit')
            self.assertNotIn('usage', result)

    def test_plan_and_clean_protect_author_source_products_locks(self):
        stale = self.put('cache/ancient/data.bin', b'garbage')
        author = self.put('cache/ancient/original.svg', b'<svg/>')
        source = self.put('cache/source.mov', b'original')
        data = json.loads(self.timeline.read_text())
        data['sources'] = [dict(id='src', path='cache/source.mov')]
        self.timeline.write_text(json.dumps(data))
        final = self.put('out/final.mp4', b'final')
        lock = self.put('cache/ancient/producer.lock', b'')
        plan = self.call('plan')
        self.assertTrue(plan['ok'], plan)
        self.assertFalse((self.root / 'cache/.cache-policy.lock').exists())
        self.assertFalse((self.root / 'cache/.cache-policy.lock.lock').exists())
        self.assertIn('cache/ancient/data.bin', [e['path'] for e in plan['plan']['entries']])
        result = self.call('clean', self.request(plan))
        self.assertTrue(result['ok'], result)
        self.assertFalse(stale.exists())
        for protected in (author, source, final, lock):
            self.assertTrue(protected.exists(), protected)
        self.assertEqual(result['removed']['bytes'], len(b'garbage'))
        self.assertEqual(result['remaining'], dict(bytes=0, count=0))
        self.assertTrue((self.root / 'cache/.cache-policy.lock.lock').exists())

    def test_stale_on_in_place_edit_added_descendant_and_timeline_bytes(self):
        stale = self.put('cache/old/data.bin', b'123')
        for mutation in ('overwrite', 'descendant', 'timeline'):
            plan = self.call('plan')
            self.assertTrue(plan['ok'], plan)
            if mutation == 'overwrite':
                stale.write_bytes(b'456')
            elif mutation == 'descendant':
                self.put('cache/old/extra.bin', b'new')
            else:
                self.timeline.write_bytes(self.timeline.read_bytes() + b'\n')
            result = self.call('clean', self.request(plan))
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['diagnostics'][0]['code'], 'plan.stale')
            self.assertTrue(stale.exists())

    def test_replacement_identity_is_stale(self):
        stale = self.put('cache/old.bin')
        plan = self.call('plan')
        replacement = self.put('replacement.bin')
        replacement.replace(stale)
        result = self.call('clean', self.request(plan))
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['diagnostics'][0]['code'], 'plan.stale')
        self.assertTrue(stale.exists())

    def test_clean_failures_and_retention_report_actual_not_planned_bytes(self):
        stale = self.put('cache/old.bin', b'123456')
        plan = self.call('plan')
        for effect in (OSError('denied'), None):
            with patch.object(CleanupPlan, 'execute', side_effect=effect):
                result = self.call('clean', self.request(plan))
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['diagnostics'][0]['code'], 'clean.incomplete')
            self.assertEqual(result['removed'], dict(bytes=0, count=0))
            self.assertEqual(result['remaining'], dict(bytes=6, count=1))
            self.assertTrue(stale.exists())

    def test_partial_failure_reports_only_actual_removed_bytes(self):
        first = self.put('cache/a.bin', b'12')
        second = self.put('cache/b.bin', b'345')
        plan = self.call('plan')
        def partial(cleanup):
            # Exercise the real pinned deletion engine for exactly one sealed
            # category entry, then emulate an OS error on the next operation.
            from vtx.runtime import fstxn
            fstxn.remove_pinned(first, cleanup.identities[first], preserve=cleanup.preserve)
            raise OSError('second deletion denied')
        with patch.object(CleanupPlan, 'execute', partial):
            result = self.call('clean', self.request(plan))
        self.assertFalse(result['ok'])
        self.assertEqual(result['removed'], dict(bytes=2, count=1))
        self.assertEqual(result['remaining'], dict(bytes=3, count=1))
        self.assertTrue(second.exists())

    def test_post_execute_scan_failure_marks_unknown(self):
        self.put('cache/old.bin')
        plan = self.call('plan')
        scan = storage._scan
        executed = []
        def fail_after(root):
            if executed:
                raise storage.StorageError('scan.limit', 'incomplete')
            return scan(root)
        with patch.object(CleanupPlan, 'execute', lambda _: executed.append(True)), \
             patch.object(storage, '_scan', side_effect=fail_after):
            result = self.call('clean', self.request(plan))
        self.assertFalse(result['ok'])
        self.assertTrue(result['outcome_unknown'])
        self.assertEqual(result['diagnostics'][0]['code'], 'clean.outcome_unknown')
        self.assertNotIn('removed', result)

    def test_plan_is_zero_write_and_routes_existing_logging_to_stderr(self):
        self.put('cache/old.bin')
        before = storage._scan(self.root)
        from vtx.pipeline.infra import Ctx
        def noisy(path):
            print('dependency log')
            return Ctx(path)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('vtx.pipeline.infra.Ctx', side_effect=noisy), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = self.call('plan')
        self.assertTrue(result['ok'], result)
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn('dependency log', stderr.getvalue())
        self.assertEqual(storage._scan(self.root), before)

    def test_plan_refuses_links_without_inspecting_targets(self):
        (self.root / 'cache').mkdir()
        (self.root / 'cache/alias').symlink_to(self.root / 'missing-target')
        with patch('vtx.pipeline.infra.Ctx', side_effect=AssertionError('must refuse before Ctx')):
            result = self.call('plan')
        self.assertFalse(result['ok'])
        self.assertEqual(result['diagnostics'][0]['code'], 'plan.links')

    def test_linked_cache_root_clean_never_creates_external_lock(self):
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external).resolve()
            (self.root / 'cache').symlink_to(outside, target_is_directory=True)
            request = dict(plan_id='sha256:' + 'a' * 64,
                           content_version='sha256:' + 'b' * 64, confirm=storage.CONFIRM)
            result = self.call('clean', request)
            self.assertFalse(result['ok'])
            self.assertEqual(result['diagnostics'][0]['code'], 'plan.links')
            self.assertEqual(list(outside.iterdir()), [])

    def test_dependency_error_does_not_break_inspect(self):
        self.put('cache/old.bin')
        with patch('vtx.pipeline.infra.Ctx', side_effect=ValueError('missing plugin/input')):
            result = self.call('plan')
            self.assertFalse(result['ok'])
            self.assertEqual(result['diagnostics'][0]['code'], 'plan.dependencies')
            self.assertTrue(self.call('inspect')['ok'])

    def test_bound_plan_and_wire_before_execute(self):
        stale = self.put('cache/old.bin')
        plan = self.call('plan')
        with patch.object(CleanupPlan, 'execute') as execute, patch.object(storage, 'WIRE_LIMIT', 300):
            result = self.call('clean', self.request(plan))
            execute.assert_not_called()
        self.assertFalse(result['ok'])
        self.assertEqual(result['diagnostics'][0]['code'], 'output.limit')
        self.assertTrue(stale.exists())
        with patch.object(storage, 'MAX_PLAN_ENTRIES', 0):
            result = self.call('plan')
        self.assertEqual(result['diagnostics'][0]['code'], 'plan.limit')

    def test_astral_diagnostic_is_bounded_after_actual_deletion(self):
        self.put('cache/old.bin', b'123')
        plan = self.call('plan')
        execute = CleanupPlan.execute
        def failing(cleanup):
            execute(cleanup)
            raise OSError('\U0001f4a5' * 2048)
        with patch.object(CleanupPlan, 'execute', failing), patch.object(storage, 'WIRE_LIMIT', 65536):
            result = self.call('clean', self.request(plan))
            self.assertLessEqual(len(storage.encode(result).encode()) + 1, 65536)
        self.assertFalse(result['ok'])
        self.assertEqual(result['content_version'], plan['content_version'])
        self.assertEqual(result['removed'], dict(bytes=3, count=1))
        self.assertEqual(result['remaining'], dict(bytes=0, count=0))
        self.assertEqual(result['diagnostics'][0]['code'], 'clean.incomplete')

    def test_post_execute_wire_fallback_preserves_actual_accounting(self):
        self.put('cache/old.bin', b'123')
        plan = self.call('plan')
        execute = CleanupPlan.execute
        def failing(cleanup):
            execute(cleanup)
            storage.WIRE_LIMIT = 512  # Force serialization failure AFTER deletion.
            raise OSError('\U0001f4a5' * 2048)
        with patch.object(CleanupPlan, 'execute', failing), patch.object(storage, 'WIRE_LIMIT', 65536):
            result = self.call('clean', self.request(plan))
            self.assertLessEqual(len(storage.encode(result).encode()) + 1, 512)
        self.assertFalse(result['ok'])
        self.assertEqual(result['content_version'], plan['content_version'])
        self.assertEqual(result['removed'], dict(bytes=3, count=1))
        self.assertEqual(result['remaining'], dict(bytes=0, count=0))
        self.assertEqual(result['diagnostics'][0]['code'], 'clean.output_limit')

    def test_post_execute_wire_fallback_preserves_unknown_outcome(self):
        self.put('cache/old.bin')
        plan = self.call('plan')
        scan = storage._scan
        started = []
        def execute(_):
            started.append(True)
            storage.WIRE_LIMIT = 512
        def fail_after(root):
            if started:
                raise OSError('\U0001f4a5' * 2048)
            return scan(root)
        with patch.object(CleanupPlan, 'execute', execute), \
             patch.object(storage, '_scan', side_effect=fail_after), \
             patch.object(storage, 'WIRE_LIMIT', 65536):
            result = self.call('clean', self.request(plan))
            self.assertLessEqual(len(storage.encode(result).encode()) + 1, 512)
        self.assertFalse(result['ok'])
        self.assertTrue(result['outcome_unknown'])
        self.assertEqual(result['content_version'], plan['content_version'])
        self.assertNotIn('removed', result)
        self.assertEqual(result['diagnostics'][0]['code'], 'clean.output_limit')

    def test_strict_request_and_capability(self):
        for request in (None, {}, dict(plan_id='x', content_version='y', confirm=storage.CONFIRM),
                        dict(plan_id='sha256:' + 'a'*64, content_version='sha256:' + 'b'*64,
                             confirm=storage.CONFIRM, paths=['cache/old.bin'])):
            result = self.call('clean', request)
            self.assertEqual(result['diagnostics'][0]['code'], 'input.request')
        self.assertFalse((self.root / 'cache').exists())
        self.assertEqual(timeline_capabilities()['capabilities']['storage'],
                         dict(schema=storage.SCHEMA, operations=['inspect', 'plan', 'clean'], request_stdin=True))

    def cli(self, *args, payload=None):
        return subprocess.run([sys.executable, '-m', 'vtx', '-t', str(self.timeline), 'storage', *args],
                              input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_cli_json_and_irrelevant_flags_and_request_limit(self):
        for args in (('inspect', '--all'), ('plan', '--request-stdin'), ('clean',),
                     ('inspect', '--dry-run'), ('plan', '--request', 'arbitrary')):
            result = self.cli(*args)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(json.loads(result.stdout)['diagnostics'][0]['code'], 'cli.arguments')
        result = self.cli('clean', '--request-stdin', payload=b'x' * (storage.REQUEST_LIMIT + 1))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['diagnostics'][0]['code'], 'input.limit')
        result = self.cli('inspect')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])


if __name__ == '__main__':
    unittest.main()
