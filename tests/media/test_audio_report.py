#!/usr/bin/env python3
"""tests/media/test_audio_report.py —— 音频一等报告（方案五阶段3）单元测试。

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

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))   # repo 根注入（v2 §1.4 包导入，不再注入 repo/vtx）

from vtx.media import audio_report as ar

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
    # Same cases exercise the pure grader and the real report/JSON/exit path.
    cases = (
        ("default_fail", {}, -0.5, "FAIL", -1),
        ("default_boundary", {}, -1, "PASS", -1),
        ("relaxed_both", {"tp_warn": 0, "tp_fail": 0}, -0.5, "FAIL", -1),
        ("relaxed_boundary", {"tp_warn": 0, "tp_fail": 0}, -1, "PASS", -1),
        ("relaxed_warn", {"tp_warn": 0}, -0.5, "FAIL", -1),
        ("relaxed_fail", {"tp_fail": 0}, -0.5, "FAIL", -1),
        ("stricter_both", {"tp_warn": -2, "tp_fail": -2}, -1.5, "FAIL", -2),
        ("stricter_boundary", {"tp_warn": -2, "tp_fail": -2}, -2, "PASS", -2),
        ("stricter_warn", {"tp_warn": -3, "tp_fail": 0}, -2.5, "FAIL", -3),
        ("stricter_fail", {"tp_warn": 0, "tp_fail": -3}, -3, "PASS", -3),
        ("large_finite", {"tp_warn": 1e308, "tp_fail": 1e308}, -0.5, "FAIL", -1),
    )
    invalid_values = (None, True, False, "0", "-2", "NaN", [], {},
                      float("nan"), float("inf"), float("-inf"), 10 ** 400)

    def test_above_minus_one_fails(self):
        # v2 §1.3（2026-09-13）：-1.0 dBFS 为唯一权威阈（与 G3/内核 qc 同口径）——
        # limiter 已并入 vtx audio，> -1 即 FAIL；旧 -1~+3「设计内」INFO 档废除
        self.assertEqual(ar.grade_tp(0.8)[0], "FAIL")    # 旧口径 v4.2 实测 +0.8 曾判 INFO
        self.assertEqual(ar.grade_tp(-0.5)[0], "FAIL")
        self.assertEqual(ar.grade_tp(3.5)[0], "FAIL")

    def test_pass(self):
        self.assertEqual(ar.grade_tp(-2.0)[0], "PASS")
        self.assertEqual(ar.grade_tp(-1.0)[0], "PASS")   # 边界含

    def test_overrides_cannot_relax_authoritative_ceiling(self):
        for name, overrides, tp, expected, threshold in self.cases:
            with self.subTest(case=name):
                level, detail = ar.grade_tp(tp, warn=overrides.get("tp_warn", ar.TP_WARN_DBTP),
                                             fail=overrides.get("tp_fail", ar.TP_FAIL_DBTP))
                self.assertEqual(level, expected)
                self.assertIn(f"> {threshold:g}" if expected == "FAIL" else f"≤{threshold:g}", detail)

    def test_invalid_thresholds_and_measurements_fail_closed(self):
        for invalid in self.invalid_values:
            with self.subTest(value=repr(invalid)):
                for key in ("warn", "fail"):
                    level, detail = ar.grade_tp(-2, **{key: invalid})
                    self.assertEqual(level, "FAIL")
                    self.assertIn("阈值无效", detail)
                level, detail = ar.grade_tp(invalid)
                self.assertEqual(level, "FAIL")
                self.assertIn("测量无效", detail)

    def _report(self, overrides, tp, *, stderr=None, returncode=0, holes=(), duck_times=None):
        """Actual report with fixture stderr only: no native media or plugin execution."""
        from contextlib import redirect_stdout
        from io import StringIO
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wav = root / "audio.wav"
            wav.write_bytes(b"owned fake WAV; no native media measurement")
            receipt = root / "receipt.json"
            receipt.write_bytes(b"existing receipt")
            timeline = {"audio_tracks": [], "_meta": {"audio_report": overrides}}
            if duck_times is not None:
                timeline["audio_tracks"] = [{"name": "voice", "events": [{}]},
                                            {"name": "bed", "duck": True, "events": [{}]}]
            original = json.dumps(timeline, sort_keys=True)
            calls = []
            min_hole = min(ar.HOLE_MIN_D, overrides.get("hole_warn", ar.HOLE_WARN))

            def fake_run(argv):
                self.assertEqual(argv[0], "ffmpeg")
                self.assertEqual(argv[argv.index("-i") + 1], str(wav))
                af = argv[argv.index("-af") + 1]
                self.assertIn(af, ("ebur128=peak=true:framelog=quiet",
                                   f"silencedetect=noise=-45dB:d={min_hole}"))
                calls.append(af)
                summary = (f"I: -14.0 LUFS\nTrue peak:\n Peak: {tp} dBFS\n"
                           if stderr is None else stderr)
                silence = "".join(f"silence_start: {s}\nsilence_end: {e}\n"
                                  for s, e in holes if e - s >= min_hole)
                return SimpleNamespace(returncode=returncode if af.startswith("ebur128") else 0,
                                       stdout="", stderr=summary if af.startswith("ebur128") else silence)

            c = SimpleNamespace(tl=timeline, root=root, out=root, cache=root / "cache", dur=300,
                                spf=1 / 30, run=fake_run)
            stems = tuple(root / name for name in ("voice.wav", "raw.wav", "duck.wav"))

            def series(_c, path):
                attack, release = duck_times
                times = sorted({1 + i / 8 for i in range(57)} | {1 + attack, 3 + release})
                return [(t, -20 - (10 if path == stems[2] and 1 + attack <= t < 3 + release else 0),
                         -14 if path == stems[0] else -36) for t in times]

            with mock.patch.object(ar._plugin_hooks, "run_audio_events", side_effect=lambda c, tracks: tracks), \
                 mock.patch.object(ar, "render_stems", return_value=stems) as render, \
                 mock.patch.object(ar, "_activity_windows", return_value=([(1, 3)], 10)), \
                 mock.patch.object(ar, "ebur128_series", side_effect=series), redirect_stdout(StringIO()):
                rc = ar.run_report(c)
            if duck_times is None:
                render.assert_not_called()
            else:
                render.assert_called_once_with(c, timeline["audio_tracks"][:1],
                                                timeline["audio_tracks"][1:], c.cache / "report_audio")
            self.assertEqual(len(calls), 2)
            self.assertEqual(json.dumps(timeline, sort_keys=True), original)
            self.assertEqual(wav.read_bytes(), b"owned fake WAV; no native media measurement")
            self.assertEqual(receipt.read_bytes(), b"existing receipt")
            report = json.loads((root / "audio_report.json").read_text(),
                                parse_constant=lambda value: self.fail(f"Non-finite JSON: {value}"))
            self.assertEqual(Path(report["source"]), wav)
            return rc, report

    def test_report_uses_effective_thresholds_and_exit_status(self):
        for name, overrides, tp, expected, threshold in self.cases:
            with self.subTest(case=name):
                rc, report = self._report(overrides, tp)
                check = next(row for row in report["checks"] if row["name"] == "整片真峰 TP")
                self.assertEqual(rc, 1 if expected == "FAIL" else 0)
                self.assertEqual(report["summary"], expected)
                self.assertEqual(check["level"], expected)
                self.assertEqual(check["pass"], expected == "PASS")
                self.assertEqual(check["measured"], {"tp_dbfs": tp})
                self.assertEqual(report["thresholds"]["tp_warn"], threshold)
                self.assertEqual(report["thresholds"]["tp_fail"], threshold)
                self.assertEqual(set(report["thresholds"]), set(ar.OVERRIDABLE))
                self.assertIn(f"> {threshold:g}" if expected == "FAIL" else f"≤{threshold:g}", check["detail"])

    def test_report_rejects_invalid_tp_overrides_without_nonfinite_json(self):
        for invalid in self.invalid_values:
            for key in ("tp_warn", "tp_fail"):
                with self.subTest(key=key, value=repr(invalid)):
                    rc, report = self._report({key: invalid}, -2)
                    check = next(row for row in report["checks"] if row["name"] == "整片真峰 TP")
                    self.assertEqual(rc, 1)
                    self.assertEqual(report["summary"], "FAIL")
                    self.assertEqual(check["level"], "FAIL")
                    self.assertFalse(check["pass"])
                    self.assertIn("阈值无效", check["detail"])
                    self.assertIsNone(report["thresholds"]["tp_warn"])
                    self.assertIsNone(report["thresholds"]["tp_fail"])

    def test_report_requires_finite_wav_measurements_but_not_optional_stems(self):
        cases = [("missing_both", "", ("FAIL", "FAIL")),
                 ("missing_i", "True peak:\n Peak: -2 dBFS", ("FAIL", "PASS")),
                 ("missing_tp", "I: -14 LUFS", ("PASS", "FAIL"))]
        for unit in ("", "garbage", "dB", "dBFSgarbage"):
            cases.append((f"invalid_tp_unit_{unit}", f"I: -14 LUFS\nTrue peak:\n Peak: -2 {unit}",
                          ("PASS", "FAIL")))
        cases.append(("invalid_i_unit", "I: -14 LUFSgarbage\nTrue peak:\n Peak: -2 dBFS",
                      ("FAIL", "PASS")))
        for token in ("NaN", "inf", "-inf", "1e999", "-1.2.3", "-1garbage", "--1",
                      "-1_0", "-１", "9" * 400):
            cases.extend(((f"invalid_i_{token}", f"I: {token} LUFS\nTrue peak:\n Peak: -2 dBFS",
                           ("FAIL", "PASS")),
                          (f"invalid_tp_{token}", f"I: -14 LUFS\nTrue peak:\n Peak: {token} dBFS",
                           ("PASS", "FAIL"))))
        for name, stderr, expected in cases:
            with self.subTest(case=name):
                rc, report = self._report({}, -2, stderr=stderr)
                self.assertEqual(rc, 1)
                self.assertEqual(report["summary"], "FAIL")
                self.assertEqual([row["level"] for row in report["checks"]],
                                 [*expected, "INFO", "INFO", "PASS"])
                for row in report["checks"][:2]:
                    self.assertEqual(row["pass"], row["level"] == "PASS")
        rc, report = self._report({}, -1, stderr="I: -1.4e1 LUFS\nTrue peak:\n Peak: -1e0 dBFS")
        self.assertEqual(rc, 0)
        self.assertEqual(report["summary"], "PASS")
        self.assertEqual([row["level"] for row in report["checks"]],
                         ["PASS", "PASS", "INFO", "INFO", "PASS"])

    def test_report_rejects_failed_measurement_even_with_valid_looking_stderr(self):
        rc, report = self._report({}, -2, returncode=1)
        self.assertEqual(rc, 1)
        self.assertEqual(report["summary"], "FAIL")
        self.assertEqual([row["level"] for row in report["checks"][:2]], ["FAIL", "FAIL"])

    def test_report_requires_existing_wav(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            c = SimpleNamespace(tl={"audio_tracks": []}, out=Path(directory), run=mock.Mock())
            self.assertEqual(ar.run_report(c), 2)
            c.run.assert_not_called()
            self.assertFalse((c.out / "audio_report.json").exists())

    def test_report_duck_overrides_reach_grader_summary_and_json(self):
        cases = (
            ({}, (0.0625, 2), "PASS"),
            ({"duck_attack_pass": 0.03125}, (0.0625, 2), "WARN"),
            ({"duck_attack_pass": 0.015625, "duck_attack_fail": 0.03125}, (0.0625, 2), "FAIL"),
            ({"duck_attack_pass": 0.125}, (0.125, 2), "PASS"),
            ({"duck_attack_fail": 0.5}, (0.5, 2), "WARN"),
            ({"duck_release_pass": 1}, (0.0625, 2), "WARN"),
            ({"duck_release_pass": 1, "duck_release_fail": 1.5}, (0.0625, 2), "FAIL"),
            ({"duck_release_pass": 3}, (0.0625, 3), "PASS"),
            ({"duck_release_fail": 4.5}, (0.0625, 4.5), "WARN"),
            ({}, (0.0625, 2.5), "PASS"),
            ({}, (0.0625, 4), "WARN"),
        )
        for overrides, times, expected in cases:
            with self.subTest(overrides=overrides, times=times):
                rc, report = self._report(overrides, -2, duck_times=times)
                check = next(row for row in report["checks"] if row["name"] == "duck 咬合/回升")
                self.assertEqual(check["level"], expected)
                self.assertEqual(check["pass"], expected == "PASS")
                self.assertEqual(check["measured"], {"depth_db": -10, "windows": 1, "no_release_windows": 0})
                self.assertEqual((rc, report["summary"]), (int(expected == "FAIL"), expected))
                for key, value in overrides.items():
                    self.assertEqual(report["thresholds"][key], value)

    def test_report_hole_override_controls_detection_but_not_opening_fail(self):
        cases = (
            ({}, (2, 3), "PASS"),
            ({}, (2, 3.5), "WARN"),
            ({"hole_warn": 0.5}, (2, 3), "WARN"),
            ({"hole_warn": 0.5}, (2, 2.5), "WARN"),
            ({"hole_warn": 3}, (2, 4), "PASS"),
            ({"hole_warn": 3}, (2, 5), "WARN"),
            ({"hole_warn": 3}, (2, 5.1), "FAIL"),
            ({"hole_warn": 3}, (6, 10), "WARN"),
            ({"hole_warn": 3}, (0, 1.6), "FAIL"),
            ({"hole_warn": 3}, (0, 1.5), "PASS"),
            ({}, (0, 1.5), "WARN"),
        )
        for overrides, hole, expected in cases:
            with self.subTest(overrides=overrides, hole=hole):
                rc, report = self._report(overrides, -2, holes=[hole])
                check = next(row for row in report["checks"] if row["name"] == "静音空洞")
                self.assertEqual((check["level"], report["summary"], rc),
                                 (expected, expected, int(expected == "FAIL")))
                self.assertEqual(check["pass"], expected == "PASS")
                detected = [list(hole)] if hole[1] - hole[0] >= min(1.5, overrides.get("hole_warn", 1.5)) else []
                self.assertEqual(check["measured"]["holes"], detected)
                self.assertEqual(report["thresholds"]["hole_warn"], overrides.get("hole_warn", ar.HOLE_WARN))

    def test_report_rejects_invalid_duck_hole_thresholds_before_measurement(self):
        from types import SimpleNamespace
        keys = ("duck_attack_pass", "duck_attack_fail", "duck_release_pass",
                "duck_release_fail", "hole_warn", "hole_fail")
        cases = [{key: value} for key in keys for value in (*self.invalid_values, -0.1)]
        cases += [{"duck_attack_pass": 0.4}, {"duck_attack_fail": 0.05},
                  {"duck_release_pass": 5}, {"duck_release_fail": 2},
                  {"hole_warn": 4}, {"hole_fail": 1}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "audio.wav").write_bytes(b"existing WAV")
            for overrides in cases:
                with self.subTest(overrides=overrides):
                    c = SimpleNamespace(tl={"audio_tracks": [], "_meta": {"audio_report": overrides}},
                                        out=root, run=mock.Mock(side_effect=AssertionError(
                                            'measurement reached before rejecting invalid threshold')))
                    with self.assertRaisesRegex(ValueError, "audio_report 阈值无效"):
                        ar.run_report(c)
                    c.run.assert_not_called()
                    self.assertFalse((root / "audio_report.json").exists())
                    self.assertEqual((root / "audio.wav").read_bytes(), b"existing WAV")


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


def _event_context(fps=30):
    from types import SimpleNamespace
    return SimpleNamespace(fps=fps, spf=1 / fps, spf_samples=48000 // fps,
                           dur=fps, cache=Path('/unused'))


@unittest.skipUnless(FF and shutil.which("ffprobe"), "FFmpeg required")
class TestMeasurePrimitives(unittest.TestCase):
    """测量原语：合成 wav（1s 静音 + 4s 440Hz 正弦 + 1s 静音）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="vtx-arep-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.wav = Path(cls.tmp.name) / "tone.wav"
        cls.c = _FakeCtx()
        subprocess.run([FF, "-v", "error", "-y",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=1",
                        "-filter_complex",
                        "[0:a][1:a][2:a]concat=n=3:v=0:a=1[aout]",
                        "-map", "[aout]", "-c:a", "pcm_s16le", str(cls.wav)], check=True)

    def test_silences_detects_head_hole(self):
        sil = ar.silences(self.c, self.wav, "-45dB", 0.5)
        self.assertTrue(any(s < 1.2 and e - s > 0.9 for s, e in sil), sil)

    def test_ebur128_series(self):
        s = ar.ebur128_series(self.c, self.wav)
        self.assertGreater(len(s), 40)                       # 6s @10Hz
        self.assertTrue(all(s[i][0] < s[i + 1][0] for i in range(len(s) - 1)))
        self.assertGreater(max(m for _, m, _ in s), -30.0)   # 正弦段有能量
        self._assert_short_event_report_and_pipeline_samples_match()

    def test_activity_windows(self):
        wins, total = ar._activity_windows(self.c, self.wav)
        self.assertAlmostEqual(total, 6.0, delta=0.1)
        self.assertEqual(len(wins), 1)
        self.assertAlmostEqual(wins[0][0], 1.0, delta=0.5)

    def test_duck_graph_single_and_multiple_keys_remain_audible(self):
        import wave
        # Zero-volume beds isolate preservation of the key streams. This does
        # not equate quantized report stems with the production float duck bus.
        for names in (['voice'], ['voice', 'cue']):
            with self.subTest(keys=names), tempfile.TemporaryDirectory() as directory:
                c = _event_context(); c.root = Path(directory); c.run = self.c.run
                event = {'src': 'a', 'in': 0, 'out': 30, 'src_in': 30, 'volume': 0.25}
                keys = [{'name': name, 'events': [dict(event)]} for name in names]
                beds = [{'name': f'bed{i}', 'duck': True, 'duck_by': [name],
                         'events': [dict(event, volume=0)]} for i, name in enumerate(names)]
                main, (voice, raw, duck) = self._render_duck_fixture(c, keys, beds,
                                                                   lambda c, sid: self.wav)
                with wave.open(str(main)) as a, wave.open(str(voice)) as b:
                    self.assertEqual(a.getnframes(), 48000)
                    self.assertEqual(a.getparams(), b.getparams())
                    samples = a.readframes(a.getnframes())
                    self.assertTrue(any(samples), 'the final mix must retain audible keys')
                    self.assertEqual(samples, b.readframes(b.getnframes()))
                for path in (raw, duck):
                    with wave.open(str(path)) as stream:
                        self.assertEqual(stream.getnframes(), 48000)
                        self.assertFalse(any(stream.readframes(stream.getnframes())))
        self._assert_unity_duck_pcm_matches_source()

    def _render_duck_fixture(self, c, keys, beds, source_of):
        from vtx.pipeline import stages
        c.tl = {'audio_tracks': keys + beds}
        main = c.root / 'main.wav'
        with mock.patch.object(stages._plugin_hooks, 'run_audio_events', side_effect=lambda c, t: t), \
             mock.patch.object(stages._plugin_hooks, 'resolve_source_wav', side_effect=source_of):
            cmd, chains, _, used = stages._audio_graph(c)
            self.assertTrue(used)
            c.run(cmd + ['-filter_complex', ';'.join(chains) + '[out]', '-map', '[out]',
                         '-c:a', 'pcm_s16le', '-ar', '48000', str(main)])
            stems = ar.render_stems(c, keys, beds, c.root / 'stems')
        return main, stems

    def _assert_unity_duck_pcm_matches_source(self):
        from array import array
        import wave
        with tempfile.TemporaryDirectory() as directory:
            c = _event_context(); c.root = Path(directory); c.run = self.c.run
            # Independent even-integer stereo PCM makes gains 0.5/1/1.5 exact,
            # with distinct nonzero channels through the final sample (no fades).
            samples = array('h', (value for i in range(48000)
                                 for value in (2000 + 2 * (i % 251), -4000 - 2 * (i % 257))))

            def pcm(numerator):
                scaled = array('h', (value * numerator // 2 for value in samples))
                if sys.byteorder != 'little':
                    scaled.byteswap()
                return scaled.tobytes()

            source = c.root / 'source.wav'
            with wave.open(str(source), 'wb') as stream:
                stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                stream.writeframes(pcm(2))
            event = {'src': 'a', 'in': 0, 'out': 30}
            keys = [{'name': name, 'events': [dict(event, volume=volume)]}
                    for name, volume in (('voice', 0), ('cue', 0.5))]
            beds = [{'name': 'bed', 'duck': True, 'duck_by': ['voice'],
                     'duck_params': {'threshold': 0.01, 'ratio': 8, 'attack_ms': 5,
                                     'release_ms': 100, 'makeup': 1},
                     'events': [dict(event, volume=1)]}]
            main, (voice, raw, duck) = self._render_duck_fixture(c, keys, beds, lambda c, sid: source)
            # A silent private key gives unity compression; the audible cue is
            # not a key. Compare each full output to source arithmetic, not peers.
            for path, numerator in ((raw, 2), (duck, 2), (voice, 1), (main, 3)):
                with self.subTest(output=path.name), wave.open(str(path)) as stream:
                    self.assertEqual((stream.getnchannels(), stream.getsampwidth(),
                                      stream.getframerate(), stream.getnframes()), (2, 2, 48000, 48000))
                    actual = stream.readframes(48000)
                    self.assertEqual(len(actual), 48000 * 4)
                    self.assertTrue(actual == pcm(numerator),
                                    f'{path.name} must match independent stereo PCM through sample N-1')

    def test_distinct_duck_keys_only_attenuate_their_own_nonzero_bed(self):
        from array import array
        import math
        import wave
        with tempfile.TemporaryDirectory() as directory:
            c = _event_context(); c.dur = 360; c.root = Path(directory); c.run = self.c.run
            frequencies = {'voice': 1500, 'cue': 2100, 'bed': 331, 'amb': 523}
            for name, frequency in frequencies.items():
                subprocess.run([FF, '-v', 'error', '-f', 'lavfi', '-i',
                                f'sine=frequency={frequency}:sample_rate=48000:duration=12',
                                '-ac', '2', '-c:a', 'pcm_s16le', str(c.root / f'{name}.wav')], check=True)
            keys = [{'name': name, 'events': [{'src': name, 'in': start, 'out': start + 60, 'volume': 2}]}
                    for name, start in (('voice', 30), ('cue', 210))]
            beds = [{'name': name, 'duck': True, 'duck_by': [key],
                     'duck_params': {'threshold': 0.01, 'ratio': 8, 'attack_ms': 5, 'release_ms': 100},
                     'events': [{'src': name, 'in': 0, 'out': 360, 'volume': 0.25}]}
                    for name, key in (('bed', 'voice'), ('amb', 'cue'))]
            main, (_voice, raw, duck) = self._render_duck_fixture(c, keys, beds,
                                                                lambda c, sid: c.root / f'{sid}.wav')

            def samples_at(path, start):
                with wave.open(str(path)) as stream:
                    self.assertEqual((stream.getframerate(), stream.getsampwidth(), stream.getnframes()),
                                     (48000, 2, 12 * 48000))
                    self.assertEqual(stream.getnchannels(), 2)
                    stream.setpos(round(start * 48000))
                    pcm = stream.readframes(48000)
                self.assertEqual(len(pcm), 48000 * 4)
                samples = array('h', pcm)
                if sys.byteorder != 'little':
                    samples.byteswap()
                return samples

            def amplitude(path, frequency, start):
                # One-second integer-frequency projection separates both nonzero
                # beds from each other and the audible keys, without numpy/FFT.
                mono = samples_at(path, start)[::2]
                phase = 2 * math.pi * frequency / 48000
                real = sum(value * math.cos(phase * i) for i, value in enumerate(mono))
                imag = sum(value * math.sin(phase * i) for i, value in enumerate(mono))
                return 2 * math.hypot(real, imag) / (48000 * 32768)

            measured = {}
            for path in (main, raw, duck):
                for name, own, other in (('bed', 1.75, 7.75), ('amb', 7.75, 1.75)):
                    frequency = frequencies[name]
                    baseline = amplitude(path, frequency, 5)
                    self.assertGreater(baseline, 0.02, 'both bed tracks must be nonzero')
                    ratios = [amplitude(path, frequency, start) / baseline for start in (own, other)]
                    measured[f'{path.name}:{name}'] = ratios
                    self.assertAlmostEqual(ratios[1], 1, delta=0.05,
                                           msg=f'{path.name}:{name} must not duck for the other key')
                    if path == raw:
                        self.assertAlmostEqual(ratios[0], 1, delta=0.05)
                    else:
                        self.assertLess(ratios[0], 0.5, f'{path.name}:{name} must duck for its own key')
            print('duck key isolation own/other amplitude ratios:', json.dumps(measured, sort_keys=True))
            for name in ('bed', 'amb'):
                self.assertGreater(amplitude(raw, frequencies[name], 11), 0.02,
                                   'both raw beds must remain audible in the terminal second')
            # These keys end at 9s with 100ms release and unity makeup. Compare
            # every stereo tail sample; allow one PCM LSB for cross-path rounding
            # near quantization half-ties, not missing content or output padding.
            reference = samples_at(raw, 11)
            for path in (main, duck):
                with self.subTest(tail_output=path.name):
                    tail = samples_at(path, 11)
                    self.assertLessEqual(max(abs(a - b) for a, b in zip(tail, reference)), 1,
                                         f'{path.name} must preserve both beds through sample N-1')


    @unittest.skipUnless(FF, 'FFmpeg required')
    def _assert_short_event_report_and_pipeline_samples_match(self):
        from unittest.mock import patch
        from vtx.pipeline import stages
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.wav'
            subprocess.run([FF, '-v', 'error', '-f', 'lavfi', '-i',
                            'sine=frequency=440:sample_rate=48000:duration=1',
                            '-ac', '2', str(source)], check=True)
            c = _event_context()
            c.run = _FakeCtx().run
            tracks = [{'name': 'voice', 'events': [{'src': 'a', 'in': 0, 'out': 10,
                       'src_in': 3, 'volume': 0.5, 'fade_in_frames': 20,
                       'fade_out_frames': 20}]}]
            c.tl = {'audio_tracks': tracks}
            with patch.object(stages._plugin_hooks, 'run_audio_events', side_effect=lambda c, t: t), \
                 patch.object(stages._plugin_hooks, 'resolve_source_wav', return_value=source):
                for frames in (30, 31, 32):
                    with self.subTest(duration_frames=frames):
                        c.dur = frames
                        cmd, chains, _, _ = stages._audio_graph(c)
                        main = root / f'main-{frames}.wav'
                        c.run(cmd + ['-filter_complex', ';'.join(chains) + '[out]', '-map', '[out]',
                                     '-c:a', 'pcm_s16le', '-ar', '48000', str(main)])
                        cmd, chains, mix, _, _ = ar._track_group(c, tracks)
                        ar._collect_inputs(c, tracks, cmd)
                        report = root / f'report-{frames}.wav'
                        c.run(cmd + ['-filter_complex', ';'.join(chains + [mix]), '-map', '[gmix]',
                                     '-c:a', 'pcm_s16le', '-ar', '48000', str(report)])
                        import wave
                        with wave.open(str(main)) as a, wave.open(str(report)) as b:
                            if frames == 30:
                                self.assertEqual(a.getnframes(), 48000)
                            self.assertEqual(a.getnframes(), c.dur * c.spf_samples)
                            self.assertEqual(b.getnframes(), c.dur * c.spf_samples)
                            self.assertEqual(a.getparams(), b.getparams())
                            self.assertEqual(a.readframes(a.getnframes()), b.readframes(b.getnframes()))

    @unittest.skipUnless(FF, 'FFmpeg required')
    def test_frame_offsets_are_sample_exact_in_main_window_and_report(self):
        import struct
        import wave
        from vtx.pipeline import stages
        from vtx.pipeline.constraints import ALLOWED_FPS, AUDIO_SAMPLE_RATE
        self.assertEqual((ALLOWED_FPS, AUDIO_SAMPLE_RATE), ((30,), 48000))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.wav'
            # Nonzero, varying stereo PCM exposes a one-sample shift at either
            # boundary, including src_in trims and wrapped loop source samples.
            pcm = b''.join(struct.pack('<hh', 100 + i % 101, -200 - i % 97)
                           for i in range(4800))
            with wave.open(str(source), 'wb') as output:
                output.setparams((2, 2, AUDIO_SAMPLE_RATE, 0, 'NONE', 'not compressed'))
                output.writeframes(pcm)
            for start, loop in ((0, False), (1, False), (2, True), (3, False),
                                (4, False), (7, True), (301, False)):
                length = 4 if loop else 2
                c = _event_context(); c.dur = 330
                event = {'src': 'a', 'in': start, 'out': start + length,
                         'src_in': 1, 'volume': 1, 'loop': loop}
                tracks = [{'name': 'voice', 'events': [event]}]
                c.tl = {'audio_tracks': tracks}
                trimmed = (pcm * 2)[6400:6400 + length * 6400]
                expected = (bytes(start * 6400) + trimmed +
                            bytes((c.dur - start - length) * 6400))
                with mock.patch.object(stages._plugin_hooks, 'run_audio_events',
                                       side_effect=lambda c, t: t), \
                     mock.patch.object(stages._plugin_hooks, 'resolve_source_wav',
                                       return_value=source):
                    for mode in ('main', 'window', 'report'):
                        with self.subTest(start=start, loop=loop, mode=mode):
                            if mode == 'report':
                                cmd, chains, mix, _, _ = ar._track_group(c, tracks)
                                ar._collect_inputs(c, tracks, cmd)
                                graph = ';'.join(chains + [mix.replace('[gmix]', '[out]')])
                            else:
                                win = (start, start + length) if mode == 'window' else None
                                cmd, chains, _, _ = stages._audio_graph(c, win=win)
                                graph = ';'.join(chains) + '[out]'
                            output = root / 'output.wav'
                            subprocess.run(cmd + ['-filter_complex', graph, '-map', '[out]',
                                                  '-c:a', 'pcm_s16le', '-ar', '48000', str(output)],
                                           check=True, capture_output=True)
                            with wave.open(str(output), 'rb') as rendered:
                                actual = rendered.readframes(rendered.getnframes())
                                self.assertEqual((rendered.getnchannels(), rendered.getsampwidth(),
                                                  rendered.getframerate()), (2, 2, 48000))
                            wanted = trimmed if mode == 'window' else expected
                            self.assertEqual(len(actual), len(wanted))
                            self.assertEqual(actual, wanted)


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

    def _tone(self, path, freq, dur, amp=0.4):
        subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency={freq}:sample_rate=48000:duration={dur}",
                        "-af", f"volume={amp}", "-ac", "2", "-c:a", "pcm_s16le",
                        str(path)], check=True)

    def _run(self, root, *args):
        return subprocess.run([sys.executable, str(VTX), "-t", str(root / "timeline.json"),
                               *args], capture_output=True, text=True)

    def _report(self, tag, bed_db=0.0, head_silence_s=0.0):
        with tempfile.TemporaryDirectory(prefix="vtx-arep-e2e-") as directory:
            root = Path(directory) / tag
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
            for stage in ("validate", "prepare", "audio"):
                result = self._run(root, stage)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            r = self._run(root, "audio", "--report-only")
            self.assertIn(r.returncode, (0, 1), r.stderr)   # FAIL 场景退出码 1；2=产物缺失等错误
            return json.loads((root / "out" / "audio_report.json").read_text(encoding="utf-8"))

    def test_scenarios(self):
        base = self._report("base")
        diff_chk = next(c for c in base["checks"] if c["name"] == "人声-床乐响度差")
        self.assertIn(diff_chk["level"], ("PASS", "WARN"))       # 基线床乐正常
        duck_chk = next(c for c in base["checks"] if c["name"] == "duck 咬合/回升")
        # 避让生效即可（纯正弦床乐无音乐包络，attack/release 判定受 ebur128 M
        # 400ms 积分与瞬时起振影响，判级在合成素材上不可锚定——v4.2 判级见 voice-ime 实测）
        self.assertLess(duck_chk["measured"]["depth_db"], -3.0)

        v41 = self._report("v41", bed_db=4.6)     # v4.1 事故态
        d41 = next(c for c in v41["checks"] if c["name"] == "人声-床乐响度差")
        d41_v = d41["measured"]["voice_s"] - d41["measured"]["bed_duck_s"]
        base_v = diff_chk["measured"]["voice_s"] - diff_chk["measured"]["bed_duck_s"]
        self.assertEqual(d41["level"], "FAIL")
        self.assertLess(d41_v, base_v)                           # +4.6dB 后差值收窄
        self.assertLess(d41_v, base_v - 3.0)                     # 收窄量接近 4.6dB

        v43 = self._report("v43", head_silence_s=3.6)   # v4.3 片头静音
        holes = next(c for c in v43["checks"] if c["name"] == "静音空洞")
        self.assertEqual(holes["level"], "FAIL")
        self.assertTrue(holes["measured"]["holes"][0][0] < 0.2)        # 空洞从片头开始


class CollectInputsPluginSource(unittest.TestCase):
    """P2 审查 P1-1 回归：_collect_inputs 必须经 resolve_source_wav 解析插件源
    （denoise 裸键按设计永不产出，读裸键 = audio --report 在插件时间线必挂）。"""

    def test_inputs_use_plugin_product_path(self):
        from types import SimpleNamespace
        from vtx.media import audio_report
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




class TestSharedEventFilters(unittest.TestCase):
    """Render and diagnostic stems must consume the same event-local transform."""

    def _graph_routes(self, argv, available=None):
        """Trace actual generated edges across PCM files; reject dangling/reused outputs."""
        import re
        available = available or {}
        inputs = [argv[i + 1] for i, token in enumerate(argv) if token == '-i']
        labels = {f'[{i}:a]': {'sources': available.get(path, {path}), 'parents': [], 'uses': 0}
                  for i, path in enumerate(inputs)}
        produced, compressors = [], []
        graph = argv[argv.index('-filter_complex') + 1]
        for chain in graph.split(';'):
            head = re.match(r'((?:\[[^\]]+\])+)(.*)', chain)
            self.assertIsNotNone(head, chain)
            parents = [labels.setdefault(label, {'parents': None, 'uses': 0})
                       for label in re.findall(r'\[[^\]]+\]', head[1])]
            for node in parents:
                node['uses'] += 1
            tail = re.search(r'((?:\[[^\]]+\])+)$', head[2])
            self.assertIsNotNone(tail, chain)
            if head[2].startswith('sidechaincompress='):
                self.assertEqual(len(parents), 2)
                compressors.append(parents)
            for label in re.findall(r'\[[^\]]+\]', tail[1]):
                node = labels.get(label)
                if node is None or node['parents'] is not None:
                    node = {'uses': 0}
                node['parents'] = parents
                labels[label] = node
                produced.append(node)

        def sources(node, seen=()):
            self.assertNotIn(id(node), seen, 'cycle in generated audio labels')
            self.assertIsNotNone(node['parents'], 'unresolved audio label')
            return set(node.get('sources', ())).union(
                *(sources(parent, (*seen, id(node))) for parent in node['parents']))

        outputs = {}
        maps = [i for i, token in enumerate(argv) if token == '-map']
        for i, stop in zip(maps, maps[1:] + [len(argv)]):
            node = labels[argv[i + 1]]
            node['uses'] += 1
            outputs[argv[stop - 1]] = sources(node)
        for node in produced:
            self.assertEqual(node['uses'], 1, 'each graph output must have exactly one consumer')
        return outputs, [(sources(bed), sources(key)) for bed, key in compressors]

    def test_distinct_duck_keys_reach_only_their_own_compressors(self):
        import copy
        from contextlib import redirect_stdout
        from io import StringIO
        from vtx.pipeline import stages
        cases = (
            ('disjoint', ['voice'], ['cue'], None, {'bed': {'voice'}, 'amb': {'cue'}}),
            ('same_group', ['voice', 'cue'], ['cue', 'voice'], None,
             {'bed': {'voice', 'cue'}, 'amb': {'voice', 'cue'}}),
            ('overlap', ['voice', 'cue'], ['cue'], None,
             {'bed': {'voice', 'cue'}, 'amb': {'cue'}}),
            ('missing', ['voice'], ['missing'], None, {'bed': {'voice'}}),
            ('default', None, ['cue'], None, {'bed': {'voice'}, 'amb': {'cue'}}),
            ('window', ['voice'], ['cue'], (0, 90), {'bed': {'voice'}, 'amb': {'cue'}}),
        )
        for name, bed_keys, amb_keys, win, expected in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                c = _event_context(); c.dur = 360; c.root = c.out = Path(directory); c.cache = c.root / 'cache'
                tracks = [{'name': name, 'events': [{'src': name, 'in': start, 'out': stop, 'volume': volume}]}
                          for name, start, stop, volume in
                          [('voice', 30, 90, 1), ('cue', 210, 270, 1), ('bed', 0, 360, 0.25), ('amb', 0, 360, 0.25)]]
                for tr, keys in zip(tracks[2:], (bed_keys, amb_keys)):
                    tr['duck'] = True
                    if keys is not None:
                        tr['duck_by'] = keys
                c.tl = {'audio_tracks': tracks}; before = copy.deepcopy(c.tl)
                (c.out / 'audio.wav').write_bytes(b'existing WAV')
                c.run = mock.Mock()
                with mock.patch.object(stages._plugin_hooks, 'run_audio_events', side_effect=lambda c, t: t) as hook, \
                     mock.patch.object(stages._plugin_hooks, 'resolve_source_wav', side_effect=lambda c, sid: c.root / f'{sid}.wav'), \
                     mock.patch.object(ar, 'ebur128_summary', return_value=(-14, -2)), \
                     mock.patch.object(ar, '_activity_windows', return_value=([], 12)), \
                     mock.patch.object(ar, 'ebur128_series', return_value=[]), \
                     mock.patch.object(ar, 'silences', return_value=[]), redirect_stdout(StringIO()):
                    cmd, chains, _, used = stages._audio_graph(c, win=win, margin_frames=0)
                    self.assertTrue(used)
                    main, main_edges = self._graph_routes(cmd + ['-filter_complex', ';'.join(chains) + '[main]',
                                                                '-map', '[main]', str(c.root / 'main.wav')])
                    self.assertEqual(ar.run_report(c), 0)
                hook.assert_has_calls([mock.call(c, tracks), mock.call(c, tracks)])
                self.assertEqual(hook.call_count, 2)
                self.assertEqual(c.run.call_count, 2)
                rendered, stem_edges = {}, []
                for call in c.run.call_args_list:
                    outputs, edges = self._graph_routes(call.args[0], rendered)
                    rendered.update(outputs); stem_edges.extend(edges)

                def routes(edges):
                    self.assertTrue(all(len(bed) == 1 for bed, _ in edges))
                    return {Path(next(iter(bed))).stem: {Path(path).stem for path in key} for bed, key in edges}

                main_expected = {'bed': {'voice'}} if win else expected
                self.assertEqual(len(main_edges), len(main_expected))
                self.assertEqual(len(stem_edges), len(expected))
                self.assertEqual(routes(main_edges), main_expected)
                self.assertEqual(routes(stem_edges), expected)
                stem_cmd = c.run.call_args.args[0]
                key_files = [stem_cmd[i + 1] for i, token in enumerate(stem_cmd)
                             if token == '-i' and stem_cmd[i + 1] in rendered]
                self.assertEqual(len(key_files), len({frozenset(keys) for keys in expected.values()}),
                                 'identical target key sets must share one PCM key file')
                audible = {Path(path).stem for path in next(iter(main.values()))}
                self.assertEqual(audible, {'voice', 'bed', 'amb'} if win else {'voice', 'cue', 'bed', 'amb'})
                self.assertEqual(c.tl, before)

    def test_canonical_primitive_and_exact_event_boundaries(self):
        from vtx.pipeline import stages
        from vtx.media.audio_filters import event_chain
        self.assertIs(ar.event_chain, event_chain)
        self.assertIs(stages.event_chain, event_chain)
        for fps in (24, 30):
            c = _event_context(fps)
            e = {'src': 'a', 'in': 5, 'out': 17, 'src_in': 3, 'volume': 0.25,
                 'pan': -1, 'fade_in_frames': 20, 'fade_out_frames': 30}
            chain = event_chain(c, 2, e)
            self.assertIn(f'atrim=start_sample={3*c.spf_samples}:end_sample={15*c.spf_samples}', chain)
            self.assertIn('volume=0.25,pan=stereo|c0=1.500*c0|c1=0.500*c1', chain)
            self.assertIn(f'afade=t=in:st=0:d={12/fps:.6f}', chain)
            self.assertIn(f'afade=t=out:st=0.000000:d={12/fps:.6f}', chain)
            self.assertTrue(chain.endswith(f'adelay=delays={5*c.spf_samples}S:all=1[e2]'))
            for bus_filter in ('amix=', 'sidechaincompress=', 'alimiter=', 'apad=', 'atempo='):
                self.assertNotIn(bus_filter, chain)
            # Audio events have no speed field in the timeline contract.
            self.assertEqual(e['fade_out_frames'], 30)

    def test_main_and_report_graphs_share_fades_and_window_offsets(self):
        from unittest.mock import patch
        from vtx.pipeline import stages
        e = {'src': 'a', 'in': 5, 'out': 15, 'volume': 1,
             'fade_out_frames': 20}
        tracks = [{'name': 'voice', 'events': [e]}]
        c = _event_context(); c.tl = {'audio_tracks': tracks}
        with patch.object(stages._plugin_hooks, 'run_audio_events', side_effect=lambda c, t: t), \
             patch.object(stages._plugin_hooks, 'resolve_source_wav', return_value='/unused/a.wav'):
            for events in ([e], [e, dict(e, **{'in': 20, 'out': 30})]):
                tracks[0]['events'] = events
                for win in (None, (7, 12)):
                    _, main, _, _ = stages._audio_graph(c, win=win)
                    _, report, _, k, _ = ar._track_group(c, tracks)
                    self.assertEqual(main[0], report[0])
                    self.assertIn('afade=t=out:st=0.000000:d=0.333333', report[0])
                    self.assertIn('adelay=delays=8000S:all=1', report[0])
                    self.assertEqual(main[-2].replace('[t0]', '[track]'),
                                     report[-1].replace(f'[tg{k}]', '[track]'))

    def test_active_duck_keys_match_render_and_report_consumers(self):
        import copy
        from contextlib import redirect_stdout
        from io import StringIO
        from types import SimpleNamespace
        from vtx.pipeline import stages
        event = {'src': 'a', 'in': 0, 'out': 30, 'volume': 1}

        def track(name, **kwargs):
            return dict(name=name, events=[dict(event)], **kwargs)

        voice, bed = track('voice'), track('bed', duck=True)
        # Empty events are a runtime/report-only boundary, not schema-valid input.
        cases = (
            ('single_key', [voice, bed], [0], [1], {1: (0,)}),
            ('duck_key', [dict(voice, duck=True), bed], [], [0, 1], {}),
            ('empty_key', [dict(voice, events=[]), bed], [], [1], {}),
            ('empty_duck', [voice, dict(bed, events=[])], [], [], {}),
            ('missing_key', [voice, dict(bed, duck_by=['missing'])], [], [1], {}),
            ('multiple_keys', [voice, track('cue'), bed, track('amb', duck=True, duck_by=['cue'])],
             [0, 1], [2, 3], {2: (0,), 3: (1,)}),
            ('mixed_duck_key', [dict(voice, duck=True), track('cue'),
                               dict(bed, duck_by=['voice', 'cue'])], [1], [0, 2], {2: (1,)}),
            ('empty_ignored', [voice, bed, track('empty', duck=True) | {'events': []}], [0], [1], {1: (0,)}),
        )
        for name, transformed, keys, ducks, targets in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                c = _event_context(); c.root = c.out = Path(directory); c.cache = c.root / 'cache'
                # Every case changes the plugin result rather than selecting the original list.
                c.tl = {'audio_tracks': copy.deepcopy([voice, bed])}
                before = copy.deepcopy(c.tl)
                wav = c.out / 'audio.wav'; wav.write_bytes(b'existing WAV')
                receipt = c.out / 'receipt.json'; receipt.write_bytes(b'existing receipt')
                c.run = mock.Mock(return_value=SimpleNamespace(returncode=0, stderr='', stdout=''))
                with mock.patch.object(ar._plugin_hooks, 'run_audio_events', return_value=transformed) as hook, \
                     mock.patch.object(ar._plugin_hooks, 'resolve_source_wav', return_value='/unused/a.wav') as resolve, \
                     mock.patch.object(ar, 'render_stems', wraps=ar.render_stems) as render, \
                     mock.patch.object(ar, 'ebur128_summary', return_value=(-14, -2)), \
                     mock.patch.object(ar, '_activity_windows', return_value=([], 1)), \
                     mock.patch.object(ar, 'ebur128_series', return_value=[]), \
                     mock.patch.object(ar, 'silences', return_value=[]), redirect_stdout(StringIO()):
                    _, chains, count, used = stages._audio_graph(c)
                    self.assertEqual(ar.run_report(c), 0)
                self.assertEqual(hook.call_args_list, [mock.call(c, c.tl['audio_tracks'])] * 2)
                self.assertEqual(count, sum(len(tr['events']) for tr in transformed))
                self.assertEqual(used, bool(keys and ducks))
                graph = ';'.join(chains)
                self.assertEqual(graph.count('sidechaincompress='), len(targets))
                if keys and ducks:
                    render.assert_called_once_with(c, [transformed[i] for i in keys],
                                                    [transformed[i] for i in ducks], c.cache / 'report_audio')
                    self.assertEqual(c.run.call_count, 2)
                    stem_graph = c.run.call_args.args[0][c.run.call_args.args[0].index('-filter_complex') + 1]
                    self.assertEqual(stem_graph.count('sidechaincompress='), len(targets))
                    for i in keys:
                        split = f'asplit={1 + sum(i in group for group in set(targets.values()))}'
                        self.assertIn(f'[t{i}]{split}', graph)
                        self.assertIn(f'[t{i}]', chains[-1], 'key must remain audible in the final mix')
                else:
                    render.assert_not_called()
                    c.run.assert_not_called()
                self.assertEqual(resolve.call_count, 1 + (sum(len(transformed[i]['events']) for i in keys + ducks)
                                                         if keys and ducks else 0))
                self.assertEqual(c.tl, before)
                self.assertEqual((wav.read_bytes(), receipt.read_bytes()), (b'existing WAV', b'existing receipt'))

    def test_duck_selection_keeps_window_filter_before_union(self):
        from vtx.pipeline import stages
        c = _event_context()
        c.tl = {'audio_tracks': [
            {'name': 'voice', 'events': [{'src': 'a', 'in': 0, 'out': 10, 'volume': 1}]},
            {'name': 'bed', 'duck': True, 'events': [{'src': 'a', 'in': 20, 'out': 30, 'volume': 1}]},
        ]}
        with mock.patch.object(stages._plugin_hooks, 'run_audio_events', side_effect=lambda c, t: t), \
             mock.patch.object(stages._plugin_hooks, 'resolve_source_wav', return_value='/unused/a.wav'):
            for win, margin, expected in ((None, 0, True), ((20, 30), 0, False), ((20, 30), 60, True)):
                with self.subTest(win=win, margin=margin):
                    _, chains, count, used = stages._audio_graph(c, win=win, margin_frames=margin)
                    self.assertEqual(used, expected)
                    self.assertEqual(count, 2 if expected else 1)
                    self.assertEqual(';'.join(chains).count('sidechaincompress='), int(expected))

    def test_fade_edge_values_preserve_production_policy(self):
        from vtx.media.audio_filters import event_chain
        c = _event_context()
        base = {'src': 'a', 'in': 0, 'out': 10, 'volume': 1}
        for value in (0, 10, 20, -1, float('inf'), float('-inf'), float('nan')):
            with self.subTest(fade=value):
                event = dict(base, fade_out_frames=value)
                chain = event_chain(c, 0, event)
                # No new validation/automatic zeroing: production min semantics
                # remain exact, including invalid values rejected upstream.
                fade = min(value, 10) if value else 0
                if fade:
                    self.assertIn(f'afade=t=out:st={(10-fade)/30:.6f}:d={fade/30:.6f}', chain)
                else:
                    self.assertNotIn('afade=', chain)
        with self.assertRaises(TypeError):
            event_chain(c, 0, dict(base, fade_out_frames='invalid'))
        with self.assertRaisesRegex(KeyError, 'out'):
            event_chain(c, 0, {'in': 0})
        with self.assertRaises(TypeError):
            event_chain(c, 0, {'in': 0, 'out': 10, 'fade_out_frames': 'invalid'})

    def test_consumers_keep_distinct_missing_volume_policy(self):
        from unittest.mock import patch
        from vtx.pipeline import stages
        event = {'src': 'a', 'in': 0, 'out': 10}
        tracks = [{'name': 'voice', 'events': [event]}]
        c = _event_context(); c.tl = {'audio_tracks': tracks}
        _, report, _, _, _ = ar._track_group(c, tracks)
        self.assertIn('volume=1', report[0])
        with patch.object(stages._plugin_hooks, 'run_audio_events', side_effect=lambda c, t: t), \
             patch.object(stages._plugin_hooks, 'resolve_source_wav', return_value='/unused/a.wav'):
            with self.assertRaises(KeyError):
                stages._audio_graph(c)

    def test_report_uses_plugin_events_without_modifying_timeline_or_outputs(self):
        import copy
        import importlib
        from contextlib import ExitStack
        from unittest.mock import Mock, patch
        duck = importlib.import_module('plugins.duck-static')
        with tempfile.TemporaryDirectory() as directory:
            c = _event_context(); c.out = Path(directory); c.cache = c.out / 'cache'
            c.root = c.out
            event = {'src': 'a', 'in': 0, 'out': 30, 'volume': 1}
            tracks = [
                {'name': 'voice', 'events': [dict(event)], 'duck_strategy': {
                    'mode': 'static', 'voice_track': 'cue', 'global_db': -8}},
                {'name': 'cue', 'events': [dict(event, **{'in': 10, 'out': 20})]},
                {'name': 'bed', 'events': [dict(event)], 'duck': True},
            ]
            c.tl = {'audio_tracks': tracks}; original = copy.deepcopy(c.tl)
            expected = duck.audio_events(c, tracks)
            self.assertNotEqual(expected[0]['events'], tracks[0]['events'])
            wav = c.out / 'audio.wav'; wav.write_bytes(b'existing WAV')
            receipt = c.out / 'receipt.json'; receipt.write_bytes(b'existing receipt')
            c.receipt = Mock()
            with ExitStack() as stack:
                hook = stack.enter_context(patch.object(ar._plugin_hooks, 'run_audio_events', side_effect=duck.audio_events))
                render = stack.enter_context(patch.object(ar, 'render_stems', return_value=(wav, wav, wav)))
                for name, value in [('ebur128_summary', (-14, -2)),
                                    ('_activity_windows', ([], 1)),
                                    ('ebur128_series', []), ('silences', [])]:
                    stack.enter_context(patch.object(ar, name, return_value=value))
                self.assertEqual(ar.run_report(c), 0)
            hook.assert_called_once_with(c, tracks)
            self.assertEqual(render.call_args.args[1], [expected[0]])
            self.assertEqual(render.call_args.args[2], [expected[2]])
            self.assertEqual(c.tl, original)
            self.assertEqual(wav.read_bytes(), b'existing WAV')
            self.assertEqual(receipt.read_bytes(), b'existing receipt')
            c.receipt.assert_not_called()
            self.assertTrue((c.out / 'audio_report.json').is_file())

    def test_report_without_tracks_keeps_existing_schema_and_skips_stems(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            c = _event_context(); c.out = Path(directory); c.tl = {'audio_tracks': []}
            c.root = c.out
            (c.out / 'audio.wav').write_bytes(b'existing WAV')
            with patch.object(ar._plugin_hooks, 'run_audio_events', side_effect=lambda c, t: t) as hook, \
                 patch.object(ar, 'ebur128_summary', return_value=(-14, -2)), \
                 patch.object(ar, 'silences', return_value=[]), \
                 patch.object(ar, 'render_stems') as render:
                self.assertEqual(ar.run_report(c), 0)
            hook.assert_called_once_with(c, [])
            render.assert_not_called()
            report = json.loads((c.out / 'audio_report.json').read_text())
            self.assertEqual(set(report), {'summary', 'fail', 'warn', 'thresholds', 'source', 'checks'})
            self.assertEqual([x['level'] for x in report['checks']], ['PASS', 'PASS', 'INFO', 'INFO', 'PASS'])
            self.assertEqual(c.tl, {'audio_tracks': []})



if __name__ == "__main__":
    unittest.main()
