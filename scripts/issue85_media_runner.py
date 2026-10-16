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


def process_records():
    records = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            records[int(entry.name)] = {'parent':int(fields[1]), 'session':int(fields[3]),
                'start':int(fields[19]), 'state':fields[0],
                'rss':max(0,int(fields[21])) * os.sysconf('SC_PAGE_SIZE')}
        except (OSError, ValueError, IndexError):
            continue
    return records


def monitored_run(args, cwd, env, log, timeout, budget_root):
    """Linux-only owned-tree monitor, with pidfd signalling and scoped adoption.

    A process-local subreaper keeps orphaned descendants attributable until they
    finish. Existing direct children are excluded. No bare PID is signalled.
    This synchronous helper refuses other Python threads and existing own child
    processes, avoiding ambiguous adoption of concurrent work. RSS/allocated
    blocks are sampled, not kernel hard reservations.
    """
    import ctypes
    command = list(map(str, args))
    if (not sys.platform.startswith('linux') or not hasattr(os, 'pidfd_open')
            or not hasattr(signal, 'pidfd_send_signal') or threading.active_count() != 1):
        raise RuntimeError('Owned-tree monitoring requires single-threaded Linux with pidfds')
    owner = os.getpid()
    probe_fd = os.pidfd_open(owner)
    try:
        signal.pidfd_send_signal(probe_fd, 0)  # Capability check; no signal delivered.
    finally:
        os.close(probe_fd)
    if any(r['parent'] == owner for r in process_records().values()):
        raise RuntimeError('Concurrent existing child processes prevent isolated ownership')
    if timeout <= 0:
        return {'exit_code':None, 'timed_out':True, 'wall_seconds':0.0,
                'started':False, 'argv':command, 'budget_exceeded':None}
    started = time.monotonic()
    peak_scratch = allocated_bytes(budget_root)
    if peak_scratch > SCRATCH_LIMIT_BYTES:
        return {'exit_code':None,'timed_out':False,'wall_seconds':0.0,
                'started':False,'argv':command,'budget_exceeded':'scratch',
                'peak_scratch_allocated_bytes':peak_scratch,'peak_process_tree_rss_bytes':0}
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
        raise RuntimeError('Cannot inspect process-local child subreaper state')
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise RuntimeError('Cannot adopt owned orphan descendants')
    process, owned, reaped = None, {}, []
    peak_rss, exceeded, expired, descendants_after_exit = 0, None, False, False
    cleanup_confirmed = False

    def observe():
        nonlocal peak_rss, peak_scratch, descendants_after_exit
        records = process_records()
        if process is None:
            return []
        # Direct adopted children belong to this isolated invocation; descendants
        # are also discovered before adoption, even if they start a new session.
        selected = {pid for pid,r in records.items() if r['parent'] == owner}
        if (process.pid in records and (process.pid not in owned or
                records[process.pid]['start'] == owned[process.pid]['start'])):
            selected.add(process.pid)
        selected |= {pid for pid, value in owned.items()
                     if pid in records and records[pid]['start'] == value['start']}
        while True:
            more = {pid for pid,r in records.items() if r['parent'] in selected}
            if more <= selected:
                break
            selected.update(more)
        live = []
        for pid in selected:
            record = records.get(pid)
            if record is None:
                continue
            if pid not in owned:
                try:
                    fd = os.pidfd_open(pid)
                except ProcessLookupError:
                    continue
                again = process_records().get(pid)
                if again is None or again['start'] != record['start']:
                    os.close(fd)
                    continue
                owned[pid] = {'fd':fd,'start':record['start']}
            if record['start'] != owned[pid]['start']:
                raise RuntimeError('Owned process identity changed unexpectedly')
            if record['state'] != 'Z':
                live.append(pid)
            elif pid != process.pid:
                try:
                    child, status = os.waitpid(pid, os.WNOHANG)
                    if child:
                        reaped.append({'pid':child,'status':status})
                except ChildProcessError:
                    pass  # Still reaped by its live original parent.
        peak_rss = max(peak_rss, sum(records[pid]['rss'] for pid in live))
        peak_scratch = max(peak_scratch, allocated_bytes(budget_root))
        if process.poll() is not None and any(pid != process.pid for pid in live):
            descendants_after_exit = True
        return live

    def signal_owned(signum):
        for pid in observe():
            try:
                signal.pidfd_send_signal(owned[pid]['fd'], signum)
            except ProcessLookupError:
                pass

    def stop():
        end = time.monotonic() + 3
        signal_owned(signal.SIGTERM)
        while observe() and time.monotonic() < end:
            signal_owned(signal.SIGKILL)
            time.sleep(.02)
        live = observe()
        if process is not None:
            process.wait(timeout=1)
        if live:
            raise RuntimeError('Owned descendant termination could not be confirmed')
        # Reap adopted zombies after their final exit; only known children.
        observe()
        return True

    try:
        with Path(log).open('xb') as stream:
            process = subprocess.Popen(command,cwd=cwd,env=env,stdin=subprocess.DEVNULL,
                stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                leader = process_records().get(process.pid)
                if leader is None:
                    raise RuntimeError('Could not establish owned leader identity')
                owned[process.pid] = {'fd':os.pidfd_open(process.pid), 'start':leader['start']}
                while True:
                    live = observe()
                    expired = time.monotonic() - started >= timeout
                    exceeded = ('rss' if peak_rss > RSS_LIMIT_BYTES else
                                'scratch' if peak_scratch > SCRATCH_LIMIT_BYTES else None)
                    if expired or exceeded:
                        cleanup_confirmed = stop()
                        break
                    if process.poll() is not None and not live:
                        cleanup_confirmed = True
                        break
                    time.sleep(min(POLL_SECONDS,max(.001,timeout-(time.monotonic()-started))))
            except BaseException:
                cleanup_confirmed = stop()
                raise
    finally:
        for value in owned.values():
            os.close(value['fd'])
        if libc.prctl(36, previous.value, 0, 0, 0) != 0:
            raise RuntimeError('Could not restore process-local subreaper state')
    return {'exit_code':process.returncode,'timed_out':expired,
        'wall_seconds':time.monotonic()-started,'started':True,'argv':command,'log':str(log),
        'budget_exceeded':exceeded,'peak_process_tree_rss_bytes':peak_rss,
        'peak_scratch_allocated_bytes':peak_scratch,'poll_seconds':POLL_SECONDS,
        'rss_limit_bytes':RSS_LIMIT_BYTES,'scratch_limit_bytes':SCRATCH_LIMIT_BYTES,
        'owned_processes_observed':len(owned),
        'owned_identities':[{'pid':pid,'start_ticks':value['start']} for pid,value in owned.items()],
        'cleanup_grace_seconds':3, 'descendants_after_leader_exit':descendants_after_exit,
        'owned_tree_finished':cleanup_confirmed,'adopted_children_reaped':reaped}


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


class CacheMeter:
    """Observe real production decisions and writes; never authorize cache reuse.

    Wrappers delegate unchanged, including failed decisions and commands. Only
    calls made during the measured `vtx all` are included. QC's own receipt is
    the evidence of skipped probes; a usable record alone is not a cache hit.
    """
    def __init__(self, project):
        self.project = Path(project).resolve()
        self.rows = []
        self.patches = []
        self.lock = threading.Lock()
        self.final_before = self.final_stat()
        self.identity_proof_wall_seconds = 0.0
        self.identity_before = self.identities()

    def identities(self):
        from vtx import cache_state
        timeline = self.project / "timeline.json"
        value = read_json(timeline)
        final = self.project / "out/final.mp4"
        started = time.monotonic()
        result = {"timeline_sha256": cache_state.file_digest(timeline),
                "config_sha256": cache_state.file_digest(self.project / "config.json"),
                "sources_sha256": {source["id"]: cache_state.file_digest(
                    self.project / source["path"]) for source in value["sources"]},
                "final_sha256": cache_state.file_digest(final) if final.exists() else None}
        self.identity_proof_wall_seconds += time.monotonic() - started
        return result

    def final_stat(self):
        try:
            value = (self.project / "out/final.mp4").stat()
        except FileNotFoundError:
            return None
        return {"size": value.st_size, "mtime_ns": value.st_mtime_ns}

    def path(self, path):
        resolved = Path(path).resolve()
        try:
            return str(resolved.relative_to(self.project))
        except ValueError:
            return str(resolved)

    def patch(self, module, name, observe):
        original = getattr(module, name)
        @functools.wraps(original)
        def wrapper(*args, **kwargs):
            row = observe(*args, **kwargs)
            row["completed"] = False
            with self.lock:
                self.rows.append(row)
            result = original(*args, **kwargs)
            row["completed"] = True
            if row["kind"] == "audio":
                row.update(path=self.path(result[0]), hit=result[1] is None)
            elif row["kind"] in ("video", "qc_record", "loudness_lookup"):
                row["hit"] = result is not None if row["kind"] != "video" else result is True
            return result
        self.patches.append((module, name, original))
        setattr(module, name, wrapper)

    def install(self):
        from vtx import stages, composite, events, recovery, qc, loudness_cache
        self.patch(stages, "source_audio_extraction", lambda c, sid:
                   {"kind": "audio", "source": sid})
        self.patch(composite, "media_cache_valid", lambda path, *a, **kw:
                   {"kind": "video", "path": self.path(path)})
        self.patch(events, "cache_hit", lambda c, method, **kw:
                   {"kind": "cache_event", "validator": method, **kw})
        self.patch(recovery, "atomic_ffmpeg", lambda c, cmd, execute:
                   {"kind": "command", "argv": list(map(str, cmd)),
                    "target": self.path(cmd[-1]) if Path(str(cmd[-1])).is_absolute() else str(cmd[-1])})
        self.patch(qc, "_load_qc_pass_record", lambda c: {"kind": "qc_record"})
        self.patch(loudness_cache, "lookup", lambda path, *a, **kw:
                   {"kind": "loudness_lookup", "path": self.path(path)})
        self.patch(loudness_cache, "measure_and_store", lambda path, *a, **kw:
                   {"kind": "loudness_measure", "path": self.path(path)})

    def summary(self):
        identity_after = self.identities()
        return {"schema": "framivra.issue85.cache-observations.v1",
                "scope": "unchanged production calls during vtx all only; no synthetic hits",
                "final_before": self.final_before, "final_after": self.final_stat(),
                "identity_before": self.identity_before, "identity_after": identity_after,
                "identity_proof_wall_seconds": self.identity_proof_wall_seconds,
                "identity_proof_scope": "extra harness full-file reads; excluded only from render_wall_seconds; "
                    "included in child/process bounds and separately phased digest accounting",
                "calls": self.rows,
                "always_recomputed": ["audio mix and WAV-domain loudness checks",
                    "video concatenation and final mux", "QC frame/duration/resolution metadata",
                    "QC concurrent-audio-window energy"],
                "qc_cache_record_refresh_is_not_probe_recomputation": True}

    def restore(self):
        for module, name, original in reversed(self.patches):
            setattr(module, name, original)


def warm_cache_assessment(cold, warm, revision):
    """Fail closed per sample; baseline remux invalidation is explicit and proven.

    The fixed fixture has one extracted source, three preencoded clips and all
    active composed segments. Every warm lookup must hit, and none of those
    caches may be rebuilt. Unchanged candidate output must reuse QC/loudness.
    A genuine content-changing remux, or baseline stat-changing remux, is an
    explicit non-applicable case only with full input/output identity evidence.
    """
    result = {"passed": False, "caches": {}, "errors": []}
    try:
        cm, wm = cold["cache_observations"], warm["cache_observations"]
        if cm["schema"] != "framivra.issue85.cache-observations.v1" or wm["schema"] != cm["schema"]:
            raise ValueError("missing or unsupported cache observation schema")
        cc, wc = cm["calls"], wm["calls"]
        identities = [cm["identity_before"], cm["identity_after"],
                      wm["identity_before"], wm["identity_after"]]
        inputs = [{key: value for key, value in identity.items() if key != "final_sha256"}
                  for identity in identities]
        digest_ok = lambda value: (isinstance(value, str) and len(value) == 64
                                   and all(ch in "0123456789abcdef" for ch in value))
        inputs_ok = (set(inputs[0]) == {"timeline_sha256", "config_sha256", "sources_sha256"}
                     and set(inputs[0]["sources_sha256"]) == {"source"}
                     and all(digest_ok(value) for value in [inputs[0]["timeline_sha256"],
                         inputs[0]["config_sha256"], *inputs[0]["sources_sha256"].values()])
                     and all(value == inputs[0] for value in inputs))
        if not inputs_ok:
            result["errors"].append("complete source/timeline/config identities changed or are missing across cold/warm")
        finals = [identity["final_sha256"] for identity in identities[1:]]
        finals_ok = all(digest_ok(value) for value in finals) and finals[0] == finals[1]
        final_unchanged = finals_ok and finals[1] == finals[2]
        result["final_complete_sha256_unchanged"] = final_unchanged
        if not finals_ok:
            result["errors"].append("warm initial complete-file SHA-256 does not match its own cold output")
        if not cc or not wc or any(row.get("completed") is not True for row in cc + wc):
            raise ValueError("missing or incomplete production cache observations")
        def calls(rows, kind):
            return [row for row in rows if row["kind"] == kind]
        commands = calls(wc, "command")
        cache_writes = [row["target"] for row in commands
                        if row["target"].startswith(("cache/audio/", "cache/seg/", "cache/vseg/",
                                                     "cache/vseg_pass/"))]
        audio = calls(wc, "audio")
        cold_audio = calls(cc, "audio")
        expected_audio = {row["path"] for row in cold_audio}
        audio_events = [row for row in calls(wc, "cache_event")
                        if row["validator"] == "audio_cache_valid"]
        audio_ok = (len(expected_audio) == 1 and {row["source"] for row in cold_audio} == {"source"}
                    and {row["path"] for row in audio} == expected_audio
                    and {row["source"] for row in audio} == {"source"}
                    and bool(audio) and all(row["hit"] is True for row in audio)
                    and len(audio_events) == len(audio) and not cache_writes)
        result["caches"]["source_audio"] = {"applicable": True, "passed": audio_ok,
            "expected_paths": sorted(expected_audio), "hits": sum(row["hit"] is True for row in audio),
            "lookups": len(audio), "production_hit_events": len(audio_events)}
        for domain, minimum in (("seg", 3), ("vseg", 1)):
            prefix = f"cache/{domain}/"
            expected = {row["path"] for row in calls(cc, "video") if row["path"].startswith(prefix)}
            observed = [row for row in calls(wc, "video") if row["path"].startswith(prefix)]
            passed = (len(expected) >= minimum and {row["path"] for row in observed} == expected
                      and all(row["hit"] is True for row in observed) and not cache_writes)
            result["caches"][domain] = {"applicable": True, "passed": passed,
                "expected_paths": sorted(expected), "lookups": len(observed),
                "hits": sum(row["hit"] is True for row in observed)}
        result["cache_rebuild_targets"] = cache_writes
        if cache_writes:
            result["errors"].append("warm invocation wrote extracted-audio or video caches")
        before, after = wm["final_before"], wm["final_after"]
        if any(not isinstance(value, dict) or set(value) != {"size", "mtime_ns"}
               or type(value["size"]) is not int or value["size"] <= 0
               or type(value["mtime_ns"]) is not int for value in (before, after)):
            raise ValueError("missing or invalid final stat snapshots")
        remuxed = any(row["target"] == "out/.final.tmp.mp4" for row in commands)
        baseline_remux = (revision == BASELINE and isinstance(before, dict) and isinstance(after, dict)
                          and before == cm["final_after"] and before["size"] == after["size"]
                          and before["mtime_ns"] != after["mtime_ns"] and remuxed and finals_ok and inputs_ok)
        content_changed = (finals_ok and not final_unchanged and remuxed and inputs_ok
                           and before == cm["final_after"] and before is not None and after is not None)
        no_final_reuse = baseline_remux or content_changed
        result["baseline_stat_cache_remux_exception"] = baseline_remux
        result["remux_changed_final_content"] = content_changed
        if not final_unchanged and not no_final_reuse:
            result["errors"].append("changed final content lacks observed remux and unchanged-input proof")
        if before != cm["final_after"] or before is None:
            result["errors"].append("warm final identity does not start at its own cold output")
        if revision not in (BASELINE, CANDIDATE):
            result["errors"].append("unknown frozen production revision")
        expected_probes = {f"底轨切点帧比对 dst={cl['dst_in']} src=source:{cl['src_in'] + 2}"
                           for cl in timeline_spec()["video_tracks"][0]["clips"]}
        expected_probes.add("空窗无叠加泄漏 @frame 359")
        qc = warm["qc_report"]
        skipped = [row["name"] for row in qc["skipped_probes"]]
        qc_lookups = calls(wc, "qc_record")
        qc_probes = [row["target"] for row in commands if row["target"].startswith("cache/qc_probe_")]
        qc_hits = sum(row["hit"] is True for row in qc_lookups)
        cold_checks = {row["name"] for row in cold["qc_report"]["checks"] if row["pass"] is True}
        warm_checks = {row["name"] for row in qc["checks"] if row["pass"] is True}
        qc_ok = (expected_probes <= cold_checks and expected_probes <= warm_checks
                 and len(qc_lookups) == 1
                 and ((not skipped and qc_hits == 0 and bool(qc_probes)) if no_final_reuse else
                      (set(skipped) == expected_probes and len(skipped) == len(expected_probes)
                       and qc_hits == 1 and not qc_probes)))
        reason = ("complete final SHA-256 changed after observed atomic remux despite unchanged inputs"
                  if content_changed else
                  "baseline stat-bound records invalidated by observed atomic remux and changed final mtime"
                  if baseline_remux else "unchanged warm output must reuse existing production records")
        result["caches"]["qc_probes"] = {"applicable": not no_final_reuse, "reason": reason,
            "passed": qc_ok, "expected_probes": sorted(expected_probes), "skipped_probes": skipped,
            "record_hits": qc_hits, "probe_commands": len(qc_probes)}
        lookup, measure = calls(wc, "loudness_lookup"), calls(wc, "loudness_measure")
        loud_hits = sum(row["hit"] is True for row in lookup)
        loud_ok = (len(lookup) == 1 and lookup[0]["path"] == "out/final.mp4"
                   and ((loud_hits == 0 and len(measure) == 1 and measure[0]["path"] == "out/final.mp4")
                        if no_final_reuse else (loud_hits == 1 and not measure)))
        result["caches"]["final_loudness"] = {"applicable": not no_final_reuse, "reason": reason,
            "passed": loud_ok, "lookups": len(lookup), "hits": loud_hits, "measurements": len(measure)}
        result["always_recomputed"] = wm["always_recomputed"]
        result["passed"] = not result["errors"] and all(row["passed"] for row in result["caches"].values())
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        result["errors"].append(f"incomplete cache evidence: {error}")
    return result


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
    cache_meter = None
    report = {"action": action, "revision": revision, "passed": False}
    try:
        meter.install()
        if action == "render":
            meter.phase = "cache_evidence_before"
            cache_meter = CacheMeter(project)
            cache_meter.install()
            from vtx.cli import main
            meter.phase = "production_all"
            sys.argv = ["vtx", "-t", str(project / "timeline.json"), "all"]
            try:
                code = main() or 0
            except SystemExit as error:
                code = error.code if isinstance(error.code, int) else 1
            report["render_exit_code"] = code
            meter.phase = "cache_evidence_after"
            report["cache_observations"] = cache_meter.summary()
            report["render_wall_seconds"] = (time.monotonic() - start
                                               - cache_meter.identity_proof_wall_seconds)
            report["harness_identity_proof_wall_seconds"] = cache_meter.identity_proof_wall_seconds
            cache_meter.restore()
            cache_meter = None
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
        if cache_meter is not None:
            meter.phase = "cache_evidence_after"
            report["cache_observations"] = cache_meter.summary()
            cache_meter.restore()
        report["digest_meter"] = meter.summary()
        report["harness_identity_proof_bytes"] = sum(row["bytes_read"] for row in meter.rows
            if row["phase"] in ("cache_evidence_before", "cache_evidence_after"))
        meter.restore()
        report["child_wall_seconds"] = time.monotonic() - start
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
            "own cold project's caches; every warm sample gates actual applicable reuse and no cache rebuild; "
            "OS page cache is uncontrolled (not disk-cold); identity-proof scans can affect page residency",
        "measurement_scope": "child setup/imports plus full production all wall time, unchanged QC, instrumentation "
            "and production imports included from child_main entry; Python interpreter launch "
            "before that entry is excluded; only separately reported extra harness identity-proof full reads "
            "are subtracted from render_wall_seconds; normal production digest costs remain included; "
            "monitored process wall includes launch, proof reads and decoded hashes",
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
                    if phase == "warm":
                        cold = next(sample for sample in report["samples"]
                                    if sample["pair"] == pair_number and sample["label"] == label
                                    and sample["phase"] == "cold")
                        result["warm_cache_gate"] = warm_cache_assessment(
                            cold.get("child", {}), result.get("child", {}), repos[label]["commit"])
                        result["passed"] = result["passed"] and result["warm_cache_gate"]["passed"]
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
