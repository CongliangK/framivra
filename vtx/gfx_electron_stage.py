"""Fail-closed public Electron scheduling/completion, separate from legacy CDP fallback."""
import hashlib
import json
import re
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import events, fstxn, gfx_cli
from .gfx_backend import OUTPUT, electron_config, identity
from .gfx_electron import PAGE_ERROR_GATE, ElectronRenderError, expected_request, frame_url, render_page
from .hygiene import gfx_cache_ns, gfx_dir, gfx_key, manifest_record


def require_result(st, frames, outdir, width, height, *, expected_html=None, expected_config=None):
    """Require this invocation's complete adapter AND worker receipts, not old PNGs."""
    def reject(reason):
        detail = {k: st.get(k) for k in ("run_id", "evidence_dir", "exit_code", "failed")} if isinstance(st, dict) else st
        raise ElectronRenderError(f"Electron incomplete/failed receipt: {reason}; {detail}")
    if not isinstance(st, dict):
        reject("not an object")
    run_id = st.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
        reject("run_id")
    evidence = Path(outdir) / ".electron" / run_id
    if st.get("evidence_dir") != str(evidence.resolve()):
        reject("evidence directory")
    if (st.get("failed") != [] or st.get("exit_code") != 0
            or st.get("rendered") != len(frames) or st.get("errors") != {}
            or st.get("requested_backend") != "electron" or st.get("effective_backend") != "electron"
            or st.get("mode") != "N" or st.get("raster_mode") != "b0"
            or st.get("path_counts") != {OUTPUT: len(frames), "failed": 0}
            or st.get("page_error_gate") != PAGE_ERROR_GATE):
        reject("adapter status/backend/path/count/error gate")
    try:
        if expected_html is None or expected_config is None:
            reject("missing caller source/settings")
        binary, mode, output, timeout = expected_config
        if mode != 'b0' or output != OUTPUT:
            reject("unsupported caller settings")
        outdir = Path(outdir).absolute()
        evidence = outdir / '.electron' / run_id
        if (not frames or any(type(f) is not int or f < 0 for f in frames)
                or frames != sorted(set(frames))):
            reject("invalid caller frames")
        # Reject aliases/escapes at every evidence and PNG path, not just the
        # marker. resolve() equality also catches symlinked parent directories.
        for p in [outdir, evidence, *(evidence / name for name in
                  ('request.json', 'adapter-result.json', 'result.json'))]:
            if p.resolve(strict=True) != p:
                reject("symlink/path escape")
        request = json.loads((evidence / 'request.json').read_text(encoding='utf-8'))
        expected = expected_request(expected_html, outdir, frames, width, height, run_id,
                                    mode=mode, output=output, timeout_s=timeout)
        # JSON type-exact comparison: false/0 and 192.0/192 are not the same job.
        if json.dumps(request, sort_keys=True) != json.dumps(expected, sort_keys=True):
            reject("request differs from current caller source/settings/run/paths/frames")
        saved = json.loads((evidence / "adapter-result.json").read_text(encoding="utf-8"))
        raw = json.loads((evidence / "result.json").read_text(encoding="utf-8"))
        if saved != st:
            reject("adapter receipt mismatch")
        if (raw.get("run_id") != run_id or raw.get("schema") != "vtx.electron.navigation/1"
                or raw.get("requested_backend") != "electron" or raw.get("effective_backend") != "electron"
                or raw.get("failed") != [] or raw.get("errors") != {}
                or raw.get("mode") != "N" or raw.get("raster_mode") != "b0" or raw.get("output") != OUTPUT
                or raw.get("page_error_gate") != PAGE_ERROR_GATE or raw.get("shared_memory") is not False
                or raw.get("max_inflight") != 1
                or raw.get("viewport") != {"width": width, "height": height, "device_scale_factor": 1}
                or raw.get("page_errors") != {str(f): [] for f in frames}
                or st.get("page_errors") != raw.get("page_errors")):
            reject("worker contract")
        receipts = raw.get("frames", [])
        if [r.get("frame") for r in receipts] != frames:
            reject("missing/duplicate/out-of-order frame receipt")
        for r in receipts:
            p = Path(outdir) / f"f{r['frame']:04d}.png"
            if (type(r.get('frame')) is not int or p.resolve(strict=True) != p
                    or r.get('url') != frame_url(expected_html, r['frame'])
                    or r.get('output') != str(p)
                    or r.get("path") != OUTPUT or r.get("page_error_fence_passed") is not True
                    or r.get("baseline_veto_passed") is not True or r.get("png_semantics_passed") is not True
                    or gfx_cli.png_invalid_reason(p, width=width, height=height)
                    or r.get("sha256") != hashlib.sha256(p.read_bytes()).hexdigest()):
                reject("frame verification")
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        reject(str(exc))
    return {"run_id": run_id, "evidence_dir": str(evidence), "frames": frames,
            "requested_backend": "electron", "effective_backend": "electron",
            "path_counts": st["path_counts"], "versions": st.get("versions", {})}


def cached_record(d, n, key, width, height, backend, ns, *, expected_html=None, expected_config=None):
    """Authenticate completion and producer receipts; return the bound PNG hashes too."""
    try:
        d = Path(d).absolute()
        marker = d / gfx_cli.COMPLETE_FILE
        if (expected_html is None or expected_config is None
                or d.resolve(strict=True) != d or marker.resolve(strict=True) != marker):
            return None
        record = json.loads(marker.read_text(encoding="utf-8"))
        runs = record["render_runs"]
        if (record.get("schema") != 1 or record.get("backend_identity") != backend
                or record.get("backend_ns") != ns or not runs
                or not isinstance(record.get("sha256"), list) or len(record["sha256"]) != n
                or [f for r in runs for f in r["frames"]] != list(range(n))
                or any(r.get("requested_backend") != "electron" or r.get("effective_backend") != "electron"
                       or not re.fullmatch(r"[a-f0-9]{32}", r.get("run_id", "")) for r in runs)):
            return None
        bad, _ = gfx_cli.inspect_page(d, n, key, width, height, backend_ns=ns)
        if bad:
            return None
        # Validate the actual producer receipts through the same gate used before
        # publication. Marker summaries and generic PNG hashes are not receipts.
        for run in runs:
            evidence = d / '.electron' / run['run_id']
            adapter = json.loads((evidence / 'adapter-result.json').read_text(encoding='utf-8'))
            if require_result(adapter, run['frames'], d, width, height,
                              expected_html=expected_html, expected_config=expected_config) != run:
                return None
        return record
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ElectronRenderError):
        return None


def render_stage(c, split_page):
    binary, mode, output, timeout = electron_config()  # fail even on a warm cache
    backend, ns = identity(), gfx_cache_ns()
    c.verify_inputs("gfx")
    split_frames = max(0, int(os.environ.get("VTX_GFX_SPLIT_FRAMES", "450")))
    split_max = max(1, int(os.environ.get("VTX_GFX_SPLIT_MAX", "3")))
    workers = max(1, int(os.environ.get("VTX_GFX_PAGES", "1")))
    pages, seen, all_runs, cached = [], set(), [], []
    for tr in c.tl["video_tracks"]:
        if tr.get("kind") != "gfx":
            continue
        for cl in tr["clips"]:
            key, d, n = gfx_key(c, cl), gfx_dir(c, cl), cl["dur"]
            if d in seen:
                continue
            seen.add(d)
            record = cached_record(d, n, key, c.w, c.h, backend, ns,
                                   expected_html=c.root / cl['src'], expected_config=(binary, mode, output, timeout))
            runs = None if record is None else record['render_runs']
            if runs:
                events.cache_hit(c, 'cached_runs', frames=[0, n])
                all_runs.extend(runs)
                cached.append(key)
                print(f"✓ {Path(cl['src']).stem}[{key}]: {n} 帧已缓存（electron {ns}）")
            else:
                pages.append((cl, key, d, n))
    for cl, key, d, n in pages:
        if c.dry:
            print(f"  [dry-run] gfx {cl['src']}[{key}]: electron b0 {n} 帧 {c.w}×{c.h}; ns={ns}")
            continue
        fstxn.mkdir(d)
        gfx_cli.invalidate_page(d)
        # Every block must produce fresh receipts. Never skip a valid-looking old
        # PNG in an incomplete Electron page; one bad block prevents completion.
        sizes = split_page(n, split_frames, split_max) or [n]
        blocks, offset = [], 0
        page_id = str(events.uuid.uuid4())
        for size in sizes:
            frames = list(range(offset, offset + size))
            observation = events.task(c, 'gfx', kind='gfx_block', metadata={
                'page_id': page_id, 'backend': 'electron',
                'frame_range': [offset, offset + size]})
            blocks.append((frames, observation))
            offset += size

        def run(block):
            frames, observation = block
            def render_and_verify():
                st = render_page((c.root / cl["src"]).resolve(), d, n, width=c.w, height=c.h,
                                 frames=frames, ctx=c, electron_path=binary, mode=mode,
                                 output=output, timeout_s=timeout)
                return require_result(st, frames, d, c.w, c.h, expected_html=c.root / cl['src'],
                                      expected_config=(binary, mode, output, timeout))
            with observation.scope():
                return observation.attempt(render_and_verify)

        # Page blocks use the existing split policy; concurrency is explicit and
        # bounded by VTX_GFX_PAGES (Electron default 1, no GPU performance work).
        with ThreadPoolExecutor(min(workers, len(blocks))) as pool:
            runs = list(pool.map(run, blocks))
        gfx_cli.complete_page(d, n, key, c.w, c.h, verify_inputs=lambda: c.verify_inputs("gfx"),
                              backend_ns=ns, backend_identity=backend, render_runs=runs)
        manifest_record(c, d, "gfx", key)
        all_runs.extend(runs)
        print(f"✓ {Path(cl['src']).stem}[{key}]: electron b0 {n} 帧，split×{len(blocks)}，零回落")
    c.receipt("gfx", True, f"{len(seen)} unique gfx pages; electron b0/native_topng; zero fallback",
              extra={"requested_backend": "electron", "effective_backend": "electron",
                     "backend_identity": backend, "backend_ns": ns,
                     "render_runs": all_runs, "cached_page_keys": cached,
                     "path_counts": {OUTPUT: sum(len(r["frames"]) for r in all_runs), "failed": 0}})
