"""vtx 区间渲染（方案五阶段1）：`video --draft --range A B` / `--clip ID` / `--track NAME`。

动机：迭代修订时「只看成片某几秒」不必重渲整片——复用 RenderPlan 与既有
cache/seg / cache/gfx 内容寻址缓存（键与窗口无关，帧内容天然一致），对全片切点
（choose_cutpoints，与整片同一切点集，不另造切点逻辑）中与窗口相交的段做
composite(a=窗口裁剪段, b=…)（段内相对时间红线见 composite.composite 注释），
字幕沿用段级 ass 机制；音频复用 build_audio 的同一 filtergraph（含 duck
sidechain）在窗口 ±margin 事件集上混音、边界 atrim，最后与画面 mux 成一个 mp4。

命名空间纪律：全部产物只落 cache/preview/（独立命名空间，键 = 段内容键），不写
cache 正式键产物、不写 out/、不更新 receipt/manifest/VERSIONS——预览渲染对正式
产物链零干扰。产物 = cache/preview/range_<A标签>_<B标签>.mp4。
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

from .composite import RenderPlan, choose_cutpoints, prepare_layers
from ..media.encoders import pick_vcodec
from ..cache.recovery import media_cache_valid
from .stages import mix_audio_wav
from ..runtime import fstxn

# 音频窗口事件纳入外延（帧）：兜住 sidechaincompress release（1500ms）与淡出尾巴
AUDIO_MARGIN_FRAMES = 60


# ---------------------------------------------------------------- 时间/窗口解析
def parse_timespec(spec: str, fps: int) -> int:
    """时间点规格 → 全局帧号。三种形式（与 subs preview 时间点约定同族）：
    `12` / `12.5` = 秒；`mm:ss` / `hh:mm:ss(.fff)` = 时间码；`300f` = 帧号。
    秒/时间码按 round(sec*fps) 落帧网格。解析失败抛 ValueError（CLI 层友好打印）。"""
    s = spec.strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)f", s, re.IGNORECASE)
    if m:
        fr = round(float(m.group(1)))
    else:
        m = re.fullmatch(r"(\d+):(\d{1,2}(?:\.\d+)?)(?::(\d{1,2}(?:\.\d+)?))?", s)
        if m:
            if m.group(3) is None:      # mm:ss
                sec = int(m.group(1)) * 60 + float(m.group(2))
            else:                       # hh:mm:ss(.fff)
                sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            fr = round(sec * fps)
        else:
            try:
                fr = round(float(s) * fps)
            except ValueError:
                raise ValueError(f"无法解析时间点 {spec!r}（支持：秒 12 / 12.5｜时间码 mm:ss / "
                                 f"hh:mm:ss(.fff)｜帧号 300f）") from None
    if fr < 0:
        raise ValueError(f"时间点 {spec!r} 为负（{fr} 帧）：区间端点必须 ≥0")
    return fr


def range_label(fr: int, fps: int) -> str:
    """产物文件名标签：秒保留 3 位小数（确定性，不依赖用户输入写法），只留文件名
    安全字符。P6 修复 B-P3-5c：v1 的 1 位小数使相邻窗（如 [3,6) 与 [4,5)）同标签
    `range_3.0_6.0` 静默互相覆盖；3 位小数（≈毫秒）在帧网格粒度（33ms@30fps）下
    无碰撞。"""
    lab = f"{fr / fps:.3f}".replace("/", "_")
    return re.sub(r"[^0-9A-Za-z._-]", "_", lab)


def window_from_clip(tl: dict, cid: str):
    """--clip <id>：按 clip 的 src 名（Path(src).stem，与日志/gfx cid 同一叫法）匹配。
    命中多轨多条时取最先出现的（图层序，即画面最上层的轨序不确定——取轨道序首个），
    返回该 clip 的窗口 (dst_in, dst_in+dur)；找不到返回 None（调用方报错）。"""
    for tr in tl["video_tracks"]:
        for cl in tr["clips"]:
            if Path(cl["src"]).stem == cid:
                return cl["dst_in"], cl["dst_in"] + cl["dur"]
    return None


def window_from_track(tl: dict, name: str):
    """--track <name>：该轨全部 clips 的外包络 [min dst_in, max dst_out)（轨内可能有
    空档，预览取连续外包络比逐段渲染便宜且足够预览用）。找不到或该轨无 clips
    （空轨不可外包）返回 None（调用方统一友好报错）。"""
    for tr in tl["video_tracks"]:
        if tr.get("name") == name or tr.get("kind") == name:
            cl = tr.get("clips") or []
            if not cl: continue
            return min(c["dst_in"] for c in cl), max(c["dst_in"] + c["dur"] for c in cl)
    return None


def select_segments(edges, fa: int, fb: int):
    """与窗口 [fa,fb) 相交的段区间列表（纯函数）。段取整片切点集（choose_cutpoints
    于全片 dur 计算，与整片渲染同一切点——硬要求：不得为区间渲染另造切点），渲染时
    只取段∩窗口的子区间 [max(a,fa), min(b,fb))，composite(a,b) 的段内相对时间语义
    对任意子区间成立（跨子区间界的层由 -ss/start_number 截入，与段界同机制）。"""
    return [(max(a, fa), min(b, fb)) for a, b in zip(edges, edges[1:])
            if a < fb and b > fa]


# ---------------------------------------------------------------- 主入口
def _receipt_audio_gain(c):
    """读 out/receipt.json 里整片 audio 阶段记录的静态增益（只读，不写回执）。
    窗口局部内容响度 ≠ 全片响度——窗口自测增益会把安静段（如片头 bed-only）放大到
    削波（实测 0-6s 窗自测 +15.7dB → max 0dB）；复用整片增益保持听感一致，且免一遍
    ebur128 测量。无回执或回执与当前输入/实现身份不符时返回 None；经统一
    load_receipt 指纹门拒绝旧 gain，失配回落窗口自测并打印陈旧告警。"""
    from ..cache.cache_state import load_receipt
    f = c.out / "receipt.json"
    had_file = f.exists()
    try:
        rec = load_receipt(f, c.input_fingerprint)
        gain = (rec.get("stages") or {}).get("audio", {}).get("gain_db")
        return float(gain) if gain is not None else None
    except Exception:
        return None
    finally:
        if had_file and gain_is_stale(c, f):
            print("  ⚠ 整片回执与当前输入/实现不符（请按序重跑或运行 vtx all）——"
                  "区间增益改用窗口自测")


def gain_is_stale(c, f) -> bool:
    """回执文件在盘但输入指纹失配 = 陈旧（供 _receipt_audio_gain 告警判定）。"""
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
        return rec.get("input_fingerprint") != c.input_fingerprint
    except Exception:
        return False


def _ensure_audio_wavs(c, fa: int, fb: int):
    """窗口音频事件引用的 prepare 抽音频 wav 缺失时冷填充。提取语义（键/命令/
    缝 A 触发）与 stages.prepare **同一实现**（source_audio_extraction，v2 §2.2
    对抗审查 P0 复审 P1-5：两处各自为政会让区间预览绕过源级插件裸抽 wav）——
    共享正式缓存非 preview 私有产物；温态零写；dry-run 不落盘。"""
    if c.dry: return
    from .stages import source_audio_extraction
    margin = AUDIO_MARGIN_FRAMES
    need = {e["src"] for tr in c.tl["audio_tracks"] for e in tr["events"]
            if e["in"] < fb + margin and e["out"] > fa - margin}
    for sid in sorted(need):
        wav, cmd = source_audio_extraction(c, sid)
        if cmd is None: continue
        print(f"  · 抽音频（prepare 同键冷填充）: {sid}")
        c.run(cmd)


def run_range(c, fa: int, fb: int):
    """区间渲染主流程（draft 档）：cache/preview/range_<A>_<B>.mp4。
    不写 out/、不更新 receipt/manifest；复用 cache/seg|gfx（可冷填充，键与整片一致）。"""
    fa, fb = max(0, fa), min(c.dur, fb)
    if not (0 <= fa < fb <= c.dur):
        print(f"✗ 无效区间 [{fa},{fb})（项目总长 {c.dur} 帧）"); sys.exit(1)
    t0 = time.monotonic()
    draft = True
    f = 0.5
    W, H = (c.w // 2) // 2 * 2, (c.h // 2) // 2 * 2
    la, lb = range_label(fa, c.fps), range_label(fb, c.fps)
    print(f"== range [{fa},{fb})（{la}s–{lb}s，{fb - fa} 帧，draft {W}x{H}）==")
    print("  ℹ 预览渲染：不写 out/ 与 receipt，产物在 cache/preview/")

    t = time.monotonic()
    layers = prepare_layers(c)      # seg 缓存命中即秒回；缺失时冷填充（键与整片一致）
    print(f"[timing] gfx/seg 复用检查（{len(layers)} 层）: {time.monotonic() - t:.1f}s")
    # gfx 冷缓存门（P1-B）：区间渲染复用 gfx 阶段产物但不自动渲 gfx——缺帧时让
    # ffmpeg 报 PNG 读不到的原始错误毫无指向性，故此处显式拦截并指路 `vtx gfx`。
    missing_gfx = []
    for L in layers:
        if not L["gfx"]: continue
        if c.dry: continue
        try:
            L["source"].require_ready()
        except ValueError as exc:
            missing_gfx.append(f"{L['cl']['src']}: {exc}")
    if missing_gfx:
        print("✗ gfx 帧缓存缺失——区间渲染只复用不渲染 gfx，请先运行 vtx gfx")
        for error in missing_gfx[:8]:
            print(f"  - {error}")
        sys.exit(1)

    plan = RenderPlan(c=c, draft=draft, f=f, W=W, H=H, layers=layers,
                      nseg=int(os.environ.get("VTX_VIDEO_SEGMENTS", "8")))
    # B03（issue #27 / F-05）：布局延迟创建——cli 不再无条件 mkdir cache/，
    # preview 目录自建（parents 兜底零音频事件、prepare_layers 未先建 cache 的边角）。
    vdir = c.cache / "preview"; fstxn.mkdir(vdir, root=c.root)
    bounds = [(L["cl"]["dst_in"], L["cl"]["dst_in"] + L["cl"]["dur"]) for L in layers]
    t = time.monotonic()
    cuts = choose_cutpoints(bounds, c.dur, plan.nseg)   # 与整片同一切点集
    edges = [0] + cuts + [c.dur]
    parts = select_segments(edges, fa, fb)
    print(f"  段选取：全片切点 {cuts} → 与窗口相交 {len(parts)} 段 "
          f"（{', '.join(f'[{a},{b})' for a, b in parts)}）"
          f"，[timing] 切点计算: {time.monotonic() - t:.1f}s")

    segs = []
    for i, (a, b) in enumerate(parts):
        t = time.monotonic()
        sfile = vdir / f"subs_{a:06d}_{b:06d}.ass"
        plan.write_seg_ass(sfile, a, b)             # 段级字幕（段内相对时间，同整片机制）
        sl = [L for L in layers if L["cl"]["dst_in"] < b and L["cl"]["dst_in"] + L["cl"]["dur"] > a]
        key = plan._seg_content_key(a, b, sl, True, sfile)
        segp = vdir / f"rseg_{a:06d}_{b:06d}_{key}.mp4"
        if media_cache_valid(segp, frames=b - a):
            print(f"  ✓ 子段 {i} [{a},{b}) 已缓存，[timing] 段命中: "
                  f"{time.monotonic() - t:.1f}s")
            segs.append(segp); continue
        plan.composite(sl, segp, True, a=a, b=b, sub_file=sfile)
        print(f"[timing] 段合成 [{a},{b})（{b - a} 帧，{len(sl)} 层）: "
              f"{time.monotonic() - t:.1f}s")
        segs.append(segp)
    vcodec, enc = pick_vcodec(c, draft)
    # 画面拼接（-c copy，各子段同编码参数）→ 临时视频；随后与窗口音频 mux
    tmpv = vdir / f"range_{la}_{lb}.video.mp4"
    lst = vdir / f"range_{la}_{lb}.list.txt"
    # 与 composite.render_segmented 同一转义法：绝对 POSIX 路径 + 引号转义（-safe 0 已带）
    fstxn.atomic_write(lst, "".join(
        "file '" + s.resolve().as_posix().replace("'", "'\\''") + "'\n" for s in segs), root=c.root)
    t = time.monotonic()
    c.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
           "-c", "copy", str(tmpv)])
    print(f"[timing] 画面 concat（{len(segs)} 段 -c copy）: {time.monotonic() - t:.1f}s")

    t = time.monotonic()
    wav = vdir / f"range_{la}_{lb}.wav"
    _ensure_audio_wavs(c, fa, fb)                   # prepare 抽音频冷填充（键与 prepare 一致）
    g = _receipt_audio_gain(c)
    mix_audio_wav(c, wav, win=(fa, fb), gain_override=g)  # 同一 filtergraph（含 duck），边界 atrim
    print(f"[timing] 窗口音频混音（±{AUDIO_MARGIN_FRAMES}f 事件外延，"
          f"增益{'复用整片回执' if g is not None else '窗口自测'}）: {time.monotonic() - t:.1f}s")

    t = time.monotonic()
    out = vdir / f"range_{la}_{lb}.mp4"
    c.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmpv), "-i", str(wav),
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
           "-movflags", "+faststart", str(out)])
    tmpv.unlink(missing_ok=True)
    # 自检：总帧数必须精确 == 窗口帧数（复合段边界丢帧不静默）。dry-run 无产物
    # （P1-A：此前 ffprobe 真执行读空 → 误报「自检失败」），只打印将执行的命令即退出。
    if c.dry:
        print(f"  [dry-run] 跳过产物自检与落盘（帧数以段切点计算为准 = {fb - fa}）")
        print("✓ [dry-run] 区间预览命令验证通过（未产出文件）")
        return None
    fr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                         "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(out)],
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace").stdout.strip()
    if not (fr.isdigit() and int(fr) == fb - fa):
        print(f"✗ 区间渲染自检失败：总帧数 {fr or '?'} ≠ {fb - fa}"); sys.exit(1)
    print(f"[timing] 音视频 mux + 自检（{fr} 帧）: {time.monotonic() - t:.1f}s")
    print(f"✓ 区间预览：{out}（{enc}，总计 [timing] {time.monotonic() - t0:.1f}s）")
    return out
