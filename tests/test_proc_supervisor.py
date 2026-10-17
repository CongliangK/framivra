"""issue #103：外置多进程树卫生监管器（vtx/proc_supervisor.py）与沙箱缓存隔离。

两类夹具，全部只杀本测试自起子进程树（sleep/锁文件链），测试结束零残留：

  1. 真实进程演练（Linux /proc）：独立 Python 宿主进程经 SupervisedProcessGroup
     拉起「模拟 Chromium」多进程树（主进程派生 3 个子进程并 flock 住
     user-data-dir 文件）；对宿主 os.kill(SIGKILL) 后 100ms 断言进程表残活成员
     == 0（DoD），再 reap_orphan_workers 断言临时目录残留 == 0（DoD）。
     plain 模式 = 非协作 Worker（子进程无 PDEATHSIG、无视 --parent-alive-fd，
     与真实 Chrome 同形——归零只能靠监管器防线 A+B）；coop 模式 = 协作 Worker
     （实现模块头契约：EOF 后 killpg(0)）。
  2. 沙箱缓存隔离（离线）：VTX_GFX_CACHE_NS 路由 cache/gfx_exp/<ns>/<key>/、
     gfx_key 一字不动、跨 ns 不命中彼此缓存（经 cli.gfx 端到端）、complete.json
     可选 backend_ns 元数据（正式态缺省字段=既有字节形状）。
  3. R3 谱系白名单（kill_group 最内层硬防护）三态演练：①伪造账本
     pgid=调用方自身组 → 拒杀+清目录+零击杀+调用方存活；②伪造账本
     pgid=真实祖先（mid→leaf 子进程树的中间祖先）组 → 拒杀；③自己的
     子进程组不受影响仍可杀。一切被测 kill 路径都在 start_new_session=True
     的隔离子进程内执行（09:22 事故铁律：子进程自杀也不许波及本测试）。

独立运行：python3 -m unittest tests.test_proc_supervisor -v
"""
import contextlib
import io
import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

from vtx import cli, gfx_cli, gfx_render, hygiene, proc_supervisor
from vtx.infra import Ctx
from vtx.proc_supervisor import (SupervisedProcessGroup, WORKER_LEDGER,
                                 boot_id, group_live_members, proc_starttime,
                                 reap_orphan_workers)

ROOT = Path(__file__).resolve().parents[1]
_LINUX_PROC = Path("/proc/self/stat").is_file()


def chunk(tag, data):
    return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))


def png(w=6, h=4, value=0, filter_byte=0):
    return (gfx_cli.PNG_SIGNATURE + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((bytes([filter_byte]) + bytes([value]) * (w * 4)) * h))
            + chunk(b'IEND', b''))


def stats(n):
    return dict(failed=[], rendered=n, ms_total=1, tabs=1, ms_per_frame=1,
                path_counts={}, timings_ms={}, static_spans=0)


def _wait_until(pred, timeout=15.0, interval=0.02):
    """轮询直到 pred() 真值（返回该值）；超时返回最后一次取值（调用方断言）。"""
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value:
            return value
        if time.monotonic() >= deadline:
            return pred()
        time.sleep(interval)


# 模拟 Chromium 多进程树：main = Worker 主进程（派生 3 个子进程）；child = 子进程
# （flock 锁 user-data-dir 内文件后常驻）。plain：与真实 Chrome 同形——子进程不挂
# PDEATHSIG、主进程无视 --parent-alive-fd；coop：实现 issue #103 协作契约。
SIM_PROGRAM = r'''
import fcntl, os, signal, subprocess, sys, time

ROLE = sys.argv[1]

if ROLE == "child":
    mode, lock_path = sys.argv[2], sys.argv[3]
    if mode == "coop":
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0)
    with open(lock_path, "a+b") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        with open(lock_path + ".pid", "a") as pf:
            pf.write(f"{os.getpid()}\\n")
    while True:
        time.sleep(0.5)
    raise SystemExit(0)

mode, profile = sys.argv[2], sys.argv[3]
alive_fd = None
for a in sys.argv[4:]:
    if a.startswith("--parent-alive-fd="):
        alive_fd = int(a.partition("=")[2])


def _pdeathsig():
    import ctypes
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0)


for k in range(3):
    subprocess.Popen([sys.executable, __file__, "child", mode,
                      os.path.join(profile, "lock_%d" % k)],
                     preexec_fn=_pdeathsig if mode == "coop" else None)

if mode == "coop" and alive_fd is not None:
    import threading

    def _watch():
        try:
            while os.read(alive_fd, 4096):
                pass
        except OSError:
            pass
        os.killpg(0, signal.SIGKILL)

    threading.Thread(target=_watch, daemon=True).start()

while True:
    time.sleep(0.5)
'''

# 独立 Python 宿主：建 Ctx → 进 SupervisedProcessGroup → 写就绪文件（run_dir/
# pgid/host_pid）→ 常驻。被测试 os.kill(SIGKILL) 即为 DoD 的「宿主暴毙」。
HOST_PROGRAM = r'''
import json, os, sys, time
from pathlib import Path
from vtx.infra import Ctx
from vtx.proc_supervisor import SupervisedProcessGroup

root, ready_path, mode, sim = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
c = Ctx(root / "timeline.json")


def worker_argv(run_dir, profile):
    return [sys.executable, str(sim), "main", mode, str(profile)]


with SupervisedProcessGroup(c, worker_argv, backend_id="sim") as sg:
    ready_path.write_text(json.dumps({
        "run_dir": str(sg.run_dir), "pgid": sg.pgid, "host_pid": os.getpid(),
        "worker_pid": sg.proc.pid}), encoding="utf-8")
    time.sleep(120)
'''


class _ProjectFixture(unittest.TestCase):
    """隔离临时项目：timeline + Ctx + 模拟 Worker 脚本；回收全部自起进程。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='vtx_issue103_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'page.html').write_text('<html><body>sim</body></html>')
        self.timeline = self.root / 'timeline.json'
        self.timeline.write_text(json.dumps(dict(
            project=dict(fps=30, width=6, height=4, duration_frames=3), sources=[],
            subtitles=[], audio_tracks=[], video_tracks=[dict(kind='gfx', clips=[
                dict(src='page.html', dst_in=0, dur=3)])])))
        self.sim = self.root / 'sim_chromium.py'
        self.sim.write_text(SIM_PROGRAM, encoding='utf-8')
        self.hosts = []
        self.addCleanup(self._kill_hosts_and_reap)
        self.clip = None

    def ctx(self):
        return Ctx(self.timeline)

    def _kill_hosts_and_reap(self):
        for host in self.hosts:
            if host.poll() is None:
                host.kill()
        for host in self.hosts:
            try:
                host.wait(10)
            except subprocess.TimeoutExpired:
                pass
        # 终极兜底：本测试只清扫自己项目内的账本（只杀账本核验过的组）
        try:
            reap_orphan_workers(self.ctx())
        except Exception:
            pass

    def tmp_ledger_dirs(self):
        tmp_root = self.root / 'cache' / hygiene.TMP
        if not tmp_root.is_dir():
            return []
        return sorted(x for x in tmp_root.iterdir()
                      if x.is_dir() and (x / WORKER_LEDGER).is_file())


@unittest.skipUnless(_LINUX_PROC, '真实进程树演练需要 Linux /proc')
class HostSigkillDoD(_ProjectFixture):
    """DoD ①：宿主被 SIGKILL 后 100ms 进程树归零 + reap 后临时目录残留为 0。"""

    def spawn_host(self, mode):
        seq = len(self.hosts)
        ready = self.root / f'ready_{seq}.json'
        err = self.root / f'host_{seq}.stderr'
        with err.open('wb') as err_fh:
            host = subprocess.Popen(
                [sys.executable, '-c', HOST_PROGRAM, str(self.root), str(ready),
                 mode, str(self.sim)],
                cwd=str(ROOT), env={**os.environ, 'PYTHONPATH': str(ROOT)},
                stdout=subprocess.DEVNULL, stderr=err_fh)
        self.hosts.append(host)

        def _load():
            try:
                return json.loads(ready.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                return None

        rec = _wait_until(_load, timeout=20)
        self.assertIsNotNone(rec,
                             f'宿主 20s 未就绪（stderr: {err.read_text(errors="replace")[-2000:] if err.exists() else "?"}）')
        self.assertEqual(rec['host_pid'], host.pid)
        # O4（红队）：就绪条件用确定性信号——「组员>=4」fork 瞬间即真，而
        # lock_*.pid 须待子进程 exec + import + flock 之后才写；负载下两者
        # 可相隔数秒，直接断言 lock 数=3 会 flake（run_all.log:1123）。这里
        # 等 lock_*.pid 文件实际出现（带超时）后才算就绪。
        run_dir = Path(rec['run_dir'])
        self.assertTrue(_wait_until(
            lambda: len(list((run_dir / 'profile').glob('lock_*.pid'))) >= 3,
            timeout=20),
            '3 个子进程未在 20s 内完成 exec+flock 并写出 lock_*.pid（确定性就绪信号未出现）')
        # 树已凑齐（上面的 lock 等待强于组员计数，此处仅复核形状）
        self.assertTrue(_wait_until(
            lambda: len(group_live_members(rec['pgid'])) >= 4, timeout=20),
                        '模拟 Chromium 树未在 20s 内凑齐 4 个成员')
        return rec

    def _assert_kill9_tree_zero_and_reap_clean(self, mode):
        rec = self.spawn_host(mode)
        pgid, run_dir = rec['pgid'], Path(rec['run_dir'])
        self.assertTrue((run_dir / WORKER_LEDGER).is_file())
        lock_pids = list((run_dir / 'profile').glob('lock_*.pid'))
        self.assertEqual(len(lock_pids), 3, '3 个子进程须各自锁住 user-data-dir 文件')
        # —— DoD：对 Python 宿主 os.kill(SIGKILL) ——
        t0 = time.monotonic()
        os.kill(rec['host_pid'], signal.SIGKILL)
        self.hosts[-1].wait(timeout=10)
        time.sleep(max(0.0, 0.105 - (time.monotonic() - t0)))
        self.assertEqual(
            group_live_members(pgid), [],
            f'kill -9 宿主 100ms 后进程表残活成员必须为 0（mode={mode}）')
        # 宿主暴毙：上下文退出未运行 → 目录/账本残留（这正是 reap 的标的）
        self.assertTrue(run_dir.exists())
        stats = reap_orphan_workers(self.ctx())
        self.assertGreaterEqual(stats['ledgers'], 1)
        self.assertGreaterEqual(stats['removed'], 1)
        self.assertFalse(run_dir.exists())
        self.assertEqual(self.tmp_ledger_dirs(), [], 'reap 后临时目录残留必须为 0')

    def test_noncooperative_tree_sigkill_zero_in_100ms(self):
        # 非协作 Worker（真实 Chrome 同形）：归零只能靠监管器（PDEATHSIG + 守卫组杀）
        self._assert_kill9_tree_zero_and_reap_clean('plain')

    def test_cooperative_watcher_contract_sigkill_zero_in_100ms(self):
        # 协作 Worker（模块头契约）：EOF→killpg(0) 与子进程 PDEATHSIG 双保险
        self._assert_kill9_tree_zero_and_reap_clean('coop')

    def test_reap_skips_live_host_group_then_cleans_after_death(self):
        # 并行 vtx 进程保护：宿主仍存活的账本组绝不被 reap 误杀
        rec = self.spawn_host('plain')
        pgid, run_dir = rec['pgid'], Path(rec['run_dir'])
        stats = reap_orphan_workers(self.ctx())
        self.assertEqual(stats['live_host'], 1)
        self.assertEqual((stats['killed'], stats['removed']), (0, 0))
        self.assertTrue(run_dir.exists())
        self.assertGreaterEqual(len(group_live_members(pgid)), 4, '活动组未被误杀')
        os.kill(rec['host_pid'], signal.SIGKILL)
        self.hosts[-1].wait(timeout=10)
        self.assertTrue(_wait_until(lambda: group_live_members(pgid) == [], timeout=15))
        stats = reap_orphan_workers(self.ctx())
        self.assertGreaterEqual(stats['removed'], 1)
        self.assertFalse(run_dir.exists())
        self.assertEqual(self.tmp_ledger_dirs(), [])

    def test_supervised_group_normal_exit_stops_tree_and_cleans_all(self):
        # 正常收尾（本进程即宿主）：优雅停组 + 账本删除 + 守卫退出 + tmp_run 清场
        c = self.ctx()
        with SupervisedProcessGroup(
                c,
                lambda run, profile: [sys.executable, str(self.sim), 'main',
                                      'plain', str(profile)],
                backend_id='sim') as sg:
            pgid, run_dir, guard = sg.pgid, sg.run_dir, sg.guard_proc
            self.assertTrue((run_dir / WORKER_LEDGER).is_file())
            self.assertTrue(sg.profile_dir.is_dir())
            self.assertTrue(_wait_until(lambda: len(group_live_members(pgid)) >= 4),
                            '树未凑齐 4 成员')
        self.assertEqual(group_live_members(pgid), [], '正常退出后进程树归零')
        self.assertFalse(run_dir.exists(), '正常退出后运行目录（含 Profile）清场')
        self.assertIsNotNone(guard.poll(), '守卫随父存活管道关闭退出')
        self.assertEqual(reap_orphan_workers(c),
                         {'ledgers': 0, 'killed': 0, 'live_host': 0,
                          'stale': 0, 'removed': 0})

    def test_reap_never_kills_on_pid_reuse_boot_mismatch_or_broken_ledger(self):
        # 防误杀三态：PID 回绕（starttime 不符）/ 旧开机账本（boot_id 不符）/
        # 损坏账本——一律只清目录，绝不动无关存活进程
        victim = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                  start_new_session=True)  # 自成组：pgid == victim.pid
        self.addCleanup(victim.wait)
        self.addCleanup(victim.kill)
        ticks = proc_starttime(victim.pid)
        self.assertIsNotNone(ticks)
        tmp_root = self.root / 'cache' / hygiene.TMP
        tmp_root.mkdir(parents=True)
        cases = {
            'pid_reuse': {'pgid': victim.pid, 'start_time_ticks': ticks + 9_999_999,
                          'boot_id': boot_id()},
            'stale_boot': {'pgid': victim.pid, 'start_time_ticks': ticks,
                           'boot_id': '00000000-0000-0000-0000-000000000000'},
        }
        made = []
        for name, extra in cases.items():
            d = tmp_root / f'20260101T000000Z-gfx_sim-fake-{name}'
            d.mkdir()
            (d / WORKER_LEDGER).write_text(json.dumps(
                {'schema': 1, 'host_pid': 999_999_999,
                 'host_start_time_ticks': 1, **extra}), encoding='utf-8')
            made.append(d)
        broken = tmp_root / '20260101T000000Z-gfx_sim-fake-broken'
        broken.mkdir()
        (broken / WORKER_LEDGER).write_text('{broken', encoding='utf-8')
        made.append(broken)
        stats = reap_orphan_workers(self.ctx())
        self.assertEqual((stats['killed'], stats['stale'], stats['removed']), (0, 3, 3))
        for d in made:
            self.assertFalse(d.exists())
        self.assertIsNone(victim.poll(), '无关存活进程毫发无损')

    def test_forged_ledger_pgid_zero_is_clean_failure_never_kills_caller(self):
        # R103-1 回归（红队 poc_e/poc_cli）：伪造 pgid=0 账本 → reap 按
        # broken_ledger 干净失败（只清目录、killed=0），绝不 killpg(0)——
        # 本测试进程能活着走到断言，本身即证明调用方进程组未被反向击杀。
        tmp_root = self.root / 'cache' / hygiene.TMP
        tmp_root.mkdir(parents=True)
        d = tmp_root / '20260101T000000Z-gfx_sim-fake-pgid0'
        d.mkdir()
        (d / WORKER_LEDGER).write_text(json.dumps(
            {'schema': 1, 'pgid': 0, 'start_time_ticks': 0,
             'host_pid': 999_999_999, 'host_start_time_ticks': 1,
             'boot_id': boot_id()}), encoding='utf-8')
        my_group = os.getpgid(0)
        stats = reap_orphan_workers(self.ctx())
        self.assertEqual((stats['killed'], stats['stale'], stats['removed']),
                         (0, 1, 1), 'pgid=0 须按损坏账本只清目录，零击杀')
        self.assertFalse(d.exists())
        self.assertIn(os.getpid(), group_live_members(my_group),
                      '调用方进程组完好（未被 killpg(0) 反杀）')


# R3 谱系白名单三态演练程序（self=自身组拒绝 / mid+leaf=真实祖先组拒绝 /
# normal=自己子组放行）。铁律：一切被测 kill 路径（kill_group/reap_run_dir/
# reap_orphan_workers）只在本程序进程内执行，而本程序由测试以
# start_new_session=True 拉起——即使白名单失效、子进程「自杀」，SIGKILL
# 也只落在隔离子进程自己的组里，绝不波及测试进程与宿主会话（09:22 事故）。
WHITELIST_DRILL_PROGRAM = r'''
import json, os, shutil, subprocess, sys
from pathlib import Path

ROOT, role = Path(sys.argv[1]), sys.argv[2]
scratch, out, timeline_src = Path(sys.argv[3]), Path(sys.argv[4]), Path(sys.argv[5])
mid_pid = int(sys.argv[6]) if len(sys.argv) > 6 else None
sys.path.insert(0, str(ROOT))
from vtx import hygiene
from vtx.infra import Ctx
from vtx.proc_supervisor import (WORKER_LEDGER, LineageKillDenied, boot_id,
                                 group_live_members, kill_group,
                                 proc_starttime, reap_orphan_workers,
                                 reap_run_dir)

if role == "mid":
    # 中间祖先：自成会话（pgid==mid.pid），派生 leaf（自成会话）执行被测
    # 代码。白名单若失效，killpg(mid.pid) 会先杀死本进程——mid 报告缺失
    # 或 leaf 报告异常即测试失败，而测试进程因会话隔离毫发无损。
    leaf_out = scratch / "leaf_report.json"
    leaf = subprocess.Popen(
        [sys.executable, __file__, str(ROOT), "leaf", str(scratch),
         str(leaf_out), str(timeline_src), str(os.getpid())],
        start_new_session=True)
    leaf.wait(90)
    try:
        leaf_rep = json.loads(leaf_out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        leaf_rep = None
    out.write_text(json.dumps({
        "leaf_rc": leaf.returncode, "leaf_report": leaf_rep,
        "mid_pid": os.getpid(), "mid_pgid": os.getpgid(0),
        "mid_group_after": group_live_members(os.getpgid(0))},
        ensure_ascii=False), encoding="utf-8")
    raise SystemExit(0)

# —— 以下角色即被测代码执行者（各自在隔离子进程内）——
scratch.mkdir(parents=True, exist_ok=True)
tl = scratch / "timeline.json"
shutil.copyfile(timeline_src, tl)
c = Ctx(tl)
tmp_root = scratch / "cache" / hygiene.TMP


def forge(name, pgid, ticks):
    tmp_root.mkdir(parents=True, exist_ok=True)
    d = tmp_root / ("20260101T000000Z-gfx_sim-fake-" + name)
    d.mkdir()
    (d / WORKER_LEDGER).write_text(json.dumps(
        {"schema": 1, "pgid": pgid, "start_time_ticks": ticks,
         "host_pid": 999_999_999, "host_start_time_ticks": 1,
         "boot_id": boot_id()}), encoding="utf-8")
    return d


def try_kill(pgid):
    try:
        kill_group(pgid)
        return "NOT-DENIED(BUG)"
    except LineageKillDenied:
        return "denied"


if role == "self":
    # 态①：伪造账本 pgid=调用方自身组——须零击杀+目录清理+调用方存活
    canary = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"])  # 同组金丝雀
    try:
        my_pgid = os.getpgid(0)
        d = forge("lineage-self", my_pgid, proc_starttime(os.getpid()))
        out.write_text(json.dumps({
            "role": "self", "direct": try_kill(my_pgid),
            "reap_status": reap_run_dir(d),
            "stats": reap_orphan_workers(c),
            "canary_alive": canary.poll() is None,
            "ledger_dir_exists": d.exists()}, ensure_ascii=False),
            encoding="utf-8")
    finally:
        canary.kill()
        canary.wait(10)
    raise SystemExit(0)

if role == "normal":
    # 态③：自己的子进程组（victim 自成会话）不受白名单影响，仍可杀
    victim = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True)
    try:
        try:
            kill_ok, denied = kill_group(victim.pid), False
        except LineageKillDenied:
            kill_ok, denied = False, True
        out.write_text(json.dumps({
            "role": "normal", "victim_pgid": victim.pid, "denied": denied,
            "kill_group_ok": kill_ok,
            "victim_members_after": group_live_members(victim.pid)},
            ensure_ascii=False), encoding="utf-8")
    finally:
        try:
            victim.wait(10)
        except subprocess.TimeoutExpired:
            victim.kill()
            victim.wait(10)
    raise SystemExit(0)

# role == "leaf"：态②——伪造账本 pgid=真实祖先（父进程 mid）的进程组
d = forge("lineage-anc", mid_pid, proc_starttime(mid_pid))
out.write_text(json.dumps({
    "role": "leaf", "direct": try_kill(mid_pid),
    "reap_status": reap_run_dir(d),
    "anc_group_alive": group_live_members(mid_pid) != []},
    ensure_ascii=False), encoding="utf-8")
raise SystemExit(0)
'''


@unittest.skipUnless(_LINUX_PROC, '谱系白名单演练需要 Linux /proc')
class LineageWhitelistDrillTests(_ProjectFixture):
    """R3 硬防护三态：kill_group 谱系白名单——自身组/真实祖先组拒杀（零击杀
    +账本目录按陈旧语义清理）、自己的子进程组照常可杀。

    演练程序在自有 scratch 项目副本里伪造账本（与本夹具项目物理隔离，
    夹具收尾的兜底 reap 永远扫不到演练账本）；测试进程只编排与断言，
    不触碰任何 kill 路径（09:22 事故铁律）。"""

    def setUp(self):
        super().setUp()
        self.drill = self.root / 'whitelist_drill.py'
        self.drill.write_text(WHITELIST_DRILL_PROGRAM, encoding='utf-8')

    def _err_tail(self, err):
        try:
            return err.read_text(errors='replace')[-1500:]
        except OSError:
            return '?'

    def _run_drill(self, role, tag):
        scratch = self.root / f'{tag}_scratch'
        out = self.root / f'{tag}_report.json'
        err = self.root / f'{tag}.stderr'
        with err.open('wb') as err_fh:
            proc = subprocess.Popen(
                [sys.executable, str(self.drill), str(ROOT), role,
                 str(scratch), str(out), str(self.timeline)],
                cwd=str(ROOT), env={**os.environ, 'PYTHONPATH': str(ROOT)},
                stdout=subprocess.DEVNULL, stderr=err_fh,
                start_new_session=True)   # 铁律：被测代码必在隔离子进程内
        self.hosts.append(proc)

        def _load():
            try:
                return json.loads(out.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                return None
        return proc, _load, err

    def test_self_group_ledger_denied_zero_kill_dir_cleaned_caller_alive(self):
        # 态①：伪造账本 pgid=调用方自身组 → 拒杀、清目录、调用方/金丝雀存活
        proc, load, err = self._run_drill('self', 'wl_self')
        rc = proc.wait(90)
        self.assertEqual(
            rc, 0, f'隔离子进程异常退出 rc={rc}（被组杀？stderr 尾：'
                   f'{self._err_tail(err)}）')
        rep = load()
        self.assertIsNotNone(rep, '演练报告缺失（子进程未活到写报告）')
        self.assertEqual(rep['direct'], 'denied',
                         'kill_group 最内层入口必须拒调用方自身组')
        self.assertEqual(rep['reap_status'], 'lineage_denied')
        self.assertEqual(rep['stats'], {'ledgers': 1, 'killed': 0,
                                        'live_host': 0, 'stale': 1,
                                        'removed': 1})
        self.assertFalse(rep['ledger_dir_exists'], '账本目录须按陈旧语义清理')
        self.assertTrue(rep['canary_alive'], '同组金丝雀存活=零击杀铁证')
        self.assertIn(os.getpid(), group_live_members(os.getpgid(0)),
                      '测试进程组完好（隔离子进程「自杀」也未波及本进程）')

    def test_ancestor_group_ledger_denied_mid_tree_intact(self):
        # 态②：mid(自成会话)→leaf(自成会话) 树，leaf 对真实祖先（mid）的组
        # 伪造账本 → 拒杀；mid 全须活着交回报告，leaf 报告拒杀铁证
        proc, load, err = self._run_drill('mid', 'wl_mid')
        rc = proc.wait(120)
        self.assertEqual(
            rc, 0, f'mid 异常退出 rc={rc}（中间祖先被误杀=白名单失效；'
                   f'stderr 尾：{self._err_tail(err)}）')
        rep = load()
        self.assertIsNotNone(rep, 'mid 报告缺失（中间祖先被误杀=白名单失效）')
        self.assertEqual(rep['mid_group_after'], [rep['mid_pid']],
                         '中间祖先组在演练后必须原样存活')
        self.assertEqual(rep['leaf_rc'], 0, 'leaf（被测代码执行者）异常退出')
        leaf = rep['leaf_report']
        self.assertIsNotNone(leaf, 'leaf 报告缺失')
        self.assertEqual(leaf['direct'], 'denied',
                         'kill_group 必须拒真实祖先进程的进程组')
        self.assertEqual(leaf['reap_status'], 'lineage_denied')
        self.assertTrue(leaf['anc_group_alive'], '祖先组未被击杀')

    def test_own_child_group_still_killable(self):
        # 态③：正常监管组（自己的子进程组）不受白名单影响，仍可杀
        proc, load, err = self._run_drill('normal', 'wl_normal')
        rc = proc.wait(90)
        self.assertEqual(rc, 0, f'隔离子进程异常退出 rc={rc}（stderr 尾：'
                                f'{self._err_tail(err)}）')
        rep = load()
        self.assertIsNotNone(rep, '演练报告缺失')
        self.assertFalse(rep['denied'], '自己的子进程组被白名单误拒')
        self.assertTrue(rep['kill_group_ok'], '子进程组击杀须照常成功')
        self.assertEqual(rep['victim_members_after'], [],
                         '击杀后进程表必须归零')


@unittest.skipUnless(_LINUX_PROC, '/proc 探针形状断言需要 Linux')
class ProcStatHelperTests(unittest.TestCase):
    def test_boot_id_starttime_and_group_membership_shapes(self):
        self.assertRegex(boot_id(), r'^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
        ticks = proc_starttime(os.getpid())
        self.assertIsInstance(ticks, int)
        self.assertGreater(ticks, 0)
        self.assertIn(os.getpid(), group_live_members(os.getpgid(0)))
        self.assertIsNone(proc_starttime(999_999_999))
        self.assertEqual(group_live_members(999_999_999), [])

    def test_supervision_available_follows_platform_probe(self):
        # O1：平台探测（/proc 缺失）→ 监管不可用（接入点据此降级分流）
        self.assertTrue(proc_supervisor.supervision_available())
        with mock.patch.object(proc_supervisor, '_HAS_PROC', False):
            self.assertFalse(proc_supervisor.supervision_available())


class SupervisionDegradationTests(_ProjectFixture):
    """O1：监管不可用平台（非 Linux/无 /proc）真实降级——不硬失败（跨平台离线）。"""

    def test_no_proc_platform_falls_back_to_unsupervised_with_warning(self):
        # monkeypatch 平台探测 → Browser 接入点回落 #103 之前的无监管路径
        # （裸 Popen + tempfile.mkdtemp，= main 基线），渲染启动照常成功 +
        # 一次性 stderr 告警。修复前此处是 _write_ledger 的 RuntimeError
        # 硬失败（macOS 官方支持平台上 gfx 阶段直接死）。
        fake_proc = mock.Mock()
        fake_proc.poll.return_value = None
        fake_tab = {"webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/page/abc"}
        err = io.StringIO()
        with mock.patch.object(proc_supervisor, '_HAS_PROC', False), \
                mock.patch.object(gfx_render, '_SUPERVISION_WARNED', False), \
                mock.patch.object(gfx_render.Browser, '_find_chrome',
                                  lambda self: None), \
                mock.patch.object(gfx_render.subprocess, 'Popen',
                                  return_value=fake_proc) as popen, \
                mock.patch.object(gfx_render.urllib.request, 'urlopen',
                                  return_value=mock.Mock()), \
                mock.patch.object(gfx_render.json, 'load',
                                  return_value=fake_tab), \
                contextlib.redirect_stderr(err):
            b = gfx_render.Browser(chrome_path='/nonexistent/chrome',
                                   tabs=1, ctx=self.ctx())
            b.start()                      # 降级路径：不抛即渲染启动成功
            self.assertIsNone(b.group, '监管不可用时不得构造监管组')
            self.assertIsNotNone(b.proc)
            self.assertTrue(str(b.user_data_dir).startswith(
                tempfile.gettempdir()), 'Profile 回落系统临时目录（#103 前基线）')
            self.assertTrue(Path(b.user_data_dir).name.startswith('vtx_gfx_cdp_'))
            self.assertEqual(b.tab_ws_urls, ['/devtools/page/abc'])
            argv = popen.call_args[0][0]
            self.assertFalse(
                any(str(a).startswith('--parent-alive-fd=') for a in argv),
                '无监管路径不得携带协作契约 fd')
            self.assertIn('监管不可用', err.getvalue(), '须显式 stderr 告警')
            b.hard_restart()               # 重起不刷屏：一次性告警
            self.assertEqual(err.getvalue().count('监管不可用'), 1)
            b.stop()
            self.assertFalse(Path(b.user_data_dir).exists(),
                             '无监管路径收尾清理临时 Profile')


class SandboxCacheNamespaceTests(_ProjectFixture):
    """DoD ②：VTX_GFX_CACHE_NS 沙箱隔离（离线，无真实进程/浏览器）。"""

    def setUp(self):
        super().setUp()
        self.c = self.ctx()
        self.clip = self.c.tl['video_tracks'][0]['clips'][0]
        self.key = hygiene.gfx_key(self.c, self.clip)

    def test_gfx_dir_ns_routing_with_key_invariance(self):
        with mock.patch.dict(os.environ, {'VTX_GFX_CACHE_NS': 'cef_a0'}):
            d_a = hygiene.gfx_dir(self.c, self.clip)
        with mock.patch.dict(os.environ, {'VTX_GFX_CACHE_NS': 'elec_b1'}):
            d_b = hygiene.gfx_dir(self.c, self.clip)
        d_default = hygiene.gfx_dir(self.c, self.clip)
        # 路由：实验态 cache/gfx_exp/<ns>/<key>/；正式态原路径逐字节不变
        self.assertEqual(d_a, self.c.cache / 'gfx_exp' / 'cef_a0' / self.key)
        self.assertEqual(d_b, self.c.cache / 'gfx_exp' / 'elec_b1' / self.key)
        self.assertEqual(d_default, self.c.cache / 'gfx' / self.key)
        # gfx_key 一字不动：同页在三个 ns 下同键（异目录、同键轴）
        with mock.patch.dict(os.environ, {'VTX_GFX_CACHE_NS': 'cef_a0'}):
            self.assertEqual(hygiene.gfx_key(self.c, self.clip), self.key)

    def test_invalid_ns_rejected_loudly(self):
        # 注：含 NUL 的值 os.environ 本身拒绝设置（putenv 报错），到不了校验层
        for bad in ('../evil', '/abs', 'a/b', '.hidden', 'a b',
                    'x' * 65, 'y' * 300):   # O3：长度超限（含红队 300 字符探针）
            with self.subTest(ns=bad[:16]), \
                    mock.patch.dict(os.environ, {'VTX_GFX_CACHE_NS': bad}):
                with self.assertRaises(ValueError):
                    hygiene.gfx_dir(self.c, self.clip)
        # 长度上界本身合法（64 = _NS_MAX）：不抛
        with mock.patch.dict(os.environ, {'VTX_GFX_CACHE_NS': 'z' * 64}):
            self.assertEqual(hygiene.gfx_dir(self.c, self.clip),
                             self.c.cache / 'gfx_exp' / ('z' * 64) / self.key)
        # 空串/空白 = 正式态（默认），不抛
        for blank in ('', '   '):
            with mock.patch.dict(os.environ, {'VTX_GFX_CACHE_NS': blank}):
                self.assertEqual(hygiene.gfx_dir(self.c, self.clip),
                                 self.c.cache / 'gfx' / self.key)

    def test_complete_json_optional_backend_ns_metadata(self):
        d = hygiene.gfx_dir(self.c, self.clip)
        d.mkdir(parents=True)
        for i in range(3):
            (d / f'f{i:04d}.png').write_bytes(png(value=i))
        gfx_cli.complete_page(d, 3, self.key, 6, 4, verify_inputs=lambda: None,
                              backend_ns='cef_a0')
        marker = json.loads((d / gfx_cli.COMPLETE_FILE).read_text())
        self.assertEqual(marker['backend_ns'], 'cef_a0')
        bad, needs = gfx_cli.inspect_page(d, 3, self.key, 6, 4, backend_ns='cef_a0')
        self.assertEqual((bad, needs), ([], False))
        # 缺省：字段不写（既有字节形状），ns 只作元数据不进失效合同
        gfx_cli.complete_page(d, 3, self.key, 6, 4, verify_inputs=lambda: None)
        marker = json.loads((d / gfx_cli.COMPLETE_FILE).read_text())
        self.assertNotIn('backend_ns', marker)
        bad, needs = gfx_cli.inspect_page(d, 3, self.key, 6, 4)
        self.assertEqual((bad, needs), ([], False))

    def _run_gfx(self, renderer, ns):
        env = {'VTX_GFX': 'cdp', 'VTX_GFX_SPLIT_FRAMES': '0', 'VTX_GFX_PAGES': '1',
               'VTX_GFX_CACHE_NS': ns}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(gfx_render, 'available', return_value=True), \
                mock.patch.object(gfx_render, 'render_page', side_effect=renderer), \
                contextlib.redirect_stdout(io.StringIO()):
            # Backend/namespace are pinned inputs: each invocation constructs
            # its context under the selected environment, just like the CLI.
            cli.gfx(self.ctx())

    def test_cross_ns_never_hits_each_other_via_cli_gfx(self):
        d_default = self.c.cache / 'gfx' / self.key
        d_a = self.c.cache / 'gfx_exp' / 'cef_a0' / self.key
        d_b = self.c.cache / 'gfx_exp' / 'elec_b0' / self.key
        rendered = []

        def renderer(html, d, n, **kw):
            for i in range(n):
                (Path(d) / f'f{i:04d}.png').write_bytes(png(value=i))
            rendered.append(Path(d).name)
            return stats(n)

        self._run_gfx(renderer, 'cef_a0')
        self.assertTrue((d_a / 'f0002.png').is_file())
        self.assertEqual(json.loads((d_a / 'complete.json').read_text())['backend_ns'],
                         'cef_a0')
        self.assertFalse(d_b.exists(), '未启用的 ns 不得被写入')
        self.assertFalse(d_default.exists(), '正式缓存不得被实验渲染触碰')

        before = len(rendered)
        self._run_gfx(renderer, 'cef_a0')          # 同 ns 二跑：全命中零渲染
        self.assertEqual(len(rendered), before)

        self._run_gfx(renderer, 'elec_b0')         # 换 ns：必须 miss（物理隔离）
        self.assertEqual(len(rendered), before + 1)
        self.assertTrue((d_b / 'f0002.png').is_file())
        self.assertEqual(json.loads((d_b / 'complete.json').read_text())['backend_ns'],
                         'elec_b0')

        self._run_gfx(renderer, '')                # 正式态：再 miss，落 cache/gfx/
        self.assertEqual(len(rendered), before + 2)
        self.assertTrue((d_default / 'f0002.png').is_file())
        self.assertNotIn('backend_ns',
                         json.loads((d_default / 'complete.json').read_text()))
        # 正式缓存命中（同键零渲染）与两个实验缓存并存互不污染
        self._run_gfx(renderer, '')
        self.assertEqual(len(rendered), before + 2)

    def test_tmp_run_unique_suffix_disambiguates_same_second(self):
        c = self.ctx()
        with hygiene.tmp_run(c, 'gfx_sim', unique=True) as d1:
            with hygiene.tmp_run(c, 'gfx_sim', unique=True) as d2:
                self.assertNotEqual(d1, d2, '同秒同名监管组目录不得互撞')
                self.assertTrue(d1.is_dir() and d2.is_dir())
            self.assertFalse(d2.exists())
        self.assertFalse(d1.exists())


if __name__ == '__main__':
    unittest.main()
