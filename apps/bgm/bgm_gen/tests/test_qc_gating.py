"""QC 门控分支单测（R4 审查 MAJOR-1 修复：bpm/key/hf_ratio/duration 门控
在 examples/minimal 里被关掉换绿，此处用合成 wav 直接打开门控测 pass/fail 两态）。

[fast] 全部；无网络无 GPU。
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

helpers.in_proc_env()
from bgm_gen.core.qc import qc_report                       # noqa: E402

SR = 44100


def _write_wav(path, sig):
    import wave
    sig = np.clip(sig, -1.0, 1.0)
    pcm = (sig * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def _metronome_78(seconds=6.0, seed=7):
    """78BPM 节拍器 + 幅度抖动 + 轻底噪（破坏 IOI 并列，口径同 test_qc）。"""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    sig = np.zeros(n)
    period = int(60.0 / 78.0 * SR)
    k = 0
    while k * period < n:
        t = np.arange(int(0.05 * SR)) / SR
        click = np.sin(2 * np.pi * 1000.0 * t) * np.exp(-t / 0.008)
        s = k * period
        amp = 0.6 * float(rng.uniform(0.85, 1.15))          # ±15% 抖动
        sig[s:s + len(click)] += amp * click[:max(0, n - s)]
        k += 1
    return sig + 0.002 * rng.standard_normal(n)


def _dm_chord(seconds=4.0):
    t = np.arange(int(seconds * SR)) / SR
    sig = sum(0.2 * np.sin(2 * np.pi * f * t) for f in (146.83, 174.61, 220.0))
    return sig


def _proj(meta, qc):
    return {"name": "gate_case", "meta": meta,
            "master": {"target_lufs": -14, "qc": qc}}


@pytest.mark.fast
def test_gate_bpm_pass():
    wav = os.path.join(helpers.tmpdir("gate"), "metro78.wav")
    _write_wav(wav, _metronome_78())
    rep = qc_report(wav, _proj({"bpm": 78, "sr": SR, "channels": 1},
                               {"bpm_tol_pct": 3}))
    assert rep["items"]["bpm"]["level"] == "pass"
    assert rep["items"]["bpm"]["pass"] is True


@pytest.mark.fast
def test_gate_bpm_fail():
    wav = os.path.join(helpers.tmpdir("gate"), "metro78b.wav")
    _write_wav(wav, _metronome_78())
    rep = qc_report(wav, _proj({"bpm": 100, "sr": SR, "channels": 1},
                               {"bpm_tol_pct": 3}))
    assert rep["items"]["bpm"]["level"] == "pass"
    assert rep["items"]["bpm"]["pass"] is False
    assert rep["ok"] is False


@pytest.mark.fast
def test_gate_key_pass_top2():
    wav = os.path.join(helpers.tmpdir("gate"), "dminor.wav")
    _write_wav(wav, _dm_chord())
    rep = qc_report(wav, _proj({"bpm": None, "key": "D minor",
                                "sr": SR, "channels": 1},
                               {"key_top2": True}))
    assert rep["items"]["key"]["level"] == "pass"
    assert rep["items"]["key"]["pass"] is True


@pytest.mark.fast
def test_gate_key_fail_wrong_target():
    wav = os.path.join(helpers.tmpdir("gate"), "dminor2.wav")
    _write_wav(wav, _dm_chord())
    rep = qc_report(wav, _proj({"bpm": None, "key": "C# major",
                                "sr": SR, "channels": 1},
                               {"key_top2": True}))
    assert rep["items"]["key"]["pass"] is False
    assert rep["ok"] is False


@pytest.mark.fast
def test_gate_duration_min_s():
    wav = os.path.join(helpers.tmpdir("gate"), "dur.wav")
    _write_wav(wav, _dm_chord(2.0))
    rep = qc_report(wav, _proj({"sr": SR, "channels": 1},
                               {"duration_min_s": 1.0}))
    assert rep["items"]["duration_s"]["pass"] is True
    rep2 = qc_report(wav, _proj({"sr": SR, "channels": 1},
                                {"duration_min_s": 999.0}))
    assert rep2["items"]["duration_s"]["pass"] is False
    assert rep2["ok"] is False


@pytest.mark.fast
def test_gate_hf_ratio_both_sides():
    lo = os.path.join(helpers.tmpdir("gate"), "low.wav")
    _write_wav(lo, _dm_chord())
    rep = qc_report(lo, _proj({"sr": SR, "channels": 1}, {"hf_ratio_max": 0.5}))
    assert rep["items"]["hf_ratio_gt8k"]["pass"] is True
    hi = os.path.join(helpers.tmpdir("gate"), "noise.wav")
    rng = np.random.default_rng(3)
    _write_wav(hi, 0.2 * rng.standard_normal(int(2.0 * SR)))
    rep2 = qc_report(hi, _proj({"sr": SR, "channels": 1}, {"hf_ratio_max": 0.1}))
    assert rep2["items"]["hf_ratio_gt8k"]["pass"] is False


@pytest.mark.fast
def test_gate_sr_mismatch_fails():
    wav = os.path.join(helpers.tmpdir("gate"), "sr.wav")
    _write_wav(wav, _dm_chord())
    rep = qc_report(wav, _proj({"sr": 48000, "channels": 1}, {}))
    assert rep["items"]["sr"]["pass"] is False
    assert rep["ok"] is False
