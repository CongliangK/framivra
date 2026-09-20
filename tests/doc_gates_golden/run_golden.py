#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_golden.py — doc_gates 金样跑器(R-22 M1 席 B 三档;M2 席 D 扩至 10 档)。

cwd 锚定仓根(由本件位置推定为 <repo>/tests/doc_gates_golden/ 的上两级)。
总 10 档,golden_<n> 按序编号:

  g1  scripts/verify_doc_budget.py     --anchor-check g1_source --snapshot g1_snapshot_tampered   (ceiling 篡改;捕获)
  g2  scripts/verify_repeat_anchors.py --snapshot g2_snapshot_phantom                            (幻词锚;捕获)
  g3  scripts/verify_charter_budget.py --snapshot g3_snapshot_over                               (measured>ceiling;捕获)
  g4~g8 各档 catalog 副本篡改/破坏 → scripts/verify_manifest_catalog.py --check(捕获)
      g4_manifest_tamper/g5_registry_drift/g6_marker_corrupt/g7_double_inject/g8_marker_missing
  g9~g10 编码/尾空白差异 → 同上(吸收,期望 exit 0)
      g9_bom_absorb/g10_trailing_ws

rc 反转语义=「每档断言符合」:
  捕获类(g1~g8)期望被测 verifier exit 1;吸收类(g9,g10)期望 exit 0。
  每档输出 golden_<n>: <名> as-expected rc=<r> PASS 或 UNEXPECTED rc=<r> FAIL;
  尾行 golden summary: <k>/10 as-expected;10/10 → exit 0,否则 1。

g4~g10 由各档目录 instruction.json 驱动(cmd/expect_rc 见各件)。
g2/g3 被测脚本由席 A 并行产出、verify_manifest_catalog.py/gen_manifest_catalog.py
由席 C 并行产出;集成时点未在位,该档如实报缺(UNEXPECTED rc=missing FAIL),
由 L3 集成席收口。
g4~g10 的 catalog.md 副本=真 docs/catalog.md 复制至各档目录后按 instruction.json
载明的篡改点落刀(真件只读零触,禁改真 docs/catalog.md);集成席收口重验。
全部金样自足,不引公司仓真实源件。仅标准库;除被测脚本自身行为外不写任何文件。
"""

import json
import os
import subprocess
import sys

GOLDEN_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(GOLDEN_DIR))

# g1~g3 维持原断言(M1 席 B;捕获类,期望被测门 exit 1)。
BASE_GATES = [
    ("g1", "anchor-check-tamper", [
        sys.executable or "python3", "scripts/verify_doc_budget.py",
        "--anchor-check", "tests/doc_gates_golden/g1_source.json",
        "--snapshot", "tests/doc_gates_golden/g1_snapshot_tampered.json",
    ], 1),
    ("g2", "phantom-anchor", [
        sys.executable or "python3", "scripts/verify_repeat_anchors.py",
        "--snapshot", "tests/doc_gates_golden/g2_snapshot_phantom.json",
    ], 1),
    ("g3", "charter-over-ceiling", [
        sys.executable or "python3", "scripts/verify_charter_budget.py",
        "--snapshot", "tests/doc_gates_golden/g3_snapshot_over.json",
    ], 1),
]

# g4~g10(M2 席 D):各档目录 instruction.json 驱动。
INSTRUCTION_DIRS = [
    "g4_manifest_tamper",
    "g5_registry_drift",
    "g6_marker_corrupt",
    "g7_double_inject",
    "g8_marker_missing",
    "g9_bom_absorb",
    "g10_trailing_ws",
]


def _load_instruction_cases():
    """读各档 instruction.json → [(id, name, cmd, expect_rc, error_or_None)]。"""
    cases = []
    for d in INSTRUCTION_DIRS:
        path = os.path.join(GOLDEN_DIR, d, "instruction.json")
        try:
            with open(path, "rb") as f:
                spec = json.loads(f.read().decode("utf-8"))
            cases.append((
                str(spec["id"]), str(spec["name"]),
                [str(t) for t in spec["cmd"]], int(spec["expect_rc"]), None,
            ))
        except (OSError, ValueError, KeyError, TypeError) as e:
            cases.append((d.split("_", 1)[0], d, None, None,
                          "instruction.json unreadable/invalid: %s" % e))
    return cases


def main():
    os.chdir(REPO_ROOT)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPYCACHEPREFIX", None)

    gates = [(gid, name, cmd, expect) for gid, name, cmd, expect in BASE_GATES]
    gates += [(gid, name, cmd, expect) for gid, name, cmd, expect, _ in _load_instruction_cases()]

    as_expected = 0
    for n, (gid, name, cmd, expect_rc) in enumerate(gates, 1):
        label = "%s_%s" % (gid, name)
        rc = None
        out = ""
        error = None
        if cmd is None:
            error = "instruction unavailable"
        elif len(cmd) > 1 and "/" in cmd[1] and not os.path.exists(cmd[1]):
            # 目标脚本缺位预检(与 doc_gates.sh 的 target-missing 同口径):
            # 如实报缺,不占用被测门的一次实跑。
            error = "%s(被测脚本未在位,由 L3 集成席收口)" % cmd[1]
        else:
            try:
                proc = subprocess.run(
                    cmd, cwd=REPO_ROOT, env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                )
                rc = proc.returncode
                out = proc.stdout.decode("utf-8", "replace").strip()
            except OSError as e:
                error = "%s: %s(被测脚本未在位,由 L3 集成席收口)" % (
                    cmd[1] if len(cmd) > 1 else cmd, e)
        if out:
            for line in out.splitlines():
                print("  | %s" % line)
        if error is not None:
            print("  | [missing] %s" % error)
        if error is None and rc == expect_rc:
            as_expected += 1
            print("golden_%d: %s as-expected rc=%d PASS" % (n, label, rc))
        else:
            print("golden_%d: %s UNEXPECTED rc=%s FAIL"
                  % (n, label, "missing" if rc is None else rc))
    print("golden summary: %d/%d as-expected" % (as_expected, len(gates)))
    sys.exit(0 if as_expected == len(gates) else 1)


if __name__ == "__main__":
    main()
