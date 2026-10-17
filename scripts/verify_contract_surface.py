#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_contract_surface.py — L7 三源键面对账门(R-22 M3,席 E)。

保:三源键面不脱节(KERNEL_KEYS/schema/edit.md fence/registry 路径域)/
不保:schema 本身正确与散文语义正确(由人工评审另行把关);
a green L7 means the three surfaces agree at these exact contents,
not that the contract is right。

对账三则:
  ① vtx/stages.py 的 KERNEL_KEYS(ast.literal_eval 提取,不 import 不执行)对照
     timeline.schema.json 容器键域。容器映射:
       root                    = properties
       project                 = properties.project.properties
       sources[]               = properties.sources.items.properties
       audio_tracks[]          = properties.audio_tracks.items.properties
       audio_tracks[].events[] = …audio_tracks.items.properties.events.items.properties
     KERNEL_KEYS[scope] 每键必须 ∈ 容器 properties 键集;未声明 scope(容器映射外)
     的键逐键视为幻影;幻影键收集全列后 exit 1。
  ② docs/skill/references/edit.md 的 ```jsonc 围栏(剥注释状态机:字符串内不动,// 行外删,
     /* */ 删;json.loads)递归收集全部对象键名,须 ⊆ schema 全文档递归收集的
     properties 键名并集。
  ③ plugins/registry.json 每插件 keys[].path 必须 ∈ KERNEL_KEYS scope 集合。

用法:
  python3 scripts/verify_contract_surface.py [--stages P] [--schema P] [--edit P] [--registry P]
  默认 = vtx/stages.py / vtx/timeline.schema.json / docs/skill/references/edit.md /
  plugins/registry.json;路径一律按仓根解析(cwd 锚仓根)。
  四覆盖参=测试缝,供 golden 免触真件。

判定:FAIL 行逐条显形;尾行
  `L7 summary: scopes=<n> fence keys=<m> registry paths=<p> FAIL=<k>`
FAIL=0 → exit 0,否则 exit 1。

仅标准库;不写任何文件。
"""

import argparse
import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# KERNEL_KEYS scope → schema 容器路径(逐段下钻;段缺 = container missing)
CONTAINER_PATHS = {
    "root": ("properties",),
    "project": ("properties", "project", "properties"),
    "sources[]": ("properties", "sources", "items", "properties"),
    "audio_tracks[]": ("properties", "audio_tracks", "items", "properties"),
    "audio_tracks[].events[]": ("properties", "audio_tracks", "items", "properties",
                                "events", "items", "properties"),
}


def _fail(msg):
    print(msg)
    sys.exit(1)


def _load_json(path, label):
    try:
        with open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        _fail("L7 FAIL: %s parse: %s" % (label, e))


def extract_kernel_keys(path):
    """ast 静态提取 stages 源的模块级 KERNEL_KEYS(dict;值为键集合),不 import。"""
    try:
        with open(path, "rb") as f:
            source = f.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        _fail("L7 FAIL: KERNEL_KEYS parse: %s" % e)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        _fail("L7 FAIL: KERNEL_KEYS parse: %s" % e)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "KERNEL_KEYS":
                try:
                    keys = ast.literal_eval(node.value)
                except (ValueError, SyntaxError, TypeError, MemoryError) as e:
                    _fail("L7 FAIL: KERNEL_KEYS parse: %s" % e)
                if not isinstance(keys, dict):
                    _fail("L7 FAIL: KERNEL_KEYS parse: not a dict")
                return keys
    _fail("L7 FAIL: KERNEL_KEYS parse: module-level KERNEL_KEYS assignment not found")


def strip_jsonc_comments(text):
    """状态机剥注释:字符串内原样(含 \\ 转义);// 行外注释删至行尾; /* */ 删。"""
    out = []
    i = 0
    n = len(text)
    state = "code"   # code | string | line_comment | block_comment
    quote = ""
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch in "\"'":
                quote = ch
                state = "string"
                out.append(ch)
            elif ch == "/" and nxt == "/":
                state = "line_comment"
                i += 1
            elif ch == "/" and nxt == "*":
                state = "block_comment"
                i += 1
            else:
                out.append(ch)
        elif state == "string":
            out.append(ch)
            if ch == "\\" and nxt:
                out.append(nxt)
                i += 1
            elif ch == quote:
                state = "code"
        elif state == "line_comment":
            if ch == "\n":
                out.append("\n")
                state = "code"
        else:   # block_comment
            if ch == "*" and nxt == "/":
                state = "code"
                i += 1
        i += 1
    return "".join(out)


def extract_jsonc_fences(text, label):
    """提取全部 ```jsonc 围栏体(定位含 ```jsonc 的行到下一个 ``` 行)。"""
    lines = text.splitlines()
    fences = []
    i = 0
    while i < len(lines):
        if "```jsonc" in lines[i]:
            body = []
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("```"):
                body.append(lines[i])
                i += 1
            if i >= len(lines):
                _fail("L7 FAIL: %s fence unterminated" % label)
            fences.append("\n".join(body))
        i += 1
    if not fences:
        _fail("L7 FAIL: %s fence missing" % label)
    return fences


def collect_object_keys(node, acc):
    """递归收集全部对象键名(数组成分下钻,列表索引不计)。"""
    if isinstance(node, dict):
        for k, v in node.items():
            acc.add(k)
            collect_object_keys(v, acc)
    elif isinstance(node, list):
        for item in node:
            collect_object_keys(item, acc)


def collect_properties_keys(node, acc):
    """schema 全文档递归收集 properties 键名并集。"""
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for k, v in props.items():
                acc.add(k)
                collect_properties_keys(v, acc)
        for k, v in node.items():
            if k != "properties":
                collect_properties_keys(v, acc)
    elif isinstance(node, list):
        for item in node:
            collect_properties_keys(item, acc)


def main():
    parser = argparse.ArgumentParser(
        description="L7 三源键面对账门(KERNEL_KEYS/schema/edit.md fence/registry)")
    parser.add_argument("--stages", default="vtx/stages.py",
                        help="KERNEL_KEYS 来源(vtx/stages.py)")
    parser.add_argument("--schema", default="vtx/timeline.schema.json",
                        help="结构权威(vtx/timeline.schema.json)")
    parser.add_argument("--edit", default="docs/skill/references/edit.md",
                        help="散文权威(docs/skill/references/edit.md)")
    parser.add_argument("--registry", default="plugins/registry.json",
                        help="插件键注册表(plugins/registry.json)")
    args = parser.parse_args()

    def _rp(p):
        return p if os.path.isabs(p) else os.path.join(ROOT, p)

    stages_path = _rp(args.stages)
    schema_path = _rp(args.schema)
    edit_path = _rp(args.edit)
    registry_path = _rp(args.registry)

    fails = []

    # ---- 三源装载(KERNEL_KEYS/schema 不解析即败;其余见各自判点)----
    kernel_keys = extract_kernel_keys(stages_path)
    schema = _load_json(schema_path, "schema")

    # ---- ① KERNEL_KEYS × schema 容器键域(幻影键收集全列)----
    for scope in sorted(kernel_keys):
        keys = kernel_keys[scope]
        if not isinstance(keys, (set, frozenset, list, tuple)):
            _fail("L7 FAIL: KERNEL_KEYS parse: scope %s value not a key collection" % scope)
        if scope not in CONTAINER_PATHS:
            # 未声明 scope:容器映射无此 scope,其键逐键视为幻影(golden 缝走此支)
            for k in sorted(keys):
                fails.append("L7 FAIL: scope %s key %s not in schema" % (scope, k))
            continue
        container = schema
        missing = False
        for seg in CONTAINER_PATHS[scope]:
            if isinstance(container, dict) and seg in container:
                container = container[seg]
            else:
                fails.append("L7 FAIL: schema container missing: %s"
                             % "/".join(CONTAINER_PATHS[scope]))
                missing = True
                break
        if missing:
            continue
        for k in sorted(keys):
            if k not in container:
                fails.append("L7 FAIL: scope %s key %s not in schema" % (scope, k))

    # ---- ② edit.md ```jsonc 围栏 × schema properties 键名并集 ----
    try:
        with open(edit_path, "rb") as f:
            edit_text = f.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        _fail("L7 FAIL: edit.md fence missing(unreadable: %s)" % e)
    fence_keys = set()
    for fence_body in extract_jsonc_fences(edit_text, "edit.md"):
        try:
            parsed = json.loads(strip_jsonc_comments(fence_body))
        except ValueError as e:
            _fail("L7 FAIL: edit.md fence parse: %s" % e)
        collect_object_keys(parsed, fence_keys)
    schema_domain = set()
    collect_properties_keys(schema, schema_domain)
    for k in sorted(fence_keys):
        if k not in schema_domain:
            fails.append("L7 FAIL: edit.md fence key %s not in schema" % k)

    # ---- ③ registry keys[].path × KERNEL_KEYS scope 集合 ----
    registry = _load_json(registry_path, "registry")
    plugins = registry.get("plugins")
    if not isinstance(plugins, dict):
        _fail("L7 FAIL: registry plugins missing")
    scope_domain = set(kernel_keys)
    n_registry_paths = 0
    for pname in sorted(plugins):
        pdef = plugins[pname]
        key_entries = pdef.get("keys") if isinstance(pdef, dict) else None
        if not isinstance(key_entries, list):
            continue
        for entry in key_entries:
            if not isinstance(entry, dict) or "path" not in entry:
                continue
            n_registry_paths += 1
            path = entry["path"]
            if path not in scope_domain:
                fails.append("L7 FAIL: registry %s path %s not in scope domain"
                             % (pname, path))

    # ---- 尾行对账 ----
    for line in fails:
        print(line)
    print("L7 summary: scopes=%d fence keys=%d registry paths=%d FAIL=%d"
          % (len(kernel_keys), len(fence_keys), n_registry_paths, len(fails)))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
