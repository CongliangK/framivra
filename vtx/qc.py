"""vtx 渲染后质检（qc 阶段）：帧数/底轨切点帧比对/上层窗口生效/泄漏/响度归一。

原 vtx.py qc 函数整体迁移（2026-09-10 模块化拆分），检查内容/顺序/名称与阈值不变。
"""
import json, re, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ---- QC 阈值命名常量（值不变，仅命名；2026-09-10 收拢）----
QC_CUT_MEAN_MAX = 6.0        # 底轨切点帧比对：平均像素差上限
QC_WINDOW_MEAN_MIN = 5.0     # 上层窗口生效：平均像素差下限（或覆盖率达到下限即生效）
QC_WINDOW_COVER_MIN = 0.03   # 上层窗口生效：覆盖率下限
QC_PIXEL_DIFF = 24           # 「显著差异像素」判定阈值（0-255）
QC_LEAK_MEAN_MAX = 2.0       # 空窗泄漏：平均像素差上限
QC_LEAK_COVER_MAX = 0.02     # 空窗泄漏：覆盖率上限
QC_LOUDNESS_TOL = 1.5        # 响度归一容差（LUFS）
TARGET_LUFS = -14            # 项目目标响度（EBU R128，与 stages.build_audio 同一权威值）


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
            launch(lambda cl=cl, j=j, key=key: cut_diff(cl, j, key))
    # 上层轨（tracks[1:]）逐 clip 窗口生效检查：窗口 clip 比对其放置区域，全屏 clip 比对整帧
    up_res = {}
    for tr in tracks[1:]:
        for ci, cl in enumerate(tr["clips"]):
            if tr.get("kind") == "gfx":
                probes = sorted({cl["dst_in"] + cl["dur"] // 4, cl["dst_in"] + cl["dur"] // 2,
                                 cl["dst_in"] + cl["dur"] - cl["dur"] // 4})
                key = f"gfx:{id(cl)}"
                launch(lambda probes=probes, key=key:
                       up_res.__setitem__(key, [fdiff(m, tag=f"{m}_{i}") for i, m in enumerate(probes)]))
                continue
            windowed = any(k in cl for k in ("scale", "x", "y"))
            m = cl["dst_in"] + cl["dur"] // 2
            if windowed:
                i = c.probe(c.src_path(cl["src"]))
                sc = cl.get("scale", 1.0)
                w2 = int(i["w"] * sc); h2 = int(i["h"] * sc)
                crop = f"{w2}:{h2 + 2}:{max(0, cl.get('x', 0) - 10)}:{max(0, cl.get('y', 0) - 10)}"
                key = f"win:{id(cl)}"
                launch(lambda m=m, crop=crop, key=key: up_res.__setitem__(key, fdiff(m, crop=crop, tag=f"{m}_")))
            else:
                key = f"full:{id(cl)}"
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
        launch(lambda: free_res.__setitem__("f", fdiff(free_frame, tag=f"{free_frame}_")))
    # 响度归一验证（EBU R128 整合响度应 ≈ 目标值）——全片解码 ~60-90s，与帧探测并行
    def loudness():
        r2 = c.run(["ffmpeg", "-nostats", "-i", str(final), "-af", "ebur128=framelog=quiet", "-f", "null", "-"])
        m2 = re.search(r"I:\s*(-?[\d.]+) LUFS", r2.stderr)
        return float(m2.group(1)) if m2 else None
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
    if c.dry:
        loud_v, v1 = None, -999   # dry-run 不执行，直接走缺省
    else:
        # 三组互不依赖：帧探测（内部 8 线程并发 ffmpeg）/ ebur128 / volumedetect 并行，墙钟 ≈ max
        with ThreadPoolExecutor(3) as gex:
            def run_probes():
                # 每任务独立 ffmpeg 子进程；结果按 key 回填 dict，报告顺序不受并发影响
                with ThreadPoolExecutor(8) as pex:
                    futs = [pex.submit(fn) for _, fn in tasks]
                    for f in futs: f.result()
            fp = gex.submit(run_probes)
            fl = gex.submit(loudness)
            fv = gex.submit(lambda: vol(stacked[1] + 2, stacked[1] + 10))
            loud_v = fl.result(); v1 = fv.result(); fp.result()

    # ---- 按原顺序回填 checks（内容/顺序/名称与旧版一致）----
    ok(f"成片帧数 = {c.dur}", nb == c.dur, f"实测 {nb}（{nb_src}）")
    ok(f"成片时长 ≈ {c.dur * c.spf:.2f}s", abs(dur - c.dur * c.spf) < 0.05, f"实测 {dur:.3f}s")
    ok(f"分辨率 {c.w}x{c.h}", st["width"] == c.w and st["height"] == c.h)
    if tracks and not bottom_gfx:
        for cl in sorted(tracks[0]["clips"], key=lambda x: x["dst_in"]):
            if "src_in" not in cl: continue
            key = f"cut:{cl['dst_in']}:{cl['src']}"
            sf, d_ = cut_res[key]
            ok(f"底轨切点帧比对 dst={cl['dst_in']} src={cl['src']}:{sf}", d_ < QC_CUT_MEAN_MAX, f"平均像素差 {d_:.2f}/255")
    for tr in tracks[1:]:
        for ci, cl in enumerate(tr["clips"]):
            if tr.get("kind") == "gfx":
                stats = up_res[f"gfx:{id(cl)}"]
                dmax = max(s[0] for s in stats); cov = max(s[1] for s in stats)
                ok(f"gfx 窗口 {Path(cl['src']).stem} 生效", dmax > QC_WINDOW_MEAN_MIN or cov > QC_WINDOW_COVER_MIN,
                   f"采样 {[cl['dst_in'] + cl['dur'] // 4, cl['dst_in'] + cl['dur'] // 2, cl['dst_in'] + cl['dur'] - cl['dur'] // 4]} 最大均值差 {dmax:.1f}，覆盖 {cov*100:.1f}%")
                continue
            windowed = any(k in cl for k in ("scale", "x", "y"))
            m = cl["dst_in"] + cl["dur"] // 2
            if windowed:
                d_, cov = up_res[f"win:{id(cl)}"]
                ok(f"窗口 clip {tr['name']} @frame {m} 生效", d_ > QC_WINDOW_MEAN_MIN or cov > QC_WINDOW_COVER_MIN,
                   f"区域差 {d_:.1f}，覆盖 {cov*100:.1f}%")
            else:
                d_, cov = up_res[f"full:{id(cl)}"]
                ok(f"全屏 clip {tr['name']} @frame {m} 生效", d_ > QC_WINDOW_MEAN_MIN or cov > QC_WINDOW_COVER_MIN,
                   f"整帧差 {d_:.1f}，覆盖 {cov*100:.1f}%")
    if free_frame is not None:
        d_, cov = free_res["f"]
        ok(f"空窗无叠加泄漏 @frame {free_frame}", d_ < QC_LEAK_MEAN_MAX and cov < QC_LEAK_COVER_MAX, f"差异 {d_:.2f}，覆盖 {cov*100:.1f}%")
    if loud_v is not None:
        ok(f"响度归一 ≈ {TARGET_LUFS:g} LUFS", abs(loud_v - TARGET_LUFS) <= QC_LOUDNESS_TOL, f"实测 {loud_v} LUFS")
    ok(f"{stacked[0]} 轨并发窗口有能量", v1 > -50, f"mean {v1:.1f} dB @frame {stacked[1]}")
    (c.out / "qc_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [x for x in rep["checks"] if not x["pass"]]
    print(f"QC：{len(rep['checks']) - len(failed)}/{len(rep['checks'])} 通过")
    c.receipt("qc", not failed, f"{len(rep['checks'])-len(failed)}/{len(rep['checks'])}")
    if failed: sys.exit(1)
