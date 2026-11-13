"""⑤旧脚本回归（PLAN.md §0 DoD5 / §5 R4-⑤；BG-D4：argv/cwd 契约冻结）。

[fast] gm_lofi / demo_lofi / midi2wav：子进程跑通（exit 0）+ 产物 sr/时长/RMS
       阈值（阈值取 R2 实测：gm 42.7s/-37.3dB、synth 39.4s、midi2wav 6s 级）。
       一律在临时 cwd 跑（契约=out/ 相对 cwd），不碰包内旧产物（红线④）。
[fast] key_check/bpm_check 对旧产物 out/gm_lofi_demo.wav 冒烟（只读）。
[service] gen.py：/tmp 副本改 duration 8（wf_ace15.json 94/98 节点），ComfyUI
       不可达→显式 skip；只验 exit 0 + 产物存在（AI 非确定性免 RMS，§0.5）。
"""

import json
import os
import sys
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

helpers.in_proc_env()
from bgm_gen.engines.base import measure_wav               # noqa: E402


def _legacy_cwd(name):
    """旧脚本契约 cwd：临时目录 + 预建 out/（产物相对 cwd 落盘，不碰包内 out/）。"""
    d = os.path.join(helpers.tmpdir("legacy"), name)
    os.makedirs(os.path.join(d, "out"), exist_ok=True)
    return d


def _run_legacy(script, args, cwd, timeout=600, script_dir=helpers.LEGACY_DIR):
    # 旧脚本 import 基点 = 仓库根（from bgm_gen.legacy.demo_lofi import ... /
    # from bgm_gen.engines…）；脚本本体在 legacy/ 或 analysis/ 子目录
    env = dict(os.environ)
    env["PYTHONPATH"] = helpers.WORKSPACE
    import subprocess
    return subprocess.run([helpers.py3(), os.path.join(script_dir, script), *args],
                          cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=timeout)


def _assert_wav(path, sr=44100, dur=None, dur_tol=1.0, rms_range=None):
    assert os.path.isfile(path) and os.path.getsize(path) > 0, f"产物缺失：{path}"
    m = measure_wav(path)
    assert m["sr"] == sr, f"{path} sr={m['sr']}"
    if dur is not None:
        assert abs(m["duration_s"] - dur) <= dur_tol, \
            f"{path} 时长 {m['duration_s']:.2f}s ∉ {dur}±{dur_tol}"
    if rms_range is not None:
        lo, hi = rms_range
        assert lo <= m["rms_db"] <= hi, f"{path} rms {m['rms_db']:.2f} ∉ [{lo},{hi}]"
    return m


# ------------------------------------------------------------------ fast
@pytest.mark.fast
def test_gm_lofi():
    cwd = _legacy_cwd("gm")
    r = _run_legacy("gm_lofi.py", [], cwd)
    assert r.returncode == 0, f"exit={r.returncode}: {r.stderr[-1200:]}"
    _assert_wav(os.path.join(cwd, "out", "gm_lofi_demo.wav"),
                dur=42.7, dur_tol=1.0, rms_range=(-38.3, -36.3))
    assert os.path.getsize(os.path.join(cwd, "out", "gm_lofi_demo.mid")) > 0


@pytest.mark.fast
def test_demo_lofi():
    cwd = _legacy_cwd("synth")
    r = _run_legacy("demo_lofi.py", [], cwd)
    assert r.returncode == 0, f"exit={r.returncode}: {r.stderr[-1200:]}"
    _assert_wav(os.path.join(cwd, "out", "synth_lofi_demo.wav"),
                dur=39.4, dur_tol=1.0, rms_range=(-20.0, -3.0))


@pytest.mark.fast
def test_midi2wav():
    cwd = _legacy_cwd("m2w")
    mid = os.path.join(cwd, "in.mid")
    from bgm_gen.engines.midi_util import write_notes_smf
    # 8 beat @ 80BPM = 6s 音符 + fluidsynth 释放尾巴（实测 ~2.8s）→ 6s 级 wav
    write_notes_smf(mid, [[60, 0.0, 8.0, 100], [67, 0.0, 8.0, 90]], bpm=80)
    wav = os.path.join(cwd, "out.wav")
    r = _run_legacy("midi2wav.py", [mid, wav], cwd)
    assert r.returncode == 0, f"exit={r.returncode}: {r.stderr[-1200:]}"
    _assert_wav(wav, dur=8.5, dur_tol=2.5, rms_range=(-60.0, 0.0))


@pytest.mark.fast
def test_key_bpm_check_on_frozen_product():
    """key_check/bpm_check 冒烟（只读旧产物，argv 契约冻结）。

    2026-09-09 仓库分家：out/ 全量迁至 music_project/out/——产物不在工具仓时
    显式 skip（与 test_smf_import 缺失即 skip 同口径），产物在侧时行为不变。
    """
    wav = os.path.join(helpers.OUT_DIR, "gm_lofi_demo.wav")
    if not os.path.isfile(wav):
        pytest.skip("out/ 已迁 music_project（2026-09-09 分家）："
                    "gm_lofi_demo.wav 不在工具仓 out/")
    for script, pat in (("key_check.py", r"[A-G]#?\s*(?:major|minor)"),
                        ("bpm_check.py", r"[0-9.]+\s*BPM")):
        r = _run_legacy(script, [wav], cwd=helpers.tmpdir("legacy"), timeout=300,
                        script_dir=helpers.ANALYSIS_DIR)
        assert r.returncode == 0, f"{script} exit={r.returncode}: {r.stderr[-800:]}"
        assert r.stdout.strip(), f"{script} 无输出"


# --------------------------------------------------------------- service
@pytest.mark.service
def test_gen_py_smoke():
    base = helpers.runtime().get("comfyui", {}).get("base_url", "http://127.0.0.1:8188")
    try:
        urllib.request.urlopen(f"{base}/system_stats", timeout=3)
    except Exception:
        pytest.skip(f"ComfyUI 不可达：{base}（service 档前置服务未启动，显式 skip 计数）")

    # /tmp 副本改 8s（94=duration / 98=seconds 节点，W2B 实证；gen.py argv 契约冻结）
    wf = helpers.load_json(os.path.join(helpers.WORKFLOWS_DIR, "wf_ace15.json"))
    wf["94"]["inputs"]["duration"] = 8
    wf["98"]["inputs"]["seconds"] = 8
    wf_path = os.path.join(helpers.tmpdir("gen"), "wf_r4smoke_8s.json")
    with open(wf_path, "w", encoding="utf-8") as f:
        json.dump(wf, f, ensure_ascii=False)

    cwd = _legacy_cwd("gen")                  # 产物落 cwd/out/（gen.py 契约）
    r = _run_legacy("gen.py", [wf_path], cwd, timeout=900)
    assert r.returncode == 0, f"gen.py exit={r.returncode}: {(r.stderr or '')[-1200:]}"
    outs = [fn for fn in os.listdir(os.path.join(cwd, "out"))
            if os.path.getsize(os.path.join(cwd, "out", fn)) > 1024]
    assert outs, f"gen.py 无有效产物（out/ 空）"
