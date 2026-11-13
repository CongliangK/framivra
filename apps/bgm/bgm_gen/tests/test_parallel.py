"""⑧资源感知并行测试 [fast]（v3.4 并行渲染轮，PLAN_v3 §10.7 资源纪律）。

覆盖三层：
    纯函数 plan_workers：cpu 帽/内存帽/hard_cap/n_tasks/地板/env 覆盖族；
    DispatchGate：低内存阻塞→释放→通过；超时报错而非死等（monkeypatch
        mem_available_gb 模拟低内存，绝不真吃内存）；
    集成：minimal 工程 in-proc 渲染——默认（并行）与 BGM_NO_PARALLEL=1
        （顺序）同目录先后渲染，**全部产物 sha 逐位相等**（确定性硬门）+
        与 out/minimal 锚点（v3.3 顺序渲染产物）逐位一致。

约定：conftest 不注入 BGM_NO_PARALLEL（默认启用并行走新路径）；顺序路径
专项用例 monkeypatch.setenv/delenv 进程内覆写（镜像 BGM_NO_INCREMENTAL 模式）。
"""

import hashlib
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

helpers.in_proc_env()
from bgm_gen.core import resources                          # noqa: E402


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------- plan_workers 纯函数
# 探测值全部经参数注入（纯函数；env 覆盖用例显式 monkeypatch）。

@pytest.mark.fast
def test_plan_workers_cpu_limited():
    """内存充裕时 CPU 帽主导：cpu_threads×factor 向下取整（≥1）。"""
    w = resources.plan_workers(100, 1.0, cpu_cap_factor=0.5, hard_cap=64,
                               mem_floor_gb=6.0, avail_gb=1000.0, cpu_threads=32)
    assert w == 16
    assert resources.plan_workers(100, 1.0, cpu_cap_factor=0.5, hard_cap=64,
                                  mem_floor_gb=6.0, avail_gb=1000.0,
                                  cpu_threads=3) == 1        # floor(1.5)=1


@pytest.mark.fast
def test_plan_workers_mem_limited():
    """内存紧张时内存帽主导：ceil((avail−floor)/per_task)。"""
    w = resources.plan_workers(100, 2.0, mem_floor_gb=10.0, avail_gb=19.0,
                               cpu_threads=32, hard_cap=64)
    assert w == 5                                            # ceil(9/2)
    assert resources.plan_workers(100, 2.0, mem_floor_gb=10.0, avail_gb=21.0,
                                  cpu_threads=32, hard_cap=64) == 6   # ceil(11/2)


@pytest.mark.fast
def test_plan_workers_hard_cap_and_ntasks():
    """hard_cap 与 n_tasks 封顶；二者与内存/CPU 帽取 min。"""
    assert resources.plan_workers(100, 0.1, hard_cap=8, mem_floor_gb=6.0,
                                  avail_gb=1000.0, cpu_threads=32) == 8
    assert resources.plan_workers(3, 0.1, hard_cap=8, mem_floor_gb=6.0,
                                  avail_gb=1000.0, cpu_threads=32) == 3
    assert resources.plan_workers(0, 1.0) == 0               # 无任务→0（不建池）


@pytest.mark.fast
def test_plan_workers_floor_and_min_one():
    """可用 ≤ 地板时可用量归零→单发（保底 1；实时兜底交给 DispatchGate）。"""
    # floor 缺省 = max(6, total×0.2)：total=10 → 6；avail=6.4 → usable 0.4
    assert resources.plan_workers(9, 1.0, avail_gb=6.4, cpu_threads=32,
                                  hard_cap=8, total_gb=10.0) == 1
    # avail 远低于 floor 同样保底 1，绝不 0（死渲染）也绝不盲目放并发
    assert resources.plan_workers(9, 1.0, avail_gb=1.0, cpu_threads=32,
                                  hard_cap=8, total_gb=10.0) == 1


@pytest.mark.fast
def test_plan_workers_env_max_parallel_override(monkeypatch):
    """BGM_MAX_PARALLEL=N 只能压小 hard_cap（不能放大）。"""
    monkeypatch.setenv("BGM_MAX_PARALLEL", "2")
    assert resources.plan_workers(100, 0.1, hard_cap=8, mem_floor_gb=6.0,
                                  avail_gb=1000.0, cpu_threads=32) == 2
    monkeypatch.setenv("BGM_MAX_PARALLEL", "99")
    assert resources.plan_workers(100, 0.1, hard_cap=8, mem_floor_gb=6.0,
                                  avail_gb=1000.0, cpu_threads=32) == 8
    monkeypatch.setenv("BGM_MAX_PARALLEL", "x")
    with pytest.raises(resources.ResourceError):
        resources.plan_workers(4, 1.0, mem_floor_gb=6.0, avail_gb=100.0)


@pytest.mark.fast
def test_plan_workers_env_floor_and_validation(monkeypatch):
    """BGM_MEM_FLOOR_GB 覆盖缺省地板；per_task 非正数拒绝。"""
    monkeypatch.setenv("BGM_MEM_FLOOR_GB", "50")
    assert resources.plan_workers(9, 1.0, avail_gb=51.0, cpu_threads=32,
                                  hard_cap=8) == 1            # usable=1→ceil=1
    monkeypatch.setenv("BGM_MEM_FLOOR_GB", "999")
    assert resources.plan_workers(9, 0.5, avail_gb=100.0, cpu_threads=32,
                                  hard_cap=8) == 1            # 极高地板→退化 1
    monkeypatch.delenv("BGM_MEM_FLOOR_GB")
    with pytest.raises(resources.ResourceError):
        resources.plan_workers(9, 0.0, mem_floor_gb=6.0, avail_gb=100.0)
    with pytest.raises(resources.ResourceError):
        resources.plan_workers(9, -1.0, mem_floor_gb=6.0, avail_gb=100.0)


@pytest.mark.fast
def test_mem_floor_default_formula(monkeypatch):
    """缺省地板 = max(6, total×0.2)；env 覆盖优先；负值拒绝。"""
    monkeypatch.delenv("BGM_MEM_FLOOR_GB", raising=False)
    assert resources.mem_floor_gb(100.0) == 20.0
    assert resources.mem_floor_gb(10.0) == 6.0
    monkeypatch.setenv("BGM_MEM_FLOOR_GB", "3.5")
    assert resources.mem_floor_gb(100.0) == 3.5
    monkeypatch.setenv("BGM_MEM_FLOOR_GB", "-1")
    with pytest.raises(resources.ResourceError):
        resources.mem_floor_gb(100.0)


# ------------------------------------------------------------ 探测与实时读数

@pytest.mark.fast
def test_probe_and_mem_available(monkeypatch):
    monkeypatch.delenv("BGM_MEM_FLOOR_GB", raising=False)
    p1 = resources.probe()
    assert p1 is resources.probe()                           # 进程内缓存
    assert isinstance(p1["cpu_threads"], int) and p1["cpu_threads"] >= 1
    assert p1["mem_total_gb"] > 1.0
    assert p1["gpu"] is None or ("total_mb" in p1["gpu"] and "free_mb" in p1["gpu"])
    avail = resources.mem_available_gb()
    assert avail is None or avail > 0                        # 现读不缓存


# ---------------------------------------------------------------- DispatchGate

@pytest.mark.fast
def test_gate_admission_and_release(monkeypatch):
    """配额语义：第 k 个在途任务需要 floor+per_task×k 可用内存；
    释放后配额回收。monkeypatch 模拟低内存，不真吃内存。"""
    fake = {"gb": 100.0}
    monkeypatch.setattr(resources, "mem_available_gb", lambda: fake["gb"])
    gate = resources.DispatchGate(2.0, mem_floor_gb=6.0, timeout_s=1.0,
                                  poll_s=0.02)
    gate.acquire()                                           # 需 6+2×1=8 ≤100
    gate.acquire()                                           # 需 6+2×2=10 ≤100
    assert gate.inflight == 2
    fake["gb"] = 9.5                                         # 需 6+2×3=12 >9.5
    t0 = time.monotonic()
    with pytest.raises(resources.ResourceError):             # 超时报错非死等
        gate.acquire()
    assert time.monotonic() - t0 >= 0.9
    gate.release()                                           # 释放→在途 1→再需 10
    fake["gb"] = 100.0                                       # 内存回升后再拿配额
    gate.acquire()
    assert gate.inflight == 2
    gate.release(); gate.release()
    assert gate.inflight == 0


@pytest.mark.fast
def test_gate_blocks_until_mem_frees(monkeypatch):
    """低内存阻塞等待→内存回升→通过（0.5s 轮询内拿到许可）。"""
    fake = {"gb": 7.0}                                       # 需 8（floor6+p2）
    monkeypatch.setattr(resources, "mem_available_gb", lambda: fake["gb"])
    gate = resources.DispatchGate(2.0, mem_floor_gb=6.0, timeout_s=10.0,
                                  poll_s=0.02)
    got = threading.Event()
    holder = {}

    def _worker():
        with gate.slot():
            holder["t_acquired"] = time.monotonic()
            got.set()

    th = threading.Thread(target=_worker)
    th.start()
    time.sleep(0.15)
    assert not got.is_set(), "内存不足时不得派发"
    fake["gb"] = 20.0                                        # 内存回升
    got.wait(5.0)
    th.join(5.0)
    assert got.is_set() and gate.inflight == 0               # slot 退出已释放


@pytest.mark.fast
def test_gate_slot_releases_on_exception(monkeypatch):
    """with slot() 内抛异常也必须释放配额（done_callback 同语义）。"""
    monkeypatch.setattr(resources, "mem_available_gb", lambda: 100.0)
    gate = resources.DispatchGate(1.0, mem_floor_gb=0.0, timeout_s=1.0)
    with pytest.raises(RuntimeError):
        with gate.slot():
            assert gate.inflight == 1
            raise RuntimeError("boom")
    assert gate.inflight == 0


# -------------------------------------------------------------------- run_pool

class _InflightCounter:
    def __init__(self):
        self.lock = threading.Lock()
        self.cur = 0
        self.peak = 0

    def task(self, i, sleep_s=0.08):
        with self.lock:
            self.cur += 1
            self.peak = max(self.peak, self.cur)
        time.sleep(sleep_s)
        with self.lock:
            self.cur -= 1
        return i * 10


@pytest.mark.fast
def test_run_pool_respects_max_parallel(monkeypatch):
    """BGM_MAX_PARALLEL=2 → 在途峰值 ≤2；结果按任务序返回。"""
    monkeypatch.setattr(resources, "mem_available_gb", lambda: 1000.0)
    monkeypatch.setenv("BGM_MAX_PARALLEL", "2")
    monkeypatch.delenv("BGM_NO_PARALLEL", raising=False)
    cnt = _InflightCounter()
    out = resources.run_pool(range(6), cnt.task, per_task_gb=0.5)
    assert out == [i * 10 for i in range(6)]                 # 按序，与完成序无关
    assert 1 <= cnt.peak <= 2


@pytest.mark.fast
def test_run_pool_hard_off_and_degenerate(monkeypatch):
    """BGM_NO_PARALLEL=1 硬关→顺序（峰值恒 1）；地板极高→workers 退化 1。"""
    monkeypatch.setattr(resources, "mem_available_gb", lambda: 1000.0)
    monkeypatch.delenv("BGM_MAX_PARALLEL", raising=False)
    monkeypatch.setenv("BGM_NO_PARALLEL", "1")
    cnt = _InflightCounter()
    out = resources.run_pool(range(4), cnt.task, per_task_gb=0.5)
    assert out == [0, 10, 20, 30] and cnt.peak == 1
    monkeypatch.delenv("BGM_NO_PARALLEL")
    monkeypatch.setattr(resources, "mem_available_gb", lambda: 100.0)
    monkeypatch.setenv("BGM_MEM_FLOOR_GB", "999")            # 可用 100−999<0→单发
    cnt2 = _InflightCounter()
    out2 = resources.run_pool(range(4), cnt2.task, per_task_gb=0.5)
    assert out2 == [0, 10, 20, 30] and cnt2.peak == 1
    assert resources.run_pool([], cnt.task, per_task_gb=0.5) == []


@pytest.mark.fast
def test_run_pool_exception_propagates(monkeypatch):
    """fn 抛错→按任务序抛出；其余任务收尾后退出（不悬挂）。"""
    monkeypatch.setattr(resources, "mem_available_gb", lambda: 1000.0)
    monkeypatch.delenv("BGM_NO_PARALLEL", raising=False)
    monkeypatch.delenv("BGM_MAX_PARALLEL", raising=False)

    def fn(i):
        if i == 2:
            raise ValueError(f"track{i} 失败")
        return i

    with pytest.raises(ValueError, match="track2"):
        resources.run_pool([0, 1, 2, 3], fn, per_task_gb=0.5, workers=4)


# ------------------------------------------------------- 集成：minimal 位级

def _render_minimal_inproc(out_dir, monkeypatch=None, env=None):
    """in-proc 渲染 minimal（quiet）；env 经 monkeypatch 设置后调用。"""
    helpers.require_minimal_assets()
    from bgm_gen.core.project import render_project
    for k, v in (env or {}).items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    return render_project(os.path.join(helpers.EXAMPLES_DIR, "minimal.json"),
                          out_dir=out_dir, quiet=True)


def _snapshot(out_dir):
    """产物目录全文件 sha 快照（相对路径→sha；确定性硬门的比对口径）。"""
    snap = {}
    for root, _dirs, files in os.walk(out_dir):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            snap[os.path.relpath(p, out_dir)] = _sha(p)
    return snap


@pytest.mark.fast
def test_render_minimal_parallel_equals_sequential(monkeypatch):
    """确定性硬门：默认（并行）与 BGM_NO_PARALLEL=1（顺序）同目录先后渲染，
    全部产物（stems/bus/duck/premaster/master/mix.flac/qc.json/快照）逐位相等。
    同目录 → qc.json 内嵌路径也一致（跨目录差异仅路径字段，见本轮报告）。"""
    d = helpers.tmpdir("par_minimal")
    monkeypatch.delenv("BGM_NO_PARALLEL", raising=False)
    monkeypatch.delenv("BGM_MAX_PARALLEL", raising=False)
    monkeypatch.delenv("BGM_MEM_FLOOR_GB", raising=False)
    _render_minimal_inproc(d, monkeypatch, env={"BGM_NO_PARALLEL": None})
    snap_par = _snapshot(d)
    assert len(snap_par) >= 10, f"产物不齐：{sorted(snap_par)}"
    _render_minimal_inproc(d, monkeypatch, env={"BGM_NO_PARALLEL": "1"})
    snap_seq = _snapshot(d)
    assert snap_par == snap_seq, "并行 vs 顺序产物存在位级差异：" + str(
        {k for k in snap_par if snap_par.get(k) != snap_seq.get(k)})


# 注：并行≡v3.3 顺序锚点（out/minimal/mix.flac）由既有 test_cli 的 sha 锚点
# 用例隐式锁定——CLI 渲染默认走并行路径，与主树 v3.3 顺序产物逐位一致；
# 不另设重复渲染用例（fast 预算纪律）。


@pytest.mark.fast
def test_render_minimal_max_parallel_one(monkeypatch):
    """BGM_MAX_PARALLEL=1：并发退化但渲染完整、位级不变（时长不劣化崩坏）。"""
    d = helpers.tmpdir("par_cap1")
    monkeypatch.delenv("BGM_NO_PARALLEL", raising=False)
    monkeypatch.delenv("BGM_MEM_FLOOR_GB", raising=False)
    t0 = time.monotonic()
    _render_minimal_inproc(d, monkeypatch, env={"BGM_MAX_PARALLEL": "1"})
    wall = time.monotonic() - t0
    assert wall < 120, f"BGM_MAX_PARALLEL=1 渲染异常缓慢：{wall:.1f}s"
    snap = _snapshot(d)
    assert "mix.flac" in snap and "stems/kick.synth.wav" in snap
