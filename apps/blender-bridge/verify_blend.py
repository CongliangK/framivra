#!/usr/bin/env python3
"""verify_blend.py — 结构校验：.blend 条 ↔ timeline.json 逐条对账
用法: blender --background --python verify_blend.py -- <timeline.json> <blend>"""
import json, sys
from pathlib import Path
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bundle_session import require_modern_vse

require_modern_vse(bpy)
argv = sys.argv[sys.argv.index("--") + 1:]
tl = json.loads(Path(argv[0]).read_text())
P = tl["project"]
CH_V = 1 + len(tl["audio_tracks"])
SUB_CH = CH_V + len(tl["video_tracks"])

bpy.ops.wm.open_mainfile(filepath=str(Path(argv[1]).resolve()))

strips = list(bpy.context.scene.sequence_editor.strips_all)
by_name = {s.name: s for s in strips}
fails, oks = [], 0
def check(cond, msg):
    global oks
    if cond: oks += 1
    else: fails.append(msg)

# 视频轨
for ti, tr in enumerate(tl["video_tracks"]):
    for i, clip in enumerate(tr["clips"]):
        nm = f"{tr['name']}|{i}"
        st = by_name.get(nm)
        check(st is not None, f"{nm}: 条不存在")
        if not st: continue
        check(st.channel == CH_V + ti, f"{nm}: 通道 {st.channel} ≠ {CH_V + ti}")
        check(st.frame_final_start == clip["dst_in"],
              f"{nm}: final_start {st.frame_final_start} ≠ {clip['dst_in']}")
        check(st.frame_final_duration == clip["dur"],
              f"{nm}: 时长 {st.frame_final_duration} ≠ {clip['dur']}")
        if tr["kind"] != "gfx":
            check(st.frame_final_start - st.frame_start == clip.get("src_in", 0),
                  f"{nm}: 源偏移 {st.frame_final_start - st.frame_start} ≠ {clip.get('src_in', 0)}")

# 音频轨：覆盖区间
for tr in tl["audio_tracks"]:
    for i, ev in enumerate(tr["events"]):
        base_nm = f"{tr['name']}|{i}|{ev['src']}"
        group = [by_name[base_nm]] + [by_name[n] for n in by_name if n.startswith(base_nm + "#L")]
        group = [g for g in group if g]
        check(bool(group), f"{base_nm}: 无条")
        if not group: continue
        group.sort(key=lambda s: s.frame_final_start)
        check(group[0].frame_final_start == ev["in"],
              f"{base_nm}: 覆盖起点 {group[0].frame_final_start} ≠ {ev['in']}")
        check(group[-1].frame_final_end == ev["out"],
              f"{base_nm}: 覆盖终点 {group[-1].frame_final_end} ≠ {ev['out']}")
        gap = any(group[k+1].frame_final_start != group[k].frame_final_end
                  for k in range(len(group)-1))
        check(not gap, f"{base_nm}: loop 展开有缝隙")

# 字幕透明轨
if tl["subtitles"]:
    subs = [s for s in strips if s.name == "subs|ASS"]
    check(len(subs) == 1, f"subs|ASS 条数 {len(subs)} ≠ 1")
    if subs:
        s = subs[0]
        check(s.frame_final_start == 0 and s.frame_final_end == P["duration_frames"],
              f"subs|ASS 区间 {s.frame_final_start}-{s.frame_final_end} ≠ 0-{P['duration_frames']}")
        check(s.channel == SUB_CH, f"subs|ASS 通道 {s.channel} ≠ {SUB_CH}")

print(f"[verify] 通过 {oks} 项，失败 {len(fails)} 项，条总数 {len(strips)}")
for f in fails:
    print("  ✗", f)
sys.exit(1 if fails else 0)
