"""Explicit CDP/CLI/CEF/Electron routing and output identities."""
import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER_DIR = ROOT / "experiments/gfx_electron_worker"
OUTPUT = "electron_native_topng"
CEF_CONFIGURATION_ENV = (
    "VTX_CEF_SYNC_MODE", "VTX_CEF_REFERENCE_CONTEXT", "VTX_CEF_CAPTURE_FPS",
    "VTX_CEF_PNG_ENCODER", "VTX_CEF_PNG_LEVEL",
    "VTX_CEF_PNG_FILTER", "VTX_CEF_ENCODE_THREADS", "VTX_GFX_PAGES",
    "VTX_GFX_SPLIT_FRAMES", "VTX_GFX_SPLIT_MAX",
)


def _cef_integer(name, default, minimum, maximum):
    value = os.environ.get(name, str(default))
    # No truncation, signs, whitespace or Python-specific numeric syntax. In
    # particular a typo must not silently create an unbounded worker pool.
    if not re.fullmatch(r"[0-9]{1,10}", value):
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return number


def _cef_choice(name, default, choices):
    value = os.environ.get(name, default)
    if value not in choices:
        raise ValueError(f"{name} must be {'|'.join(choices)}; no silent fallback")
    return value


@dataclass(frozen=True)
class CefRuntimeOptions:
    """One normalized source for native argv, provenance and cache identity."""

    sync_mode: str = "n"
    png_encoder: str = "spng"
    png_level: int = 1
    png_filter: str = "sub_up"
    encode_threads: int = 2
    reference_context: str = "shared"
    capture_fps: int = 120

    def to_dict(self):
        return asdict(self)

    def argv(self):
        return [f"--sync-mode={self.sync_mode}",
                f"--reference-context={self.reference_context}", f"--png-encoder={self.png_encoder}",
                f"--png-level={self.png_level}", f"--png-filter={self.png_filter}",
                f"--encode-threads={self.encode_threads}", f"--capture-fps={self.capture_fps}"]


def cef_runtime_options():
    """Mode N remains universal; Mode P is explicit and requires page hooks.

    PNG options change bytes (not decoded quality); they are nevertheless bound
    to this experimental backend's identity. Never infer P from HTML text.
    """
    return CefRuntimeOptions(
        sync_mode=_cef_choice("VTX_CEF_SYNC_MODE", "n", ("n", "p")),
        reference_context=_cef_choice("VTX_CEF_REFERENCE_CONTEXT", "shared", ("shared", "isolated")),
        png_encoder=_cef_choice("VTX_CEF_PNG_ENCODER", "spng", ("spng", "fpng", "zlib")),
        png_level=_cef_integer("VTX_CEF_PNG_LEVEL", 1, 1, 3),
        png_filter=_cef_choice("VTX_CEF_PNG_FILTER", "sub_up", ("none", "sub", "up", "sub_up", "all")),
        encode_threads=_cef_integer("VTX_CEF_ENCODE_THREADS", 2, 1, 16),
        capture_fps=_cef_integer("VTX_CEF_CAPTURE_FPS", 120, 1, 240))


def cef_scheduling_options():
    """Global worker bound shared by pages and frame chunks, never nested."""
    return {
        "pages": _cef_integer("VTX_GFX_PAGES", 7, 1, 32),
        "split_frames": _cef_integer("VTX_GFX_SPLIT_FRAMES", 450, 0, 65536),
        "split_max": _cef_integer("VTX_GFX_SPLIT_MAX", 3, 1, 32),
    }


def cef_configuration():
    return {"runtime": cef_runtime_options().to_dict(),
            "scheduling": cef_scheduling_options()}


def cef_launch_flags(mode):
    return ["mode=" + str(mode), "sync-canary=exact"] + [
        flag[2:] for flag in cef_runtime_options().argv()]


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
             WORKER_DIR / "pixel_codec.mjs", Path(__file__), ROOT / "vtx/render/gfx_electron.py",
             ROOT / "vtx/render/gfx_electron_stage.py", ROOT / "vtx/render/gfx_cli.py",
             ROOT / "vtx/runtime/proc_supervisor.py"]
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
    cef_configuration()  # fail before starting a worker or admitting cache reuse
    return worker, mode


def _cef_identity():
    """All output-affecting CEF settings, including the actual native executable.

    Default CDP/CLI keep the legacy key axis. CEF never shares it. Runtime
    resources must remain the locked distribution deployed beside the worker.
    """
    if selected() != "cef":
        return ""
    worker, mode = cef_config()
    files = [worker, Path(__file__), ROOT / "vtx/render/gfx_cef.py",
             ROOT / "vtx/render/cef_client.py", ROOT / "vtx/render/gfx_cli.py",
             ROOT / "vtx/cli/commands.py", ROOT / "vtx/runtime/proc_supervisor.py"]
    return "cef-alpha-exact-v2|" + json.dumps(
        [mode, cef_configuration(), [content_digest(p) for p in files]],
        sort_keys=True, separators=(",", ":"))


def identity():
    backend = selected()
    if backend == "electron":
        return _electron_identity()
    if backend == "cef":
        return _cef_identity()
    return ""
