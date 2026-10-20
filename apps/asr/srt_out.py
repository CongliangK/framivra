#!/usr/bin/env python3
"""srt_out —— 独立 SRT 1.0 写出

用途：把 segline 产出的帧级 subtitles[] 写成 SRT 字幕文件（时间码 HH:MM:SS,mmm），
供第三方工具（剪辑软件/播放器）直接消费；timeline.json 合并走 tl_merge，与本文件无关。

用法（平铺 import，vtx 同款手法）：
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import srt_out
    srt_out.write_file(subs, "/tmp/out.srt", fps=30)

机制：帧区间 [in, out) 左闭右开 → SRT 显示区间 [in/fps, out/fps)（毫秒取整）；
序号从 1 连续递增；条目间空行分隔；末尾以换行收束。

来源：https://github.com/CongliangK/framivra/blob/c476b4f5f13687c61c9be02f4015e289a176ddc8/docs/plans/2026-09-11-三大模块整合方案.md v2.1 §3.2/§3.7。
"""
from pathlib import Path


def frame_to_srt_time(frames, fps=30):
    """帧数 → "HH:MM:SS,mmm"（SRT 1.0 时间码，毫秒段用逗号）。"""
    total_ms = round(frames * 1000.0 / fps)
    h, rem = divmod(total_ms, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, ms = divmod(rem, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def render_srt(subs, fps=30):
    """subtitles[] → SRT 全文（str）。空列表 → 空串。"""
    blocks = []
    for i, sub in enumerate(subs, 1):
        t0 = frame_to_srt_time(sub["in"], fps)
        t1 = frame_to_srt_time(sub["out"], fps)
        blocks.append("%d\n%s --> %s\n%s" % (i, t0, t1, sub["text"]))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def write_file(subs, out_path, fps=30):
    """写 SRT 文件（父目录不存在则创建），返回 SRT 全文。"""
    content = render_srt(subs, fps=fps)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return content


if __name__ == "__main__":
    import argparse
    import json
    import sys
    ap = argparse.ArgumentParser(description="subtitles[]（stdin json）→ SRT（stdout / -o 文件）")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()
    subs = json.load(sys.stdin)
    text = render_srt(subs, fps=args.fps)
    if args.out:
        write_file(subs, args.out, fps=args.fps)
        print("✓ SRT 已写出：%s（%d 条）" % (args.out, len(subs)))
    else:
        sys.stdout.write(text)
