"""②引擎层测试（PLAN.md §5 R4-②）。

[fast]  确定性引擎基线回归：baselines_w2a.json 逐条按 spec 指针重渲到 /tmp，
        断言 sr 相等、duration 偏差 ≤0.1s、rms ≥ assert_rms_floor_db（≥3dB 余量）、
        出参契约 7 字段——顺带覆盖 PLAN 协作协议第 3 条"轮间重跑冒烟集"。
[slow]  GPU 引擎：demucs 10s 四分轨（stem RMS 对 baselines_w2b，null 项只验
        存在+非零字节）；master loudnorm（ebur128 复测 -14±1）。
[service] ai 引擎 8s 小工作流（ComfyUI 8188 不可达 → 显式 pytest.skip 计数，
        绝不静默当绿）。
"""

import os
import sys
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

helpers.in_proc_env()
from bgm_gen.engines import EngineError, run_engine        # noqa: E402
from bgm_gen.engines.master import measure_lufs            # noqa: E402

CONTRACT_FIELDS = ("path", "sr", "channels", "duration_s", "rms_db",
                   "engine", "determinism")


@pytest.mark.fast
def test_default_out_lands_in_caller_workspace(monkeypatch, tmp_path):
    """缺省输出必须落仓库根 out/，绝不落包内（R1 审查 MAJOR 的回归锁：
    REPO_ROOT 推导曾少一层 dirname，恰好落在全部测试都显式 -o 的盲区）。"""
    from bgm_gen.engines.base import REPO_ROOT, default_out
    assert REPO_ROOT == helpers.WORKSPACE, (
        f"REPO_ROOT={REPO_ROOT} != 仓库根 {helpers.WORKSPACE}（dirname 层数错误？）")
    monkeypatch.chdir(tmp_path)
    p = default_out("smoke", "x", "y.wav")
    assert os.path.join(str(tmp_path), "out", "smoke", "x", "y.wav") == p
    assert not p.startswith(helpers.PKG_DIR + os.sep)


@pytest.mark.fast
def test_sfz_loop_watchdog_terminates_unbounded_render():
    """v3 R4 冻机事故回归锁：loop_continuous/超长 release 音色（VPO PERF 族
    实测行为：30s 写出 4.28GB、渲染不终止）必须被输出体积闸快速终止并报
    EngineError，不得留下超限半成品。"""
    helpers.require_engine_assets("sfz")
    import time as _time
    import wave as _wave
    import numpy as _np
    d = helpers.tmpdir("sfz_loop_guard")
    sr = 22050
    t = _np.arange(int(sr * 0.3)) / sr
    sig = (0.5 * _np.sin(2 * _np.pi * 440.0 * t) * 32767).astype("<i2")
    sample = os.path.join(d, "loop.wav")
    with _wave.open(sample, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(sig.tobytes())
    sfz = os.path.join(d, "loop.sfz")
    with open(sfz, "w", encoding="utf-8") as f:
        f.write(f"<region> sample=loop.wav lokey=59 hikey=61 pitch_keycenter=60 "
                f"loop_mode=loop_continuous loop_start=0 loop_end={len(sig)} "
                f"ampeg_release=100000\n")
    out = os.path.join(d, "out.wav")
    t0 = _time.time()
    with pytest.raises(EngineError) as ei:
        run_engine({"engine": "sfz", "spec_version": "1", "sr": 44100,
                    "payload": {"sfz": sfz, "bpm": 120.0,
                                "notes": [[60, 0, 2, 100]]},
                    "out": out})
    assert _time.time() - t0 < 60, "护栏必须快速终止（预期秒级）"
    assert ("无限循环" in str(ei.value)) or ("上限" in str(ei.value)), \
        str(ei.value)
    assert not os.path.exists(out), "超限半成品必须被清除"


def _w2a_baselines():
    return helpers.load_json(helpers.BASELINES_W2A)["baselines"]


# 位级 sha256 锚点的外部工具依赖（描述符 binary_pin 口径；用户裁定 2026-10-11
# 版本门控）：sf2=libfluidsynth 渲染+ffmpeg_match_wav 兜底；master=ffmpeg
# loudnorm 两遍法。synth/sample（纯 numpy/wave 直读，sr=44100 不触 ffmpeg）与
# sfz（sfizz_render，sha/size pin，无版本探针）不依赖这两个版本化二进制——
# 保持无条件严格执行，漂移破坏其字节时如实失败（新漂移证据，不属已知存量）。
_W2A_BITLEVEL_BIN_DEPS = {"sf2": ("fluidsynth", "ffmpeg"), "master": ("ffmpeg",)}


# ------------------------------------------------------------- [fast] 基线
@pytest.mark.fast
@pytest.mark.parametrize("b", _w2a_baselines(),
                         ids=lambda b: f"{b['engine']}.{b['case']}")
def test_w2a_baseline(b):
    helpers.require_fixture(os.path.join(helpers.WORKSPACE, b["spec"]))
    spec = helpers.load_json(os.path.join(helpers.WORKSPACE, b["spec"]))   # 基线指针含 out/ 前缀，拼仓库根
    out = os.path.join(helpers.tmpdir("w2a"), f"{b['engine']}_{b['case']}.wav")
    spec["out"] = out
    result = run_engine(spec)

    for k in CONTRACT_FIELDS:
        assert k in result, f"出参契约缺字段 {k}：{result.keys()}"
    assert result["engine"] == b["engine"]
    assert result["determinism"] == "deterministic"
    assert result["sr"] == b["sr"], (result["sr"], b["sr"])
    if spec.get("sr") is not None:
        assert result["sr"] == int(spec["sr"])          # BG-D5 口径
    assert result["channels"] == b["channels"]
    assert abs(result["duration_s"] - b["duration_s"]) <= 0.1, (
        f"时长偏差 {abs(result['duration_s'] - b['duration_s']):.3f}s > 0.1s")
    assert result["rms_db"] >= b["assert_rms_floor_db"], (
        f"rms {result['rms_db']} 低于冻结下限 {b['assert_rms_floor_db']}"
        f"（基线 {b['rms_db']:.2f}，3dB 余量）")
    assert os.path.getsize(result["path"]) > 0
    # 位级锚点（R4 审查 MINOR-2）：确定性引擎重渲必须与冻结 sha 一致——仅此断言段
    # 版本门控（红队复审 m-1 收窄：出参契约/sr/channels/时长/RMS 断言无条件执行）
    if b.get("sha256"):
        helpers.skip_if_binary_version_drift(*_W2A_BITLEVEL_BIN_DEPS.get(b["engine"], ()))
        got = helpers.sha256(result["path"])
        assert got == b["sha256"], (
            f"{b['engine']}.{b['case']} 位级漂移：{got} ≠ 冻结 {b['sha256']}")


# ------------------------------------------------------------- [slow] GPU
@pytest.mark.slow
def test_demucs_10s_four_stems():
    src = os.path.join(helpers.OUT_DIR, "smoke", "demucs", "demucs_in10s.wav")
    assert os.path.isfile(src), f"分轨输入缺失：{src}"
    out_dir = helpers.tmpdir("demucs")
    result = run_engine({"engine": "demucs", "spec_version": "1", "sr": 44100,
                         "payload": {"input": src, "out_dir": out_dir,
                                     "device": "auto"}})
    assert result["sr"] == 44100
    assert abs(result["duration_s"] - 10.031) <= 0.5

    floors = {e["case"].split("/", 1)[1]: e["assert_rms_floor_db"]
              for e in helpers.load_json(helpers.BASELINES_W2B)
              if e["engine"] == "demucs" and e["case"].startswith("smoke_10s/")}
    stems = result["stems"]
    assert set(stems) >= {"vocals", "drums", "bass", "other"}, stems.keys()
    for name, m in stems.items():
        assert os.path.getsize(m["path"]) > 0, f"stem 零字节：{name}"
        assert m["sr"] == 44100
        floor = floors.get(name)
        if floor is None:
            continue        # 基线注明等效静音 stem：只验存在性（W2B 口径）
        assert m["rms_db"] >= floor, (
            f"{name} rms {m['rms_db']} 低于下限 {floor}（tolerant 级 3dB 余量）")


@pytest.mark.slow
def test_master_loudnorm_ebur128():
    src = os.path.join(helpers.OUT_DIR, "smoke", "demucs", "demucs_in10s.wav")
    out = os.path.join(helpers.tmpdir("master"), "loudnorm_r4.wav")
    result = run_engine({"engine": "master", "spec_version": "1",
                         "payload": {"input": src, "mode": "loudnorm",
                                     "target_lufs": -14, "true_peak": -1,
                                     "out": out}})
    lufs = measure_lufs(out)["lufs"]                     # ebur128 独立复测
    assert abs(lufs - (-14.0)) <= 1.0, f"ebur128 复测 {lufs} ∉ -14±1"
    assert abs(result["lufs"] - lufs) <= 0.5             # 引擎出参与复测自洽
    assert result["true_peak_db"] is not None and result["true_peak_db"] <= -1.0 + 1e-6
    assert abs(result["duration_s"] - 10.031) <= 0.5


# ---------------------------------------------------------- [service] ai
def _comfyui_up(base):
    try:
        urllib.request.urlopen(f"{base}/system_stats", timeout=3)
        return True
    except Exception:
        return False


@pytest.mark.service
def test_ai_engine_8s_smoke():
    base = helpers.runtime().get("comfyui", {}).get("base_url", "http://127.0.0.1:8188")
    if not _comfyui_up(base):
        pytest.skip(f"ComfyUI 不可达：{base}（service 档前置服务未启动，显式 skip 计数）")
    result = run_engine({
        "engine": "ai", "spec_version": "1",
        "payload": {
            "workflow": os.path.join(helpers.WORKFLOWS_DIR, "wf_ace15.json"),
            "params": {"94.inputs.duration": 8, "98.inputs.seconds": 8,
                       "104s.inputs.filename_prefix": "r4smoke"},
            "timeout_s": 600,
            "out": helpers.tmpdir("ai") + "/"}})
    assert result["determinism"] == "nondeterministic"   # PLAN §0.5：AI 免 RMS 基线
    assert result["sr"] == 48000                          # SaveAudioMP3 恒 48000（W2B 实测）
    assert abs(result["duration_s"] - 8.0) <= 0.5, result["duration_s"]
    assert result["rms_db"] > -60                         # 非静音底线
    assert os.path.getsize(result["path"]) > 0
