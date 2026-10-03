#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish_pack.py · tech-talk 模板包 · 发布物料骨架生成（R5 PUBLISH-PACK 自动化）

用途：生成 <项目>/out/PUBLISH-PACK.md 骨架——参数快照表 / 简介模板 / 备答模板 /
      timeline 版本 / FREEZE 指针，从 timeline.json 与 out/FREEZE.json 自动填充能填的字段，
      其余留 TODO 占位由人工补写。
用法：python3 publish_pack.py <项目目录> [--version v1.0]
      python3 publish_pack.py --help
依赖：python3 标准库（json/os/datetime），无第三方依赖。
产物：仅写 <项目>/out/PUBLISH-PACK.md（git 管理区），绝不写 cache/（D52 灭失实案）。
幂等：每次执行整体重写 PUBLISH-PACK.md（骨架文件，人工补写前可安全重生成；
      已人工补写过则不要重跑，或先另存）。
"""

import argparse
import json
import os
import sys
from datetime import datetime

TEMPLATE = """# {title} · PUBLISH-PACK（发布物料包）

> 由 `skills/tech-talk/scripts/publish_pack.py` 于 {date} 生成；TODO 段由人工补写。
> 冻结点元数据 → `out/FREEZE.json`（{freeze_ref}）；变更单 → `../CHANGELOG.md`。

## 一、参数快照表（自动填充自 FREEZE.json）

| 项 | 值 |
|---|---|
| 冻结版本 | {version} |
| 冻结时间 | {frozen_at} |
{artifact_rows}
| timeline 版本 | {timeline_version} |
| QC 摘要 | {qc} |

## 二、简介模板（TODO 人工补写）

{brief_todo}

## 三、备答模板（TODO 人工补写）

{qa_todo}

## 四、发布核对单（TODO 逐项勾）

- [ ] 上传母带 md5 与 FREEZE.json 一致（上传前另存一份并记录 md5，严禁以 live out/ 为准）
- [ ] check_4k 断言全 PASS（4K 投稿版）
- [ ] 封面过 make_cover qc 门 + 四端裁切预览人审
- [ ] 简介与产品 README 口径一致（若视频关联开源项目）
- [ ] BV 号发布后回填：TODO

## 五、指针

- 冻结点：`out/FREEZE.json`（本包 §一 数据源）
- 变更单：`../CHANGELOG.md`
- timeline：`../timeline.json`（md5 `{timeline_md5}`）
"""

BRIEF_TODO = """```
【标题】TODO
【简介】TODO（100 字内：一句话钩子 + 解决什么问题 + 适合谁；与 01-brief 结论一致）
【标签】TODO
【置顶评论】TODO（补充材料/仓库链接/勘误）
```"""

QA_TODO = """```
Q1 预期高频提问：TODO —— 备答：TODO
Q2 质疑类（难度/对比已有方案）：TODO —— 备答：TODO
Q3 资源获取类（安装/源码）：TODO —— 备答：TODO
```"""


def guard_no_cache(path):
    parts = os.path.realpath(path).split(os.sep)
    if "cache" in parts:
        raise SystemExit(f"错误：输出路径落在 cache/ 下，拒绝写入：{path}（D52）")


def main():
    ap = argparse.ArgumentParser(description="生成 PUBLISH-PACK.md 骨架（自动填充 + TODO 占位）")
    ap.add_argument("project", help="项目目录（含 timeline.json 与 out/）")
    ap.add_argument("--version", default="", help="版本号（缺省取 FREEZE 最新节版本）")
    args = ap.parse_args()

    project = os.path.realpath(args.project)
    if not os.path.isdir(project):
        raise SystemExit(f"错误：项目目录不存在：{project}")
    out_path = os.path.join(project, "out", "PUBLISH-PACK.md")
    guard_no_cache(out_path)

    date = datetime.now().astimezone().isoformat(timespec="seconds")

    # timeline.json：md5 + _meta 版本线索
    tl_path = os.path.join(project, "timeline.json")
    timeline_md5, timeline_version = "（timeline.json 不存在）", "TODO"
    if os.path.isfile(tl_path):
        import hashlib
        timeline_md5 = hashlib.md5(open(tl_path, "rb").read()).hexdigest()
        try:
            meta = json.load(open(tl_path, encoding="utf-8")).get("_meta", {})
            for key in ("version", "live_version", "frozen_version"):
                if key in meta:
                    timeline_version = str(meta[key])
                    break
            else:
                timeline_version = "（_meta 无版本键，以 FREEZE/CHANGELOG 口径为准）"
        except Exception as e:
            timeline_version = f"（_meta 解析失败：{e}）"

    # FREEZE.json：取最新节
    freeze_path = os.path.join(project, "out", "FREEZE.json")
    version, frozen_at, artifact_rows, qc = args.version or "TODO", "TODO", "", "TODO"
    freeze_ref = "尚未生成：先跑 freeze.py"
    if os.path.isfile(freeze_path):
        try:
            freeze = json.load(open(freeze_path, encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"错误：{freeze_path} 不是合法 JSON（{e}）；请人工修复后重跑")
        history = freeze.get("history", [])
        node = history[-1] if history else {}
        if not args.version and node.get("version"):
            version = node["version"]
        if node.get("date"):
            frozen_at = node["date"]
        if node.get("qc"):
            qc = str(node["qc"])
        freeze_ref = f"最新节 §{node.get('version', '?')}（共 {len(history)} 节）"
        for name, a in (node.get("artifacts") or {}).items():
            if isinstance(a, dict):
                ff = a.get("ffprobe")
                dur = ff.get("duration_s") if isinstance(ff, dict) else None
                artifact_rows += (
                    f"| {name} | md5 `{a['md5']}` / {a['size_bytes'] / 1048576:.1f} MiB"
                    + (f" / {dur}s" if dur else "") + " |\n")
            else:
                artifact_rows += f"| {name} | {a} |\n"
    if artifact_rows:
        artifact_rows = artifact_rows.rstrip("\n")
    else:
        artifact_rows = "| 产物 | TODO（先跑 freeze.py） |"

    title = os.path.basename(project)
    text = TEMPLATE.format(title=title, date=date, freeze_ref=freeze_ref, version=version,
                           frozen_at=frozen_at, artifact_rows=artifact_rows,
                           timeline_version=timeline_version, qc=qc,
                           brief_todo=BRIEF_TODO, qa_todo=QA_TODO,
                           timeline_md5=timeline_md5)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "w", encoding="utf-8").write(text)
    print(f"✓ 生成 {out_path}（版本 {version}；TODO 段人工补写）")


if __name__ == "__main__":
    sys.exit(main())
