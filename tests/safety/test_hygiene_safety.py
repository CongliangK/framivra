"""Clean/gc/snapshot attacks, interruption, and repeated execution (no media tools)."""
import contextlib
import io
import json
import os
from pathlib import Path
from unittest import mock

try:
    from ._support import A01_FIXED, SandboxCase
except ImportError:  # unittest discover -s tests/safety
    from _support import A01_FIXED, SandboxCase

from vtx import hygiene


class CleanupSafety(SandboxCase):
    def garbage(self):
        self.put(self.root / "cache/seg/dead.mp4", b"garbage")
        self.put(self.root / "out/intermediate.bin", b"intermediate")

    def test_clean_is_scoped_and_idempotent(self):
        self.garbage()
        asset = self.put(self.root / "assets/source.bin", b"source")
        for _ in range(2):
            self.assert_ok(self.run_vtx("clean"))
            self.assertFalse((self.root / "cache").exists())
            self.assertFalse((self.root / "out").exists())
            self.assertEqual(asset.read_bytes(), b"source")
            self.assert_outside_unchanged()

    def test_clean_does_not_follow_root_directory_symlinks(self):
        (self.root / "cache").symlink_to(self.outside, target_is_directory=True)
        (self.root / "out").symlink_to(self.outside, target_is_directory=True)
        self.assert_ok(self.run_vtx("clean"))
        self.assert_outside_unchanged()

    # ---- A02（issue #21 / F-01）：不可再生交付件保护 ----
    # 保护清单权威 = hygiene.out_protected（OUT_PROTECTED_*）：普通 clean/gc 绝不
    # 删除；全删唯一入口 = clean --i-know-this-nukes-everything 显式危险旗标。

    def test_clean_preserves_nonregenerable_deliverables(self):
        self.garbage()
        protected = ["out/final.mp4", "out/VERSIONS.json", "out/history/old.mp4",
                     "out/FREEZE.json", "out/FREEZE_v47.json", "out/final_4k.mp4",
                     "out/final_4k_x264_ref.mp4", "out/reports/a.json"]
        for name in protected:
            self.put(self.root / name, name.encode())
        self.assert_ok(self.run_vtx("clean"))
        self.assertFalse((self.root / "cache").exists())
        self.assertFalse((self.root / "out/intermediate.bin").exists())
        for name in protected:
            self.assertEqual((self.root / name).read_bytes(), name.encode(),
                             f"不可再生交付件被普通 clean 删除: {name}")
        self.assert_outside_unchanged()

    def test_clean_nuke_flag_is_the_only_full_delete_path(self):
        # 危险旗标变体：显式确认后恢复全清空语义（对照组=上一用例的默认保留）
        for name in ("out/final.mp4", "out/VERSIONS.json", "out/history/old.mp4",
                     "out/FREEZE.json"):
            self.put(self.root / name, name.encode())
        self.assert_ok(self.run_vtx("clean", "--i-know-this-nukes-everything"))
        self.assertFalse((self.root / "cache").exists())
        self.assertFalse((self.root / "out").exists())
        self.assert_outside_unchanged()

    def test_nuke_flag_rejected_on_non_clean_commands(self):
        # gc 永不删除交付件，旗标对 gc/其他命令是用法错（退出码 2、零副作用）
        before = self.tree()
        for argv in (("gc", "--i-know-this-nukes-everything"),
                     ("gc", "--all", "--i-know-this-nukes-everything"),
                     ("df", "--i-know-this-nukes-everything")):
            result = self.run_vtx(*argv)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("仅作用于 clean", result.stdout)
        self.assertEqual(self.tree(), before)

    def test_clean_refuses_out_entry_symlink_outside_roots(self):
        # issue #21：解析目标在根外时连链接也不列计划，不能枚举其内容。
        self.garbage()
        (self.root / "out/intermediate.bin").unlink()
        (self.root / "out/intermediate.bin").symlink_to(self.outside,
                                                        target_is_directory=True)
        result = self.run_vtx("clean")
        self.assert_ok(result)
        self.assertIn("cache/out 根之外", result.stdout)
        self.assertTrue((self.root / "out/intermediate.bin").is_symlink())
        self.assert_outside_unchanged()

    def test_gc_dry_flag_preserves_exact_tree(self):
        self.garbage()
        before = self.tree()
        self.assert_ok(self.run_vtx("gc", "--dry-run"))
        self.assertEqual(self.tree(), before)

    def test_gc_deep_dry_preserves_exact_tree(self):
        self.garbage()
        before = self.tree()
        self.assert_ok(self.run_vtx("gc", "--all", "--dry-run"))
        self.assertEqual(self.tree(), before)

    def test_gc_preserves_evidence_and_deliverables_and_is_idempotent(self):
        self.garbage()
        protected = ["cache/reports/evidence/a.txt", "cache/manifest.json",
                     "cache/audio/loudness_receipts.json",
                     "cache/audio/loudness_receipts.json.lock", "cache/snapshots/old/timeline.json",
                     "out/final.mp4", "out/VERSIONS.json", "out/history/old.mp4",
                     "out/receipt.json", "out/reports/a.json", "out/final_4k.mp4", "out/FREEZE.json"]
        for name in protected:
            self.put(self.root / name, name.encode())
        self.assert_ok(self.run_vtx("gc"))
        self.assertFalse((self.root / "cache/seg/dead.mp4").exists())
        self.assertFalse((self.root / "out/intermediate.bin").exists())
        for name in protected:
            self.assertEqual((self.root / name).read_bytes(), name.encode())
        after = self.tree()
        self.assert_ok(self.run_vtx("gc"))
        self.assertEqual(self.tree(), after)
        self.assert_outside_unchanged()

    def test_gc_deep_preserves_protected_history(self):
        self.garbage()
        protected = ["cache/reports/a.json", "cache/snapshots/old/timeline.json",
                     "out/final.mp4", "out/history/old.mp4", "out/VERSIONS.json"]
        for name in protected:
            self.put(self.root / name, name.encode())
        self.assert_ok(self.run_vtx("gc", "--all"))
        self.assertFalse((self.root / "cache/seg").exists())
        for name in protected:
            self.assertEqual((self.root / name).read_bytes(), name.encode())
        self.assert_outside_unchanged()

    def test_gc_keep_days_retains_recent_garbage(self):
        self.garbage()
        old = self.put(self.root / "cache/seg/old.mp4")
        os.utime(old, (1, 1))
        self.assert_ok(self.run_vtx("gc", "--keep-days", "1"))
        self.assertFalse(old.exists())
        self.assertTrue((self.root / "cache/seg/dead.mp4").exists())

    def test_gc_does_not_follow_legacy_symlink(self):
        c = self.context()
        (c.cache / "escaped-dir").symlink_to(self.outside, target_is_directory=True)
        (c.cache / "escaped-file").symlink_to(self.sentinel)
        self.assert_ok(self.run_vtx("gc"))
        self.assert_outside_unchanged()

    def test_gc_bad_manifest_paths_cannot_authorize_deletion(self):
        self.garbage()
        manifest = {"entries": [{"path": "../../outside/keep.txt", "stage": "video"},
                                {"path": str(self.sentinel), "stage": "video"}]}
        self.put(self.root / "cache/manifest.json", json.dumps(manifest).encode())
        self.assert_ok(self.run_vtx("gc"))
        self.assert_outside_unchanged()

    def test_invalid_timeline_interrupts_before_cleanup(self):
        self.garbage()
        self.put(self.timeline, b"{invalid json")
        before = self.tree()
        result = self.run_vtx("clean")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.tree(), before)
        self.assert_outside_unchanged()

    def test_gc_plan_error_interrupts_before_any_deletion(self):
        self.garbage()
        c = self.context()
        before = self.tree()
        with mock.patch.object(hygiene, "_current_keys", side_effect=RuntimeError("injected key error")):
            with self.assertRaisesRegex(RuntimeError, "injected key error"):
                hygiene.gc(c, do=True)
        self.assertEqual(self.tree(), before)

    def test_temporary_run_cleans_up_on_exception_without_hiding_error(self):
        c = self.context()
        with self.assertRaisesRegex(RuntimeError, "injected interruption"):
            with hygiene.tmp_run(c, "safety") as path:
                self.checked(path)
                self.put(path / "partial.bin")
                raise RuntimeError("injected interruption")
        self.assertFalse(path.exists())
        self.assert_outside_unchanged()

    # 红队对抗复审（A01 波）：以下两个用例走 CLI 面（run_vtx gc），统一清理
    # 路径已在 vtx/cli.py _cleanup_plan 拒绝符号链接根，转为常跑回归；A04
    # （issue #23）落地 hygiene.snapshot 的写前校验后，直调面的三个用例同样
    # 转为常跑回归（修在 vtx/hygiene.py，2026-10-13 随 A04 波合入）。
    def test_gc_symlinked_cache_root_cannot_delete_external_files(self):
        (self.root / "cache").symlink_to(self.outside, target_is_directory=True)
        before = self.tree(self.outside)
        self.run_vtx("gc")  # Refusal or safe no-op are both acceptable.
        self.assertEqual(self.tree(self.outside), before)
        self.assert_outside_unchanged()

    def test_gc_deep_symlinked_out_root_cannot_delete_external_files(self):
        (self.root / "out").symlink_to(self.outside, target_is_directory=True)
        before = self.tree(self.outside)
        self.run_vtx("gc", "--all")
        self.assertEqual(self.tree(self.outside), before)
        self.assert_outside_unchanged()

    @A01_FIXED
    def test_gc_global_dry_run_cannot_delete_files(self):
        self.garbage()
        before = self.tree()
        self.assert_ok(self.run_vtx("gc", "--dry-run"))
        self.assertEqual(self.tree(), before)

    @A01_FIXED
    def test_gc_deep_global_dry_run_cannot_delete_files(self):
        self.garbage()
        before = self.tree()
        self.assert_ok(self.run_vtx("gc", "--all", "--dry-run"))
        self.assertEqual(self.tree(), before)

    @A01_FIXED
    def test_clean_global_dry_run_cannot_delete_files(self):
        self.garbage()
        before = self.tree()
        self.assert_ok(self.run_vtx("clean", "--dry-run"))
        self.assertEqual(self.tree(), before)

    @A01_FIXED
    def test_dry_run_does_not_create_missing_cache_or_out(self):
        before = self.tree()
        self.assert_ok(self.run_vtx("gc", "--dry-run"))
        self.assertEqual(self.tree(), before)


class LedgerTxnLockSafety(SandboxCase):
    """issue #31（B07，关联 F-09）：账本事务（fstxn.locked_update）与 A04 写路径
    守卫（issue #23）的协同——预置 <账本>.lock → 根外 的符号链接时记账必须拒绝，
    不得在根外制造锁文件，也不得把账本写到链接目标。"""

    def test_manifest_lock_symlink_escape_is_refused(self):
        # manifest_record 的外层兜底把 ProjectPathError 降级为 ⚠——渲染不中断，
        # 但锁文件绝不在根外创建、账本绝不落盘（守卫在 tmp/写动作之前触发）。
        c = self.context()
        (c.cache / "manifest.json.lock").symlink_to(self.outside / "spawn.lock")
        victim = self.put(self.root / "victim.bin", b"victim")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hygiene.manifest_record(c, victim, "video", "k")
        self.assertIn("记账失败", out.getvalue())
        self.assertIn("事务锁文件", out.getvalue())
        self.assertIn("issue #23", out.getvalue())
        self.assertFalse((self.outside / "spawn.lock").exists(),
                         "锁文件不得在项目外被创建")
        self.assertFalse((c.cache / "manifest.json").exists(),
                         "锁守卫触发时账本不得落盘")
        self.assert_outside_unchanged()

    def test_versions_lock_symlink_escape_is_refused(self):
        # versions_* 无兜底：ProjectPathError（ValueError 子类）直接上抛中断 mux
        c = self.context()
        (c.out / "VERSIONS.json.lock").symlink_to(self.outside / "spawn.lock")
        prod = self.put(c.out / "final.mp4", b"final")
        for name, call in (("versions_record",
                            lambda: hygiene.versions_record(c, prod, "mux")),
                           ("versions_check",
                            lambda: hygiene.versions_check(c, prod))):
            with self.subTest(fn=name):
                with self.assertRaisesRegex(ValueError, "事务锁文件"):
                    call()
                self.assertFalse((self.outside / "spawn.lock").exists())
                self.assertFalse((c.out / "VERSIONS.json").exists())
        self.assert_outside_unchanged()

    def test_manifest_target_symlink_replaced_not_followed(self):
        # 对照组（A04 目标侧免疫，issue #23/#31）：预置 cache/manifest.json →
        # 根外 的符号链接——os.replace 只换目录项，链接目标内容原样；账本在
        # cache 内成为真身（读侧经链接摄入外部内容后落笔为本地新账本，无外泄）。
        c = self.context()
        # 链接目标用合法账本形状（形状不符会让记账显式失败，偏离本用例目标）
        outside_ledger = self.put(self.outside / "stolen.json", b'{"entries": []}')
        (c.cache / "manifest.json").symlink_to(outside_ledger)
        victim = self.put(self.root / "victim.bin", b"victim")
        with contextlib.redirect_stdout(io.StringIO()):
            hygiene.manifest_record(c, victim, "video", "k")
        self.assertFalse((c.cache / "manifest.json").is_symlink(),
                         "replace 后 cache/manifest.json 应为真身而非链接")
        self.assertEqual(outside_ledger.read_bytes(), b'{"entries": []}',
                         "链接目标内容不得被跟随改写")
        entries = json.loads((c.cache / "manifest.json")
                             .read_text(encoding="utf-8"))["entries"]
        self.assertEqual(entries[-1]["stage"], "video")
        self.assert_outside_unchanged()


class SnapshotSafety(SandboxCase):
    def graphics(self, src="gfx/card.html"):
        self.tl["video_tracks"] = [{"name": "gfx", "kind": "gfx", "clips": [{"src": src, "dur": 30}]}]
        self.save_timeline()

    def test_snapshot_copies_text_not_media_and_preserves_originals(self):
        self.graphics()
        self.put(self.root / "gfx/card.html", b"<html>fixture</html>")
        self.put(self.root / "assets/media.mp4", b"not actual media")
        c = self.context()
        before = (self.timeline.read_bytes(), (self.root / "gfx/card.html").read_bytes())
        dst = hygiene.snapshot(c)
        self.checked(dst)
        self.assertEqual((dst / "timeline.json").read_bytes(), before[0])
        self.assertEqual((dst / "gfx/card.html").read_bytes(), before[1])
        self.assertFalse((dst / "assets/media.mp4").exists())
        self.assertEqual(self.timeline.read_bytes(), before[0])
        self.assert_outside_unchanged()

    def test_snapshot_same_input_is_idempotent(self):
        c = self.context()
        with mock.patch.object(hygiene.time, "strftime", return_value="20000101T000000Z"):
            first = hygiene.snapshot(c)
            before = self.tree()
            second = hygiene.snapshot(c)
        self.assertEqual(first, second)
        self.assertEqual(self.tree(), before)

    def test_snapshot_retention_only_removes_old_snapshots(self):
        c = self.context()
        self.put(c.cache / "reports/evidence.txt", b"protected")
        paths = []
        for index in range(3):
            self.tl["_meta"] = {"revision": index}
            self.save_timeline()
            c = self.context()
            with mock.patch.object(hygiene.time, "strftime", return_value=f"20000101T00000{index}Z"):
                paths.append(hygiene.snapshot(c, keep=2))
        self.assertFalse(paths[0].exists())
        self.assertTrue(all(p.exists() for p in paths[1:]))
        self.assertEqual((c.cache / "reports/evidence.txt").read_bytes(), b"protected")
        self.assert_outside_unchanged()

    @A01_FIXED
    def test_snapshot_global_dry_run_cannot_write(self):
        self.context()
        before = self.tree()
        self.assert_ok(self.run_vtx("snapshot", "--dry-run"))
        self.assertEqual(self.tree(), before)

    def test_snapshot_dotdot_asset_is_rejected_without_overwrite(self):
        self.graphics("../payload.html")
        self.put(self.sandbox / "payload.html", b"attacker text")
        c = self.context()
        victim = self.put(c.cache / "snapshots/payload.html", b"existing snapshot asset")
        with self.assertRaises((ValueError, OSError)):
            hygiene.snapshot(c)
        self.assertEqual(victim.read_bytes(), b"existing snapshot asset")
        self.assert_outside_unchanged()

    def test_snapshot_absolute_asset_is_rejected_without_overwrite(self):
        self.graphics(str(self.sentinel))
        c = self.context()
        before = self.tree(self.sandbox)
        with self.assertRaises((ValueError, OSError)):
            hygiene.snapshot(c)
        self.assertEqual(self.tree(self.sandbox), before,
                         "unsafe absolute input must be rejected before partial snapshot writes")

    def test_snapshot_symlink_asset_outside_project_is_rejected(self):
        self.graphics("gfx/linked.html")
        (self.root / "gfx").mkdir()
        (self.root / "gfx/linked.html").symlink_to(self.sentinel)
        c = self.context()
        with self.assertRaises((ValueError, OSError)):
            hygiene.snapshot(c)
        self.assert_outside_unchanged()
