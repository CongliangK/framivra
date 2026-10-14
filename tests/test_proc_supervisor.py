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

from vtx import cli, gfx_cli, gfx_render, hygiene
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
        # 主进程 + 3 个子进程都进组，且子进程已锁住 user-data-dir 文件
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
        for bad in ('../evil', '/abs', 'a/b', '.hidden', 'a b'):
            with self.subTest(ns=bad), \
                    mock.patch.dict(os.environ, {'VTX_GFX_CACHE_NS': bad}):
                with self.assertRaises(ValueError):
                    hygiene.gfx_dir(self.c, self.clip)
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
            cli.gfx(self.c)

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
