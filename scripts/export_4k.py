#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_4k.py · 4K 投稿版并行导出（方案三阶段2：N 段并行 h264_nvenc p4 + concat）

替代 runbook R5 的单进程 x264 medium 4K 命令（bench_p2 2026-09-13 实测 133.8s）：
母带按 timeline 帧数均分 N 段，每段独立「输入 seek + lanczos 放大 + nvenc p4」并行
编码，段间 concat demuxer -c copy 无损拼接（各段统一编码参数，copy 拼接零重编码）。
8 段并行 nvenc p4 实测 236fps（60s 片段 7.6s），全片外推 ~71s。

段缓存（对齐 composite.render_segmented 惯例）：段文件名含内容寻址键
sha1(媒体 stat 指纹 + 段区间/放大/编码参数)[:12]（stat 指纹复用 vtx/cache_state.
media_stat_digest），命中且流头帧数==段长跳过重编——发布链重跑（如断言后改参数重导）
段全命中时墙钟秒级。

音频：段内一律 -an，aac 从母带只在最终 concat 时 -c:a copy 直通一次——aac 段切会
坏帧边界（帧间重叠窗口跨段界必坏），任何「段内带音频再拼」的方案都不做。

NVENC 探测（合成帧须 ≥145 宽——128px 假失败坑见 DEV.md §二）：失败显式告警后整链
回落现行 x264 medium 单进程命令（--fallback-x264 强制回落），不静默。
自检：concat 后流头帧数必须 == project.duration_frames，不等报错退出（对齐
render_segmented 拼接自检）。

NVENC 并发纪律（跨进程，对齐 vtx/encoders.py「NVENC 会话预算」注释口径）：本脚本
8 段并行是独立进程，不占 vtx 进程内 VTX_NVENC_BUDGET 信号量（该预算只管 vtx 本
进程的 seg 预编码/段渲染）。若与 vtx 的 NVENC 阶段并行运行，两进程会话数叠加可能
超驱动并发上限（拐点实测 8）——须自行下调本脚本并发（--segments）或错峰运行
（R5 时序上在 vtx 完成后执行，正常流程无重叠）。

用法（项目根）：
  env python3 scripts/export_4k.py -t timeline.json [--media out/final.mp4]
      [-o out/final_4k.mp4] [--segments 8] [--fallback-x264]

产出后跑规格断言：
  bash $VIDEO_TOOLS/scripts/check_4k.sh out/final_4k.mp4

来源：docs/plans-2026-09-11/方案三 阶段2。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# v2 §1.4 包化：注入 repo 根 + 包导入（不再注入 repo/vtx，防双模块裂分）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vtx.cache_state import media_stat_digest

SCALE_4K = "scale=3840:2160:flags=lanczos"
# nvenc 段编码参数（方案三阶段2定版）：p4 实测快于 p6；-g 250 < check_4k 门 300；
# 码率三参对齐 R5 x264 命令（19.5M 目标均值 + 55M/80M 峰值缓冲）。
NVENC_ARGS = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
              "-b:v", "19.5M", "-maxrate", "55M", "-bufsize", "80M",
              "-profile:v", "high", "-pix_fmt", "yuv420p", "-g", "250"]
# 回落路径 = runbook R5 现行命令逐字一致（唯一权威命令，改这里须同步 runbook）
X264_ARGS = ["-c:v", "libx264", "-profile:v", "high", "-preset", "medium",
             "-b:v", "19.5M", "-maxrate", "55M", "-bufsize", "80M",
             "-pix_fmt", "yuv420p"]


def _sh(cmd, need=True):
    """子进程封装（对齐 infra.Ctx.run 语义）：失败打印命令 + stderr 尾部并退出。"""
    r = subprocess.run([str(x) for x in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 and need:
        print("CMD FAILED:", " ".join(map(str, cmd))[:300], file=sys.stderr)
        print(r.stderr[-1500:], file=sys.stderr)
        sys.exit(1)
    return r


def split_frames(total: int, n: int):
    """帧均分 N 段：前 total%n 段各 floor+1 帧。返回升序 [(a,b)]，Σ帧数==total、
    边界严格单调（n>total 时收缩到 total 段，每段 ≥1 帧）。"""
    n = max(1, min(n, total))
    base, rem = divmod(total, n)
    edges = [0]
    for i in range(n):
        edges.append(edges[-1] + base + (1 if i < rem else 0))
    return [(edges[i], edges[i + 1]) for i in range(n)]


def seg_key(media_fp: str, a: int, b: int, scale: str, codec) -> str:
    """段内容寻址键：sha1(媒体 stat 指纹 + 段区间/放大/编码参数)[:12]。

    媒体变（stat 指纹变）或段参数/编码器变 → 键变 → 旧段不再命中（对齐 composite
    段缓存语义；编码器 token 入键防 nvenc/x264 切换后吃到旧段）。
    """
    h = hashlib.sha1()
    h.update((media_fp + "\n").encode())
    h.update(f"{a}|{b}|{scale}\n".encode())
    h.update((" ".join(map(str, codec)) + "\n").encode())
    return h.hexdigest()[:12]


def probe_nb_frames(path) -> int:
    """流头 nb_frames（本脚本产物均为 mp4 流头精确）；缺失回落 -count_packets。"""
    r = _sh(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(path)])
    nb = r.stdout.strip()
    if not nb.isdigit() or int(nb) == 0:
        r2 = _sh(["ffprobe", "-v", "error", "-select_streams", "v:0",
                  "-count_packets", "-show_entries", "stream=nb_read_packets",
                  "-of", "csv=p=0", str(path)])
        nb = r2.stdout.strip()
    return int(nb) if nb.isdigit() else 0


def probe_media_fps(path) -> float:
    """母带 r_frame_rate → fps（P2-4 前置校验用）。解析失败/分母非正 → fail-closed
    显式报错退出（不猜测回落——帧率口径错了 seek 时刻全错）。"""
    r = _sh(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(path)])
    rfr = r.stdout.strip()
    num, _, den = rfr.partition("/")
    try:
        fps = float(num) / float(den or 1)
        if fps <= 0:
            raise ValueError
    except (ValueError, ZeroDivisionError):
        print(f"✗ 无法解析母带帧率 r_frame_rate={rfr!r}：{path}", file=sys.stderr)
        sys.exit(2)
    return fps


def nvenc_probe() -> bool:
    """NVENC 可用性探测（encoders.nvenc_ok 同款命令）：≥145 宽合成帧试编一次。
    VTX_NO_NVENC=1 显式禁用（A/B 回退开关）。"""
    if os.environ.get("VTX_NO_NVENC"):
        return False
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=256x144:d=0.04",
                        "-frames:v", "1", "-c:v", "h264_nvenc",
                        "-f", "null", "-"], capture_output=True, text=True)
    return r.returncode == 0


def seg_cmd(media, a: int, nframes: int, fps: int, scale: str, codec, out):
    """单段编码命令。-ss 输入 seek（-i 之前）transcode 下帧精确；seek 时刻取
    (a-0.2)/fps 保证输出首帧=全局帧 a（vtx/qc.py gray_frame 同款语义，a=0 用 0）。"""
    ss = "0" if a == 0 else f"{(a - 0.2) / fps:.6f}"
    return ["ffmpeg", "-v", "error", "-y", "-ss", ss, "-i", str(media),
            "-vf", scale, "-frames:v", str(nframes), "-an", *codec, str(out)]


def fallback_x264_cmd(media, out):
    """回落命令 = runbook R5 现行 x264 medium 单进程（含 -c:a copy + faststart）。"""
    return ["ffmpeg", "-v", "error", "-y", "-i", str(media), "-vf", SCALE_4K,
            *X264_ARGS, "-c:a", "copy", "-movflags", "+faststart", str(out)]


def concat_copy_cmd(list_txt, media, out):
    """concat -c copy + 母带音轨直通（视频取段、音频取母带，各自零重编码）。"""
    return ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_txt), "-i", str(media),
            "-map", "0:v", "-map", "1:a", "-c", "copy",
            "-movflags", "+faststart", str(out)]


def concat_entry(path: Path) -> str:
    """concat 清单项：绝对 POSIX 路径 + 引号转义（composite.render_segmented 同款，
    '#' 等特殊字符目录下相对路径会被 URL 基准解析出错）。"""
    name = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{name}'\n"


def project_root(tl_path) -> Path:
    """项目根 = timeline 所在目录（abspath，不 resolve）。

    符号链接 timeline（bench 项目挂 live timeline 的形态）不得穿透到 live 项目——
    resolve() 会把项目根换成链接目标目录，段缓存/输出全部写进 live（vtx.py 同款
    教训，见 DEV.md 迁移即测试基线「防符号链接穿透把回执写到 live 项目」）。
    """
    return Path(os.path.abspath(str(tl_path))).parent


def export(tl_path: Path, media: Path, out: Path, segments: int = 8,
           force_fallback: bool = False, scale: str = SCALE_4K) -> dict:
    """主流程。返回统计 dict（encoder/wall/seg_wall/concat/hits，测试与打点共用）。
    scale 参数仅供小规模合成素材的集成测试覆盖（默认 4K）。"""
    t_total = time.monotonic()
    tl = json.loads(Path(tl_path).read_text(encoding="utf-8"))
    proj = tl["project"]
    fps, total = int(proj["fps"]), int(proj["duration_frames"])
    proj_root = project_root(tl_path)
    if not Path(media).exists():
        print(f"✗ 母带不存在：{media}", file=sys.stderr); sys.exit(2)
    mnb = probe_nb_frames(media)
    if mnb != total:
        print(f"✗ 母带帧数 {mnb} ≠ timeline duration_frames {total}（先跑 vtx all 产出现行母带）",
              file=sys.stderr); sys.exit(2)
    # fps 前置校验（阶段2审查 P2-4）：timeline fps 被单独误改而母带未换时帧数校验
    # 拦不住（帧数没变），seek 时刻按错 fps 计算 → 产出内容错位的成片且 check_4k
    # 全过（其 30/1 断言对母带本身恒真）——母带 r_frame_rate 与 timeline fps 不一致
    # 必须显式报错。
    mfps = probe_media_fps(media)
    if abs(mfps - fps) > 0.01:
        print(f"✗ 母带 r_frame_rate {mfps:g}fps ≠ timeline fps {fps}（timeline fps 被改而母带"
              f"未换？seek 会按错 fps 取帧——先跑 vtx all 产出现行母带，或改回 timeline fps）",
              file=sys.stderr); sys.exit(2)

    # ---- NVENC 探测（失败显式告警回落，不静默——DEV.md 坑位教训） ----
    if force_fallback or not nvenc_probe():
        if not force_fallback:
            print("⚠ NVENC 探测失败，回落 x264 medium 单进程（显式告警，非静默）")
        cmd = fallback_x264_cmd(media, out)
        t0 = time.monotonic(); _sh(cmd); dt = time.monotonic() - t0
        nb = probe_nb_frames(out)
        if nb != total:
            print(f"✗ 回落路径自检失败：帧数 {nb} ≠ {total}", file=sys.stderr); sys.exit(1)
        print(f"[timing] x264 回落单进程: {dt:.1f}s")
        stats = {"encoder": "x264-medium(回落)", "wall": time.monotonic() - t_total,
                 "seg_wall": dt, "concat": 0.0, "hits": 0, "segments": 1}
        print(f"✓ 4K 导出完成（回落路径）：{out}（{total}f，总墙钟 {stats['wall']:.1f}s）")
        return stats

    # ---- 段切分 + 内容寻址缓存 ----
    spans = split_frames(total, segments)
    segdir = proj_root / "cache" / "4k"; segdir.mkdir(parents=True, exist_ok=True)
    media_fp = media_stat_digest(media)
    todo, seg_paths, hits = [], [], 0
    for i, (a, b) in enumerate(spans):
        key = seg_key(media_fp, a, b, scale, NVENC_ARGS)
        segp = segdir / f"seg{i:02d}_{a:06d}_{key}.mp4"
        seg_paths.append(segp)
        # 命中即跳过（键已含全部内容决定因素；流头帧数检查是便宜兜底，对齐 composite）
        if segp.exists() and probe_nb_frames(segp) == b - a:
            print(f"  ✓ seg {i} [{a},{b}) 已缓存")
            hits += 1
        else:
            todo.append((i, a, b, segp))

    # ---- 段并行编码（ThreadPoolExecutor N 并行；完成序打点） ----
    t_seg = time.monotonic()
    if todo:
        print(f"  段编码：{len(todo)} 段待编，{len(todo)} 线程并行（h264_nvenc p4）")
        failed = []

        def render_one(item):
            i, a, b, segp = item
            cmd = seg_cmd(media, a, b - a, fps, scale, NVENC_ARGS, segp)
            t0 = time.monotonic()
            try:
                _sh(cmd)
            except SystemExit:
                raise RuntimeError(f"seg {i} 编码失败")   # 线程内 sys.exit 不生效，转抛
            return i, a, b, time.monotonic() - t0

        with ThreadPoolExecutor(max_workers=len(todo)) as ex:
            futs = {ex.submit(render_one, it): it for it in todo}
            for fut in as_completed(futs):               # 完成序打印（不按段号）
                try:
                    i, a, b, ts = fut.result()
                    print(f"  ✓ seg {i} [{a},{b}) {ts:.1f}s")
                except RuntimeError as e:
                    print(f"✗ {e}", file=sys.stderr); failed.append(str(e))
        if failed:
            print(f"✗ {len(failed)} 段编码失败，中止", file=sys.stderr); sys.exit(1)
    seg_wall = time.monotonic() - t_seg
    print(f"[timing] 段渲染（{len(spans)} 段 / {len(todo)} 编 / 命中 {hits}）: {seg_wall:.1f}s")

    # ---- concat -c copy + 音频直通 + 自检 ----
    lst = segdir / "list.txt"
    lst.write_text("".join(concat_entry(s) for s in seg_paths), encoding="utf-8")
    t0 = time.monotonic()
    _sh(concat_copy_cmd(lst, media, out))
    concat_dt = time.monotonic() - t0
    nb = probe_nb_frames(out)
    if nb != total:
        print(f"✗ 拼接自检失败：总帧数 {nb or '?'} ≠ {total}（段边界丢帧，禁止交付）",
              file=sys.stderr); sys.exit(1)
    print(f"  ✓ 拼接自检：总帧数 {nb} == {total}")
    print(f"[timing] concat+封装（音轨母带直通）: {concat_dt:.1f}s")
    wall = time.monotonic() - t_total
    stats = {"encoder": "nvenc-p4", "wall": wall, "seg_wall": seg_wall,
             "concat": concat_dt, "hits": hits, "segments": len(spans)}
    print(f"[timing] 总墙钟: {wall:.1f}s")
    print(f"✓ 4K 导出完成：{out}（{total}f，nvenc p4 ×{len(spans)} 段，命中 {hits}）")
    return stats


def main():
    ap = argparse.ArgumentParser(description="4K 投稿版并行导出（方案三阶段2）")
    ap.add_argument("-t", "--timeline", default="./timeline.json")
    ap.add_argument("--media", default=None, help="母带（缺省 <timeline目录>/out/final.mp4）")
    ap.add_argument("-o", "--out", default=None, help="输出（缺省 <timeline目录>/out/final_4k.mp4）")
    ap.add_argument("--segments", type=int, default=8, help="并行段数（默认 8）")
    ap.add_argument("--fallback-x264", action="store_true",
                    help="强制回落 x264 medium 单进程（现行 R5 命令）")
    args = ap.parse_args()
    tl_path = Path(os.path.abspath(args.timeline))
    proj_root = project_root(tl_path)
    media = Path(args.media) if args.media else proj_root / "out" / "final.mp4"
    out = Path(args.out) if args.out else proj_root / "out" / "final_4k.mp4"
    export(tl_path, media, out, segments=args.segments,
           force_fallback=args.fallback_x264)
    print(f"→ 规格断言：bash {Path(__file__).resolve().parent / 'check_4k.sh'} {out}")


if __name__ == "__main__":
    main()
