#!/usr/bin/env python3
"""probe_display_matrix.py — Linux 三态显示环境 × A0/A1 实测矩阵（issue #109）。

工单矩阵口径（目标机 Linux + RTX 4080，探测为准、绝不伪造）：
  ① env -u DISPLAY -u WAYLAND_DISPLAY（纯无头）
  ② xvfb-run -a（Xvfb 虚拟帧缓冲；无 root 权限时用免安装前缀，见 --xvfb-prefix）
  ③ 真实显示（本机会话 DISPLAY/WAYLAND_DISPLAY 原样继承；GNOME Wayland 下
     DISPLAY=:0 即 XWayland）

每个矩阵格（cell）独立起一个 vtx_cef_worker（隔离会话 + 独立临时
root_cache_path），加载 examples/skill-demo/gfxA.html?f=0 出一张
1920x1080 PNG，并用内部探针（WEBGL_debug_renderer_info + chrome://gpu）
记录真实生效后端；后端可归类时按 vtx/env_manifest 的不可伪造契约落
env_manifest.json（runtime_gpu_info.source=cef）。

证据落档（--out-dir，默认 /tmp/framivra-evidence/issue-109/matrix/）：
  <cell>/result.json          worker 原始结果（含探针证据/计时/退出码）
  <cell>/env_manifest.json    生效后端环境指纹（可归类时）
  <cell>/out.png              渲染产物（--no-render 时无）
  <cell>/worker.log           stdout+stderr 全量
  matrix_summary.json / .md   汇总表

用法：
  python3 experiments/gfx_cef_worker/probe_display_matrix.py [-h] [--worker PATH]
      [--out-dir DIR] [--xvfb-prefix DIR] [--timeout N] [--cells a,b]

独立可跑（不依赖 vtx 包可直接降级为纯矩阵记录，无 manifest）；
默认自动把仓根加入 sys.path 以复用 vtx/env_manifest（证据一致性）。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = Path("/tmp/framivra-evidence/issue-109/matrix")

# 矩阵定义：state = 显示环境三态；mode = a0/a1；ozone = A1 的平台切换。
# ①纯无头下的 a1-x11 / ①②下的 a1-wayland 属「预期失败」探针：显式记录
#   启动行为（工单要的是实测矩阵，不是只跑会成功的组合）。
CELLS = [
    dict(state="1_pure_headless", mode="a0", ozone=None),
    dict(state="1_pure_headless", mode="a1", ozone="headless"),
    dict(state="1_pure_headless", mode="a1", ozone="x11"),
    dict(state="2_xvfb", mode="a0", ozone=None),
    dict(state="2_xvfb", mode="a1", ozone="x11"),
    dict(state="2_xvfb", mode="a1", ozone="headless"),
    dict(state="2_xvfb", mode="a1", ozone="wayland"),
    dict(state="3_real_display", mode="a0", ozone=None),
    dict(state="3_real_display", mode="a1", ozone="x11"),
    dict(state="3_real_display", mode="a1", ozone="wayland"),
]


def cell_name(cell: dict) -> str:
    ozone = cell["ozone"] or ("-" if cell["mode"] == "a0" else "default")
    return f"{cell['state']}__{cell['mode']}__{ozone}"


def log(msg: str) -> None:
    print(f"[matrix] {msg}", flush=True)


def build_cmd(cell: dict, worker: Path, xvfb_prefix: Path):
    """组装 cell 命令行（env 三态 + 模式开关），返回 (argv, env)。"""
    argv = []
    env = dict(os.environ)
    state = cell["state"]
    if state == "1_pure_headless":
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
    elif state == "2_xvfb":
        xvfb_run = xvfb_prefix / "usr/bin/xvfb-run"
        argv += [str(xvfb_run), "-a", "-s", "-screen 0 1920x1080x24"]
        if xvfb_prefix != Path("/"):
            env["LD_LIBRARY_PATH"] = os.pathsep.join(
                p for p in (str(xvfb_prefix / "usr/lib64"),
                            env.get("LD_LIBRARY_PATH", "")) if p)
    # ③ 真实显示：继承本机会话 DISPLAY/WAYLAND_DISPLAY（探测为准）

    cmd = [str(worker), f"--mode={cell['mode']}", "--probe-gpu-page",
           "--timeout-ms=30000"]
    if cell["ozone"]:
        cmd.append(f"--ozone-platform={cell['ozone']}")
    argv += cmd
    return argv, env


def classify(result: dict) -> str:
    """worker 结果 → 生效后端（vtx.env_manifest 词表）；不可得返回 ""。"""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from vtx.env_manifest import classify_gl_backend
    except Exception:
        return ""  # 降级：纯矩阵记录
    probe = result.get("page_probe") or {}
    webgl = probe.get("webgl") or {}
    return classify_gl_backend(webgl.get("renderer") or "",
                               "")


def run_cell(cell: dict, args) -> dict:
    name = cell_name(cell)
    out_dir = args.out_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / "out.png"
    result_json = out_dir / "result.json"
    log_path = out_dir / "worker.log"

    url = (f"file://{REPO_ROOT}/examples/skill-demo/gfxA.html?f=0")
    argv, env = build_cmd(cell, Path(args.worker), Path(args.xvfb_prefix))
    argv += [f"--url={url}", f"--out-png={png}",
             f"--out-json={result_json}",
             f"--root-cache-path={out_dir / 'cef_cache'}"]

    t0 = time.monotonic()
    # 安全铁律：worker 一律隔离会话（自成进程组），超时只组杀该子组。
    log(f"[{name}] 启动: {' '.join(argv[:6])}…")
    with log_path.open("wb") as lf:
        try:
            proc = subprocess.Popen(argv, stdout=lf, stderr=subprocess.STDOUT,
                                    env=env, start_new_session=True,
                                    cwd=str(out_dir))
            try:
                rc = proc.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                rc = 124
        except OSError as e:
            log_path.write_text(f"启动失败: {e}\n", encoding="utf-8")
            rc = 127
    elapsed = round(time.monotonic() - t0, 2)

    result = json.loads(result_json.read_text(encoding="utf-8")) \
        if result_json.is_file() else None
    backend = classify(result) if result else ""
    summary = {
        "cell": name,
        "state": cell["state"],
        "mode": cell["mode"],
        "ozone_platform": cell["ozone"],
        "worker_rc": rc,
        "elapsed_s": elapsed,
        "png_valid": png.is_file() and png.stat().st_size > 0,
        "effective_backend": backend or "(不可归类)",
        "webgl_renderer": _renderer_of(result),
        "gpu_feature_excerpt": _gpu_features(result),
        "worker_error": (result or {}).get("error", ""),
        "worker_exit": (result or {}).get("exit", ""),
    }

    # 生效后端环境指纹（env_manifest 不可伪造契约：内部接口实测值才入档）
    if result and backend:
        manifest = _collect_manifest(cell, result, backend)
        if manifest is not None:
            (out_dir / "env_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2,
                           sort_keys=True), encoding="utf-8")
            summary["env_manifest"] = "written"
        else:
            summary["env_manifest"] = "collect_failed"
    else:
        summary["env_manifest"] = "skipped"
    log(f"[{name}] rc={rc} {elapsed}s backend={summary['effective_backend']} "
        f"png={summary['png_valid']} err={summary['worker_error'][:80]}")
    return summary


def _kill_group(proc: subprocess.Popen) -> None:
    """只杀本脚本自起的子进程组（安全铁律：killpg 限自起组）。"""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _renderer_of(result) -> str:
    try:
        return ((result.get("page_probe") or {}).get("webgl") or {}) \
            .get("renderer") or ""
    except AttributeError:
        return ""


def _gpu_features(result) -> dict:
    try:
        feats = ((result.get("gpu_page_probe") or {}).get("features")) or {}
        keys = ("Canvas", "Compositing", "Rasterization", "WebGL", "WebGPU",
                "OpenGL", "Vulkan", "Skia Graphite")
        return {k: v for k, v in feats.items() if k in keys}
    except AttributeError:
        return {}


def _collect_manifest(cell: dict, result: dict, backend: str):
    try:
        from vtx import env_manifest
    except Exception:
        return None
    probe = result.get("page_probe") or {}
    gl_probe = {
        "webgl": probe.get("webgl") or {},
        "webgpu": probe.get("webgpu") or {},
        "chrome_gpu_features": _gpu_features(result),
        "ozone_platform": cell["ozone"] or ("(a0 default)" if
                                            cell["mode"] == "a0" else ""),
    }
    runtime_gpu_info = {
        "effective_backend": backend,
        "source": "cef",
        "evidence": {
            "cef_version": {
                "cef": result.get("cef_version"),
                "chromium": result.get("chromium_version"),
            },
            "gl_probe": gl_probe,
        },
        "chromium_version": result.get("chromium_version"),
        "viewport": {"width": 1920, "height": 1080},
        "color_space": "srgb",
    }
    # display_server 指纹按 worker 实测环境采样（格②的 DISPLAY 由 xvfb-run
    # 动态分配、格①显式置空——脚本自身环境不代表 cell），临时切换进程 env。
    worker_env = result.get("env") or {}
    saved = {k: os.environ.get(k) for k in ("DISPLAY", "WAYLAND_DISPLAY")}
    try:
        for key in ("DISPLAY", "WAYLAND_DISPLAY"):
            value = worker_env.get(key) or ""
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        manifest = env_manifest.collect_env_manifest(
            f"cef-route-a-{cell['mode']}", runtime_gpu_info)
        return manifest
    except ValueError as e:
        log(f"manifest 采集拒绝: {e}")
        return None
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)


def write_summary(summaries, out_dir: Path) -> None:
    (out_dir / "matrix_summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["| 矩阵格 | rc | 耗时s | PNG | 生效后端 | WebGL renderer |",
             "|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s['cell']} | {s['worker_rc']} | {s['elapsed_s']} | "
            f"{'✓' if s['png_valid'] else '✗'} | {s['effective_backend']} "
            f"| {s['webgl_renderer'][:60]} |")
    (out_dir / "matrix_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="issue #109 Linux 三态显示环境矩阵探测")
    parser.add_argument("--worker", default=str(
        Path(__file__).resolve().parent / "build/bin/vtx_cef_worker"))
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--xvfb-prefix", type=Path, default=Path("/tmp/xvfb-dl/root"),
                        help="免安装 Xvfb 解包前缀（含 usr/bin/xvfb-run）")
    parser.add_argument("--timeout", type=int, default=90,
                        help="单格 worker 超时秒数")
    parser.add_argument("--cells", default="",
                        help="逗号分隔的 state 过滤（如 1_pure_headless）")
    args = parser.parse_args(argv)

    if not Path(args.worker).is_file():
        print(f"worker 不存在: {args.worker}（先跑 build_cef_worker.sh）",
              file=sys.stderr)
        return 1
    xvfb_run = args.xvfb_prefix / "usr/bin/xvfb-run"
    if not xvfb_run.is_file():
        print(f"xvfb-run 不存在: {xvfb_run}（矩阵②需要；--xvfb-prefix 指定前缀）",
              file=sys.stderr)
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)

    cells = CELLS
    if args.cells:
        want = {x.strip() for x in args.cells.split(",") if x.strip()}
        cells = [c for c in CELLS if c["state"] in want]
    log(f"显示环境: DISPLAY={os.environ.get('DISPLAY')!r} "
        f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY')!r} "
        f"XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE')!r}")
    log(f"矩阵 {len(cells)} 格 → {args.out_dir}")

    summaries = [run_cell(cell, args) for cell in cells]
    write_summary(summaries, args.out_dir)
    log(f"完成：{args.out_dir}/matrix_summary.json|.md")
    for s in summaries:
        print(f"{s['cell']:<42} rc={s['worker_rc']:<4} "
              f"png={'Y' if s['png_valid'] else 'N'} "
              f"backend={s['effective_backend']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
