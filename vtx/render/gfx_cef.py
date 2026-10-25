"""Experimental CEF Alpha: explicit native exact capture, no fallback.

Trusted local frame-driven HTML only: the native worker disables Chromium's
sandbox. Worker trees, profiles, and scratch files stay under supervision.
"""
import errno
import json
import os
import secrets
import socket
import sys
import time
from itertools import islice
from pathlib import Path

from ..runtime import events, fstxn
from . import gfx_cli
from .cef_client import (CefWorkerClient, CefWorkerError, HELLO_FLAG_PAIRED_EXACT,
                         MAX_VIEWPORT_SIZE)
from .gfx_backend import cef_config, cef_runtime_options
from ..runtime.proc_supervisor import SupervisedProcessGroup, supervision_available


def _selected_frames(n_frames, frames):
    if type(n_frames) is not int or not 0 <= n_frames <= 65536:
        raise ValueError("CEF exact protocol requires an integer frame count in 0..65536")
    # Reject oversized/infinite iterables without materializing more than the
    # protocol's frame bound plus one sentinel element.
    selected = list(range(n_frames) if frames is None else islice(frames, n_frames + 1))
    if (any(type(f) is not int or not 0 <= f < n_frames for f in selected)
            or selected != sorted(set(selected))):
        raise ValueError("CEF frames must be sorted unique integer indices in 0..n_frames-1")
    return selected


def _commit_png(source, target, *, root):
    """Publish an already validated, owned native file without copying its bytes.

    Native output and graphics caches normally share cache/ and one filesystem.
    Keep the previous safe atomic-write behavior for an explicitly mounted
    destination on another filesystem; never fall back for any other error.
    A consumed buffer is always removed before that basename can be reused.
    """
    # The old atomic_write destination was private (0600); native fopen uses
    # the worker umask. Preserve the cache permission contract on the owned
    # source inode before publishing it, without following a substituted leaf.
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    try:
        fstxn.replace(source, target, root=root)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        fstxn.atomic_write(target, source.read_bytes(), root=root)
        fstxn.unlink(source, root=root)
        return "copy"
    return "rename"


def render_page(html, outdir, n_frames, *, ctx, width, height, frames=None):
    block_started = time.perf_counter_ns()
    worker, mode = cef_config()
    options = cef_runtime_options()
    selected = _selected_frames(n_frames, frames)
    if not supervision_available():
        raise RuntimeError("CEF Alpha requires verified process-tree supervision")
    if (type(width) is not int or type(height) is not int
            or not 16 <= width <= MAX_VIEWPORT_SIZE
            or not 16 <= height <= MAX_VIEWPORT_SIZE):
        raise ValueError(f"CEF exact protocol requires integer viewport dimensions in 16..{MAX_VIEWPORT_SIZE}")
    html, outdir = Path(html).resolve(), Path(outdir)
    # Do not start a native tree for an empty/already valid selected block.
    # This is full PNG validation, not a count or signature-only cache shortcut.
    validation_started = time.perf_counter_ns()
    missing = [f for f in selected if gfx_cli.png_invalid_reason(
        outdir / f"f{f:04d}.png", width=width, height=height) is not None]
    cached_validated = time.perf_counter_ns()
    label = f"CEF Alpha {mode} Mode {options.sync_mode.upper()} native-exact"
    if not missing:
        return f"{label}×0 (no fallback)"
    report = ctx.cache / "reports" / "cef"
    fstxn.mkdir(report, root=ctx.root)
    key, receipts, descriptors = f"{outdir.name}-{secrets.token_hex(4)}", [], []
    execution, observation = events.context(ctx), events.current(ctx)
    pipeline = {"schema": "vtx.cef-host-pipeline/1", "frames": selected,
                "execution": {"run_id": getattr(execution, "run_id", None)},
                "task": {"task_id": getattr(observation, "task_id", None),
                         "attempt_no": getattr(observation, "attempt_no", None)},
                "clock": "python.perf_counter_ns", "block_started_counter_us": block_started // 1000,
                "rendered_frames": [], "worker_options": options.to_dict(),
                "max_native_inflight": 1, "output_buffers": 2,
                "native_wait_us": 0, "native_receive_attempts": 0,
                "host_validate_us": 0, "host_commit_us": 0,
                "cached_validate_us": (cached_validated - validation_started) // 1000,
                "final_validate_us": None, "worker_startup_us": None,
                "request_roundtrips": [], "completed": False,
                "request_wall_scope": "submit to receive; includes overlapped host work, not native CPU time",
                "native_wait_scope": "host receive-call wall including protocol parsing; never native CPU or stage wall time",
                "wall_scope": "block through validation/cleanup; excludes final diagnostic write",
                "output_commits": {"rename": 0, "copy": 0}}
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
        # A resident Mode P page receives f through __vtx_render; changing its
        # URL would invalidate both signed/unsigned persistent document identity.
        url = html.as_uri() + ("?f={f}" if options.sync_mode == "n" else "?f=0")
        argv = [str(worker), f"--mode={mode}", "--serve", "--sync",
                *options.argv(), "--sync-canary=exact", "--sync-log=sync.jsonl",
                "--ipc-socket=ipc.sock", f"--root-cache-path={profile}",
                f"--url-template={url}",
                f"--width={width}", f"--height={height}"]
        if pair:
            argv.remove("--ipc-socket=ipc.sock")
            argv.append(f"--ipc-fd={pair[1].fileno()}")
        if mode == "a0" and not pair:
            argv.append("--ozone-platform=headless")
        return argv

    failure = operation_error = None
    try:
        # Preserve native logs on FAILURE as well as success. No unread pipes;
        # staging protects report destinations against symlink substitution.
        try:
            with fstxn.locked_staging(report / f"{key}.worker.log", root=ctx.root) as logpath:
                try:
                    with open(logpath, "wb") as log:
                        worker_started = time.perf_counter_ns()
                        with SupervisedProcessGroup(ctx, argv, backend_id=f"cef_{mode}",
                                                    stdout=log, stderr=log, env=worker_env,
                                                    pass_fds=(pair[1].fileno(),) if pair else ()) as group:
                            session_error = None
                            try:
                                try:
                                    if pair:
                                        pair[1].close()
                                        connection = CefWorkerClient.from_socket(pair[0], ready_timeout=45)
                                    else:
                                        sock = f"/proc/{os.getpid()}/fd/{descriptors[-1]}/ipc.sock"
                                        connection = CefWorkerClient.connect(sock, connect_timeout=45)
                                finally:
                                    pipeline["worker_startup_us"] = (time.perf_counter_ns() - worker_started) // 1000
                                with connection as client:
                                    if client.hello["pid"] != group.proc.pid:
                                        raise CefWorkerError("Hello PID does not match supervised worker")
                                    pipeline["worker_hello"] = dict(client.hello)
                                    if not client.hello.get("flags", 0) & HELLO_FLAG_PAIRED_EXACT:
                                        raise CefWorkerError(
                                            "CEF worker lacks paired-exact Hello capability; "
                                            "rebuild VTX_CEF_WORKER from this revision (no fallback)")
                                    ctx.verify_inputs("gfx")
                                    # One native request plus one completed host file:
                                    # never assume native request parallelism, and never
                                    # overwrite the file currently being validated.
                                    request_started = time.perf_counter_ns()
                                    client.submit_png(
                                        missing[0], "frame0.png", request_id=missing[0] + 1,
                                        expected_wh=(width, height), worker_relative=True)
                                    for index, f in enumerate(missing):
                                        started = time.perf_counter_ns()
                                        pipeline["native_receive_attempts"] += 1
                                        try:
                                            receipt = client.receive_png()
                                        finally:
                                            received = time.perf_counter_ns()
                                            pipeline["native_wait_us"] += (received - started) // 1000
                                        timing = {"frame": f, "wall_us": (received - request_started) // 1000,
                                                  "submit_offset_us": (request_started - block_started) // 1000,
                                                  "receive_offset_us": (received - block_started) // 1000,
                                                  "committed": False}
                                        pipeline["request_roundtrips"].append(timing)
                                        h = receipt.header
                                        if (h["canary_frame_index"] != f or h["width"] != width
                                                or h["height"] != height or h["stride"] != width * 4
                                                or (h["pixel_format"], h["color_space"]) != (1, 1)):
                                            raise CefWorkerError("verified frame geometry/canary/pixel format mismatch")
                                        if index + 1 < len(missing):
                                            next_frame = missing[index + 1]
                                            request_started = time.perf_counter_ns()
                                            client.submit_png(
                                                next_frame, f"frame{(index + 1) % 2}.png",
                                                request_id=next_frame + 1, expected_wh=(width, height),
                                                worker_relative=True)
                                        source = group.run_dir / f"frame{index % 2}.png"
                                        started = time.perf_counter_ns()
                                        timing["validate_start_offset_us"] = (started - block_started) // 1000
                                        try:
                                            reason = gfx_cli.png_invalid_reason(source, width=width, height=height)
                                        finally:
                                            validated = time.perf_counter_ns()
                                            pipeline["host_validate_us"] += (validated - started) // 1000
                                            timing["validate_end_offset_us"] = (validated - block_started) // 1000
                                        if reason:
                                            # A speculative next frame remains private.
                                            # Context exit closes its stream and the
                                            # supervisor terminates the owned tree.
                                            raise CefWorkerError(f"CEF output invalid: {reason}")
                                        started = time.perf_counter_ns()
                                        timing["commit_start_offset_us"] = (started - block_started) // 1000
                                        try:
                                            method = _commit_png(source, outdir / f"f{f:04d}.png", root=ctx.root)
                                        finally:
                                            committed = time.perf_counter_ns()
                                            pipeline["host_commit_us"] += (committed - started) // 1000
                                            timing["commit_end_offset_us"] = (committed - block_started) // 1000
                                        timing["committed"] = True
                                        pipeline["output_commits"][method] += 1
                                        pipeline["rendered_frames"].append(f)
                                        receipts.append(h)
                                rc = group.proc.wait(timeout=15)
                                if rc:
                                    raise CefWorkerError(f"CEF worker exited {rc}; see {report}")
                            except BaseException as error:
                                session_error = error
                                raise
                            finally:
                                try:
                                    sync = group.run_dir / "sync.jsonl"
                                    if sync.is_file():
                                        fstxn.atomic_write(report / f"{key}.sync.jsonl", sync.read_bytes(), root=ctx.root)
                                    fstxn.atomic_write(report / f"{key}.receipts.json", json.dumps(receipts, indent=2), root=ctx.root)
                                except Exception as diagnostic_error:
                                    if session_error is None:
                                        raise
                                    session_error.args = (f"{session_error}; CEF native evidence failed: {diagnostic_error}",
                                                          *session_error.args[1:])
                except Exception as error:
                    failure = error
        except Exception as diagnostic_error:
            if failure is None:
                raise
            failure.args = (f"{failure}; CEF worker log commit failed: {diagnostic_error}",
                            *failure.args[1:])
        if failure is not None:
            raise CefWorkerError(f"{failure}; native logs: {report}",
                                 status_code=getattr(failure, "status_code", None),
                                 detail=getattr(failure, "detail", None)) from failure
        # Only the selected block is checked here. The shared page coordinator
        # retains whole-page/input-fingerprint certification after all blocks.
        started = time.perf_counter_ns()
        try:
            gfx_cli.require_frames(outdir, selected, width=width, height=height)
        finally:
            pipeline["final_validate_us"] = (time.perf_counter_ns() - started) // 1000
        pipeline["completed"] = True
    except BaseException as error:
        operation_error = error
        raise
    finally:
        if pair:
            for endpoint in pair:
                endpoint.close()
        for fd in descriptors:
            os.close(fd)
        pipeline["block_wall_us"] = (time.perf_counter_ns() - block_started) // 1000
        try:
            fstxn.atomic_write(report / f"{key}.pipeline.json", json.dumps(pipeline, indent=2), root=ctx.root)
        except Exception as diagnostic_error:
            if operation_error is None:
                raise
            # Python 3.9-compatible note: preserve the original exception type,
            # status and cause when optional performance evidence cannot be saved.
            operation_error.args = (f"{operation_error}; CEF pipeline diagnostic failed: {diagnostic_error}",
                                    *operation_error.args[1:])
    return f"{label}×{len(receipts)} host-pipeline×2 (no fallback)"
