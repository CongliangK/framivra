"""master.pre_limit 母前峰值限幅器（PLAN_v3 §4.3 crest 预案，R4 主线仲裁落地）。

覆盖：①峰值上限遵守（削顶只发生在越限段）；②远端安静段零扰动（释放窗
外增益恒 1）；③逐位确定（同输入两次 sha 相等——确定性 DSP 族契约）；
④工程校验（v1 拒绝 / v2 未知键拒绝 / ceiling_db 必填 / 范围门）。
"""

import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

pytestmark = pytest.mark.fast

SR = 44100


def _synth_premix(path, sr=SR, dur_s=2.0):
    """基准音 0.1 幅 + 每 0.5s 一记 1.5 幅爆发（crest 人为拉高）→ f32 wav。"""
    from bgm_gen.core.mixer import _write_f32_wav
    t = np.arange(int(sr * dur_s)) / sr
    base = 0.1 * np.sin(2 * np.pi * 440.0 * t)
    sig = base.copy()
    for k in range(4):
        i0 = int((0.1 + 0.5 * k) * sr)
        sig[i0:i0 + int(0.02 * sr)] += 1.5 * np.sin(
            2 * np.pi * 880.0 * t[i0:i0 + int(0.02 * sr)])
    audio = np.stack([sig, sig], axis=1)
    _write_f32_wav(path, audio.astype(np.float32), sr)
    return path


def _limit(path, ceiling_db, attack_ms=5.0, release_ms=150.0):
    from bgm_gen.core.mixer import _apply_premaster_limit, _read_audio_f32
    sr = SR
    out = _apply_premaster_limit(
        path, {"ceiling_db": ceiling_db, "attack_ms": attack_ms,
               "release_ms": release_ms}, sr, 2, helpers.tmpdir("premaster"))
    return out, _read_audio_f32(out, sr, 2)


def test_peak_ceiling_respected_and_quiet_untouched(tmp_path):
    src = _synth_premix(str(tmp_path / "premix.wav"))
    out, a = _limit(src, ceiling_db=-6.0)               # 0.5 线性幅
    assert os.path.basename(out) == "mix_premaster_limited.wav"
    ceiling = 10 ** (-6.0 / 20)
    assert np.abs(a).max() <= ceiling * (1 + 1e-6), np.abs(a).max()
    # 首爆发(0.1s)前的头部段：增益恒 1（lookahead 只前瞻不回溯），零扰动。
    # （末段不能这样断言：一阶释放 τ=150ms，距末爆发 80-400ms 处增益仍在
    #   恢复中——-0.8~-4dB 属正常限幅器行为，不是缺陷。）
    head_in = _read(src)[:int(0.08 * SR)]
    assert np.abs(head_in - a[:int(0.08 * SR)]).max() < 1e-6


def _read(path):
    from bgm_gen.core.mixer import _read_audio_f32
    return _read_audio_f32(path, SR, 2)


def test_bit_determinism(tmp_path):
    src = _synth_premix(str(tmp_path / "premix.wav"))
    out1, _ = _limit(src, -6.0)
    out2, _ = _limit(src, -6.0)
    assert helpers.sha256(out1) == helpers.sha256(out2)


def test_crest_reduced(tmp_path):
    """削顶后 crest（峰值-基准段 dB 差）收窄 ≥6dB（预案目的：TP-I 降）。"""
    src = _synth_premix(str(tmp_path / "premix.wav"))
    _, a = _limit(src, -6.0)
    b = _read(src)
    base_n = int(0.08 * SR)
    in_crest = 20 * np.log10(np.abs(b).max() / np.abs(b[:base_n]).max())
    out_crest = 20 * np.log10(np.abs(a).max() / np.abs(a[:base_n]).max())
    assert in_crest - out_crest >= 6.0 - 1e-3


def test_worst_phase_block_onset_no_overshoot(tmp_path):
    """R4 审查 B·M-2 回归锁：峰值样本必须在自己治理窗内（对齐延迟 look-1）。
    最坏相位 = 峰起始于 1kHz 块首（此前版本实测 12dB 限幅单样本过冲 7%，
    靠末段硬钳掩盖）。"""
    from bgm_gen.core.mixer import _write_f32_wav
    blk = int(round(SR / 1000))
    n = int(SR * 1.0)
    sig = np.zeros(n)
    j0 = blk * 500                                  # 峰恰落块首
    sig[j0:j0 + int(0.02 * SR)] = 4.0               # 深限幅（ceiling 0.5 → -18dB）
    p = str(tmp_path / "premix.wav")
    _write_f32_wav(p, np.stack([sig, sig], axis=1).astype(np.float32), SR)
    _, a = _limit(p, -12.0)                         # ceiling=0.2512，限 24dB
    peak = np.abs(a).max()
    ceiling = 10 ** (-12.0 / 20)
    assert peak <= ceiling * (1 + 1e-4), (
        f"最坏相位过冲 {peak / ceiling:.4f}×（对齐 off-by-one 回归？）")


# ------------------------------------------------------------ 工程校验门

def _proj_with_master(tmp_path, master_patch, version="2"):
    p = json.load(open(os.path.join(helpers.EXAMPLES_DIR, "minimal.json")))
    p["meta"]["spec_version"] = version
    p["master"].update(master_patch)
    path = str(tmp_path / "p.json")
    json.dump(p, open(path, "w"), ensure_ascii=False)
    return path


def _load_err(path):
    from bgm_gen.core.project import ProjectError, load_project
    with pytest.raises(ProjectError) as ei:
        load_project(path)
    return "\n".join(ei.value.args)


def test_v1_rejects_pre_limit(tmp_path):
    err = _load_err(_proj_with_master(
        tmp_path, {"pre_limit": {"ceiling_db": 6.0}}, version="1"))
    assert "仅 spec_version=\"2\"" in err


def test_v2_unknown_key_and_missing_ceiling(tmp_path):
    err = _load_err(_proj_with_master(
        tmp_path, {"pre_limit": {"ceiling_db": 6.0, "knee_db": 3}}))
    assert "未知键" in err and "knee_db" in err
    err = _load_err(_proj_with_master(tmp_path, {"pre_limit": {"attack_ms": 5}}))
    assert "ceiling_db：必填" in err


def test_v2_range_gates(tmp_path):
    """R4 审查 B·M-3：范围门负例（此前 docstring 声称覆盖但零用例）。"""
    for bad in ({"ceiling_db": 13.0},                # > hi=12
                {"ceiling_db": 6.0, "attack_ms": 0.05},   # < lo=0.1
                {"ceiling_db": 6.0, "release_ms": 3000.0},  # > hi=2000
                {"ceiling_db": float("nan")}):       # isfinite（_num 层）
        err = _load_err(_proj_with_master(tmp_path, {"pre_limit": bad}))
        assert "越界" in err or "有限" in err, (bad, err)


def test_v2_empty_object_rejected_null_is_off(tmp_path):
    """R4 审查 B·M-4：{} 违反 required 必须拒绝（falsy 门曾静默当 off）；
    null = 显式关（合法，等价缺省）。"""
    err = _load_err(_proj_with_master(tmp_path, {"pre_limit": {}}))
    assert "ceiling_db：必填" in err
    from bgm_gen.core.project import load_project
    proj = load_project(_proj_with_master(tmp_path, {"pre_limit": None}))
    assert "pre_limit" not in proj["master"]


def test_v2_valid_normalizes_defaults(tmp_path):
    from bgm_gen.core.project import load_project
    proj = load_project(_proj_with_master(
        tmp_path, {"pre_limit": {"ceiling_db": 6.0}}))
    pl = proj["master"]["pre_limit"]
    assert pl == {"ceiling_db": 6.0, "attack_ms": 5.0, "release_ms": 150.0}
    # save 回写含键且不丢；v1 工程保存不得出现 "pre_limit": null
    from bgm_gen.core.project import save_project
    save_project(str(tmp_path / "rt.json"), proj)
    rt = json.load(open(str(tmp_path / "rt.json")))
    assert rt["master"]["pre_limit"] == pl
    proj1 = load_project(_proj_with_master(tmp_path, {}, version="1"))
    save_project(str(tmp_path / "rt1.json"), proj1)
    rt1 = json.load(open(str(tmp_path / "rt1.json")))
    assert "pre_limit" not in rt1["master"]
