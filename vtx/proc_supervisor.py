#!/usr/bin/env python3
"""proc_supervisor.py — 外置多进程树卫生监管器（GFX 离屏渲染双路线战役 Phase 0，
issue #103；供 CEF 路线A / Electron 路线B Worker 的高压 kill -9 测试消费）。

Chromium/CEF/Electron 会派生 Browser、GPU、Zygote、Renderer 多级子进程。本模块
保证两条硬不变量（工单 DoD）：
  ① 宿主（Python 侧 vtx 进程）被 SIGKILL 强杀时，Worker 进程树与临时 Profile
     100% 归零；
  ② 实验渲染产物与正式 cache/gfx/<key> 物理隔离（隔离路由在 hygiene.gfx_dir 的
     VTX_GFX_CACHE_NS，本模块只管进程与临时目录卫生）。

三重防线（层层独立、任一生效即归零）：
  A. PDEATHSIG：Worker 以 start_new_session=True 启动（独立会话/进程组长，
     pgid == worker.pid），preexec_fn 在 exec 前挂 prctl(PR_SET_PDEATHSIG,
     SIGKILL)——宿主一死内核即刻击杀 Worker 主进程（fork→prctl 间宿主先死的
     竞态由 preexec 内 getppid 复读兜底自杀）。
  B. 父存活管道 + 守卫进程：宿主持匿名管道写端；一个**不经 PDEATHSIG、自成
     会话**的守卫子进程阻塞 read 读端。宿主暴毙 → 内核关闭写端 → 守卫 EOF →
     对 Worker 进程组 killpg(SIGKILL) 后退出。这杀的是**整个组**——真实
     Chromium 子进程并不挂 PDEATHSIG（防线A只杀得主进程），守卫是唯一对
     「任意非协作 Worker」也成立的全树归零机制；守卫不持写端、宿主正常收尾
     关写端时组已被优雅停毕，killpg 落空无害。
  C. 账本 + reap_orphan_workers(c)：每个监管组在 tmp_run 目录写
     .worker_pgid.json（pgid/boot_id/start_time_ticks/host_pid/...）。
     宿主被 -9 后守卫若也失效（极端），下一次 gfx 阶段启动 / 手动
     `python3 -m vtx.proc_supervisor reap <timeline.json>` 扫描 cache/tmp/
     账本：核对 /proc/<pid>/stat 第 22 字段 starttime（防 PID 回绕误杀）与
     boot_id 后 killpg，等进程表清空再安全移除临时 Profile 目录。

Worker 侧协作契约（CEF/Electron 路线实现方须遵守，守卫使其成为「锦上添花」
而非「必需」）：宿主在 argv 尾部追加 --parent-alive-fd=<fd>（读端 fd 原号透传）。
Worker 起一个后台线程阻塞 read(fd)；EOF 后 50ms 内向自身进程组
killpg(0, SIGKILL)（线程在 read 上阻塞、EOF 后第一件事就是 killpg，勿夹杂
Python 忙活）；并建议对自派生的子进程同样挂 PDEATHSIG。

红线：进程状态绝不写 manifest.json（会破坏 hygiene._load_manifest 与
tests/test_hygiene_ledger_txn.py）——账本是 tmp_run 目录内的独立文件，
随目录生命周期消亡。

运行目录纪律：工作目录与浏览器 --user-data-dir / root_cache_path 统一分配在
hygiene.tmp_run(c, "gfx_<backend_id>") 创建的 cache/tmp/<UTC>-gfx_<backend_id>/
下（unique=True 追加 -<pid>-<序号> 后缀，并行监管组不互撞）——杜绝写系统
/tmp tmpfs 占内存。平台能力：完整监管依赖 /proc（账本的 starttime/boot_id 与
组成员扫描）；supervision_available()=False 的平台（非 Linux、无 /proc），
SupervisedProcessGroup 不做「假装降级」——账本拒绝编造（_write_ledger 恒
RuntimeError，宁可不起不可无账本裸奔），真实降级发生在接入点
gfx_render.Browser.start：检测监管不可用即回落 #103 之前的无监管路径
（裸 Popen + tempfile.mkdtemp，行为=main 基线）并一次性 stderr 告警。
进程演练测试按平台跳过。

纯标准库（ctypes 仅用于 prctl）。独立测试：python3 -m unittest
tests.test_proc_supervisor -v（真实进程树演练，只杀本测试自起子进程）。
"""
import ctypes
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

if __package__:   # 包内相对导入（vtx/__init__ §1.4）
    from . import fstxn, hygiene
else:             # flat 直跑（python3 vtx/proc_supervisor.py）仅留最小依赖面
    import fstxn
    import hygiene

__all__ = ["SupervisedProcessGroup", "reap_orphan_workers", "reap_run_dir",
           "kill_group", "group_live_members", "proc_starttime", "boot_id",
           "supervision_available", "WORKER_LEDGER", "main"]

# 伴生 PID 账本文件名（hygiene._sweep_tmp_worker_ledger 经本常量惰性取用，
# 名字是两模块间的共享契约，改动须两侧同审）
WORKER_LEDGER = ".worker_pgid.json"

_REPO_ROOT = Path(__file__).resolve().parents[1]

# backend_id 进入 tmp_run 目录名（<UTC>-gfx_<backend_id>-<pid>-<n>），收紧字符集
_BACKEND_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

# Linux prctl 常量（<linux/prctl.h>）；非 Linux 无 PDEATHSIG，preexec 整体不挂
_PR_SET_PDEATHSIG = 1
try:
    _LIBC = ctypes.CDLL("libc.so.6", use_errno=True) \
        if sys.platform.startswith("linux") else None
except OSError:   # pragma: no cover - musl/无 libc 场景：降级为无 PDEATHSIG
    _LIBC = None

# O2（红队）：prctl 函数指针在 import 期（单线程）预解析。若惰性解析，首次
# `_LIBC.prctl` 属性求值（CDLL.__getattr__ → dlsym）会发生在 fork 出的子进程
# 内（preexec 首调）——宿主 VTX_GFX_PAGES 默认 7 线程池，fork 瞬间其他线程
# 若持动态加载器锁，子内 dlsym 即死锁。预绑定后 _preexec 只剩已绑定指针的
# int 参数调用（libffi cif 随绑定预生成，无分配）+ getppid/kill 两个 syscall
# 包装，不触碰任何懒加载/分配路径。
# 方案取舍（为何保持 preexec_fn 而非 exec 包装器/守卫侧挂载）：prctl 只能对
# 自身进程设置（无 Popen 等价参数），守卫无法替 Worker 挂 PDEATHSIG；exec
# 包装器要彻底去掉 preexec_fn 须给每个 Worker 多付一次解释器启动并把 argv
# 契约整体重排进包装脚本——相比已收窄到「纯 syscall 的 preexec」收益为负。
try:
    _PRCTL = _LIBC.prctl if _LIBC is not None else None
except AttributeError:   # pragma: no cover - libc 未导出 prctl 的病态环境
    _PRCTL = None

_HAS_PROC = Path("/proc/self/stat").is_file()


def supervision_available() -> bool:
    """平台是否具备完整监管能力（issue #103 O1）：监管的击杀核验（账本
    starttime/boot_id、组成员扫描）全部依赖 /proc，缺失即监管不可用。
    接入点（gfx_render.Browser.start）据此分流——False 时回落 #103 之前的
    无监管路径并一次性 stderr 告警，而非等 _write_ledger 抛 RuntimeError
    把 gfx 阶段打死。测试可 monkeypatch 模块级 _HAS_PROC 模拟平台探测。"""
    return _HAS_PROC


# ---------------------------------------------------------------- /proc 只读探针

def _proc_stat_tail(pid) -> "list[str] | None":
    """/proc/<pid>/stat 的 comm 字段可含 ')' 与空格——在**最后一个** ')' 之后
    切分才是字段区（否则 Chrome 风格 "(chrome --headl)" 类进程名会错位）。
    返回 state 起始的字段表（tail[0]=state(3)、tail[1]=ppid(4)、tail[2]=pgrp(5)、
    …、tail[19]=starttime(22)），进程消失/不可读 → None。"""
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="ascii",
                                                       errors="replace")
    except (OSError, ValueError):
        return None
    tail = raw.rpartition(")")[2].split()
    return tail or None


def proc_starttime(pid) -> "int | None":
    """/proc/<pid>/stat 第 22 字段 starttime（内核时钟 tick）——PID 回绕后同名
    pid 的 starttime 必变，账本击杀前的身份核对依据（issue #103 工单口径）。"""
    tail = _proc_stat_tail(pid)
    if tail is None or len(tail) < 20:
        return None
    try:
        return int(tail[19])
    except ValueError:
        return None


def group_live_members(pgid) -> list:
    """进程组 pgid 的存活成员 pid 表（扫 /proc pgrp 字段；僵尸 Z 不计——僵尸
    不持文件锁/不占调度，由 init 收尸）。非 Linux/无 /proc → 恒空表。"""
    if not _HAS_PROC:
        return []
    try:
        names = os.listdir("/proc")
    except OSError:   # pragma: no cover - /proc 消失的病态环境
        return []
    out = []
    for name in names:
        if not name.isdigit():
            continue
        tail = _proc_stat_tail(name)
        if tail is None or len(tail) < 3 or tail[0] == "Z":
            continue
        try:
            if int(tail[2]) == pgid:
                out.append(int(name))
        except (IndexError, ValueError):
            continue
    return sorted(out)


def boot_id():
    """/proc/sys/kernel/random/boot_id：账本跨启动周期的作废判据（旧开机的
    pgid 在本开机毫无意义，只清目录绝不动进程）。不可得 → None。"""
    try:
        text = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii")
        return text.strip() or None
    except OSError:
        return None


def wait_group_empty(pgid, timeout: float = 10.0) -> bool:
    """轮询直到进程组无存活成员（僵尸不计）；超时返回当下是否已空。"""
    deadline = time.monotonic() + timeout
    while True:
        if not group_live_members(pgid):
            return True
        if time.monotonic() >= deadline:
            return not group_live_members(pgid)
        time.sleep(0.02)


def kill_group(pgid, *, timeout: float = 10.0) -> bool:
    """身份核验后的组击杀：killpg(SIGKILL) + 等进程表清空。组已消亡
    （ESRCH）视作成功。Linux 语义：unlink 不受打开文件影响，等待只为满足
    「先清进程再移除目录」的账目顺序与日志确定性。"""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    if wait_group_empty(pgid, timeout):
        return True
    print(f"⚠ 监管器：进程组 {pgid} 击杀后 {timeout}s 仍有残活成员 "
          f"{group_live_members(pgid)}（目录移除照常，残员交由账本下轮清扫）")
    return False


# ---------------------------------------------------------------- PDEATHSIG preexec

def _make_pdeathsig_preexec(expected_ppid: int):
    """构造 Popen(preexec_fn=)（fork 子进程内执行，晚于 setsid、早于 exec）：
    ① prctl(PR_SET_PDEATHSIG, SIGKILL)——宿主（确切说：fork 本 Worker 的宿主
    线程）死亡时内核补发 SIGKILL；② fork→prctl 间宿主可能已死（该窗口内死亡
    内核不会补发信号），prctl 后复读 getppid 兜底自杀，关死该竞态窗。
    prctl 指针已在模块顶层预解析为 _PRCTL（见 O2 注）——fork 后子进程内
    不走 CDLL 惰性属性查找/dlsym，防多线程 fork 死锁。"""
    def _preexec():   # pragma: no cover - fork 子进程内执行，覆盖工具看不见
        if _PRCTL(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0) != 0:
            os._exit(126)
        if os.getppid() != expected_ppid:
            os.kill(os.getpid(), signal.SIGKILL)
    return _preexec


# ---------------------------------------------------------------- 守卫进程

def _guard_main(pgid: int, alive_fd: int):
    """守卫子进程主循环（python3 -m vtx.proc_supervisor guard <pgid> <fd>）：
    阻塞读父存活管道 → EOF（宿主死亡/收尾关写端）→ 立即 killpg(SIGKILL)。
    守卫不经 PDEATHSIG、自成会话（不在 Worker 组内，killpg 打不到自己）。
    关键路径刻意只做一次 killpg syscall（不预扫 /proc）：EOF 距宿主死亡仅
    毫秒级，PID 复用撞进同一 pgid 实际不可达（新会话组长必得新 pgid），
    组已消亡时 killpg 得 ESRCH 吞掉即可——毫秒级预算不留给进程表扫描；
    对 PID 复用的严谨防御由 reap 账本路径的 starttime 核对承担。任何异常
    路径都安静退出——守卫身后还有账本+reap。"""
    try:
        while os.read(alive_fd, 4096):   # 宿主不写数据；EOF(b"") 是唯一结局
            pass
    except OSError:
        pass
    try:
        os.close(alive_fd)
    except OSError:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    os._exit(0)   # 不走 sys.exit：绕过解释器收尾（atexit/flush）直退


# ---------------------------------------------------------------- 监管组

class SupervisedProcessGroup:
    """外置 Worker 进程组监管上下文（issue #103 工单项 1）。

    with SupervisedProcessGroup(c, argv, backend_id="cef_a0") as sg:
        sg.proc / sg.pgid / sg.run_dir / sg.profile_dir ...

    - argv：list[str] 原样使用，或 callable(run_dir, profile_dir) -> list[str]
      （Browser 这类 --user-data-dir 依赖运行目录的调用方用 callable 形态）。
      supervisor 统一在尾部追加 --parent-alive-fd=<fd>（协作契约，见模块头）。
    - start_new_session=True：Worker 独立会话/进程组长（pgid == worker.pid），
      与宿主终端信号彻底解耦，killpg 全树命中。
    - profile_dir = run_dir/"profile" 预建（浏览器 --user-data-dir / root_cache_path
      统一落此处）；cwd = run_dir。
    - 正常退出（with 出界/stop()）：SIGTERM 主进程 → 宽限 → 组残员 killpg →
      移除账本 → 关管道（守卫自然退出）→ tmp_run 收尾 rmtree 运行目录。
    - 宿主被 SIGKILL：stop 不会运行——账本留在 run_dir，交 reap_orphan_workers。
    """

    def __init__(self, c, argv, *, backend_id: str, kill_grace: float = 5.0,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=None):
        if not isinstance(backend_id, str) or not _BACKEND_ID_RE.fullmatch(backend_id):
            raise ValueError(f"backend_id 非法（{backend_id!r}）：须为字母数字与 _.- "
                             "的组合且不以 . 开头（它进入 tmp_run 目录名）")
        self.c = c
        self.argv = argv
        self.backend_id = backend_id
        self.kill_grace = kill_grace
        self._stdout, self._stderr, self._env = stdout, stderr, env
        self.proc = None
        self.guard_proc = None
        self.pgid = None
        self.run_dir = None
        self.profile_dir = None
        self._tmp = None
        self._pipe_w = None
        self._stopped = True

    # 生命周期 ------------------------------------------------------------
    def start(self):
        if not self._stopped:
            return self
        # tmp_run(unique)：cache/tmp/<UTC>-gfx_<backend_id>-<pid>-<n>/——并行
        # 监管组（VTX_GFX_PAGES 多浏览器/跨进程同项目）目录互不冲突
        self._tmp = hygiene.tmp_run(self.c, f"gfx_{self.backend_id}", unique=True)
        self.run_dir = self._tmp.path
        self.profile_dir = self.run_dir / "profile"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        r_fd = None
        try:
            r_fd, self._pipe_w = os.pipe()
            argv = self.argv(self.run_dir, self.profile_dir) \
                if callable(self.argv) else [str(a) for a in self.argv]
            argv = [*argv, f"--parent-alive-fd={r_fd}"]
            preexec = _make_pdeathsig_preexec(os.getpid()) if _PRCTL is not None else None
            self.proc = subprocess.Popen(
                argv, cwd=str(self.run_dir), start_new_session=True,
                preexec_fn=preexec, pass_fds=(r_fd,),
                stdout=self._stdout, stderr=self._stderr, env=self._env)
            self.pgid = self.proc.pid   # 会话组长：pgid 恒等于主进程 pid
            self._write_ledger()
            self.guard_proc = subprocess.Popen(
                [sys.executable, "-m", "vtx.proc_supervisor",
                 "guard", str(self.pgid), str(r_fd)],
                cwd=str(_REPO_ROOT), start_new_session=True, pass_fds=(r_fd,),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={**os.environ, "PYTHONPATH": os.pathsep.join(
                    p for p in (str(_REPO_ROOT),
                                os.environ.get("PYTHONPATH", "")) if p)})
            os.close(r_fd)   # 宿主侧读端到此为止：EOF 只由宿主写端关闭触发
            r_fd = None
            self._stopped = False
            return self
        except BaseException:
            if r_fd is not None:
                try:
                    os.close(r_fd)
                except OSError:
                    pass
            self.stop()   # 半启动回收：杀 Worker/删账本/收守卫/删运行目录
            raise

    def stop(self):
        """正常收尾（幂等）：优雅停组 → 删账本 → 关管道收守卫 → tmp_run 收尾。
        半启动（start 中途异常）同样安全：各槽位 None 自守。"""
        if self._stopped and self._tmp is None:
            return
        self._stopped = True
        try:
            self._terminate_worker()
            self._unlink_ledger()
            self._close_pipe_and_reap_guard()
        finally:
            if self._tmp is not None:
                tmp, self._tmp = self._tmp, None
                tmp.__exit__(None, None, None)   # 账本已删→清扫 no-op + rmtree

    def __enter__(self):
        return self.start()

    def __exit__(self, et, ev, tb):
        self.stop()
        return False

    # 内部步骤 ------------------------------------------------------------
    def _terminate_worker(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()   # SIGTERM 主进程：Chrome 优雅拆子进程
                self.proc.wait(self.kill_grace)
            except (subprocess.TimeoutExpired, OSError):
                pass
        if self.pgid is not None and group_live_members(self.pgid):
            kill_group(self.pgid)   # 残员兜底（僵尸不计）

    def _ledger_path(self) -> Path:
        return self.run_dir / WORKER_LEDGER

    def _unlink_ledger(self):
        # 账本先于关管道删除：守卫 EOF 后扫描组已空、账本已除，安静退出；
        # tmp_run 收尾的伴生账本清扫也因此恒为 no-op（正 常 路 径 不 依 赖 它）。
        try:
            self._ledger_path().unlink(missing_ok=True)
        except OSError as e:
            print(f"⚠ 监管器：账本删除失败（{e}）——reap 下轮会按陈旧账本兜底")

    def _close_pipe_and_reap_guard(self):
        if self._pipe_w is not None:
            try:
                os.close(self._pipe_w)
            finally:
                self._pipe_w = None
        guard, self.guard_proc = self.guard_proc, None
        if guard is not None:
            try:
                guard.wait(3)
            except subprocess.TimeoutExpired:
                guard.kill()
                try:
                    guard.wait(3)
                except subprocess.TimeoutExpired:   # pragma: no cover
                    print(f"⚠ 监管器：守卫 pid={guard.pid} 强杀后仍未退出")

    def _write_ledger(self):
        """>>> 红线：账本是 tmp_run 目录内的独立文件，绝不入 manifest.json。"""
        ticks = proc_starttime(self.pgid)
        host_ticks = proc_starttime(os.getpid())
        boot = boot_id()
        if ticks is None or host_ticks is None or boot is None:
            raise RuntimeError(
                "无法读取 /proc starttime/boot_id（非 Linux 或 /proc 不可用）："
                "监管账本拒绝编造，Worker 启动失败（issue #103）")
        rec = {"schema": 1,
               "backend_id": self.backend_id,
               "pgid": self.pgid,
               "boot_id": boot,
               "start_time_ticks": ticks,          # /proc/<pgid>/stat 第 22 字段
               "host_pid": os.getpid(),
               "host_start_time_ticks": host_ticks,
               "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        fstxn.atomic_write(self._ledger_path(),
                           json.dumps(rec, ensure_ascii=False, indent=2,
                                      sort_keys=True))


# ---------------------------------------------------------------- 账本清扫 / reap

# reap_run_dir 状态词（调用方据此决定目录去留）：
#   no_ledger      无账本（正常收尾后的空目录）——不动进程
#   live_host      宿主仍存活（host_pid+starttime 吻合）——活动组，跳过一切
#   killed         已 killpg 并等进程表清空
#   already_dead   组已无存活成员（守卫/PDEATHSIG 已收走）
#   pid_reuse      组长 pid 已被无关进程复用（starttime 不符）——只清目录不杀
#   stale_boot     账本属旧开机（boot_id 不符）——只清目录不杀
#   broken_ledger  账本不可解析/形状不符——只清目录不杀
_LIVE_STATUSES = ("killed", "already_dead")


def reap_run_dir(run_dir, *, ledger_path=None) -> str:
    """单个 tmp_run 目录的账本清扫（不删目录，删留归调用方）：
    校验 boot_id → 宿主存活（跳过）→ 组长 starttime 吻合（防 PID 回绕误杀）
    → killpg + 等进程表清空。组长已消亡但组内仍有孤儿子进程时同样击杀——
    无关进程无法自然加入死组长的进程组，killpg 只会命中本 Worker 树残员。"""
    run_dir = Path(run_dir)
    ledger = Path(ledger_path) if ledger_path else run_dir / WORKER_LEDGER
    if not ledger.is_file():
        return "no_ledger"
    try:
        rec = json.loads(ledger.read_text(encoding="utf-8"))
        pgid = rec["pgid"]
        ticks = rec["start_time_ticks"]
        host_pid = rec["host_pid"]
        host_ticks = rec["host_start_time_ticks"]
        if not all(isinstance(v, int) for v in (pgid, ticks, host_pid, host_ticks)):
            raise KeyError("字段类型不符")
        if pgid < 1:
            # R103-1（红队）：pgid=0 使 proc_starttime(0)=None 绕过 starttime
            # 核验、group_live_members(0) 把内核线程组（pgrp=0）误判为组内
            # 残活 → killpg(0) 反杀调用方自己的进程组；负值同理。合法进程组
            # 的 pgid 恒 ≥ 1（会话组长 pid），一律按损坏账本处理（只清目录）。
            raise KeyError("pgid 非法（<1）")
    except (OSError, ValueError, KeyError, TypeError):
        return "broken_ledger"
    current_boot = boot_id()
    if current_boot and rec.get("boot_id") not in (None, current_boot):
        return "stale_boot"
    if proc_starttime(host_pid) == host_ticks:
        return "live_host"   # 并行 gfx 进程的活动组：绝不误杀（issue #103 红线）
    leader = proc_starttime(pgid)
    if leader is not None and leader != ticks:
        return "pid_reuse"   # pid 已归无关进程：宁可放过，绝不误杀
    if leader is not None or group_live_members(pgid):
        kill_group(pgid)
        return "killed"
    return "already_dead"


def _remove_run_dir(c, run_dir: Path):
    """进程表已清空后的目录移除：走 gc 同款安全门（cleanup_allowed +
    fstxn.remove_pinned 身份钉扎，锁文件保留）。任何拒绝只告警不硬删。"""
    if not hygiene.cleanup_allowed(c, run_dir):
        print(f"⚠ 监管器：目录移除被清理安全门拒绝（保留现场）: {run_dir}")
        return False
    identity = fstxn.cleanup_identity(run_dir)
    if identity is None:
        return True   # 已被并发清扫者移除（幂等成功）
    try:
        fstxn.remove_pinned(run_dir, identity,
                            preserve=lambda p: p.name.endswith(".lock"))
        return True
    except ValueError as e:   # ProjectPathError：路径身份变化，绝不盲删
        print(f"⚠ 监管器：目录移除失败（保留现场）: {e}")
        return False


def reap_orphan_workers(c) -> dict:
    """扫描 cache/tmp/ 下残留 .worker_pgid.json（工单项 2）：核对 starttime
    一致后 killpg，等进程表清空再安全移除临时 Profile 目录。宿主仍存活的
    活动组跳过（并行 vtx 进程互不误杀）。返回统计 dict；空项目零写态。"""
    stats = {"ledgers": 0, "killed": 0, "live_host": 0, "stale": 0, "removed": 0}
    tmp_root = Path(c.cache) / hygiene.TMP
    if not tmp_root.is_dir():
        return stats
    for run_dir in sorted(tmp_root.iterdir()):
        if not run_dir.is_dir() or not (run_dir / WORKER_LEDGER).is_file():
            continue
        stats["ledgers"] += 1
        status = reap_run_dir(run_dir)
        if status == "live_host":
            stats["live_host"] += 1
            continue
        if status == "killed":
            stats["killed"] += 1
        elif status not in _LIVE_STATUSES:
            stats["stale"] += 1
        if _remove_run_dir(c, run_dir):
            stats["removed"] += 1
    return stats


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["guard"]:
        # 内部守卫子命令：python3 -m vtx.proc_supervisor guard <pgid> <fd>
        if len(argv) != 3:
            print("usage: guard <pgid> <alive_fd>", file=sys.stderr)
            return 2
        _guard_main(int(argv[1]), int(argv[2]))
        return 0   # os._exit 直退，此处不可达
    if argv[:1] == ["reap"]:
        # 运维/高压测试入口：清扫项目孤儿 Worker（读 timeline.json 锚定 cache/）
        if len(argv) != 2:
            print("usage: reap <timeline.json>", file=sys.stderr)
            return 2
        from .infra import Ctx
        stats = reap_orphan_workers(Ctx(Path(argv[1])))
        print(f"✓ 孤儿 Worker 清扫：账本 {stats['ledgers']} 份，击杀 {stats['killed']} 组，"
              f"存活宿主跳过 {stats['live_host']}，陈旧 {stats['stale']}，"
              f"移除目录 {stats['removed']}")
        return 0
    print("vtx.proc_supervisor：子命令 = guard <pgid> <fd>（内部）| "
          "reap <timeline.json>（孤儿 Worker 清扫）")
    return 2


if __name__ == "__main__":
    sys.exit(main())
