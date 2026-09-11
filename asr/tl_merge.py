#!/usr/bin/env python3
"""tl_merge —— timeline.json subtitles[] 合并（保护其他字段 + 写前备份）

用途：asr_cli timeline 子命令把 segline 产出的新 subtitles[] 就地合并进项目
timeline.json——只替换 subtitles[] 一个键，sources/video_tracks/audio_tracks/
gfx_tracks/project 及任何自定义扩展键原样保留；写前把旧 subtitles 备份到
<项目>/cache/reports/subtitles_backup_<UTC>.json；tempfile+rename 原子写回。

用法（平铺 import，vtx 同款手法）：
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import tl_merge
    tl_merge.merge_subtitles("/path/timeline.json", new_subs)

机制：
  - 读入 JSON 失败（损坏）→ 立即抛错、原文件一个字节不动；
  - dry_run=True → 只返回新旧摘要，零写盘（不写 timeline、不产生备份）；
  - 新条目校验（in/out 整数、text 字符串、0 <= in < out）不过 → 拒绝写，保护现状。

来源：docs/plans/2026-09-11-三大模块整合方案.md v2.1 §3.2/§3.7/§9.1。
"""
import json
import os
import tempfile
import time
from pathlib import Path


def _validate_subs(subs):
    """新 subtitles[] 结构校验（validate 硬门 0 <= in < out 的最小子集）。"""
    if not isinstance(subs, list):
        raise ValueError("subtitles 必须是数组")
    for i, s in enumerate(subs):
        if not isinstance(s, dict) or not {"in", "out", "text"} <= set(s):
            raise ValueError("第 %d 条缺少 in/out/text 键" % (i + 1))
        if not isinstance(s["in"], int) or not isinstance(s["out"], int):
            raise ValueError("第 %d 条 in/out 必须是整数帧" % (i + 1))
        if not isinstance(s["text"], str):
            raise ValueError("第 %d 条 text 必须是字符串" % (i + 1))
        if not (0 <= s["in"] < s["out"]):
            raise ValueError("第 %d 条违反 0 <= in < out（in=%d, out=%d）"
                             % (i + 1, s["in"], s["out"]))


def merge_subtitles(timeline_path, new_subs, dry_run=False, reports_dir=None):
    """合并新 subtitles[] 进 timeline.json，返回摘要 dict。

    摘要：{"old_n", "new_n", "backup": 备份路径或 None, "dry_run": bool}。
    reports_dir 缺省 = timeline 所在项目（timeline.parent）/cache/reports。
    """
    tl_path = Path(timeline_path)
    raw = tl_path.read_text(encoding="utf-8")
    try:
        tl = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("timeline.json 不是合法 JSON（%s），已保持原文件不动：%s"
                           % (exc, tl_path)) from exc
    if not isinstance(tl, dict):
        raise RuntimeError("timeline.json 顶层必须是对象，已保持原文件不动：%s" % tl_path)
    old_subs = tl.get("subtitles", [])
    _validate_subs(new_subs)

    backup = None
    if not dry_run:
        rdir = Path(reports_dir) if reports_dir else tl_path.parent / "cache" / "reports"
        rdir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-%d" % os.getpid()
        backup = rdir / ("subtitles_backup_%s.json" % stamp)
        backup.write_text(json.dumps(old_subs, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        tl["subtitles"] = new_subs
        # 原子写回：同目录 tempfile + os.replace（崩溃不留半截文件）
        fd, tmp_name = tempfile.mkstemp(dir=str(tl_path.parent),
                                        prefix=tl_path.name + ".tmp", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(tl, f, ensure_ascii=False, indent=1)
                f.write("\n")
            os.replace(tmp_name, tl_path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
    return {"old_n": len(old_subs), "new_n": len(new_subs),
            "backup": str(backup) if backup else None, "dry_run": bool(dry_run)}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="stdin subtitles json → 合并进 timeline.json")
    ap.add_argument("-t", "--timeline", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    new_subs = json.load(__import__("sys").stdin)
    result = merge_subtitles(args.timeline, new_subs, dry_run=args.dry_run)
    print("✓ 合并完成：%s" % json.dumps(result, ensure_ascii=False)
          if not args.dry_run else "✓ dry-run：未写盘。%s" % json.dumps(result, ensure_ascii=False))
