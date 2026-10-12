"""aroll CLI 参数面单测（A4 缺陷1）。

- aup3_extract.py / trim_takes.py --help：退出码 0、有 usage、有中文描述；
- 假 .aup3（非 SQLite）/ 文件不存在：报错干净（无 traceback）、退出非零。

运行：cd apps/aroll && env python3 -m unittest discover -s tests -t .
（仅用标准库 + 子进程 list 形式传参，无 shell 注入面。）
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]


def run_cli(args):
    return subprocess.run([sys.executable, *args], capture_output=True,
                          encoding="utf-8", errors="replace", cwd=str(APP_DIR), timeout=60)


class Aup3ExtractHelpTests(unittest.TestCase):
    def test_help_exit_zero_with_usage(self):
        r = run_cli(["aup3_extract.py", "--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage:", r.stdout)
        self.assertIn("aup3", r.stdout)        # 位置参数仍在 usage 面（兼容旧调用）
        self.assertIn("输出目录", r.stdout)     # 中文描述
        self.assertIn("--dump", r.stdout)      # 既有旗标保留

    def test_no_args_is_clean_argparse_error(self):
        r = run_cli(["aup3_extract.py"])
        self.assertEqual(r.returncode, 2, r.stderr)   # argparse 参数错（旧版为 IndexError traceback）
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("usage:", r.stderr)


class TrimTakesHelpTests(unittest.TestCase):
    def test_help_exit_zero_with_usage(self):
        r = run_cli(["trim_takes.py", "--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage:", r.stdout)
        self.assertIn("src_dir", r.stdout)     # 位置参数顺序不变
        self.assertIn("掐头去尾", r.stdout)     # 中文描述


class Aup3BadInputTests(unittest.TestCase):
    def test_fake_aup3_reports_clean_error(self):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "fake.aup3"
            fake.write_bytes(b"definitely not a sqlite database, just junk bytes 0123456789")
            out = Path(td) / "out"
            r = run_cli(["aup3_extract.py", str(fake), str(out)])
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("OperationalError", r.stderr)
        self.assertIn("无法读取工程", r.stderr)  # 一行式中文报错

    def test_missing_aup3_reports_clean_error(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_cli(["aup3_extract.py", str(Path(td) / "nope.aup3"), td])
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("无法读取工程", r.stderr)


if __name__ == "__main__":
    unittest.main()
