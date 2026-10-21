#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_doc_budget.py — L2 文档预算核验(R-22 M1,席 B;R5 改仓内自足)。

用法(cwd 锚定仓根):
    python3 scripts/docs/verify_doc_budget.py [--snapshot PATH]

唯一模式:加载快照(默认 scripts/doc_budget.snapshot.json,仓内自足快照,
不引用任何外部仓源件),校验 version==1、
source.sha256 为 64 位十六进制,逐件校验必填字段、unit∈{word,char}(byte 即无效)、
ceiling/warning/measured 为 int、measured<=ceiling;逐行输出 `L2 <id>: ... OK`,
warning<measured<=ceiling 加行常鸣态显形(A20 登记,不阻断);尾行 summary;全过 exit 0,
任一失败首差即列 exit 1。

仅对照仓内 snapshot,无任何外部源件锚核对/存在性检查;仅标准库;不写任何文件。
"""

import argparse
import json
import re
import sys

DEFAULT_SNAPSHOT = "scripts/doc_budget.snapshot.json"

SNAPSHOT_ITEM_FIELDS = (
    "id", "path_company", "ceiling", "unit", "extract_cmd",
    "warning", "headroom_min", "generated_budget",
    "measured", "measured_at", "state",
)

SHA256_RE = re.compile(r"\A[0-9a-fA-F]{64}\Z")


def _fail(msg):
    print(msg)
    sys.exit(1)


def _fmt(v):
    return json.dumps(v, ensure_ascii=False)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _load_json_bytes(path, label):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        _fail("L2 FAIL: cannot read %s %s: %s" % (label, path, e))


def _load_json(path, label):
    raw = _load_json_bytes(path, label)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        _fail("L2 FAIL: cannot parse %s %s: %s" % (label, path, e))


def run_default(snapshot_path):
    snap = _load_json(snapshot_path, "snapshot")
    if not isinstance(snap, dict):
        _fail("L2 FAIL: snapshot root is not an object")

    version = snap.get("version")
    if not _is_int(version) or version != 1:
        _fail("L2 FAIL: snapshot version %s != 1" % _fmt(version))

    source = snap.get("source")
    if not isinstance(source, dict):
        _fail("L2 FAIL: snapshot.source missing or not an object")
    sha = source.get("sha256")
    if not isinstance(sha, str) or not SHA256_RE.match(sha):
        _fail("L2 FAIL: source.sha256 is not 64-hex: %s" % _fmt(sha))

    items = snap.get("budget_items")
    if not isinstance(items, list):
        _fail("L2 FAIL: snapshot.budget_items missing or not a list")

    for idx, item in enumerate(items):
        iid = item.get("id") if isinstance(item, dict) else None
        if not isinstance(item, dict):
            _fail("L2 FAIL: budget_items[%d] is not an object" % idx)
        for field in SNAPSHOT_ITEM_FIELDS:
            if field not in item:
                _fail("L2 FAIL: item %s missing field %s" % (_fmt(iid), field))
        unit = item["unit"]
        if unit not in ("word", "char"):
            _fail("L2 FAIL: item %s unit %s invalid (word|char only; byte is invalid)"
                  % (_fmt(iid), _fmt(unit)))
        for field in ("ceiling", "warning", "measured"):
            if not _is_int(item[field]):
                _fail("L2 FAIL: item %s field %s is not int: %s"
                      % (_fmt(iid), field, _fmt(item[field])))
        ceiling = item["ceiling"]
        warning = item["warning"]
        measured = item["measured"]
        if measured > ceiling:
            _fail("L2 FAIL: %s measured %d > ceiling %d" % (iid, measured, ceiling))
        print("L2 %s: ceiling=%d unit=%s measured=%d state=%s OK"
              % (iid, ceiling, unit, measured, item["state"]))
        if warning < measured <= ceiling:
            print("L2 常鸣态显形: %s measured %d > warning %d(A20 登记,不阻断)"
                  % (iid, measured, warning))

    print("L2 summary: items=%d FAIL=0" % len(items))
    sys.exit(0)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="L2 doc budget verify (R-22 M1 seat B; R5 仓内自足快照)")
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT,
                        help="snapshot path (default: %(default)s, relative to cwd=仓根)")
    args = parser.parse_args(argv)

    run_default(args.snapshot)


if __name__ == "__main__":
    main()
