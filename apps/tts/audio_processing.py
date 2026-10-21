#!/usr/bin/env python3
"""TTS final-audio processing: fit duration, then normalize peak level.

Tempo is raw_duration / budget (never its inverse); the target is exact through
padding/trimming to the frame budget. Model duration_factor is a different,
inversely directed control. Peak normalization targets -6 dBFS with an idle
band, so quiet synthesized speech can drive the downstream ducking threshold.
These are the shared primitives used by the TTS worker and maintenance CLI.
"""
import argparse
import math
import os
import subprocess

MIN_TEMPO = 0.5
MAX_TEMPO = 2.0


def atempo_chain(raw_s: float, budget_s: float) -> str:
    """纯函数：实测时长 raw_s 贴预算 budget_s 所需的 atempo 滤镜串。

    返回值口径：
      - 返回 ""（空串）= 无需滤镜——|raw/budget - 1| < 1e-9 时输出时长已等于
        预算，调用方直接跳过 -af（应用器 apply_atempo 同口径；返回空而非
        "atempo=1.0" 是为彻底不动音频流）。
      - 域内单级 "atempo=1.199475"；域外 n 级同值串联 "atempo=√r,atempo=√r"
        （更极端依次再拆，n 级全同值）。
    """
    if not math.isfinite(raw_s) or not math.isfinite(budget_s) or raw_s <= 0 or budget_s <= 0:
        raise ValueError(f"时长非法：raw={raw_s}s budget={budget_s}s")
    r = raw_s / budget_s
    if not math.isfinite(r) or r <= 0:
        raise ValueError(f"时长比非法：raw={raw_s}s budget={budget_s}s")
    if abs(r - 1.0) < 1e-9:
        return ""
    n = 1
    while True:
        seg = r ** (1.0 / n)
        if MIN_TEMPO <= seg <= MAX_TEMPO:
            one = f"atempo={seg:.6f}"
            return one if n == 1 else ",".join([one] * n)
        n += 1


def apply_atempo(raw_path: str, budget_s: float, out_path: str,
                 raw_s: float = None) -> float:
    """对 raw 音频套 atempo_chain 并落 final 约定格式（48kHz/mono/pcm_s16le）。

    raw_s 缺省现场 ffprobe 实测。返回输出时长（秒）。链无需滤镜时仍做
    重采样/单声道/s16 转码（保证 final 格式契约），音频流零变速。

    精确落点（2026-09-11 集成验收发现）：atempo 滤镜内部按帧重采样，输出
    时长有 ~0.3% 样本量化漂移（实测 7.5989s vs 7.62 预算，占位态少 1 帧
    被 vtx validate 拦）。故输出统一 `apad`（无限尾静音）+ `-t budget`：
    长了裁、短了补，输出样本数恰为 round(budget×48000)。尾部 ≤20ms 静音
    与「链帧+20ms 句尾保护」语义一致；峰值归一按 max_volume 计，静音尾巴
    不影响 levelnorm 结果（先落点后归一两可，本链保持先落点）。
    """
    if raw_s is None:
        raw_s = probe_duration(raw_path)
    chain = atempo_chain(raw_s, budget_s)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", raw_path,
           "-filter:a", (chain + ",apad") if chain else "apad",
           "-t", f"{budget_s:.6f}",
           "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"atempo 贴预算失败：{r.stderr[:300]}")
    return probe_duration(out_path)


def probe_duration(path: str) -> float:
    """ffprobe 容器时长（秒）。"""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffprobe 失败 {path}：{r.stderr[:200]}")
    return float(r.stdout.strip())


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    tempo = commands.add_parser("tempo", help="compute the duration-fitting filter without writing audio")
    tempo.add_argument("raw_s", type=float, help="实测时长（秒）")
    tempo.add_argument("budget_s", type=float, help="窗口预算（秒）")
    normalize = commands.add_parser("normalize", help="WRITES: normalize an existing WAV or directory")
    normalize.add_argument("path", help="wav 文件或 final/ 目录")
    normalize.add_argument("--target", type=float, default=TARGET_DB, help="目标峰值 dBFS")
    args = parser.parse_args(argv)
    if args.command == "tempo":
        chain = atempo_chain(args.raw_s, args.budget_s)
        print(f"✓ r={args.raw_s / args.budget_s:.6f} → {chain or '（无需滤镜）'}")
    elif os.path.isdir(args.path):
        normalize_dir(args.path, target_db=args.target)
    else:
        gain = normalize_file(args.path, args.target)
        print(f"✓ {args.path}: peak {peak_db(args.path)} dB（增益 {gain}）")


if __name__ == "__main__":
    main()
