"""Electron-only Mode N adapter; public routing/cache live in gfx_backend/stage.

No Chrome fallback and no cache hits: every requested frame navigates the original
HTML with ?f=. The caller owns output isolation/cache identity and supplies a Ctx
for the existing process supervisor. Only electron_native_topng is supported;
the legacy shared helper path is NOT a verified production output route.
"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import time
import uuid

from .gfx_cli import png_invalid_reason
from .proc_supervisor import SupervisedProcessGroup, supervision_available

WORKER_DIR = Path(__file__).resolve().parents[1] / "experiments/gfx_electron_worker"
PAGE_ERROR_GATE = "cdp-html-unhandled-exceptions/2"


class ElectronRenderError(RuntimeError):
    """Explicit backend cannot start or cannot attest its output; never fallback."""


def expected_request(html_path, outdir, frames, width, height, run_id, *, mode, output, timeout_s):
    """Build the host job from caller-owned inputs, never from saved receipts."""
    page, directory = Path(html_path).resolve(strict=True), Path(outdir).absolute()
    return {"run_id": run_id, "page": str(page), "out_dir": str(directory),
            "result": str(directory / ".electron" / run_id / "result.json"),
            "width": width, "height": height, "frames": list(frames),
            "timeout_ms": int(timeout_s * 1000), "mode": mode, "output": output,
            "source_sha256": hashlib.sha256(page.read_bytes()).hexdigest()}


def frame_url(html_path, frame):
    # Node pathToFileURL + URL.searchParams.set('f', String(frame)), on Linux.
    from urllib.parse import quote
    return 'file://' + quote(str(Path(html_path).resolve(strict=True)), safe="/!$&'()*+,-.:;=@_~") + f'?f={frame}'


def render_page(html_path, outdir, n_frames, *, width=1920, height=1080,
                frames=None, ctx=None, timeout_s=15, electron_path=None,
                mode="b0", output="electron_native_topng", progress=None):
    """Render selected frames to f{frame:04d}.png; return path_counts/failed.

    ctx is required (cache/tmp profile, independent guarded process group).
    Missing binary/invalid options raise ElectronRenderError or ValueError.
    Runtime failures return failed frame IDs and diagnostic errors, never CLI
    fallback. Old files are not evidence: only this run's verified receipts count.
    Caller MUST treat failed as a hard failure, even if an old file is present.
    Logs/receipts are per-run under outdir/.electron/<run_id>/.
    """
    if ctx is None or not supervision_available():
        raise ElectronRenderError("Electron requires a Ctx and Linux /proc supervision")
    if mode not in ("b0", "b1") or output != "electron_native_topng":
        raise ValueError("unsupported Electron mode/output (shared helper not verified)")
    if (type(n_frames) is not int or not 0 <= n_frames <= 20000
            or type(width) is not int or type(height) is not int
            or not 64 <= width <= 3840 or not 2 <= height <= 2160
            or width * height > 8294400):
        raise ValueError("invalid frame count/viewport")
    requested = list(range(n_frames) if frames is None else frames)
    if (len(requested) > 20000 or len(set(requested)) != len(requested)
            or any(type(f) is not int or not 0 <= f < n_frames for f in requested)):
        raise ValueError("invalid/duplicate requested frames")
    if not 0.5 <= timeout_s <= 120:
        raise ValueError("timeout_s must be between 0.5 and 120")
    page = Path(html_path).resolve(strict=True)
    if not page.is_file():
        raise ValueError("HTML must be a file")
    binary = Path(electron_path or os.environ.get("VTX_ELECTRON_BIN") or
                  WORKER_DIR / "node_modules/electron/dist/electron").resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ElectronRenderError(f"Electron executable unavailable: {binary}")
    outdir = Path(outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    evidence = outdir / ".electron" / run_id
    evidence.mkdir(parents=True)
    result_path = evidence / "result.json"
    job = expected_request(page, outdir, requested, width, height, run_id,
                           mode=mode, output=output, timeout_s=timeout_s)
    (evidence / "request.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    started = time.monotonic()

    def argv(run_dir, profile_dir):
        job_path = run_dir / "job.json"
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        return [str(binary), str(WORKER_DIR / "navigation_worker.mjs"),
                f"--job={job_path}", f"--profile={profile_dir}"]

    env = dict(os.environ)
    # An inherited Electron-as-Node flag would bypass the actual browser.
    env.pop("ELECTRON_RUN_AS_NODE", None)
    with (evidence / "stdout.log").open("wb") as stdout, (evidence / "stderr.log").open("wb") as stderr:
        with SupervisedProcessGroup(ctx, argv, backend_id="electron_nav", kill_grace=2,
                                    stdout=stdout, stderr=stderr, env=env) as sg:
            try:
                rc = sg.proc.wait(timeout=max(10, len(requested) * timeout_s + 10))
            except subprocess.TimeoutExpired:
                rc = -1  # context manager terminates ONLY this supervised tree
    raw = {}
    if result_path.is_file():
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    valid_run = (raw.get("run_id") == run_id
                 and raw.get("schema") == "vtx.electron.navigation/1"
                 and raw.get("requested_backend") == "electron"
                 and raw.get("effective_backend") == "electron"
                 and raw.get("output") == output and raw.get("mode") == "N"
                 and raw.get("page_error_gate") == PAGE_ERROR_GATE)
    receipts = raw.get("frames", []) if valid_run else []
    errors = dict(raw.get("errors", {})) if valid_run else {}
    page_errors = raw.get("page_errors", {}) if valid_run else {}
    successful = set()
    for receipt in receipts:
        f = receipt.get("frame")
        if f not in requested or f in successful:
            continue
        if (receipt.get("page_error_fence_passed") is not True
                or page_errors.get(str(f)) or f in raw.get("failed", [])):
            errors.setdefault(str(f), "page_error_gate:unattested_or_failed")
            continue
        target = outdir / f"f{f:04d}.png"
        try:
            reason = png_invalid_reason(target, width=width, height=height)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if (reason or digest != receipt.get("sha256") or receipt.get("path") != output
                    or receipt.get("url") != frame_url(page, f)
                    or receipt.get("output") != str(target) or target.resolve() != target):
                errors[str(f)] = f"output_verification:{reason or 'receipt mismatch'}"
                continue
        except OSError as exc:
            errors[str(f)] = f"output_verification:{exc}"
            continue
        successful.add(f)
        if progress:
            progress(f"electron frame {f} verified")
    # A crashed worker invalidates even earlier receipts: a batch is not success
    # merely because some old/new files exist. Ordinary per-frame rejection (1)
    # retains attested successes so callers can report exactly which frames failed.
    if rc not in (0, 1) or not valid_run:
        successful.clear()
        errors["worker"] = f"exit={rc}; valid_run={valid_run}; see {evidence}"
    failed = sorted(set(requested) - successful)
    elapsed = (time.monotonic() - started) * 1000
    summary = {"n": n_frames, "rendered": len(successful), "failed": failed,
               "requested_backend": "electron", "effective_backend": "electron" if valid_run else None,
               "path_counts": {output: len(successful), "failed": len(failed)},
               "run_id": run_id, "exit_code": rc, "errors": errors,
               "page_error_gate": raw.get("page_error_gate"), "page_errors": page_errors,
               "evidence_dir": str(evidence), "versions": raw.get("versions", {}),
               "ms_total": elapsed, "ms_per_frame": elapsed / max(1, len(requested)),
               "mode": "N", "raster_mode": mode, "max_inflight": 1}
    (evidence / "adapter-result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
