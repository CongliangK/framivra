"""B03 / issue #27（关联 finding F-05，2026-10-13）：cache/、out/ 创建延迟到实际写阶段。

验收（issue #27 原文）：`validate` 的只读部分、`df` 和 dry-run 不产生意外写入
——F-05 判据：命令执行前后项目树出现新目录即违例。本回归对只读调用在
「cache/、out/ 均不存在」与「存在」两种初始态下做前后项目树快照
（find + sha256sum 语义，含空目录/符号链接）全等断言；写命令
（snapshot / subs check / 阶段回执链）仍须正常创建布局。

Run: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.cache.test_layout_laziness
全部用例为隔离临时项目，零 Chrome/FFmpeg 依赖（timeline 零源：validate 全程
不触发 ffprobe；gfx 轨只查 html 存在性）。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(REPO))
from tests._support import tree_sha256
VTX = REPO / "vtx" / "vtx.py"

# 只读调用面：非干燥 df 本身就是只读体检；validate 只读部分以 --dry-run 计划路径
# 代表（非干燥 validate 的唯一落盘 = 回执链，见 test_validate_readonly_part_*）；
# gc/clean 的干燥变体含 --all 深度回收计划路径。
READONLY_ARGV = [
    ("df",),
    ("df", "--dry-run"),
    ("validate", "--dry-run"),
    ("gc", "--dry-run"),
    ("gc", "--all", "--dry-run"),
    ("clean", "--dry-run"),
    ("snapshot", "--dry-run"),
]

REJECTED_ARGV = [(*command, flag)
                 for command in (("validate",), ("gc",), ("gc", "--all"), ("clean",))
                 for flag in ("--dry", "--dry-", "--dry-r", "--dry-ru")]


class LayoutLazinessFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = Path(self.tmp.name) / "proj"
        (self.proj / "gfx").mkdir(parents=True)
        (self.proj / "gfx" / "title.html").write_text(
            "<canvas></canvas>", encoding="utf-8")
        self.timeline = self.proj / "timeline.json"
        # 零源 timeline：validate 不触发 ffprobe（回归不依赖外部二进制）；
        # gfx 轨校验只查 html 存在性——契约校验段（只读部分）零写盘。
        self.timeline.write_text(json.dumps({
            "version": 2,
            "project": {"fps": 30, "width": 1920, "height": 1080,
                        "duration_frames": 30},
            "sources": [],
            "video_tracks": [{"name": "title", "kind": "gfx", "clips": [
                {"src": "gfx/title.html", "dst_in": 0, "dur": 30}]}],
            "audio_tracks": [],
            "subtitles": [{"in": 0, "out": 30, "text": "标题"}],
        }, ensure_ascii=False), encoding="utf-8")

    def run_vtx(self, *args):
        # PYTHONDONTWRITEBYTECODE + -B：__pycache__ 不落盘，项目树快照零噪声
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", VTX_NO_NVENC="1",
                   VTX_DOCTOR_OFFLINE="1")
        return subprocess.run(
            ["env", "python3", "-B", str(VTX), "-t", str(self.timeline), *args],
            cwd=REPO, env=env, capture_output=True, text=True, timeout=60)

    def populate_existing_roots(self):
        """初始态二：cache/out 已存在（陈旧缓存 + 受保护交付件 + 旧回执）。"""
        for name, content in {
            "cache/gfx/stale/f0000.png": b"stale png",
            "cache/manifest.json": b'{"entries":[]}',
            "out/final.mp4": b"current final",
            "out/history/final_old.mp4": b"historical final",
            "out/receipt.json": b'{"stages":{}}',
        }.items():
            path = self.proj / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


class ReadOnlyCommandsKeepTreeIdentical(LayoutLazinessFixture):
    """F-05 判据：只读命令执行前后项目树快照完全一致（无新目录/新文件/字节变化）。"""

    def test_absent_roots_stay_absent(self):
        # 初始态一：cache/、out/ 均不存在——不得因体检/计划长出布局
        for argv, expected_rc in ([(a, 0) for a in READONLY_ARGV]
                                  + [(a, 2) for a in REJECTED_ARGV]):
            with self.subTest(argv=argv):
                before = tree_sha256(self.proj)
                result = self.run_vtx(*argv)
                self.assertEqual(result.returncode, expected_rc,
                                 result.stdout + result.stderr)
                if expected_rc == 2:
                    self.assertIn("unrecognized arguments:", result.stderr)
                self.assertEqual(tree_sha256(self.proj), before, argv)
        self.assertFalse((self.proj / "cache").exists())
        self.assertFalse((self.proj / "out").exists())

    def test_existing_roots_stay_untouched(self):
        # 初始态二：cache/out 已存在——计划/体检不得改动任何条目（含陈旧缓存）
        self.populate_existing_roots()
        for argv, expected_rc in ([(a, 0) for a in READONLY_ARGV]
                                  + [(a, 2) for a in REJECTED_ARGV]):
            with self.subTest(argv=argv):
                before = tree_sha256(self.proj)
                result = self.run_vtx(*argv)
                self.assertEqual(result.returncode, expected_rc,
                                 result.stdout + result.stderr)
                if expected_rc == 2:
                    self.assertIn("unrecognized arguments:", result.stderr)
                self.assertEqual(tree_sha256(self.proj), before, argv)


class ValidateReadOnlyPart(LayoutLazinessFixture):
    """validate 非干燥：只读部分（契约校验段）零布局写入。

    validate 的唯一落盘是文档化的回执链（out/receipt.json 及其 flock 锁），
    属「实际写阶段」——cache/ 从不因 validate 出现（旧行为两个根都建）。
    """

    def test_absent_roots_no_cache_created(self):
        before = tree_sha256(self.proj)
        result = self.run_vtx("validate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = tree_sha256(self.proj)
        self.assertFalse((self.proj / "cache").exists())
        # 新增路径只允许回执链（out/ 目录 + receipt.json + 锁），此外零新增
        created = set(after) - set(before)
        self.assertTrue(created <= {"out", "out/receipt.json",
                                    "out/receipt.json.lock"}, created)

    def test_existing_roots_only_receipt_chain_changes(self):
        self.populate_existing_roots()
        before = tree_sha256(self.proj)
        result = self.run_vtx("validate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = tree_sha256(self.proj)
        # cache/ 侧逐字节不变；out/ 侧差异收敛到回执链
        self.assertEqual({k: v for k, v in before.items() if k.startswith("cache/")},
                         {k: v for k, v in after.items() if k.startswith("cache/")})
        created = set(after) - set(before)
        changed = {k for k in set(before) & set(after) if before[k] != after[k]}
        self.assertTrue(created <= {"out/receipt.json", "out/receipt.json.lock",
                                    "out/receipt.prev.json"}, created)
        self.assertTrue(changed <= {"out/receipt.json"}, changed)
        # 交付件/历史/陈旧缓存不受回执重写波及
        for kept in ("out/final.mp4", "out/history/final_old.mp4",
                     "cache/gfx/stale/f0000.png"):
            self.assertEqual(before[kept], after[kept], kept)


class WriteCommandsStillCreateLayout(LayoutLazinessFixture):
    """延迟创建不削弱写命令：snapshot 建 cache/ 布局、subs check 建 out/ 报告件。"""

    def test_snapshot_and_subs_check_create_layout(self):
        result = self.run_vtx("snapshot")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        snapshots = sorted((self.proj / "cache" / "snapshots").iterdir())
        self.assertTrue(snapshots, "snapshot 须创建 cache/snapshots/<stamp>/")
        self.assertTrue((snapshots[-1] / "timeline.json").is_file())
        self.assertTrue((snapshots[-1] / "gfx" / "title.html").is_file())
        result = self.run_vtx("subs", "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.proj / "out" / "subs_check.json").is_file())


if __name__ == "__main__":
    unittest.main()
