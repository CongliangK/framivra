#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/safety/test_ps1_candidate_args.py —— R1:PS1 单元素候选切片守卫。

红队阻断:install_windows.ps1 两处 `$candidate[1..($candidate.Length - 1)]`
在候选为单元素 @('python')(Length=1)时,PowerShell 降序区间 1..0=@(1,0),
切片得 @($null,'python')——探测/调用带出杂散参数必炸。

本机无 pwsh(验证方式如实声明):本测试做两件事——
  1) 文本断言:两处切片均已被 `Length -gt 1` 守卫,无裸切片残留;
  2) 语义模拟:按 PowerShell 文档化语义(降序区间;越界标量索引→$null)
     复现旧表达式的杂散参数,并证明守卫后为空参数表。真实执行由
     hosted Windows CI job(windows-install)承担,本文件不谎称跑过 pwsh。
"""
import unittest
from pathlib import Path

PS1 = Path(__file__).resolve().parents[2] / "scripts" / "install_windows.ps1"


def ps_range(lo, hi):
    """PowerShell 区间语义:含两端,lo>hi 时降序。1..0 → [1,0]。"""
    step = 1 if hi >= lo else -1
    return list(range(lo, hi + step, step))


def ps_slice(array, indexes):
    """按索引列表取元素;越界标量索引 → None($null)。"""
    return [array[i] if 0 <= i < len(array) else None for i in indexes]


class CandidateSliceSemantics(unittest.TestCase):
    def test_old_expression_yields_stray_null_for_single_element(self):
        """修前表达式(红):@('python') 经 1..0 切片得 [None,'python']。"""
        candidate = ["python"]                      # @('python')
        indexes = ps_range(1, len(candidate) - 1)   # 1..0 → [1,0]
        self.assertEqual(indexes, [1, 0])
        sliced = ps_slice(candidate, indexes)       # 越界索引 1 → $null
        self.assertEqual(sliced, [None, "python"])  # 杂散参数实锤

    def test_guarded_expression_yields_empty_for_single_element(self):
        """修后守卫(绿):Length==1 → 空参数表。"""
        candidate = ["python"]
        guarded = (ps_slice(candidate, ps_range(1, len(candidate) - 1))
                   if len(candidate) > 1 else [])
        self.assertEqual(guarded, [])

    def test_two_element_candidate_unchanged(self):
        """@('py','-3') 两元素:守卫前后均为 ['-3'],行为不回退。"""
        candidate = ["py", "-3"]
        legacy = ps_slice(candidate, ps_range(1, len(candidate) - 1))
        guarded = (ps_slice(candidate, ps_range(1, len(candidate) - 1))
                   if len(candidate) > 1 else [])
        self.assertEqual(legacy, ["-3"])
        self.assertEqual(guarded, ["-3"])


class Ps1GuardedSource(unittest.TestCase):
    """文本断言:脚本两处切片(探测/调用)均带长度守卫,无裸切片残留。"""

    def test_both_slice_sites_are_guarded(self):
        text = PS1.read_text(encoding="utf-8")
        guarded = text.count("Length -gt 1")
        self.assertGreaterEqual(guarded, 2,
                                "探测与调用两处切片均需 Length -gt 1 守卫")
        # 任何含 [1..( 切片的行,同一行或其赋值行上下文必须出现守卫
        # (守卫写法:`$x = if ($y.Length -gt 1) { $y[1..(...)] } else { @() }`,
        #  切片行本身同行含守卫)。逐行检查:含切片的行须含守卫。
        for i, line in enumerate(text.splitlines()):
            if "[1..(" in line:
                self.assertIn(
                    "Length -gt 1", line,
                    "第 %d 行切片缺少同行长度守卫: %s" % (i + 1, line.strip()))

    def test_get_item_not_regressed(self):
        """附带回归哨兵:文件大小打印须仍是 Get-Item(防修 R1 时误改)。"""
        text = PS1.read_text(encoding="utf-8")
        self.assertIn("Get-Item $target", text)


if __name__ == "__main__":
    unittest.main()
