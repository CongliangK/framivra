#!/usr/bin/env python3
"""json2blend.py — timeline.json (契约 v2) → Blender VSE 预览工程

用法: blender --background --factory-startup --python json2blend.py -- <timeline.json> <out.blend>
打开产物默认即为「视频编辑」工作区（时间线+视频预览面板直接呈现，无需手动切换）。

与契约 v2 的对应（语义以 vtx.py 源码为准）:
- video_tracks 数组序 = 图层序：[0] 最底 → 最低视频通道；音频轨占更低通道
- clip: frame_final_start=dst_in, frame_final_end=dst_in+dur；源偏移=src_in
- 全屏 clip: crop169 语义（16:9 中心裁切）+ 等比拉满画布（vtx L313）
- 窗口 clip(scale/x/y): scale 相对【源原始尺寸】直接缩放（vtx L307-311），x/y=左上角
- gfx clip: 当前后端/namespace/内容键的 vtx 帧缓存（只读校验后使用）
- 字幕: 不经 Blender 文字渲染——subs_alpha.py 产出的 cache/subs_alpha.mov
  （ASS+libass 透明轨，与终渲同源）作为最上层普通视频条叠加
- fade_*: blend_alpha / volume 关键帧（预览近似）
- 色彩: view_transform=Standard；同步模式 FRAME_DROP
- 条命名 {track}|{idx}[|{src}]；scene 记录来源 json（回写桥钩子）
- 顺序铁律: 先 trim 再设 channel（源全长重叠时 channel 赋值会被自动顶替）
"""
import json, subprocess, sys
from pathlib import Path
from types import SimpleNamespace

try:
    import bpy
except ImportError:
    # 本脚本须由 blender 解释器运行（python3 直跑无 bpy 模块）：
    print("错误: 未找到 bpy 模块 —— json2blend.py 必须由 blender 解释器运行，不能 python3 直跑。", file=sys.stderr)
    print("正确调用:", file=sys.stderr)
    print("  blender --background --factory-startup --python json2blend.py -- <timeline.json> <out.blend>", file=sys.stderr)
    sys.exit(2)

argv = sys.argv[sys.argv.index("--") + 1:]
# 相对素材/cache 属于 timeline 文件所在项目，不能跟随其符号链接到别的项目。
tl_path, blend_path = Path(argv[0]).absolute(), Path(argv[1]).resolve()
# 可选第 3 参（P6，plugins/blender-bridge finish 传入）：源产物解析映射
# {sid: wav 绝对路径}——虚拟源（bgm 工程 JSON）与降噪源的音频实际可用件是
# prepare 插件产物（cache/audio/*.wav），直引源路径对 JSON 源无效。**以 .json
# 后缀判定**（bundle_session 语境下 argv[2]=json2blend.py 自身路径，不能按位
# 置盲取）；无映射时保持 v1 行为（源路径直引——无插件产物的项目零变化）。
RESOLVED = {}
if len(argv) > 2 and argv[2].endswith(".json") and Path(argv[2]).is_file():
    RESOLVED = json.loads(Path(argv[2]).read_text(encoding="utf-8"))

tl = json.loads(tl_path.read_text(encoding="utf-8"))
base = tl_path.parent
P = tl["project"]
W, H, FPS, DUR = P["width"], P["height"], P["fps"], P["duration_frames"]
SRC = {s["id"]: (base / s["path"]).resolve() for s in tl["sources"]}


def audio_src(sid):
    """音轨事件源路径解析：插件产物映射优先（虚拟源/降噪源），否则源路径。
    JSON 虚拟源无映射 → None（记 error：先跑 vtx prepare/all 渲出产物）。"""
    p = SRC[sid]
    w = RESOLVED.get(sid)
    if sid in RESOLVED or p.suffix.lower() == ".json":
        return Path(w) if w else None
    return p

errors, stats = [], {"video": 0, "gfx": 0, "audio": 0, "subs": 0}

# ---------- 工作区：以官方 Video Editing 模板为底 ----------
# 目标：打开生成的 .blend 即为「视频编辑」工作区（时间线+预览面板直接呈现），免手动切换。
# 机制：出厂启动文件不含 Video Editing 工作区，且 headless 下 win.workspace= 赋值不生效、
# append 法保存后仍回落 Layout（实测 5.2.1）；唯一可靠路径=open 模板 startup.blend 为底
# （其活动工作区即 VE）→ 清掉模板示例物件 → 在其场景上建 VSE → 保存（活动工作区随文件持久）。
_VE_TPL = Path(bpy.utils.script_paths()[0]) / "startup" / "bl_app_templates_system" \
    / "Video_Editing" / "startup.blend"
if "Video Editing" not in {w.name for w in bpy.data.workspaces} and _VE_TPL.is_file():
    bpy.ops.wm.open_mainfile(filepath=str(_VE_TPL))
    for ob in list(bpy.context.scene.objects):
        bpy.data.objects.remove(ob, do_unlink=True)

# ---------- 场景 ----------
sc = bpy.context.scene
sc.render.fps = FPS
sc.render.fps_base = 1.0
sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = W, H, 100
sc.frame_start, sc.frame_end = 0, DUR - 1
sc.view_settings.view_transform, sc.view_settings.look = "Standard", "None"
sc.sync_mode = "FRAME_DROP"
sc.render.use_sequencer = True
sc.name = tl_path.stem
sc["vtx_timeline"] = str(tl_path)
sc["vtx_generator"] = "json2blend.py"

seq = sc.sequence_editor_create().strips  # Blender 5.x: sequences → strips

_probe = {}
def probe(path):
    """ffprobe 已选路径 (w, h, 总帧数)；纯音频源（含虚拟源产物 wav）按 format.duration×
    FPS 折算帧数——v1 音频源恒 (0,0,0) 使 loop 事件必报「无法测帧数」（bgm 床乐
    loop 平铺是常用形态，P6 修）。"""
    if path not in _probe:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-count_frames", "-show_entries",
                            "stream=width,height,nb_read_frames",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        try:
            w, h, n = (r.stdout.strip().split(",") + ["0", "0", "0"])[:3]
            w, h, n = int(w), int(h), int(n)
        except ValueError:
            w, h, n = 0, 0, 0
        if not n:
            r2 = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                 "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
            try:
                n = round(float(r2.stdout.strip()) * FPS)
            except ValueError:
                n = 0
        _probe[path] = (w, h, n)
    return _probe[path]

def kf_linear(strip, prop, points):
    """(帧, 值) → 关键帧（5.x 无 animation_data 可寻，插值取默认；预览近似）。
    结束后把静态属性还原为最大值（末值 0 会让回写比对误报）"""
    for f, v in sorted(points):
        try:
            setattr(strip, prop, v)
            strip.keyframe_insert(data_path=prop, frame=f)
        except Exception:
            return
    try:
        setattr(strip, prop, max(v for _, v in points))
    except Exception:
        pass

def apply_fade(strip, clip, base_val, prop):
    fi, fo = clip.get("fade_in_frames", 0), clip.get("fade_out_frames", 0)
    end = clip["dst_in"] + clip["dur"]
    pts = []
    if fi: pts += [(clip["dst_in"], 0.0), (clip["dst_in"] + fi, base_val)]
    if fo: pts += [(end - fo, base_val), (end, 0.0)]
    if pts: kf_linear(strip, prop, sorted(pts))

def crop169_dims(w, h):
    """vtx crop169: 16:9 中心裁切（先按宽裁高，超高则按高裁宽），返回 (cw, ch)"""
    cw = w - w % 2
    ch = int(w * 9 / 16); ch -= ch % 2
    if ch > h:
        ch = h - h % 2
        cw = int(h * 16 / 9); cw -= cw % 2
    return cw, ch


# Blender's --python does not add the script's repository to sys.path. Anchor
# this entry point to its own checkout and use the canonical vtx package (no
# copied key formula, alternate module name or global installed checkout).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from vtx import gfx_cache_reader

# Only the fields consumed by the read-only production cache resolver; do not
# construct a new pipeline Ctx or run prepare/render/receipt writers in Blender.
_GFX_CONTEXT = SimpleNamespace(root=base, cache=base / "cache", tl=tl,
                               fps=FPS, w=W, h=H)

def place(strip, track_i, ch_base, clip, src_w, src_h):
    """视频条：trim→channel→变换（顺序不可换）。
    全屏: crop169+等比拉满；窗口: scale 相对源原始尺寸，x/y 左上角"""
    strip.frame_final_start = clip["dst_in"]
    strip.frame_final_end = clip["dst_in"] + clip["dur"]
    strip.channel = ch_base + track_i
    strip.blend_type = "ALPHA_OVER"
    strip.blend_alpha = clip.get("opacity", 1.0)
    t = strip.transform
    if src_w and src_h:
        if any(k in clip for k in ("scale", "x", "y")):   # 窗口 clip（vtx L306-311）
            s = clip.get("scale", 1.0)
            t.scale_x = t.scale_y = s
            if "x" in clip or "y" in clip:
                w2, h2 = src_w * s, src_h * s
                t.offset_x = (clip.get("x", 0) + w2 / 2) - W / 2
                t.offset_y = (H / 2) - (clip.get("y", 0) + h2 / 2)
        else:                                              # 全屏 clip（vtx L313: crop169+scale 拉满）
            cw, ch = crop169_dims(src_w, src_h)
            if ch < src_h:
                strip.crop.min_y = strip.crop.max_y = (src_h - ch) // 2
            elif cw < src_w:
                strip.crop.min_x = strip.crop.max_x = (src_w - cw) // 2
            t.scale_x = t.scale_y = W / cw                 # cw:ch=16:9 → 恰好等比拉满
    apply_fade(strip, clip, clip.get("opacity", 1.0), "blend_alpha")

# ---------- 音频轨（通道 1..A） ----------
CH_V = 1 + len(tl["audio_tracks"])
for ti, tr in enumerate(tl["audio_tracks"], start=1):
    for i, ev in enumerate(tr["events"]):
        sid, name = ev["src"], f"{tr['name']}|{i}|{ev['src']}"
        path = audio_src(sid)
        if path is None:
            errors.append(f"虚拟源 {sid} 无产物映射（bgm 工程 JSON 须先跑 vtx prepare/all 渲出插件产物）"); continue
        if not path.exists():
            errors.append(f"音频源缺失 {sid}: {path}"); continue
        src_in, t_in, t_out = ev.get("src_in", 0), ev["in"], ev["out"]
        if ev.get("loop"):
            total = probe(path)[2]
            if not total:
                errors.append(f"loop 音频无法测帧数 {sid}"); continue
            reps = -(-(t_out - t_in) // total)
            for j in range(reps):
                nm = name if j == 0 else f"{name}#L{j}"
                st = seq.new_sound(nm, str(path), ti, int(t_in - src_in if j == 0 else t_in + j * total))
                st.frame_final_start = t_in + j * total
                st.frame_final_end = min(t_out, t_in + (j + 1) * total)
                st.channel = ti
                st.volume, st.pan = ev.get("volume", 1.0), ev.get("pan", 0.0)
                fake = {**ev, "dst_in": st.frame_final_start, "dur": st.frame_final_duration,
                        "fade_in_frames": ev.get("fade_in_frames", 0) if j == 0 else 0,
                        "fade_out_frames": ev.get("fade_out_frames", 0) if j == reps - 1 else 0}
                apply_fade(st, fake, ev.get("volume", 1.0), "volume")
                stats["audio"] += 1
        else:
            st = seq.new_sound(name, str(path), ti, int(t_in - src_in))
            st.frame_final_start, st.frame_final_end = t_in, t_out
            st.channel = ti
            st.volume, st.pan = ev.get("volume", 1.0), ev.get("pan", 0.0)
            apply_fade(st, {**ev, "dst_in": t_in, "dur": t_out - t_in},
                       ev.get("volume", 1.0), "volume")
            stats["audio"] += 1

# ---------- 视频轨（通道 CH_V..，[0] 最底） ----------
for ti, tr in enumerate(tl["video_tracks"]):
    for i, clip in enumerate(tr["clips"]):
        name, ch = f"{tr['name']}|{i}", CH_V + ti
        if tr["kind"] == "gfx":  # HTML → vtx 帧缓存
            try:
                files = gfx_cache_reader.resolve_frames(_GFX_CONTEXT, clip)
            except (OSError, ValueError) as exc:
                errors.append(f"gfx 缓存解析失败 {name}: {exc}")
                continue
            st = seq.new_image(name, str(files[0]), ch, clip["dst_in"])
            for f in files[1:clip["dur"]]:
                st.elements.append(f.name)
            place(st, ti, CH_V, clip, W, H)
            stats["gfx"] += 1
        else:
            path = SRC.get(clip["src"])
            if path is None or not path.exists():
                errors.append(f"视频源缺失 {name}: {clip['src']} -> {path}")
                continue
            st = seq.new_movie(name, str(path), ch, int(clip["dst_in"] - clip.get("src_in", 0)))
            place(st, ti, CH_V, clip, *probe(path)[:2])
            stats["video"] += 1

# ---------- 字幕：透明视频轨（subs_alpha.mov，与终渲同源） ----------
SUB_CH = CH_V + len(tl["video_tracks"])
if tl["subtitles"]:
    subs_mov = base / "cache" / "subs_alpha.mov"
    if subs_mov.exists():
        st = seq.new_movie("subs|ASS", str(subs_mov), SUB_CH, 0)
        st.frame_final_start, st.frame_final_end = 0, DUR
        st.channel = SUB_CH
        st.blend_type = "ALPHA_OVER"
        stats["subs"] = 1
    else:
        errors.append(f"缺少字幕透明轨 {subs_mov}（先跑 subs_alpha.py）")

# ---------- 保存 ----------
bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
print(f"[json2blend] {tl_path.name} -> {blend_path.name}")
print(f"[json2blend] 视频 {stats['video']} | gfx {stats['gfx']} | 音频 {stats['audio']}"
      f" | 字幕轨 {'有' if stats['subs'] else '无'}")
print(f"[json2blend] 通道: 音频 1..{CH_V-1}, 视频 {CH_V}..{SUB_CH-1}, 字幕 {SUB_CH}")
if errors:
    print(f"[json2blend] 错误 {len(errors)} 条:")
    for e in errors: print("  !", e)
    sys.exit(1)
print("[json2blend] OK 无错误")
