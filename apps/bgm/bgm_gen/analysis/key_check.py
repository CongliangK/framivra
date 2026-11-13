#!/usr/bin/env python3
"""chroma + Krumhansl-Kessler 调性检测：验证生成音频对请求 keyscale 的遵守度。"""
import subprocess, sys, numpy as np

SR, NFFT, HOP = 22050, 8192, 2048
KK_MAJ = np.array([6.35,2.23,3.48,2.33,4.67,2.39,3.34,6.71,2.34,2.98,3.34,2.52])
KK_MIN = np.array([6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])
NAMES = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']

def chroma(path):
    raw = subprocess.run(["ffmpeg","-v","error","-i",path,"-ac","1","-ar",str(SR),
                          "-f","f32le","-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    frames = int((len(x) - NFFT) // HOP)
    idx = np.arange(NFFT)[None,:] + HOP * np.arange(frames)[:,None]
    S = np.abs(np.fft.rfft(x[idx] * np.hanning(NFFT), axis=1))
    freqs = np.fft.rfftfreq(NFFT, 1/SR)
    keep = (freqs >= 65) & (freqs <= 2000)
    S, fr = S[:,keep], freqs[keep]
    pitch = np.round(12 * np.log2(fr/440) + 69).astype(int) % 12
    c = np.zeros(12)
    for t in range(len(S)):
        c += np.bincount(pitch, weights=S[t], minlength=12)
    return c / c.sum()

def top_keys(c, n=3):
    scores = []
    for root in range(12):
        r = np.roll(c, -root)
        scores.append((np.corrcoef(r, KK_MAJ)[0,1], f"{NAMES[root]} major"))
        scores.append((np.corrcoef(r, KK_MIN)[0,1], f"{NAMES[root]} minor"))
    scores.sort(reverse=True)
    return scores[:n]

if __name__ == "__main__":
    for path in sys.argv[1:]:
        print(path.split('/')[-1], "→", " | ".join(f"{k}(r={r:.2f})" for r,k in top_keys(chroma(path))))
