#!/usr/bin/env python3
"""tests/media/test_seg_nvenc.py —— seg 预编码 NVENC 化 + NVENC 会话预算（方案二阶段3）单元测试。

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
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))   # repo 根注入（v2 §1.4 包导入，不再注入 repo/vtx）

from vtx.media import encoders
from vtx.media.encoders import (nvenc_budget, nvenc_budget_value, reset_nvenc_budget,
                          reset_nvenc_ok_memo, seg_encoder_kind)
from vtx.cache.hygiene import seg_key


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


class _SignaledRLock:
    """Observe a named thread before acquisition, not after a blocked probe."""
    def __init__(self, thread_name, arrived):
        self.lock = threading.RLock()
        self.thread_name, self.arrived = thread_name, arrived

    def __enter__(self):
        if threading.current_thread().name == self.thread_name:
            self.arrived.set()
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        self.lock.release()


def _isolate_mock_nvenc_environment(test):
    # Only dry/fake-probe fixtures opt out; restore inherited CPU-only guards
    # even if a later setUp step or the test body raises.
    environment = mock.patch.dict(os.environ, {}, clear=False)
    environment.start()
    test.addCleanup(environment.stop)
    for name in ('VTX_NO_NVENC', 'VTX_SEG_NVENC'):
        os.environ.pop(name, None)


class TestSegEncoderKind(unittest.TestCase):
    def setUp(self):
        _isolate_mock_nvenc_environment(self)
        reset_nvenc_budget()
        reset_nvenc_ok_memo()   # 类内 dry 短路判定不得被前序用例的 memo 污染

    def test_mock_environment_restores_inherited_cpu_guards(self):
        with mock.patch.dict(os.environ, {'VTX_NO_NVENC': '1', 'VTX_SEG_NVENC': '0'}):
            before = dict(os.environ)
            probe = unittest.TestCase()
            try:
                _isolate_mock_nvenc_environment(probe)
                self.assertNotIn('VTX_NO_NVENC', os.environ)
                self.assertNotIn('VTX_SEG_NVENC', os.environ)
            finally:
                probe.doCleanups()
            self.assertTrue(dict(os.environ) == before, 'fixture did not restore inherited environment')

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
        _isolate_mock_nvenc_environment(self)
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

    def test_concurrent_first_calls_share_encoder_and_cache_key(self):
        for first_ok in (True, False):
            with self.subTest(first_probe_succeeded=first_ok):
                reset_nvenc_ok_memo()
                first_probe, second_arrived, release = (threading.Event() for _ in range(3))
                calls, results, errors = [], {}, []

                class Context(_KeyCtx):
                    dry = False

                    def run(self, command, need=True):
                        index = len(calls)
                        calls.append(threading.current_thread().name)
                        if index == 0:
                            first_probe.set()
                            if not release.wait(3):
                                raise AssertionError('first probe was not released')
                        else:
                            # Baseline reaches a second probe; the serialized
                            # path signals from lock entry instead, never from
                            # a two-probe barrier that would deadlock the fix.
                            second_arrived.set()
                        ok = first_ok if index == 0 else not first_ok
                        return SimpleNamespace(returncode=0 if ok else 1)

                def choose():
                    try:
                        c = Context()
                        kind = seg_encoder_kind(c)
                        results[threading.current_thread().name] = (
                            kind, seg_key(c, _CL, 0, 0, False, False, kind))
                    except BaseException as error:
                        errors.append(error)

                with mock.patch.object(encoders, '_NVENC_OK_MEMO_LOCK',
                                       _SignaledRLock('gc', second_arrived)):
                    first = threading.Thread(target=choose, name='prepare', daemon=True)
                    second = threading.Thread(target=choose, name='gc', daemon=True)
                    first.start()
                    try:
                        self.assertTrue(first_probe.wait(3))
                        second.start()
                        self.assertTrue(second_arrived.wait(3))
                    finally:
                        release.set()
                        first.join(3)
                        if second.ident is not None:
                            second.join(3)
                    self.assertFalse(first.is_alive() or second.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(calls, ['prepare'])
                self.assertEqual(results['prepare'], results['gc'])
                self.assertEqual(results['prepare'][0], 'nvenc' if first_ok else 'x264')

    def test_probe_exception_releases_lock_without_caching(self):
        for error in (RuntimeError('probe failed'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                reset_nvenc_ok_memo()
                c = SimpleNamespace(dry=False, run=mock.Mock(side_effect=[
                    error, SimpleNamespace(returncode=0)]))
                result, failures = [], []

                def retry():
                    try:
                        result.append(encoders.nvenc_ok(c, use_memo=True))
                    except BaseException as failure:
                        failures.append(failure)

                with mock.patch.object(encoders, '_NVENC_OK_MEMO_LOCK', threading.RLock()):
                    with self.assertRaises(type(error)):
                        encoders.nvenc_ok(c, use_memo=True)
                    self.assertIsNone(encoders._NVENC_OK_MEMO)
                    worker = threading.Thread(target=retry, daemon=True)
                    worker.start()
                    worker.join(3)
                    self.assertFalse(worker.is_alive(), 'failed probe retained the memo lock')
                self.assertEqual(failures, [])
                self.assertEqual(result, [True])
                self.assertEqual(c.run.call_count, 2)

    def test_nonmemo_and_disabled_calls_bypass_memo_lock(self):
        c, calls = self._ctx_with_probe(fail=False)
        with mock.patch.object(encoders, '_NVENC_OK_MEMO_LOCK') as lock:
            lock.__enter__.side_effect = AssertionError('unexpected memo lock')
            for _ in range(3):
                self.assertTrue(encoders.nvenc_ok(c))
            with mock.patch.dict(os.environ, {'VTX_NO_NVENC': '1'}):
                self.assertFalse(encoders.nvenc_ok(c, use_memo=True))
        self.assertEqual(len(calls), 3)
        self.assertIsNone(encoders._NVENC_OK_MEMO)

    def test_dry_probe_does_not_initialize_memo(self):
        c = SimpleNamespace(dry=True, run=mock.Mock(side_effect=AssertionError('dry probe')))
        self.assertTrue(encoders.nvenc_ok(c, use_memo=True))
        self.assertIsNone(encoders._NVENC_OK_MEMO)
        c.run.assert_not_called()

    def test_same_thread_reset_in_probe_callback_does_not_deadlock(self):
        result, errors = [], []

        def probe(command, need=True):
            reset_nvenc_ok_memo()
            return SimpleNamespace(returncode=0)

        def work():
            try:
                result.append(encoders.nvenc_ok(SimpleNamespace(dry=False, run=probe),
                                               use_memo=True))
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(encoders, '_NVENC_OK_MEMO_LOCK', threading.RLock()):
            worker = threading.Thread(target=work, daemon=True)
            worker.start()
            worker.join(3)
            self.assertFalse(worker.is_alive(), 'same-thread reset deadlocked')
        self.assertEqual(errors, [])
        self.assertEqual(result, [True])
        self.assertIs(encoders._NVENC_OK_MEMO, True)

    def test_other_thread_reset_clears_finished_probe(self):
        entered, resetting, release = (threading.Event() for _ in range(3))
        results, errors = [], []

        def probe(command, need=True):
            entered.set()
            if not release.wait(3):
                raise AssertionError('probe was not released')
            return SimpleNamespace(returncode=0)

        def work():
            try:
                results.append(encoders.nvenc_ok(SimpleNamespace(dry=False, run=probe),
                                                use_memo=True))
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(encoders, '_NVENC_OK_MEMO_LOCK',
                               _SignaledRLock('reset', resetting)):
            worker = threading.Thread(target=work, daemon=True)
            reset = threading.Thread(target=reset_nvenc_ok_memo, name='reset', daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                reset.start()
                self.assertTrue(resetting.wait(3))
            finally:
                release.set()
                worker.join(3)
                if reset.ident is not None:
                    reset.join(3)
            self.assertFalse(worker.is_alive() or reset.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [True])
        self.assertIsNone(encoders._NVENC_OK_MEMO)
        c, calls = self._ctx_with_probe(fail=True)
        self.assertFalse(encoders.nvenc_ok(c, use_memo=True))
        self.assertEqual(len(calls), 1)


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
        self.addCleanup(reset_nvenc_budget)
        environment = mock.patch.dict(os.environ, {'VTX_NVENC_BUDGET': '8'})
        environment.start(); self.addCleanup(environment.stop)
        guard = mock.patch.object(encoders, 'vram_guard_tier', return_value=8)
        guard.start(); self.addCleanup(guard.stop)

    def test_invalid_request_never_initializes_allocator(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), \
                    mock.patch.object(encoders, '_nvenc_pool') as pool, \
                    self.assertRaises(ValueError):
                with nvenc_budget(SimpleNamespace(dry=False), value):
                    self.fail('invalid request entered body')
            pool.assert_not_called()

    def test_batch_uses_one_frozen_effective_capacity(self):
        for configured, tier, expected in ((8, 1, 1), (2, 8, 2), (8, 4, 4)):
            with self.subTest(configured=configured, tier=tier):
                reset_nvenc_budget()
                with mock.patch.dict(os.environ, {'VTX_NVENC_BUDGET': str(configured)}), \
                        mock.patch.object(encoders, 'vram_guard_tier', return_value=tier) as guard:
                    with nvenc_budget(SimpleNamespace(dry=False), 8) as granted:
                        self.assertEqual(granted, expected)
                        self.assertFalse(encoders._nvenc_pool()[0].acquire(blocking=False))
                    with mock.patch.dict(os.environ, {'VTX_NVENC_BUDGET': '99'}), \
                            nvenc_budget(SimpleNamespace(dry=False), 8) as granted:
                        self.assertEqual(granted, expected)
                    guard.assert_called_once()

    def test_concurrent_batches_do_not_split_reservations(self):
        semaphore = threading.Semaphore(2)
        reservation = threading.Lock()
        first_token = threading.Event(); second_request = threading.Event()
        release_first = threading.Event(); first_body = threading.Event()
        second_body = threading.Event(); acquisitions = []; failures = []
        class RecordingLock:
            def __enter__(self):
                if threading.current_thread().name == 'second':
                    second_request.set()
                reservation.acquire()
            def __exit__(self, *args):
                reservation.release()
        class Slots:
            def acquire(self, **kwargs):
                got = semaphore.acquire(**kwargs)
                if got:
                    name = threading.current_thread().name
                    acquisitions.append(name)
                    if len(acquisitions) == 1:
                        first_token.set()
                        if not second_request.wait(2):
                            raise RuntimeError('second batch never requested slots')
                return got
            def release(self):
                semaphore.release()
        def work(first):
            try:
                with nvenc_budget(SimpleNamespace(dry=False), 2) as granted:
                    self.assertEqual(granted, 2)
                    (first_body if first else second_body).set()
                    if first and not release_first.wait(2):
                        raise RuntimeError('test did not release first batch')
            except BaseException as error:
                failures.append(error)
        with mock.patch.object(encoders, '_nvenc_pool', return_value=(Slots(), 2)), \
                mock.patch.object(encoders, '_NVENC_RESERVATION_LOCK', RecordingLock()):
            first = threading.Thread(target=work, args=(True,), name='first', daemon=True)
            second = threading.Thread(target=work, args=(False,), name='second', daemon=True)
            first.start()
            try:
                self.assertTrue(first_token.wait(2))
                second.start()
                self.assertTrue(first_body.wait(2))
                self.assertFalse(second_body.is_set())
            finally:
                release_first.set()
                first.join(3)
                if second.ident is not None:
                    second.join(3)
            self.assertFalse(first.is_alive() or second.is_alive(), 'batch reservation deadlocked')
        self.assertEqual(failures, [])
        self.assertEqual(acquisitions, ['first', 'first', 'second', 'second'])
        self.assertTrue(second_body.is_set())

    def test_partial_acquisition_and_body_failure_release_slots(self):
        c = SimpleNamespace(dry=False)
        sem = mock.Mock()
        sem.acquire.side_effect = [True, KeyboardInterrupt()]
        with mock.patch.object(encoders, '_nvenc_pool', return_value=(sem, 2)), \
                self.assertRaises(KeyboardInterrupt):
            with nvenc_budget(c, 2):
                self.fail('partial reservation must not enter body')
        self.assertEqual(sem.release.call_count, 1)
        with mock.patch.dict(os.environ, {'VTX_NVENC_BUDGET': '2'}):
            with self.assertRaisesRegex(RuntimeError, 'body'):
                with nvenc_budget(c, 2):
                    raise RuntimeError('body')
            with nvenc_budget(c, 2) as granted:
                self.assertEqual(granted, 2)

    def test_segment_executor_matches_actual_grant_and_x264_bypasses(self):
        import tempfile
        from vtx.pipeline import composite
        for encoder, configured, tier, expected in (('NVENC p7', 8, 1, 1),
                ('NVENC p7', 2, 8, 2), ('NVENC p7', 8, 2, 2), ('x264', 1, 1, 4)):
            with self.subTest(encoder=encoder, tier=tier), tempfile.TemporaryDirectory() as temp:
                reset_nvenc_budget()
                c = SimpleNamespace(root=Path(temp), cache=Path(temp), dur=4, dry=False, gpu=True, run=mock.Mock())
                plan = composite.RenderPlan(c, False, 1, 2, 2, layers=[], nseg=4)
                workers = []
                class Executor:
                    def __init__(self, count): workers.append(count)
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def submit(self, fn, *args):
                        future = Future()
                        try: future.set_result(fn(*args))
                        except BaseException as exc: future.set_exception(exc)
                        return future
                    def map(self, fn, items): return map(fn, items)
                with mock.patch.dict(os.environ, {'VTX_NVENC_BUDGET': str(configured)}), \
                        mock.patch.object(composite, 'choose_cutpoints', return_value=[1, 2, 3]), \
                        mock.patch.object(composite, 'pick_vcodec', return_value=([], encoder)), \
                        mock.patch.object(composite, 'nvenc_concurrency', side_effect=lambda c, n: n), \
                        mock.patch.object(composite.os, 'cpu_count', return_value=16), \
                        mock.patch.object(composite, 'ThreadPoolExecutor', Executor), \
                        mock.patch.object(composite, 'media_cache_valid', return_value=False), \
                        mock.patch.object(plan, 'composite'), \
                        mock.patch.object(composite.subprocess, 'run', return_value=SimpleNamespace(stdout='4')), \
                        mock.patch.object(encoders, 'vram_guard_tier', return_value=tier) as guard:
                    plan.render_segmented(Path(temp) / 'out.mp4', with_subs=False)
                self.assertEqual(workers, [expected])
                self.assertEqual(guard.call_count, 0 if encoder == 'x264' else 1)


    def test_native_arm64_x264_schedules_heavier_segments_first_and_reorders_results(self):
        import tempfile
        from vtx.pipeline import composite

        with tempfile.TemporaryDirectory() as temp:
            c = SimpleNamespace(root=Path(temp), cache=Path(temp), dur=12,
                                dry=False, gpu=False, run=mock.Mock())
            layers = [
                {"cl": {"dst_in": 0, "dur": 12}},
                {"cl": {"dst_in": 0, "dur": 5}},
                {"cl": {"dst_in": 7, "dur": 5}},
            ]
            plan = composite.RenderPlan(c, False, 1, 1280, 720,
                                        layers=layers, nseg=4)
            submitted, worker_counts, observed_ranges = [], [], []

            class Executor:
                def __init__(self, count): worker_counts.append(count)
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def submit(self, fn, *args):
                    submitted.append(args[0])
                    future = Future()
                    try: future.set_result(fn(*args))
                    except BaseException as exc: future.set_exception(exc)
                    return future
                def map(self, fn, items): return map(fn, items)

            class Observation:
                def __init__(self, metadata): self.metadata = metadata
                def call(self, fn, *args):
                    observed_ranges.append(self.metadata["frame_range"])
                    return fn(*args)

            with mock.patch.object(composite, 'choose_cutpoints', return_value=[2, 5, 7]), \
                    mock.patch.object(composite, 'pick_vcodec', return_value=([], 'x264')), \
                    mock.patch.object(composite, 'platform') as host_platform, \
                    mock.patch.object(composite.sys, 'platform', 'darwin'), \
                    mock.patch.object(composite.os, 'cpu_count', return_value=8), \
                    mock.patch.object(composite, 'ThreadPoolExecutor', Executor), \
                    mock.patch.object(composite.events, 'task', side_effect=lambda c, kind, metadata: Observation(metadata)), \
                    mock.patch.object(plan, '_seg_content_key', return_value='key'), \
                    mock.patch.object(composite, 'media_cache_valid', return_value=False), \
                    mock.patch.object(plan, 'composite'), \
                    mock.patch.object(composite.subprocess, 'run', return_value=SimpleNamespace(stdout='12')):
                host_platform.machine.return_value = 'arm64'
                plan.render_segmented(Path(temp) / 'out.mp4', with_subs=False)

            self.assertEqual(worker_counts, [4])
            # Estimated work: [4, 6, 4, 10]; ties use the original segment index.
            self.assertEqual(submitted, [3, 1, 0, 2])
            self.assertEqual(observed_ranges, [[7, 12], [2, 5], [0, 2], [5, 7]])
            entries = (Path(temp) / 'vseg' / 'list.txt').read_text().splitlines()
            self.assertEqual([Path(line[6:-1]).name.split('_')[0] for line in entries],
                             ['seg00', 'seg01', 'seg02', 'seg03'])

    def test_native_arm64_x264_failure_waits_for_running_segments(self):
        import tempfile
        from vtx.pipeline import composite

        with tempfile.TemporaryDirectory() as temp:
            c = SimpleNamespace(root=Path(temp), cache=Path(temp), dur=4,
                                dry=False, gpu=False, run=mock.Mock())
            plan = composite.RenderPlan(c, False, 1, 640, 480, layers=[], nseg=4)
            barrier = threading.Barrier(4)
            finished = set()
            lock = threading.Lock()

            def composite_segment(_sl, _out, _subs, *, a, b, **_kwargs):
                barrier.wait(timeout=3)
                if a == 0:
                    raise RuntimeError('segment failure')
                with lock:
                    finished.add(a)

            with mock.patch.object(composite, 'choose_cutpoints', return_value=[1, 2, 3]), \
                    mock.patch.object(composite, 'pick_vcodec', return_value=([], 'x264')), \
                    mock.patch.object(composite.platform, 'machine', return_value='arm64'), \
                    mock.patch.object(composite.sys, 'platform', 'darwin'), \
                    mock.patch.object(composite.os, 'cpu_count', return_value=8), \
                    mock.patch.object(plan, '_seg_content_key', return_value='key'), \
                    mock.patch.object(composite, 'media_cache_valid', return_value=False), \
                    mock.patch.object(plan, 'composite', side_effect=composite_segment), \
                    mock.patch.object(composite.subprocess, 'run', return_value=SimpleNamespace(stdout='4')):
                with self.assertRaisesRegex(RuntimeError, 'segment failure'):
                    plan.render_segmented(Path(temp) / 'out.mp4', with_subs=False)

            self.assertEqual(finished, {1, 2, 3})
            c.run.assert_not_called()

    def test_native_arm64_x264_failure_cancels_pending_before_join(self):
        import tempfile
        from vtx.pipeline import composite

        for phase in ('result', 'submit'):
            for error_type in (RuntimeError, SystemExit, KeyboardInterrupt):
                with self.subTest(phase=phase, error=error_type.__name__), \
                        tempfile.TemporaryDirectory() as temp:
                    failure = error_type('segment failure')
                    c = SimpleNamespace(root=Path(temp), cache=Path(temp), dur=10,
                                        dry=False, gpu=False, run=mock.Mock())
                    plan = composite.RenderPlan(c, False, 1, 640, 480, layers=[], nseg=10)
                    submitted, started, finished, exits, worker_counts = {}, [], set(), [], []

                    def composite_segment(_sl, _out, _subs, *, a, b, **_kwargs):
                        if phase == 'result' and a == 0:
                            raise failure
                        finished.add(a)

                    def finish(future, fn, si):
                        try:
                            future.set_result(fn(si))
                        except BaseException as exc:
                            future.set_exception(exc)

                    class Executor:
                        # Real Future states, deterministic worker scheduling:
                        # four running jobs and five pending jobs at the failure.
                        def __init__(self, count):
                            self.count = count
                            worker_counts.append(count)
                        def __enter__(self): return self
                        def submit(self, fn, si):
                            if phase == 'submit' and si == 9:
                                raise failure
                            future = Future()
                            future.cancel = mock.Mock(wraps=future.cancel)
                            future.result = mock.Mock(wraps=future.result)
                            submitted[si] = future, fn
                            if sum(f.running() for f, _ in submitted.values()) < self.count:
                                future.set_running_or_notify_cancel()
                                started.append(si)
                            if phase == 'result' and si == 0:
                                finish(future, fn, si)
                            return future
                        def __exit__(self, _type, error, _traceback):
                            exits.append((error, set(finished),
                                          {si for si, (f, _) in submitted.items()
                                           if f.cancelled()}))
                            # Like shutdown(wait=True), drain every job that was
                            # not cancelled; it must not cancel pending work itself.
                            for si, (future, fn) in submitted.items():
                                if future.done():
                                    continue
                                if not future.running():
                                    future.set_running_or_notify_cancel()
                                    started.append(si)
                                finish(future, fn, si)

                    with mock.patch.object(composite, 'choose_cutpoints', return_value=list(range(1, 10))), \
                            mock.patch.object(composite, 'pick_vcodec', return_value=([], 'x264')), \
                            mock.patch.object(composite.platform, 'machine', return_value='arm64'), \
                            mock.patch.object(composite.sys, 'platform', 'darwin'), \
                            mock.patch.object(composite.os, 'cpu_count', return_value=16), \
                            mock.patch.object(composite, 'ThreadPoolExecutor', Executor), \
                            mock.patch.object(plan, '_seg_content_key', return_value='key'), \
                            mock.patch.object(composite, 'media_cache_valid', return_value=False), \
                            mock.patch.object(plan, 'composite', side_effect=composite_segment), \
                            mock.patch.object(composite.subprocess, 'run') as probe:
                        with self.assertRaises(error_type) as caught:
                            plan.render_segmented(Path(temp) / 'out.mp4', with_subs=False)

                    self.assertIs(caught.exception, failure)
                    self.assertEqual(worker_counts, [4])
                    running = set(range(1, 5)) if phase == 'result' else set(range(4))
                    pending = set(range(5, 10)) if phase == 'result' else set(range(4, 9))
                    self.assertEqual(exits, [(failure, set(), pending)])
                    self.assertEqual(finished, running)
                    self.assertEqual(started, list(range(5 if phase == 'result' else 4)))
                    self.assertEqual(list(submitted), list(range(10 if phase == 'result' else 9)))
                    for si, (future, _) in submitted.items():
                        future.cancel.assert_called_once_with()
                        self.assertEqual(future.result.call_count,
                                         int(phase == 'result' and si == 0))
                        self.assertTrue(future.done())
                    c.run.assert_not_called()
                    probe.assert_not_called()
                    self.assertFalse((Path(temp) / 'vseg' / 'list.txt').exists())

    def test_x264_worker_expansion_requires_native_arm64_small_canvas_known_cpu_and_live_run(self):
        import tempfile
        from vtx.pipeline import composite

        cases = [
            ('eligible', 'darwin', 'arm64', 1280, 720, 8, False, 4),
            ('unknown cpu', 'darwin', 'arm64', 1280, 720, None, False, 2),
            ('other os', 'linux', 'arm64', 1280, 720, 8, False, 2),
            ('other arch', 'darwin', 'x86_64', 1280, 720, 8, False, 2),
            ('4k canvas', 'darwin', 'arm64', 3840, 2160, 8, False, 2),
            ('dry run', 'darwin', 'arm64', 1280, 720, 8, True, 2),
        ]
        for name, os_name, arch, width, height, cpus, dry, expected in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temp:
                c = SimpleNamespace(root=Path(temp), cache=Path(temp), dur=4,
                                    dry=dry, gpu=False, run=mock.Mock())
                plan = composite.RenderPlan(c, False, 1, width, height, layers=[], nseg=4)
                worker_counts = []

                class Executor:
                    def __init__(self, count): worker_counts.append(count)
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def submit(self, fn, *args):
                        future = Future()
                        try: future.set_result(fn(*args))
                        except BaseException as exc: future.set_exception(exc)
                        return future
                    def map(self, fn, items): return map(fn, items)

                with mock.patch.object(composite, 'choose_cutpoints', return_value=[1, 2, 3]), \
                        mock.patch.object(composite, 'pick_vcodec', return_value=([], 'x264')), \
                        mock.patch.object(composite, 'platform') as host_platform, \
                        mock.patch.object(composite.sys, 'platform', os_name), \
                        mock.patch.object(composite.os, 'cpu_count', return_value=cpus), \
                        mock.patch.object(composite, 'ThreadPoolExecutor', Executor), \
                        mock.patch.object(plan, '_seg_content_key', return_value='key'), \
                        mock.patch.object(composite, 'media_cache_valid', return_value=True), \
                        mock.patch.object(composite.subprocess, 'run', return_value=SimpleNamespace(stdout='4')):
                    host_platform.machine.return_value = arch
                    plan.render_segmented(Path(temp) / 'out.mp4', with_subs=False)
                self.assertEqual(worker_counts, [] if dry else [expected])

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


class TestPrepareProducerDedup(unittest.TestCase):
    """#208: one producer per invocation, without losing logical consumers."""

    def setUp(self):
        import tempfile
        from vtx.pipeline import composite
        self.composite = composite
        temporary = tempfile.TemporaryDirectory(prefix='vtx-seg-producers-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'a.mp4').write_bytes(b'first source')
        (self.root / 'b.mp4').write_bytes(b'second source')
        self.clips = [dict(src='a', src_in=0, src_out=6, dur=6, dst_in=i * 6)
                      for i in range(12)]
        self.c = SimpleNamespace(
            root=self.root, cache=self.root / 'cache', fps=30, spf=1 / 30,
            w=64, h=48, dry=False,
            tl={'video_tracks': [{'kind': 'broll', 'clips': self.clips}]},
            src_path=lambda sid: self.root / (sid + '.mp4'),
            probe=lambda path: {'w': 64, 'h': 48},
            crop169=mock.Mock(return_value='crop=64:36:0:6'),
            verify_inputs=mock.Mock())
        for name, target, kwargs in (
                ('cache', 'media_cache_valid', {'return_value': False}),
                ('kind', 'seg_encoder_kind', {'return_value': 'x264'}),
                ('runner', 'run_parallel', {})):
            patch = mock.patch.object(composite, target, **kwargs)
            setattr(self, name, patch.start())
            self.addCleanup(patch.stop)

    def prepare(self):
        layers = self.composite.prepare_layers(self.c)
        return layers, self.runner.call_args.args[1]

    def test_twelve_cold_consumers_share_exactly_one_producer(self):
        layers, tasks = self.prepare()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(len(layers), 12)
        self.assertEqual([layer['cl'] for layer in layers], self.clips)
        self.assertEqual([layer['cl']['dst_in'] for layer in layers], list(range(0, 72, 6)))
        self.assertEqual(len({layer['seg'] for layer in layers}), 1)
        self.assertTrue(all(layer['ti'] == 0 and not layer['gfx'] for layer in layers))
        self.assertEqual(self.cache.call_count, 12)  # do not change validation lifetime
        self.c.verify_inputs.assert_called_once_with('video')
        self.assertFalse(tasks[0][2])
        self.assertEqual(tasks[0][1][-1], str(layers[0]['seg']))

    def test_equal_production_across_tracks_retains_track_order(self):
        self.c.tl['video_tracks'] = [
            {'kind': 'broll', 'clips': self.clips[:2]},
            {'kind': 'broll', 'clips': self.clips[2:]}]
        layers, tasks = self.prepare()
        self.assertEqual(len(tasks), 1)
        self.assertEqual([layer['ti'] for layer in layers], [0, 0] + [1] * 10)
        self.assertEqual([layer['cl'] for layer in layers], self.clips)

    def test_different_inputs_and_production_parameters_stay_distinct(self):
        changes = ({'src': 'b'}, {'src_in': 1}, {'src_out': 7}, {'dur': 7},
                   {'scale': 0.5}, {'x': 2}, {'y': 2}, {'opacity': 0.5},
                   {'fade_in_frames': 2}, {'fade_out_frames': 2})
        for change in changes:
            with self.subTest(change=change):
                self.c.tl['video_tracks'][0]['clips'] = [self.clips[0],
                                                       dict(self.clips[1], **change)]
                layers, tasks = self.prepare()
                self.assertEqual(len(tasks), 2)
                self.assertNotEqual(layers[0]['seg'], layers[1]['seg'])

    def test_encoder_modes_keep_their_key_argv_and_budget_flag(self):
        keys = []
        for kind in ('x264', 'nvenc'):
            with self.subTest(kind=kind):
                self.kind.return_value = kind
                layers, tasks = self.prepare()
                keys.append(layers[0]['seg'])
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0][2], kind == 'nvenc')
                self.assertIn('h264_nvenc' if kind == 'nvenc' else 'libx264', tasks[0][1])
        self.assertNotEqual(*keys)
        for clip in self.clips:
            clip['opacity'] = 0.5
        layers, tasks = self.prepare()
        self.assertEqual(len(tasks), 1)
        self.assertIn('prores_ks', tasks[0][1])
        self.assertFalse(tasks[0][2])
        self.assertEqual(layers[0]['seg'].suffix, '.mov')

    def test_same_target_different_specs_fail_before_cold_or_warm_use(self):
        for warm in (False, True):
            for change in ({'src': 'b'}, {'src_in': 1}, {'dur': 7}, {'scale': 0.5}):
                with self.subTest(warm=warm, change=change):
                    self.cache.return_value = warm
                    self.runner.reset_mock()
                    self.c.tl['video_tracks'][0]['clips'] = [self.clips[0],
                                                           dict(self.clips[1], **change)]
                    with mock.patch.object(self.composite, 'seg_key', return_value='collision'), \
                            self.assertRaisesRegex(ValueError, 'conflicting seg producer'):
                        self.composite.prepare_layers(self.c)
                    self.runner.assert_not_called()

    def test_actual_filter_difference_conflicts_even_when_key_is_equal(self):
        self.c.tl['video_tracks'][0]['clips'] = self.clips[:2]
        self.c.crop169.side_effect = ['crop=64:36:0:6', 'crop=62:34:0:6']
        with self.assertRaisesRegex(ValueError, 'conflicting seg producer'):
            self.composite.prepare_layers(self.c)
        self.runner.assert_not_called()

    def test_different_keys_are_not_merged_by_equivalent_commands(self):
        self.c.tl['video_tracks'][0]['clips'] = self.clips[:2]
        with mock.patch.object(self.composite, 'seg_key', side_effect=['key-a', 'key-b']):
            layers, tasks = self.prepare()
        self.assertEqual(len(tasks), 2)
        self.assertNotEqual(layers[0]['seg'], layers[1]['seg'])

    def test_warm_hits_and_mixed_cache_states_preserve_all_consumers(self):
        for hits, expected in (([True] * 12, 0),
                               ([True] + [False] * 11, 1),
                               ([False] + [True] * 11, 1)):
            with self.subTest(hits=hits):
                self.cache.reset_mock(side_effect=True)
                self.cache.side_effect = hits
                layers, tasks = self.prepare()
                self.assertEqual(len(tasks), expected)
                self.assertEqual(len(layers), 12)
                self.assertEqual(self.cache.call_count, 12)

    def test_table_is_local_to_each_invocation(self):
        first, first_tasks = self.prepare()
        second, second_tasks = self.prepare()
        self.assertEqual(len(first_tasks), 1)
        self.assertEqual(len(second_tasks), 1)
        self.assertEqual(first, second)

    def test_draft_and_range_keep_same_key_and_verification_stage(self):
        reference, _ = self.prepare()
        for draft, window, expected in ((True, None, 'video_draft'),
                                        (False, (2, 5), None),
                                        (True, (2, 5), None)):
            with self.subTest(draft=draft, window=window):
                self.c.draft, self.c.range_window = draft, window
                self.c.verify_inputs.reset_mock()
                layers, tasks = self.prepare()
                self.assertEqual(layers, reference)
                self.assertEqual(len(tasks), 1)
                self.c.verify_inputs.assert_called_once_with(expected)

    def test_empty_tracks_still_verify_inputs_without_producers(self):
        self.c.tl['video_tracks'] = []
        layers, tasks = self.prepare()
        self.assertEqual((layers, tasks), ([], []))
        self.c.verify_inputs.assert_called_once_with('video')

    def test_dry_run_prints_one_producer_without_media_or_receipt(self):
        import contextlib
        import io
        from vtx.pipeline import infra
        self.c.dry = True
        output = io.StringIO()
        with mock.patch.object(self.composite, 'run_parallel', infra.run_parallel), \
                mock.patch.object(infra.subprocess, 'run', side_effect=AssertionError('dry process')), \
                contextlib.redirect_stdout(output):
            layers = self.composite.prepare_layers(self.c)
        self.assertEqual(len(layers), 12)
        self.assertEqual(output.getvalue().count('[dry-run]'), 1)
        self.assertEqual(list((self.c.cache / 'seg').iterdir()), [])
        self.assertFalse((self.root / 'out' / 'receipt.json').exists())

    def test_producer_failure_blocks_all_dependants_without_extra_retries(self):
        import contextlib
        import io
        from vtx.pipeline import infra
        for workers in (1, 3):
            with self.subTest(workers=workers):
                self.c.verify_inputs.reset_mock()
                seen = []
                # An unrelated producer still completes under existing run_parallel policy.
                self.c.tl['video_tracks'][0]['clips'] = self.clips + [dict(self.clips[0], src='b')]
                def execute(c, label, cmd, nvenc=False, **kwargs):
                    source = cmd[cmd.index('-i') + 1]
                    seen.append(source)
                    if source == str(self.root / 'a.mp4'):
                        raise RuntimeError('owned producer failed')
                with mock.patch.object(self.composite, 'run_parallel', infra.run_parallel), \
                        mock.patch.object(infra, 'io_workers', return_value=workers), \
                        mock.patch.object(infra, 'run_ffmpeg_task', side_effect=execute), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit) as caught:
                    self.composite.prepare_layers(self.c)
                self.assertEqual(caught.exception.code, 1)
                self.assertEqual(sorted(seen), [str(self.root / 'a.mp4'), str(self.root / 'b.mp4')])
                self.c.verify_inputs.assert_not_called()
                self.assertFalse((self.root / 'out' / 'receipt.json').exists())
        _, tasks = self.prepare()  # failure did not poison the next invocation
        self.assertEqual(len(tasks), 2)

    def test_unique_producer_cancellation_is_not_retried_or_swallowed(self):
        import contextlib
        import io
        from vtx.pipeline import infra
        with mock.patch.object(self.composite, 'run_parallel', infra.run_parallel), \
                mock.patch.object(infra, 'run_ffmpeg_task', side_effect=KeyboardInterrupt) as encode, \
                contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(KeyboardInterrupt):
            self.composite.prepare_layers(self.c)
        self.assertEqual(encode.call_count, 1)
        self.c.verify_inputs.assert_not_called()
        self.assertFalse((self.root / 'out' / 'receipt.json').exists())


if __name__ == "__main__":
    unittest.main()
