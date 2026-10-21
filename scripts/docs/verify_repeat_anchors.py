#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_repeat_anchors.py — L3 复现锚校验(R-22 M1,席 A)。

读共享快照(默认 scripts/doc_budget.snapshot.json,--snapshot 可覆盖),
对每条 repeat_anchor 的每个 file 跑 `grep -c -F <word> <file>`(cwd=仓根;
grep 无匹配 exit 1 且输出 0,按 0 处理),live 计数与 expected 逐一比对。

全等 → 逐 anchor 打 `L3 anchor <word>: <c1>/<c2>/... == expected OK`,
结尾 `L3 summary: anchors=<n> FAIL=0`,exit 0;
任一不等 → 逐条收集全打 `L3 FAIL: anchor <word> file <f>: live=<n> expected=<e>`
(首差即列),结尾 `L3 summary: anchors=<n> FAIL=<k>`,exit 1。
快照缺文件 / 缺 repeat_anchors 列表 / version!=1 → stderr 原因,exit 1。
仅标准库;PYTHONDONTWRITEBYTECODE=1 下不落 pyc。
"""

import argparse
import json
import os
import subprocess
import sys

DEFAULT_SNAPSHOT = os.path.join("scripts", "doc_budget.snapshot.json")


def repo_root():
    # __file__ = <仓根>/scripts/docs/verify_repeat_anchors.py → 向上三级定位仓根
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def die(reason):
    sys.stderr.write("L3 snapshot invalid: %s\n" % reason)
    sys.exit(1)


def load_anchors(path):
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
    anchors = snap.get("repeat_anchors")
    if not isinstance(anchors, list):
        die("snapshot field repeat_anchors missing or not a list")
    return anchors


def live_count(word, relpath):
    """grep -c -F <word> <relpath>;无匹配(rc=1,输出0)按 0;grep 本身出错按 -1。"""
    try:
        proc = subprocess.run(
            ["grep", "-c", "-F", word, relpath],
            cwd=os.getcwd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        sys.stderr.write("L3 grep spawn error for %s: %s\n" % (relpath, exc))
        return -1
    if proc.returncode in (0, 1):
        text = proc.stdout.decode("utf-8", "replace").strip()
        try:
            return int(text) if text else 0
        except ValueError:
            return -1
    sys.stderr.write(
        "L3 grep error rc=%d for %s: %s\n"
        % (
            proc.returncode,
            relpath,
            proc.stderr.decode("utf-8", "replace").strip(),
        )
    )
    return -1


def main():
    os.chdir(repo_root())  # cwd 锚定仓根
    parser = argparse.ArgumentParser(
        description="L3 repeat-anchor verifier (doc_budget snapshot)"
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
    anchors = load_anchors(snap_path)

    ok_lines = []
    fail_lines = []
    for idx, anchor in enumerate(anchors):
        if not isinstance(anchor, dict) or not isinstance(anchor.get("word"), str):
            fail_lines.append(
                "L3 FAIL: anchor #%d malformed (word must be a str)" % idx
            )
            continue
        word = anchor["word"]
        files = anchor.get("files")
        expected = anchor.get("expected")
        if not isinstance(files, list) or not isinstance(expected, list):
            fail_lines.append(
                "L3 FAIL: anchor %s malformed (files/expected must be lists)" % word
            )
            continue
        if len(files) != len(expected):
            fail_lines.append(
                "L3 FAIL: anchor %s files/expected length mismatch: %d files vs %d expected"
                % (word, len(files), len(expected))
            )
            continue
        counts = []
        anchor_fails = []
        for relpath, want in zip(files, expected):
            if isinstance(want, bool) or not isinstance(want, int):
                anchor_fails.append(
                    "L3 FAIL: anchor %s file %s malformed expected (must be int): %r"
                    % (word, relpath, want)
                )
                continue
            live = live_count(word, relpath)
            counts.append(live)
            if live != want:
                anchor_fails.append(
                    "L3 FAIL: anchor %s file %s: live=%d expected=%d"
                    % (word, relpath, live, want)
                )
        if anchor_fails:
            fail_lines.extend(anchor_fails)
        else:
            ok_lines.append(
                "L3 anchor %s: %s == expected OK"
                % (word, "/".join(str(c) for c in counts))
            )

    if fail_lines:
        for line in fail_lines:
            print(line)
        print("L3 summary: anchors=%d FAIL=%d" % (len(anchors), len(fail_lines)))
        sys.exit(1)
    for line in ok_lines:
        print(line)
    print("L3 summary: anchors=%d FAIL=0" % len(anchors))
    sys.exit(0)


if __name__ == "__main__":
    main()
