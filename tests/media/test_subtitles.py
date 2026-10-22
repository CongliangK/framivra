r"""subs check 纯函数单元测试（零渲染）：check_subtitles(subs, voice_windows, fps)。

构造最小帧坐标输入直接调用 vtx/media/subs.py 的纯函数，不触 ffmpeg/文件系统。
运行：env python3 -X utf8 tests/media/test_subtitles.py

tests/media/test_subtitles.py —— v2 §1.1 条目 6：内核 subtitles_src 键。

覆盖：
  ① SRT 解析纯函数：时间戳边界（整秒/毫秒→帧四舍五入 half-up）/跨帧取整/多行文本
     \N 连接/BOM 与 CRLF 容错/空文件 = 无字幕/结构损坏 ValueError；
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

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests._support import full_hd_timeline as _tl
from vtx.media import subs as subs_mod
from vtx.infra import Ctx
from vtx.media.subtitles_src import parse_srt, parse_subs_json, resolve_subtitles_src


FPS = 30


def mk(subs, wins):
    return subs_mod.check_subtitles(subs, [(a, b) for a, b in wins], FPS)


class NormalAllGreen(unittest.TestCase):
    def test_aligned_reasonable_subs_pass(self):
        # voice 窗口 [60,150)（2-5s），字幕 60-120 帧（30 条字 /2s = 15 CPS？不——
        # 8 字/2s = 4 CPS），对齐偏移 0
        rep = mk([{'in': 60, 'out': 120, 'text': '大家好今天讲剪映'}], [(60, 150)])
        self.assertEqual(rep['summary'], 'PASS')
        self.assertEqual(rep['coverage'], '1/1')
        r = rep['results'][0]
        self.assertEqual(r['align']['status'], 'ok')
        self.assertEqual(r['cps']['status'], 'ok')
        self.assertEqual(r['overlap']['status'], 'ok')
        self.assertEqual(r['width']['status'], 'ok')


class Alignment(unittest.TestCase):
    def test_drift_minus_4s_fails_red(self):
        # 字幕滞后 voice 4.3s：|偏移| ≥ 1.5 → FAIL
        rep = mk([{'in': 60 + 129, 'out': 60 + 189, 'text': '大家好'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['align']['status'], 'fail')
        self.assertEqual(rep['summary'], 'FAIL')

    def test_early_2s_fails(self):
        rep = mk([{'in': 60 - 60, 'out': 30, 'text': '大家好'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['align']['status'], 'fail')

    def test_late_2s_fails(self):
        rep = mk([{'in': 60 + 60, 'out': 150, 'text': '大家好'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['align']['status'], 'fail')

    def test_small_drift_warns(self):
        rep = mk([{'in': 60 + 18, 'out': 120, 'text': '大家好'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['align']['status'], 'warn')

    def test_max_overlap_window_wins(self):
        # 两个窗口：字幕与第二窗口重叠更大，偏移相对第二窗口计算 → ok
        rep = mk([{'in': 300, 'out': 360, 'text': '大家好'}], [(60, 240), (300, 480)])
        r = rep['results'][0]['align']
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['offset_s'], 0.0)

    def test_unmatched_far_window_warns_honest(self):
        # 最近 voice 窗口距离 > 5s → WARN「无法检查对齐」，覆盖率如实扣减
        rep = mk([{'in': 0, 'out': 30, 'text': '大家好'}], [(300, 600)])
        r = rep['results'][0]['align']
        self.assertEqual(r['status'], 'warn')
        self.assertIn('无法检查对齐', r['detail'])
        self.assertEqual(rep['coverage'], '0/1')

    def test_no_voice_track_warns_all(self):
        rep = mk([{'in': 60, 'out': 120, 'text': '大家好'},
                  {'in': 180, 'out': 240, 'text': '第二条'}], [])
        self.assertTrue(all(r['align']['status'] == 'warn' for r in rep['results']))
        self.assertEqual(rep['coverage'], '0/2')
        # 无 voice 轨本身不算 FAIL（诚实条款），但其他检查仍生效
        self.assertEqual(rep['summary'], 'PASS')

    def test_align_threshold_cli_override(self):
        # --align-threshold 放宽到 3s：原 2s 偏移从 FAIL 变 WARN
        rep = subs_mod.check_subtitles(
            [{'in': 120, 'out': 180, 'text': '大家好'}], [(60, 300)], FPS, align_fail=3.0)
        self.assertEqual(rep['results'][0]['align']['status'], 'warn')


class SpeedOverlapWidth(unittest.TestCase):
    def test_cps_over_fail_threshold(self):
        # 19 字 / 2s = 9.5 CPS > 9 → FAIL
        rep = mk([{'in': 60, 'out': 120, 'text': '这行字幕特别长两秒之内完全读不完呀朋友'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['cps']['status'], 'fail')

    def test_cps_over_warn_threshold(self):
        # 16 字 / 2s = 8 CPS：>7.5 且 ≤9 → WARN
        rep = mk([{'in': 60, 'out': 120, 'text': '这行字幕的阅读速度稍微偏快了一点'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['cps']['status'], 'warn')

    def test_too_short_duration_warns(self):
        # 4 字 / 0.5s = 8 CPS 但时长 <0.6s → WARN
        rep = mk([{'in': 60, 'out': 75, 'text': '大家好'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['cps']['status'], 'warn')

    def test_overlapping_subs_both_fail(self):
        cases = [
            ('original pair', [(60, 120, '大家好'), (90, 150, '插进来了')], ['fail', 'fail']),
            ('nested gap', [(0, 150, 'A'), (30, 60, 'B'), (90, 120, 'C')],
             ['fail', 'fail', 'fail']),
            ('nested touching', [(0, 150, 'A'), (30, 60, 'B'), (60, 120, 'C')],
             ['fail', 'fail', 'fail']),
            ('same start', [(0, 30, 'A'), (0, 150, 'B'), (60, 120, 'C')],
             ['fail', 'fail', 'fail']),
            ('unsorted nested', [(90, 120, 'C'), (0, 150, 'A'), (30, 60, 'B')],
             ['fail', 'fail', 'fail']),
            ('touching only', [(0, 30, 'A'), (30, 60, 'B'), (60, 90, 'C')],
             ['ok', 'ok', 'ok']),
            ('disjoint', [(0, 30, 'A'), (60, 90, 'B'), (120, 150, 'C')],
             ['ok', 'ok', 'ok']),
            ('overlap then isolated', [(0, 60, 'A'), (30, 60, 'B'), (60, 90, 'C')],
             ['fail', 'fail', 'ok']),
            ('one frame', [(0, 1, 'A'), (0, 1, 'B'), (1, 2, 'C')],
             ['fail', 'fail', 'ok']),
            ('single', [(0, 30, 'A')], ['ok']),
            ('empty', [], []),
        ]
        for name, cues, expected in cases:
            with self.subTest(name=name):
                rep = mk([{'in': a, 'out': b, 'text': text} for a, b, text in cues],
                         [(60, 300)])
                self.assertEqual([r['overlap']['status'] for r in rep['results']], expected)
                self.assertEqual([r['overlap']['detail'] for r in rep['results']],
                                 ['与其他字幕时间重叠' if s == 'fail' else '' for s in expected])

    def test_three_lines_fails(self):
        rep = mk([{'in': 60, 'out': 120, 'text': '第一行\\N第二行\\N第三行'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['width']['status'], 'fail')

    def test_wide_line_fails(self):
        rep = mk([{'in': 60, 'out': 120, 'text': '一' * 29}], [(60, 300)])
        self.assertEqual(rep['results'][0]['width']['status'], 'fail')

    def test_width_boundary_28_ok(self):
        rep = mk([{'in': 60, 'out': 120, 'text': '一' * 28}], [(60, 300)])
        self.assertEqual(rep['results'][0]['width']['status'], 'ok')




class ReviewS1PostWindow(unittest.TestCase):
    """红队 S1 回归：字幕在 voice 窗口结束后开始，偏移参照最近外沿（非窗口起点）。"""
    def test_short_gap_after_window_ok(self):
        # voice 0-5s，字幕 5.5s 开始（滞后外沿 0.5s）：不得再报 +5.5s FAIL
        rep = mk([{'in': 165, 'out': 210, 'text': '大家好'}], [(0, 150)])
        r = rep['results'][0]['align']
        # 0.5s 恰落 WARN 下限（>= 语义）——S1 修复前此处是 +5.0s FAIL
        self.assertEqual(r['status'], 'warn')
        self.assertEqual(r['offset_s'], 0.5)
        self.assertIn('滞后窗口结束', r['detail'])

    def test_window_switch_boundary_zero_distance(self):
        # 切窗（CLI 真实可达形态：_union 合并后剩余间隙单窗）字幕起点 ==
        # 窗口末端（真实距离 0）：偏移 0 而非被夸大成 FAIL
        rep = mk([{'in': 300, 'out': 360, 'text': '大家好'}], [(0, 300)])
        r = rep['results'][0]['align']
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['offset_s'], 0.0)

    def test_pair_radius_boundary_inclusive(self):
        # 距离恰 == 5.0s 半径（>5 严格语义）：仍参与对齐检查（FAIL 而非 WARN）
        rep = mk([{'in': 300, 'out': 330, 'text': '大家好'}], [(0, 150)])
        r = rep['results'][0]['align']
        self.assertEqual(r['status'], 'fail')   # offset +5.0 ≥ 1.5
        # 略超半径 → WARN 无法检查，coverage 如实扣减
        rep2 = mk([{'in': 306, 'out': 336, 'text': '大家好'}], [(0, 150)])
        self.assertEqual(rep2['results'][0]['align']['status'], 'warn')
        self.assertEqual(rep2['coverage'], '0/1')

    def test_lead_before_window_negative_offset(self):
        # 字幕整体在窗口开始前 1s（无重叠）：负偏移（超前语义），参照窗口起点
        rep = mk([{'in': 0, 'out': 30, 'text': '大家好'}], [(30, 150)])
        r = rep['results'][0]['align']
        self.assertEqual(r['status'], 'warn')
        self.assertEqual(r['offset_s'], -1.0)
        self.assertIn('超前窗口开始', r['detail'])

    def test_align_fail_boundary_inclusive(self):
        # |偏移| == 1.5s 精确落在 FAIL 阈值（>= 语义锁定）
        rep = mk([{'in': 165, 'out': 210, 'text': '大家好'}], [(0, 150)])
        rep2 = subs_mod.check_subtitles([{'in': 240, 'out': 285, 'text': '大家好'}],
                                        [(0, 150)], FPS, align_fail=3.0)
        self.assertEqual(rep['results'][0]['align']['status'], 'warn')   # 0.5 ≤ 1.5 边界内
        # 偏移 3.0s == align_fail 3.0：>= 含边界 → FAIL（语义锁定）
        self.assertEqual(rep2['results'][0]['align']['status'], 'fail')


class ReviewS2ActiveSetFrameIntent(unittest.TestCase):
    """Issue178: preview follows integer intent, not the old rounded ASS bug."""

    def test_half_open_edges_and_single_frames(self):
        from types import SimpleNamespace
        for start, end in ((310, 329), (310, 311), (311, 312), (312, 313)):
            sub = {'in': start, 'out': end, 'text': '边界'}
            c = SimpleNamespace(fps=30, tl={"subtitles": [sub]})
            for f in range(start - 1, end + 2):
                self.assertEqual(subs_mod._active_subs(c, f),
                                 [sub] if start <= f < end else [])


#!/usr/bin/env python3





# Source parsing, loading, receipt identity and CLI integration.
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
        from vtx.media.subtitles_src import _sec_to_frame
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
        self.addCleanup(self._tmp.cleanup)
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
