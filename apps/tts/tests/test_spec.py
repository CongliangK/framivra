#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_spec.py · timeline voice 轨 → spec.json 预算反推单测（方案 §9.2）

golden 来源：voice-ime manifest.json 实测——vt_A1 单片窗 (in=1492, out=1720)
= 228 帧 → budget = 228/30 + 0.02 = 7.62s。
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import spec  # noqa: E402

TL = {
    "project": {"fps": 30, "resolution": [1920, 1080]},
    "sources": [
        {"id": "vt_A1", "kind": "audio", "path": "assets/voice_tts/final/A1.wav"},
        {"id": "vt_B", "kind": "audio", "path": "assets/voice_tts/final/B.wav"},
        {"id": "sfx01", "kind": "audio", "path": "assets/sfx/click.wav"},
        {"id": "bgm", "kind": "audio", "path": "assets/bgm/loop.wav"},
    ],
    "audio_tracks": [
        {"name": "bed", "duck": True,
         "events": [{"src": "bgm", "in": 0, "out": 3000, "volume": 0.36, "loop": True}]},
        {"name": "voice",
         "events": [
             {"src": "vt_A1", "in": 1492, "out": 1720, "volume": 0.9},
             {"src": "vt_B", "in": 2000, "out": 2300, "src_in": 0, "volume": 0.9},
             {"src": "vt_B", "in": 2600, "out": 2650, "src_in": 300, "volume": 0.9},
             {"src": "sfx01", "in": 2800, "out": 2830, "volume": 1.0},
         ]},
    ],
}


def write_tl(d):
    p = os.path.join(d, "timeline.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(TL, f, ensure_ascii=False)
    return p


class TestSpec(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vt_tts_spec_")
        self.tl = write_tl(self.tmp)

    def test_single_window_golden(self):
        """vt_A1 单片窗 (1492,1720)=228 帧 → budget=228/30+0.02=7.62。"""
        out = io.StringIO()
        with redirect_stdout(out):
            s = spec.build_spec(self.tl)
        w = {x["id"]: x for x in s["windows"]}
        self.assertIn("A1", w)
        self.assertEqual(w["A1"]["budget_s"], 7.62)
        self.assertEqual(w["A1"]["text"], "")
        self.assertEqual(w["A1"]["n_events"], 1)

    def test_chain_window_sum(self):
        """vt_B 两片窗：链 = max(0+300, 300+50) = 350 帧 → 350/30+0.02。"""
        out = io.StringIO()
        with redirect_stdout(out):
            s = spec.build_spec(self.tl)
        w = {x["id"]: x for x in s["windows"]}
        self.assertEqual(w["B"]["n_events"], 2)
        self.assertAlmostEqual(w["B"]["budget_s"], 350 / 30 + 0.02, places=3)

    def test_non_vt_skipped_with_warning(self):
        """非 vt_ 前缀源（sfx01/bgm）跳过并警告；windows 只含 vt_ 去前缀 ID。"""
        out = io.StringIO()
        with redirect_stdout(out):
            s = spec.build_spec(self.tl)
        ids = [x["id"] for x in s["windows"]]
        self.assertEqual(sorted(ids), ["A1", "B"])
        self.assertIn("⚠", out.getvalue())
        self.assertIn("sfx01", out.getvalue())

    def test_ids_filter(self):
        out = io.StringIO()
        with redirect_stdout(out):
            s = spec.build_spec(self.tl, ids={"A1"})
        self.assertEqual([x["id"] for x in s["windows"]], ["A1"])

    def test_spec_shape(self):
        s = spec.build_spec(self.tl)
        self.assertEqual(s["version"], 1)
        self.assertEqual(s["engine"], "indextts")
        self.assertEqual(s["fps"], 30)
        self.assertTrue(os.path.isabs(s["voice_ref"]))  # cfg_get 默认链 + ~ 展开
        for x in s["windows"]:
            self.assertEqual(sorted(x.keys()), ["budget_s", "id", "n_events", "text"])

    def test_missing_voice_track(self):
        p = os.path.join(self.tmp, "tl2.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"audio_tracks": [{"name": "bed", "events": [
                {"src": "bgm", "in": 0, "out": 10, "volume": 1}]}]}, f)
        with self.assertRaises(RuntimeError):
            spec.build_spec(p)


if __name__ == "__main__":
    unittest.main()
