"""install.py 交互向导测试(用户裁决:默认=执行,可主动选不装;纯终端提问)。

覆盖四层:
  - Wizard 单元:回车=默认 Y、y/n 显式、非法输入重问、EOF 回车等价、
    跳过项入 skipped、disabled 时零提问全默认。
  - 模式判定:_make_wizard 在 --non-interactive/--defaults/非 tty 三种
    情形零提问;仅 tty 且无旗标才启用。
  - 集成:交互回车→全装;n→对应项跳过且总结含跳过+手动指引;
    --dry-run 零副作用且打印选择。
  - 回归:既有安全矩阵(test_install_py_safety)以 stdin=DEVNULL 运行,
    非 tty 自动全默认,行为不变(该套件自身覆盖,此处不重复)。

全部 CPU-only,mock input/tty,不触真实 winget/网络/注册表。
"""
import argparse
import importlib.util
import io
import unittest
from unittest import mock

try:
    from ._support import REPO, SandboxCase
except ImportError:
    from _support import REPO, SandboxCase

INSTALL_PY = REPO / "scripts" / "install.py"


def _load_installer():
    spec = importlib.util.spec_from_file_location("vt_install_interactive", INSTALL_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WizardUnit(SandboxCase):
    def setUp(self):
        super().setUp()
        self.installer = _load_installer()

    def test_enter_accepts_default_true(self):
        answers = iter([""])
        wizard = self.installer.Wizard(True, prompt=lambda p: next(answers))
        self.assertTrue(wizard.ask("k", "描述"))
        self.assertEqual(wizard.choices, {"k": True})
        self.assertEqual(wizard.skipped, [])

    def test_explicit_n_skips_and_records(self):
        answers = iter(["n"])
        wizard = self.installer.Wizard(True, prompt=lambda p: next(answers))
        self.assertFalse(wizard.ask("k", "描述"))
        self.assertEqual(wizard.skipped, [("k", "描述")])

    def test_invalid_then_yes_reprompts(self):
        answers = iter(["maybe", "y"])
        prompts = []
        wizard = self.installer.Wizard(
            True, prompt=lambda p: (prompts.append(p), next(answers))[1])
        self.assertTrue(wizard.ask("k", "描述"))
        self.assertEqual(len(prompts), 2)

    def test_eof_behaves_like_enter(self):
        def eof(prompt):
            raise EOFError()
        wizard = self.installer.Wizard(True, prompt=eof)
        self.assertTrue(wizard.ask("k", "描述"))

    def test_disabled_asks_nothing_and_defaults(self):
        calls = []
        wizard = self.installer.Wizard(
            False, prompt=lambda p: calls.append(p) or (_ for _ in ()).throw(
                AssertionError("disabled wizard must not prompt")))
        self.assertTrue(wizard.ask("a", "甲"))
        self.assertFalse(wizard.ask("b", "乙", default=False))
        self.assertEqual(calls, [])
        # 默认 False 同样入 skipped(总结/手动指引面),只是不提问。
        self.assertEqual(wizard.skipped, [("b", "乙")])

    def test_summary_lists_choices_and_skips(self):
        wizard = self.installer.Wizard(False)
        wizard.ask("install", "安装")
        wizard.ask("shims", "写 PATH", default=False)
        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            wizard.summary()
        text = buffer.getvalue()
        self.assertIn("install=执行", text)
        self.assertIn("shims=跳过", text)
        self.assertIn("已跳过:写 PATH", text)


class ModeSelection(SandboxCase):
    def setUp(self):
        super().setUp()
        self.installer = _load_installer()

    def _args(self, **over):
        base = dict(dir=str(self.root / "t"), version="9.9.9", chrome=False,
                    non_interactive=False, defaults=False, dry_run=False,
                    upgrade=False)
        base.update(over)
        return argparse.Namespace(**base)

    def test_tty_without_flags_enables(self):
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = True
            self.assertTrue(self.installer._make_wizard(self._args()).enabled)

    def test_non_interactive_disables_even_with_tty(self):
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = True
            self.assertFalse(
                self.installer._make_wizard(self._args(non_interactive=True)).enabled)

    def test_defaults_disables_even_with_tty(self):
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = True
            self.assertFalse(
                self.installer._make_wizard(self._args(defaults=True)).enabled)

    def test_pipe_disables_even_without_flags(self):
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            self.assertFalse(self.installer._make_wizard(self._args()).enabled)


class WizardIntegration(SandboxCase):
    """进程内集成:mock tty + input,断言决定被执行层消费、跳过有指引。"""

    def setUp(self):
        super().setUp()
        self.installer = _load_installer()
        self.target = self.root / "installed"

    def _install_with(self, answers, argv_over=None):
        """构造最小安装路径:跳过下载/解包,只走到向导与执行层接线。"""
        argv_over = argv_over or {}
        base = dict(dir=str(self.target), version="9.9.9", chrome=False,
                    non_interactive=False, defaults=False, dry_run=True,
                    upgrade=False)
        base.update(argv_over)
        args = argparse.Namespace(**base)
        stream = iter(answers)
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = True
        with mock.patch.object(self.installer.sys, "stdin", fake_stdin), \
                mock.patch("builtins.input", lambda p: next(stream)):
            # dry-run=True:提问照常、打印计划、零副作用——正好检验
            # 「所有提问集中在下载/写盘之前」。
            buffer = io.StringIO()
            with mock.patch("sys.stdout", buffer):
                rc = self.installer.install(args)
        return rc, buffer.getvalue()

    def test_interactive_enter_installs_all_defaults(self):
        rc, out = self._install_with([""])
        self.assertEqual(rc, 0)
        self.assertIn("向导选择:install=执行", out)
        self.assertIn("dry-run", out)
        self.assertFalse(self.target.exists())  # 零写入

    def test_decline_install_cancels_cleanly(self):
        rc, out = self._install_with(["n"])
        self.assertEqual(rc, 0)
        self.assertIn("已按选择取消安装", out)
        self.assertFalse(self.target.exists())

    def test_chrome_question_only_when_flag(self):
        rc, out = self._install_with(["", ""], argv_over={"chrome": True})
        self.assertEqual(rc, 0)
        self.assertIn("chrome_cft=执行", out)
        rc, out = self._install_with([""])
        self.assertNotIn("chrome_cft", out)

    def test_win32_shims_question_and_skip_hint(self):
        # 非 win32 平台不问 shims;用 monkeypatch IS_WINDOWS=True 检验
        # 提问与跳过指引路径(write_shims 早退,不触注册表)。
        with mock.patch.object(self.installer, "IS_WINDOWS", True):
            rc, out = self._install_with(["", "n"])
        self.assertEqual(rc, 0)
        self.assertIn("shims_path=跳过", out)

    def test_answers_consumed_exactly_once_per_key(self):
        # 同一键只允许提问一次:install/chrome 各一次;若实现多问,
        # next(stream) 抛 StopIteration 直接失败(答案数严格对齐)。
        rc, out = self._install_with(["", "y"], argv_over={"chrome": True})
        self.assertEqual(rc, 0)
        self.assertIn("install=执行", out)
        self.assertIn("chrome_cft=执行", out)
        self.assertNotIn("跳过", out)


if __name__ == "__main__":
    unittest.main()
