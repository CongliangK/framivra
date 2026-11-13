"""Independent-inode file copies: Linux reflink when supported, bytes otherwise.

Publication and destination safety belong to fstxn. A validated source stays open
for a whole batch, so pathname replacement cannot switch the bytes being copied.
This is not a lock against hostile in-place writers; observed mutations abort.
"""
import contextlib
import errno
import os
from pathlib import Path
import stat
import sys

from . import fstxn

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

_FICLONE = 0x40049409
_UNSUPPORTED = {errno.ENOTTY, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV,
                errno.EINVAL}


def _try_reflink(source_fd, target_fd):
    """Return False only for unsupported clone operations, never real I/O errors."""
    if sys.platform != 'linux' or fcntl is None:
        return False
    try:
        fcntl.ioctl(target_fd, _FICLONE, source_fd)
    except OSError as error:
        if error.errno in _UNSUPPORTED:
            return False
        raise
    return True


def _version(info):
    return (fstxn.entry_identity(info), info.st_size, info.st_mtime_ns,
            info.st_ctime_ns)


@contextlib.contextmanager
def _source_fd(path, root):
    from ..pipeline.infra import ProjectPathError

    path = Path(os.path.abspath(path))
    flags = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
             | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    if fstxn.write_capability() == 'fd-anchored':
        with fstxn.pinned_parent(path, create=False, root=root) as (parent, name, _):
            fd = os.open(name, flags, dir_fd=parent)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ProjectPathError(f'Clone source is not a regular file: {path}')
                yield fd
            finally:
                os.close(fd)
        return
    # Match fstxn's legacy parent-resolution/root contract on Windows/macOS;
    # refuse a symlink leaf and compare the opened descriptor with that leaf.
    parent = path.parent.resolve()
    if root is not None:
        real_root = Path(root).resolve()
        if parent != real_root and real_root not in parent.parents:
            raise ProjectPathError(f'Clone source escapes project root: {path}')
    path = parent / path.name
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ProjectPathError(f'Clone source is not a regular file: {path}')
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode)
                or fstxn.entry_identity(opened) != fstxn.entry_identity(before)):
            raise ProjectPathError(f'Clone source changed while opening: {path}')
        yield fd
    finally:
        os.close(fd)


class _ValidatedSource:
    def __init__(self, stream, version, root):
        self.stream, self.version, self.root = stream, version, root
        self._payload = None

    def _check(self):
        if _version(os.fstat(self.stream.fileno())) != self.version:
            raise OSError(errno.ESTALE, 'Clone source changed after validation')

    def copy_to(self, target):
        """Atomically publish validated bytes; return 'reflink' or 'copy'."""
        self._check()
        with fstxn.locked_write(target, root=self.root) as tmp:
            with open(tmp, 'r+b') as output:
                cloned = _try_reflink(self.stream.fileno(), output.fileno())
                if not cloned:
                    if self._payload is None:
                        self.stream.seek(0)
                        self._payload = self.stream.read()
                    # An unsupported ioctl must not leave partial output behind.
                    output.seek(0)
                    output.truncate()
                    output.write(self._payload)
            self._check()  # Must run before fstxn's atomic publication.
        return 'reflink' if cloned else 'copy'


@contextlib.contextmanager
def validated_source(path, *, validate, root=None):
    """Pin a regular no-follow source and validate its open stream once per batch.

    validate(stream, size) returns None or an invalidity reason. It must consume
    the same stream, not reopen the original pathname. No validation failure is
    treated as an unsupported reflink. root=None retains standalone library use.
    """
    with _source_fd(path, root) as fd:
        # The descriptor owner above closes fd, even when validation raises.
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            version = _version(os.fstat(fd))
            reason = validate(stream, version[1])
            if reason:
                raise OSError(errno.EINVAL, f'Invalid clone source: {reason}')
            source = _ValidatedSource(stream, version, root)
            source._check()
            yield source
