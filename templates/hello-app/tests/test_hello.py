#!/usr/bin/env python3
"""templates/hello-app/tests/test_hello.py —— 最小 app 范本的两例冒烟。

① `cli.py hello --name X`：exit 0、打印产物路径、素材文件真实落盘（app = 产出
   独立素材的最小语义）；
② `health_check.py`：exit 0（doctor health.command 探针契约）。
子进程一律 env python3（argv[0] 劫持纪律，见全局 AGENTS.md）。
运行：bash scripts/run_all_tests.sh（批次四发现 templates/*/tests）或
      cd templates/hello-app && env python3 -m unittest discover -s tests -t ..
"""
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]


class HelloAppCli(unittest.TestCase):
    def test_hello_produces_standalone_asset(self):
        r = subprocess.run(["env", "python3", str(APP / "cli.py"),
                            "hello", "--name", "v2"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("hello, v2!", r.stdout)
        self.assertIn("产物", r.stdout)
        product = APP / "out" / "hello-v2.txt"
        self.assertTrue(product.is_file())
        self.assertIn("hello, v2!", product.read_text(encoding="utf-8"))

    def test_health_check_exits_zero(self):
        r = subprocess.run(["env", "python3", str(APP / "health_check.py")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("healthy", r.stdout)


if __name__ == "__main__":
    unittest.main()
