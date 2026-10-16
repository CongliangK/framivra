"""Strict, plan-bound Golden evidence validation (#62).

This validates renderer-produced receipts, not their authors' honesty. A runner must
freeze the plan before starting either arm, record actual runtime evidence, and
write the completed receipt only after all frames succeed. It must not manufacture
receipts by inspecting an old cache. No timestamps establish render provenance.
The versioned wire format and trust boundary live in docs/gfx-evidence.md.
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

from PIL import Image
from vtx import env_manifest
from vtx.cache_state import declared_dependencies


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def resolve_evidence_path(path):
    """Resolve required inputs, normalizing path-specific failures across Python versions."""
    try:
        # Inputs must already exist. Non-strict resolution silently accepts symlink
        # loops on Python 3.13+, unlike earlier versions. Output paths use atomic_write.
        return Path(path).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"cannot resolve evidence path {path}: {error}") from error


def input_fingerprint(c, clips):
    paths = sorted(set([s for s, _ in clips] + declared_dependencies(c)))
    timeline_bytes = c.tl_path.read_bytes()
    require(timeline_bytes == c.timeline_bytes, "on-disk timeline changed since context creation")
    return digest({"timeline_sha256": hashlib.sha256(timeline_bytes).hexdigest(), "files": [
        [p, hashlib.sha256((c.root / p).read_bytes()).hexdigest()] for p in paths]})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def instances(items):
    require(isinstance(items, list) and bool(items), "instances must be a nonempty list")
    out = {}
    for item in items:
        require(isinstance(item, dict), "instance must be an object")
        src, dur = item.get("src"), item.get("dur")
        require(isinstance(src, str) and bool(src) and type(dur) is int and dur > 0,
                "invalid (src,dur)")
        require((src, dur) not in out, "duplicate (src,dur)")
        out[src, dur] = item
    return out


def _nonempty_text(value):
    return (isinstance(value, str) and bool(value.strip())
            and value.lower() not in ("unknown", "unavailable", "none", "null"))


def _observed_backend(truth):
    """Conservatively classify supplied runtime observations, never launch flags.

    Device inventories are not active renderer evidence. Ambiguous/unsupported
    observations fail closed and require a producer with better runtime probes.
    """
    evidence, source = truth["evidence"], truth["source"]
    for key in env_manifest.SOURCE_REQUIRED_EVIDENCE[source]:
        require(isinstance(evidence.get(key), dict) and bool(evidence[key]),
                f"missing substantive {source} evidence: {key}")
    if source == "cdp":
        gpu = evidence["system_info"].get("gpu")
        require(isinstance(gpu, dict) and bool(gpu), "missing CDP GPU observations")
        features = gpu.get("featureStatus")
        aux = gpu.get("auxAttributes", {})
        renderer = aux.get("glRenderer") if isinstance(aux, dict) else None
        version = evidence["browser_version"].get("product")
        feature_key = "gpu_compositing"
    elif source == "electron":
        features = evidence["gpu_feature_status"]
        aux = evidence["gpu_info_complete"].get("auxAttributes", {})
        renderer = aux.get("glRenderer") if isinstance(aux, dict) else None
        version = evidence.get("chrome_version")
        feature_key = "gpu_compositing"
    else:
        probe = evidence["gl_probe"]
        features = probe.get("chrome_gpu_features")
        webgl = probe.get("webgl", {})
        renderer = webgl.get("renderer") if isinstance(webgl, dict) else None
        version = evidence["cef_version"].get("chromium")
        feature_key = "Compositing"
    require(isinstance(features, dict) and _nonempty_text(features.get(feature_key)),
            "missing actual compositing feature status")
    status = features[feature_key].lower()
    software = status in ("disabled_software", "software_only") or "software only" in status
    hardware = status in ("enabled", "enabled_on") or status == "hardware accelerated"
    require(software or hardware, "unverifiable compositing feature status")
    text = renderer.lower() if _nonempty_text(renderer) else ""
    if "swiftshader" in text:
        backend = "swiftshader"
    elif any(s in text for s in ("llvmpipe", "softpipe", "software")):
        require(software, "software renderer contradicts hardware compositing")
        backend = "software_skia"
    elif "vulkan" in text:
        require(hardware, "Vulkan renderer contradicts software compositing")
        backend = "vulkan"
    elif any(s in text for s in ("nvidia", "geforce", "quadro", "tesla")):
        require(hardware, "hardware renderer contradicts software compositing")
        backend = "nvidia_egl"
    else:
        require(software and not text, "unverifiable actual renderer")
        backend = "software_skia"
    require(backend == truth["effective_backend"], "claimed backend contradicts runtime observations")
    versions = [re.search(r"[0-9]+(?:\.[0-9]+){3}", str(v))
                for v in (version, truth.get("chromium_version"))]
    require(all(versions) and versions[0].group() == versions[1].group(),
            f"{source} full version evidence mismatch")


def _host_controls(m):
    os_info = m.get("os_kernel")
    require(isinstance(os_info, dict) and all(_nonempty_text(os_info.get(k))
            for k in ("system", "release", "version", "machine")), "missing OS evidence")
    require(_nonempty_text(m.get("cpu_model")), "missing CPU evidence")
    display = m.get("display_server")
    require(isinstance(display, dict) and display.get("session_type") in
            ("none", "x11", "wayland") and isinstance(display.get("env"), dict),
            "missing display evidence")
    denv = display["env"]
    require(set(("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE")) <= set(denv),
            "incomplete display evidence")
    session = display["session_type"]
    if session == "none":
        require(not any(denv.values()), "headless display evidence contradiction")
    elif session == "x11":
        require(_nonempty_text(denv["DISPLAY"]) and isinstance(display.get("xdpyinfo"), dict)
                and display["xdpyinfo"].get("available") is True, "missing X11 evidence")
    else:
        require(_nonempty_text(denv["WAYLAND_DISPLAY"]), "missing Wayland evidence")
    gpu = m.get("gpu_driver_and_vram")
    if gpu == {"status": "not_applicable", "reason": "software_backend"}:
        require(m["runtime_backend_truth"]["effective_backend"] in ("software_skia", "swiftshader"),
                "GPU evidence required for hardware backend")
    else:
        require(isinstance(gpu, dict) and isinstance(gpu.get("gpus"), list) and bool(gpu["gpus"]),
                "missing GPU host evidence")
        for device in gpu["gpus"]:
            require(isinstance(device, dict) and all(_nonempty_text(device.get(k))
                    for k in ("name", "driver_version"))
                    and type(device.get("memory_total_mib")) in (int, float)
                    and device["memory_total_mib"] > 0, "invalid GPU host evidence")


def _manifest(run, expected, viewport):
    m = run.get("env_manifest")
    require(isinstance(m, dict) and m.get("schema") == "vtx.env_manifest/1",
            "missing/unknown env_manifest")
    require(m.get("run_id") == run["run_id"], "manifest run identity mismatch")
    t = m.get("runtime_backend_truth")
    require(isinstance(t, dict), "missing runtime_backend_truth")
    # Recheck the collector's required internal evidence and parsed version.
    normalized = env_manifest._runtime_backend_truth(m.get("backend_id"), t)
    _observed_backend(t)
    for field in ("effective_backend", "chromium_version", "chromium_major", "viewport", "color_space"):
        require(t.get(field) == normalized[field] and t.get(field) is not None,
                f"invalid runtime {field}")
    require(t["viewport"] == viewport, "manifest viewport does not match timeline")
    require(t.get("backend_id") == m.get("backend_id"), "backend identity mismatch")
    require(t.get("color_space") == "srgb", "strict evidence requires srgb")
    # The normalized contract preserves an unavailable marker through repeated
    # normalization; a legacy default must not become an explicit observation.
    require(normalized.get("color_space_contract") == {
        "source": "explicit_config", "value": "srgb", "measured": False},
        "strict evidence requires explicit sRGB configuration, not a default or measured-color claim")
    require(m.get("backend_id") == expected.get("backend_id"), "actual/declared backend_id mismatch")
    for field in ("effective_backend", "chromium_version"):
        require(t[field] == expected.get(field), f"actual/declared {field} mismatch")
    require(isinstance(run.get("config"), dict) and run["config"] == expected.get("config"),
            "actual/declared config mismatch")
    fonts = m.get("fonts_fingerprint")
    require(isinstance(fonts, dict), "missing font evidence")
    for family in (*env_manifest.REQUIRED_FONT_FAMILIES, env_manifest.GENERIC_FALLBACK_FAMILY):
        f = (fonts.get("required") or {}).get(family) or fonts.get(family)
        require(isinstance(f, dict), f"missing font evidence: {family}")
        if f.get("status") == "missing" and family != env_manifest.GENERIC_FALLBACK_FAMILY:
            continue
        files = f.get("files")
        require(f.get("status") == "ok" and isinstance(files, dict) and bool(files)
                and all(isinstance(h, str) and len(h) == 64
                        and all(ch in "0123456789abcdef" for ch in h) for h in files.values()),
                f"unverifiable font files: {family}")
    require(not env_manifest._font_incompatibilities(m.get("fonts_fingerprint"),
                                                    m.get("fonts_fingerprint")),
            "missing font evidence")
    _host_controls(m)
    return m


def _producer_binding(run, plan, viewport):
    """Extra mandatory checks for the bounded #63 producer profile only."""
    from vtx import events, gfx_runtime
    from vtx.infra import Ctx
    from vtx.hygiene import gfx_key
    timeline = run.get('timeline_path')
    require(isinstance(timeline, str) and Path(timeline).is_absolute(), 'missing producer timeline')
    producer_context = Ctx(Path(timeline))
    required = list(instances(plan.get('required_instances')))
    require(input_fingerprint(producer_context, required) == plan['input_fingerprint'],
            'producer timeline/input drift')
    binding = run.get('producer_binding')
    require(isinstance(binding, dict) and set(binding) == {'runtime', 'page_witnesses'},
            'missing experiment producer binding')
    runtimes, witnesses = binding['runtime'], binding['page_witnesses']
    require(isinstance(runtimes, dict) and bool(runtimes) and len(runtimes) <= 256,
            'invalid runtime evidence set')
    require(isinstance(witnesses, dict) and bool(witnesses) and len(witnesses) <= 32768,
            'invalid page witness set')
    for key, raw in runtimes.items():
        require([arg for arg in raw.get('effective_argv', []) if arg.startswith('--force-color-profile')] ==
                ['--force-color-profile=srgb'], 'actual browser color configuration missing or conflicting')
        require(digest(raw) == key and raw.get('run_id') == run['run_id'] and
                gfx_runtime.identity(raw) == plan.get('runtime_identity'), 'runtime evidence mismatch')
    width, height = (int(part) for part in viewport.split('x'))
    for key, witness in witnesses.items():
        require(digest(witness) == key and witness.get('source') == 'page-cdp-eval' and
                witness.get('viewport') == {'width': width, 'height': height, 'device_scale': 1} and
                witness.get('alpha_path') in ('opaque', 'override', 'twoshot', 'canvas'),
                'page observation mismatch')
    path = run.get('events_path')
    require(isinstance(path, str) and Path(path).is_absolute(), 'missing producer event path')
    with Path(path).open('rb') as stream:
        data = stream.read(128 * 1024 * 1024 + 1)
    require(len(data) <= 128 * 1024 * 1024 and hashlib.sha256(data).hexdigest() == run.get('events_sha256'),
            'producer event digest/size mismatch')
    parsed = events.read_events(data.decode('utf-8'))
    require(parsed['complete'] and parsed['exit_code'] == 0 and parsed['run_id'] == run.get('execution_run_id'),
            'producer event stream is not successfully closed')
    commits = {row['seq']: row['extensions'] for row in parsed['events'] if row['event'] == 'frame_commit'}
    require(bool(commits), 'no observed publications')
    for fields in commits.values():
        require(fields.get('runtime_sha256') in runtimes and fields.get('page_witness_sha256') in witnesses,
                'unbound publication observation')
    for instance in run['instances']:
        expected_key = gfx_key(producer_context, {'src': instance['src'], 'dur': instance['dur']})
        for frame, record in instance['frames'].items():
            fields = commits.get(record.get('publication_seq'))
            require(isinstance(fields, dict) and fields['gfx_key'] == expected_key and
                    fields['frame_index'] == int(frame) and
                    fields['frame_origin'] == record['origin'] and all(fields.get(key) == record.get(key)
                    for key in ('gfx_key', 'png_sha256', 'runtime_sha256', 'page_witness_sha256')),
                    'completion is not bound to publication event')
            path = Path(instance['frame_dir']) / ('f' + frame + '.png')
            with path.open('rb') as stream:
                raw = stream.read(128 * 1024 * 1024 + 1)
            require(len(raw) <= 128 * 1024 * 1024 and hashlib.sha256(raw).hexdigest() == record['png_sha256'],
                    'published PNG bytes changed')
    manifest = run['env_manifest']
    actual_host = {field: manifest.get(field) for field in ('os_kernel', 'cpu_model', 'display_server',
        'gpu_driver_and_vram', 'fonts_fingerprint', 'font_configuration_files',
        'font_environment', 'git_sha', 'git_clean')}
    require(actual_host == plan.get('host_identity') and manifest.get('git_clean') is True,
            'frozen source/host/font configuration mismatch')


def verify(c, clips, golden, plan, candidate):
    """Return a fail-closed, serializable report, including successfully compared counts."""
    started = time.perf_counter()
    report = {"schema": "vtx.gfx_evidence_report/1", "ok": False,
              "same_version_causal": False, "hashed_frames": 0, "compared_frames": 0,
              "different_frames": [], "missing_frames": [], "unknown_provenance": [],
              "uncovered_instances": [], "errors": [], "confounders": []}
    try:
        require(isinstance(plan, dict) and plan.get("schema") == "vtx.gfx_comparison_plan/1",
                "missing/unknown comparison plan")
        required = instances(plan.get("required_instances"))
        report["uncovered_instances"] = [{"src": s, "dur": d} for s, d in sorted(set(clips) - set(required))]
        require(set(required) == set(clips), "plan must cover exactly the timeline gfx instances")
        require(plan.get("input_fingerprint") == input_fingerprint(c, clips), "input fingerprint mismatch")
        plan_hash = digest(plan)
        report["plan_sha256"] = plan_hash
        require(isinstance(golden, dict), "golden must be an object")
        meta = golden.get("meta", {})
        require(meta.get("fps") == c.fps and meta.get("viewport") == f"{c.w}x{c.h}",
                "golden project identity mismatch")
        baseline = meta.get("run_evidence")
        require(isinstance(baseline, dict), "legacy golden has no strict run evidence")
        arms = plan.get("arms")
        require(isinstance(arms, dict) and set(arms) == {"baseline", "candidate"}, "invalid plan arms")
        manifests, runs = {}, {"baseline": baseline, "candidate": candidate}
        maps = {}
        for side, run in runs.items():
            expected = arms[side]
            require(isinstance(expected, dict) and isinstance(run, dict), "invalid arm/run")
            require(run.get("schema") == "vtx.gfx_run/1" and run.get("complete") is True,
                    f"{side}: no completed run receipt")
            require(isinstance(run.get("run_id"), str) and bool(run["run_id"])
                    and run["run_id"] == expected.get("run_id"), f"{side}: run identity mismatch")
            require(run.get("plan_sha256") == plan_hash, f"{side}: plan not bound to run")
            require(run.get("input_fingerprint") == plan["input_fingerprint"], f"{side}: input mismatch")
            manifests[side] = _manifest(run, expected, f"{c.w}x{c.h}")
            if expected.get("config", {}).get("profile") == "issue63-short-four-pages-v1":
                _producer_binding(run, plan, f"{c.w}x{c.h}")
            require(run.get("env_sha256") == digest(run["env_manifest"]), f"{side}: environment digest mismatch")
            raw_instances = run.get("instances")
            maps[side] = {} if raw_instances == [] else instances(raw_instances)
            report["uncovered_instances"].extend({"side": side, "src": s, "dur": d}
                for s, d in sorted(set(required) - set(maps[side])))
            require(not (set(maps[side]) - set(required)), f"{side}: unexpected instances")
        require(not report["uncovered_instances"], "incomplete run instance coverage")
        require(baseline["run_id"] != candidate["run_id"], "baseline and candidate share run_id")
        require(meta.get("env_manifest") == baseline["env_manifest"], "golden baseline manifest mismatch")
        axes = plan.get("variable_axes")
        require(isinstance(axes, list) and all(isinstance(x, str) for x in axes)
                and len(axes) == len(set(axes)), "invalid variable_axes")
        ba, ca = arms["baseline"], arms["candidate"]
        require(set(ba.get("config", {})) == set(ca.get("config", {})), "config keys must match")
        allowed = {"backend_id", "effective_backend", "chromium_version"} | {
            "config." + k for k in ba["config"]}
        require(set(axes) <= allowed, "unknown or forbidden comparison axis")
        changed = {k for k in ("backend_id", "effective_backend", "chromium_version") if ba[k] != ca[k]}
        changed |= {"config." + k for k in ba["config"] if ba["config"][k] != ca["config"][k]}
        require(changed <= set(axes), "undeclared experimental difference")
        ma, mb = manifests["baseline"], manifests["candidate"]
        require(not env_manifest._font_incompatibilities(ma.get("fonts_fingerprint"),
                                                        mb.get("fonts_fingerprint")), "font drift")
        # Host state is controlled, not a blanket ignored environment mismatch.
        for field in ("os_kernel", "cpu_model", "display_server", "gpu_driver_and_vram"):
            require(field in ma and field in mb and ma[field] == mb[field], f"environment drift: {field}")
        if "chromium_version" in changed:
            report["confounders"].append({"axis": "chromium_version", "baseline": ba["chromium_version"],
                                          "candidate": ca["chromium_version"]})
        report["same_version_causal"] = not report["confounders"]
        report["run_ids"] = {s: r["run_id"] for s, r in runs.items()}
        report["actual_environments"] = manifests
        file_ids = {side: set() for side in runs}
        for src, dur in required:
            inst = golden.get("pages", {}).get(src, {}).get("durs", {}).get(str(dur))
            keys = {f"{i:04d}" for i in range(dur)}
            if not isinstance(inst, dict):
                report["uncovered_instances"].append({"side": "golden", "src": src, "dur": dur})
                continue
            golden_frames = inst.get("frames")
            if isinstance(golden_frames, dict):
                report["missing_frames"].extend({"side": "golden", "src": src, "dur": dur,
                    "frame": fk, "reason": "absent Golden frame"} for fk in sorted(keys - set(golden_frames)))
            if not isinstance(golden_frames, dict) or set(golden_frames) != keys:
                report["errors"].append(f"golden lacks exact full-frame coverage: {src} dur={dur}")
                continue
            require(isinstance(inst, dict) and inst.get("dur") == dur and inst.get("mode") == "full"
                    and isinstance(inst.get("frames"), dict) and set(inst["frames"]) == keys,
                    f"golden lacks exact full-frame coverage: {src} dur={dur}")
            dirs = {}
            complete = True
            for side in runs:
                ri = maps[side][src, dur]
                require(isinstance(ri.get("frame_dir"), str) and Path(ri["frame_dir"]).is_absolute(),
                        "frame_dir must be absolute")
                dirs[side] = resolve_evidence_path(ri["frame_dir"])
                frames = ri.get("frames")
                if isinstance(frames, dict):
                    report["missing_frames"].extend({"side": side, "src": src, "dur": dur,
                        "frame": fk, "reason": "absent completion frame"} for fk in sorted(keys - set(frames)))
                if not isinstance(frames, dict) or set(frames) != keys:
                    report["errors"].append(f"{side}: completion frame set mismatch: {src} dur={dur}")
                    complete = False
            if not complete:
                continue
            require(dirs["baseline"] != dirs["candidate"], "candidate uses baseline frame root")
            actual = {s: {} for s in runs}
            for fk in sorted(keys):
                paths = {s: d / f"f{fk}.png" for s, d in dirs.items()}
                if all(p.is_file() for p in paths.values()):
                    require(not paths["baseline"].samefile(paths["candidate"]), "candidate aliases baseline frame")
                for side, path in paths.items():
                    label = {"side": side, "src": src, "dur": dur, "frame": fk}
                    record = maps[side][src, dur]["frames"][fk]
                    if not isinstance(record, dict) or record.get("run_id") != runs[side]["run_id"] or record.get("origin") not in (("rendered",) if side == "baseline" else ("rendered", "static_copy")):
                        report["unknown_provenance"].append(label)
                        continue
                    try:
                        with path.open("rb") as stream:
                            stat = os.fstat(stream.fileno())
                            file_ids[side].add((stat.st_dev, stat.st_ino))
                            with Image.open(stream) as im:
                                require(im.format == "PNG" and im.size == (c.w, c.h), "PNG viewport mismatch")
                                im.load()
                                h = hashlib.sha256(im.convert("RGBA").tobytes()).hexdigest()
                        report["hashed_frames"] += 1
                        require(h == record.get("sha256"), "completion digest mismatch")
                        actual[side][fk] = h
                    except (OSError, ValueError) as error:
                        report["missing_frames"].append({**label, "reason": str(error)})
                if fk in actual["baseline"] and fk in actual["candidate"]:
                    report["compared_frames"] += 1
                    if actual["baseline"][fk] != inst["frames"][fk] or actual["candidate"][fk] != inst["frames"][fk]:
                        report["different_frames"].append({"src": src, "dur": dur, "frame": fk})
            for side in runs:
                records = maps[side][src, dur]["frames"]
                for fk, record in records.items():
                    if isinstance(record, dict) and record.get("origin") == "static_copy":
                        head = record.get("head_frame")
                        valid = (record.get("static_status") == "verified_static" and isinstance(head, str)
                                 and head in keys and int(head) < int(fk)
                                 and isinstance(records[head], dict) and records[head].get("origin") == "rendered"
                                 and head in actual[side] and fk in actual[side]
                                 and actual[side][head] == actual[side][fk]
                                 and inst["frames"][head] == inst["frames"][fk])
                        if not valid:
                            report["unknown_provenance"].append({"side": side, "src": src, "dur": dur, "frame": fk})
        require(not (file_ids["baseline"] & file_ids["candidate"]), "candidate aliases baseline frame set")
        require(input_fingerprint(c, clips) == plan["input_fingerprint"], "inputs changed during verification")
        report["ok"] = (report["compared_frames"] == sum(d for _, d in required)
                        and not any(report[k] for k in ("different_frames", "missing_frames", "unknown_provenance", "confounders", "errors", "uncovered_instances")))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        report["errors"].append(str(error))
    if any(report[k] for k in ("errors", "missing_frames", "unknown_provenance", "uncovered_instances")):
        report["same_version_causal"] = False
    for key in ("different_frames", "missing_frames", "unknown_provenance", "uncovered_instances"):
        report[key + "_count"] = len(report[key])
    from vtx.quality import golden_quality
    report["quality"] = golden_quality(report, fixed_environment=bool(report["ok"] and not changed))
    report["quality_cost"] = {"scope": "strict_golden_verification",
                              "wall_seconds": time.perf_counter() - started,
                              "render_seconds": None}
    return report
