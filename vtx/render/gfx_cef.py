"""Experimental CEF Alpha: explicit native exact capture, no fallback.

Trusted local frame-driven HTML only: the native worker disables Chromium's
sandbox. Worker trees, profiles, and scratch files stay under supervision.
"""
import ctypes as C
import errno
import json
import os
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from ctypes import wintypes as W
from itertools import islice
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from uuid import UUID

from ..runtime import events, fstxn
from . import gfx_cli
from .cef_client import (CefWorkerClient, CefWorkerError, HELLO_FLAG_PAIRED_EXACT,
                         MAX_VIEWPORT_SIZE, _WindowsPipes, _check_windows_cancel)
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


def _windows_local_appdata():
    # FOLDERID_LocalAppData from the Shell, not TMP/USERPROFILE inherited from
    # a caller. CEF/Chromium can still use MAX_PATH-limited profile APIs.
    folder_id = (C.c_ubyte * 16).from_buffer_copy(UUID('F1B32785-6FBA-4FCF-9D55-7B8E7F157091').bytes_le)
    known_folder = C.WinDLL('shell32').SHGetKnownFolderPath
    known_folder.argtypes = (C.c_void_p, W.DWORD, W.HANDLE, C.POINTER(C.c_void_p))
    known_folder.restype = W.LONG
    free = C.WinDLL('ole32').CoTaskMemFree
    free.argtypes, free.restype = (C.c_void_p,), None
    name = C.c_void_p()
    try:
        status = known_folder(C.byref(folder_id), 0, None, C.byref(name))
        if status or not name:
            raise OSError(f'Windows CEF cannot locate LocalAppData (HRESULT {status & 0xffffffff:#010x})')
        path = Path(C.wstring_at(name))
    finally:
        if name:
            free(name)
    drive = C.WinDLL('kernel32').GetDriveTypeW
    drive.argtypes, drive.restype = (W.LPCWSTR,), W.UINT
    if not path.is_absolute() or drive(path.anchor) != 3:  # DRIVE_FIXED
        raise CefWorkerError('Windows CEF requires a fixed local drive for its short private profile')
    return path


def _windows_environment(private_root):
    # Do not inherit PATH, arbitrary Chromium switches or the user's scratch
    # locations. Anchor Windows directories in the OS, not the ambient env.
    get_system = C.WinDLL('kernel32', use_last_error=True).GetSystemWindowsDirectoryW
    get_system.argtypes, get_system.restype = (W.LPWSTR, W.UINT), W.UINT
    buffer = C.create_unicode_buffer(32768)
    if not 0 < get_system(buffer, len(buffer)) < len(buffer):
        raise OSError('cannot locate the Windows system directory for CEF')
    system = Path(buffer.value)
    if not system.is_absolute():
        raise OSError('Windows system directory is not absolute')
    private = {'TEMP': 'temp', 'TMP': 'temp', 'TMPDIR': 'temp', 'HOME': 'home',
               'USERPROFILE': 'home', 'APPDATA': 'config', 'LOCALAPPDATA': 'data'}
    for name in sorted(set(private.values())):
        (private_root / name).mkdir()
    env = {key: str(private_root / value) for key, value in private.items()}
    env.update(SystemRoot=str(system), WINDIR=str(system), SYSTEMDRIVE=system.drive,
               PATH=str(system / 'System32'))
    return C.create_unicode_buffer('\0'.join(f'{key}={env[key]}' for key in sorted(env, key=str.casefold)) + '\0')


@contextmanager
def _windows_group(ctx, argv, log, *, cancel=None):
    """Run one fixed worker in its own atomic, kill-on-close Windows Job."""
    _check_windows_cancel(cancel)
    import msvcrt
    from ..cache import hygiene
    from ..runtime import windows_browser as native
    from ..runtime.windows_cleanup import pinned

    scratch = Path(ctx.cache) / hygiene.TMP
    fstxn.mkdir(scratch, root=ctx.root)
    with ExitStack() as cleanup:
        job, owned, settled = None, [], False

        def settle(_exc_type, primary, _traceback):
            nonlocal settled
            if job is not None:
                try:
                    job.close()
                except OSError as error:
                    paths = ', '.join(str(path) for path, _ in owned)
                    failure = native.JobSettlementError(
                        f'Windows CEF Job settlement unconfirmed; retained {paths}')
                    failure.__cause__ = error
                    if primary is not None:
                        raise primary from failure
                    raise failure from error
                except KeyboardInterrupt as error:
                    if isinstance(primary, KeyboardInterrupt):
                        raise primary from error.__cause__
                    raise
            settled = True

        def discard(_exc_type, primary, _traceback):
            if job is not None and not settled:
                return
            errors = []
            for path, identity in reversed(owned):
                try:
                    fstxn.remove_pinned(path, identity, preserve=lambda _: False)
                except Exception as error:
                    errors.append(error)
            if errors:
                if primary is None:
                    raise errors[0]
                primary.args = (f'{primary}; could not delete settled CEF scratch: {errors[0]}',
                                *primary.args[1:])

        cleanup.push(discard)
        # Keep no-reparse ancestors pinned until the Job settles. Discard then
        # reopens both original trees by identity; no PID/path-based recovery.
        ancestors = cleanup.enter_context(ExitStack())

        def pin_chain(path):
            for directory in (*reversed(path.parents), path):
                ancestors.enter_context(pinned(directory, directory=True))

        def create_owned(root, prefix):
            path = Path(tempfile.mkdtemp(prefix=prefix, dir=root))
            owned.append((path, fstxn.cleanup_identity(path)))
            ancestors.enter_context(pinned(path, directory=True))
            return path

        scratch = scratch.absolute()
        pin_chain(scratch)
        run = create_owned(scratch, 'gfx_cef_a0-')
        output = run / 'o'
        output.mkdir()
        # Win32 atomic file replacement also opens the immediate parent for
        # write sharing. Keep the directory nonempty with a pinned sentinel
        # before relaxing only that leaf's sharing, so NTFS cannot turn it
        # into a junction in place; DELETE/rename of the directory stays denied.
        with pinned(output, directory=True) as (_, output_info, _):
            sentinel = output / '.vtx-cef-owned'
            sentinel.touch(exist_ok=False)
            _, sentinel_info, _ = ancestors.enter_context(pinned(sentinel))
            if (not stat.S_ISREG(sentinel_info.st_mode) or sentinel_info.st_nlink != 1
                    or sentinel_info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise CefWorkerError('Windows CEF output guard is not an owned regular file')
        _, writable_info, _ = ancestors.enter_context(pinned(output, directory=True, allow_child_renames=True))
        if fstxn.entry_identity(writable_info) != fstxn.entry_identity(output_info):
            raise CefWorkerError('Windows CEF output directory changed before launch')
        # Keep frame staging/output and cwd under the project. Place Chromium's
        # deep internal layout under the short, user-local Windows temp anchor.
        local = _windows_local_appdata()
        pin_chain(local)
        short_temp = local / 'Temp'
        short_temp.mkdir(exist_ok=True)
        ancestors.enter_context(pinned(short_temp, directory=True))
        private_root = create_owned(short_temp, 'vtxcef-')
        profile = private_root / 'profile'
        if len(str(profile).encode('utf-16-le')) // 2 > 128:
            raise CefWorkerError('Windows CEF private profile base is too long for legacy Chromium paths')
        profile.mkdir()
        cleanup.push(settle)
        pipes = _WindowsPipes(cancel)
        cleanup.callback(pipes.close)
        pipes.open()
        child_read, child_write = pipes.child_handles
        args = [*argv(output, profile), f'--ipc-read-handle={child_read}',
                f'--ipc-write-handle={child_write}']
        executable = PureWindowsPath(args[0])
        if not executable.is_absolute() or executable.suffix.lower() != '.exe' or any('\0' in arg for arg in args):
            raise CefWorkerError('Windows CEF requires a fixed absolute worker .exe and valid arguments')
        environment = _windows_environment(private_root)
        _check_windows_cancel(cancel)
        job = native._OwnedJob(args)
        job._launch(args, subprocess.DEVNULL, subprocess.DEVNULL,
                    cwd=str(output), environment=environment,
                    inherited_handles=pipes.child_handles, log_handle=msvcrt.get_osfhandle(log.fileno()))
        pipes.close_child_ends()
        _check_windows_cancel(cancel)
        yield SimpleNamespace(proc=job.proc, run_dir=output, profile_dir=profile, pipes=pipes)


def _wait_windows_worker(proc, cancel, timeout):
    deadline = time.monotonic() + timeout
    while True:
        _check_windows_cancel(cancel)
        code = proc.poll()
        if code is not None:
            return code
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        time.sleep(min(.02, remaining))


def _windows_png_info(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise CefWorkerError('CEF output is not an owned regular file')
    return info


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
    if sys.platform == 'win32':
        # Windows files inherit the cache directory DACL; POSIX chmod is not
        # available and must not be emulated via Windows' read-only bit.
        _windows_png_info(source)
    else:
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


def render_page(html, outdir, n_frames, *, ctx, width, height, frames=None, cancel=None):
    block_started = time.perf_counter_ns()
    worker, mode = cef_config()
    options = cef_runtime_options()
    selected = _selected_frames(n_frames, frames)
    windows = sys.platform == 'win32'
    if windows:
        _check_windows_cancel(cancel)
        if mode != 'a0':
            raise CefWorkerError("Windows CEF requires software A0 (no fallback)")
        pinned = getattr(ctx, '_gfx_environment', None)
        if (not pinned or pinned[1].get('backend') != 'cef' or pinned[1].get('status') != 'input-bound'
                or (worker, mode) != pinned[2:4]):
            raise CefWorkerError("Windows CEF configuration does not match the pinned worker identity (no fallback)")
        worker = pinned[2]
        ctx.verify_inputs("gfx")  # recheck the pinned runtime before launching any executable
    elif not supervision_available():
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
    from ..cache.policy import storage_root
    report = storage_root(ctx) / "reports" / "cef"
    fstxn.mkdir(report, root=ctx.root)
    key, receipts, descriptors = f"{outdir.name}-{secrets.token_hex(4)}", [], []
    execution, observation = events.context(ctx), events.current(ctx)
    pipeline = {"schema": "vtx.cef-host-pipeline/1", "frames": selected,
                "execution": {"run_id": getattr(execution, "run_id", None)},
                "task": {"task_id": getattr(observation, "task_id", None),
                         "attempt_no": getattr(observation, "attempt_no", None)},
                "clock": "python.perf_counter_ns", "block_started_counter_us": block_started // 1000,
                "rendered_frames": [], "worker_options": options.to_dict(),
                "max_native_inflight": 2, "output_buffers": 2,
                "native_wait_us": 0, "native_receive_attempts": 0,
                "host_validate_us": 0, "host_commit_us": 0,
                "cached_validate_us": (cached_validated - validation_started) // 1000,
                "final_validate_us": None, "worker_startup_us": None,
                "request_roundtrips": [], "completed": False,
                "request_wall_scope": "submit to receive; includes overlapped host work and the pipelined next-frame capture, not native CPU time",
                "native_wait_scope": "host receive-call wall including protocol parsing; never native CPU or stage wall time",
                "wall_scope": "block through validation/cleanup; excludes final diagnostic write",
                "output_commits": {"rename": 0, "copy": 0}}
    worker_env = dict(os.environ) if not windows else None
    pair = socket.socketpair() if sys.platform == 'darwin' else None
    if pair:
        # Native socketpair avoids AF_UNIX pathname limits, cwd mutation and
        # external scratch aliases. Shared memory still travels via SCM_RIGHTS.
        worker_env["TMPDIR"] = str(outdir.parent)


    def argv(run, profile):
        # AF_UNIX and Chromium's own scratch sockets have short path limits.
        # Parent-owned fd aliases support arbitrarily long/Unicode project roots
        # while retaining ALL scratch bytes in supervised cache/tmp (not /tmp).
        if not windows:
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
        if windows or pair:
            argv.remove("--ipc-socket=ipc.sock")
        if pair:
            argv.append(f"--ipc-fd={pair[1].fileno()}")
        elif not windows and mode == "a0":
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
                        supervisor = (_windows_group(ctx, argv, log, cancel=cancel) if windows else
                                      SupervisedProcessGroup(ctx, argv, backend_id=f"cef_{mode}",
                                                             stdout=log, stderr=log, env=worker_env,
                                                             pass_fds=(pair[1].fileno(),) if pair else ()))
                        with supervisor as group:
                            session_error = None
                            try:
                                try:
                                    if windows:
                                        connection = CefWorkerClient.from_windows_pipes(
                                            group.pipes, expected_pid=group.proc.pid, expected_wh=(width, height))
                                    elif pair:
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
                                    _check_windows_cancel(cancel)
                                    # Dual-ticket pipeline (depth 2): the next
                                    # request is submitted before the current
                                    # receipt is consumed, so the native worker
                                    # overlaps frame N+1 capture with frame N
                                    # encode. Receipts stay strictly later than
                                    # their own frame's encode (received file is
                                    # complete), and alternating basenames mean
                                    # the file being validated is never the one
                                    # being written.
                                    request_started = time.perf_counter_ns()
                                    client.submit_png(
                                        missing[0], "frame0.png", request_id=missing[0] + 1,
                                        expected_wh=(width, height), worker_relative=True)
                                    next_submit_started = None
                                    for index, f in enumerate(missing):
                                        # 流水提交：等待当前回执前送入下一请求。
                                        if index + 1 < len(missing):
                                            next_frame = missing[index + 1]
                                            next_submit_started = time.perf_counter_ns()
                                            client.submit_png(
                                                next_frame, f"frame{(index + 1) % 2}.png",
                                                request_id=next_frame + 1, expected_wh=(width, height),
                                                worker_relative=True)
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
                                            request_started = next_submit_started
                                        source = group.run_dir / f"frame{index % 2}.png"
                                        started = time.perf_counter_ns()
                                        timing["validate_start_offset_us"] = (started - block_started) // 1000
                                        try:
                                            if windows:
                                                _windows_png_info(source)
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
                                        _check_windows_cancel(cancel)
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
                                rc = (_wait_windows_worker(group.proc, cancel, 15) if windows
                                      else group.proc.wait(timeout=15))
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
            cause = failure
            if windows:
                from ..runtime.windows_browser import JobSettlementError
                if isinstance(failure.__cause__, JobSettlementError):
                    cause = failure.__cause__
            settlement = f"; {cause}" if cause is not failure else ""
            raise CefWorkerError(f"{failure}{settlement}; native logs: {report}",
                                 status_code=getattr(failure, "status_code", None),
                                 detail=getattr(failure, "detail", None)) from cause
        # Only the selected block is checked here. The shared page coordinator
        # retains whole-page/input-fingerprint certification after all blocks.
        _check_windows_cancel(cancel)
        started = time.perf_counter_ns()
        try:
            gfx_cli.require_frames(outdir, selected, width=width, height=height)
        finally:
            pipeline["final_validate_us"] = (time.perf_counter_ns() - started) // 1000
        _check_windows_cancel(cancel)
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
