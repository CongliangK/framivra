"""Measure current WAV; validate before replacing and invalidate consumers.

v2（P1-1，2026-09-13）：响度终链（增益 + alimiter + 复测校验 + 时长校验 + 回执
post_loudness 字段）已并入 vtx audio（ARCHITECTURE §1.2）——`vtx audio` 产出的
out/audio.wav 恒为「归一+限幅」终态，R4 链（all --until video → mux → qc）不再
需要本件。本文件保留 argparse 命令面作兼容薄壳：实现改为调内核兼容函数
vtx.stages.post_loudness_compat。**幂等性声明（P1 对抗审查 F1 修正）**：真幂等
仅当 ①audio.wav 与回执 post_loudness.sha256 一致（终态，直接跳过）或 ②当前 I
已在 target；对限幅损耗停在 target-1.5~-2 的产物重跑会按 v1 旧语义再推增益
（实测 +1.9dB 蠕升）并改变交付响度——函数会打印蠕升警告，v2 正常链路无需
本命令。不做 v1 的回执摘除-回写手术（v2 单写者 = vtx audio，audio 阶段回执
照常有效）。post_loudness.sh 照旧调本件。
"""
import argparse, json, os, sys, wave
from pathlib import Path

# v2 §1.4：注入 repo 根（不再注入 repo/vtx——平铺/包双实例会让锁形同虚设）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vtx.stages import post_loudness_compat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', nargs='?', default='.')
    parser.add_argument('--ffmpeg', default=os.environ.get('FFMPEG', 'ffmpeg'))
    parser.add_argument('--target', type=float, default=float(os.environ.get('TARGET_LUFS', '-14')))
    args = parser.parse_args()
    try:
        print(json.dumps(post_loudness_compat(args.project, args.ffmpeg, args.target), ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, KeyError, wave.Error) as exc:
        print(f'FATAL: {exc}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
