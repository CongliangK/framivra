#!/usr/bin/env python3
"""tests/media/test_range_render.py —— 区间渲染（方案五阶段1）单元测试。

覆盖（纯函数部分零渲染免费测）：
  ① parse_timespec：秒 / 小数秒 / mm:ss / hh:mm:ss(.fff) / 帧号 f 后缀 / 非法输入；
  ② range_label：秒 1 位小数 + 文件名安全字符；
  ③ 窗口解析：--clip 按 src 名 / --track 外包络 / 找不到返回 None；
  ④ select_segments：与全片切点集求交（含中段、跨段边界、窗口越界钳制）；
  ⑤ CLI 校验：--range 无 --draft 拒绝、三标志互斥、空区间拒绝（子进程真跑 argparse 路径）；
  ⑥ 区间渲染不写 out/ 与 receipt（以 mock 合成 plan 层面验证 run_range 的副作用边界
     ——真实渲染走可选集成测试，VTX_RANGE_INTEG=1 时对小型合成 timeline 实跑）。
运行：env python3 -m unittest discover tests -v（本机 ZCode 环境必须 env python3 前缀，
裸 python3 的 sys.executable 被 AppImage 劫持，见全局 AGENTS.md）
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from fractions import Fraction
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))   # repo 根注入（v2 §1.4 包导入，不再注入 repo/vtx）

from vtx.pipeline.range import (parse_timespec, range_label, window_from_clip,
                        window_from_track, select_segments)


FPS = 30
TL = {
    "video_tracks": [
        {"name": "base", "kind": "aroll", "clips": [
            {"src": "a.mp4", "src_in": 0, "src_out": 180, "dst_in": 0, "dur": 180},
            {"src": "b.mp4", "src_in": 0, "src_out": 180, "dst_in": 180, "dur": 180}]},
        {"name": "pip", "kind": "pip", "clips": [
            {"src": "cam.mp4", "src_in": 10, "src_out": 100, "dst_in": 60, "dur": 90}]},
        {"name": "gfx-full", "kind": "gfx", "clips": [
            {"src": "gfx/page.html", "dst_in": 30, "dur": 60}]},
    ],
}


class TestConcatManifest(unittest.TestCase):
    def manifest(self, edges):
        from vtx.pipeline.composite import _concat_manifest
        return _concat_manifest(((Path(f"segment{n}.mp4"), a, b)
                                 for n, (a, b) in enumerate(zip(edges, edges[1:]))), FPS)

    def durations(self, manifest):
        return [Fraction(line.split()[1]) for line in manifest.splitlines()[1::2]]

    def test_canonical_irregular_cuts_have_frame_derived_durations(self):
        edges = [0, 112, 225, 338, 450, 562, 675, 788, 900]
        durations = self.durations(self.manifest(edges))
        self.assertEqual(durations, [Fraction(n, 1_000_000) for n in
            (3733333, 3766667, 3766667, 3733333, 3733333, 3766667, 3766667, 3733333)])
        self.assertEqual(sum(durations), 30)
        elapsed = Fraction(0)
        for a, duration in zip(edges, durations):
            self.assertEqual(round(elapsed * 15360), a * 512)
            elapsed += duration

    def test_many_cuts_do_not_accumulate_rounding_and_range_origin_is_local(self):
        self._assert_ntsc_boundaries_are_exact_and_do_not_accumulate()
        edges = list(range(10001))
        durations = self.durations(self.manifest(edges))
        elapsed = Fraction(0)
        for a, duration in zip(edges, durations):
            self.assertLessEqual(abs(elapsed - Fraction(a, FPS)), Fraction(1, 2_000_000))
            self.assertEqual(round(elapsed * 15360), a * 512)
            elapsed += duration
        self.assertEqual(self.manifest(edges), self.manifest([n + 10**12 for n in edges]))
        self.assertEqual(self.durations(self.manifest([110, 112, 225, 230])),
                         [Fraction(n, 1_000_000) for n in (66667, 3766666, 166667)])

    def _assert_ntsc_boundaries_are_exact_and_do_not_accumulate(self):
        from vtx.pipeline.composite import _concat_manifest
        edges = [10**12 + n for n in range(1001)]
        for fps in (29.97, '30000/1001', '120000/1001'):
            rate = Fraction(30000 if fps != '120000/1001' else 120000, 1001)
            manifest = _concat_manifest([(Path('segment.mp4'), a, b)
                                        for a, b in zip(edges, edges[1:])], fps)
            elapsed = Fraction(0)
            for frame, duration in enumerate(self.durations(manifest)):
                self.assertLessEqual(abs(elapsed - frame / rate), Fraction(1, 2_000_000))
                elapsed += duration
            self.assertLessEqual(abs(elapsed - 1000 / rate), Fraction(1, 2_000_000))

    def test_paths_keep_absolute_posix_quote_and_unicode_escaping(self):
        from vtx.pipeline.composite import _concat_manifest
        path = Path("剪辑 # /take's.mp4")
        manifest = _concat_manifest([(path, 0, 1)], FPS)
        escaped = path.resolve().as_posix().replace("'", "'\\''")
        self.assertEqual(manifest, f"file '{escaped}'\nduration 0.033333\n")
        self.assertEqual(_concat_manifest([], FPS), '')


class TestParseTimespec(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(parse_timespec("12", FPS), 360)
        self.assertEqual(parse_timespec("12.5", FPS), 375)   # 12.5*30=375 整
        self.assertEqual(parse_timespec(" 0 ", FPS), 0)

    def test_timecode(self):
        self.assertEqual(parse_timespec("0:30", FPS), 900)
        self.assertEqual(parse_timespec("02:05", FPS), 3750)
        self.assertEqual(parse_timespec("1:01:01.5", FPS), round(3661.5 * FPS))

    def test_frame_suffix(self):
        self.assertEqual(parse_timespec("300f", FPS), 300)
        self.assertEqual(parse_timespec("300F", FPS), 300)

    def test_invalid(self):
        for bad in ("abc", "12:60x", "", "1.2.3", "-5"):
            with self.assertRaises(ValueError):
                parse_timespec(bad, FPS)


class TestRangeLabel(unittest.TestCase):
    def test_label(self):
        self.assertEqual(range_label(0, FPS), "0.000")
        self.assertEqual(range_label(180, FPS), "6.000")
        self.assertEqual(range_label(185, FPS), "6.167")   # round 秒 3 位（P6：1 位小数相邻窗碰撞）
        # 相邻窗不碰撞（P6 修复 B-P3-5c：[3,6) 与 [4,5) 曾同标签互相覆盖）
        self.assertNotEqual(range_label(90, FPS), range_label(120, FPS))
        # 文件名安全：即使将来换标签来源也不会漏出危险字符
        self.assertRegex(range_label(300, FPS), r"^[0-9A-Za-z._-]+$")


class TestWindows(unittest.TestCase):
    def test_clip_window(self):
        self.assertEqual(window_from_clip(TL, "b"), (180, 360))
        self.assertEqual(window_from_clip(TL, "page"), (30, 90))   # gfx html stem
        self.assertIsNone(window_from_clip(TL, "nope"))

    def test_track_window(self):
        self.assertEqual(window_from_track(TL, "pip"), (60, 150))
        self.assertEqual(window_from_track(TL, "base"), (0, 360))
        self.assertEqual(window_from_track(TL, "gfx"), (30, 90))   # kind 兜底匹配
        self.assertIsNone(window_from_track(TL, "ghost"))


class TestSelectSegments(unittest.TestCase):
    EDGES = [0, 2093, 4186, 6279, 8373]   # 全片切点集示意（与整片渲染同源）

    def test_middle_window(self):
        self.assertEqual(select_segments(self.EDGES, 100, 300), [(100, 300)])
        # 跨切点的窗口按段拆两块渲染（各段 composite 子区间），总帧数不变
        self.assertEqual(select_segments(self.EDGES, 2000, 2500),
                         [(2000, 2093), (2093, 2500)])

    def test_across_boundary(self):
        self.assertEqual(select_segments(self.EDGES, 2000, 2200), [(2000, 2093), (2093, 2200)])

    def test_full_span_and_clamp(self):
        self.assertEqual(select_segments(self.EDGES, 0, 8373),
                         [(0, 2093), (2093, 4186), (4186, 6279), (6279, 8373)])
        # 窗口越界由调用方钳制，select 不再扩（保持纯函数无副作用）
        self.assertEqual(select_segments(self.EDGES, -5, 99), [(0, 99)])
        self.assertEqual(select_segments(self.EDGES, 9000, 9999), [])

    def test_exact_segment_frame(self):
        self.assertEqual(select_segments(self.EDGES, 2093, 4186), [(2093, 4186)])


class TestCliGuards(unittest.TestCase):
    """CLI 校验路径（argparse+main 前置检查，不触渲染）：失败必须 exit≠0 且不改产物。"""

    def _run(self, *extra):
        return subprocess.run(
            ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
             "-t", str(REPO / "tests" / "fixtures_nonexist.json"), "video", *extra],
            capture_output=True, text=True, cwd=str(REPO))

    def test_range_requires_draft_video(self):
        r = self._run("--range", "0", "6")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("video --draft", r.stdout)

    def test_flags_mutually_exclusive(self):
        r = self._run("--draft", "--range", "0", "6", "--clip", "a")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("互斥", r.stdout)
        r = self._run("--draft", "--clip", "a", "--track", "base")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("互斥", r.stdout)

    def test_range_vs_frames_gpu_exclusive(self):
        # P2-b（复审）：区间预览恒为 draft 快速路径，与 --frames/--gpu 无组合语义
        for extra in (("--frames", "90"), ("--gpu",)):
            r = self._run("--draft", "--range", "0", "6", *extra)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("互斥", r.stdout)

    def test_empty_track_friendly(self):
        # P2-a（复审）：空轨（无 clips）不得在 min() 上裸崩，须给出列出现有轨的文案
        with tempfile.TemporaryDirectory() as d:
            tl = {"version": 2, "project": {"fps": 30, "width": 64, "height": 64,
                                            "duration_frames": 300},
                  "sources": [], "subtitles": [], "audio_tracks": [],
                  "video_tracks": [{"name": "ghost", "kind": "aroll", "clips": []}]}
            p = Path(d) / "timeline.json"
            p.write_text(json.dumps(tl), encoding="utf-8")
            r = subprocess.run(
                ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(p), "video", "--draft", "--track", "ghost"],
                capture_output=True, text=True, cwd=str(REPO))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("空轨", r.stdout)
            self.assertIn("ghost", r.stdout)

    def test_window_from_track_empty_returns_none(self):
        self.assertIsNone(window_from_track(
            {"video_tracks": [{"name": "x", "kind": "aroll", "clips": []}]}, "x"))

    def test_bad_timespec_friendly(self):
        # 校验发生在 timeline 存在性检查之后——用真实存在的最小 timeline 验证友好报错
        with tempfile.TemporaryDirectory() as d:
            tl = {"version": 2, "project": {"fps": 30, "width": 64, "height": 64,
                                            "duration_frames": 300},
                  "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
            p = Path(d) / "timeline.json"
            p.write_text(json.dumps(tl), encoding="utf-8")
            r = subprocess.run(
                ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(p), "video", "--draft", "--range", "abc", "6"],
                capture_output=True, text=True, cwd=str(REPO))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("无法解析时间点", r.stdout)
            self.assertFalse((Path(d) / "cache" / "preview").exists())

    def test_dry_run_no_false_selfcheck_failure(self):
        # P1-A（复审）：--dry-run + --range 组合——dry 不产物流，末尾自检 ffprobe
        # 不得真执行（曾误报「✗ 区间渲染自检失败：总帧数 ? ≠ 90」exit=1）
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "timeline.json"
            p.write_text(json.dumps(self._min_tl()), encoding="utf-8")
            r = subprocess.run(
                ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(p), "video", "--draft", "--range", "0", "6", "--dry-run"],
                capture_output=True, text=True, cwd=str(REPO))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("[dry-run]", r.stdout)
            self.assertNotIn("自检失败", r.stdout)

    def test_cold_gfx_friendly_error(self):
        # P1-B（复审）：gfx 页缓存缺失时须显式指路 `vtx gfx`，而非 ffmpeg 原始报错
        with tempfile.TemporaryDirectory() as d:
            gfxdir = Path(d) / "gfx"; gfxdir.mkdir()
            (gfxdir / "page.html").write_text("<html></html>", encoding="utf-8")
            p = Path(d) / "timeline.json"
            p.write_text(json.dumps(self._min_tl(video_tracks=[
                {"name": "g", "kind": "gfx",
                 "clips": [{"src": "gfx/page.html", "dst_in": 0, "dur": 60}]}])), encoding="utf-8")
            r = subprocess.run(
                ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(p), "video", "--draft", "--range", "0", "6"],
                capture_output=True, text=True, cwd=str(REPO))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("先运行", r.stdout)
            self.assertIn("gfx/page.html", r.stdout)

    @staticmethod
    def _min_tl(video_tracks=()):
        return {"version": 2, "project": {"fps": 30, "width": 64, "height": 64,
                                          "duration_frames": 300},
                "sources": [], "subtitles": [], "audio_tracks": [],
                "video_tracks": list(video_tracks)}


class TestRangeCachePolicy(unittest.TestCase):
    """Exercise range routing and the real receipt gate, not rendering quality."""

    def setUp(self):
        from vtx.pipeline.infra import Ctx
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        timeline = self.root / 'timeline.json'
        timeline.write_text(json.dumps(TestCliGuards._min_tl()), encoding='utf-8')
        self.c = Ctx(timeline)
        self.c.dry = True
        self.c.run = mock.Mock()

    def test_all_modes_reuse_current_formal_gain_and_reject_stale_receipt(self):
        from vtx.cache.policy import CachePolicy
        from vtx.pipeline import range as ranges
        self.c.out.mkdir()
        receipt = self.c.out / 'receipt.json'
        plan = mock.Mock(nseg=1)
        plan._seg_content_key.return_value = 'fixture-key'
        with mock.patch.object(ranges, 'RenderPlan', return_value=plan), \
                mock.patch.object(ranges, 'prepare_layers', return_value=[]), \
                mock.patch.object(ranges, 'media_cache_valid', return_value=False), \
                mock.patch.object(ranges, 'pick_vcodec', return_value=([], 'fixture')), \
                mock.patch.object(ranges, 'mix_audio_wav') as mix, \
                mock.patch.object(ranges.fstxn, 'mkdir'), \
                mock.patch.object(ranges.fstxn, 'atomic_write'), \
                mock.patch.object(ranges.subprocess, 'run') as subprocess_run:
            for mode in ('off', 'core', 'aggressive'):
                self.c.cache_policy = CachePolicy(mode)
                for current in (True, False):
                    with self.subTest(mode=mode, current=current):
                        record = {'input_fingerprint': self.c.input_fingerprint if current else 'stale',
                                  'stages': {'audio': {'ok': True, 'gain_db': -3.25}}}
                        receipt.write_text(json.dumps(record), encoding='utf-8')
                        before = receipt.read_bytes()
                        mix.reset_mock()
                        with contextlib.redirect_stdout(io.StringIO()) as output:
                            ranges.run_range(self.c, 0, 30)
                        mix.assert_called_once_with(
                            self.c, self.c.cache / 'preview/range_0.000_1.000.wav',
                            win=(0, 30), gain_override=-3.25 if current else None)
                        self.assertEqual(receipt.read_bytes(), before)
                        self.assertIn('复用整片回执' if current else '区间增益改用窗口自测', output.getvalue())
            subprocess_run.assert_not_called()
        self.assertFalse(self.c.cache.exists(), 'mock producers must not create media/cache files')

    def test_range_concat_uses_clipped_frame_spans_without_formal_writes(self):
        from vtx.pipeline import range as ranges
        plan = mock.Mock(nseg=3)
        plan._seg_content_key.return_value = 'fixture-key'
        # An unprepared clip beyond the window still participates in cut selection.
        self.c.tl['video_tracks'] = [{'clips': [{'dst_in': 230, 'dur': 60}]}]
        with mock.patch.object(ranges, 'RenderPlan', return_value=plan), \
                mock.patch.object(ranges, 'choose_cutpoints', return_value=[112, 225]) as cuts, \
                mock.patch.object(ranges, 'prepare_layers', return_value=[]), \
                mock.patch.object(ranges, 'media_cache_valid', return_value=False), \
                mock.patch.object(ranges, 'pick_vcodec', return_value=([], 'fixture')), \
                mock.patch.object(ranges, 'mix_audio_wav'), \
                mock.patch.object(ranges.fstxn, 'mkdir'), \
                mock.patch.object(ranges.fstxn, 'atomic_write') as write, \
                mock.patch.object(ranges.subprocess, 'run') as probe, \
                contextlib.redirect_stdout(io.StringIO()):
            ranges.run_range(self.c, 110, 230)
        cuts.assert_called_once_with([(230, 290)], self.c.dur, plan.nseg)
        write.assert_called_once()
        path, manifest = write.call_args.args
        self.assertEqual(path.parent, self.c.cache / 'preview')
        self.assertEqual(manifest.splitlines()[1::2],
                         ['duration 0.066667', 'duration 3.766666', 'duration 0.166667'])
        self.assertEqual([(call.kwargs['a'], call.kwargs['b'])
                          for call in plan.composite.call_args_list],
                         [(110, 112), (112, 225), (225, 230)])
        concat = self.c.run.call_args_list[0].args[0]
        self.assertEqual(concat[-3:-1], ['-c', 'copy'])
        probe.assert_not_called()
        self.assertFalse(self.c.cache.exists())
        self.assertFalse(self.c.out.exists())

    def test_off_gfx_rejected_before_preencoding_with_actionable_guidance(self):
        from vtx.cache.policy import CachePolicy
        from vtx.pipeline import range as ranges
        self.c.dry = False
        self.c.cache_policy = CachePolicy('off')
        self.c.tl['video_tracks'] = [{'kind': 'gfx', 'clips': [
            {'src': 'page.html', 'dst_in': 0, 'dur': 30}]}]
        with mock.patch.object(ranges, 'prepare_layers') as prepare, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as failure:
                ranges.run_range(self.c, 0, 30)
        self.assertEqual(failure.exception.code, 1)
        prepare.assert_not_called()
        self.c.run.assert_not_called()
        message = output.getvalue()
        for guidance in ('off', '不启动图形渲染器', 'core/aggressive', 'gfx', 'all'):
            self.assertIn(guidance, message)
        self.assertFalse(self.c.cache.exists())
        self.assertFalse(self.c.out.exists())


@unittest.skipUnless(os.environ.get("VTX_RANGE_INTEG") == "1",
                     "可选集成测试：VTX_RANGE_INTEG=1 且需 ffmpeg（真实渲染，含gfx则需 Chrome）")
class TestRangeIntegration(unittest.TestCase):
    """小型合成 timeline 实跑：无 gfx / 2 轨 / 有音频与字幕，验证产物落点、
    含音轨、帧数精确、不写 out/ 与 receipt。"""

    def setUp(self):
        from unittest.mock import patch
        directory = tempfile.TemporaryDirectory(prefix='vtx-range-cache-config-')
        self.addCleanup(directory.cleanup)
        config = Path(directory.name) / 'config.json'
        # This fixture audits retained intermediate WAV samples as well as the
        # delivery; preserve its full PCM assertions under explicit retention.
        config.write_text('{"cache":{"mode":"aggressive"}}')
        settings = patch.dict(os.environ, {'VTX_CONFIG': str(config)})
        settings.start()
        self.addCleanup(settings.stop)

    def test_irregular_medium_b_frame_concat_keeps_exact_pts(self):
        """Small clock probe; canonical 1080p acceptance remains separate."""
        from vtx.pipeline.composite import _concat_manifest
        for fps in (Fraction(30), Fraction(30000, 1001), Fraction(120000, 1001)):
            with self.subTest(fps=fps), tempfile.TemporaryDirectory(prefix="vtx-concat-clock-") as directory:
                root = Path(directory)
                edges = [0, 112, 225, 338, 450, 562, 675, 788, 900]
                segments = []
                for n, (a, b) in enumerate(zip(edges, edges[1:])):
                    path = root / f"segment{n}.mp4"
                    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        f'color=black:s=160x90:r={fps}', '-frames:v', str(b - a),
                        '-c:v', 'libx264', '-preset', 'medium', '-threads', '1',
                        '-pix_fmt', 'yuv420p', str(path)], check=True, capture_output=True, timeout=60)
                    segments.append((path, a, b))
                manifest = root / 'list.txt'
                manifest.write_text(_concat_manifest(segments, fps), encoding='utf-8')
                movie = root / 'concat.mp4'
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0',
                    '-i', str(manifest), '-c', 'copy', str(movie)],
                    check=True, capture_output=True, timeout=60)
                probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                    '-show_streams', '-show_frames', '-of', 'json', str(movie)],
                    check=True, capture_output=True, timeout=60)
                metadata = json.loads(probe.stdout)
                stream = metadata['streams'][0]
                time_base = Fraction(stream['time_base'])
                self.assertGreater(stream['has_b_frames'], 0)
                self.assertEqual(int(stream['nb_frames']), 900)
                self.assertEqual(Fraction(stream['avg_frame_rate']), fps)
                self.assertEqual(int(stream['duration_ts']) * time_base, 900 / fps)
                self.assertEqual([int(frame['pts']) * time_base for frame in metadata['frames']],
                                 [n / fps for n in range(900)])

    def test_silent_gap_window_renders_exact_audio_without_formal_writes(self):
        import math
        import struct
        import wave
        from vtx.pipeline.infra import Ctx
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'voice.wav'
            with wave.open(str(source), 'wb') as audio:
                audio.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b''.join(struct.pack('<hh', value, value)
                                  for i in range(48000)
                                  for value in [round(3000 * math.sin(2 * math.pi * 440 * i / 48000))]))
            tl = TestCliGuards._min_tl()
            tl['sources'] = [{'id': 'voice', 'path': source.name}]
            tl['audio_tracks'] = [{'name': 'voice', 'events': [
                {'src': 'voice', 'in': 0, 'out': 30, 'volume': 1}]}]
            timeline = root / 'timeline.json'
            timeline.write_text(json.dumps(tl), encoding='utf-8')
            source_before, timeline_before = source.read_bytes(), timeline.read_bytes()
            for has_receipt in (False, True):
                if has_receipt:
                    (root / 'out').mkdir()
                    receipt = {'input_fingerprint': Ctx(timeline).input_fingerprint,
                               'stages': {'audio': {'ok': True, 'gain_db': 3.25}}}
                    (root / 'out' / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
                    (root / 'out' / 'audio.wav').write_bytes(b'keep formal audio')
                formal_before = {p.name: p.read_bytes() for p in (root / 'out').glob('*')}
                for end in (151, 181):
                    with self.subTest(has_receipt=has_receipt, end=end):
                        # Both windows lie beyond the only event and its 60-frame
                        # selection margin; no prepared source WAV is needed.
                        result = subprocess.run(
                            [sys.executable, str(REPO / 'vtx' / 'vtx.py'), '-t', str(timeline),
                             'video', '--draft', '--range', '150f', f'{end}f'],
                            capture_output=True, text=True, cwd=str(REPO))
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        stem = f'range_5.000_{range_label(end, FPS)}'
                        preview = root / 'cache' / 'preview'
                        self.assertTrue((preview / f'{stem}.mp4').is_file())
                        with wave.open(str(preview / f'{stem}.wav'), 'rb') as audio:
                            self.assertEqual((audio.getnchannels(), audio.getsampwidth(),
                                              audio.getframerate(), audio.getnframes()),
                                             (2, 2, 48000, (end - 150) * 1600))
                            self.assertEqual(audio.readframes(audio.getnframes()),
                                             bytes((end - 150) * 1600 * 4))
                        self.assertEqual({p.name: p.read_bytes() for p in (root / 'out').glob('*')},
                                         formal_before)
                        self.assertFalse((root / 'cache' / 'audio').exists())
                        self.assertEqual(source.read_bytes(), source_before)
                        self.assertEqual(timeline.read_bytes(), timeline_before)

    def _assert_short_ntsc_range_keeps_all_video_frames_when_audio_rounds_down(self):
        import wave
        from vtx.pipeline.infra import Ctx
        from vtx.pipeline.range import run_range
        with tempfile.TemporaryDirectory(prefix='vtx-range-ntsc-tail-') as directory:
            root = Path(directory)
            source = root / 'tone.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                'sine=frequency=440:sample_rate=48000:duration=1', '-ac', '2',
                '-c:a', 'pcm_s16le', str(source)], check=True, capture_output=True, timeout=60)
            timeline = root / 'timeline.json'
            timeline.write_text(json.dumps({'version': 2, 'project': {'fps': 29.97,
                'width': 320, 'height': 180, 'duration_frames': 29},
                'sources': [{'id': 'tone', 'path': source.name}], 'video_tracks': [],
                'subtitles': [], 'audio_tracks': [{'name': 'voice', 'events': [
                    {'src': 'tone', 'in': 0, 'out': 29, 'volume': 1}]}]}))
            c = Ctx(timeline)
            c.range_window = (0, 7)
            with mock.patch.dict(os.environ, {'VTX_NO_NVENC': '1', 'VTX_SEG_NVENC': '0'}):
                output = run_range(c, 0, 7)
            # 7 * 48000 / (30000/1001) = 11211.2 samples: rounded audio is
            # shorter by 0.2 sample. Mux -shortest used to discard two video
            # packets despite the already exact seven-frame range segment.
            rate = Fraction(30000, 1001)
            with wave.open(str(output.with_suffix('.wav'))) as audio:
                self.assertEqual(audio.getnframes(), 11211)
                self.assertEqual(audio.getframerate(), 48000)
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams',
                '-show_frames', '-of', 'json', str(output)],
                check=True, capture_output=True, timeout=60)
            metadata = json.loads(probe.stdout)
            video = next(stream for stream in metadata['streams'] if stream['codec_type'] == 'video')
            self.assertTrue(any(stream['codec_type'] == 'audio' for stream in metadata['streams']))
            self.assertEqual(int(video['nb_frames']), 7)
            self.assertEqual(Fraction(video['avg_frame_rate']), rate)
            self.assertEqual([int(frame['pts']) * Fraction(video['time_base'])
                              for frame in metadata['frames'] if frame['media_type'] == 'video'],
                             [frame / rate for frame in range(7)])
            self.assertEqual(int(video['duration_ts']) * Fraction(video['time_base']), 7 / rate)
            self.assertFalse((root / 'out').exists(), 'range must not publish formal outputs')

    def test_range_render(self):
        self._assert_short_ntsc_range_keeps_all_video_frames_when_audio_rounds_down()
        from vtx.pipeline.range import run_range  # noqa: F401  （经 CLI 子进程实跑，见下）
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            assets = root / "assets"; assets.mkdir()
            import shutil
            ff = shutil.which("ffmpeg")
            self.assertIsNotNone(ff)
            for name, hz in (("a", 330), ("b", 550)):
                subprocess.run([ff, "-v", "error", "-y",
                                "-f", "lavfi", "-i", f"color=c=gray:s=320x180:r=30:d=10",
                                "-f", "lavfi", "-i", f"sine=frequency={hz}:duration=10",
                                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                                "-c:a", "aac", "-shortest", str(assets / f"{name}.mp4")],
                               check=True)
            tl = {"version": 2,
                  "project": {"fps": 30, "width": 320, "height": 180, "duration_frames": 300},
                  "sources": [{"id": n, "path": str(assets / f"{n}.mp4")} for n in ("a", "b")],
                  "video_tracks": [
                      {"name": "base", "kind": "aroll", "clips": [
                          {"src": "a", "src_in": 0, "src_out": 150, "dst_in": 0, "dur": 150},
                          {"src": "b", "src_in": 0, "src_out": 150, "dst_in": 150, "dur": 150}]},
                      {"name": "pip", "kind": "pip", "clips": [
                          {"src": "b", "src_in": 0, "src_out": 60, "dst_in": 60, "dur": 60,
                           "scale": 0.3, "x": 10, "y": 10}]}],
                  "subtitles": [{"in": 30, "out": 90, "text": "区间渲染字幕"}],
                  "audio_tracks": [
                      {"name": "voice", "events": [
                          {"src": "a", "in": 0, "out": 150, "volume": 0.5},
                          {"src": "b", "in": 150, "out": 300, "volume": 0.5}]},
                      {"name": "bed", "duck": True, "duck_by": ["voice"], "events": [
                          {"src": "b", "in": 0, "out": 300, "loop": True, "volume": 0.1}]}]}
            proj = root / "proj"; proj.mkdir()
            (proj / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
            r = subprocess.run(
                ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(proj / "timeline.json"), "video", "--draft", "--range", "2", "5"],
                capture_output=True, text=True, cwd=str(REPO))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = proj / "cache" / "preview" / "range_2.000_5.000.mp4"
            self.assertTrue(out.exists(), "产物应落在 cache/preview/")
            # [60,150) uses base a and pip b. The next base clip starts at
            # exactly 150: do not produce its full-screen intermediate.
            self.assertEqual(len(list((proj / 'cache' / 'seg').glob('*.mp4'))), 2)
            # 不写 out/ 与 receipt
            self.assertFalse((proj / "out" / "receipt.json").exists())
            self.assertFalse((proj / "out" / "draft.mp4").exists())
            pr = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
                                capture_output=True, text=True)
            streams = json.loads(pr.stdout)["streams"]
            nb = next(s for s in streams if s["codec_type"] == "video")["nb_frames"]
            self.assertEqual(int(nb), 90)   # [60,150)=90 帧
            self.assertTrue(any(s["codec_type"] == "audio" for s in streams), "必须含音轨")


if __name__ == "__main__":
    unittest.main()
