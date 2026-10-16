"""Explicit renderer selection and content identity (CEF is experimental Alpha)."""
import hashlib
import os
from functools import lru_cache
from pathlib import Path


def selected():
    name = os.environ.get("VTX_GFX", "cdp").strip().lower()
    if name not in ("cdp", "cli", "cef"):
        raise ValueError(f"VTX_GFX={name!r}: expected cdp|cli|cef; no silent fallback")
    return name


def cef_config():
    raw = os.environ.get("VTX_CEF_WORKER", "")
    worker = Path(raw).expanduser().resolve() if raw else None
    if worker is None or not worker.is_file() or not os.access(worker, os.X_OK):
        raise ValueError("CEF Alpha requires VTX_CEF_WORKER=<built vtx_cef_worker executable>")
    mode = os.environ.get("VTX_CEF_MODE", "a0")
    if mode not in ("a0", "a1"):
        raise ValueError("VTX_CEF_MODE must be a0|a1")
    return worker, mode


@lru_cache(maxsize=16)
def _binary_digest(path, stat):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity():
    """All output-affecting CEF settings, including the actual native executable.

    Default CDP/CLI keep the legacy key axis. CEF never shares it. Runtime
    resources must remain the locked distribution deployed beside the worker.
    """
    if selected() != "cef":
        return ""
    worker, mode = cef_config()
    st = worker.stat()
    digest = _binary_digest(str(worker), (st.st_dev, st.st_ino, st.st_size,
                                         st.st_mtime_ns, st.st_ctime_ns))
    return f"cef-alpha-exact-v1|{mode}|mode-n|{digest}"
