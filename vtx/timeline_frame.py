"""Authorized, warm-input single-frame composition; never a delivery/QC shortcut.

The read/reference protocol remains asset independent. This separately versioned
operation only writes cache/preview, rejects active plugins, and uses the kernel
compositor/validation. Media input identity is Fast, not adversarial full hashing.
"""
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

from .timeline import _error
from .timeline_reference import reference_timeline, _wire_size

_SCHEMA = "framivra.timeline.frame/v1"
_LIMIT = 8 * 1024 * 1024


class _FrameError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _digest(value):
    return _hash(json.dumps(value, sort_keys=True, ensure_ascii=True,
                            separators=(",", ":"), allow_nan=False).encode())


def _finish(result):
    if _wire_size(result, _LIMIT - 1) >= _LIMIT:
        result.update(ok=False, reference=None, image=None, diagnostics=[])
        _error(result, "output.limit", "", "frame result exceeds JSON wire byte limit", limit_bytes=_LIMIT)
    return result


def _png(path, width, height, limit):
    from .render.gfx_cli import png_invalid_reason
    # Bound actual reads, not just the advertised metadata. The temporary file
    # may exceed this delivery cap; it is never published as a complete result.
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise _FrameError("image.limit", "PNG exceeds requested byte limit")
    reason = png_invalid_reason(path, width=width, height=height)
    if reason:
        raise _FrameError("image.invalid", str(reason))
    return {"sha256": _hash(data), "bytes": len(data)}


def _environment():
    # Reuse the existing conservative tool/font certificate collectors. No
    # browser is started, and no second font/dependency inventory is maintained.
    from .render import gfx_environment as env
    from .pipeline.infra import filter_threads
    binary = shutil.which("ffmpeg")
    if not binary:
        raise _FrameError("environment.unavailable", "ffmpeg is unavailable")
    if any(os.environ.get(k) for k in ("LD_PRELOAD", "LD_AUDIT", "FFREPORT")):
        raise _FrameError("environment.unsupported", "loader injection/FFREPORT is unsupported")
    return {"ffmpeg": env._file(binary), "libraries": env._libraries(binary),
            "fonts": env._fonts(), "platform": sys.platform, "filter_threads": filter_threads(),
            "environment": {k: os.environ.get(k) for k in env.PIXEL_ENV
                            if k not in ("DISPLAY", "WAYLAND_DISPLAY")}}


def _layers_identity(layers, frame):
    from .render.gfx_environment import _file
    out = []
    for layer in layers:
        if layer["gfx"]:
            source = layer["source"]
            rel = frame - layer["cl"]["dst_in"]
            source.require_ready(rel, rel + 1)
            asset = {"upstream": source.identity, "file": _file(source.frame_path(rel))}
        else:
            from .cache.recovery import media_cache_valid
            if not media_cache_valid(layer["seg"], layer["cl"]["dur"]):
                raise _FrameError("inputs.not_ready", "complete current seg required (short verified previews are not accepted)")
            asset = {"upstream": layer["seg"].name, "file": _file(layer["seg"])}
        out.append({"asset": asset, "clip": layer["cl"], "track": layer["ti"],
                    "x": layer["x"], "y": layer["y"]})
    return out


def _render(path, reference, frame, limit):
    from .runtime import fstxn
    if sys.platform != "linux" or fstxn.write_capability() != "fd-anchored":
        raise _FrameError("platform.unsupported", "render requires Linux fd-anchored writes")
    from .pluginlib.hooks import active_plugins
    stored = json.loads(reference["document"]["raw"])
    # Static manifest/config selection, BEFORE Ctx/fingerprints can import any
    # active plugin. Installed but irrelevant keyed plugins do not block core.
    active = active_plugins(SimpleNamespace(tl=stored))
    if active:
        raise _FrameError("plugins.unsupported", "first slice supports core-only projects; active plugins: " +
                          ", ".join(p["name"] for p in active))
    from .timeline_structure import structure_errors
    errors = structure_errors(stored)
    if errors:
        raise _FrameError("render.structure", "; ".join(errors[:16]))
    from .pipeline.infra import Ctx
    from .pipeline.stages import validate_core
    from .pipeline.composite import RenderPlan, _graph_code_digest
    from .media.subs import _layer_ready
    from .cache.cache_state import verify_input_identity
    from .render.gfx_environment import _files

    class PreviewCtx(Ctx):
        write_root = None

        def receipt(self, *args, **kwargs):
            raise RuntimeError("frame preview cannot write delivery receipts")

        def run(self, cmd, need=True, **kwargs):
            if Path(str(cmd[0])).name == "ffmpeg":
                if self.write_root is None:
                    # Encoder capability probes use a null muxer, no disk output.
                    if cmd[-1] not in ("-", os.devnull):
                        raise _FrameError("write.denied", "preflight cannot write media")
                else:
                    target = Path(cmd[-1]).absolute()
                    if self.write_root not in target.parents:
                        raise _FrameError("write.denied", "frame write escaped preview namespace")
            # subprocess.run on some supported Pythons re-raises SIGINT after
            # only a short wait, leaving the media child alive. Own and reap our
            # direct core-only child; do not detach it from the caller's job group.
            import subprocess
            from .cache.recovery import atomic_ffmpeg

            def execute(argv):
                process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, encoding="utf-8", errors="replace")
                try:
                    stdout, stderr = process.communicate()
                except BaseException:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    process.communicate()
                    raise
                return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)

            value = atomic_ffmpeg(self, cmd, execute, **kwargs) if need else execute(cmd)
            if need and value.returncode:
                raise _FrameError("render.process", "renderer/probe failed: " + value.stderr[-1500:])
            return value

    path = Path(os.path.abspath(Path(path).expanduser()))
    version = reference["content_version"]

    def document_current():
        with path.open("rb") as stream:
            raw = stream.read(_LIMIT + 1)
        if len(raw) > _LIMIT or "sha256:" + _hash(raw) != version:
            raise _FrameError("snapshot.changed", "timeline changed during frame operation")

    document_current()
    c = PreviewCtx(path)
    if "sha256:" + c.timeline_sha256 != version:
        raise _FrameError("snapshot.changed", "timeline changed while loading renderer")
    errors = validate_core(c)
    if errors:
        raise _FrameError("render.validation", "; ".join(errors[:16]))
    layers = _layer_ready(c, frame)
    if layers is None:
        raise _FrameError("inputs.not_ready", "current warm seg/gfx inputs required; run preparation explicitly")
    plan = RenderPlan(c=c, draft=False, f=1.0, W=c.w, H=c.h,
                      layers=layers, png_output=True)
    ass = plan.segment_ass_bytes(frame, frame + 1)
    environment = _environment()
    identities = _layers_identity(layers, frame)
    # Full implementation files protect helper/ASS/profile changes too. This is
    # conservative code invalidation, not a claim of cross-host pixel equivalence.
    from .pipeline import composite
    code = _files([__file__, inspect.getfile(composite), inspect.getfile(Ctx)])
    inputs = {"schema": _SCHEMA, "content_version": version,
              "input_fingerprint": c.input_fingerprint, "frame": frame, "fps": c.fps,
              "width": c.w, "height": c.h, "layers": identities,
              "ass_sha256": _hash(ass), "graph": _graph_code_digest(),
              "profile": "kernel-composite/png-rgb24/ffv1/v1", "code": code,
              "environment": environment}
    key = _digest(inputs)
    directory = c.cache / "preview" / ("frame_" + key)
    image = directory / "image.png"
    marker = directory / "complete.json"

    def current():
        verify_input_identity(c)
        document_current()
        if active_plugins(SimpleNamespace(tl=stored)):
            raise _FrameError("plugins.changed", "active plugin selection changed")
        if _layers_identity(layers, frame) != identities or _environment() != environment:
            raise _FrameError("inputs.changed", "render inputs/environment changed")
        if _files([__file__, inspect.getfile(composite), inspect.getfile(Ctx)]) != code:
            raise _FrameError("implementation.changed", "compositor implementation changed")

    # Preflight checks have not written anything. Only the explicit authorization
    # path reaches this point; same-key producers serialize before reading cache.
    current()
    fstxn.mkdir(directory, root=c.root)
    with fstxn.file_lock(directory / "frame.lock", root=c.root):
        current()
        try:
            record = json.loads(marker.read_bytes())
            facts = _png(image, c.w, c.h, limit)
            hit = record == {"schema": _SCHEMA, "render_key": key, **facts}
        except (OSError, ValueError):
            hit = False
        if not hit:
            c.write_root = directory
            import secrets
            plan.scratch_dir = directory / ("passes_" + secrets.token_hex(8))
            ass_path = directory / "subtitles.ass"
            fstxn.atomic_write(ass_path, ass, root=c.root)
            with fstxn.locked_staging(image, root=c.root) as staging:
                plan.composite(layers, staging, with_subs=True, a=frame, b=frame + 1,
                               sub_file=ass_path)
                facts = _png(staging, c.w, c.h, limit)
                current()
            current()
            # The marker is the sole success authority. Interrupted/failed PNG
            # writes never receive one; old mismatched markers cannot bless bytes.
            fstxn.atomic_write(marker, json.dumps({"schema": _SCHEMA, "render_key": key, **facts}), root=c.root)
            try:
                current()
            except BaseException:
                fstxn.unlink(marker, root=c.root)
                raise
        else:
            current()
    return {"state": "generated", "kind": "current-composite", "approximate": False,
            "path": str(image.relative_to(c.root)), "mime_type": "image/png",
            "width": c.w, "height": c.h, "frame": frame, "fps": {"num": int(c.fps), "den": 1},
            "content_version": version, "input_fingerprint": c.input_fingerprint,
            "identity_semantics": "Fast media stat identity; non-adversarial inputs",
            "render_key": key, "cache_hit": hit, **facts}


def frame_timeline(path, *, frame, expected_content_version=None, render=False,
                   max_png_bytes=_LIMIT):
    """Reference + exact current-composite PNG, only with explicit render=True.

    Defaults do not construct Ctx, probe assets, lock or create directories.
    Authorized first slice requires Linux fd-anchored writes, core-only projects,
    and current warm seg/gfx inputs. It never prepares inputs, executes plugins,
    modifies timeline/store/delivery state, or uses final/qc_clean as fallback.
    Both timeline/reference and ASCII JSON wire limits are 8 MiB; PNG delivery is
    bounded by max_png_bytes (1..8 MiB). Transient encoder files may exceed it.
    """
    reference = reference_timeline(path, global_frame=frame,
                                   expected_content_version=expected_content_version)
    result = {"schema": _SCHEMA, "operation": "frame", "ok": reference["ok"],
              "content_version": reference["content_version"], "reference": reference,
              "image": None, "diagnostics": list(reference["diagnostics"])}
    _finish(result)
    if not result["ok"]:
        return result
    if type(frame) is not int:
        _error(result, "frame.integer", "/frame", "required integer global frame")
    if type(render) is not bool:
        _error(result, "authorization.type", "/render", "render must be boolean")
    if type(max_png_bytes) is not int or not 1 <= max_png_bytes <= _LIMIT:
        _error(result, "image.limit", "/max_png_bytes", "expected byte limit in [1,8388608]")
    if not result["ok"]:
        return _finish(result)
    if not render:
        _error(result, "render.not_authorized", "/render", "explicit render=True/--render required; no renderer started")
        return _finish(result)
    try:
        result["image"] = _render(path, reference, frame, max_png_bytes)
    except KeyboardInterrupt:
        _error(result, "render.cancelled", "", "frame rendering cancelled; no success returned")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, SystemExit) as error:
        _error(result, error.code if isinstance(error, _FrameError) else "render.failed", "", str(error)[:2000])
    return _finish(result)
