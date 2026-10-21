#!/usr/bin/env python3
"""tests/media/test_nvenc_oom_guard.py —— S5 NVENC OOM 守卫微测件（R-07h：J6′ 六态 + K1b）。

判据对照行（修订件修 5③ 程序钉死，落本桌 records）：
  J6′ = env python3 -m unittest tests.media.test_nvenc_oom_guard -v   （六态类全绿 rc=0）
  K1b = 零失败态断言（TestState3ZeroFailure：41 任务全绿夹具——重试队列零入队/
        退避时钟零调用/非 nvenc 任务占槽域零触达/全件执行次数=1）

覆盖（签发版 §三 J6′ 六态；修 9 四点+修 5 七点法定义务）：
  态① TestState1FirstFailRetrySuccess 首败重试成：失败件执行次数=2、成功件=1、
       重试=原 argv 逐元素全等、退避时钟注入记录器恰 [0.5]、瞬时型清单行；
  态② TestState2Exhausted 恒败耗尽：执行次数=1+N=3、fail-fast（SystemExit rc=1）、
       失败清单含「环境型」与「尝试=3/3」、退避 [0.5, 1.0]；
  态③ TestState3ZeroFailure K1b 零失败态：41 任务（38 nvenc+3 x264）全绿；
  态④ TestState4HalfBreak 半途断独立记账：41 任务 3 败 38 成（修 9③ 混合夹具）——
       2 件瞬时（各 2 次）+1 件耗尽（3 次）触发 fail-fast 不牵连成功件；
  态⑤a TestState5aGuardClamp 守卫降档：used 桩=13000MiB→档位 2→有效预算=min(8,2)=2、
       峰值并发 ≤ 档位（占槽 enter/exit 计数器采样，采样点=槽内工作体起止，修 5⑥）、
       告警一行含「VRAM 守卫」；
  态⑤b TestState5bFailOpen fail-open 三型（命令不可得∪超时∪解析失败）→
       不钳制（有效预算=预算原值）+告警一行含「VRAM 守卫」；
  附：TestTierTable D1 档位表纯映射（两实测锚：W0 安全基线 2000MiB→档 8、
      R-07e OOM 锚 14300/15010/15817MiB→档 1）；TestRetryBackoff N=2/基值 0.5s
      指数；TestQuerySeam nvidia-smi 超时阈值 2.0s+解析边界实测（patch encoders 侧
      subprocess.run，不起真 nvidia-smi）；TestBackoffNoSlot 退避期不持槽（占槽纪律）。

不真编码：subprocess.run 以假执行器替身（patch 点 vtx.infra.subprocess.run——替身体
即「槽内工作体」，峰值采样点）；档位/退避时钟全注入桩，不起 nvidia-smi、零真等待。
setUp 统一重置预算信号量/守卫 memo/护栏计数并注入退避记录器，teardown 还原，
不对既有测试件泄漏（本件按字母序先于 test_seg_nvenc 执行，状态零外溢）。
运行：env python3 -m unittest tests.media.test_nvenc_oom_guard -v
（env python3 纪律见全局 AGENTS.md：AppImage 劫持裸 python3 的 sys.executable）
"""
import contextlib
import io
import os
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from vtx import encoders, infra
from vtx.encoders import (NVENC_RETRY_ATTEMPTS, NVENC_RETRY_N,
                          nvenc_budget_value, nvenc_effective_budget,
                          nvenc_guard_stats, nvenc_retry_backoff,
                          nvenc_retry_run, reset_nvenc_budget,
                          reset_vram_guard, vram_tier_for, _vram_query_used_mib)


class _R:
    """subprocess.CompletedProcess 最小替身。"""
    def __init__(self, rc, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


class FakeFFmpeg:
    """假 ffmpeg 执行器：program[label]=首 N 次失败（999=恒败）；替身体即「槽内工作体」，
    enter/exit 计数器采样峰值并发（采样点=槽内工作体起止，修 5⑥；桩 ctx 计数器断言
    先例=tests/media/test_seg_nvenc.py TestNvencBudget 款）。cmd 携带 "seg:<label>:<i>"
    标记元素（按前缀定位，不依赖下标）。
    """

    def __init__(self, program, body_s=0.0):
        self.program = program
        self.body_s = body_s
        self.lock = threading.Lock()
        self.execs = {}     # label → [argv 快照, ...]（原 argv 逐元素全等断言）
        self.counts = {}    # label → 执行次数（逐件独立记账）
        self.cur = 0
        self.peak = 0

    def __call__(self, cmd, **kw):
        if str(cmd[0]).endswith("nvidia-smi"):
            return _R(0, stdout="")   # 防御：守卫查询面误入替身时按不可得（fail-open）
        marker = next(str(x) for x in cmd if str(x).startswith("seg:"))
        label = marker.split(":", 1)[1].rsplit(":", 1)[0]
        with self.lock:
            self.execs.setdefault(label, []).append([str(x) for x in cmd])
            self.counts[label] = self.counts.get(label, 0) + 1
            n = self.counts[label]
            self.cur += 1
            self.peak = max(self.peak, self.cur)
        try:
            if self.body_s:
                threading.Event().wait(self.body_s)
        finally:
            with self.lock:
                self.cur -= 1
        return _R(1 if n <= self.program.get(label, 0) else 0,
                  stderr="h264_nvenc InitializeEncoder failed: out of memory\n")

    def argv(self, label):
        return self.execs[label]

    def n(self, label):
        return self.counts.get(label, 0)


def _cmd(label, i=0):
    """假 seg 预编码命令（is_ffmpeg_cmd 判 cmd[0] endswith "ffmpeg"；替身不真跑）。"""
    return ["ffmpeg", "-v", "error", f"seg:{label}:{i}", "-c:v", "h264_nvenc", "out.mp4"]


class _Ctx:
    """run_ffmpeg_task/run_parallel 最小上下文（非 dry；nvenc_budget 只读 dry 位）。"""
    dry = False


class GuardTestCase(unittest.TestCase):
    """公共夹具：守卫/预算/统计全量重置 + 退避时钟注入记录器 + 环境去依赖 + 输出捕获。"""

    def setUp(self):
        reset_nvenc_budget()
        reset_vram_guard()
        self.backoff = []
        self._clock_patch = mock.patch.object(
            encoders, "retry_clock", side_effect=self.backoff.append)
        self._clock_patch.start()
        self.addCleanup(self._clock_patch.stop)
        self._env = mock.patch.dict(os.environ, {"VTX_NVENC_BUDGET": "8"})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

    def tearDown(self):
        reset_nvenc_budget()
        reset_vram_guard()

    def seed_tier(self, tier):
        """档位桩注入（修 9①，不起 nvidia-smi）：直写守卫 memo（等价「守卫已求值
        定型」，绕开真查询——若经 patch vram_guard_tier 注入则 memo 恒空，运行期
        真守卫会落到 subprocess 替身上）。tearDown/reset_vram_guard 清除。"""
        encoders._VRAM_TIER_MEMO = tier
        return tier


# ---------------------------------------------------------------- J6′ 态①~④（重试队列）
class TestState1FirstFailRetrySuccess(GuardTestCase):
    """态① 成功=首败重试成：失败件执行次数=2、成功件=1、原 argv 逐元素全等重发。"""

    def test_first_fail_retry_success(self):
        fake = FakeFFmpeg({"A": 1})
        self.seed_tier(8)
        with mock.patch.object(infra.subprocess, "run", fake), \
             contextlib.redirect_stdout(self.stdout), \
             contextlib.redirect_stderr(self.stderr):
            infra.run_parallel(_Ctx(), [("A", _cmd("A"), True),
                                        ("B", _cmd("B"), True),
                                        ("C", _cmd("C"), True)], "seg 预编码")
        self.assertEqual(fake.n("A"), 2)                      # 失败件：首败+重试成
        self.assertEqual(fake.n("B"), 1)                      # 成功件：零重试零副作用
        self.assertEqual(fake.n("C"), 1)
        self.assertEqual(fake.argv("A")[0], fake.argv("A")[1])  # 重试=原 argv 原样重发
        for i, (x, y) in enumerate(zip(*fake.argv("A"))):
            self.assertEqual(x, y, f"argv[{i}] 逐元素不等")      # 逐元素全等（红线）
        self.assertEqual(self.backoff, [0.5])                 # 指数退避基值，恰一次
        s = nvenc_guard_stats()
        self.assertEqual((s["retry"], s["transient"], s["exhausted"]), (1, 1, 0))
        self.assertIn("瞬时型", self.stdout.getvalue())        # 判别=瞬时型（重试成功）
        self.assertIn("label=A", self.stdout.getvalue())


class TestState2Exhausted(GuardTestCase):
    """态② 耗尽=恒败执行 1+N=3 次+fail-fast（SystemExit rc=1）+清单含「环境型」。"""

    def test_exhausted_fail_fast(self):
        fake = FakeFFmpeg({"A": 999})
        self.seed_tier(8)
        with mock.patch.object(infra.subprocess, "run", fake), \
             contextlib.redirect_stdout(self.stdout), \
             contextlib.redirect_stderr(self.stderr):
            with self.assertRaises(SystemExit) as cm:
                infra.run_parallel(_Ctx(), [("A", _cmd("A"), True),
                                            ("B", _cmd("B"), True)], "seg 预编码")
        self.assertEqual(cm.exception.code, 1)                # 现行 fail-fast 语义零变化
        self.assertEqual(fake.n("A"), NVENC_RETRY_ATTEMPTS)   # 1+N=3
        self.assertEqual(fake.n("B"), 1)                      # 成功件零牵连
        self.assertEqual(self.backoff, [0.5, 1.0])            # 指数退避恰 2 次
        err = self.stderr.getvalue()
        self.assertIn("环境型", err)                           # 失败清单行（stderr 留档）
        self.assertIn("label=A", err)
        self.assertIn("尝试=3/3", err)
        s = nvenc_guard_stats()
        self.assertEqual((s["retry"], s["transient"], s["exhausted"]), (2, 0, 1))


class TestState3ZeroFailure(GuardTestCase):
    """态③=K1b 零失败态：41 任务全绿（38 nvenc+3 x264）——全件执行次数=1、重试队列
    零入队、退避时钟零调用、非 nvenc 任务占槽域零触达。"""

    def test_41_all_green_zero_enqueue(self):
        fake = FakeFFmpeg({})
        self.seed_tier(8)
        labels = [(f"t{i:02d}", True) for i in range(38)] + \
                 [(f"x{i:02d}", False) for i in range(3)]
        entries = {"n": 0}
        real_budget = infra.nvenc_budget

        def counted(c, n=1):   # 占槽域触达计数（包装真信号量，行为不变）
            entries["n"] += 1
            return real_budget(c, n)

        tasks = [(lab, _cmd(lab), nv) for lab, nv in labels]
        with mock.patch.object(infra.subprocess, "run", fake), \
             mock.patch.object(infra, "nvenc_budget", counted), \
             contextlib.redirect_stdout(self.stdout), \
             contextlib.redirect_stderr(self.stderr):
            infra.run_parallel(_Ctx(), tasks, "seg 预编码")       # 零 SystemExit=全绿
        self.assertEqual([fake.n(lab) for lab, _ in labels], [1] * 41)  # 全件恰一次
        self.assertEqual(self.backoff, [])                    # 退避时钟零调用
        s = nvenc_guard_stats()
        self.assertEqual((s["retry"], s["transient"], s["exhausted"]), (0, 0, 0))
        self.assertEqual(entries["n"], 38)                    # 非 nvenc 任务占槽域零触达

    def test_x264_zero_slot_touch_concurrent(self):
        # 占槽域零触达第二证：预算 1 下 6 路纯 x264 任务替身体自由重叠（峰值 ≥2——
        # 若任一 x264 触达信号量，则被钳串行峰值恒 1）。对齐 test_seg_nvenc 直通款。
        fake = FakeFFmpeg({}, body_s=0.05)
        with mock.patch.dict(os.environ, {"VTX_NVENC_BUDGET": "1"}):
            reset_nvenc_budget()
            self.seed_tier(8)                                 # 守卫不钳（预算=1 生效）
            with mock.patch.object(infra.subprocess, "run", fake), \
                 contextlib.redirect_stdout(self.stdout):
                with ThreadPoolExecutor(6) as ex:
                    list(ex.map(lambda i: infra.run_ffmpeg_task(
                        _Ctx(), f"x{i:02d}", _cmd(f"x{i:02d}"), nvenc=False), range(6)))
        self.assertEqual([fake.n(f"x{i:02d}") for i in range(6)], [1] * 6)
        self.assertGreaterEqual(fake.peak, 2)                 # 未触占槽域（否则峰值=1）


class TestState4HalfBreak(GuardTestCase):
    """态④ 半途断=独立记账：41 任务 3 败 38 成（修 9③ 混合夹具）——仅 3 件入队，
    2 件瞬时（各 2 次）+1 件耗尽（3 次）触发 fail-fast，成功件零重试零牵连。"""

    def test_mixed_batch_independent_accounting(self):
        fake = FakeFFmpeg({"t36": 1, "t37": 1, "t38": 999})
        self.seed_tier(8)
        tasks = [(f"t{i:02d}", _cmd(f"t{i:02d}"), True) for i in range(41)]
        with mock.patch.object(infra.subprocess, "run", fake), \
             contextlib.redirect_stdout(self.stdout), \
             contextlib.redirect_stderr(self.stderr):
            with self.assertRaises(SystemExit) as cm:
                infra.run_parallel(_Ctx(), tasks, "seg 预编码")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual([fake.n(f"t{i:02d}") for i in (*range(36), 39, 40)],
                         [1] * 38)  # 所有原本成功件，包括失败任务之后的两件
        self.assertEqual(fake.n("t36"), 2)                    # 瞬时型：首败+重试成
        self.assertEqual(fake.n("t37"), 2)                    # 逐件尝试次数独立记账
        self.assertEqual(fake.n("t38"), 3)                    # 环境型：1+N=3
        self.assertEqual(sorted(self.backoff), [0.5, 0.5, 0.5, 1.0])
        out, err = self.stdout.getvalue(), self.stderr.getvalue()
        self.assertEqual(out.count("瞬时型"), 2)
        self.assertIn("环境型", err)
        self.assertIn("label=t38", err)
        self.assertIn("尝试=3/3", err)
        s = nvenc_guard_stats()
        self.assertEqual((s["retry"], s["transient"], s["exhausted"]), (4, 2, 1))


# ---------------------------------------------------------------- J6′ 态⑤a/⑤b（VRAM 守卫）
class TestState5aGuardClamp(GuardTestCase):
    """态⑤a 守卫触发：used 桩=13000MiB（高位）→档位 2→有效预算=min(8,2)=2、
    峰值并发 ≤ 档位、告警一行含「VRAM 守卫」。守卫体真实执行（仅查询注入，修 9①）。"""

    def test_high_tier_clamps_concurrency(self):
        fake = FakeFFmpeg({}, body_s=0.03)
        with mock.patch.object(encoders, "_vram_query_used_mib", return_value=13000), \
             mock.patch.object(infra.subprocess, "run", fake), \
             contextlib.redirect_stdout(self.stdout):
            with ThreadPoolExecutor(8) as ex:                 # 8 路直发（>档位 2）
                list(ex.map(lambda i: infra.run_ffmpeg_task(
                    _Ctx(), f"s{i:02d}", _cmd(f"s{i:02d}"), nvenc=True), range(8)))
        self.assertEqual(nvenc_effective_budget(), 2)         # 降档生效
        self.assertLessEqual(fake.peak, 2)                    # 峰值并发 ≤ 档位（判据）
        self.assertEqual(fake.peak, 2)                        # 且确曾双开（非串行假阳）
        self.assertEqual([fake.n(f"s{i:02d}") for i in range(8)], [1] * 8)
        out = self.stdout.getvalue()
        self.assertIn("VRAM 守卫", out)                       # 告警一行（grep 标记）
        self.assertIn("档位 2", out)
        self.assertIn("守卫降档触发", out)
        self.assertEqual(nvenc_guard_stats()["clamp"], 1)


class TestState5bFailOpen(GuardTestCase):
    """态⑤b fail-open：触发面三型（命令不可得∪超时∪解析失败）→不钳制（有效预算=
    预算原值）+告警一行含「VRAM 守卫」（失效面显形不静默）。"""

    def _open_case(self, query):
        with mock.patch.dict(os.environ, {"VTX_NVENC_BUDGET": "5"}), \
             contextlib.redirect_stdout(self.stdout):
            reset_nvenc_budget()
            reset_vram_guard()
            with mock.patch.object(encoders, "_vram_query_used_mib", side_effect=query):
                self.assertEqual(nvenc_effective_budget(), 5)  # 不钳制=预算原值
        self.assertIn("VRAM 守卫", self.stdout.getvalue())
        self.assertIn("fail-open", self.stdout.getvalue())
        self.assertEqual(nvenc_guard_stats()["failopen"], 1)

    def test_fail_open_unavailable(self):                     # ①命令不可得
        self._open_case(FileNotFoundError(2, "nvidia-smi: not found"))

    def test_fail_open_timeout(self):                         # ②超时
        self._open_case(subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=2.0))

    def test_fail_open_parse_failure(self):                   # ③解析失败（查询面返回 None）
        self._open_case(lambda: None)


# ---------------------------------------------------------------- D1 法定义务附测
class TestTierTable(unittest.TestCase):
    """D1 档位表值纯映射（预注册冻结）：两实测锚覆盖——W0 安全基线 ~2000MiB→档 8；
    R-07e 轮 3 OOM 窗 19 样本 min 14300/中位 15010/max 15817MiB→档 1（串行）；
    中间档=保守初值（无实测锚，方向恒保守，修 4② 显式登记）。"""

    def test_anchor_safe_baseline_full_budget(self):
        self.assertEqual(vram_tier_for(1232), 8)              # 本机待机实测
        self.assertEqual(vram_tier_for(2000), 8)              # W0 窗起安全锚
        self.assertEqual(vram_tier_for(5999), 8)

    def test_anchor_oom_floor_serial(self):
        self.assertEqual(vram_tier_for(14300), 1)             # OOM 锚下沿（19 样本 min）
        self.assertEqual(vram_tier_for(15010), 1)             # OOM 锚中位
        self.assertEqual(vram_tier_for(15817), 1)             # OOM 锚上沿
        self.assertEqual(vram_tier_for(99999), 1)

    def test_middle_tiers_derate(self):
        for used, tier in [(6000, 6), (8999, 6), (9000, 4), (11999, 4),
                           (12000, 2), (14299, 2)]:
            with self.subTest(used=used):
                self.assertEqual(vram_tier_for(used), tier)


class TestRetryBackoff(unittest.TestCase):
    """D1：N=2/退避基值 0.5s（保守经验初值，无实测恢复时标，显式登记）指数序列；
    机制本体直测——仅失败件重试（成功件零调用），退避时钟可注入零延时模式。"""

    def test_shape(self):
        self.assertEqual(NVENC_RETRY_N, 2)
        self.assertEqual(NVENC_RETRY_ATTEMPTS, 3)             # 总尝试=1+N
        self.assertEqual(nvenc_retry_backoff(1), 0.5)
        self.assertEqual(nvenc_retry_backoff(2), 1.0)

    def test_mechanism_only_failed_items_retry(self):
        calls = {"a": 0, "b": 0}

        class R:
            def __init__(self, rc):
                self.returncode = rc

        def program(which, n):
            calls[which] += 1
            if which == "a":
                return R(0 if calls["a"] > 1 else 1)          # 首败重试成
            return R(1)                                       # 恒败

        clock = []
        with mock.patch.object(encoders, "retry_clock", side_effect=clock.append):
            ok_a, n_a, _ = nvenc_retry_run(lambda _: program("a", calls["a"] + 1), "a")
            ok_b, n_b, last = nvenc_retry_run(lambda _: program("b", calls["b"] + 1), "b")
        self.assertEqual((ok_a, n_a), (True, 2))              # 瞬时型
        self.assertEqual((ok_b, n_b), (False, NVENC_RETRY_ATTEMPTS))  # 环境型
        self.assertEqual(last.returncode, 1)
        self.assertEqual((calls["a"], calls["b"]), (2, 3))
        self.assertEqual(clock, [0.5, 0.5, 1.0])              # a 一次+b 两次（零延时注入）


class TestBackoffNoSlot(unittest.TestCase):
    """占槽纪律（冻结）：退避等待期不持槽——预算 1 下，退避时钟触发瞬间信号量必须
    全空（若重试循环持槽等待，acquire(blocking=False) 失败=红；R3 交互防御断言）。
    独立夹具（不经 GuardTestCase：本件需自管退避探针时钟）。"""

    def setUp(self):
        reset_nvenc_budget()
        reset_vram_guard()
        self.stdout = io.StringIO()

    def tearDown(self):
        reset_nvenc_budget()
        reset_vram_guard()

    def test_backoff_window_holds_no_slot(self):
        fake = FakeFFmpeg({"A": 1})
        backoff = []
        with mock.patch.dict(os.environ, {"VTX_NVENC_BUDGET": "1"}):
            encoders._VRAM_TIER_MEMO = 8                      # 预置档位 memo（预算=1 生效）

            def probe(delay):
                backoff.append(delay)
                sem = encoders._nvenc_pool()[0]
                got = sem.acquire(blocking=False)
                self.assertTrue(got, "退避等待期持槽=违反占槽纪律（R3）")
                sem.release()

            with mock.patch.object(encoders, "retry_clock", probe), \
                 mock.patch.object(infra.subprocess, "run", fake), \
                 contextlib.redirect_stdout(self.stdout):
                infra.run_ffmpeg_task(_Ctx(), "A", _cmd("A"), nvenc=True)
        self.assertEqual(fake.n("A"), 2)
        self.assertEqual(backoff, [0.5])


class TestQuerySeam(unittest.TestCase):
    """D1 第七点：nvidia-smi 超时阈值 2.0s+解析边界（空串/非数字/[N/A] 畸形/带 MiB 尾/
    裸数字/小数）实测——patch encoders 侧 subprocess.run，不起真 nvidia-smi。"""

    def test_timeout_threshold_2s_and_cmd_shape(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = list(cmd)
            seen["timeout"] = kw.get("timeout")
            raise subprocess.TimeoutExpired(cmd=list(cmd), timeout=kw.get("timeout"))

        with mock.patch.object(encoders.subprocess, "run", fake_run):
            self.assertIsNone(_vram_query_used_mib())
        self.assertEqual(seen["timeout"], 2.0)                # 超时阈值（D1 第七点成文值）
        self.assertEqual(seen["cmd"][0], "nvidia-smi")
        self.assertIn("--query-gpu=memory.used", seen["cmd"])

    def test_parse_boundaries(self):
        cases = {"2048 MiB\n": 2048, "2048\n": 2048, "": None, "abc": None,
                 "[N/A]": None, "\n": None, "12.5": None}
        for out, want in cases.items():
            with self.subTest(out=out):
                with mock.patch.object(encoders.subprocess, "run",
                                       return_value=_R(0, stdout=out)):
                    self.assertEqual(_vram_query_used_mib(), want)

    def test_oserror_unavailable(self):
        with mock.patch.object(encoders.subprocess, "run",
                               side_effect=FileNotFoundError(2, "nvidia-smi")):
            self.assertIsNone(_vram_query_used_mib())


class TestBudgetUnchangedSurface(GuardTestCase):
    """既有面零变化回归：nvenc_budget_value 三态（缺省 8/合法覆盖/非法回落 8）与
    档位不钳场景下有效预算=预算原值（守卫零扰动面）。"""

    def test_budget_value_semantics_unchanged(self):
        with mock.patch.dict(os.environ, {"VTX_NVENC_BUDGET": "3"}):
            self.assertEqual(nvenc_budget_value(), 3)
        with mock.patch.dict(os.environ, {"VTX_NVENC_BUDGET": "bogus"}):
            self.assertEqual(nvenc_budget_value(), 8)
        with mock.patch.dict(os.environ):
            os.environ.pop("VTX_NVENC_BUDGET", None)
            self.assertEqual(nvenc_budget_value(), 8)

    def test_effective_equals_budget_when_tier_not_clamping(self):
        self.seed_tier(8)
        self.assertEqual(nvenc_effective_budget(), nvenc_budget_value())


if __name__ == "__main__":
    unittest.main()
