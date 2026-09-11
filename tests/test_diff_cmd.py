#!/usr/bin/env python3
"""tests/test_diff_cmd.py —— vtx diff 命令单元测试。

构造两份最小 qc_report.json 与 subs_check.json，经 CLI 子进程（`env python3`，
规避本机 ZCode AppImage 对 sys.executable 的劫持，见全局 AGENTS.md）断言：
  ① 同构报告全等 → 输出 PASS、退出码 0、0 条差异；
  ② qc check 状态翻转（pass true→false）被显著标出（[状态翻转]）；
  ③ subs_check summary PASS→FAIL 与逐条字幕检查状态翻转被检出，差异条数正确；
  ④ 未知字段走通用递归兜底（数值变化给 旧→新）。
运行：env python3 -X utf8 tests/test_diff_cmd.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VTX = REPO / "vtx" / "vtx.py"
PY = sys.executable or "python3"

FAILS = []


def check(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAILS.append(msg)


def run_diff(a, b):
    """CLI 子进程跑 diff，返回 (returncode, stdout)。一律 ['env','python3',…] 防劫持。"""
    with tempfile.TemporaryDirectory() as d:
        pa, pb = Path(d) / "a.json", Path(d) / "b.json"
        pa.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        pb.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(["env", PY, "-X", "utf8", str(VTX), "diff", str(pa), str(pb)],
                           capture_output=True, text=True, cwd=str(REPO))
        return r.returncode, r.stdout + r.stderr


def qc_rep(checks):
    return {"checks": [{"name": n, "pass": p, "detail": dt} for n, p, dt in checks]}


def subs_rep(summary, rows):
    """最小 subs_check 报告：rows = [(index, align_status, cps_status)]。"""
    results = []
    for i, (idx, al, cps) in enumerate(rows):
        results.append({"index": idx, "in_s": 1.0 + i, "out_s": 2.0 + i, "text": f"字幕{idx}",
                        "align": {"status": al, "detail": "起点偏移 +0.10s" if al == "ok" else "起点偏移 +2.00s"},
                        "cps": {"status": cps, "detail": f"CPS {cps}"},
                        "overlap": {"status": "ok", "detail": ""},
                        "width": {"status": "ok", "detail": ""}})
    return {"total": len(results), "checked": len(results),
            "coverage": f"{len(results)}/{len(results)}",
            "results": results, "summary": summary}


def main():
    print("== vtx diff 单元测试 ==")

    # ① 同构全等 → PASS
    qa = qc_rep([("成片帧数 = 300", True, "实测 300"), ("响度归一 ≈ -14 LUFS", True, "实测 -14.1")])
    code, out = run_diff(qa, json.loads(json.dumps(qa)))
    check(code == 0, f"全等 qc 报告退出码 0（实得 {code}）")
    check("PASS｜两份报告无实质差异" in out, "全等 qc 报告末行 PASS")

    # ② qc 状态翻转 + detail 数值变化
    qb = qc_rep([("成片帧数 = 300", True, "实测 300"),
                 ("响度归一 ≈ -14 LUFS", False, "实测 -18.2 LUFS")])
    code, out = run_diff(qa, qb)
    check(code == 1, f"qc 翻转退出码 1（实得 {code}）")
    check("[状态翻转]" in out and "响度归一 ≈ -14 LUFS" in out, "qc check 状态翻转被显著标出")
    check("✓ → ✗" in out, "翻转方向 ✓→✗ 正确")
    check("-18.2 LUFS" in out and "-14.1" in out, "detail 数值变化给 旧→新")
    check(out.rstrip().endswith("DIFF｜共 2 条差异（状态翻转 1 条）"),
          f"末行差异计数正确（实得：{out.rstrip().splitlines()[-1]}）")

    # ③ subs_check：summary 翻转 + 字幕检查状态翻转 + 新增字幕
    sa = subs_rep("PASS", [(0, "ok", "ok"), (1, "ok", "ok")])
    sb = subs_rep("FAIL", [(0, "ok", "ok"), (1, "fail", "ok"), (2, "ok", "ok")])
    code, out = run_diff(sa, sb)
    check(code == 1, f"subs 差异退出码 1（实得 {code}）")
    check("[状态翻转] summary: PASS → FAIL" in out, "summary PASS→FAIL 翻转被检出")
    check("results[#001].align: ✓ → ✗" in out, "逐条字幕 align 状态翻转被检出")
    check("results[#002]" in out and "[新增]" in out, "新增字幕条目被检出")
    check(out.rstrip().splitlines()[-1] == "DIFF｜共 7 条差异（状态翻转 2 条）",
          f"subs 差异条数 = 7（含 total/checked/coverage 元数据随之变化；实得：{out.rstrip().splitlines()[-1]}）")

    # ④ 未知字段通用兜底 + --dry-run 尊重
    sa2 = dict(sa); sa2["total"] = 2; sa2["future_field"] = {"x": 1}
    sb2 = dict(sb); sb2["total"] = 3; sb2["future_field"] = {"x": 9}
    code, out = run_diff(sa2, sb2)
    check("future_field.x: 1 → 9" in out, "未知字段走通用递归 diff 兜底")
    check("total: 2 → 3" in out, "顶层标量变化给 旧→新")
    # --dry-run：纯读命令，打印说明且结果一致
    with tempfile.TemporaryDirectory() as d:
        pa = Path(d) / "a.json"; pa.write_text(json.dumps(qa), encoding="utf-8")
        r = subprocess.run(["env", PY, "-X", "utf8", str(VTX), "--dry-run", "diff",
                            str(pa), str(pa)], capture_output=True, text=True, cwd=str(REPO))
        check(r.returncode == 0 and "[dry-run]" in r.stdout and "PASS" in r.stdout,
              "--dry-run 尊重（纯读打印说明，输出 PASS）")

    print(f"\n{'全部通过' if not FAILS else f'{len(FAILS)} 项失败'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
