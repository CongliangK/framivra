#!/usr/bin/env python3
"""Bounded, isolated issue #85 comparison against two immutable revisions.

This helper never acquires media. Its caller supplies a qualified, normalized
30fps audiovisual source of at least 20 seconds. Output is 1080p. Evidence is
written under a new ``benchmark`` directory. Negative tests temporarily mutate
and restore one byte of the caller-owned normalized source. Import runs nothing.
All measurements include unchanged production validation, rendering and QC.
"""
from __future__ import annotations

import contextlib
import builtins
import functools
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time
import traceback

BASELINE = "b8820dbba6989dd216c8fa519633371e3d380cb6"
CANDIDATE = "68e94cae8afd5519737fa3fce250bfb045c75dfc"
PAIR_ORDER = (("baseline", "candidate"), ("candidate", "baseline"),
              ("baseline", "candidate"))
RUN_SECONDS = 900
TOTAL_SECONDS = 5400
RSS_LIMIT_BYTES = 4 * 1024 ** 3
SCRATCH_LIMIT_BYTES = 8 * 1024 ** 3
POLL_SECONDS = 0.2
REQUIRED_STAGES = ("validate", "audit", "prepare", "gfx", "video", "audio", "mux", "qc")
CPU_POLICY = {"VTX_NO_NVENC": "1", "VTX_SEG_NVENC": "0"}


def write_json(path, value):
    """Write a fresh evidence file; never replace an existing report."""
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False,
                  allow_nan=False)
        stream.write("\n")


def timeline_spec(source="source.mp4"):
    """12 seconds, three discontiguous four-second cuts with matching audio."""
    clips, events = [], []
    for number, source_start in enumerate((0, 240, 480)):
        target = number * 120
        clips.append({"src": "source", "src_in": source_start,
                      "src_out": source_start + 120, "dst_in": target, "dur": 120})
        events.append({"src": "source", "src_in": source_start,
                       "in": target, "out": target + 120, "volume": 1.0})
    return {"version": 2,
            "project": {"fps": 30, "width": 1920, "height": 1080,
                        "duration_frames": 360},
            "sources": [{"id": "source", "path": str(source)}],
            "video_tracks": [{"name": "camera", "kind": "aroll", "clips": clips}],
            "subtitles": [],
            "audio_tracks": [{"name": "voice", "events": events}]}


def safe_directory(path):
    """Require an existing physical directory, rejecting every symlink ancestor."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if stat.S_ISLNK(part.lstat().st_mode):
            raise ValueError(f"symlink directory refused: {part}")
    if not path.is_dir():
        raise ValueError(f"not a directory: {path}")
    return path


def isolated_environment(project):
    """Do not inherit renderer controls, credentials, plugins or Python hooks."""
    home = Path(project) / "home"
    return {"PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C.UTF-8",
            "HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_CACHE_HOME": str(home / "cache"), "TMPDIR": str(home / "tmp"),
            "VTX_CONFIG": str(Path(project) / "config.json"), **CPU_POLICY}


def checked_revision(repository, expected):
    """Refuse drift in production trees, including ignored/untracked code."""
    repository = safe_directory(repository)
    env = {"PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C.UTF-8",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    def git(*args):
        result = subprocess.run(["git", "-C", str(repository), *args],
                                capture_output=True, text=True, env=env, timeout=30)
        if result.returncode:
            raise ValueError(f"revision verification failed: {result.stderr.strip()}")
        return result.stdout.strip()
    actual = git("rev-parse", "HEAD")
    if actual != expected:
        raise ValueError(f"expected exact revision {expected}; found {actual}")
    production = ("vtx", "plugins", "apps", "pyproject.toml", "vt.py")
    git("diff", "--exit-code", expected, "--", *production)
    extras = git("ls-files", "--others", "--", *production).splitlines()
    # Bytecode is not imported: each child has an empty private pycache_prefix.
    extras = [p for p in extras if "__pycache__" not in Path(p).parts]
    if extras:
        raise ValueError(f"untracked production files refused: {extras[:8]}")
    return {"path": str(repository), "commit": actual,
            "tracked_production_diff": "clean", "untracked_production": []}


def allocated_bytes(root):
    """Allocated filesystem blocks, without symlink traversal or hardlink duplication."""
    seen, total = set(), 0
    for directory, directories, files in os.walk(root, followlinks=False):
        for path in [Path(directory), *(Path(directory) / name for name in files),
                     *(Path(directory) / name for name in directories
                       if (Path(directory) / name).is_symlink())]:
            try:
                info = path.lstat()
            except FileNotFoundError:  # owned FFmpeg atomic temp rename
                continue
            identity = (info.st_dev, info.st_ino)
            if identity not in seen:
                seen.add(identity)
                total += info.st_blocks * 512
    return total


def process_tree_rss(leader):
    """Linux RSS for our session and any descendants, not unrelated processes."""
    records = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            # comm can contain spaces and parentheses; fields begin after its last ')'.
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            records[int(entry.name)] = (int(fields[1]), int(fields[3]),
                                        int(fields[21]) * os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError, IndexError):
            continue  # process exited between directory enumeration and stat
    owned = {leader} | {pid for pid, (_, session, _) in records.items() if session == leader}
    while True:
        descendants = {pid for pid, (parent, _, _) in records.items() if parent in owned}
        if descendants <= owned:
            break
        owned.update(descendants)
    return sum(max(0, records[pid][2]) for pid in owned if pid in records)


def monitored_run(args, cwd, env, log, timeout, budget_root):
    """Bound an owned Linux process tree: elapsed time, RSS and allocated scratch.

    Polling is observational (0.2 s), not an OS hard resource reservation. Peak
    values are sampled upper sums of per-process RSS, including shared pages.
    Only the newly started process group is ever signalled.
    """
    command = list(map(str, args))
    if not sys.platform.startswith("linux"):
        raise RuntimeError("RSS monitoring requires Linux /proc")
    if timeout <= 0:
        return {"exit_code": None, "timed_out": True, "wall_seconds": 0.0,
                "started": False, "argv": command, "budget_exceeded": None}
    started = time.monotonic()
    peak_scratch = allocated_bytes(budget_root)
    if peak_scratch > SCRATCH_LIMIT_BYTES:
        return {"exit_code": None, "timed_out": False, "wall_seconds": 0.0,
                "started": False, "argv": command, "budget_exceeded": "scratch",
                "peak_scratch_allocated_bytes": peak_scratch,
                "peak_process_tree_rss_bytes": 0}
    peak_rss, exceeded, expired = 0, None, False
    with Path(log).open("xb") as stream:
        process = subprocess.Popen(command, cwd=cwd, env=env,
                                   stdin=subprocess.DEVNULL, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        def stop():
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        try:
            while process.poll() is None:
                peak_rss = max(peak_rss, process_tree_rss(process.pid))
                peak_scratch = max(peak_scratch, allocated_bytes(budget_root))
                expired = time.monotonic() - started >= timeout
                exceeded = ("rss" if peak_rss > RSS_LIMIT_BYTES else
                            "scratch" if peak_scratch > SCRATCH_LIMIT_BYTES else None)
                if expired or exceeded:
                    stop()
                    break
                try:
                    process.wait(timeout=min(POLL_SECONDS,
                        max(0.001, timeout - (time.monotonic() - started))))
                except subprocess.TimeoutExpired:
                    pass
            peak_scratch = max(peak_scratch, allocated_bytes(budget_root))
            if peak_scratch > SCRATCH_LIMIT_BYTES:
                exceeded = "scratch"
        except BaseException:
            stop()
            raise
    return {"exit_code": process.returncode, "timed_out": expired,
            "wall_seconds": time.monotonic() - started, "started": True,
            "argv": command, "log": str(log), "budget_exceeded": exceeded,
            "peak_process_tree_rss_bytes": peak_rss,
            "peak_scratch_allocated_bytes": peak_scratch,
            "poll_seconds": POLL_SECONDS,
            "rss_limit_bytes": RSS_LIMIT_BYTES,
            "scratch_limit_bytes": SCRATCH_LIMIT_BYTES}


class DigestMeter:
    """Observe original functions and actual returned read bytes, not estimates.

    Path.open/open are transparently delegated except inside an instrumented digest
    call. The original hash function runs unchanged. Thread-local scope ensures
    the production pipeline's concurrent calls do not mix byte accounting.
    This measures Python hashing I/O only, not FFmpeg reads or physical disk I/O.
    Instrumentation time is included in all measured wall-clock times.
    """
    def __init__(self):
        self.rows = []
        self.local = threading.local()
        self.lock = threading.Lock()
        self.phase = "initialization"
        self.original_open = Path.open
        self.original_builtin_open = builtins.open
        self.patches = []

    def install(self):
        meter = self
        class Reader:
            def __init__(self, stream, row):
                self.stream, self.row = stream, row
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def read(self, *args, **kwargs):
                data = self.stream.read(*args, **kwargs)
                self.row["read_calls"] += 1
                self.row["bytes_read"] += len(data)
                return data
        def wrap_stream(stream, args, kwargs):
            row = getattr(meter.local, "row", None)
            mode = args[0] if args else kwargs.get("mode", "r")
            if row is not None and "r" in mode and "b" in mode and not isinstance(stream, Reader):
                return Reader(stream, row)
            return stream
        def counted_open(path, *args, **kwargs):
            return wrap_stream(meter.original_open(path, *args, **kwargs), args, kwargs)
        def counted_builtin_open(path, *args, **kwargs):
            return wrap_stream(meter.original_builtin_open(path, *args, **kwargs), args, kwargs)
        Path.open = counted_open
        builtins.open = counted_builtin_open
        from vtx import cache_state
        self.patch(cache_state, "file_digest")
        self.patch(cache_state, "media_stat_digest")
        # Existing recovery/manifest hashing is part of the full consumer cost.
        from vtx import recovery, hygiene
        self.patch(recovery, "_digest")
        self.patch(hygiene, "_md5_file")

    def patch(self, module, name):
        original = getattr(module, name)
        meter = self
        @functools.wraps(original)
        def wrapper(path, *args, **kwargs):
            row = {"function": f"{module.__name__}.{name}", "path": str(path),
                   "phase": meter.phase, "bytes_read": 0, "read_calls": 0,
                   "completed": False}
            previous = getattr(meter.local, "row", None)
            meter.local.row = row
            start = time.monotonic()
            row["monotonic_start"] = start
            try:
                value = original(path, *args, **kwargs)
                row["completed"] = True
                return value
            finally:
                row["monotonic_end"] = time.monotonic()
                row["wall_seconds"] = row["monotonic_end"] - start
                meter.local.row = previous
                with meter.lock:
                    meter.rows.append(row)
        self.patches.append((module, name, original))
        setattr(module, name, wrapper)

    def summary(self):
        totals = {}
        for row in self.rows:
            key = (row["phase"], row["function"], row["path"])
            total = totals.setdefault(key, {"phase": key[0], "function": key[1],
                "path": key[2], "calls": 0, "completed_calls": 0,
                "read_calls": 0, "bytes_read": 0, "wall_seconds_sum": 0.0})
            total["calls"] += 1
            total["completed_calls"] += int(row["completed"])
            total["read_calls"] += row["read_calls"]
            total["bytes_read"] += row["bytes_read"]
            total["wall_seconds_sum"] += row["wall_seconds"]
        return {"scope": "actual Python read() bytes inside original digest functions; "
                "not physical disk I/O; concurrent call times may overlap",
                "file_digest_calls": sum(x["function"] == "vtx.cache_state.file_digest"
                                         for x in self.rows),
                "file_digest_bytes": sum(x["bytes_read"] for x in self.rows
                                         if x["function"] == "vtx.cache_state.file_digest"),
                "all_instrumented_digest_bytes": sum(x["bytes_read"] for x in self.rows),
                "all_digest_wall_seconds_union": interval_union_seconds(self.rows),
                "file_digest_wall_seconds_union": interval_union_seconds([
                    row for row in self.rows if row["function"] == "vtx.cache_state.file_digest"]),
                "by_phase_function_path": list(totals.values()), "raw_calls": self.rows}

    def restore(self):
        for module, name, original in reversed(self.patches):
            setattr(module, name, original)
        Path.open = self.original_open
        builtins.open = self.original_builtin_open


def interval_union_seconds(rows):
    """Wall-time union avoids double counting concurrent digest calls."""
    end, total = None, 0.0
    for start, finish in sorted((r["monotonic_start"], r["monotonic_end"]) for r in rows):
        total += max(0.0, finish - max(start, end if end is not None else start))
        end = max(finish, end if end is not None else finish)
    return total


@contextlib.contextmanager
def middle_byte_mutation(path, *, outside_samples=False):
    """Mutate an explicitly owned media file, restoring byte and nanosecond times."""
    path = Path(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size == 0:
        raise ValueError("mutation requires a nonempty regular file")
    offset = before.st_size // 2
    if outside_samples and not (65536 <= offset < before.st_size - 65536):
        raise ValueError("source is too small for a mutation outside both 64 KiB samples")
    with path.open("rb") as stream:
        stream.seek(offset)
        original = stream.read(1)
    try:
        with path.open("r+b") as stream:
            stream.seek(offset)
            stream.write(bytes([original[0] ^ 1]))
            stream.flush()
            os.fsync(stream.fileno())
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = path.stat()
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise RuntimeError("failed to preserve size and mtime_ns")
        yield {"offset": offset, "size": before.st_size,
               "mtime_ns": before.st_mtime_ns, "restored_mtime": True,
               "ctime_preserved": False}
    finally:
        with path.open("r+b") as stream:
            stream.seek(offset)
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_receipts(project):
    receipt = read_json(Path(project) / "out/receipt.json")
    stages = receipt.get("stages", {})
    failed = [s for s in REQUIRED_STAGES if stages.get(s, {}).get("ok") is not True]
    qc = read_json(Path(project) / "out/qc_report.json")
    checks = qc.get("checks", [])
    if failed or not checks or any(x.get("pass") is not True for x in checks):
        raise RuntimeError(f"production gates failed: stages={failed}, QC={checks}")
    return {"receipt": receipt, "qc_report": qc,
            "qc_check_count": len(checks), "all_stages_passed": True}


def decoded_identity(final, evidence):
    """Retain every decoded frame hash and a complete 48 kHz stereo PCM hash."""
    outputs = {}
    for kind, flags in (
        ("video", ["-map", "0:v:0", "-an", "-c:v", "rawvideo", "-pix_fmt", "yuv420p",
                   "-f", "framehash", "-hash", "sha256"]),
        ("audio", ["-map", "0:a:0", "-vn", "-ar", "48000", "-ac", "2",
                   "-c:a", "pcm_s16le", "-f", "hash", "-hash", "sha256"])):
        destination = Path(evidence) / f"decoded-{kind}.txt"
        command = ["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(final),
                   *flags, str(destination)]
        result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True)
        (Path(evidence) / f"decoded-{kind}.stderr.txt").write_text(result.stderr)
        if result.returncode or result.stderr.strip():
            raise RuntimeError(f"decoded {kind} hash failed: {result.stderr[-1000:]}")
        outputs[kind] = destination.read_text()
    rows = [line for line in outputs["video"].splitlines() if line and not line.startswith("#")]
    canonical = []
    for row in rows:
        fields = [field.strip() for field in row.split(",")]
        if len(fields) != 6 or len(fields[-1]) != 64:
            raise RuntimeError("unexpected framehash output")
        canonical.append(fields)
    if len(canonical) != 360:
        raise RuntimeError(f"decoded frame count {len(canonical)} != 360")
    pcm = outputs["audio"].strip()
    if not pcm.startswith("SHA256=") or len(pcm.removeprefix("SHA256=")) != 64:
        raise RuntimeError("unexpected PCM hash output")
    return {"video_frames": len(canonical),
            "video_framehash_sha256": hashlib.sha256(json.dumps(canonical,
                separators=(",", ":")).encode()).hexdigest(),
            "pcm_s16le_48000_stereo_sha256": pcm.split("=", 1)[1],
            "video_pixel_format": "yuv420p", "audio_format": "s16le/48000/stereo"}


def identity_negatives(project, evidence, meter, candidate):
    """Actual key/pin/reuse APIs on real warmed outputs; no corrupted render."""
    from vtx import cache_state, hygiene, stages, qc, loudness_cache
    from vtx.infra import Ctx
    project = Path(project)
    context = Ctx(project / "timeline.json")
    context.draft = context.gpu = context.strict = False
    context.frames = None
    source, final = context.src_path("source"), context.out / "final.mp4"
    original_receipt = (context.out / "receipt.json").read_bytes()
    results = {"candidate_expected_secure": candidate,
               "scope": "real warm consumer APIs, formal receipt and QC cache invalidation; "
                        "corrupted media is not rendered", "cases": {}}
    def source_snapshot():
        wav, command = stages.source_audio_extraction(context, "source")
        return {"audio_key": hygiene.audio_key(context, "source"),
                "segment_keys": [hygiene.seg_key(context, clip, 0, i, False, False, "x264")
                    for i, clip in enumerate(context.tl["video_tracks"][0]["clips"])],
                "fingerprint": cache_state.receipt_fingerprint(context),
                "fast_token": cache_state.media_stat_digest(source),
                "extraction_path": str(wav), "extraction_cache_hit": command is None}
    meter.phase = "negative_source_before"
    before = source_snapshot()
    if not before["extraction_cache_hit"]:
        raise RuntimeError("source negative requires a real warm extracted-audio hit")
    meter.phase = "negative_source_changed"
    try:
        with middle_byte_mutation(source, outside_samples=True) as mutation:
            after = source_snapshot()
            pin_rejected = False
            try:
                cache_state.verify_input_identity(context)
            except cache_state.InputIdentityError:
                pin_rejected = True
            fresh = Ctx(context.tl_path)
            inherited = cache_state.load_receipt(context.out / "receipt.json",
                                                  fresh.input_fingerprint)
            success_rejected = False
            try:
                context.receipt("qc", True, "issue85 formal-pin negative")
            except (cache_state.InputIdentityError, SystemExit):
                success_rejected = True
            formal_after = read_json(context.out / "receipt.json")
            write_json(Path(evidence) / "source-negative-receipt.json", formal_after)
            observed = {"fast_token_unchanged": before["fast_token"] == after["fast_token"],
                "audio_key_changed": before["audio_key"] != after["audio_key"],
                "all_segment_keys_changed": all(a != b for a, b in
                    zip(before["segment_keys"], after["segment_keys"])),
                "fingerprint_changed": before["fingerprint"] != after["fingerprint"],
                "extraction_cache_invalidated": not after["extraction_cache_hit"],
                "input_pin_rejected": pin_rejected,
                "fresh_receipt_invalidated": not inherited.get("stages"),
                "formal_success_rejected": success_rejected,
                "formal_qc_success_revoked": formal_after.get("stages", {}).get("qc", {}).get("ok") is False}
            expected = {key: (True if key == "fast_token_unchanged" else candidate)
                        for key in observed}
            results["cases"]["source_middle_byte"] = {"mutation": mutation,
                "before": before, "after": after, "observed": observed,
                "expected": expected, "passed": observed == expected}
    finally:
        # Keep the warm render's original evidence intact; negative receipt above
        # is separately retained. The owned normalized source is restored too.
        (context.out / "receipt.json").write_bytes(original_receipt)
    meter.phase = "negative_final_before"
    qc_before = qc._load_qc_pass_record(context)
    loud_before = loudness_cache.lookup(final, root=context.root)
    if qc_before is None or loud_before is None:
        raise RuntimeError("final negative requires real warm QC and loudness records")
    meter.phase = "negative_final_changed"
    with middle_byte_mutation(final) as mutation:
        observed = {"qc_record_invalidated": qc._load_qc_pass_record(context) is None,
                    "loudness_record_invalidated": loudness_cache.lookup(final, root=context.root) is None}
        results["cases"]["final_middle_byte"] = {"mutation": mutation,
            "observed": observed, "expected": {key: candidate for key in observed},
            "passed": all(value == candidate for value in observed.values())}
    meter.phase = "negative_segment_before"
    record = qc._load_qc_pass_record(context)
    if not record or not record.get("segs"):
        raise RuntimeError("segment negative requires active production QC segments")
    segment = record["segs"][0]
    segment_path = context.cache / "vseg" / segment["name"]
    meter.phase = "negative_segment_changed"
    with middle_byte_mutation(segment_path) as mutation:
        changed_record = qc._load_qc_pass_record(context)
        invalidated = changed_record is None or segment["name"] not in {
            item["name"] for item in changed_record["segs"]}
        results["cases"]["segment_middle_byte"] = {"mutation": mutation,
            "segment": segment["name"], "invalidated": invalidated,
            "expected_invalidated": candidate, "passed": invalidated == candidate}
    meter.phase = "negative_restored"
    cache_state.verify_input_identity(context)
    if qc._load_qc_pass_record(context) is None or loudness_cache.lookup(final, root=context.root) is None:
        raise RuntimeError("restored media did not recover its original warm identities")
    results["passed"] = all(case["passed"] for case in results["cases"].values())
    return results


def child_main(repository, project, evidence, action, revision):
    """Fresh process import isolation; this is the only production-import path."""
    start = time.monotonic()
    repository, project, evidence = map(Path, (repository, project, evidence))
    sys.path.insert(0, str(repository))
    sys.pycache_prefix = str(project / "home/unused-bytecode")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(TimeoutError("parent deadline")))
    meter = DigestMeter()
    report = {"action": action, "revision": revision, "passed": False}
    try:
        meter.install()
        if action == "render":
            from vtx.cli import main
            meter.phase = "production_all"
            sys.argv = ["vtx", "-t", str(project / "timeline.json"), "all"]
            try:
                code = main() or 0
            except SystemExit as error:
                code = error.code if isinstance(error.code, int) else 1
            report["render_wall_seconds"] = time.monotonic() - start
            report["render_exit_code"] = code
            if code != 0:
                raise RuntimeError(f"production vtx all exited {code}")
            report.update(validate_receipts(project))
            meter.phase = "decoded_output_equivalence"
            report["decoded_identity"] = decoded_identity(project / "out/final.mp4", evidence)
            report["passed"] = True
        elif action == "negative":
            report["negative"] = identity_negatives(project, evidence, meter,
                                                      candidate=revision == CANDIDATE)
            report["passed"] = report["negative"]["passed"]
        else:
            raise ValueError(f"unknown child action: {action}")
    except BaseException as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        traceback.print_exc()
    finally:
        report["child_wall_seconds"] = time.monotonic() - start
        report["digest_meter"] = meter.summary()
        meter.restore()
        write_json(evidence / "result.json", report)
    return 0 if report["passed"] else 1


def compare_identities(samples):
    """Require decoded equivalence for every cold and warm sample, not averages."""
    identities = [s.get("child", {}).get("decoded_identity") for s in samples]
    return bool(identities) and all(item is not None and item == identities[0]
                                    for item in identities)


def performance_assessment(samples):
    """Evaluate cold pairs; describe warm pairs and both percentage denominators.

    The range-of-deltas uncertainty band is a conservative experiment heuristic,
    not an additional Issue #85 acceptance requirement. An actual gate excess
    always fails, independently of this heuristic.
    """
    import statistics
    timings, pairs, inconclusive = {}, [], False
    for phase in ("cold", "warm"):
        medians = {}
        for label in ("baseline", "candidate"):
            values = [sample["child"]["render_wall_seconds"] for sample in samples
                      if sample["label"] == label and sample["phase"] == phase]
            if len(values) != 3 or any(value <= 0 for value in values):
                raise ValueError("performance assessment requires three positive times per phase/label")
            medians[label] = statistics.median(values)
            timings[f"{phase}_{label}"] = {"seconds": values,
                "median_seconds": medians[label], "range_seconds": max(values) - min(values),
                "range_over_median": (max(values) - min(values)) / medians[label]}
        timings[f"{phase}_candidate_over_baseline"] = medians["candidate"] / medians["baseline"]
        phase_pairs = []
        for pair in range(1, 4):
            times = {sample["label"]: sample["child"]["render_wall_seconds"]
                     for sample in samples if sample["pair"] == pair and sample["phase"] == phase}
            delta = times["candidate"] - times["baseline"]
            allowed = max(60.0, 0.1 * times["baseline"])
            candidate_allowed = max(60.0, 0.1 * times["candidate"])
            phase_pairs.append({"pair": pair, "phase": phase, **times,
                "candidate_minus_baseline_seconds": delta, "allowed_increase_seconds": allowed,
                "candidate_denominator_allowance_seconds": candidate_allowed,
                "candidate_denominator_check_passed": delta <= candidate_allowed,
                "increase_over_baseline": delta / times["baseline"],
                "increase_over_candidate": delta / times["candidate"],
                "gate_applies": phase == "cold",
                "gate_passed": delta <= allowed, "margin_seconds": allowed - delta})
        spread = max(p["candidate_minus_baseline_seconds"] for p in phase_pairs) - min(
            p["candidate_minus_baseline_seconds"] for p in phase_pairs)
        for pair in phase_pairs:
            pair["paired_delta_range_seconds"] = spread
            pair["uncertainty_overlaps_gate"] = pair["margin_seconds"] <= spread
            inconclusive |= phase == "cold" and pair["uncertainty_overlaps_gate"]
        pairs.extend(phase_pairs)
    status = ("FAIL" if any(pair["gate_applies"] and not pair["gate_passed"] for pair in pairs) else
              "INCONCLUSIVE" if inconclusive else "PASS")
    return {"status": status, "timings": timings, "paired_checks": pairs,
            "gate": "every cold pair: candidate - baseline <= max(60 seconds, 0.10 * baseline); "
                "warm pairs are descriptive only; candidate-denominator figures are also descriptive",
            "noise_heuristic": "INCONCLUSIVE if any cold threshold margin <= range of cold paired deltas; "
                "conservative experiment heuristic, not a new issue requirement"}


def run_experiment(baseline: Path, candidate: Path, source: Path, root: Path) -> dict:
    """Create root/benchmark, execute three cold pairs + six warms + negatives.

    Per-child wall limit is 900 seconds, including decoded-output verification.
    The 5,400-second overall deadline includes runner setup and all child work.
    Acquisition/normalization belongs to the caller and is not included.
    """
    start = time.monotonic()
    deadline = start + TOTAL_SECONDS
    if not sys.platform.startswith("linux"):
        raise RuntimeError("benchmark requires Linux process-tree monitoring")
    root = Path(os.path.abspath(root))
    budget_root = safe_directory(root.parent)
    if not root.exists():
        root.mkdir(mode=0o700)
    root = safe_directory(root)
    source = Path(os.path.abspath(source))
    safe_directory(source.parent)
    if not stat.S_ISREG(source.lstat().st_mode) or source.stat().st_size <= 131072:
        raise ValueError("qualified source must be a regular non-symlink file larger than 128 KiB")
    if budget_root not in source.parents or source.stat().st_nlink != 1:
        raise ValueError("normalized source must be an unshared owned file under the scratch budget root")
    repos = {"baseline": checked_revision(baseline, BASELINE),
             "candidate": checked_revision(candidate, CANDIDATE)}
    output = root / "benchmark"
    output.mkdir(mode=0o700)
    report = {"schema": "framivra.issue85.real-media-benchmark.v1", "passed": False, "status": "FAIL",
        "baseline": repos["baseline"], "candidate": repos["candidate"],
        "pair_order": [list(pair) for pair in PAIR_ORDER],
        "run_limit_seconds": RUN_SECONDS, "total_limit_seconds": TOTAL_SECONDS,
        "rss_limit_bytes": RSS_LIMIT_BYTES, "scratch_limit_bytes": SCRATCH_LIMIT_BYTES,
        "scratch_budget_root": str(budget_root),
        "resource_monitor": "Linux process-tree RSS and allocated st_blocks, sampled every 0.2 s; "
            "includes original/normalized media and outputs; no OS hard reservation",
        "production_command": ["vtx", "-t", "timeline.json", "all"],
        "environment_policy": CPU_POLICY,
        "cache_policy": "new project/application caches for each cold run; warm reuses its "
            "own cold project's caches; OS page cache is uncontrolled (not disk-cold)",
        "measurement_scope": "child setup/imports plus full production all wall time, unchanged QC, instrumentation "
            "and production imports included from child_main entry; Python interpreter launch "
            "before that entry is excluded; monitored process wall also includes launch and decoded hashes",
        "source_acquisition_and_normalization_included": False,
        "source": str(source), "source_bytes": source.stat().st_size,
        "samples": [], "negatives": [], "output": str(output)}
    write_json(output / "plan.json", {k: v for k, v in report.items()
                                     if k not in ("samples", "negatives")})
    projects = {}
    helper = Path(__file__).resolve()
    def execute(label, project, name, action):
        evidence = project / name
        evidence.mkdir()
        remaining = deadline - time.monotonic()
        result = monitored_run([sys.executable, "-I", "-B", str(helper), "--child",
            repos[label]["path"], str(project), str(evidence), action, repos[label]["commit"]],
            cwd=project, env=isolated_environment(project), log=evidence / "process.log",
            timeout=min(RUN_SECONDS, remaining), budget_root=budget_root)
        result.update(label=label, project=str(project), evidence=str(evidence), phase=name)
        child_report = evidence / "result.json"
        if child_report.exists():
            result["child"] = read_json(child_report)
        result["passed"] = (result["exit_code"] == 0 and not result["timed_out"]
                            and not result["budget_exceeded"]
                            and result.get("child", {}).get("passed") is True)
        return result
    try:
        # One shared, explicitly owned normalized file avoids six 1.2 GiB copies.
        # Production permits absolute source references; caches/out stay isolated.
        setup_start = time.monotonic()
        for pair_number in range(1, 4):
            for label in ("baseline", "candidate"):
                if time.monotonic() >= deadline:
                    raise TimeoutError("overall benchmark deadline during setup")
                project = output / f"pair-{pair_number}-{label}"
                project.mkdir()
                for relative in ("home/config", "home/cache", "home/tmp"):
                    (project / relative).mkdir(parents=True)
                write_json(project / "timeline.json", timeline_spec(source))
                write_json(project / "config.json", {})
                projects[(pair_number, label)] = project
        report["setup_wall_seconds"] = time.monotonic() - setup_start
        for pair_number, pair in enumerate(PAIR_ORDER, 1):
            # Adjacent cold samples preserve B/C, C/B, B/C ordering. Both warm
            # samples follow the pair, before moving to the next fresh projects.
            for phase in ("cold", "warm"):
                for label in pair:
                    result = execute(label, projects[(pair_number, label)], phase, "render")
                    result["pair"] = pair_number
                    report["samples"].append(result)
                    if not result["passed"]:
                        raise RuntimeError(f"pair {pair_number} {label} {phase} failed; inspect {result['evidence']}")
        report["decoded_output_equivalence"] = compare_identities(report["samples"])
        if not report["decoded_output_equivalence"]:
            raise RuntimeError("decoded frame/PCM identity mismatch across cold/warm baseline/candidate")
        for label in ("baseline", "candidate"):
            result = execute(label, projects[(3, label)], "negative", "negative")
            report["negatives"].append(result)
            if not result["passed"]:
                raise RuntimeError(f"{label} identity negatives failed; inspect {result['evidence']}")
        report["performance"] = performance_assessment(report["samples"])
        report["timings"] = report["performance"]["timings"]
        report["status"] = report["performance"]["status"]
        report["passed"] = report["status"] == "PASS"
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        report["total_wall_seconds"] = time.monotonic() - start
        if report["total_wall_seconds"] > TOTAL_SECONDS:
            report["passed"] = False
            report["status"] = "FAIL"
            report["overall_deadline_exceeded"] = True
        write_json(output / "result.json", report)
    return report


if __name__ == "__main__":
    if len(sys.argv) == 7 and sys.argv[1] == "--child":
        sys.exit(child_main(*sys.argv[2:]))
    raise SystemExit("Import run_experiment(); source acquisition belongs to the caller.")
