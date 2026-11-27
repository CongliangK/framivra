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

from tests._support import cli_python
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


class TestNativeConcatClock(unittest.TestCase):
    def test_rejects_different_time_bases_even_with_the_same_decode_delay(self):
        from types import SimpleNamespace
        from vtx.pipeline.range import _native_concat_clock

        # Both one-frame segments are locally exact CFR with zero delay.
        probes = [SimpleNamespace(stderr='', stdout=json.dumps({
            'streams': [{'codec_name': 'h264', 'time_base': f'1/{den}'}],
            'packets': [{'pts': 0, 'dts': 0, 'duration': duration}],
        })) for den, duration in ((30000, 1001), (60000, 2002))]
        ctx = SimpleNamespace(run=mock.Mock(side_effect=probes), frame_rate=Fraction(30000, 1001))
        with self.assertRaisesRegex(ValueError, 'same time_base'):
            _native_concat_clock(ctx, ('a.mp4', 'b.mp4'), [(0, 1), (1, 2)])


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

    def test_declared_media_stem_preserves_existing_matches_and_track_order(self):
        tl = {**TL, 'sources': [
            {'id': 'a.mp4', 'path': '素材 #/take 01.mp4'},
            {'id': 'b.mp4', 'path': '素材 #/page.mp4'},
            {'id': 'cam.mp4', 'path': 'alt/take 01.mov'},
        ]}
        self.assertEqual(window_from_clip(tl, 'take 01'), (0, 180))
        self.assertEqual(window_from_clip(tl, 'b'), (180, 360))
        # A newly recognized media stem must not redirect an existing command.
        self.assertEqual(window_from_clip(tl, 'page'), (30, 90))

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
            [*cli_python(), "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
             "-t", str(REPO / "tests" / "fixtures_nonexist.json"), "video", *extra],
            capture_output=True, encoding="utf-8", cwd=REPO, timeout=120)

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
                [*cli_python(), "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(p), "video", "--draft", "--track", "ghost"],
                capture_output=True, encoding="utf-8", cwd=REPO, timeout=120)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("空轨", r.stdout)
            self.assertIn("ghost", r.stdout)

    def test_dry_run_clip_selects_declared_media_stem(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / '时间线.json'
            config = root / 'config.json'
            config.write_bytes(b'{}')
            # Lexical selection does not need the media file or an FFmpeg probe.
            media = str(Path('素材 #') / '片段 01.mp4')
            tl = self._min_tl()
            tl['sources'] = [{'id': 'camera-main', 'path': media}]
            tl['video_tracks'] = [{'name': 'base', 'kind': 'aroll', 'clips': [
                {'src': 'camera-main', 'src_in': 0, 'src_out': 30, 'dst_in': 24, 'dur': 30}]}]
            authored = json.dumps(tl, ensure_ascii=False).encode('utf-8')
            path.write_bytes(authored)
            env = dict(os.environ, VTX_CONFIG=str(config))
            result = subprocess.run(
                [*cli_python(), '-X', 'utf8', str(REPO / 'vtx' / 'vtx.py'), '-t', str(path),
                 'video', '--draft', '--clip', '片段 01', '--dry-run'],
                capture_output=True, encoding='utf-8', cwd=REPO, env=env, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('[dry-run] video draft 区间 [24,54)', result.stdout)
            self.assertEqual(path.read_bytes(), authored)
            self.assertEqual(config.read_bytes(), b'{}')
            self.assertEqual(set(root.iterdir()), {path, config})

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
                [*cli_python(), "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(p), "video", "--draft", "--range", "abc", "6"],
                capture_output=True, encoding="utf-8", cwd=REPO, timeout=120)
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
                [*cli_python(), "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(p), "video", "--draft", "--range", "0", "6", "--dry-run"],
                capture_output=True, encoding="utf-8", cwd=REPO, timeout=120)
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
            for start, end in [('0', '6'), ('2', '3')]:
                # Draft must still qualify a cold page even when it ends
                # exactly at the requested start (no active overlap).
                with self.subTest(start=start):
                    r = subprocess.run(
                        [*cli_python(), "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                         "-t", str(p), "video", "--draft", "--range", start, end],
                        capture_output=True, encoding="utf-8", cwd=REPO, timeout=120)
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

    def test_native_window_rejects_unqualified_graphics_and_nonisolated_workspace(self):
        from tests.gfx._environment import bind_environment
        from vtx.cache.policy import CachePolicy
        from vtx.pipeline import range as ranges
        from vtx.pipeline.infra import Ctx
        bind_environment(self)
        (self.root / 'cold.html').write_text('<html><body>cold page</body></html>')
        timeline = self.root / 'timeline.json'
        tl = TestCliGuards._min_tl(video_tracks=[{'name': 'gfx', 'kind': 'gfx', 'clips': [
            {'src': 'cold.html', 'dst_in': 6, 'dur': 4}]}])
        tl['project'].update(width=1920, height=1080)
        timeline.write_text(json.dumps(tl))
        self.c = Ctx(timeline)
        self.c.run = mock.Mock()
        self.c.cache_policy = CachePolicy('core')
        workspace = self.c.cache / 'preview' / 'request'
        workspace.mkdir(parents=True)
        (workspace / 'foreign').touch()
        with mock.patch.object(ranges, 'prepare_layers') as prepare:
            with mock.patch.dict(os.environ, {'FFREPORT': 'file=unexpected.log'}), \
                    self.assertRaisesRegex(ValueError, 'refuses FFREPORT'):
                ranges.render_1080p_window(self.c, 0, 6, workspace=workspace)
            cold = workspace.with_name('cold')
            cold.mkdir()
            with self.assertRaisesRegex(ValueError, 'no scoped SVG pack'):
                ranges.render_1080p_window(self.c, 5, 7, workspace=cold)
            self.assertEqual(list(cold.iterdir()), [])
            with self.assertRaisesRegex(ValueError, 'integer frame interval'):
                ranges.render_1080p_window(self.c, False, 6, workspace=workspace)
            with self.assertRaisesRegex(ValueError, 'empty physical workspace'):
                ranges.render_1080p_window(self.c, 0, 6, workspace=workspace)
            self.c.out.mkdir()
            with self.assertRaisesRegex(ValueError, 'empty physical workspace'):
                ranges.render_1080p_window(self.c, 0, 6, workspace=self.c.out)
        prepare.assert_not_called()
        self.c.run.assert_not_called()
        self.assertEqual(list(self.c.out.iterdir()), [])

    @unittest.skipUnless(sys.platform in ('linux', 'darwin'), 'requires the anchored pack reader')
    def test_native_window_stages_packed_intersections_and_rechecks_before_return(self):
        import struct
        import zlib
        from tests.gfx._environment import bind_environment
        from vtx.cache.hygiene import gfx_dir
        from vtx.cache.policy import CachePolicy
        from vtx.cache.session import pack_path
        from vtx.pipeline import range as ranges
        from vtx.pipeline.infra import Ctx
        from vtx.pipeline.stages import validate_core
        from vtx.render import gfx_cli, gfx_environment, gfx_pack, gfx_packed_reader

        def png(red):
            def chunk(tag, data):
                return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
            row = b'\0' + bytes((red, 42, 80, 128)) * 1920
            return (gfx_cli.PNG_SIGNATURE + chunk(b'IHDR', struct.pack('>IIBBBBB', 1920, 1080, 8, 6, 0, 0, 0))
                    + chunk(b'IDAT', zlib.compress(row * 1080)) + chunk(b'IEND', b''))

        bind_environment(self)
        environment = mock.patch.dict(os.environ, VTX_GFX='cdp', VTX_GFX_CACHE_NS='',
                                      VTX_VIDEO_SEGMENTS='1', VTX_SEG_NVENC='0')
        environment.start()
        self.addCleanup(environment.stop)
        for name in ('lower', 'upper', 'cold'):
            (self.root / f'{name}.html').write_text(f'<body>{name}</body>')
        tracks = [{'kind': 'gfx', 'name': 'lower', 'clips': [
            {'src': 'lower.html', 'dur': 4, 'dst_in': 6.0, 'opacity': .7},
            {'src': 'cold.html', 'dur': 4, 'dst_in': 10}]},
            {'kind': 'gfx', 'name': 'upper', 'clips': [
                {'src': 'upper.html', 'dur': 4, 'dst_in': 8, 'fade_in_frames': 2}]}]
        tl = TestCliGuards._min_tl(video_tracks=tracks)
        tl['project'].update(width=1920, height=1080)
        timeline = self.root / 'timeline.json'
        timeline.write_text(json.dumps(tl))
        c = Ctx(timeline)
        c.cache_policy = CachePolicy('core')
        c.run = mock.Mock()
        self.assertEqual(validate_core(c), [])
        lower, upper = (t['clips'][0] for t in c.tl['video_tracks'])
        self.assertIs(type(lower['dst_in']), float)
        expected, packs = {}, []
        for clip, seed in ((lower, 20), (upper, 40)):
            directory = gfx_dir(c, clip)
            directory.mkdir(parents=True)
            for i in range(4):
                expected[clip['src'], i] = data = png(seed + i)
                (directory / f'f{i:04d}.png').write_bytes(data)
            gfx_cli.complete_page(directory, 4, directory.name, c.w, c.h,
                                  environment=gfx_environment.certificate(c), verify_inputs=lambda: None)
            record = json.loads((directory / 'complete.json').read_bytes())
            archive = pack_path(c, clip)
            archive.parent.mkdir(parents=True, exist_ok=True)
            gfx_pack.pack_page(directory, archive, root=c.root, expected_record=record)
            for member in directory.iterdir():
                member.unlink()
            packs.append(archive)

        consumed, cancelled = [], []
        def composite(plan, layers, _out, _subs, *, a, b, sub_file):
            self.assertEqual((a, b), (7, 10))
            self.assertEqual([layer['cl'] for layer in layers], [lower, upper])
            self.assertIs(type(layers[0]['cl']['dst_in']), int)
            for layer, (first, stop) in zip(layers, ((1, 4), (0, 2))):
                source = layer['source']
                self.assertEqual(layer['source_stop'], stop)
                self.assertEqual(plan._layer_input_args(layer, a)[5], str(first))
                self.assertEqual(sorted(p.name for p in source.directory.iterdir()),
                                 [f'f{i:04d}.png' for i in range(first, stop)])
                for i in range(first, stop):
                    self.assertEqual(source.frame_path(i).read_bytes(), expected[layer['cl']['src'], i])
            consumed.append(True)

        def check_cancel():
            if cancelled:
                raise KeyboardInterrupt

        def render(case):
            work = c.cache / 'preview' / case
            work.mkdir(parents=True)
            # Legacy multi-layer fixtures exercise only the transplanted core,
            # not the restricted SVG-only launcher or its production evidence.
            return ranges._render_native_window(c, 7, 10, workspace=work,
                                                check_cancel=check_cancel, expected_svg_pack=None)

        def readback(*_args, **_kwargs):
            if case == 'cancelled':
                cancelled.append(True)
            elif case == 'changed-pack':
                with packs[-1].open('ab') as stream:
                    stream.write(b'late change')
            return mock.Mock(stdout='3\n')

        c._preview_window_tasks = mock.Mock()
        c.run.side_effect = lambda cmd, **kwargs: readback() if cmd[0] == 'ffprobe' else mock.Mock()
        real_read = gfx_packed_reader.read_frames
        with mock.patch.object(ranges.RenderPlan, 'composite', composite), \
                mock.patch.object(ranges, 'mix_audio_wav'), \
                mock.patch.object(ranges, 'pick_vcodec', return_value=([], 'mock')), \
                mock.patch.object(ranges, 'media_cache_valid', side_effect=lambda p, **_: p.name.startswith('native_')), \
                mock.patch.object(ranges.subprocess, 'run', side_effect=readback), \
                mock.patch.object(gfx_packed_reader, 'read_frames', wraps=gfx_packed_reader.read_frames) as read:
            case = 'warm'
            self.assertEqual(render(case).parent.name, case)
            self.assertEqual([call.args[1:] for call in read.call_args_list],
                             [(lower, 1, 4), (upper, 0, 2)])
            read_ctx = read.call_args.args[0]
            self.assertIs(read.call_args_list[0].args[0], read_ctx)
            self.assertIsNot(read_ctx, c)
            self.assertIs(read_ctx.tl, c.tl)
            self.assertEqual(read_ctx.input_fingerprint, c.input_fingerprint)
            self.assertEqual(read_ctx.cache, c.cache)
            self.assertTrue(read_ctx._core_only_preview)
            self.assertIsNone(read_ctx._svg_consumer_sources)
            self.assertIsNot(read_ctx.execution, c.execution)
            self.assertEqual(consumed, [True])
            case = 'over-budget'
            with mock.patch.object(gfx_packed_reader, '_MAX_BYTES',
                                   sum(len(expected['lower.html', i]) for i in range(1, 4))
                                   + len(expected['upper.html', 0])), \
                    self.assertRaisesRegex(ValueError, 'byte limit'):
                render(case)
            self.assertEqual(consumed, [True])
            case = 'cancelled'
            with self.assertRaises(KeyboardInterrupt):
                render(case)
            cancelled.clear()
            case = 'saved-during-gfx-recheck'
            original = timeline.read_bytes()

            def read_while_saving(ctx, clip, start, stop, **kwargs):
                if clip is upper:
                    keep = kwargs['keep_recheck']
                    def keep_with_save(recheck):
                        def recheck_and_save():
                            recheck()
                            timeline.write_bytes(original + b'\n')
                        keep(recheck_and_save)
                    kwargs['keep_recheck'] = keep_with_save
                return real_read(ctx, clip, start, stop, **kwargs)

            with mock.patch.object(gfx_packed_reader, 'read_frames', side_effect=read_while_saving), \
                    self.assertRaises(SystemExit) as stale:
                render(case)
            self.assertEqual(stale.exception.code, 1)
            timeline.write_bytes(original)
            case = 'changed-pack'
            with self.assertRaisesRegex(ValueError, 'packed gfx changed'):
                render(case)
        self.assertFalse(c.out.exists())
        self.assertTrue(all(not any(gfx_dir(c, clip).iterdir()) for clip in (lower, upper)))


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

    def test_native_faded_pip_slice_matches_full_prores_and_keeps_window_clocks(self):
        import resource
        import wave
        from vtx.cache.hygiene import seg_key
        from vtx.pipeline.composite import RenderPlan, prepare_layers
        from vtx.pipeline.constraints import frame_sample
        from vtx.pipeline.infra import Ctx
        from vtx.pipeline.range import render_1080p_window, _NativeWindowPlan
        from vtx.timeline_preview_window import _probe_facts

        def ff(*args):
            return subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', *args], check=True,
                                  capture_output=True, timeout=60).stdout

        def pixels(path, fmt):
            # Use identical decode/colour-conversion on both paths; hash each
            # complete frame including its decoded native alpha plane.
            raw = ff('-i', str(path), '-map', '0:v:0', '-pix_fmt', fmt, '-threads:v', '1', '-f', 'framehash', '-')
            return [row.split(b',')[-1].strip() for row in raw.splitlines() if not row.startswith(b'#')]

        def frame_pts(path):
            result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames',
                                     '-show_streams', '-of', 'json', str(path)], check=True,
                                    capture_output=True, timeout=30)
            probe = json.loads(result.stdout)
            tb = Fraction(probe['streams'][0]['time_base'])
            return [frame['pts'] * tb for frame in probe['frames']]

        rate = Fraction(30000, 1001)
        with tempfile.TemporaryDirectory(prefix='vtx-native-pip-range-') as directory:
            root = Path(directory)
            # Deliberately use different input and timeline frame rates, source
            # trim, native alpha, partial opacity, even rounded scale and odd x/y.
            ff('-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=25:d=4,format=yuva444p10le',
               '-c:v', 'prores_ks', '-profile:v', '4', '-threads:v', '1', str(root / 'pip.mov'))
            clip = {'src': 'pip', 'src_in': 7, 'src_out': 43, 'dst_in': 51, 'dur': 36,
                    'scale': 0.53, 'x': 17, 'y': 13, 'opacity': 0.78,
                    'fade_in_frames': 8, 'fade_out_frames': 8}
            path = root / 'timeline.json'
            path.write_text(json.dumps({**TestCliGuards._min_tl(),
                'project': {'fps': str(rate), 'width': 320, 'height': 180, 'duration_frames': 95},
                'sources': [{'id': 'pip', 'path': 'pip.mov'}],
                'video_tracks': [{'name': 'pip', 'kind': 'pip', 'clips': [clip]}],
                'subtitles': [{'in': 51, 'out': 87, 'text': 'Synthetic PiP'}]}), encoding='utf-8')
            c = Ctx(path)
            def serial(ctx, jobs, _what):
                for _label, argv, gpu in jobs:
                    self.assertFalse(gpu)
                    ctx.run(argv)
            with mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0', 'VTX_VIDEO_SEGMENTS': '1'}):
                whole, = prepare_layers(c, run_tasks=serial)
                legacy_key = seg_key(c, clip, 0, 0, True, True, 'prores')
                self.assertEqual(whole['seg'].name, legacy_key + '.mov')
                self.assertEqual(len(frame_pts(whole['seg'])), 36)
                full_hashes = pixels(whole['seg'], 'yuva444p12le')
                normal = RenderPlan(c=c, draft=False, f=1, W=c.w, H=c.h, layers=[whole])
                test_dir = c.cache / 'preview'
                test_dir.mkdir(parents=True)
                sizes, sliced_hashes = [], {}
                for a, b in ((51, 53), (53, 55), (51, 55), (58, 60), (59, 61), (72, 74), (78, 80), (85, 87)):
                    with self.subTest(window=(a, b)):
                        sliced, = prepare_layers(c, run_tasks=serial, native_window=(a, b), staged_gfx={})
                        self.assertEqual(sliced['cl'], clip)
                        self.assertEqual(sliced['native_seg_span'], (a, b))
                        self.assertEqual((sliced['x'], sliced['y'], sliced['w2'], sliced['h2']), (17, 13, 168, 94))
                        self.assertNotEqual(sliced['seg'].name, whole['seg'].name)
                        self.assertEqual(frame_pts(sliced['seg']), [i / rate for i in range(b - a)])
                        ordinal = a - clip['dst_in']
                        sliced_hashes[a, b] = pixels(sliced['seg'], 'yuva444p12le')
                        self.assertEqual(sliced_hashes[a, b], full_hashes[ordinal:ordinal + b - a])
                        if b - a == 2:
                            sizes.append(sliced['seg'].stat().st_size)
                        # Real overlay and ASS burn, with identical native range
                        # inputs except for the private pre-encode origin.
                        ass = test_dir / f'{a}-{b}.ass'
                        normal.write_seg_ass(ass, a, b)
                        reference, actual = (test_dir / f'{name}-{a}-{b}.mp4' for name in ('full', 'native'))
                        normal.composite([whole], reference, True, a=a, b=b, sub_file=ass)
                        _NativeWindowPlan(c=c, draft=False, f=1, W=c.w, H=c.h, layers=[sliced]).composite(
                            [sliced], actual, True, a=a, b=b, sub_file=ass)
                        self.assertEqual(pixels(actual, 'yuv420p'), pixels(reference, 'yuv420p'))
                        self.assertEqual(frame_pts(actual), [i / rate for i in range(b - a)])
                self.assertEqual(sliced_hashes[51, 53] + sliced_hashes[53, 55], sliced_hashes[51, 55])
                self.assertEqual([p.name for p in c.cache.joinpath('seg').glob(f'{legacy_key}*') if p.suffix == '.mov'], [whole['seg'].name])
                print(f'native faded PiP two-frame ProRes bytes={sizes} full={whole["seg"].stat().st_size}')

            # Actual wrapper with audio: choose endpoints whose absolute NTSC
            # rounding differs from a reset-at-zero duration (3204 vs 3203).
            content = json.loads(path.read_text())
            content['project'].update(width=1920, height=1080)
            content['subtitles'] = [{'in': 58, 'out': 64, 'text': 'Synthetic PiP'}]
            path.write_text(json.dumps(content), encoding='utf-8')
            c = Ctx(path)
            a, b = 59, 61
            self.assertEqual(frame_sample(b, rate) - frame_sample(a, rate), 3204)
            workspace = c.cache / 'preview' / 'isolated-pip-window'
            workspace.mkdir()
            soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
            limit = 32 << 20 if soft == resource.RLIM_INFINITY else min(soft, 32 << 20)
            with mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0', 'VTX_VIDEO_SEGMENTS': '1'}):
                resource.setrlimit(resource.RLIMIT_FSIZE, (limit, hard))
                try:
                    out = render_1080p_window(c, a, b, workspace=workspace)
                finally:
                    resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
            part, = (workspace / 'work/seg').glob('native-alpha-v1-*.mov')
            self.assertEqual(frame_pts(part), [0, 1 / rate])
            self.assertEqual(frame_pts(out), [0, 1 / rate])
            info = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_packets', '-of', 'json', str(out)],
                                  check=True, capture_output=True, timeout=30)
            _probe_facts(json.loads(info.stdout), rate, b - a, 3204)
            with wave.open(str(workspace / 'work/preview' / out.with_suffix('.wav').name), 'rb') as pcm:
                self.assertEqual((pcm.getframerate(), pcm.getnframes()), (48000, 3204))
            print(f'native faded PiP two-frame quota-bound 1080p window intermediate={part.stat().st_size} bytes')

    def test_native_alpha_slice_fits_original_file_limit_when_full_clip_does_not(self):
        import resource
        from vtx.pipeline.composite import prepare_layers
        from vtx.pipeline.infra import Ctx

        with tempfile.TemporaryDirectory(prefix='vtx-native-pip-cap-') as directory:
            root = Path(directory)
            source = root / 'pip.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
                'testsrc2=s=1280x720:r=30,noise=alls=38:allf=t+u:all_seed=77',
                '-frames:v', '108', '-c:v', 'libx264', '-crf', '20', '-preset', 'ultrafast',
                '-threads:v', '1', str(source)], check=True, capture_output=True, timeout=60)
            clip = {'src': 'pip', 'src_in': 3, 'src_out': 103, 'dst_in': 31, 'dur': 100,
                    'x': -13, 'y': 5, 'scale': 1, 'fade_in_frames': 12, 'fade_out_frames': 12}
            timeline = root / 'timeline.json'
            timeline.write_text(json.dumps({**TestCliGuards._min_tl(),
                'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 135},
                'sources': [{'id': 'pip', 'path': source.name}],
                'video_tracks': [{'name': 'pip', 'kind': 'pip', 'clips': [clip]}]}), encoding='utf-8')
            c = Ctx(timeline)
            soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
            limit = 32 << 20
            with mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0'}):
                # The reference is produced once unrestricted in fixture scratch
                # solely to measure why the unmodified worker must refuse it.
                whole, = prepare_layers(c)
                self.assertGreater(whole['seg'].stat().st_size, limit)
                resource.setrlimit(resource.RLIMIT_FSIZE, (min(soft, limit) if soft != resource.RLIM_INFINITY else limit, hard))
                try:
                    two, = prepare_layers(c, native_window=(73, 75), staged_gfx={})
                finally:
                    resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
            self.assertLess(two['seg'].stat().st_size, limit)
            print(f'quota stress full PiP={whole["seg"].stat().st_size} bytes; two-frame PiP={two["seg"].stat().st_size} bytes')

    def test_native_mp4_preserves_short_window_presentation_clock(self):
        import wave
        from vtx.cache.recovery import completion_marker, media_cache_valid
        from vtx.pipeline.infra import Ctx
        from vtx.pipeline.range import render_1080p_window
        from vtx.pipeline.stages import validate_core
        from vtx.timeline_preview_window import _probe_facts

        def probe_packets(path):
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_packets',
                                    '-of', 'json', str(path)], check=True, capture_output=True, timeout=60)
            self.assertEqual(probe.stderr, b'')
            return json.loads(probe.stdout)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, wav_options in (('tone.wav', []), ('bwf.wav', ['-write_bext', '1'])):
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                                'aevalsrc=0.09*sin(2*PI*440*t)|0.03*sin(2*PI*660*t):s=48000:d=3',
                                '-c:a', 'pcm_s16le', *wav_options, str(root / name)],
                               check=True, capture_output=True, timeout=60)
            pipe = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(root / 'tone.wav'),
                                   '-f', 'wav', '-'], check=True, capture_output=True, timeout=60).stdout
            self.assertEqual(pipe[4:8], b'\xff' * 4)  # Stream-written RIFF has no finalized extent.
            (root / 'stream.wav').write_bytes(pipe)
            # Default millisecond edit lists presented 1584 and 3216 samples.
            # NTSC must use S(61)-S(59) = 97698-94494, not round(2*1601.6).
            ntsc = '30000/1001'
            mixed, concat_packets = {}, {}
            for fps, fa, fb, samples, name, force_extract, cut, mux_concat in (
                    (30, 60, 61, 1600, 'tone.wav', False, None, False),
                    (ntsc, 59, 61, 3204, 'tone.wav', False, None, False),
                    (ntsc, 59, 61, 3204, 'tone.wav', True, None, False),
                    (ntsc, 59, 61, 3204, 'bwf.wav', False, None, False),
                    (30, 60, 61, 1600, 'stream.wav', False, None, False),
                    (30, 13, 133, 192000, 'tone.wav', False, 131, True),
                    (ntsc, 13, 43, 48048, 'tone.wav', False, 41, False),
                    (ntsc, 13, 43, 48048, 'tone.wav', False, 41, True)):
                with self.subTest(fps=fps, window=(fa, fb), source=name,
                                  force_extract=force_extract, mux_concat=mux_concat):
                    direct = name == 'tone.wav' and not force_extract
                    timeline = root / 'timeline.json'
                    timeline.write_text(json.dumps({
                        **TestCliGuards._min_tl(),
                        'project': {'fps': fps, 'width': 1920, 'height': 1080,
                                    'duration_frames': 2 * cut if cut is not None else 300},
                        'sources': [{'id': 'tone', 'path': name}],
                        'audio_tracks': [{'name': 'voice', 'events': [
                            {'src': 'tone', 'src_in': 9, 'in': 0, 'out': 75, 'volume': 1}]}],
                    }), encoding='utf-8')
                    c = Ctx(timeline)
                    self.assertEqual(validate_core(c), [])
                    workspace = c.cache / 'preview' / f'clock-{fa}-{fb}-{name}-{force_extract}-{mux_concat}'
                    workspace.mkdir(parents=True)
                    fallback = (mock.patch('vtx.media.preview_pcm.qualify', return_value=None)
                                if force_extract else contextlib.nullcontext())
                    with mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0', 'VTX_VIDEO_SEGMENTS': '2'}), fallback:
                        output = render_1080p_window(c, fa, fb, workspace=workspace, _mux_concat=mux_concat)
                    segment_dir = workspace / 'work' / 'preview'
                    segments = sorted(segment_dir.glob('rseg_*.mp4'))
                    parts = [(fa, fb)] if cut is None else [(fa, cut), (cut, fb)]
                    self.assertEqual([p.name.split('_')[1:3] for p in segments],
                                     [[f'{a:06d}', f'{b:06d}'] for a, b in parts])
                    if cut is None:
                        self.assertFalse(list(segment_dir.glob('*.list.txt')))
                    else:
                        # Confirm the real long+2 encode exercises differing B-frame decode delays.
                        leading_dts = []
                        for segment in segments:
                            part = probe_packets(segment)
                            stream, = part['streams']
                            leading_dts.append(part['packets'][0]['dts'] * Fraction(stream['time_base']))
                        self.assertNotEqual(leading_dts[0], leading_dts[1])
                        self.assertTrue(all(media_cache_valid(p, frames=b - a)
                                            for p, (a, b) in zip(segments, parts)))
                        self.assertTrue(media_cache_valid(output, frames=fb - fa))
                        # Legacy deletes its intermediate MP4 but leaves its completion marker.
                        intermediate = segment_dir / output.with_suffix('.video.mp4').name
                        self.assertEqual(completion_marker(intermediate).exists(), not mux_concat)
                    with wave.open(str(segment_dir / output.with_suffix('.wav').name)) as pcm:
                        self.assertEqual((pcm.getframerate(), pcm.getnframes()), (48000, samples))
                        if fps == ntsc and name == 'tone.wav' and cut is None:
                            mixed[force_extract] = pcm.readframes(samples)
                    # Special BWF metadata and unknown-length pipe output still
                    # use ordinary extraction; direct PCM has no certificate.
                    audio_cache = workspace / 'work' / 'audio'
                    self.assertEqual(len(list(audio_cache.glob('*.wav'))), 0 if direct else 1)
                    self.assertEqual(len(list(audio_cache.glob('*.wav.complete.json'))), 0 if direct else 1)

                    info = probe_packets(output)
                    video, audio = info['streams']
                    rate = Fraction(fps)
                    vtb = Fraction(video['time_base'])
                    self.assertEqual((video['codec_name'], video['width'], video['height']), ('h264', 1920, 1080))
                    self.assertEqual((video['start_pts'], video['duration_ts'] * vtb), (0, (fb - fa) / rate))
                    video_packets = [p for p in info['packets'] if p['codec_type'] == 'video']
                    self.assertEqual(sorted(p['pts'] * vtb for p in video_packets), [i / rate for i in range(fb - fa)])
                    self.assertEqual([p['duration'] * vtb for p in video_packets], [1 / rate] * (fb - fa))
                    self.assertEqual((audio['codec_name'], audio['channels'], Fraction(audio['time_base']),
                                      audio['start_pts'], audio['duration_ts']), ('aac', 2, Fraction(1, 48000), 0, samples))
                    # Negative packets carry encoder priming, not presented audio;
                    # generic AAC decode can still return trailing padding.
                    cursor = 0
                    for packet in info['packets']:
                        if packet['codec_type'] == 'audio' and packet['pts'] >= 0:
                            self.assertEqual(packet['pts'], cursor)
                            self.assertGreater(packet['duration'], 0)
                            cursor += packet['duration']
                    self.assertEqual(cursor, samples)
                    if cut is not None:
                        _probe_facts(info, rate, fb - fa, samples, verify_dts=fps == 30)
                        if fps == 30:
                            # The 4s window has two different source segment decode delays;
                            # its final interleaved audio must not advance the video DTS index.
                            self.assertLess(video_packets[0]['dts'], 0)
                            self.assertNotEqual([p['pts'] for p in video_packets], sorted(p['pts'] for p in video_packets))
                            presented = [p for p in info['packets'] if p['codec_type'] == 'audio' and p['pts'] >= 0]
                            self.assertEqual((presented[-1]['pts'], presented[-1]['duration']), (191488, 512))
                            continue
                        # Compare packet order and decode/presentation clocks,
                        # not MP4 layout or file hashes across the two routes.
                        bases = {s['index']: Fraction(s['time_base']) for s in info['streams']}
                        concat_packets[mux_concat] = {
                            s['codec_type']: [tuple(p[k] * bases[s['index']] for k in ('pts', 'dts', 'duration'))
                                              for p in info['packets'] if p['stream_index'] == s['index']]
                            for s in info['streams']}
            self.assertEqual(concat_packets[False], concat_packets[True])
            # Direct mux must retain the old pre-audio transaction checkpoint.
            # Simulate a real save immediately after the segment clock probe.
            from vtx.pipeline import range as ranges
            seam_clock = ranges._native_concat_clock

            def save_after_clock(*args):
                clock = seam_clock(*args)
                timeline.write_bytes(timeline.read_bytes() + b'\n')
                return clock

            stale = c.cache / 'preview' / 'changed-after-clock'
            stale.mkdir()
            with mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0', 'VTX_VIDEO_SEGMENTS': '2'}), \
                    mock.patch.object(ranges, '_native_concat_clock', side_effect=save_after_clock), \
                    mock.patch.object(ranges, '_ensure_audio_wavs') as audio, self.assertRaises(SystemExit):
                render_1080p_window(c, fa, fb, workspace=stale, _mux_concat=True)
            audio.assert_not_called()
            self.assertEqual(mixed[False], mixed[True])
            self.assertTrue(any(mixed[False][i:i + 2] != mixed[False][i + 2:i + 4]
                                for i in range(0, len(mixed[False]), 4)))

    def test_program_pcm24_native_mp4_matches_whole_mix_at_short_ntsc_tail(self):
        import hashlib
        import math
        import wave
        from vtx.pipeline import preview_program_audio as program, range as ranges
        from vtx.pipeline.constraints import frame_sample
        from vtx.pipeline.infra import Ctx
        from vtx.pipeline.stages import mix_audio_wav, source_audio_extraction, validate_core
        from vtx.render.png_artifact import project_identity
        from vtx.timeline_preview_window import _context, _probe_facts

        rate, fa, fb = Fraction(30000, 1001), 59, 61
        total = frame_sample(fb, rate)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, timeline = root / 'tone.wav', root / 'timeline.json'
            with wave.open(str(source), 'wb') as wav:
                wav.setparams((2, 3, 48000, 0, 'NONE', 'not compressed'))
                pcm = bytearray()
                for i in range(total):
                    scale = 2300000 if i < 48000 else 25000
                    v = round(scale * math.sin(2 * math.pi * 440 * i / 48000))
                    pcm += v.to_bytes(3, 'little', signed=True) + (v // 2).to_bytes(3, 'little', signed=True)
                wav.writeframes(pcm)
            stored = {**TestCliGuards._min_tl(), 'sources': [{'id': 'tone', 'path': source.name}],
                      'project': {'fps': str(rate), 'width': 1920, 'height': 1080, 'duration_frames': fb},
                      'audio_tracks': [{'name': 'voice', 'events': [
                          {'src': 'tone', 'in': 0, 'out': fb, 'volume': 1}]}]}
            raw = json.dumps(stored).encode()
            timeline.write_bytes(raw)
            formal = Ctx(timeline)
            _, cmd = source_audio_extraction(formal, 'tone')
            formal.run(cmd)
            reference = root / 'formal-reference.wav'
            mix_audio_wav(formal, reference)
            with wave.open(str(reference), 'rb') as wav:
                wav.setpos(frame_sample(fa, rate))
                expected = wav.readframes(3204)

            payload = source.read_bytes()
            paths = program.inputs(stored, root, rate)
            manifest = program.manifest(paths.values(), {str(source): (len(payload), hashlib.sha256(payload).hexdigest())})
            request = root / 'cache' / 'preview' / 'program-test'
            workspace = request / 'render'
            workspace.mkdir(parents=True)
            copies, facts = program.stage(paths, manifest, root=root, root_identity=project_identity(root),
                                          directory=request / 'program-inputs', check=lambda: None)
            with mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0', 'VTX_VIDEO_SEGMENTS': '2'}):
                c = _context(timeline, lambda: None, os.environ.copy(), lambda: raw,
                             epoch_sources={str(source)}, program_copies=copies)
                program.probe(c, copies, facts)
                self.assertEqual(validate_core(c), [])
                c.write_root = workspace
                output = ranges._render_native_window(
                    c, fa, fb, workspace=workspace, check_cancel=lambda: None, expected_svg_pack=None,
                    program_sources={'tone': (source, total)})
            with wave.open(str(workspace / 'work' / 'preview' / output.with_suffix('.wav').name), 'rb') as actual:
                self.assertEqual(actual.getnframes(), 3204)
                self.assertEqual(actual.readframes(3204), expected)
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_packets',
                                     '-of', 'json', str(output)], check=True, capture_output=True, timeout=60)
            self.assertEqual(result.stderr, b'')
            self.assertEqual(_probe_facts(json.loads(result.stdout), rate, 2, 3204)['audio_channels'], 2)
            self.assertFalse((root / 'out').exists())

    def test_1080p_window_uses_isolated_workspace_with_cold_inactive_gfx(self):
        import wave
        from vtx.cache.recovery import completion_marker
        from vtx.pipeline.infra import Ctx
        from vtx.pipeline.range import render_1080p_window
        from vtx.pipeline.stages import validate_core

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clip = root / 'screen.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                            'color=red:s=320x180:r=30:d=1', '-f', 'lavfi', '-i',
                            'sine=frequency=440:sample_rate=48000:duration=1',
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(clip)],
                           check=True, capture_output=True, timeout=60)
            (root / 'page.html').write_text('<html></html>', encoding='utf-8')
            timeline = root / 'timeline.json'
            timeline.write_text(json.dumps({
                **TestCliGuards._min_tl(video_tracks=[
                    {'name': 'screen', 'kind': 'screen', 'clips': [
                        {'src': 'screen', 'src_in': 0, 'src_out': 12, 'dst_in': 0, 'dur': 12}]},
                    {'name': 'gfx', 'kind': 'gfx', 'clips': [
                        {'src': 'page.html', 'dst_in': 12, 'dur': 12}]}]),
                'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 300},
                'sources': [{'id': 'screen', 'path': clip.name}],
                'subtitles': [{'in': 0, 'out': 12, 'text': 'window'}],
                'audio_tracks': [{'name': 'voice', 'events': [
                    {'src': 'screen', 'in': 0, 'out': 12, 'volume': 1}]}],
            }), encoding='utf-8')
            c = Ctx(timeline)
            self.assertEqual(validate_core(c), [])
            observation = io.StringIO()
            c.execution.activate(observation)
            before = observation.getvalue()
            self.assertTrue(before)
            workspace = c.cache / 'preview' / 'owned'
            workspace.mkdir(parents=True)
            draft = workspace.parent / 'range_0.000_0.400.mp4'
            draft.write_bytes(b'keep draft')
            with mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0'}), \
                    mock.patch('vtx.pipeline.range._receipt_audio_gain', side_effect=AssertionError):
                output = render_1080p_window(c, 0, 12, workspace=workspace)

            self.assertEqual(output, workspace / 'native_range_0.000_0.400.mp4')
            self.assertEqual(c.cache, root / 'cache')
            self.assertFalse(hasattr(c, 'range_window'))
            self.assertFalse(c.out.exists())
            self.assertEqual(list(c.cache.iterdir()), [workspace.parent])
            self.assertEqual(draft.read_bytes(), b'keep draft')
            self.assertFalse((workspace / 'work' / 'gfx').exists())
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_frames',
                                    '-of', 'json', str(output)], check=True, capture_output=True, timeout=60)
            info = json.loads(probe.stdout)
            video, audio = info['streams']
            self.assertEqual((video['width'], video['height'], video['avg_frame_rate']), (1920, 1080, '30/1'))
            self.assertEqual([frame['pts'] * Fraction(video['time_base']) for frame in info['frames']
                              if frame['media_type'] == 'video'], [Fraction(i, 30) for i in range(12)])
            self.assertEqual((audio['codec_name'], audio['sample_rate'], audio['channels']), ('aac', '48000', 2))
            with wave.open(str(workspace / 'work' / 'preview' / output.with_suffix('.wav').name)) as pcm:
                self.assertEqual(pcm.getnframes(), 12 * 1600)
                self.assertTrue(any(pcm.readframes(pcm.getnframes())))

            run = subprocess.run
            for fault in ('certificate', 'input'):
                with self.subTest(fault=fault):
                    late = workspace.with_name(fault)
                    late.mkdir()
                    target = late / output.name
                    changed = []

                    def mutate_at_readback(argv, *args, **kwargs):
                        result = run(argv, *args, **kwargs)
                        if (argv[0] == 'ffprobe' and 'stream=nb_frames' in argv
                                and argv[-1] == str(target)):
                            self.assertEqual(result.returncode, 0)
                            if fault == 'certificate':
                                completion_marker(target).unlink()
                            else:
                                timeline.write_bytes(timeline.read_bytes() + b'\n')
                            changed.append(True)
                        return result

                    # A successful final ffprobe isn't sufficient: inject a
                    # change after it has read the atomically published MP4.
                    expected = (self.assertRaisesRegex(RuntimeError, 'media completion record')
                                if fault == 'certificate' else self.assertRaises(SystemExit))
                    with mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0'}), \
                            mock.patch.object(subprocess, 'run', side_effect=mutate_at_readback), expected:
                        render_1080p_window(c, 0, 12, workspace=late)
                    if fault == 'input':
                        self.assertEqual(expected.exception.code, 1)
                    self.assertEqual(changed, [True])
                    self.assertFalse(c.out.exists())
            self.assertEqual(observation.getvalue(), before)

    @unittest.skipUnless(sys.platform == 'linux', 'uses the Linux window worker context')
    def test_private_native_first_frame_from_real_segment(self):
        import hashlib
        from vtx.cache.recovery import completion_marker
        from vtx.pipeline.range import _measure_1080p_window
        from vtx.pipeline.stages import validate_core
        from vtx.timeline_preview_window import _context

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, lavfi in (
                    ('base', "color=red:s=320x180:r=30:d=3,drawbox=c=lime:t=fill:enable='gte(n,24)'"),
                    ('pip', 'color=blue:s=320x180:r=30:d=3')):
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', lavfi,
                                '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo:d=3',
                                '-map', '0:v', '-map', '1:a', '-c:a', 'aac',
                                '-c:v', 'libx264', str(root / f'{name}.mp4')],
                               check=True, capture_output=True, timeout=60)
            timeline = root / 'timeline.json'
            timeline.write_text(json.dumps({
                **TestCliGuards._min_tl(video_tracks=[
                    {'name': name, 'kind': kind, 'clips': [
                        {'src': name, 'src_in': 0, 'src_out': 80, 'dst_in': 105, 'dur': 80, **placement}]}
                    for name, kind, placement in (('base', 'aroll', {}),
                        ('pip', 'pip', {'x': 40, 'y': 40, 'scale': 1}))]),
                'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 300},
                'sources': [{'id': name, 'path': f'{name}.mp4'} for name in ('base', 'pip')],
            }), encoding='utf-8')
            workspace = root / 'cache' / 'preview' / 'first-frame'
            workspace.mkdir(parents=True)
            with mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0', 'VTX_VIDEO_SEGMENTS': '2'}):
                c = _context(timeline, lambda: None, os.environ.copy(), timeline.read_bytes)
                self.assertEqual(validate_core(c), [])
                c.write_root = workspace
                output, first, stages = _measure_1080p_window(c, 140, 170, workspace=workspace)

            self.assertEqual(first['window'], (140, 170))
            self.assertEqual(first['segment'], (140, 150))
            self.assertEqual((first['frame'], first['vpts'], first['fps']), (140, 0, Fraction(30)))
            self.assertEqual((first['width'], first['height']), (1920, 1080))
            self.assertEqual(first['content_version'], 'sha256:' + hashlib.sha256(timeline.read_bytes()).hexdigest())
            self.assertEqual(first['input_fingerprint'], c.input_fingerprint)
            segments = list((workspace / 'work' / 'preview').glob('rseg_*.mp4'))
            self.assertEqual(len(segments), 2)
            segment = next(p for p in segments if p.name.startswith('rseg_000140_000150_'))
            self.assertEqual(first['segment_key'], segment.stem.rsplit('_', 1)[1])
            self.assertEqual(first['segment_sha256'], hashlib.sha256(segment.read_bytes()).hexdigest())
            self.assertEqual(json.loads(completion_marker(output).read_bytes())['frames'], 30)
            self.assertEqual(first['sha256'], hashlib.sha256(first['png']).hexdigest())
            self.assertEqual(first['bytes'], len(first['png']))
            self.assertGreater(stages['layers_ready'], 0)
            self.assertLess(stages['layers_ready'], stages['first_segment_committed'])
            self.assertLess(stages['first_segment_committed'], stages['first_frame_verified'])
            self.assertLess(stages['first_frame_verified'], stages['window_verified'])

            def first_rgb(source, data=None):
                return subprocess.run(['ffmpeg', '-v', 'error', '-xerror', *source,
                    '-map', '0:v:0', '-frames:v', '1', '-pix_fmt', 'rgb24',
                    '-c:v', 'rawvideo', '-threads:v', '1', '-f', 'rawvideo', 'pipe:1'],
                    input=data, check=True, capture_output=True, timeout=60).stdout

            pixels = first_rgb(['-f', 'png_pipe', '-i', 'pipe:0'], first['png'])
            self.assertEqual(len(pixels), 1920 * 1080 * 3)
            self.assertEqual(pixels, first_rgb(['-i', str(output)]))
            # Not the source's first (red) frame, nor a layer-only or black still.
            pip = 3 * (64 * 1920 + 64)
            self.assertGreater(pixels[pip + 2], 200)
            self.assertLess(max(pixels[pip:pip + 2]), 40)
            base = 3 * (500 * 1920 + 800)
            self.assertGreater(pixels[base + 1], 200)
            self.assertLess(max(pixels[base], pixels[base + 2]), 40)
            self.assertFalse((root / 'out').exists())

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
                [*cli_python(), "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
                 "-t", str(proj / "timeline.json"), "video", "--draft", "--range", "2", "5"],
                capture_output=True, encoding="utf-8", cwd=REPO, timeout=120)
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
