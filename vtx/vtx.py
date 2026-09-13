#!/usr/bin/env python3
"""vtx — 视频剪辑中间层 CLI（timeline.json → ffmpeg，帧精确合成）

用法：
  vtx.py [-t TIMELINE] [--dry-run] <command>

命令（按依赖顺序，all = 依次全部执行）：
  validate   校验 timeline.json 契约（对照素材实测，失败即退出）
  audit      指标审计（建议性，不阻断）：画面构成/黑场窗口/无声窗口/字幕阅读速度/语速，对照实测基准，写 audit_report.json
  prepare    提取素材音频 → 48kHz 立体声 PCM
  gfx        渲染 kind=gfx 轨的 HTML 动效 → 透明 PNG 序列（Chrome headless，带缓存）
  video      黑场画布 + 全部视频轨按图层序时移叠加 + 字幕烧录（v2 轨道等价模型）
  audio      多轨音频混音（帧网格对齐、fade/pan/loop、duck 音乐避让、EBU R128 响度归一 -14 LUFS）
             （--report 混音后附加一等报告 / --report-only 只读已有产物出报告：
             整片 LUFS/TP、人声-床乐响度差、duck 咬合/回升、静音空洞 → out/audio_report.json）
  mux        视音频封装
  qc         渲染后质检（帧数/底轨切点帧比对/上层窗口生效/泄漏/响度归一），写 qc_report.json
             （--strict：帧数改用 -count_frames 真解码计数，默认容器元数据）
  clean      清空 cache/ 与 out/
  gc         缓存回收（键集外垃圾 + tmp 孤儿 + out 中间物；--dry 只列清单，--all 深度回收，
             --keep-days N 暂缓近 N 天条目；cache/reports/ 永不回收）
  df         体积体检：cache/out 分项占用 + top-20 大文件 + tmp 孤儿清单
  snapshot   timeline + 文本资产快照到 cache/snapshots/（只留最近 2 份，VTX_SNAPSHOT_KEEP 可调）
  diff       对比：diff <旧.json> <新.json> [--json PATH]。两参均为 timeline JSON
             （含 project/video_tracks 特征键）时走时间线语义 diff——逐 clip/字幕/音轨
             字段级变化 + 缓存键重算影响面（seg/gfx/段失效）+ 需重跑阶段/耗时预估 +
             推荐修改路径（timeline_diff.py）；否则为报告对比（qc/audit/subs_check 同构
             报告字段级差异，检查项状态翻转显著标出，末行 PASS/DIFF）。纯读不写产物
  subs       字幕信息性命令（不进流水线）：render 生成 cache/subs.ass（内容寻址记账）/
             preview <时间点...> 单帧字幕预览 PNG（时间点 `12.5`=秒 / `f360`=帧号）/
             check 纯 timeline 字幕四项检查（对齐/CPS/重叠/行宽），写 out/subs_check.json

缓存卫生（2026-09-11 治理升级）：gfx/seg/audio 缓存全部内容寻址（键 = sha1(内容+参数)[:12]，
键变即新条目，旧键由 gc 回收）；mux 每次成功追加 out/VERSIONS.json（覆盖检测 + superseded
留痕 + out/history/ 保留最近 N 版，--keep-versions 默认 2）；环境变量 VTX_CACHE_BUDGET_GB
设预算时 gfx/video 阶段前超限自动跑 gc 清单（VTX_GC_AUTO=1 直接回收）。
阶段回执（PR#1 合入）：receipt.json 绑定输入身份（timeline + 源 stat 指纹 + gfx html +
声明的 cache_dependencies）——输入变化不继承旧阶段成功；每阶段运行前做依赖门（audio 需
prepare、video 需 gfx（有 gfx 轨时）、mux 需 video+audio、qc 需 mux）并撤销下游成功。
单跑某阶段前置不满足会直接拒绝；编辑输入后请按序重跑或直接 `all`。详见 docs/cache-provenance.md。

迭代提速（2026-09-08，video/mux 专属标志）：
  --draft            草稿模式：540p 半分辨率 + NVENC 硬编码（不可用自动回落 x264 veryfast），
                     跳过 qc_clean 基准轨渲染，产物 out/draft.mp4（mux --draft → out/draft_final.mp4），
                     不触碰正式产物 video_noaudio.mp4 / final.mp4 / cache/subs.ass
  --draft --frames N 只渲前 N 帧（输入按头窗截短，避免 drain 全量解码；改动集中片头时提速一个量级）
  --gpu              正式参数 GPU 渲染：NVENC p7+tune hq+vbr cq17（1080p 交付透明），
                     产物仍是 video_noaudio.mp4；NVENC 不可用自动回落 x264 crf17 medium
  （阶段二 2026-09-10）video 正式/草稿渲染默认走「时间轴分段 + 段间并行渲染 + concat
  -c copy 拼接」；环境变量 VTX_VIDEO_SEGMENTS=N 控制段数（默认 8，=1 回落旧单 graph，
  行为逐字节等价）；--draft --frames 组合强制旧单 graph（头窗截短与分段冲突）
  （阶段三 2026-09-10）seg 预编码与 prepare 抽音频并行化：两处逐任务串行的 ffmpeg
  改为线程池并行（VTX_VIDEO_SEGMENTS 分段开关同上，不受影响）；并行线程数 =
  min(6, cpu//4)，环境变量 VTX_IO_WORKERS=N 可覆盖；任一任务失败即整阶段失败退出
  （方案五阶段1 2026-09-12）video --draft --range A B / --clip ID / --track NAME：
             区间预览渲染——复用整片切点与 cache/seg|gfx 缓存，产物 cache/preview/*.mp4
             （独立命名空间：不写 out/、不更新 receipt，含窗口音频混音）
  （方案二阶段2 2026-09-12）`vtx all` 默认走跨阶段依赖图流水线：validate→audit→prepare
             （串行）→ 并行波 gfx ‖ audio ‖ video 预编码（seg 预编码提前入波，墙钟与
             gfx 重叠）→ video → mux → qc（串行）；VTX_PIPELINE=0 回退线性序列（独立
             A/B 开关）。单阶段命令与各阶段产物语义/缓存键/依赖门不变。
  （方案三阶段3 2026-09-13）`all --until <stage>`：只执行到该阶段止——R4 冷跑链
             audio 之后须 post_loudness 再 mux，`vtx all` 一条龙会把 mux/qc 白跑一遍；
             `all --until video` → post_loudness → `vtx mux` → `vtx qc` 消灭双跑。
             允许值 = STAGES 线性序中除 audio 外的 7 个阶段名（audio 是流水线波内
             成员不作独立终点；线性模式按 STAGES 序字面截断，until=video 不含 audio）。
             流水线模式映射见 _run_all_pipeline docstring。

模块布局（2026-09-10 单文件拆分为包，入口本文件保持可直接运行）：
  infra.py      Ctx/执行/ffmpeg/并行调度            encoders.py  Chrome/NVENC/编码参数选型
  composite.py  切点选择 + 合成 + ass（RenderPlan）  qc.py        质检
  stages.py     validate/audit/prepare/audio/mux    gfx_render.py CDP gfx 渲染后端
  hygiene.py    缓存键/gc/df/snapshot/VERSIONS      cache_state.py 回执身份/依赖门
  本文件        gfx 阶段 + main/argparse/STAGES 编排

约定：AI 智能体不应手写 ffmpeg 剪辑命令；只编写/修改 timeline.json 并调用本工具。
每条时间量都是整数帧；区间 [in, out) 左闭右开。回执写入 out/receipt.json。
契约 v2（D15 轨道等价模型）：video_tracks 所有轨道等价，数组顺序即图层顺序（[0] 最底）；
画布为黑场，无任何覆盖性约束；kind（aroll/broll/screen/pip/gfx）纯语义标注，渲染不区分；
clip 的 scale/x/y/opacity/fade 全部可选（缺省＝全屏不透明），fade 统一为 alpha 淡入淡出。
仅依赖 python3 标准库 + ffmpeg/ffprobe/google-chrome。
"""
import argparse, difflib, json, os, shutil, subprocess, sys, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# 入口可直接 `python3 vtx/vtx.py ...` 运行：把本目录加进 sys.path 后平铺 import
# 同目录模块（与既有 gfx_render 平铺引用同一模式）。
VTX_DIR = Path(__file__).resolve().parent
if str(VTX_DIR) not in sys.path:
    sys.path.insert(0, str(VTX_DIR))

from composite import build_video, prepare_layers
from encoders import find_chrome
from hygiene import budget_gate, df, gc, gfx_key, gfx_dir, manifest_record, snapshot
from infra import Ctx, run_wave
from qc import qc
from stages import audit, build_audio, mux, prepare, validate
from subs import run_subs


# ---------------------------------------------------------------- gfx
def _cdp_via(st):
    """render_page stats → 一行渲染摘要（方案 v2 阶段 0 观测）：路径打标 + ms/帧 +
    分项 p50。只读 stats 字段直接格式化，不做字符串状态判断（DEV.md NIT）。
    path_counts 键序固定、零值也显示，保证多页日志可 grep 对齐；navfree/static_copied
    为 v2 阶段 2 加速特性新键（旧版 stats 无该键由 `if k in pc` 兜住）；span×K =
    静态跨度生效数。"""
    pc, tm = st["path_counts"], st["timings_ms"]
    paths = " ".join(f"{k}×{pc[k]}" for k in
                     ("override", "canvas", "twoshot", "opaque", "navfree",
                      "canvas_fallback", "static_copied", "failed")
                     if k in pc)
    if "cli_fallback" in pc:
        paths += f" cli_fallback×{pc['cli_fallback']}"
    if st.get("static_spans"):
        paths += f" span×{st['static_spans']}"
    split = "/".join(f"{k} {tm[k]['p50']:.0f}" for k in ("nav", "export", "decode", "write")
                     if k in tm)
    return (f"{st['tabs']} tabs CDP {paths}, {st['ms_per_frame']:.0f}ms/帧 "
            f"({split} ms@p50)")


def _split_page(n, split_frames, split_max):
    """页内分片切块数学（方案 v2 阶段 1）：split_frames<=0（开关默认关）或
    n<=split_frames → None（现行整页单块）；否则块数 k=min(split_max,
    ceil(n/split_frames))，返回 k 个连续块大小（Σ=n，前 r 块各 +1）。"""
    if split_frames <= 0 or n <= split_frames or split_max < 1:
        return None
    k = min(split_max, -(-n // split_frames))
    base, r = divmod(n, k)
    return [base + (1 if b < r else 0) for b in range(k)]


def _merge_cdp_stats(stats_list):
    """同页多块 render_page stats → 合并 stats（供 _cdp_via 聚合格式化）。
    路径计数逐键求和；ms_per_frame=Σms_total/Σ帧数（等价于按帧数加权）；
    timings 各分项 p50/sum 按块帧数加权（p50 真值需原始样本，聚合为展示近似，
    p95 不参与展示不合并）；static_spans 取 max（同页各块报各自生效跨度数，
    求和会对跨块跨度重复计数）。空列表返回 None。"""
    if not stats_list:
        return None
    pc, tm = {}, {}
    frames = ms = 0.0
    tabs = 1
    spans = 0
    for s in stats_list:
        w = s["rendered"] + len(s["failed"])
        frames += w
        ms += s["ms_total"]
        tabs = max(tabs, s["tabs"])
        spans = max(spans, s.get("static_spans", 0))
        for k, v in s["path_counts"].items():
            pc[k] = pc.get(k, 0) + v
        for cat, t in s["timings_ms"].items():
            a = tm.setdefault(cat, {"w": 0, "p50": 0.0, "sum": 0.0})
            a["w"] += w
            a["p50"] += t["p50"] * w
            a["sum"] += t["sum"]
    frames = max(1, frames)
    return {"tabs": tabs, "path_counts": pc, "ms_per_frame": ms / frames,
            "static_spans": spans,
            "timings_ms": {cat: {"p50": a["p50"] / max(1, a["w"]), "p95": 0.0, "sum": a["sum"]}
                           for cat, a in tm.items()}}


def gfx(c: Ctx):
    budget_gate(c, "gfx")
    total = 0
    engine_notes = []          # 每页渲染引擎与耗时（receipt 用）
    use_cdp = os.environ.get("VTX_GFX", "cdp").lower() != "cli"
    cdp_mod = None
    if use_cdp:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import gfx_render as cdp_mod
            if not cdp_mod.available():
                cdp_mod = None
        except Exception as e:
            print(f"⚠ CDP 渲染器不可用（{e}），回落 CLI 截图路径")
            cdp_mod = None

    def shot_cli(i, d, html, chrome):                    # 原 CLI 路径（回落保底）
        # 视口尺寸取项目分辨率（2026-09-10 参数化，原硬编码 1920,1080；1080p 项目不变）
        return subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
             f"--window-size={c.w},{c.h}", "--default-background-color=00000000",
             f"--screenshot={d}/f{i:04d}.png", f"{html.resolve().as_uri()}?f={i}"], capture_output=True)

    def render_cli(i_range, d, html, chrome):
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(lambda i: shot_cli(i, d, html, chrome), i_range))

    # 页内分片开关（方案 v2 阶段 1）：VTX_GFX_SPLIT_FRAMES 为块大小阈值，
    # VTX_GFX_SPLIT_MAX（默认 3）为单页最大块数。默认 450（2026-09-12 定版，
    # 基准 28 页 12024 帧：PAGES=7+SPLIT450 冷跑 129.8/131.9s，RSS 15.0-15.2GB）。
    split_frames = max(0, int(os.environ.get("VTX_GFX_SPLIT_FRAMES", "450")))
    split_max = max(1, int(os.environ.get("VTX_GFX_SPLIT_MAX", "3")))

    jobs = []   # (cid, key, cl, n, d, html, frames|None) 待渲块；frames=None=整页现行语义
    for tr in c.tl["video_tracks"]:
        if tr.get("kind") != "gfx": continue
        for cl in tr["clips"]:
            cid = Path(cl["src"]).stem
            # 内容寻址键（2026-09-11 治理升级）：键 = sha1(html字节+fps+视口+时长)[:12]。
            # html 变了键就变（新条目），旧键由 gc 回收——D29 mtime 补丁与 D38 同索引
            # 误命中从机制上消失，故此处不再有任何 mtime 比对。
            key = gfx_key(c, cl)
            n = cl["dur"]
            d = gfx_dir(c, cl)
            # dry-run 只报告不动缓存：清缓存/mkdir 均为真实副作用，门控（2026-09-11）
            if not c.dry:
                if d.exists() and len(list(d.glob("f*.png"))) != n:  # 帧数不符（换时长）→ 清缓存重渲
                    shutil.rmtree(d)
                d.mkdir(parents=True, exist_ok=True)
            if d.exists() and len(list(d.glob("f*.png"))) == n:
                print(f"✓ {cid}[{key}]: {n} 帧已缓存"); total += 1; continue
            html = (c.root / cl["src"]).resolve()
            # 页内分片（方案 v2 阶段 1，默认关）：SPLIT_FRAMES>0 且页帧数超阈值时把
            # [0,n) 切成 min(SPLIT_MAX, ceil(n/阈值)) 个连续块，每块独立一次
            # render_page（独立 Chrome 实例）；dry-run / CLI 回落态不拆。
            sizes = (None if (c.dry or cdp_mod is None)
                     else _split_page(n, split_frames, split_max))
            if sizes is None:
                jobs.append((cid, key, cl, n, d, html, None))
                continue
            off, put = 0, 0
            for sz in sizes:
                fb = list(range(off, off + sz)); off += sz
                if all((d / f"f{i:04d}.png").exists() for i in fb):
                    continue    # 整块已缓存 → 跳过（不起 Chrome、不占池槽）
                jobs.append((cid, key, cl, n, d, html, fb)); put += 1
            if put == 0:        # 页级帧数门已滤全缓存，理论不可达；防御性兜底
                print(f"✓ {cid}[{key}]: {n} 帧已缓存"); total += 1

    # 页级并行（2026-09-11）：Chrome 对同源 tab 共用渲染进程，单浏览器内加 tab 光栅
    # 收益递减；真正的并行单位是浏览器实例×页。VTX_GFX_PAGES 控制并行页数
    # （默认 7：2026-09-12 定版，PAGES=8 峰值 RSS 16.5-20.4GB 破 16GB 线，
    # 7 页 15.0-15.2GB 达标且 129.8-131.9s 仅比 8 页慢 ~2%）。
    page_workers = max(1, int(os.environ.get("VTX_GFX_PAGES", "7")))

    # 同页多块聚合：块完成只并入聚合器，末块收尾时输出一行 ✓（路径计数求和、
    # ms/帧按帧数加权、块数标注 split×k），receipt engine_notes 同样每页一条。
    agg_lock = threading.Lock()
    page_agg = {}
    for j in jobs:
        if j[6] is not None:
            page_agg.setdefault((j[0], j[1]), {"left": 0, "blocks": []})["left"] += 1

    def render_one(job):
        cid, key, cl, n, d, html, frames = job
        st = None
        if cdp_mod is not None:
            try:
                # 视口宽高传项目分辨率（render_page 默认 1920×1080，非 1080p 项目此前错渲）
                st = cdp_mod.render_page(html, d, n, chrome_path=find_chrome(),
                                         width=c.w, height=c.h, frames=frames)
                if st["failed"]:
                    print(f"⚠ {cid}: CDP 帧 {st['failed']} 失败，回落 CLI 补渲")
                    render_cli(st["failed"], d, html, find_chrome())
                    # CLI 补渲计数由调用方补进 path_counts（gfx_render 不感知 CLI 通道）
                    st["path_counts"]["cli_fallback"] = len(st["failed"])
            except Exception as e:
                # CDP 块级失败：只回落本块帧区间（frames=None 时整页，与现行一致）
                fb = range(n) if frames is None else frames
                print(f"⚠ {cid}: CDP 渲染失败（{e}），"
                      f"{'该块' if frames is not None else '整页'}回落 CLI")
                render_cli(fb, d, html, find_chrome())
                st = None
        if cdp_mod is None:
            render_cli(range(n), d, html, find_chrome())
        if frames is None:
            # —— 整页单块（SPLIT 关或页不超阈值）：日志格式与现行逐字节一致 ——
            rendered_via = "cli 6 并发" if st is None else _cdp_via(st)
            got = len(list(d.glob("f*.png")))
            if got != n: print(f"✗ {cid}: 期望 {n} 帧得到 {got}"); sys.exit(1)
            print(f"✓ {cid}[{key}]: 渲染 {n} 帧（含 alpha，{rendered_via}）")
            manifest_record(c, d, "gfx", key)
            return cid, rendered_via
        # —— split 块：并入页聚合器，末块负责校验/打印/记账 ——
        with agg_lock:
            acc = page_agg[(cid, key)]
            acc["blocks"].append((len(frames), st))
            acc["left"] -= 1
            last = acc["left"] == 0
        if not last:
            return cid, None
        blocks = acc["blocks"]
        k = len(blocks)
        stats_list = [s for _, s in blocks if s is not None]
        n_cli = sum(1 for _, s in blocks if s is None)
        if stats_list:
            rendered_via = _cdp_via(_merge_cdp_stats(stats_list))
            if n_cli:
                rendered_via += f"，{n_cli} 块CLI回落"
            if k > 1:
                rendered_via += f"，split×{k}"
        else:
            rendered_via = "cli 6 并发" + (f"（split×{k}）" if k > 1 else "")
        got = len(list(d.glob("f*.png")))
        if got != n: print(f"✗ {cid}: 期望 {n} 帧得到 {got}"); sys.exit(1)
        print(f"✓ {cid}[{key}]: 渲染 {n} 帧（含 alpha，{rendered_via}）")
        manifest_record(c, d, "gfx", key)
        return cid, rendered_via

    if jobs:
        if c.dry:
            # dry-run：只报告将渲染的页，不起 Chrome（否则冷态 all --dry-run 白付
            # 数分钟真实渲染 + GB 级 PNG 落盘，2026-09-11 门控；dry 不拆块）
            for cid, key, cl, n, d, html, frames in jobs:
                print(f"  [dry-run] gfx {cid}[{key}]: 将渲染 {n} 帧（Chrome {c.w}×{c.h}）")
            total += len(jobs)
        else:
            with ThreadPoolExecutor(min(page_workers, len(jobs))) as pex:
                for cid, rendered_via in pex.map(render_one, jobs):
                    if rendered_via is None:
                        continue    # split 中间块：✓ 行由末块聚合输出
                    engine_notes.append(f"{cid}: {rendered_via}")
                    total += 1
    c.receipt("gfx", True, f"{total} gfx clips"
              + ("｜" + "; ".join(engine_notes) if engine_notes else ""))


# STAGES = 单阶段命令分发的权威表 + 线性文档序（`all` = 依次全部执行）。
# `vtx all` 实际编排（方案二阶段2，2026-09-12）默认走依赖图（_run_all_pipeline）：
#   串行头 validate→audit→prepare → 并行波（gfx ‖ audio ‖ video 预编码 prepare_layers）
#   → 串行尾 video（用预计算 layers）→ mux → qc——波内三路零数据依赖（基线实测
#   gfx 全冷 165s / seg 预编码 33s / audio 混音 27s 本机 32C），后两路完全落在 gfx
#   墙钟窗口内省 ~60s 串行等待；VTX_PIPELINE=0 回退本表线性序列。
STAGES = [("validate", validate), ("audit", audit), ("prepare", prepare), ("gfx", gfx),
          ("video", build_video), ("audio", build_audio), ("mux", mux), ("qc", qc)]

# `all --until <stage>` 允许值（方案三阶段3）：线性 STAGES 序中除 audio 外的 7 个阶段名
# ——audio 是流水线波内成员（与 gfx/预编码三路同波，语义上不存在「跑到 audio 为止」的
# 编排边界），故不作独立终点；until=mux/qc 时 audio 随波/随序自然包含。序列与 STAGES
# 的相对顺序保持一致（静态表，改 STAGES 时同步）。
_UNTIL_STAGES = ("validate", "audit", "prepare", "gfx", "video", "mux", "qc")


def _stage_receipt_name(name: str, draft: bool) -> str:
    """阶段回执名（现行内联规则抽 helper，2026-09-12）：draft 模式的 video/mux 记为
    video_draft/mux_draft（receipt/依赖门/下游撤销共用同一名字），其余阶段原名。
    线性序列与流水线两处复用（单一权威）。"""
    return name + "_draft" if draft and name in ("video", "mux") else name


def _pipeline_enabled(env=None) -> bool:
    """VTX_PIPELINE 解析（方案二阶段2 A/B 开关）：仅显式 "0" 回退线性序列，缺省与
    其他任意值（含空串/"false"/"1"）走依赖图——开关语义单值精确，避免布尔猜测。"""
    e = os.environ if env is None else env
    return e.get("VTX_PIPELINE", "1") != "0"


def _run_all_pipeline(c: Ctx, until=None):
    """`vtx all` 依赖图编排（方案二阶段2）：串行头（validate/audit/prepare，现行
    begin_stage + 阶段函数语义）→ 并行波（gfx ‖ audio ‖ video 预编码，run_wave）→
    串行尾（video/mux/qc）。波内前两路 = begin_stage(该阶段名) + 现行阶段函数；
    「video 预编码」= 无 begin_stage 无 receipt 的 prepare_layers——receipt/manifest
    记账仍由 video 阶段本体负责（预编码只产 layers 数据 + seg 缓存侧副作用），结果
    经 c._pre_layers 传入 build_video。各阶段产物语义、缓存键、依赖门判定、receipt
    文案全部不变，只改执行顺序。注：STAGES 名集须与下面三段划分一致（静态表，
    见 STAGES 旁注释）。

    until（方案三阶段3，None=缺省全链）：按阶段族截断执行——
      until∈{validate,audit,prepare} → 只跑串行头到该阶段（不起波）；
      until∈{gfx,video} → 串行头 + 并行波（波内 gfx‖audio‖预编码三路全跑：audio 是
        波成员不拆，until=gfx 也完整跑波以保证 audio 回执/段缓存就绪）+（until=video
        时）video 尾；until=gfx 到波结束即止；
      until∈{mux,qc} → 与缺省全链同一编排，到该阶段止（qc 即完整全链）。
    依赖门/receipt 语义不变：截断只少跑阶段，已跑阶段的回执照常落盘（--until video
    后 `vtx mux` 的依赖门 video+audio 恰好满足）。"""
    by_name = dict(STAGES)
    for name in ("validate", "audit", "prepare"):   # —— 串行头 ——
        print(f"== {name} ==")
        try:
            c.begin_stage(_stage_receipt_name(name, c.draft))
        except RuntimeError as e:
            print(f"✗ {e}"); sys.exit(1)
        by_name[name](c)
        if until == name:
            return

    def _stage_job(name, fn):                        # —— 波内阶段路：现行阶段语义 ——
        def run():
            try:
                c.begin_stage(_stage_receipt_name(name, c.draft))
            except RuntimeError as e:
                print(f"✗ {e}"); sys.exit(1)
            fn(c)
        return run

    def _pre_layers():                               # —— 波内预编码路：无门无回执 ——
        c._pre_layers = prepare_layers(c)

    run_wave(c, [("gfx", _stage_job("gfx", by_name["gfx"])),
                 ("audio", _stage_job("audio", by_name["audio"])),
                 ("video 预编码", _pre_layers)])
    if until == "gfx":
        return   # 波结束即止：audio 回执/seg 缓存已就绪，video 尾不跑
    for name in ("video", "mux", "qc"):              # —— 串行尾 ——
        print(f"== {name} ==")
        try:
            c.begin_stage(_stage_receipt_name(name, c.draft))
        except RuntimeError as e:
            print(f"✗ {e}"); sys.exit(1)
        if name == "video":
            build_video(c, layers=c._pre_layers)   # 波内预编码产物（Ctx.__init__ 显式声明，见 infra）
        else:
            by_name[name](c)
        if until == name:
            return

def main():
    ap = argparse.ArgumentParser(description="vtx 视频剪辑中间层")
    ap.add_argument("-t", "--timeline", default="timeline.json", help="timeline.json 路径（默认 ./timeline.json）")
    ap.add_argument("--until", metavar="STAGE", default=None,
                    help="all 专属：只执行到该阶段止（validate|audit|prepare|gfx|video|mux|qc，"
                         "STAGES 线性序；audio 为波内成员不作终点）。R4 冷跑链用法："
                         "all --until video → post_loudness → mux → qc（免 mux/qc 白跑）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的 ffmpeg 命令，不执行")
    ap.add_argument("--draft", action="store_true", help="草稿模式（仅 video/mux）：540p+NVENC 快速预览，不影响正式产物")
    ap.add_argument("--frames", type=int, metavar="N", help="video --draft 专属：只渲前 N 帧")
    ap.add_argument("--range", nargs=2, metavar=("A", "B"),
                    help="video --draft 专属：只渲全局帧区间 [A,B) 的画面+音频（含字幕）。"
                         "时间点支持 秒（12 / 12.5）/ 时间码（mm:ss / hh:mm:ss(.fff)）/ 帧号后缀 f（300f）。"
                         "产物 cache/preview/range_<A>_<B>.mp4（独立命名空间：不写 out/、不更新 receipt）；"
                         "与 --clip / --track 互斥")
    ap.add_argument("--clip", metavar="ID",
                    help="video --draft 专属：只渲单个 clip 所在窗口（按 src 文件名匹配，推出区间 [dst_in,dst_out)）；与 --range / --track 互斥")
    ap.add_argument("--track", metavar="NAME",
                    help="video --draft 专属：只渲单轨窗口（该轨 clips 的外包络）；与 --range / --clip 互斥")
    ap.add_argument("--gpu", action="store_true", help="video 专属：正式参数 GPU 渲染（NVENC p7 hq cq17），产物仍为 video_noaudio.mp4")
    ap.add_argument("--strict", action="store_true",
                    help="qc 专属：成片帧数用 -count_frames 真解码计数（默认容器元数据，省全片解码）")
    ap.add_argument("--dry", action="store_true",
                    help="gc 专属：只列回收清单不删除（gc 默认执行，--all 深度回收）")
    ap.add_argument("--all", action="store_true",
                    help="gc 专属：深度回收（等价原 clean，但保留 reports/VERSIONS/history/final）")
    ap.add_argument("--keep-days", type=int, metavar="N", default=0,
                    help="gc 专属：近 N 天动过的条目暂缓回收（默认 0 = 只看键集）")
    ap.add_argument("--keep-versions", type=int, metavar="N", default=2,
                    help="mux 专属：out/history/ 保留最近 N 版历史成片（默认 2）")
    ap.add_argument("--align-threshold", type=float, metavar="S", default=1.5,
                    help="subs check 专属：口播对齐 FAIL 阈值秒数（默认 1.5，≥0.5 先 WARN）")
    # 方案五阶段3：音频一等报告（audio 专属）。--report 混音后附报告；--report-only
    # 纯报告（只读已有 out/audio.wav + cache/audio，不重混音、不做 receipt）
    ap.add_argument("--report", action="store_true",
                    help="audio 专属：混音完成后附加音频一等报告（整片 LUFS/TP、人声-床乐响度差、"
                         "duck 咬合/回升、静音空洞），写 out/audio_report.json；有 FAIL 退出码 1")
    ap.add_argument("--report-only", action="store_true",
                    help="audio 专属：纯报告模式——不重混音，只读已有 out/audio.wav 出报告"
                         "（缺失报错）；与 --report 互斥")
    ap.add_argument("--json", metavar="PATH",
                    help="diff 时间线模式专属：机器可读 diff/影响面/推荐路径 JSON 写入 PATH"
                         "（缺省只打印人读摘要；报告对比模式不支持）；"
                         "audio --report/--report-only 专属：报告 JSON 落盘 PATH（默认 out/audio_report.json）")
    ap.add_argument("command",
                    help="all | " + " | ".join(s[0] for s in STAGES) +
                         " | clean | gc | df | snapshot | subs | diff")
    # 必须定义在 command 之后：argparse 位置参数按定义序匹配，`*` 放前面会把
    # `subs check` 的两个 token 全部吞掉、command 只剩末位（实测 2026-09-11）
    ap.add_argument("subs_extra", nargs="*", metavar="ARG",
                    help="subs 专属：render | preview <时间点...> | check（时间点 `12.5`=秒 / `f360`=帧号）；"
                         "diff 专属：<旧报告.json> <新报告.json>（复用同一位置参数槽，见上注）")
    a = ap.parse_args()
    # --until 校验（方案三阶段3）：仅 all 合法 + 值域 = _UNTIL_STAGES；两错均退出码 2
    # （与「未知命令」同级：用法错而非运行错，脚本侧好分类）。audio 不可作终点的
    # 原因见 _UNTIL_STAGES 旁注释。
    if a.until is not None:
        if a.command != "all":
            print("--until 仅在 all 命令下可用（正确用法：vtx.py -t timeline.json all --until video）"); sys.exit(2)
        if a.until not in _UNTIL_STAGES:
            print(f"✗ --until 非法阶段名：{a.until!r}（可用：{' | '.join(_UNTIL_STAGES)}；"
                  "audio 是流水线波内成员，不作独立终点）"); sys.exit(2)
    if a.frames and not (a.draft and a.command == "video"):
        print("--frames 仅在 video --draft 下可用（正确用法：vtx.py video --draft --frames 90）"); sys.exit(1)
    # 方案五阶段1：区间渲染标志（video --draft 专属；三者为「选一种窗口来源」互斥组）
    range_flags = [x for x in (a.range, a.clip, a.track) if x]
    if range_flags and not (a.draft and a.command == "video"):
        print("--range/--clip/--track 仅在 video --draft 下可用"
              "（正确用法：vtx.py video --draft --range 0 6）"); sys.exit(1)
    if len(range_flags) > 1:
        print("--range / --clip / --track 互斥：窗口来源三选一"
              "（--clip 按 src 名推出单 clip 区间；--track 取单轨 clips 外包络）"); sys.exit(1)
    # P2（阶段1复审）：区间预览恒为 draft 快速路径，与头窗截短/正式 GPU 参数无组合语义
    if range_flags and (a.frames or a.gpu):
        print("--range/--clip/--track 与 --frames/--gpu 互斥（区间预览固定 draft 540p；"
              "截短请直接缩小区间，正式参数请走完整 video 阶段）"); sys.exit(1)
    if a.draft and a.command not in ("video", "mux"):
        print("--draft 仅作用于 video/mux 阶段（正确用法：vtx.py video --draft 或 vtx.py mux --draft）"); sys.exit(1)
    # 方案五阶段3：音频一等报告标志（audio 专属，两开关互斥）
    if (a.report or a.report_only) and a.command != "audio":
        print("--report/--report-only 仅作用于 audio 阶段"
              "（正确用法：vtx.py audio --report 或 vtx.py audio --report-only）"); sys.exit(1)
    if a.report and a.report_only:
        print("--report 与 --report-only 互斥：--report 混音后出报告，--report-only 只读已有产物"); sys.exit(1)
    if (a.report or a.report_only) and a.dry_run:
        print("✗ --report/--report-only 与 --dry-run 冲突（报告需要真实产物测量）"); sys.exit(1)
    if a.gpu and a.command != "video":
        print("--gpu 仅作用于 video 阶段（正确用法：vtx.py video --gpu）"); sys.exit(1)
    # 未知命令先于 timeline 检查（错字时给最接近建议，不被 timeline 缺失报错挡住）
    valid_names = [s[0] for s in STAGES] + ["all", "clean", "gc", "df", "snapshot", "subs", "diff"]
    if a.command not in valid_names:
        sug = difflib.get_close_matches(a.command, valid_names, n=3, cutoff=0.6)
        hint = f"，最接近：{' | '.join(sug)}" if sug else ""
        print(f"✗ 未知命令：{a.command!r}（可用：{' | '.join(valid_names)}）{hint}"); sys.exit(2)
    if a.command == "diff":
        # 报告对比（信息性，与 gc/df/snapshot 同类）：不依赖 timeline，故在 timeline
        # 检查前分发；两个位置参数复用 subs_extra 槽（argparse `*` 在 command 后的
        # 既有约定，见上注）。纯读命令：不写任何产物、不做 receipt；--dry-run 尊重。
        if len(a.subs_extra) != 2:
            print("✗ 用法：diff <旧报告.json> <新报告.json>"
                  "（timeline 语义 diff 同形：diff <旧timeline> <新timeline> [--json PATH]）"); sys.exit(2)
        # 时间线语义 diff（方案五阶段2）：两参均为 timeline JSON（含 project+video_tracks
        # 特征键）时自动走 timeline_diff；否则维持报告对比（diffcmd）——一个 diff 入口，
        # 按输入特征分流，最小惊讶。坏 JSON / 类型不一致（timeline 混报告）给分类文案。
        from diffcmd import run_diff
        from timeline_diff import is_timeline
        ja, jb = a.subs_extra
        try:
            da = json.loads(Path(ja).read_text(encoding="utf-8"))
            db = json.loads(Path(jb).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"✗ 输入读取/解析失败（timeline 语义 diff 与报告对比都要求两份合法 JSON）：{e}")
            sys.exit(2)
        tl_a, tl_b = is_timeline(da), is_timeline(db)
        if tl_a and tl_b:
            if a.json and a.dry_run:
                print("✗ --json 落盘与 --dry-run 冲突（diff 为纯读命令，dry 下不写文件）"); sys.exit(2)
            from timeline_diff import run_timeline_diff
            sys.exit(run_timeline_diff(ja, jb, json_path=a.json, dry=a.dry_run))
        if tl_a != tl_b:
            print(f"✗ 两参类型不一致（{ja if tl_a else jb} 是 timeline，"
                  f"{jb if tl_a else ja} 不是）：时间线语义 diff 要求两参均为 timeline JSON"
                  "（含 project/video_tracks/sources）；报告对比要求两份同构报告"); sys.exit(2)
        if a.json:
            print("✗ --json 仅在 diff 时间线模式（两参均为 timeline JSON）下可用"); sys.exit(2)
        sys.exit(run_diff(ja, jb, dry=a.dry_run))
    # 不跟随符号链接（resolve() 会穿透 cp -rs 链接树等隔离副本，把 out/ 回执写到 live 项目）
    tp = Path(os.path.abspath(os.path.expanduser(a.timeline)))
    if not tp.exists(): print(f"timeline 不存在: {tp}"); sys.exit(1)
    try:
        c = Ctx(tp)   # PR#1：构造期校验 cache_dependencies（缺失/非文件 → 友好报错）
    except (ValueError, KeyError, TypeError, ZeroDivisionError, FileNotFoundError) as e:
        # 2026-09-11 扩捕获：timeline 结构缺键（KeyError）/类型错（TypeError）/fps=0
        # （ZeroDivisionError）/文件缺失（FileNotFoundError）均友好打印而非裸栈
        print(f"✗ timeline 无效：{e}"); sys.exit(1)
    c.cache.mkdir(exist_ok=True); c.out.mkdir(exist_ok=True)
    c.dry = a.dry_run   # 原 DRY 全局改为 Ctx 字段（消除可变全局）
    c.draft = a.draft; c.frames = a.frames; c.gpu = a.gpu; c.strict = a.strict
    c.keep_versions = a.keep_versions   # mux：out/history/ 保留版数
    # 区间渲染（方案五阶段1）：解析窗口帧区间；独立命名空间路径，绕过 receipt 依赖门
    if range_flags:
        from range import parse_timespec, window_from_clip, window_from_track
        try:
            if a.range:
                fa, fb = parse_timespec(a.range[0], c.fps), parse_timespec(a.range[1], c.fps)
            elif a.clip:
                w = window_from_clip(c.tl, a.clip)
                if w is None:
                    print(f"✗ 找不到 clip {a.clip!r}（按 src 文件名匹配，不含扩展名）"); sys.exit(1)
                fa, fb = w
            else:
                w = window_from_track(c.tl, a.track)
                if w is None:
                    names = [f"{t.get('name')}/{t.get('kind')}"
                             + ("（空轨）" if not t.get("clips") else "")
                             for t in c.tl["video_tracks"]]
                    print(f"✗ 找不到有 clips 的轨道 {a.track!r}（按 name 或 kind 匹配；"
                          f"现有：{'、'.join(names) or '无轨道'}）"); sys.exit(1)
                fa, fb = w
        except ValueError as e:
            print(f"✗ {e}"); sys.exit(1)
        if fa >= fb:
            print(f"✗ 空区间：B（帧 {fb}）必须大于 A（帧 {fa}）"); sys.exit(1)
        c.range_window = (fa, fb)
    if a.strict and a.command != "qc":
        print("--strict 仅作用于 qc 阶段（正确用法：vtx.py qc --strict）"); sys.exit(1)
    if a.command == "clean":
        shutil.rmtree(c.cache, ignore_errors=True); shutil.rmtree(c.out, ignore_errors=True)
        print("✓ 已清空 cache/ out/"); return
    if a.command == "gc":
        gc(c, do=not a.dry, deep=a.all, keep_days=a.keep_days); return
    if a.command == "df":
        df(c); return
    if a.command == "snapshot":
        snapshot(c); return
    if a.command == "subs":
        # 信息性命令（render/preview/check）：不进流水线、不做 receipt 依赖门，
        # 但 subs render 写 cache 走 manifest_record 记账；--dry-run 被尊重。
        if a.subs_extra and a.subs_extra[0] in ("render", "preview", "check"):
            c.align_threshold = a.align_threshold
            run_subs(c, a.subs_extra); return
        print("✗ 用法：subs render | subs preview <时间点...> | subs check"
              "（时间点 `12.5`=秒 / `f360`=帧号）"); sys.exit(2)
    # 区间渲染分发（在常规阶段编排之前短路）：不进 receipt 依赖门、不写回执——
    # 预览渲染对正式产物链（out/、receipt、manifest、VERSIONS）零干扰
    if getattr(c, "range_window", None):
        from range import run_range
        run_range(c, *c.range_window)
        return
    # 音频纯报告分发（方案五阶段3，区间渲染同款短路）：只读已有产物，不进依赖门、
    # 不做 receipt——报告对正式产物链零干扰（写 out/audio_report.json 一个报告件）
    if a.report_only:
        from audio_report import run_report
        sys.exit(run_report(c, json_path=a.json))
    if a.command == "all":
        todo = STAGES
        if a.until is not None:
            # 线性模式截断（方案三阶段3）：按 STAGES 序字面保留到目标阶段。注意与
            # 流水线映射的口径差——线性序中 audio 在 video 之后，until=video 不含
            # audio（后续 mux 需先 `vtx audio` 补回执）；until=mux/qc 则自然包含。
            idx = next(i for i, s in enumerate(STAGES) if s[0] == a.until)
            todo = STAGES[:idx + 1]
    else:
        todo = [next((s for s in STAGES if s[0] == a.command), None)]
    names = [s[0] for s in todo] if a.command == "all" else [a.command]
    if todo == [None]:
        names = " | ".join([s[0] for s in STAGES] + ["clean", "gc", "df", "snapshot"])
        print(f"✗ 未知命令：{a.command!r}（可用：{names}）"); sys.exit(2)
    if a.command == "all" and _pipeline_enabled():
        # 方案二阶段2：all 默认走依赖图（串行头 → 并行波 → 串行尾）；
        # VTX_PIPELINE=0 回退下方线性序列（独立 A/B 开关）；
        # 方案三阶段3：--until 截断在依赖图内按阶段族映射（见 _run_all_pipeline）
        _run_all_pipeline(c, until=a.until)
    else:
        for name, fn in todo:
            print(f"== {name} ==")
            # PR#1：阶段开始前做依赖门 + 撤销下游旧成功（失败重跑不得保留旧 QC PASS）。
            # draft 模式的 video/mux 记为 video_draft/mux_draft。
            try:
                c.begin_stage(_stage_receipt_name(name, c.draft))
            except RuntimeError as e:
                print(f"✗ {e}"); sys.exit(1)
            fn(c)
            # audio --report：混音阶段（含依赖门/receipt）完成后附加一等报告（方案五阶段3）；
            # 报告判 FAIL 时以报告退出码覆盖（阶段本身成功，但报告是本次运行的结论）
            if name == "audio" and a.report:
                from audio_report import run_report
                rc = run_report(c, json_path=a.json)
                if rc: sys.exit(rc)
    # 完成行（方案三阶段3 微调）：--until 截在 mux/qc 之前时 final.mp4 尚不存在，
    # 打印已跑阶段名单而非产物路径（all 缺省/mux/qc 及单阶段 mux/qc 仍打印产物）
    final_ready = a.command in ("all", "mux", "qc") and a.until in (None, "mux", "qc")
    print("完成：", c.out / "final.mp4" if final_ready else names)

if __name__ == "__main__":
    main()
