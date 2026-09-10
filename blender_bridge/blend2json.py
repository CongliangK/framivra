#!/usr/bin/env python3
"""blend2json.py — (用户修改过的) preview.blend → timeline.json 回写

用法: blender --background --python blend2json.py -- <preview.blend> [out.json]
json 源路径取 scene["vtx_timeline"]（json2blend 埋的钩子）；
缺省输出 <项目>/timeline.blendback.json（不覆盖原 timeline.json）。

回写面（用户约定，2026-08-29）：
- 视频条的时间平移（dst_in）
- 换轨（channel → video_tracks 索引；须同族：MOVIE 条 ↔ 非 gfx 轨，IMAGE 条 ↔ gfx 轨）
- 左/右缘裁剪（dur 与 src_in/src_out 同步重算；左缘拖动会改变入点）
不回写（检测并告警）：音频条、字幕条的一切改动。
拒收（保留原值并报因）：dst<0、越过画布尾、素材 src 区间越界、跨族换轨、条丢失。
产出后建议跑: python3 vtx.py -t <out.json> validate 验契约。
"""
import copy, json, subprocess, sys
from pathlib import Path

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
blend_path = Path(argv[0]).resolve()
bpy.ops.wm.open_mainfile(filepath=str(blend_path))
sc = bpy.context.scene
src_json = Path(sc["vtx_timeline"])
out_path = Path(argv[1]).resolve() if len(argv) > 1 else src_json.parent / "timeline.blendback.json"

tl = copy.deepcopy(json.loads(src_json.read_text()))
base = src_json.parent
DUR = tl["project"]["duration_frames"]
CH_V = 1 + len(tl["audio_tracks"])
SUB_CH = CH_V + len(tl["video_tracks"])
SRC = {s["id"]: (base / s["path"]).resolve() for s in tl["sources"]}

strips = {s.name: s for s in sc.sequence_editor.strips_all}
changes, warns, rejects, moves = [], [], [], []

_probe = {}
def src_frames(sid):
    if sid not in _probe:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                            "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0",
                            str(SRC[sid])], capture_output=True, text=True)
        try: _probe[sid] = int(r.stdout.strip())
        except ValueError: _probe[sid] = 0
    return _probe[sid]

# ---------- 视频条 ----------
for ti, tr in enumerate(tl["video_tracks"]):
    for i, clip in enumerate(tr["clips"]):
        nm = f"{tr['name']}|{i}"
        st = strips.get(nm)
        if st is None:
            rejects.append(f"{nm}: 条在 blend 中不存在（被删？）保留原值")
            continue
        gfx_track = tr["kind"] == "gfx"
        # 通道 → 目标轨（同族约束）
        new_ti = ti
        if st.channel != CH_V + ti:
            if CH_V <= st.channel < SUB_CH:
                cand = st.channel - CH_V
                cand_gfx = tl["video_tracks"][cand]["kind"] == "gfx"
                is_image = st.type == "IMAGE"
                if (is_image and cand_gfx) or ((not is_image) and (not cand_gfx)):
                    new_ti = cand
                else:
                    rejects.append(f"{nm}: 跨族换轨（{'gfx' if is_image else 'movie'} 条 → "
                                   f"{'gfx' if cand_gfx else '非gfx'} 轨）拒收")
            else:
                rejects.append(f"{nm}: 通道 {st.channel} 在视频区外（音频/字幕通道）拒收换轨")

        new_dst, new_dur = int(st.frame_final_start), int(st.frame_final_duration)
        new_si = int(st.frame_final_start - st.frame_start)
        reason = None
        if new_dst < 0:
            reason = "dst_in < 0"
        elif new_dst + new_dur > DUR:
            reason = f"越过画布尾 ({new_dst + new_dur} > {DUR})"
        elif not gfx_track and (new_si < 0 or (src_frames(clip["src"]) and
                new_si + new_dur > src_frames(clip["src"]))):
            reason = f"素材 src 越界 [{new_si},{new_si + new_dur})"
        if reason:
            rejects.append(f"{nm}: {reason} — 保留原值")
            continue

        diff = []
        if new_dst != clip["dst_in"]:
            diff.append(f"dst_in {clip['dst_in']}→{new_dst}"); clip["dst_in"] = new_dst
        if new_dur != clip["dur"]:
            diff.append(f"dur {clip['dur']}→{new_dur}"); clip["dur"] = new_dur
        if not gfx_track:
            old_si = clip.get("src_in", 0)
            old_so = clip.get("src_out", old_si + clip["dur"])
            if new_si != old_si or new_si + new_dur != old_so:
                clip["src_in"], clip["src_out"] = new_si, new_si + new_dur
                diff.append(f"src [{old_si},{old_so})→[{new_si},{new_si + new_dur})")
        if new_ti != ti:
            diff.append(f"轨 {tl['video_tracks'][ti]['name']}→{tl['video_tracks'][new_ti]['name']}")
            moves.append((ti, i, new_ti))
        if diff:
            changes.append(f"{nm}: " + "; ".join(diff))

# 换轨两段式：记录对象 → 每轨按原索引从大到小摘除 → 追加到目标轨 → 各轨按 dst_in 重排
moved_objs = []
for ti, i, new_ti in sorted(moves, key=lambda m: (m[0], -m[1])):
    moved_objs.append((tl["video_tracks"][ti]["clips"].pop(i), new_ti))
for obj, new_ti in moved_objs:
    tl["video_tracks"][new_ti]["clips"].append(obj)
for tr in tl["video_tracks"]:
    tr["clips"].sort(key=lambda c: c["dst_in"])

# 同轨重叠终检（含未改动轨）
overlap = False
for tr in tl["video_tracks"]:
    cs = tr["clips"]
    for a, b in zip(cs, cs[1:]):
        if a["dst_in"] + a["dur"] > b["dst_in"]:
            warns.append(f"轨 {tr['name']}: clip@{a['dst_in']} 与 clip@{b['dst_in']} 重叠 — 请在 Blender 修正后重跑")
            overlap = True

# ---------- 音频条回写（2026-08-29 用户扩展：支持平移/边缘裁剪） ----------
# 保护两条：换通道不回写（音频轨名驱动 ducking/audit 语义）；loop 组部分拖动不回写
for ti, tr in enumerate(tl["audio_tracks"]):
    for i, ev in enumerate(tr["events"]):
        nm = f"{tr['name']}|{i}|{ev['src']}"
        base_st = strips.get(nm)
        if base_st is None:
            rejects.append(f"{nm}: 条在 blend 中不存在（被删？）保留原值")
            continue
        group = [base_st] + [strips[n] for n in sorted(k for k in strips if k.startswith(nm + "#L"))]
        group.sort(key=lambda s: s.frame_final_start)
        if not all(s.channel == ti + 1 for s in group):
            warns.append(f"音频 {nm} 换了通道（音频轨名驱动 ducking/audit 语义，不回写；请移回通道 {ti + 1}）")
            continue
        if len(group) > 1 and any(group[k + 1].frame_final_start != group[k].frame_final_end
                                  for k in range(len(group) - 1)):
            warns.append(f"音频 {nm} loop 组被部分拖动（出现缝隙）— 不回写")
            continue
        new_in, new_out = int(group[0].frame_final_start), int(group[-1].frame_final_end)
        new_si = int(group[0].frame_final_start - group[0].frame_start)
        reason = None
        if new_in < 0:
            reason = "in < 0"
        elif new_out > DUR:
            reason = f"越过画布尾 ({new_out} > {DUR})"
        elif new_si < 0:
            reason = "src_in < 0"
        if reason:
            rejects.append(f"{nm}: {reason} — 保留原值")
            continue
        diff = []
        if new_in != ev["in"]:
            diff.append(f"in {ev['in']}→{new_in}"); ev["in"] = new_in
        if new_out != ev["out"]:
            diff.append(f"out {ev['out']}→{new_out}"); ev["out"] = new_out
        old_si = ev.get("src_in", 0)
        if new_si != old_si:
            ev["src_in"] = new_si
            diff.append(f"src_in {old_si}→{new_si}")
        # 音量/声像：带淡变的事件静态属性不可靠（关键帧驱动），跳过比对
        if not (ev.get("fade_in_frames") or ev.get("fade_out_frames")):
            if abs(base_st.volume - ev.get("volume", 1.0)) > 1e-4:
                warns.append(f"音频 {nm} 音量 {ev.get('volume', 1.0)}→{base_st.volume}（本次不回写）")
            if abs(getattr(base_st, "pan", 0.0) - ev.get("pan", 0.0)) > 1e-4:
                warns.append(f"音频 {nm} 声像 {ev.get('pan', 0.0)}→{base_st.pan}（本次不回写）")
        if diff:
            changes.append(f"{nm}: " + "; ".join(diff))

# 音频同轨重叠终检
for tr in tl["audio_tracks"]:
    evs = sorted(tr["events"], key=lambda e: e["in"])
    for a, b in zip(evs, evs[1:]):
        if a["out"] > b["in"]:
            warns.append(f"音频轨 {tr['name']}: @{a['in']} 与 @{b['in']} 重叠 — 请在 Blender 修正")
            overlap = True
subs = strips.get("subs|ASS")
if subs and (subs.frame_final_start != 0 or subs.frame_final_end != DUR):
    warns.append("字幕轨被移动（字幕回写需改 subtitles 数组，本次不回写）")

# ---------- 输出 ----------
out_path.write_text(json.dumps(tl, ensure_ascii=False, indent=2) + "\n")
print(f"[blend2json] 源 {src_json.name} ← {blend_path.name}")
print(f"[blend2json] 回写 {len(changes)} 项 / 拒收 {len(rejects)} 项 / 告警 {len(warns)} 项 → {out_path.name}")
for c in changes: print("  改", c)
for r in rejects: print("  拒", r)
for w in warns:   print("  警", w)
if overlap:
    print("[blend2json] 存在同轨重叠，文件已写出但 vtx validate 将不通过")
    sys.exit(2)
print("[blend2json] OK")
