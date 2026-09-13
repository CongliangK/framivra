#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_cli_baseurl.py · --base-url 主级/子级位置回归（P1-1，终审 2026-09-11）

subparser 的 default=None 会覆盖主级已解析值 → 主级 --base-url 被静默丢弃。
修复：子级 default=argparse.SUPPRESS。本测钉死两种位置等价。
"""
import os
import subprocess
import sys
import unittest

CLI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "asr_cli.py")


def run(args):
    return subprocess.run([sys.executable, CLI] + args,
                          capture_output=True, text=True, timeout=60)


class TestBaseUrlPosition(unittest.TestCase):
    def test_prefix_position_honored(self):
        """主级位置：--base-url X sidecar health → 请求打到 X（不被默认覆盖）。"""
        r = run(["--base-url", "http://127.0.0.1:19999", "sidecar", "health"])
        self.assertIn("19999", r.stdout + r.stderr)

    def test_suffix_position_honored(self):
        """子级位置：sidecar health --base-url X → 同样生效。"""
        r = run(["sidecar", "health", "--base-url", "http://127.0.0.1:19999"])
        self.assertIn("19999", r.stdout + r.stderr)

    def test_default_when_absent(self):
        """未传 → config/默认 18085。"""
        r = run(["sidecar", "health"])
        self.assertIn("18085", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
