"""vtx — 视频剪辑中间层 CLI（timeline.json → ffmpeg，帧精确合成）

用法：
  vtx.py [-t TIMELINE] [--dry-run] <command>

命令（按依赖顺序，all = 依次全部执行）：
  validate   校验 timeline.json 契约（对照素材实测，失败即退出）
  audit      指标审计（建议性，不阻断）：画面构成/黑场窗口/无声窗口/字幕阅读速度/语速，对照实测基准，写 audit_report.json
  prepare    提取素材音频 → 48kHz 立体声 PCM
  gfx        渲染 kind=gfx 轨的 HTML 动效 → 透明 PNG 序列（Chrome headless，带缓存）
  video      黑场画布 + 全部视频轨按图层序时移叠加 + 字幕烧录（v2 轨道等价模型）
  audio      多轨音频混音（帧网格对齐、fade/pan/loop、duck 音乐避让、EBU R128 响度归一
             -14 LUFS + alimiter 采样限幅——v2 §1.2 响度终链并入：out/audio.wav 恒为
             「归一+限幅」终态，终态复测 ±1.5 LU + 时长 ±1 采样，回执附 post_loudness 字段）
             （--report 混音后附加一等报告 / --report-only 只读已有产物出报告：
             整片 LUFS/TP、人声-床乐响度差、duck 咬合/回升、静音空洞 → out/audio_report.json）
  mux        视音频封装
  qc         渲染后质检（帧数/底轨切点帧比对/上层窗口生效/泄漏/响度归一），写 qc_report.json
             （--strict：帧数改用 -count_frames 真解码计数，默认容器元数据）
  clean      清空 cache/ 与 out/（A02/issue #21：默认保留不可再生交付件——final.mp4、
             VERSIONS.json、history/、FREEZE* 冻结凭证、final_4k*.mp4、out/reports/ 证据；
             全删需显式危险旗标 --i-know-this-nukes-everything）
  gc         缓存回收（键集外垃圾 + tmp 孤儿 + out 中间物；--dry 只列清单，--all 深度回收，
             --keep-days N 暂缓近 N 天条目；cache/reports/ 永不回收；不可再生交付件
             任何 gc 变体都不删——全删唯一入口是 clean --i-know-this-nukes-everything）
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
             `vtx all` 一条龙会把 mux/qc 白跑一遍；`all --until video` → mux → qc
             消灭双跑（v2 §1.2：limiter 已并入 vtx audio，post_loudness 步骤消亡，
             R4 链不再有独立 post 环节）。
             允许值 = STAGES 线性序中除 audio 外的 7 个阶段名（audio 是流水线波内
             成员不作独立终点；线性模式按 STAGES 序字面截断，until=video 不含 audio）。
             流水线模式映射见 _run_all_pipeline docstring。
  （协议 v1.2 · P6 2026-09-14）all 全链（mux/qc 完成后）触发插件 finish 收尾钩子
             （blender-bridge 自动导出 .blend 等）；qc 失败不阻断 finish（产物已就绪），
             退出码按 qc/finish 各自失败态如实汇总；--draft/--dry-run/--until 截断链不触发。

模块布局（2026-09-10 单文件拆分；v2 §1.4 包化 2026-09-13：模块内全部相对导入，
入口三通——vtx/vtx.py 薄启动器 / python3 -m vtx / pyproject console script `vtx`）：
  infra.py      Ctx/执行/ffmpeg/并行调度            encoders.py  Chrome/NVENC/编码参数选型
  composite.py  切点选择 + 合成 + ass（RenderPlan）  qc.py        质检
  stages.py     validate/audit/prepare/audio/mux    gfx_render.py CDP gfx 渲染后端
  hygiene.py    缓存键/gc/df/snapshot/VERSIONS      cache_state.py 回执身份/依赖门
  本文件(cli)   gfx 阶段 + main/argparse/STAGES 编排（vtx/vtx.py 只剩薄启动器）

约定：AI 智能体不应手写 ffmpeg 剪辑命令；只编写/修改 timeline.json 并调用本工具。
每条时间量都是整数帧；区间 [in, out) 左闭右开。回执写入 out/receipt.json。
契约 v2（D15 轨道等价模型）：video_tracks 所有轨道等价，数组顺序即图层顺序（[0] 最底）；
画布为黑场，无任何覆盖性约束；kind（aroll/broll/screen/pip/gfx）纯语义标注，渲染不区分；
clip 的 scale/x/y/opacity/fade 全部可选（缺省＝全屏不透明），fade 统一为 alpha 淡入淡出。
仅依赖 python3 标准库 + ffmpeg/ffprobe/google-chrome。
"""
import argparse, difflib, json, os, shutil, subprocess, sys, threading
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from pathlib import Path

# v2 §1.4 包化：模块内全部相对导入（外部一律 from vtx import / import vtx.xxx；
# sys.path 锚定由 vtx/vtx.py 薄启动器或 python3 -m vtx / pip 安装保证）。
from .composite import build_video, prepare_layers
from .encoders import find_chrome
from . import gfx_cli, hygiene, fstxn, events
from .hygiene import (budget_gate, df, gfx_key, gfx_dir, gfx_cache_ns,
                      manifest_record, snapshot)
from .infra import (Ctx, ProjectPathError, ensure_inside_project,
                    run_wave)
from .qc import qc
from .stages import audit, build_audio, mux, prepare, validate
from .subs import run_subs


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
    # issue #28：任何渲染/有效性门失败均写失败回执，不允许静默计完成。
    try:
        _gfx(c)
    except SystemExit as e:
        if e.code:
            c.receipt("gfx", False, f"gfx 提前退出（退出码 {e.code}）")
        raise
    except Exception as e:
        c.receipt("gfx", False, str(e))
        print(f"✗ gfx: {e}")
        sys.exit(1)


def _gfx(c: Ctx):
    budget_gate(c, "gfx")
    # issue #103：gfx 阶段开工前清扫上次崩溃残留的监管 Worker 孤儿组（只杀
    # 账本核验过、宿主已死的组；并行 vtx 进程的活动组由账本 host 字段跳过）。
    # dry 零写态纪律：dry 跳过（reap 有目录移除动作）。
    if not c.dry:
        from .proc_supervisor import reap_orphan_workers
        reap_orphan_workers(c)
    # issue #103：实验命名空间（VTX_GFX_CACHE_NS）——整阶段取一次：gfx_dir 路由
    # 与 complete.json 的 backend_ns 元数据同源；非法值在此显式失败（不静默落
    # 正式缓存破坏隔离）。
    backend_ns = gfx_cache_ns()
    from .gfx_dependencies import policy, resolve, digest, DependencyError
    strict = policy(c) == 'strict'
    dependency_records = {}
    total = 0
    engine_notes = []          # 每页渲染引擎与耗时（receipt 用）
    use_cdp = os.environ.get("VTX_GFX", "cdp").lower() != "cli"
    cdp_mod = None
    if use_cdp:
        try:
            from . import gfx_render as cdp_mod
            if not cdp_mod.available():
                cdp_mod = None
        except Exception as e:
            print(f"⚠ CDP 渲染器不可用（{e}），回落 CLI 截图路径")
            cdp_mod = None

    if strict and cdp_mod is None:
        raise DependencyError('strict dependencies require CDP; CLI downgrade forbidden')

    def render_cli(i_range, d, html, chrome):
        # issue #28：强制 CLI、整页回落、帧级补渲共用退出码与产物双重校验。
        frame_list = list(i_range)
        observation = events.task(c, "gfx", kind="gfx_fallback",
                                  metadata={"backend": "cli", **events.frame_metadata(frame_list)})
        with observation.scope():
            return observation.attempt(gfx_cli.render_cli, frame_list, d, html, chrome,
                                       width=c.w, height=c.h)

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
            page_id = str(events.uuid.uuid4())
            dependency_records[key] = resolve(c, cl) if strict else None
            dependency_digest = digest(dependency_records[key]) if strict else None
            n = cl["dur"]
            d = gfx_dir(c, cl)
            # issue #28：逐一核验 [0,n)，f9999/空文件等不再冒充完整缓存。
            bad, needs_marker = gfx_cli.inspect_page(d, n, key, c.w, c.h,
                                                     backend_ns=backend_ns,
                                                     dependency_digest=dependency_digest)
            if not bad:
                if needs_marker and not c.dry:
                    gfx_cli.complete_page(d, n, key, c.w, c.h,
                                          verify_inputs=lambda: c.verify_inputs("gfx"),
                                          backend_ns=backend_ns)
                events.cache_hit(c, "inspect_page", frames=[0, n])
                print(f"✓ {cid}[{key}]: {n} 帧已缓存"); total += 1; continue
            # dry-run 不清缓存、不 mkdir；保留有效帧供 CDP 分块复用。
            if not c.dry:
                fstxn.mkdir(d)
                gfx_cli.invalidate_page(d)
                for i, _ in bad:
                    fstxn.unlink(d / f"f{i:04d}.png")
            html = (c.root / cl["src"]).resolve()
            # 页内分片（方案 v2 阶段 1，默认关）：SPLIT_FRAMES>0 且页帧数超阈值时把
            # [0,n) 切成 min(SPLIT_MAX, ceil(n/阈值)) 个连续块，每块独立一次
            # render_page（独立 Chrome 实例）；dry-run / CLI 回落态不拆。
            sizes = (None if (c.dry or cdp_mod is None or strict)
                     else _split_page(n, split_frames, split_max))
            if sizes is None:
                jobs.append((cid, key, cl, n, d, html, None,
                             events.task(c, "gfx", kind="gfx_block",
                                         metadata={"page_id": page_id, "backend": "cdp" if cdp_mod else "cli", "frame_range": [0, n]})))
                continue
            off, put = 0, 0
            for sz in sizes:
                fb = list(range(off, off + sz)); off += sz
                if not gfx_cli.verify_frames(d, fb, width=c.w, height=c.h):
                    events.cache_hit(c, "verify_frames", frames=[fb[0], fb[-1] + 1])
                    continue    # 整块已缓存 → 跳过（不起 Chrome、不占池槽）
                jobs.append((cid, key, cl, n, d, html, fb,
                             events.task(c, "gfx", kind="gfx_block",
                                         metadata={"page_id": page_id, "backend": "cdp", "frame_range": [fb[0], fb[-1] + 1]}))); put += 1
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
        return job[7].call(render_block, job)

    def render_block(job):
        cid, key, cl, n, d, html, frames, observation = job
        st = None
        if cdp_mod is not None:
            try:
                # 视口宽高传项目分辨率（render_page 默认 1920×1080，非 1080p 项目此前错渲）。
                # ctx 透传（issue #103）：CDP 常驻 Chrome 交 proc_supervisor 监管
                # （独立进程组 + PDEATHSIG + 守卫 + tmp_run 账本，Profile 不再落系统 /tmp）。
                st = observation.attempt(cdp_mod.render_page, html, d, n, chrome_path=find_chrome(),
                                         width=c.w, height=c.h, frames=frames,
                                         ctx=c,
                                         **({"dependency_record": dependency_records[key]} if strict else {}))
            except Exception as e:
                if strict:
                    gfx_cli.invalidate_page(d)
                    raise
                # CDP 块级失败：只回落本块帧区间（frames=None 时整页，与现行一致）
                fb = range(n) if frames is None else frames
                print(f"⚠ {cid}: CDP 渲染失败（{e}），"
                      f"{'该块' if frames is not None else '整页'}回落 CLI")
                render_cli(fb, d, html, find_chrome())
                st = None
            else:
                if st["failed"]:
                    if strict:
                        gfx_cli.invalidate_page(d)
                        raise DependencyError('strict CDP frame failure; CLI downgrade forbidden')
                    print(f"⚠ {cid}: CDP 帧 {st['failed']} 失败，回落 CLI 补渲")
                    # issue #28：补渲失败不可被 CDP 的 except 吞掉后重试成成功。
                    render_cli(st["failed"], d, html, find_chrome())
                    st["path_counts"]["cli_fallback"] = len(st["failed"])
        if cdp_mod is None:
            render_cli(range(n), d, html, find_chrome())
        if frames is None:
            # —— 整页单块（SPLIT 关或页不超阈值）：日志格式与现行逐字节一致 ——
            rendered_via = "cli 6 并发" if st is None else _cdp_via(st)
            gfx_cli.complete_page(d, n, key, c.w, c.h,
                                  verify_inputs=lambda: c.verify_inputs("gfx"),
                                  backend_ns=backend_ns,
                                  **({'dependency_digest': digest(dependency_records[key]),
                                      'dependency_enforcement': st.get('dependency_enforcement'),
                                      'dependency_observed': st.get('dependency_observed')}
                                     if strict else {}))
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
        gfx_cli.complete_page(d, n, key, c.w, c.h,
                              verify_inputs=lambda: c.verify_inputs("gfx"),
                              backend_ns=backend_ns)
        print(f"✓ {cid}[{key}]: 渲染 {n} 帧（含 alpha，{rendered_via}）")
        manifest_record(c, d, "gfx", key)
        return cid, rendered_via

    if jobs:
        if c.dry:
            # dry-run：只报告将渲染的页，不起 Chrome（否则冷态 all --dry-run 白付
            # 数分钟真实渲染 + GB 级 PNG 落盘，2026-09-11 门控；dry 不拆块）
            for cid, key, cl, n, d, html, frames, observation in jobs:
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


def _stage_exit_code(fn, c: Ctx, *, observation=None) -> int:
    """`vtx all` 编排内运行 qc 阶段并捕获其 SystemExit（2026-09-14 裁定：qc 失败
    不阻断 finish 收尾——final.mp4/qc_report.json 等产物此时已就绪，历史行为
    SystemExit 直接冒出会跳过缝 D 的 run_finish，存量假阴性项目永远到不了收尾）。
    仅 SystemExit 被捕获记退出码，其他异常照旧即时终止（final 未就绪时 finish
    不该跑）；单阶段 `vtx qc` 不经本函数，退出码冒泡语义不变。"""
    try:
        fn(c)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
        print(f"⚠ qc 阶段失败（退出码 {code}）——按裁定不阻断 finish 收尾，"
              "退出码在收尾后如实汇总")
        if observation is not None:
            observation.finish(code, e)
        return code
    return 0


def _observed_stage(c, name, fn, *, capture_exit=False):
    stage = _stage_receipt_name(name, c.draft)
    with events.task(c, stage, kind='stage').scope() as observation:
        try:
            c.begin_stage(stage)
        except RuntimeError as error:
            print(f"✗ {error}"); sys.exit(1)
        if capture_exit:
            code = _stage_exit_code(fn, c, observation=observation)
            observation.finish(code, RuntimeError() if code else None)
            return code
        return fn(c)


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
    后 `vtx mux` 的依赖门 video+audio 恰好满足）。

    返回 qc 阶段退出码（int，0=成功/未跑到；2026-09-14 裁定 qc 失败不阻断 finish
    ——SystemExit 在此捕获记码，由 main 在 finish 后与收尾失败态一并汇总退出）。"""
    by_name = dict(STAGES)
    for name in ("validate", "audit", "prepare"):   # —— 串行头 ——
        print(f"== {name} ==")
        _observed_stage(c, name, by_name[name])
        if until == name:
            return 0

    def _stage_job(name, fn):                        # —— 波内阶段路：现行阶段语义 ——
        def run():
            _observed_stage(c, name, fn)
        return run

    def _pre_layers():                               # —— 波内预编码路：无门无回执 ——
        c._pre_layers = events.task(c, "video", kind="preencode").call(prepare_layers, c)

    run_wave(c, [("gfx", _stage_job("gfx", by_name["gfx"])),
                 ("audio", _stage_job("audio", by_name["audio"])),
                 ("video 预编码", _pre_layers)])
    if until == "gfx":
        return 0   # 波结束即止：audio 回执/seg 缓存已就绪，video 尾不跑
    qc_exit = 0
    for name in ("video", "mux", "qc"):              # —— 串行尾 ——
        print(f"== {name} ==")
        if name == "video":
            _observed_stage(c, name, lambda c: build_video(c, layers=c._pre_layers))   # 波内预编码产物（Ctx.__init__ 显式声明，见 infra）
        elif name == "qc":
            # 2026-09-14 裁定：qc 失败不阻断 finish（产物已就绪）——捕获退出码，
            # 由 main 在 finish 后汇总；video/mux 失败仍即时终止
            qc_exit = _observed_stage(c, "qc", by_name["qc"], capture_exit=True)
        else:
            _observed_stage(c, name, by_name[name])
        if until == name:
            return qc_exit
    return qc_exit

class ExecutionPolicy(Enum):
    """CLI 副作用边界：PLAN 只读；EXECUTE 才允许创建、写入或删除。"""
    PLAN = "plan"
    EXECUTE = "execute"

    @property
    def dry(self):
        return self is ExecutionPolicy.PLAN

    def execute_cleanup(self, plan):
        if self.dry:
            return
        if not isinstance(plan, hygiene.CleanupPlan):
            raise ProjectPathError("拒绝清理：执行需要计划阶段绑定身份的 CleanupPlan")
        plan.execute()


def _symlink_roots(c):
    """cache/out 中本身是符号链接的根。清理与快照一律拒绝跟随（A03 红线）：
    根级链接把 iterdir/rmtree/unlink 全部导向链接目标——攻击者把 cache 换成
    指向任意目录的链接，等 `vtx gc`/`clean` 定时执行即可越界删除外部文件。"""
    return [p for p in (c.cache, c.out) if p.is_symlink()]


def _plan_without_linked_roots(plan, links):
    """丢弃位于符号链接根之内（含根本身）的计划条目（文本前缀判定即可：
    计划条目路径全部由 c.cache/c.out 拼接而来，无需 resolve）。"""
    if not links:
        return plan
    # Filter only: keep the sealed plan and its original identity tokens. A
    # replacement dict or a new seal here would lose/rebind plan-time identity.
    for cat, items in plan.items():
        plan[cat] = [e for e in items
                     if not any(e[0] == l or l in e[0].parents for l in links)]
    return plan


def _cleanup_plan(c, command, *, deep=False, keep_days=0, nuke=False):
    """先完整列出删除候选，再执行；沿用 hygiene 的现有保护策略。

    A02（issue #21 / F-01）清理三段式的计划段（预览=_run_cleanup，执行=
    ExecutionPolicy.execute_cleanup）：
      - clean（默认）：cache/ 整树清空；out/ 只清可再生条目——不可再生交付件
        （hygiene.out_protected：final.mp4/VERSIONS.json/history/FREEZE*/
        final_4k*/reports）永不进计划。
      - clean --i-know-this-nukes-everything（nuke=True）：唯一全删入口，恢复
        全清空语义（cache/out 整树，但锁文件与拒绝的链接目标永不删除）。
      - gc --all 深度回收仍用波1白名单（不在 A02 扩大或缩小保护范围），out 侧
        保护名单改经 out_protected() 单源取判据（等价合并，行为不变）。
      - 普通 gc 复用 gc_plan；cache 不存在时仅需计算 out 的既有中间物规则。
    cache/out 根为符号链接时整根拒绝（打印原因，不列、不删链接目标内容）。
    """
    links = _symlink_roots(c)
    for p in links:
        print(f"✗ 拒绝清理符号链接根 {p.relative_to(c.root)} → {os.readlink(p)}"
              "（不跟随符号链接，防越界删除；如需回收请先解除链接）")
    if command == "clean":
        if nuke:
            # issue #30：危险全删也永不删锁；危险链接仍拒绝并保留其祖先。
            plan = {"clean": []}
            for p in (c.cache, c.out):
                if p.exists() and not p.is_symlink():
                    plan["clean"].extend(hygiene.cleanup_entries(
                        c, p, "全清空（危险旗标：含不可再生交付件，锁除外）", nuke=True))
            return hygiene.CleanupPlan(c, plan, nuke=True)
        plan = {"cache": [], "out": []}
        if c.cache.exists() and not c.cache.is_symlink():
            plan["cache"].extend(hygiene.cleanup_entries(c, c.cache, "可再生缓存"))
        for p in hygiene.cleanup_children(c, c.out):
            if not hygiene.out_protected(p.name):
                plan["out"].extend(hygiene.cleanup_entries(c, p, "可再生 out 条目"))
        return hygiene.CleanupPlan(c, plan)
    if deep:
        plan = {"cache": [], "out": []}
        for root, category in ((c.cache, "cache"), (c.out, "out")):
            if not root.exists() or root.is_symlink():
                continue
            for path in hygiene.cleanup_children(c, root):
                if category == "cache" and (path.name in
                        (hygiene.REPORTS, hygiene.TMP, "snapshots")
                        or path.name in hygiene.KEEP_BASELINES):
                    continue
                if category == "out" and (path.name in ("draft_final.mp4",
                                                        "receipt.json")
                                          or hygiene.out_protected(path.name)):
                    continue
                plan[category].extend(hygiene.cleanup_entries(c, path, "深度回收"))
        return hygiene.CleanupPlan(c, _plan_without_linked_roots(plan, links))
    if c.cache.exists() and not c.cache.is_symlink():
        # gc_plan 会 iterdir cache/out：根为符号链接时同样不得进入（只列清单也算
        # 读穿链接目标）；cache 侧被拒后仅保留 out 的既有中间物规则
        return _plan_without_linked_roots(hygiene.gc_plan(c, keep_days), links)
    out_keep = {"final.mp4", "draft_final.mp4", "draft.mp4", "video_noaudio.mp4",
                "audio.wav", "receipt.json", "receipt.prev.json", "VERSIONS.json",
                "audit_report.json", "qc_report.json", "history", "reports"}
    plan = {"out_intermediate": []}
    for p in hygiene.cleanup_children(c, c.out):
        if p.name not in out_keep and not hygiene.out_protected(p.name):
            plan["out_intermediate"].extend(hygiene.cleanup_entries(c, p, "out 一次性中间物"))
    return hygiene.CleanupPlan(c, plan)


def _clean_preview(c, nuke: bool):
    """A02 预览段：把「不可再生交付件会发生什么」说清楚再动手——

    默认 clean 列出将保留的保护件（用户须看见 final.mp4 留下来了，以及全删
    需要哪个旗标）；危险旗标下列出将被销毁的保护件（最后警告）。只读 out/
    一级条目，不 stat 深层（dir_size 惰性）。dry 与 EXECUTE 都打印（预览不是
    dry 专属：执行前同样要看见）。
    """
    if not c.out.exists() or c.out.is_symlink():
        return
    protected = [p for p in sorted(c.out.iterdir())
                 if hygiene.out_protected(p.name)]
    if nuke:
        print("⚠ 危险操作（--i-know-this-nukes-everything）：以下不可再生交付件将被一并删除")
        for p in protected:
            print(f"  ✗ {p.relative_to(c.root)}  {hygiene.gb(hygiene.dir_size(p))}")
        if not protected:
            print("  （out/ 当前无不可再生交付件）")
        return
    print("保留不可再生交付件（A02 默认保护；确实需要全删请加 --i-know-this-nukes-everything）：")
    for p in protected:
        print(f"  ✓ {p.relative_to(c.root)}  {hygiene.gb(hygiene.dir_size(p))}")
    if not protected:
        print("  （out/ 当前无不可再生交付件）")


def _run_cleanup(c, policy, command, *, deep=False, keep_days=0, nuke=False):
    # 计划段：先完整列出删除候选（nuke 时最后警告先行，眼见为实）
    if command == "clean":
        _clean_preview(c, nuke)
    plan = _cleanup_plan(c, command, deep=deep, keep_days=keep_days, nuke=nuke)
    if command == "gc" and deep:
        print("注：深度回收保留不可再生交付件与 reports/snapshots"
              "（gc 无全删变体；全删唯一入口 clean --i-know-this-nukes-everything）")
    count = total = 0
    for category, items in plan.items():
        if not items:
            continue
        size = sum(s for _, s, _ in items)
        count += len(items); total += size
        print(f"{category}: {len(items)} 项，{hygiene.gb(size)}")
        for path, size, reason in items[:30]:
            print(f"  - {path.relative_to(c.root)}  {hygiene.gb(size)}  {reason}")
        if len(items) > 30:
            print(f"  … 另 {len(items) - 30} 项")
    print(f"合计：{count} 项，{hygiene.gb(total)}")
    # 执行段：PLAN 只到这里（预览输出已在上方），EXECUTE 才落删
    policy.execute_cleanup(plan)
    if policy.dry:
        print("[dry-run] 仅生成清理计划，未删除或写入任何文件")
        return
    if command == "clean":
        if not nuke:
            # out/ 清空后无保护件残留时连空壳一起收走（有保护件则整目录保留）
            if c.out.exists() and not c.out.is_symlink():
                try:
                    c.out.rmdir()
                except OSError:
                    pass
            if c.out.exists():
                kept = sum(1 for p in c.out.iterdir())
                print(f"✓ 已清理 cache/ 与 out/ 可再生条目"
                      f"（out 保留 {kept} 项交付件/锁/拒绝路径；cache 锁与拒绝路径保留）")
            else:
                print("✓ 已清理 cache/ 与 out/（out 无残留；cache 锁与拒绝路径保留）")
        else:
            print("✓ 已清理 cache/ out/（含不可再生交付件——危险旗标已确认；锁与拒绝路径保留）")
    else:
        print(f"✓ 已回收 {count} 项，{hygiene.gb(total)}")


def _preview_command(c, a):
    """只读阶段计划，不调用 legacy dry 分支中仍写 ass/清单/tmp 的执行器。

    CLI 的 dry 不是「禁用 ffmpeg」：预算门、插件钩子、快照轮转等也不可写。
    限于阶段级计划，不把计划误报为已完成渲染或已通过真实产物测量。
    """
    if a.command == "subs":
        if a.subs_extra[0] != "preview":
            c.align_threshold = a.align_threshold
            run_subs(c, a.subs_extra)  # check/render 的 dry 分支是纯读
        else:
            from .subs import _parse_points
            for frame, _ in _parse_points(a.subs_extra[1:], c):
                print(f"[dry-run] subs preview → {c.out / f'subs_preview_f{frame}.png'}")
        return
    if a.command == "snapshot":
        print(f"[dry-run] snapshot：将快照 timeline/文本资产并轮转 {c.cache / 'snapshots'}")
        return
    if a.command == "df":
        print(f"[dry-run] cache: {hygiene.gb(hygiene.dir_size(c.cache))}；"
              f"out: {hygiene.gb(hygiene.dir_size(c.out))}")
        return
    if getattr(c, "range_window", None):
        fa, fb = c.range_window
        fa, fb = max(0, fa), min(c.dur, fb)
        if fa >= fb:
            print(f"✗ 无效区间 [{fa},{fb})（项目总长 {c.dur} 帧）"); sys.exit(2)
        print(f"[dry-run] video draft 区间 [{fa},{fb}) → {c.cache / 'preview'}")
        return
    names = [name for name, _ in STAGES]
    if a.command != "all":
        names = [a.command]
    elif _pipeline_enabled():
        head = ["validate", "audit", "prepare"]
        if a.until in head:
            names = head[:head.index(a.until) + 1]
        else:
            names = head + ["gfx", "audio", "video 预编码"]
            tail = ["video", "mux", "qc"]
            if a.until != "gfx":
                names += tail[:tail.index(a.until) + 1] if a.until in tail else tail
    elif a.until:
        names = names[:names.index(a.until) + 1]
    for name in names:
        print(f"== {name} ==")
        print(f"[dry-run] 计划阶段 {name}（不执行渲染、测量或插件钩子）")
    print("[dry-run] 计划完成；未删除或写入任何文件")


def main():
    """One invocation, including input-load and finish failures."""
    execution = events.ExecutionContext()
    code, failure = 0, None
    try:
        try:
            result = _main(execution)
            code = result or 0
            return result
        except ProjectPathError as error:
            print(f'✗ {error}', file=sys.stderr)
            code, failure = 1, error
            return 1
    except BaseException as error:
        code, failure = events.exit_code(error), error
        raise
    finally:
        execution.finish(code, failure)


def _main(execution=None):
    execution = execution if execution is not None else events.ExecutionContext()
    ap = argparse.ArgumentParser(description="vtx 视频剪辑中间层")
    ap.add_argument("-t", "--timeline", default="timeline.json", help="timeline.json 路径（默认 ./timeline.json）")
    ap.add_argument("--events-jsonl", metavar="PATH",
                    help="optional exclusive JSONL execution evidence; independent of stdout")
    ap.add_argument("--resumed-from-run-id", metavar="UUID",
                    help="explicit predecessor execution (does not bypass input validation)")
    ap.add_argument("--until", metavar="STAGE", default=None,
                    help="all 专属：只执行到该阶段止（validate|audit|prepare|gfx|video|mux|qc，"
                         "STAGES 线性序；audio 为波内成员不作终点）。R4 冷跑链用法："
                         "all --until video → mux → qc（免 mux/qc 白跑；v2 limiter 已并入"
                         " audio，无 post 步）")
    ap.add_argument("--dry-run", "--dry", dest="dry_run", action="store_true",
                    help="只读执行计划：不删除、创建或写入文件（--dry 为兼容别名）")
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
    ap.add_argument("--all", action="store_true",
                    help="gc 专属：深度回收（等价原 clean，但保留 reports/snapshots 与"
                         " final/VERSIONS/history/FREEZE* 等不可再生交付件）")
    ap.add_argument("--i-know-this-nukes-everything", dest="nuke_everything",
                    action="store_true",
                    help="clean 专属危险操作旗标：连不可再生交付件（final.mp4、VERSIONS.json、"
                         "history/、FREEZE* 冻结凭证、final_4k*.mp4）一并全删。默认 clean "
                         "保留这些交付件——只有本旗标显式确认后才是真正的全清空")
    ap.add_argument("--keep-days", type=int, metavar="N", default=None,
                    help="gc 专属：近 N 天动过的条目暂缓回收（默认 0 = 只看键集）")
    ap.add_argument("--keep-versions", type=int, metavar="N", default=None,
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
    ap.add_argument("--capture-impact", action="store_true",
                    help="diff only: capture one timeline's CPU/formal input identities")
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
    a = ap.parse_intermixed_args()
    policy = ExecutionPolicy.PLAN if a.dry_run else ExecutionPolicy.EXECUTE
    # --until 校验（方案三阶段3）：仅 all 合法 + 值域 = _UNTIL_STAGES；两错均退出码 2
    # （与「未知命令」同级：用法错而非运行错，脚本侧好分类）。audio 不可作终点的
    # 原因见 _UNTIL_STAGES 旁注释。
    if a.until is not None:
        if a.command != "all":
            print("--until 仅在 all 命令下可用（正确用法：vtx.py -t timeline.json all --until video）"); sys.exit(2)
        if a.until not in _UNTIL_STAGES:
            print(f"✗ --until 非法阶段名：{a.until!r}（可用：{' | '.join(_UNTIL_STAGES)}；"
                  "audio 是流水线波内成员，不作独立终点）"); sys.exit(2)
    if a.frames is not None and not (a.draft and a.command == "video"):
        print("--frames 仅在 video --draft 下可用（正确用法：vtx.py video --draft --frames 90）"); sys.exit(2)
    if a.frames is not None and a.frames <= 0:
        print("--frames 必须为正整数"); sys.exit(2)
    range_flags = [x for x in (a.range, a.clip, a.track) if x is not None]
    if range_flags and not (a.draft and a.command == "video"):
        print("--range/--clip/--track 仅在 video --draft 下可用"); sys.exit(2)
    if len(range_flags) > 1:
        print("--range / --clip / --track 互斥：窗口来源三选一"); sys.exit(2)
    if range_flags and (a.frames is not None or a.gpu):
        print("--range/--clip/--track 与 --frames/--gpu 互斥"); sys.exit(2)
    if a.draft and a.command not in ("video", "mux"):
        print("--draft 仅作用于 video/mux 阶段"); sys.exit(2)
    if (a.report or a.report_only) and a.command != "audio":
        print("--report/--report-only 仅作用于 audio 阶段"); sys.exit(2)
    if a.report and a.report_only:
        print("--report 与 --report-only 互斥"); sys.exit(2)
    if (a.report or a.report_only) and policy.dry:
        print("✗ --report/--report-only 与 --dry-run/--dry 冲突（报告需要真实产物测量）"); sys.exit(2)
    if a.gpu and a.command != "video":
        print("--gpu 仅作用于 video 阶段"); sys.exit(2)
    if a.strict and a.command != "qc":
        print("--strict 仅作用于 qc 阶段"); sys.exit(2)
    if (a.all or a.keep_days is not None) and a.command != "gc":
        print("--all/--keep-days 仅作用于 gc 命令"); sys.exit(2)
    if a.nuke_everything and a.command != "clean":
        print("--i-know-this-nukes-everything 仅作用于 clean 命令"
              "（gc 永不删除不可再生交付件，无全删变体）"); sys.exit(2)
    if a.keep_versions is not None and a.command not in ("mux", "all"):
        print("--keep-versions 仅作用于 mux/all 命令"); sys.exit(2)
    if (a.keep_days is not None and a.keep_days < 0 or
            a.keep_versions is not None and a.keep_versions < 0):
        print("--keep-days/--keep-versions 必须 ≥0"); sys.exit(2)
    if a.subs_extra and a.command not in ("subs", "diff"):
        print(f"✗ {a.command} 不接受位置参数：{' '.join(a.subs_extra)}"); sys.exit(2)
    if a.json and not (a.command == "diff" or
                       a.command == "audio" and (a.report or a.report_only)):
        print("--json 仅用于 diff 或 audio --report/--report-only"); sys.exit(2)
    if a.command == "subs":
        if (not a.subs_extra or a.subs_extra[0] not in ("render", "preview", "check")
                or a.subs_extra[0] == "preview" and len(a.subs_extra) < 2
                or a.subs_extra[0] != "preview" and len(a.subs_extra) != 1):
            print("✗ 用法：subs render | subs preview <时间点...> | subs check"); sys.exit(2)
    # 未知命令先于 timeline 检查（错字时给最接近建议，不被 timeline 缺失报错挡住）
    valid_names = [s[0] for s in STAGES] + ["all", "clean", "gc", "df", "snapshot", "subs", "diff"]
    if a.command not in valid_names:
        sug = difflib.get_close_matches(a.command, valid_names, n=3, cutoff=0.6)
        hint = f"，最接近：{' | '.join(sug)}" if sug else ""
        print(f"✗ 未知命令：{a.command!r}（可用：{' | '.join(valid_names)}）{hint}"); sys.exit(2)
    if a.resumed_from_run_id:
        try:
            valid = events.canonical_uuid(a.resumed_from_run_id)
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            print("invalid predecessor execution UUID"); sys.exit(2)
        execution.resumed_from_run_id = a.resumed_from_run_id
    if a.capture_impact and a.command != "diff":
        print("--capture-impact only applies to diff"); sys.exit(2)
    if a.command == "diff":
        # 报告对比（信息性，与 gc/df/snapshot 同类）：不依赖 timeline，故在 timeline
        # 检查前分发；两个位置参数复用 subs_extra 槽（argparse `*` 在 command 后的
        # 既有约定，见上注）。纯读命令：不写任何产物、不做 receipt；--dry-run 尊重。
        if a.capture_impact:
            if len(a.subs_extra) != 1:
                print("Usage: diff --capture-impact <timeline.json> [--json NEW_PATH]")
                sys.exit(2)
            from .timeline_diff import run_impact_capture
            sys.exit(run_impact_capture(a.subs_extra[0], a.json, a.dry_run))
        if len(a.subs_extra) != 2:
            print("✗ 用法：diff <旧报告.json> <新报告.json>"
                  "（timeline 语义 diff 同形：diff <旧timeline> <新timeline> [--json PATH]）"); sys.exit(2)
        # 时间线语义 diff（方案五阶段2）：两参均为 timeline JSON（含 project+video_tracks
        # 特征键）时自动走 timeline_diff；否则维持报告对比（diffcmd）——一个 diff 入口，
        # 按输入特征分流，最小惊讶。坏 JSON / 类型不一致（timeline 混报告）给分类文案。
        from .diffcmd import run_diff
        from .timeline_diff import is_timeline
        ja, jb = a.subs_extra
        try:
            da = json.loads(Path(ja).read_text(encoding="utf-8"))
            db = json.loads(Path(jb).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"✗ 输入读取/解析失败（timeline 语义 diff 与报告对比都要求两份合法 JSON）：{e}")
            sys.exit(2)
        if (isinstance(da, dict) and
                str(da.get("schema", "")).startswith("framivra-impact-snapshot-")):
            from .timeline_diff import run_snapshot_diff
            sys.exit(run_snapshot_diff(da, jb, a.json, a.dry_run))
        tl_a, tl_b = is_timeline(da), is_timeline(db)
        if tl_a and tl_b:
            if a.json and a.dry_run:
                print("✗ --json 落盘与 --dry-run 冲突（diff 为纯读命令，dry 下不写文件）"); sys.exit(2)
            from .timeline_diff import run_timeline_diff
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
    if not policy.dry and a.command not in ("df", "clean", "gc", "snapshot", "diff"):
        role = ("all_until_mux" if a.command == "all" and a.until == "mux" else
                "qc_strict" if a.command == "qc" and a.strict else None)
        from .execution_links import open_response
        stream, request = open_response(tp, role)
        if stream is not None:
            execution.activate(stream, request)
        elif a.events_jsonl:
            try:
                execution.activate(events.open_sink(a.events_jsonl))
            except (OSError, ValueError):
                execution._warn()
    if not tp.exists():
        print(f"timeline 不存在: {tp}")
        raise SystemExit(1) from FileNotFoundError()
    try:
        c = Ctx(tp, execution=execution)   # PR#1：构造期校验 cache_dependencies（缺失/非文件 → 友好报错）
    except (ValueError, KeyError, TypeError, ZeroDivisionError, FileNotFoundError) as e:
        # 2026-09-11 扩捕获：timeline 结构缺键（KeyError）/类型错（TypeError）/fps=0
        # （ZeroDivisionError）/文件缺失（FileNotFoundError）均友好打印而非裸栈
        print(f"✗ timeline 无效：{e}"); sys.exit(1)
    c.dry = policy.dry   # 所有阶段读取同一显式策略；PLAN 下不得 mkdir
    c.draft = a.draft; c.frames = a.frames; c.gpu = a.gpu; c.strict = a.strict
    c.keep_versions = a.keep_versions if a.keep_versions is not None else 2
    # 区间渲染（方案五阶段1）：解析窗口帧区间；独立命名空间路径，绕过 receipt 依赖门
    if range_flags:
        from .range import parse_timespec, window_from_clip, window_from_track
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
    if a.command in ("clean", "gc"):
        _run_cleanup(c, policy, a.command, deep=a.all,
                     keep_days=a.keep_days or 0, nuke=a.nuke_everything)
        return
    if policy.dry:
        _preview_command(c, a)
        return
    # issue #23（A04 / P0）写路径统一安全门：cache/out 两写根及一切衍生写路径
    # （回执/锁文件、manifest、VERSIONS/history、qc_report、tmp、快照、gfx/seg/
    # audio 缓存……全部由两根拼接而来）绝不允许经 ../、绝对路径或符号链接逃逸
    # 项目根——在首个写动作（mkdir）之前经 ensure_inside_project 判定。素材源的
    # 外部引用是「读」，不经过本门（validate/gc 等照常，对照组见 tests/safety/
    # test_timeline_escape_safety.py）。符号链接指向项目内 = 允许（resolve 后仍
    # 在根内）；指向外 = 拒绝并报去向。clean/gc 在上方已分发——A02/#21/#30 的
    # 清理保护有自己的拒绝语义与退出码契约（拒绝条目保留、整体仍 exit 0），
    # 不受本门影响；--dry-run 计划路径亦在门之前返回（只读）。
    try:
        ensure_inside_project(c.cache, c.root, what="cache 写根")
        ensure_inside_project(c.out, c.root, what="out 写根")
    except ProjectPathError as e:
        print(f"✗ {e}"); sys.exit(1)
    # B03（issue #27 / F-05，2026-10-13）：cache/out 布局创建延迟到实际写阶段——
    # 此处不再无条件 mkdir（旧行为让 df/validate 只读部分等一切 EXECUTE 命令
    # 都长出 cache/ 与 out/，命令执行前后项目树出现新目录即违例）。落点分工：
    #   - 只读路径零建目录：df 在下方即刻返回（hygiene.df 不再 mkdir）；
    #     validate 的契约校验段零写盘；dry-run 已在上方门之前返回。
    #   - out/ 由首个真实写动作自建：回执链 begin_stage/receipt → fstxn 事务的
    #     parents mkdir（subs check/preview 亦各自 mkdir）。
    #   - cache/ 由各写侧自建：prepare（cache/audio）/gfx（cache/gfx）/subs
    #     render/snapshot/range/qc 记账等落笔点 mkdir(parents=True)。
    # A04 安全门语义与位置不动：门在校验（先行），mkdir 延迟到写侧——首个
    # mkdir 仍发生在门之后，tests/safety 对抗用例不受影响。
    if a.command == "df":
        df(c); return
    if a.command == "snapshot":
        # A03×A01 红线：cache 根为符号链接时快照会 mkdir/写穿链接目标（越界
        # 写入链接所指目录）——拒绝执行；dry 计划路径只打印不受影响。
        # issue #23（A04）：指向根外的链接根到不了这里（上方统一写根门已拒）；
        # hygiene.snapshot 自身再对逐资产引用做 ../、绝对路径、符号链接逃逸的
        # 写前校验（timeline 不可信输入），拒绝时在此转友好报错 + 退出码 1。
        if c.cache.is_symlink():
            print(f"✗ 拒绝快照：{c.cache.relative_to(c.root)} 是符号链接"
                  f"（→ {os.readlink(c.cache)}，不跟随，防越界写入；如需快照请先解除链接）")
            sys.exit(1)
        try:
            snapshot(c)
        except ProjectPathError as e:
            print(f"✗ {e}"); sys.exit(1)
        return
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
        from .range import run_range
        run_range(c, *c.range_window)
        return
    # 音频纯报告分发（方案五阶段3，区间渲染同款短路）：只读已有产物，不进依赖门、
    # 不做 receipt——报告对正式产物链零干扰（写 out/audio_report.json 一个报告件）
    if a.report_only:
        from .audio_report import run_report
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
    qc_exit = 0
    if a.command == "all" and _pipeline_enabled():
        # 方案二阶段2：all 默认走依赖图（串行头 → 并行波 → 串行尾）；
        # VTX_PIPELINE=0 回退下方线性序列（独立 A/B 开关）；
        # 方案三阶段3：--until 截断在依赖图内按阶段族映射（见 _run_all_pipeline）
        qc_exit = _run_all_pipeline(c, until=a.until)
    else:
        for name, fn in todo:
            print(f"== {name} ==")
            # PR#1：阶段开始前做依赖门 + 撤销下游旧成功（失败重跑不得保留旧 QC PASS）。
            # draft 模式的 video/mux 记为 video_draft/mux_draft。
            if name == "qc" and a.command == "all":
                # 2026-09-14 裁定（同流水线路径）：all 内 qc 失败不阻断 finish——
                # 捕获退出码交 main 汇总；单阶段 `vtx qc` 仍走下方 fn(c) 冒泡退出
                qc_exit = _observed_stage(c, name, fn, capture_exit=True)
            else:
                _observed_stage(c, name, fn)
            # audio --report：混音阶段（含依赖门/receipt）完成后附加一等报告（方案五阶段3）；
            # 报告判 FAIL 时以报告退出码覆盖（阶段本身成功，但报告是本次运行的结论）
            if name == "audio" and a.report:
                from .audio_report import run_report
                rc = run_report(c, json_path=a.json)
                if rc: sys.exit(rc)
    # 缝 D（协议 v1.2，P6）：`vtx all` 全链（mux/qc 完成后）触发插件 finish 收尾钩子
    # ——blender-bridge 类「渲染完成自动导出」动作挂此缝。qc 失败不阻断 finish
    # （final.mp4/qc_report.json 等产物已就绪——存量假阴性项目也要能到收尾），退出码
    # 按 qc/finish 各自失败态如实汇总（任一失败 exit 1）。--draft / --dry-run /
    # --until 截断链不触发（draft 链无正式产物；dry 不落产物；截断链非完整渲染）。
    # 收尾失败以退出码 1 结束但产物已就绪（final.mp4 不受影响——失败可见即可）。
    finish_failed = False
    if (a.command == "all" and a.until in (None, "qc")
            and not c.draft and not c.dry):
        from .pluginlib import hooks as _plugin_hooks
        with events.task(c, "finish", kind="stage").scope() as observation:
            _finish_errs = _plugin_hooks.run_finish(c)
            if _finish_errs:
                observation.finish(1, RuntimeError())
        if _finish_errs:
            for e in _finish_errs:
                print("  ✗", e)
            print("✗ finish 钩子失败（正式产物已就绪；修复后重跑对应插件动作或 `vtx all`）")
            finish_failed = True
    if qc_exit or finish_failed:
        sys.exit(qc_exit or 1)
    # 完成行（方案三阶段3 微调）：--until 截在 mux/qc 之前时 final.mp4 尚不存在，
    # 打印已跑阶段名单而非产物路径（all 缺省/mux/qc 及单阶段 mux/qc 仍打印产物）
    final_ready = a.command in ("all", "mux", "qc") and a.until in (None, "mux", "qc")
    print("完成：", c.out / "final.mp4" if final_ready else names)

if __name__ == "__main__":
    sys.exit(main())
