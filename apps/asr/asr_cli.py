#!/usr/bin/env python3
"""asr_cli —— Qwen3-ASR 字幕产线总入口（系统 python3 ≥3.9 stdlib）

用法：
  python3 apps/asr/asr_cli.py sidecar start|stop|status|health   # 服务管理（start 自动拉起+等健康）
  python3 apps/asr/asr_cli.py transcribe <输入.wav|mp4|...> [--timestamps] [--json]
      # 默认打印纯文本；--timestamps --json 附 words[]（字级秒，3 位小数）
  python3 apps/asr/asr_cli.py srt <输入> -o <out.srt>
      [--limit 18] [--rate-cap 9] [--no-filler] [--t2s] [--language Chinese]
  python3 apps/asr/asr_cli.py timeline <输入> -t <timeline.json>
      [--offset-frames 0] [--min-dur-frames 18] [--gap-frames 2] [--limit 18]
      [--rate-cap 9] [--no-filler] [--t2s] [--dry-run] [--append]
  python3 apps/asr/asr_cli.py batch <目录|清单.txt> -o <输出目录>   # 逐文件 srt + 汇总 json 报告

机制（方案 §3.2，链路：client 抽 16k wav → sidecar 转写+words[] → textnorm 规范化 →
segline 锚定断句 → srt_out / tl_merge）：
  - <输入> 音视频皆可；临时 16k wav → 项目 cache/tmp/<runid>/（退出即删；输入不在
    项目内则退 /tmp 小件）；SRT/审计/备份 → 项目 cache/reports/；
  - timeline：读 project.fps/duration_frames，subtitles[] 就地合并（写前备份进
    cache/reports/subtitles_backup_<UTC>.json），--dry-run 只打印不写；
  - sidecar 返回 words=[]（全失配）→ 客户端回退「整句均分」并显著告警（§3.4 步骤 3）；
  - --t2s 繁简统一仅作用于聚合完成后的显示文本（N6，绝不动锚定字流）。

来源：docs/plans/2026-09-11-三大模块整合方案.md v2.1 §3.2/§3.5/§3.7。
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

# 平铺 import 同目录模块（vtx 同款手法）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import client          # noqa: E402
import segline         # noqa: E402
import srt_out         # noqa: E402
import textnorm        # noqa: E402
import tl_merge        # noqa: E402


# ---------------------------------------------------------------- 公共

def _runid():
    return time.strftime("%Y%m%d-%H%M%S") + "-%d" % os.getpid()


def _project_root(path):
    """从输入路径向上找项目根（含 cache/ 或 timeline.json 或 assets/ 的目录，最多 5 级）。"""
    p = Path(path).resolve().parent
    for _ in range(5):
        if (p / "cache").exists() or (p / "timeline.json").exists() or (p / "assets").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return None


def _tmp_dir_for(input_path, runid):
    """临时 16k wav 目录：项目 cache/tmp/<runid>/；无项目上下文退 /tmp（小件）。"""
    root = _project_root(input_path)
    if root:
        return root / "cache" / "tmp" / runid
    return Path("/tmp/asr_tmp_%s" % runid)


def _words_or_fallback(resp, verbose=True):
    """§3.4 步骤 3 客户端回退：words=[] 且有文本 → 整句均分（按 duration_s），显著告警。"""
    words = resp.get("words") or []
    text = resp.get("text") or ""
    if words:
        if resp.get("degraded_ranges"):
            if verbose:
                print("⚠ 对齐部分失配（区段 %s）：失配字时间已线性插值兜底" % resp["degraded_ranges"])
        return words
    if text.strip():
        dur = float(resp.get("duration_s") or 0.0)
        if verbose:
            print("✗⚠ 字级时间戳全失配（words=[]）——回退整句均分（时长 %.2fs）；"
                  "断句行时间将失去字级锚定，建议检查音频质量" % dur)
        n = len(text)
        per = dur / n if n and dur > 0 else 0.0
        return [{"w": ch, "s": round(i * per, 3), "e": round((i + 1) * per, 3)}
                for i, ch in enumerate(text)]
    return []


def _transcribe_words(input_path, args, cfg):
    """公共链：ensure_sidecar → 抽 16k wav → /transcribe(timestamps) → words[]（含回退）。"""
    base_url = getattr(args, "base_url", None) or client.cfg_get(cfg, "asr.base_url", client.DEFAULT_BASE_URL)
    if not client.ensure_sidecar(cfg, base_url=base_url):
        sys.exit(2)
    runid = _runid()
    tmp_dir = _tmp_dir_for(input_path, runid)
    try:
        wav = client.extract_wav16k(input_path, tmp_dir, runid)
        lang = getattr(args, "language", None) or client.cfg_get(cfg, "asr.language", "Chinese")
        resp = client.transcribe(base_url, wav, timestamps=True, language=lang,
                                 timeout=getattr(args, "timeout", None) or client.TRANSCRIBE_TIMEOUT_DEFAULT)
        return resp, _words_or_fallback(resp)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)     # hygiene：退出即删


def _apply_t2s(subs, enabled):
    """N6：--t2s 仅对聚合完成后的显示文本做繁→简（不动帧，不动锚定字流）。"""
    if not enabled:
        return
    ok_all = True
    for s in subs:
        out, ok = textnorm.t2s_convert(s["text"])
        s["text"] = out
        ok_all = ok_all and ok
    if not ok_all:
        print("⚠ 繁简转换部分/全部未生效（libopencc 软依赖，可 sudo dnf install opencc）")


def _audit_report_path(out_like, runid):
    """审计摘要落位：输出文件同目录；timeline 场景由调用方传项目 cache/reports。"""
    return Path(out_like).parent / ("%s.asr_audit_%s.json" % (Path(out_like).stem, runid))


# ---------------------------------------------------------------- 子命令

def cmd_sidecar(args, cfg):
    base_url = args.base_url or client.cfg_get(cfg, "asr.base_url", client.DEFAULT_BASE_URL)
    if args.action == "health":
        info = client.health(base_url)
        print("✓ /health：%s" % json.dumps(info, ensure_ascii=False) if info
              else "✗ sidecar 未运行：%s" % base_url)
        return 0 if info else 1
    if args.action == "status":
        info = client.health(base_url)
        port = client._port_of(base_url)
        pid = client.pid_file(port)
        pid_val = pid.read_text().strip() if pid.exists() else None
        if info:
            print("✓ 运行中：%s（本 CLI 拉起 pid=%s）" % (base_url, pid_val or "外部进程"))
        else:
            print("✗ 未运行：%s" % base_url)
        return 0 if info else 1
    if args.action == "start":
        return 0 if client.ensure_sidecar(cfg, base_url=base_url) else 2
    if args.action == "stop":
        return 0 if client.stop_sidecar(base_url=base_url, cfg=cfg) else 1
    print("✗ 未知动作：%s" % args.action)
    return 2


def cmd_transcribe(args, cfg):
    base_url = args.base_url or client.cfg_get(cfg, "asr.base_url", client.DEFAULT_BASE_URL)
    if not client.ensure_sidecar(cfg, base_url=base_url):
        return 2
    runid = _runid()
    tmp_dir = _tmp_dir_for(args.input, runid)
    try:
        wav = client.extract_wav16k(args.input, tmp_dir, runid)
        lang = args.language or client.cfg_get(cfg, "asr.language", "Chinese")
        resp = client.transcribe(base_url, wav, timestamps=args.timestamps,
                                 language=lang, timeout=args.timeout)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if args.json:
        print(json.dumps(resp, ensure_ascii=False, indent=1))
    else:
        print(resp.get("text", ""))
        if args.timestamps and resp.get("degraded_ranges"):
            print("⚠ 对齐部分失配区段（字符索引）：%s" % resp["degraded_ranges"], file=sys.stderr)
    return 0


def cmd_srt(args, cfg):
    resp, words = _transcribe_words(args.input, args, cfg)
    if not words:
        print("✗ 无有效转写内容（空音频或识别为空）")
        return 1
    subs, summary = segline.build_subtitles(
        words, limit=args.limit, rate_cap=args.rate_cap,
        min_dur=args.min_dur_frames, gap=args.gap_frames,
        remove_fillers=not args.no_filler)
    _apply_t2s(subs, args.t2s)
    content = srt_out.write_file(subs, args.output)
    audit = {"source": str(args.input), "summary": summary,
             "subtitles": subs, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    audit_path = _audit_report_path(args.output, _runid())
    try:
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8")
    except OSError as exc:
        print("⚠ 审计摘要写盘失败（%s）——SRT 不受影响" % exc)
    print("✓ SRT 已写出：%s（%d 行，%d 字，最快速率 %.1f 字/s，最宽行 %d 字）"
          % (args.output, summary["n_lines"], summary["n_chars"],
             summary["max_rate"], summary["max_width"]))
    for w in summary["warnings"]:
        print("⚠ %s" % w)
    print("… 审计摘要：%s" % audit_path)
    return 0


def cmd_timeline(args, cfg):
    tl_path = Path(args.timeline).expanduser()
    if not tl_path.exists():
        print("✗ timeline.json 不存在：%s" % tl_path)
        return 1
    try:
        tl = json.loads(tl_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print("✗ timeline.json 不是合法 JSON（%s）" % exc)
        return 1
    project = tl.get("project", {}) if isinstance(tl, dict) else {}
    fps = int(project.get("fps", 30) or 30)
    duration = project.get("duration_frames")
    duration = int(duration) if duration is not None else None
    resp, words = _transcribe_words(args.input, args, cfg)
    if not words:
        print("✗ 无有效转写内容（空音频或识别为空）")
        return 1
    subs, summary = segline.build_subtitles(
        words, fps=fps, limit=args.limit, rate_cap=args.rate_cap,
        min_dur=args.min_dur_frames, gap=args.gap_frames,
        duration_frames=duration, offset_frames=args.offset_frames,
        remove_fillers=not args.no_filler)
    _apply_t2s(subs, args.t2s)
    if args.append and isinstance(tl.get("subtitles"), list):
        subs = sorted(tl["subtitles"] + subs, key=lambda s: s["in"])
        summary["n_lines"] = len(subs)
    print("摘要：fps=%d duration=%s → 新字幕 %d 行 / %d 字 / 最快速率 %.1f 字/s / 最宽行 %d 字"
          % (fps, duration, summary["n_lines"], summary["n_chars"],
             summary["max_rate"], summary["max_width"]))
    for w in summary["warnings"]:
        print("⚠ %s" % w)
    if args.dry_run:
        print("… dry-run：预览前 8 行（未写盘）")
        for s in subs[:8]:
            print("  [%6d,%6d) %s" % (s["in"], s["out"], s["text"]))
        return 0
    try:
        result = tl_merge.merge_subtitles(str(tl_path), subs)
    except (RuntimeError, ValueError) as exc:
        print("✗ %s" % exc)
        return 1
    print("✓ subtitles[] 已合并：%s（旧 %d 条 → 新 %d 条；备份 %s）"
          % (tl_path, result["old_n"], result["new_n"], result["backup"]))
    reports = tl_path.parent / "cache" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / ("asr_timeline_%s.json" % _runid())).write_text(
        json.dumps({"source": str(args.input), "summary": summary, "subtitles": subs},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


def _iter_batch_inputs(target):
    p = Path(target)
    if p.is_dir():
        return sorted(q for q in p.iterdir() if q.suffix.lower() in client.AUDIO_EXTS)
    if p.is_file():
        return [Path(line.strip()) for line in p.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")]
    raise FileNotFoundError("批处理目标不存在：%s" % target)


def cmd_batch(args, cfg):
    inputs = _iter_batch_inputs(args.input)
    if not inputs:
        print("✗ 无输入文件（支持扩展名：%s）" % ", ".join(sorted(client.AUDIO_EXTS)))
        return 1
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    base_url = args.base_url or client.cfg_get(cfg, "asr.base_url", client.DEFAULT_BASE_URL)
    if not client.ensure_sidecar(cfg, base_url=base_url):
        return 2
    report = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "items": []}
    fail = 0
    for q in inputs:
        entry = {"source": str(q), "ok": False}
        print("… %s" % q)
        try:
            resp, words = _transcribe_words(q, args, cfg)
            subs, summary = segline.build_subtitles(
                words, limit=args.limit, rate_cap=args.rate_cap,
                min_dur=args.min_dur_frames, gap=args.gap_frames,
                remove_fillers=not args.no_filler)
            _apply_t2s(subs, args.t2s)
            out_srt = out_dir / (q.stem + ".srt")
            srt_out.write_file(subs, out_srt)
            entry.update(ok=True, srt=str(out_srt), lines=summary["n_lines"],
                         chars=summary["n_chars"], warnings=summary["warnings"])
            print("  ✓ %s（%d 行）" % (out_srt, summary["n_lines"]))
        except Exception as exc:
            entry["error"] = str(exc)
            fail += 1
            print("  ✗ 失败：%s" % exc)
        report["items"].append(entry)
    report_path = out_dir / ("batch_report_%s.json" % _runid())
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
    print("✓ 批处理完成：%d 成功 / %d 失败；报告 %s" % (len(inputs) - fail, fail, report_path))
    return 0 if fail == 0 else 1


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Qwen3-ASR 字幕产线（sidecar/transcribe/srt/timeline/batch）")
    ap.add_argument("--base-url", default=None, help="覆盖 config asr.base_url（默认 127.0.0.1:18085；放子命令前后均可）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sidecar", help="服务管理")
    p.add_argument("action", choices=["start", "stop", "status", "health"])
    p.add_argument("--base-url", default=argparse.SUPPRESS, help=argparse.SUPPRESS)  # SUPPRESS：未传时保留主级值（P1-1，subparser default 会覆盖主级）
    p.set_defaults(fn=cmd_sidecar)

    p = sub.add_parser("transcribe", help="转写（默认纯文本）")
    p.add_argument("input")
    p.add_argument("--timestamps", action="store_true", help="附字级时间戳 words[]")
    p.add_argument("--json", action="store_true", help="JSON 输出")
    p.add_argument("--language", default=None)
    p.add_argument("--timeout", type=float, default=client.TRANSCRIBE_TIMEOUT_DEFAULT)
    p.add_argument("--base-url", default=argparse.SUPPRESS, help=argparse.SUPPRESS)  # SUPPRESS：未传时保留主级值（P1-1，subparser default 会覆盖主级）
    p.set_defaults(fn=cmd_transcribe)

    def add_seg_opts(p):
        p.add_argument("--limit", type=int, default=18, help="行宽上限（字，含标点）")
        p.add_argument("--rate-cap", type=float, default=9.0, help="阅读速度上限（字/s，audit 同口径）")
        p.add_argument("--min-dur-frames", type=int, default=18, help="最短时长（帧，默认 18=0.6s）")
        p.add_argument("--gap-frames", type=int, default=2, help="相邻行最小间隙（帧）")
        p.add_argument("--no-filler", action="store_true", help="关闭 N5 口水词清除")
        p.add_argument("--t2s", action="store_true", help="N6 繁→简（仅显示文本）")
        p.add_argument("--language", default=None)
        p.add_argument("--timeout", type=float, default=None)
        p.add_argument("--base-url", default=argparse.SUPPRESS, help=argparse.SUPPRESS)  # SUPPRESS：未传时保留主级值（P1-1，subparser default 会覆盖主级）

    p = sub.add_parser("srt", help="转写 → 独立 SRT")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    add_seg_opts(p)
    p.set_defaults(fn=cmd_srt)

    p = sub.add_parser("timeline", help="转写 → timeline.json subtitles[] 合并")
    p.add_argument("input")
    p.add_argument("-t", "--timeline", required=True)
    p.add_argument("--offset-frames", type=int, default=0, help="整体平移（帧）")
    p.add_argument("--dry-run", action="store_true", help="只打印预览，不写盘")
    p.add_argument("--append", action="store_true", help="追加到既有 subtitles 后（按 in 排序）")
    add_seg_opts(p)
    p.set_defaults(fn=cmd_timeline)

    p = sub.add_parser("batch", help="目录/清单 → 批量 SRT + 汇总报告")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    add_seg_opts(p)
    p.set_defaults(fn=cmd_batch)

    args = ap.parse_args()
    cfg = client.load_config()
    try:
        return args.fn(args, cfg)
    except client.ClientError as exc:
        print("✗ sidecar 返回错误：%s" % exc.message)
        return 1
    except client.TranscribeTimeout as exc:
        print("✗ %s（可 --timeout 加大）" % exc)
        return 1
    except KeyboardInterrupt:
        print("\n… 已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
