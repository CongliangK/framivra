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

install_windows.ps1 已重构为克隆模式(克隆/安全更新仓库 + 运行 vt.py init,
设计对齐 ibus-voice-ime 的 remote-install.sh):切片守卫断言原样保留;
原「Get-Item 下载大小打印」哨兵随下载段一并移除,改守克隆模式关键分支。
红队实证纯文本 in 断言只钉到注释/错误消息(删 reset --hard 执行行、删
marker 写入、把数据根比较改成 if ($false) 后测试仍绿),故克隆模式哨兵
改为行级结构断言:关键行为 token 必须出现在剥 '#' 注释后的代码行,调用类
(reset/status/fetch/clone/ls-tree/ls-files)必须钉在含 Invoke-Git 的实际
执行行上。
"""

import unittest
from pathlib import Path

PS1 = Path(__file__).resolve().parents[2] / "scripts" / "install_windows.ps1"


def ps1_code_lines():
    """install_windows.ps1 剥行注释后的代码行(该 ps1 只用 '#' 行注释、
    '#' 不出现在字符串字面量内;截断方向保守:只会让可匹配行更少、断言更严)。"""
    text = PS1.read_text(encoding="utf-8-sig")
    lines = []
    for raw in text.splitlines():
        cut = raw.find("#")
        lines.append(raw if cut < 0 else raw[:cut])
    return lines


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

class Ps1CloneModeStructure(unittest.TestCase):
    """克隆模式关键分支的行级结构断言(红队三变异的哨兵)。

    旧哨兵纯文本 in 断言被红队实证失效(12 token 中 9 个只钉到注释/错误
    消息:删执行行仍绿)。本类一律剥 '#' 注释后逐行匹配,要求关键行为 token
    出现在非注释代码行;git 调用类必须钉在含 Invoke-Git 的实际执行行。
    本机无 pwsh 的验证方式声明不变:文本/结构断言 + python 语义模拟,
    真实执行由 hosted Windows CI job(windows-install)承担。
    """

    def assert_code_line(self, tokens, what):
        """断言存在至少一行非注释代码,同时包含 tokens 的全部片段。"""
        for line in ps1_code_lines():
            if all(t in line for t in tokens):
                return
        self.fail("克隆模式关键执行行缺失(%s):需一行非注释代码同时含 %r"
                  % (what, tokens))

    def test_dirty_check_counts_untracked_files(self):
        self.assert_code_line(
            ["Invoke-Git", "'status'", "'--porcelain'", "'--untracked-files=all'"],
            "porcelain 判脏(未跟踪文件计入)的实际执行行")

    def test_clean_update_executes_reset_hard(self):
        self.assert_code_line(
            ["Invoke-Git", "'reset'", "'--hard'"],
            "干净克隆更新动作 reset --hard 的实际执行行")

    def test_update_path_writes_sync_marker(self):
        self.assert_code_line(
            ["Set-Content", "$markerPath", "$newStr"],
            "更新路径写本地提交保护 marker 的实际执行行")

    def test_clone_path_writes_sync_marker(self):
        self.assert_code_line(
            ["Set-Content", "$markerPath", "$head.Output"],
            "全新克隆路径写本地提交保护 marker 的实际执行行")

    def test_data_root_guard_comparison_is_live(self):
        self.assert_code_line(
            ["if ($dirNorm -eq $dataRoot)"],
            "克隆目录 vs 运行时数据根的实际 if 比较行")

    def test_dir_must_be_absolute_and_segments_clean(self):
        self.assert_code_line(
            ["$Dir -notmatch '^[A-Za-z]:[\\\\/]'"],
            "FRAMIVRA_DIR 相对路径拒绝的判断行")
        self.assert_code_line(
            ["throw", "绝对路径"],
            "FRAMIVRA_DIR 相对路径拒绝的报错行")
        self.assert_code_line(
            ["$seg -match '[. ]$'"],
            "FRAMIVRA_DIR 尾点/尾空格路径段拒绝的判断行")

    def test_ignored_collision_precheck_is_invoked(self):
        self.assert_code_line(
            ["Assert-NoIgnoredCollision", "$targetRef"],
            "ignored→tracked 碰撞预检的实际调用行")

    def test_collision_precheck_enumerates_both_sides(self):
        self.assert_code_line(
            ["Invoke-Git", "'ls-tree'", "'--name-only'"],
            "碰撞预检枚举远端将跟踪路径集(ls-tree)的执行行")
        self.assert_code_line(
            ["Invoke-Git", "'ls-files'", "'--exclude-standard'"],
            "碰撞预检枚举本地被忽略文件集(ls-files)的执行行")

    def test_fetch_and_clone_invocations(self):
        self.assert_code_line(
            ["Invoke-Git", "'fetch'", "'--depth'", "+${sourceRef}:${targetRef}"],
            "fetch 显式 refspec 的实际执行行")
        self.assert_code_line(
            ["$sourceRef = 'refs/heads/main'"],
            "main 分支源引用赋值")
        self.assert_code_line(
            ['$sourceRef = "refs/tags/$Ref"'],
            "冻结版本 tag 源引用赋值")
        self.assert_code_line(
            ["Invoke-Git", "'clone'", "'--depth'"],
            "全新克隆的实际执行行")

    def test_remaining_semantic_tokens_are_code(self):
        for tokens, what in (
            (["if ($SkipUpdate)"], "-SkipUpdate 分支判断"),
            (["$env:FRAMIVRA_DIR"], "克隆目录覆盖变量读取"),
            (["$env:FRAMIVRA_REPO"], "仓库地址覆盖变量读取"),
            (["$env:FRAMIVRA_REF"], "分支覆盖变量读取"),
            (["Assert-BootstrapVar 'FRAMIVRA_DIR'"], "FRAMIVRA_DIR 值校验调用"),
            (["Join-Path $env:LOCALAPPDATA 'framivra-repo'"],
             "克隆目录默认值(与数据根分离)"),
            (["Join-Path $env:LOCALAPPDATA 'framivra'"], "运行时数据根构造行"),
            (["framivra-bootstrap"], "本地提交保护 marker 文件名"),
            (["& $py[0] $pyArgs 'vt.py' 'init'"], "克隆内运行 vt.py init 的执行行"),
        ):
            self.assert_code_line(tokens, what)


if __name__ == "__main__":
    unittest.main()
