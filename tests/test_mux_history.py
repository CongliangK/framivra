#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_mux_history.py —— mux 临时路径+原子替换（P0-2 修复）回归测试。

阶段2审查 P0-2（先在 vtx 缺陷，2026-09-12 04:08 事故实锤）：rotate_history 用
os.link（硬链接）把旧 final 轮转进 out/history/——历史副本与 live 件共享 inode；
旧实现让 ffmpeg -y 直接写 final 路径（原地 O_TRUNC 同一 inode）→ 每次渲染把 live
件与全部 history「保留」副本一并改写为最新内容（三名一 inode），历史保留只剩文件
名里的 md5。修复（stages.mux）：ffmpeg 先写 out/.final.tmp.mp4，成功后 os.replace
到 final——replace 只换目录项，旧 inode（含 history 硬链接组）内容原样保留；失败
时 finally 清理半写 tmp；dry-run 语义不变（不落产物）。

运行：env python3 -m unittest discover tests -v（本机 ZCode 环境必须 env python3
前缀：AppImage 劫持 argv[0]，见全局 AGENTS.md）
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "vtx"))

import stages          # noqa: E402
from infra import Ctx  # noqa: E402


def _tl(root: Path) -> Path:
    tl = {"project": {"fps": 30, "duration_frames": 30, "width": 640, "height": 360},
          "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
    p = root / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return p


def _ff(args):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise AssertionError(f"ffmpeg 合成失败: {r.stderr[-800:]}")


@unittest.skipUnless(shutil.which("ffmpeg"), "mux 历史保留测试需要真实 ffmpeg")
class TestMuxPreservesHistory(unittest.TestCase):
    """①旧 final 轮转进 history 后重 mux：live 件换新内容、history 副本保留旧
    inode 旧内容（修复前两者一并被改成最新）；②mux 失败清理半写 tmp；③dry-run
    不落产物不动 final。"""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.td.cleanup)
        cls.root = Path(cls.td.name)
        cls.out = cls.root / "out"
        cls.out.mkdir(parents=True)
        # mux 输入件：30 帧无视频音 + 1s 48k wav（aac 编码用，真编全链 <1s）
        _ff(["-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=1",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             str(cls.out / "video_noaudio.mp4")])
        _ff(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
             "-c:a", "pcm_s16le", str(cls.out / "audio.wav")])

    def _ctx(self):
        c = Ctx(_tl(self.root))
        self.addCleanup(shutil.rmtree, self.out / "history", ignore_errors=True)
        self.addCleanup((self.out / "VERSIONS.json").unlink, missing_ok=True)
        return c

    def test_rotate_history_hardlink_semantics(self):
        # 前置锚点：本文件系统上 rotate_history 确用硬链接（两名一 inode）——这是
        # 「ffmpeg 原地写会击穿 history 保留」的成因，也是修复必要性的前提。
        c = self._ctx()
        c.dry = False
        final = self.out / "final.mp4"
        content_a = b"OLD_FINAL_CONTENT_A" * 64
        final.write_bytes(content_a)
        stages.rotate_history(c, final, keep=2)
        hist = next((self.root / "out" / "history").glob("final_*.mp4"))
        self.assertEqual(final.stat().st_ino, hist.stat().st_ino)
        self.assertEqual(hist.read_bytes(), content_a)

    def test_mux_keeps_history_hardlink_content(self):
        # 真实流：旧 final（A）→ mux（内部 rotate_history 硬链接保留 + ffmpeg 写
        # tmp + os.replace）→ final 换新内容（B）、history 副本保留旧 inode 内容 A。
        # 修复前：ffmpeg -y 直接写 final → A/B 同 inode → history 副本一并变 B。
        c = self._ctx()
        c.dry = False
        final = self.out / "final.mp4"
        content_a = b"OLD_FINAL_CONTENT_A" * 64      # 旧版字节（非 mp4 亦可：轮转只哈希）
        final.write_bytes(content_a)
        stages.mux(c)                                # mux 内部完成 rotate_history
        hist = next((self.root / "out" / "history").glob("final_*.mp4"))
        self.assertEqual(hist.read_bytes(), content_a,
                         "history 副本必须保留旧 inode 内容（修复前被原地写击穿）")
        new = final.read_bytes()
        self.assertNotEqual(new, content_a)
        self.assertTrue(new.startswith(b"\x00\x00\x00"))   # 真实 mp4（ftyp box 头）
        self.assertGreater(len(new), 1000)
        self.assertNotEqual(final.stat().st_ino, hist.stat().st_ino)
        self.assertFalse((self.out / ".final.tmp.mp4").exists(), "成功路径不留 tmp")
        # mux 成功记账（成功全链跑完的旁证）
        self.assertTrue((self.out / "VERSIONS.json").exists())

    def test_mux_failure_cleans_tmp_and_keeps_final(self):
        c = self._ctx()
        c.dry = False
        final = self.out / "final.mp4"
        final.write_bytes(b"OLD" * 64)
        tmp = self.out / ".final.tmp.mp4"
        tmp.write_bytes(b"HALF-WRITTEN")             # 模拟 ffmpeg 失败遗留的半写件
        with mock.patch.object(c, "run", side_effect=SystemExit(1)):
            with self.assertRaises(SystemExit):
                stages.mux(c)
        self.assertFalse(tmp.exists(), "失败路径必须清理半写 tmp")
        self.assertEqual(final.read_bytes(), b"OLD" * 64, "final 未被失败 mux 动过")

    def test_dry_run_does_not_touch_products(self):
        c = self._ctx()
        c.dry = True
        final = self.out / "final.mp4"
        final.write_bytes(b"DRY" * 64)
        stages.mux(c)                                # dry：不轮转/不 replace/不记账
        self.assertEqual(final.read_bytes(), b"DRY" * 64)
        self.assertFalse((self.out / ".final.tmp.mp4").exists())
        self.assertFalse((self.out / "history").exists(),
                         "dry-run 不得轮转/创建真实产物链")


if __name__ == "__main__":
    unittest.main()
