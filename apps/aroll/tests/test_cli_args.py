"""aroll CLI 参数、序列化输入与 float32 WAV 提取/裁剪回归。

- aup3_extract.py / trim_takes.py --help：退出码 0、有 usage、有中文描述；
- 假 .aup3（非 SQLite）/ 文件不存在：报错干净（无 traceback）、退出非零。
- 44100/48000Hz WAV 保持采样率、气口秒数与合法格式；畸形输入不写产物。

运行：cd apps/aroll && env python3 -m unittest discover -s tests -t .
（标准库 + numpy；CLI 子进程用 list 传参，WAV 用小型临时数组，无模型或 FFmpeg。）
"""
import contextlib
import io
import json
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
import trim_takes
from wav_float32 import read_wav_float32


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


class FloatWavPipelineTests(unittest.TestCase):
    def _trim(self, root, samples, rate):
        source, output = root / "source", root / "output"
        source.mkdir()
        aup3_extract.write_wav_float32(source / "track01_voice.wav", samples, rate)
        with contextlib.redirect_stdout(io.StringIO()):
            trim_takes.main(source, output)
        return (output / "take01_voice.wav").read_bytes(), json.loads(
            (output / "takes_index.json").read_text())[0]

    def test_extracted_wav_keeps_44100_and_48000_rates(self):
        np = trim_takes.np
        for rate in (44100, 48000):
            with self.subTest(rate=rate), tempfile.TemporaryDirectory() as directory:
                samples = (.2 * np.sin(2 * np.pi * 1000 * np.arange(rate) / rate)).astype("<f4")
                raw, index = self._trim(Path(directory), samples, rate)
                # Independent header/duration oracle, not the shared reader.
                self.assertEqual(struct.unpack_from("<I", raw, 24)[0], rate)
                self.assertEqual(struct.unpack_from("<I", raw, 40)[0], rate * 4)
                self.assertEqual(raw[44:], samples.tobytes())
                self.assertEqual(index["orig_s"], 1.0)
                self.assertEqual(index["new_s"], 1.0)

    def test_padding_remains_in_seconds_at_48000(self):
        np, rate = trim_takes.np, 48000
        samples = np.zeros(rate, dtype="<f4")
        samples[19200:28800] = .2 * np.sin(2 * np.pi * 1000 * np.arange(9600) / rate)
        with tempfile.TemporaryDirectory() as directory:
            raw, index = self._trim(Path(directory), samples, rate)
        # Speech [0.4,0.6]s plus 150/250ms pad, allowing frame/RMS quantization.
        self.assertAlmostEqual(index["head_cut_s"], .25, delta=.04)
        self.assertAlmostEqual(index["tail_cut_s"], .15, delta=.04)
        self.assertEqual(struct.unpack_from("<I", raw, 24)[0], rate)
        self.assertEqual(index["new_samples"], (len(raw) - 44) // 4)
        self.assertAlmostEqual(index["new_s"], index["new_samples"] / rate, places=3)

    def test_malformed_or_unsupported_wav_is_rejected_before_output(self):
        np = trim_takes.np
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "canonical.wav"
            aup3_extract.write_wav_float32(path, np.ones(4096, dtype="<f4"), 48000)
            valid = path.read_bytes()
            cases = {"short": valid[:40], "truncated": valid[:-4], "trailing": valid + b"\0"}
            for name, offset, format_, value in (
                    ("riff_size", 4, "I", len(valid)), ("fmt_size", 16, "I", 18),
                    ("pcm", 20, "H", 1), ("stereo", 22, "H", 2),
                    ("zero_rate", 24, "I", 0), ("byte_rate", 28, "I", 48000),
                    ("alignment", 32, "H", 2), ("bits", 34, "H", 16),
                    ("data_size", 40, "I", 3)):
                raw = bytearray(valid)
                struct.pack_into("<" + format_, raw, offset, value)
                cases[name] = bytes(raw)
            for name, raw in cases.items():
                with self.subTest(name=name):
                    source, output = root / name, root / (name + "-out")
                    source.mkdir()
                    invalid = source / "track01_voice.wav"
                    invalid.write_bytes(raw)
                    with self.assertRaises(ValueError):
                        read_wav_float32(invalid)
                    with self.assertRaises(ValueError):
                        trim_takes.main(source, output)
                    self.assertEqual(list(output.iterdir()), [])


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
