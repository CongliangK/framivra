"""Issue #59: snapshot atomicity and fixed-name write confinement."""
import contextlib
import io
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path
from unittest import mock

from tests._support import temporary_root
from vtx.runtime import fstxn
from vtx.cache import hygiene
from vtx.media import loudness_cache, subs
from vtx.pipeline.infra import Ctx, ProjectPathError

REPO = Path(__file__).resolve().parents[2]


class WriteSafetyResidual(unittest.TestCase):
    def setUp(self):
        self.base = temporary_root(self)
        self.root = self.base / 'project'
        self.root.mkdir()
        self.outside = self.base / 'outside'
        self.outside.mkdir()
        self.victim = self.outside / 'victim'
        self.victim.write_bytes(b'unchanged')
        self.timeline = self.root / 'timeline.json'
        self.tl = {'project': {'fps': 30, 'duration_frames': 30, 'width': 640,
                               'height': 360}, 'sources': [], 'video_tracks': [],
                   'audio_tracks': [], 'subtitles': []}
        self.save()

    def save(self):
        self.timeline.write_text(json.dumps(self.tl))
        self.c = Ctx(self.timeline)

    def gfx(self, src):
        self.tl['video_tracks'] = [{'kind': 'gfx', 'clips': [
            {'src': src, 'dst_in': 0, 'dur': 30}]}]
        self.save()

    def assert_no_snapshot(self):
        snaps = self.root / 'cache/snapshots'
        self.assertFalse(snaps.exists() and any(p.is_dir() for p in snaps.iterdir()))

    def test_snapshot_invalid_sources_fail_before_writing(self):
        (self.root / 'directory').mkdir()
        for src in ('', '.', 'directory', 'bad\x00.html'):
            with self.subTest(src=src):
                self.gfx(src)
                with self.assertRaises(ProjectPathError):
                    hygiene.snapshot(self.c)
                self.assert_no_snapshot()

    def test_snapshot_asset_read_failure_does_not_publish_partial(self):
        asset = self.root / 'page.html'
        asset.write_text('<p>hello</p>')
        self.gfx('page.html')
        original = Path.read_bytes
        def read(path):
            if path == asset:
                raise OSError('injected read failure')
            return original(path)
        with mock.patch.object(Path, 'read_bytes', read):
            with self.assertRaises((ProjectPathError, OSError)):
                hygiene.snapshot(self.c)
        self.assert_no_snapshot()

    def test_snapshot_swapped_root_after_guard_never_writes_outside(self):
        cache = self.root / 'cache'
        cache.mkdir()
        snaps = cache / 'snapshots'
        snaps.mkdir()
        real = hygiene.ensure_inside_project
        swapped = []
        def guard(path, root, **kwargs):
            result = real(path, root, **kwargs)
            if not swapped and kwargs.get('what') == '快照目录':
                snaps.rename(cache / 'snapshots-original')
                snaps.symlink_to(self.outside, target_is_directory=True)
                swapped.append(True)
            return result
        with mock.patch.object(hygiene, 'ensure_inside_project', side_effect=guard):
            with self.assertRaises(ProjectPathError):
                hygiene.snapshot(self.c)
        self.assertTrue(swapped)
        self.assertEqual(list(self.outside.iterdir()), [self.victim])

    def test_subtitles_preplanted_leaf_does_not_write_through(self):
        cache = self.root / 'cache'
        cache.mkdir()
        (cache / 'subs.ass').symlink_to(self.victim)
        with self.assertRaises(ProjectPathError):
            subs.run_render(self.c, False)
        self.assertEqual(self.victim.read_bytes(), b'unchanged')

    def test_loudness_target_lock_and_legacy_temp_do_not_write_through(self):
        media = self.root / 'out/final.mp4'
        media.parent.mkdir()
        media.write_bytes(b'input')
        directory = self.root / 'cache/audio'
        directory.mkdir(parents=True)
        for name in ('loudness_receipts.json', 'loudness_receipts.json.lock',
                     'loudness_receipts.json.tmp'):
            with self.subTest(name=name):
                link = directory / name
                link.symlink_to(self.victim)
                try:
                    try:
                        loudness_cache.measure_and_store(media, lambda: (-14.0, -1.5),
                                                        root=self.root)
                    except ProjectPathError:
                        pass
                    self.assertEqual(self.victim.read_bytes(), b'unchanged')
                finally:
                    link.unlink(missing_ok=True)
                    for path in directory.iterdir():
                        if path.is_file():
                            path.unlink()

    def test_snapshot_success_has_complete_assets(self):
        (self.root / 'gfx').mkdir()
        (self.root / 'gfx/page.html').write_text('<p>hello</p>')
        self.gfx('gfx/page.html')
        dst = hygiene.snapshot(self.c)
        self.assertEqual((dst / 'timeline.json').read_bytes(), self.timeline.read_bytes())
        self.assertEqual((dst / 'gfx/page.html').read_text(), '<p>hello</p>')

    def test_cli_invalid_source_and_lock_fail_without_traceback(self):
        for src in ('', '.', 'bad\x00.html'):
            with self.subTest(src=src):
                self.gfx(src)
                run = subprocess.run([sys.executable, '-m', 'vtx', '-t',
                                      str(self.timeline), 'snapshot'], cwd=REPO,
                                     capture_output=True, text=True)
                self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertNotIn('Traceback', run.stdout + run.stderr)
                self.assert_no_snapshot()
        self.tl['video_tracks'] = []
        self.save()
        out = self.root / 'out'
        out.mkdir(exist_ok=True)
        (out / 'receipt.json.lock').symlink_to(self.victim)
        run = subprocess.run([sys.executable, '-m', 'vtx', '-t', str(self.timeline),
                              'validate'], cwd=REPO, capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertNotIn('Traceback', run.stdout + run.stderr)
        self.assertEqual(self.victim.read_bytes(), b'unchanged')

    def test_snapshot_late_write_failure_cleans_staging_by_fd(self):
        (self.root / 'page.html').write_text('<p>hello</p>')
        self.gfx('page.html')
        actual = hygiene._snapshot_write_at
        calls = []
        def write(fd, relative, data):
            calls.append(relative)
            if len(calls) == 2:
                raise OSError('injected second write failure')
            return actual(fd, relative, data)
        with mock.patch.object(hygiene, '_snapshot_write_at', side_effect=write):
            with self.assertRaises(ProjectPathError):
                hygiene.snapshot(self.c)
        self.assert_no_snapshot()
        self.assertFalse(any(p.name.endswith('.tmp') for p in
                             (self.root / 'cache/snapshots').iterdir()))

    def test_snapshot_preplanted_staging_hardlink_refused_by_exclusive_create(self):
        """issue #59 缺陷2 回归（红队 mut-hardlink-noexcl 演练固化）：staging
        目录可被列目录发现、叶子名可猜，预置硬链接是普通文件、能穿过
        O_NOFOLLOW——叶子 open 的 O_EXCL 是承载性防护：必须 EEXIST 干净拒绝
        （ProjectPathError + FileExistsError 链），victim（硬链接目标 inode）
        内容绝不被写入，且不留半途快照。"""
        (self.root / 'page.html').write_text('<p>asset</p>')
        self.gfx('page.html')
        planted = []
        real = hygiene._snapshot_write_at
        def write(fd, relative, data):
            # timeline.json 是首个载荷：借首个写窗把 victim 硬链接预置到后续
            # 叶子 page.html（普通文件身份，O_NOFOLLOW 拦不住，唯 O_EXCL 可拒）
            if not planted:
                os.link(self.victim, 'page.html', dst_dir_fd=fd)
                planted.append(True)
            return real(fd, relative, data)
        with mock.patch.object(hygiene, '_snapshot_write_at', side_effect=write):
            with self.assertRaises(ProjectPathError) as raised:
                hygiene.snapshot(self.c)
        self.assertTrue(planted)
        self.assertIsInstance(raised.exception.__cause__, FileExistsError)
        self.assertEqual(self.victim.read_bytes(), b'unchanged')
        self.assert_no_snapshot()
        self.assertFalse(any(p.name.endswith('.tmp') for p in
                             (self.root / 'cache/snapshots').iterdir()))

    def test_snapshot_final_write_root_exchange_does_not_write_outside(self):
        real = hygiene._snapshot_write_at
        snaps = self.root / 'cache/snapshots'
        swapped = []
        def write(fd, relative, data):
            if not swapped:
                snaps.rename(snaps.with_name('saved-snapshots'))
                snaps.symlink_to(self.outside, target_is_directory=True)
                swapped.append(True)
            return real(fd, relative, data)
        with mock.patch.object(hygiene, '_snapshot_write_at', side_effect=write):
            hygiene.snapshot(self.c)
        self.assertEqual(list(self.outside.iterdir()), [self.victim])
        saved = self.root / 'cache/saved-snapshots'
        self.assertEqual(len(list(saved.glob('*/timeline.json'))), 1)

    def test_atomic_final_replace_uses_pinned_parent(self):
        directory = self.root / 'cache/seg'
        directory.mkdir(parents=True)
        target = directory / 'fixed.ass'
        target.write_bytes(b'old')
        outside_target = self.outside / target.name
        outside_target.write_bytes(b'outside')
        actual = os.replace
        swapped = []
        def replace(source, dest, **kwargs):
            if not swapped:
                directory.rename(directory.with_name('saved-seg'))
                directory.symlink_to(self.outside, target_is_directory=True)
                swapped.append(True)
            return actual(source, dest, **kwargs)
        with mock.patch.object(os, 'replace', side_effect=replace):
            fstxn.atomic_write(target, b'new')
        self.assertEqual(outside_target.read_bytes(), b'outside')
        self.assertEqual((self.root / 'cache/saved-seg/fixed.ass').read_bytes(), b'new')

    def test_locked_write_pins_temp_inode_against_final_open_exchange(self):
        target = self.root / 'out/final.mp4'
        with self.assertRaises(ProjectPathError):
            with fstxn.locked_write(target) as tmp:
                actual = tmp.resolve()
                actual.unlink()
                actual.symlink_to(self.victim)
                tmp.write_bytes(b'new media')
        self.assertEqual(self.victim.read_bytes(), b'unchanged')
        self.assertFalse(target.exists())

    def test_lock_final_open_ancestor_exchange_does_not_create_outside(self):
        directory = self.root / 'cache/audio'
        directory.mkdir(parents=True)
        target = directory / 'loudness_receipts.json'
        actual = os.open
        swapped = []
        def open_file(path, flags, *args, **kwargs):
            if str(path) == target.name + '.lock' and not swapped:
                directory.rename(directory.with_name('saved-audio'))
                directory.symlink_to(self.outside, target_is_directory=True)
                swapped.append(True)
            return actual(path, flags, *args, **kwargs)
        with mock.patch.object(os, 'open', side_effect=open_file):
            with fstxn.locked_write(target) as tmp:
                tmp.write_bytes(b'entry')
        self.assertTrue(swapped)
        self.assertEqual(list(self.outside.iterdir()), [self.victim])

    def test_fixed_text_frame_and_directories_reject_external_links(self):
        from vtx.pipeline import composite
        from vtx.render import gfx_render
        from vtx.inspection import qc
        cache = self.root / 'cache'
        cache.mkdir()
        (cache / 'subs.ass').symlink_to(self.victim)
        plan = composite.RenderPlan(self.c, False, 1.0, self.c.w, self.c.h)
        with self.assertRaises(ProjectPathError):
            plan.write_global_ass(cache / 'subs.ass')
        self.assertEqual(self.victim.read_bytes(), b'unchanged')
        (cache / 'gfx').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(ProjectPathError):
            gfx_render._atomic_write_bytes(cache / 'gfx/f0000.png', b'frame', root=self.root)
        (cache / 'seg').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(ProjectPathError):
            fstxn.mkdir(cache / 'seg/nested', root=self.root)
        self.assertEqual(list(self.outside.iterdir()), [self.victim])
        report = self.root / 'out/qc_report.json'
        report.parent.mkdir()
        report.symlink_to(self.victim)
        with self.assertRaises(ProjectPathError):
            fstxn.atomic_write(report, '{"checks": []}')
        self.assertEqual(self.victim.read_bytes(), b'unchanged')

    def other_context(self, parent=None):
        other = (self.base if parent is None else parent) / 'other-project'
        other.mkdir(exist_ok=True)
        timeline = other / 'timeline.json'
        timeline.write_text(json.dumps(self.tl))
        return Ctx(timeline)

    def write_modes(self):
        for native in (False, True):
            if native and not fstxn._probe_fd_writes():
                continue
            yield native

    def test_context_ownership_survives_interleaved_nested_and_threaded_contexts(self):
        # All three roots belong to this one fixture; "outside" is not ambient data.
        (self.root / 'out').symlink_to(self.outside, target_is_directory=True)
        for native in self.write_modes():
            with mock.patch.object(fstxn, '_FD_WRITE_SUPPORT', native):
                for operation in ('receipt', 'begin_stage'):
                    def rejected():
                        with self.assertRaises(ProjectPathError):
                            if operation == 'receipt':
                                self.c.receipt('validate', True)
                            else:
                                self.c.begin_stage('validate')
                    rejected()  # owner remains enforced before any other Ctx exists
                    for interleave in ('sequential', 'nested', 'threaded'):
                        with self.subTest(native=native, operation=operation, interleave=interleave):
                            if interleave == 'threaded':
                                ready = threading.Event()
                                def construct():
                                    self.other_context()
                                    ready.set()
                                def write():
                                    self.assertTrue(ready.wait(10))
                                    rejected()
                                with ThreadPoolExecutor(2) as pool:
                                    tasks = [pool.submit(write), pool.submit(construct)]
                                    for task in tasks:
                                        task.result(timeout=15)
                            elif interleave == 'nested':
                                with fstxn.locked_write(self.root / 'nested.json', root=self.root) as tmp:
                                    self.other_context()
                                    rejected()
                                    tmp.write_text('{}')
                            else:
                                self.other_context()
                                rejected()
                            self.assertEqual(list(self.outside.iterdir()), [self.victim])

    def test_nested_project_cannot_borrow_parent_context_ownership(self):
        nested = self.other_context(self.root)
        sibling = self.root / 'sibling'
        sibling.mkdir()
        nested.out.symlink_to(sibling, target_is_directory=True)
        for native in self.write_modes():
            with self.subTest(native=native), mock.patch.object(fstxn, '_FD_WRITE_SUPPORT', native):
                Ctx(self.timeline)
                for operation in (lambda: nested.receipt('validate', True),
                                  lambda: nested.begin_stage('validate')):
                    with self.assertRaises(ProjectPathError):
                        operation()
                self.assertEqual(list(sibling.iterdir()), [])

    def test_explicit_owner_rejects_every_write_entry_before_mutation(self):
        (self.root / 'out').symlink_to(self.outside, target_is_directory=True)
        source = self.root / 'source'
        source.write_bytes(b'owned')
        def enter(manager):
            with manager:
                self.fail('unsafe transaction body entered')
        for native in self.write_modes():
            with mock.patch.object(fstxn, '_FD_WRITE_SUPPORT', native):
                for target in (self.root / 'out/victim', self.victim):
                    operations = {
                        'atomic': lambda: fstxn.atomic_write(target, b'bad', root=self.root),
                        'update': lambda: fstxn.locked_update(target, lambda _: self.fail('unsafe read'), root=self.root),
                        'write': lambda: enter(fstxn.locked_write(target, root=self.root)),
                        'staging': lambda: enter(fstxn.locked_staging(target, root=self.root)),
                        'lock': lambda: enter(fstxn.file_lock(target, root=self.root)),
                        'mkdir': lambda: fstxn.mkdir(target / 'new', root=self.root),
                        'unlink': lambda: fstxn.unlink(target, root=self.root),
                        'replace-target': lambda: fstxn.replace(source, target, root=self.root),
                        'replace-source': lambda: fstxn.replace(target, source, root=self.root),
                    }
                    if native:
                        operations['parent'] = lambda: enter(fstxn.pinned_parent(target, root=self.root))
                    for name, run in operations.items():
                        with self.subTest(native=native, target=target, operation=name):
                            with self.assertRaises(ProjectPathError):
                                run()
                            self.assertEqual(self.victim.read_bytes(), b'unchanged')
                            self.assertEqual(source.read_bytes(), b'owned')
                            self.assertEqual(list(self.outside.iterdir()), [self.victim])

    def test_receipt_owners_share_locks_not_project_state(self):
        for native in self.write_modes():
            with self.subTest(native=native), mock.patch.object(fstxn, '_FD_WRITE_SUPPORT', native):
                a2, b = Ctx(self.timeline), self.other_context()
                self.c.out.mkdir(exist_ok=True)
                old = {'input_fingerprint': 'stale', 'stages': {'old': {'ok': True}}}
                (self.c.out / 'receipt.json').write_text(json.dumps(old))
                def write(item):
                    c, stage = item
                    c.receipt(stage, True)
                with ThreadPoolExecutor(6) as pool:
                    list(pool.map(write, [(c, f'{i}-{j}') for i, c in enumerate((self.c, a2, b))
                                          for j in range(4)]))
                for c, owners in ((self.c, (0, 1)), (b, (2,))):
                    receipt = json.loads((c.out / 'receipt.json').read_text())
                    self.assertEqual(receipt['input_fingerprint'], c.input_fingerprint)
                    self.assertEqual(set(receipt['stages']), {f'{i}-{j}' for i in owners for j in range(4)})
                    self.assertFalse(list(c.out.glob('.*.tmp')))
                self.assertEqual(json.loads((self.c.out / 'receipt.prev.json').read_text()), old)
                self.assertEqual(list(self.outside.iterdir()), [self.victim])

    def test_explicit_owner_allows_internal_links_and_ledger_leaf_replacement(self):
        directory = self.root / 'storage'
        directory.mkdir()
        alias = self.root / 'alias'
        alias.symlink_to(directory, target_is_directory=True)
        for native in self.write_modes():
            with self.subTest(native=native), mock.patch.object(fstxn, '_FD_WRITE_SUPPORT', native):
                fstxn.mkdir(alias / 'nested', root=self.root)
                fstxn.atomic_write(alias / 'nested/data', b'inside', root=self.root)
                target = alias / 'ledger'
                target.symlink_to(self.victim)
                fstxn.locked_update(target, lambda _: {'owned': True}, root=self.root)
                self.assertFalse(target.is_symlink())
                self.assertEqual(json.loads(target.read_text()), {'owned': True})
                target.unlink()
                self.assertEqual(self.victim.read_bytes(), b'unchanged')

    def test_library_outputs_do_not_inherit_any_context_root(self):
        alias = self.root / 'library-output'
        alias.symlink_to(self.outside, target_is_directory=True)
        media = self.outside / 'out/media.wav'
        media.parent.mkdir()
        media.write_bytes(b'library measurement input')
        shared = self.base / 'shared-library-cache'
        shared.mkdir()
        (self.outside / 'cache').symlink_to(shared, target_is_directory=True)
        for native in self.write_modes():
            with self.subTest(native=native), mock.patch.object(fstxn, '_FD_WRITE_SUPPORT', native):
                Ctx(self.timeline)
                fstxn.atomic_write(alias / 'standalone.json', '{}')
                self.other_context()
                loudness_cache.measure_and_store(media, lambda: (-14.0, -1.5))
                self.assertTrue((shared / 'audio/loudness_receipts.json').is_file())
                fstxn.locked_update(alias / 'standalone.json', lambda _: {'library': True})
                self.assertEqual(json.loads((self.outside / 'standalone.json').read_text()), {'library': True})
                self.assertEqual(self.victim.read_bytes(), b'unchanged')

    def test_unsupported_platform_degrades_to_legacy_without_paralysis(self):
        """issue #59 返工：能力缺失平台不得抛错瘫痪——写入仍成功、降级标记
        显式可观测（write_capability()=='legacy' + stderr 一次性记录）。"""
        target = self.root / 'cache/fixed.json'
        marker = io.StringIO()
        with mock.patch.object(fstxn, '_FD_WRITE_SUPPORT', False), \
                mock.patch.object(fstxn, '_DEGRADED_NOTICED', False), \
                contextlib.redirect_stderr(marker):
            self.assertEqual(fstxn.write_capability(), 'legacy')
            fstxn.atomic_write(target, b'legacy payload')
            self.assertEqual(target.read_bytes(), b'legacy payload')
            # 同一降级进程内，锁事务与目录原语同样走基线安全等级且可用
            fstxn.locked_update(target, lambda d: {'v': 2})
            self.assertEqual(json.loads(target.read_text()), {'v': 2})
            fstxn.mkdir(self.root / 'cache/legacy/dir')
            fstxn.unlink(target)
            self.assertFalse(target.exists())
        self.assertIn('降级', marker.getvalue())
        # 能力探测未被降级模拟污染：真实平台恢复 fd 锚定等级
        with mock.patch.object(fstxn, '_FD_WRITE_SUPPORT', None):
            self.assertEqual(fstxn.write_capability(), 'fd-anchored')


if __name__ == '__main__':
    unittest.main()
