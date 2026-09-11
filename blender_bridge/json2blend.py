#!/usr/bin/env python3
"""json2blend.py — timeline.json (契约 v2) → Blender VSE 预览工程

用法: blender --background --factory-startup --python json2blend.py -- <timeline.json> <out.blend>
打开产物默认即为「视频编辑」工作区（时间线+视频预览面板直接呈现，无需手动切换）。

与契约 v2 的对应（语义以 vtx.py 源码为准）:
- video_tracks 数组序 = 图层序：[0] 最底 → 最低视频通道；音频轨占更低通道
- clip: frame_final_start=dst_in, frame_final_end=dst_in+dur；源偏移=src_in
- 全屏 clip: crop169 语义（16:9 中心裁切）+ 等比拉满画布（vtx L313）
- 窗口 clip(scale/x/y): scale 相对【源原始尺寸】直接缩放（vtx L307-311），x/y=左上角
- gfx clip: vtx 帧缓存 cache/<stem>/f*.png 图像序列条
- 字幕: 不经 Blender 文字渲染——subs_alpha.py 产出的 cache/subs_alpha.mov
  （ASS+libass 透明轨，与终渲同源）作为最上层普通视频条叠加
- fade_*: blend_alpha / volume 关键帧（预览近似）
- 色彩: view_transform=Standard；同步模式 FRAME_DROP
- 条命名 {track}|{idx}[|{src}]；scene 记录来源 json（回写桥钩子）
- 顺序铁律: 先 trim 再设 channel（源全长重叠时 channel 赋值会被自动顶替）
"""
import hashlib
import json, subprocess, sys
from pathlib import Path

try:
    import bpy
except ImportError:
    # 本脚本须由 blender 解释器运行（python3 直跑无 bpy 模块）：
    print("错误: 未找到 bpy 模块 —— json2blend.py 必须由 blender 解释器运行，不能 python3 直跑。", file=sys.stderr)
    print("正确调用:", file=sys.stderr)
    print("  blender --background --factory-startup --python json2blend.py -- <timeline.json> <out.blend>", file=sys.stderr)
    sys.exit(2)

argv = sys.argv[sys.argv.index("--") + 1:]
tl_path, blend_path = Path(argv[0]).resolve(), Path(argv[1]).resolve()

tl = json.loads(tl_path.read_text())
base = tl_path.parent
P = tl["project"]
W, H, FPS, DUR = P["width"], P["height"], P["fps"], P["duration_frames"]
SRC = {s["id"]: (base / s["path"]).resolve() for s in tl["sources"]}

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
def probe(sid):
    """ffprobe 源 (w, h, 总帧数)；音频源 (0,0,0)"""
    if sid not in _probe:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-count_frames", "-show_entries",
                            "stream=width,height,nb_read_frames",
                            "-of", "csv=p=0", str(SRC[sid])], capture_output=True, text=True)
        try:
            w, h, n = (r.stdout.strip().split(",") + ["0", "0", "0"])[:3]
            _probe[sid] = (int(w), int(h), int(n))
        except ValueError:
            _probe[sid] = (0, 0, 0)
    return _probe[sid]

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


def _file_sha256(path):
    """小文件全量 sha256，1MB 块流式（与 vtx cache_state.file_digest 同式）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def gfx_cache_key(html_path, clip):
    """与 vtx hygiene.gfx_key 同键（D44 漂移修复，2026-09-11）：
    sha1(html 字节 + 逐依赖 str(resolve路径)+\\0+sha256 内容 + f"|{FPS}|{W}x{H}|{dur}")[:12]。
    json2blend 由 blender 解释器运行、不能 import 仓内模块，就地复刻；
    cache_dependencies 缺省（老 timeline 无此键）= 无依赖不加料，与 hygiene 行为一致。"""
    blob = html_path.read_bytes()
    for dep in tl.get("project", {}).get("cache_dependencies", []):
        p = (base / dep).resolve()
        blob += b"|" + str(p).encode() + b"\0" + _file_sha256(p).encode()
    return hashlib.sha1(blob + f"|{FPS}|{W}x{H}|{clip['dur']}".encode()).hexdigest()[:12]

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
        path = SRC[sid]
        if not path.exists():
            errors.append(f"音频源缺失 {sid}: {path}"); continue
        src_in, t_in, t_out = ev.get("src_in", 0), ev["in"], ev["out"]
        if ev.get("loop"):
            total = probe(sid)[2]
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
            # 缓存目录解析：旧版 cache/<stem>/，2026-09-11 起内容寻址 cache/gfx/<sha1[:12]>
            # （键式与 vtx hygiene.gfx_key 一致：html 字节+声明的 cache_dependencies 内容
            # +fps+视口+时长；D44——两实现必须同键，见上方 gfx_cache_key）
            _html = (base / clip["src"]).resolve()
            _key = gfx_cache_key(_html, clip)
            pngdir = (base / "cache" / "gfx" / _key).resolve()
            if not pngdir.is_dir():  # 回落旧版布局 cache/<stem>/
                pngdir = (base / "cache" / Path(clip["src"]).stem).resolve()
            files = sorted(pngdir.glob("f*.png")) if pngdir.is_dir() else []
            if len(files) < clip["dur"]:
                errors.append(f"gfx 缓存不足 {name}: {pngdir} 有 {len(files)} 帧 < dur {clip['dur']}")
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
            place(st, ti, CH_V, clip, *probe(clip["src"])[:2])
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
