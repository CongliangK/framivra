"""Experimental CEF Alpha: explicit Mode N + native exact capture, no fallback.

Trusted local frame-driven HTML only: the native worker disables Chromium's
sandbox. Worker trees, profiles, and scratch files stay under supervision.
"""
import json
import os
import secrets
import socket
import sys
from pathlib import Path

from . import fstxn, gfx_cli
from .cef_client import CefWorkerClient, CefWorkerError
from .gfx_backend import cef_config, cef_png_config
from .proc_supervisor import SupervisedProcessGroup, supervision_available


def render_page(html, outdir, n_frames, *, ctx, width, height):
    worker, mode = cef_config()
    png_encoder, png_level, png_filter = cef_png_config()
    if not supervision_available():
        raise RuntimeError("CEF Alpha requires verified process-tree supervision")
    if n_frames > 65536 or width < 16 or height < 16:
        raise ValueError("CEF exact protocol requires <=65536 frames and viewport >=16x16")
    html, outdir = Path(html).resolve(), Path(outdir)
    report = ctx.cache / "reports" / "cef"
    fstxn.mkdir(report)
    key, receipts, descriptors = f"{outdir.name}-{secrets.token_hex(4)}", [], []
    worker_env = dict(os.environ)
    pair = socket.socketpair() if sys.platform == 'darwin' else None
    if pair:
        # Native socketpair avoids AF_UNIX pathname limits, cwd mutation and
        # external scratch aliases. Shared memory still travels via SCM_RIGHTS.
        worker_env["TMPDIR"] = str(outdir.parent)


    def argv(run, profile):
        # AF_UNIX and Chromium's own scratch sockets have short path limits.
        # Parent-owned fd aliases support arbitrarily long/Unicode project roots
        # while retaining ALL scratch bytes in supervised cache/tmp (not /tmp).
        fd = os.open(run, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        worker_env["TMPDIR"] = str(run) if pair else f"/proc/{os.getpid()}/fd/{fd}"
        # A0 是软件无头 OSR：显式钉 ozone=headless，禁止 Chromium 在
        # Wayland 会话自动选 wayland（wl_display roundtrip 可无限阻塞，
        # 使 A0 沦为环境依赖的超时）。A1 保留显式平台参数语义不变。
        argv = [str(worker), f"--mode={mode}", "--serve", "--sync",
                "--sync-mode=n", "--sync-canary=exact", "--sync-log=sync.jsonl",
                "--ipc-socket=ipc.sock", f"--root-cache-path={profile}",
                f"--url-template={html.as_uri()}?f={{f}}",
                f"--width={width}", f"--height={height}",
                f"--png-encoder={png_encoder}"]
        if png_encoder == "spng":
            argv.extend((f"--png-level={png_level}", f"--png-filter={png_filter}"))
        if pair:
            argv.remove("--ipc-socket=ipc.sock")
            argv.append(f"--ipc-fd={pair[1].fileno()}")
        if mode == "a0" and not pair:
            argv.append("--ozone-platform=headless")
        return argv

    failure = None
    try:
        # Preserve native logs on FAILURE as well as success. No unread pipes;
        # staging protects report destinations against symlink substitution.
        with fstxn.locked_staging(report / f"{key}.worker.log") as logpath:
            try:
                with open(logpath, "wb") as log:
                    with SupervisedProcessGroup(ctx, argv, backend_id=f"cef_{mode}",
                                                stdout=log, stderr=log, env=worker_env,
                                                pass_fds=(pair[1].fileno(),) if pair else ()) as group:
                        try:
                            if pair:
                                pair[1].close()
                                connection = CefWorkerClient.from_socket(pair[0], ready_timeout=45)
                            else:
                                sock = f"/proc/{os.getpid()}/fd/{descriptors[-1]}/ipc.sock"
                                connection = CefWorkerClient.connect(sock, connect_timeout=45)
                            with connection as client:
                                if client.hello["pid"] != group.proc.pid:
                                    raise CefWorkerError("Hello PID does not match supervised worker")
                                ctx.verify_inputs("gfx")
                                for f in range(n_frames):
                                    target = outdir / f"f{f:04d}.png"
                                    if gfx_cli.png_invalid_reason(target, width=width, height=height) is None:
                                        continue
                                    receipt = client.request_png(
                                        f, "frame.png", request_id=f + 1,
                                        expected_wh=(width, height), worker_relative=True)
                                    h = receipt.header
                                    if (h["canary_frame_index"] != f or h["width"] != width
                                            or h["height"] != height or h["stride"] != width * 4
                                            or h["pixel_format"] != 1):
                                        raise CefWorkerError("verified frame geometry/canary/pixel format mismatch")
                                    source = group.run_dir / "frame.png"
                                    reason = gfx_cli.png_invalid_reason(source, width=width, height=height)
                                    if reason:
                                        raise CefWorkerError(f"CEF output invalid: {reason}")
                                    # Page completion rechecks pinned inputs before
                                    # certification (same gate as CDP, not per-frame
                                    # project-wide media sampling at production scale).
                                    fstxn.atomic_write(target, source.read_bytes())
                                    receipts.append(h)
                            rc = group.proc.wait(timeout=15)
                            if rc:
                                raise CefWorkerError(f"CEF worker exited {rc}; see {report}")
                        finally:
                            sync = group.run_dir / "sync.jsonl"
                            if sync.is_file():
                                fstxn.atomic_write(report / f"{key}.sync.jsonl", sync.read_bytes())
                            fstxn.atomic_write(report / f"{key}.receipts.json", json.dumps(receipts, indent=2))
            except Exception as error:
                failure = error
        if failure is not None:
            raise CefWorkerError(f"{failure}; native logs: {report}") from failure
    finally:
        if pair:
            for endpoint in pair:
                endpoint.close()
        for fd in descriptors:
            os.close(fd)
    gfx_cli.require_frames(outdir, range(n_frames), width=width, height=height)
    return f"CEF Alpha {mode} Mode N native-exact×{len(receipts)} (no fallback)"
