#!/usr/bin/env python3
"""Isolated 48-target NAVFREE contract. One arm only; existing r12 owns execution."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback

BASE_SHA = "e4a067f86e8914b4f9ccb40c42fbf22c95db700e36ac1c9aabad7df3c21cbdaf"
SHARED_SHA = "ab9c1c65ca5f41062b691cf45b55bc9beaa1a14caa28cb0f1013ed62d1421cee"
GLOBAL_FRAMES = (1380, 1381, 1403, 1404, 1405, 1433, 1434, 1435,
                 1451, 1452, 1453, 1503, 1514, 1515, 1516, 1571,
                 1572, 1589, 1590, 1619, 1620, 1639, 1640, 1679)
ENTRY_ORDER = ("T08.01", "T07.01")
ARMS = ("navigation-ascending", "navfree-ascending", "navfree-descending")
PATH_KEYS = {"override", "canvas", "twoshot", "opaque", "navfree",
             "canvas_fallback", "static_copied", "failed"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_base(path):
    import hashlib
    path = Path(path).absolute()
    require(hashlib.sha256(path.read_bytes()).hexdigest() == BASE_SHA,
            "frozen r1 helper identity changed")
    spec = importlib.util.spec_from_file_location("navfree_frozen_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frames(base, entry_id, arm):
    require(entry_id in ENTRY_ORDER and arm in ARMS, "unknown target/arm")
    values = [g - base.ENTRIES[entry_id]["dst_in"] for g in GLOBAL_FRAMES]
    return values[::-1] if arm == "navfree-descending" else values


def environment(base, arm, namespace):
    require(arm in ARMS and re.fullmatch(r"[A-Za-z0-9_-]+", namespace), "invalid arm/namespace")
    return {**base.COMMON_ENV, "VTX_GFX_PNG_FAST": "1", "VTX_GFX_CACHE_NS": namespace,
            "VTX_GFX_NAVFREE": "0" if arm == ARMS[0] else "1"}


def capture_gate(stats, arm, duration):
    """A new explicit NAVFREE gate; never rewrites stats or bypasses r1's gate."""
    require(arm in ARMS, "unknown arm")
    require(stats["n"] == duration and stats["rendered"] == 24 and not stats["failed"],
            "duration/target count differs or failed targets")
    require(stats["tabs"] == 1 and stats["transparent"] is True and
            stats["canvas_export"] is False and stats["static_spans"] == 0,
            "unexpected page route")
    expected_path = "override" if arm == ARMS[0] else "navfree"
    paths = stats["path_counts"]
    require(set(paths) == PATH_KEYS and paths[expected_path] == 24 and
            all(type(v) is int and v == (24 if k == expected_path else 0)
                for k, v in paths.items()), "actual route differs; fallback/reuse forbidden")
    capture = stats["png_capture"]
    params = {"format": "png", "optimizeForSpeed": True}
    require(capture["requested_parameters"] == capture["actual_parameters"] == params and
            capture["protocol_checks"] == {"succeeded": 1, "failed": 0},
            "FAST parameters or browser generation differs")
    calls = capture["capture_calls"]
    require(type(calls["succeeded"]) is int and calls["failed"] == 0 and
            calls["succeeded"] - 24 in (1, 2), "capture failures or extra target/probe calls")
    require(all(capture[k] == 0 for k in ("reused_frames", "canvas_frames", "static_copied_frames"))
            and not capture.get("cleanup_failures"), "reuse or capture cleanup failure")
    return {"requested_navfree": arm != ARMS[0], "actual_path_counts": dict(paths),
            "target_count": 24, "probe_calls_inferred": calls["succeeded"] - 24,
            "probe_trace_available": False, "underlying_navfree_shot_path": "NOT_SEPARATELY_EXPOSED"}


def inspect_events(base, events, data, pages):
    result = events.read_events(data)
    require(result["complete"] and result["exit_code"] == 0, "incomplete/failed events")
    rows = result["events"]
    require(not any(r["event"] in ("retry_requested", "cache_hit") or
                    r["scope"] == "gfx_fallback" or r["extensions"].get("metadata_redacted")
                    for r in rows), "retry/reuse/fallback/redacted event")
    starts = [r for r in rows if r["event"] == "task_start" and r["scope"] == "gfx_block"]
    require(len(starts) == len(pages) == 2, "expected exactly two pages")
    for start, page in zip(starts, pages):
        require(start["extensions"] == page["event_metadata"], "event target mismatch")
        attempted = [r for r in rows if r["task_id"] == start["task_id"] and
                     r["event"] in ("attempt_start", "attempt_terminal")]
        terminal = [r for r in rows if r["task_id"] == start["task_id"] and r["event"] == "task_terminal"]
        require(len(attempted) == 2 and attempted[0]["event"] == "attempt_start" and
                attempted[1]["event"] == "attempt_terminal" and
                attempted[0]["attempt"] == attempted[1]["attempt"] == 1 and
                attempted[1]["outcome"] == "succeeded" and len(terminal) == 1 and
                terminal[0]["outcome"] == "succeeded", "unexpected page attempt sequence")
    return {"run_id": result["run_id"], "pages": 2, "attempts": 2, "complete": True}


def run_arm(base, project, namespace, chrome, log, arm, events, render, ctx_factory):
    """CLI injects only pinned production APIs; tests inject an ordinary renderer mock."""
    project, log = Path(project), Path(log)
    require(arm in ARMS, "unknown arm")
    require(not (project / "cache").exists() and not (project / "out").exists(), "cold project required")
    require(all(os.environ.get(k) == v for k, v in environment(base, arm, namespace).items()),
            "arm environment differs")
    log.mkdir(exist_ok=False)
    report = {"schema": "navfree-pixel-pilot/1", "arm": arm, "start_utc": base.utc(),
              "start_monotonic": time.monotonic(), "pages": [], "qualification": "NOT_GRANTED",
              "scope": "direct production render_page, 48 sparse targets; not CLI/all",
              "cleanup": "REQUIRES_R12_OWNER_REVIEW", "exit_code": 1}
    error = None
    with (log / "events.jsonl").open("x", encoding="utf-8") as stream:
        execution = events.ExecutionContext(stream)
        report["run_id"] = execution.run_id
        try:
            ctx = ctx_factory(project / "timeline.json", execution=execution)
            for entry_id in ENTRY_ORDER:
                entry, requested = base.ENTRIES[entry_id], frames(base, entry_id, arm)
                outdir = project / "cache" / "navfree_pilot" / namespace / entry_id
                # Production event geometry is a sorted set, not an execution-order trace.
                # The exact requested order remains separately recorded and passed unchanged.
                metadata = {"page_id": str(events.uuid.uuid4()), "backend": "cdp",
                            **events.frame_metadata(sorted(requested))}
                page = {"entry_id": entry_id, "requested_local_order": requested,
                        "requested_global_order": [f + entry["dst_in"] for f in requested],
                        "outdir": str(outdir), "event_metadata": metadata,
                        "source": base.identity(project / entry["src"]), "start_utc": base.utc()}
                report["pages"].append(page)
                try:
                    task = events.task(ctx, "gfx", kind="gfx_block", metadata=metadata)
                    with task.scope():
                        ctx.verify_inputs("gfx")
                        page["render_start_monotonic"] = time.monotonic()
                        try:
                            page["stats"] = task.attempt(render, project / entry["src"], outdir,
                                entry["dur"], chrome_path=chrome, tabs=1, width=1920, height=1080,
                                frames=requested, ctx=ctx)  # Production reads FAST/NAVFREE env.
                        finally:
                            page["render_end_monotonic"] = time.monotonic()
                            page["complete_render_wall_seconds"] = (page["render_end_monotonic"] -
                                                                     page["render_start_monotonic"])
                        page["capture_gate"] = capture_gate(page["stats"], arm, entry["dur"])
                        base.exact_target_set(outdir, requested)
                        ctx.verify_inputs("gfx")
                except BaseException as exc:
                    page["error"] = {"type": type(exc).__name__, "text": str(exc),
                        "png_capture": getattr(exc, "png_capture", None),
                        "png_cleanup_errors": [{"type": type(e).__name__, "text": str(e),
                            "traceback": "".join(traceback.format_exception(type(e), e, e.__traceback__))}
                            for e in getattr(exc, "png_cleanup_errors", ())],
                        "traceback": traceback.format_exc()}
                    raise
                finally:
                    page["end_utc"] = base.utc()
                    base.json_new(log / (entry_id + ".json"), page)
            report["exit_code"] = 0
        except BaseException as exc:
            error = exc
            report["error"] = {"type": type(exc).__name__, "text": str(exc), "traceback": traceback.format_exc()}
            raise
        finally:
            execution.finish(report["exit_code"], error)
            report.update(end_utc=base.utc(), end_monotonic=time.monotonic(),
                          event_evidence_complete=not execution.incomplete,
                          result="CALLS_COMPLETE_REQUIRES_PIXELS_AND_CLEANUP" if report["exit_code"] == 0 else "FAILED")
            base.json_new(log / "arm.status.json", report)
    report["event_gate"] = inspect_events(base, events, (log / "events.jsonl").read_bytes(), report["pages"])
    base.json_new(log / "event-gate.json", report["event_gate"])
    return report


def compare_pair(base, left, right, entry_id, index, width=1920, height=1080, *, progress=None):
    """Every sparse pair; no compositing, tolerance, hidden-RGB removal or raw dump."""
    progress = progress if progress is not None else {}
    progress.update(expected_pairs=24, completed_pairs=0, failed_target=None,
                    failure_stage="left_target_set")
    requested = frames(base, entry_id, ARMS[0])
    base.exact_target_set(left, requested)
    progress["failure_stage"] = "right_target_set"
    base.exact_target_set(right, requested)
    count, mismatches = 0, []
    progress["failure_stage"] = "index_open"
    with Path(index).open("x", encoding="utf-8") as stream:
        for local in requested:
            target = {"entry_id": entry_id, "local": local,
                      "global": local + base.ENTRIES[entry_id]["dst_in"]}
            progress.update(failure_stage="decode_left", failed_target={
                **target, "side": "left", "path": str(Path(left) / f"f{local:04d}.png")})
            a, ia = base.decode_rgba(Path(left) / f"f{local:04d}.png", width, height)
            progress.update(failure_stage="decode_right", failed_target={
                **target, "side": "right", "path": str(Path(right) / f"f{local:04d}.png")})
            b, ib = base.decode_rgba(Path(right) / f"f{local:04d}.png", width, height)
            equal = a == b
            if not equal:
                mismatches.append(local)
            progress.update(failure_stage="index_write", failed_target=target)
            stream.write(json.dumps({"entry_id": entry_id, "local": local,
                "global": local + base.ENTRIES[entry_id]["dst_in"], "rgba_equal": equal,
                "left": ia, "right": ib}) + "\n")
            stream.flush()
            count += 1
            progress.update(completed_pairs=count, failed_target=None)
            del a, b
    progress["failure_stage"] = None
    return {"entry_id": entry_id, "rgba_pairs": count, "rgba_equal": not mismatches,
            "mismatch_local_frames": mismatches, "hidden_rgb_included": True}


def compare_and_report(base, left, right, entry_id, index, report, width=1920, height=1080):
    """Append-only result on valid mismatches AND incomplete/invalid PNG evidence."""
    require(not Path(report).exists(), "comparison report already exists")
    record = {"schema": "navfree-pixel-comparison/1", "status": "INCOMPLETE",
              "entry_id": entry_id, "left": str(left), "right": str(right),
              "index": str(index), "expected_pairs": 24, "completed_pairs": 0,
              "failed_target": None, "start_utc": base.utc()}
    try:
        result = compare_pair(base, left, right, entry_id, index, width, height, progress=record)
    except BaseException as exc:
        record.update(end_utc=base.utc(), error={"type": type(exc).__name__,
                      "text": str(exc), "traceback": traceback.format_exc()})
        base.json_new(report, record)
        raise
    record.update(result, end_utc=base.utc(), status="MATCH" if result["rgba_equal"] else "MISMATCH")
    base.json_new(report, record)
    require(result["rgba_equal"], "RGBA mismatch; see complete comparison index and report")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-harness", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run-arm")
    for name in ("repo", "project", "inventory", "log", "run-spec"):
        run.add_argument("--" + name, required=True, type=Path)
    run.add_argument("--chrome", required=True)
    run.add_argument("--namespace", required=True)
    run.add_argument("--arm", choices=ARMS, required=True)
    compare = sub.add_parser("compare-pair")
    for name in ("repo", "left", "right", "index", "report"):
        compare.add_argument("--" + name, required=True, type=Path)
    compare.add_argument("--entry", choices=ENTRY_ORDER, required=True)
    args = parser.parse_args()
    base = load_base(args.base_harness)
    if args.command == "compare-pair":
        base.import_production(args.repo)
        compare_and_report(base, args.left, args.right, args.entry, args.index, args.report)
        return
    require(sys.platform == "darwin", "real arm is Mac-only and requires reviewed r12 owner release")
    rows = base.load_inventory(args.inventory)
    base.verify_inputs(args.project, rows)
    require(base.identity(args.project / "runtime/shared.js")["sha256"] == SHARED_SHA, "shared runtime changed")
    spec_identity = base.identity(args.run_spec)
    spec = json.loads(args.run_spec.read_bytes())
    require(spec["expected_commit"] == base.COMMIT and spec["expected_tree"] == base.TREE and
            Path(spec["repo"]).absolute() == args.repo.absolute() and
            Path(spec["project"]).absolute() == args.project.absolute() and
            args.log.absolute().parent == Path(spec["evidence_dir"]).absolute() and
            spec["expected_nested_generations"] == 2 and spec["run_deadline_seconds"] == 600 and
            spec["resource_observation_mode"] == "diagnostic_census_v1", "run-spec experiment binding differs")
    require(Path(spec["argv"][0]).resolve() == Path(sys.executable).resolve() and
            spec["argv"][1:] == ["-B", str(Path(__file__).absolute()), *sys.argv[1:]], "run-spec argv differs")
    api = base.import_production(args.repo)
    primary = None
    try:
        run_arm(base, args.project, args.namespace, args.chrome, args.log, args.arm, *api)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if args.log.is_dir():
            try:
                require(base.identity(args.run_spec) == spec_identity, "run spec changed")
                checked = base.verify_inputs(args.project, rows)
                base.json_new(args.log / "inputs-after.json", {"ok": True, "inputs": checked,
                    "run_spec": spec_identity, "base_harness": base.identity(args.base_harness),
                    "extension": base.identity(__file__)})
            except BaseException as exc:
                base.json_new(args.log / "inputs-after-failure.json", {"ok": False,
                    "type": type(exc).__name__, "text": str(exc), "traceback": traceback.format_exc()})
                if primary is None:
                    raise


if __name__ == "__main__":
    main()
