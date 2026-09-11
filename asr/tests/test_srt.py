#!/usr/bin/env python3
"""srt_out 单测（方案 §9.1：时间码毫秒格式、序号连续、空行分隔——golden 文本比对）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import srt_out  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestSrtOut(unittest.TestCase):
    def test_frame_to_srt_time(self):
        self.assertEqual(srt_out.frame_to_srt_time(24), "00:00:00,800")
        self.assertEqual(srt_out.frame_to_srt_time(42), "00:00:01,400")
        self.assertEqual(srt_out.frame_to_srt_time(0), "00:00:00,000")
        self.assertEqual(srt_out.frame_to_srt_time(1965), "00:01:05,500")
        self.assertEqual(srt_out.frame_to_srt_time(108000), "01:00:00,000")

    def test_golden(self):
        subs = [
            {"in": 24, "out": 42, "text": "你好"},
            {"in": 60, "out": 90, "text": "世界"},
            {"in": 1965, "out": 1980, "text": "第三十条（越界前夹取）"},
        ]
        golden = (FIXTURES / "golden_srt.txt").read_text(encoding="utf-8")
        self.assertEqual(srt_out.render_srt(subs), golden)

    def test_write_file(self):
        subs = [{"in": 0, "out": 30, "text": "单行"}]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "sub" / "a.srt"
            srt_out.write_file(subs, out)
            content = out.read_text(encoding="utf-8")
        self.assertEqual(content, "1\n00:00:00,000 --> 00:00:01,000\n单行\n")
        self.assertTrue(content.endswith("\n"))

    def test_empty_subs(self):
        self.assertEqual(srt_out.render_srt([]), "")


if __name__ == "__main__":
    unittest.main()
