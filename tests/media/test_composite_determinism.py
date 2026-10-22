#!/usr/bin/env python3
"""tests/media/test_composite_determinism.py —— 段合成跨运行确定性回归（R4-E1-v2，2026-09-14）。

背景（核销 KNOWN-ISSUES ⑭）：`vtx video` 分段合成曾存在「同 argv 跨运行输出不同」
的非确定性（voice-ime 前三段隔离复渲 4 次各得 4 个 md5，R3-E2/R4-E1 取证在案）。
机制诊断（R4-E1-v2，/tmp/vtx_r4/FINDINGS.md）：链式多输入 graph 下 overlay/framesync
的**输出节律**（丢帧 2~4 帧 / 主帧×次帧配对滞后）跨运行随机——showinfo 直证双 overlay
最小图一版缺 4 帧、另一版输出旧帧内容；错帧全程随机发生，静止区 checksum 相同不可见，
只在层淡入/淡出（enable 窗口边界带）被观测（E2 的「差异带在层窗口边界 ±20 帧」与
96.9% 帧一致由此而来，是观测偏差而非病灶位置）。两族参数化假说均被复渲证伪：
tpad 尾垫至段尾（EOF 裁决假说——尾垫后仍 4/4 发散）、全输入 -threads 1（解码线程
假说——仍发散）。framesync.c 状态机本身闭合（miss→request 等待、同 pts 同轮推进），
非确定源在滤镜图框架对「帧何时可得」的调度依赖，参数层无法钉死。

R4-E1-v2 修复 = composite() 段级多层路径改**逐层 pass 合成**：每 pass 恰 2 输入
（主=canvas 或上游 pass 中间物 + 次=1 层），单 overlay+canvas 已实测三输入形态
（PNG/prores/h264）各 2 次复渲全确定；中间物 FFV1 intra（-level 3 -g 1，无损保
alpha/位深，与链式 graph 的协商格式域逐位同构——曾用 libx264 qp0+强制 8bit 落盘，
prores alpha 10bit 层使量化点错位产生全帧 ±1 级系统性微差，E-B2/FINDINGS 在案）
落 cache/vseg_pass，pass 键 = sha1(上游键|层内容/参数|合成代码摘要)[:12] 温态复用、
上游/参数/代码任一变更自动失效（摘要=合成路径五函数联合，曾只摘路由函数 composite()
致 _composite_passes 改动不翻转键，已修）；中间物帧数硬校验 == 段长（创建后
-count_frames 实测 fail-fast，不 cfr 补帧）+ 帧数旁车 .n 温态快路径（FFV1 大文件
全文件解包校验数十秒/个，温态改用完成证书+字节摘要；.n 仅保额外帧数约束，
见 _pass_fresh docstring）。合成代码摘要
_graph_code_digest 掺段键与 pass 键（防改码后旧缓存命中「洗白」）。

覆盖：
  ① _graph_code_digest：12 位 hex、同进程稳定（lru_cache）；
  ② 段内容键漂移：摘要翻转（模拟 composite() 改码）→ _seg_content_key 必变——
     「代码变更 → 旧段缓存失效」的机制锁；
  ③ 合成图跨运行确定性（真渲染，走逐层 pass 产品路径）：dense-EOF 端点图（6 层
     PNG 序列，窗口端点聚簇 44-46 / 58-60 / 85-89 帧，贴合 E2 观测带几何）——
     整片形态（a=0，含 rel_in==0 无头垫分支）与中段形态（a=40,b=100，含跨段层
     start_number 分支、尾垫自然退化分支）各 4 次**并发**复渲，断言 md5 逐字节
     一致，且输出帧数精确 == 段长（pass 尾垫/硬钳不得产生 extra 帧——v3.6 教训
     非回归锁；pass 中间物帧数自检在 composite 内 fail-fast）。

敏感性边界（诚实声明）：合成 PNG 图无法在单测尺度复现真实病灶——已实测 14 层
1080p 同构 PNG 在旧单 graph 行为下仍逐字节一致（疾病需 h264/prores/PNG 异构输入
与真实媒体 IO 的到达时序，voice-ime 前三段才稳定显形）；本测试是「pass 路径产品
形态同 argv 并发复渲逐字节一致」的回归锁 + graph 构造（tpad/enable/pass 键）快照，
真实数据的确定性验收见 voice-ime 08-remake-tasks/R4-E1-工具侧修复与温态复验.md。
跑法：env python3 -m unittest tests.media.test_composite_determinism -v（本机 ZCode 环境
必须 env python3 前缀，裸 python3 的 sys.executable 被 AppImage 劫持，见全局
AGENTS.md）。①② 纯函数零渲染；③ 需 ffmpeg+libx264（缺任一 skip），单形态 4 次
并发 ~10s 级。
"""
import hashlib
import inspect
import json
import struct
import zlib
from fractions import Fraction
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))   # repo 根注入（v2 §1.4 包导入）

from vtx.render.frame_source import PngSequence
from vtx.infra import Ctx
from vtx import composite
from vtx.composite import RenderPlan, _graph_code_digest


def _render_context(root, cache, width, height, duration):
    """Real writer/identity context; synthetic PNG inputs stay read-only fixtures."""
    timeline = root / 'timeline.json'
    timeline.write_text(json.dumps({
        'project': {'fps': 30, 'duration_frames': duration, 'width': width, 'height': height},
        'sources': [], 'video_tracks': [], 'audio_tracks': [], 'subtitles': [],
    }))
    c = Ctx(timeline)
    c.cache = cache  # isolated passes within the same pinned project root
    return c


def _have_ffmpeg_x264() -> bool:
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    r = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True)
    return "libx264" in (r.stdout + r.stderr)


class TestGraphCodeDigest(unittest.TestCase):
    """段内容键的代码摘要成分（纯函数，零渲染）。"""

    def test_digest_shape_and_stability(self):
        d1, d2 = _graph_code_digest(), _graph_code_digest()
        self.assertRegex(d1, r"^[0-9a-f]{12}$")
        self.assertEqual(d1, d2)   # lru_cache：进程内稳定

    def test_seg_key_drift_on_code_change(self):
        """代码变更 → 段键必变（防洗白机制锁）：摘要翻转时 _seg_content_key 逐位不同。

        R4-E1 场景：composite() 改合成策略后，若段键不含代码摘要，旧段文件仍被
        内容键命中——脏缓存洗白。此处以 monkeypatch 模拟「composite() 改码」
        （摘要翻转），断言键漂移。
        """
        class _P:
            pass
        p = _P(); p.W, p.H, p.draft = 1920, 1080, False
        p.c = mock.MagicMock(gpu=False)
        sl = [{"cl": {"dst_in": 5, "dur": 30}, "gfx": False,
               "seg": Path("/cache/seg/abc123.mov"), "source": PngSequence(Path("/cache/gfx/x"), "x", 30, 30, 64, 64),
               "x": 0, "y": 0, "w2": None, "h2": None}]
        real = _graph_code_digest()
        with mock.patch.object(composite, "_graph_code_digest", return_value=real):
            k_real = RenderPlan._seg_content_key(p, 5, 35, sl, False, None)
        with mock.patch.object(composite, "_graph_code_digest", return_value="a" * 12):
            k_a = RenderPlan._seg_content_key(p, 5, 35, sl, False, None)
        with mock.patch.object(composite, "_graph_code_digest", return_value="b" * 12):
            k_b = RenderPlan._seg_content_key(p, 5, 35, sl, False, None)
        self.assertEqual(len({k_real, k_a, k_b}), 3,
                         "合成代码摘要翻转必须使段内容键漂移（否则旧段缓存洗白）")
        self.assertEqual(len(k_real), 12)

    def test_digest_covers_pass_path(self):
        """摘要函数集覆盖断言：_SYNTH_FUNCS 必须包含全部合成路径函数。

        R4-E1-v2 教训锁：摘要曾只摘路由函数 composite()，_composite_passes 改动
        （如中间物编码参数）不翻转键——脏缓存洗白随路由化回归。此处断言函数集
        完整且每个名字确为 RenderPlan 可取源码的函数（改名/删函数立即显式失败，
        强迫维护者同步本表）。
        """
        required = {"composite", "_composite_graph", "_composite_passes",
                    "_layer_chain", "_layer_input_args"}
        self.assertTrue(required <= set(composite._SYNTH_FUNCS),
                        f"摘要遗漏合成路径：{sorted(required - set(composite._SYNTH_FUNCS))}")
        for name in composite._SYNTH_FUNCS:
            self.assertTrue(hasattr(RenderPlan, name), f"_SYNTH_FUNCS 名单过期：{name}")
            inspect.getsource(getattr(RenderPlan, name))   # 取不到即抛


class TestLayerHalfOpenWindow(unittest.TestCase):
    """The emitted visibility interval must match integer-frame [in, out) intent.

    Inspect the actual shared graph fragment, not a copy of its edge formula.
    These rational-clock checks supplement, not replace, real pixel/PTS tests.
    """

    def _active(self, start, end, origin, stop, gfx=True, x=0, y=0, scale=1):
        c = SimpleNamespace(fps=30, spf=1 / 30)
        plan = RenderPlan(c=c, draft=scale != 1, f=scale, W=64, H=64)
        layer = {'cl': {'dst_in': start, 'dur': end - start},
                 'gfx': gfx, 'x': x, 'y': y}
        _, overlay, _ = plan._layer_chain(layer, 0, 'base', origin, stop - origin, 1)
        # Generic enable on FFmpeg 6.1 is overwritten by secondary-input
        # lookahead. Only overlay's own per-main-frame x expression may gate.
        self.assertNotIn('enable=', overlay)
        match = re.search(r"overlay=x='if\(between\(t,([^,]+),([^\)]+)\),"
                          r"(-?\d+),NAN\)':y=(-?\d+):", overlay)
        self.assertIsNotNone(match, overlay)
        lo, hi = map(Fraction, match.groups()[:2])
        self.assertEqual(tuple(map(int, match.groups()[2:])), (int(x * scale), int(y * scale)))
        self.assertIn('eval=frame', overlay)
        self.assertIn('eof_action=repeat', overlay)
        return [frame for frame in range(origin, stop)
                if lo <= Fraction(frame - origin, 30) <= hi]

    def test_all_endpoint_quantization_classes_and_segment_origins(self):
        # At 30fps, out % 3 covers all six-decimal rounding directions.
        for origin in (0, 30, 31, 32):
            for start in (0, 1, 33, 34, 35):
                for end in (60, 61, 62):
                    for gfx in (False, True):
                        with self.subTest(origin=origin, start=start, end=end, gfx=gfx):
                            self.assertEqual(self._active(start, end, origin, 65, gfx),
                                             list(range(max(origin, start), end)))
        # Visible coordinates retain integer/chroma normalization, including
        # negative, partially/fully offscreen positions and draft scaling.
        for x, y in ((-3, -1), (1, 3), (65, 66)):
            for scale in (1, 0.5):
                with self.subTest(x=x, y=y, scale=scale):
                    self.assertEqual(self._active(1, 60, 0, 65, x=x, y=y, scale=scale),
                                     list(range(1, 60)))

    def test_one_frame_clips_and_exact_cut_boundaries(self):
        for origin in (0, 29, 30, 31, 32, 59, 60, 61, 62):
            for start in (0, 1, 2, 59, 60, 61, 62):
                with self.subTest(origin=origin, start=start):
                    self.assertEqual(self._active(start, start + 1, origin, 65),
                                     [start] if start >= origin else [])

    def test_adjacent_and_empty_windows_have_exact_active_sets(self):
        for clips in (((0, 60), (60, 61), (61, 62)),
                      ((1, 60), (61, 62))):
            for origin in (0, 30, 31, 32, 60):
                active = [set(self._active(start, end, origin, 65))
                          for start, end in clips]
                for frame in range(origin, 65):
                    with self.subTest(clips=clips, origin=origin, frame=frame):
                        self.assertEqual([i for i, frames in enumerate(active) if frame in frames],
                                         [i for i, (start, end) in enumerate(clips)
                                          if start <= frame < end])


class TestPassFreshSidecar(unittest.TestCase):
    """_pass_fresh 旁车快路径（纯文件操作，零渲染）。

    语义：完成证书/字节摘要证明媒体完整，<target>.n 内容==段长是额外约束；
    仅有旁车不认证媒体。下方文件负控拒绝裸字节，独立 stub 证书有效后核旁车分支。
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vtx_sc_")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        c = SimpleNamespace(fps=30, spf=1.0 / 30, dur=4.0,
                            w=64, h=64, cache=root, dry=False)
        self.plan = RenderPlan(c=c, draft=False, f=1.0, W=64, H=64,
                               nframes=None, nseg=1, layers=[])
        self.target = root / "p0_deadbeef.mkv"
        self.target.write_bytes(b"x")

    def test_sidecar_semantics(self):
        # 无旁车 → 不命中（截断/中断产物无法伪命中）
        self.assertFalse(self.plan._pass_fresh(self.target, 120, sidecar=True))
        # 旁车数值不符 → 不命中
        self.target.with_suffix(".mkv.n").write_text("119")
        self.assertFalse(self.plan._pass_fresh(self.target, 120, sidecar=True))
        # 旁车匹配也不能认证假的/中断的媒体（issue #34）。
        self.target.with_suffix(".mkv.n").write_text("120")
        self.assertFalse(self.plan._pass_fresh(self.target, 120, sidecar=True))
        # target 空文件 → 不命中（旁车在也挡不住）
        self.target.write_bytes(b"")
        self.assertFalse(self.plan._pass_fresh(self.target, 120, sidecar=True))
        # target 缺失 → 不命中
        self.target.unlink()
        self.assertFalse(self.plan._pass_fresh(self.target, 120, sidecar=True))
        # 非 sidecar 路径同样要求完成证书，而不是裸文件存在
        self.assertFalse(self.plan._pass_fresh(self.target, 120))   # 文件已删
        self.target.write_bytes(b"not a media file")
        self.assertFalse(self.plan._pass_fresh(self.target, 120))
        # Isolate the extra .n contract alongside the shared completion gate.
        # This stub proves sidecar control flow, not real media validity.
        with mock.patch.object(composite, 'media_cache_valid', return_value=True) as valid:
            for text, expected in ((None, False), ('119', False), ('120', True)):
                with self.subTest(certified_media=True, sidecar=text):
                    sidecar = self.target.with_suffix('.mkv.n')
                    if text is None:
                        sidecar.unlink(missing_ok=True)
                    else:
                        sidecar.write_text(text)
                    self.assertEqual(self.plan._pass_fresh(self.target, 120, sidecar=True), expected)
            valid.assert_called_once_with(self.target, 120)

    def test_sidecar_corrupt_text(self):
        self.target.with_suffix(".mkv.n").write_text("not-a-number")
        self.assertFalse(self.plan._pass_fresh(self.target, 120, sidecar=True))
        with mock.patch.object(composite, 'media_cache_valid', return_value=True) as valid:
            self.assertFalse(self.plan._pass_fresh(self.target, 120, sidecar=True))
        valid.assert_not_called()

    def test_sidecar_rejection_precedes_media_io(self):
        sidecar = self.target.with_suffix('.mkv.n')
        for contents in (None, 'not-a-number', '119', '121', ''):
            with self.subTest(sidecar=contents):
                if contents is None:
                    sidecar.unlink(missing_ok=True)
                else:
                    sidecar.write_text(contents)
                with mock.patch.object(composite, 'media_cache_valid',
                                       side_effect=AssertionError('unnecessary media IO')):
                    self.assertFalse(self.plan._pass_fresh(self.target, 120, sidecar=True))

    def test_matching_sidecar_still_revalidates_every_consumer(self):
        self.target.with_suffix('.mkv.n').write_text('120')
        with mock.patch.object(composite, 'media_cache_valid',
                               side_effect=(True, False, True)) as valid:
            self.assertTrue(self.plan._pass_fresh(self.target, 120, sidecar=True))
            self.assertFalse(self.plan._pass_fresh(self.target, 120, sidecar=True))
            self.assertTrue(self.plan._pass_fresh(self.target, 120))
        self.assertEqual(valid.call_args_list, [mock.call(self.target, 120)] * 3)

    def test_matching_sidecar_propagates_completion_gate_errors(self):
        self.target.with_suffix('.mkv.n').write_text('120')
        with mock.patch.object(composite, 'media_cache_valid',
                               side_effect=RuntimeError('completion gate failed')):
            with self.assertRaisesRegex(RuntimeError, 'completion gate failed'):
                self.plan._pass_fresh(self.target, 120, sidecar=True)

    def test_sidecar_changed_during_media_gate_still_fails(self):
        sidecar = self.target.with_suffix('.mkv.n')
        for contents in (None, 'not-a-number', '119', ''):
            with self.subTest(sidecar_after_media_gate=contents):
                sidecar.write_text('120')

                def changed_during_hash(*args):
                    if contents is None:
                        sidecar.unlink()
                    else:
                        sidecar.write_text(contents)
                    return True

                with mock.patch.object(composite, 'media_cache_valid',
                                       side_effect=changed_during_hash) as valid:
                    self.assertFalse(self.plan._pass_fresh(self.target, 120, sidecar=True))
                valid.assert_called_once_with(self.target, 120)


class TestPassKeySubsSeparation(unittest.TestCase):
    """R-03.J2（R12-G1-001）键分离回归：pass 键种子必须掺 with_subs 政策位。

    机理（R12-G1-001 互染，双席互证+联席核准在案）：正式段（with_subs=True，图尾
    subtitles rgba 钉定）与 qc 基准段（with_subs=False，图尾 null）图尾两分支产出
    异质内容，而键种子 prev_key 若只有 a|b|WxH|draft 四元，两路径的 prev_key/层
    身份/合成代码摘要全同源 → 同 pkey 写同一 cache/vseg_pass/p{k}_<pkey>.mkv——
    先渲者占键，后渲者读错域中间物。段级无染（段键 _seg_content_key 的字幕字节面
    已随 with_subs 漂移），染在 pass 级；修复位=_composite_passes prev_key 尾部掺
    |{int(bool(with_subs))}。代价（在案声明）：键变 → 既有 vseg_pass 缓存一次性
    全失效（下次全量冷建），属修复预期。

    驱动形态：c.dry=True 纯计算（dry 分支命令经 Ctx.run 只打印不执行，此处录制）
    ——pass 键自中间物目标文件名 p{k}_{pkey}.mkv 提取，锁产品路径的键计算
    本身，非复制品重算。零 ffmpeg 依赖（pick_vcodec 正式档无 gpu 不探测；
    _pass_fresh 对缺失产物快速 False，不触 ffprobe）。
    """

    def _p0_pkey(self, with_subs):
        class _Ctx:
            pass
        outs = []

        def _run(cmd, need=True):   # dry：只录命令尾元（中间物 .mkv 路径）
            outs.append(str(cmd[-1]))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory(prefix="vtx_pkey_") as td:
            d = Path(td)
            c = _Ctx()
            c.fps, c.spf, c.dur = 30, 1.0 / 30, 4.0
            c.w, c.h, c.dry = 64, 64, True
            c.root = d
            c.cache = d / "cache"
            c.run = _run
            plan = RenderPlan(c=c, draft=False, f=1.0, W=64, H=64,
                              nframes=None, nseg=1, layers=[])
            sl = [{"cl": {"dst_in": 0, "dur": 30}, "gfx": True,
                   "source": PngSequence(d / "seqA", "seqA", 30, 30, 64, 64), "x": 0, "y": 0, "w2": None, "h2": None, "ti": 0},
                  {"cl": {"dst_in": 10, "dur": 30}, "gfx": True,
                   "source": PngSequence(d / "seqB", "seqB", 30, 30, 64, 64), "x": 8, "y": 4, "w2": None, "h2": None, "ti": 0}]
            sfile = None
            if with_subs:
                # 尾支 last and with_subs 触 subtitles=filename=（filter_filename
                # 解析路径），dry 下仍须真实路径——哑 ass 只走命令字符串不真渲
                sfile = d / "seg.ass"
                sfile.write_text("[Script Info]\n", encoding="utf-8")
            plan._composite_passes(sl, d / "seg.mp4", with_subs, a=0, b=120,
                                   sub_file=sfile)
        pks = re.findall(r"p(\d+)_([0-9a-f]{12})\.mkv$", "\n".join(outs), re.M)
        self.assertTrue(pks, f"dry 命令未录得中间物 pass 键: {outs}")
        return dict(pks)

    def test_with_subs_flips_pass_key(self):
        """同 layers 同窗口 with_subs=True/False：pass 键必分离（R12-G1-001 关闭）。"""
        k_true = self._p0_pkey(True)
        k_false = self._p0_pkey(False)
        self.assertIn("0", k_true)
        self.assertIn("0", k_false)
        self.assertNotEqual(k_true["0"], k_false["0"],
                            "with_subs 两路径 pass 键同源——pass 级互染未关闭")

    def test_same_subs_mode_key_stable(self):
        """同 with_subs 复算键稳定：政策位掺入不得破坏温态命中确定性。"""
        self.assertEqual(self._p0_pkey(True), self._p0_pkey(True))
        self.assertEqual(self._p0_pkey(False), self._p0_pkey(False))


_HAVE_FFMPEG_X264 = _have_ffmpeg_x264()


@unittest.skipUnless(_HAVE_FFMPEG_X264, "需要 ffmpeg 与 libx264 编码器")
class TestCrossRunDeterminism(unittest.TestCase):
    """dense-EOF 端点图的跨运行确定性（真渲染，走 RenderPlan.composite 逐层 pass 路径）。

    直接驱动 RenderPlan.composite（vtx 合成唯一实现）而非手拼 ffmpeg 命令——
    锁的是产品路径的 graph 构造（tpad 头/尾垫、enable 界值、pass 键、FFV1 中间物、
    -fps_mode cfr），不是它的近似复制品。b 非 None 且多层 → 必进 _composite_passes。
    VTX_NO_NVENC=1 强制 x264（去 GPU 依赖 + 探测提速）。
    """

    W, H, FPS, DUR = 640, 360, 30, 120      # 主链 120 帧 = 4s
    # (pngdir 索引, dst_in)——窗口端点聚簇 44-46 / 58-60 / 85-89，dur 由序列长度定
    LAYERS = [(0, 0), (1, 30), (0, 44), (1, 58), (2, 85), (1, 100)]
    SEQ_FRAMES = (45, 30, 75)               # 三条 PNG 序列长度（=层 dur）

    def _gen_seqs(self, d: Path):
        for i, n in enumerate(self.SEQ_FRAMES):
            sub = d / f"seq{i}"; sub.mkdir()
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                            "-i", f"testsrc=size={self.W}x{self.H}:rate={self.FPS}"
                                  f":duration={n / self.FPS}",
                            "-start_number", "0", str(sub / "f%04d.png")], check=True)

    def _layers(self, d):
        out = []
        for k, (si, dst_in) in enumerate(self.LAYERS):
            out.append({"cl": {"src": f"s{si}", "src_in": 0, "src_out": self.SEQ_FRAMES[si],
                               "dst_in": dst_in, "dur": self.SEQ_FRAMES[si]},
                        "source": PngSequence(d / f"seq{si}", f"seq{si}", self.SEQ_FRAMES[si], self.FPS, self.W, self.H),
                        "x": (k * 137) % (self.W // 2), "y": (k * 89) % (self.H // 2),
                        "w2": None, "h2": None, "gfx": True, "ti": 0})
        return out

    def _render_md5s(self, a, b, n=4):
        """同一 argv 并发复渲 n 次（并发 = 真实段间并行形态，放大到达顺序扰动）。

        每线程独立 cache 目录（pass 中间物互不共享）：pass 键内容寻址，共享目录时
        n 线程并发写**同一个**中间物文件会互踩——FFV1 大文件写入窗口数十秒，实测
        踩中（读方拿到半截 mkv 帧数自检失败；libx264 qp0 时代窗口小侥幸通过）。
        产品侧经 Ctx.run 单一事务原子落盘（composite._composite_passes），此处再以隔离
        cache 复原「只放大到达顺序、不引入文件竞态」的实验本意；PNG 输入目录
        只读共享。
        """
        with tempfile.TemporaryDirectory(prefix="vtx_det_") as td:
            d = Path(td)
            self._gen_seqs(d)
            sl_all = self._layers(d)
            sl = [L for L in sl_all
                  if L["cl"]["dst_in"] < b and L["cl"]["dst_in"] + L["cl"]["dur"] > a] \
                if a else sl_all

            contexts = [_render_context(d, d / f"cache{idx}", self.W, self.H, self.DUR)
                        for idx in range(n)]

            def _render(idx: int):
                c = contexts[idx]
                plan = RenderPlan(c=c, draft=False, f=1.0, W=self.W, H=self.H,
                                  nframes=None, nseg=1, layers=[])
                out = d / f"run{idx}.mp4"
                plan.composite(sl, out, False, a=a, b=b)
                return hashlib.md5(out.read_bytes()).hexdigest(), self._nb_frames(out)

            with ThreadPoolExecutor(n) as ex:
                results = list(ex.map(_render, range(n)))
            md5s = [m for m, _ in results]
            frames = [f for _, f in results]
            # pass 中间物落盘位置断言（vseg_pass 目录 + 帧数自检过的产物）
            pdir = d / "cache0" / "vseg_pass"
            self.assertTrue(pdir.is_dir(), "逐层 pass 中间物目录未建立")
            return md5s, frames

    def _nb_frames(self, p: Path) -> int:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(p)],
                           capture_output=True, text=True)
        return int(r.stdout.strip())

    def test_full_graph_a0(self):
        """整片形态（a=0）：含 rel_in==0 无头垫分支；4 次并发复渲 md5 全等。"""
        with mock.patch.dict(os.environ, {"VTX_NO_NVENC": "1"}):
            md5s, frames = self._render_md5s(0, self.DUR)
        self.assertEqual(len(set(md5s)), 1, f"整片形态跨运行发散：{md5s}")
        self.assertEqual(set(frames), {self.DUR},
                         f"输出帧数 != 段长（pass/硬钳 extra 帧泄漏/丢帧）：{frames}")

    def test_mid_segment(self):
        """中段形态（a=40,b=100）：含跨段层（start_number 截入）与尾垫退化分支。"""
        with mock.patch.dict(os.environ, {"VTX_NO_NVENC": "1"}):
            md5s, frames = self._render_md5s(40, 100)
        self.assertEqual(len(set(md5s)), 1, f"中段形态跨运行发散：{md5s}")
        self.assertEqual(set(frames), {100 - 40},
                         f"输出帧数 != 段长 60：{frames}")


@unittest.skipUnless(_HAVE_FFMPEG_X264, "需要 ffmpeg 与 libx264 编码器")
class TestPngSourceFrameAlignment(unittest.TestCase):
    """Decode frame IDs: correct counts/PTS alone cannot detect a skipped PNG.

    The original clock cases checked only inside each clip's active interval.
    The half-open case also checks every emitted frame across adjacent clips
    and empty windows; equal MD5s cannot establish correct visibility.
    """

    W, H, FPS, DUR = 160, 96, 30, 300

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vtx_png_clock_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        frames = self.root / "frames"
        frames.mkdir()
        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
        for frame in range(self.DUR):
            # ID zero is the black canvas; PNG n carries n+1 in ten wide bars.
            row = b"".join(bytes((230 if (frame + 1) & (1 << bit) else 20,)) * 48
                           for bit in range(10))
            png = (b"\x89PNG\r\n\x1a\n"
                   + chunk(b"IHDR", struct.pack(">IIBBBBB", self.W, self.H, 8, 2, 0, 0, 0))
                   + chunk(b"IDAT", zlib.compress((b"\0" + row) * self.H))
                   + chunk(b"IEND", b""))
            (frames / f"f{frame:04d}.png").write_bytes(png)
        self.source = PngSequence(frames, "clock", self.DUR, self.FPS, self.W, self.H)
        self.sources = {self.DUR: self.source}
        self.outputs = 0

    def _layer(self, dst=0, y=0, duration=None):
        duration = self.DUR - dst if duration is None else duration
        if duration not in self.sources:
            directory = self.root / f"frames{duration}"
            directory.mkdir()
            for n in range(duration):
                (directory / f"f{n:04d}.png").write_bytes(self.source.frame_path(n).read_bytes())
            self.sources[duration] = PngSequence(directory, f"clock{duration}", duration,
                                                 self.FPS, self.W, self.H)
        return {"cl": {"src": "clock", "dst_in": dst, "dur": duration},
                "source": self.sources[duration], "gfx": True, "x": 0, "y": y,
                "w2": None, "h2": None, "ti": 0}

    def _render(self, layers, a, b, with_subs=False, duration=None):
        self.outputs += 1
        cache = self.root / f"render{self.outputs}"
        cache.mkdir()
        def run(argv, **kwargs):
            return subprocess.run(argv, capture_output=True, check=True, timeout=120)
        duration = self.DUR if duration is None else duration
        c = _render_context(self.root, cache, self.W, self.H, duration)
        plan = RenderPlan(c=c, draft=False, f=1, W=self.W, H=self.H)
        ass = cache / "empty.ass"
        ass.write_text("\n".join(plan.ass_head()), encoding="utf-8")
        out = cache / "output.mp4"
        execute = subprocess.run

        def bounded_execute(argv, **kwargs):
            kwargs.setdefault('timeout', 120)  # preserve this fixture's writer bound
            return execute(argv, **kwargs)

        with mock.patch('vtx.infra.subprocess.run', side_effect=bounded_execute):
            plan.composite(layers, out, with_subs, a=a, b=b, sub_file=ass)
        raw = run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(out),
                   "-f", "rawvideo", "-pix_fmt", "gray", "-"]).stdout
        count = (duration if b is None else b) - a
        self.assertEqual(len(raw), count * self.W * self.H)
        probe = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                               "-show_streams", "-show_frames", "-of", "json", str(out)]).stdout)
        tb = Fraction(probe["streams"][0]["time_base"])
        self.assertEqual([int(f["pts"]) * tb for f in probe["frames"]],
                         [Fraction(n, self.FPS) for n in range(count)])
        def ids(row):
            return [sum(1 << bit for bit in range(10)
                        if raw[n * self.W * self.H + row * self.W + bit * 16 + 8] > 128)
                    for n in range(count)]
        return ids

    def test_default_segments_and_single_graph_decode_every_source_frame(self):
        edges = [0] + composite.choose_cutpoints([(0, self.DUR)], self.DUR,
                                                  composite.DEFAULT_SEGMENTS) + [self.DUR]
        self.assertEqual(edges, [0, 41, 82, 123, 164, 205, 246, 300])
        for a, b in list(zip(edges, edges[1:])) + [(0, None)]:
            with self.subTest(a=a, b=b):
                ids = self._render([self._layer()], a, b, with_subs=True)(24)
                self.assertEqual(ids, list(range(a + 1, (self.DUR if b is None else b) + 1)))

    def test_delayed_source_has_exact_integer_ticks(self):
        # dst=63 catches a half-timebase-only fix that still divides by fps/TB.
        for dst in (1, 63, 111):
            with self.subTest(dst=dst):
                ids = self._render([self._layer(dst)], 0, 150)(24)
                self.assertEqual(ids[dst:], list(range(1, 151 - dst)))

    def test_crossing_sources_remain_aligned_through_passes(self):
        for with_subs in (False, True):
            with self.subTest(with_subs=with_subs):
                ids = self._render([self._layer(), self._layer(7, 48)], 41, 123, with_subs)
                self.assertEqual(ids(24), list(range(42, 124)))
                self.assertEqual(ids(72), list(range(35, 117)))

    def test_half_open_adjacent_clips_and_gap_keep_exact_frame_ids(self):
        # Distinct rows expose an old layer even when its successor is opaque.
        # A separate alpha/source-pixel oracle covers transparent overlap; this
        # fixture exercises the actual single-graph and multi-pass producers.
        stop = 66
        for end in (60, 61, 62):
            layers = [self._layer(1, duration=end - 1),
                      self._layer(end, y=48, duration=1),
                      self._layer(end + 2, y=48, duration=1)]
            for origin, boundary in ((0, None), (0, stop), (31, stop)):
                with self.subTest(end=end, origin=origin, boundary=boundary):
                    ids = self._render(layers, origin, boundary, duration=stop)
                    top = [frame if 1 <= frame < end else 0
                           for frame in range(origin, stop)]
                    bottom = [1 if frame in (end, end + 2) else value
                              for frame, value in zip(range(origin, stop), top)]
                    self.assertEqual(ids(24), top)
                    self.assertEqual(ids(72), bottom)


if __name__ == "__main__":
    unittest.main()
