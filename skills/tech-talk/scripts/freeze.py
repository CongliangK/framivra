#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""freeze.py · tech-talk 模板包 · 发布冻结脚本（R5 冻结三件套之 FREEZE/CHANGELOG 自动化）

用途：对 <项目>/out/ 下的 final.mp4（及 final_4k.mp4 若存在）计算 md5/size/ffprobe 元数据，
      收集 timeline.json 的 _meta 摘要与当前 git 状态，生成/更新 <项目>/out/FREEZE.json
      （多版本节，历史保留），并在 <项目>/CHANGELOG.md 追加版本节（不存在则从模板头创建）。
      结构范式参照 voice-ime 第 1 期 out/FREEZE.json（version/date/md5/qc 占位）。
用法：python3 freeze.py <项目目录> [--version v1.0] [--note "说明"]
      python3 freeze.py --help
依赖：python3 标准库；ffprobe 可选（缺失时优雅降级，元数据字段标注跳过）；
      git 可选（项目不在 git 仓时对应字段标注「非 git 仓」）。
产物：仅写 <项目>/out/FREEZE.json 与 <项目>/CHANGELOG.md（git 管理区），
      绝不写 cache/（voice-ime 发布物料灭失实案，决策 D52/D54）。
幂等：重复执行追加新版本节（FREEZE.history 增长、CHANGELOG 追加新节），历史节永不改写。
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

CHANGELOG_HEADER = """# {name} CHANGELOG（变更单）

> **纪律声明**：本文件是本项目的变更单。任何 timeline.json 或 out/ 产物的改动
> **必须**落一条版本节（版本号/改动内容/验证门结果/产物 md5），先记录后改动。
> 本文件必须留在项目目录（git 管理区），**严禁写入 cache/**
> （voice-ime 第 1 期实案：变更单放不入 git 的 cache 目录，cache 重组后原件灭失，决策 D52/D54）。
> 冻结点元数据见 `out/FREEZE.json`（可用 `skills/tech-talk/scripts/freeze.py` 生成/更新）。
"""


def now_iso():
    """本地时区 ISO 格式时间戳。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def md5_of(path, chunk=1024 * 1024):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def guard_no_cache(path):
    """输出路径严禁落在 cache/ 下（D52 灭失实案）。"""
    parts = os.path.realpath(path).split(os.sep)
    if "cache" in parts:
        raise SystemExit(f"错误：输出路径落在 cache/ 下，拒绝写入：{path}（D52）")


def probe_file(path):
    """单产物档案：md5/size/mtime + ffprobe 元数据（ffprobe 缺失则降级标注）。"""
    info = {
        "path": os.path.relpath(path, os.path.dirname(os.path.dirname(path))),
        "md5": md5_of(path),
        "size_bytes": os.path.getsize(path),
    }
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        info["ffprobe"] = "（ffprobe 不可用，已跳过元数据字段）"
        return info
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=60, check=True).stdout
        d = json.loads(out)
        fmt = d.get("format", {})
        vstream = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
        astream = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
        info["ffprobe"] = {
            "duration_s": fmt.get("duration"),
            "bit_rate": fmt.get("bit_rate"),
            "video": {k: vstream.get(k) for k in
                      ("codec_name", "width", "height", "r_frame_rate", "pix_fmt")},
            "audio": {k: astream.get(k) for k in
                      ("codec_name", "sample_rate", "channels", "bit_rate")},
        }
    except Exception as e:  # ffprobe 存在但执行失败也降级，不阻断冻结
        info["ffprobe"] = f"（ffprobe 执行失败已跳过：{e}）"
    return info


def timeline_summary(project):
    """timeline.json 的 _meta 摘要（只取标量/短值字段，重文档字段截断）。"""
    tl_path = os.path.join(project, "timeline.json")
    if not os.path.isfile(tl_path):
        return {"error": "timeline.json 不存在"}
    summary = {"md5": md5_of(tl_path)}
    try:
        meta = json.load(open(tl_path, encoding="utf-8")).get("_meta", {})
    except Exception as e:
        summary["_meta"] = f"（_meta 解析失败：{e}）"
        return summary
    picked = {}
    for k, v in meta.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            picked[k] = v if len(str(v)) <= 200 else str(v)[:200] + "…"
        elif isinstance(v, dict) and k in ("timebase",):
            picked[k] = v
    if picked:
        summary["_meta"] = picked
    return summary


def git_state(project):
    """项目 git 状态摘要（非 git 仓则标注）。"""
    def git(*args):
        return subprocess.run(["git", "-C", project, *args],
                              capture_output=True, text=True, timeout=30)
    import shutil as _sh
    if _sh.which("git") is None:
        return {"note": "git 不可用，已跳过（FREEZE/CHANGELOG 建议在 git 仓内使用）"}
    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        return {"note": "项目不在 git 仓内（建议入库：FREEZE/CHANGELOG 须有版本历史可追溯）"}
    state = {}
    head = git("rev-parse", "--short", "HEAD")
    state["head"] = head.stdout.strip() if head.returncode == 0 else "(无提交)"
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    state["branch"] = branch.stdout.strip() if branch.returncode == 0 else ""
    dirty = git("status", "--porcelain")
    lines = dirty.stdout.splitlines() if dirty.returncode == 0 else []
    state["dirty_count"] = len(lines)
    state["dirty_sample"] = lines[:20]
    return state


def load_freeze(path):
    """读已有 FREEZE.json；兼容 voice-ime 第 1 期旧扁平结构（无 history 键则包一层迁移）。"""
    if not os.path.isfile(path):
        return {"history": []}
    try:
        data = json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"错误：{path} 不是合法 JSON（{e}）；请人工修复后再冻结，脚本不会覆盖损坏文件")
    if "history" not in data:
        data = {"history": [dict(data, _legacy_note="旧版扁平结构，由 freeze.py 首跑自动迁移为本节")]}
    return data


def build_entry(project, version, note):
    out_dir = os.path.join(project, "out")
    artifacts = {}
    final = os.path.join(out_dir, "final.mp4")
    if os.path.isfile(final):
        artifacts["out/final.mp4"] = probe_file(final)
    else:
        artifacts["out/final.mp4"] = "（缺失：out/final.mp4 不存在，冻结前应先完成渲染）"
    final4k = os.path.join(out_dir, "final_4k.mp4")
    if os.path.isfile(final4k):
        artifacts["out/final_4k.mp4"] = probe_file(final4k)
    return {
        "version": version,
        "date": now_iso(),
        "note": note or "",
        "artifacts": artifacts,
        "timeline": timeline_summary(project),
        "git": git_state(project),
        # qc 摘要占位：冻结后人工核对补写（qc/gates/check_4k 结果与 verdict）
        "qc": "TODO（人工补写：vtx qc 分数 / content_gates 各门 / check_4k 断言结果与结论 verdict）",
        "generated_by": "skills/tech-talk/scripts/freeze.py",
    }


def append_changelog(project, entry):
    path = os.path.join(project, "CHANGELOG.md")
    guard_no_cache(path)
    if not os.path.isfile(path):
        open(path, "w", encoding="utf-8").write(
            CHANGELOG_HEADER.format(name=os.path.basename(os.path.realpath(project))))
        print(f"✓ 创建 {path}（含纪律声明模板头）")
    lines = [f"\n## {entry['version']}（{entry['date']}）\n"]
    if entry["note"]:
        lines.append(f"- **说明**：{entry['note']}\n")
    for name, a in entry["artifacts"].items():
        if isinstance(a, dict):
            size_mib = a["size_bytes"] / 1024 / 1024
            lines.append(f"- **{name}**：md5 `{a['md5']}` / {size_mib:.1f} MiB"
                         + (f" / {a['ffprobe']['duration_s']}s" if isinstance(a.get("ffprobe"), dict) else ""))
    tl = entry.get("timeline", {})
    if isinstance(tl, dict) and "md5" in tl:
        lines.append(f"- **timeline.json**：md5 `{tl['md5']}`")
    lines.append("- **QC**：（人工补写：qc/gates/check_4k 结果）\n")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"✓ 追加版本节 → {path} §{entry['version']}")


def main():
    ap = argparse.ArgumentParser(description="发布冻结：生成/更新 FREEZE.json + 追加 CHANGELOG.md")
    ap.add_argument("project", help="项目目录（含 timeline.json 与 out/）")
    ap.add_argument("--version", default="v1.0", help="版本号（默认 v1.0）")
    ap.add_argument("--note", default="", help="本版说明（改动内容/验证门摘要）")
    args = ap.parse_args()

    project = os.path.realpath(args.project)
    if not os.path.isdir(project):
        raise SystemExit(f"错误：项目目录不存在：{project}")

    freeze_path = os.path.join(project, "out", "FREEZE.json")
    guard_no_cache(freeze_path)
    os.makedirs(os.path.dirname(freeze_path), exist_ok=True)

    freeze = load_freeze(freeze_path)
    entry = build_entry(project, args.version, args.note)
    freeze["history"].append(entry)
    freeze["doc"] = ("发布冻结点登记（多版本节，历史保留）。生成工具=skills/tech-talk/scripts/freeze.py；"
                     "范式源=voice-ime 第 1 期 out/FREEZE.json（D52/D54 后脚本化）。"
                     "qc 字段为占位，冻结后人工核对补写。")
    with open(freeze_path, "w", encoding="utf-8") as f:
        json.dump(freeze, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"✓ {'追加' if len(freeze['history']) > 1 else '生成'}版本节 §{args.version} → {freeze_path}"
          f"（共 {len(freeze['history'])} 节）")

    append_changelog(project, entry)
    print("下一步：人工核对 FREEZE.json qc 占位段并补写验证门结果；PUBLISH-PACK 用 "
          "publish_pack.py 生成。")


if __name__ == "__main__":
    sys.exit(main())
