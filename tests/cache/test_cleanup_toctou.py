"""Issue #56: cleanup plans must not follow replaced ancestors at deletion."""
import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tests._support import temporary_root
from vtx.cli import commands as cli
from vtx.runtime import fstxn, proc_supervisor
from vtx.pipeline.infra import Ctx, ProjectPathError


class CleanupToctou(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self)
        timeline = self.root / 'timeline.json'
        timeline.write_text(json.dumps({
            'project': {'fps': 30, 'duration_frames': 30, 'width': 640, 'height': 360},
            'sources': [], 'video_tracks': [], 'audio_tracks': [], 'subtitles': []}))
        self.c = Ctx(timeline)
        self.protected = self.root / 'out/history/old.mp4'
        self.protected.parent.mkdir(parents=True, exist_ok=True)
        self.protected.write_bytes(b'protected-history')
        self.stale = self.root / 'cache/gfx/stale'
        self.stale.mkdir(parents=True)
        (self.stale / 'active.lock').write_bytes(b'lock')
        (self.stale / 'old.mp4').write_bytes(b'garbage')

    def plan(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return cli._cleanup_plan(self.c, 'gc')

    def exchange(self, path, destination):
        saved = path.with_name(path.name + '-saved')
        path.rename(saved)
        path.symlink_to(destination, target_is_directory=True)
        return saved

    def test_ancestor_replaced_between_plan_and_execute_is_refused(self):
        plan = self.plan()
        self.exchange(self.stale, '../../out/history')
        with self.assertRaises(ProjectPathError):
            cli.ExecutionPolicy.EXECUTE.execute_cleanup(plan)
        self.assertEqual(self.protected.read_bytes(), b'protected-history')

    def test_cache_root_replaced_between_plan_and_execute_is_refused(self):
        plan = self.plan()
        outside = self.root / 'outside'
        (outside / 'gfx/stale').mkdir(parents=True)
        victim = outside / 'gfx/stale/old.mp4'
        victim.write_bytes(b'outside')
        self.exchange(self.c.cache, outside)
        with self.assertRaises(ProjectPathError):
            cli.ExecutionPolicy.EXECUTE.execute_cleanup(plan)
        self.assertEqual(victim.read_bytes(), b'outside')

    def test_normal_garbage_removed_and_lock_preserved(self):
        cli.ExecutionPolicy.EXECUTE.execute_cleanup(self.plan())
        self.assertFalse((self.stale / 'old.mp4').exists())
        self.assertEqual((self.stale / 'active.lock').read_bytes(), b'lock')
        self.assertEqual(self.protected.read_bytes(), b'protected-history')

    def test_swap_at_unlink_does_not_follow_new_ancestor(self):
        plan = self.plan()
        real_unlink = os.unlink
        swapped = []

        def exchange_then_unlink(path, *args, **kwargs):
            if not swapped and Path(path).name == 'old.mp4':
                swapped.append(self.exchange(self.stale, '../../out/history'))
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(os, 'unlink', side_effect=exchange_then_unlink):
            # A pinned directory may finish deleting the original garbage, or
            # refuse the namespace change, but must never enter its replacement.
            try:
                cli.ExecutionPolicy.EXECUTE.execute_cleanup(plan)
            except ProjectPathError:
                pass
        self.assertTrue(swapped)
        self.assertEqual(self.protected.read_bytes(), b'protected-history')
        self.assertTrue((swapped[0] / 'active.lock').exists())

    def test_recursive_child_swap_at_unlink_cannot_follow_outside(self):
        (self.stale / 'active.lock').unlink()
        nested = self.stale / 'nested'
        nested.mkdir()
        (nested / 'old.mp4').write_bytes(b'nested garbage')
        plan = self.plan()
        real_unlink = os.unlink
        swapped = []

        def exchange_then_unlink(path, *args, **kwargs):
            if not swapped and Path(path).name == 'old.mp4':
                swapped.append(self.exchange(nested, self.protected.parent))
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(os, 'unlink', side_effect=exchange_then_unlink):
            try:
                cli.ExecutionPolicy.EXECUTE.execute_cleanup(plan)
            except ProjectPathError:
                pass
        self.assertTrue(swapped)
        self.assertEqual(self.protected.read_bytes(), b'protected-history')

    def test_recursive_child_exchange_before_open_is_refused(self):
        (self.stale / 'active.lock').unlink()
        nested = self.stale / 'nested'
        nested.mkdir()
        (nested / 'old.mp4').write_bytes(b'nested garbage')
        plan = self.plan()
        real_open = os.open
        swapped = []

        def exchange_then_open(path, flags, *args, **kwargs):
            if not swapped and str(path) == 'nested' and kwargs.get('dir_fd') is not None:
                swapped.append(self.exchange(nested, self.protected.parent))
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(os, 'open', side_effect=exchange_then_open):
            with self.assertRaises(ProjectPathError):
                cli.ExecutionPolicy.EXECUTE.execute_cleanup(plan)
        self.assertTrue(swapped)
        self.assertEqual(self.protected.read_bytes(), b'protected-history')

    def test_leaf_symlink_exchange_at_unlink_never_deletes_target(self):
        plan = self.plan()
        real_unlink = os.unlink
        swapped = []

        def exchange_then_unlink(path, *args, **kwargs):
            if not swapped and Path(path).name == 'old.mp4':
                original = self.stale / 'old.mp4'
                real_unlink(original)
                original.symlink_to(self.protected)
                swapped.append(True)
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(os, 'unlink', side_effect=exchange_then_unlink):
            cli.ExecutionPolicy.EXECUTE.execute_cleanup(plan)
        self.assertTrue(swapped)
        self.assertEqual(self.protected.read_bytes(), b'protected-history')

    def test_lock_created_after_plan_is_preserved_in_recursive_delete(self):
        (self.stale / 'active.lock').unlink()
        plan = self.plan()
        (self.stale / 'new.lock').write_bytes(b'new lock')
        cli.ExecutionPolicy.EXECUTE.execute_cleanup(plan)
        self.assertEqual((self.stale / 'new.lock').read_bytes(), b'new lock')
        self.assertFalse((self.stale / 'old.mp4').exists())

    def test_unsupported_fd_primitives_fail_closed(self):
        plan = self.plan()
        with mock.patch.object(os, 'supports_dir_fd', set()):
            with self.assertRaises(ProjectPathError):
                cli.ExecutionPolicy.EXECUTE.execute_cleanup(plan)
        self.assertEqual((self.stale / 'old.mp4').read_bytes(), b'garbage')

    def test_project_root_replaced_after_plan_is_refused(self):
        plan = self.plan()
        destination = self.root / 'replacement'
        (destination / 'cache/gfx/stale').mkdir(parents=True)
        victim = destination / 'cache/gfx/stale/old.mp4'
        victim.write_bytes(b'replacement')
        # Rename the project and substitute a symlink to an attacker tree.
        saved = self.root.with_name(self.root.name + '-saved')
        self.root.rename(saved)
        self.root.symlink_to(saved / 'replacement', target_is_directory=True)
        self.addCleanup(lambda: self.root.unlink(missing_ok=True))
        self.addCleanup(lambda: __import__('shutil').rmtree(saved, ignore_errors=True))
        with self.assertRaises(ProjectPathError):
            cli.ExecutionPolicy.EXECUTE.execute_cleanup(plan)
        self.assertEqual((saved / 'replacement/cache/gfx/stale/old.mp4').read_bytes(),
                         b'replacement')

    def aged_run(self, name, *, locked=False):
        run = self.c.cache / 'tmp' / name
        (run / 'profile').mkdir(parents=True)
        (run / 'profile/data').write_bytes(b'worker profile')
        if locked:
            (run / 'active.lock').write_bytes(b'keep')
        os.utime(run, (1, 1))
        return run

    def test_gc_retains_all_worker_marker_types_without_reaping(self):
        for kind in ('valid', 'broken', 'directory', 'symlink'):
            with self.subTest(kind=kind):
                run = self.aged_run(kind)
                marker = run / proc_supervisor.WORKER_LEDGER
                if kind == 'directory':
                    marker.mkdir()
                elif kind == 'symlink':
                    marker.symlink_to(run / 'missing')
                else:
                    marker.write_text(json.dumps(dict(schema=1, pgid=424242,
                        start_time_ticks=111, host_pid=424243,
                        host_start_time_ticks=222, boot_id='unknown-boot'))
                        if kind == 'valid' else 'broken JSON')
                os.utime(run, (1, 1))
                with mock.patch.object(proc_supervisor, 'reap_run_dir', side_effect=AssertionError('GC must not reap')), \
                        mock.patch.object(proc_supervisor, 'kill_group', side_effect=AssertionError('GC must not signal')):
                    plan = self.plan()
                    self.assertFalse(any(run == p or run in p.parents
                                         for entries in plan.values() for p, _, _ in entries))
                    cli.ExecutionPolicy.EXECUTE.execute_cleanup(plan)
                self.assertEqual((run / 'profile/data').read_bytes(), b'worker profile')
                self.assertTrue(os.path.lexists(marker))

    def test_gc_execution_retains_late_marker_for_whole_and_split_runs(self):
        for locked in (False, True):
            with self.subTest(locked=locked):
                run = self.aged_run('late-' + str(locked), locked=locked)
                plan = self.plan()
                selected = [p for entries in plan.values() for p, _, _ in entries
                            if p == run or run in p.parents]
                self.assertTrue(selected)
                self.assertEqual(run in selected, not locked)
                (run / proc_supervisor.WORKER_LEDGER).write_text('unsettled')
                cli.ExecutionPolicy.PLAN.execute_cleanup(plan)
                cli.ExecutionPolicy.EXECUTE.execute_cleanup(plan)
                self.assertEqual((run / 'profile/data').read_bytes(), b'worker profile')
                self.assertEqual((run / proc_supervisor.WORKER_LEDGER).read_text(), 'unsettled')

    def test_gc_internal_symlink_remains_unlink_only(self):
        run = self.aged_run('linked-worker')
        (run / proc_supervisor.WORKER_LEDGER).write_text('unsettled')
        os.utime(run, (1, 1))
        link = run.with_name('worker-alias')
        link.symlink_to(run, target_is_directory=True)
        cli.ExecutionPolicy.EXECUTE.execute_cleanup(self.plan())
        self.assertFalse(os.path.lexists(link))
        self.assertEqual((run / 'profile/data').read_bytes(), b'worker profile')
        self.assertTrue((run / proc_supervisor.WORKER_LEDGER).is_file())

    def test_gc_guard_preserves_identity_refusal_and_unowned_cleanup(self):
        run = self.aged_run('replaced', locked=True)
        ordinary = self.aged_run('ordinary')
        plan = self.plan()
        saved = run.with_name('saved')
        run.rename(saved)
        run.mkdir()
        (run / 'profile').mkdir()
        (run / 'profile/data').write_bytes(b'replacement')
        (run / proc_supervisor.WORKER_LEDGER).write_text('marker must not hide replacement')
        with self.assertRaises(ProjectPathError):
            cli.ExecutionPolicy.EXECUTE.execute_cleanup(plan)
        self.assertEqual((run / 'profile/data').read_bytes(), b'replacement')
        # A fresh plan still removes genuinely unowned aged runs.
        cli.ExecutionPolicy.EXECUTE.execute_cleanup(self.plan())
        self.assertFalse(ordinary.exists())

    def test_retention_guard_requires_ancestor_and_basename(self):
        expected = fstxn.cleanup_identity(self.stale)
        for directory, marker in ((self.root / 'other', 'marker'),
                                  (self.stale, '../marker'), (self.stale, '')):
            with self.subTest(directory=directory, marker=marker):
                with self.assertRaises(ValueError):
                    fstxn.remove_pinned(self.stale, expected, preserve=lambda p: False,
                                        retain_if_present=(directory, marker))
        self.assertTrue((self.stale / 'old.mp4').exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX dir_fd lookup injection')
    def test_gc_marker_lookup_error_fails_closed(self):
        run = self.aged_run('unreadable')
        plan = self.plan()
        original = os.stat
        def stat(path, *args, **kwargs):
            if path == proc_supervisor.WORKER_LEDGER and kwargs.get('dir_fd') is not None:
                raise PermissionError('marker status unavailable')
            return original(path, *args, **kwargs)
        with mock.patch.object(os, 'stat', side_effect=stat):
            # Keep the existing capability check intact under syscall injection.
            with mock.patch.object(os, 'supports_follow_symlinks', os.supports_follow_symlinks | {os.stat}):
                with self.assertRaises(ProjectPathError):
                    cli.ExecutionPolicy.EXECUTE.execute_cleanup(plan)
        self.assertEqual((run / 'profile/data').read_bytes(), b'worker profile')


if __name__ == '__main__':
    unittest.main()
