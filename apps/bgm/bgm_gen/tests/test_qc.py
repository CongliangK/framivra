"""④QC 层回归 [fast]（PLAN.md §5 R4-④，阈值=§6.3 与第 3 轮审查 BLOCKER-4 裁定）。

- bpm_check：合成 78BPM 节拍器，敲 16 拍，±15% 幅度抖动 + 轻底噪破坏 IOI 折叠
  对等幅并列（bpm_check 对等幅节拍器 78/156 恒并列的已知缺陷）→ 断言 top-3
  候选含 78±3 或其 0.5/2 倍频。
- key_check：合成 D 小调三和弦持续 4s（D3/F3/A4）→ 断言 top-1 r≥0.70 且
  top-2 内含 D minor。
- core/qc.py 测量器：尾部 2s 静音 → 最长无声窗 1.9-2.1s；-20 LUFS 校准信号
  ebur128 偏差 ≤0.5；220Hz 正弦 hf_ratio<0.01。
"""

import json
import os
import re
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

helpers.in_proc_env()
from bgm_gen.core.qc import (hf_energy_ratio, silent_window_stats)  # noqa: E402
from bgm_gen.engines.master import measure_lufs                    # noqa: E402

SR = 22050


def _run_checker(script, wav):
    """跑旧脚本（argv 契约冻结：`<py> <script> <audio>`），返回最后一行 stdout。"""
    r = helpers.run([helpers.py3(), os.path.join(helpers.ANALYSIS_DIR, script), wav],
                    timeout=300)
    assert r.returncode == 0, f"{script} exit={r.returncode}: {r.stderr[-800:]}"
    lines = (r.stdout or "").strip().splitlines()
    assert lines, f"{script} 无输出"
    return lines[-1]


# ------------------------------------------------------------ bpm_check
@pytest.mark.fast
def test_bpm_check_metronome_78():
    rng = np.random.default_rng(7)
    bpm, n_beats = 78.0, 16
    beat = 60.0 / bpm
    dur = beat * n_beats + 1.0
    sig = np.zeros(int(dur * SR))
    click_len = int(0.05 * SR)
    tt = np.arange(click_len) / SR
    click = np.sin(2 * np.pi * 1000 * tt) * np.exp(-tt / 0.012)
    for i in range(n_beats):
        amp = 0.8 * float(rng.uniform(0.85, 1.15))     # ±15% 幅度抖动破坏并列
        s = int(round(i * beat * SR))
        sig[s:s + click_len] += click * amp
    sig += rng.normal(0, 0.004, len(sig))              # 轻底噪
    wav = helpers.write_wav(os.path.join(helpers.tmpdir("qc"), "metronome78.wav"), sig)

    line = _run_checker("bpm_check.py", wav)
    cands = [float(m) for m in re.findall(r"([0-9.]+)\s*BPM", line)]
    assert len(cands) >= 1, f"bpm_check 未给候选：{line!r}"
    top3 = cands[:3]
    hits = [b for b in top3
            if abs(b - 78.0) <= 3 or abs(b - 39.0) <= 1.5 or abs(b - 156.0) <= 6]
    assert hits, f"top-3 {top3} 不含 78±3 及其 0.5/2 倍频：{line!r}"


# ----------------------------------------------------------- key_check
@pytest.mark.fast
def test_key_check_d_minor_triad():
    t = np.arange(int(4.0 * SR)) / SR
    sig = np.zeros_like(t)
    for f, amp in ((146.83, 1.0), (174.61, 0.9), (440.0, 0.7)):   # D3/F3/A4
        sig += amp * np.sin(2 * np.pi * f * t)
    sig *= 0.25 * np.minimum(1.0, t / 0.02)
    sig = sig / np.max(np.abs(sig)) * 0.8
    wav = helpers.write_wav(os.path.join(helpers.tmpdir("qc"), "dmin.wav"), sig)

    line = _run_checker("key_check.py", wav)
    hits = re.findall(r"([A-G]#?(?:\s*(?:major|minor)))\(r=(-?[0-9.]+)\)", line)
    assert len(hits) >= 2, f"key_check 候选不足 top-2：{line!r}"
    top1_r = float(hits[0][1])
    top2_keys = [k.strip().lower() for k, _ in hits[:2]]
    assert top1_r >= 0.70, f"top-1 r={top1_r} < 0.70：{line!r}"
    assert "d minor" in top2_keys, f"top-2 {top2_keys} 不含 D minor：{line!r}"


# ------------------------------------------------- core/qc.py 测量器回归
@pytest.mark.fast
def test_silent_window_tail_2s():
    t = np.arange(int(3.0 * SR)) / SR
    sig = np.where(t < 1.0, 0.3 * np.sin(2 * np.pi * 440 * t), 0.0)
    wav = helpers.write_wav(os.path.join(helpers.tmpdir("qc"), "tail_silence.wav"), sig)
    st = silent_window_stats(wav)
    assert 1.9 <= st["longest_s"] <= 2.1, \
        f"尾部 2s 静音测得最长无声窗 {st['longest_s']}s（50ms 分帧口径）"


@pytest.mark.fast
def test_ebur128_calibration_minus20():
    """ffmpeg loudnorm(I=-20) 校准信号 → ebur128 复测偏差 ≤0.5（两滤镜互证）。"""
    d = helpers.tmpdir("qc")
    src = os.path.join(d, "cal_src.wav")
    t = np.arange(int(5.0 * SR)) / SR
    rng = np.random.default_rng(3)
    sig = 0.5 * rng.normal(size=len(t))                # 平稳宽带，测量口径友好
    helpers.write_wav(src, sig)
    cal = os.path.join(d, "cal_m20.wav")
    p1 = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", src, "-af",
                         "loudnorm=I=-20:TP=-2:LRA=11:print_format=json",
                         "-f", "null", "-"], capture_output=True, text=True, check=True)
    j = json.loads(p1.stdout[p1.stdout.index("{"):p1.stdout.rindex("}") + 1]) \
        if "{" in p1.stdout else json.loads(p1.stderr[p1.stderr.index("{"):p1.stderr.rindex("}") + 1])
    af = ("loudnorm=I=-20:TP=-2:LRA=11"
          f":measured_I={j['input_i']}:measured_TP={j['input_tp']}"
          f":measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}"
          f":offset={j['target_offset']}:linear=true")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", af,
                    "-ar", str(SR), cal], check=True)
    lufs = measure_lufs(cal)["lufs"]
    assert abs(lufs - (-20.0)) <= 0.5, f"ebur128 复测 {lufs} ≠ -20±0.5"


@pytest.mark.fast
def test_hf_ratio_pure_tone():
    t = np.arange(int(3.0 * SR)) / SR
    sig = 0.5 * np.sin(2 * np.pi * 220.0 * t)
    wav = helpers.write_wav(os.path.join(helpers.tmpdir("qc"), "sine220.wav"), sig)
    ratio = hf_energy_ratio(wav)
    assert ratio < 0.01, f"220Hz 正弦 >8kHz 能量占比 {ratio} ≥ 0.01（应近 0）"
