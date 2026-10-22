#!/usr/bin/env python3
"""run_gfx_electron_worker.py — gfx_electron_worker 的 vtx 宿主驱动（issue #112 ELEC-01）。

职责（issue #112「接入 vtx/runtime/proc_supervisor.py」工单项的 Python 侧）：
  1. 以 vtx.pipeline.infra.Ctx 锚定项目 cache/，用 vtx.runtime.proc_supervisor.SupervisedProcessGroup
     拉起 Electron 宿主（backend_id="gfx_elec" → 运行目录 cache/tmp/<UTC>-gfx_elec-*/，
     userData 即其中的 profile/，与工单「app.setPath('userData', cache/tmp/<UTC>-gfx_elec/)」
     同一落点族）；
  2. 等 main.mjs 渲完 gfxA.html?f=0 首帧并落盘 frame_f0000.png / truth.json / result.json；
  3. 用 truth.json 的内部接口实测值（getGPUFeatureStatus + getGPUInfo('complete') +
     process.versions 三元组）构造 runtime_gpu_info，经 vtx.inspection.env_manifest 生成
     env_manifest.json（vtx.env_manifest/1 形态，供 gfx_golden 跨后端比对门禁消费）。

用法：
  python3 experiments/gfx_electron_worker/run_gfx_electron_worker.py --mode=b0 \
      [--timeline PATH] [--page PATH] [--frame 0] [--out-root DIR]
      [--hold-ms N --ready-file PATH]      # 卫生演练：渲完首帧后全树存活等待强杀
      [--timeout-s 120]

安全铁律：本驱动只经 SupervisedProcessGroup 启动/回收**自起** Electron 进程树；
对宿主自身的强杀演练由 drill_sigkill.py 在隔离子进程内执行。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXP_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT))   # vtx 包导入（驱动可从任意 cwd 运行）

from vtx.inspection import env_manifest                                   # noqa: E402
from vtx.pipeline.infra import Ctx                                       # noqa: E402
from vtx.runtime.proc_supervisor import SupervisedProcessGroup          # noqa: E402

DEFAULT_TIMELINE = REPO_ROOT / "templates" / "skill-demo" / "timeline.json"
DEFAULT_PAGE = REPO_ROOT / "templates" / "skill-demo" / "gfxA.html"


def electron_bin() -> Path:
    """dist/electron 直取（.bin/electron 是包装脚本，多一层 exec 无收益）。"""
    for cand in (EXP_DIR / "node_modules" / "electron" / "dist" / "electron",
                 EXP_DIR / "node_modules" / ".bin" / "electron"):
        if cand.is_file():
            return cand
    sys.exit("✗ Electron 二进制缺失：先在 experiments/gfx_electron_worker/ 下执行\n"
             "  env ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ "
             "npm install --registry=https://registry.npmmirror.com\n"
             "（详见 experiments/gfx_electron_worker/README.md）")


def _wait_result_file(out_dir: Path, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        f = out_dir / "result.json"
        if f.is_file():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass                      # 写到一半：继续轮询（main.mjs 原子性有限，容忍）
        time.sleep(0.1)
    return {}


def _tail(path: Path, n: int = 40) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace")
                         .splitlines()[-n:])
    except OSError:
        return "<不可读>"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", required=True, choices=("b0", "b1"),
                    help="b0=Software output device（禁硬件加速）；b1=GPU 加速→CPU 共享内存位图")
    ap.add_argument("--timeline", type=Path, default=DEFAULT_TIMELINE,
                    help="项目锚点 timeline.json（cache/tmp 落其项目内；默认 skill-demo）")
    ap.add_argument("--page", type=Path, default=DEFAULT_PAGE,
                    help="要离屏渲染的 gfx 页面（默认 templates/skill-demo/gfxA.html）")
    ap.add_argument("--frame", type=int, default=0, help="帧号 f（gfx 协议纯函数入参）")
    ap.add_argument("--out-root", type=Path, default=Path.cwd(),
                    help="产物根目录（frame PNG / truth / env_manifest 落其 <mode>_<UTC>/ 子目录）")
    ap.add_argument("--hold-ms", type=int, default=0,
                    help="渲完首帧后 Electron 全树存活等待时长（kill -9 卫生演练用）")
    ap.add_argument("--ready-file", type=Path, default=None,
                    help="--hold-ms 时写出就绪信息（run_dir/pgid/host_pid/electron_pid）")
    ap.add_argument("--timeout-s", type=float, default=120.0, help="首帧+退出整体超时")
    args = ap.parse_args(argv)

    c = Ctx(args.timeline)
    out_dir = args.out_root / f"{args.mode}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    out_dir.mkdir(parents=True, exist_ok=True)
    bin_path = electron_bin()

    stdout_log = open(out_dir / "electron.stdout.log", "wb")
    stderr_log = open(out_dir / "electron.stderr.log", "wb")

    def worker_argv(run_dir: Path, profile_dir: Path):
        # #103 契约：SupervisedProcessGroup 会在尾部追加 --parent-alive-fd=<fd>
        return [str(bin_path), str(EXP_DIR / "main.mjs"),
                f"--mode={args.mode}", f"--page={args.page}",
                f"--frame={args.frame}", f"--out-dir={out_dir}",
                f"--user-data-dir={profile_dir}",
                f"--timeout-ms={int(args.timeout_s * 1000)}",
                f"--hold-ms={args.hold_ms}"]

    with SupervisedProcessGroup(c, worker_argv, backend_id="gfx_elec",
                                stdout=stdout_log, stderr=stderr_log,
                                env=dict(os.environ)) as sg:
        result = _wait_result_file(out_dir, args.timeout_s if args.hold_ms else max(args.timeout_s, 1.0))
        if not result:
            stderr_log.flush()
            print(f"✗ Electron 未在 {args.timeout_s}s 内产出 result.json；"
                  f"stderr 尾部：\n{_tail(out_dir / 'electron.stderr.log')}", file=sys.stderr)
            return 1
        if args.hold_ms > 0:
            if args.ready_file:
                args.ready_file.write_text(json.dumps({
                    "run_dir": str(sg.run_dir), "pgid": sg.pgid,
                    "host_pid": os.getpid(), "electron_pid": sg.proc.pid,
                    "out_dir": str(out_dir), "mode": args.mode}), encoding="utf-8")
            # 全树存活窗口：宿主在此被 SIGKILL（drill_sigkill.py），或在超时后正常收尾
            deadline = time.monotonic() + (args.hold_ms / 1000.0) + args.timeout_s
            while sg.proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.2)
            return 0
        rc = sg.proc.wait(timeout=30)

    stdout_log.close()
    stderr_log.close()
    if rc != 0:
        print(f"✗ Electron 退出码 {rc}；stderr 尾部：\n"
              f"{_tail(out_dir / 'electron.stderr.log')}", file=sys.stderr)
        return 1
    if not result.get("ok"):
        print(f"✗ result.ok=false：{result}", file=sys.stderr)
        return 1

    # 首帧产物核验（DoD：B0/B1 截取 gfxA.html?f=0 首帧成功）
    png_path = out_dir / result["png"]
    png_bytes = png_path.read_bytes()
    sha = hashlib.sha256(png_bytes).hexdigest()
    if len(png_bytes) != result["png_bytes"] or sha != result["png_sha256"]:
        print(f"✗ 首帧 PNG 校验不符：bytes {len(png_bytes)}≠{result['png_bytes']} "
              f"或 sha256 不匹配", file=sys.stderr)
        return 1

    # 版本三元组 + GPU 内部接口实测 → env_manifest（issue #102 形态）
    truth = json.loads((out_dir / "truth.json").read_text(encoding="utf-8"))
    v, gpu = truth["versions"], truth["gpu"]
    runtime_info = {
        "effective_backend": gpu["effective_backend"],
        "source": "electron",
        "evidence": {
            "gpu_feature_status": gpu["gpu_feature_status"],      # 内部接口实测（必键）
            "gpu_info_complete": gpu["gpu_info_complete"],        # 内部接口实测（必键）
            "gpu_info_error": gpu.get("gpu_info_error"),
            "electron_version": v["electron"],                    # process.versions 三元组
            "chrome_version": v["chrome"],
            "v8_version": v["v8"],
            "node_version": v["node"],
            "page_device_pixel_ratio": (truth.get("page_report") or {}).get("devicePixelRatio"),
            "classify_inputs": gpu.get("classify_inputs"),
            "mode": truth["mode"],
        },
        "chromium_version": v["chrome"],
        "viewport": {"width": truth["viewport"]["width"],
                     "height": truth["viewport"]["height"]},
        "color_space": truth["color_space"],
    }
    if runtime_info["effective_backend"] not in env_manifest.RUNTIME_BACKENDS:
        print(f"✗ 生效后端 {runtime_info['effective_backend']!r} 不在词表 "
              f"{env_manifest.RUNTIME_BACKENDS}——拒绝生成 manifest（不编造）", file=sys.stderr)
        return 1
    manifest = env_manifest.collect_env_manifest(f"electron-route-b-{args.mode}",
                                                 runtime_info)
    env_manifest.write_manifest(out_dir / "env_manifest.json", manifest)

    t = manifest["runtime_backend_truth"]
    print(f"✓ [{args.mode}] 首帧 {result['png']} ({result['width']}x{result['height']}, "
          f"{result['png_bytes']}B, sha256={sha[:16]}…) "
          f"electron={v['electron']} chrome={v['chrome']} v8={v['v8']} "
          f"effective_backend={t['effective_backend']} "
          f"({result['elapsed_ms']}ms)\n  产物目录：{out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
