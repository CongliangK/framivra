#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R-22 M2 · manifest catalog 生成器(单一发射器)。

用法:
  python3 scripts/gen_manifest_catalog.py
      → stdout 输出完整生成区文本(含 BEGIN/END 两 marker 行与中间内容)。
  python3 scripts/gen_manifest_catalog.py --inject docs/catalog.md
      → 读 catalog,BEGIN/END 各恰 1(0 或 ≥2 → stderr 原因 exit 1),
        将两 marker 间内容(含 marker 行)整体替换为新区,写回,exit 0。

数据源: apps/*/app.json(12 件) + plugins/*/plugin.json(4 件) + plugins/registry.json。
确定性: 同输入必同字节(apps/plugins 按目录名字典序;JSON 保序解析;无时间戳/无随机)。
指针行: 每组件行前 `- source: <manifest rel>:<version 键所在行号,实际解析确定>`;
        registry 汇总行后 `- source: plugins/registry.json:2`(2=_comment 行,固定)。
本文件仅标准库。
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BEGIN_MARKER = (
    "<!-- BEGIN GENERATED manifest-catalog "
    "(gen: scripts/gen_manifest_catalog.py; 区内禁手改) -->"
)
END_MARKER = "<!-- END GENERATED manifest-catalog -->"
BEGIN_SUB = "BEGIN GENERATED"
END_SUB = "END GENERATED"

TABLE_HEADER = "| 组件名 | 类型 | 版本 | 键 | hooks | installed | tests 态 | README 态 |"
TABLE_SEP = "| --- | --- | --- | --- | --- | --- | --- | --- |"
NA = "—"
REGISTRY_REL = "plugins/registry.json"
REGISTRY_POINTER = "- source: plugins/registry.json:2"  # 2 = _comment 行(固定)


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def load_json(rel):
    with (ROOT / rel).open("r", encoding="utf-8") as f:
        return json.load(f)  # dict 保序(py3.7+),排序确定性不依赖此处


def top_level_key_line(abs_path, key):
    """返回顶层 key 所在 1-based 行号(实际解析确定);找不到返回 None。

    逐行扫描,跟踪 {}/[] 深度与字符串内外状态,仅在 depth==1 时匹配
    `"key":`(带冒号,防 `"version_x"` 前缀误配)。
    """
    pat = re.compile(r'^"%s"\s*:' % re.escape(key))
    depth = 0
    in_str = False
    escaped = False
    with open(abs_path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if depth == 1 and not in_str and pat.match(line.lstrip()):
                return lineno
            for ch in line:
                if in_str:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch in "{[":
                        depth += 1
                    elif ch in "}]":
                        depth -= 1
    return None


def cell(text):
    return str(text).replace("|", "\\|")


def keys_cell(items):
    items = [str(i) for i in items if str(i)]
    return ", ".join(items) if items else NA


def state_flag(path, want_dir, name):
    p = path / name
    ok = p.is_dir() if want_dir else p.is_file()
    return "有" if ok else "无"


def component_row(kind, comp_root, manifest, registry_plugins):
    name = manifest.get("name") or comp_root.name
    mtype = manifest.get("kind") or kind
    version = manifest.get("version", NA)
    if kind == "app":
        keys = keys_cell(manifest.get("config_keys") or [])
        hooks = NA
        installed = NA
    else:
        ext = manifest.get("extends") or {}
        keys = keys_cell(
            "%s:%s" % (k.get("path"), k.get("key")) for k in (ext.get("schema_keys") or [])
        )
        hooks = keys_cell(ext.get("hooks") or [])
        reg = registry_plugins.get(name)
        if reg is None:
            installed = "未注册"
        else:
            installed = "true" if reg.get("installed") is True else "false"
    tests = state_flag(comp_root, True, "tests")
    readme = state_flag(comp_root, False, "README.md")
    return "| %s | %s | %s | %s | %s | %s | %s | %s |" % (
        cell(name), cell(mtype), cell(version), cell(keys),
        cell(hooks), cell(installed), tests, readme,
    )


def build_region_text():
    app_dirs = sorted(p.parent.name for p in (ROOT / "apps").glob("*/app.json"))
    plugin_dirs = sorted(p.parent.name for p in (ROOT / "plugins").glob("*/plugin.json"))
    registry = load_json(REGISTRY_REL)
    registry_plugins = registry.get("plugins", {})

    lines = [BEGIN_MARKER, TABLE_HEADER, TABLE_SEP]
    for d in app_dirs:
        rel = "apps/%s/app.json" % d
        vline = top_level_key_line(ROOT / rel, "version")
        if vline is None:
            die("gen fail: %s 缺顶层 version 键,无法定指针行号" % rel)
        lines.append("- source: %s:%d" % (rel, vline))
        lines.append(component_row("app", ROOT / "apps" / d, load_json(rel), registry_plugins))
    for d in plugin_dirs:
        rel = "plugins/%s/plugin.json" % d
        vline = top_level_key_line(ROOT / rel, "version")
        if vline is None:
            die("gen fail: %s 缺顶层 version 键,无法定指针行号" % rel)
        lines.append("- source: %s:%d" % (rel, vline))
        lines.append(component_row("plugin", ROOT / "plugins" / d, load_json(rel), registry_plugins))

    hook_tally = {}
    for p in registry_plugins.values():
        for h in p.get("hooks") or []:
            hook_tally[h] = hook_tally.get(h, 0) + 1
    hooks_summary = ", ".join("%s×%d" % (h, hook_tally[h]) for h in sorted(hook_tally))
    keys_total = sum(len(p.get("keys") or []) for p in registry_plugins.values())
    installed_n = sum(1 for p in registry_plugins.values() if p.get("installed") is True)
    lines.append(
        "| %s | registry | %s | %d 键声明 | %s | %d/%d | %s | %s |"
        % (cell("registry"), NA, keys_total, cell(hooks_summary or NA),
           installed_n, len(registry_plugins), NA, NA)
    )
    lines.append(REGISTRY_POINTER)
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def cmd_inject(catalog_arg):
    path = Path(catalog_arg)
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        die("inject fail: 文件不存在: %s" % path)
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    b_idx = [i for i, l in enumerate(lines) if BEGIN_SUB in l]
    e_idx = [i for i, l in enumerate(lines) if END_SUB in l]
    if len(b_idx) != 1 or len(e_idx) != 1:
        die(
            "inject fail: %s marker 计数 BEGIN=%d END=%d expected 1/1"
            " (0=缺注入锚,≥2=重复注入)" % (path, len(b_idx), len(e_idx))
        )
    if e_idx[0] <= b_idx[0]:
        die("inject fail: %s END marker 在 BEGIN 之前" % path)
    region_lines = build_region_text().splitlines()
    new_lines = lines[: b_idx[0]] + region_lines + lines[e_idx[0] + 1:]
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print("injected: %s region-lines=%d" % (path, len(region_lines)))
    return 0


def main():
    ap = argparse.ArgumentParser(description="manifest catalog 生成区单一发射器")
    ap.add_argument("--inject", metavar="CATALOG_MD",
                    help="注入目标(仓根相对或绝对路径);缺省则 stdout 输出新区")
    args = ap.parse_args()
    if args.inject:
        return cmd_inject(args.inject)
    sys.stdout.write(build_region_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
