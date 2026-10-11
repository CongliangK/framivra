#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R-22 M2 · manifest catalog 校验器(L5 门)。

用法: python3 scripts/verify_manifest_catalog.py --check [--catalog docs/catalog.md]

保什么(全过 → exit 0):
  1) catalog 生成区(BEGIN/END 两 marker 之间,含 marker 行)与 gen 单源输出
     normalize 后逐字节一致 —— gen 是唯一发射器,两文件禁互相 import,
     本文件以 subprocess 调 gen 取新区(单一来源);
  2) 每条指针行(`- source: <file>:<n>`)的 file 部分在仓根相对路径下真实存在。
不保什么:
  1) 区外手写散文的语义正确性(=review 人审);
  2) manifest 本身的语义正确性(安装态/键面事实的源真值不在本件职责内);
  3) 指针行号与现文件实际行号的漂移只显形计数(drift-display,信息,不 red)。
本文件仅标准库。
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BEGIN_SUB = "BEGIN GENERATED"
END_SUB = "END GENERATED"
POINTER_RE = re.compile(r"^- source: (\S+):(\d+)$")


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def normalize(text):
    """唯一 normalize(仅本文件定义):去 BOM、行尾统一 LF、去每行行尾空白、文件尾单换行。"""
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def top_level_key_line(abs_path, key):
    """verify 侧自带实现(与 gen 禁互 import):顶层 key 所在 1-based 行号,找不到 None。"""
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


def pointer_expected_line(rel):
    """指针行号的现文件实际行号口径:manifest=version 键;registry=_comment 键;未知=None。"""
    if rel.endswith("/app.json") or rel.endswith("/plugin.json"):
        return top_level_key_line(ROOT / rel, "version")
    if rel == "plugins/registry.json":
        return top_level_key_line(ROOT / rel, "_comment")
    return None


def main():
    ap = argparse.ArgumentParser(description="manifest catalog L5 校验门")
    ap.add_argument("--check", action="store_true",
                    help="执行校验(唯一支持模式)")
    ap.add_argument("--catalog", default="docs/catalog.md",
                    help="catalog 路径(仓根相对或绝对),缺省 docs/catalog.md")
    args = ap.parse_args()
    if not args.check:
        print("usage: python3 scripts/verify_manifest_catalog.py --check [--catalog docs/catalog.md]",
              file=sys.stderr)
        sys.exit(2)

    # ① 读 catalog,断言 BEGIN/END 各恰 1(verify 侧双断言)
    cat_path = Path(args.catalog)
    if not cat_path.is_absolute():
        cat_path = ROOT / cat_path
    if not cat_path.is_file():
        fail("L5 FAIL: catalog 不存在: %s" % cat_path)
    cat_lines = normalize(cat_path.read_text(encoding="utf-8")).split("\n")[:-1]
    b_lines = [i for i, l in enumerate(cat_lines) if BEGIN_SUB in l]
    e_lines = [i for i, l in enumerate(cat_lines) if END_SUB in l]
    if len(b_lines) != 1 or len(e_lines) != 1:
        fail("L5 FAIL: marker count BEGIN=%d END=%d expected 1/1" % (len(b_lines), len(e_lines)))
    if e_lines[0] <= b_lines[0]:
        fail("L5 FAIL: marker order END before BEGIN")
    region = cat_lines[b_lines[0]: e_lines[0] + 1]

    # ② subprocess 调 gen(单一来源,唯一发射器)
    proc = subprocess.run(
        [sys.executable, "scripts/gen_manifest_catalog.py"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        fail("L5 FAIL: gen exited %d: %s" % (proc.returncode, proc.stderr.strip().splitlines()[-1]
                                             if proc.stderr.strip() else "(no stderr)"))
    gen_lines = normalize(proc.stdout).split("\n")[:-1]

    # ④ 区提取后逐行比对:指针行豁免但统计,其余逐字节,首差即列
    pointers = 0
    for i in range(max(len(region), len(gen_lines))):
        c = region[i] if i < len(region) else "<absent>"
        g = gen_lines[i] if i < len(gen_lines) else "<absent>"
        if c != "<absent>" and POINTER_RE.match(c):
            pointers += 1
            continue
        if c != g:
            fail("L5 FAIL: first difference at line %d: catalog=<%s> generated=<%s>" % (i + 1, c, g))

    # ⑤ 指针全量核:file 存在性(red)+ 行号漂移(仅显形计数)
    missing = []
    drift = 0
    for line in region:
        m = POINTER_RE.match(line)
        if not m:
            continue
        rel, lineno = m.group(1), int(m.group(2))
        if not (ROOT / rel).is_file():
            missing.append(rel)
            continue
        actual = pointer_expected_line(rel)
        if actual is not None and actual != lineno:
            drift += 1
    if missing:
        fail("L5 FAIL: pointer missing %d: %s" % (len(missing), missing[0]))

    # ⑥ 尾行汇总;全过 exit 0
    if drift:
        print("L5 pointer-drift displayed: %d" % drift)
    print("L5 summary: region lines=%d pointers=%d exempt=%d drift-display=%d missing=0"
          % (len(region), pointers, pointers, drift))
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())
