#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""levelnorm.py · TTS 占位语音峰值归一 -6dBFS（幂等）

来源：voice-ime/assets/voice_tts/normalize.py 内化（T4-S4 修复 P0-①）。
根因照录：IndexTTS2.5 克隆参考为 Resemble 降噪后的低电平本人语音，模型输出
raw 批次整体 mean -44~-51dB / peak -20~-34dBFS（生成链无任何缩放，atempo
也不改电平）。该电平直接进混音：人声被 bed（约 -25LUFS）淹没 25dB+，
sidechaincompress 阈值 0.03（-30.4dBFS）不触发、duck 失效。
规格：峰值归一到 -6dBFS（语音 RMS 约 -22±4dBFS）——明显高于 duck 阈值，
混音 vol 0.9 后清晰可闻，峰值余量在 loudnorm+alimiter 链内不削波。
确定性：纯峰值测量 + volume 差值应用，无随机成分；幂等（已在 -6±0.5dB
内则不动，二次运行增益=0）。
"""
import os
import subprocess

TARGET_DB = -6.0
IDLE_BAND_DB = 0.5   # 幂等带宽：|peak - 目标| < 此值不动（方案 §4.1 口径）
APPLY_EPS_DB = 0.05  # 增益小于此值视为无需应用（normalize.py 同款）


def peak_db(path: str) -> float:
    """ffmpeg volumedetect 测 max_volume（dBFS）。"""
    r = subprocess.run(["ffmpeg", "-i", path, "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True)
    for ln in r.stderr.splitlines():
        if "max_volume" in ln:
            return float(ln.split("max_volume:")[1].split("dB")[0])
    raise RuntimeError(f"✗ 无法测量峰值 {path}：{r.stderr[-200:]}")


def normalize_file(path: str, target_db: float = TARGET_DB) -> float:
    """单文件峰值归一（原位，.tmp + rename 原子落盘）。返回实际应用的增益 dB。

    幂等：实测峰已在 target±IDLE_BAND_DB 内 → 返回 0.0 不动文件。
    """
    p = peak_db(path)
    if abs(p - target_db) < IDLE_BAND_DB:
        return 0.0
    gain = round(target_db - p, 2)
    if abs(gain) < APPLY_EPS_DB:
        return 0.0
    tmp = path + ".norm.tmp"
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path,
                        "-af", f"volume={gain}dB", "-c:a", "pcm_s16le",
                        "-ar", "48000", "-ac", "1", "-f", "wav", tmp],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"✗ 归一失败 {path}：{r.stderr[:300]}")
    os.replace(tmp, path)
    p2 = peak_db(path)
    if abs(p2 - target_db) > 0.1:
        raise RuntimeError(f"✗ {path} 归一后 {p2}dB ≠ 目标 {target_db}dB")
    return gain


def normalize_dir(final_dir: str, ids=None, target_db: float = TARGET_DB):
    """final/ 目录批量峰值归一。返回 [(id, 峰值, 增益), ...]。"""
    rows = []
    names = sorted(n for n in os.listdir(final_dir)
                   if n.endswith(".wav") and not n.startswith("."))
    for n in names:
        wid = n[:-4]
        if ids and wid not in ids:
            continue
        f = os.path.join(final_dir, n)
        before = peak_db(f)
        gain = normalize_file(f, target_db)
        after = peak_db(f)
        rows.append((wid, before, gain))
        if gain:
            print(f"✓ {wid}: {before} → {after} dB（+{gain}）")
        else:
            print(f"✓ {wid}: peak {before} dB（已达标，未动）")
    n_adj = sum(1 for _, _, g in rows if g)
    print(f"\n归一完成：{n_adj} 件调整，目标峰值 {target_db} dBFS")
    return rows


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="TTS final 峰值归一 -6dBFS（幂等）")
    ap.add_argument("path", help="wav 文件或 final/ 目录")
    ap.add_argument("--target", type=float, default=TARGET_DB, help="目标峰值 dBFS")
    a = ap.parse_args()
    if os.path.isdir(a.path):
        normalize_dir(a.path, target_db=a.target)
    else:
        g = normalize_file(a.path, a.target)
        print(f"✓ {a.path}: peak {peak_db(a.path)} dB（增益 {g}）")
