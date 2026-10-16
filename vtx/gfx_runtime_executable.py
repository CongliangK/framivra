"""Narrow #48 primitive transplant; see docs/execution-events.md provenance.

Cooperative byte/deadline limits do not bound a stalled OS read syscall.
"""
import hashlib
import os
from pathlib import Path
import stat
import time

MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024


class EnvironmentIdentityError(ValueError):
    pass


def observed_executable(pid, *, deadline):
    """Linux process executable bytes, not launcher text or PATH --version.

    Caller owns PID association/lifetime. This does not identify a browser by
    itself and does not walk a supervisor's descendants guessing which is Chrome.
    """
    if type(pid) is not int or pid <= 0:
        raise EnvironmentIdentityError('positive observed browser PID required')
    link = Path('/proc') / str(pid) / 'exe'
    # Hash via proc's executable link to bind the running inode. Do not resolve
    # it then reopen its pathname: a replaced on-disk executable is not running.
    fd = os.open(link, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_EXECUTABLE_BYTES:
            raise EnvironmentIdentityError('observed executable is not regular')
        target = os.readlink(link)
        h = hashlib.sha256()
        total = 0
        if time.monotonic() >= deadline:
            raise EnvironmentIdentityError('executable observation deadline')
        for block in iter(lambda: os.read(fd, 1024 * 1024), b''):
            total += len(block)
            if total > MAX_EXECUTABLE_BYTES or time.monotonic() >= deadline:
                raise EnvironmentIdentityError('executable observation bound')
            h.update(block)
        if total != before.st_size or time.monotonic() >= deadline:
            raise EnvironmentIdentityError('executable changed or observation expired')
        after = os.fstat(fd)
        now = link.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size,
                                        after.st_mtime_ns, after.st_ctime_ns) or \
                (now.st_dev, now.st_ino) != (after.st_dev, after.st_ino):
            raise EnvironmentIdentityError('observed executable changed')
        return {'path': target, 'sha256': h.hexdigest(),
                'size': after.st_size, 'observation_method': 'linux-proc-exe',
                '_stat_identity': (after.st_dev, after.st_ino, after.st_size,
                                   after.st_mtime_ns, after.st_ctime_ns)}
    finally:
        os.close(fd)
