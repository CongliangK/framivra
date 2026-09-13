#!/usr/bin/env python3
"""tests/test_subtitles_src.py —— v2 §1.1 条目 6：内核 subtitles_src 键。

覆盖：
  ① SRT 解析纯函数：时间戳边界（整秒/毫秒→帧四舍五入 half-up）/跨帧取整/多行文本
     \\N 连接/BOM 与 CRLF 容错/空文件 = 无字幕/结构损坏 ValueError；
  ② .json 格式：v1 subtitles[] 同构列表帧值直用 + {"subtitles": [...]} 包裹形 +
     坏结构 ValueError；
  ③ 加载点语义（Ctx 构造期）：互斥（两者都非空记错误不解析）/文件缺失/解析失败
     （错误入 c.subtitles_errors、tl["subtitles"] 置 []）/成功（tl["subtitles"]
     即为解析结果）；
  ④ 回执身份：subtitles_src 文件内容变 → input_fingerprint 变（对齐 gfx html/
     声明依赖的处理方式）；
  ⑤ CLI 端到端（需 ffmpeg）：含 subtitles_src 的最小 timeline 走 validate +
     subs check 通过（raw 文件不含 subtitles[] 键）。
运行：env python3 -m unittest discover tests -t . -v（run_all_tests.sh 统一入口）
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from vtx.infra import Ctx
from vtx.subtitles_src import parse_srt, parse_subs_json, resolve_subtitles_src


def _tl(root: Path, **over):
    tl = {"version": 2,
          "project": {"fps": 30, "duration_frames": 180, "width": 1920, "height": 1080},
          "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
    tl.update(over)
    p = root / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return p


class ParseSrt(unittest.TestCase):
    FPS = 30

    def test_basic_boundary_and_rounding(self):
        srt = "1\n00:00:01,000 --> 00:00:03,500\n大家好\n"
        subs = parse_srt(srt, 30)
        self.assertEqual(subs, [{"in": 30, "out": 105, "text": "大家好"}])  # 3.5s×30=105

    def test_half_frame_rounds_up(self):
        # half-up 语义直接测换算函数：0.5 帧边界 → 1（Python round() 是 banker's
        # 会给 0）。30fps + 整数毫秒的 SRT 永远到不了 .5 帧（30ms|1000 无解），
        # 该语义为 24/25fps 等项目时基与未来毫秒精度输入预留。
        from vtx.subtitles_src import _sec_to_frame
        self.assertEqual(_sec_to_frame(0.5, 1), 1)
        self.assertEqual(_sec_to_frame(1.5, 1), 2)
        # 30fps 整数毫秒：1016ms → 30.48 帧 → 30（非边界，两舍入法一致）
        srt = "1\n00:00:01,016 --> 00:00:02,000\n半帧\n"
        self.assertEqual(parse_srt(srt, 30)[0]["in"], 30)

    def test_point_separator_and_attributes_tolerated(self):
        srt = "1\n00:00:00.500 --> 00:00:01,000 X1:100 X2:200\n点分隔毫秒\n\n"
        self.assertEqual(parse_srt(srt, 30), [{"in": 15, "out": 30, "text": "点分隔毫秒"}])

    def test_multiline_joined_with_ass_break(self):
        srt = "1\n00:00:00,000 --> 00:00:02,000\n第一行\n第二行\n"
        self.assertEqual(parse_srt(srt, 30)[0]["text"], "第一行\\N第二行")

    def test_bom_crlf_and_empty(self):
        self.assertEqual(parse_srt("", 30), [])                       # 空文件 = 无字幕
        self.assertEqual(parse_srt("\ufeff", 30), [])
        srt = "1\r\n00:00:00,000 --> 00:00:01,000\r\n回车换行\r\n\r\n"
        self.assertEqual(parse_srt(srt, 30)[0]["text"], "回车换行")

    def test_missing_index_line_ok_garbage_line_fails(self):
        # 缺索引行（按时间戳行分块）合法
        self.assertEqual(len(parse_srt("00:00:00,000 --> 00:00:01,000\n无序号\n", 30)), 1)
        with self.assertRaises(ValueError):
            parse_srt("垃圾行\n00:00:00,000 --> 00:00:01,000\n有文本\n", 30)

    def test_timestamp_without_text_fails(self):
        with self.assertRaises(ValueError):
            parse_srt("1\n00:00:00,000 --> 00:00:01,000\n\n", 30)


class ParseSubsJson(unittest.TestCase):
    def test_list_direct_and_wrapped(self):
        lst = [{"in": 10, "out": 40, "text": "帧值直用"}]
        self.assertEqual(parse_subs_json(lst, 30), lst)
        self.assertEqual(parse_subs_json({"subtitles": lst}, 30), lst)

    def test_bad_structure_raises(self):
        with self.assertRaises(ValueError):
            parse_subs_json({"subtitles": "nope"}, 30)
        with self.assertRaises(ValueError):
            parse_subs_json([{"in": 1, "out": 2}], 30)          # 缺 text
        with self.assertRaises(ValueError):
            parse_subs_json([{"in": True, "out": 2, "text": "x"}], 30)  # bool 污染


class ResolveAtLoadPoint(unittest.TestCase):
    def _ctx(self, root, **over):
        return Ctx(_tl(root, **over))

    def test_mutual_exclusion_records_error_keeps_inline(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "s.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n外部\n", encoding="utf-8")
            c = self._ctx(root, subtitles_src="s.srt",
                          subtitles=[{"in": 0, "out": 30, "text": "内联"}])
            self.assertEqual(len(c.subtitles_errors), 1)
            self.assertIn("互斥", c.subtitles_errors[0])
            self.assertEqual(c.tl["subtitles"], [{"in": 0, "out": 30, "text": "内联"}])  # 不替换

    def test_empty_inline_subs_allows_src(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "s.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n外部\n", encoding="utf-8")
            c = self._ctx(root, subtitles_src="s.srt", subtitles=[])
            self.assertEqual(c.subtitles_errors, [])
            self.assertEqual(c.tl["subtitles"], [{"in": 0, "out": 30, "text": "外部"}])

    def test_missing_file_and_parse_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = self._ctx(root, subtitles_src="nope.srt", subtitles=[])
            self.assertTrue(any("不存在" in e for e in c.subtitles_errors))
            self.assertEqual(c.tl["subtitles"], [])              # 防下游 KeyError
            (root / "bad.srt").write_text("垃圾行\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
            c2 = self._ctx(root, subtitles_src="bad.srt", subtitles=[])
            self.assertTrue(any("解析失败" in e for e in c2.subtitles_errors))
            (root / "bad.json").write_text("[{", encoding="utf-8")
            c3 = self._ctx(root, subtitles_src="bad.json", subtitles=[])
            self.assertTrue(any("解析失败" in e for e in c3.subtitles_errors))

    def test_unsupported_suffix_and_bad_type(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "s.vtt").write_text("x", encoding="utf-8")
            c = self._ctx(root, subtitles_src="s.vtt", subtitles=[])
            self.assertTrue(any("不支持" in e for e in c.subtitles_errors))
            c2 = self._ctx(root, subtitles_src=123, subtitles=[])
            self.assertTrue(any("必须为字符串" in e for e in c2.subtitles_errors))

    def test_absent_or_empty_string_is_default(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = self._ctx(root)                                   # 无键
            self.assertEqual(c.subtitles_errors, [])
            c2 = self._ctx(root, subtitles_src="", subtitles=[])  # 空串等价未声明
            self.assertEqual(c2.subtitles_errors, [])


class ReceiptIdentity(unittest.TestCase):
    def test_file_content_change_invalidates_fingerprint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "subs.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n版本一\n", encoding="utf-8")
            p = _tl(root, subtitles_src="subs.srt", subtitles=[])
            fp1 = Ctx(p).input_fingerprint
            (root / "subs.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n版本二\n", encoding="utf-8")
            fp2 = Ctx(p).input_fingerprint
            self.assertNotEqual(fp1, fp2, "subtitles_src 文件内容变 → 回执必须失效")
            # 不变的文件 → 指纹稳定（幂等）
            self.assertEqual(fp2, Ctx(p).input_fingerprint)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "CLI 端到端需要 ffmpeg/ffprobe（合成媒体）")
class CliEndToEnd(unittest.TestCase):
    """最小 timeline（raw 无 subtitles[]，声明 subtitles_src）走 validate + subs check。"""

    def _run(self, *args):
        return subprocess.run(
            ["env", "python3", str(REPO / "vtx" / "vtx.py"), "-t", str(self.tl), *args],
            capture_output=True, text=True, cwd=str(REPO))

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        ff = shutil.which("ffmpeg")
        src = root / "a.mp4"
        subprocess.run([ff, "-v", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2",
                        "-f", "lavfi", "-i", "sine=frequency=330:duration=2",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(src)], check=True)
        (root / "subs.srt").write_text(
            "1\n00:00:00,333 --> 00:00:02,500\n大家好\n", encoding="utf-8")
        tl = {"version": 2,
              "project": {"fps": 30, "width": 640, "height": 360, "duration_frames": 90},
              "sources": [{"id": "a", "path": "a.mp4"}],
              "video_tracks": [{"name": "base", "kind": "aroll", "clips": [
                  {"src": "a", "src_in": 0, "src_out": 60, "dst_in": 0, "dur": 60}]}],
              "audio_tracks": [{"name": "voice", "events": [
                  {"src": "a", "in": 0, "out": 60, "volume": 0.8}]}],
              "subtitles_src": "subs.srt"}   # 注意：无 subtitles[] 键（90 帧 ≥ SRT 出帧 75）
        self.root = root
        self.tl = root / "timeline.json"
        self.tl.write_text(json.dumps(tl), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_validate_and_subs_check_pass(self):
        r = self._run("validate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✓ 校验通过：1 源 / 1 视频轨 1 clips / 1 字幕", r.stdout)  # 解析后 1 条
        r2 = self._run("subs", "check")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("PASS", r2.stdout)
        rep = json.loads((self.root / "out" / "subs_check.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["total"], 1)
        self.assertEqual(rep["results"][0]["text"], "大家好")   # SRT 解析产物
        # 互斥：再内联 subtitles[] → validate FAIL
        tl = json.loads(self.tl.read_text(encoding="utf-8"))
        tl["subtitles"] = [{"in": 0, "out": 30, "text": "内联"}]
        self.tl.write_text(json.dumps(tl), encoding="utf-8")
        r3 = self._run("validate")
        self.assertEqual(r3.returncode, 1)
        self.assertIn("互斥", r3.stdout)


if __name__ == "__main__":
    unittest.main()
