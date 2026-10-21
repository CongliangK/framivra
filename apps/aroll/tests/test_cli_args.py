"""aroll CLI 参数面单测（A4 缺陷1）。

- aup3_extract.py / trim_takes.py --help：退出码 0、有 usage、有中文描述；
- 假 .aup3（非 SQLite）/ 文件不存在：报错干净（无 traceback）、退出非零。

运行：cd apps/aroll && env python3 -m unittest discover -s tests -t .
（仅用标准库 + 子进程 list 形式传参，无 shell 注入面。）
"""
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
import aup3_extract


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
    def test_serialized_reads_reject_invalid_lengths_without_advancing(self):
        for length in (-5, -1, 2, 2**31 - 1):
            with self.subTest(length=length):
                reader = aup3_extract.R(b'x')
                with self.assertRaises(aup3_extract.Aup3FormatError):
                    reader.raw(length)
                self.assertEqual(reader.i, 0)
        for method, size in [('u8', 1), ('u16', 2), ('u32', 4), ('i32', 4),
                             ('i64', 8), ('f32', 4), ('f64', 8)]:
            with self.subTest(method=method):
                reader = aup3_extract.R(b'\0' * (size - 1))
                with self.assertRaises(aup3_extract.Aup3FormatError):
                    getattr(reader, method)()
                self.assertEqual(reader.i, 0)

    def test_variable_length_tokens_reject_bad_data_and_preserve_valid_data(self):
        for token in (3, 11, 12, 16):
            prefix = bytes([token]) + (struct.pack('<H', 1) if token in (3, 16) else b'')
            for length in (-5, -1, 2, 2**31 - 1):
                with self.subTest(token=token, length=length):
                    with self.assertRaises(aup3_extract.Aup3FormatError):
                        aup3_extract.parse_doc(prefix + struct.pack('<i', length) + b'x', {1: 'name'}, 1)
            for payload in (b'', b'x'):
                with self.subTest(token=token, payload=payload):
                    events = aup3_extract.parse_doc(prefix + struct.pack('<i', len(payload)) + payload,
                                                   {1: 'name'}, 1)
                    expected = ('attr', 'name', payload.decode()) if token == 3 else (
                        ('blob', 'name', payload) if token == 16 else
                        (aup3_extract.FT[token], payload.decode()))
                    self.assertEqual(events, [expected])
        self.assertEqual(aup3_extract.parse_dict(b'\0\1\x0f\1\0\1\0x'), {1: 'x'})
        with self.assertRaises(aup3_extract.Aup3FormatError):
            aup3_extract.parse_dict(b'\0\1\x0f\1\0\2\0x')

    def test_malformed_serialized_project_reports_clean_error(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / 'malformed.aup3'
            with sqlite3.connect(project) as con:
                con.execute('CREATE TABLE project (id INTEGER, dict BLOB, doc BLOB)')
                con.execute('INSERT INTO project VALUES (1, ?, ?)',
                            (b'\0\1', bytes.fromhex('0bfbffffff')))
            r = run_cli(['aup3_extract.py', str(project), str(Path(td) / 'out')])
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('Traceback', r.stderr)
        self.assertIn('工程数据损坏', r.stderr)

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
