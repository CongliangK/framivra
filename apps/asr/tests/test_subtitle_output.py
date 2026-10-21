#!/usr/bin/env python3
'Subtitle output contracts: SRT formatting and transactional timeline merge.'


# srt contracts
# srt_out 单测（方案 §9.1：时间码毫秒格式、序号连续、空行分隔——golden 文本比对）。
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


# tl merge contracts
# tl_merge 单测（方案 §9.1：合并后除 subtitles 外逐键相等、备份落 cache/reports/、
# --dry-run 不写、损坏 JSON 拒绝并保原文件）。
import json
import shutil

import tl_merge  # noqa: E402

NEW_SUBS = [
    {"in": 0, "out": 60, "text": "新字幕第一行"},
    {"in": 90, "out": 180, "text": "新字幕第二行"},
    {"in": 200, "out": 260, "text": "新第三行"},
]


class TestTlMerge(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="asr_tl_merge_"))
        self.tl = self.dir / "timeline.json"
        shutil.copy(FIXTURES / "timeline_fixture.json", self.tl)
        self.before = json.loads(self.tl.read_text(encoding="utf-8"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_merge_keeps_all_other_keys(self):
        result = tl_merge.merge_subtitles(str(self.tl), NEW_SUBS)
        self.assertFalse(result["dry_run"])
        after = json.loads(self.tl.read_text(encoding="utf-8"))
        self.assertEqual(after["subtitles"], NEW_SUBS)
        for key, val in self.before.items():
            if key == "subtitles":
                continue
            self.assertEqual(after[key], val, "键 %s 被改动" % key)
        # 逐键相等且无新增/丢失顶层键
        self.assertEqual(set(after.keys()), set(self.before.keys()))

    def test_backup_written_to_reports(self):
        result = tl_merge.merge_subtitles(str(self.tl), NEW_SUBS)
        backup = Path(result["backup"])
        self.assertTrue(backup.exists())
        self.assertEqual(backup.parent, self.dir / "cache" / "reports")
        old = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(old, self.before["subtitles"])

    def test_dry_run_writes_nothing(self):
        raw_before = self.tl.read_bytes()
        result = tl_merge.merge_subtitles(str(self.tl), NEW_SUBS, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(self.tl.read_bytes(), raw_before, "dry-run 不得写 timeline")
        self.assertFalse((self.dir / "cache").exists(), "dry-run 不得产生备份")
        self.assertEqual(result["old_n"], 2)
        self.assertEqual(result["new_n"], 3)

    def test_corrupt_json_rejected_file_kept(self):
        self.tl.write_text("{ 这不是 JSON !!!", encoding="utf-8")
        raw = self.tl.read_bytes()
        with self.assertRaises(RuntimeError):
            tl_merge.merge_subtitles(str(self.tl), NEW_SUBS)
        self.assertEqual(self.tl.read_bytes(), raw, "损坏时必须保持原文件不动")

    def test_invalid_subs_rejected(self):
        bad = [{"in": 100, "out": 50, "text": "in>=out"}]     # 违反 0<=in<out
        with self.assertRaises(ValueError):
            tl_merge.merge_subtitles(str(self.tl), bad)
        self.assertEqual(json.loads(self.tl.read_text())["subtitles"],
                         self.before["subtitles"], "非法条目必须拒绝且保护现状")
        bad2 = [{"in": 0, "out": 30}]                          # 缺 text
        with self.assertRaises(ValueError):
            tl_merge.merge_subtitles(str(self.tl), bad2)

    def test_two_merges_idempotent_shape(self):
        tl_merge.merge_subtitles(str(self.tl), NEW_SUBS)
        again = tl_merge.merge_subtitles(str(self.tl), NEW_SUBS)
        after = json.loads(self.tl.read_text(encoding="utf-8"))
        self.assertEqual(after["subtitles"], NEW_SUBS)
        self.assertEqual(again["old_n"], 3)


if __name__ == "__main__":
    unittest.main()
