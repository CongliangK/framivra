"""Clean/gc/snapshot attacks, interruption, and repeated execution (no media tools)."""
import json
import os
from pathlib import Path
from unittest import mock

try:
    from ._support import A01_FIXED, A03_FIXED, SandboxCase
except ImportError:  # unittest discover -s tests/safety
    from _support import A01_FIXED, A03_FIXED, SandboxCase

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

    def test_gc_dry_flag_preserves_exact_tree(self):
        self.garbage()
        before = self.tree()
        self.assert_ok(self.run_vtx("gc", "--dry"))
        self.assertEqual(self.tree(), before)

    def test_gc_deep_dry_preserves_exact_tree(self):
        self.garbage()
        before = self.tree()
        self.assert_ok(self.run_vtx("gc", "--all", "--dry"))
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
    # 路径已在 vtx/cli.py _cleanup_plan 拒绝符号链接根，转为常跑回归（不再
    # A03 门控）；hygiene.snapshot 直调面的三个用例仍留 A03 门控（修在
    # vtx/hygiene.py，不在 A01 波可改集内）。
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

    @A03_FIXED
    def test_snapshot_dotdot_asset_is_rejected_without_overwrite(self):
        self.graphics("../payload.html")
        self.put(self.sandbox / "payload.html", b"attacker text")
        c = self.context()
        victim = self.put(c.cache / "snapshots/payload.html", b"existing snapshot asset")
        with self.assertRaises((ValueError, OSError)):
            hygiene.snapshot(c)
        self.assertEqual(victim.read_bytes(), b"existing snapshot asset")
        self.assert_outside_unchanged()

    @A03_FIXED
    def test_snapshot_absolute_asset_is_rejected_without_overwrite(self):
        self.graphics(str(self.sentinel))
        c = self.context()
        before = self.tree(self.sandbox)
        with self.assertRaises((ValueError, OSError)):
            hygiene.snapshot(c)
        self.assertEqual(self.tree(self.sandbox), before,
                         "unsafe absolute input must be rejected before partial snapshot writes")

    @A03_FIXED
    def test_snapshot_symlink_asset_outside_project_is_rejected(self):
        self.graphics("gfx/linked.html")
        (self.root / "gfx").mkdir()
        (self.root / "gfx/linked.html").symlink_to(self.sentinel)
        c = self.context()
        with self.assertRaises((ValueError, OSError)):
            hygiene.snapshot(c)
        self.assert_outside_unchanged()
