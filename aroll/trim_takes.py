#!/usr/bin/env python3
"""trim_takes.py — 对提取的口播 take 做掐头去尾。

检测：300-3400Hz 带通（滤除 50/100/150Hz 工频哼声，避开其对静音判断的干扰）
      后按 1024/512 帧计算 RMS，帧级阈值 -48dBFS 判定语音活动。
裁剪：首个/末个活动帧外扩 head 150ms / tail 250ms 气口；中部气口不裁
      （留给 repack_voice.py 按静音谷切分）。输出保持 float32/44100Hz 原样。
"""
import glob
import json
import os
import sys

import numpy as np

RATE = 44100
FRAME, HOP = 1024, 512
TH = 10 ** (-48 / 20)
PAD_HEAD_MS, PAD_TAIL_MS = 150, 250


def load_f32wav(path):
    raw = open(path, 'rb').read()
    assert raw[:4] == b'RIFF' and raw[8:12] == b'WAVE' and raw[36:40] == b'data'
    return np.frombuffer(raw[44:], dtype='<f4').copy()


def write_f32wav(path, d, rate=RATE):
    import struct
    data = d.astype('<f4').tobytes()
    with open(path, 'wb') as f:
        f.write(b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVE')
        f.write(b'fmt ' + struct.pack('<IHHIIHH', 16, 3, 1, rate, rate * 4, 4, 32))
        f.write(b'data' + struct.pack('<I', len(data)))
        f.write(data)


def bandpass(d, lo=300, hi=3400):
    spec = np.fft.rfft(d)
    freqs = np.fft.rfftfreq(len(d), 1 / RATE)
    spec[(freqs < lo) | (freqs > hi)] = 0
    return np.fft.irfft(spec, len(d))


def trim_points(d):
    n = (len(d) - FRAME) // HOP + 1
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    rms = np.sqrt((d[idx] ** 2).mean(axis=1))
    act = np.where(rms > TH)[0]
    assert len(act) > 0, 'no active speech found'
    first_s = max(0, act[0] * HOP - PAD_HEAD_MS * RATE // 1000)
    last_s = min(len(d), (act[-1] * HOP + FRAME) + PAD_TAIL_MS * RATE // 1000)
    return first_s, last_s


def main(src_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    index = []
    for i, f in enumerate(sorted(glob.glob(os.path.join(src_dir, 'track*.wav'))), 1):
        d = load_f32wav(f)
        s, e = trim_points(bandpass(d))
        cut = d[s:e]
        track_name = os.path.basename(f).split('_', 1)[1][:-4].replace(' ', '')
        out = os.path.join(out_dir, f'take{i:02d}_{track_name}.wav')
        write_f32wav(out, cut)
        index.append({
            'file': os.path.basename(out),
            'source_track': track_name,
            'orig_samples': int(len(d)), 'orig_s': round(len(d) / RATE, 3),
            'head_cut_s': round(s / RATE, 3), 'tail_cut_s': round((len(d) - e) / RATE, 3),
            'new_samples': int(len(cut)), 'new_s': round(len(cut) / RATE, 3),
            'peak': round(float(np.abs(cut).max()), 4),
            'rms': round(float(np.sqrt(np.mean(cut ** 2))), 5),
        })
        print(f"{os.path.basename(out):28s} 原{len(d)/RATE:7.2f}s → 新{len(cut)/RATE:7.2f}s "
              f"(掐头 {s/RATE:.2f}s / 去尾 {(len(d)-e)/RATE:.2f}s)")
    with open(os.path.join(out_dir, 'takes_index.json'), 'w') as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print(f"\nwrote {len(index)} takes → {out_dir}")


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '.',
         sys.argv[2] if len(sys.argv) > 2 else '../../assets/voice_takes')
