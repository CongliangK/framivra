#!/usr/bin/env python3
"""实测生成音频的 BPM 与时长：谱通量 onset 包络 + 自相关，对照工作流请求值。"""
import subprocess, sys, numpy as np

SR, HOP, FPS = 22050, 256, 22050 / 256

def analyze(path):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    dur = len(x) / SR
    # STFT 谱通量
    nfft, win = 1024, np.hanning(1024)
    frames = int((len(x) - nfft) // HOP)
    idx = np.arange(nfft)[None, :] + HOP * np.arange(frames)[:, None]
    S = np.abs(np.fft.rfft(x[idx] * win, axis=1))
    flux = np.maximum(0, np.diff(S, axis=0)).sum(axis=1)
    flux -= flux.mean()
    # 自相关，扫 50-200 BPM，抛物线插值
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    ac /= ac[0] + 1e-9
    cands = []
    for bpm100 in range(5000, 20000):
        lag = 60 * FPS / (bpm100 / 100)
        if lag >= len(ac) - 2:
            continue
        i = int(lag)
        y0, y1, y2 = ac[i - 1], ac[i], ac[i + 1]
        delta = (y0 - y2) / (2 * (y0 - 2 * y1 + y2) + 1e-12)
        cands.append((y1 - 0.25 * (y0 - y2) * delta, bpm100 / 100))
    cands.sort(reverse=True)
    # 贪心去倍频/半频重叠
    kept = []
    for score, bpm in cands:
        if all(abs(np.log2(bpm / k[1])) > 0.09 for k in kept):
            kept.append((score, bpm))
        if len(kept) == 3:
            break
    return dur, kept

REQUESTS = {  # 文件名前缀: (请求bpm, 请求时长s, 说明)
    "ace15_lofi_test": (78, 180, "turbo lo-fi"),
    "ace15_ambient": (60, 180, "turbo ambient"),
    "ace15_synthwave": (100, 180, "turbo synthwave"),
    "piano_base": (76, 180, "base50 钢琴"),
    "piano_xl_4b": (76, 180, "XL+4B 钢琴"),
    "music3_lofi_v3": (None, 150, "Music3 caption式"),
}

for prefix, (rbpm, rdur, note) in REQUESTS.items():
    path = sys.argv[1].rstrip("/") + "/" + prefix + "_00001." + ("flac" if "piano" in prefix else "mp3")
    try:
        dur, kept = analyze(path)
    except Exception as e:
        print(f"{prefix}: FAIL {e}")
        continue
    top = ", ".join(f"{b:.1f}BPM(ρ={s:.2f})" for s, b in kept)
    print(f"{prefix:18s} [{note}] 请求bpm={rbpm} 时长请求={rdur}s 实测={dur:.1f}s")
    print(f"{'':18s} 实测BPM候选: {top}")
