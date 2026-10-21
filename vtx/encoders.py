"""vtx 编码器/渲染器选型纯决策函数：Chrome 定位、NVENC 能力探测、输出编码参数选择。

无副作用（除探测子进程本身）；全部行为与拆分前逐字一致。
"""
import contextlib, os, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from .browser_paths import chrome_names, chrome_install_paths


def find_chrome() -> str:
    """跨平台定位 Chrome/Chromium：环境变量 VTX_CHROME 优先，其次 PATH 常见名，再退标准安装路径。"""
    cands = (*chrome_names(), *chrome_install_paths(sys.platform))
    for c in cands:
        if c and (shutil.which(c) or os.path.isfile(c)):
            return c
    print("✗ 找不到 Chrome/Chromium（gfx 帧截图需要）。可设环境变量 VTX_CHROME 指定可执行文件路径"); sys.exit(1)


def nvenc_ok(c, use_memo: bool = False) -> bool:
    if os.environ.get("VTX_NO_NVENC"): return False
    # P2-1（方案二阶段3 复审）：use_memo=True 时真探测结果做进程级 memo——
    # seg_encoder_kind 的消费者（prepare_layers 编码 / gc._current_keys 键集重算 /
    # subs 预检 / timeline_diff）各自独立调用，若每次都真探测，探测结果在两次调用
    # 间翻转（NVENC 硬件级故障，如驱动忙/设备消失）会导致键集分叉：gc 把活 seg 误判
    # 成键集外垃圾回收 → 整轮重编。memo 后同一进程内全部消费者同值（memo 判定放在
    # dry 短路之前：先有真探测结论时，后续 dry 调用也同值，键口径进程内恒一致）；
    # 跨进程翻转场景仍存在，但属硬件级故障（罕见且后果仅一次性重编码，不做跨进程
    # 协调）。pick_vcodec 不用 memo（保持默认 use_memo=False）：成片编码器探测失败
    # 回落 x264 的告警语义要保持即时性（每次真探测）。
    global _NVENC_OK_MEMO
    if use_memo and _NVENC_OK_MEMO is not None:
        return _NVENC_OK_MEMO
    if c.dry: return True
    # 探测尺寸须 ≥ NVENC 最小宽 145（128 会假失败）
    r = c.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.04",
               "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-"], need=False)
    ok = r != "" and r.returncode == 0
    if use_memo:
        _NVENC_OK_MEMO = ok
    return ok


def nvenc_concurrency(c, workers: int) -> int:
    """NVENC 并发会话探测：workers 路并发编码探测，任一失败回落串行（返回 1）。

    自 composite.render_segmented 提取（2026-09-10 模块化拆分），探测命令与
    打印信息与拆分前逐字一致。
    """
    def _p(_):
        r = c.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                   "-i", "color=c=black:s=256x144:d=0.04", "-frames:v", "1",
                   "-c:v", "h264_nvenc", "-f", "null", "-"], need=False)
        return r == "" or r.returncode == 0
    with ThreadPoolExecutor(workers) as ex:
        futures = [ex.submit(copy_context().run, _p, i) for i in range(workers)]
        if not all(f.result() for f in futures):
            print("⚠ NVENC 并发会话探测失败，回落串行渲染")
            return 1
    return workers


# ---------------------------------------------------------------- NVENC 会话预算（方案二阶段3，2026-09-12）
# 管的是【本进程内】全部 NVENC 编码点：① seg 预编码（composite.prepare_layers 无
# alpha 分支的 NVENC 任务，经 infra.run_ffmpeg_task 逐任务占槽）；② 段渲染
# （composite.render_segmented 的 NVENC 段，批次开始一次性占 min(workers, 预算) 个
# 槽、结束释放）。4K 导出（apps/publish/check_4k.sh）是独立 ffmpeg 进程，不经过本信号量
# ——跨进程协调靠用户把 VTX_NVENC_BUDGET 按「预算 − 本进程并发」下调。
# 预算默认 8：NVENC 并发拐点实测 8（超过后聚合 fps 无收益，反触发驱动会话回落，
# 见方案二/DEV.md）。探测（nvenc_concurrency）与预算分工：探测管「NVENC 会话真
# 可用」（驱动层面并发上限，探测失败回落串行）；预算管「本进程内不超发」（防
# seg 预编码后台任务与段渲染同进程叠加——当前编排时间上不重叠，机制上封死）。
_NVENC_SEM = None
_NVENC_CAPACITY = None
_NVENC_RESERVATION_LOCK = threading.Lock()
_NVENC_SEM_LOCK = threading.Lock()
_NVENC_OK_MEMO = None   # nvenc_ok(use_memo=True) 的进程级探测结果缓存（P2-1，见函数注释）


def nvenc_budget_value() -> int:
    v = os.environ.get("VTX_NVENC_BUDGET", "")
    return int(v) if v.isdigit() and int(v) > 0 else 8


# ---------------- S5 NVENC OOM 守卫·VRAM 判读档位（R-07h 签发版 §二.1，2026-09-26） ----------------
# 三项守卫的第三项（①并发探测 nvenc_concurrency／②预算信号量两项既有语义零变化）：
# nvidia-smi 查 used → 档位映射，并入信号量定值域：有效预算=min(VTX_NVENC_BUDGET, 档位)。
# 求值=进程内一次定值（同 _NVENC_SEM／_NVENC_OK_MEMO memo 先例），仅在 NVENC 首次占槽
# （_nvenc_pool 定值）时触达；非 NVENC 任务/dry/探测路径零调用。分态效力（修订件修 3）：
# 入场态（窗起 VRAM 高位）=守卫防御；窗内爬升态=memo 不追踪爬升、守卫无直接防御，
# 由重试队列（瞬时型）+耗尽 fail-fast 失败清单（持续型）承接——如实声明。
#
# 档位表（D1 预注册，签发即冻结；窗内不可调，调档走修订程序——修订件修 4③）：
#   used < 6000 MiB      → 档位 8   安全区：W0 窗起实测 ~2.0G 量级（本机待机 1.2G），
#                          全额预算不钳制（基准窗常态落本档，K4 带宽面零扰动）
#   6000 ≤ used < 9000   → 档位 6   保守初值（无实测锚，线性降档中继，方向恒保守）
#   9000 ≤ used < 12000  → 档位 4   保守初值（同上）
#   12000 ≤ used < 14300 → 档位 2   逼近 OOM 锚下沿，强钳近串行（锚间保守外推）
#   used ≥ 14300 MiB     → 档位 1   OOM 锚：R-07e 轮 3 死亡窗 19 样本 used
#                          min 14300/中位 15010/max 15817MiB（perf 案卷），≥min 即
#                          NVENC 分配失败实证在案 → 串行
# 两实测锚覆盖声明（修 4①）：安全基线 2.0G→档 8；OOM 时点 14300MiB→档 1。
# memory.used=全机口径（含桌面 Chrome 本底），本底高位触发钳制系设计语义内有意保守
# 非缺陷（修 4④）；审计面=nvenc_guard_stats()（clamp/failopen 计数，随回执留档）。
# fail-open 语义冻结（§二.1）：nvidia-smi 不可得/超时/解析失败 → 档位=不钳制+显式
# 告警一行（grep 标记「VRAM 守卫」，守卫失效面显形不静默；非 NVENC 机器兼容性强制
# fail-open，否则无卡机全测套件恒红）。
_VRAM_TIER_UNSET = object()   # 「未求值」哨兵（None=已求值且 fail-open，二者须可辨）
_VRAM_TIER_MEMO = _VRAM_TIER_UNSET
_GUARD_STATS = {"clamp": 0, "failopen": 0, "retry": 0, "transient": 0, "exhausted": 0}


def nvenc_guard_stats() -> dict:
    """守卫/重试护栏计数（J7′ 回执数据源）：clamp=守卫降档触发、failopen=失效面、
    retry=重试执行次数、transient=重试成功件（瞬时型）、exhausted=重试耗尽件（环境型）。"""
    return dict(_GUARD_STATS)


def nvenc_guard_note(kind: str) -> None:
    """护栏计数登记（infra 失败语义域调用）："transient"=重试成功（瞬时型）/
    "exhausted"=重试耗尽（环境型）（修 11 判别随回执留档）。"""
    _GUARD_STATS[kind] += 1


def _vram_query_used_mib():
    """nvidia-smi 单查（GPU0 首行口径，超时阈值 2.0s——D1 第七点成文值）。返回 used
    MiB 整数；任何失效 → None：①命令不可得（FileNotFoundError/OSError）②超时
    （subprocess.TimeoutExpired）③解析失败（空串/非数字/[N/A] 畸形/无输出行）。
    三型统一 fail-open（tests 经本 seam 注入桩，不起真 nvidia-smi）。"""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=2.0)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (r.stdout or "").strip().splitlines()
    if not lines:
        return None
    tok = lines[0].strip()
    if tok.lower().endswith("mib"):
        tok = tok[:-3].strip()
    return int(tok) if tok.isdigit() else None


def vram_tier_for(used_mib: int) -> int:
    """used→档位纯映射（D1 档位表唯一实现，依据见节注预注册；tests 注入假值直测）。"""
    if used_mib >= 14300: return 1
    if used_mib >= 12000: return 2
    if used_mib >= 9000: return 4
    if used_mib >= 6000: return 6
    return 8


def vram_guard_tier():
    """VRAM 判读档位（进程内一次定值 memo）。返回档位 int；None=fail-open（不钳制）。
    可注入桩：tests monkeypatch 本函数或 _vram_query_used_mib/vram_tier_for（修 9①，
    不起 nvidia-smi）；改桩后须 reset_vram_guard()（memo 先例同 reset_nvenc_budget）。"""
    global _VRAM_TIER_MEMO
    if _VRAM_TIER_MEMO is not _VRAM_TIER_UNSET:
        return _VRAM_TIER_MEMO
    try:
        used = _vram_query_used_mib()
    except Exception:   # noqa: BLE001 —— 守卫不得伤主链：查询面任何意外（含注入桩
        used = None     # 抛异常）=失效面，统一 fail-open（显形告警，非静默）
    if used is None:
        _VRAM_TIER_MEMO = None
        _GUARD_STATS["failopen"] += 1
        print("⚠ VRAM 守卫：nvidia-smi 不可得/超时/解析失败 → 档位不钳制"
              "（fail-open，守卫失效面显形，非静默）")
        return None
    tier = vram_tier_for(used)
    _VRAM_TIER_MEMO = tier
    budget = nvenc_budget_value()
    if tier < budget:
        _GUARD_STATS["clamp"] += 1
        print(f"⚠ VRAM 守卫：显存 used={used}MiB → 档位 {tier}，"
              f"有效预算=min(预算 {budget}, 档位 {tier})={tier}（守卫降档触发）")
    else:
        print(f"VRAM 守卫：显存 used={used}MiB → 档位 {tier} ≥ 预算 {budget}，不钳制")
    return tier


def nvenc_effective_budget() -> int:
    """有效预算=min(VTX_NVENC_BUDGET, VRAM 档位)；fail-open（档位 None）=预算原值。
    仅由 _nvenc_pool 定值域调用（NVENC 首次占槽），进程内一次定值。"""
    b = nvenc_budget_value()
    t = vram_guard_tier()
    return b if t is None else min(b, t)


def reset_vram_guard():
    """仅测试用：清空 VRAM 守卫 memo 与护栏计数（改桩后须重置才生效——memo 先例）。"""
    global _VRAM_TIER_MEMO
    with _NVENC_SEM_LOCK:
        _VRAM_TIER_MEMO = _VRAM_TIER_UNSET
    for k in _GUARD_STATS:
        _GUARD_STATS[k] = 0


def _nvenc_pool():
    """Return the semaphore and its capacity from the same frozen allocation."""
    global _NVENC_SEM, _NVENC_CAPACITY
    with _NVENC_SEM_LOCK:
        if _NVENC_SEM is None:
            _NVENC_CAPACITY = nvenc_effective_budget()
            _NVENC_SEM = threading.Semaphore(_NVENC_CAPACITY)
        return _NVENC_SEM, _NVENC_CAPACITY


def reset_nvenc_budget():
    """仅测试用：重置惰性信号量（改 VTX_NVENC_BUDGET 后须重置才生效——信号量
    预算在首次使用时定值，进程内不改）。"""
    global _NVENC_SEM, _NVENC_CAPACITY
    with _NVENC_SEM_LOCK:
        _NVENC_SEM = _NVENC_CAPACITY = None


def reset_nvenc_ok_memo():
    """仅测试用：清空 nvenc_ok(use_memo=True) 的进程级探测缓存。"""
    global _NVENC_OK_MEMO
    _NVENC_OK_MEMO = None


# ---------------- S5 失败 seg 重试队列（R-07h 签发版 §二.2：机制落本模块，失败语义落 infra） ----------------
# 仅 nvenc=True 任务失败入队：至多 N=2 次重试（总尝试=1+N=3），指数退避基值 0.5s
# （D1：NVENC 瞬时争用恢复时标无实测在案=保守经验初值，显式登记——修订件修 5/P-6；
# 0.5→1.0s 退避窗 ≪ 分钟级编码任务，快速失败终局不被显著推迟）。判别（修 11）：
# 重试成功=瞬时型／重试耗尽=环境型，随失败清单行+护栏计数留档。
# 占槽纪律（冻结）：重试循环位于占槽域之外——每次尝试独立占槽，退避等待期不持槽
# （微测断言退避期峰值并发 ≤ 有效预算：tests/media/test_nvenc_oom_guard.py TestBackoffNoSlot）。
# 重试=原 argv 原样重发：cmd 列表对象原样重用、逐元素全等（编码命令一字不动红线）。
NVENC_RETRY_N = 2                 # 重试次数上限（总尝试=1+N=3）
NVENC_RETRY_ATTEMPTS = NVENC_RETRY_N + 1
_NVENC_RETRY_BACKOFF_BASE = 0.5   # 指数退避基值秒（D1 保守经验初值）
retry_clock = time.sleep          # 可注入退避时钟（tests 注入记录器=零延时模式）


def nvenc_retry_backoff(failed_attempt: int) -> float:
    """第 failed_attempt 次尝试失败后的退避秒数：0.5×2**(k−1) → 0.5s, 1.0s。"""
    return _NVENC_RETRY_BACKOFF_BASE * (2 ** (failed_attempt - 1))


def nvenc_retry_run(attempt_fn, label: str):
    """重试队列本体：attempt_fn(attempt_no)→结果对象（须有 .returncode，0=成功；由
    调用方逐次独立占槽执行——本函数零占槽）。仅失败件重试（成功件零重试零副作用，
    首试成即返回）；耗尽→返回最终失败（fail-fast 语义由调用方既有链承接）。
    返回 (ok, attempts, last_result)。"""
    attempts = 0
    while True:
        attempts += 1
        r = attempt_fn(attempts)
        if getattr(r, "returncode", 1) == 0:
            return True, attempts, r
        if attempts > NVENC_RETRY_N:
            return False, attempts, r
        delay = nvenc_retry_backoff(attempts)
        print(f"↻ [NVENC 重试] {label} 第 {attempts} 次尝试失败，退避 {delay:.1f}s 后"
              f"原样重发（重试 {attempts}/{NVENC_RETRY_N}；仅此失败件，成功件不受牵连）")
        _GUARD_STATS["retry"] += 1
        retry_clock(delay)   # 退避等待在占槽域之外（占槽纪律：等待期不持槽）


def seg_encoder_kind(c) -> str:
    """「无 alpha seg 用哪个编码器」的单一判定（方案二阶段3）。

    composite.prepare_layers（实际编码）与 hygiene.gc/_current_keys、subs 预检、
    timeline_diff（键重算）共用——键集判定与实际编码器必须同源，否则 gc 会把活
    seg 误判成键集外垃圾回收、diff 影响面算错（这是 seg 键含编码器 token 后的
    防键集漂移关键）。VTX_SEG_NVENC=0 显式回退 x264（独立 A/B 开关，仅 "0" 生效，
    与 _pipeline_enabled 同款单值精确语义）；NVENC 探测失败显式告警后回落 x264
    （DEV.md 坑位教训：探测失败禁止静默回落——128px 假失败曾致草稿静默降级无人
    察觉）。dry 下 nvenc_ok 恒 True 不真探测（与 pick_vcodec 同语义）。
    """
    if os.environ.get("VTX_SEG_NVENC", "1") == "0":
        return "x264"
    # use_memo=True（P2-1）：进程级 memo 探测——prepare_layers/gc/subs/diff 各自调用
    # 本函数，memo 保证同进程内键集判定同值（详见 nvenc_ok 注释）。
    if nvenc_ok(c, use_memo=True):
        return "nvenc"
    print("⚠ NVENC 探测失败，seg 预编码回落 x264（显式告警，非静默）")
    return "x264"


@contextlib.contextmanager
def nvenc_budget(c, n: int = 1):
    """Reserve up to n slots; yield the actual frozen-capacity grant.

    Serialize acquisition (not work or release) so concurrent multi-slot batches
    cannot split the remaining tokens and wait forever for one another. Dry runs
    bypass allocation and VRAM probing entirely.
    """
    if type(n) is not int or n < 1:
        raise ValueError("NVENC slot request must be a positive integer")
    if getattr(c, "dry", False):
        yield n
        return
    sem, capacity = _nvenc_pool()
    granted = min(n, capacity)
    t0 = time.monotonic()
    got = 0
    try:
        with _NVENC_RESERVATION_LOCK:
            for _ in range(granted):
                while not sem.acquire(timeout=0.1):
                    pass
                got += 1
        waited = time.monotonic() - t0
        if waited > 0.1:
            print(f"⏳ NVENC 预算等待 {waited:.1f}s（申请 {n} 槽 / 实得 {granted} / 预算 {capacity}）")
        yield granted
    finally:
        for _ in range(got):
            sem.release()


def pick_vcodec(c, draft: bool):
    """输出编码参数选择（自 composite 提取，2026-09-10 阶段二；选项与旧版逐字一致）。"""
    if draft:
        if nvenc_ok(c):
            return (["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                     "-pix_fmt", "yuv420p"], "NVENC p4")
        return (["-c:v", "libx264", "-crf", "26", "-preset", "veryfast", "-pix_fmt", "yuv420p"], "x264 veryfast")
    if getattr(c, "gpu", False):
        if nvenc_ok(c):
            # GPU 正式渲染：p7 最高质量档 + tune hq + vbr-cq17（对 1080p 交付与
            # 下游 4K 重编码透明；voice-ime 2026-09-08 引入）
            return (["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "17",
                     "-b:v", "0", "-pix_fmt", "yuv420p"], "NVENC p7")
        print("⚠ NVENC 不可用，回落 x264 medium（官方参数）")
        return (["-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p"], "x264 medium(回落)")
    return (["-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p"], "x264 medium")
