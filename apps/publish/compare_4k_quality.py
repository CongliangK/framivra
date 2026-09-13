#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compare_4k_quality.py · 4K 导出版画质门禁（方案三阶段2，验收 §四.2）

对比 nvenc 4K 导出版与 x264 medium 基线版（runbook R5 现行命令产物）：同位抽帧算
单帧 PSNR 与 SSIM，门 = 每帧 PSNR ≥ --psnr-min（默认 42dB）或 SSIM ≥ --ssim-min
（默认 0.99），任一达标即过（方案三口径，或语义）。

抽帧（seed 固定可复现）：段边界帧（0 + 各段起点；段切分复用 export_4k.split_frames
同一数学，默认 8 段）+ 全片中点 + 随机点补足 ≥ --frames（默认 10）。

实现：两版同位提取（-ss 输入 seek + -frames:v 1，yuv420p rawvideo——与 export_4k
段 seek 同款帧精确语义）落 raw 临时文件，ffmpeg psnr/ssim filter 单帧比对各跑一遍
（PSNR 一遍 + SSIM 一遍；不自实现 SSIM 窗口化，与仓内 vtx 全部走 ffmpeg 生态一致）。
逐位相同帧 PSNR=inf 视为达标（方案二 seg 代差对照同款处理）。

用法（项目根）：
  env python3 apps/publish/compare_4k_quality.py out/final_4k.mp4 out/final_4k_x264_ref.mp4
      [--frames 10] [--segments 8] [--psnr-min 42] [--ssim-min 0.99]
  （兼容旧路径 scripts/compare_4k_quality.py 经 symlink 同义）

退出码：0 = 全帧达标 PASS / 1 = 存在不达标帧 FAIL / 2 = 输入不可比（帧数不等）或
指标解析失败（fail-closed，阶段2审查 P2-1）。
"""
import argparse
import math
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_4k import split_frames

SEED = 20260913   # 随机补点固定 seed（可复现）


def _sh(cmd):
    """子进程封装（对齐 export_4k._sh / infra.Ctx.run 语义）。"""
    r = subprocess.run([str(x) for x in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("CMD FAILED:", " ".join(map(str, cmd))[:300], file=sys.stderr)
        print(r.stderr[-1500:], file=sys.stderr)
        sys.exit(2)
    return r


def probe(path):
    """(w, h, fps, nb_frames)。fps 取 r_frame_rate（4K 断言本就要求 30/1 精确）。"""
    r = _sh(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
             "-of", "csv=p=0", str(path)])
    w, h, rfr, nb = r.stdout.strip().split(",")
    num, _, den = rfr.partition("/")
    return int(w), int(h), float(num) / float(den or 1), int(nb)


def extract_frame(path, n: int, fps: float, dst: Path):
    """同位提帧：-ss (n-0.2)/fps 输入 seek + -frames:v 1（n=0 用 0；qc.gray_frame 语义）。"""
    ss = "0" if n == 0 else f"{(n - 0.2) / fps:.6f}"
    _sh(["ffmpeg", "-v", "error", "-y", "-ss", ss, "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "yuv420p", str(dst)])


def raw_metric(a_raw: Path, b_raw: Path, w: int, h: int, filt: str) -> str:
    """ffmpeg psnr/ssim filter 对两份单帧 raw 比对，返回指标行（stderr 日志段）。"""
    r = subprocess.run(["ffmpeg", "-nostdin",
                        "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}", "-i", str(a_raw),
                        "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}", "-i", str(b_raw),
                        "-lavfi", filt, "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"✗ {filt} 比对失败：{r.stderr[-800:]}", file=sys.stderr)
        sys.exit(2)
    return r.stderr


def parse_psnr(log: str) -> float:
    """解析失败 fail-closed（阶段2审查 P2-1）：打不出数值（ffmpeg 版本升级致日志
    措辞变化等）直接报错 exit 2，绝不把解析失败当满分（旧行为返回 inf → 恒 PASS）。"""
    m = re.search(r"average:([\d.]+|inf)", log)
    if m is None:
        print("✗ PSNR 解析失败：psnr filter 日志无 average 行（ffmpeg 输出格式变化？）",
              file=sys.stderr)
        sys.exit(2)
    return float("inf") if m.group(1) == "inf" else float(m.group(1))


def parse_ssim(log: str) -> float:
    m = re.search(r"All:([\d.]+)", log)
    if m is None:
        print("✗ SSIM 解析失败：ssim filter 日志无 All 行（ffmpeg 输出格式变化？）",
              file=sys.stderr)
        sys.exit(2)
    return float(m.group(1))


def fmt_psnr(v: float) -> str:
    return "     inf" if math.isinf(v) else f"{v:8.2f}"


def main():
    ap = argparse.ArgumentParser(description="4K 导出版画质门禁（nvenc vs x264 基线抽帧对比）")
    ap.add_argument("nvenc_4k", help="nvenc 导出版（export_4k.py 产物）")
    ap.add_argument("x264_ref", help="x264 medium 基线版（R5 现行命令产物）")
    ap.add_argument("--frames", type=int, default=10, help="最少抽帧总数（默认 10）")
    ap.add_argument("--segments", type=int, default=8, help="段边界口径（默认 8，与导出一致）")
    ap.add_argument("--psnr-min", type=float, default=42.0, help="单帧 PSNR 门（dB，默认 42）")
    ap.add_argument("--ssim-min", type=float, default=0.99, help="单帧 SSIM 门（默认 0.99）")
    args = ap.parse_args()

    w1, h1, fps1, nb1 = probe(args.nvenc_4k)
    w2, h2, fps2, nb2 = probe(args.x264_ref)
    if nb1 != nb2 or (w1, h1) != (w2, h2):
        print(f"✗ 两版不可比：nvenc {w1}x{h1}/{nb1}f vs x264 {w2}x{h2}/{nb2}f", file=sys.stderr)
        sys.exit(2)
    if nb1 == 0:
        print("✗ 帧数探测为 0，无法抽帧", file=sys.stderr); sys.exit(2)

    # 抽帧点位：段边界（0 + 各段起点）+ 中点 + 固定 seed 随机补足
    spans = split_frames(nb1, args.segments)
    cand = {0, nb1 // 2}
    for a, _ in spans:
        cand.add(a)
    rng = random.Random(SEED)
    while len(cand) < max(1, args.frames):
        cand.add(rng.randrange(nb1))
    frames = sorted(f for f in cand if 0 <= f < nb1)
    n_bound = len([a for a, _ in spans if a > 0]) + 1
    print(f"抽帧 {len(frames)} 帧（段边界 {n_bound} + 中点 + 随机补足，seed={SEED}）：")

    t0 = time.monotonic()
    rows, n_bad = [], 0
    with tempfile.TemporaryDirectory(prefix="cmp4k_") as td:
        ra, rb = Path(td) / "a.raw", Path(td) / "b.raw"
        for n in frames:
            extract_frame(args.nvenc_4k, n, fps1, ra)
            extract_frame(args.x264_ref, n, fps2, rb)
            psnr = parse_psnr(raw_metric(ra, rb, w1, h1, "psnr"))
            ssim = parse_ssim(raw_metric(ra, rb, w1, h1, "ssim"))
            ok = psnr >= args.psnr_min or ssim >= args.ssim_min
            n_bad += 0 if ok else 1
            rows.append((n, psnr, ssim, ok))
            print(f"  f{n:6d}  PSNR {fmt_psnr(psnr)}  SSIM {ssim:.6f}  {'✓' if ok else '✗'}")
    dt = time.monotonic() - t0
    print(f"[timing] 抽帧+双指标（{len(frames)} 帧 × 2 版 × PSNR/SSIM 两遍）: {dt:.1f}s")

    psnrs, ssims = [r[1] for r in rows], [r[2] for r in rows]

    def med(vs):
        return statistics.median(vs)

    print(f"  PSNR min = {fmt_psnr(min(psnrs)).strip()} dB / median = {fmt_psnr(med(psnrs)).strip()} dB")
    print(f"  SSIM min = {min(ssims):.6f} / median = {med(ssims):.6f}")
    if n_bad == 0:
        print(f"画质门禁 PASS（{len(rows)}/{len(rows)} 帧达标：PSNR≥{args.psnr_min} 或 SSIM≥{args.ssim_min}）")
        sys.exit(0)
    print(f"画质门禁 FAIL（{n_bad}/{len(rows)} 帧不达标：门 = PSNR≥{args.psnr_min} 或 SSIM≥{args.ssim_min}）")
    sys.exit(1)


if __name__ == "__main__":
    main()
