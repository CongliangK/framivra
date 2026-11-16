"""Trusted, declared-input HTML layer previews, never composite/cache authority.

Only the selected saved page is rendered. Unlisted local/network resources and
host fonts are not a dependency closure; explicit trust is not a sandbox.
"""
import base64
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
from types import SimpleNamespace

from .timeline import _error
from .timeline_reference import reference_timeline, _wire_size
from .timeline_frame import _SCHEMA, _LIMIT, _FrameError, _digest, _finish

_IDENTITY = "Trusted HTML and declared dependencies; not a dependency closure or sandbox"
_ADDRESS = re.compile(r"/video_tracks/(0|[1-9][0-9]*)/clips/(0|[1-9][0-9]*)\Z")
_INPUT_LIMIT = 32 * 1024 * 1024
_TOTAL_LIMIT = 64 * 1024 * 1024
_DEPENDENCY_LIMIT = 1024


def _read_regular(path, limit, *, code="inputs.invalid"):
    """Transient bounded regular-file snapshot, including mutation detection.

    This portable reader deliberately does not extend Linux png_artifact's
    anchored-read contract or claim hostile-filesystem protection on macOS.
    """
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) |
                 getattr(os, "O_BINARY", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise _FrameError(code, "input is not a regular file")
        if before.st_size > limit:
            raise _FrameError(code, "file exceeds bounded preview byte limit")
        chunks, count = [], 0
        while count <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
        after = os.fstat(fd)
        fields = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size,
                            s.st_mtime_ns, s.st_ctime_ns)
        if count > limit:
            raise _FrameError(code, "file exceeds bounded preview byte limit")
        if fields(before) != fields(after) or count != before.st_size:
            raise _FrameError("snapshot.changed", "file changed during preview read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _local(root, value):
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise _FrameError("inputs.path", "expected project-root-relative file path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise _FrameError("inputs.path", "expected project-root-relative file path")
    path = (root / relative).resolve(strict=True)
    if root not in path.parents:
        raise _FrameError("inputs.path", "input escaped project root")
    return path


def _snapshot(path, root, stored, clip):
    # Re-resolve declarations each time, so changing a symlink target is visible.
    timeline = _read_regular(path, _LIMIT)
    declarations = []
    for owner in (stored["project"], clip):
        values = owner.get("cache_dependencies", [])
        if not isinstance(values, list) or len(values) > _DEPENDENCY_LIMIT:
            raise _FrameError("inputs.dependencies", "expected bounded cache_dependencies list")
        declarations.extend(values)
    if len(declarations) > _DEPENDENCY_LIMIT:
        raise _FrameError("inputs.dependencies", "too many declared preview dependencies")
    inputs, total = [], 0
    for value in [clip["src"], *declarations]:
        local = _local(root, value)
        data = _read_regular(local, min(_INPUT_LIMIT, _TOTAL_LIMIT - total))
        total += len(data)
        inputs.append({"declaration": value, "path": local.relative_to(root).as_posix(),
                       "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    return {"timeline": hashlib.sha256(timeline).hexdigest(), "declared_inputs": inputs}


def _browser():
    from .render.browser_paths import find_chrome_candidate
    from .render.gfx_environment import CHROME_VISUAL_FLAGS
    candidate = find_chrome_candidate(sys.platform)
    if not candidate:
        raise _FrameError("environment.unavailable", "Chrome is unavailable")
    executable = Path(shutil.which(candidate) or candidate).resolve(strict=True)
    info = executable.stat()
    if not stat.S_ISREG(info.st_mode):
        raise _FrameError("environment.unavailable", "Chrome is not a regular executable")
    return str(executable), {"backend": "cdp", "executable": str(executable),
                             "size": info.st_size, "mtime_ns": info.st_mtime_ns,
                             "platform": sys.platform, "visual_flags": list(CHROME_VISUAL_FLAGS),
                             "identity_scope": "browser path/stat; no environment certificate or reuse"}


def _render(path, reference, address, frame, limit):
    from .pipeline.constraints import size_error, fps_error
    from .runtime import fstxn
    from .runtime.proc_supervisor import supervision_available, WORKER_LEDGER
    stored = json.loads(reference["document"]["raw"])
    match = _ADDRESS.fullmatch(address)
    if not match:
        raise _FrameError("html.target", "expected canonical /video_tracks/N/clips/M address")
    ti, ci = map(int, match.groups())
    track = stored["video_tracks"][ti]
    clip = track["clips"][ci]
    if track["kind"] != "gfx" or clip.get("format", "html") != "html":
        raise _FrameError("html.target", "selected block must be a saved gfx HTML clip")
    project = stored["project"]
    w, h, fps = project.get("width"), project.get("height"), project["fps"]
    if type(w) is not int or type(h) is not int or size_error(w, h) or fps_error(fps):
        raise _FrameError("render.validation", "unsupported project dimensions/fps")
    if any(type(clip.get(k)) is not int for k in ("dst_in", "dur")) or clip["dur"] <= 0:
        raise _FrameError("render.validation", "clip requires integer dst_in and positive dur")
    if project.get("gfx_dependency_policy", "legacy") != "legacy":
        raise _FrameError("html.policy", "HTML preview supports legacy declared dependencies only; strict is unsupported")
    if not supervision_available():
        raise _FrameError("platform.unsupported", "HTML preview requires supported browser supervision")
    path = Path(os.path.abspath(Path(path).expanduser()))
    root = path.parent
    if root.resolve(strict=True) != root:
        raise _FrameError("inputs.path", "project root must be canonical")
    page = _local(root, clip.get("src"))
    if page.suffix.lower() != ".html":
        raise _FrameError("html.target", "selected page must have .html extension")
    before = _snapshot(path, root, stored, clip)
    if "sha256:" + before["timeline"] != reference["content_version"]:
        raise _FrameError("snapshot.changed", "timeline changed before HTML preview")
    chrome, browser = _browser()
    local = frame - clip["dst_in"]
    fingerprint = _digest(before)
    key = _digest({"schema": _SCHEMA, "kind": "html-layer", "inputs": before,
                   "address": address, "frame": frame, "local_frame": local,
                   "duration_frames": clip["dur"], "width": w, "height": h,
                   "fps": fps, "browser": browser, "identity_semantics": _IDENTITY})
    directory = root / "cache" / "preview" / ("html_" + secrets.token_hex(16))
    work = directory / "work"
    # Reject namespace aliases before mkdir. Native renderer and supervisor use
    # this same root/cache pair; no normal gfx namespace is reachable via ctx.
    if directory.resolve() != directory:
        raise _FrameError("inputs.path", "preview namespace must be canonical")
    fstxn.mkdir(work, root=root)
    success = False
    try:
        from .render.gfx_render import render_page
        with redirect_stdout(sys.stderr):
            stats = render_page(str(page), str(work / "frames"), n_frames=clip["dur"],
                                chrome_path=chrome, frames=[local], tabs=1, width=w, height=h,
                                ctx=SimpleNamespace(root=root, cache=work))
        if stats.get("failed"):
            raise _FrameError("render.failed", "native HTML renderer failed; no alternate renderer is permitted")
        data = _read_regular(work / "frames" / ("f%04d.png" % local), limit, code="image.limit")
        from .render.gfx_cli import _png_stream_invalid_reason
        reason = _png_stream_invalid_reason(io.BytesIO(data), len(data), width=w, height=h)
        if reason:
            raise _FrameError("image.invalid", str(reason))
        if _snapshot(path, root, stored, clip) != before or _browser() != (chrome, browser):
            raise _FrameError("snapshot.changed", "timeline/page/declared dependencies/browser changed during preview")
        # A retained ledger is recovery evidence, not permission to publish success.
        if any(work.rglob(WORKER_LEDGER)):
            raise _FrameError("render.unsettled", "browser recovery ledger remains; preview evidence preserved")
        image = directory / "image.png"
        fstxn.atomic_write(image, data, root=root)
        success = True
        return ({"state": "generated", "kind": "html-layer", "approximate": False,
                 "path": image.relative_to(root).as_posix(), "mime_type": "image/png",
                 "width": w, "height": h, "frame": frame, "fps": {"num": int(fps), "den": 1},
                 "content_version": reference["content_version"], "input_fingerprint": fingerprint,
                 "identity_semantics": _IDENTITY, "render_key": key, "cache_hit": False,
                 "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                 "browser_provenance": browser,
                 "layer": {"block_address": address, "local_frame": local,
                           "duration_frames": clip["dur"], "html": page.relative_to(root).as_posix()}}, data)
    finally:
        # Only our random request directory is eligible. Never erase a remaining
        # supervisor ledger/profile, even when its process status is uncertain.
        if not any(directory.rglob(WORKER_LEDGER)):
            target = work if success else directory
            if target.parent not in (directory, directory.parent) or target.resolve() != target:
                raise _FrameError("cleanup.failed", "preview cleanup target changed")
            shutil.rmtree(target)


def frame_html_timeline(path, *, frame, expected_content_version, render,
                        max_png_bytes, include_png, html_block_address, trust_html):
    canonical = isinstance(html_block_address, str) and _ADDRESS.fullmatch(html_block_address)
    reference = reference_timeline(path, global_frame=frame,
                                   block_addresses=[html_block_address] if canonical else None,
                                   expected_content_version=expected_content_version)
    result = {"schema": _SCHEMA, "operation": "frame", "ok": reference["ok"],
              "content_version": reference["content_version"], "reference": reference,
              "image": None, "diagnostics": list(reference["diagnostics"])}
    _finish(result)
    if not result["ok"]:
        return result
    if not canonical:
        _error(result, "html.target", "/html_block_address", "expected canonical saved video clip address")
    if type(frame) is not int:
        _error(result, "frame.integer", "/frame", "required integer global frame")
    if type(render) is not bool or type(trust_html) is not bool:
        _error(result, "authorization.type", "", "render and trust_html must be boolean")
    if type(include_png) is not bool:
        _error(result, "image.inline_type", "/include_png", "include_png must be boolean")
    if type(max_png_bytes) is not int or not 1 <= max_png_bytes <= _LIMIT:
        _error(result, "image.limit", "/max_png_bytes", "expected byte limit in [1,8388608]")
    if not render:
        _error(result, "render.not_authorized", "/render", "explicit render=True/--render required")
    if not trust_html:
        _error(result, "html.not_trusted", "/trust_html", "explicit trust_html=True/--trust-html required; HTML is not sandboxed")
    if not result["ok"]:
        return _finish(result)
    try:
        image, data = _render(path, reference, html_block_address, frame, max_png_bytes)
        result["image"] = image
        if include_png:
            image["inline"] = {"encoding": "base64", "data": ""}
            if _wire_size(result, _LIMIT - 1) + 4 * ((len(data) + 2) // 3) >= _LIMIT:
                result.update(ok=False, reference=None, image=None, diagnostics=[])
                _error(result, "output.limit", "", "frame result exceeds JSON wire byte limit", limit_bytes=_LIMIT)
            else:
                image["inline"]["data"] = base64.b64encode(data).decode("ascii")
    except KeyboardInterrupt:
        result["image"] = None
        _error(result, "render.cancelled", "", "HTML rendering cancelled; no success returned")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, SystemExit) as error:
        result["image"] = None
        _error(result, getattr(error, "code", "render.failed"), "", str(error)[:2000])
    return _finish(result)
