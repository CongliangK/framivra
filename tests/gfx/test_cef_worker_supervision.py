"""issue #109：vtx_cef_worker（CEF Native OSR）监管接入与 DoD 验收。

门控：VTX_TEST_CEF_WORKER=<vtx_cef_worker 可执行路径>（先
`bash experiments/gfx_cef_worker/build_cef_worker.sh`）。未设置即跳过——
本测试需要真实 CEF 二进制与多进程树，不属 CPU CI 媒体面。

三组验收（工单 DoD 逐条）：
  1. A0（Software OSR）命令行独立加载 templates/skill-demo/gfxA.html?f=0
     → 1920x1080 PNG 过 gfx_cli.png_invalid_reason()；
  2. A1（GPU 合成→CPU Buffer，纯无头 ozone=headless，任何显示环境可跑）
     → 同上；
  3. 监管 kill 演练：独立 Python 宿主经 SupervisedProcessGroup（#103）拉起
     A1 worker（--root-cache-path=监管运行目录，渲染途中 --hold-ms 保活），
     对宿主 SIGKILL 后 105ms 断言：Worker 进程树残活 == 0 且
     pgrep -f vtx_cef_worker == 0；reap_orphan_workers 清扫后临时目录
     残留 == 0。

安全铁律（09:22 事故后）：一切被杀对象都是本测试自起子进程（worker 组是
宿主 start_new_session 的独立组；宿主是测试的直接子进程）；kill 均按 PID
或账本核验组进行。

独立运行：VTX_TEST_CEF_WORKER=<path> python3 -m unittest
tests.gfx.test_cef_worker_supervision -v
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from vtx.render.gfx_cli import png_invalid_reason
from vtx.cache.hygiene import TMP
from vtx.infra import Ctx
from vtx.runtime.proc_supervisor import (WORKER_LEDGER, SupervisedProcessGroup,
                                 group_live_members, reap_orphan_workers)

ROOT = Path(__file__).resolve().parents[2]
_LINUX_PROC = Path("/proc/self/stat").is_file()
_WORKER = os.environ.get("VTX_TEST_CEF_WORKER", "")
GFAX_URL = (ROOT / "templates/skill-demo/gfxA.html").as_uri() + "?f=0"

_GATE = _LINUX_PROC and _WORKER and Path(_WORKER).is_file()


def _wait_until(pred, timeout=30.0, interval=0.05):
    """轮询直到 pred() 真值；超时返回最后一次取值（调用方断言）。"""
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value:
            return value
        if time.monotonic() >= deadline:
            return pred()
        time.sleep(interval)


def _pgrep_worker_count() -> int:
    """pgrep -f 计数（DoD 口径；pgrep 自身永不匹配自己）。"""
    try:
        out = subprocess.run(["pgrep", "-f", _WORKER],
                             capture_output=True, text=True, timeout=10)
        return len([ln for ln in out.stdout.splitlines() if ln.strip()])
    except (OSError, subprocess.SubprocessError):
        return -1


# 独立 Python 宿主：Ctx → SupervisedProcessGroup 拉起 A1 worker（渲染→
# --hold-ms 保活），out.png 出现即写就绪文件。被 SIGKILL 即 DoD「宿主暴毙」。
_HOST_PROGRAM = r'''
import json, os, sys, time
from pathlib import Path
from vtx.infra import Ctx
from vtx.runtime.proc_supervisor import SupervisedProcessGroup

root, ready_path, worker, gfx_url = (Path(sys.argv[1]), Path(sys.argv[2]),
                                     sys.argv[3], sys.argv[4])
c = Ctx(root / "timeline.json")


def worker_argv(run_dir, profile):
    return [worker, "--mode=a1", "--ozone-platform=headless",
            f"--url={gfx_url}", f"--out-png={run_dir}/out.png",
            f"--root-cache-path={run_dir}",
            "--begin-frames=4", "--hold-ms=180000"]


with SupervisedProcessGroup(c, worker_argv, backend_id="cef_a1") as sg:
    while not (sg.run_dir / "out.png").is_file():
        time.sleep(0.05)
    ready_path.write_text(json.dumps({
        "run_dir": str(sg.run_dir), "pgid": sg.pgid, "host_pid": os.getpid(),
        "worker_pid": sg.proc.pid}), encoding="utf-8")
    time.sleep(600)
'''


# 渲染宿主：直接 Popen worker（隔离会话）→ 等退出 → 转发退出码
_HOST_RENDER = r'''
import subprocess, sys
worker, mode, ozone, out, gfx_url = sys.argv[1:6]
argv = [worker, f"--mode={mode}"]
if ozone:
    argv.append(f"--ozone-platform={ozone}")
argv += [f"--url={gfx_url}", f"--out-png={out}", "--timeout-ms=60000"]
raise SystemExit(subprocess.Popen(argv, start_new_session=True).wait())
'''


class _ProjectFixture(unittest.TestCase):
    """隔离临时项目 + 严格回收：只杀本测试自起进程树。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vtx_issue109_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.timeline = self.root / "timeline.json"
        self.timeline.write_text(json.dumps(dict(
            project=dict(fps=30, width=6, height=4, duration_frames=3),
            sources=[], subtitles=[], audio_tracks=[],
            video_tracks=[dict(kind="gfx", clips=[
                dict(src="page.html", dst_in=0, dur=3)])])))
        self.hosts = []
        self.addCleanup(self._kill_hosts_and_reap)

    def ctx(self):
        return Ctx(self.timeline)

    def _kill_hosts_and_reap(self):
        for host in self.hosts:
            if host.poll() is None:
                host.kill()   # 测试自起子进程：允许按 PID 杀
        for host in self.hosts:
            try:
                host.wait(10)
            except subprocess.TimeoutExpired:
                pass
        try:
            reap_orphan_workers(self.ctx())   # 只杀账本核验过的组
        except Exception:
            pass

    def tmp_ledger_dirs(self):
        tmp_root = self.root / "cache" / TMP
        if not tmp_root.is_dir():
            return []
        return sorted(x for x in tmp_root.iterdir()
                      if x.is_dir() and (x / WORKER_LEDGER).is_file())

    def render_png(self, mode, ozone=None):
        """命令行独立渲染一帧（worker 为隔离子进程，经独立渲染宿主）。"""
        out = self.root / f"out_{mode}_{ozone or 'default'}.png"
        argv = [sys.executable, "-c", _HOST_RENDER, _WORKER, mode,
                ozone or "", str(out), GFAX_URL]
        # 渲染经独立隔离子进程跑（铁律：不在测试进程内嵌 CEF 生命周期）
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=180)
        self.assertEqual(proc.returncode, 0,
                         f"渲染宿主失败:\n{proc.stdout}\n{proc.stderr}")
        reason = png_invalid_reason(out, width=1920, height=1080)
        self.assertIsNone(reason, f"{mode}/{ozone} PNG 无效: {reason}")
        return out


@unittest.skipUnless(_GATE, "需要 VTX_TEST_CEF_WORKER（CEF worker 可执行）+ Linux /proc")
class StandaloneRenderDoD(_ProjectFixture):
    """DoD ①：A0 与 A1 各出一张 1920x1080 PNG 且过 png_invalid_reason。"""

    def test_a0_software_osr_png_valid(self):
        png = self.render_png("a0")
        self.assertGreater(png.stat().st_size, 0)

    def test_a1_gpu_composition_headless_png_valid(self):
        png = self.render_png("a1", ozone="headless")
        self.assertGreater(png.stat().st_size, 0)


@unittest.skipUnless(_GATE, "需要 VTX_TEST_CEF_WORKER（CEF worker 可执行）+ Linux /proc")
class SupervisedKillDoD(_ProjectFixture):
    """DoD ②：kill -9 Python 宿主 → 100ms 组内残活=0 → reap 后残留=0。"""

    def _spawn_drill_host(self):
        ready = self.root / "ready_drill.json"
        err = self.root / "host_drill.stderr"
        with err.open("wb") as err_fh:
            host = subprocess.Popen(
                [sys.executable, "-c", _HOST_PROGRAM, str(self.root),
                 str(ready), _WORKER, GFAX_URL],
                cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT)},
                stdout=subprocess.DEVNULL, stderr=err_fh)
        self.hosts.append(host)

        def _load():
            try:
                return json.loads(ready.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None

        rec = _wait_until(_load, timeout=60)
        self.assertIsNotNone(
            rec, f"演练宿主 60s 未就绪（stderr: "
                 f"{err.read_text(errors='replace')[-1500:] if err.exists() else '?'}）")
        self.assertEqual(rec["host_pid"], host.pid)
        run_dir = Path(rec["run_dir"])
        # 确定性就绪：PNG 已出（渲染链路完整走通）+ 真实 CEF 树 ≥3 成员
        self.assertTrue((run_dir / "out.png").is_file(), "就绪时 PNG 必在")
        self.assertTrue(
            _wait_until(lambda: len(group_live_members(rec["pgid"])) >= 3,
                        timeout=30),
            "CEF 树未在 30s 内凑齐 ≥3 成员（browser/gpu/renderer）")
        return rec

    def test_kill9_host_tree_zero_in_105ms_and_reap_clean(self):
        rec = self._spawn_drill_host()
        pgid, run_dir = rec["pgid"], Path(rec["run_dir"])
        self.assertTrue((run_dir / WORKER_LEDGER).is_file(), "账本必须在场")
        self.assertGreaterEqual(len(group_live_members(pgid)), 3)
        # —— DoD：对 Python 宿主（本测试子进程）SIGKILL，渲染途中 ——
        t0 = time.monotonic()
        os.kill(rec["host_pid"], signal.SIGKILL)
        self.hosts[-1].wait(timeout=10)
        time.sleep(max(0.0, 0.105 - (time.monotonic() - t0)))
        self.assertEqual(
            group_live_members(pgid), [],
            "kill -9 宿主 ~100ms 后 Worker 进程树残活必须为 0")
        self.assertEqual(_pgrep_worker_count(), 0,
                         "pgrep -f vtx_cef_worker 计数必须为 0")
        # 宿主暴毙：账本/目录残留（reap 标的）
        self.assertTrue(run_dir.exists())
        stats = reap_orphan_workers(self.ctx())
        self.assertGreaterEqual(stats["ledgers"], 1)
        self.assertGreaterEqual(stats["removed"], 1)
        self.assertFalse(run_dir.exists(), "reap 后临时 root_cache_path 残留=0")
        self.assertEqual(self.tmp_ledger_dirs(), [], "reap 后账本残留=0")


if __name__ == "__main__":
    unittest.main()
