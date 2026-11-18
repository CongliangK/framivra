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
import subprocess
import time


_PTR, _SIZE, _H, _D, _B = C.c_void_p, C.c_size_t, W.HANDLE, W.DWORD, W.BOOL
_JOB_LIST, _HANDLE_LIST = 0x0002000D, 0x00020002
_KILL_ON_CLOSE, _EXTENDED_STARTUPINFO = 0x2000, 0x00080000
_WAIT_TIMEOUT, _INFINITE = 258, 0xFFFFFFFF
_STOP_TIMEOUT, _DRAIN_TIMEOUT = 10, 2
_CAPTURE_LIMIT = 32 * 1024 * 1024  # aggregate stdout + stderr, including timeout cleanup


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


@lru_cache(maxsize=1)
def _api():
    kernel = C.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateJobObjectW': (_H, _PTR, W.LPCWSTR),
        'SetInformationJobObject': (_B, _H, C.c_int, _PTR, _D),
        'InitializeProcThreadAttributeList': (_B, _PTR, _D, _D, C.POINTER(_SIZE)),
        'UpdateProcThreadAttribute': (_B, _PTR, _D, _SIZE, _PTR, _SIZE, _PTR, _PTR),
        'DeleteProcThreadAttributeList': (None, _PTR),
        'CreateProcessW': (_B, W.LPCWSTR, W.LPWSTR, _PTR, _PTR, _B, _D, _PTR,
                           W.LPCWSTR, C.POINTER(_StartupInfoEx), C.POINTER(_ProcessInfo)),
        'CreateFileW': (_H, W.LPCWSTR, _D, _D, C.POINTER(_SecurityAttributes), _D, _D, _H),
        'CreatePipe': (_B, C.POINTER(_H), C.POINTER(_H), _PTR, _D),
        'SetHandleInformation': (_B, _H, _D, _D),
        'SetNamedPipeHandleState': (_B, _H, C.POINTER(_D), _PTR, _PTR),
        'ReadFile': (_B, _H, _PTR, _D, C.POINTER(_D), _PTR),
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
def _startup(api, job, stdio):
    distinct = tuple(dict.fromkeys(stdio))
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

    def open(self, api, temporary):
        writer = _H()
        temporary.callback(_close_handle, api, writer, 'value')
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


class BrowserJob:
    def __init__(self, argv, *, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
        args = [os.fsdecode(value) for value in argv]
        if (not args or not PureWindowsPath(args[0]).is_absolute()
                or PureWindowsPath(args[0]).suffix.lower() != '.exe' or any('\0' in arg for arg in args)):
            raise OSError(errno.EINVAL, 'Windows browser requires a fixed absolute .exe and valid argv')
        if any(stream not in (subprocess.DEVNULL, subprocess.PIPE) for stream in (stdout, stderr)):
            raise ValueError('Windows browser accepts only DEVNULL or PIPE for stdout/stderr')
        self._api = api = _api()
        self._job, self._info, self._captures = _H(), _ProcessInfo(), []
        self.proc = _Process(api, self._info, argv)
        try:
            self._job.value = _check(api.CreateJobObjectW(None, None), 'CreateJobObjectW')
            limits = _ExtendedLimits()
            limits.basic.flags = _KILL_ON_CLOSE
            _check(api.SetInformationJobObject(self._job, 9, C.byref(limits), C.sizeof(limits)),
                   'SetInformationJobObject')
            with ExitStack() as temporary:
                temporary.callback(_close_handle, api, self._info, 'thread')
                security = _SecurityAttributes(C.sizeof(_SecurityAttributes), None, True)
                nul = api.CreateFileW('NUL', 0xC0000000, 3, C.byref(security), 3, 0x80, None)
                _check(nul and nul != _H(-1).value, 'CreateFileW (NUL)')
                temporary.callback(_close_handle, api, _H(nul), 'value')
                stdio = [nul]  # no browser stdin contract; do not inherit the host console handle
                for stream in (stdout, stderr):
                    if stream == subprocess.PIPE:
                        capture = _Capture()
                        self._captures.append(capture)
                        stdio.append(capture.open(api, temporary))
                    else:
                        stdio.append(nul)
                with _startup(api, self._job.value, stdio) as startup:
                    cmd = C.create_unicode_buffer(subprocess.list2cmdline(args))
                    # Absolute lpApplicationName, mutable argv; inherit cwd/environment, not arbitrary handles.
                    _check(api.CreateProcessW(args[0], cmd, None, None, True, _EXTENDED_STARTUPINFO,
                                              None, None, C.byref(startup), C.byref(self._info)),
                           'CreateProcessW')
        except BaseException:
            self.close()
            raise

    def _stop_job(self):
        _close_handle(self._api, self._job, 'value')

    def _close(self, wait_timeout):
        try:
            self._stop_job()
            if self._info.process:
                self.proc.wait(wait_timeout)
        finally:
            with ExitStack() as closing:
                closing.callback(_close_handle, self._api, self._info, 'process')
                closing.callback(_close_handle, self._api, self._info, 'thread')
                for capture in self._captures:
                    closing.callback(_close_handle, self._api, capture.handle, 'value')

    def close(self):
        self._close(_STOP_TIMEOUT)

    def _pump(self, timeout):
        deadline = None if timeout is None else time.monotonic() + timeout
        remaining = _CAPTURE_LIMIT - sum(len(capture.data) for capture in self._captures)
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


def run_browser(argv, timeout=None):
    """Capture both streams and settle the owned tree on every exit."""
    job = BrowserJob(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = cleanup_error = None
    try:
        if job._pump(timeout):
            stdout, stderr = job._output()
            return subprocess.CompletedProcess(argv, job.proc.returncode, stdout, stderr)
        deadline = time.monotonic() + _DRAIN_TIMEOUT
        try:
            job._stop_job()  # before attempting any further reads; descendants may hold writers
            job._pump(max(0, deadline - time.monotonic()))
        except OSError as cleanup:
            cleanup_error = cleanup
    finally:
        if deadline is None:
            job.close()
        else:
            try:
                # The exact root gets only the remainder, even if the drain failed or overflowed.
                job._close(max(0, deadline - time.monotonic()))
            except (OSError, subprocess.TimeoutExpired) as cleanup:
                if cleanup_error is None:
                    cleanup_error = cleanup
    raise subprocess.TimeoutExpired(argv, timeout, *job._output()) from cleanup_error
