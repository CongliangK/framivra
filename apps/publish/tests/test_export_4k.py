#!/usr/bin/env python3
"""tests/test_export_4k.py —— 4K 并行导出（方案三阶段2）单元/集成测试。

覆盖：
  ① 段切分数学：Σ帧数==总帧数、边界严格单调、每段 ≥1 帧；n>total 收缩；n=1 单段；
  ② 内容寻址键：媒体 stat 变→键变；段区间/编码参数变→键变；同输入键稳定；
  ③ -ss 首帧等价：合成 60 帧视频，段起点 seek 提帧与全解提帧逐字节一致
     （帧号 0 与中间段各验一例——export_4k.seg_cmd 的帧精确前提）；
  ④ concat 后帧数==期望：合成 90 帧 640x360→1280x720 小规模 nvenc 真编全链
     （export() 走探测/分段/编码/concat/自检；探测失败 skip 并标注）；二次运行段全命中；
  ⑤ x264 回落路径参数：mock 探测 False，断言执行的 ffmpeg 命令含 libx264 medium
     及质量模式/VBV/时基/音轨映射关键参数；
  ⑥ maxrate ASCII 数字、后缀归一化与最小值门；两链母带音轨前置校验。
运行：repo 根 `bash scripts/run_all_tests.sh`（P2a 起统一运行器含 apps/*/tests 发现）；
本 app 内独立跑：`cd apps/publish && env python3 -m unittest discover -s tests -t . -v`
（本机 ZCode 环境必须 env python3 前缀：AppImage 劫持 argv[0]，裸 python3 的
sys.executable 指向错误解释器，见全局 AGENTS.md）
"""
import argparse
import io
import json
import os
import shutil
from contextlib import redirect_stderr, redirect_stdout
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

APP = Path(__file__).resolve().parents[1]  # apps/publish/（app 根；tests 随 app 迁入 P2a）
REPO = APP.parent.parent                   # repo 根（apps/publish → apps → repo）
sys.path.insert(0, str(APP))               # export_4k.py 非包成员，按路径导入
sys.path.insert(0, str(REPO))              # repo 根（v2 §1.4 包导入，不再注入 repo/vtx）

import export_4k
import compare_4k_quality
from export_4k import (NVENC_ARGS, SCALE_4K, fallback_x264_cmd, project_root,
                       seg_cmd, seg_key, split_frames)


def ff(args):
    """合成素材用 ffmpeg 直调（-v error，失败抛 AssertionError 带 stderr）。"""
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise AssertionError(f"ffmpeg 合成失败: {r.stderr[-800:]}")


def write_timeline(td: Path, frames: int, fps: int = 30, w: int = 640, h: int = 360) -> Path:
    tl = td / "timeline.json"
    tl.write_text(json.dumps({"project": {"fps": fps, "w": w, "h": h,
                                          "duration_frames": frames}}),
                  encoding="utf-8")
    return tl


class TestQualitySampling(unittest.TestCase):
    def test_nonpositive_media_frame_count_fails_before_extracting(self):
        for total in (0, -1):
            with self.subTest(total=total), \
                 mock.patch.object(sys, 'argv', ['compare', 'a', 'b']), \
                 mock.patch.object(compare_4k_quality, 'probe', return_value=(640, 360, 30, total)), \
                 mock.patch.object(compare_4k_quality, 'extract_frame') as extract, \
                 mock.patch.object(compare_4k_quality, 'raw_metric') as metric, \
                 redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit:
                compare_4k_quality.main()
            self.assertEqual(exit.exception.code, 2)
            extract.assert_not_called()
            metric.assert_not_called()

    def test_sampling_is_bounded_and_preserves_normal_seed_sequence(self):
        for total, requested in ((1, 10), (2, 10), (9, 10), (30, 100),
                                 (100, 10), (100, 0), (100, -1)):
            with self.subTest(total=total, requested=requested):
                rng = compare_4k_quality.random.Random(compare_4k_quality.SEED)
                calls = []
                def bounded_randrange(stop):
                    calls.append(stop)
                    if len(calls) > 100 + 10 * total:
                        raise AssertionError('sampler failed to terminate within bounded draws')
                    return rng.randrange(stop)
                bounded_rng = SimpleNamespace(randrange=bounded_randrange)
                extracted = []
                def extract(path, n, fps, dst):
                    extracted.append((path, n))
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(sys, 'argv', ['compare', 'a', 'b', '--frames', str(requested)]), \
                     mock.patch.object(compare_4k_quality, 'probe', return_value=(640, 360, 30, total)), \
                     mock.patch.object(compare_4k_quality.random, 'Random', return_value=bounded_rng), \
                     mock.patch.object(compare_4k_quality, 'extract_frame', side_effect=extract), \
                     mock.patch.object(compare_4k_quality, 'raw_metric', return_value='average:inf All:1.0'), \
                     redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit) as exit:
                    compare_4k_quality.main()
                self.assertEqual(exit.exception.code, 0)
                frames = [n for path, n in extracted if path == 'a']
                self.assertEqual(frames, [n for path, n in extracted if path == 'b'])
                self.assertEqual(frames, sorted(set(frames)))
                self.assertTrue(all(0 <= n < total for n in frames))
                if requested > total:
                    self.assertEqual(frames, list(range(total)))
                    self.assertIn('检查全部帧', err.getvalue())
                else:
                    # Reproduce the previous valid-input sampling algorithm unchanged.
                    expected = {0, total // 2, *(a for a, _ in split_frames(total, 8))}
                    original_rng = compare_4k_quality.random.Random(compare_4k_quality.SEED)
                    while len(expected) < max(1, requested):
                        expected.add(original_rng.randrange(total))
                    self.assertEqual(frames, sorted(expected))
                    self.assertEqual(err.getvalue(), '')


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("bash"),
                     "check_4k.sh requires Linux GNU stat/readlink and bash")
class TestCheck4kLiteralArguments(unittest.TestCase):
    def test_quoted_paths_and_numeric_inputs_are_data_not_python(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bindir = root / 'bin'; bindir.mkdir()
            probe = bindir / 'ffprobe'
            probe.write_text('#!' + sys.executable + '\n' + """
import sys
args = sys.argv[1:]
entry = args[args.index('-show_entries') + 1]
audio = '-select_streams' in args and args[args.index('-select_streams') + 1] == 'a:0'
values = {'stream=width': '3840', 'stream=height': '2160',
          'stream=r_frame_rate': '30/1', 'stream=avg_frame_rate': '30/1',
          'stream=codec_name': 'aac' if audio else 'h264', 'stream=profile': 'High',
          'stream=pix_fmt': 'yuv420p', 'stream=nb_read_packets': '60',
          'stream=bit_rate': '192000' if audio else '10000000',
          'format=duration': '2', 'stream=sample_rate': '48000', 'packet=flags': 'K_'}
print(values[entry])
""")
            probe.chmod(0o755)
            env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ.get('PATH', ''),
                       BITRATE_CAP_MBPS='20.5')
            media_dir = root / "quoted ' folder"; media_dir.mkdir()
            media = media_dir / "clip 'name.mp4"
            media.write_bytes(b'\0\0\0\x10moov' + b'\0' * 56)
            timeline = media_dir / 'timeline.json'
            timeline.write_text(json.dumps({'project': {'duration_frames': 60}}))
            marker = root / 'must-not-execute'
            expression = f"(__import__('pathlib').Path({str(marker)!r}).touch() or 60)"
            for name, expected_frames, cap, expected_status in (
                    ('auto', None, '20.5', 0), ('explicit', '60', '20.5', 0),
                    ('frame_expression', expression, '20.5', 1),
                    ('cap_expression', '60', expression, 1),
                    ('real_quality_failure', '60', '1', 1)):
                with self.subTest(name=name):
                    marker.unlink(missing_ok=True)
                    command = ['bash', str(APP / 'check_4k.sh'), str(media)]
                    if expected_frames is not None:
                        command.append(expected_frames)
                    result = subprocess.run(command, cwd=root,
                                            env=dict(env, BITRATE_CAP_MBPS=cap),
                                            capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, expected_status, result.stdout + result.stderr)
                    self.assertFalse(marker.exists())
                    if expected_status == 0:
                        self.assertIn('PASS=14 FAIL=0', result.stdout)
                        self.assertNotIn('SKIP', result.stdout)
                    if name == 'real_quality_failure':
                        self.assertIn('FAIL  5 平均码率超门', result.stdout)


class TestMaxrate(unittest.TestCase):
    def test_valid_rates_are_normalized(self):
        for value, expected in [("20m", "20M"), ("20M", "20M"), ("1000k", "1000k"),
                                ("1000K", "1000k"), ("1000", "1000"), ("0.001M", "0.001M")]:
            with self.subTest(value=value):
                self.assertEqual(export_4k.parse_maxrate(value), expected)
                for nvenc in (True, False):
                    args = export_4k.video_args(nvenc, value)
                    for option in ("-maxrate", "-bufsize"):
                        self.assertEqual(args[args.index(option) + 1], expected)

    def test_invalid_rates_rejected(self):
        for value in ("２０M", "-5M", "0.00001", "20M;rm", "0", "999", "0.999k",
                      "1e6", "NaN", "inf", "1" + "0" * 400):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    export_4k.parse_maxrate(value)
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "过小"):
            export_4k.parse_maxrate("0.00001")


class TestAudioPrecheck(unittest.TestCase):
    def test_missing_audio_rejected_before_either_encoder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            media = root / "master.mp4"
            media.write_bytes(b"dummy")
            tl = write_timeline(root, 75, fps=25)
            for force_fallback in (False, True):
                with self.subTest(force_fallback=force_fallback):
                    stderr = io.StringIO()
                    with mock.patch.object(export_4k, "_sh", return_value=SimpleNamespace(
                            returncode=0, stdout="", stderr="")) as probe, \
                         mock.patch.object(export_4k, "nvenc_probe") as nvenc, \
                         redirect_stderr(stderr):
                        with self.assertRaises(SystemExit) as cm:
                            export_4k.export(tl, media, root / "4k.mp4",
                                             force_fallback=force_fallback)
                    self.assertNotEqual(cm.exception.code, 0)
                    self.assertIn("母带无音轨，4K 投稿版需音轨直通", stderr.getvalue())
                    self.assertIn(str(media), stderr.getvalue())
                    self.assertIn("检查 vtx mux 阶段产物", stderr.getvalue())
                    probe.assert_called_once_with([
                        "ffprobe", "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(media)])
                    nvenc.assert_not_called()
                    self.assertFalse((root / "cache").exists())
                    self.assertFalse((root / "4k.mp4").exists())

    def test_audio_present(self):
        for output in ("audio\n", "audio\naudio\n"):
            with mock.patch.object(export_4k, "_sh", return_value=SimpleNamespace(
                    returncode=0, stdout=output, stderr="")):
                self.assertTrue(export_4k.probe_has_audio("master.mp4"))


class TestSplitFrames(unittest.TestCase):
    def test_sum_and_monotone(self):
        # bench_p2 实尺 + 各种整除/非整除/小片段形态
        for total, n in [(16746, 8), (90, 3), (100, 8), (7, 3), (1, 1),
                         (59, 8), (240, 4), (8, 8)]:
            spans = split_frames(total, n)
            self.assertEqual(spans[0][0], 0)
            self.assertEqual(spans[-1][1], total, f"{total}/{n}")
            self.assertEqual(sum(b - a for a, b in spans), total, f"{total}/{n}")
            for (a1, b1), (a2, b2) in zip(spans, spans[1:]):
                self.assertEqual(b1, a2)              # 无缝衔接
                self.assertGreater(b1 - a1, 0)         # 每段 ≥1 帧
            # 均分性质：段长极差 ≤1
            lens = [b - a for a, b in spans]
            self.assertLessEqual(max(lens) - min(lens), 1, f"{total}/{n}")

    def test_n_exceeds_total_shrinks(self):
        spans = split_frames(7, 8)                     # 段数收缩到 7（每段 1 帧）
        self.assertEqual(spans, [(i, i + 1) for i in range(7)])

    def test_single_segment(self):
        self.assertEqual(split_frames(90, 1), [(0, 90)])


class TestSegKey(unittest.TestCase):
    def _key(self, td, content, a=100, b=200, rewrite=True):
        from vtx.cache_state import media_stat_digest
        p = td / "m.mp4"
        if rewrite or not p.exists():
            p.write_bytes(content)
        return seg_key(media_stat_digest(p), a, b, SCALE_4K, NVENC_ARGS)

    def test_media_stat_change_changes_key(self):
        with tempfile.TemporaryDirectory() as td:
            k1 = self._key(Path(td), b"a" * 4096)
            k2 = self._key(Path(td), b"b" * 4096)     # 同尺寸换内容（哈希变）
            k3 = self._key(Path(td), b"a" * 8192)     # 换尺寸（stat 变）
            self.assertNotEqual(k1, k2)
            self.assertNotEqual(k1, k3)

    def test_params_and_encoder_change_key(self):
        with tempfile.TemporaryDirectory() as td:
            k1 = self._key(Path(td), b"x" * 4096)
            # 同一文件不重写 → stat 不变 → 键稳定（重写同内容会变 mtime_ns，属键语义）
            self.assertEqual(k1, self._key(Path(td), b"", rewrite=False))
            self.assertNotEqual(k1, self._key(Path(td), b"x" * 4096, a=101, b=200))
            self.assertNotEqual(k1, self._key(Path(td), b"x" * 4096, a=100, b=201))
            # 编码器/参数变（回落 x264 或调码率）→ 键变
            k_x = seg_key("fp", 100, 200, SCALE_4K, fallback_x264_cmd("m", "o")[5:14])
            self.assertNotEqual(seg_key("fp", 100, 200, SCALE_4K, NVENC_ARGS), k_x)
            self.assertNotEqual(seg_key("fp", 100, 200, "scale=1920:1080:flags=lanczos", NVENC_ARGS),
                                seg_key("fp", 100, 200, SCALE_4K, NVENC_ARGS))

    def test_key_length(self):
        self.assertEqual(len(seg_key("fp", 0, 90, SCALE_4K, NVENC_ARGS)), 12)


class TestSSSeekEquivalence(unittest.TestCase):
    """段起点 -ss 提帧 vs 全解提帧逐字节一致（seg_cmd 帧精确语义的最小验证）。"""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.src = Path(cls.td.name) / "s60.mp4"
        # testsrc2 逐帧内容不同 + -g 15（2s 一个关键帧）→ 中段 seek 必跨关键帧前向解码
        ff(["-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2",
            "-c:v", "libx264", "-crf", "18", "-g", "15", "-pix_fmt", "yuv420p", str(cls.src)])
        cls.fsize = 640 * 360 * 3 // 2                 # yuv420p 单帧字节数

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def _seek_bytes(self, n):
        # 直接取 seg_cmd 生成的 -ss 时刻（被测语义本体），提帧链与 qc.gray_frame 同构
        cmd = seg_cmd(self.src, n, 1, 30, "null", [], "unused.mp4")
        ss = cmd[cmd.index("-ss") + 1]
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", ss, "-i", str(self.src),
                            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                           capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode()[-500:])
        return r.stdout

    def test_frame0_and_midframe(self):
        # 全解提帧：整个文件解成 raw，按帧字节切片（不依赖任何 select 滤镜行为）
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.src),
                            "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                           capture_output=True)
        self.assertEqual(len(r.stdout), 60 * self.fsize)
        for n in (0, 37):                              # 帧号 0 + 中间段起点（跨关键帧）
            self.assertEqual(self._seek_bytes(n), r.stdout[n * self.fsize:(n + 1) * self.fsize])


class TestNvencConcatIntegration(unittest.TestCase):
    """小规模真编全链：90 帧 640x360→1280x720、3 段并行、concat 帧数自检、缓存命中。"""

    @classmethod
    def setUpClass(cls):
        if not export_4k.nvenc_probe():
            raise unittest.SkipTest("NVENC 探测失败（无 GPU/驱动不可用），跳过真编集成——"
                                    "段切分/键/回落路径由其余用例覆盖")
        cls.td = tempfile.TemporaryDirectory()
        cls.root = Path(cls.td.name)
        cls.media = cls.root / "out" / "final.mp4"
        cls.media.parent.mkdir(parents=True)
        # 90 帧 + aac 48k 音轨（concat -map 1:a 需要；音频与视频同长 3s）
        ff(["-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=3",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
            "-c:v", "libx264", "-crf", "18", "-g", "15", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", str(cls.media)])
        cls.tl = write_timeline(cls.root, 90)
        cls.out = cls.root / "out" / "final_4k.mp4"

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_full_chain_and_cache_hit(self):
        stats = export_4k.export(self.tl, self.media, self.out, segments=3,
                                 scale="scale=1280:720:flags=lanczos")
        self.assertEqual(stats["encoder"], "nvenc-p4")
        self.assertEqual(stats["hits"], 0)
        self.assertEqual(export_4k.probe_nb_frames(self.out), 90)   # concat 自检已内部跑过，这里独立复核
        w = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "csv=p=0",
                            str(self.out)], capture_output=True, text=True).stdout.strip()
        self.assertEqual(w, "1280,720")
        a = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=codec_name,sample_rate", "-of", "csv=p=0",
                            str(self.out)], capture_output=True, text=True).stdout.strip()
        self.assertEqual(a, "aac,48000")                            # 音轨母带直通在位
        # 二次运行：段全命中（不重编），帧数仍精确
        stats2 = export_4k.export(self.tl, self.media, self.out, segments=3,
                                  scale="scale=1280:720:flags=lanczos")
        self.assertEqual(stats2["hits"], 3)
        self.assertEqual(stats2["segments"], 3)
        self.assertEqual(export_4k.probe_nb_frames(self.out), 90)


class TestSymlinkTimelineNoPenetration(unittest.TestCase):
    """符号链接 timeline（bench 项目挂 live timeline 形态）不得穿透到 live 项目：

    项目根必须取链接所在目录（abspath），resolve() 会把 cache/4k 与输出全部写进
    live 项目（2026-09-13 验收实测事故：bench_p2/timeline.json → voice-ime 符号链接
    穿透，voice-ime/out/final_4k.mp4 被误覆盖）。vtx.py 同款教训（DEV.md 迁移基线）。
    """

    def test_project_root_uses_symlink_dir(self):
        with tempfile.TemporaryDirectory() as td_live, tempfile.TemporaryDirectory() as td_bench:
            live, bench = Path(td_live), Path(td_bench)
            write_timeline(live, 30)
            (bench / "timeline.json").symlink_to(live / "timeline.json")
            self.assertEqual(project_root(bench / "timeline.json"), bench)
            self.assertNotEqual(project_root(bench / "timeline.json"), live)

    def test_export_writes_cache_beside_symlink(self):
        # 真编小例（NVENC 不可用则跳过）：cache/4k 必须落在 bench 目录、live 零写入
        if not export_4k.nvenc_probe():
            self.skipTest("NVENC 探测失败，跳过真编符号链接集成")
        with tempfile.TemporaryDirectory() as td_live, tempfile.TemporaryDirectory() as td_bench:
            live, bench = Path(td_live), Path(td_bench)
            write_timeline(live, 30)
            (bench / "timeline.json").symlink_to(live / "timeline.json")
            (bench / "out").mkdir()
            media = bench / "out" / "final.mp4"
            ff(["-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=1",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", str(media)])
            out = export_4k.export(bench / "timeline.json", media,
                                   bench / "out" / "final_4k.mp4", segments=2,
                                   scale="scale=640:360:flags=lanczos")
            self.assertEqual(out["encoder"], "nvenc-p4")
            self.assertTrue((bench / "cache" / "4k" / "list.txt").exists())
            self.assertFalse((live / "cache").exists())       # live 零写入（穿透即失败）
            self.assertFalse((live / "out").exists())


class TestFallbackX264(unittest.TestCase):
    """回落路径：探测失败（mock False）→ 执行命令必须是 R5 现行 x264 medium 全参数。"""

    def test_fallback_command_params(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            media = root / "out" / "final.mp4"
            media.parent.mkdir()
            media.write_bytes(b"dummy")               # 存在性检查用；ffprobe 已被 mock
            tl = write_timeline(root, 90)
            cmds = []

            def fake_sh(cmd, need=True):
                cmd = [str(x) for x in cmd]
                cmds.append(cmd)
                if "stream=codec_type" in cmd:
                    return SimpleNamespace(returncode=0, stdout="audio\n", stderr="")
                if any("r_frame_rate" in x for x in cmd):   # fps 前置校验探测（P2-4）
                    return SimpleNamespace(returncode=0, stdout="30/1\n", stderr="")
                return SimpleNamespace(returncode=0, stdout="90\n", stderr="")

            with mock.patch.object(export_4k, "nvenc_probe", return_value=False), \
                 mock.patch.object(export_4k, "_sh", side_effect=fake_sh):
                stats = export_4k.export(tl, media, root / "out" / "f4k.mp4")
            self.assertIn("x264", stats["encoder"])
            enc = [c for c in cmds if c[0] == "ffmpeg" and "libx264" in c]
            self.assertEqual(len(enc), 1)             # 恰一条编码命令（单进程整链）
            c = enc[0]
            for tok in ["-i", str(media), "-vf", SCALE_4K, "-c:v", "libx264",
                        "-profile:v", "high", "-preset", "medium", "-crf", "18",
                        "-maxrate", "20M", "-bufsize", "20M", "-pix_fmt", "yuv420p",
                        "-c:a", "copy", "-movflags", "+faststart",
                        "-video_track_timescale", "15360", "-movie_timescale", "15360"]:
                self.assertIn(tok, c)
            self.assertNotIn("h264_nvenc", c)

    def test_fallback_x264_cmd_matches_runbook(self):
        c = fallback_x264_cmd("m.mp4", "o.mp4")
        # 质量模式 + VBV 上限 + 双时基（check_4k.sh 断言对应的生成侧）
        self.assertEqual(c[c.index("-vf") + 1], "scale=3840:2160:flags=lanczos")
        self.assertEqual([c[i + 1] for i, token in enumerate(c) if token == "-map"],
                         ["0:v:0", "0:a"])
        self.assertEqual(c[c.index("-preset") + 1], "medium")
        self.assertEqual(c[c.index("-crf") + 1], "18")
        self.assertEqual(c[c.index("-maxrate") + 1], "20M")
        self.assertEqual(c[c.index("-movie_timescale") + 1], "15360")


class TestFpsPrecheck(unittest.TestCase):
    """母带 r_frame_rate 与 timeline fps 前置校验（阶段2审查 P2-4）：人为误改
    timeline fps 而母带未换时帧数校验拦不住（帧数没变），seek 会按错 fps 取帧 →
    产出内容错位成片且 check_4k 全过——必须在此显式报错。"""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.td.cleanup)
        cls.root = Path(cls.td.name)
        cls.media = cls.root / "out" / "final.mp4"
        cls.media.parent.mkdir(parents=True)
        # 30 帧 @25fps（duration_frames 校验可对齐 30，只有 fps 口径暴露差异）
        ff(["-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=1.2",
            "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=1.2",
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", str(cls.media)])
        assert export_4k.probe_nb_frames(cls.media) == 30

    def test_mismatch_rejected_exit_2(self):
        tl = write_timeline(self.root, 30, fps=30)   # 帧数一致、fps 不一致
        with self.assertRaises(SystemExit) as cm:
            export_4k.export(tl, self.media, self.root / "out" / "f4k.mp4")
        self.assertEqual(cm.exception.code, 2)

    def test_match_passes_precheck(self):
        # fps 一致（25/25）→ 过前置校验进主流程；mock 探测失败走回落 + mock 编码，
        # 只验「不因 fps 误杀」
        tl = write_timeline(self.root, 30, fps=25)
        def fake_sh(cmd, need=True):
            cmd = [str(x) for x in cmd]
            if "stream=codec_type" in cmd:
                return SimpleNamespace(returncode=0, stdout="audio\n", stderr="")
            if any("r_frame_rate" in x for x in cmd):
                return SimpleNamespace(returncode=0, stdout="25/1\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="30\n", stderr="")
        with mock.patch.object(export_4k, "nvenc_probe", return_value=False), \
             mock.patch.object(export_4k, "_sh", side_effect=fake_sh):
            stats = export_4k.export(tl, self.media, self.root / "out" / "f4k.mp4")
        self.assertIn("x264", stats["encoder"])

    def test_probe_media_fps_unparseable_exits_2(self):
        # fail-closed：r_frame_rate 解析不出正数（如 0/0、空）→ 显式 exit 2，不猜测
        for garbage in ("0/0\n", "\n", "n/a\n"):
            with mock.patch.object(export_4k, "_sh", return_value=SimpleNamespace(
                    returncode=0, stdout=garbage, stderr="")):
                with self.assertRaises(SystemExit) as cm:
                    export_4k.probe_media_fps(self.media)
            self.assertEqual(cm.exception.code, 2)


class TestCompareParseFailClosed(unittest.TestCase):
    """compare_4k_quality 解析失败 fail-closed（阶段2审查 P2-1）：打不出数值必须
    exit 2，旧行为（PSNR miss → inf 恒 PASS）是把解析失败当满分。"""

    def test_parse_psnr_valid_and_inf(self):
        import compare_4k_quality as cq
        self.assertEqual(cq.parse_psnr("... average:52.73 ..."), 52.73)
        self.assertEqual(cq.parse_psnr("... average:inf ..."), float("inf"))

    def test_parse_psnr_garbage_exits_2(self):
        import compare_4k_quality as cq
        with self.assertRaises(SystemExit) as cm:
            cq.parse_psnr("ffmpeg 8.x 全新日志措辞，无 average 行")
        self.assertEqual(cm.exception.code, 2)

    def test_parse_ssim_valid(self):
        import compare_4k_quality as cq
        self.assertAlmostEqual(cq.parse_ssim("SSIM Y:0.99 All:0.996864 (24.2)"), 0.996864)

    def test_parse_ssim_garbage_exits_2(self):
        import compare_4k_quality as cq
        with self.assertRaises(SystemExit) as cm:
            cq.parse_ssim("无指标行")
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
