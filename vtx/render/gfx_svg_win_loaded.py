"""Windows SVG image handles reported by the loader for our owned launch."""
import ctypes as C
from ctypes import wintypes as W
import time

from ..runtime import windows_browser as native
from .gfx_svg_win_snapshot import _FileIdInfo, _handle_info, _normalized_dos_name, _windows_api


_CREATE, _EXIT, _LOAD = 3, 5, 6
_CONTINUE, _NOT_HANDLED = 0x00010002, 0x80010001
_BREAKPOINT = 0x80000003


class _ImageRejected(ValueError):
    """An otherwise owned loader event did not match the held image scope."""


class _Event(C.Structure):
    # AMD64 DEBUG_EVENT: aligned union includes EXCEPTION_DEBUG_INFO (160 B).
    _fields_ = [('code', W.DWORD), ('pid', W.DWORD), ('tid', W.DWORD), ('data', C.c_uint64 * 20)]


def _file_key(handle, kernel):
    info = _handle_info(handle, kernel, 18, _FileIdInfo())
    if not any(info.file_id):
        raise ValueError('Windows SVG loader returned an invalid image file identity')
    return _normalized_dos_name(handle), info.volume, bytes(info.file_id)


class _ImageEvents:
    """Same-thread debug queue for a job created with DEBUG_PROCESS.

    The render path only compares loader-provided handles to held files.
    A preflight can independently certify additional installed OS libraries;
    seeing an event alone is never authority for that library.
    """
    def __init__(self, job, entry, images, certify_library=None, helpers=None):
        if C.sizeof(_Event) != 176 or _Event.data.offset != 16:
            raise ValueError('Windows SVG image event ABI requires native x64')
        self.job = job
        self.wait = _windows_api('WaitForDebugEvent', W.BOOL, C.POINTER(_Event), W.DWORD)
        self.resume = _windows_api('ContinueDebugEvent', W.BOOL, W.DWORD, W.DWORD, W.DWORD)
        self.member = _windows_api('IsProcessInJob', W.BOOL, W.HANDLE, W.HANDLE, C.POINTER(W.BOOL))
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        info = self.kernel.GetFileInformationByHandleEx
        info.restype, info.argtypes = W.BOOL, (W.HANDLE, C.c_int, C.c_void_p, W.DWORD)
        self.entry = _file_key(entry, self.kernel)
        self.allowed = {_file_key(handle, self.kernel) for handle in images}
        self.certify_library = certify_library
        self.helpers = None if helpers is None else {_file_key(handle, self.kernel) for handle in helpers}
        if self.entry not in self.allowed or self.helpers is not None and not self.helpers <= self.allowed:
            raise ValueError('Windows SVG launch entry is not part of the held image scope')
        self.live, self.breakpoints = set(), set()
        self.created = self.exited = 0
        self.pending = None
        self.root_exited = self.uncertain = False

    def _continue(self):
        if self.pending is not None:
            event, status = self.pending
            self.pending = None  # Never repeat an ACK whose delivery is uncertain.
            if not self.resume(event.pid, event.tid, status):
                self.uncertain = True
                raise native.JobSettlementError('Windows SVG could not continue an owned event')
            if event.code == _CREATE:
                self.live.add(event.pid)
                self.created += 1
            elif event.code == _EXIT:
                self.live.remove(event.pid)
                self.breakpoints.discard(event.pid)
                self.exited += 1
                self.root_exited |= event.pid == self.job.proc.pid

    def _step(self, deadline, *, verify):
        event = _Event()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if not self.wait(C.byref(event), min(5, int(1000 * remaining))):
            if C.get_last_error() == 121:  # ERROR_SEM_TIMEOUT
                return
            raise C.WinError(C.get_last_error())
        code, pid = event.code, event.pid
        if code in (_CREATE, _LOAD):
            file = event.data[0]  # hFile in CREATE_PROCESS_DEBUG_INFO or LOAD_DLL_DEBUG_INFO
            try:
                if code == _CREATE:
                    member = W.BOOL()
                    if (pid in self.live or (pid == self.job.proc.pid and self.root_exited)
                            or not self.member(event.data[1], self.job._job, C.byref(member)) or not member.value):
                        raise native.JobSettlementError('Windows SVG create event is not an owned process')
                elif pid not in self.live:
                    raise native.JobSettlementError('Windows SVG DLL event has no owned process')
                self.pending = event, _CONTINUE
                if verify:
                    if not file:
                        raise _ImageRejected('Windows SVG loader did not supply an image file handle')
                    try:
                        key = _file_key(file, self.kernel)
                    except (ValueError, OSError) as error:
                        raise _ImageRejected('Windows SVG loader image identity could not be read') from error
                    if code == _CREATE:
                        if pid == self.job.proc.pid and key != self.entry:
                            raise _ImageRejected('Windows SVG loader did not use the held entry')
                        if pid != self.job.proc.pid and self.helpers is not None and key not in self.helpers:
                            raise _ImageRejected('Windows SVG loader created an unexpected renderer helper')
                    if key not in self.allowed:
                        if code != _LOAD or self.certify_library is None:
                            raise _ImageRejected('Windows SVG loader selected an image outside the held scope')
                        try:
                            self.certify_library(key, deadline)
                        except (ValueError, OSError) as error:
                            raise _ImageRejected('Windows SVG system library could not be certified') from error
                        self.allowed.add(key)
            finally:
                if file and not self.job._api.CloseHandle(file):
                    self.uncertain = True
                    raise native.JobSettlementError('Windows SVG could not release an image event handle')
        elif pid not in self.live or code not in (1, 2, 4, _EXIT, 7, 8):
            raise native.JobSettlementError('Windows SVG received an unexpected process event')
        else:
            self.pending = event, _CONTINUE
        if code == 1:  # EXCEPTION_DEBUG_EVENT: preserve normal delivery apart from the loader's initial break.
            initial = ((event.data[0] & 0xffffffff) == _BREAKPOINT and (event.data[19] & 0xffffffff) == 1
                       and pid not in self.breakpoints)
            if initial:
                self.breakpoints.add(pid)
            else:
                self.pending = event, _NOT_HANDLED
        self._continue()

    def pump(self, deadline):
        job = self.job
        remaining = job._capture_limit - sum(len(chunk.data) for chunk in job._captures)
        while time.monotonic() < deadline:
            for chunk in job._captures:
                remaining -= chunk.read(job._api, remaining)
            if self.root_exited and all(chunk.eof for chunk in job._captures):
                return True
            self._step(deadline, verify=True)
        return False

    def stop(self, deadline, *, verify):
        """Kill the owned Job, drain its exit events, and verify full accounting."""
        self.job._stop_job()
        self.certify_library = None  # Stopping is not runtime discovery.
        self._continue()
        accounting, size = native._BasicAccounting(), W.DWORD()
        job = self.job
        rejected = None
        while time.monotonic() < deadline:
            if self.root_exited and not self.live:
                native._check(job._api.QueryInformationJobObject(job._job, native._JOB_BASIC_ACCOUNTING,
                                                                C.byref(accounting), C.sizeof(accounting), C.byref(size)),
                              'QueryInformationJobObject (SVG image events)')
                if size.value != C.sizeof(accounting):
                    raise native.JobSettlementError('Windows SVG image event accounting size mismatch')
                if (not accounting.active and accounting.total == self.created == self.exited
                        and time.monotonic() < deadline):
                    if self.uncertain:
                        raise native.JobSettlementError('Windows SVG image event cleanup was uncertain')
                    return rejected
            # A child can be in the Job before its CREATE event is delivered.
            try:
                self._step(deadline, verify=verify and rejected is None)
            except _ImageRejected as error:
                rejected = error
                # The Job was terminated before this event was received.
                self._continue()
        raise native.JobSettlementError('Windows SVG image event queue did not settle') from rejected
