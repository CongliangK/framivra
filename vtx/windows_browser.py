"""Windows browser Job Object: kill the entire owned tree on handle closure.

The browser starts suspended, joins the job before it can spawn children, then
resumes. The non-inheritable job handle belongs only to the Python host, so
TerminateProcess on the host also closes it and terminates the browser tree.
"""
import ctypes
from ctypes import wintypes
import subprocess


class BrowserJob:
    def __init__(self, argv, *, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel, self.handle, self.proc = kernel, None, None

        class BasicLimits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t),
                        ('max_ws', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', ctypes.c_uint64 * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.proc = subprocess.Popen(argv, creationflags=0x4,  # CREATE_SUSPENDED
                                         stdout=stdout, stderr=stderr)
            if not kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(self.proc._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            resume = ctypes.WinDLL('ntdll').NtResumeProcess
            resume.argtypes = [wintypes.HANDLE]
            resume.restype = ctypes.c_long
            if resume(wintypes.HANDLE(self.proc._handle)) != 0:
                raise RuntimeError('could not resume Windows browser process')
        except BaseException:
            if self.proc is not None:
                self.proc.kill()
                self.proc.wait(timeout=5)
            self.close()
            raise

    def close(self):
        if self.handle is not None:
            handle, self.handle = self.handle, None
            if not self.kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        if self.proc is not None:
            self.proc.wait(timeout=10)


def run_browser(argv, timeout=None):
    """subprocess.run equivalent with Windows tree cleanup on every exit."""
    job = BrowserJob(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = job.proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, job.proc.returncode, stdout, stderr)
    finally:
        job.close()
