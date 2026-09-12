#!/usr/bin/env python3
"""tests/test_seg_nvenc.py —— seg 预编码 NVENC 化 + NVENC 会话预算（方案二阶段3）单元测试。

覆盖：
  ① seg_encoder_kind env 边界：缺省 → nvenc（dry 短路不真探测）、VTX_SEG_NVENC=0 →
     x264（独立 A/B 回退开关）、"0" 之外任意值（如 "false"/"1"）→ nvenc——开关语义
     是「仅显式 "0" 回退」，与 _pipeline_enabled 同款单值精确；
  ② seg_key 编码器 token 入键：同 clip 同参数不同 encoder 键必不同（键集漂移防护），
     同 encoder 键稳定；alpha（prores）与 opaque 同 encoder 仍由 need_alpha 位区分；
  ③ nvenc_budget 信号量并发：预算 2 时 8 线程各占 1 槽，峰值并发 ≤2（计数器断言，
     不真编码——只测上下文管理器本身）；dry 上下文直通不占槽；
  ④ nvenc_ok(use_memo=True) 进程级 memo（P2-1 复审）：真探测仅首次、结果恒定，
     False 结论同样入 memo，dry 短路在 memo 之后（键口径进程内恒一致）；默认路径
     不 memo（pick_vcodec 告警即时性）。
运行：env python3 -m unittest discover tests -v（本机 ZCode 环境必须 env python3 前缀：
AppImage 劫持 argv[0]，裸 python3 的 sys.executable 指向错误解释器，见全局 AGENTS.md）
"""
import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "vtx"))

import encoders
from encoders import (nvenc_budget, nvenc_budget_value, reset_nvenc_budget,
                      reset_nvenc_ok_memo, seg_encoder_kind)
from hygiene import seg_key


class _KindCtx:
    """seg_encoder_kind 最小上下文：dry=True 使 nvenc_ok 短路（不真探测、无需 ffmpeg）。"""
    dry = True


class _KeyCtx:
    """seg_key 最小上下文：键函数只用 fps/w/h/src_path（对齐 timeline_diff.DiffCtx 口径）。"""
    fps = 30
    w = 1920
    h = 1080

    def src_path(self, sid):
        # 真实文件（_fp 做 stat+首尾 64KB 采样哈希）：README.md 稳定存在即可，内容无关
        return REPO / "README.md"


_CL = {"src": "README.md", "src_in": 0, "src_out": 90, "dur": 90}


class TestSegEncoderKind(unittest.TestCase):
    def setUp(self):
        reset_nvenc_budget()
        reset_nvenc_ok_memo()   # 类内 dry 短路判定不得被前序用例的 memo 污染

    def test_default_is_nvenc(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VTX_SEG_NVENC", None)
            self.assertEqual(seg_encoder_kind(_KindCtx()), "nvenc")

    def test_explicit_zero_falls_back_x264(self):
        with mock.patch.dict(os.environ, {"VTX_SEG_NVENC": "0"}):
            self.assertEqual(seg_encoder_kind(_KindCtx()), "x264")

    def test_nonzero_values_still_nvenc(self):
        # 仅显式 "0" 回退；"false"/空串/"1" 等任意其他值均走 NVENC（单值精确语义）
        for v in ("false", "", "1", "00x"):
            with mock.patch.dict(os.environ, {"VTX_SEG_NVENC": v}):
                self.assertEqual(seg_encoder_kind(_KindCtx()), "nvenc", f"VTX_SEG_NVENC={v!r}")

    def test_budget_value_env(self):
        with mock.patch.dict(os.environ, {"VTX_NVENC_BUDGET": "3"}):
            self.assertEqual(nvenc_budget_value(), 3)
        with mock.patch.dict(os.environ, {"VTX_NVENC_BUDGET": "bogus"}):
            self.assertEqual(nvenc_budget_value(), 8)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VTX_NVENC_BUDGET", None)
            self.assertEqual(nvenc_budget_value(), 8)


class TestNvencOkMemo(unittest.TestCase):
    """P2-1（方案二阶段3 复审）：nvenc_ok(use_memo=True) 进程级 memo——不真探测，
    用假 ctx 计数探测次数，断言 memo 后只探一次且结果恒定；默认路径（pick_vcodec 用）
    不 memo，每次都探。"""

    def setUp(self):
        reset_nvenc_ok_memo()

    def _ctx_with_probe(self, fail):
        calls = []
        class Ctx:
            dry = False
            def run(self, cmd, need=True):
                calls.append(1)
                class R: returncode = 1 if fail else 0
                return R()
        return Ctx(), calls

    def test_memo_probes_once_and_stays(self):
        c, calls = self._ctx_with_probe(fail=False)
        self.assertTrue(encoders.nvenc_ok(c, use_memo=True))
        self.assertTrue(encoders.nvenc_ok(c, use_memo=True))
        self.assertTrue(encoders.nvenc_ok(c, use_memo=True))
        self.assertEqual(len(calls), 1)          # 真探测仅首次
        self.assertEqual(seg_encoder_kind(c), "nvenc")
        self.assertEqual(len(calls), 1)          # seg_encoder_kind 复用 memo

    def test_memo_false_result_also_memoed(self):
        c, calls = self._ctx_with_probe(fail=True)
        self.assertFalse(encoders.nvenc_ok(c, use_memo=True))
        self.assertFalse(encoders.nvenc_ok(c, use_memo=True))
        self.assertEqual(len(calls), 1)
        self.assertEqual(seg_encoder_kind(c), "x264")
        self.assertEqual(len(calls), 1)

    def test_no_memo_probes_every_time(self):
        # 默认 use_memo=False（pick_vcodec 口径）：不 memo，逐次真探测
        c, calls = self._ctx_with_probe(fail=False)
        for _ in range(3):
            self.assertTrue(encoders.nvenc_ok(c))
        self.assertEqual(len(calls), 3)

    def test_memo_precedes_dry_shortcircuit(self):
        # 先有真探测结论（False）后，dry ctx 的 seg_encoder_kind 也同值（键口径一致）
        c, _ = self._ctx_with_probe(fail=True)
        self.assertFalse(encoders.nvenc_ok(c, use_memo=True))
        class DryCtx: dry = True
        self.assertEqual(seg_encoder_kind(DryCtx()), "x264")


class TestSegKeyEncoderToken(unittest.TestCase):
    def test_different_encoder_different_key(self):
        c = _KeyCtx()
        base = dict(windowed=False, need_alpha=False)
        k_nv = seg_key(c, _CL, 0, 0, encoder="nvenc", **base)
        k_x = seg_key(c, _CL, 0, 0, encoder="x264", **base)
        k_pr = seg_key(c, _CL, 0, 0, encoder="prores", **base)
        self.assertNotEqual(k_nv, k_x)
        self.assertNotEqual(k_nv, k_pr)
        self.assertNotEqual(k_x, k_pr)

    def test_same_encoder_stable(self):
        c = _KeyCtx()
        self.assertEqual(seg_key(c, _CL, 0, 0, False, False, "nvenc"),
                         seg_key(c, _CL, 0, 0, False, False, "nvenc"))
        # ti/ci 不参与键（既有语义，token 化后不得回退）
        self.assertEqual(seg_key(c, _CL, 0, 0, False, False, "nvenc"),
                         seg_key(c, _CL, 5, 9, False, False, "nvenc"))

    def test_alpha_bit_still_distinguishes(self):
        c = _KeyCtx()
        self.assertNotEqual(seg_key(c, _CL, 0, 0, False, True, "prores"),
                            seg_key(c, _CL, 0, 0, False, False, "prores"))


class TestNvencBudget(unittest.TestCase):
    def setUp(self):
        reset_nvenc_budget()

    def test_concurrency_capped_at_budget(self):
        # 预算 2：8 线程各占 1 槽做 20ms 伪工作，峰值并发必须 ≤2 且全部完成
        with mock.patch.dict(os.environ, {"VTX_NVENC_BUDGET": "2"}):
            reset_nvenc_budget()
            lock = threading.Lock()
            cur = peak = 0
            # _KindCtx 是 dry 短路 ctx，并发上限须用非 dry 假 ctx 才真正过信号量
            class LiveCtx:
                dry = False
            def work2(_):
                nonlocal cur, peak
                with nvenc_budget(LiveCtx()):
                    with lock:
                        cur += 1
                        peak = max(peak, cur)
                    threading.Event().wait(0.02)
                    with lock:
                        cur -= 1
            with ThreadPoolExecutor(8) as ex:
                list(ex.map(work2, range(8)))
            self.assertLessEqual(peak, 2)
            self.assertEqual(cur, 0)

    def test_dry_bypasses_budget(self):
        # dry 上下文直通：预算 1 下 4 路并发全部立即通过（任务只打印不执行，不占槽）
        with mock.patch.dict(os.environ, {"VTX_NVENC_BUDGET": "1"}):
            reset_nvenc_budget()
            bar = threading.Barrier(4, timeout=5)   # 4 路须同时进入 with 体才算直通
            def work(_):
                with nvenc_budget(_KindCtx()):
                    bar.wait()
            with ThreadPoolExecutor(4) as ex:
                list(ex.map(work, range(4)))   # Barrier 超时会抛 BrokenBarrierError → 测试失败


if __name__ == "__main__":
    unittest.main()
