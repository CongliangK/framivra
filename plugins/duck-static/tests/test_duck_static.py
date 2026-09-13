#!/usr/bin/env python3
"""tests/test_duck_static.py —— duck-static 插件（缝 B + validate 钩子 v1.1）测试。

覆盖：
  ① 口播窗合并：gap ≤ merge_gap 归并 / 超阈分开 / 重叠事件取 max(out)；
  ② 边界切段：窗口边界切 bed 事件 / src_in 连续（音乐不断跳）/ 段数正确；
  ③ 音量数学：窗外 ×10^(global_db/20)、窗内再 ×10^(duck_db/20)、round 6 位；
  ④ 微淡入出：原边缘淡入出只在首/末段；避让段 fade = max(既有, attack/release)；
  ⑤ 不改性入参：audio_events 返回全新结构，原 tracks/events 逐字段不变；
  ⑥ validate 钩子：duck:true × duck_strategy 互斥 / _meta.bed_static_duck 双施
     （提示 pre-duck 备份）/ 非对象 / mode≠static / 数值类型 / voice_track 缺轨 /
     干净 timeline 空 errors；
  ⑦ 多轨声明：多轨各挂键各变换（参数可不同）；未声明轨原样恒等；
  ⑧ 参数缺省：{"mode":"static"} → CLI 默认全套（-8/-4/voice/20/5/8）；
  ⑨ 真插件总线（经 hooks.run_audio_events/run_validate，registry 真实 active）；
  ⑩ 集成（VTX_TEST_DUCK_INTEG=1 门控，需 ffmpeg）：真实混音 graph 跑通声明轨。
运行：repo 根 `bash scripts/run_all_tests.sh`；本插件内独立跑：
cd plugins/duck-static && env python3 -m unittest discover -s tests -t . -v
（本机 ZCode 环境必须 env python3 前缀：AppImage 劫持 argv[0]，见全局 AGENTS.md）。
"""
import copy
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]   # plugins/duck-static/tests → repo 根
sys.path.insert(0, str(REPO))

_duck = importlib.import_module("plugins.duck-static")   # 连字符目录经 import_module

from vtx.pluginlib import hooks as pl_hooks   # noqa: E402


class _FakeCtx:
    """validate/audio_events 只消费 c.tl——免媒体/免 ffmpeg 的鸭子类型上下文。"""

    def __init__(self, tl):
        self.tl = tl


def _tl(tracks, _meta=None):
    tl = {"version": 2,
          "project": {"fps": 30, "duration_frames": 600, "width": 640, "height": 360},
          "sources": [], "video_tracks": [], "subtitles": [],
          "audio_tracks": tracks}
    if _meta is not None:
        tl["_meta"] = _meta
    return tl


VOICE_EVENTS = [{"in": 0, "out": 90, "volume": 0.8},
                {"in": 100, "out": 200, "volume": 0.8},     # 与上 gap=10 ≤ 20 → 归并
                {"in": 300, "out": 390, "volume": 0.8}]     # gap=100 > 20 → 独立窗


class VoiceWindows(unittest.TestCase):
    def test_merge_within_gap_and_split_beyond(self):
        wins = _duck.voice_windows(VOICE_EVENTS, merge_gap=20)
        self.assertEqual(wins, [[0, 200], [300, 390]])

    def test_overlap_takes_max_out(self):
        evs = [{"in": 0, "out": 100}, {"in": 60, "out": 150}]
        self.assertEqual(_duck.voice_windows(evs, 20), [[0, 150]])


def _bed_track():
    return {"name": "bed", "events": [
        {"src": "bgm", "in": 0, "out": 300, "src_in": 0, "volume": 1.0,
         "fade_in_frames": 10, "fade_out_frames": 10}]}


def _ducked_tl(strategy=None, tracks=None, _meta=None):
    tracks = tracks if tracks is not None else [
        {"name": "voice", "events": VOICE_EVENTS}, _bed_track()]
    if strategy is not None:
        tracks[1]["duck_strategy"] = strategy
    return _tl(tracks, _meta=_meta)


class TransformMath(unittest.TestCase):
    """②③④：切段/音量/淡入出（transform_events 纯函数，v1 数学等价）。"""

    def setUp(self):
        self.wins = [[0, 200], [300, 390]]
        self.ev = {"src": "bgm", "in": 0, "out": 480, "src_in": 0, "volume": 1.0,
                   "fade_in_frames": 10, "fade_out_frames": 10}

    def test_boundary_split_and_volume(self):
        segs = _duck.transform_events([self.ev], self.wins, -8.0, -4.0, 5, 8)
        # 窗 [0,200] 与 [300,390] 切 [0,480) → [0,200) 窗内 + [200,300) 窗外 +
        # [300,390) 窗内 + [390,480) 窗外
        self.assertEqual([(s["in"], s["out"]) for s in segs],
                         [(0, 200), (200, 300), (300, 390), (390, 480)])
        g = 10 ** (-8 / 20)
        d = 10 ** (-4 / 20)
        self.assertEqual(segs[0]["volume"], round(g * d, 6))
        self.assertEqual(segs[1]["volume"], round(g, 6))
        self.assertEqual(segs[2]["volume"], round(g * d, 6))
        self.assertEqual(segs[3]["volume"], round(g, 6))
        # 窗与事件边界相贴不造零长段：上界 200=窗口末=切点，无 (200,200)
        self.assertTrue(all(s["out"] > s["in"] for s in segs))

    def test_src_in_continuous(self):
        segs = _duck.transform_events([self.ev], self.wins, -8, -4, 5, 8)
        starts = [0, 200, 300, 390]
        for seg, st in zip(segs, starts):
            self.assertEqual(seg["src_in"], st)          # 连续推进（音乐不断跳）

    def test_fades_kept_on_edges_microfade_on_duck(self):
        segs = _duck.transform_events([self.ev], self.wins, -8, -4, 5, 8)
        # 原淡入只在首段、原淡出只在末段；中段（非避让）无任何 fade
        self.assertEqual(segs[0]["fade_in_frames"], 10)
        self.assertNotIn("fade_in_frames", segs[1])
        self.assertNotIn("fade_out_frames", segs[1])
        self.assertNotIn("fade_in_frames", segs[3])
        self.assertEqual(segs[3]["fade_out_frames"], 10)
        # 避让段微淡入出 = max(既有, attack/release)；首段同时是避让段
        # （[0,200) 窗内）：淡入取大 10、微淡出 8；窗内中段 5/8
        self.assertEqual(segs[0]["fade_out_frames"], 8)
        self.assertEqual(segs[2]["fade_in_frames"], 5)
        self.assertEqual(segs[2]["fade_out_frames"], 8)

    def test_no_input_mutation(self):
        ev = dict(self.ev)
        before = copy.deepcopy(ev)
        _duck.transform_events([ev], self.wins, -8, -4, 5, 8)
        self.assertEqual(ev, before)                      # 纯函数不改性入参


class ValidateHook(unittest.TestCase):
    def test_duck_and_strategy_mutually_exclusive(self):
        tl = _ducked_tl({"mode": "static"})
        tl["audio_tracks"][1]["duck"] = True
        errs = _duck.validate(_FakeCtx(tl))
        self.assertEqual(len(errs), 1)
        self.assertIn("互斥", errs[0])
        self.assertIn("bed", errs[0])

    def test_meta_marker_blocks_double_application(self):
        tl = _ducked_tl({"mode": "static"},
                        _meta={"bed_static_duck": {"script": "scripts/x.py"}})
        errs = _duck.validate(_FakeCtx(tl))
        self.assertEqual(len(errs), 1)
        self.assertIn("_meta.bed_static_duck", errs[0])
        self.assertIn("pre-duck", errs[0])

    def test_shape_errors(self):
        for bad, expect in (
                ("static", "须为对象"),
                ({"mode": "auto"}, "仅支持"),
                ({"mode": "static", "global_db": "-8"}, "须为数值"),
                ({"mode": "static", "merge_gap": 1.5}, "须为整数"),
                ({"mode": "static", "voice_track": "ghost"}, "不是本 timeline 的音轨名")):
            errs = _duck.validate(_FakeCtx(_ducked_tl(bad)))
            self.assertEqual(len(errs), 1, f"{bad!r} 应恰一条错误")
            self.assertIn(expect, errs[0])

    def test_clean_timeline_no_errors(self):
        self.assertEqual(_duck.validate(_FakeCtx(_ducked_tl({"mode": "static"}))), [])
        self.assertEqual(_duck.validate(_FakeCtx(_ducked_tl())), [])   # 不写键无行为


class AudioEventsHook(unittest.TestCase):
    def test_defaults_and_multi_track(self):
        # 缺省参数 = CLI 默认全套；两床轨各挂键（参数不同）各变换
        tl = _ducked_tl({"mode": "static"})
        bed2 = {"name": "bed2", "duck_strategy": {"mode": "static", "global_db": -12.0},
                "events": [{"src": "bgm", "in": 50, "out": 150, "src_in": 0,
                            "volume": 0.5}]}
        tl["audio_tracks"].append(bed2)
        tracks_before = copy.deepcopy(tl["audio_tracks"])
        out = _duck.audio_events(_FakeCtx(tl), tl["audio_tracks"])
        # voice 轨恒等（原对象原样带回）、bed 轨全新对象
        self.assertIs(out[0], tl["audio_tracks"][0])
        self.assertIsNot(out[1], tl["audio_tracks"][1])
        self.assertEqual(tracks_before[1], tl["audio_tracks"][1])   # 不改性入参
        # bed[0] = [0,200) 窗内：×10^(-8/20)×10^(-4/20)
        g, d = 10 ** (-8 / 20), 10 ** (-4 / 20)
        bed_out = next(t for t in out if t["name"] == "bed")
        self.assertEqual(bed_out["events"][0]["volume"], round(g * d, 6))
        self.assertEqual(bed_out["events"][1]["volume"], round(g, 6))
        # bed2 全段 [50,150) ⊂ 窗 [0,200)：×10^(-12/20)×10^(-4/20)
        bed2_out = next(t for t in out if t["name"] == "bed2")
        self.assertEqual(bed2_out["events"][0]["volume"],
                         round(0.5 * 10 ** (-12 / 20) * d, 6))

    def test_voice_track_windows_from_original_events(self):
        # voice 轨自身声明键也不影响窗口计算（窗口用原始 events）
        tl = _ducked_tl({"mode": "static", "voice_track": "voice"})
        tl["audio_tracks"][0]["duck_strategy"] = {"mode": "static", "global_db": 0.0}
        out = _duck.audio_events(_FakeCtx(tl), tl["audio_tracks"])
        self.assertEqual(len(out[0]["events"]), len(VOICE_EVENTS))   # 窗=[全口播]无切点

    def test_missing_voice_track_no_windows(self):
        # voice_track 轨不存在（validate 会拦；钩子自身不崩，全局增益照施）
        tl = _tl([{"name": "bed", "duck_strategy": {"mode": "static"},
                   "events": [{"src": "b", "in": 0, "out": 100, "volume": 1.0}]}])
        out = _duck.audio_events(_FakeCtx(tl), tl["audio_tracks"])
        self.assertEqual(out[0]["events"][0]["volume"], round(10 ** (-8 / 20), 6))


class RealBus(unittest.TestCase):
    """⑨ 真插件总线：registry 真实 active（installed:true）下经 hooks 入口生效。"""

    def test_run_audio_events_via_hooks(self):
        tl = _ducked_tl({"mode": "static", "duck_db": -6.0})
        c = _FakeCtx(tl)
        out = pl_hooks.run_audio_events(c, tl["audio_tracks"])
        g, d = 10 ** (-8 / 20), 10 ** (-6 / 20)
        self.assertEqual(out[1]["events"][0]["volume"], round(g * d, 6))

    def test_run_validate_via_hooks(self):
        tl = _ducked_tl({"mode": "static"})
        tl["audio_tracks"][1]["duck"] = True
        errs = pl_hooks.run_validate(_FakeCtx(tl))
        self.assertEqual(len(errs), 1)
        self.assertIn("互斥", errs[0])

    def test_run_validate_no_key_timeline_empty(self):
        # 不写 duck_strategy → 插件对本 timeline 不生效（生效过滤）→ 零错误零变换
        tl = _ducked_tl()
        c = _FakeCtx(tl)
        self.assertEqual(pl_hooks.run_validate(c), [])
        self.assertIs(pl_hooks.run_audio_events(c, tl["audio_tracks"]),
                      tl["audio_tracks"])


@unittest.skipUnless(os.environ.get("VTX_TEST_DUCK_INTEG") == "1",
                     "可选集成测试：VTX_TEST_DUCK_INTEG=1 且需 ffmpeg（真实混音链）")
class DuckInteg(unittest.TestCase):
    """⑩ 真实端到端：vtx validate + audio 在声明 duck_strategy 的工程上跑通，
    产物时长精确、非静音（床乐衰减仍可测）——缝 B 在真 filtergraph 的证据。"""

    def test_validate_and_audio_with_strategy(self):
        ff = shutil.which("ffmpeg")
        assert ff
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "a.mp4"
            subprocess.run([ff, "-v", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6",
                            "-f", "lavfi", "-i", "sine=frequency=330:duration=6",
                            "-c:v", "libx264", "-preset", "ultrafast",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                            str(src)], check=True)
            tl = {"version": 2,
                  "project": {"fps": 30, "width": 640, "height": 360,
                              "duration_frames": 180},
                  "sources": [{"id": "a", "path": str(src)}],
                  "video_tracks": [{"name": "base", "kind": "aroll", "clips": [
                      {"src": "a", "src_in": 0, "src_out": 180,
                       "dst_in": 0, "dur": 180}]}],
                  "subtitles": [],
                  "audio_tracks": [
                      {"name": "voice", "events": [
                          {"src": "a", "in": 30, "out": 150, "volume": 0.8}]},
                      {"name": "bed", "duck_strategy": {"mode": "static"},
                       "events": [{"src": "a", "in": 0, "out": 180, "volume": 1.0}]}]}
            p = root / "timeline.json"
            p.write_text(json.dumps(tl), encoding="utf-8")
            vtx = REPO / "vtx" / "vtx.py"
            r = subprocess.run(["env", "python3", str(vtx), "-t", str(p),
                                "validate"], capture_output=True, text=True, cwd=str(REPO))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for stage in ("prepare", "audio"):     # 单命令单阶段（audio 依赖门要 prepare）
                rs = subprocess.run(["env", "python3", str(vtx), "-t", str(p), stage],
                                    capture_output=True, text=True, cwd=str(REPO))
                self.assertEqual(rs.returncode, 0, f"{stage}: {rs.stdout}{rs.stderr}")
            awav = root / "out" / "audio.wav"
            self.assertTrue(awav.is_file())
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                 str(awav)], capture_output=True, text=True)
            st = json.loads(probe.stdout)["streams"][0]
            self.assertEqual(st["sample_rate"], "48000")
            self.assertEqual(st["duration"], "6.000000")


if __name__ == "__main__":
    unittest.main()
