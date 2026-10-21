"""Issue #59: snapshot atomicity and fixed-name write confinement."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vtx import fstxn, hygiene, loudness_cache, subs
from vtx.infra import Ctx, ProjectPathError

REPO = Path(__file__).resolve().parents[2]


class WriteSafetyResidual(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
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
        from vtx import composite, gfx_render, qc
        cache = self.root / 'cache'
        cache.mkdir()
        (cache / 'subs.ass').symlink_to(self.victim)
        plan = composite.RenderPlan(self.c, False, 1.0, self.c.w, self.c.h)
        with self.assertRaises(ProjectPathError):
            plan.write_global_ass(cache / 'subs.ass')
        self.assertEqual(self.victim.read_bytes(), b'unchanged')
        (cache / 'gfx').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(ProjectPathError):
            gfx_render._atomic_write_bytes(cache / 'gfx/f0000.png', b'frame')
        (cache / 'seg').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(ProjectPathError):
            fstxn.mkdir(cache / 'seg/nested')
        self.assertEqual(list(self.outside.iterdir()), [self.victim])
        report = self.root / 'out/qc_report.json'
        report.parent.mkdir()
        report.symlink_to(self.victim)
        with self.assertRaises(ProjectPathError):
            fstxn.atomic_write(report, '{"checks": []}')
        self.assertEqual(self.victim.read_bytes(), b'unchanged')

    def test_unsupported_platform_degrades_to_legacy_without_paralysis(self):
        """issue #59 返工：能力缺失平台不得抛错瘫痪——写入仍成功、降级标记
        显式可观测（write_capability()=='legacy' + stderr 一次性记录）。"""
        target = self.root / 'cache/fixed.json'
        marker = io.StringIO()
        with mock.patch.object(fstxn, '_FD_WRITE_SUPPORT', False), \
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
