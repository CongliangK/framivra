"""Windows physical-memory probe: portable mocks plus native Windows smoke test."""
import ctypes
import os
import threading
from types import SimpleNamespace

import pytest

from bgm_gen.core import resources


class FakeGlobalMemoryStatusEx:
    def __init__(self, readings):
        self.readings = iter(readings)
        self.lengths = []

    def __call__(self, pointer):
        status = ctypes.cast(pointer, ctypes.POINTER(resources._MemoryStatusEx)).contents
        self.lengths.append(status.dwLength)
        value = next(self.readings)
        if isinstance(value, Exception):
            raise value
        if value is None:
            return 0
        status.ullTotalPhys, status.ullAvailPhys = value
        # Ensure physical memory, not pagefile or virtual memory, drives results.
        status.ullTotalPageFile = 99 * 1024**3
        status.ullAvailVirtual = 88 * 1024**3
        return 1


def install_fake(monkeypatch, readings):
    fake = FakeGlobalMemoryStatusEx(readings)
    monkeypatch.setattr(resources, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(resources.ctypes, "WinDLL",
                        lambda *args, **kwargs: SimpleNamespace(GlobalMemoryStatusEx=fake),
                        raising=False)
    return fake


@pytest.mark.fast
def test_windows_physical_memory_and_fresh_available(monkeypatch):
    gib = 1024**3
    fake = install_fake(monkeypatch, [(32*gib, 12*gib), (32*gib, 7*gib),
                                     (32*gib, 0)])
    assert ctypes.sizeof(resources._MemoryStatusEx) == 64
    assert resources.mem_total_gb() == 32.0
    assert resources.mem_available_gb() == 7.0
    assert resources.mem_available_gb() == 0.0
    assert fake.lengths == [64, 64, 64]
    assert fake.argtypes == [ctypes.POINTER(resources._MemoryStatusEx)]
    assert fake.restype is ctypes.c_int


@pytest.mark.fast
@pytest.mark.parametrize("reading", [None, RuntimeError("API failure"), (0, 0), (1, 2)])
def test_windows_bad_result_returns_none(monkeypatch, reading):
    install_fake(monkeypatch, [reading, reading])
    assert resources.mem_total_gb() is None
    assert resources.mem_available_gb() is None


@pytest.mark.fast
def test_probe_cache_does_not_cache_available(monkeypatch):
    gib = 1024**3
    fake = install_fake(monkeypatch, [(16*gib, 8*gib), (16*gib, 7*gib),
                                     (16*gib, 6*gib)])
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 4, raising=False)
    monkeypatch.setattr(resources, "_probe_cache", None)
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout=""))
    first = resources.probe()
    assert first["mem_total_gb"] == 16.0
    assert resources.probe() is first
    assert resources.mem_available_gb() == 7.0
    assert resources.mem_available_gb() == 6.0
    assert len(fake.lengths) == 3


@pytest.mark.fast
def test_windows_api_load_failure_returns_none(monkeypatch):
    monkeypatch.setattr(resources, "os", SimpleNamespace(name="nt"))
    def fail(*args, **kwargs):
        raise OSError("missing kernel32")
    monkeypatch.setattr(resources.ctypes, "WinDLL", fail, raising=False)
    assert resources.mem_total_gb() is None
    assert resources.mem_available_gb() is None


@pytest.mark.fast
def test_non_windows_proc_logic_unchanged(monkeypatch):
    monkeypatch.setattr(resources, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(resources, "_read_meminfo_kb",
                        lambda key: {"MemTotal": 4 * 1024**2,
                                     "MemAvailable": 1536 * 1024}[key])
    assert resources.mem_total_gb() == 4.0
    assert resources.mem_available_gb() == 1.5
    monkeypatch.setattr(resources, "_read_meminfo_kb", lambda key: None)
    assert resources.mem_total_gb() is None
    assert resources.mem_available_gb() is None


@pytest.mark.fast
def test_low_windows_memory_gate_and_sequential_contract(monkeypatch):
    gib = 1024**3
    install_fake(monkeypatch, [(16*gib, 0), (16*gib, 0)])
    gate = resources.DispatchGate(1, mem_floor_gb=6, timeout_s=0)
    with pytest.raises(resources.ResourceError):
        gate.acquire()
    # The existing single-worker path intentionally does not enter the gate.
    monkeypatch.setattr(resources, "parallel_enabled", lambda: True)
    assert resources.run_pool([1], lambda x: x + 1, per_task_gb=1, workers=1) == [2]


@pytest.mark.fast
@pytest.mark.parametrize("recover", [True, False])
def test_run_pool_windows_probe_failure_after_planning(monkeypatch, recover):
    gib = 1024**3
    readings = [(16*gib, 12*gib), None]
    if recover:
        readings += [(16*gib, 12*gib), (16*gib, 12*gib)]
    else:
        readings += [None] * 100
    fake = install_fake(monkeypatch, readings)
    monkeypatch.setattr(resources.os, "environ", os.environ, raising=False)
    monkeypatch.setattr(resources, "probe", lambda: {"cpu_threads": 8,
                                                       "mem_total_gb": 16})
    monkeypatch.setattr(resources, "mem_total_gb", lambda: 16)
    monkeypatch.delenv("BGM_NO_PARALLEL", raising=False)
    monkeypatch.delenv("BGM_MAX_PARALLEL", raising=False)
    monkeypatch.delenv("BGM_MEM_FLOOR_GB", raising=False)
    dispatched = []

    def task(value):
        dispatched.append((value, len(fake.lengths)))
        return value

    if recover:
        assert resources.run_pool([1, 2], task, per_task_gb=1,
                                  gate_timeout_s=1, gate_poll_s=0.001) == [1, 2]
        assert len(dispatched) == 2
        assert all(reads >= 3 for _, reads in dispatched)
        assert len(fake.lengths) == 4
    else:
        with pytest.raises(resources.ResourceError, match="不可探测"):
            resources.run_pool([1, 2], task, per_task_gb=1,
                               gate_timeout_s=0.02, gate_poll_s=0.001)
        assert dispatched == []
        assert len(fake.lengths) >= 3


@pytest.mark.fast
@pytest.mark.parametrize("blind_probe", ["available", "total"])
@pytest.mark.parametrize("recover", [True, False])
def test_inflight_blindness_and_callback_release(monkeypatch, recover, blind_probe):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(resources, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.delenv("BGM_MEM_FLOOR_GB", raising=False)
    monkeypatch.delenv("BGM_NO_PARALLEL", raising=False)
    monkeypatch.delenv("BGM_MAX_PARALLEL", raising=False)
    monkeypatch.setattr(resources, "probe", lambda: {"cpu_threads": 8, "mem_total_gb": 16})
    both_started, blind, restored, finish, third_submitted = (threading.Event() for _ in range(5))
    started, submitted, results, gates = [], [], [], []
    timed_out = threading.Event()
    lock = threading.Lock()
    reads = {"available": 0, "total": 0}

    def reading(kind, value, successful_reads):
        reads[kind] += 1
        if kind != blind_probe or reads[kind] <= successful_reads:
            return value
        assert both_started.wait(5)
        # The second blind poll proves the first None reached the gate and
        # was rejected before the test can signal recovery.
        if reads[kind] >= successful_reads + 2:
            blind.set()
        return value if restored.is_set() else None

    def available():
        return reading("available", 12.0, 3)  # plan and two admissions

    def total():
        return reading("total", 16.0, 2)  # two admissions

    class TrackingExecutor(ThreadPoolExecutor):
        def submit(self, fn, *args, **kwargs):
            future = super().submit(fn, *args, **kwargs)
            submitted.append(args[0])
            if len(submitted) == 3:
                third_submitted.set()
            return future

    original_gate = resources.DispatchGate
    class TrackingGate(original_gate):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            gates.append(self)
        def acquire(self):
            try:
                return super().acquire()
            except resources.ResourceError:
                timed_out.set()
                raise

    def task(value):
        with lock:
            started.append(value)
            if len(started) == 2:
                both_started.set()
        assert finish.wait(10)
        return value

    def run():
        try:
            results.append(resources.run_pool([1, 2, 3], task, per_task_gb=1,
                hard_cap=2, gate_timeout_s=1.5, gate_poll_s=0.001))
        except Exception as exc:
            results.append(exc)

    monkeypatch.setattr(resources, "mem_available_gb", available)
    monkeypatch.setattr(resources, "mem_total_gb", total)
    monkeypatch.setattr(resources, "ThreadPoolExecutor", TrackingExecutor)
    monkeypatch.setattr(resources, "DispatchGate", TrackingGate)
    dispatcher = threading.Thread(target=run)
    dispatcher.start()
    try:
        assert both_started.wait(5) and blind.wait(5)
        assert submitted == [1, 2]
        assert gates[0].inflight == 2
        if recover:
            restored.set()
            assert third_submitted.wait(5)
        else:
            assert timed_out.wait(5)  # run_pool must still wait for in-flight tasks
            assert dispatcher.is_alive()
            assert submitted == [1, 2]
    finally:
        finish.set()
        dispatcher.join(5)
    assert not dispatcher.is_alive()
    assert gates[0].inflight == 0  # done callbacks released both original slots
    if recover:
        assert results == [[1, 2, 3]]
        assert submitted == [1, 2, 3]
    else:
        assert len(results) == 1 and isinstance(results[0], resources.ResourceError)
        assert sorted(started) == [1, 2]


@pytest.mark.fast
def test_failed_total_retries_without_gpu_and_recovery_uses_large_floor(monkeypatch):
    import subprocess
    gib = 1024**3
    fake = install_fake(monkeypatch, [None, None, (128*gib, 40*gib),
                                     (128*gib, 24*gib), (128*gib, 24*gib),
                                     (128*gib, 24*gib)])
    monkeypatch.setattr(resources.os, "environ", os.environ, raising=False)
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 16, raising=False)
    monkeypatch.setattr(resources, "_probe_cache", None)
    gpu_calls = []
    def no_gpu(*args, **kwargs):
        gpu_calls.append(1)
        return SimpleNamespace(returncode=1, stdout="")
    monkeypatch.setattr(subprocess, "run", no_gpu)
    monkeypatch.delenv("BGM_MEM_FLOOR_GB", raising=False)
    monkeypatch.delenv("BGM_MAX_PARALLEL", raising=False)
    assert resources.plan_workers(8, 1, cpu_threads=16, avail_gb=40) == 1
    assert resources.plan_workers(8, 1, cpu_threads=16, avail_gb=40) == 1
    assert len(gpu_calls) == 1
    assert resources.plan_workers(8, 1, cpu_threads=16, avail_gb=40) == 8
    assert len(gpu_calls) == 1
    assert resources.mem_floor_gb() == pytest.approx(25.6)
    gate = resources.DispatchGate(1, timeout_s=0)
    with pytest.raises(resources.ResourceError):
        gate.acquire()  # 24 GiB < 25.6 GiB floor + 1 GiB
    assert len(fake.lengths) == 5
    assert resources.probe(refresh=True)["gpu"] is None
    assert len(gpu_calls) == 2  # explicit refresh retains existing GPU semantics


@pytest.mark.fast
def test_explicit_workers_cannot_bypass_unknown_total(monkeypatch):
    import subprocess
    fake = install_fake(monkeypatch, [None] * 100)
    monkeypatch.setattr(resources.os, "environ", os.environ, raising=False)
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 16, raising=False)
    monkeypatch.setattr(resources, "_probe_cache", None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout=""))
    monkeypatch.delenv("BGM_NO_PARALLEL", raising=False)
    monkeypatch.delenv("BGM_MEM_FLOOR_GB", raising=False)
    dispatched = []
    with pytest.raises(resources.ResourceError, match="总量不可探测"):
        resources.run_pool([1, 2], dispatched.append, per_task_gb=1, workers=2,
                           gate_timeout_s=0.02, gate_poll_s=0.001)
    assert dispatched == []
    assert len(fake.lengths) >= 2


@pytest.mark.fast
@pytest.mark.parametrize("override", ["0", "2.5"])
def test_windows_explicit_floor_does_not_require_total(monkeypatch, override):
    gib = 1024**3
    install_fake(monkeypatch, [(16*gib, 12*gib)] * 3)
    monkeypatch.setattr(resources.os, "environ", os.environ, raising=False)
    monkeypatch.setenv("BGM_MEM_FLOOR_GB", override)
    monkeypatch.delenv("BGM_NO_PARALLEL", raising=False)
    monkeypatch.delenv("BGM_MAX_PARALLEL", raising=False)
    monkeypatch.setattr(resources, "mem_total_gb", lambda: None)
    def forbidden_probe(*args, **kwargs):
        pytest.fail("floor override must not call probe")
    monkeypatch.setattr(resources, "probe", forbidden_probe)
    assert resources.plan_workers(2, 1, cpu_threads=8, avail_gb=12) == 2
    assert resources.run_pool([1, 2], lambda x: x, per_task_gb=1, workers=2,
                              gate_timeout_s=0.1) == [1, 2]


@pytest.mark.fast
@pytest.mark.parametrize("override", ["bad", "-1"])
def test_windows_invalid_floor_is_rejected(monkeypatch, override):
    monkeypatch.setattr(resources, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setenv("BGM_MEM_FLOOR_GB", override)
    monkeypatch.setattr(resources, "mem_total_gb", lambda: None)
    with pytest.raises(resources.ResourceError):
        resources.plan_workers(2, 1, cpu_threads=8, avail_gb=12)
    with pytest.raises(resources.ResourceError):
        resources.DispatchGate(1, timeout_s=0).acquire()


@pytest.mark.fast
def test_windows_total_read_does_not_hold_gate_lock(monkeypatch):
    monkeypatch.setattr(resources, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setenv("BGM_MEM_FLOOR_GB", "0")
    monkeypatch.setattr(resources, "mem_available_gb", lambda: 100)
    monkeypatch.setattr(resources, "probe", lambda: pytest.fail("unexpected GPU probe"))
    gate = resources.DispatchGate(1, timeout_s=0.05, poll_s=0.001)
    gate.acquire()
    monkeypatch.delenv("BGM_MEM_FLOOR_GB")
    entered, resume, released = threading.Event(), threading.Event(), threading.Event()
    def slow_total():
        entered.set()
        resume.wait(2)
        return None
    monkeypatch.setattr(resources, "mem_total_gb", slow_total)
    def acquire():
        with pytest.raises(resources.ResourceError):
            gate.acquire()
    worker = threading.Thread(target=acquire)
    worker.start()
    try:
        assert entered.wait(1)
        releaser = threading.Thread(target=lambda: (gate.release(), released.set()))
        releaser.start()
        try:
            assert released.wait(1), "release blocked by total probe"
        finally:
            resume.set()
            releaser.join(2)
    finally:
        resume.set()
        worker.join(2)
    assert not worker.is_alive()
    assert gate.inflight == 0


@pytest.mark.fast
def test_non_windows_gate_retains_unknown_memory_behavior(monkeypatch):
    monkeypatch.setattr(resources, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(resources, "mem_available_gb", lambda: None)
    gate = resources.DispatchGate(1, mem_floor_gb=6, timeout_s=0)
    gate.acquire()
    assert gate.inflight == 1
    gate.release()


@pytest.mark.fast
@pytest.mark.skipif(os.name != "nt", reason="native Windows API only")
def test_native_windows_memory():
    memory = resources._windows_physical_memory()
    assert memory is not None
    total, available = memory
    assert total > 0 and 0 <= available <= total
    assert resources.mem_total_gb() > 0
    assert resources.mem_available_gb() is not None
