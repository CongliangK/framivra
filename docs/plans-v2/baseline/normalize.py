#!/usr/bin/env python3
"""vtx P0 基线归一化器（v2 重构复验口径的唯一权威实现）。

用法：
  env python3 normalize.py text <输入日志> <输出>   # 文本归一化（stdout/stderr 日志）
  env python3 normalize.py json <输入JSON> <输出>   # JSON 归一化（qc/audio/subs 报告等）

口径（与 NORMALIZATION.md 一致，改口径须两处同步）：
  剔除：
    - 时间戳/日期/耗时类字段与行：[timing] 行、gfx ms/帧统计行、预估耗时行、
      estimated_seconds / estimate_basis / elapsed / duration_s / updated / when 等；
    - 绝对路径 → 占位符：<SHADOW>（影子树）/ <PROJECT>（live 树）；
    - 文件 stat 指纹类字段：mtime_ns / mtime / size / digest 等（receipt 的 stat 段）；
    - tmp run 目录名（UTC 时间戳前缀）→ <TMPRUN>。
  保留主体：
    - checks[] 的 name/level(pass)/detail 数值、汇总计数、PASS/FAIL 判定序列；
    - 原始日志首行 `# exit=N` 退出码。
  实现：JSON 走 json.load + 递归遍历剔除（不手写正则硬抠）；文本走逐行过滤 +
  子串替换。
"""
import argparse
import json
import re
import sys
from pathlib import Path

# ---- 路径占位符（顺序敏感：影子前缀更长，先替换，防被 live 前缀吞并）----
PATH_SUBS = [
    ("/home/liangruochong/文档/04_bilibili视频/_shadow_v2/voice-ime-shadow", "<SHADOW>"),
    ("/home/liangruochong/文档/04_bilibili视频/voice-ime", "<PROJECT>"),
]

# tmp run 目录名：cache/tmp/<UTC时间戳>-<label>/ → <TMPRUN>
TMPRUN_RE = re.compile(r"\d{8}T\d{6}Z-[A-Za-z0-9_.-]+")

# ---- 文本行剔除（整行丢弃）：时间戳/日期/耗时类 ----
DROP_LINE_RES = [
    re.compile(r"\[timing\]"),                 # audio --report 末行耗时
    re.compile(r"ms/帧"),                      # gfx 帧统计行
    re.compile(r"ms@p50"),                     # gfx 分项耗时
    re.compile(r"预估耗时"),                   # diff 耗时预估行
    re.compile(r"estimated_seconds"),
    re.compile(r"estimate_basis"),
    re.compile(r"\belapsed\b"),
    re.compile(r"\bduration_s\b"),
    re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"),  # 行首日期时间戳
]

# ---- JSON 键剔除（递归）：时间戳/日期/耗时 + stat 指纹类 ----
DROP_KEYS = {
    "updated", "when", "elapsed", "duration_s", "estimated_seconds",
    "estimate_basis", "mtime_ns", "mtime", "ctime", "atime", "digest",
    "size", "sha1", "sha256", "md5", "stat",
}

# JSON 数值/字符串中出现的路径同样替换占位符（source 字段等）


def _sub_paths(s: str) -> str:
    for old, new in PATH_SUBS:
        s = s.replace(old, new)
    return TMPRUN_RE.sub("<TMPRUN>", s)


def normalize_text(in_path: Path, out_path: Path) -> None:
    out_lines = []
    for line in in_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# exit="):        # 退出码头行：保留（对比信号）
            out_lines.append(line)
            continue
        if any(rx.search(line) for rx in DROP_LINE_RES):
            continue
        out_lines.append(_sub_paths(line))
    out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def _normalize_obj(obj):
    if isinstance(obj, dict):
        return {k: _normalize_obj(v) for k, v in obj.items() if k not in DROP_KEYS}
    if isinstance(obj, list):
        return [_normalize_obj(x) for x in obj]
    if isinstance(obj, str):
        return _sub_paths(obj)
    return obj


def normalize_json(in_path: Path, out_path: Path) -> None:
    data = json.loads(in_path.read_text(encoding="utf-8"))
    out_path.write_text(
        json.dumps(_normalize_obj(data), ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="vtx P0 基线归一化器")
    ap.add_argument("mode", choices=["text", "json"])
    ap.add_argument("infile")
    ap.add_argument("outfile")
    a = ap.parse_args()
    src, dst = Path(a.infile), Path(a.outfile)
    if not src.is_file():
        print(f"✗ 输入不存在: {src}", file=sys.stderr)
        sys.exit(2)
    dst.parent.mkdir(parents=True, exist_ok=True)
    (normalize_text if a.mode == "text" else normalize_json)(src, dst)
    print(f"✓ 归一化完成: {src} → {dst}")


if __name__ == "__main__":
    main()
