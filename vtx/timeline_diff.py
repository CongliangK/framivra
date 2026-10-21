"""vtx 时间线语义 diff —— 两份 timeline JSON 的逐 clip/字幕/音轨对比 + 影响面推断。

与 diffcmd.py（报告对比）并存：本模块处理「时间线语义」对比——clip 按 id/恒等元组
配对、字段级变化、seg/gfx/audio 缓存键逐 clip 重算比对（键是纯函数，重算即真值）、
分段合成段内容键失效推断（复刻 composite.choose_cutpoints + _seg_content_key 的
决定因素）、需重跑阶段与耗时预估、推荐修改路径（改生成器 / 幂等补丁 / 白名单手改）。

命令形态：`vtx diff <旧timeline> <新timeline>`——两参均为 timeline JSON（含
project+video_tracks 特征键）时自动走本模块，否则回落报告 diff（diffcmd）。
`--json PATH` 把机器可读结果落盘。纯读命令：不写任何产物、不做 receipt。

准确性口径（硬门槛）：失效 seg 键集合 == 按公式（hygiene.seg_key）重算的集合，
不多报——无关 clip 改动不产生受影响段；分段合成（vseg）层另按段内容键决定因素
单独推断（时长/位置变化推移切点时该层失效，但 per-clip seg 仍命中，两层分开报）。

耗时预估：基准常数 + 受影响比例缩放，估算依据写入 JSON（estimate_basis）；
历史 voice-ime 测量及指定环境见 docs/DEV.md §一；估算不是当前输入或机器的实测。
"""
import json
import sys
from pathlib import Path
from . import fstxn

from .composite import SEG_CUT_GUARD, choose_cutpoints
from .encoders import seg_encoder_kind
from .hygiene import gfx_key, seg_key

DEFAULT_NSEG = 8   # 与 composite.DEFAULT_SEGMENTS 同值（避免 import 产生环形依赖，取字面量并注释）


def _cold_rebuild_hints(project_root: Path) -> list:
    """R7（2026-09-15）：影响面之外的磁盘侧失效提示。

    timeline 侧影响面是「新旧时间线同用当前代码摘要」的纯函数比对，天然看不见
    两类磁盘侧失效（实测事故：R5 注释提交 4e4b756 后 diff 报 0 失效，实际全片
    ~900s 冷重建）：
      ① 合成代码摘要漂移——上一代段缓存由旧摘要产出（cache/vseg/.graph_digest
         记账），摘要变则全部段键翻转，与时间线内容无关；
      ② 段缓存目录被 gc 清空/半清——known 白名单不含 vseg_pass（R4-E2' A3），
         gc 后温态冻结语义失效，下次冷建。
    只提示不改判定：影响面数字仍以纯函数口径为准（键为纯函数即真值）。
    红队审查修订（2026-09-15）：gc 对 vseg 是整目录 rmtree，目录不存在才是最常见
    形态——原实现恰好在该形态下静默（漏报），改为显式报冷建。
    """
    hints = []
    vdir = project_root / "cache" / "vseg"
    try:
        from .composite import _graph_code_digest
        recorded = (vdir / ".graph_digest").read_text(encoding="utf-8").strip()
        current = _graph_code_digest()
        if recorded != current:
            hints.append(
                f"  ⚠ 合成代码摘要已漂移（磁盘段缓存 {recorded} → 当前 {current}）："
                "vseg 段/中间物将全部冷重建（全片段级合成，实测口径百秒~千秒级），与本次 timeline 改动无关")
    except (OSError, ImportError):
        pass
    if not vdir.is_dir():
        hints.append("  ⚠ cache/vseg 段缓存目录不存在（被 gc 回收或从未渲染）：video 阶段将全量冷建，非温态")
    elif not [p for p in vdir.iterdir() if p.suffix == ".mp4"]:
        hints.append("  ⚠ cache/vseg 无段文件（被 gc 或从未渲染）：video 阶段将全量冷建，非温态")
    return hints

# clip 几何/时间字段：任何变化推移同轨后续 clip / 切点 / 段内容——走生成器
GEOM_FIELDS = ("src", "src_in", "src_out", "dst_in", "dur", "scale", "x", "y",
               "opacity", "fade_in_frames", "fade_out_frames")
# 音频事件可补丁字段：不进任何视频缓存键，只影响 audio 阶段重混——幂等补丁脚本可安全改
AUDIO_FIELDS = ("volume", "pan", "fade_in_frames", "fade_out_frames")

# 耗时预估常数（秒），沿用历史估算；voice-ime 测量及指定环境见 docs/DEV.md §一。
# video 按「失效段帧数占比 × 全片渲染」+「per-clip seg 重编码」线性模型。
EST_VALIDATE = 3
EST_PREPARE_PER_SOURCE = 2
EST_AUDIO = 40
EST_MUX = 20
EST_QC = 11
# video 全片分段渲染（含 concat 自检）：formal=voice-ime N=8 x264 1080p 历史测量注记 89.8s；
# draft=voice-ime N=8 NVENC 540p 增量实测校准——只改 1 clip 时长（失效 2/8 段 4186 帧）
# 实跑 46.0s，按 90s 常数估 27s 误差 -41% 超阈，校准 150s 后估 42.0s 误差 +8.7%（2026-09-12）。
# draft 慢于 formal 的归因：draft 单段 filtergraph（61 层单线程）占比高于编码收益。
EST_VIDEO_FULL = {"draft": 150, "formal": 90}
EST_SEG_ENCODE_PER_FRAME = 0.05   # per-clip seg 重编码（x264 veryfast 1080p 经验值）


class DiffCtx:
    """键计算最小上下文：只提供 hygiene 键函数用到的属性（tl/root/fps/w/h/src_path）。
    不走 Ctx（其构造会校验 cache_dependencies 强制存在、算全量回执指纹——diff 是
    纯读命令，对 frozen 历史文件不该有这些前置要求）。"""

    dry = True   # seg 编码器判定（seg_encoder_kind→nvenc_ok）按 dry 短路：diff 纯读，
                 # 不真探测 NVENC（env 判定仍生效，与真实运行同一 VTX_SEG_NVENC 口径）

    def __init__(self, tl, root: Path):
        self.tl = tl
        self.root = root
        self.fps = tl["project"]["fps"]
        self.w = tl["project"]["width"]
        self.h = tl["project"]["height"]

    def src_path(self, sid):
        return self.root / next(s for s in self.tl["sources"] if s["id"] == sid)["path"]


# ---------------------------------------------------------------- 识别与加载
def is_timeline(d) -> bool:
    """timeline JSON 特征键：project(dict) + video_tracks(list) + sources(list)。"""
    return (isinstance(d, dict) and isinstance(d.get("project"), dict)
            and isinstance(d.get("video_tracks"), list)
            and isinstance(d.get("sources"), list))


def _load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        print(f"✗ 文件读取/解析失败：{p}：{e}")
        sys.exit(2)


# ---------------------------------------------------------------- 配对
def _clip_identity(cl):
    """clip 恒等元组（无 id 字段时的配对键）：EDL 五元组全等 = 未改动。"""
    return (cl.get("src"), cl.get("src_in"), cl.get("src_out"),
            cl.get("dst_in"), cl.get("dur"))


def pair_by_identity(a_list, b_list, identity):
    """通用两段式配对：① 恒等元组全等直配；② 剩余项在同组内（clip 按 src、
    字幕/事件按整体）按顺序贪心配对（捕捉「变了但同源同类」的改动）。
    返回 (matched=[(a,b)], added=[b], deleted=[a])。"""
    ma = {}
    for x in a_list:
        ma.setdefault(identity(x), []).append(x)
    matched, used_b = [], set()
    for y in b_list:
        k = identity(y)
        if ma.get(k):
            matched.append((ma[k].pop(0), y))
            used_b.add(id(y))
    ra = [x for k, v in ma.items() for x in v]
    rb = [y for y in b_list if id(y) not in used_b]
    # ② 同组 leftovers 按出现顺序配（同 src 的 clip 移窗/变长；字幕按序修订）
    while ra and rb:
        matched.append((ra.pop(0), rb.pop(0)))
    return matched, rb, ra


def pair_clips(ca, cb):
    """clip 配对：① 五元组全等；② 剩余按 src 分组后组内顺序配（同源 clip 的
    窗口/时长/位置变化），不同 src 的 leftovers 落入 add/del。"""
    return pair_by_identity(ca, cb, _clip_identity)


# ---------------------------------------------------------------- 键重算
def _clip_seg_key(c, tr, ti, cl):
    """普通 clip 的 seg 键（与 hygiene._current_keys 同推导：windowed/need_alpha）。
    源缺失（timeline 引用了不存在的媒体）返回 None——两侧同缺则视为相等，不多报。"""
    try:
        windowed = any(k in cl for k in ("scale", "x", "y"))
        need_alpha = (cl.get("opacity", 1) < 1 or
                      cl.get("fade_in_frames") or cl.get("fade_out_frames"))
        # 编码器 token 与 composite.prepare_layers 同源（方案二阶段3）：两侧同一 env
        # 口径，token 同值不影响「改 clip → 键变」的失效判定。
        return seg_key(c, cl, ti, 0, windowed, need_alpha,
                       "prores" if need_alpha else seg_encoder_kind(c))   # ci 不参与键，占位 0
    except OSError:
        return None


def _layer_ident(c, tr, ti, cl):
    """图层上游内容键 + 参数快照（复刻 composite._seg_content_key 的决定因素）。
    w2/h2 由 scale×源尺寸决定，两侧同源同值，以 None 占位保持相等语义。"""
    if tr.get("kind") == "gfx":
        try:
            ident = gfx_key(c, cl) if (c.root / cl["src"]).exists() else "MISSING"
        except OSError:
            ident = "MISSING"
    else:
        ident = _clip_seg_key(c, tr, ti, cl)
    return (ident, json.dumps(cl, sort_keys=True, ensure_ascii=False),
            cl.get("x", 0), cl.get("y", 0), None, None)


def _seg_dialogue(subs, fps, a, b):
    """Use the renderer's exact clipped, local-clock ASS dialogue bytes."""
    from types import SimpleNamespace
    from .composite import RenderPlan
    c = SimpleNamespace(fps=fps, tl={"subtitles": subs})
    plan = RenderPlan(c=c, draft=False, f=1.0, W=1920, H=1080)
    return "\n".join(line for line in plan.segment_ass_bytes(a, b).decode("utf-8").splitlines()
                     if line.startswith("Dialogue:"))


def _seg_layers(c, layers_by_track, a, b):
    """段内图层序列（∩ [a,b) 与 composite.render_segmented 同判据及叠放顺序）。"""
    items = []
    for tr, ti, idents in layers_by_track:
        for cl, ident in zip(tr["clips"], idents):
            if cl["dst_in"] < b and cl["dst_in"] + cl["dur"] > a:
                # 源缺失时 ident[0] 为 None——归一成标记串，保证可比对
                # （一侧缺失一侧存在 → 值不同 → 所在段正确判失效）
                items.append((ident[0] or "MISSING",
                              json.dumps(cl, sort_keys=True, ensure_ascii=False),
                              ident[2], ident[3]))
    return items


def _nseg() -> int:
    """分段数与 composite.build_video 同源取值（VTX_VIDEO_SEGMENTS，缺省 8），
    保证 diff 的段失效推断与实际渲染同一切点集。非法值静默回落 8。"""
    import os
    try:
        return max(1, int(os.environ.get("VTX_VIDEO_SEGMENTS", str(DEFAULT_NSEG))))
    except ValueError:
        return DEFAULT_NSEG


def _edges(tl, nseg):
    dur = tl["project"]["duration_frames"]
    bounds = [(cl["dst_in"], cl["dst_in"] + cl["dur"])
              for tr in tl["video_tracks"] for cl in tr["clips"]]
    # Empty tracks still render a segmented black canvas; use the renderer's
    # cut selection (including short-duration guard coalescing) in that case.
    return [0] + choose_cutpoints(bounds, dur, max(1, nseg)) + [dur]


# ---------------------------------------------------------------- 主 diff
def diff_timelines(tla, roota, tlb, rootb, nseg=None):
    """两份 timeline → 机器可读 dict（entries/impact/advice）。纯函数：无副作用。"""
    if nseg is None:
        nseg = _nseg()
    ca, cb = DiffCtx(tla, roota), DiffCtx(tlb, rootb)
    entries = []          # (kind, path, old, new, category) category ∈ gen/patch/white
    impact = {"seg_invalid": [], "seg_orphan": [], "gfx_invalid": [], "gfx_orphan": [],
              "audio_prepare_new": [], "audio_orphan": [],
              "sources_changed": {"visual": [], "audio_only": []},
              "vseg_invalid": {"count": 0, "total": 0, "segments": []},
              "stages": {}, "estimated_seconds": {}, "estimate_basis": ""}
    advice = {"generator": {"items": [], "reason": ""}, "patch_script": {"items": [], "reason": ""},
              "manual_whitelist": {"items": [], "reason": ""}}

    _CAT = {"gen": "generator", "patch": "patch_script", "white": "manual_whitelist"}

    def add(kind, path, old, new, cat):
        entries.append({"kind": kind, "path": path, "old": old, "new": new, "category": cat})
        advice[_CAT[cat]]["items"].append(path)

    advice["generator"]["reason"] = (
        "影响 clip 几何/时长/字幕的改动会推移同轨后续 clip 与分段切点，且 timeline 由"
        " make_subs.py 等生成器再生产（禁止手改）——此类改动应回到生成器改参数重生成")
    advice["patch_script"]["reason"] = (
        "纯音频参数（volume/pan/事件 fade/duck_params）不进任何视频缓存键，幂等补丁脚本"
        "（带防重施标记 + vtx snapshot 先行）可安全小改，走 R1 口径只重混音频")
    advice["manual_whitelist"]["reason"] = (
        "白名单仅 _meta 类注释性字段（不参与任何缓存键与渲染）可直接手改；runbook 原则："
        "其余一律生成器或幂等脚本")

    # ---- 顶层 / project ----
    for k in sorted(set(tla) | set(tlb)):
        if k in ("project", "sources", "video_tracks", "subtitles", "audio_tracks"):
            continue
        if k not in tla:
            add("add", k, None, "<新顶层键>", "white" if k == "_meta" else "gen")
        elif k not in tlb:
            add("del", k, "<旧顶层键>", None, "white" if k == "_meta" else "gen")
        elif tla[k] != tlb[k]:
            cat = "white" if k == "_meta" else "gen"
            add("change", k, _short(tla[k]), _short(tlb[k]), cat)
    for k in ("fps", "width", "height", "duration_frames", "cache_dependencies"):
        va, vb = tla["project"].get(k), tlb["project"].get(k)
        if va != vb:
            add("change", f"project.{k}", va, vb, "gen")

    # ---- sources ----
    # 画面引用集（P1 修复）：被任一非 gfx video clip 引用的源 id。同 id 换 path 时按此
    # 分流影响面——画面源变化进 seg 键（路径入 _fp 指纹）→ video 重渲；纯音频源变化
    # 只改 audio_key（prepare 按键提取）→ prepare/audio 重跑，不碰 video（此前
    # sources.* 前缀一律置 video=True 属误报）。
    vis_refs = {cl["src"] for tl_ in (tla, tlb) for tr in tl_["video_tracks"]
                if tr.get("kind") != "gfx" for cl in tr["clips"]}
    audio_refs = {e["src"] for tl_ in (tla, tlb) for t in tl_.get("audio_tracks", [])
                  for e in t.get("events", [])}
    sa = {s["id"]: s for s in tla["sources"]}
    sb = {s["id"]: s for s in tlb["sources"]}
    for sid in sorted(set(sa) - set(sb)):
        add("del", f"sources[{sid}]", sa[sid].get("path"), None, "gen")
        (impact["sources_changed"]["visual"] if sid in vis_refs
         else impact["sources_changed"]["audio_only"]).append(sid)
        if sid not in vis_refs:
            impact["audio_orphan"].append(sid)
    for sid in sorted(set(sb) - set(sa)):
        add("add", f"sources[{sid}]", None, sb[sid].get("path"), "gen")
        (impact["sources_changed"]["visual"] if sid in vis_refs
         else impact["sources_changed"]["audio_only"]).append(sid)
        if sid not in vis_refs:
            impact["audio_prepare_new"].append(sid)
    for sid in sorted(set(sa) & set(sb)):
        if sa[sid] != sb[sid]:
            add("change", f"sources[{sid}]", _short(sa[sid]), _short(sb[sid]), "gen")
            # 同 id 换 path/transform：画面源由 seg 键重算捕获（下文）；纯音频源必须
            # 重跑 prepare（audio_key=sha1(源指纹+…)，path 变即键变）+ audio 重混。
            if sid in vis_refs:
                impact["sources_changed"]["visual"].append(sid)
            else:
                impact["sources_changed"]["audio_only"].append(sid)
                impact["audio_prepare_new"].append(sid)

    # ---- 视频轨 / clips ----
    ta = {tr.get("name", f"#{i}"): (i, tr) for i, tr in enumerate(tla["video_tracks"])}
    tb = {tr.get("name", f"#{i}"): (i, tr) for i, tr in enumerate(tlb["video_tracks"])}
    # Compare surviving tracks only: inserting/deleting a track is already reported
    # below, but changing their relative z-order changes the renderer's layer list.
    order_a = [name for name in ta if name in tb]
    order_b = [name for name in tb if name in ta]
    if order_a != order_b:
        add("change", "video_tracks.order", order_a, order_b, "gen")
    for name in sorted(set(ta) | set(tb)):
        ia, tra = ta.get(name, (None, None))
        ib, trb = tb.get(name, (None, None))
        if name not in ta:
            add("add", f"video_tracks[{name}]", None,
                f"kind={trb.get('kind')}, {len(trb['clips'])} clips", "gen")
            matched, added, deleted = [], trb["clips"], []
        elif name not in tb:
            add("del", f"video_tracks[{name}]", f"kind={tra.get('kind')}", None, "gen")
            matched, added, deleted = [], [], tra["clips"]
        else:
            if tra.get("kind") != trb.get("kind"):
                add("change", f"video_tracks[{name}].kind", tra.get("kind"), trb.get("kind"), "gen")
            matched, added, deleted = pair_clips(tra["clips"], trb["clips"])
            for x, y in matched:
                for k in sorted(set(x) | set(y)):
                    va, vb = x.get(k), y.get(k)
                    if va == vb:
                        continue
                    label = f"{y.get('src')}@{x.get('dst_in')}"
                    add("change", f"video_tracks[{name}].clips[{label}].{k}",
                        va, vb, "gen" if k in GEOM_FIELDS else "gen")
            for y in added:
                add("add", f"video_tracks[{name}].clips[{y.get('src')}@{y.get('dst_in')}]",
                    None, _short(y), "gen")
            for x in deleted:
                add("del", f"video_tracks[{name}].clips[{x.get('src')}@{x.get('dst_in')}]",
                    _short(x), None, "gen")
        # 键重算（真值）：整轨增删与现有轨内 clip 增删共用同一处理。
        for x, y in matched:
            if tra.get("kind") == "gfx":
                ka = gfx_key(ca, x) if (ca.root / x["src"]).exists() else None
                kb = gfx_key(cb, y) if (cb.root / y["src"]).exists() else None
                if ka != kb:
                    impact["gfx_invalid"].append(
                        {"track": name, "clip": f"{y['src']}@{y['dst_in']}",
                         "old_key": ka, "new_key": kb})
            else:
                ka = _clip_seg_key(ca, tra, ia, x)
                kb = _clip_seg_key(cb, trb, ib, y)
                if ka != kb:
                    impact["seg_invalid"].append(
                        {"track": name, "clip": f"{y['src']}@{y['dst_in']}",
                         "old_key": ka, "new_key": kb,
                         "reencode_seconds_est": round((y["src_out"] - y["src_in"]) * EST_SEG_ENCODE_PER_FRAME, 1)})
        for y in added:
            if trb.get("kind") == "gfx":
                if (cb.root / y["src"]).exists():
                    impact["gfx_invalid"].append(
                        {"track": name, "clip": f"{y['src']}@{y['dst_in']}",
                         "old_key": None, "new_key": gfx_key(cb, y)})
            else:
                impact["seg_invalid"].append(
                    {"track": name, "clip": f"{y['src']}@{y['dst_in']}",
                     "old_key": None, "new_key": _clip_seg_key(cb, trb, ib, y),
                     "reencode_seconds_est": round((y["src_out"] - y["src_in"]) * EST_SEG_ENCODE_PER_FRAME, 1)})
        for x in deleted:
            if tra.get("kind") == "gfx":
                if (ca.root / x["src"]).exists():
                    impact["gfx_orphan"].append(
                        {"track": name, "clip": f"{x['src']}@{x['dst_in']}", "old_key": gfx_key(ca, x)})
            else:
                impact["seg_orphan"].append(
                    {"track": name, "clip": f"{x['src']}@{x['dst_in']}", "old_key": _clip_seg_key(ca, tra, ia, x)})

    # ---- 字幕 ----
    subs_a, subs_b = tla.get("subtitles", []), tlb.get("subtitles", [])
    ident = lambda s: (s.get("in"), s.get("out"), s.get("text"))
    matched, added, deleted = pair_by_identity(subs_a, subs_b, ident)
    for x, y in matched:
        for k in ("in", "out", "text"):
            if x.get(k) != y.get(k):
                add("change", f"subtitles[{_sub_label(x)}].{k}", x.get(k), y.get(k), "gen")
    for y in added:
        add("add", f"subtitles[{_sub_label(y)}]", None, y.get("text"), "gen")
    for x in deleted:
        add("del", f"subtitles[{_sub_label(x)}]", x.get("text"), None, "gen")

    # ---- 音轨 ----
    ata = {t.get("name", f"#{i}"): t for i, t in enumerate(tla.get("audio_tracks", []))}
    atb = {t.get("name", f"#{i}"): t for i, t in enumerate(tlb.get("audio_tracks", []))}
    audio_changed = bool(impact["audio_prepare_new"] or impact["audio_orphan"])
    for name in sorted(set(ata) | set(atb)):
        if name not in ata:
            add("add", f"audio_tracks[{name}]", None, f"{len(atb[name]['events'])} events", "patch")
            audio_changed = True
            continue
        if name not in atb:
            add("del", f"audio_tracks[{name}]", f"{len(ata[name]['events'])} events", None, "patch")
            audio_changed = True
            continue
        xa, xb = ata[name], atb[name]
        for k in ("duck", "duck_by", "duck_params"):
            if xa.get(k) != xb.get(k):
                add("change", f"audio_tracks[{name}].{k}", _short(xa.get(k)), _short(xb.get(k)), "patch")
                audio_changed = True
        ev_ident = lambda e: (e.get("src"), e.get("src_in", 0), e.get("in"), e.get("out"))
        m, ad, dl = pair_by_identity(xa.get("events", []), xb.get("events", []), ev_ident)
        for x, y in m:
            for k in sorted(set(x) | set(y)):
                if x.get(k) != y.get(k):
                    label = f"{x.get('src')}@{x.get('in')}"
                    add("change", f"audio_tracks[{name}].events[{label}].{k}",
                        x.get(k), y.get(k), "patch")
                    audio_changed = True
        for y in ad:
            add("add", f"audio_tracks[{name}].events[{y.get('src')}@{y.get('in')}]",
                None, _short(y), "patch")
            audio_changed = True
        for x in dl:
            add("del", f"audio_tracks[{name}].events[{x.get('src')}@{x.get('in')}]",
                _short(x), None, "patch")
            audio_changed = True

    # ---- 分段合成（vseg）段键失效推断：切点/段决定因素为纯函数，直接重算比对 ----
    edges_a = _edges(tla, nseg)
    edges_b = _edges(tlb, nseg)
    _, lay_a = _seg_layers_prep(tla, roota)
    lay_b = _seg_layers_prep(tlb, rootb)[1]
    dur_b = tlb["project"]["duration_frames"]
    inv_segs = []
    total_b = len(edges_b) - 1
    for si in range(total_b):
        a, b = edges_b[si], edges_b[si + 1]
        old_same = (len(edges_a) == total_b + 1)
        if old_same:
            oa, ob = edges_a[si], edges_a[si + 1]
        else:
            oa, ob = None, None
        if not old_same or oa != a or ob != b:
            inv_segs.append([a, b])
            continue
        la = _seg_layers(tla, lay_a, a, b)
        lb = _seg_layers(tlb, lay_b, a, b)
        da = _seg_dialogue(tla.get("subtitles", []), ca.fps, a, b)
        db = _seg_dialogue(tlb.get("subtitles", []), cb.fps, a, b)
        if la != lb or da != db:
            inv_segs.append([a, b])
    impact["vseg_invalid"] = {"count": len(inv_segs), "total": total_b,
                              "segments": inv_segs, "nseg": nseg,
                              "guard": SEG_CUT_GUARD}

    # ---- 阶段推断 + 耗时预估 ----
    # P1 修复：sources.* 变化不再一概触发 video——只有画面源（被非 gfx clip 引用）
    # 变化才置 video；纯音频源变化只走 prepare/audio。画面源变化即使 seg 键因文件
    # 缺失算不出（None），video 置位也不依赖键比对成功。
    video_changed = bool(entries and any(e["path"].startswith(("video_tracks", "subtitles", "project"))
                                         or e["path"] in ("version",) for e in entries)) \
        or bool(impact["sources_changed"]["visual"]) \
        or bool(impact["seg_invalid"] or impact["gfx_invalid"] or impact["seg_orphan"] or impact["gfx_orphan"])
    prepare_needed = bool(impact["sources_changed"]["visual"] or impact["sources_changed"]["audio_only"]
                          or impact["audio_prepare_new"] or impact["audio_orphan"])
    n_prepare = len(impact["sources_changed"]["visual"]) + len(impact["sources_changed"]["audio_only"]) \
        + len(impact["audio_prepare_new"]) + len(impact["audio_orphan"])
    stages = {"validate": bool(entries)}
    stages["prepare"] = prepare_needed   # prepare 对全部 sources 按键提取，任何源变化都重提取
    stages["gfx"] = bool(impact["gfx_invalid"] or impact["gfx_orphan"])
    stages["video"] = video_changed
    # 纯音频源替换 = 提取内容变了 → audio 必重混（不依赖音轨 entries 是否变化）；
    # 画面源若同时被音频事件引用（双料源），其替换同样重混
    src_audio_touched = bool({*impact["sources_changed"]["visual"],
                              *impact["sources_changed"]["audio_only"]} & audio_refs)
    stages["audio"] = audio_changed or bool(impact["sources_changed"]["audio_only"]) or src_audio_touched
    stages["mux"] = stages["video"] or stages["audio"]
    stages["qc"] = stages["mux"]
    impact["stages"] = stages

    est = {"validate": EST_VALIDATE if stages["validate"] else 0}
    if stages["prepare"]:
        est["prepare"] = EST_PREPARE_PER_SOURCE * max(1, n_prepare)
    if stages["gfx"]:
        est["gfx"] = 15   # 单页重渲经验下限，页多时可能低估；历史 CDP 测量见 docs/DEV.md §一
    if stages["video"]:
        total_frames = max(1, dur_b)
        inv_frames = sum(b - a for a, b in inv_segs)
        seg_enc = sum(x.get("reencode_seconds_est", 0) for x in impact["seg_invalid"])
        est["video"] = round(inv_frames / total_frames * EST_VIDEO_FULL["draft"] + seg_enc, 1)
        est_basis_v = (f"失效段帧数 {inv_frames}/{total_frames} × 全片分段渲染 "
                       f"{EST_VIDEO_FULL['draft']}s(draft)/{EST_VIDEO_FULL['formal']}s(formal)"
                       f"（voice-ime N=8 实测基准）+ per-clip seg 重编码 {seg_enc}s"
                       f"（{EST_SEG_ENCODE_PER_FRAME}s/帧经验值）")
    if stages["audio"]:
        est["audio"] = EST_AUDIO
    if stages["mux"]:
        est["mux"] = EST_MUX
    if stages["qc"]:
        est["qc"] = EST_QC
    impact["estimated_seconds"] = est
    impact["estimate_basis"] = (
        "线性模型，沿用历史估算常数；voice-ime 历史测量及环境见 docs/DEV.md §一"
        + ("；video：" + est_basis_v if stages["video"] else "")
        + "；非实测承诺，±30% 校准口径")

    advice["recommended_path"] = _recommended_path(advice, stages)
    return {"kind": "timeline", "entries": entries, "impact": impact, "advice": advice,
            "n_entries": len(entries)}


def _seg_layers_prep(tl, root):
    """(DiffCtx, [(tr, ti, [ident per clip])])——段图层恒等的预计算。"""
    c = DiffCtx(tl, root)
    layers = []
    for ti, tr in enumerate(tl["video_tracks"]):
        idents = [_layer_ident(c, tr, ti, cl) for cl in tr["clips"]]
        layers.append((tr, ti, idents))
    return c, layers


def _recommended_path(advice, stages):
    if not advice["generator"]["items"] and not advice["patch_script"]["items"]:
        return "none（无实质差异，或仅白名单字段——可直接手改）"
    if advice["generator"]["items"] and advice["patch_script"]["items"]:
        return "mixed：几何/字幕类走生成器重生成，音频参数类可并入幂等补丁脚本一次改完"
    if advice["generator"]["items"]:
        return "generator：改 make_subs.py 等生成器参数后再生 timeline（R2 口径）"
    return "patch_script：写幂等补丁脚本改音轨参数（R1 口径，snapshot 先行 + 防重施标记）"


def _sub_label(s):
    return f"{s.get('in')}-{s.get('out')}"


def _short(v, n=60):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s[:n] + "…" if len(s) > n else s


# ---------------------------------------------------------------- CLI
def run_timeline_diff(old_path, new_path, json_path=None, dry=False):
    """CLI 入口：打印人读摘要 + 可选 --json 落盘。退出码 PASS=0 / DIFF=1（与 diffcmd 一致）。"""
    ta, tb = _load(old_path), _load(new_path)
    if not (is_timeline(ta) and is_timeline(tb)):
        print("✗ 两份输入不都是 timeline JSON（缺 project/video_tracks/sources 特征键）；"
              "报告对比请直接 diff <旧报告.json> <新报告.json>")
        return 2
    res = diff_timelines(ta, Path(old_path).parent, tb, Path(new_path).parent)
    impact = res["impact"]
    print(f"对比：{old_path}  vs  {new_path}（类型：timeline / timeline，语义 diff）")
    if dry:
        print("  [dry-run] diff 为纯读命令，真实运行输出与此一致")
    for e in res["entries"]:
        if e["kind"] == "change":
            print(f"  · [变化] {e['path']}: {_short(e['old'], 40)} → {_short(e['new'], 40)}")
        elif e["kind"] == "add":
            print(f"  + [新增] {e['path']}: {_short(e['new'], 60)}")
        else:
            print(f"  - [删除] {e['path']}: {_short(e['old'], 60)}")
    if res["entries"]:
        print(f"── 影响面（缓存键重算比对，键为纯函数即真值）──")
        print(f"  seg 失效需重编码：{len(impact['seg_invalid'])} 个 clip"
              + (f"：{'、'.join(x['clip'] for x in impact['seg_invalid'][:8])}"
                 if impact["seg_invalid"] else ""))
        for x in impact["seg_invalid"]:
            print(f"    ⚡ {x['track']}[{x['clip']}]  {str(x['old_key'])[:12]} → {str(x['new_key'])[:12]}")
        print(f"  gfx 失效需重渲：{len(impact['gfx_invalid'])} 页"
              + (f"：{'、'.join(x['clip'] for x in impact['gfx_invalid'][:8])}" if impact["gfx_invalid"] else ""))
        print(f"  分段合成（vseg）失效：{impact['vseg_invalid']['count']}/{impact['vseg_invalid']['total']} 段"
              f"（nseg={impact['vseg_invalid'].get('nseg', 8)}，切点 guard=±{SEG_CUT_GUARD} 帧）")
        for a, b in impact["vseg_invalid"]["segments"][:10]:
            print(f"    ⚡ 段 [{a},{b})")
        if impact["vseg_invalid"]["segments"][10:]:
            print(f"    … 另 {len(impact['vseg_invalid']['segments']) - 10} 段")
        # R7 磁盘侧真相提示：上面是 timeline 侧纯函数比对（两份时间线同用当前代码
        # 摘要重算），0 失效 ≠ 温态命中——段/中间物缓存还受合成代码摘要与 gc 影响。
        # （打印点在函数尾统一执行，含 entries 为空的 PASS 分支。）
        chain = [s for s in ("validate", "prepare", "gfx", "video", "audio", "mux", "qc")
                 if impact["stages"].get(s)]
        print(f"  需重跑：{' → '.join(chain) or '无'}")
        est = impact["estimated_seconds"]
        print(f"  预估耗时：≈{sum(est.values()):.0f}s（{', '.join(f'{k} {v:.0f}s' for k, v in est.items() if v)}）"
              f"｜估算依据：{impact['estimate_basis']}")
        print("── 推荐修改路径 ──")
        print(f"  总体：{res['advice']['recommended_path']}")
        for cat, key in (("改生成器（R2）", "generator"), ("幂等补丁脚本（R1）", "patch_script"),
                         ("白名单手改", "manual_whitelist")):
            ad = res["advice"][key]
            if ad["items"]:
                shown = "、".join(ad["items"][:5]) + ("…" if len(ad["items"]) > 5 else "")
                print(f"  [{cat}] {len(ad['items'])} 项：{shown}")
                print(f"    理由：{ad['reason']}")
    # 磁盘侧预警独立于 timeline 差异打印（红队修订）："代码摘要漂移 + timeline 未变"
    # 正是本机制的核心场景——原实现挂在 if entries 分支内，无差异时静默漏报。
    for line in _cold_rebuild_hints(Path(new_path).parent):
        print(line)
    if res["entries"]:
        print(f"DIFF｜共 {len(res['entries'])} 条差异；受影响段 "
              f"{impact['vseg_invalid']['count']}/{impact['vseg_invalid']['total']}，"
              f"seg 重编码 {len(impact['seg_invalid'])}，gfx 重渲 {len(impact['gfx_invalid'])}")
        rc = 1
    else:
        print("PASS｜两份 timeline 无实质差异")
        rc = 0
    if json_path:
        jp = Path(json_path)
        fstxn.atomic_write(jp, json.dumps(res, ensure_ascii=False, indent=1))
        print(f"✓ 机器可读结果已写入 {json_path}")
    return rc


def _write_new_impact(path, result):
    """Explicit analysis outputs never overwrite an existing file or symlink."""
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def run_impact_capture(timeline_path, json_path=None, dry=False):
    """Capture a CPU/formal before identity without rendering or project writes."""
    import os
    from .impact import capture
    try:
        if os.environ.get('VTX_SEG_NVENC') != '0' and not os.environ.get('VTX_NO_NVENC'):
            raise ValueError('impact capture requires explicit CPU contract: VTX_SEG_NVENC=0')
        if json_path and dry:
            raise ValueError('--json and --dry-run cannot be combined')
        path = Path(timeline_path)
        timeline = json.loads(path.read_text(encoding='utf-8'))
        if not is_timeline(timeline):
            raise ValueError('capture input is not a timeline')
        nseg = int(os.environ.get('VTX_VIDEO_SEGMENTS', '8'))
        result = capture(timeline, path.parent, nseg=nseg)
        if json_path:
            _write_new_impact(json_path, result)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'Impact capture failed: {error}', file=sys.stderr)
        return 2


def run_snapshot_diff(before, timeline_path, json_path=None, dry=False):
    """Compare a preserved before snapshot with the current project inputs."""
    from .impact import capture, compare
    try:
        if json_path and dry:
            raise ValueError('--json and --dry-run cannot be combined')
        path = Path(timeline_path)
        timeline = json.loads(path.read_text(encoding='utf-8'))
        if not is_timeline(timeline):
            raise ValueError('snapshot comparison requires a current timeline')
        result = compare(before, capture(timeline, path.parent, **before['execution']))
        if json_path:
            _write_new_impact(json_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result['affected_nodes'] or result['audio_mix_changed'] else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'Impact analysis failed: {error}', file=sys.stderr)
        return 2
