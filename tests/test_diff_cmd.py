#!/usr/bin/env python3
"""tests/test_diff_cmd.py —— vtx diff 命令单元测试。

构造两份最小 qc_report.json 与 subs_check.json，经 CLI 子进程（`env python3`，
规避本机 ZCode AppImage 对 sys.executable 的劫持，见全局 AGENTS.md）断言：
  ① 同构报告全等 → 输出 PASS、退出码 0、0 条差异；
  ② qc check 状态翻转（pass true→false）被显著标出（[状态翻转]）；
  ③ subs_check summary PASS→FAIL 与逐条字幕检查状态翻转被检出，差异条数正确；
  ④ 未知字段走通用递归兜底（数值变化给 旧→新）+ --dry-run 尊重。
运行：
  env python3 -X utf8 tests/test_diff_cmd.py          # 直跑（历史方式，行为不变）
  env python3 -m unittest discover -s tests -t . -v   # unittest discover（T-02 起 TestCase 化）
"""
import json
import subprocess
import sys
import tempfile
import unittest
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


def make_identical_qc():
    """场景①②共用的全等基线 qc 报告（原 main() 中 qa 的构造，原样提取）。"""
    return qc_rep([("成片帧数 = 300", True, "实测 300"),
                   ("响度归一 ≈ -14 LUFS", True, "实测 -14.1")])


def make_subs_pair():
    """场景③④共用的 subs_check 报告对（原 main() 中 sa/sb 的构造，原样提取）。"""
    sa = subs_rep("PASS", [(0, "ok", "ok"), (1, "ok", "ok")])
    sb = subs_rep("FAIL", [(0, "ok", "ok"), (1, "fail", "ok"), (2, "ok", "ok")])
    return sa, sb


def scenario_1_identical():
    """① 同构全等 → PASS。"""
    print("== 场景① 同构 qc 报告全等 ==")
    qa = make_identical_qc()
    code, out = run_diff(qa, json.loads(json.dumps(qa)))
    check(code == 0, f"全等 qc 报告退出码 0（实得 {code}）")
    check("PASS｜两份报告无实质差异" in out, "全等 qc 报告末行 PASS")


def scenario_2_status_flip():
    """② qc 状态翻转 + detail 数值变化。"""
    print("== 场景② qc check 状态翻转 ==")
    qa = make_identical_qc()
    qb = qc_rep([("成片帧数 = 300", True, "实测 300"),
                 ("响度归一 ≈ -14 LUFS", False, "实测 -18.2 LUFS")])
    code, out = run_diff(qa, qb)
    check(code == 1, f"qc 翻转退出码 1（实得 {code}）")
    check("[状态翻转]" in out and "响度归一 ≈ -14 LUFS" in out, "qc check 状态翻转被显著标出")
    check("✓ → ✗" in out, "翻转方向 ✓→✗ 正确")
    check("-18.2 LUFS" in out and "-14.1" in out, "detail 数值变化给 旧→新")
    check(out.rstrip().endswith("DIFF｜共 2 条差异（状态翻转 1 条）"),
          f"末行差异计数正确（实得：{out.rstrip().splitlines()[-1]}）")


def scenario_3_subs_diff():
    """③ subs_check：summary 翻转 + 字幕检查状态翻转 + 新增字幕。"""
    print("== 场景③ subs_check 差异 ==")
    sa, sb = make_subs_pair()
    code, out = run_diff(sa, sb)
    check(code == 1, f"subs 差异退出码 1（实得 {code}）")
    check("[状态翻转] summary: PASS → FAIL" in out, "summary PASS→FAIL 翻转被检出")
    check("results[#001].align: ✓ → ✗" in out, "逐条字幕 align 状态翻转被检出")
    check("results[#002]" in out and "[新增]" in out, "新增字幕条目被检出")
    check(out.rstrip().splitlines()[-1] == "DIFF｜共 7 条差异（状态翻转 2 条）",
          f"subs 差异条数 = 7（含 total/checked/coverage 元数据随之变化；实得：{out.rstrip().splitlines()[-1]}）")


def scenario_4_unknown_and_dryrun():
    """④ 未知字段通用兜底 + --dry-run 尊重。"""
    print("== 场景④ 未知字段兜底 + dry-run ==")
    sa, sb = make_subs_pair()
    sa2 = dict(sa); sa2["total"] = 2; sa2["future_field"] = {"x": 1}
    sb2 = dict(sb); sb2["total"] = 3; sb2["future_field"] = {"x": 9}
    code, out = run_diff(sa2, sb2)
    check("future_field.x: 1 → 9" in out, "未知字段走通用递归 diff 兜底")
    check("total: 2 → 3" in out, "顶层标量变化给 旧→新")
    # --dry-run：纯读命令，打印说明且结果一致
    with tempfile.TemporaryDirectory() as d:
        pa = Path(d) / "a.json"; pa.write_text(json.dumps(make_identical_qc()), encoding="utf-8")
        r = subprocess.run(["env", PY, "-X", "utf8", str(VTX), "--dry-run", "diff",
                            str(pa), str(pa)], capture_output=True, text=True, cwd=str(REPO))
        check(r.returncode == 0 and "[dry-run]" in r.stdout and "PASS" in r.stdout,
              "--dry-run 尊重（纯读打印说明，输出 PASS）")


class TestVtxDiffCmd(unittest.TestCase):
    """TestCase 包装（T-02 休眠假绿修复）：原 main() 四场景逐组搬入，
    check() 判定逻辑与直跑完全同源——断言失败经 FAILS 增量收集后在此转正失败。"""

    def _run_scenario(self, fn):
        before = len(FAILS)
        fn()
        new_fails = FAILS[before:]
        self.assertEqual([], new_fails,
                         "场景断言失败：\n" + "\n".join("  ✗ " + m for m in new_fails))

    def test_1_identical_qc_reports_equal(self):
        """① 同构报告全等 → 退出码 0、末行 PASS。"""
        self._run_scenario(scenario_1_identical)

    def test_2_qc_check_status_flip(self):
        """② qc check 状态翻转被显著标出、detail 旧→新、差异计数正确。"""
        self._run_scenario(scenario_2_status_flip)

    def test_3_subs_check_diff(self):
        """③ subs summary 翻转、逐条 align 翻转、新增字幕、差异条数 = 7。"""
        self._run_scenario(scenario_3_subs_diff)

    def test_4_unknown_field_fallback_and_dry_run(self):
        """④ 未知字段通用递归兜底 + --dry-run 尊重。"""
        self._run_scenario(scenario_4_unknown_and_dryrun)


def main():
    print("== vtx diff 单元测试 ==")
    scenario_1_identical()
    scenario_2_status_flip()
    scenario_3_subs_diff()
    scenario_4_unknown_and_dryrun()
    print(f"\n{'全部通过' if not FAILS else f'{len(FAILS)} 项失败'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
