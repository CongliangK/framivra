"""QC 测量器与对照报告（PLAN.md §5 R3 / §6.3）。

measure(path)：全指标测量（不动用工程上下文）
    - 无声窗口：分帧 RMS 能量闸（帧 50ms，帧 RMS<-60dB 计无声），报最长窗；
    - LUFS/真峰值：复用 engines/master.measure_lufs（ebur128 peak=true，
      只取 Summary 段——每 100ms 的行也含 "I: -xx LUFS" 且 t≈0 恒 -70，
      "Normalization Type:" 大小写两见，master 引擎已处理）；
    - >8kHz 能量占比：numpy 分帧功率谱累计（替代肉眼"削平"判定）；
    - 调性/BPM：子进程复用分析脚本 analysis/key_check.py / bpm_check.py
      （argv 契约冻结：python3 key_check.py <audio>，PLAN §0.5/红线⑤）。

qc_report(mix_path, project)：measure + 对照 master.qc 目标逐项 pass/fail。
null 目标 = info 项（记录不判过）。报告 ok = 无 fail 项。
"""

import json
import os
import re
import subprocess
import sys

import numpy as np

from ..engines.base import load_runtime, measure_wav
from ..engines.master import measure_lufs

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SILENT_FRAME_MS = 50.0
SILENT_THRESH_DB = -60.0
HF_CUTOFF_HZ = 8000.0
KEY_TOP1_R = 0.70            # §6.3：top-1 r>=0.70 或 top-2 命中
BPM_OCTAVES = (0.5, 1.0, 2.0)  # §6.3 口径：top-3 含 1/2/2 倍频命中即过


def _python(runtime=None):
    """跑旧脚本的解释器：优先 runtime.json 的 python_system（权威、带 numpy）。

    不直接信 sys.executable：实测在部分沙箱直呼环境下它会被改写成宿主
    AppImage 路径（Python 之外的可执行文件），spawn 出来的是电子应用。
    """
    rt = load_runtime() if runtime is None else runtime
    py = rt.get("python_system")
    if py and os.path.isfile(py):
        return py
    return sys.executable


# ------------------------------------------------------------------ 测量器

def _decode(path, sr=22050):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1",
                          "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def silent_window_stats(path, frame_ms=SILENT_FRAME_MS, thresh_db=SILENT_THRESH_DB):
    """最长无声窗（秒）/ 无声段数 / 无声总时长。帧 RMS<thresh 计无声。"""
    x = _decode(path)
    n = max(1, int(22050 * frame_ms / 1000.0))
    frames = len(x) // n
    if frames == 0:
        return {"longest_s": 0.0, "count": 0, "total_s": 0.0,
                "threshold_db": thresh_db, "frame_ms": frame_ms}
    e = x[:frames * n].reshape(frames, n)
    rms = np.sqrt(np.mean(np.square(e.astype(np.float64)), axis=1))
    silent = rms < 10 ** (thresh_db / 20.0)
    longest = count = 0
    cur = 0
    for s in silent:
        cur = cur + 1 if s else 0
        count += (s and cur == 1)
        longest = max(longest, cur)
    return {"longest_s": round(longest * frame_ms / 1000.0, 3),
            "count": int(count),
            "total_s": round(float(np.sum(silent)) * frame_ms / 1000.0, 3),
            "threshold_db": thresh_db, "frame_ms": frame_ms}


def hf_energy_ratio(path, cutoff_hz=HF_CUTOFF_HZ, nfft=8192, hop=2048):
    """>cutoff 能量占全带能量比例（分帧功率谱累计，hop=NFFT/4）。"""
    x = _decode(path)
    if len(x) < nfft:
        nfft, hop = 2048, 512
    frames = int((len(x) - nfft) // hop) + 1
    if frames <= 0:
        return 0.0
    idx = np.arange(nfft)[None, :] + hop * np.arange(frames)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(nfft), axis=1)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1 / 22050.0)
    power = spec.sum(axis=0)
    total = float(power.sum())
    if total <= 0:
        return 0.0
    return float(power[freqs > cutoff_hz].sum() / total)


def _run_script(script, path, runtime=None):
    r = subprocess.run([_python(runtime), os.path.join(PKG_DIR, "analysis", script), path],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return None, ((r.stderr or "") + (r.stdout or ""))[-400:]
    return (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else "", None


def detect_key(path, runtime=None):
    """key_check.py 输出：`name → C major(r=0.71) | A minor(r=0.65) | ...` → top-3。"""
    line, err = _run_script("key_check.py", path, runtime)
    if err:
        raise RuntimeError(f"key_check.py 失败：{err}")
    hits = re.findall(r"([A-G]#?(?:\s*(?:major|minor)))\(r=(-?[0-9.]+)\)", line or "")
    return [{"key": k.strip(), "r": float(r)} for k, r in hits[:3]]


def detect_bpm(path, runtime=None):
    """bpm_check.py 输出：`name  onsets=NNN   78.0 BPM (w=123) | ...` → top-3。"""
    line, err = _run_script("bpm_check.py", path, runtime)
    if err:
        raise RuntimeError(f"bpm_check.py 失败：{err}")
    hits = re.findall(r"([0-9.]+)\s*BPM\s*\(w=([0-9.]+)\)", line or "")
    return [{"bpm": float(b), "weight": float(w)} for b, w in hits[:3]]


def measure(path):
    """全指标测量汇总（duration/sr/channels/rms 来自 measure_wav）。"""
    m = measure_wav(path)
    lufs = measure_lufs(path)
    return {"path": os.path.abspath(path),
            "duration_s": round(m["duration_s"], 3),
            "sr": m["sr"], "channels": m["channels"], "rms_db": round(m["rms_db"], 2),
            "lufs": lufs["lufs"], "true_peak_db": lufs["true_peak_db"],
            "silent": silent_window_stats(path),
            "hf_ratio_gt8k": round(hf_energy_ratio(path), 5)}


# ------------------------------------------------------------------ 对照报告

def _item(value, target, ok, level, note=None):
    it = {"value": value, "target": target,
          "level": level, "pass": bool(ok) if level != "info" else None}
    if note:
        it["note"] = note
    return it


def qc_report(mix_path, project=None, runtime=None):
    """measure + 对照 project.master 目标。报告结构：
    {ok, mix, items: {name: {value,target,level,pass,note?}}, measured, generated_from}
    """
    if not os.path.isfile(mix_path):
        raise FileNotFoundError(f"输入文件不存在：{mix_path}")
    # Resolve once, before optional-analysis error handling: bad user runtime
    # must fail, while an explicit dict (including {}) never reloads defaults.
    runtime = load_runtime() if runtime is None else runtime
    meas = measure(mix_path)
    items = {}

    mst = (project or {}).get("master") or {}
    qc = mst.get("qc") or {}
    meta = (project or {}).get("meta") or {}
    lufs_tol = mst.get("lufs_tol", 1.0)

    t_lufs = mst.get("target_lufs", -14)
    items["lufs"] = _item(meas["lufs"], t_lufs,
                          abs(meas["lufs"] - t_lufs) <= lufs_tol, "pass",
                          f"|Δ|<={lufs_tol}")
    t_tp = mst.get("true_peak_db", -1)
    items["true_peak_db"] = _item(meas["true_peak_db"], f"<={t_tp}",
                                  meas["true_peak_db"] is not None
                                  and meas["true_peak_db"] <= t_tp + 1e-6, "pass")

    t_sil = qc.get("silent_window_max_s", 1.5)
    if t_sil is None:
        items["silent_window"] = _item(meas["silent"]["longest_s"], None, None, "info")
    else:
        items["silent_window"] = _item(meas["silent"]["longest_s"], f"<={t_sil}",
                                       meas["silent"]["longest_s"] <= t_sil, "pass",
                                       f"count={meas['silent']['count']}")

    items["hf_ratio_gt8k"] = _item(
        meas["hf_ratio_gt8k"], qc.get("hf_ratio_max"), None,
        "info" if qc.get("hf_ratio_max") is None else "pass",
        ">8kHz 能量占比（削平/噪声异常观察项）")
    if qc.get("hf_ratio_max") is not None:
        items["hf_ratio_gt8k"]["pass"] = meas["hf_ratio_gt8k"] <= qc["hf_ratio_max"]

    # BPM：top-3 口径，1/2/2 倍频命中即过（§6.3；bpm_check IOI 折叠对等幅节拍器
    # 78/156 并列的已知问题，断言按 top-3 写，PLAN §1）
    try:
        bpm_top = detect_bpm(mix_path, runtime=runtime)
    except Exception as e:                                  # noqa: BLE001
        bpm_top, bpm_err = [], str(e)
    else:
        bpm_err = None
    meas["bpm_top3"] = bpm_top
    tol = qc.get("bpm_tol_pct")
    want_bpm = meta.get("bpm")
    if tol is None or want_bpm is None:
        items["bpm"] = _item([b["bpm"] for b in bpm_top], want_bpm, None,
                             "info", bpm_err or "bpm_tol_pct/meta.bpm 缺省 → info")
    else:
        hit = any(abs(b["bpm"] - want_bpm * m) <= want_bpm * m * tol / 100.0
                  for b in bpm_top for m in BPM_OCTAVES)
        items["bpm"] = _item([b["bpm"] for b in bpm_top],
                             f"top-3 含 {want_bpm}±{tol}%（或 1/2/2 倍频）", hit,
                             "pass", bpm_err)

    # 调性：声明 key → 必须 top-2 命中；未声明 → top-1 r>=0.70 兜底
    # （R4 门控单测发现并修复：原 "top-1 r≥0.70 或 top-2 命中" 会在声明与实测
    #  不符时也放行；语义修订对齐 §6.3 意图——r≥0.70 只对未声明工程生效）
    try:
        key_top = detect_key(mix_path, runtime=runtime)
    except Exception as e:                                  # noqa: BLE001
        key_top, key_err = [], str(e)
    else:
        key_err = None
    meas["key_top3"] = key_top
    want_key = meta.get("key")
    if not qc.get("key_top2"):
        items["key"] = _item([k["key"] for k in key_top], want_key, None,
                             "info", key_err or "key_top2 缺省/false → info")
    elif not want_key:
        hit = bool(key_top) and key_top[0]["r"] >= KEY_TOP1_R
        items["key"] = _item([f"{k['key']}(r={k['r']:.2f})" for k in key_top],
                             f"top-1 r≥{KEY_TOP1_R}（未声明 key）", hit, "pass",
                             key_err)
    else:
        hit = any(k["key"].lower() == want_key.lower()
                  for k in key_top[:2])
        items["key"] = _item([f"{k['key']}(r={k['r']:.2f})" for k in key_top],
                             want_key, hit, "pass", key_err)

    # sr/channels 对齐 meta（BG-D5 硬口径）
    if project:
        items["sr"] = _item(meas["sr"], meta.get("sr"),
                            meas["sr"] == meta.get("sr"), "pass")
        items["channels"] = _item(meas["channels"], meta.get("channels"),
                                  meas["channels"] == meta.get("channels"), "pass")
    dmin = qc.get("duration_min_s")
    if dmin is None:
        items["duration_s"] = _item(meas["duration_s"], None, None, "info")
    else:
        items["duration_s"] = _item(round(meas["duration_s"], 3), f"≥{dmin}s",
                                    meas["duration_s"] >= dmin, "pass",
                                    "duration_min_s 目标（§6.3 ①，R4 审查 M2）")

    ok = all(it["pass"] is not False for it in items.values())
    return {"ok": ok, "mix": os.path.abspath(mix_path),
            "project": (project or {}).get("name"), "items": items,
            "measured": meas}


def write_report(report, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
