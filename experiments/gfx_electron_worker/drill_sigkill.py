#!/usr/bin/env python3
"""drill_sigkill.py — kill -9 强杀宿主卫生演练（issue #112 DoD；安全铁律内执行）。

演练什么：宿主驱动（run_gfx_electron_worker.py --hold-ms）拉起完整 Electron OSR
进程树（Main+GPU+Zygote+Renderer）渲完首帧后保持存活 → 本脚本对**自己启动的**
宿主进程 os.kill(SIGKILL) → 断言（#103 三重防线）：
  ① 守卫 EOF → killpg：Electron 进程组残活成员 == 0（轮询 /proc，僵尸不计）；
  ② 无任何进程的 cwd/argv 还落在监管运行目录内（防 pgid 外漏网，如自换进程组者）；
  ③ python3 -m vtx.runtime.proc_supervisor reap（账本 starttime+boot_id 核验后 killpg）→
     cache/tmp 下该运行目录被安全移除：临时目录残留 == 0。

安全边界（09:22 事故铁律）：本脚本只 kill 自己 spawn 的宿主子进程（start_new_session=True
隔离子进程内跑全部被测代码）；Electron 树的击杀全部由 vtx.runtime.proc_supervisor 的守卫与
reap 完成（谱系白名单必经）。自造临时项目锚定在系统 /tmp 下的抛弃目录，不碰仓内
cache/。演练成功退出 0 并打印 JSON 证据行 DRILL_RESULT <json>。
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXP_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from vtx.pipeline.infra import Ctx                                        # noqa: E402
from vtx.cache.hygiene import TMP                                      # noqa: E402
from vtx.runtime.proc_supervisor import (WORKER_LEDGER,                  # noqa: E402
                                 group_live_members, reap_orphan_workers)

DRIVER = EXP_DIR / "run_gfx_electron_worker.py"
PAGE_SRC = REPO_ROOT / "templates" / "skill-demo" / "gfxA.html"

# gfxA.html 帧驱动协议（f 纯函数、1920x1080、N=90）——演练页与正式验证同源
TIMELINE_TMPL = {
    "version": 2,
    "project": {"fps": 30, "width": 1920, "height": 1080, "duration_frames": 90},
    "sources": [],
    "video_tracks": [{"name": "gfx-overlay", "kind": "gfx",
                      "clips": [{"src": "gfxA.html", "dst_in": 0, "dur": 90}]}],
    "subtitles": [], "audio_tracks": [],
}


def _proc_open_paths(run_dir: Path) -> int:
    """进程表扫描：cwd 或 argv 仍落在 run_dir 内的进程数（漏网孤儿探针）。"""
    hits = 0
    want = str(run_dir)
    for name in os.listdir("/proc"):
        if not name.isdigit() or name == str(os.getpid()):
            continue
        try:
            cwd = os.readlink(f"/proc/{name}/cwd")
            if cwd == want or cwd.startswith(want + "/"):
                hits += 1
                continue
            with open(f"/proc/{name}/cmdline", "rb") as fh:
                if want.encode() in fh.read():
                    hits += 1
        except OSError:
            continue
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="kill -9 宿主 → Electron 全树归零演练（DoD）")
    ap.add_argument("--mode", default="b0", choices=("b0", "b1"))
    ap.add_argument("--hold-ms", type=int, default=60000,
                    help="宿主/Electron 存活窗口（默认 60s，演练在就绪后立即杀）")
    ap.add_argument("--kill-timeout-s", type=float, default=15.0,
                    help="强杀宿主后等进程组归零的上限")
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="vtx112_drill_") as td:
        root = Path(td)
        (root / "gfxA.html").write_bytes(PAGE_SRC.read_bytes())
        (root / "timeline.json").write_text(json.dumps(TIMELINE_TMPL), encoding="utf-8")
        ready = root / "ready.json"
        log = open(root / "host.log", "wb")

        # 宿主 = 本脚本自起子进程（start_new_session 隔离；只对它发信号）
        host = subprocess.Popen(
            [sys.executable, str(DRIVER), f"--mode={args.mode}",
             "--timeline", str(root / "timeline.json"),
             "--out-root", str(root / "out"), f"--hold-ms={args.hold_ms}",
             "--ready-file", str(ready)],
            start_new_session=True, stdout=log, stderr=subprocess.STDOUT,
            cwd=str(REPO_ROOT))

        # 就绪 = Electron 首帧已渲完 + 全树存活（ready.json 由宿主在 result.json 后写出）
        deadline = time.monotonic() + 180.0
        while time.monotonic() < deadline:
            if ready.is_file():
                try:
                    info = json.loads(ready.read_text(encoding="utf-8"))
                    break
                except (OSError, ValueError):
                    pass
            if host.poll() is not None:
                log.close()
                print(f"✗ 宿主提前退出 rc={host.returncode}；日志：\n"
                      f"{(root / 'host.log').read_text(errors='replace')[-2000:]}",
                      file=sys.stderr)
                return 1
            time.sleep(0.2)
        else:
            host.kill()
            print("✗ 180s 未等到就绪文件", file=sys.stderr)
            return 1

        time.sleep(2.0)                      # 子进程（GPU/Zygote/Renderer）安定
        pgid, run_dir = info["pgid"], Path(info["run_dir"])
        tree_before = group_live_members(pgid)
        ledger = run_dir / WORKER_LEDGER
        assert len(tree_before) >= 1 and ledger.is_file(), \
            f"演练前置失败：进程组成员 {tree_before}，账本 {ledger.is_file()}"

        # —— 强杀宿主（唯一显式 kill 落点：本脚本自起子进程）——
        os.kill(info["host_pid"], signal.SIGKILL)
        host.wait(timeout=10)

        # ① 守卫 EOF → killpg：进程组残活归零
        deadline = time.monotonic() + args.kill_timeout_s
        while time.monotonic() < deadline:
            if not group_live_members(pgid):
                break
            time.sleep(0.1)
        orphans = group_live_members(pgid)
        # ② 漏网探针：cwd/argv 仍在运行目录内的进程
        leaked = _proc_open_paths(run_dir)

        # ③ 账本 reap（starttime+boot_id 核验后击杀/清扫）→ 临时目录残留归零
        stats = reap_orphan_workers(Ctx(root / "timeline.json"))
        residue = [d for d in sorted((root / "cache" / TMP).iterdir())
                   if d.is_dir()] if (root / "cache" / TMP).is_dir() else []
        log.close()

        ok = (not orphans) and leaked == 0 and not residue
        result = {"ok": ok, "mode": args.mode, "pgid": pgid,
                  "host_pid": info["host_pid"], "electron_pid": info["electron_pid"],
                  "tree_members_before_kill": len(tree_before),
                  "orphan_members_after_kill": orphans,
                  "processes_leaking_run_dir": leaked,
                  "run_dir_removed": not run_dir.exists(),
                  "reap_stats": stats, "tmp_residue_dirs": [d.name for d in residue]}
        print(f"DRILL_RESULT {json.dumps(result, ensure_ascii=False)}")
        if not ok:
            print("✗ 卫生演练失败：孤儿/漏网/临时目录残留非零", file=sys.stderr)
            return 1
        print(f"✓ [{args.mode}] kill -9 宿主（pid={info['host_pid']}）后 Electron 全树"
              f"（杀前 {len(tree_before)} 个成员）残活=0、漏网=0、临时目录残留=0"
              f"（reap: killed={stats['killed']} removed={stats['removed']}）")
        return 0


if __name__ == "__main__":
    sys.exit(main())
