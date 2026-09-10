#!/usr/bin/env python3
"""subs_alpha.py — timeline.json 字幕 → 透明底字幕视频轨（ProRes 4444 alpha）

用 vtx 生成的 cache/subs.ass（字体/样式与终渲完全同源），在透明画布上经 libass
渲染，产出 cache/subs_alpha.mov 供 json2blend 当作普通视频轨叠加——Blender 不
再自行渲染任何文字。

用法: python3 subs_alpha.py <timeline.json>
"""
import json, subprocess, sys
from pathlib import Path

tl_path = Path(sys.argv[1]).resolve()
tl = json.loads(tl_path.read_text())
P = tl["project"]
W, H, FPS, DUR = P["width"], P["height"], P["fps"], P["duration_frames"]
ass = tl_path.parent / "cache" / "subs.ass"
out = tl_path.parent / "cache" / "subs_alpha.mov"

if not ass.exists():
    sys.exit(f"缺少 {ass}（先跑 vtx 的字幕阶段生成 subs.ass）")

cmd = ["ffmpeg", "-v", "error", "-y",
       "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={DUR / FPS:.6f}",
       "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={DUR / FPS:.6f}",
       "-filter_complex",
       "[0:v]format=rgb24[b];[1:v]format=gray[a];[b][a]alphamerge,format=yuva420p,"
       f"ass=filename='{ass}':alpha=1[v]",   # alphamerge 造透明画布 + ass 的 alpha=1 处理 alpha 平面
       "-map", "[v]", "-frames:v", str(DUR),
       "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
       str(out)]
r = subprocess.run(cmd)
sys.exit(r.returncode)
