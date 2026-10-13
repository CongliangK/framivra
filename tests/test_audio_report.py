#!/usr/bin/env python3
"""tests/test_audio_report.py —— 音频一等报告（方案五阶段3）单元测试。

覆盖（判级纯函数零渲染免费测 + 合成工程端到端）：
  ① grade_lufs / grade_tp：PASS/FAIL 边界（LUFS 容差沿用 qc.QC_LOUDNESS_TOL；TP 为
    v2 §1.3 权威阈 -1.0——limiter 已并入 vtx audio，> -1 即 FAIL，INFO 档已废除）；
  ② grade_loudness_diff：v4.1 事故态（锚 22.0-4.6=17.4dB 与原始 4.6dB）→ FAIL、
    v4.2 定稿实测锚 22.0dB → PASS、边缘带 WARN、床乐过弱 WARN；
  ③ grade_duck：v4.2 实测值（深度-10.4 / 咬合 0.05 / 回升 2.41）→ PASS、深度不足/
    未回升/超时 → FAIL、显著劣化 → WARN；
  ④ grade_holes：v4.3 片头 3.6s → FAIL、片中 >3s → FAIL、>1.5s → WARN、
    片尾淡出（距片尾 <0.5s）→ WARN、无空洞 → PASS；
  ⑤ merge_thresholds：timeline _meta.audio_report 白名单覆盖；
  ⑥ 测量原语（真实 ffmpeg，合成 wav）：silencedetect 开头空洞、ebur128 series；
  ⑦ CLI 校验：--report/--report-only 作用域/互斥/--dry-run 冲突（子进程真跑 argparse）；
  ⑧ 端到端（合成工程，真实 validate/prepare/audio 管线，VTX_AUDIO_REPORT_INTEG=1 时跑）：
    基线不 FAIL / BGM +4.6dB 判 FAIL / 片头 3.6s 静音判 FAIL。
运行：env python3 -m unittest discover tests -v（本机 ZCode 环境必须 env python3 前缀，
裸 python3 的 sys.executable 被 AppImage 劫持，见全局 AGENTS.md）
"""
import json
import os
from unittest import mock
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))   # repo 根注入（v2 §1.4 包导入，不再注入 repo/vtx）

from vtx import audio_report as ar

VTX = REPO / "vtx" / "vtx.py"
FF = shutil.which("ffmpeg")


class TestGradeLufs(unittest.TestCase):
    def test_pass(self):
        for i in (-14.0, -12.6, -15.4, -12.5, -15.5):   # |d|<=1.5 边界含
            self.assertEqual(ar.grade_lufs(i)[0], "PASS", i)

    def test_warn(self):
        for i in (-12.4, -15.6, -11.6):                 # 1.5<d<=2.5
            self.assertEqual(ar.grade_lufs(i)[0], "WARN", i)

    def test_fail(self):
        for i in (-11.4, -16.6, 0.0):
            self.assertEqual(ar.grade_lufs(i)[0], "FAIL", i)


class TestGradeTp(unittest.TestCase):
    def test_above_minus_one_fails(self):
        # v2 §1.3（2026-09-13）：-1.0 dBFS 为唯一权威阈（与 G3/内核 qc 同口径）——
        # limiter 已并入 vtx audio，> -1 即 FAIL；旧 -1~+3「设计内」INFO 档废除
        self.assertEqual(ar.grade_tp(0.8)[0], "FAIL")    # 旧口径 v4.2 实测 +0.8 曾判 INFO
        self.assertEqual(ar.grade_tp(-0.5)[0], "FAIL")
        self.assertEqual(ar.grade_tp(3.5)[0], "FAIL")

    def test_pass(self):
        self.assertEqual(ar.grade_tp(-2.0)[0], "PASS")
        self.assertEqual(ar.grade_tp(-1.0)[0], "PASS")   # 边界含


class TestGradeLoudnessDiff(unittest.TestCase):
    def test_v42_anchor_pass(self):
        self.assertEqual(ar.grade_loudness_diff(22.0)[0], "PASS")   # v4.2 定稿实测
        self.assertEqual(ar.grade_loudness_diff(21.0)[0], "PASS")   # 下边界含
        self.assertEqual(ar.grade_loudness_diff(29.0)[0], "PASS")   # 上边界含

    def test_v41_incident_fail(self):
        self.assertEqual(ar.grade_loudness_diff(17.4)[0], "FAIL")   # 锚-4.6dB
        self.assertEqual(ar.grade_loudness_diff(4.6)[0], "FAIL")    # v4.1 原始总线差
        self.assertEqual(ar.grade_loudness_diff(18.9)[0], "FAIL")

    def test_warn_bands(self):
        self.assertEqual(ar.grade_loudness_diff(19.0)[0], "WARN")   # 19-21 边缘带
        self.assertEqual(ar.grade_loudness_diff(20.9)[0], "WARN")
        self.assertEqual(ar.grade_loudness_diff(29.1)[0], "WARN")   # 床乐过弱


class TestGradeDuck(unittest.TestCase):
    def test_v42_measured_pass(self):
        lv, _ = ar.grade_duck(-10.4, 0.05, 2.41)   # v4.2 现状实跑值
        self.assertEqual(lv, "PASS")

    def test_depth_fail(self):
        self.assertEqual(ar.grade_duck(-2.0, 0.05, 1.0)[0], "FAIL")   # 避让未生效
        self.assertEqual(ar.grade_duck(None, None, None)[0], "FAIL")

    def test_release_fail(self):
        self.assertEqual(ar.grade_duck(-10.0, 0.05, 4.1)[0], "FAIL")  # 回升超时
        self.assertEqual(ar.grade_duck(-10.0, 0.05, None)[0], "FAIL")  # 未回升

    def test_attack_fail(self):
        self.assertEqual(ar.grade_duck(-10.0, 0.31, 2.0)[0], "FAIL")

    def test_degraded_warn(self):
        self.assertEqual(ar.grade_duck(-10.0, 0.12, 2.41)[0], "WARN")
        self.assertEqual(ar.grade_duck(-10.0, 0.05, 2.6)[0], "WARN")


class TestGradeHoles(unittest.TestCase):
    def test_none_pass(self):
        self.assertEqual(ar.grade_holes([])[0], "PASS")

    def test_v43_opening_fail(self):
        lv, dt = ar.grade_holes([(0.0, 3.6)], total_dur=558.2)   # v4.3 片头 3.6s
        self.assertEqual(lv, "FAIL")
        self.assertIn("FAIL", dt)

    def test_opening_small_fail(self):
        self.assertEqual(ar.grade_holes([(0.0, 1.6)], total_dur=100.0)[0], "FAIL")  # 片头 >1.5 即 FAIL

    def test_mid_warn_and_fail(self):
        self.assertEqual(ar.grade_holes([(10.0, 12.0)], total_dur=100.0)[0], "WARN")
        self.assertEqual(ar.grade_holes([(10.0, 13.1)], total_dur=100.0)[0], "FAIL")

    def test_tail_only_warn(self):
        # 片尾淡出（距片尾 <0.5s）：>3s 也只 WARN（voice-ime v4.2 现状 555.0-558.2）
        self.assertEqual(ar.grade_holes([(555.0, 558.2)], total_dur=558.2)[0], "WARN")


class TestMergeThresholds(unittest.TestCase):
    def test_override(self):
        tl = {"_meta": {"audio_report": {"diff_fail": 15.0, "hole_warn": 2.0,
                                         "not_whitelisted": 1}}}
        ov = ar.merge_thresholds(tl)
        self.assertEqual(ov, {"diff_fail": 15.0, "hole_warn": 2.0})

    def test_no_meta(self):
        self.assertEqual(ar.merge_thresholds({}), {})
        self.assertEqual(ar.merge_thresholds(None), {})


@unittest.skipUnless(FF and shutil.which("ffprobe"), "FFmpeg required")
class TestMeasurePrimitives(unittest.TestCase):
    """测量原语：合成 wav（1s 静音 + 4s 440Hz 正弦 + 1s 静音）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="vtx-arep-")
        cls.wav = Path(cls.tmp.name) / "tone.wav"
        cls.c = _FakeCtx()
        subprocess.run([FF, "-v", "error", "-y",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=1",
                        "-filter_complex",
                        "[0:a][1:a][2:a]concat=n=3:v=0:a=1[aout]",
                        "-map", "[aout]", "-c:a", "pcm_s16le", str(cls.wav)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_silences_detects_head_hole(self):
        sil = ar.silences(self.c, self.wav, "-45dB", 0.5)
        self.assertTrue(any(s < 1.2 and e - s > 0.9 for s, e in sil), sil)

    def test_ebur128_series(self):
        s = ar.ebur128_series(self.c, self.wav)
        self.assertGreater(len(s), 40)                       # 6s @10Hz
        self.assertTrue(all(s[i][0] < s[i + 1][0] for i in range(len(s) - 1)))
        self.assertGreater(max(m for _, m, _ in s), -30.0)   # 正弦段有能量

    def test_activity_windows(self):
        wins, total = ar._activity_windows(self.c, self.wav)
        self.assertAlmostEqual(total, 6.0, delta=0.1)
        self.assertEqual(len(wins), 1)
        self.assertAlmostEqual(wins[0][0], 1.0, delta=0.5)


class _FakeCtx:
    """测量原语用的最小 c.run（不走 Ctx/timeline；只转发 ffmpeg/ffprobe）。"""
    dry = False

    def run(self, cmd, need=True):
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0 and need:
            raise RuntimeError(r.stderr[-500:])
        return r


class TestCliValidation(unittest.TestCase):
    """CLI 校验：真跑 argparse 路径（不需要 timeline——校验在 timeline 加载前）。"""

    def run_vtx(self, *args):
        return subprocess.run([sys.executable, str(VTX), *args],
                              capture_output=True, text=True, cwd=str(REPO))

    def test_report_only_on_audio(self):
        r = self.run_vtx("video", "--report")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--report", r.stdout + r.stderr)

    def test_report_mutually_exclusive(self):
        r = self.run_vtx("audio", "--report", "--report-only")
        self.assertEqual(r.returncode, 2)
        self.assertIn("互斥", r.stdout)

    def test_report_dry_run_conflict(self):
        r = self.run_vtx("audio", "--report", "--dry-run")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--dry-run", r.stdout)


@unittest.skipUnless(FF and shutil.which("ffprobe"), "FFmpeg required")
@unittest.skipUnless(os.environ.get("VTX_AUDIO_REPORT_INTEG") == "1",
                     "set VTX_AUDIO_REPORT_INTEG=1 to run end-to-end")
class TestEndToEndSynthetic(unittest.TestCase):
    """合成工程端到端：voice（断续正弦=口播）+ bed（连续正弦=床乐，duck:true），
    真跑 validate/prepare/audio 管线后 audio --report-only 判级。
    场景：基线（不 FAIL）/ v4.1 态（床乐 +4.6dB → FAIL）/ v4.3 态（片头 3.6s 无声 → FAIL）。"""

    FPS, DUR = 30, 600     # 20s 工程

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="vtx-arep-e2e-")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _tone(self, path, freq, dur, amp=0.4):
        subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency={freq}:sample_rate=48000:duration={dur}",
                        "-af", f"volume={amp}", "-ac", "2", "-c:a", "pcm_s16le",
                        str(path)], check=True)

    def _project(self, tag, bed_db=0.0, head_silence_s=0.0):
        root = Path(self.tmp.name) / tag
        (root / "out").mkdir(parents=True)
        self._tone(root / "voice.wav", 440, 6.0, 0.8)      # 口播电平（相对床乐高 ~16dB）
        self._tone(root / "bed.wav", 220, 20.0 - head_silence_s, 0.12)
        f = self.FPS
        tl = {
            "version": 2,
            "project": {"fps": self.FPS, "width": 1920, "height": 1080,
                        "duration_frames": self.DUR},
            "sources": [{"id": "voice", "path": "voice.wav"},
                        {"id": "bed", "path": "bed.wav"}],
            "video_tracks": [], "subtitles": [],
            "audio_tracks": [
                {"name": "voice", "events": [
                    {"src": "voice", "in": 3 * f, "out": 9 * f, "volume": 1.0},
                    {"src": "voice", "in": 12 * f, "out": 18 * f, "volume": 1.0}]},
                {"name": "bgm", "duck": True,
                 "duck_params": {"threshold": 0.0126, "ratio": 4, "attack_ms": 20,
                                 "release_ms": 1500, "makeup": 1},
                 "events": [{"src": "bed", "in": round(head_silence_s * f),
                             "out": self.DUR,
                             "volume": round(0.12 * 10 ** (bed_db / 20) / 0.12, 4)}]},
            ],
        }
        (root / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
        return root

    def _run(self, root, *args):
        return subprocess.run([sys.executable, str(VTX), "-t", str(root / "timeline.json"),
                               *args], capture_output=True, text=True)

    def _report(self, root):
        self._run(root, "validate")
        self._run(root, "prepare")
        self._run(root, "audio")
        r = self._run(root, "audio", "--report-only")
        self.assertIn(r.returncode, (0, 1), r.stderr)   # FAIL 场景退出码 1；2=产物缺失等错误
        return json.loads((root / "out" / "audio_report.json").read_text(encoding="utf-8"))

    def test_scenarios(self):
        base = self._report(self._project("base"))
        diff_chk = next(c for c in base["checks"] if c["name"] == "人声-床乐响度差")
        self.assertIn(diff_chk["level"], ("PASS", "WARN"))       # 基线床乐正常
        duck_chk = next(c for c in base["checks"] if c["name"] == "duck 咬合/回升")
        # 避让生效即可（纯正弦床乐无音乐包络，attack/release 判定受 ebur128 M
        # 400ms 积分与瞬时起振影响，判级在合成素材上不可锚定——v4.2 判级见 voice-ime 实测）
        self.assertLess(duck_chk["measured"]["depth_db"], -3.0)

        v41 = self._report(self._project("v41", bed_db=4.6))     # v4.1 事故态
        d41 = next(c for c in v41["checks"] if c["name"] == "人声-床乐响度差")
        d41_v = d41["measured"]["voice_s"] - d41["measured"]["bed_duck_s"]
        base_v = diff_chk["measured"]["voice_s"] - diff_chk["measured"]["bed_duck_s"]
        self.assertEqual(d41["level"], "FAIL")
        self.assertLess(d41_v, base_v)                           # +4.6dB 后差值收窄
        self.assertLess(d41_v, base_v - 3.0)                     # 收窄量接近 4.6dB

        v43 = self._report(self._project("v43", head_silence_s=3.6))   # v4.3 片头静音
        holes = next(c for c in v43["checks"] if c["name"] == "静音空洞")
        self.assertEqual(holes["level"], "FAIL")
        self.assertTrue(holes["measured"]["holes"][0][0] < 0.2)        # 空洞从片头开始


class CollectInputsPluginSource(unittest.TestCase):
    """P2 审查 P1-1 回归：_collect_inputs 必须经 resolve_source_wav 解析插件源
    （denoise 裸键按设计永不产出，读裸键 = audio --report 在插件时间线必挂）。"""

    def test_inputs_use_plugin_product_path(self):
        from types import SimpleNamespace
        from vtx import audio_report
        product = Path("/tmp/fake") / "cache/audio/abc::denoise:1.0.0:d41.wav"
        naked = Path("/tmp/fake") / "cache/audio/abc.wav"
        c = SimpleNamespace(cache=Path("/tmp/fake/cache"))
        tracks = [{"name": "voice",
                   "events": [{"src": "v1", "in": 0, "out": 30, "volume": 1.0}]}]
        with mock.patch("vtx.pluginlib.hooks.resolve_source_wav",
                        return_value=product):
            cmd = []
            audio_report._collect_inputs(c, tracks, cmd)
        self.assertIn(str(product), cmd)
        self.assertNotIn(str(naked), cmd)
        # 无插件受理（resolve 返回 None）→ 回落裸键
        with mock.patch("vtx.pluginlib.hooks.resolve_source_wav", return_value=None), \
             mock.patch.object(audio_report, "audio_key", return_value="abc"):
            cmd2 = []
            audio_report._collect_inputs(c, tracks, cmd2)
        self.assertIn(str(naked), cmd2)


if __name__ == "__main__":
    unittest.main()
