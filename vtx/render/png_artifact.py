"""Linux frame-output snapshots; not a replacement for general gfx/media I/O.

No pathname is opened for payload reads. Pin all ancestors, bind the original
project/parent identity, reject special leaves before reading, and validate the
same bounded immutable bytes subsequently used by the frame certificate.
"""
import hashlib
import io
import os
from pathlib import Path
import stat
import sys

from ..runtime import fstxn
from .gfx_cli import _png_stream_invalid_reason


class ArtifactError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _version(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def project_identity(root):
    """Capture canonical no-link project ancestry without opening file contents."""
    root = Path(os.path.abspath(root))
    identity = fstxn.cleanup_identity(root)
    if (identity is None or identity[-1][2] != stat.S_IFDIR
            or root.resolve(strict=True) != root):
        raise ArtifactError("image.invalid", "project root is not a canonical directory")
    return identity


def read_artifact(path, *, root, limit, root_identity=None):
    """Bounded regular-file read, with before/after fd and namespace checks.

    The lexical chain check is intentional: pinned_parent normalizes its parent
    for write compatibility. An alias must not silently become read authority.
    No /proc-fd reopen, fallback, implicit mkdir, or descriptor outlives this call.
    """
    if sys.platform != "linux" or fstxn.write_capability() != "fd-anchored":
        raise ArtifactError("platform.unsupported", "artifact read requires Linux fd anchoring")
    path, root = Path(os.path.abspath(path)), Path(os.path.abspath(root))
    if root not in path.parents:
        raise ArtifactError("image.invalid", "artifact escaped project root")
    expected_root = root_identity if root_identity is not None else project_identity(root)
    expected = fstxn.cleanup_identity(path)
    if (expected is None or expected[:len(expected_root)] != expected_root
            or expected[-1][2] != stat.S_IFREG):
        raise ArtifactError("image.invalid", "artifact is missing, special, or project identity changed")

    def current():
        if fstxn.cleanup_identity(path) != expected:
            raise ArtifactError("image.invalid", "artifact namespace changed during read")

    with fstxn.pinned_parent(path, create=False, root=root) as (parent, name, canonical):
        if (canonical != path or fstxn.entry_identity(os.fstat(parent)) != expected[-2]):
            raise ArtifactError("image.invalid", "artifact parent identity changed")
        current()
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or fstxn.entry_identity(before) != expected[-1]:
                raise ArtifactError("image.invalid", "artifact leaf identity changed or is special")
            current()
            if before.st_size > limit:
                raise ArtifactError("image.limit", "PNG exceeds requested byte limit")
            chunks, count = [], 0
            while count <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - count))
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
            if count > limit:
                raise ArtifactError("image.limit", "PNG exceeds requested byte limit")
            if _version(os.fstat(fd)) != _version(before) or count != before.st_size:
                raise ArtifactError("image.invalid", "artifact changed or short read")
            current()
            return b"".join(chunks)
        finally:
            os.close(fd)


def read_png(path, *, root, width, height, limit=8388608, root_identity=None):
    """Return (certificate facts, immutable PNG) from one anchored snapshot."""
    data = read_artifact(path, root=root, limit=limit, root_identity=root_identity)
    reason = _png_stream_invalid_reason(io.BytesIO(data), len(data), width=width, height=height)
    if reason:
        raise ArtifactError("image.invalid", str(reason))
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}, data
