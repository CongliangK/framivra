#!/usr/bin/env python3
"""bpm_check — onset 峰选 + IOI 折叠直方图测速（对稀疏瞬态/底噪材料比纯自相关稳）。
用法: bpm_check.py <audio>...  （每个文件给 top-3 候选）
"""
import subprocess, sys, numpy as np

SR, NFFT, HOP = 22050, 1024, 256
FPS = SR / HOP

def onset_env(path):
    raw = subprocess.run(["ffmpeg","-v","error","-i",path,"-ac","1","-ar",str(SR),
                          "-f","f32le","-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    n = int((len(x)-NFFT)//HOP)
    idx = np.arange(NFFT)[None,:] + HOP*np.arange(n)[:,None]
    S = np.abs(np.fft.rfft(x[idx]*np.hanning(NFFT), axis=1))
    return np.maximum(0, np.diff(S, axis=0)).sum(axis=1)

def analyze(path):
    env = onset_env(path)
    env = env / (env.max() + 1e-9)
    # 峰选：局部极大 且 高于滑动均值 + 0.08
    win = int(FPS * 0.5)
    mean = np.convolve(env, np.ones(win)/win, "same")
    peaks = [i for i in range(2, len(env)-2)
             if env[i] == max(env[i-2:i+3]) and env[i] > mean[i] + 0.08]
    # IOI 折叠：每对相邻 onset 的间隔折算成 50-200 BPM 候选（含倍频）
    votes = np.zeros(4001)
    for a, b in zip(peaks, peaks[1:]):
        ioi = (b - a) / FPS
        if ioi < 0.15 or ioi > 4.0:
            continue
        for mult in (0.25, 0.5, 1, 2, 4):
            bpm = 60.0 / ioi * mult
            if 50 <= bpm <= 200:
                votes[int(round(bpm*20))] += min(env[a], env[b])
    votes = np.convolve(votes, np.exp(-np.arange(-40, 41)**2 / 200), "same")
    order = np.argsort(votes)[::-1]
    kept = []
    for i in order:
        if all(abs(np.log2((i/20)/k)) > 0.09 for k, _ in kept):
            kept.append((i/20, votes[i]))
        if len(kept) == 3:
            break
    return kept, len(peaks)

if __name__ == "__main__":
    for p in sys.argv[1:]:
        kept, npk = analyze(p)
        print(f"{p.split('/')[-1]:28s} onsets={npk:4d}  " +
              " | ".join(f"{b:6.1f} BPM (w={w:.0f})" for b, w in kept))
