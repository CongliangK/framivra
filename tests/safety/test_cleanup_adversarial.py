"""issue #21/#30：嵌套链接穿透及清理拆掉活动 flock 的红队回归。"""
import json
import os
import subprocess
import sys
from unittest import mock

try:
    from ._support import REPO, SandboxCase
except ImportError:
    from _support import REPO, SandboxCase

from vtx.runtime import fstxn
from vtx import hygiene


class NestedCleanupLinks(SandboxCase):
    def test_gc_nested_gfx_link_cannot_delete_protected_history(self):
        c = self.context()
        old = self.put(c.out / "history/old.mp4", "不可再生历史交付件".encode())
        (c.cache / "gfx").symlink_to("../out/history", target_is_directory=True)
        # issue #21：原复现 gc_plan 把 cache/gfx/old.mp4 当垃圾并 unlink 真实交付件。
        plan = hygiene.gc_plan(c)
        self.assertFalse(any(items for items in plan.values()))
        for argv in (("gc", "--dry-run"), ("gc",), ("gc", "--all"), ("clean",)):
            with self.subTest(argv=argv):
                result = self.run_vtx(*argv)
                self.assert_ok(result)
                self.assertIn("不可再生交付件保护集", result.stdout)
                self.assertEqual(old.read_bytes(), "不可再生历史交付件".encode())
                self.assertTrue((c.cache / "gfx").is_symlink())

    def test_cleanup_nested_links_outside_roots_are_refused(self):
        c = self.context()
        for name in ("gfx", "seg", "audio", "tmp", "vseg", "legacy/nested"):
            p = c.cache / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.symlink_to(self.outside, target_is_directory=True)
        alias = c.out / "intermediate"
        alias.symlink_to(self.outside, target_is_directory=True)
        before = self.tree(self.outside)
        for argv in (("gc",), ("gc", "--all"), ("clean",),
                     ("clean", "--i-know-this-nukes-everything")):
            with self.subTest(argv=argv):
                result = self.run_vtx(*argv)
                self.assert_ok(result)
                self.assertIn("cache/out 根之外", result.stdout)
                self.assertEqual(self.tree(self.outside), before)
                self.assertTrue(alias.is_symlink())
                self.assertTrue((c.cache / "legacy/nested").is_symlink())

    def test_direct_gc_and_plugin_cache_cannot_follow_protected_alias(self):
        c = self.context()
        old = self.put(c.out / "history/old.mp4", b"history")
        (c.cache / "bgm").symlink_to("../out/history", target_is_directory=True)
        with mock.patch.object(hygiene._plugin_hooks, "get_cache_dirs", return_value={"bgm"}), \
             mock.patch.object(hygiene._plugin_hooks, "get_cache_dir_keys", return_value={"bgm": set()}):
            hygiene.gc(c, do=True)
            hygiene.gc(c, do=True, deep=True)
        self.assertEqual(old.read_bytes(), b"history")
        self.assertTrue((c.cache / "bgm").is_symlink())


class CleanupLockInodes(SandboxCase):
    def test_all_cleanup_variants_preserve_nested_and_root_lock_inodes(self):
        c = self.context()
        locks = [self.put(self.root / p, b"lock") for p in (
            "out/receipt.json.lock", "out/VERSIONS.json.lock", "cache/manifest.json.lock",
            "cache/gfx/stale/active.lock", "cache/tmp/orphan/deep/job.lock",
            "out/intermediate/nested/writer.lock")]
        os.utime(c.cache / "tmp/orphan", (1, 1))
        before = {p: p.stat().st_ino for p in locks}
        for argv in (("gc",), ("gc", "--all"), ("clean",),
                     ("clean", "--i-know-this-nukes-everything")):
            with self.subTest(argv=argv):
                self.put(c.cache / "gfx/stale/garbage.bin")
                self.put(c.out / "intermediate/garbage.bin")
                self.assert_ok(self.run_vtx(*argv))
                for p, ino in before.items():
                    self.assertEqual(p.stat().st_ino, ino)
                self.assertFalse((c.cache / "gfx/stale/garbage.bin").exists())
                self.assertFalse((c.out / "intermediate/garbage.bin").exists())
        hygiene.gc(c, do=True)
        hygiene.gc(c, do=True, deep=True)
        for p, ino in before.items():
            self.assertEqual(p.stat().st_ino, ino)

    def test_gc_cannot_unlink_active_flock_and_admit_second_writer(self):
        # issue #30：A 持锁暂停 → CLI 清理 → B 非阻塞入锁；固定后 B 必须被同 inode 拦住。
        c = self.context()
        script = '''import fcntl, sys
from pathlib import Path
p = Path(sys.argv[1])
p.parent.mkdir(parents=True, exist_ok=True)
with p.open("a+b") as f:
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("BLOCKED_SAME_INODE")
        sys.exit(0)
    print("ACQUIRED_NEW_INODE")
    sys.exit(9)
'''
        for argv in (("gc",), ("gc", "--all"), ("clean",),
                     ("clean", "--i-know-this-nukes-everything")):
            with self.subTest(argv=argv):
                c.receipt("seed", True)
                f = c.out / "receipt.json"
                lock = c.out / "receipt.json.lock"
                with fstxn._flock_for(f):
                    ino = lock.stat().st_ino
                    snapshot = json.loads(f.read_text())
                    self.assert_ok(self.run_vtx(*argv))
                    b = subprocess.run([sys.executable, "-c", script, str(lock)],
                                       capture_output=True, text=True, timeout=10)
                    self.assertEqual(b.returncode, 0, b.stdout + b.stderr)
                    self.assertIn("BLOCKED_SAME_INODE", b.stdout)
                    self.assertEqual(lock.stat().st_ino, ino)
                    snapshot["stages"]["sA"] = {"ok": True}
                    fstxn.atomic_write(f, json.dumps(snapshot))
                # A 恢复提交后，B 正常事务追加阶段；两阶段均保留而非后写覆盖。
                b = subprocess.run([sys.executable, "-c",
                    "from vtx.infra import Ctx; from pathlib import Path; "
                    "import sys; Ctx(Path(sys.argv[1])).receipt('sB', True)", str(c.tl_path)],
                    cwd=REPO, env=self.env, capture_output=True, text=True, timeout=10)
                self.assertEqual(b.returncode, 0, b.stdout + b.stderr)
                rec = json.loads(f.read_text())
                self.assertTrue(rec["stages"]["sA"]["ok"])
                self.assertTrue(rec["stages"]["sB"]["ok"])
