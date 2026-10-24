#!/usr/bin/env python3
"""tests/media/test_range_render.py —— 区间渲染（方案五阶段1）单元测试。

覆盖（纯函数部分零渲染免费测）：
  ① parse_timespec：秒 / 小数秒 / mm:ss / hh:mm:ss(.fff) / 帧号 f 后缀 / 非法输入；
  ② range_label：秒 1 位小数 + 文件名安全字符；
  ③ 窗口解析：--clip 按 src 名 / --track 外包络 / 找不到返回 None；
  ④ select_segments：与全片切点集求交（含中段、跨段边界、窗口越界钳制）；
  ⑤ CLI 校验：--range 无 --draft 拒绝、三标志互斥、空区间拒绝（子进程真跑 argparse 路径）；
  ⑥ 区间渲染不写 out/ 与 receipt（以 mock 合成 plan 层面验证 run_range 的副作用边界
     ——真实渲染走可选集成测试，VTX_RANGE_INTEG=1 时对小型合成 timeline 实跑）。
运行：env python3 -m unittest discover tests -v（本机 ZCode 环境必须 env python3 前缀，
裸 python3 的 sys.executable 被 AppImage 劫持，见全局 AGENTS.md）
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))   # repo 根注入（v2 §1.4 包导入，不再注入 repo/vtx）

from vtx.pipeline.range import (parse_timespec, range_label, window_from_clip,
                        window_from_track, select_segments)


FPS = 30
TL = {
    "video_tracks": [
        {"name": "base", "kind": "aroll", "clips": [
            {"src": "a.mp4", "src_in": 0, "src_out": 180, "dst_in": 0, "dur": 180},
            {"src": "b.mp4", "src_in": 0, "src_out": 180, "dst_in": 180, "dur": 180}]},
        {"name": "pip", "kind": "pip", "clips": [
            {"src": "cam.mp4", "src_in": 10, "src_out": 100, "dst_in": 60, "dur": 90}]},
        {"name": "gfx-full", "kind": "gfx", "clips": [
            {"src": "gfx/page.html", "dst_in": 30, "dur": 60}]},
    ],
}


class TestParseTimespec(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(parse_timespec("12", FPS), 360)
        self.assertEqual(parse_timespec("12.5", FPS), 375)   # 12.5*30=375 整
        self.assertEqual(parse_timespec(" 0 ", FPS), 0)

    def test_timecode(self):
        self.assertEqual(parse_timespec("0:30", FPS), 900)
        self.assertEqual(parse_timespec("02:05", FPS), 3750)
        self.assertEqual(parse_timespec("1:01:01.5", FPS), round(3661.5 * FPS))

    def test_frame_suffix(self):
        self.assertEqual(parse_timespec("300f", FPS), 300)
        self.assertEqual(parse_timespec("300F", FPS), 300)

    def test_invalid(self):
        for bad in ("abc", "12:60x", "", "1.2.3", "-5"):
            with self.assertRaises(ValueError):
                parse_timespec(bad, FPS)


class TestRangeLabel(unittest.TestCase):
    def test_label(self):
        self.assertEqual(range_label(0, FPS), "0.000")
        self.assertEqual(range_label(180, FPS), "6.000")
        self.assertEqual(range_label(185, FPS), "6.167")   # round 秒 3 位（P6：1 位小数相邻窗碰撞）
        # 相邻窗不碰撞（P6 修复 B-P3-5c：[3,6) 与 [4,5) 曾同标签互相覆盖）
        self.assertNotEqual(range_label(90, FPS), range_label(120, FPS))
        # 文件名安全：即使将来换标签来源也不会漏出危险字符
        self.assertRegex(range_label(300, FPS), r"^[0-9A-Za-z._-]+$")


class TestWindows(unittest.TestCase):
    def test_clip_window(self):
        self.assertEqual(window_from_clip(TL, "b"), (180, 360))
        self.assertEqual(window_from_clip(TL, "page"), (30, 90))   # gfx html stem
        self.assertIsNone(window_from_clip(TL, "nope"))

    def test_track_window(self):
        self.assertEqual(window_from_track(TL, "pip"), (60, 150))
        self.assertEqual(window_from_track(TL, "base"), (0, 360))
        self.assertEqual(window_from_track(TL, "gfx"), (30, 90))   # kind 兜底匹配
        self.assertIsNone(window_from_track(TL, "ghost"))


class TestSelectSegments(unittest.TestCase):
    EDGES = [0, 2093, 4186, 6279, 8373]   # 全片切点集示意（与整片渲染同源）

    def test_middle_window(self):
        self.assertEqual(select_segments(self.EDGES, 100, 300), [(100, 300)])
        # 跨切点的窗口按段拆两块渲染（各段 composite 子区间），总帧数不变
        self.assertEqual(select_segments(self.EDGES, 2000, 2500),
                         [(2000, 2093), (2093, 2500)])

    def test_across_boundary(self):
        self.assertEqual(select_segments(self.EDGES, 2000, 2200), [(2000, 2093), (2093, 2200)])

    def test_full_span_and_clamp(self):
        self.assertEqual(select_segments(self.EDGES, 0, 8373),
                         [(0, 2093), (2093, 4186), (4186, 6279), (6279, 8373)])
        # 窗口越界由调用方钳制，select 不再扩（保持纯函数无副作用）
        self.assertEqual(select_segments(self.EDGES, -5, 99), [(0, 99)])
        self.assertEqual(select_segments(self.EDGES, 9000, 9999), [])

    def test_exact_segment_frame(self):
        self.assertEqual(select_segments(self.EDGES, 2093, 4186), [(2093, 4186)])


class TestCliGuards(unittest.TestCase):
    """CLI 校验路径（argparse+main 前置检查，不触渲染）：失败必须 exit≠0 且不改产物。"""

    def _run(self, *extra):
        return subprocess.run(
            ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
             "-t", str(REPO / "tests" / "fixtures_nonexist.json"), "video", *extra],
            capture_output=True, text=True, cwd=str(REPO))

    def test_range_requires_draft_video(self):
        r = self._run("--range", "0", "6")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("video --draft", r.stdout)

    def test_flags_mutually_exclusive(self):
        r = self._run("--draft", "--range", "0", "6", "--clip", "a")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("互斥", r.stdout)
        r = self._run("--draft", "--clip", "a", "--track", "base")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("互斥", r.stdout)

    def test_range_vs_frames_gpu_exclusive(self):
        # P2-b（复审）：区间预览恒为 draft 快速路径，与 --frames/--gpu 无组合语义
        for extra in (("--frames", "90"), ("--gpu",)):
            r = self._run("--draft", "--range", "0", "6", *extra)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("互斥", r.stdout)

    def test_empty_track_friendly(self):
        # P2-a（复审）：空轨（无 clips）不得在 min() 上裸崩，须给出列出现有轨的文案
        with tempfile.TemporaryDirectory() as d:
            tl = {"version": 2, "project": {"fps": 30, "width": 64, "height": 64,
                                            "duration_frames": 300},
                  "sources": [], "subtitles": [], "audio_tracks": [],
                  "video_tracks": [{"name": "ghost", "kind": "aroll", "clips": []}]}
            p = Path(d) / "timeline.json"
            p.write_text(json.dumps(tl), encoding="utf-8")
            r = subprocess.run(
                ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(p), "video", "--draft", "--track", "ghost"],
                capture_output=True, text=True, cwd=str(REPO))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("空轨", r.stdout)
            self.assertIn("ghost", r.stdout)

    def test_window_from_track_empty_returns_none(self):
        self.assertIsNone(window_from_track(
            {"video_tracks": [{"name": "x", "kind": "aroll", "clips": []}]}, "x"))

    def test_bad_timespec_friendly(self):
        # 校验发生在 timeline 存在性检查之后——用真实存在的最小 timeline 验证友好报错
        with tempfile.TemporaryDirectory() as d:
            tl = {"version": 2, "project": {"fps": 30, "width": 64, "height": 64,
                                            "duration_frames": 300},
                  "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
            p = Path(d) / "timeline.json"
            p.write_text(json.dumps(tl), encoding="utf-8")
            r = subprocess.run(
                ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(p), "video", "--draft", "--range", "abc", "6"],
                capture_output=True, text=True, cwd=str(REPO))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("无法解析时间点", r.stdout)
            self.assertFalse((Path(d) / "cache" / "preview").exists())

    def test_dry_run_no_false_selfcheck_failure(self):
        # P1-A（复审）：--dry-run + --range 组合——dry 不产物流，末尾自检 ffprobe
        # 不得真执行（曾误报「✗ 区间渲染自检失败：总帧数 ? ≠ 90」exit=1）
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "timeline.json"
            p.write_text(json.dumps(self._min_tl()), encoding="utf-8")
            r = subprocess.run(
                ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(p), "video", "--draft", "--range", "0", "6", "--dry-run"],
                capture_output=True, text=True, cwd=str(REPO))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("[dry-run]", r.stdout)
            self.assertNotIn("自检失败", r.stdout)

    def test_cold_gfx_friendly_error(self):
        # P1-B（复审）：gfx 页缓存缺失时须显式指路 `vtx gfx`，而非 ffmpeg 原始报错
        with tempfile.TemporaryDirectory() as d:
            gfxdir = Path(d) / "gfx"; gfxdir.mkdir()
            (gfxdir / "page.html").write_text("<html></html>", encoding="utf-8")
            p = Path(d) / "timeline.json"
            p.write_text(json.dumps(self._min_tl(video_tracks=[
                {"name": "g", "kind": "gfx",
                 "clips": [{"src": "gfx/page.html", "dst_in": 0, "dur": 60}]}])), encoding="utf-8")
            r = subprocess.run(
                ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(p), "video", "--draft", "--range", "0", "6"],
                capture_output=True, text=True, cwd=str(REPO))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("先运行", r.stdout)
            self.assertIn("gfx/page.html", r.stdout)

    @staticmethod
    def _min_tl(video_tracks=()):
        return {"version": 2, "project": {"fps": 30, "width": 64, "height": 64,
                                          "duration_frames": 300},
                "sources": [], "subtitles": [], "audio_tracks": [],
                "video_tracks": list(video_tracks)}


@unittest.skipUnless(os.environ.get("VTX_RANGE_INTEG") == "1",
                     "可选集成测试：VTX_RANGE_INTEG=1 且需 ffmpeg（真实渲染，含gfx则需 Chrome）")
class TestRangeIntegration(unittest.TestCase):
    """小型合成 timeline 实跑：无 gfx / 2 轨 / 有音频与字幕，验证产物落点、
    含音轨、帧数精确、不写 out/ 与 receipt。"""

    def test_silent_gap_window_renders_exact_audio_without_formal_writes(self):
        import math
        import struct
        import wave
        from vtx.pipeline.infra import Ctx
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'voice.wav'
            with wave.open(str(source), 'wb') as audio:
                audio.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b''.join(struct.pack('<hh', value, value)
                                  for i in range(48000)
                                  for value in [round(3000 * math.sin(2 * math.pi * 440 * i / 48000))]))
            tl = TestCliGuards._min_tl()
            tl['sources'] = [{'id': 'voice', 'path': source.name}]
            tl['audio_tracks'] = [{'name': 'voice', 'events': [
                {'src': 'voice', 'in': 0, 'out': 30, 'volume': 1}]}]
            timeline = root / 'timeline.json'
            timeline.write_text(json.dumps(tl), encoding='utf-8')
            source_before, timeline_before = source.read_bytes(), timeline.read_bytes()
            for has_receipt in (False, True):
                if has_receipt:
                    (root / 'out').mkdir()
                    receipt = {'input_fingerprint': Ctx(timeline).input_fingerprint,
                               'stages': {'audio': {'ok': True, 'gain_db': 3.25}}}
                    (root / 'out' / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
                    (root / 'out' / 'audio.wav').write_bytes(b'keep formal audio')
                formal_before = {p.name: p.read_bytes() for p in (root / 'out').glob('*')}
                for end in (151, 181):
                    with self.subTest(has_receipt=has_receipt, end=end):
                        # Both windows lie beyond the only event and its 60-frame
                        # selection margin; no prepared source WAV is needed.
                        result = subprocess.run(
                            [sys.executable, str(REPO / 'vtx' / 'vtx.py'), '-t', str(timeline),
                             'video', '--draft', '--range', '150f', f'{end}f'],
                            capture_output=True, text=True, cwd=str(REPO))
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        stem = f'range_5.000_{range_label(end, FPS)}'
                        preview = root / 'cache' / 'preview'
                        self.assertTrue((preview / f'{stem}.mp4').is_file())
                        with wave.open(str(preview / f'{stem}.wav'), 'rb') as audio:
                            self.assertEqual((audio.getnchannels(), audio.getsampwidth(),
                                              audio.getframerate(), audio.getnframes()),
                                             (2, 2, 48000, (end - 150) * 1600))
                            self.assertEqual(audio.readframes(audio.getnframes()),
                                             bytes((end - 150) * 1600 * 4))
                        self.assertEqual({p.name: p.read_bytes() for p in (root / 'out').glob('*')},
                                         formal_before)
                        self.assertFalse((root / 'cache' / 'audio').exists())
                        self.assertEqual(source.read_bytes(), source_before)
                        self.assertEqual(timeline.read_bytes(), timeline_before)

    def test_range_render(self):
        from vtx.pipeline.range import run_range  # noqa: F401  （经 CLI 子进程实跑，见下）
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            assets = root / "assets"; assets.mkdir()
            import shutil
            ff = shutil.which("ffmpeg")
            self.assertIsNotNone(ff)
            for name, hz in (("a", 330), ("b", 550)):
                subprocess.run([ff, "-v", "error", "-y",
                                "-f", "lavfi", "-i", f"color=c=gray:s=320x180:r=30:d=10",
                                "-f", "lavfi", "-i", f"sine=frequency={hz}:duration=10",
                                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                                "-c:a", "aac", "-shortest", str(assets / f"{name}.mp4")],
                               check=True)
            tl = {"version": 2,
                  "project": {"fps": 30, "width": 320, "height": 180, "duration_frames": 300},
                  "sources": [{"id": n, "path": str(assets / f"{n}.mp4")} for n in ("a", "b")],
                  "video_tracks": [
                      {"name": "base", "kind": "aroll", "clips": [
                          {"src": "a", "src_in": 0, "src_out": 150, "dst_in": 0, "dur": 150},
                          {"src": "b", "src_in": 0, "src_out": 150, "dst_in": 150, "dur": 150}]},
                      {"name": "pip", "kind": "pip", "clips": [
                          {"src": "b", "src_in": 0, "src_out": 60, "dst_in": 60, "dur": 60,
                           "scale": 0.3, "x": 10, "y": 10}]}],
                  "subtitles": [{"in": 30, "out": 90, "text": "区间渲染字幕"}],
                  "audio_tracks": [
                      {"name": "voice", "events": [
                          {"src": "a", "in": 0, "out": 150, "volume": 0.5},
                          {"src": "b", "in": 150, "out": 300, "volume": 0.5}]},
                      {"name": "bed", "duck": True, "duck_by": ["voice"], "events": [
                          {"src": "b", "in": 0, "out": 300, "loop": True, "volume": 0.1}]}]}
            proj = root / "proj"; proj.mkdir()
            (proj / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
            r = subprocess.run(
                ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(proj / "timeline.json"), "video", "--draft", "--range", "2", "5"],
                capture_output=True, text=True, cwd=str(REPO))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = proj / "cache" / "preview" / "range_2.000_5.000.mp4"
            self.assertTrue(out.exists(), "产物应落在 cache/preview/")
            # 不写 out/ 与 receipt
            self.assertFalse((proj / "out" / "receipt.json").exists())
            self.assertFalse((proj / "out" / "draft.mp4").exists())
            pr = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
                                capture_output=True, text=True)
            streams = json.loads(pr.stdout)["streams"]
            nb = next(s for s in streams if s["codec_type"] == "video")["nb_frames"]
            self.assertEqual(int(nb), 90)   # [60,150)=90 帧
            self.assertTrue(any(s["codec_type"] == "audio" for s in streams), "必须含音轨")


if __name__ == "__main__":
    unittest.main()
