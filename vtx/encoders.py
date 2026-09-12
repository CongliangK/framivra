"""vtx 编码器/渲染器选型纯决策函数：Chrome 定位、NVENC 能力探测、输出编码参数选择。

无副作用（除探测子进程本身）；全部行为与拆分前逐字一致。
"""
import contextlib, os, shutil, sys, threading, time
from concurrent.futures import ThreadPoolExecutor


def find_chrome() -> str:
    """跨平台定位 Chrome/Chromium：环境变量 VTX_CHROME 优先，其次 PATH 常见名，再退标准安装路径。"""
    cands = [os.environ.get("VTX_CHROME")] if os.environ.get("VTX_CHROME") else []
    cands += ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
              "chrome", "msedge"]  # msedge 同为 Chromium 内核，headless 截图兼容
    w = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    lapp = os.environ.get("LOCALAPPDATA", "")
    if sys.platform == "win32":
        cands += [rf"{w}\Google\Chrome\Application\chrome.exe",
                  rf"{pf86}\Google\Chrome\Application\chrome.exe",
                  rf"{lapp}\Google\Chrome\Application\chrome.exe",
                  rf"{w}\Microsoft\Edge\Application\msedge.exe",
                  rf"{pf86}\Microsoft\Edge\Application\msedge.exe"]  # 系统自带 Edge 默认装 (x86) 目录（只装 Edge 的出厂机 gfx 链不断）
    elif sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
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
        if not all(ex.map(_p, range(workers))):
            print("⚠ NVENC 并发会话探测失败，回落串行渲染")
            return 1
    return workers


# ---------------------------------------------------------------- NVENC 会话预算（方案二阶段3，2026-09-12）
# 管的是【本进程内】全部 NVENC 编码点：① seg 预编码（composite.prepare_layers 无
# alpha 分支的 NVENC 任务，经 infra.run_ffmpeg_task 逐任务占槽）；② 段渲染
# （composite.render_segmented 的 NVENC 段，批次开始一次性占 min(workers, 预算) 个
# 槽、结束释放）。4K 导出（scripts/check_4k.sh）是独立 ffmpeg 进程，不经过本信号量
# ——跨进程协调靠用户把 VTX_NVENC_BUDGET 按「预算 − 本进程并发」下调。
# 预算默认 8：NVENC 并发拐点实测 8（超过后聚合 fps 无收益，反触发驱动会话回落，
# 见方案二/DEV.md）。探测（nvenc_concurrency）与预算分工：探测管「NVENC 会话真
# 可用」（驱动层面并发上限，探测失败回落串行）；预算管「本进程内不超发」（防
# seg 预编码后台任务与段渲染同进程叠加——当前编排时间上不重叠，机制上封死）。
_NVENC_SEM = None
_NVENC_SEM_LOCK = threading.Lock()
_NVENC_OK_MEMO = None   # nvenc_ok(use_memo=True) 的进程级探测结果缓存（P2-1，见函数注释）


def nvenc_budget_value() -> int:
    v = os.environ.get("VTX_NVENC_BUDGET", "")
    return int(v) if v.isdigit() and int(v) > 0 else 8


def _nvenc_sem() -> threading.Semaphore:
    global _NVENC_SEM
    with _NVENC_SEM_LOCK:
        if _NVENC_SEM is None:
            _NVENC_SEM = threading.Semaphore(nvenc_budget_value())
        return _NVENC_SEM


def reset_nvenc_budget():
    """仅测试用：重置惰性信号量（改 VTX_NVENC_BUDGET 后须重置才生效——信号量
    预算在首次使用时定值，进程内不改）。"""
    global _NVENC_SEM
    with _NVENC_SEM_LOCK:
        _NVENC_SEM = None


def reset_nvenc_ok_memo():
    """仅测试用：清空 nvenc_ok(use_memo=True) 的进程级探测缓存。"""
    global _NVENC_OK_MEMO
    _NVENC_OK_MEMO = None


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
    """占用 n 个 NVENC 会话槽（默认 1），退出释放。阻塞等待超过 0.1s 才打一行
    「⏳ NVENC 预算等待」（acquire 前后计时，非每帧打——避免日志噪音）。dry-run
    下任务只打印不执行，信号量直通（不占槽）。"""
    if getattr(c, "dry", False):
        yield
        return
    sem = _nvenc_sem()
    t0 = time.monotonic()
    got = 0
    try:
        for _ in range(n):
            while not sem.acquire(timeout=0.1):
                pass          # 0.1s 轮询：只为统计等待时长决定是否打日志
            got += 1
        waited = time.monotonic() - t0
        if waited > 0.1:
            print(f"⏳ NVENC 预算等待 {waited:.1f}s（申请 {n} 槽 / 预算 {nvenc_budget_value()}）")
        yield
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
