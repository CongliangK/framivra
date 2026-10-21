#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_charter_budget.py — L4 宪章预算门(R-22 M1,席 A)。

读共享快照(默认 scripts/doc_budget.snapshot.json,--snapshot 可覆盖)的
budget_items,逐件判定:
  measured > ceiling                    → `L4 FAIL: <id> measured <m> > ceiling <c>`(收集,有则 exit 1)
  warning < measured <= ceiling         → `L4 常鸣态显形: <id> measured <m> > warning <w>(A20 登记,不阻断)`
  其余                                  → `L4 <id>: ceiling=<c> measured=<m> OK`
结尾 `L4 summary: items=<n> over=<x> changming=<k>`;exit 0/1。
快照缺文件 / version!=1 / 缺 budget_items 列表 / unit 不属 {"word","char"}
(出现 "byte" 一律判无效)→ stderr 原因,exit 1。
仅标准库;PYTHONDONTWRITEBYTECODE=1 下不落 pyc。
"""

import argparse
import json
import math
import os
import sys

DEFAULT_SNAPSHOT = os.path.join("scripts", "doc_budget.snapshot.json")
VALID_UNITS = ("word", "char")


def repo_root():
    # __file__ = <仓根>/scripts/docs/verify_charter_budget.py → 向上三级定位仓根
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def die(reason):
    sys.stderr.write("L4 snapshot invalid: %s\n" % reason)
    sys.exit(1)


def is_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value)))


def load_items(path):
    if not os.path.isfile(path):
        die("snapshot not found: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            snap = json.load(fh)
    except (OSError, ValueError) as exc:
        die("cannot parse snapshot %s: %s" % (path, exc))
    if not isinstance(snap, dict):
        die("snapshot root is not a JSON object")
    if snap.get("version") != 1:
        die("snapshot version must == 1, got %r" % (snap.get("version"),))
    items = snap.get("budget_items")
    if not isinstance(items, list) or not items:
        die("snapshot field budget_items must be a nonempty list")
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            die("budget_items[%d] is not an object" % idx)
        unit = item.get("unit")
        if unit not in VALID_UNITS:
            die(
                "budget_items[%d] (id=%r) unit must be one of %s, got %r "
                '(unit "byte" is always invalid)'
                % (idx, item.get("id"), "/".join(VALID_UNITS), unit)
            )
        for field in ("id", "ceiling", "warning", "measured"):
            if item.get(field) is None:
                die("budget_items[%d] missing field: %s" % (idx, field))
        for field in ("ceiling", "warning", "measured"):
            if not is_number(item.get(field)):
                die("budget_items[%d] (id=%r) field %s must be a finite number" % (idx, item.get("id"), field))
    return items


def fmt(value):
    return str(value)


def main():
    os.chdir(repo_root())  # cwd 锚定仓根
    parser = argparse.ArgumentParser(
        description="L4 charter budget verifier (doc_budget snapshot)"
    )
    parser.add_argument(
        "--snapshot",
        default=DEFAULT_SNAPSHOT,
        help="snapshot JSON path (default: %s)" % DEFAULT_SNAPSHOT,
    )
    args = parser.parse_args()

    snap_path = args.snapshot
    if not os.path.isabs(snap_path):
        snap_path = os.path.join(os.getcwd(), snap_path)
    items = load_items(snap_path)

    over = 0
    changming = 0
    for item in items:
        item_id = item["id"]
        ceiling = item["ceiling"]
        warning = item["warning"]
        measured = item["measured"]
        if measured > ceiling:
            print(
                "L4 FAIL: %s measured %s > ceiling %s"
                % (item_id, fmt(measured), fmt(ceiling))
            )
            over += 1
        elif warning < measured <= ceiling:
            print(
                "L4 常鸣态显形: %s measured %s > warning %s(A20 登记,不阻断)"
                % (item_id, fmt(measured), fmt(warning))
            )
            changming += 1
        else:
            print(
                "L4 %s: ceiling=%s measured=%s OK"
                % (item_id, fmt(ceiling), fmt(measured))
            )

    print("L4 summary: items=%d over=%d changming=%d" % (len(items), over, changming))
    sys.exit(1 if over else 0)


if __name__ == "__main__":
    main()
