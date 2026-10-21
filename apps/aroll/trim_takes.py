#!/usr/bin/env python3
"""trim_takes.py — 对提取的口播 take 做掐头去尾。

检测：300-3400Hz 带通（滤除 50/100/150Hz 工频哼声，避开其对静音判断的干扰）
      后按 1024/512 帧计算 RMS，帧级阈值 -48dBFS 判定语音活动。
裁剪：首个/末个活动帧外扩 head 150ms / tail 250ms 气口；中部气口不裁
      （留给 repack_voice.py 按静音谷切分）。输出保持 float32 与输入采样率。
"""
import argparse
import glob
import json
import os
import sys

import numpy as np
from wav_float32 import read_wav_float32, write_wav_float32

FRAME, HOP = 1024, 512
TH = 10 ** (-48 / 20)
PAD_HEAD_MS, PAD_TAIL_MS = 150, 250


def bandpass(d, rate, lo=300, hi=3400):
    spec = np.fft.rfft(d)
    freqs = np.fft.rfftfreq(len(d), 1 / rate)
    spec[(freqs < lo) | (freqs > hi)] = 0
    return np.fft.irfft(spec, len(d))


def trim_points(d, rate):
    n = (len(d) - FRAME) // HOP + 1
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    rms = np.sqrt((d[idx] ** 2).mean(axis=1))
    act = np.where(rms > TH)[0]
    assert len(act) > 0, 'no active speech found'
    first_s = max(0, act[0] * HOP - PAD_HEAD_MS * rate // 1000)
    last_s = min(len(d), (act[-1] * HOP + FRAME) + PAD_TAIL_MS * rate // 1000)
    return first_s, last_s


def main(src_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    index = []
    for i, f in enumerate(sorted(glob.glob(os.path.join(src_dir, 'track*.wav'))), 1):
        d, rate = read_wav_float32(f)
        s, e = trim_points(bandpass(d, rate), rate)
        cut = d[s:e]
        track_name = os.path.basename(f).split('_', 1)[1][:-4].replace(' ', '')
        out = os.path.join(out_dir, f'take{i:02d}_{track_name}.wav')
        write_wav_float32(out, cut, rate)
        index.append({
            'file': os.path.basename(out),
            'source_track': track_name,
            'orig_samples': int(len(d)), 'orig_s': round(len(d) / rate, 3),
            'head_cut_s': round(s / rate, 3), 'tail_cut_s': round((len(d) - e) / rate, 3),
            'new_samples': int(len(cut)), 'new_s': round(len(cut) / rate, 3),
            'peak': round(float(np.abs(cut).max()), 4),
            'rms': round(float(np.sqrt(np.mean(cut ** 2))), 5),
        })
        print(f"{os.path.basename(out):28s} 原{len(d)/rate:7.2f}s → 新{len(cut)/rate:7.2f}s "
              f"(掐头 {s/rate:.2f}s / 去尾 {(len(d)-e)/rate:.2f}s)")
    with open(os.path.join(out_dir, 'takes_index.json'), 'w') as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print(f"\nwrote {len(index)} takes → {out_dir}")


def build_arg_parser():
    """统一参数面（A4）：位置参数顺序与缺省值与旧版 sys.argv 手工解析完全一致；
    --help/-h 由 argparse 提供中文描述。"""
    ap = argparse.ArgumentParser(
        prog='trim_takes.py',
        description='对 aup3_extract 提取的口播 take 做掐头去尾（300-3400Hz 带通 RMS VAD + 气口外扩）',
        epilog='扫描 <src_dir>/track*.wav，输出 take*.wav 与 takes_index.json 到 <out_dir>')
    ap.add_argument('src_dir', nargs='?', default='.', help='提取输出目录（含 track*.wav；默认当前目录）')
    ap.add_argument('out_dir', nargs='?', default='../../assets/voice_takes',
                    help='take 输出目录（默认 ../../assets/voice_takes）')
    return ap


if __name__ == '__main__':
    _args = build_arg_parser().parse_args()
    main(_args.src_dir, _args.out_dir)
