"""Launch a fixed Windows browser in a private kill-on-close Job Object."""
import ctypes as C
from ctypes import wintypes as W
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from pathlib import PureWindowsPath
from types import SimpleNamespace
import errno
import math
import os
import shutil
import subprocess
import time


_PTR, _SIZE, _H, _D, _B = C.c_void_p, C.c_size_t, W.HANDLE, W.DWORD, W.BOOL
_JOB_LIST, _HANDLE_LIST = 0x0002000D, 0x00020002
_JOB_BASIC_ACCOUNTING = 1  # JobObjectBasicAccountingInformation
_KILL_ON_CLOSE, _EXTENDED_STARTUPINFO = 0x2000, 0x00080000
_WAIT_TIMEOUT, _INFINITE = 258, 0xFFFFFFFF
_STOP_TIMEOUT, _DRAIN_TIMEOUT = 10, 2
_CAPTURE_LIMIT = 32 * 1024 * 1024  # aggregate stdout + stderr, including timeout cleanup


class JobSettlementError(OSError):
    """The owned Windows browser could not be proved shut down."""


def job_cleanup_unconfirmed(error):
    """An unproved Job shutdown must not be routed to another browser."""
    return isinstance(error, JobSettlementError) or isinstance(error.__cause__, JobSettlementError)


def remove_settled_profile(profile):
    """Remove this invocation's private profile, only after proved Job settlement."""
    path = os.path.abspath(profile)
    if not path.startswith('\\\\?\\'):
        # Browser component directories can contain paths beyond MAX_PATH.
        path = ('\\\\?\\UNC\\' + path[2:] if path.startswith('\\\\') else '\\\\?\\' + path)
    for attempt in range(30):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 29:
                raise
            time.sleep(.1)


class _StartupInfo(C.Structure):
    _fields_ = [('cb', _D), ('reserved', W.LPWSTR), ('desktop', W.LPWSTR), ('title', W.LPWSTR),
                ('position_and_fill', _D * 7), ('flags', _D), ('show', W.WORD),
                ('reserved_len', W.WORD), ('reserved_bytes', _PTR),
                ('stdin', _H), ('stdout', _H), ('stderr', _H)]


class _StartupInfoEx(C.Structure):
    _fields_ = [('startup', _StartupInfo), ('attributes', _PTR)]


class _ProcessInfo(C.Structure):
    _fields_ = [('process', _H), ('thread', _H), ('pid', _D), ('tid', _D)]


class _SecurityAttributes(C.Structure):
    _fields_ = [('length', _D), ('descriptor', _PTR), ('inherit', _B)]


class _BasicLimits(C.Structure):
    _fields_ = [('process_time', C.c_int64), ('job_time', C.c_int64), ('flags', _D),
                ('min_ws', _SIZE), ('max_ws', _SIZE), ('active', _D), ('affinity', _SIZE),
                ('priority', _D), ('scheduling', _D)]


class _ExtendedLimits(C.Structure):
    _fields_ = [('basic', _BasicLimits), ('io', C.c_uint64 * 6), ('memory', _SIZE * 4)]


class _BasicAccounting(C.Structure):
    _fields_ = [('times', C.c_int64 * 4), ('page_faults', _D), ('total', _D),
                ('active', _D), ('total_terminated', _D)]


@lru_cache(maxsize=1)
def _api():
    kernel = C.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateJobObjectW': (_H, _PTR, W.LPCWSTR),
        'SetInformationJobObject': (_B, _H, C.c_int, _PTR, _D),
        'TerminateJobObject': (_B, _H, W.UINT),
        'QueryInformationJobObject': (_B, _H, C.c_int, _PTR, _D, C.POINTER(_D)),
        'InitializeProcThreadAttributeList': (_B, _PTR, _D, _D, C.POINTER(_SIZE)),
        'UpdateProcThreadAttribute': (_B, _PTR, _D, _SIZE, _PTR, _SIZE, _PTR, _PTR),
        'DeleteProcThreadAttributeList': (None, _PTR),
        'CreateProcessW': (_B, W.LPCWSTR, W.LPWSTR, _PTR, _PTR, _B, _D, _PTR,
                           W.LPCWSTR, C.POINTER(_StartupInfoEx), C.POINTER(_ProcessInfo)),
        'CreateFileW': (_H, W.LPCWSTR, _D, _D, C.POINTER(_SecurityAttributes), _D, _D, _H),
        'CreatePipe': (_B, C.POINTER(_H), C.POINTER(_H), _PTR, _D),
        'SetHandleInformation': (_B, _H, _D, _D),
        'DuplicateHandle': (_B, _H, _H, _H, C.POINTER(_H), _D, _B, _D),
        'SetNamedPipeHandleState': (_B, _H, C.POINTER(_D), _PTR, _PTR),
        'ReadFile': (_B, _H, _PTR, _D, C.POINTER(_D), _PTR),
        'WriteFile': (_B, _H, _PTR, _D, C.POINTER(_D), _PTR),
        'WaitForSingleObject': (_D, _H, _D),
        'GetExitCodeProcess': (_B, _H, C.POINTER(_D)),
        'CloseHandle': (_B, _H),
    }
    api = SimpleNamespace()
    for name, (result, *args) in signatures.items():
        fn = getattr(kernel, name)
        fn.restype, fn.argtypes = result, args
        setattr(api, name, fn)
    return api


def _win_error(stage, code):
    return OSError(0, f'{stage} failed', None, code)


def _check(value, stage):
    if not value:
        raise _win_error(stage, C.get_last_error())
    return value


def _close_handle(api, owner, field):
    handle = getattr(owner, field)
    if handle:
        _check(api.CloseHandle(handle), 'CloseHandle')
        setattr(owner, field, None)


@contextmanager
def _remember_interrupt(interrupted):
    try:
        yield
    except KeyboardInterrupt as cancel:
        if not interrupted:
            interrupted.append(cancel)
        raise


@contextmanager
def _launch_handles():
    interrupted = []

    def close(api, owner, field):
        with _remember_interrupt(interrupted):
            _close_handle(api, owner, field)

    try:
        with ExitStack() as temporary, _remember_interrupt(interrupted):
            yield temporary, close, interrupted
    except BaseException as error:
        if interrupted and error is not interrupted[0]:
            raise interrupted[0] from error
        raise


@contextmanager
def _startup(api, job, stdio, inherited_handles=()):
    distinct = tuple(dict.fromkeys((*stdio, *inherited_handles)))
    handles = (_H * len(distinct))(*distinct)
    values = ((_JOB_LIST, (_H * 1)(job)), (_HANDLE_LIST, handles))
    size = _SIZE()
    result = api.InitializeProcThreadAttributeList(None, len(values), 0, C.byref(size))
    error = C.get_last_error()
    if result or error != 122 or not size.value:  # first call must report required size
        raise _win_error('InitializeProcThreadAttributeList (size)', error)
    attrs = (_PTR * ((size.value + C.sizeof(_PTR) - 1) // C.sizeof(_PTR)))()
    _check(api.InitializeProcThreadAttributeList(attrs, len(values), 0, C.byref(size)),
           'InitializeProcThreadAttributeList')
    try:
        for key, value in values:  # both arrays outlive the attribute list
            _check(api.UpdateProcThreadAttribute(attrs, 0, key, value, C.sizeof(value), None, None),
                   'UpdateProcThreadAttribute')
        info = _StartupInfoEx()
        info.startup.cb, info.startup.flags = C.sizeof(info), 0x100  # STARTF_USESTDHANDLES
        info.startup.stdin, info.startup.stdout, info.startup.stderr = stdio
        info.attributes = C.cast(attrs, _PTR)
        yield info
    finally:
        api.DeleteProcThreadAttributeList(attrs)


class _Process:
    """Only the root's exact launch handle; never reacquire ownership by PID."""
    def __init__(self, api, info, argv):
        self._api, self._info, self.args = api, info, argv
        self.returncode = None

    @property
    def pid(self):
        return self._info.pid

    def _wait(self, ms):
        if self.returncode is None:
            state = self._api.WaitForSingleObject(self._info.process, ms)
            if state == _WAIT_TIMEOUT:
                return None
            if state != 0:
                raise _win_error('WaitForSingleObject', C.get_last_error())
            code = _D()
            _check(self._api.GetExitCodeProcess(self._info.process, C.byref(code)), 'GetExitCodeProcess')
            self.returncode = code.value
        return self.returncode

    def poll(self):
        return self._wait(0)

    def wait(self, timeout=None):
        ms = _INFINITE if timeout is None else min(_INFINITE - 1, math.ceil(max(0, timeout) * 1000))
        status = self._wait(ms)
        if status is None:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return status


class _Capture:
    def __init__(self):
        self.handle = _H()
        self.data, self.eof, self.buffer = bytearray(), False, C.create_string_buffer(65536)

    def open(self, api, temporary, close=_close_handle):
        writer = _H()
        temporary.callback(close, api, writer, 'value')
        _check(api.CreatePipe(C.byref(self.handle), C.byref(writer), None, 0), 'CreatePipe')
        _check(api.SetHandleInformation(writer, 1, 1), 'SetHandleInformation')
        # Only the private read end is nonblocking; no background readers to join or cancel.
        mode = _D(1)  # PIPE_NOWAIT
        _check(api.SetNamedPipeHandleState(self.handle, C.byref(mode), None, None),
               'SetNamedPipeHandleState')
        return writer.value

    def read(self, api, remaining):
        if self.eof:
            return 0
        count = _D()
        if api.ReadFile(self.handle, self.buffer, min(len(self.buffer), remaining + 1), C.byref(count), None):
            self.data.extend(self.buffer[:min(count.value, remaining)])
            if count.value > remaining:
                raise OSError(errno.EFBIG, 'Windows browser output exceeded capture limit')
            return count.value
        error = C.get_last_error()
        if error == 109:  # ERROR_BROKEN_PIPE: last writer closed and buffer drained
            self.eof = True
        elif error != 232:  # ERROR_NO_DATA: empty but still connected
            raise _win_error('ReadFile', error)
        return 0


class _OwnedJob:
    """Private native machinery; the caller validates inputs and owns cleanup before launch."""
    def __init__(self, argv, *, capture_limit=None):
        self._api = api = _api()
        self._job, self._info, self._captures = _H(), _ProcessInfo(), []
        self._close_failed = False
        self._capture_limit = _CAPTURE_LIMIT if capture_limit is None else capture_limit
        self.proc = _Process(api, self._info, argv)

    def _launch(self, args, stdout, stderr, *, stdin=None, cwd=None, environment=None, debug_images=False,
                inherited_handles=(), log_handle=None):
        """Extra inheritable handles are caller-owned; log_handle is borrowed and duplicated."""
        if log_handle is not None and (stdout, stderr) != (subprocess.DEVNULL, subprocess.DEVNULL):
            raise ValueError('an owned Job log replaces both output streams')
        api = self._api
        self._job.value = _check(api.CreateJobObjectW(None, None), 'CreateJobObjectW')
        limits = _ExtendedLimits()
        limits.basic.flags = _KILL_ON_CLOSE
        _check(api.SetInformationJobObject(self._job, 9, C.byref(limits), C.sizeof(limits)),
               'SetInformationJobObject')
        with _launch_handles() as (temporary, close, interrupted):
            temporary.callback(close, api, self._info, 'thread')
            nul = None
            if stdin is None or subprocess.DEVNULL in (stdout, stderr):
                security = _SecurityAttributes(C.sizeof(_SecurityAttributes), None, True)
                nul = api.CreateFileW('NUL', 0xC0000000, 3, C.byref(security), 3, 0x80, None)
                _check(nul and nul != _H(-1).value, 'CreateFileW (NUL)')
                temporary.callback(close, api, _H(nul), 'value')
            inherited = _H()
            if stdin is not None:
                # Inherit a duplicate with the original read access, not a reopened name.
                temporary.callback(close, api, inherited, 'value')
                current = _H(-1)  # GetCurrentProcess pseudo-handle
                _check(api.DuplicateHandle(current, stdin, current, C.byref(inherited), 0, True, 2),
                       'DuplicateHandle (stdin)')  # DUPLICATE_SAME_ACCESS
            stdio = [inherited.value if stdin is not None else nul]
            if log_handle is not None:
                log = _H()
                temporary.callback(close, api, log, 'value')
                current = _H(-1)
                _check(api.DuplicateHandle(current, log_handle, current, C.byref(log), 0, True, 2),
                       'DuplicateHandle (log)')
                stdio.extend((log.value, log.value))
            else:
                for stream in (stdout, stderr):
                    if stream == subprocess.PIPE:
                        capture = _Capture()
                        self._captures.append(capture)
                        stdio.append(capture.open(api, temporary, close))
                    else:
                        stdio.append(nul)
            with _startup(api, self._job.value, stdio, inherited_handles) as startup, _remember_interrupt(interrupted):
                cmd = C.create_unicode_buffer(subprocess.list2cmdline(args))
                flags = _EXTENDED_STARTUPINFO
                if debug_images:
                    flags |= 1  # DEBUG_PROCESS: the creating thread owns the event queue.
                if environment is not None:
                    flags |= 0x400 | 0x08000000  # CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW
                _check(api.CreateProcessW(args[0], cmd, None, None, True, flags, environment,
                                          cwd, C.byref(startup), C.byref(self._info)), 'CreateProcessW')

    def _stop_job(self):
        # Only the unnamed, non-inheritable Job created above, never the caller's Job.
        _check(self._api.TerminateJobObject(self._job, 1), 'TerminateJobObject')

    def _wait_job_empty(self, deadline):
        accounting, size = _BasicAccounting(), _D()
        while time.monotonic() < deadline:
            _check(self._api.QueryInformationJobObject(self._job, _JOB_BASIC_ACCOUNTING,
                                                       C.byref(accounting), C.sizeof(accounting),
                                                       C.byref(size)), 'QueryInformationJobObject')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break  # even a zero returned at or after the deadline is not proof
            if size.value != C.sizeof(accounting):
                raise OSError(errno.EIO, 'Windows browser Job accounting size mismatch')
            if accounting.active == 0:
                return
            time.sleep(min(.005, remaining))
        raise OSError(errno.ETIMEDOUT, 'Windows browser Job settlement deadline expired')

    def _close(self, deadline, *, drain=False):
        interrupted = []

        def close_owned(owner, field):
            with _remember_interrupt(interrupted):
                _close_handle(self._api, owner, field)

        try:
            # Capture cancellation before finally/ExitStack can replace it.
            with ExitStack() as closing, _remember_interrupt(interrupted):
                closing.callback(close_owned, self._info, 'process')
                closing.callback(close_owned, self._info, 'thread')
                for capture in self._captures:
                    closing.callback(close_owned, capture.handle, 'value')
                # On any failure this still kills on last close, but cannot prove settlement.
                closing.callback(close_owned, self._job, 'value')
                if self._close_failed:
                    raise OSError(errno.EIO, 'Windows browser Job cleanup previously failed')
                if self._info.process:  # no child to settle on a pre-launch failure
                    self._stop_job()  # stop before draining pipes that descendants may hold open
                    try:
                        with _remember_interrupt(interrupted):
                            if drain:
                                self._pump(deadline)
                    finally:
                        # Preserve the exact root status and release its handle before accounting.
                        try:
                            self.proc.wait(max(0, deadline - time.monotonic()))
                        except subprocess.TimeoutExpired:
                            # Do not expose the browser argv/profile as a cleanup cause.
                            raise OSError(errno.ETIMEDOUT, 'Windows browser root wait timed out') from None
                        _close_handle(self._api, self._info, 'process')
                        self._wait_job_empty(deadline)
        except BaseException as error:
            self._close_failed = True  # a retry must not confuse a closed handle with proof
            cancel = interrupted[0] if interrupted else error
            if isinstance(cancel, KeyboardInterrupt):
                unconfirmed = JobSettlementError('Windows browser Job cleanup interrupted; shutdown unconfirmed')
                if error is not cancel:
                    unconfirmed.__cause__ = error
                raise cancel from unconfirmed
            raise

    def close(self):
        self._close(time.monotonic() + _STOP_TIMEOUT)

    def _pump(self, deadline):
        remaining = self._capture_limit - sum(len(capture.data) for capture in self._captures)
        while deadline is None or time.monotonic() < deadline:
            before = remaining
            for capture in self._captures:
                remaining -= capture.read(self._api, remaining)  # one bounded read per stream per turn
            if all(capture.eof for capture in self._captures) and self.proc.poll() is not None:
                return True
            if before == remaining:
                pause = .005 if deadline is None else min(.005, deadline - time.monotonic())
                if pause > 0:
                    time.sleep(pause)
        return False

    def _output(self):
        return tuple(bytes(capture.data) for capture in self._captures)


class BrowserJob(_OwnedJob):
    def __init__(self, argv, *, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
        args = [os.fsdecode(value) for value in argv]
        if (not args or not PureWindowsPath(args[0]).is_absolute()
                or PureWindowsPath(args[0]).suffix.lower() != '.exe' or any('\0' in arg for arg in args)):
            raise OSError(errno.EINVAL, 'Windows browser requires a fixed absolute .exe and valid argv')
        if any(stream not in (subprocess.DEVNULL, subprocess.PIPE) for stream in (stdout, stderr)):
            raise ValueError('Windows browser accepts only DEVNULL or PIPE for stdout/stderr')
        super().__init__(argv)
        try:
            self._launch(args, stdout, stderr)
        except BaseException as primary:
            try:
                self.close()
            except OSError as cleanup:
                unconfirmed = JobSettlementError('Windows browser Job cleanup unconfirmed during launch')
                unconfirmed.__cause__ = cleanup
                raise primary from unconfirmed
            except KeyboardInterrupt as cleanup:
                if isinstance(primary, KeyboardInterrupt) and isinstance(cleanup.__cause__, JobSettlementError):
                    raise primary from cleanup.__cause__
                raise
            raise


def run_browser(argv, timeout=None):
    """Capture both streams and settle the owned tree on every exit."""
    job = BrowserJob(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        captured = job._pump(None if timeout is None else time.monotonic() + timeout)
    except BaseException as primary:
        try:
            job.close()
        except (OSError, subprocess.TimeoutExpired) as cleanup:
            raise primary from cleanup
        raise
    if captured:
        job.close()  # root exit and two EOFs do not establish that the Job is empty
        return subprocess.CompletedProcess(argv, job.proc.returncode, *job._output())
    cleanup_error = None
    try:
        # Share the existing timeout cleanup budget across capture, root and Job settlement.
        job._close(time.monotonic() + _DRAIN_TIMEOUT, drain=True)
    except (OSError, subprocess.TimeoutExpired) as cleanup:
        cleanup_error = cleanup
    raise subprocess.TimeoutExpired(argv, timeout, *job._output()) from cleanup_error
