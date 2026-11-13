"""v3.2 空间化机制（PLAN_v3 空间化轮）：IR 生成器 / 深度模型 / wet 总线 / 校验门。

四组覆盖（任务书口径）：
    ① IR：逐位确定（同参数两次 sha 相等）/ Schroeder RT60 自测 ±15% /
       立体声去相关（|corr(L,R)|<0.3）/ 早期反射左右能量差 >3dB；
    ② 深度模型单调性：depth 0/0.5/1 电平递减、HF 能量占比递减、互相关
       预延迟递增（纯 numpy 直测 apply_track_space——中性点逐位恒等另锁）；
    ③ 集成：3 synth 轨不同 depth 全链渲染（wet 尾长=IR 卷积全长、尾段
       能量非零、无 space 工程完全旁路）；
    ④ 校验负例：v1+space 拒 / 未知键 / depth·width·send_db·rt60_s·seed
       越界缺型 / null=显式关 / save 不落 null 键。

预算纪律：fast ≤75s（PLAN §6）——本文件仅一次全链渲染（小夹具 ~2s），
其余纯 numpy / load_project；无 space 工程位级回归由 test_cli 的
out/minimal mix.flac sha 锚点承担（机制纯增量的最强证明）。
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


def _db(x):
    return 20.0 * np.log10(max(float(x), 1e-12))


def _rms(a):
    return float(np.sqrt(np.mean(np.asarray(a, dtype=np.float64) ** 2)))


def _mono(a):
    a = np.asarray(a, dtype=np.float64)
    return a.mean(axis=1) if a.ndim > 1 else a


def _hf_fraction(a, sr=SR, split_hz=8000.0):
    """>split_hz 能量占比（深度模型 HF 滚降的单调性观测量）。"""
    m = _mono(a)
    spec = np.abs(np.fft.rfft(m)) ** 2
    f = np.fft.rfftfreq(len(m), 1 / sr)
    return float(spec[f > split_hz].sum() / spec.sum())


def _xcorr_lag(a, b, sr=SR, max_lag_s=0.035):
    """a 相对 b 的正延迟（样本）：FFT 互相关正向滞后窗 argmax。"""
    a, b = _mono(a), _mono(b)
    n = 1 << (len(a) + len(b) - 1 - 1).bit_length()
    c = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    lim = int(max_lag_s * sr)
    return int(np.argmax(c[:lim + 1]))


def _click_train(dur_s=1.0, period_s=0.1, width_s=0.002, seed=5):
    """确定性宽带 click 列（深度模型测试信号：预延迟可互相关定位、
    HF 滚降可分频段计量）。噪声取 space._hash_uniform（自产确定性）。"""
    from bgm_gen.core.space import _hash_uniform
    n = int(dur_s * SR)
    x = np.zeros((n, 2), dtype=np.float64)
    w = int(width_s * SR)
    noise = _hash_uniform(seed, 0, n)               # 独立 L/R（立体声素材）
    noise_r = _hash_uniform(seed, 1, n)
    for start in range(0, n - w, int(period_s * SR)):
        x[start:start + w, 0] = noise[start:start + w] * np.hanning(w)
        x[start:start + w, 1] = noise_r[start:start + w] * np.hanning(w)
    return x


# ---------------------------------------------------------------- ① IR

def test_ir_bit_determinism():
    """同参数两次生成逐位相等；不同 seed 输出不同（确定性红线）。"""
    from bgm_gen.core.space import make_hall_ir
    a = make_hall_ir(1.6, seed=7)
    b = make_hall_ir(1.6, seed=7)
    assert a.tobytes() == b.tobytes()
    c = make_hall_ir(1.6, seed=8)
    assert a.tobytes() != c.tobytes()
    # 跨调用参数次序稳定性（size_m 关键字）
    d = make_hall_ir(1.6, size_m=25.0, seed=7)
    assert a.tobytes() == d.tobytes()


def test_ir_rt60_self_consistency():
    """Schroeder 倒积分实测 RT60 与参数差 ≤15%（工程校验门量程全跨度）。"""
    from bgm_gen.core.space import ir_rt60_schroeder, make_hall_ir
    for rt60 in (0.4, 0.8, 1.6, 4.0):               # schema 量程 0.4..4.0
        ir = make_hall_ir(rt60, seed=3)
        m = ir_rt60_schroeder(ir, SR)
        assert abs(m - rt60) / rt60 <= 0.15, (rt60, m)


def test_ir_stereo_decorrelation():
    """晚期混响两通道独立去相关：|corr(L,R)| < 0.3（多 seed）。"""
    from bgm_gen.core.space import make_hall_ir
    for seed in (0, 7, 42):
        ir = make_hall_ir(2.0, seed=seed)
        c = float(np.corrcoef(ir[:, 0], ir[:, 1])[0, 1])
        assert abs(c) < 0.3, (seed, c)


def test_ir_early_reflection_asymmetry():
    """早期反射簇左右不对称：早期窗（默认 30ms）L/R 能量差 >3dB（多尺寸）。"""
    from bgm_gen.core.space import IR_DEFAULT_SIZE_M, make_hall_ir
    for size in (12.0, 25.0, 50.0):
        ir = make_hall_ir(1.0, size_m=size, seed=11)
        win = ir[:int(round(0.03 * size / IR_DEFAULT_SIZE_M * SR))]
        e_l, e_r = float((win[:, 0] ** 2).sum()), float((win[:, 1] ** 2).sum())
        assert 10.0 * np.log10(e_l / e_r) > 3.0, (size, e_l, e_r)


def test_ir_energy_normalized():
    """全局能量归一 Σh²=1（跨通道合计——send_db 的用户语义锚点）。"""
    from bgm_gen.core.space import make_hall_ir
    ir = make_hall_ir(2.2, seed=1)
    assert abs(float((ir ** 2).sum()) - 1.0) < 1e-12
    ir2 = make_hall_ir(0.5, seed=1)                  # 不同 rt60 同口径
    assert abs(float((ir2 ** 2).sum()) - 1.0) < 1e-12


def test_ola_convolve_matches_direct():
    """OLA 分块卷积 == 直接卷积（小信号全等校验，块边界正确性）。"""
    from bgm_gen.core.space import _hash_uniform, ola_convolve
    x = _hash_uniform(1, 0, 500)[:, None] * np.ones((1, 2))
    ir = np.zeros((61, 2))
    ir[3, 0], ir[17, 1], ir[60, 0] = 0.5, -0.3, 0.2
    ref = np.stack([np.convolve(x[:, 0], ir[:, 0]),
                    np.convolve(x[:, 1], ir[:, 1])], axis=1)
    for block in (64, 128, 500):                     # 块长整除/非整除/超长
        out = ola_convolve(x, ir, block=block)
        assert out.shape == ref.shape
        np.testing.assert_allclose(out, ref, atol=1e-9)


def test_ola_convolve_ir_longer_than_block():
    """v3.2 审收 M-4 补测：IR 比分块长（产线真实路径 m≈89k > block=65536）
    的对拍——此前用例 ir=61 恰好错过该区。小 IR + 小 block 等价触发同一
    数学路径（nfft ≥ block+m-1 的混叠约束），另加一例真尺寸 IR。"""
    from bgm_gen.core.space import make_hall_ir, ola_convolve
    rng_x = np.linspace(-1.0, 1.0, 8820)[:, None] * np.ones((1, 2))  # 0.2s
    # ① 小 IR + 小 block：ir(约 40k) > block(8192) → 同一约束路径
    ir = make_hall_ir(0.9, seed=3)
    assert len(ir) > 8192, len(ir)
    ref = np.stack([np.convolve(rng_x[:, c], ir[:, c]) for c in range(2)],
                   axis=1)
    out = ola_convolve(rng_x, ir, block=8192)
    np.testing.assert_allclose(out, ref, atol=1e-9)
    # ② 真尺寸路径冒烟：2s IR（≈89k 样本）对 0.2s 信号，仅断言形状与有限性
    #   （全量直接卷积对拍 ~1e9 MACs 过重，①已锁数学等价）
    ir2 = make_hall_ir(2.0, seed=3)
    out2 = ola_convolve(rng_x, ir2, block=65536)
    assert out2.shape == (len(rng_x) + len(ir2) - 1, 2)
    assert np.isfinite(out2).all()


# ------------------------------------------------------------ ② 深度模型

def test_depth_level_hf_predelay_monotonic():
    """depth 0/0.5/1 三档：电平递减（≈-4.5dB/档）、HF 占比递减、预延迟递增。"""
    from bgm_gen.core.space import PREDELAY_MAX_S, apply_track_space
    x = _click_train()
    ys = {d: apply_track_space(x, SR, d, 0.3) for d in (0.0, 0.5, 1.0)}
    rms = {d: _db(_rms(y)) for d, y in ys.items()}
    assert rms[0.0] - rms[0.5] >= 1.5                # 理论 2.25dB/档（滤波另减）
    assert rms[0.5] - rms[1.0] >= 1.5
    hf = {d: _hf_fraction(y) for d, y in ys.items()}
    assert hf[0.0] > hf[0.5] > hf[1.0] > 0.0
    assert hf[0.5] < 0.5 * hf[0.0]
    lag = {d: _xcorr_lag(y, x) for d, y in ys.items()}
    exp = {d: int(round(d * PREDELAY_MAX_S * SR)) for d in (0.5, 1.0)}
    tol = int(0.003 * SR)                            # ±3ms（滤波去高频的相关峰展宽）
    assert lag[0.5] == pytest.approx(exp[0.5], abs=tol), lag
    assert lag[1.0] == pytest.approx(exp[1.0], abs=tol), lag
    assert 0 < lag[0.5] < lag[1.0]


def test_depth_zero_neutral_bit_identity():
    """depth=0 + width=0.3 = 全中性点：输出与输入逐位相等（机制纯增量语义）。"""
    from bgm_gen.core.space import apply_track_space
    x = _click_train(dur_s=0.5)
    y = apply_track_space(x, SR, 0.0, 0.3)
    assert y.tobytes() == np.asarray(x, dtype=np.float64).tobytes()


def test_width_extremes():
    """width=0 → L≡R（单声道坍缩）；width>0.3 → side 能量放大（width/0.3）。"""
    from bgm_gen.core.space import apply_track_space
    x = _click_train(dur_s=0.5)
    mono = apply_track_space(x, SR, 0.0, 0.0)
    np.testing.assert_allclose(mono[:, 0], mono[:, 1], atol=0)
    wide = apply_track_space(x, SR, 0.0, 0.6)        # side ×2
    s0 = _rms(x[:, 0] - x[:, 1])
    s1 = _rms(wide[:, 0] - wide[:, 1])
    assert _db(s1 / s0) == pytest.approx(6.0, abs=0.5)   # 2× = +6dB


def test_depth_default_send_formula():
    from bgm_gen.core.space import depth_default_send_db
    assert depth_default_send_db(0.0) == -12.0
    assert depth_default_send_db(1.0) == -3.0
    assert depth_default_send_db(0.5) == -7.5


def test_mono_project_support():
    """mono 工程（meta.channels=1）：深度模型预延迟/电平/HF 照常、宽度跳过；
    mono IR（早期反射取 L/R 均值 + 单流晚期噪声）RT60 自测同过。"""
    from bgm_gen.core.space import apply_track_space, ir_rt60_schroeder, \
        make_hall_ir
    x = _click_train(dur_s=0.4)[:, :1]               # (n, 1)
    y = apply_track_space(x, SR, 0.5, 0.9)           # width 对 mono 无效不报错
    assert y.shape == x.shape
    assert _xcorr_lag(y, x) == pytest.approx(int(round(0.012 * SR)),
                                             abs=int(0.003 * SR))
    ir = make_hall_ir(1.0, seed=4, channels=1)
    assert ir.shape[1] == 1
    assert abs(ir_rt60_schroeder(ir, SR) - 1.0) / 1.0 <= 0.15


# ------------------------------------------------------------ ③ 集成

def _space_project(tmp_path):
    """小合成工程：3 synth 轨 depth 0/0.5/1 + 共享厅堂（rt60 0.6s）。时长 1.5s。"""
    tracks = [
        {"name": "kick", "engine": "synth", "instrument": "kick",
         "notes": [[36, 0, 0.25, 120], [36, 1, 0.25, 110], [36, 2, 0.25, 120]],
         "clip": {"start_beat": 0, "end_beat": 3}, "space": {"depth": 0.0}},
        {"name": "ep", "engine": "synth", "instrument": "fm_ep",
         "notes": [[60, 0, 1, 90], [64, 1.5, 1, 85]],
         "clip": {"start_beat": 0, "end_beat": 3}, "space": {"depth": 0.5}},
        {"name": "bass", "engine": "synth", "instrument": "bass",
         "notes": [[43, 0, 2, 100], [41, 2, 1, 95]],
         "clip": {"start_beat": 0, "end_beat": 3}, "space": {"depth": 1.0}},
    ]
    proj = {"meta": {"spec_version": "2", "sr": SR, "channels": 2, "bpm": 120,
                     "duration_hint": 1.5},
            "tracks": tracks,
            "master": {"space_ir": {"rt60_s": 0.6, "seed": 21}}}
    path = str(tmp_path / "proj.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False)
    return path


def test_full_chain_render_with_wet(tmp_path):
    """3 synth 轨不同 depth 全链渲染：成功 + premix 时长 = 工程 + IR 卷积全长
    + wet 尾段有能量 + 送量 stems 落盘。"""
    from bgm_gen.core.project import render_project
    from bgm_gen.core.space import make_hall_ir
    out = str(tmp_path / "out")
    res = render_project(_space_project(tmp_path), out_dir=out, quiet=True)
    assert os.path.isfile(os.path.join(out, "qc.json"))
    wet = os.path.join(out, "mix_wet.wav")
    assert os.path.isfile(wet)
    assert res["mixer"]["space"] and "wet hall" in res["mixer"]["space"]
    ir = make_hall_ir(0.6, seed=21, sr=SR, channels=2)
    n_dry = int(round(1.5 * SR))
    expect = n_dry + ir.shape[0] - 1                 # OLA full 卷积全长
    from bgm_gen.core.mixer import _read_audio_f32
    wet_x = _read_audio_f32(wet, SR, 2)
    assert wet_x.shape[0] == expect
    premix = _read_audio_f32(os.path.join(out, "mix_premaster.wav"), SR, 2)
    assert premix.shape[0] == expect
    # 尾段（工程时长后 0.15s 起）湿声有能量且低于干段 ≥6dB（混响衰减中）
    tail = wet_x[n_dry + int(0.15 * SR):]
    assert _db(_rms(tail)) > -80.0
    assert _db(_rms(tail)) < _db(_rms(wet_x[:n_dry])) - 6.0
    for name in ("kick", "ep", "bass"):
        assert os.path.isfile(os.path.join(out, "stems", f"{name}.send.wav"))


def test_bypass_without_space(tmp_path):
    """无 space 轨 → 空间化级完全旁路（零文件产物、摘要 None）。
    无 space 工程渲染位级一致由 test_cli 的 out/minimal mix.flac sha 锚点
    独立锁定（v1 工程零 space——机制纯增量的最强回归证明）。"""
    from bgm_gen.core.mixer import _apply_spatial
    assert _apply_spatial({"master": {}}, {"tracks": [{"name": "a"}]}, {}, SR, 2,
                          str(tmp_path)) == (None, None)


def test_dry_only_when_no_ir_declared(tmp_path):
    """space 声明而 master.space_ir 缺省：仅 dry 深度处理（bus 原地施加、
    无送量 stem、无 wet 文件；零观点：不强制配厅堂）。mixer 级直测。"""
    from bgm_gen.core.mixer import _apply_spatial, _read_audio_f32
    from bgm_gen.core.space import write_f32_wav
    bus = str(tmp_path / "t.bus.wav")
    write_f32_wav(bus, _click_train(dur_s=0.5), SR)
    before = _read_audio_f32(bus, SR, 2)
    proj = {"master": {}}
    plan = {"tracks": [{"name": "t", "space": {"depth": 1.0, "width": 0.3}}]}
    wet, note = _apply_spatial(proj, plan, {"t": bus}, SR, 2, str(tmp_path))
    assert wet is None and note and "dry-only" in note
    after = _read_audio_f32(bus, SR, 2)
    assert not np.array_equal(before, after)        # 深度模型已原地施加
    assert _xcorr_lag(after, before) == pytest.approx(
        int(round(0.024 * SR)), abs=int(0.003 * SR))
    assert not os.path.exists(str(tmp_path / "t.send.wav"))
    assert not os.path.exists(str(tmp_path / "mix_wet.wav"))


# ------------------------------------------------------------ ④ 校验负例

def _minimal_v2(tmp_path, track_patch=None, master_patch=None, version="2"):
    p = json.load(open(os.path.join(helpers.EXAMPLES_DIR, "minimal.json")))
    p["meta"]["spec_version"] = version
    if track_patch is not None:
        p["tracks"][0].update(track_patch)
    if master_patch is not None:
        p["master"].update(master_patch)
    path = str(tmp_path / "p.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False)
    return path


def _load_err(path):
    from bgm_gen.core.project import ProjectError, load_project
    with pytest.raises(ProjectError) as ei:
        load_project(path)
    return "\n".join(ei.value.args)


def test_v1_rejects_space_and_space_ir(tmp_path):
    err = _load_err(_minimal_v2(tmp_path, {"space": {"depth": 0.5}}, version="1"))
    assert "space" in err and "spec_version=\"1\"" in err
    err = _load_err(_minimal_v2(tmp_path, master_patch={
        "space_ir": {"rt60_s": 1.5, "seed": 1}}, version="1"))
    assert "space_ir" in err and "仅 spec_version=\"2\"" in err


def test_space_unknown_key_and_missing_depth(tmp_path):
    err = _load_err(_minimal_v2(tmp_path, {"space": {"depth": 0.5, "reverb": 1}}))
    assert "未知键" in err and "reverb" in err
    err = _load_err(_minimal_v2(tmp_path, {"space": {}}))
    assert "space.depth：必填" in err


def test_space_range_gates(tmp_path):
    for bad in ({"depth": 1.5}, {"depth": -0.1}, {"depth": 0.5, "width": 1.3},
                {"depth": 0.5, "send_db": -61.0}):
        err = _load_err(_minimal_v2(tmp_path, {"space": bad}))
        assert "越界" in err, (bad, err)


def test_space_ir_gates(tmp_path):
    for bad in ({"rt60_s": 0.2, "seed": 1},          # < 0.4
                {"rt60_s": 4.5, "seed": 1},          # > 4.0
                {"rt60_s": 1.5, "seed": "x"},        # seed 非整数
                {"rt60_s": 1.5},                     # seed 缺失
                {"rt60_s": 1.5, "seed": 1, "size_m": 30}):   # 未知键
        err = _load_err(_minimal_v2(tmp_path, master_patch={"space_ir": bad}))
        assert ("越界" in err or "整数" in err or "必填" in err
                or "未知键" in err), (bad, err)


def test_null_is_explicit_off_and_normalizes_defaults(tmp_path):
    from bgm_gen.core.project import load_project, save_project
    proj = load_project(_minimal_v2(tmp_path, {"space": None},
                                    master_patch={"space_ir": None}))
    assert all("space" not in t for t in proj["tracks"])
    assert "space_ir" not in proj["master"]
    proj = load_project(_minimal_v2(tmp_path, {"space": {"depth": 0.7}}))
    assert proj["tracks"][0]["space"] == {"depth": 0.7, "width": 0.3}
    proj = load_project(_minimal_v2(
        tmp_path, {"space": {"depth": 0.7, "send_db": -6.0}}))
    assert proj["tracks"][0]["space"] == {"depth": 0.7, "width": 0.3,
                                          "send_db": -6.0}
    # save 回写：space 键保全（含显式 send_db），不落 null；v1 落盘无 space 键
    rt = str(tmp_path / "rt.json")
    save_project(rt, proj)
    raw = json.load(open(rt))
    assert raw["tracks"][0]["space"] == {"depth": 0.7, "width": 0.3,
                                         "send_db": -6.0}
    assert "space_ir" not in raw["master"]
    proj1 = load_project(_minimal_v2(tmp_path, version="1"))
    save_project(str(tmp_path / "rt1.json"), proj1)
    raw1 = json.load(open(str(tmp_path / "rt1.json")))
    assert all("space" not in t for t in raw1["tracks"])
    assert "space_ir" not in raw1["master"]
