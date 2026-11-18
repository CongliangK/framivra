#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_pipeline.py · finish 链 manifest 记账 + final 格式契约单测（方案 §9.2）

绕过网络/模型：ffmpeg 合成 KB 级 raw wav → 直接调 edge_worker.finish_window
（纯 ffmpeg/本地操作）→ 断言 manifest 六字段、final 格式 48000/mono/pcm_s16le、
时长贴预算 ±0.02s（repack_voice.py 消费契约静态断言，不动 live）。
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import edge_worker  # noqa: E402
import tts_cli  # noqa: E402


class TestIndexTTSInterpreter(unittest.TestCase):
    def test_init_template_uses_native_conda_default(self):
        template = Path(tts_cli.REPO_ROOT) / "config.example.json"
        cfg = json.loads(template.read_text(encoding="utf-8"))
        env = Path.home() / "miniconda3" / "envs" / "indextts"
        expected = env / "python.exe" if os.name == "nt" else env / "bin" / "python"
        self.assertEqual(Path(tts_cli.DEFAULT_TTS_PY), expected)
        with tempfile.TemporaryDirectory() as td:
            conda = Path(td) / expected.name
            conda.touch()
            with patch.object(tts_cli, "DEFAULT_TTS_PY", str(conda)), \
                 patch.object(tts_cli, "load_config", return_value=cfg):
                self.assertEqual(tts_cli._indextts_python(td), str(conda))

    def test_probe_and_gen_agree_on_native_venv_fallback(self):
        with tempfile.TemporaryDirectory(prefix="tts interpreter ") as td:
            repo = Path(td)
            layout = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
            fallback = repo.joinpath(".venv", *layout)
            fallback.parent.mkdir(parents=True)
            fallback.touch()
            spec = repo / "spec.json"
            spec.write_text('{"windows": []}', encoding="utf-8")
            configured = repo / "configured"
            configured.mkdir()  # A directory must not shadow the interpreter.
            cfg = {"tts": {"repo": str(repo), "python": str(configured)}}
            args = argparse.Namespace(spec=str(spec), engine="indextts", ids=None,
                                      dry_run=False, project=str(repo), only_missing=False)
            with patch.object(tts_cli, "load_config", return_value=cfg), \
                 patch.object(tts_cli.shutil, "which", return_value=None), \
                 patch.object(tts_cli.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], 4)) as run, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                tts_cli.cmd_probe(None)
                run.assert_not_called()
                self.assertIn(f"✓ 解释器 — {fallback}", output.getvalue())
                self.assertIn("IndexTTS 推理仍受许可策略阻断", output.getvalue())
                with self.assertRaises(SystemExit) as stopped:
                    tts_cli.cmd_gen(args)
                self.assertEqual(stopped.exception.code, 4)
                self.assertEqual(run.call_args.args[0][0], str(fallback))
                cfg["tts"]["python"] = sys.executable
                self.assertEqual(tts_cli._indextts_python(str(repo)), sys.executable)


def synth_raw(path, amp=0.2, dur=3.0, freq=440, rate=22050):
    """模拟引擎 raw（IndexTTS 出 22.05k）：KB 级正弦 wav。"""
    src = f"aevalsrc={amp}*sin(2*PI*{freq}*t):d={dur}:s={rate}"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src,
                    "-ac", "1", "-c:a", "pcm_s16le", path], check=True)
    return path


def ffprobe_stream(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                        "-show_entries", "stream=codec_name,sample_rate,channels",
                        "-of", "json", path], capture_output=True, text=True)
    return json.loads(r.stdout)["streams"][0]


def ffprobe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


class TestFinishWindow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vt_tts_fin_")
        self.final_dir = os.path.join(self.tmp, "final")
        os.makedirs(self.final_dir, exist_ok=True)
        self.raw = synth_raw(os.path.join(self.tmp, "raw_T1.wav"))

    def test_manifest_fields(self):
        row = edge_worker.finish_window("T1", "测试口播五字", 2.0,
                                        self.raw, self.final_dir)
        self.assertEqual(sorted(row.keys()),
                         ["budget_s", "chars", "duration_factor", "final_s",
                          "id", "peak_db", "raw_s"])
        self.assertEqual(row["id"], "T1")
        self.assertEqual(row["chars"], 6)  # "测试口播五字" 6 字（n_chars 去空白计）
        self.assertEqual(row["budget_s"], 2.0)
        self.assertAlmostEqual(row["raw_s"], 3.0, delta=0.05)
        self.assertIsNone(row["duration_factor"])  # edge 引擎无该模型参数
        # final_s 为 2 位小数舍入值（manifest 记账口径），容差取舍入半宽
        self.assertAlmostEqual(row["final_s"], 2.0, delta=0.006)
        self.assertAlmostEqual(row["peak_db"], -6.0, delta=0.5)

    def test_final_format_contract(self):
        """repack_voice.py 消费契约：48kHz / mono / pcm_s16le / 时长=budget±0.002。

        精确落点（apad+-t，2026-09-11 修复后收紧）：atempo 样本量化漂移
        （~0.3%）已被消除，占位 wav 不得再少帧被 vtx validate 拦。
        """
        edge_worker.finish_window("T1", "测试口播五字", 2.0, self.raw, self.final_dir)
        f = os.path.join(self.final_dir, "T1.wav")
        self.assertTrue(os.path.exists(f))
        st = ffprobe_stream(f)
        self.assertEqual(st["codec_name"], "pcm_s16le")
        self.assertEqual(st["sample_rate"], "48000")
        self.assertEqual(st["channels"], 1)
        self.assertAlmostEqual(ffprobe_duration(f), 2.0, delta=0.002)

    def test_stretch_direction(self):
        """atempo 方向集成验证：raw 3s 贴预算 4s → 变慢（r=0.75，输出更长）。"""
        edge_worker.finish_window("T2", "测试", 4.0, self.raw, self.final_dir)
        f = os.path.join(self.final_dir, "T2.wav")
        self.assertAlmostEqual(ffprobe_duration(f), 4.0, delta=0.002)

    def test_exact_quantization_anchor(self):
        """量化点精确落点（2026-09-11 集成验收缺陷回归）：
        ① raw 3.43s / budget 3.0（非整比）→ final 恰 3.0s；
        ② budget 7.62（228 帧+20ms 口径）→ final 恰 7.620s 且 int(dur×30)=228
           ——占位态 vtx validate 不再被「音频源少 1 帧」拦。
        """
        import audio_processing as _audio
        for budget, raw_dur in [(3.0, 3.43), (7.62, 3.0)]:
            with tempfile.TemporaryDirectory(prefix="vt_tts_q_") as td:
                raw = synth_raw(os.path.join(td, "raw.wav"), dur=raw_dur)
                out = os.path.join(td, "final.wav")
                got = _audio.apply_atempo(raw, budget, out)
                self.assertAlmostEqual(got, budget, delta=0.002)
                self.assertAlmostEqual(ffprobe_duration(out), budget, delta=0.002)
        self.assertEqual(int(7.62 * 30), 228)  # 口径自检：7.62s=228.6 帧≥228 链帧


if __name__ == "__main__":
    unittest.main()
