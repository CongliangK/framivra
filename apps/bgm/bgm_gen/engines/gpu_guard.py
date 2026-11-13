"""GPU 串行保护（PLAN.md BG-D8）。

职责：
1. free_vram_mb()          —— nvidia-smi 探测空闲显存（MB；探测失败返回 None）。
2. wait_for_vram(...)      —— 文件锁（/tmp/bgm_gpu.lock，flock）+ 显存等待，返回上下文
   管理器；`with wait_for_vram(need_mb, timeout_s, on_timeout=...) as g:` 期间持锁
   （= 本包内 GPU 任务互斥），g 为结果 dict：
       {"device": "cuda"|"cpu", "degraded": bool, "reason": "",
        "need_mb": int, "free_mb_start": int|None, "free_mb_run": int|None,
        "waited_s": float, "lock_path": str}
   语义：
   - 先抢文件锁（排队等别的 bgm GPU 任务放锁），再轮询空闲显存 ≥ need_mb；
   - 超时策略 on_timeout：
       "cpu"    → 降级 device="cpu"（degraded=True）并立即放锁（CPU 任务不占 GPU，
                  不该阻塞队列）；
       "raise"  → 抛 EngineError；
       "proceed"→ 照常返回 device="cuda"（degraded=False, reason="vram_timeout"），
                  供 ai 引擎用（ComfyUI 自带排队，显存不足提交后会自己等）。
   - nvidia-smi 不可用（无 GPU 机器）→ device="cpu", reason="nvidia-smi 不可用"。

注意：ComfyUI 等外部进程不持本锁，显存探测是尽力而为（检查通过后仍可能被外部
进程挤占，由调用方自身容错——demucs CUDA OOM 时可 payload.device="cpu" 重试）。
"""

import fcntl
import os
import subprocess
import time

from .base import EngineError

LOCK_PATH = "/tmp/bgm_gpu.lock"

VALID_ON_TIMEOUT = ("cpu", "raise", "proceed")


def free_vram_mb(index=0):
    """空闲显存（MB）。nvidia-smi 失败/超时返回 None（视为无 GPU 可探测）。"""
    try:
        r = subprocess.run(
            ["nvidia-smi", "-i", str(index), "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return None
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return None


class VramGuard:
    """wait_for_vram 返回的上下文管理器（见模块 docstring）。"""

    def __init__(self, need_mb, timeout_s, on_timeout, poll_s=2.0,
                 lock_path=LOCK_PATH):
        self.need_mb = int(need_mb)
        self.timeout_s = float(timeout_s)
        self.on_timeout = on_timeout
        self.poll_s = float(poll_s)
        self.lock_path = lock_path
        self._fd = None
        self.record = None

    # ---- 锁 ----
    def _acquire_lock(self, deadline):
        import time as _t
        while _t.time() < deadline:
            if self._fd is None:
                self._fd = open(self.lock_path, "a+")
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                time.sleep(0.5)
        return False

    def _release_lock(self):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                self._fd.close()
                self._fd = None

    # ---- 上下文协议 ----
    def __enter__(self):
        deadline = time.time() + self.timeout_s
        rec = {"device": "cuda", "degraded": False, "reason": "",
               "need_mb": self.need_mb, "free_mb_start": None,
               "free_mb_run": None, "waited_s": 0.0, "lock_path": self.lock_path}
        t0 = time.time()

        free = free_vram_mb()
        rec["free_mb_start"] = free
        if free is None:
            # 无 nvidia-smi：直接 CPU（不持锁、不等待）
            rec.update(device="cpu", degraded=True, reason="nvidia-smi 不可用")
            self.record = rec
            return rec

        if not self._acquire_lock(deadline):
            rec["waited_s"] = round(time.time() - t0, 2)
            if self.on_timeout == "raise":
                raise EngineError(
                    f"GPU 文件锁 {self.lock_path} 等待超时（{self.timeout_s}s，"
                    f"另有 bgm GPU 任务持锁）")
            if self.on_timeout == "cpu":
                rec.update(device="cpu", degraded=True, reason="lock_timeout")
                return rec
            rec.update(reason="lock_timeout")  # proceed：不持锁继续
            self.record = rec
            return rec

        try:
            while True:
                free = free_vram_mb()
                if free is not None and free >= self.need_mb:
                    rec["free_mb_run"] = free
                    rec["waited_s"] = round(time.time() - t0, 2)
                    self.record = rec
                    return rec  # 持锁返回，任务体在锁内执行
                if time.time() >= deadline:
                    rec["waited_s"] = round(time.time() - t0, 2)
                    if self.on_timeout == "raise":
                        self._release_lock()
                        raise EngineError(
                            f"空闲显存不足：need={self.need_mb}MB，"
                            f"free={free}MB，等待 {self.timeout_s}s 超时")
                    if self.on_timeout == "cpu":
                        # 降级 CPU：立即放锁（CPU 任务不占 GPU，不该阻塞队列）
                        self._release_lock()
                        rec.update(device="cpu", degraded=True,
                                   reason="vram_timeout", free_mb_run=free)
                        return rec
                    rec.update(reason="vram_timeout", free_mb_run=free)
                    self.record = rec
                    return rec
                time.sleep(self.poll_s)
        except Exception:
            self._release_lock()
            raise

    def __exit__(self, exc_type, exc, tb):
        self._release_lock()
        return False


def wait_for_vram(need_mb=4096, timeout_s=120, on_timeout="cpu",
                  poll_s=2.0, lock_path=LOCK_PATH):
    """见模块 docstring。默认阈值 4GB（PLAN §5 R2 W2B 任务约定）。"""
    if on_timeout not in VALID_ON_TIMEOUT:
        raise EngineError(f"on_timeout 非法：{on_timeout!r}（允许 {VALID_ON_TIMEOUT}）")
    return VramGuard(need_mb=need_mb, timeout_s=timeout_s, on_timeout=on_timeout,
                     poll_s=poll_s, lock_path=lock_path)
