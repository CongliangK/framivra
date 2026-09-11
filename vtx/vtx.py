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
  mux        视音频封装
  qc         渲染后质检（帧数/底轨切点帧比对/上层窗口生效/泄漏/响度归一），写 qc_report.json
             （--strict：帧数改用 -count_frames 真解码计数，默认容器元数据）
  clean      清空 cache/ 与 out/
  gc         缓存回收（键集外垃圾 + tmp 孤儿 + out 中间物；--dry 只列清单，--all 深度回收，
             --keep-days N 暂缓近 N 天条目；cache/reports/ 永不回收）
  df         体积体检：cache/out 分项占用 + top-20 大文件 + tmp 孤儿清单
  snapshot   timeline + 文本资产快照到 cache/snapshots/（只留最近 2 份，VTX_SNAPSHOT_KEEP 可调）

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

模块布局（2026-09-10 单文件拆分为包，入口本文件保持可直接运行）：
  infra.py      Ctx/DRY/ffmpeg 执行/并行调度        encoders.py  Chrome/NVENC/编码参数选型
  composite.py  切点选择 + 合成 + ass（RenderPlan）  qc.py        质检
  stages.py     validate/audit/prepare/audio/mux    gfx_render.py CDP gfx 渲染后端
  本文件        gfx 阶段 + main/argparse/STAGES 编排

约定：AI 智能体不应手写 ffmpeg 剪辑命令；只编写/修改 timeline.json 并调用本工具。
每条时间量都是整数帧；区间 [in, out) 左闭右开。回执写入 out/receipt.json。
契约 v2（D15 轨道等价模型）：video_tracks 所有轨道等价，数组顺序即图层顺序（[0] 最底）；
画布为黑场，无任何覆盖性约束；kind（aroll/broll/screen/pip/gfx）纯语义标注，渲染不区分；
clip 的 scale/x/y/opacity/fade 全部可选（缺省＝全屏不透明），fade 统一为 alpha 淡入淡出。
仅依赖 python3 标准库 + ffmpeg/ffprobe/google-chrome。
"""
import argparse, difflib, os, shutil, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# 入口可直接 `python3 vtx/vtx.py ...` 运行：把本目录加进 sys.path 后平铺 import
# 同目录模块（与既有 gfx_render 平铺引用同一模式）。
VTX_DIR = Path(__file__).resolve().parent
if str(VTX_DIR) not in sys.path:
    sys.path.insert(0, str(VTX_DIR))

from composite import build_video
from encoders import find_chrome
from hygiene import budget_gate, df, gc, gfx_key, gfx_dir, manifest_record, snapshot
from infra import Ctx
from qc import qc
from stages import audit, build_audio, mux, prepare, validate


# ---------------------------------------------------------------- gfx
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

    jobs = []   # (cid, key, cl, n, d, html) 需要渲染的页
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
            jobs.append((cid, key, cl, n, d, (c.root / cl["src"]).resolve()))

    # 页级并行（2026-09-11）：Chrome 对同源 tab 共用渲染进程，单浏览器内加 tab 光栅
    # 收益递减；真正的并行单位是浏览器实例×页。VTX_GFX_PAGES 控制并行页数（默认 6）。
    page_workers = max(1, int(os.environ.get("VTX_GFX_PAGES", "6")))

    def render_one(job):
        cid, key, cl, n, d, html = job
        rendered_via = "cli"
        if cdp_mod is not None:
            try:
                # 视口宽高传项目分辨率（render_page 默认 1920×1080，非 1080p 项目此前错渲）
                st = cdp_mod.render_page(html, d, n, chrome_path=find_chrome(),
                                         width=c.w, height=c.h)
                if st["failed"]:
                    print(f"⚠ {cid}: CDP 帧 {st['failed']} 失败，回落 CLI 补渲")
                    render_cli(st["failed"], d, html, find_chrome())
                rendered_via = f"{st['tabs']} tabs CDP, {st['ms_per_frame']:.0f}ms/帧"
            except Exception as e:
                print(f"⚠ {cid}: CDP 渲染失败（{e}），整页回落 CLI")
                rendered_via = f"cli（CDP 失败回落: {e}）"
        if cdp_mod is None or rendered_via.startswith("cli（"):
            render_cli(range(n), d, html, find_chrome())
            rendered_via = "cli 6 并发"
        got = len(list(d.glob("f*.png")))
        if got != n: print(f"✗ {cid}: 期望 {n} 帧得到 {got}"); sys.exit(1)
        print(f"✓ {cid}[{key}]: 渲染 {n} 帧（含 alpha，{rendered_via}）")
        manifest_record(c, d, "gfx", key)
        return cid, rendered_via

    if jobs:
        if c.dry:
            # dry-run：只报告将渲染的页，不起 Chrome（否则冷态 all --dry-run 白付
            # 数分钟真实渲染 + GB 级 PNG 落盘，2026-09-11 门控）
            for cid, key, cl, n, d, html in jobs:
                print(f"  [dry-run] gfx {cid}[{key}]: 将渲染 {n} 帧（Chrome {c.w}×{c.h}）")
            total += len(jobs)
        else:
            with ThreadPoolExecutor(min(page_workers, len(jobs))) as pex:
                for cid, rendered_via in pex.map(render_one, jobs):
                    engine_notes.append(f"{cid}: {rendered_via}")
                    total += 1
    c.receipt("gfx", True, f"{total} gfx clips"
              + ("｜" + "; ".join(engine_notes) if engine_notes else ""))


STAGES = [("validate", validate), ("audit", audit), ("prepare", prepare), ("gfx", gfx),
          ("video", build_video), ("audio", build_audio), ("mux", mux), ("qc", qc)]

def main():
    ap = argparse.ArgumentParser(description="vtx 视频剪辑中间层")
    ap.add_argument("-t", "--timeline", default="timeline.json", help="timeline.json 路径（默认 ./timeline.json）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的 ffmpeg 命令，不执行")
    ap.add_argument("--draft", action="store_true", help="草稿模式（仅 video/mux）：540p+NVENC 快速预览，不影响正式产物")
    ap.add_argument("--frames", type=int, metavar="N", help="video --draft 专属：只渲前 N 帧")
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
    ap.add_argument("command",
                    help="all | " + " | ".join(s[0] for s in STAGES) +
                         " | clean | gc | df | snapshot")
    a = ap.parse_args()
    if a.frames and not (a.draft and a.command == "video"):
        print("--frames 仅在 video --draft 下可用（正确用法：vtx.py video --draft --frames 90）"); sys.exit(1)
    if a.draft and a.command not in ("video", "mux"):
        print("--draft 仅作用于 video/mux 阶段（正确用法：vtx.py video --draft 或 vtx.py mux --draft）"); sys.exit(1)
    if a.gpu and a.command != "video":
        print("--gpu 仅作用于 video 阶段（正确用法：vtx.py video --gpu）"); sys.exit(1)
    # 未知命令先于 timeline 检查（错字时给最接近建议，不被 timeline 缺失报错挡住）
    valid_names = [s[0] for s in STAGES] + ["all", "clean", "gc", "df", "snapshot"]
    if a.command not in valid_names:
        sug = difflib.get_close_matches(a.command, valid_names, n=3, cutoff=0.6)
        hint = f"，最接近：{' | '.join(sug)}" if sug else ""
        print(f"✗ 未知命令：{a.command!r}（可用：{' | '.join(valid_names)}）{hint}"); sys.exit(2)
    # 不跟随符号链接（resolve() 会穿透 cp -rs 链接树等隔离副本，把 out/ 回执写到 live 项目）
    tp = Path(os.path.abspath(os.path.expanduser(a.timeline)))
    if not tp.exists(): print(f"timeline 不存在: {tp}"); sys.exit(1)
    try:
        c = Ctx(tp)   # PR#1：构造期校验 cache_dependencies（缺失/非文件 → 友好报错）
    except ValueError as e:
        print(f"✗ timeline 无效：{e}"); sys.exit(1)
    c.cache.mkdir(exist_ok=True); c.out.mkdir(exist_ok=True)
    c.dry = a.dry_run   # 原 DRY 全局改为 Ctx 字段（消除可变全局）
    c.draft = a.draft; c.frames = a.frames; c.gpu = a.gpu; c.strict = a.strict
    c.keep_versions = a.keep_versions   # mux：out/history/ 保留版数
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
    names = [s[0] for s in STAGES] if a.command == "all" else [a.command]
    todo = STAGES if a.command == "all" else \
        [next((s for s in STAGES if s[0] == a.command), None)]
    if todo == [None]:
        names = " | ".join([s[0] for s in STAGES] + ["clean", "gc", "df", "snapshot"])
        print(f"✗ 未知命令：{a.command!r}（可用：{names}）"); sys.exit(2)
    for name, fn in todo:
        print(f"== {name} ==")
        # PR#1：阶段开始前做依赖门 + 撤销下游旧成功（失败重跑不得保留旧 QC PASS）。
        # draft 模式的 video/mux 记为 video_draft/mux_draft。
        stage = name + "_draft" if c.draft and name in ("video", "mux") else name
        try:
            c.begin_stage(stage)
        except RuntimeError as e:
            print(f"✗ {e}"); sys.exit(1)
        fn(c)
    print("完成：", c.out / "final.mp4" if a.command in ("all", "mux", "qc") else names)

if __name__ == "__main__":
    main()
