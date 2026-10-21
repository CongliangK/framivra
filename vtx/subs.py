"""vtx 字幕信息性命令：subs render / preview / check。

不进 STAGES 流水线、不做 receipt 依赖门（与 gc/df/snapshot 同类）；
subs render 写 cache 走 manifest_record 记账。检查逻辑核心是纯函数
check_subtitles(subs, voice_windows, fps)——可脱离视频/音频产物独立测试。
"""
import hashlib, json, re, sys
from pathlib import Path

from . import fstxn
from .composite import RenderPlan
from .encoders import seg_encoder_kind
from .frame_source import png_source
from .hygiene import manifest_record, seg_key, tmp_run
from .infra import Ctx, filter_filename

# ---- 阈值常量（与 stages.audit 字幕基准对齐；2026-08-21 实测，见 stages 注释）----
CPS_FAIL = 9.0           # 阅读速度 >9 字/s 过快（≈P99）→ FAIL
CPS_WARN = 7.5           # >7.5 偏快（≈P92）→ WARN
SUB_MIN_DUR = 0.6        # 时长 <0.6s 过短 → WARN
ALIGN_FAIL = 1.5         # 与 voice 窗口起点偏移 ≥1.5s → FAIL（--align-threshold 可调）
ALIGN_WARN = 0.5         # ≥0.5s → WARN
ALIGN_RADIUS = 5.0       # 最近 voice 窗口距离 >5s → 视为无法配对（WARN，诚实条款）
MAX_LINES = 2            # ASS 硬换行（\N）行数 >2 → FAIL
# 行宽上限推算：PlayResX=1920、Fontsize=58、MarginL/R=60 → 可用宽 1800px；
# CJK 全角字宽 ≈ Fontsize=58px → 约 31 字，保守取 28（半角混排/描边留余量）。
MAX_LINE_CHARS = 28


# ---------------------------------------------------------------- check（纯逻辑）
def _union(wins):
    """区间并集（帧坐标，[in,out)）；相邻/重叠合并。"""
    out = []
    for a, b in sorted(wins):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _match_voice(sub, wins, fps):
    """字幕 → voice 窗口匹配。返回 (窗口|None, 偏移秒|None, 最近距离秒, 参照描述|None)。

    重叠最大者优先：偏移 = 字幕起点 − 窗口起点（负=超前，v39 语义）。
    无任何重叠取距 sub.in 最近的窗口（区间外沿距离）：偏移 = 字幕起点 −
    最近外沿（窗口末端滞后为正 / 窗口起点超前为负）——若仍参照窗口起点，
    字幕在长窗口结束后顺延开始时偏移会被夸大一个窗口长（红队 S1）。
    """
    best, best_ov = None, 0
    for w in wins:
        ov = min(sub["out"], w[1]) - max(sub["in"], w[0])
        if ov > best_ov:
            best, best_ov = w, ov
    if best is not None:
        return best, (sub["in"] - best[0]) / fps, 0.0, None
    near, dist = None, None
    for w in wins:
        d = max(w[0] - sub["in"], sub["in"] - w[1], 0)   # 区间外沿距离（含在内=0）
        if dist is None or d < dist:
            near, dist = w, d
    if near is None:
        return None, None, float("inf"), None
    if sub["in"] >= near[1]:
        ref, ref_desc = near[1], "滞后窗口结束"
    elif sub["in"] < near[0]:
        ref, ref_desc = near[0], "超前窗口开始"
    else:   # 在窗口内部但零长（in==out）等退化态：只报参照点不带方向词
        ref, ref_desc = near[0], "参照窗口起点" 
    return near, (sub["in"] - ref) / fps, dist / fps, ref_desc


def check_subtitles(subs, voice_windows, fps, align_fail=ALIGN_FAIL):
    """四项检查纯函数（帧坐标输入）：返回报告 dict（人类/机器两用）。

    voice_windows：voice 轨 events 并集 [(in_f, out_f)]；空列表 = 无法检查对齐。
    """
    results = []
    sorted_subs = sorted(subs, key=lambda s: s["in"])
    # 相邻重叠预判（两条都标 FAIL）：按 in 排序后 out > 后条 in 即重叠
    ov_idx = set()
    for i, (a, b) in enumerate(zip(sorted_subs, sorted_subs[1:])):
        if a["out"] > b["in"]:
            ov_idx.update((i, i + 1))
    for i, s in enumerate(sorted_subs):
        dur = (s["out"] - s["in"]) / fps
        n = len(s["text"].replace("\\N", ""))
        cps = n / dur if dur > 0 else float("inf")
        # ① 口播对齐
        if not voice_windows:
            align = {"status": "warn", "detail": "timeline 无 voice 轨，无法检查对齐"}
        else:
            w, off, dist, ref_desc = _match_voice(s, voice_windows, fps)
            if dist > ALIGN_RADIUS:
                align = {"status": "warn",
                         "detail": f"最近 voice 窗口距离 {dist:.1f}s > {ALIGN_RADIUS}s，无法检查对齐"}
            else:
                st = "ok"
                if abs(off) >= align_fail:
                    st = "fail"
                elif abs(off) >= ALIGN_WARN:
                    st = "warn"
                align = {"status": st, "offset_s": round(off, 2),
                         "detail": f"起点偏移 {off:+.2f}s" + (f"（{ref_desc}）" if ref_desc else "")}
        # ② 阅读速度
        if cps > CPS_FAIL:
            cps_r = {"status": "fail", "detail": f"CPS {cps:.1f} > {CPS_FAIL} 过快"}
        elif cps > CPS_WARN:
            cps_r = {"status": "warn", "detail": f"CPS {cps:.1f} > {CPS_WARN} 偏快"}
        elif dur < SUB_MIN_DUR:
            cps_r = {"status": "warn", "detail": f"时长 {dur:.2f}s < {SUB_MIN_DUR}s 过短"}
        else:
            cps_r = {"status": "ok", "detail": f"CPS {cps:.1f}"}
        # ③ 时间重叠
        if i in ov_idx:
            ov_r = {"status": "fail", "detail": "与相邻字幕时间重叠"}
        else:
            ov_r = {"status": "ok", "detail": ""}
        # ④ 行宽/行数
        lines = s["text"].split("\\N")
        wide = [ln for ln in lines if len(ln) > MAX_LINE_CHARS]
        if len(lines) > MAX_LINES:
            wd_r = {"status": "fail", "detail": f"行数 {len(lines)} > {MAX_LINES}"}
        elif wide:
            wd_r = {"status": "fail",
                    "detail": f"行宽超限（{max(len(ln) for ln in wide)} > {MAX_LINE_CHARS} 字）"}
        else:
            wd_r = {"status": "ok", "detail": ""}
        results.append({"index": i, "in_s": round(s["in"] / fps, 2),
                        "out_s": round(s["out"] / fps, 2), "text": s["text"],
                        "align": align, "cps": cps_r, "overlap": ov_r, "width": wd_r})
    checked = sum(1 for r in results if r["align"]["status"] != "warn"
                  or "无法检查" not in r["align"]["detail"])
    fail = any(r[k]["status"] == "fail" for r in results for k in ("align", "cps", "overlap", "width"))
    return {"total": len(results), "checked": checked,
            "coverage": f"{checked}/{len(results)}",
            "results": results, "summary": "FAIL" if fail else "PASS"}


def _print_check_report(rep, align_fail):
    """人类可读表格（对齐 audit 的 ✓/⚠/✗ 风格）。"""
    sym = {"ok": "✓", "warn": "⚠", "fail": "✗"}
    print(f"字幕检查：共 {rep['total']} 条，对齐检查 {rep['coverage']} 条"
          + ("" if rep["checked"] == rep["total"] else "（其余 WARN：无法配对 voice 窗口）"))
    for r in rep["results"]:
        parts = [f"{sym[r[k]['status']]} {r[k]['detail']}".rstrip()
                 for k in ("align", "cps", "overlap", "width") if r[k]["detail"]]
        text = r["text"].replace("\n", " ")[:24]
        print(f"  #{r['index']:03d} [{r['in_s']:.2f}-{r['out_s']:.2f}s] 「{text}」  " + "  ".join(parts))
    print(f"汇总：{rep['summary']}（对齐 FAIL 阈值 ±{align_fail}s / WARN ±{ALIGN_WARN}s）")


def run_check(c: Ctx, align_fail: float, dry: bool):
    """subs check 入口：零渲染，仅 timeline 文本；无任何产物也可跑。"""
    voice_tr = [tr for tr in c.tl.get("audio_tracks", []) if tr.get("name") == "voice"]
    wins = _union([(e["in"], e["out"]) for tr in voice_tr for e in tr.get("events", [])])
    rep = check_subtitles(c.tl.get("subtitles", []), wins, c.fps, align_fail)
    _print_check_report(rep, align_fail)
    out = c.out / "subs_check.json"
    if dry:
        print(f"[dry-run] 将写报告：{out}")
    else:
        fstxn.atomic_write(out, json.dumps(rep, ensure_ascii=False, indent=2), root=c.root)
    print(("PASS" if rep["summary"] == "PASS" else "FAIL") + f"｜报告：{out}")
    if rep["summary"] != "PASS":
        sys.exit(1)   # 与 validate 同语义：FAIL 即非零退出，可直接做 CI 门


# ---------------------------------------------------------------- render
def run_render(c: Ctx, dry: bool):
    """subs render：纯 ass 生成（不渲染），内容寻址——字节一致则已缓存跳过。"""
    dst = c.cache / "subs.ass"
    if dry:
        # dry-run 不写任何文件（含临时 ass），只报告意图与现状
        act = "已缓存（字节一致性留待真实运行比对）" if dst.exists() else "将生成写入"
        print(f"[dry-run] 将用 RenderPlan.write_global_ass 生成字幕 ass → {dst}（{act}）")
        print(f"PASS（dry-run）｜产物：{dst}")
        return
    fstxn.mkdir(c.cache, root=c.root)
    with tmp_run(c, "subs_render") as d:
        tmp = d / "subs.ass"
        RenderPlan(c=c, draft=False, f=1.0, W=c.w, H=c.h, nframes=None,
                   layers=[]).write_global_ass(tmp)   # 复用唯一权威实现，不另拼 ass
        new = tmp.read_bytes()
    if dst.exists() and dst.read_bytes() == new:
        print(f"✓ cache/subs.ass 已缓存（字节一致），跳过")
    else:
        fstxn.atomic_write(dst, new, root=c.root)
        key = hashlib.sha1(new).hexdigest()[:12]
        print(f"✓ 写入 cache/subs.ass（内容键 {key}）")
        manifest_record(c, dst, "subs", key)
    print(f"PASS｜产物：{dst}")


# ---------------------------------------------------------------- preview
def _parse_points(tokens, c: Ctx):
    """时间点 token → 帧号列表（`12.5`=秒浮点，`f360`=帧号）。"""
    out = []
    for t in tokens:
        if re.fullmatch(r"f\d+", t):
            out.append((int(t[1:]), t))
        else:
            try:
                sec = float(t)
            except ValueError:
                print(f"✗ 无法解析时间点 {t!r}（格式：秒 `12.5` 或帧号 `f360`）"); sys.exit(2)
            out.append((round(sec * c.fps), t))
    for f, t in out:
        if not (0 <= f < c.dur):
            print(f"✗ 时间点 {t}（帧 {f}）超出项目范围 [0, {c.dur})"); sys.exit(2)
    return out


def _active_subs(c: Ctx, f: int):
    """Frame-space intent is authoritative, including the exclusive out edge.

    RenderPlan puts ASS edges between frames on the actual rendering clock so
    supported 30fps burn-in agrees with this interval, rather than having the
    point preview reproduce an ASS quantization error.
    """
    active = []
    for s in c.tl["subtitles"]:
        start, end = RenderPlan._frame_number(s["in"]), RenderPlan._frame_number(s["out"])
        if start <= f < end:
            active.append(s)
    return active


def _write_point_ass(c: Ctx, path: Path, subs):
    """单帧预览 ass：头部用 RenderPlan.ass_head（样式逐字节一致）；时间覆盖首帧
    （静态帧渲染与全局 ass 在该时刻的栅格化逐像素一致）。"""
    plan = RenderPlan(c=c, draft=False, f=1.0, W=c.w, H=c.h, nframes=None, layers=[])
    lines = plan.ass_head()
    for s in subs:
        lines.append(f"Dialogue: 0,{plan._fc(0)},{plan._fc(c.fps)},Def,,0,0,0,,{s['text']}")
    fstxn.atomic_write(path, "\n".join(lines), root=c.root)


def _layer_ready(c: Ctx, f: int):
    """帧 f 处全部覆盖层素材是否现成（只读检查，不触发编码/渲染）。返回层列表或 None。

    缓存命中判定可能触发一次 nvenc 能力探测（进程级 memo 后仅首次，P2-1）。
    """
    layers = []
    # seg 键编码器 token 与 composite.prepare_layers 同源判定（方案二阶段3）——
    # 否则这里会按旧编码器键找文件，NVENC seg 恒 miss。
    seg_kind = seg_encoder_kind(c)
    for ti, tr in enumerate(c.tl["video_tracks"]):
        for ci, cl in enumerate(tr["clips"]):
            if not (cl["dst_in"] <= f < cl["dst_in"] + cl["dur"]):
                continue
            if tr.get("kind") == "gfx":
                source = png_source(c, cl)
                rel = max(0, f - cl["dst_in"])
                try:
                    source.require_ready(rel, rel + 1)
                except ValueError:
                    from . import gfx_svg
                    if gfx_svg.is_svg(cl):
                        raise  # Explicit SVG cache failures cannot become a successful black preview.
                    return None
                # The preview consumes one output frame. Keep readiness at the
                # same clip-local boundary; later PNGs need not exist yet.
                layers.append({"cl": cl, "source": source, "source_stop": rel + 1,
                               "x": cl.get("x", 0), "y": cl.get("y", 0),
                               "w2": None, "h2": None, "gfx": True, "ti": ti})
            else:
                windowed = any(k in cl for k in ("scale", "x", "y"))
                need_alpha = (cl.get("opacity", 1) < 1 or cl.get("fade_in_frames")
                              or cl.get("fade_out_frames"))
                skey = seg_key(c, cl, ti, ci, windowed, need_alpha,
                               "prores" if need_alpha else seg_kind)
                seg = c.cache / "seg" / f"{skey}.{'mov' if need_alpha else 'mp4'}"
                if not seg.exists():
                    return None
                layers.append({"cl": cl, "seg": seg, "x": cl.get("x", 0) if windowed else 0,
                               "y": cl.get("y", 0) if windowed else 0, "w2": None, "h2": None,
                               "gfx": False, "ti": ti})
    return layers


def _bg_frame(c: Ctx, f: int, d: Path, dry: bool):
    """背景帧来源三级优先：qc_clean 提帧 → 图层单帧合成 → 黑场兜底。
    返回 (png 路径|None, 来源描述)。dry 只返回描述不执行。"""
    qc = c.cache / "qc_clean.mp4"
    if qc.exists():
        p = d / "bg.png"
        if not dry:
            c.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{f * c.spf:.6f}", "-i", str(qc),
                   "-frames:v", "1", str(p)])
        return p, "cache/qc_clean.mp4 提帧"
    layers = _layer_ready(c, f)
    if layers is not None:
        plan = RenderPlan(c=c, draft=False, f=1.0, W=c.w, H=c.h, nframes=None, layers=[])
        p = d / "bg.mp4"
        if not dry:
            plan.composite(layers, p, False, a=f, b=f + 1)
            c.run(["ffmpeg", "-v", "error", "-y", "-i", str(p), "-frames:v", "1", str(d / "bg.png")])
        return d / "bg.png", "图层单帧合成（with_subs=False）"
    print(f"⚠ 帧 {f}：无 qc_clean.mp4 且图层素材不全——背景为黑场，仅字幕位置/样式预览")
    p = d / "bg.png"
    if not dry:
        c.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
               "-i", f"color=c=black:s={c.w}x{c.h}:d=1", "-frames:v", "1", str(p)])
    return p, "黑场画布（兜底）"


def _run_preview(c: Ctx, tokens, dry: bool, *, before_summary=None):
    """subs preview：每个时间点产一张 out/subs_preview_f<帧号>.png（≤5s/张）。"""
    points = _parse_points(tokens, c)
    seen = set()
    points = [p for p in points
              if not (p[0] in seen or seen.add(p[0]))]   # 去重保序（红队 S6）
    if not dry:
        c.out.mkdir(exist_ok=True)
    ok = True
    for f, tok in points:
        dst = c.out / f"subs_preview_f{f}.png"
        subs = _active_subs(c, f)
        with tmp_run(c, "subs_preview") as d:
            bg, src = _bg_frame(c, f, d, dry)
            ass = d / "point.ass"
            if not dry:
                _write_point_ass(c, ass, subs)
                r = c.run(["ffmpeg", "-v", "error", "-y", "-i", str(bg),
                           "-vf", f"subtitles=filename={filter_filename(ass)}",
                           "-frames:v", "1", str(dst)], need=False)
                if r.returncode != 0 or not dst.exists():
                    print(f"✗ 帧 {f}：字幕烧录失败"); ok = False; continue
        tag = "[dry-run] " if dry else ""
        print(f"{tag}✓ 帧 {f}（{tok}）：{dst}｜背景={src}｜活跃字幕 {len(subs)} 条")
    if before_summary is not None:
        before_summary()  # Full input recheck before any final PASS declaration.
    tail = "（dry-run 未落盘）" if dry else ""
    print(("PASS" if ok else "FAIL") + "｜产物：" +
          "、".join(str(c.out / f"subs_preview_f{f}.png") for f, _ in points) + tail)
    if not ok:
        sys.exit(1)


def run_preview(c: Ctx, tokens, dry: bool):
    from . import gfx_svg
    if gfx_svg.route(c) != 'svg-sequence':
        return _run_preview(c, tokens, dry)
    from .cache_state import verify_input_identity
    verify_input_identity(c)
    # _layer_ready and RenderPlan both retain this exact context and source.
    # Exceptions/cancellation only clear scope; cleanup never replaces errors.
    with gfx_svg.consumer_scope(c):
        return _run_preview(c, tokens, dry, before_summary=lambda: verify_input_identity(c))


# ---------------------------------------------------------------- CLI 路由
def _run_subs(c: Ctx, args):
    """vtx.py `subs` 命令入口。args = [子命令, 时间点...]；--dry-run 经 c.dry 生效。"""
    if not args:
        print("✗ 用法：subs render | subs preview <时间点...> | subs check"); sys.exit(2)
    sub, rest = args[0], args[1:]
    if sub == "render":
        run_render(c, c.dry)
    elif sub == "preview":
        if not rest:
            print("✗ 用法：subs preview <时间点...>（`12.5`=秒 / `f360`=帧号，可多个）"); sys.exit(2)
        run_preview(c, rest, c.dry)
    elif sub == "check":
        run_check(c, getattr(c, "align_threshold", ALIGN_FAIL), c.dry)
    else:
        print(f"✗ 未知 subs 子命令：{sub!r}（可用：render | preview | check）"); sys.exit(2)


def run_subs(c: Ctx, args):
    """入口包装：timeline 结构缺键/类型错给一行友好提示（信息性命令不走
    validate 依赖门，裸 KeyError 栈对调用方无用——红队 S4）。"""
    try:
        _run_subs(c, args)
    except (KeyError, TypeError) as e:
        print(f"✗ timeline 结构不满足 subs 命令：{e}（先跑 validate 看完整校验）")
        sys.exit(1)
