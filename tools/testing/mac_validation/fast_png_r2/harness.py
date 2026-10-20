#!/usr/bin/env python3
"""Frozen narrow acceptance tools. No launcher, adapter, or automatic next arm."""
import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone

COMMIT = "85fb352f9f95f9882222b2eacee2c887b520f495"
LOCAL_REVIEW_COMMIT = "1d6af253c2555483af73eff67ecb247510f5a6ec"
TREE = "7d499f03b734cb87c0b4286947b9fb60bd5d303c"
PLAN_SHA = "21ea3e71b84cbe57f4943bc0c35f24cd898d2ce892ad0f4b82c6e79a18238296"
INVENTORY_SHA = "ac7a6a3c3b9b276fd6ec544ba21aadd37b57302fe35072238e01802cd8e38ef0"
TIMELINE_SHA = "957e893ce00e8a116fe130251e60f4f23662a00bccd0b413b7b4a52efa9e9b19"
ENTRY_SHA = "9e27f25e34f5d67be9d6ccb70b1423292aee22f7822123e52a9aae6ffe1a8177"
INPUT_BYTES = 20274304
ENTRIES = {
    "T08.01": {"src": "gfx/T08_0018_3600.html", "dst_in": 18, "dur": 3582,
               "paintIndex": 6, "local": [1362, 1662], "global": [1380, 1680]},
    "T07.01": {"src": "gfx/T07_0000_3600.html", "dst_in": 0, "dur": 3600,
               "paintIndex": 7, "local": [1380, 1680], "global": [1380, 1680]},
    "T10.01": {"src": "gfx/T10_0330_0630.html", "dst_in": 330, "dur": 300,
               "paintIndex": 9, "local": [0, 300], "global": [330, 630]},
}
ORDER = {"c300-01": ["off", "on"], "c300-02": ["on", "off"],
         "t10-multitab": ["off", "on"], "t10-split": ["off", "on"]}
COMMON_ENV = {
    "VTX_GFX": "cdp", "VTX_GFX_PAGES": "1", "VTX_GFX_TABS": "1",
    "VTX_GFX_SPLIT_FRAMES": "0", "VTX_GFX_SPLIT_MAX": "1",
    "VTX_GFX_NAVFREE": "0", "VTX_GFX_STATIC": "1", "VTX_IO_WORKERS": "1",
    "VTX_FILTER_THREADS": "1", "VTX_VIDEO_SEGMENTS": "1", "VTX_NO_NVENC": "1",
    "VTX_SEG_NVENC": "0", "VTX_PIPELINE": "1", "PYTHONDONTWRITEBYTECODE": "1",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def utc():
    return datetime.now(timezone.utc).isoformat()


def identity(path):
    path = Path(path)
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            count += len(block)
            digest.update(block)
    return {"bytes": count, "sha256": digest.hexdigest()}


def json_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def load_inventory(path):
    require(identity(path)["sha256"] == INVENTORY_SHA, "input inventory identity changed")
    rows = json.loads(Path(path).read_text())
    require(len(rows) == 114 and sum(r["bytes"] for r in rows) == INPUT_BYTES,
            "unexpected whitelist count or payload")
    require(len({r["path"] for r in rows}) == 114, "duplicate whitelist item")
    return rows


def verify_inputs(project, rows, *, projected=False):
    checked = []
    for row in rows:
        if projected and row["path"] == "timeline.json":
            continue
        actual = identity(Path(project) / row["path"])
        require(actual == {k: row[k] for k in ("bytes", "sha256")},
                "input mismatch: " + row["path"])
        checked.append({"path": row["path"], **actual})
    return checked


def copy_inputs(source, project, rows):
    """Only the supplied frozen whitelist; no recursive project/cache copy."""
    source, project = Path(source), Path(project)
    require(not project.exists(), "fresh project already exists")
    before = verify_inputs(source, rows)
    project.mkdir(parents=False)
    copied = []
    for row in rows:
        relative = row["path"]
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
        st = target.stat()
        copied.append({"path": relative, "source": str(source / relative),
                       "copy": str(target), "copy_utc": utc(),
                       "mode": oct(st.st_mode & 0o777), "mtime_ns": st.st_mtime_ns,
                       **identity(target)})
    after = verify_inputs(project, rows)
    require(before == after and before == verify_inputs(source, rows),
            "input changed during copy")
    return {"source": str(source), "project": str(project), "copied": copied,
            "items": len(rows), "bytes": sum(r["bytes"] for r in rows),
            "cache_or_out_copied": False, "source_rechecked": True}


def t10_projection(timeline):
    require(timeline["project"]["fps"] == 30 and
            timeline["project"]["width"] == 1920 and
            timeline["project"]["height"] == 1080 and
            timeline["project"]["duration_frames"] == 3600, "project geometry changed")
    tracks = [t for t in timeline["video_tracks"] if t.get("kind") == "gfx" and
              any(c.get("src") == ENTRIES["T10.01"]["src"] for c in t["clips"])]
    require(len(tracks) == 1 and len(tracks[0]["clips"]) == 1, "T10 is not one original track/clip")
    clip = tracks[0]["clips"][0]
    require(all(clip[k] == ENTRIES["T10.01"][k] for k in ("src", "dst_in", "dur")),
            "T10 clip timing changed")
    result = copy.deepcopy(timeline)
    result["video_tracks"] = copy.deepcopy(tracks)
    return result


def project_t10(source, project, rows):
    require(identity(Path(source) / "timeline.json")["sha256"] == TIMELINE_SHA,
            "original timeline identity changed")
    original = json.loads((Path(source) / "timeline.json").read_text())
    projected = t10_projection(original)
    result = copy_inputs(source, project, rows)
    # This is a fresh, explicitly derived project, never the bound/full project.
    target = Path(project) / "timeline.json"
    target.write_text(json.dumps(projected, ensure_ascii=False, indent=2) + "\n")
    result["projection"] = {"original": TIMELINE_SHA, "derived": identity(target),
                            "rule": "retain exact T10 gfx track; other fields unchanged",
                            "clip": projected["video_tracks"][0]["clips"][0]}
    verify_inputs(project, rows, projected=True)
    verify_inputs(source, rows)
    return result


def environment(case, arm, namespace):
    require(case in ORDER and arm in ORDER[case], "unknown case or arm")
    require(re.fullmatch(r"[A-Za-z0-9_-]+", namespace) is not None,
            "namespace must be nonempty alphanumeric/underscore/hyphen")
    result = {**COMMON_ENV, "VTX_GFX_CACHE_NS": namespace,
              "VTX_GFX_PNG_FAST": "1" if arm == "on" else "0"}
    if case == "t10-multitab":
        result["VTX_GFX_TABS"] = "2"
    if case == "t10-split":
        result.update(VTX_GFX_SPLIT_FRAMES="150", VTX_GFX_SPLIT_MAX="2")
    return result


def route_commands(repo, python, project, log, case, arm, namespace):
    require(case in ("t10-multitab", "t10-split"), "not a T10 route")
    commands = [[str(python), "-B", "-m", "vtx", "-t", str(Path(project) / "timeline.json"),
                 "--events-jsonl", str(Path(log) / (stage + ".events.jsonl")), stage]
                for stage in ("validate", "gfx")]
    return {"cwd": str(repo), "env_overrides": environment(case, arm, namespace),
            "argv_in_order": commands, "execute_second_only_if_first_succeeds": True,
            "execution": "NOT_STARTED", "expected_gfx_blocks": 2 if case == "t10-split" else 1,
            "expected_ranges": [[0, 150], [150, 300]] if case == "t10-split" else [[0, 300]],
            "per_tab_target_distribution": ("UNPROVEN_BY_EXISTING_AGGREGATE_STATS" if case == "t10-multitab"
                                            else "NOT_APPLICABLE_SINGLE_TAB"),
            "cleanup": "REQUIRES_EXISTING_OWNER_LEDGER_REVIEW"}


def import_production(repo, *, allow_local_review=False):
    repo = Path(repo).absolute()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
    allowed = {COMMIT, LOCAL_REVIEW_COMMIT} if allow_local_review else {COMMIT}
    require(git("rev-parse", "HEAD") in allowed and git("rev-parse", "HEAD^{tree}") == TREE,
            "production commit/tree changed")
    require(not git("status", "--porcelain", "--untracked-files=no"), "tracked production tree dirty")
    sys.path.insert(0, str(repo))
    import vtx
    from vtx import events, gfx_render
    from vtx.infra import Ctx
    require(Path(vtx.__file__).absolute().parent == repo / "vtx", "wrong vtx import")
    return events, gfx_render.render_page, Ctx


def inspect_route(case, arm, validate_data, gfx_data, receipt, events):
    """Inspect exposed route facts only; does not infer missing per-tab work."""
    require(case in ("t10-multitab", "t10-split") and arm in ("off", "on"), "not a route arm")
    validate = events.read_events(validate_data)
    gfx = events.read_events(gfx_data)
    require(validate["complete"] and validate["exit_code"] == 0 and
            gfx["complete"] and gfx["exit_code"] == 0, "incomplete/failed validate or gfx events")
    rows = gfx["events"]
    blocks = [r for r in rows if r["event"] == "task_start" and r["scope"] == "gfx_block"]
    expected = [[0, 150], [150, 300]] if case == "t10-split" else [[0, 300]]
    require([r["extensions"].get("frame_range") for r in blocks] == expected and
            all(r["extensions"].get("backend") == "cdp" for r in blocks), "actual route blocks differ")
    require(not any(r["event"] in ("retry_requested", "cache_hit") for r in rows), "retry/reuse observed")
    ends = {r["task_id"]: r for r in rows if r["event"] == "task_terminal"}
    previous_end = None
    for block in blocks:
        attempts = [r for r in rows if r["event"] == "attempt_terminal" and r["task_id"] == block["task_id"]]
        require(len(attempts) == 1 and attempts[0]["outcome"] == "succeeded" and
                ends[block["task_id"]]["outcome"] == "succeeded", "unexpected route attempt")
        began = block["timing"]["start_ms"]
        require(previous_end is None or began >= previous_end, "split blocks overlap")
        previous_end = ends[block["task_id"]]["timing"]["offset_ms"]
    stage = receipt["stages"]["gfx"]
    require(stage["ok"] is True, "gfx receipt failed")
    ref = stage["observed_by"]
    linked = [r for r in rows if r["event_id"] == ref["event_id"]]
    require(len(linked) == 1 and linked[0]["event"] == "receipt_commit" and
            linked[0]["run_id"] == ref["run_id"] == gfx["run_id"] and
            linked[0]["task_id"] == ref["task_id"] and
            linked[0]["extensions"].get("receipt_stage") == "gfx", "receipt event link mismatched")
    counts = dict((k, int(v)) for k, v in re.findall(r"([a-z_]+)×([0-9]+)", stage["detail"]))
    split_count = counts.pop("split", None)
    require(split_count == (2 if case == "t10-split" else None), "route split summary differs")
    require(re.findall(r"([0-9]+) tabs CDP", stage["detail"]) ==
            ["2" if case == "t10-multitab" else "1"], "route tab configuration differs")
    keys = ("override", "canvas", "twoshot", "opaque", "navfree", "canvas_fallback", "static_copied", "failed")
    require(all(k in counts for k in keys) and counts["override"] == 300 and
            all(v == 0 for k, v in counts.items() if k != "override"), "unexpected route path counts")
    if arm == "on":
        p = stage["png_capture"]  # Ctx flattens extra into the gfx stage.
        params = {"format": "png", "optimizeForSpeed": True}
        require(p["requested_parameters"] == p["actual_parameters"] == params and
                p["protocol_checks"] == {"succeeded": len(expected), "failed": 0}, "route fast provenance differs")
        require(p["capture_calls"]["failed"] == 0 and
                len(expected) <= p["capture_calls"]["succeeded"] - 300 <= 2 * len(expected), "route capture accounting differs")
        require(all(p[k] == 0 for k in ("reused_frames", "canvas_frames", "static_copied_frames"))
                and not p.get("cleanup_failures"), "route reuse/fallback/cleanup failure")
    else:
        require("png_capture" not in stage, "unexpected OFF fast provenance")
    return {"exposed_route_facts": "MATCH", "case": case, "arm": arm,
            "blocks": expected, "path_counts": counts,
            "png_capture": stage.get("png_capture"), "observed_by": ref,
            "per_tab_target_distribution": "UNPROVEN" if case == "t10-multitab" else "SINGLE_TAB",
            "pixels_certificates_source_consumption_cleanup": "REQUIRES_SEPARATE_REVIEW",
            "qualification": "NOT_GRANTED_BY_THIS_INSPECTOR"}


def capture_gate(stats, fast, count=300):
    require(stats["rendered"] == count and not stats["failed"], "missing/failed targets")
    require(stats["tabs"] == 1 and stats["transparent"] is True and
            stats["canvas_export"] is False and stats["static_spans"] == 0,
            "unexpected C300 rendering route")
    paths = stats["path_counts"]
    require(paths.get("override") == count and all(v == 0 for k, v in paths.items() if k != "override"),
            "non-override/recovery/fallback/static target path")
    if not fast:
        require("png_capture" not in stats, "OFF unexpectedly contains fast provenance")
        return {"target_calls_from_path_counts": count, "probe_calls": None,
                "protocol_checks": "NOT_EXPOSED_FOR_OFF"}
    capture = stats["png_capture"]
    requested = {"format": "png", "optimizeForSpeed": True}
    require(capture["requested_parameters"] == requested and capture["actual_parameters"] == requested,
            "fast capture parameters missing/mismatched")
    require(capture["protocol_checks"] == {"succeeded": 1, "failed": 0}, "extra/failed browser generation")
    calls = capture["capture_calls"]
    require(calls["failed"] == 0 and calls["succeeded"] - count in (1, 2),
            "unexpected target/probe capture accounting")
    require(all(capture[k] == 0 for k in ("reused_frames", "canvas_frames", "static_copied_frames"))
            and not capture.get("cleanup_failures"), "reuse, noncapture path, or cleanup failure")
    return {"target_calls_from_path_counts": count,
            "probe_calls_inferred_from_normal_override_branch": calls["succeeded"] - count,
            "probe_trace_available": False}


def run_c300(project, namespace, chrome, log, fast, events, render, ctx_factory):
    """Test seam is argument injection; CLI always passes real production objects."""
    project, log = Path(project), Path(log)
    require(not (project / "cache").exists() and not (project / "out").exists(),
            "C300 requires a cold project with no cache/out")
    require(not log.exists(), "log directory already exists")
    log.mkdir()
    execution = events.ExecutionContext((log / "c300.events.jsonl").open("x", encoding="utf-8"))
    report = {"schema": "production-fast-png-c300/1", "start_utc": utc(),
              "start_monotonic": time.monotonic(), "run_id": execution.run_id,
              "project": str(project), "namespace": namespace, "fast": fast,
              "scope": "direct render_page, not CLI gfx or all receipt", "pages": [],
              "cleanup": "UNPROVEN_REQUIRES_OWNER_PROCESS_IDENTITY_LEDGER"}
    error, code = None, 1
    try:
        ctx = ctx_factory(project / "timeline.json", execution=execution)
        for entry_id in ("T08.01", "T07.01"):
            entry = ENTRIES[entry_id]
            outdir = project / "cache" / "gfx_exp" / namespace / entry_id
            page = {"entry_id": entry_id, **entry, "outdir": str(outdir),
                    "source": identity(project / entry["src"]), "start_utc": utc(),
                    "start_monotonic": time.monotonic()}
            report["pages"].append(page)
            try:
                observation = events.task(ctx, "gfx", kind="gfx_block", metadata={
                    "page_id": str(events.uuid.uuid4()), "backend": "cdp", "frame_range": entry["local"]})
                with observation.scope():
                    ctx.verify_inputs("gfx")
                    # Deliberately omit png_fast: production reads VTX_GFX_PNG_FAST.
                    page["render_call_start_monotonic"] = time.monotonic()
                    try:
                        stats = observation.attempt(render, project / entry["src"], outdir,
                                                    entry["dur"], chrome_path=chrome, tabs=1,
                                                    width=1920, height=1080,
                                                    frames=range(*entry["local"]), ctx=ctx)
                    finally:
                        page["render_call_end_monotonic"] = time.monotonic()
                        page["complete_render_call_wall_seconds"] = (
                            page["render_call_end_monotonic"] - page["render_call_start_monotonic"])
                    page["stats"] = stats
                    require(stats["n"] == entry["dur"], "renderer duration accounting changed")
                    page["capture_accounting"] = capture_gate(stats, fast)
                    ctx.verify_inputs("gfx")
            except BaseException as exc:
                page["error"] = {"type": type(exc).__name__, "text": str(exc),
                                 "png_capture": getattr(exc, "png_capture", None),
                                 "png_cleanup_errors": [
                                     {"type": type(cleanup).__name__, "text": str(cleanup),
                                      "traceback": "".join(traceback.format_exception(
                                          type(cleanup), cleanup, cleanup.__traceback__))}
                                     for cleanup in getattr(exc, "png_cleanup_errors", ())],
                                 "traceback": traceback.format_exc()}
                raise
            finally:
                page.update(end_utc=utc(), end_monotonic=time.monotonic())
                page["page_wrapper_wall_seconds"] = page["end_monotonic"] - page["start_monotonic"]
                json_new(log / (entry_id + ".json"), page)
        code = 0
    except BaseException as exc:
        error = exc
        report["error"] = {"type": type(exc).__name__, "text": str(exc), "traceback": traceback.format_exc()}
        raise
    finally:
        execution.finish(code, error)
        report.update(exit_code=code, end_utc=utc(), end_monotonic=time.monotonic(),
                      event_evidence_complete=not execution.incomplete,
                      result="CALLS_COMPLETE_REQUIRES_PIXELS_AND_CLEANUP" if code == 0 else "FAILED")
        json_new(log / "c300.status.json", report)
    require(not execution.incomplete, "event evidence incomplete")
    reduced = events.read_events((log / "c300.events.jsonl").read_bytes())
    require(reduced["complete"] and reduced["exit_code"] == 0, "event boundaries/attempts incomplete")
    return report


def exact_target_set(directory, frames):
    expected = {f"f{i:04d}.png" for i in frames}
    actual = {p.name for p in Path(directory).iterdir() if p.suffix.lower() == ".png"}
    require(actual == expected, "target set mismatch: missing=%d extra=%d" %
            (len(expected - actual), len(actual - expected)))


def decode_rgba(path, width=1920, height=1080):
    """Strict production structure check; separately decode every pixel using Pillow."""
    from vtx.gfx_cli import png_invalid_reason
    from PIL import Image
    checked, sizes = [], []
    reason = png_invalid_reason(path, width=width, height=height,
                                _fingerprint=checked, _verified_size=sizes)
    require(reason is None, "invalid PNG %s: %s" % (path, reason))
    data = Path(path).read_bytes()
    require(len(data) == sizes[0] and hashlib.sha256(data).hexdigest() == checked[0],
            "PNG changed between validation and decoding")
    require(data[24] == 8 and data[25] in (2, 6), "only 8-bit RGB/RGBA permitted")
    with Image.open(io.BytesIO(data)) as im:
        require(im.size == (width, height) and im.mode in ("RGB", "RGBA") and
                "transparency" not in im.info, "unsupported PNG interpretation")
        im.load()
        rgba = im.convert("RGBA").tobytes() if im.mode == "RGB" else im.tobytes()
    return rgba, {"path": str(path), "bytes": sizes[0], "sha256": checked[0],
                  "color_type": data[25], "rgba_sha256": hashlib.sha256(rgba).hexdigest()}


def compare_page(left, right, entry_id, index, width=1920, height=1080):
    entry = ENTRIES[entry_id]
    frames = range(*entry["local"])
    exact_target_set(left, frames)
    exact_target_set(right, frames)
    count, totals = 0, [0, 0]
    with Path(index).open("x", encoding="utf-8") as stream:
        for local in frames:
            a, ia = decode_rgba(Path(left) / f"f{local:04d}.png", width, height)
            b, ib = decode_rgba(Path(right) / f"f{local:04d}.png", width, height)
            equal = a == b  # Includes RGB hidden behind alpha=0.
            row = {"entry_id": entry_id, "local": local, "global": entry["dst_in"] + local,
                   "rgba_equal": equal, "left": ia, "right": ib}
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            require(equal, "RGBA mismatch at %s local=%d global=%d" %
                    (entry_id, local, entry["dst_in"] + local))
            count += 1
            totals[0] += ia["bytes"]
            totals[1] += ib["bytes"]
            del a, b
    return {"entry_id": entry_id, "rgba_pairs": count, "rgba_equal": True,
            "left_png_bytes": totals[0], "right_png_bytes": totals[1],
            "local_range": entry["local"], "global_range": entry["global"],
            "comparison_scope": "all requested PNGs, no video/audio/cleanup conclusion"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("copy-input", "project-t10"):
        p = sub.add_parser(name)
        for flag in ("source", "project", "inventory", "report"):
            p.add_argument("--" + flag, required=True, type=Path)
    p = sub.add_parser("verify-input")
    for flag in ("project", "inventory", "report"):
        p.add_argument("--" + flag, required=True, type=Path)
    p.add_argument("--original-timeline", type=Path, help="required for a derived T10 route project")
    for name in ("c300", "route-commands"):
        p = sub.add_parser(name)
        for flag in ("repo", "project", "log", "run-spec"):
            p.add_argument("--" + flag, required=True, type=Path)
        p.add_argument("--case", required=True, choices=list(ORDER)[:2] if name == "c300" else list(ORDER)[2:])
        p.add_argument("--arm", required=True, choices=("off", "on"))
        p.add_argument("--namespace", required=True)
        if name == "c300":
            p.add_argument("--chrome", required=True)
            p.add_argument("--inventory", required=True, type=Path)
        else:
            p.add_argument("--python", required=True)
            p.add_argument("--report", required=True, type=Path)
    p = sub.add_parser("compare")
    for flag in ("repo", "left", "right", "index", "report"):
        p.add_argument("--" + flag, required=True, type=Path)
    p.add_argument("--entry", choices=list(ENTRIES), required=True)
    p = sub.add_parser("inspect-route")
    for flag in ("repo", "validate-events", "gfx-events", "receipt", "report"):
        p.add_argument("--" + flag, required=True, type=Path)
    p.add_argument("--case", choices=("t10-multitab", "t10-split"), required=True)
    p.add_argument("--arm", choices=("off", "on"), required=True)
    args = parser.parse_args()
    if args.command in ("copy-input", "project-t10"):
        require(not args.report.exists(), "report already exists")
        rows = load_inventory(args.inventory)
        fn = copy_inputs if args.command == "copy-input" else project_t10
        result = fn(args.source, args.project, rows)
        json_new(args.report, result)
    elif args.command == "verify-input":
        rows = load_inventory(args.inventory)
        if args.original_timeline:
            require(identity(args.original_timeline)["sha256"] == TIMELINE_SHA, "original timeline changed")
            expected = t10_projection(json.loads(args.original_timeline.read_text()))
            require(json.loads((args.project / "timeline.json").read_text()) == expected,
                    "derived timeline differs from exact T10 projection")
        result = verify_inputs(args.project, rows, projected=bool(args.original_timeline))
        json_new(args.report, {"inputs": result, "timeline": identity(args.project / "timeline.json")})
    elif args.command == "route-commands":
        result = route_commands(args.repo, args.python, args.project, args.log,
                                args.case, args.arm, args.namespace)
        result["run_spec"] = {"path": str(args.run_spec), **identity(args.run_spec)}
        json_new(args.report, result)
    elif args.command == "c300":
        require(sys.platform == "darwin", "real C300 is Mac-only and requires root release/owner launcher")
        require(not args.log.exists(), "log directory already exists")
        expected = environment(args.case, args.arm, args.namespace)
        require(all(os.environ.get(k) == v for k, v in expected.items()), "execution environment differs")
        rows = load_inventory(args.inventory)
        verify_inputs(args.project, rows)
        require(identity(args.project / "entry-manifest.json")["sha256"] == ENTRY_SHA,
                "entry manifest changed")
        run_spec_identity = identity(args.run_spec)
        api = import_production(args.repo)
        primary_error = None
        try:
            result = run_c300(args.project, args.namespace, args.chrome, args.log,
                              args.arm == "on", *api)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            if args.log.is_dir():
                json_new(args.log / "invocation.json", {
                    "argv": sys.argv, "case": args.case, "arm": args.arm,
                    "case_order": ORDER[args.case], "environment": expected,
                    "repo": str(args.repo), "commit": COMMIT, "tree": TREE,
                    "run_spec": {"path": str(args.run_spec), **run_spec_identity}})
                try:
                    checked = verify_inputs(args.project, rows)
                    json_new(args.log / "inputs-after.json", {"ok": True, "inputs": checked})
                except BaseException as exc:
                    json_new(args.log / "inputs-after-failure.json", {
                        "ok": False, "error": str(exc), "traceback": traceback.format_exc()})
                    if primary_error is None:
                        raise
    elif args.command == "inspect-route":
        require(not args.report.exists(), "report already exists")
        events, _, _ = import_production(args.repo)
        result = inspect_route(args.case, args.arm, args.validate_events.read_bytes(),
                               args.gfx_events.read_bytes(), json.loads(args.receipt.read_text()), events)
        json_new(args.report, result)
    else:
        require(not args.report.exists(), "report already exists")
        import_production(args.repo)
        result = compare_page(args.left, args.right, args.entry, args.index)
        json_new(args.report, result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
