#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_golden.py — doc_gates 负例金样跑器(R-22 M1,席 B)。

cwd 锚定仓根(由本件位置推定为 <repo>/tests/doc_gates_golden/ 的上两级)。
依次 subprocess 运行三档负例,断言被测门各以 rc=1 捕获:

  g1  scripts/verify_doc_budget.py     --anchor-check g1_source --snapshot g1_snapshot_tampered   (ceiling 篡改)
  g2  scripts/verify_repeat_anchors.py --snapshot g2_snapshot_phantom                            (幻词锚)
  g3  scripts/verify_charter_budget.py --snapshot g3_snapshot_over                               (measured>ceiling)

rc 反转:全部捕获(各 rc==1)→ 汇总 3/3,exit 0;任一未捕获 → exit 1。
g2/g3 被测脚本由席 A 并行产出;集成时点若未在位,该档报 NOT-captured FAIL
(缺文件),由 L3 集成席收口。全部负例金样自足,不引公司仓真实源件。
仅标准库;不写任何文件。
"""

import os
import subprocess
import sys

GOLDEN_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(GOLDEN_DIR))

GATES = [
    ("anchor-check-tamper", [
        sys.executable or "python3", "scripts/verify_doc_budget.py",
        "--anchor-check", "tests/doc_gates_golden/g1_source.json",
        "--snapshot", "tests/doc_gates_golden/g1_snapshot_tampered.json",
    ]),
    ("phantom-anchor", [
        sys.executable or "python3", "scripts/verify_repeat_anchors.py",
        "--snapshot", "tests/doc_gates_golden/g2_snapshot_phantom.json",
    ]),
    ("charter-over-ceiling", [
        sys.executable or "python3", "scripts/verify_charter_budget.py",
        "--snapshot", "tests/doc_gates_golden/g3_snapshot_over.json",
    ]),
]


def main():
    os.chdir(REPO_ROOT)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPYCACHEPREFIX", None)

    captured = 0
    for n, (name, cmd) in enumerate(GATES, 1):
        try:
            proc = subprocess.run(
                cmd, cwd=REPO_ROOT, env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            rc = proc.returncode
            out = proc.stdout.decode("utf-8", "replace").strip()
            if out:
                for line in out.splitlines():
                    print("  | %s" % line)
            ok = (rc == 1)
        except OSError as e:
            rc = None
            ok = False
            print("  | [missing] %s: %s(被测脚本未在位,由 L3 集成席收口)" % (cmd[1], e))
        if ok:
            captured += 1
            print("golden_%d: %s captured rc=%d PASS" % (n, name, rc))
        else:
            print("golden_%d: %s NOT-captured rc=%s FAIL"
                  % (n, name, "missing" if rc is None else rc))
    print("golden summary: %d/3 captured" % captured)
    sys.exit(0 if captured == 3 else 1)


if __name__ == "__main__":
    main()
