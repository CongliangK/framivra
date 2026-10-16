"""vtx 渲染后质检（qc 阶段）：帧数/底轨切点帧比对/上层窗口生效/泄漏/响度归一/解码域真峰。

原 vtx.py qc 函数整体迁移（2026-09-10 模块化拆分），检查内容/顺序/名称与阈值不变。
方案三阶段1（2026-09-13）新增：响度检查 -vn+peak 并消费 loudness_cache 回执；
命中段免检（qc_pass_segs.json，见下方辅助函数注释）。
v2 §1.3（2026-09-13）新增：解码域真峰 TP 判定项（阈 -1.0 dBFS，与 gates G3 同一
权威值、同一回执不重测）——limiter 恒在 build_audio 尾部后，成片 TP 超阈 = 上游
削波/限幅失效，属产物可信度问题，归内核 qc。
"""
import json, re, sys
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import loudness_cache, fstxn
from .hygiene import gfx_dir   # R-05 探针口径对齐：gfx 内容域 bbox 需定位 png 序列目录

# ---- QC 阈值命名常量（值不变，仅命名；2026-09-10 收拢）----
QC_CUT_MEAN_MAX = 6.0        # 底轨切点帧比对：平均像素差上限
QC_WINDOW_MEAN_MIN = 5.0     # 上层窗口生效：平均像素差下限（或覆盖率达到下限即生效）
QC_WINDOW_COVER_MIN = 0.03   # 上层窗口生效：覆盖率下限
QC_PIXEL_DIFF = 24           # 「显著差异像素」判定阈值（0-255）
QC_LEAK_MEAN_MAX = 2.0       # 空窗泄漏：平均像素差上限
QC_LEAK_COVER_MAX = 0.02     # 空窗泄漏：覆盖率上限
# 响度归一分级（P6 修复 B-P1-1，总控裁定 2026-09-14 对齐 §1.2）：|I-目标| >3
# 硬 FAIL（增益链断裂，与 build_audio 终态校验同一硬阈）；(1.5, 3] **放行带
# WARN 注记**——限幅响度损耗属内容级（发布侧内容门 G2 拦截口径），边界内容 +
# aac 解码域 0.1~0.3 LU 漂移不得让 `vtx all` 在 qc 处 flaky 翻车（audio 阶段
# WARN 放行而 qc ±1.5 硬 FAIL 的门内矛盾就此消解）；≤1.5 静默通过（v1 语义）。
QC_LOUDNESS_TOL = 1.5        # 响度归一容差（LUFS，≤ 此值静默通过）
QC_LOUDNESS_FAIL = 3.0       # 响度归一硬 FAIL 阈（> 此值，对齐 build_audio）
TARGET_LUFS = -14            # 项目目标响度（EBU R128，与 stages.build_audio 同一权威值）
QC_TP_MAX = -1.0             # 解码域真峰阈（dBFS，v2 §1.3：G3 的 -1.0 为唯一权威）

# ---- QC 命中段免检记录（方案三阶段1，2026-09-13）----
# 记录 cache/qc_pass_segs.json：{"version":1,"final":{size,mtime_ns,frames},
# "segs":[{name,a,b,key,size,mtime_ns}],"checks":{检查名:上轮是否 pass}}。
# 免检判定（非 --strict，见 qc() 内）：final (size,mtime_ns) 与记录一致 + 检查项
# 上轮 pass + 探测帧全部落在 stat 一致的记录段 [a,b) 内 → 跳过实际探测（仍占
# checks 位，pass=True，detail 前缀「免检」——检查项数量不减少）。final 重封装
# （stat 变）→ 记录整体失效全检；段文件被换/重写（stat 变）→ 该段失配实测。
# 全局元数据项（帧数/时长/分辨率/响度/并发窗能量）不在此机制内，永远实测。
# 裁定记录（与方案原文的偏差，总控未应答现场问询、子代理按验收一致性裁定）：
# 原文「全检全过才写记录，有 ✗ 删记录」在存在持续假阴性 ✗ 的项目（bench_p2 恒
# 74/75，c4_mini 已证伪探针假阴性）会使记录永不建立、机制永不生效，与验收要求
# 「74/75 不变 + 温态免检生效」矛盾——改为按检查项记忆：每轮刷新记录，✗ 项永远
# 实测（下轮必复测，持续失败持续可见），逐项内容绑定的安全性与全过门等价。
_QC_SEG_RECORD = "qc_pass_segs.json"
_VSEG_NAME = re.compile(r"^seg(\d+)_(\d{6})_([0-9a-f]{12})\.mp4$")


def _load_qc_pass_record(c):
    """读免检记录；缺失/损坏/version 不符/final stat 失配/segs 空 → None（全检）。"""
    try:
        rec = json.loads((c.cache / _QC_SEG_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("version") != 1:
        return None
    fin = rec.get("final") or {}
    try:
        st = (c.out / "final.mp4").stat()
    except OSError:
        return None
    if fin.get("size") != st.st_size or fin.get("mtime_ns") != st.st_mtime_ns:
        return None   # final 重封装（stat 变）→ 记录整体失效
    if not isinstance(rec.get("segs"), list) or not rec["segs"] \
            or not isinstance(rec.get("checks"), dict):
        return None
    return rec


def _collect_active_segs(c):
    """当前生效段序列 → [{name,a,b,key,size,mtime_ns}]；不可得 → None（机制不生效）。
    段文件名只带段起点（seg{si}_{a:06d}_{key}.mp4），b = 下一段起点（末段 = c.dur）。
    以 cache/vseg/list.txt（最近一次段渲染的拼接清单）为准——直接 glob seg* 会把
    同槽旧键残留也当成生效段，产生重叠 [a,b) 区间：stat 未变的陈旧段可错误豁免
    被篡改的现役段所在帧（清单之外的一切不参与免检，见实现报告）。"""
    vdir = c.cache / "vseg"
    lst = vdir / "list.txt"
    if not lst.exists():
        return None
    names = []
    for line in lst.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^file '(.+)'$", line.strip())
        if m:
            names.append(Path(m.group(1)).name)
    parsed = []
    for nm in names:
        m = _VSEG_NAME.match(nm)
        if not m:
            return None   # 清单含非标准段名（理论不可达）→ 放弃，全检
        parsed.append((nm, int(m.group(2)), m.group(3)))
    if not parsed or parsed[0][1] != 0 \
            or any(parsed[i][1] >= parsed[i + 1][1] for i in range(len(parsed) - 1)):
        return None   # 段序列须从 0 起且起点严格递增（连续覆盖 [0,c.dur)）
    segs = []
    for i, (nm, a, key) in enumerate(parsed):
        try:
            st = (vdir / nm).stat()
        except OSError:
            return None
        segs.append({"name": nm, "a": a,
                     "b": parsed[i + 1][1] if i + 1 < len(parsed) else c.dur,
                     "key": key, "size": st.st_size, "mtime_ns": st.st_mtime_ns})
    return segs


def _write_qc_pass_record(c, frames_nb, checks):
    """每轮非 dry qc 结束刷新记录：segs/final 重新绑定当前 stat；checks 按检查名
    记忆（同名多项须全部 pass 才可免检——防同名 gfx 页一项假阴性被另一项真过
    掩盖成免检）。落盘失败不影响 qc 主流程（下轮全检而已）。"""
    mem = {}
    for x in checks:
        mem[x["name"]] = mem.get(x["name"], True) and bool(x["pass"])
    segs = _collect_active_segs(c) or []
    try:
        st = (c.out / "final.mp4").stat()
    except OSError:
        return
    rec = {"version": 1,
           "final": {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                     "frames": frames_nb},
           "segs": segs, "checks": mem}
    try:
        fstxn.atomic_write(c.cache / _QC_SEG_RECORD,
                           json.dumps(rec, ensure_ascii=False, indent=1))
    except OSError:
        pass


def qc(c):
    # dry-run 无产物可检（ffprobe 不在 dry 门控内，探测不存在的 final.mp4 会误报退出）
    # —— 整阶段跳过，真实运行时逐项检查（2026-09-11 门控）
    if getattr(c, "dry", False):
        print("  [dry-run] qc：产物未产出，跳过探测（命令预览模式不做质量检查）")
        return
    # 性能升级（2026-09-10，voice-ime 实测 qc 674s → 目标 <120s）三处病灶与对策：
    #   1) select=eq(n,N) 无输入 seek：每个探测点从片头解码到第 N 帧（多在 8000+ 帧），
    #      约 70 探测点 × 2 源串行 → 改 -ss 输入 seek（transcode 下帧精确）+ -frames:v 1；
    #   2) -count_frames 全片解码数帧 → 默认容器元数据（--strict 才解码计数）；
    #   3) 全片 qc_clean 基准轨 + 全部检查串行 → 探测帧现场合成底轨基准 +
    #      帧探测/ebur128/volumedetect 三组线程并行（墙钟 ≈ max）。
    rep = {"checks": []}
    def ok(name, passed, detail=""):
        rep["checks"].append({"name": name, "pass": bool(passed), "detail": detail})
        print(("  ✓ " if passed else "  ✗ ") + name + (f"  {detail}" if detail else ""))
    skipped_probes = []   # qc_report.json 的 skipped_probes 字段素材（免检项+覆盖区间）
    def ok_exempt(name, span):
        """免检项入账：照常占 checks 位（pass=True），detail 前缀「免检」——
        检查项数量与名称同全检，只是免实际探测。"""
        ok(name, True, f"免检（段 [{span[0]},{span[1]}) 内容键命中）")
        skipped_probes.append({"name": name, "seg": list(span)})
    final = c.out / "final.mp4"
    # 帧数/时长/分辨率：容器元数据（流头 nb_frames；缺失回落 duration×fps 四舍五入）。
    # -count_frames 逐帧解码全片实测数十秒，仅 --strict 时使用（真解码计数）。
    r = c.run(["ffprobe", "-v", "error", "-print_format", "json",
               "-select_streams", "v:0", "-show_entries", "stream=nb_frames,width,height",
               "-show_entries", "format=duration", str(final)])
    d = json.loads(r.stdout); st = d["streams"][0]
    dur = float(d["format"]["duration"])
    if getattr(c, "strict", False):
        r = c.run(["ffprobe", "-v", "error", "-count_frames", "-print_format", "json",
                   "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", str(final)])
        nb = int(json.loads(r.stdout)["streams"][0]["nb_read_frames"]); nb_src = "解码计数"
    else:
        nb0 = str(st.get("nb_frames") or "")
        nb = int(nb0) if nb0.isdigit() and int(nb0) > 0 else round(dur * c.fps)
        nb_src = "容器元数据"
    tracks = c.tl["video_tracks"]
    bottom_gfx = bool(tracks) and tracks[0].get("kind") == "gfx"

    # ---- 帧提取原语：-ss 输入 seek + -frames:v 1（精确取帧）----
    # 关键正确性点：-ss 在 -i 前（输出流帧号从 0 重计）→ 不再保留 select=eq(n,N)，
    # 用输入 seek 时刻取帧；transcode 模式下输入 seek 为帧精确（从关键帧解码前向
    # 丢弃到目标时刻）。seek 语义 = 输出 pts ≥ 目标的首帧，故目标须落在
    # (pts[n-1], pts[n]] = ((n-1)/fps, n/fps]：取 (n-0.2)/fps（n=0 用 0）。
    # 已对 final/qc_clean/4 个源 × 多帧位验证与旧 select=eq(n,N) 提帧逐字节一致。
    def gray_frame(path: Path, n: int, pre_crop: str = "", tag: str = "") -> bytes:
        vf = (f"{pre_crop}," if pre_crop else "") + "scale=48:27,format=gray"
        tmp = c.cache / f"qc_probe_{tag}.raw"
        ss = f"{max(0.0, (n - 0.2) / c.fps):.6f}" if n else "0"
        c.run(["ffmpeg", "-v", "error", "-y", "-ss", ss,
               "-i", str(path), "-an", "-vf", vf, "-frames:v", "1", "-f", "rawvideo", str(tmp)])
        b = tmp.read_bytes(); tmp.unlink()
        return b
    # 底轨基准帧（等价于全片 qc_clean.mp4 在该帧的像素）：qc_clean 本就是 tracks[0]
    # 底轨 clip 全屏无叠层的第二遍合成，探测帧现场从源视频提帧（src_in + 帧偏移，
    # 输入 seek）过同一 crop169+scale 链即等价；无 clip 覆盖 → 黑帧（画布为黑场）。
    # 探测点均取 clip 中段，天然避开 fade 边界。
    _BLACK = bytes(48 * 27)
    def bottom_frame(df: int, tag: str, post_crop: str = None) -> bytes:
        if bottom_gfx:  # 底轨是 gfx（无源视频）：回落全片 qc_clean 基准轨
            return gray_frame(c.cache / "qc_clean.mp4", df,
                              pre_crop=f"crop={post_crop}" if post_crop else "", tag=tag)
        for cl in sorted(tracks[0]["clips"], key=lambda x: x["dst_in"]):
            if cl["dst_in"] <= df < cl["dst_in"] + cl["dur"] and "src_in" in cl:
                sf = cl["src_in"] + (df - cl["dst_in"])
                # post_crop：窗口 clip 检查时对基准帧施加与成片相同的放置区域裁剪
                # （旧版对 qc_clean 亦施加同一 crop，语义对齐）
                pre = (f"{c.crop169(cl['src'])},scale={c.w}:{c.h}:flags=lanczos"
                       + (f",crop={post_crop}" if post_crop else ""))
                return gray_frame(c.src_path(cl["src"]), sf, pre_crop=pre, tag=tag)
        return _BLACK
    def fdiff(n: int, crop: str = None, tag: str = ""):
        a = gray_frame(final, n, pre_crop=f"crop={crop}" if crop else "", tag=tag + "a")
        b = bottom_frame(n, tag=tag + "b", post_crop=crop)
        mean = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
        cover = sum(1 for x, y in zip(a, b) if abs(x - y) > QC_PIXEL_DIFF) / len(a)
        return mean, cover

    # ---- 命中段免检（方案三阶段1）：--strict 整体禁用；记录不可用 → 全检 ----
    # 判定在任务生成阶段做：免检项不 submit 探测但仍占 checks 位（输出顺序/名称
    # 与全检完全一致）。_seg_rec 由 _load_qc_pass_record 校验过 final stat 绑定。
    _seg_rec = None if getattr(c, "strict", False) else _load_qc_pass_record(c)
    exempt = {}    # 任务键 -> 覆盖段区间 (a,b)（免测项）
    cut_sf = {}    # cut 免测项的比对帧 sf（检查名要显示 src:帧号）
    def seg_span_of(f0):
        """f0 是否落在某 stat 与记录一致的段 [a,b) 内；是 → (a,b)，否 → None。"""
        if _seg_rec is None:
            return None
        for s in _seg_rec["segs"]:
            if s["a"] <= f0 < s["b"]:
                try:
                    st = (c.cache / "vseg" / s["name"]).stat()
                except OSError:
                    return None
                if st.st_size == s["size"] and st.st_mtime_ns == s["mtime_ns"]:
                    return (s["a"], s["b"])
                return None   # 段文件被换/重写（stat 变）→ 该段失配实测
        return None
    def check_exempt(frames, name):
        """检查项免测判定：上轮该名 pass 且全部探测帧都在 stat 一致段内 →
        合并覆盖区间 (min_a, max_b)；否则 None。"""
        if _seg_rec is None or not _seg_rec["checks"].get(name):
            return None
        spans = [seg_span_of(f0) for f0 in frames]
        if any(s is None for s in spans):
            return None
        return (min(a_ for a_, _ in spans), max(b_ for _, b_ in spans))

    def gfx_content_crop(cl):
        """gfx overlay 内容域 crop（R-05 探针口径对齐，2026-09-17 定谳单）：
        从 png 稳态帧（dur//2）alpha 通道提取内容 bbox，叠加 clip x/y 放置偏移，
        偶对齐后作 fdiff 采样域。背景：c4_mini 类「全画布 png+小内容条带」页
        （内容仅占画布 4.63%）在整帧 48:27 口径被稀释——放置域实测 cover 82%
        vs 整帧 2.9%，双阈双低误判「未生效」恒红。png 缺失/读取失败/内容全透明
        （空页）/矩形退化 → None 回落整帧口径（旧行为）。仅改采样域：阈值、
        检查名、checks 数量全部不变；域内信号单调不弱于整帧，既有 PASS 不翻。"""
        try:
            png = gfx_dir(c, cl) / f"f{cl['dur'] // 2:04d}.png"
            if not png.is_file():
                return None
            with png.open("rb") as fh:
                head = fh.read(24)
            if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
                return None
            pw = int.from_bytes(head[16:20], "big")
            ph = int.from_bytes(head[20:24], "big")
            if pw <= 0 or ph <= 0:
                return None
            tmp = c.cache / "qc_gfx_bbox.raw"
            c.run(["ffmpeg", "-v", "error", "-y", "-i", str(png), "-vf", "format=rgba",
                   "-frames:v", "1", "-f", "rawvideo", str(tmp)], need=False)
            raw = tmp.read_bytes(); tmp.unlink()
            if len(raw) < pw * ph * 4:
                return None
            # alpha>24 视为内容（与 QC_PIXEL_DIFF 同量级的存在性阈）；
            # translate + 逐行 find 全 C 路径，1080p 单帧毫秒级。
            t = raw[3::4].translate(bytes(1 if v > 24 else 0 for v in range(256)))
            x0 = y0 = x1 = y1 = -1
            for y in range(ph):
                row = t[y * pw:(y + 1) * pw]
                i = row.find(1)
                if i < 0:
                    continue
                if y0 < 0:
                    y0 = y
                y1 = y
                if x0 < 0 or i < x0:
                    x0 = i
                j = row.rfind(1)
                if j > x1:
                    x1 = j
            if x0 < 0 or y0 < 0:
                return None   # 内容全透明（空页）→ 回落整帧，判定将如实 FAIL
            ox = int(cl.get("x", 0)); oy = int(cl.get("y", 0))
            cx0 = max(0, x0 + ox); cy0 = max(0, y0 + oy)
            cx1 = min(c.w, x1 + 1 + ox); cy1 = min(c.h, y1 + 1 + oy)
            w = cx1 - cx0; h = cy1 - cy0
            if w < 2 or h < 2:
                return None
            w -= w % 2; h -= h % 2   # 偶对齐（对齐 composite 放置 //2*2 口径）
            return f"{w}:{h}:{cx0}:{cy0}"
        except Exception:
            return None   # 任何失败回落整帧——探针永不因本 helper 阻断 qc

    # ---- 并行任务收集（结果按 timeline 顺序回填，checks 顺序不变）----
    tasks = []   # (key, callable)
    ti_ = 0
    def launch(fn):
        nonlocal ti_
        tasks.append((f"t{ti_}", fn)); ti_ += 1
    # 底轨（tracks[0]）全屏 clip 的切点帧比对：比对语义 = qc_clean 基准帧（仅底轨
    # 全屏合成）vs 源帧。qc_clean 去全片化后，前者由 bottom_frame 现场合成（同
    # crop169+scale 链，像素等价），后者不变。
    cut_res = {}
    def cut_diff(cl, j, key):
        df = cl["dst_in"] + j; sf = cl["src_in"] + j
        a = bottom_frame(df, tag=f"c{df}_")
        b = gray_frame(c.src_path(cl["src"]), sf,
                       pre_crop=f"{c.crop169(cl['src'])},scale={c.w}:{c.h}:flags=lanczos", tag=f"c{df}_")
        mean = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
        # 注意：check 名沿用旧版 dst={cl['dst_in']}（旧版打印的也是 clip 起点而非比对帧 df）
        cut_res[key] = (sf, mean)
    if tracks and not bottom_gfx:
        for cl in sorted(tracks[0]["clips"], key=lambda x: x["dst_in"]):
            if "src_in" not in cl: continue
            j = max(2, cl.get("fade_in_frames", 0) + 1)
            if cl.get("fade_out_frames"): j = max(j, cl["dur"] - cl["fade_out_frames"] - 1)
            j = min(j, cl["dur"] - 1)
            key = f"cut:{cl['dst_in']}:{cl['src']}"
            sf = cl["src_in"] + j   # 检查名显示的源帧号（免检路径无 cut_diff 可读）
            e = check_exempt([cl["dst_in"] + j],
                             f"底轨切点帧比对 dst={cl['dst_in']} src={cl['src']}:{sf}")
            if e is not None:
                exempt[key] = e; cut_sf[key] = sf
            else:
                launch(lambda cl=cl, j=j, key=key: cut_diff(cl, j, key))
    # 上层轨（tracks[1:]）逐 clip 窗口生效检查：窗口 clip 比对其放置区域，全屏 clip 比对整帧
    up_res = {}
    for tr in tracks[1:]:
        for ci, cl in enumerate(tr["clips"]):
            if tr.get("kind") == "gfx":
                probes = sorted({cl["dst_in"] + cl["dur"] // 4, cl["dst_in"] + cl["dur"] // 2,
                                 cl["dst_in"] + cl["dur"] - cl["dur"] // 4})
                key = f"gfx:{id(cl)}"
                e = check_exempt(probes, f"gfx 窗口 {Path(cl['src']).stem} 生效")
                if e is not None:
                    exempt[key] = e
                else:
                    gcrop = gfx_content_crop(cl)   # R-05 探针口径对齐（None=整帧回落）
                    launch(lambda probes=probes, key=key, gcrop=gcrop:
                           up_res.__setitem__(key, [fdiff(m, crop=gcrop, tag=f"{m}_{i}") for i, m in enumerate(probes)]))
                continue
            windowed = any(k in cl for k in ("scale", "x", "y"))
            m = cl["dst_in"] + cl["dur"] // 2
            if windowed:
                key = f"win:{id(cl)}"
                e = check_exempt([m], f"窗口 clip {tr['name']} @frame {m} 生效")
                if e is not None:
                    exempt[key] = e
                    continue   # 免检路径无需 crop（c.probe 也可省一次）
                i = c.probe(c.src_path(cl["src"]))
                sc = cl.get("scale", 1.0)
                w2 = int(i["w"] * sc); h2 = int(i["h"] * sc)
                crop = f"{w2}:{h2 + 2}:{max(0, cl.get('x', 0) - 10)}:{max(0, cl.get('y', 0) - 10)}"
                launch(lambda m=m, crop=crop, key=key: up_res.__setitem__(key, fdiff(m, crop=crop, tag=f"{m}_")))
            else:
                key = f"full:{id(cl)}"
                e = check_exempt([m], f"全屏 clip {tr['name']} @frame {m} 生效")
                if e is not None:
                    exempt[key] = e
                else:
                    launch(lambda m=m, key=key: up_res.__setitem__(key, fdiff(m, tag=f"{m}_")))
    # 空窗帧（无任何上层/字幕覆盖）泄漏检查；底轨非 gfx 时额外避开底轨 fade 边界
    # （现场提帧基准不含 fade 混黑，落入 fade 带会产生假差异）
    covered = [(s["in"], s["out"]) for s in c.tl["subtitles"]]
    for tr in tracks[1:]:
        covered += [(cl["dst_in"], cl["dst_in"] + cl["dur"]) for cl in tr["clips"]]
    def in_bottom_fade(f0):
        if bottom_gfx: return False
        for cl in tracks[0]["clips"]:
            if cl["dst_in"] <= f0 < cl["dst_in"] + cl["dur"]:
                off = f0 - cl["dst_in"]
                return off < cl.get("fade_in_frames", 0) or off >= cl["dur"] - cl.get("fade_out_frames", 0)
        return False
    free_frame = next((f0 for f0 in range(c.dur - 1, 0, -1)
                       if not any(a_ <= f0 < b_ for a_, b_ in covered) and not in_bottom_fade(f0)), None)
    free_res = {}
    if free_frame is not None:
        e = check_exempt([free_frame], f"空窗无叠加泄漏 @frame {free_frame}")
        if e is not None:
            exempt["f"] = e
        else:
            launch(lambda: free_res.__setitem__("f", fdiff(free_frame, tag=f"{free_frame}_")))
    # 响度归一验证（EBU R128 整合响度应 ≈ 目标值）——与帧探测并行。
    # 方案三阶段1（2026-09-13）：①加 -vn 免整遍视频解码（-f null 默认流选择会连
    # 视频一起解码，本机实测 4.9s→0.4s）；②peak=true 顺带测 TP 归档回执
    # （vtx/loudness_cache.py——同一解码域输入全链只测一次）；③先查回执，命中免测
    # （内容键绑定，文件任何变动自然失效重测，不跨版本复用数字）。
    # v2 §1.3（2026-09-13）：TP 从「只归档回执」升为内核 qc 判定项（阈 -1.0 dBFS，
    # 与 gates G3 同一权威值、同一回执不重测）——检查项见下方 checks 回填段。
    def loudness():
        hit = loudness_cache.lookup(final, root=c.root)
        if hit is not None:
            return hit["i_lufs"], hit["tp_dbfs"], True
        def measure():
            r2 = c.run(["ffmpeg", "-nostats", "-vn", "-i", str(final),
                        "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"])
            mI = re.search(r"I:\s*(-?[\d.]+) LUFS", r2.stderr)
            mP = re.search(r"Peak:\s*(-?[\d.]+) dBFS", r2.stderr)
            return (float(mI.group(1)) if mI else None,
                    float(mP.group(1)) if mP else None)
        r = loudness_cache.measure_and_store(final, measure, root=c.root)
        return r["i_lufs"], r["tp_dbfs"], False
    def vol(f0, f1):
        r = c.run(["ffmpeg", "-v", "info", "-ss", f"{f0*c.spf:.3f}", "-t", f"{(f1-f0)*c.spf:.3f}",
                   "-i", str(final), "-map", "0:a", "-af", "volumedetect", "-f", "null", "-"])
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
        return float(m.group(1)) if m else -999
    stacked = None
    marks = [(e["in"], e["out"]) for tr in c.tl["audio_tracks"] for e in tr["events"]]
    for f0 in range(0, c.dur, 24):
        active = sum(1 for a_, b_ in marks if a_ <= f0 < b_)
        if stacked is None or active > stacked[0]:
            stacked = (active, f0)
    # 并发窗探测帧域防御（P6 修复 B-P3-5a）：v1 直接取 stacked[1]+2..+10，短 timeline
    # （dur < 探测帧上界）时 -ss 越片尾 volumedetect 空读 -999 假 FAIL；无任何
    # 音频事件（stacked None）时 v1 在 dry 外路径裸 TypeError。
    vol_span = None
    if stacked is not None:
        v1_end = min(stacked[1] + 10, c.dur)
        v1_beg = min(stacked[1] + 2, max(v1_end - 1, 0))
        vol_span = (v1_beg, v1_end)
    if c.dry:
        loud_v, loud_tp, loud_hit, v1 = None, None, False, -999   # dry-run 不执行，直接走缺省
    else:
        # 三组互不依赖：帧探测（内部 8 线程并发 ffmpeg）/ ebur128 / volumedetect 并行，墙钟 ≈ max
        with ThreadPoolExecutor(3) as gex:
            def run_probes():
                # 每任务独立 ffmpeg 子进程；结果按 key 回填 dict，报告顺序不受并发影响
                # （免检项在生成阶段已被剔除，此处全部为需实测任务）
                with ThreadPoolExecutor(8) as pex:
                    futs = [pex.submit(copy_context().run, fn) for _, fn in tasks]
                    for f in futs: f.result()
            fp = gex.submit(copy_context().run, run_probes)
            fl = gex.submit(copy_context().run, loudness)
            fv = gex.submit(copy_context().run, lambda: vol(*vol_span) if vol_span else -999)
            loud_v, loud_tp, loud_hit = fl.result(); v1 = fv.result(); fp.result()

    # ---- 按原顺序回填 checks（内容/顺序/名称与旧版一致）----
    ok(f"成片帧数 = {c.dur}", nb == c.dur, f"实测 {nb}（{nb_src}）")
    ok(f"成片时长 ≈ {c.dur * c.spf:.2f}s", abs(dur - c.dur * c.spf) < 0.05, f"实测 {dur:.3f}s")
    ok(f"分辨率 {c.w}x{c.h}", st["width"] == c.w and st["height"] == c.h)
    if tracks and not bottom_gfx:
        for cl in sorted(tracks[0]["clips"], key=lambda x: x["dst_in"]):
            if "src_in" not in cl: continue
            key = f"cut:{cl['dst_in']}:{cl['src']}"
            if key in exempt:
                ok_exempt(f"底轨切点帧比对 dst={cl['dst_in']} src={cl['src']}:{cut_sf[key]}",
                          exempt[key])
                continue
            sf, d_ = cut_res[key]
            ok(f"底轨切点帧比对 dst={cl['dst_in']} src={cl['src']}:{sf}", d_ < QC_CUT_MEAN_MAX, f"平均像素差 {d_:.2f}/255")
    for tr in tracks[1:]:
        for ci, cl in enumerate(tr["clips"]):
            if tr.get("kind") == "gfx":
                key = f"gfx:{id(cl)}"
                if key in exempt:
                    ok_exempt(f"gfx 窗口 {Path(cl['src']).stem} 生效", exempt[key])
                    continue
                stats = up_res[key]
                dmax = max(s[0] for s in stats); cov = max(s[1] for s in stats)
                ok(f"gfx 窗口 {Path(cl['src']).stem} 生效", dmax > QC_WINDOW_MEAN_MIN or cov > QC_WINDOW_COVER_MIN,
                   f"采样 {[cl['dst_in'] + cl['dur'] // 4, cl['dst_in'] + cl['dur'] // 2, cl['dst_in'] + cl['dur'] - cl['dur'] // 4]} 最大均值差 {dmax:.1f}，覆盖 {cov*100:.1f}%")
                continue
            windowed = any(k in cl for k in ("scale", "x", "y"))
            m = cl["dst_in"] + cl["dur"] // 2
            if windowed:
                key = f"win:{id(cl)}"
                if key in exempt:
                    ok_exempt(f"窗口 clip {tr['name']} @frame {m} 生效", exempt[key])
                    continue
                d_, cov = up_res[key]
                ok(f"窗口 clip {tr['name']} @frame {m} 生效", d_ > QC_WINDOW_MEAN_MIN or cov > QC_WINDOW_COVER_MIN,
                   f"区域差 {d_:.1f}，覆盖 {cov*100:.1f}%")
            else:
                key = f"full:{id(cl)}"
                if key in exempt:
                    ok_exempt(f"全屏 clip {tr['name']} @frame {m} 生效", exempt[key])
                    continue
                d_, cov = up_res[key]
                ok(f"全屏 clip {tr['name']} @frame {m} 生效", d_ > QC_WINDOW_MEAN_MIN or cov > QC_WINDOW_COVER_MIN,
                   f"整帧差 {d_:.1f}，覆盖 {cov*100:.1f}%")
    if free_frame is not None:
        if "f" in exempt:
            ok_exempt(f"空窗无叠加泄漏 @frame {free_frame}", exempt["f"])
        else:
            d_, cov = free_res["f"]
            ok(f"空窗无叠加泄漏 @frame {free_frame}", d_ < QC_LEAK_MEAN_MAX and cov < QC_LEAK_COVER_MAX, f"差异 {d_:.2f}，覆盖 {cov*100:.1f}%")
    # 响度/TP 检查项（P6 修复 B-P3-4）：测量失败（None）不再是「检查项静默消失」
    # ——final 的音频流异常到测不出响度/真峰，属产物可信度问题，显性 FAIL。
    if loud_v is not None:
        dev = abs(loud_v - TARGET_LUFS)
        if dev > QC_LOUDNESS_FAIL:
            ok(f"响度归一 ≈ {TARGET_LUFS:g} LUFS", False,
               f"实测 {loud_v} LUFS（偏离 {dev:.1f} LU >{QC_LOUDNESS_FAIL:g}——疑增益链断裂，"
               f"对齐 build_audio 终态硬阈）" + ("（回执命中，免测）" if loud_hit else ""))
        elif dev > QC_LOUDNESS_TOL:
            ok(f"响度归一 ≈ {TARGET_LUFS:g} LUFS", True,
               f"实测 {loud_v} LUFS（偏离 {dev:.1f} LU ∈({QC_LOUDNESS_TOL:g},{QC_LOUDNESS_FAIL:g}]——"
               f"限幅响度损耗属内容级，WARN 放行；发布侧内容门 G2 拦截口径）"
               + ("（回执命中，免测）" if loud_hit else ""))
        else:
            ok(f"响度归一 ≈ {TARGET_LUFS:g} LUFS", True,
               f"实测 {loud_v} LUFS" + ("（回执命中，免测）" if loud_hit else ""))
    else:
        ok(f"响度归一 ≈ {TARGET_LUFS:g} LUFS", False,
           "ebur128 解析失败（final 音频流异常或不可测）")
    if loud_tp is not None:
        # v2 §1.3 解码域真峰 TP 判定：阈 -1.0 dBFS（G3 同一权威值，同一回执不重测）。
        # vtx audio 已内置 alimiter，成片 TP 超阈 = 上游削波/限幅失效，须查上游。
        ok(f"解码域真峰 TP ≤ {QC_TP_MAX:g} dBFS", loud_tp <= QC_TP_MAX,
           f"实测 {loud_tp} dBFS" + ("（回执命中，免测）" if loud_hit else "")
           + ("" if loud_tp <= QC_TP_MAX
              else "——audio 已内置 alimiter，若仍超标检查上游削波"))
    else:
        ok(f"解码域真峰 TP ≤ {QC_TP_MAX:g} dBFS", False,
           "ebur128 peak 解析失败（final 音频流异常或不可测）")
    ok(f"{stacked[0] if stacked else 0} 轨并发窗口有能量", v1 > -50,
       f"mean {v1:.1f} dB"
       + (f" @frame {stacked[1]}" if stacked else "（无任何音频事件）"))
    from .quality import qc_quality
    rep["skipped_probes"] = skipped_probes
    rep["quality"] = qc_quality(rep)
    fstxn.atomic_write(c.out / "qc_report.json", json.dumps(rep, ensure_ascii=False, indent=2))
    failed = [x for x in rep["checks"] if not x["pass"]]
    print("质量标签：" + rep["quality"]["display"] + "（visual_content=unknown）")
    print(f"QC：{len(rep['checks']) - len(failed)}/{len(rep['checks'])} 通过")
    if skipped_probes:
        print(f"⚠ 免检 {len(skipped_probes)} 项（内容键命中段，stat 绑定一致）："
              "段内像素级篡改若同时保持段文件 stat 不变则本模式不查——--strict 恢复全检")
    c.receipt("qc", not failed, f"{len(rep['checks'])-len(failed)}/{len(rep['checks'])}",
              extra={"skipped_probes": skipped_probes})
    # 免检记录每轮刷新（方案三阶段1）：segs/final 重绑当前 stat，checks 按名记忆
    # pass——✗ 项下轮必复测；记录在 exit 判定前写（失败轮同样刷新，失败项记忆
    # 为 False 即「不可免检」，安全性不受影响）。
    _write_qc_pass_record(c, nb, rep["checks"])
    if failed: sys.exit(1)
