"""A02 黄金测试：清理操作保护机制（issue #21 / F-01 关联）。

覆盖四条黄金路径与保护机制本身：
  1. 正常 gc          —— 键集外垃圾回收；不可再生交付件永不消失；
  2. 深度 gc --all    —— 波1白名单行为保持（reports/snapshots/基线/交付件保留）；
  3. 全清理 clean      —— 默认保留不可再生交付件（hygiene.out_protected 清单）；
  4. 危险旗标变体      —— clean --i-know-this-nukes-everything 才允许全删。
另有：保护清单谓词真值表、预览输出（dry 三段式的预览段）、旗标用法校验、
clean 新路径的符号链接安全（不回退波1 A03 红线）。

Run: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.cache.test_cleanup_protection
全部用例为隔离临时项目，无 Chrome/FFmpeg 依赖。
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from tests._support import cleanup_timeline

REPO = Path(__file__).resolve().parents[2]
VTX = REPO / "vtx" / "vtx.py"

# 保护清单真值表（与 hygiene.OUT_PROTECTED_* 单源对齐的观测面）
PROTECTED_NAMES = ["final.mp4", "VERSIONS.json", "history", "reports",
                   "FREEZE.json", "FREEZE_v47.json", "FREEZE-20261013.json",
                   "final_4k.mp4", "final_4k_x264_ref.mp4"]
UNPROTECTED_NAMES = ["draft.mp4", "draft_final.mp4", "video_noaudio.mp4",
                     "audio.wav", "receipt.json", "receipt.prev.json",
                     "qc_report.json", "audit_report.json", "intermediate.bin",
                     "freeze.json", "VERSION.json", "historyx", "final.mp5",
                     "final_4k.txt", "subs_check.json"]


def present(root: Path) -> set:
    """现存相对路径集合（posix），矩阵断言用。"""
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


class CleanupProtectionFixture(unittest.TestCase):
    """统一项目脚手架：时间线（gfx 轨+音频源）+ 活键缓存 + 陈旧垃圾 + 交付件。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.timeline = cleanup_timeline(self.root)
        # 不可再生交付件（A02 保护清单全集观测：工单四类 + final_4k* + 证据目录）
        self.protected = {
            "out/final.mp4": b"FINAL", "out/VERSIONS.json": b"VERSIONS",
            "out/history/final_20260101-000000_abcd1234.mp4": b"HISTORY",
            "out/FREEZE.json": b"FREEZE", "out/FREEZE_v47.json": b"FREEZE47",
            "out/final_4k.mp4": b"4K", "out/final_4k_x264_ref.mp4": b"4KREF",
            "out/reports/qc_evidence.json": b"EVIDENCE",
        }
        files = dict(self.protected)
        files.update({
            # out 可再生件（clean 删；正常 gc 只删不在 out_keep 的 intermediate）
            "out/intermediate.bin": b"I", "out/draft.mp4": b"D",
            "out/audio.wav": b"A", "out/video_noaudio.mp4": b"V",
            "out/qc_report.json": b"Q", "out/receipt.json": b"RC",
            # cache 陈旧垃圾（正常 gc 各类别）
            "cache/gfx/stale/f0000.png": b"stale-gfx",
            "cache/seg/stale.mp4": b"stale-seg",
            "cache/audio/stale.wav": b"stale-audio",
            "cache/vseg/list.txt": b"stale-vseg",
            "cache/legacy.bin": b"legacy",
            "cache/tmp/orphan/work.bin": b"orphan",
            # cache 证据/基线（gc 永不回收）
            "cache/reports/evidence.json": b"cache-evidence",
            "cache/audio/loudness_receipts.json": b'{"e":{}}',
            "cache/manifest.json": b'{"entries":[]}',
            "cache/qc_pass_segs.json": b'{"v":1}',
            "cache/snapshots/20000101/timeline.json": b"{}",
        })
        for name, content in files.items():
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        os.utime(self.root / "cache/tmp/orphan", (1, 1))
        self.wav_size = (self.root / "assets/voice.wav").stat().st_size
        # 活键缓存（当前 timeline 键集内，正常 gc 须保活）
        from vtx import hygiene
        from vtx.infra import Ctx
        c = Ctx(self.timeline)
        live_gfx = self.root / "cache/gfx" / hygiene.gfx_key(
            c, c.tl["video_tracks"][0]["clips"][0])
        live_gfx.mkdir()
        (live_gfx / "f0000.png").write_bytes(b"live-gfx")
        (self.root / "cache/audio" / (hygiene.audio_key(c, "voice") + ".wav")) \
            .write_bytes(b"live-audio")
        self.live = {"cache/gfx/" + live_gfx.name + "/f0000.png",
                     "cache/audio/" + hygiene.audio_key(c, "voice") + ".wav"}

    def run_vtx(self, *args):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", VTX_NO_NVENC="1",
                   VTX_DOCTOR_OFFLINE="1")
        return subprocess.run(["env", "python3", "-B", str(VTX),
                               "-t", str(self.timeline), *args],
                              cwd=REPO, env=env, capture_output=True, text=True,
                              timeout=60)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_protected_intact(self):
        """黄金判据：全部不可再生交付件原字节存活。"""
        for name, content in self.protected.items():
            p = self.root / name
            self.assertTrue(p.exists(), f"保护件被删: {name}")
            self.assertEqual(p.read_bytes(), content, f"保护件被改: {name}")

    def assert_sources_intact(self):
        self.assertEqual((self.root / "assets/voice.wav").stat().st_size,
                         self.wav_size)
        self.assertEqual((self.root / "gfx/title.html").read_text(), "<canvas></canvas>")
        self.assertTrue(self.timeline.exists())


class ProtectionListTest(unittest.TestCase):
    """保护清单谓词真值表（hygiene.out_protected，A02 唯一权威判据）。"""

    def test_protected_names(self):
        from vtx.hygiene import out_protected
        for name in PROTECTED_NAMES:
            self.assertTrue(out_protected(name), f"应保护: {name}")

    def test_unprotected_names(self):
        from vtx.hygiene import out_protected
        for name in UNPROTECTED_NAMES:
            self.assertFalse(out_protected(name), f"不应保护: {name}")

    def test_pattern_sources_are_the_protection_list(self):
        # 单源校验：生产保护谓词直接使用统一模式清单，不保留第二个模式入口
        from vtx import hygiene
        self.assertEqual(hygiene.out_protected("FREEZE_v47.json"), True)
        self.assertEqual(hygiene.out_protected("freeze.json"), False)
        self.assertEqual(set(hygiene.OUT_PROTECTED_PATTERNS),
                         {"final_4k*.mp4", "FREEZE*"})


class NormalGCGolden(CleanupProtectionFixture):
    """黄金测试 1：正常 gc——键集外垃圾回收，交付件与证据零损伤。"""

    def test_golden(self):
        result = self.run_vtx("gc")
        self.assert_ok(result)
        after = present(self.root)
        # 陈旧垃圾按类别回收
        for gone in ("cache/gfx/stale", "cache/seg/stale.mp4",
                     "cache/audio/stale.wav", "cache/vseg",
                     "cache/legacy.bin", "cache/tmp/orphan",
                     "out/intermediate.bin"):
            self.assertNotIn(gone, after, f"应回收: {gone}")
        # 活键缓存保活（键集判据未被破坏）
        for live in self.live:
            self.assertIn(live, after, f"活键被误回收: {live}")
        # 证据/基线/快照保留
        for keep in ("cache/reports/evidence.json",
                     "cache/audio/loudness_receipts.json",
                     "cache/manifest.json", "cache/qc_pass_segs.json",
                     "cache/snapshots/20000101/timeline.json",
                     "out/draft.mp4", "out/receipt.json"):
            self.assertIn(keep, after, f"应保留: {keep}")
        # 黄金判据 + 幂等（二次 gc 树稳定）
        self.assert_protected_intact()
        stable = present(self.root)
        self.assert_ok(self.run_vtx("gc"))
        self.assertEqual(present(self.root), stable)
        self.assert_sources_intact()


class DeepGCGolden(CleanupProtectionFixture):
    """黄金测试 2：深度 gc --all——波1白名单行为逐项保持（不扩大不缩小）。"""

    def test_golden(self):
        result = self.run_vtx("gc", "--all")
        self.assert_ok(result)
        after = present(self.root)
        # cache 深清：gfx/seg/audio/vseg/legacy 全回收（含活键——深度回收语义）
        for gone in ("cache/gfx", "cache/seg", "cache/audio", "cache/vseg",
                     "cache/legacy.bin"):
            self.assertNotIn(gone, after, f"应深度回收: {gone}")
        # 波1 保留面原样：reports/snapshots/tmp/记账基线
        for keep in ("cache/reports/evidence.json",
                     "cache/snapshots/20000101/timeline.json",
                     "cache/tmp/orphan/work.bin",
                     "cache/manifest.json", "cache/qc_pass_segs.json"):
            self.assertIn(keep, after, f"波1 白名单应保留: {keep}")
        # out：可再生中间物回收；receipt/draft_final 仍按波1 白名单保留
        for gone in ("out/intermediate.bin", "out/draft.mp4", "out/audio.wav",
                     "out/video_noaudio.mp4", "out/qc_report.json"):
            self.assertNotIn(gone, after, f"应深度回收: {gone}")
        self.assertIn("out/receipt.json", after)
        # 黄金判据：交付件在深度回收下同样永不消失
        self.assert_protected_intact()
        self.assert_sources_intact()


class CleanGolden(CleanupProtectionFixture):
    """黄金测试 3：全清理 clean（默认保护变体）——交付件永不消失。"""

    def test_golden(self):
        result = self.run_vtx("clean")
        self.assert_ok(result)
        after = present(self.root)
        # cache 整树清空（含证据/快照/基线——clean 语义是清空可再生工作区）
        self.assertFalse((self.root / "cache").exists())
        # out 只清可再生条目；out 根因保护件存活而保留
        for gone in ("out/intermediate.bin", "out/draft.mp4", "out/audio.wav",
                     "out/video_noaudio.mp4", "out/qc_report.json",
                     "out/receipt.json"):
            self.assertNotIn(gone, after, f"应清理: {gone}")
        # 黄金判据：不可再生交付件在普通 clean 下永不消失（字节原样）
        self.assert_protected_intact()
        self.assertTrue(self.root.joinpath("out/final.mp4").exists())
        self.assert_sources_intact()
        # 幂等：再次 clean 交付件仍在
        self.assert_ok(self.run_vtx("clean"))
        self.assert_protected_intact()

    def test_clean_removes_empty_out_husk(self):
        # 无保护件残留时连空 out/ 壳一起收走（保留旧 clean 的目录级观感）
        shutil.rmtree(self.root / "out")
        (self.root / "out").mkdir()
        (self.root / "out" / "intermediate.bin").write_bytes(b"only-garbage")
        result = self.run_vtx("clean")
        self.assert_ok(result)
        self.assertFalse((self.root / "cache").exists())
        self.assertFalse((self.root / "out").exists())


class CleanNukeGolden(CleanupProtectionFixture):
    """黄金测试 4：危险旗标变体——唯一被允许删除交付件的全清空。"""

    def test_golden(self):
        result = self.run_vtx("clean", "--i-know-this-nukes-everything")
        self.assert_ok(result)
        # 真正的全清空：cache/out 两树连根删除
        self.assertFalse((self.root / "cache").exists())
        self.assertFalse((self.root / "out").exists())
        # 源资产与时间线不受影响（clean 的作用域红线）
        self.assert_sources_intact()


class CleanPreviewTest(CleanupProtectionFixture):
    """A02 预览段：clean 的 dry 输出必须说清保护件的去留与全删入口。"""

    def test_plain_clean_dry_previews_kept_deliverables(self):
        before = present(self.root)
        result = self.run_vtx("clean", "--dry")
        self.assert_ok(result)
        self.assertIn("保留不可再生交付件", result.stdout)
        self.assertIn("--i-know-this-nukes-everything", result.stdout)
        for name in ("out/final.mp4", "out/VERSIONS.json", "out/history",
                     "out/FREEZE.json"):
            self.assertIn(name, result.stdout)
        self.assertEqual(present(self.root), before)

    def test_nuke_clean_dry_previews_destruction_warning(self):
        before = present(self.root)
        result = self.run_vtx("clean", "--i-know-this-nukes-everything", "--dry")
        self.assert_ok(result)
        self.assertIn("危险操作", result.stdout)
        self.assertIn("将被一并删除", result.stdout)
        self.assertIn("out/final.mp4", result.stdout)
        self.assertEqual(present(self.root), before)

    def test_deep_gc_dry_mentions_protection(self):
        result = self.run_vtx("gc", "--all", "--dry")
        self.assert_ok(result)
        self.assertIn("不可再生交付件", result.stdout)


class NukeFlagValidationTest(CleanupProtectionFixture):
    """危险旗标用法校验：clean 专属，其他命令一律退出码 2 且零副作用。"""

    def test_flag_rejected_outside_clean(self):
        for argv in (("gc",), ("gc", "--all"), ("df",), ("snapshot",),
                     ("all",), ("validate",)):
            with self.subTest(argv=argv):
                before = present(self.root)
                result = self.run_vtx(*argv, "--i-know-this-nukes-everything")
                self.assertEqual(result.returncode, 2,
                                 result.stdout + result.stderr)
                self.assertIn("仅作用于 clean", result.stdout)
                self.assertEqual(present(self.root), before)

    def test_flag_spelling_aliases_are_read_only_in_dry(self):
        for flags in (("--dry-run",), ("--dry",), ("--dry-run", "--dry")):
            with self.subTest(flags=flags):
                before = present(self.root)
                result = self.run_vtx("clean", "--i-know-this-nukes-everything",
                                      *flags)
                self.assert_ok(result)
                self.assertEqual(present(self.root), before)


class CleanSymlinkSafetyTest(CleanupProtectionFixture):
    """clean 新路径的符号链接安全（A03 波1红线不回退）。"""

    def test_clean_refuses_symlinked_out_root(self):
        store = self.root / "linked_store"
        store.mkdir()
        (store / "keep.txt").write_bytes(b"precious")
        shutil.rmtree(self.root / "out")
        (self.root / "out").symlink_to(store)
        result = self.run_vtx("clean")
        self.assert_ok(result)
        self.assertIn("拒绝清理符号链接根", result.stdout)
        self.assertEqual((store / "keep.txt").read_bytes(), b"precious")
        self.assertTrue((self.root / "out").is_symlink())
        # cache 侧不受 out 链接影响，照常清空
        self.assertFalse((self.root / "cache").exists())

    def test_clean_refuses_nonprotected_out_entry_symlink_outside_roots(self):
        # issue #21：解析后在 cache/out 根之外的链接不得进入删除计划。
        store = self.root / "external"
        store.mkdir()
        (store / "data.bin").write_bytes(b"external-data")
        (self.root / "out" / "intermediate.bin").unlink()
        (self.root / "out" / "intermediate.bin").symlink_to(store)
        result = self.run_vtx("clean")
        self.assert_ok(result)
        self.assertIn("cache/out 根之外", result.stdout)
        self.assertTrue((self.root / "out" / "intermediate.bin").is_symlink())
        self.assertEqual((store / "data.bin").read_bytes(), b"external-data")
        self.assert_protected_intact()

    def test_clean_keeps_protected_out_entry_symlink_without_following(self):
        # 保护名单内的符号链接条目：按名保护、不跟随（目标自然不动）
        store = self.root / "external"
        store.mkdir()
        (store / "final.mp4").write_bytes(b"external-final")
        (self.root / "out" / "final.mp4").unlink()
        (self.root / "out" / "final.mp4").symlink_to(store)
        result = self.run_vtx("clean")
        self.assert_ok(result)
        self.assertTrue((self.root / "out" / "final.mp4").is_symlink())
        self.assertEqual((store / "final.mp4").read_bytes(), b"external-final")


if __name__ == "__main__":
    unittest.main()
