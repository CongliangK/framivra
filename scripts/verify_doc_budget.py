#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_doc_budget.py — L2 文档预算核验/锚核(R-22 M1,席 B)。

用法(cwd 锚定仓根):
    python3 scripts/verify_doc_budget.py [--snapshot PATH]
    python3 scripts/verify_doc_budget.py --anchor-check SOURCEPATH [--snapshot PATH]

默认模式:加载快照(默认 scripts/doc_budget.snapshot.json),校验 version==1、
source.sha256 为 64 位十六进制,逐件校验必填字段、unit∈{word,char}(byte 即无效)、
ceiling/warning/measured 为 int、measured<=ceiling;逐行输出 `L2 <id>: ... OK`,
warning<measured<=ceiling 加行常鸣态显形(A20 登记,不阻断);尾行 summary;全过 exit 0,
任一失败首差即列 exit 1。

--anchor-check 模式:先比对 SOURCEPATH 字节 sha256 与快照 source.sha256,再逐件
比对策略字段子集(items: id,path_company,ceiling,unit,extract_cmd,warning,
headroom_min,generated_budget;repeat_anchors: word,files,baseline,cross_ref,
source_ref),first difference 风格,首差即列;全等 exit 0。

仅标准库;不写任何文件。
"""

import argparse
import hashlib
import json
import re
import sys

DEFAULT_SNAPSHOT = "scripts/doc_budget.snapshot.json"

POLICY_ITEM_FIELDS = (
    "id", "path_company", "ceiling", "unit", "extract_cmd",
    "warning", "headroom_min", "generated_budget",
)
ANCHOR_FIELDS = ("word", "files", "baseline", "cross_ref", "source_ref")
SNAPSHOT_ITEM_FIELDS = POLICY_ITEM_FIELDS + ("measured", "measured_at", "state")

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


def run_anchor_check(source_path, snapshot_path):
    src_bytes = _load_json_bytes(source_path, "source")
    src_sha = hashlib.sha256(src_bytes).hexdigest()
    source = json.loads(src_bytes.decode("utf-8"))
    snap = _load_json(snapshot_path, "snapshot")

    if not isinstance(source, dict):
        _fail("anchor-check FAIL: source root is not an object")
    if not isinstance(snap, dict):
        _fail("anchor-check FAIL: snapshot root is not an object")

    snap_source = snap.get("source")
    snap_sha = snap_source.get("sha256") if isinstance(snap_source, dict) else None
    if not isinstance(snap_sha, str) or snap_sha.lower() != src_sha:
        shown = snap_sha if isinstance(snap_sha, str) else str(snap_sha)
        _fail("anchor-check FAIL: source sha256 mismatch (snapshot=%s source=%s)"
              % (shown[:8], src_sha[:8]))

    src_items = source.get("items")
    snap_items = snap.get("budget_items")
    if not isinstance(src_items, list):
        _fail("anchor-check FAIL: source.items missing or not a list")
    if not isinstance(snap_items, list):
        _fail("anchor-check FAIL: snapshot.budget_items missing or not a list")
    for i, s_item in enumerate(src_items):
        if not isinstance(s_item, dict):
            _fail("anchor-check FAIL: source.items[%d] is not an object" % i)
        sid = s_item.get("id", "<idx%d>" % i)
        if i >= len(snap_items):
            _fail("anchor-check FAIL: item %s missing in snapshot.budget_items "
                  "(snapshot has %d items, source has %d)"
                  % (_fmt(sid), len(snap_items), len(src_items)))
        t_item = snap_items[i]
        if not isinstance(t_item, dict):
            _fail("anchor-check FAIL: item %s snapshot entry is not an object" % _fmt(sid))
        for field in POLICY_ITEM_FIELDS:
            sv = s_item.get(field)
            tv = t_item.get(field)
            if sv != tv:
                _fail("anchor-check FAIL: item %s field %s: snapshot=%s source=%s"
                      % (_fmt(sid), field, _fmt(tv), _fmt(sv)))

    src_anchors = source.get("repeat_anchors")
    snap_anchors = snap.get("repeat_anchors")
    if not isinstance(src_anchors, list):
        _fail("anchor-check FAIL: source.repeat_anchors missing or not a list")
    if not isinstance(snap_anchors, list):
        _fail("anchor-check FAIL: snapshot.repeat_anchors missing or not a list")
    for i, s_anchor in enumerate(src_anchors):
        if not isinstance(s_anchor, dict):
            _fail("anchor-check FAIL: source.repeat_anchors[%d] is not an object" % i)
        label = s_anchor.get("word", "<idx%d>" % i)
        if i >= len(snap_anchors):
            _fail("anchor-check FAIL: repeat_anchor %s missing in snapshot.repeat_anchors "
                  "(snapshot has %d, source has %d)"
                  % (_fmt(label), len(snap_anchors), len(src_anchors)))
        t_anchor = snap_anchors[i]
        if not isinstance(t_anchor, dict):
            _fail("anchor-check FAIL: repeat_anchor %s snapshot entry is not an object"
                  % _fmt(label))
        for field in ANCHOR_FIELDS:
            sv = s_anchor.get(field)
            tv = t_anchor.get(field)
            if sv != tv:
                _fail("anchor-check FAIL: repeat_anchor %s field %s: snapshot=%s source=%s"
                      % (_fmt(label), field, _fmt(tv), _fmt(sv)))

    print("anchor-check OK (source=%s)" % src_sha[:8])
    sys.exit(0)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="L2 doc budget verify / anchor-check (R-22 M1 seat B)")
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT,
                        help="snapshot path (default: %(default)s, relative to cwd=仓根)")
    parser.add_argument("--anchor-check", metavar="SOURCEPATH", default=None,
                        help="verify policy SOURCEPATH (doc_budget.json) against snapshot")
    args = parser.parse_args(argv)

    if args.anchor_check:
        run_anchor_check(args.anchor_check, args.snapshot)
    run_default(args.snapshot)


if __name__ == "__main__":
    main()
