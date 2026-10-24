#!/usr/bin/env python3
"""双系统音频同步（无标记）sync_audio.py v1（2026-08-26 定案，D26）

相机轨（同步参考，可直接传视频文件）× 话筒轨 → 滑动窗 GCC-PHAT 互相关
→ 稳健线性漂移拟合 → soxr 微重采样，conform 出与相机时钟对齐的话筒 WAV。
视频永远是不动的一方（帧不能重采样），所以 conform 的总是音频。

用法:
  ~/miniconda3/envs/denoise/bin/python sync_audio.py <camera.(wav|mp4|mov)> <mic.wav> <out.wav> \
      [--max-offset 30] [--win 20] [--hop 10] [--search 2] [--report report.json]

判定: conform 后复验残差 max < 1.5ms 达标（目标 <1ms，对应 vtx 1ms 音频网格）；
异常窗（拟合离群点）= 素材掉样/跳变报警，建议人工听读该时间点。
原理与经验记录: voice-ime/07-shooting-guide.md §七「双系统同步」；拍掌标记已废除。
依赖: denoise conda 环境（numpy/scipy/soundfile/soxr）+ 系统 ffmpeg。
"""
import argparse, importlib, json, os, subprocess, sys, tempfile
from pathlib import Path

EXIT_MISSING_DEP = 3  # 依赖缺失专用退出码（与 argparse 参数错=2、运行期报错=1 区分；A4）

try:
    import numpy as np
except ImportError:   # 不让 --help 失败；main() 入口统一给中文指引后退出（A4）
    np = None

SR = 48000  # 统一工作采样率（与 vtx/契约一致）
CONF_MIN = 3.0      # 峰旁比置信度门限（峰/相关序列中位绝对值）
ENERGY_FRAC = 0.15  # 语音窗能量门限（占最高窗 RMS 的比例）


def _denoise_interpreter():
    """缺依赖时的解释器指引（A4）：config.local.json denoise.python（如有）优先，
    其次本机默认约定路径 ~/miniconda3/envs/denoise/bin/python；均不存在则回退通用文案。
    返回 (路径或 None, 来源标签)——路径存在性由调用方判断，绝不硬编码只在某机可用的路径。"""
    cfg_py = None
    cfg_path = os.environ.get("VTX_CONFIG") or (
        Path(__file__).resolve().parents[2] / "config.local.json")
    try:
        with open(os.path.expanduser(cfg_path), encoding="utf-8") as f:
            config = json.load(f)
        denoise = config.get("denoise") if isinstance(config, dict) else None
        p = denoise.get("python") if isinstance(denoise, dict) else None
        if isinstance(p, str) and p.strip():
            cfg_py = os.path.expanduser(p.strip())
    except (OSError, ValueError):
        pass  # 无 config.local.json / 坏 JSON：按空配置继续（与仓内 config 加载策略一致）
    default_py = os.path.expanduser("~/miniconda3/envs/denoise/bin/python")
    for p, src in ((cfg_py, "config"), (default_py, "default")):
        if p and os.path.isfile(p):
            return p, src
    return (cfg_py or default_py), "absent"


def _dep_guidance(missing):
    """缺依赖 → 中文指引：缺什么、用哪个解释器跑、pip 备选。"""
    lines = [f"sync_audio: 缺少第三方依赖：{', '.join(missing)}",
             "本工具须在 conda denoise 环境（numpy/scipy/soundfile/soxr）中运行，推荐："]
    py, src = _denoise_interpreter()
    example = "apps/sync/sync_audio.py <camera.(wav|mp4|mov)> <mic.wav> <out.wav>"
    if src == "config":
        lines.append(f"  {py} {example}   # config.local.json denoise.python 已配置的解释器")
    elif src == "default":
        lines.append(f"  {py} {example}   # 本机已安装 denoise 环境")
    else:
        lines.append(f"  ~/miniconda3/envs/denoise/bin/python {example}   "
                     "# 默认约定路径（本机未检测到，需先创建 denoise 环境）")
    lines.append(f"或给当前解释器补装：{sys.executable or 'python3'} -m pip install {' '.join(missing)}")
    return "\n".join(lines)


def require_deps():
    """入口统一探测第三方依赖（A4）：numpy 已在模块顶尝试导入；scipy/soundfile/soxr
    在此预探测（原本深处 bandpass/load_mono/conform 的懒 import，缺库要到 ffmpeg 转码
    之后才炸裸 traceback）。置于 argparse 之后 → --help 永不因缺依赖失败
    （app.json health.command = `sync_audio.py --help`，系统解释器无 scipy）。"""
    missing = []
    if np is None:
        missing.append("numpy")
    for mod in ("scipy", "soundfile", "soxr"):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print(_dep_guidance(missing), file=sys.stderr)
        sys.exit(EXIT_MISSING_DEP)


def load_mono(path):
    """任意容器 → mono float32 @48k（ffmpeg 统一转码；视频文件直接传，自动抽轨）"""
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "a.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path),
                        "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", wav], check=True)
        import soundfile as sf
        x, sr = sf.read(wav, dtype="float32")
        assert sr == SR, f"unexpected sr {sr}"
        return x


def bandpass(x):
    """300–3400Hz 带通：滤 50Hz 哼声/低频隆隆与两链路高频音色差，保留语音相关主体"""
    from scipy.signal import butter, sosfiltfilt
    sos = butter(4, [300.0, 3400.0], btype="band", fs=SR, output="sos")
    return sosfiltfilt(sos, x)


def xcorr_peak(m, c):
    """GCC-PHAT：短参考 m 在长搜索段 c 中的起始位置。
    返回 (lag_samples 含抛物线亚采样插值, confidence=峰/中位绝对值)。"""
    nfft = 1 << ((len(m) + len(c)) - 1).bit_length()
    C = np.fft.rfft(c, nfft)
    M = np.fft.rfft(m, nfft)
    R = C * np.conj(M)                      # r[k] = Σ c[n]·m[n-k]，k = m 在 c 中的起点（正 lag 从下标 0 起）
    mag = np.abs(R)
    # PHAT 相对门限白化：静带 bin（任一侧无能量的频点）置零而非除小数放大——
    # 绝对地板值(1e-10)会把带外残渣放大成随机相量海啸淹没真峰（合成自测实测教训）
    eps = 1e-3 * float(mag.max())
    w = np.where(mag > eps, 1.0 / np.where(mag > eps, mag, 1.0), 0.0)
    r = np.fft.irfft(R * w, nfft)
    n_valid = len(c) - len(m) + 1
    r = r[:n_valid]
    k = int(np.argmax(r))
    peak = float(r[k])
    frac = 0.0
    if 0 < k < len(r) - 1:                 # 抛物线亚采样插值
        y0, y1, y2 = r[k - 1], r[k], r[k + 1]
        den = y0 - 2 * y1 + y2
        if abs(den) > 1e-12:
            frac = float(np.clip(0.5 * (y0 - y2) / den, -1.0, 1.0))
    med = float(np.median(np.abs(r)))
    return k + frac, (peak / med if med > 0 else 0.0)


def window_starts(n, win_s, hop_s):
    W, H = int(win_s * SR), int(hop_s * SR)
    if n <= W:
        return [0], W
    return list(range(0, n - W + 1, H)) or [0], W


def collect_offsets(mic, cam, d_pred, search_s, win_s, hop_s):
    """滑动窗测 d(t) = 相机时刻 - 话筒时刻（以各窗起点计）。
    mic/cam 须已带通。返回 [{'t','d','conf'}...]（只含有语音且置信度达标的窗）。"""
    starts, W = window_starts(len(mic), win_s, hop_s)
    S = int(search_s * SR)
    rms = np.array([float(np.sqrt(np.mean(mic[st:st + W] ** 2))) for st in starts])
    gate = max(1e-4, ENERGY_FRAC * float(rms.max()))
    out = []
    for st, r in zip(starts, rms):
        if r < gate:
            continue
        t0 = st / SR
        cam0 = t0 + d_pred
        c0 = max(0, int(round(cam0 * SR - S)))
        c1 = min(len(cam), int(round(cam0 * SR - S)) + W + 2 * S)
        if c1 - c0 < W + min(2 * S, SR // 10):   # 钳位后搜索余量至少保留 min(2S, 0.1s)
            continue
        lag, conf = xcorr_peak(mic[st:st + W], cam[c0:c1])
        if conf < CONF_MIN:
            continue
        out.append({"t": t0, "d": (c0 + lag) / SR - t0, "conf": round(conf, 1)})
    return out


def bootstrap_d(mic, cam, max_off_s, win_s):
    """首轮：取能量最高的 mic 窗，在 ±max_offset 全范围搜初始偏移。"""
    starts, W = window_starts(len(mic), win_s, max(win_s / 2, 5))
    rms = [float(np.sqrt(np.mean(mic[st:st + W] ** 2))) for st in starts]
    st = starts[int(np.argmax(rms))]
    S = int(max_off_s * SR)
    c0 = max(0, int(round((st / SR) * SR - S))); c1 = min(len(cam), c0 + W + 2 * S)
    if c1 - c0 < W:
        raise SystemExit("相机轨太短，无法搜索初始偏移")
    lag, conf = xcorr_peak(mic[st:st + W], cam[c0:c1])
    if conf < CONF_MIN:
        raise SystemExit(f"初始对齐置信度不足（{conf:.1f}）：两轨内容可能不对应，"
                         f"或加大 --max-offset（当前 {max_off_s}s）")
    return (c0 + lag) / SR - st / SR, conf


def robust_fit(wins):
    """d(t)=a+b·t 稳健拟合：3 轮 MAD 离群剔除。返回 (a, b, 保留窗, 离群窗)。"""
    kept, flagged = list(wins), []
    a = b = None
    for _ in range(3):
        t = np.array([w["t"] for w in kept]); d = np.array([w["d"] for w in kept])
        if len(t) < 3:
            break
        b, a = np.polyfit(t, d, 1)
        res = d - (a + b * t)
        mad = max(float(np.median(np.abs(res - np.median(res)))) * 1.4826, 1e-6)
        keep = np.abs(res) <= max(3 * mad, 0.001)     # 残差 >3MAD 或 >1ms 判离群
        flagged += [w for w, k in zip(kept, keep) if not k]
        kept = [w for w, k in zip(kept, keep) if k]
        if len(kept) < 3:
            break
    if a is None or len(kept) < 3:
        raise SystemExit(f"可用拟合窗不足（{len(kept)}）：语音覆盖太少或置信度低，"
                         "试减小 --win 或确认两轨为同一场录制")
    return float(a), float(b), kept, flagged


def conform(mic, a, b, n_out):
    """输出 out(t) = mic(γ(t-a))，γ=1/(1+b)：先 soxr 时域伸缩（ppm 级，音高变化 <0.2 音分），
    再按 a 平移（正=话筒晚于相机 → 前端补零）。n_out 对齐相机轨长度。"""
    import soxr
    g = 1.0 / (1.0 + b)
    warped = soxr.resample(np.asarray(mic, dtype=np.float32), SR, SR / g)
    shift = int(round(a * SR))
    out = np.zeros(n_out, dtype=np.float32)
    if shift >= 0:
        seg = warped[:max(0, n_out - shift)]
        out[shift:shift + len(seg)] = seg
    else:
        seg = warped[-shift:-shift + n_out]
        out[:len(seg)] = seg
    return out


def verify(out, cam, win_s, k=5):
    """conform 后复验：内部窗（跳过首尾——边缘窗两端内容覆盖不对齐有伪残差）搜 ±100ms，残差应≈0。"""
    starts, W = window_starts(len(out), win_s, max(win_s, 10))
    idx = set(np.linspace(0, len(starts) - 1, min(k, len(starts))).astype(int))
    interior = range(1, len(starts) - 1) if len(starts) > 2 else range(len(starts))
    idx = sorted(idx & set(interior)) or list(interior)
    want = {round(starts[i] / SR, 1) for i in idx}
    wins = collect_offsets(out, cam, 0.0, 0.1, win_s, win_s)
    return [w["d"] for w in wins if round(w["t"], 1) in want] or [w["d"] for w in wins]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camera"); ap.add_argument("mic"); ap.add_argument("out")
    ap.add_argument("--max-offset", type=float, default=30, help="两设备启动时差上限(秒)")
    ap.add_argument("--win", type=float, default=20, help="相关窗长(秒)")
    ap.add_argument("--hop", type=float, default=10, help="滑窗步距(秒)")
    ap.add_argument("--search", type=float, default=2, help="逐窗搜索半径(秒)")
    ap.add_argument("--report", default=None, help="JSON 报告输出路径")
    args = ap.parse_args()
    require_deps()

    mic = load_mono(args.mic)
    cam = load_mono(args.camera)
    micb, camb = bandpass(mic), bandpass(cam)

    d0, c0 = bootstrap_d(micb, camb, args.max_offset, args.win)
    wins = collect_offsets(micb, camb, d0, args.search, args.win, args.hop)
    a, b, kept, flagged = robust_fit(wins)
    out = conform(mic, a, b, len(cam))          # conform 用未带通原始话筒轨（音质无损）

    import soundfile as sf
    sf.write(args.out, out, SR, subtype="PCM_16")

    vres = verify(bandpass(out), camb, args.win)
    vres_max = max(abs(x) for x in vres) if vres else float("nan")
    dur = len(cam) / SR
    drift_total = b * dur * 1000
    fit_res_max = max(abs(w["d"] - (a + b * w["t"])) for w in kept) * 1000
    # 阶跃型破坏（相机掉样/跳变）不会被 MAD 离群剔除抓到（半数窗整体位移=换斜率），
    # 但拟合残差绝对值会大到离谱（正常 <1ms，掉样 ≥ 数十 ms）→ 用残差门槛兜底
    step_broken = fit_res_max > 10.0
    ok = (not np.isnan(vres_max)) and vres_max < 0.0015 and len(kept) >= 3 and not step_broken

    rep = {
        "a_offset_s": round(a, 6), "b_drift": b, "drift_ppm": round(b * 1e6, 2),
        "drift_over_clip_ms": round(drift_total, 2),
        "windows_total": len(wins), "windows_used": len(kept),
        "fit_residual_ms": {"max": round(fit_res_max, 3)},
        "step_suspect": step_broken,
        "outliers": [{**w, "dev_ms": round((w["d"] - (a + b * w["t"])) * 1000, 1)} for w in flagged],
        "verify_residual_ms": [round(x * 1000, 3) for x in vres],
        "verify_residual_max_ms": round(vres_max * 1000, 3),
        "verdict": "OK" if ok else "CHECK",
    }
    if args.report:
        with open(args.report, "w") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
    print(f"偏移 a = {a*1000:.1f} ms｜漂移 b = {b*1e6:+.2f} ppm（{dur:.0f}s 素材累计 {drift_total:+.1f} ms）")
    print(f"拟合窗 {len(kept)}/{len(wins)}｜离群报警 {len(flagged)} 窗"
          + (f"（疑似掉样/跳变 @ " + ", ".join(f"{w['t']:.0f}s" for w in flagged) + "）" if flagged else ""))
    print(f"conform 后复验残差 max = {vres_max*1000:.2f} ms"
          + ("｜⚠ 拟合残差 " + f"{fit_res_max:.0f}ms 异常：疑似掉样/跳变" if step_broken else "")
          + f" → {'✅ OK' if ok else '⚠ CHECK（建议人工听验离群点）'}")
    print(f"OK -> {args.out}")


if __name__ == "__main__":
    main()
