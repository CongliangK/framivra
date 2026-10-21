"""Explicit CDP/CLI/CEF/Electron routing and output identities."""
import hashlib
import json
import math
import os
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER_DIR = ROOT / "experiments/gfx_electron_worker"
OUTPUT = "electron_native_topng"


def selected():
    name = os.environ.get("VTX_GFX", "cdp").strip().lower()
    if name not in ("cdp", "cli", "cef", "electron"):
        raise ValueError(f"VTX_GFX={name!r}: expected cdp|cli|cef|electron; no silent fallback")
    return name


def electron_config():
    binary = Path(os.environ.get("VTX_ELECTRON_BIN") or
                  WORKER_DIR / "node_modules/electron/dist/electron").expanduser().resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError(f"Electron executable unavailable: {binary}")
    mode = os.environ.get("VTX_ELECTRON_MODE", "b0")
    output = os.environ.get("VTX_ELECTRON_OUTPUT", OUTPUT)
    if mode != "b0" or output != OUTPUT:
        raise ValueError("Electron production Alpha supports only b0/electron_native_topng; no GPU/shared route")
    timeout = float(os.environ.get("VTX_ELECTRON_TIMEOUT", "15"))
    if not math.isfinite(timeout) or not 0.5 <= timeout <= 120:
        raise ValueError("VTX_ELECTRON_TIMEOUT must be finite and between 0.5 and 120 seconds")
    return binary, mode, output, timeout


@lru_cache(maxsize=64)
def _digest(path, stat_identity):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def content_digest(path):
    p = Path(path)
    st = p.stat()
    return _digest(str(p), (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns))


def _electron_identity():
    """Electron executable + actual worker/gate/codec/adapter/config implementation.

    CDP/CLI retain their historical shared key axis. Namespace is a separate key
    component in hygiene, including for CDP/CLI. Stat-keyed digest memoization
    avoids repeatedly streaming the executable while still detecting replacement.
    Runtime distribution/resources must remain deployed with the locked binary.
    """
    if selected() != "electron":
        return ""
    binary, mode, output, timeout = electron_config()
    files = [binary, WORKER_DIR / "navigation_worker.mjs", WORKER_DIR / "page_error_gate.mjs",
             WORKER_DIR / "pixel_codec.mjs", Path(__file__), ROOT / "vtx/gfx_electron.py",
             ROOT / "vtx/gfx_electron_stage.py", ROOT / "vtx/gfx_cli.py",
             ROOT / "vtx/proc_supervisor.py"]
    digests = [content_digest(p) for p in files]
    return "electron-alpha/1|" + json.dumps(
        [mode, "N", output, timeout, digests], separators=(",", ":"))


def cef_config():
    raw = os.environ.get("VTX_CEF_WORKER", "")
    worker = Path(raw).expanduser().resolve() if raw else None
    if worker is None or not worker.is_file() or not os.access(worker, os.X_OK):
        raise ValueError("CEF Alpha requires VTX_CEF_WORKER=<built vtx_cef_worker executable>")
    mode = os.environ.get("VTX_CEF_MODE", "a0")
    if mode not in ("a0", "a1"):
        raise ValueError("VTX_CEF_MODE must be a0|a1")
    return worker, mode


def _cef_identity():
    """All output-affecting CEF settings, including the actual native executable.

    Default CDP/CLI keep the legacy key axis. CEF never shares it. Runtime
    resources must remain the locked distribution deployed beside the worker.
    """
    if selected() != "cef":
        return ""
    worker, mode = cef_config()
    digest = content_digest(worker)
    return f"cef-alpha-exact-v1|{mode}|mode-n|{digest}"


def identity():
    backend = selected()
    if backend == "electron":
        return _electron_identity()
    if backend == "cef":
        return _cef_identity()
    return ""
