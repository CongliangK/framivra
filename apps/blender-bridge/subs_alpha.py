#!/usr/bin/env python3
"""subs_alpha.py — timeline.json 字幕 → 透明底字幕视频轨（ProRes 4444 alpha）

用 vtx 生成的 cache/subs.ass（字体/样式与终渲完全同源），在透明画布上经 libass
渲染，产出 cache/subs_alpha.mov 供 json2blend 当作普通视频轨叠加——Blender 不
再自行渲染任何文字。

用法: python3 subs_alpha.py <timeline.json>
"""
import json, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from vtx.pipeline.composite import RenderPlan
from types import SimpleNamespace
from vtx.pipeline.constraints import parse_fps, fps_expression

# 保留 timeline 符号链接所在项目的根，不跳到链接目标项目的 cache。
tl_path = Path(sys.argv[1]).absolute()
tl = json.loads(tl_path.read_text(encoding="utf-8"))
P = tl["project"]
W, H, FPS, DUR = P["width"], P["height"], float(parse_fps(P["fps"])), P["duration_frames"]
FPS_EXPR = fps_expression(P["fps"])
ass = tl_path.parent / "cache" / "subs.ass"
out = tl_path.parent / "cache" / "subs_alpha.mov"

if not ass.exists():
    sys.exit(f"缺少 {ass}（先跑 vtx 的字幕阶段生成 subs.ass）")

plan = RenderPlan(c=SimpleNamespace(fps=FPS), draft=False, f=1.0, W=W, H=H)
# Global ASS uses the renderer's virtual subtitle clock at high frame rates.
subtitle_filter = plan._subtitle_filter(ass)
cmd = ["ffmpeg", "-v", "error", "-y",
       "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS_EXPR}:d={DUR / FPS:.6f}",
       "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS_EXPR}:d={DUR / FPS:.6f}",
       "-filter_complex",
       "[0:v]format=rgb24[b];[1:v]format=gray[a];[b][a]alphamerge,format=yuva420p,"
       f"{subtitle_filter.replace('subtitles=filename=', 'subtitles=alpha=1:filename=')}[v]",   # alphamerge 造透明画布 + ass 的 alpha=1 处理 alpha 平面
       "-map", "[v]", "-frames:v", str(DUR),
       "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
       str(out)]
r = subprocess.run(cmd)
sys.exit(r.returncode)
