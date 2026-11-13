"""资源感知并发层（v3.4 并行渲染轮）。

背景（PLAN_v3 §10.7 冻机事故）：本机曾因渲染失控 + 全量加载叠加致 61GB 内存
耗尽、zram 风暴整机冻死——资源纪律是本层的第一优先级：**宁可欠利用，不可
冻机**。设计目标（64G/32G/16G 机器自适应）：

    probe()            静态探测（进程内缓存）：cpu 硬件线程 / 内存总量 / GPU
                       （nvidia-smi 风格，与 engines.gpu_guard.free_vram_mb 同源
                       探测逻辑；无 GPU 返回 None）。
    mem_available_gb() 实时读 /proc/meminfo MemAvailable——**每次现读，绝不缓存**
                       （缓存会让门控对本机其他进程的内存涨落失明）。
    plan_workers()     并发数规划（纯函数，可单测）：
                       workers = min( ceil(可用内存/per_task_gb),
                                      cpu_threads × cpu_cap_factor,
                                      hard_cap, n_tasks )
                       可用 = avail_gb − mem_floor；mem_floor 缺省
                       = max(6GB, 总内存×0.2)。
    DispatchGate       并行执行期实时门控：每次派发新任务前检查
                       mem_available_gb() ≥ floor + per_task_gb×(在途数+1)，
                       不足则 0.5s 轮询等待，超时（缺省 10 分钟）报错而非死等；
                       任务完成后释放配额。GIL 无碍——重活在子进程/ctypes
                       （fluidsynth）/numpy 释放 GIL 的算子里。
    run_pool()         ThreadPoolExecutor + DispatchGate 驱动器：结果按任务
                       序收集（与完成顺序无关）；并行关闭/单任务时退化为顺序
                       直接调用（fn 行为与旧循环逐位一致）。

环境开关（镜像 BGM_NO_INCREMENTAL 的 conftest 注入模式）：
    BGM_NO_PARALLEL=1   硬关：完全走旧顺序路径（run_pool 直接逐个 fn）。
    BGM_MAX_PARALLEL=N  硬并发上限覆盖（与 hard_cap 取 min，只能更小）。
    BGM_MEM_FLOOR_GB=X  内存地板覆盖（GB；缺省 max(6, total×0.2)）。

GPU 排除声明：demucs/ai 等 GPU 引擎**不进本池**——gpu_guard（BG-D8）已有本包
内 GPU 任务互斥（文件锁 + 显存闸），池化 GPU 任务会让多任务同时抢显存，明确
排除（工程轨层按 BG-D10 恒为确定性 CPU 引擎，池成员见 CPU_POOL_ENGINES）。

per_task_gb 估算表 = 开发期 /tmp/render_guard.py 包裹实测各引擎单任务峰值
RSS × 保守系数 1.5（实测记录见模块尾部"标定记录"）；估算偏大只损失并发度，
绝不放大冻机风险。
"""

import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

DEFAULT_CPU_CAP_FACTOR = 0.5    # 并行渲染只用一半硬件线程（另一半留给系统/其他 agent）
DEFAULT_HARD_CAP = 8            # 并行度绝对上限（更高收益递减、内存峰值难控）
MIN_FLOOR_GB = 6.0              # 内存地板下限：系统+zram+桌面环境的绝对保留
FLOOR_FRAC = 0.2                # 地板 = max(MIN_FLOOR_GB, 总内存×0.2)
GATE_POLL_S = 0.5               # DispatchGate 内存轮询间隔
GATE_TIMEOUT_S = 600.0          # 派发等待上限（10 分钟）：报错而非死等
SAFETY_FACTOR = 1.5             # 实测峰值 RSS → per_task 估算的保守系数

# 进池资格（工程 stem 层的 CPU 确定性引擎；GPU 引擎明确排除，见模块 docstring）
CPU_POOL_ENGINES = frozenset({"synth", "sf2", "sfz", "sample"})

# per_task_gb 经验估算表（实测峰值 RSS × 1.5 后再向上保守取整；标定记录
# 见模块尾部——估算偏大只损失并发度，绝不放大冻机风险）
PER_TASK_GB = {
    "synth": 0.5,        # 进程内 numpy：实测峰值树 37MB（微量），但波形中间量
                         # 随轨时长线性伸缩（5min 轨 float64 多级中间量可至
                         # 数百 MB）→ 按长轨口径保守 0.5GB
    "sf2": 0.5,          # 进程内 ctypes fluidsynth：实测 151MB（FluidR3_GM.sf2
                         # ~140MB 全量驻留/实例）×1.5≈0.23 → 0.5GB
    "sfz": 0.5,          # sfizz_render 子进程：微型合成 37MB / VSCO2 合奏 65MB /
                         # SalamanderGrand（715MB 采样库，流式读）157MB，
                         # 最重增量 ~120MB ×1.5 → 0.5GB
    "sample": 0.5,       # 进程内 numpy 混合 + ffmpeg 解码子进程：实测 103MB → 0.5GB
    "track_chain": 0.3,  # mixer 轨链 ffmpeg 子进程：实测 78MB ×1.5≈0.12 → 0.3GB
}
PER_TASK_DEFAULT_GB = 1.5        # 未知任务类型：按最重 CPU 引擎口径保守


class ResourceError(Exception):
    """资源感知层失败（门控超时/配置非法）。"""


# ---------------------------------------------------------------- 静态探测

_probe_cache = None
_probe_lock = threading.Lock()


def _read_meminfo_kb(key):
    """读 /proc/meminfo 单键（kB 值）；缺失返回 None。"""
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for ln in f:
                if ln.startswith(key + ":"):
                    return int(ln.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def mem_available_gb():
    """实时可用内存（GB）。**每次现读 /proc/meminfo，绝不缓存**（门控语义）。
    读不到（非 Linux / 异常）返回 None——调用方视为不可探测。"""
    kb = _read_meminfo_kb("MemAvailable")
    return None if kb is None else kb / (1024.0 * 1024.0)


def mem_total_gb():
    kb = _read_meminfo_kb("MemTotal")
    return None if kb is None else kb / (1024.0 * 1024.0)


def probe(refresh=False):
    """静态探测（进程内缓存，probe() 幂等）：cpu 硬件线程 / 内存总量 / GPU。

    GPU 探测复用 engines.gpu_guard 的 nvidia-smi 风格（探测失败=None 视为
    无 GPU 可探测）；结果 dict：
        {"cpu_threads": int, "mem_total_gb": float, "gpu": {...} | None}
    """
    global _probe_cache
    if _probe_cache is not None and not refresh:
        return _probe_cache
    with _probe_lock:
        if _probe_cache is not None and not refresh:
            return _probe_cache
        gpu = None
        try:
            import subprocess
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total,memory.free",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                total_mb, free_mb = r.stdout.strip().splitlines()[0].split(",")
                gpu = {"total_mb": int(total_mb.strip()),
                       "free_mb": int(free_mb.strip())}
        except Exception:                                     # noqa: BLE001
            gpu = None
        _probe_cache = {
            "cpu_threads": os.cpu_count() or 1,
            "mem_total_gb": mem_total_gb() or 0.0,
            "gpu": gpu,
        }
        return _probe_cache


# ---------------------------------------------------------------- 开关与地板

def parallel_enabled():
    """BGM_NO_PARALLEL=1 → 硬关（完全走旧顺序路径）。读取点在 run_pool，
    in-process 调用者与子进程同受保护（镜像 BGM_NO_INCREMENTAL 模式）。"""
    return os.environ.get("BGM_NO_PARALLEL") != "1"


def env_max_parallel():
    """BGM_MAX_PARALLEL=N 解析（非法值报 ResourceError，不静默吞）。"""
    v = os.environ.get("BGM_MAX_PARALLEL")
    if v is None or v == "":
        return None
    try:
        n = int(v)
    except ValueError:
        raise ResourceError(f"BGM_MAX_PARALLEL 非整数：{v!r}")
    if n < 1:
        raise ResourceError(f"BGM_MAX_PARALLEL 必须 ≥1：{v!r}")
    return n


def mem_floor_gb(total_gb=None):
    """内存地板（GB）：env BGM_MEM_FLOOR_GB 覆盖 > max(6, total×0.2)。"""
    v = os.environ.get("BGM_MEM_FLOOR_GB")
    if v is not None and v != "":
        try:
            f = float(v)
        except ValueError:
            raise ResourceError(f"BGM_MEM_FLOOR_GB 非数值：{v!r}")
        if f < 0:
            raise ResourceError(f"BGM_MEM_FLOOR_GB 不得为负：{v!r}")
        return f
    if total_gb is None:
        total_gb = probe()["mem_total_gb"]
    return max(MIN_FLOOR_GB, total_gb * FLOOR_FRAC)


# ---------------------------------------------------------------- 并发规划

def plan_workers(n_tasks, per_task_gb, *, cpu_cap_factor=DEFAULT_CPU_CAP_FACTOR,
                 hard_cap=DEFAULT_HARD_CAP, mem_floor_gb=None, avail_gb=None,
                 cpu_threads=None, total_gb=None):
    """并发数规划（纯函数：探测值经参数注入，缺省取本机当前值）。

        workers = max(1, min( ceil(可用 / per_task_gb),
                              floor(cpu_threads × cpu_cap_factor),
                              hard_cap, n_tasks ))
        可用 = avail_gb − mem_floor；floor 缺省 = max(6, total×0.2)
        （经 mem_floor_gb() 读 env 覆盖）。

    env BGM_MAX_PARALLEL=N 与 hard_cap 取 min（只能更小）。
    内存再紧也保底 1（单任务在途由 DispatchGate 实时门控兜底——真的低于
    floor+per_task 时门控会等待/超时报错，绝不盲目派发）。
    """
    n_tasks = int(n_tasks)
    if n_tasks <= 0:
        return 0
    if per_task_gb is None or per_task_gb <= 0:
        raise ResourceError(f"per_task_gb 必须为正数：{per_task_gb!r}")
    if cpu_threads is None:
        cpu_threads = probe()["cpu_threads"]
    if mem_floor_gb is None:
        mem_floor_gb = globals()["mem_floor_gb"](total_gb)
    if avail_gb is None:
        avail_gb = mem_available_gb()

    hard = int(hard_cap)
    env_cap = env_max_parallel()
    if env_cap is not None:
        hard = min(hard, env_cap)

    if avail_gb is not None:                       # 可探测才做内存约束
        usable = max(0.0, avail_gb - mem_floor_gb)
        mem_cap = max(1, math.ceil(usable / per_task_gb))
    else:
        mem_cap = 1                                # 不可探测 → 最保守单发
    cpu_cap = max(1, int(math.floor(cpu_threads * cpu_cap_factor)))
    return max(1, min(mem_cap, cpu_cap, hard, n_tasks))


def max_per_task_gb(engines):
    """引擎名集合 → 池化 per_task 估算（混编取最大——宁可保守）。"""
    return max((PER_TASK_GB.get(e, PER_TASK_DEFAULT_GB) for e in engines),
               default=PER_TASK_DEFAULT_GB)


# ---------------------------------------------------------------- 实时门控

class DispatchGate:
    """派发门控：内存实时配额管理（线程安全）。

    acquire()：在途数+1 前检查 mem_available_gb() ≥ floor + per_task×(在途+1)；
    不足则 poll_s 轮询直到 timeout_s 抛 ResourceError（报错而非死等）。
    release()：任务完成（含异常）释放配额。内存不可探测（None）时放行——
    规划层已按最保守口径限并发，探测缺失不应让渲染完全停摆。
    """

    def __init__(self, per_task_gb, mem_floor_gb=None, timeout_s=GATE_TIMEOUT_S,
                 poll_s=GATE_POLL_S):
        self.per_task_gb = float(per_task_gb)
        self.timeout_s = float(timeout_s)
        self.poll_s = float(poll_s)
        self._floor = mem_floor_gb   # None → 每次 acquire 时现算（env 可变）
        self._lock = threading.Lock()
        self._inflight = 0

    def _floor_gb(self):
        return self._floor if self._floor is not None \
            else globals()["mem_floor_gb"]()

    @property
    def inflight(self):
        with self._lock:
            return self._inflight

    def acquire(self):
        deadline = time.monotonic() + self.timeout_s
        while True:
            # 检查与递增同锁段（v3.4 验收 M-1：原 check-then-increment 有
            # TOCTOU 窗口，多线程同时 acquire 可瞬时超发；现役调用仅单
            # 派发线程不受影响，此处收口防未来复用踩坑）
            with self._lock:
                inflight = self._inflight
                need = self._floor_gb() + self.per_task_gb * (inflight + 1)
                avail = mem_available_gb()
                if avail is None or avail >= need:
                    self._inflight += 1
                    return
            if time.monotonic() >= deadline:
                raise ResourceError(
                    f"DispatchGate 派发等待超时（{self.timeout_s:.0f}s）："
                    f"需要可用内存 ≥{need:.1f}GB（floor {self._floor_gb():.1f}"
                    f" + per_task {self.per_task_gb:g}×{inflight + 1}），"
                    f"实测 {avail:.1f}GB——系统内存长期紧张，拒绝继续派发")
            time.sleep(self.poll_s)

    def release(self):
        with self._lock:
            self._inflight = max(0, self._inflight - 1)

    class _Slot:
        __slots__ = ("gate",)

        def __init__(self, gate):
            self.gate = gate

        def __enter__(self):
            self.gate.acquire()
            return self

        def __exit__(self, exc_type, exc, tb):
            self.gate.release()
            return False

    def slot(self):
        """with gate.slot(): ... —— acquire/release 配对糖。"""
        return DispatchGate._Slot(self)


# ---------------------------------------------------------------- 池驱动器

def run_pool(tasks, fn, *, per_task_gb, workers=None,
             cpu_cap_factor=DEFAULT_CPU_CAP_FACTOR, hard_cap=DEFAULT_HARD_CAP,
             gate_timeout_s=GATE_TIMEOUT_S, gate_poll_s=GATE_POLL_S):
    """ThreadPoolExecutor + DispatchGate 驱动：tasks 逐个经门控派发给 fn，
    结果按任务序返回（与完成顺序无关——收集按 index，输出路径不变）。

    退化路径（与旧顺序循环 fn(t) 逐位一致）：BGM_NO_PARALLEL=1 / workers≤1 /
    单任务 → 直接顺序调用。

    异常语义：fn 抛错 → 对应 future.result() 按任务序抛出（消息含轨名，调用
    方包装）；在途其余任务自然收尾后退出（产物留在盘上，与顺序版"先完成者
    已落盘"语义一致）。派发门控超时 → 取消未启动任务、等在途收尾、抛
    ResourceError。
    """
    tasks = list(tasks)
    if not tasks:
        return []
    if not parallel_enabled():
        return [fn(t) for t in tasks]
    if workers is None:
        workers = plan_workers(len(tasks), per_task_gb,
                               cpu_cap_factor=cpu_cap_factor, hard_cap=hard_cap)
    if workers <= 1 or len(tasks) == 1:
        return [fn(t) for t in tasks]

    gate = DispatchGate(per_task_gb, timeout_s=gate_timeout_s, poll_s=gate_poll_s)
    results = [None] * len(tasks)
    futures = []
    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="bgm-par") as ex:
        try:
            for i, t in enumerate(tasks):
                gate.acquire()                       # 派发前门控（阻塞式背压）
                fut = ex.submit(fn, t)
                fut.add_done_callback(lambda _f: gate.release())
                futures.append((i, fut))
        except BaseException:
            # 门控超时/提交失败：取消未启动任务；在途任务持资源必须等其收尾
            for _, f in futures:
                f.cancel()
            for _, f in futures:
                try:
                    f.result()
                except BaseException:
                    pass
            raise
        for i, f in futures:
            results[i] = f.result()
    return results


# ---------------------------------------------------------------- 标定记录
# 开发期实测（2026-08-23，64GB/32 线程机，worktree music_tools_v34；
# 工具=/tmp/rss_probe.py 50ms 进程树 RSS 采样，验证过 500MB 负载报 504MB；
# 树峰含 ~36MB python+numpy 父进程基线——miniconda numpy RSS 实测仅 36MB）：
#   synth stem（lead 8beat 全音符轨）       树峰 37MB → 表 0.5GB（长轨伸缩余量）
#   sf2 stem（FluidR3_GM strings，4s）      树峰 151MB → 表 0.5GB
#   sample stem（kenney click×8，ogg 解码） 树峰 103MB → 表 0.5GB
#   sfz stem 微型合成 SFZ（0.4s 正弦）      树峰 37MB ┐
#   sfz stem VSCO2 CelloEnsSusVib 真乐器    树峰 65MB │→ 表 0.5GB
#   sfz stem SalamanderGrand V3（715MB 库） 树峰 157MB┘（sfizz 流式读采样）
#   track_chain（minimal strings 链）       树峰 78MB → 表 0.3GB
# 产线交叉锚点：R4-W1 命运工程 23 轨全链顺序渲染峰值 ~1.3GB（PLAN_v3 §10.8）
# ——顺序单发口径与本表（并发单槽 0.5GB）量级吻合。
#（标定命令与逐项输出全文见本轮最终报告；表值 ≥ 实测×1.5。）
