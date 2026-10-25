"""Offline, fail-closed analysis for the opt-in CEF end-to-end benchmark.

This module never executes recorded commands. Evidence is local observation, not
an authenticity proof: retain run.json's digest independently when sharing it.
Missing optional native metrics stay unknown; they are never imputed as zero.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
import hashlib
import math
from pathlib import Path, PurePosixPath
import re

from vtx.runtime.events import read_events, strict_json_loads

SCHEMA = "framivra.cef-benchmark-report/1"
RUN_SCHEMA = "framivra.cef-benchmark/1"
STAGES = ("validate", "audit", "prepare", "gfx", "video", "audio", "mux", "qc")
ROLES = ("baseline", "candidate")
CACHE_STATES = ("cold", "warm")
NATIVE_TIMINGS = (
    "request_wall_to_reply_us", "capture_wall_us", "signed_ready_us",
    "unsigned_load_us", "unsigned_ready_us", "unsigned_two_samples_us",
    "unsigned_veto_us", "signed_proof_us", "encode_queue_us", "alpha_us",
    "png_us", "disk_us", "backpressure_us",
)
NATIVE_COUNTS = (
    "signed_navigations", "unsigned_navigations", "signed_fence_count",
    "unsigned_fence_count", "signed_native_paints", "unsigned_native_paints",
    "unsigned_samples_captured", "unsigned_copied_paints", "unsigned_copied_bytes",
    "unsigned_discarded_paints", "capture_fps_requested",
)
HOST_TIMINGS = (
    "block_wall_us", "worker_startup_us", "cached_validate_us", "native_wait_us",
    "host_validate_us", "host_commit_us", "final_validate_us",
)
LIMITS = [
    "This workload is the original 900-frame HTML project, not the canonical 900-frame SVG baseline.",
    "Actual Mac execution is UNRUN unless this record contains completed runs from that Mac; synthetic/cloud tests are not Mac results.",
    "Strict QC passed is not visual perfection or human visual acceptance; decoded-stream equality is not compressed-file equality.",
    "Wall time is the timed all command; preparation, snapshots, extra strict QC and decode verification are outside that boundary.",
    "Stage and wave intervals overlap. Their union is reported, not a sum of parents, children or parallel work.",
    "Native capture milestones overlap and are elapsed offsets, not CPU times. Do not add them or subtract them across clock origins.",
    "Host final_validate_us checks only the selected block; whole-page certification remains inside gfx execution events.",
    "Sampled process-tree RSS can miss short peaks; CPU is a sampled lower bound. GPU/VRAM and unmeasured resources are unknown.",
    "Legacy receipts without observed_by retain unknown causal linkage; present links are checked, not treated as authenticity.",
    "File hashes detect evidence changes against this manifest, not a rewritten manifest or an untrusted producer.",
]


def _dict(value):
    return value if isinstance(value, dict) else {}


def _number(value, positive=False):
    return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def _absolute(value):
    return isinstance(value, str) and bool(value) and "\0" not in value and Path(value).is_absolute() and ".." not in Path(value).parts


def _digest(value, lengths=(64,)):
    return isinstance(value, str) and len(value) in lengths and re.fullmatch(r"[0-9a-f]+", value) is not None


def _safe(value):
    """Keep invalid raw observations visible without emitting invalid JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _stats(values):
    values = [v for v in values if type(v) in (int, float) and math.isfinite(v)]
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = None
    if ordered:
        median = ordered[middle]
        if len(ordered) % 2 == 0:
            low, high = ordered[middle - 1], ordered[middle]
            # The arithmetic mean of two finite values can overflow even when
            # their true midpoint is finite. Preserve every observation.
            median = low + (high - low) / 2 if (low >= 0) == (high >= 0) else low / 2 + high / 2
    return {"count": len(values), "median": median,
            "min": ordered[0] if ordered else None, "max": ordered[-1] if ordered else None}


def _distribution(values, expected):
    result = _stats(values)
    ordered = sorted(values)
    for name, quantile in (("p90", .90), ("p95", .95)):
        result[name] = ordered[max(0, math.ceil(len(ordered) * quantile) - 1)] if ordered else None
    result["missing_count"] = max(0, expected - len(values))
    result["status"] = "unknown" if not values else "partial" if len(values) < expected else "measured"
    return result


def _union(intervals):
    """Duration of a set of intervals; nested/parallel intervals count once."""
    total, end = 0.0, None
    for left, right in sorted(intervals):
        if end is None or left > end:
            total += right - left
        elif right > end:
            total += right - end
        end = right if end is None else max(end, right)
    return total


class _Evidence:
    def __init__(self, base_dir, run, issues):
        self.base = Path(base_dir).resolve()
        self.issues = issues
        self.manifest = run.get("evidence_files")
        self.checked = {}
        self.errors = []
        self.hashed = 0
        if not isinstance(self.manifest, dict) or not self.manifest:
            issues.append("nonempty evidence_files hash manifest is required")
        if self.manifest is not None and not isinstance(self.manifest, dict):
            issues.append("evidence_files is not a mapping")
            self.manifest = {}
        if isinstance(self.manifest, dict):
            for name, identity in self.manifest.items():
                if not isinstance(identity, dict) or set(identity) != {"sha256", "size"}:
                    message = "{}: evidence manifest identity must contain SHA256 and size only".format(name)
                    issues.append(message)
                    self.errors.append(message)
                self.path({**_dict(identity), "path": name}, require_hash=True)

    def path(self, reference, require_hash=False):
        item = reference if isinstance(reference, dict) else {"path": reference}
        name = item.get("path")
        try:
            if (not isinstance(name, str) or not name or "\\" in name or ":" in name
                    or PurePosixPath(name).is_absolute()
                    or any(part in ("", ".", "..") for part in name.split("/"))):
                raise ValueError("unsafe or missing relative evidence path")
            path = self.base.joinpath(*name.split("/"))
            for part in (path, *path.parents):
                if part == self.base:
                    break
                if part.is_symlink():
                    raise ValueError("symlink evidence is unsupported")
            if not path.is_file():
                raise ValueError("evidence is missing or not a regular file")
            manifest_identity = _dict(_dict(self.manifest).get(name))
            if self.manifest is not None and name not in self.manifest:
                raise ValueError("referenced evidence is absent from evidence_files")
            expected = item.get("sha256", manifest_identity.get("sha256"))
            size = item.get("size", manifest_identity.get("size"))
            if expected is None and (require_hash or self.manifest is not None):
                raise ValueError("evidence SHA256 is missing")
            if expected is not None:
                if not _digest(expected):
                    raise ValueError("invalid evidence SHA256")
                if manifest_identity.get("sha256", expected) != expected:
                    raise ValueError("conflicting evidence SHA256 declarations")
                if name not in self.checked:
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                    self.checked[name] = (digest.hexdigest(), path.stat().st_size)
                    self.hashed += 1
                actual, actual_size = self.checked[name]
                if actual != expected:
                    raise ValueError("evidence SHA256 mismatch")
                if size is not None and (type(size) is not int or size < 0 or size != actual_size):
                    raise ValueError("evidence size mismatch")
            return path
        except (OSError, ValueError) as error:
            message = "{}: {}".format(name if isinstance(name, str) else "evidence", error)
            if message not in self.issues:
                self.issues.append(message)
            if message not in self.errors:
                self.errors.append(message)
            return None

    def json(self, reference):
        path = self.path(reference)
        if path is None:
            return None
        try:
            return strict_json_loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            message = "{}: JSON parser error: {}".format(path.relative_to(self.base), error)
            self.issues.append(message)
            self.errors.append(message)
            return None


def _events(evidence, reference):
    unknown = {"status": "unknown", "complete": False, "stages": {}, "wave_jobs": [],
               "wave_union_seconds": None, "run_seconds": None, "receipt_commits": [], "reason": "not recorded"}
    if reference is None:
        return unknown
    path = evidence.path(reference)
    if path is None:
        return unknown
    try:
        parsed = read_events(path.read_bytes())
    except (OSError, ValueError, TypeError, UnicodeError) as error:
        evidence.issues.append("{}: events parser error: {}".format(path.name, error))
        return dict(unknown, status="invalid", reason=str(error))
    rows = parsed["events"]
    stage_spans, waves = {}, []
    for row in rows:
        if row["event"] != "task_terminal":
            continue
        start, end = row["timing"]["start_ms"], row["timing"]["offset_ms"]
        if start is None:
            continue
        span = (start / 1000, end / 1000)
        if row["scope"] == "stage" and row["stage"]:
            stage_spans.setdefault(row["stage"], []).append(span)
        if row["scope"] == "wave_job":
            children = sorted({child["stage"] for child in rows
                               if child["parent_task_id"] == row["task_id"] and child["stage"]})
            waves.append({"task_id": row["task_id"], "stages": children,
                          "start_seconds": span[0], "end_seconds": span[1],
                          "seconds": span[1] - span[0], "outcome": row["outcome"]})
    stage_intervals = [span for spans in stage_spans.values() for span in spans]
    terminal = next((r for r in rows if r["event"] == "run_terminal"), None)
    return {"status": "complete" if parsed["complete"] else "incomplete",
            "complete": parsed["complete"], "run_id": parsed["run_id"],
            "exit_code": parsed["exit_code"], "reason": parsed["reason"],
            "run_seconds": terminal["monotonic_duration"] / 1000 if terminal else None,
            "stages": {stage: {"union_seconds": _union(spans), "occurrences": len(spans)}
                       for stage, spans in sorted(stage_spans.items())},
            "all_stage_union_seconds": _union(stage_intervals) if stage_intervals else None,
            "wave_jobs": waves,
            "wave_union_seconds": _union([(w["start_seconds"], w["end_seconds"]) for w in waves]) if waves else None,
            "receipt_commits": [{key: row[key] for key in ("run_id", "task_id", "event_id", "stage", "outcome")}
                                for row in rows if row["event"] == "receipt_commit"],
            "cache_hits": sum(r["event"] == "cache_hit" for r in rows),
            "failed_attempts": sum(r["event"] == "attempt_terminal" and r["exit_code"] != 0 for r in rows)}


def _native(evidence, references, run_id):
    host, adapter, unlinked_adapter, files = [], [], [], []
    if references is None:
        references = []
    if not isinstance(references, list):
        evidence.issues.append("native_paths is not a list")
        references = []
    paths = []
    for reference in references:
        path = evidence.path(reference)
        if path is not None:
            paths.append(path)
    # Pipeline execution IDs provide attribution. A warm snapshot can contain
    # cold files; never count those costs again merely because they are present.
    linked, foreign = set(), set()
    for path in paths:
        if not path.name.endswith(".pipeline.json"):
            continue
        prefix = str(path)[:-len(".pipeline.json")]
        try:
            data = strict_json_loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema") != "vtx.cef-host-pipeline/1":
                files.append({"path": str(path.relative_to(evidence.base)), "status": "unknown_format"})
                continue
            for key in HOST_TIMINGS:
                if data.get(key) is not None and not _number(data[key]):
                    raise ValueError("invalid host timing " + key)
            observed = _dict(data.get("execution")).get("run_id")
            if not observed or not run_id:
                files.append({"path": str(path.relative_to(evidence.base)), "status": "unknown_attribution"})
                continue
            if observed != run_id:
                foreign.add(prefix)
                files.append({"path": str(path.relative_to(evidence.base)), "status": "different_execution"})
                evidence.issues.append(path.name + ": current native evidence belongs to another execution")
                continue
            if data.get("completed") is not True:
                evidence.issues.append(path.name + ": host pipeline is incomplete")
            for key in HOST_TIMINGS:
                if data.get(key) is not None and not _number(data[key]):
                    raise ValueError("invalid host timing " + key)
            linked.add(prefix)
            host.append(data)
            files.append({"path": str(path.relative_to(evidence.base)), "status": "linked"})
        except (OSError, UnicodeError, ValueError, TypeError) as error:
            evidence.issues.append("{}: native parser error: {}".format(path.name, error))
            files.append({"path": str(path.relative_to(evidence.base)), "status": "invalid"})
    for path in paths:
        name = path.name
        if name.endswith(".pipeline.json"):
            continue
        try:
            status = "read"
            if name.endswith(".sync.jsonl"):
                prefix = str(path)[:-len(".sync.jsonl")]
                text = path.read_text(encoding="utf-8")
                if text and not text.endswith("\n"):
                    raise ValueError("truncated native JSONL")
                status = "linked" if prefix in linked else "different_execution" if prefix in foreign else "unknown_attribution"
                for line_number, line in enumerate(text.splitlines(), 1):
                    data = strict_json_loads(line)
                    if not isinstance(data, dict):
                        raise ValueError("native JSONL row is not an object at line " + str(line_number))
                    if data.get("event") != "adapter_timing":
                        continue
                    for key in NATIVE_TIMINGS + NATIVE_COUNTS:
                        if data.get(key) is not None and not _number(data[key]):
                            raise ValueError("invalid native metric " + key)
                    if data.get("delivery_status", 0) != 0:
                        evidence.issues.append(name + ": native delivery failed")
                    if prefix in linked:
                        adapter.append(data)
                    elif prefix not in foreign:
                        unlinked_adapter.append(data)
            elif name.endswith(".receipts.json"):
                data = strict_json_loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    raise ValueError("native receipts is not a list")
            files.append({"path": str(path.relative_to(evidence.base)), "status": status})
        except (OSError, UnicodeError, ValueError, TypeError) as error:
            evidence.issues.append("{}: native parser error: {}".format(name, error))
            files.append({"path": str(path.relative_to(evidence.base)), "status": "invalid"})
    return {"status": "measured" if host or adapter else "unknown", "files": files,
            "host_blocks": len(host), "adapter_samples": len(adapter),
            "unlinked_adapter_samples": len(unlinked_adapter),
            "attribution": "matching host execution.run_id and shared file stem; inherited costs excluded",
            "host_us": {key: _distribution([r[key] for r in host if _number(r.get(key))], len(host))
                        for key in HOST_TIMINGS},
            "adapter_us": {key: _distribution([r[key] for r in adapter if _number(r.get(key))], len(adapter))
                           for key in NATIVE_TIMINGS},
            "unlinked_adapter_us": {key: _distribution([r[key] for r in unlinked_adapter if _number(r.get(key))], len(unlinked_adapter))
                                    for key in NATIVE_TIMINGS},
            "adapter_counts": {key: _distribution([r[key] for r in adapter if _number(r.get(key))], len(adapter))
                               for key in NATIVE_COUNTS}}


def _command_ok(command, name, issues, strict=False):
    command = _dict(command)
    if type(command.get("exit_code")) is not int or command.get("exit_code") != 0:
        issues.append(name + " did not exit successfully")
    if command.get("timed_out") is not False:
        issues.append(name + " timeout status is not explicitly false")
    if command.get("error"):
        issues.append(name + " recorded an error")
    if _dict(command.get("cleanup")).get("errors"):
        issues.append(name + " recorded process-cleanup errors")
    if strict and (not isinstance(command.get("argv"), list) or "--strict" not in command["argv"] or "qc" not in command["argv"]):
        issues.append("strict QC invocation is not evidenced by --strict")
    if not _number(command.get("elapsed_seconds"), positive=True):
        issues.append(name + " elapsed_seconds is missing, zero, negative or nonfinite")


def _check_receipt(receipt, issues, label):
    if not isinstance(receipt, dict):
        issues.append(label + " receipt is unavailable")
        return {"status": "unknown", "stages": {}}
    for key in ("timeline", "input_fingerprint"):
        if not _digest(receipt.get(key)):
            issues.append(label + " receipt " + key + " identity is missing or invalid")
    stages = _dict(receipt.get("stages"))
    statuses = {name: "passed" if _dict(stages.get(name)).get("ok") is True else
                "failed" if _dict(stages.get(name)).get("ok") is False else "unknown" for name in STAGES}
    if any(value != "passed" for value in statuses.values()):
        issues.append(label + " receipt has failed or missing required stages")
    backend = _dict(receipt.get("gfx_backend"))
    if (backend.get("requested_backend") != "cef" or not isinstance(backend.get("identity"), str)
            or not backend["identity"] or backend.get("namespace") != "cef-alpha"):
        issues.append(label + " receipt does not identify the CEF backend and experimental namespace")
    return {"status": "passed" if all(v == "passed" for v in statuses.values()) else "failed",
            "stages": statuses, "input_fingerprint": receipt.get("input_fingerprint"),
            "timeline": receipt.get("timeline"), "gfx_backend": receipt.get("gfx_backend")}


def _check_qc(qc, issues, strict=True, receipt=None):
    checks = _dict(qc).get("checks")
    passed = isinstance(checks, list) and bool(checks) and all(_dict(r).get("pass") is True for r in checks)
    names = [_dict(row).get("name") for row in checks] if isinstance(checks, list) else []
    if not names or any(not isinstance(name, str) or not name.strip() for name in names):
        issues.append("QC check names are missing or invalid")
        passed = False
    core = ("成片帧数 = 900", "成片时长 ≈ 30.00s", "分辨率 1920x1080",
            "响度归一 ≈ -14 LUFS", "解码域真峰 TP ≤ -1 dBFS")
    if any(names.count(name) != 1 for name in core) or not any(
            isinstance(name, str) and re.fullmatch(r"[0-9]+ 轨并发窗口有能量", name) for name in names):
        issues.append("QC required frame/duration/resolution/audio checks are incomplete")
        passed = False
    detail = _dict(_dict(_dict(receipt).get("stages")).get("qc")).get("detail")
    if detail != "{0}/{0}".format(len(names)):
        issues.append("QC receipt count does not match the complete report")
        passed = False
    if strict and not any(_dict(row).get("name") == core[0] and
                          "解码计数" in str(_dict(row).get("detail", "")) for row in (checks if isinstance(checks, list) else [])):
        issues.append("strict QC does not evidence decoded frame counting")
        passed = False
    skipped = _dict(qc).get("skipped_probes")
    if not passed:
        issues.append("QC report has failed, empty or unknown checks")
    if strict and skipped != []:
        issues.append("strict QC skipped-probe evidence is missing or nonempty")
    return {"status": "passed" if passed and (not strict or skipped == []) else "failed",
            "names": names, "checks": len(checks) if isinstance(checks, list) else None,
            "passed_checks": sum(_dict(c).get("pass") is True for c in checks) if isinstance(checks, list) else None,
            "skipped_probes": skipped, "visual_content": "unknown"}


def _source_valid(source):
    source = _dict(source)
    worker = _dict(source.get("worker"))
    return (_absolute(source.get("repo")) and _digest(source.get("head"), (40, 64))
            and _digest(source.get("tree"), (40, 64)) and source.get("clean") is True
            and _absolute(worker.get("path")) and _digest(worker.get("sha256"))
            and type(worker.get("size")) is int and worker["size"] > 0)


def _source_key(source):
    source = _dict(source)
    worker = _dict(source.get("worker"))
    return [source.get(k) for k in ("repo", "head", "tree", "clean")] + [worker.get(k) for k in ("path", "sha256", "size")]


def _tool_key(tools):
    return {name: {key: _dict(_dict(tools).get(name)).get(key) for key in ("path", "sha256")}
            for name in ("python", "ffmpeg", "ffprobe")}


def _identity(plan, runs):
    issues = []
    if not _digest(plan.get("input_digest")):
        issues.append("plan input identity is missing or invalid")
    for role in ROLES:
        if not _source_valid(plan.get(role)):
            issues.append(role + " source/worker identity is incomplete or unclean")
    tools = _tool_key(plan.get("tools"))
    if any(not _absolute(value["path"]) or not _digest(value["sha256"]) for value in tools.values()):
        issues.append("tool identity is incomplete")
    if not isinstance(plan.get("common_env"), dict) or not isinstance(plan.get("candidate_env"), dict):
        issues.append("configuration identity is incomplete")
    try:
        from scripts.gfx.bench_cef import FIXED_ENV, _overrides
        common = _dict(plan.get("common_env"))
        candidate = _dict(plan.get("candidate_env"))
        if set(common) != set(FIXED_ENV):
            raise ValueError("common configuration keys are missing or unknown")
        _overrides([key + "=" + value for key, value in common.items()
                    if isinstance(value, str) and value != FIXED_ENV[key]])
        _overrides([key + "=" + value for key, value in candidate.items() if isinstance(value, str)])
        if any(not isinstance(value, str) for value in list(common.values()) + list(candidate.values())):
            raise ValueError("configuration values must be strings")
        label = plan.get("ablation")
        if candidate and (not isinstance(label, str) or not label.strip()):
            raise ValueError("candidate settings require a declared ablation label")
        base_env = _dict(plan.get("environment"))
        fixed = {"LANG": "C", "LC_ALL": "C", "TZ": "UTC", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        if (set(base_env) != set(fixed) | {"PATH", "HOME"}
                or any(base_env.get(k) != v for k, v in fixed.items())
                or not all(isinstance(base_env.get(k), str) and base_env[k] for k in ("PATH", "HOME"))):
            raise ValueError("base execution environment is incomplete or differs from the isolated policy")
    except (ImportError, ValueError, TypeError) as error:
        issues.append("configuration identity invalid: " + str(error))
    platform = _dict(plan.get("platform"))
    if not all(platform.get(key) for key in ("system", "release", "machine", "python")):
        issues.append("platform identity is incomplete")
    invocation_roots = set()
    for run in runs:
        argv = _dict(run.get("command")).get("argv")
        if isinstance(argv, list) and len(argv) > 4 and _absolute(argv[4]) and len(Path(argv[4]).parents) >= 4:
            invocation_roots.add(str(Path(argv[4]).parents[3]))
    if len(invocation_roots) != 1:
        issues.append("recorded commands do not share one invocation output root")
    for index, run in enumerate(runs):
        if run.get("input_digest") != plan.get("input_digest"):
            issues.append("run {} input identity differs or is missing".format(index + 1))
        role = run.get("role")
        expected = _dict(plan.get(role)) if role in ROLES else {}
        if run.get("source_identity_valid") is not True:
            issues.append("run {} source identity was not stable".format(index + 1))
        for boundary in ("identity_before", "identity_after"):
            if not _source_valid(run.get(boundary)) or _source_key(run.get(boundary)) != _source_key(expected):
                issues.append("run {} {} differs from declared source".format(index + 1, boundary))
        if run.get("tools_identity_valid") is not True:
            issues.append("run {} tool identity was not stable".format(index + 1))
        for boundary in ("tools_before", "tools_after"):
            if _tool_key(run.get(boundary)) != tools:
                issues.append("run {} {} differs from declared tools".format(index + 1, boundary))
        if "platform" in run and run["platform"] != plan.get("platform"):
            issues.append("run {} platform differs from declared context".format(index + 1))
        if "env" in run:
            expected_env = dict(_dict(plan.get("common_env")))
            if role == "candidate":
                expected_env.update(_dict(plan.get("candidate_env")))
            if run["env"] != expected_env:
                issues.append("run {} environment differs from declared context".format(index + 1))
    difference = {key: {"baseline": _dict(plan.get("common_env")).get(key), "candidate": value}
                  for key, value in _dict(plan.get("candidate_env")).items()
                  if _dict(plan.get("common_env")).get(key) != value}
    return {"status": "verified" if not issues else "unknown_or_different", "issues": issues,
            "comparison_kind": "strategy_ablation" if difference else "same_configuration_code_comparison",
            "candidate_setting_differences": difference,
            "input_digest": plan.get("input_digest"), "baseline": plan.get("baseline"),
            "candidate": plan.get("candidate"), "tools": plan.get("tools"),
            "platform": plan.get("platform"), "common_env": plan.get("common_env")}


def _equal(digests):
    known = [value for value in digests if _digest(value)]
    if len(set(known)) > 1:
        return "different"
    return "equal" if len(known) == len(digests) and len(known) >= 2 else "unknown"


def _receipt_links(receipt, observations, expected, issues, label):
    """Legacy missing links stay unknown; contradictory present links reject."""
    commits = [row for observation in observations for row in observation.get("receipt_commits", [])]
    linked, absent = 0, 0
    for stage in STAGES:
        row = _dict(_dict(_dict(receipt).get("stages")).get(stage))
        ref = row.get("observed_by")
        if ref is None:
            absent += 1
            continue
        matches = [event for event in commits if event["stage"] == stage
                   and event["run_id"] == expected.get(stage)
                   and event["outcome"] == "succeeded"
                   and ref == {key: event[key] for key in ("run_id", "task_id", "event_id")}]
        if len(matches) == 1:
            linked += 1
        else:
            issues.append(label + " receipt " + stage + " has invalid or swapped observation linkage")
    return {"status": "complete" if linked == len(STAGES) else "unknown" if absent == len(STAGES) else "partial_or_invalid",
            "linked_stages": linked, "missing_stages": absent}


def _resources(raw):
    """Malformed optional samples are unknown, never negative/fictional costs."""
    result = dict(_dict(raw))
    reasons = dict(_dict(result.get("reasons")))
    valid_samples = type(result.get("samples")) is int and result["samples"] > 0
    measured = result.get("status") in ("sampled", "partial") and valid_samples
    problems = []
    for key, reason_key in (("peak_tree_rss_bytes", "rss"), ("cpu_seconds_lower_bound", "cpu"),
                            ("peak_sampled_processes", "processes")):
        value = result.get(key)
        valid = _number(value) and (key == "cpu_seconds_lower_bound" or type(value) is int)
        if value is not None and (not valid or not measured):
            problems.append("invalid or unsupported " + key)
            value = None
        result[key] = value
        if value is None and not reasons.get(reason_key):
            reasons[reason_key] = "not measured" if not problems else "invalid optional resource evidence"
    result["reasons"] = reasons
    result["validation_errors"] = problems
    if problems or result.get("status") not in ("sampled", "partial", "unavailable"):
        result["status"] = "unknown"
    return result


def _media_evidence(media, evidence, issues):
    required_checks = ("final_exists", "ffprobe_full_stream", "video_stream", "audio_stream",
                       "video_geometry", "video_frame_count", "video_frame_rate", "video_start_time",
                       "audio_format", "audio_start_time", "video_full_decode", "video_timestamps",
                       "video_decoded_frame_count", "audio_full_decode", "audio_nonempty",
                       "audio_timestamps", "audio_duration", "source_unchanged")
    if any(_dict(media.get("checks")).get(name) is not True for name in required_checks):
        issues.append("required full-stream media checks are failed or incomplete")
    if media.get("error"):
        issues.append("media verifier recorded an error")
    references = _dict(media.get("evidence"))
    for name in ("ffprobe", "video", "audio"):
        _command_ok(_dict(media.get("commands")).get(name), "media " + name, issues)
        evidence.path(references.get(name + "_log"))
    try:
        from scripts.gfx._bench_runtime import _normalize_framemd5, _normalize_audio_framemd5
        probe = evidence.json(references.get("ffprobe"))
        streams = _dict(probe).get("streams")
        if not isinstance(streams, list):
            raise ValueError("ffprobe stream evidence is missing")
        videos = [row for row in streams if _dict(row).get("codec_type") == "video"]
        audios = [row for row in streams if _dict(row).get("codec_type") == "audio"]
        if len(videos) != 1 or len(audios) != 1 or videos[0] != media.get("video") or audios[0] != media.get("audio"):
            raise ValueError("reported stream metadata does not match ffprobe evidence")
        video, audio = videos[0], audios[0]
        if (video.get("width") != 1920 or video.get("height") != 1080
                or not isinstance(video.get("pix_fmt"), str) or not video["pix_fmt"]
                or str(video.get("nb_read_frames")) != "900"
                # 与 _bench_runtime.py 的 video_frame_rate 同判据：r_frame_rate
                # 精确 30，avg_frame_rate 允许 pts 舍入漂移（相对容差 2e-3）。
                or Fraction(str(video.get("avg_frame_rate"))) <= 0
                or abs(float(Fraction(str(video.get("avg_frame_rate")))) - 30.0) > 2e-3 * 30.0
                or Fraction(str(video.get("r_frame_rate"))) != 30
                or Fraction(str(video.get("start_pts"))) != 0
                or str(audio.get("sample_rate")) != "48000" or audio.get("channels") != 2
                or Fraction(str(audio.get("start_pts"))) != 0):
            raise ValueError("ffprobe evidence violates the fixed media workload")
        for kind, metadata, normalize, digest_name in (
                ("video", video, _normalize_framemd5, "video_digest"),
                ("audio", audio, _normalize_audio_framemd5, "audio_timeline_digest")):
            path = evidence.path(references.get(kind + "_framemd5"))
            normalized = evidence.json(references.get(kind + "_normalized"))
            if path is None or normalized is None:
                raise ValueError(kind + " decoded evidence is missing")
            actual, digest = normalize(path, metadata, 900, Fraction(30, 1))
            if actual != normalized or digest != media.get(digest_name):
                raise ValueError(kind + " decoded digest or normalized artifact disagrees with raw frames")
        path = evidence.path(references.get("audio_sha256"))
        if path is None:
            raise ValueError("decoded audio SHA256 evidence is missing")
        match = re.fullmatch(r"SHA256=([0-9a-fA-F]{64})\s*", path.read_text(encoding="ascii"))
        if (match is None or match.group(1).lower() != media.get("audio_digest")
                or match.group(1).lower() == hashlib.sha256(b"").hexdigest()):
            raise ValueError("decoded audio digest differs from evidence or is empty")
    except (ImportError, OSError, UnicodeError, ValueError, TypeError, KeyError, ZeroDivisionError) as error:
        issues.append("media evidence parser error: " + str(error))


def _analyze_run(run, index, base_dir, plan):
    issues = []
    evidence = _Evidence(base_dir, run, issues)
    if run.get("status") != "passed":
        issues.append("run status is not passed")
    if run.get("error") or run.get("identity_error"):
        issues.append("run recorded an execution or identity error")
    _command_ok(run.get("command"), "all command", issues)
    _command_ok(run.get("strict_command"), "strict QC command", issues, strict=True)
    try:
        role, round_number = run.get("role"), run.get("round")
        if role not in ROLES or type(round_number) is not int:
            raise ValueError("invalid role/round")
        argv = _dict(run.get("command")).get("argv")
        if not isinstance(argv, list) or len(argv) < 5 or not _absolute(argv[4]) or len(Path(argv[4]).parents) < 4:
            raise ValueError("missing original invocation root")
        invocation_root = Path(argv[4]).parents[3]
        prefix = [_dict(_dict(plan.get("tools")).get("python")).get("path"), "-B",
                  str(Path(_dict(plan.get(role)).get("repo", "")) / "vtx/vtx.py"), "-t",
                  str(invocation_root / "projects" / ("round-%03d" % round_number) / role / "timeline.json")]
        for name, reference, suffix in (("command", run.get("events_path"), ["all"]),
                                        ("strict_command", run.get("strict_events_path"), ["qc", "--strict"])):
            reference = _dict(reference).get("path") if isinstance(reference, dict) else reference
            if not isinstance(reference, str):
                raise ValueError("missing events path")
            expected = prefix + ["--events-jsonl", str(invocation_root / reference)] + suffix
            if _dict(run.get(name)).get("argv") != expected:
                issues.append(name + " argv differs from the declared full benchmark invocation")
    except (TypeError, ValueError) as error:
        issues.append("command provenance is incomplete: " + str(error))
    media = _dict(run.get("media"))
    if media.get("status") != "passed":
        issues.append("decoded media verification did not pass")
    for name in ("video_digest", "audio_digest", "audio_timeline_digest"):
        if not _digest(media.get(name)):
            issues.append("decoded " + name + " is missing or invalid")
    checks = media.get("checks")
    if not isinstance(checks, dict) or not checks or any(v is not True for v in checks.values()):
        issues.append("media checks are failed or incomplete")
    for reference in _dict(media.get("evidence")).values():
        if reference is not None:
            evidence.path(reference)
    _media_evidence(media, evidence, issues)
    receipt_raw = evidence.json(run.get("receipt_path"))
    all_receipt_raw = evidence.json(run.get("all_receipt_path"))
    receipt = _check_receipt(receipt_raw, issues, "strict")
    all_receipt = _check_receipt(all_receipt_raw, issues, "all")
    qc = _check_qc(evidence.json(run.get("qc_path")), issues, receipt=receipt_raw)
    all_qc = _check_qc(evidence.json(run.get("all_qc_path")), issues, strict=False, receipt=all_receipt_raw)
    if Counter(str(n) for n in qc["names"]) != Counter(str(n) for n in all_qc["names"]):
        issues.append("all/strict QC check sets differ or are truncated")
    for key in ("timeline", "input_fingerprint"):
        if receipt.get(key) != all_receipt.get(key):
            issues.append("all/strict receipt " + key + " changed")
    observation = _events(evidence, run.get("events_path"))
    strict_observation = _events(evidence, run.get("strict_events_path"))
    for name, value in (("all", observation), ("strict QC", strict_observation)):
        if value["status"] != "complete" or value.get("exit_code") != 0:
            issues.append(name + " event evidence is incomplete or unsuccessful")
        wall = _dict(run.get("command" if name == "all" else "strict_command")).get("elapsed_seconds")
        if _number(wall, True) and _number(value.get("run_seconds")) and value["run_seconds"] > wall + .001:
            issues.append(name + " child event duration exceeds enclosing command wall (1 ms tolerance)")
    if not set(STAGES).issubset(observation["stages"]):
        issues.append("all events lack required full-pipeline stage coverage")
    if "qc" not in strict_observation["stages"]:
        issues.append("strict QC events lack the qc stage")
    if observation.get("run_id") and observation.get("run_id") == strict_observation.get("run_id"):
        issues.append("all and strict QC reused the same execution ID")
    expected_all = {stage: observation.get("run_id") for stage in STAGES}
    linkage = {"all": _receipt_links(all_receipt_raw, [observation], expected_all, issues, "all"),
               "strict": _receipt_links(receipt_raw, [observation, strict_observation],
                                        dict(expected_all, qc=strict_observation.get("run_id")), issues, "strict")}
    native = _native(evidence, run.get("native_paths"), observation.get("run_id"))
    native["inherited_files"] = len(run.get("native_inherited_paths", [])) if isinstance(run.get("native_inherited_paths", []), list) else None
    elapsed = _dict(run.get("command")).get("elapsed_seconds")
    return {"index": index, "round": run.get("round"), "role": run.get("role"),
            "cache_state": run.get("cache_state"), "status": run.get("status", "unknown"),
            "elapsed_seconds": elapsed if _number(elapsed, positive=True) else None,
            "raw_elapsed_seconds": _safe(elapsed), "correctness": "passed" if not issues else "failed_or_unknown",
            "issues": list(dict.fromkeys(issues)), "media": media, "receipt": receipt, "qc": qc,
            "events": observation, "strict_events": strict_observation, "native": native, "receipt_linkage": linkage,
            "resources": _resources(_dict(run.get("command")).get("resources")),
            "command": run.get("command"), "strict_command": run.get("strict_command"),
            "evidence_integrity": {"status": "invalid" if evidence.errors else "hashed" if evidence.hashed else "unanchored",
                                   "hashed_files": evidence.hashed, "errors": evidence.errors},
            "outside_timed_seconds": {key: run.get(key) for key in ("preparation_seconds", "snapshot_seconds", "qa_seconds")}}


def _pair(round_number, grouped):
    issues, roles = [], {}
    for role in ROLES:
        matches = grouped.get((round_number, role), [])
        if len(matches) != 1:
            issues.append("{} has {} observations, expected exactly one".format(role, len(matches)))
        roles[role] = matches[0] if len(matches) == 1 else {}
    baseline, candidate = roles["baseline"], roles["candidate"]
    old, new = baseline.get("elapsed_seconds"), candidate.get("elapsed_seconds")
    ratio = new / old if _number(old, True) and _number(new, True) else None
    if ratio is not None and not math.isfinite(ratio):
        ratio = None
    equality = {kind: _equal([_dict(row.get("media")).get(kind + "_digest") for row in (baseline, candidate)])
                for kind in ("video", "audio", "audio_timeline")}
    if any(value != "equal" for value in equality.values()):
        issues.append("paired decoded media/timing is different or unknown")
    if baseline.get("correctness") != "passed" or candidate.get("correctness") != "passed":
        issues.append("paired run correctness failed or is unknown")
    if ratio is None or ratio <= 0:
        issues.append("paired elapsed ratio is unavailable")
    gain = old / new - 1 if ratio is not None and ratio > 0 else None
    if gain is not None and not math.isfinite(gain):
        gain = None
        issues.append("paired throughput gain is nonfinite")
    reduction = (1 - ratio) * 100 if ratio is not None else None
    gain_percent = gain * 100 if gain is not None else None
    if any(value is not None and not math.isfinite(value) for value in (reduction, gain_percent)):
        issues.append("derived paired percentage is nonfinite")
        reduction = gain_percent = None
    return {"round": round_number, "baseline_seconds": old, "candidate_seconds": new,
            "baseline_run_index": baseline.get("index"), "candidate_run_index": candidate.get("index"),
            "candidate_over_baseline": ratio, "elapsed_reduction_percent": reduction,
            "throughput_gain_percent": gain_percent,
            "decoded_comparison": equality, "correctness": "passed" if not issues else "failed_or_unknown",
            "complete": not issues, "issues": issues}


def build_report(record, base_dir):
    """Return JSON-serializable observations and qualification, without execution."""
    record = _dict(record)
    plan = _dict(record.get("plan"))
    issues = []
    if record.get("schema", record.get("schema_version")) != RUN_SCHEMA:
        issues.append("unsupported run schema")
    if record.get("status") != "passed":
        issues.append("benchmark is not terminal passed")
    if record.get("original_input_valid") is not True:
        issues.append("original input stability is unverified or changed")
    if record.get("errors") != []:
        issues.append("benchmark has errors or lacks an explicit empty error list")
    workload = _dict(plan.get("workload"))
    for name, expected in (("duration_frames", 900), ("fps", 30), ("width", 1920), ("height", 1080), ("seconds", 30)):
        if type(workload.get(name)) not in (int, float) or workload[name] != expected:
            issues.append("workload " + name + " does not match the original 900-frame HTML scope")
    if type(workload.get("html_clips")) is not int or workload["html_clips"] < 1 or workload.get("timeline") != "timeline.json":
        issues.append("workload HTML/timeline identity is incomplete")
    raw_runs = record.get("runs")
    if not isinstance(raw_runs, list) or not raw_runs or any(not isinstance(r, dict) for r in raw_runs):
        issues.append("runs must be a nonempty list of objects")
    raw_runs = [r for r in raw_runs if isinstance(r, dict)] if isinstance(raw_runs, list) else []
    rounds = plan.get("rounds")
    if type(rounds) is not int or not 1 <= rounds <= 10000:
        issues.append("planned round count is missing or invalid")
        rounds = 0
    identity = _identity(plan, raw_runs)
    issues.extend(identity["issues"])
    runs = [_analyze_run(run, index, base_dir, plan) for index, run in enumerate(raw_runs, 1)]
    seen_executions = set()
    for run in runs:
        for field in ("events", "strict_events"):
            execution_id = run[field].get("run_id")
            if execution_id and execution_id in seen_executions:
                run["issues"].append("execution ID reused by another recorded invocation")
                run["correctness"] = "failed_or_unknown"
            if execution_id:
                seen_executions.add(execution_id)
        if run["role"] not in ROLES or run["cache_state"] not in CACHE_STATES:
            issues.append("run {} has invalid role/cache state".format(run["index"]))
        if type(run["round"]) is not int or not 1 <= run["round"] <= rounds:
            issues.append("run {} has unexpected round identity".format(run["index"]))
        issues.extend("run {}: {}".format(run["index"], issue) for issue in run["issues"])
    cross_cache = []
    for round_number in range(1, rounds + 1):
        for role in ROLES:
            selected = [r for r in runs if r["round"] == round_number and r["role"] == role]
            row = {"round": round_number, "role": role}
            for kind in ("video", "audio", "audio_timeline"):
                row[kind] = _equal([_dict(r["media"]).get(kind + "_digest") for r in selected]) if (
                    len(selected) == 2 and sorted(str(r["cache_state"]) for r in selected) == sorted(CACHE_STATES)) else "unknown"
            cross_cache.append(row)
    repeated_media = {kind: _equal([_dict(run["media"]).get(kind + "_digest") for run in runs])
                      for kind in ("video", "audio", "audio_timeline")}
    if any(v != "equal" for v in repeated_media.values()):
        issues.append("decoded media across roles/rounds/cache states is different or unknown")
    if any(row[kind] != "equal" for row in cross_cache for kind in ("video", "audio", "audio_timeline")):
        issues.append("cold/warm decoded media/timing comparison is different or unknown")
    cache_states = {}
    for state in CACHE_STATES:
        state_runs = [r for r in runs if r["cache_state"] == state]
        grouped = {}
        for run in state_runs:
            if type(run["round"]) is int and run["role"] in ROLES:
                grouped.setdefault((run["round"], run["role"]), []).append(run)
        observed_rounds = {r["round"] for r in state_runs if type(r["round"]) is int}
        pairs = [_pair(n, grouped) for n in sorted(set(range(1, rounds + 1)) | observed_rounds)]
        complete = sum(pair["complete"] for pair in pairs)
        blockers = list(issues)
        if complete < 3:
            blockers.append("fewer than three complete pairs")
        if len(state_runs) != rounds * 2 or not pairs or any(not p["complete"] for p in pairs):
            blockers.append("planned pairs are incomplete, duplicated or invalid")
        stats = _stats([p["candidate_over_baseline"] for p in pairs if _number(p["candidate_over_baseline"], True)])
        qualified = not blockers
        cache_states[state] = {
            "pairs": pairs, "complete_pairs": complete, "planned_pairs": rounds,
            "baseline_seconds": _stats([r["elapsed_seconds"] for r in state_runs if r["role"] == "baseline"]),
            "candidate_seconds": _stats([r["elapsed_seconds"] for r in state_runs if r["role"] == "candidate"]),
            "paired_ratio": stats,
            "elapsed_reduction_percent": (1 - stats["median"]) * 100 if stats["median"] is not None else None,
            "throughput_gain_percent": _stats([p["throughput_gain_percent"] for p in pairs if p["throughput_gain_percent"] is not None])["median"],
            "qualified": qualified, "goal_met": bool(qualified and stats["median"] is not None and stats["median"] <= .5),
            "issues": list(dict.fromkeys(blockers))}
    stage_summary, resource_summary = {}, {}
    for state in CACHE_STATES:
        stage_summary[state], resource_summary[state] = {}, {}
        for role in ROLES:
            selected = [r for r in runs if r["cache_state"] == state and r["role"] == role]
            names = sorted({name for r in selected for name in r["events"]["stages"]})
            stage_summary[state][role] = {name: _stats([r["events"]["stages"].get(name, {}).get("union_seconds") for r in selected]) for name in names}
            stage_summary[state][role]["wave_union_seconds"] = _stats([r["events"]["wave_union_seconds"] for r in selected])
            resource_summary[state][role] = {name: _stats([r["resources"].get(name) for r in selected])
                                           for name in ("peak_tree_rss_bytes", "cpu_seconds_lower_bound", "peak_sampled_processes")}
    candidates = [(metrics["median"], stage) for stage, metrics in stage_summary["cold"]["candidate"].items()
                  if stage != "wave_union_seconds" and metrics["median"] is not None]
    if candidates:
        duration, stage = max(candidates)
        next_bottleneck = "Inspect {} first (candidate cold median observed stage union {:.3f}s); overlapping stages are not additive or a causal critical-path proof.".format(stage, duration)
    else:
        next_bottleneck = "Unknown: collect complete candidate cold execution events before attributing a bottleneck."
    execution_valid = not issues and all(
        len(value["pairs"]) == rounds and value["complete_pairs"] == rounds and rounds > 0
        for value in cache_states.values())
    return _safe({"schema": SCHEMA, "status": record.get("status", "unknown"),
                  "execution_valid": execution_valid,
                  "workload": plan.get("workload"), "identity": identity, "runs": runs,
                  "cache_states": cache_states, "qualified": all(v["qualified"] for v in cache_states.values()),
                  "goal_met": all(v["qualified"] for v in cache_states.values()) and cache_states["cold"]["goal_met"],
                  "goal": {"scope": "cold full all-command elapsed time",
                           "warm_policy": "diagnostic performance only; complete planned matrix and all correctness still required",
                           "candidate_over_baseline_lte": .5, "minimum_complete_pairs_per_cache_state": 3,
                           "statistic": "median of all paired elapsed ratios; cold and warm remain separate"},
                  "correctness": {"decoded_comparison": repeated_media, "cross_cache": cross_cache,
                                  "strict_qc": "passed" if runs and all(r["qc"]["status"] == "passed" for r in runs) else "failed_or_unknown",
                                  "visual_content": "unknown"},
                  "stages": stage_summary, "resources": resource_summary,
                  "issues": list(dict.fromkeys(issues)), "limits": LIMITS,
                  "next_bottleneck": next_bottleneck})


def _fmt(value, digits=3):
    return ("{:." + str(digits) + "f}").format(value) if type(value) in (int, float) and math.isfinite(value) else "unknown"


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _range(stats):
    return "{} [{}–{}] n={}".format(_fmt(stats.get("median")), _fmt(stats.get("min")), _fmt(stats.get("max")), stats.get("count", 0))


def render_markdown(summary):
    """Concise human report; raw per-run evidence remains in the JSON summary."""
    identity = _dict(summary.get("identity"))
    lines = ["# CEF end-to-end performance report", "",
             "**Cold full-task 50% elapsed-time goal: {}.** Qualification: {}.".format(
                 "MET" if summary.get("goal_met") else "NOT ESTABLISHED" if not summary.get("qualified") else "NOT MET",
                 "passed" if summary.get("qualified") else "failed or incomplete"), "",
             "## Identity and scope", "",
             "- Input SHA256: `{}`.".format(_cell(identity.get("input_digest", "unknown"))),
             "- Comparison: **{}**; identity: {}.".format(identity.get("comparison_kind", "unknown"), identity.get("status", "unknown"))]
    for role in ROLES:
        source = _dict(identity.get(role))
        lines.append("- {}: commit `{}`, tree `{}`, worker SHA256 `{}`.".format(role, _cell(source.get("head", "unknown")), _cell(source.get("tree", "unknown")), _cell(_dict(source.get("worker")).get("sha256", "unknown"))))
    platform = _dict(identity.get("platform"))
    lines.append("- Host: {} / {} / {}; Python {}.".format(*[_cell(platform.get(key, "unknown")) for key in ("system", "release", "machine", "python")]))
    differences = _dict(identity.get("candidate_setting_differences"))
    for key, values in sorted(differences.items()):
        lines.append("- Declared setting change `{}`: `{}` → `{}`; **not a same-configuration code-only claim**.".format(_cell(key), _cell(values.get("baseline")), _cell(values.get("candidate"))))
    lines += ["", "## Paired elapsed time", "", "The primary goal applies to cold full-task wall time. Warm performance is diagnostic, not required to halve; every planned cold/warm observation must still pass correctness. A warm regression remains visible below.", "", "Medians [min–max]; all recorded valid timings retained, including failures. Invalid timings remain in raw JSON. No best-round selection.", "",
              "| Cache | Baseline s | Candidate s | Paired candidate/baseline | Elapsed reduction | Throughput gain | Complete pairs | Goal |",
              "|---|---:|---:|---:|---:|---:|---:|---|"]
    for state in CACHE_STATES:
        result = summary["cache_states"][state]
        lines.append("| {} | {} | {} | {} | {}% | {}% | {}/{} | {} |".format(
            state, _range(result["baseline_seconds"]), _range(result["candidate_seconds"]), _range(result["paired_ratio"]),
            _fmt(result["elapsed_reduction_percent"], 1), _fmt(result["throughput_gain_percent"], 1), result["complete_pairs"], result["planned_pairs"],
            ("met" if result["goal_met"] else "not met" if result["qualified"] else "unqualified") if state == "cold" else "diagnostic only"))
    lines += ["", "Elapsed reduction = (1 − candidate/baseline) × 100%; throughput gain = (baseline/candidate − 1) × 100%. A 50% elapsed reduction is a 100% throughput gain.", "",
              "| Cache / round | Baseline s | Candidate s | Ratio | Decoded video/audio | Correctness |", "|---|---:|---:|---:|---|---|"]
    for state in CACHE_STATES:
        for pair in summary["cache_states"][state]["pairs"]:
            media = pair["decoded_comparison"]
            lines.append("| {} / {} | {} | {} | {} | {} / {} | {} |".format(state, pair["round"], _fmt(pair["baseline_seconds"]), _fmt(pair["candidate_seconds"]), _fmt(pair["candidate_over_baseline"]), media["video"], media["audio"], pair["correctness"]))
    correctness = summary["correctness"]
    lines += ["", "## Correctness", "", "- Strict QC: **{}**; visual content: **unknown** (not visual perfection).".format(correctness["strict_qc"]),
              "- Decoded video/audio across roles, rounds and cold/warm: **{} / {}**.".format(correctness["decoded_comparison"]["video"], correctness["decoded_comparison"]["audio"])]
    blockers = list(summary.get("issues", []))
    for state in CACHE_STATES:
        blockers.extend("{}: {}".format(state, issue) for issue in summary["cache_states"][state]["issues"] if issue not in summary.get("issues", []))
    if blockers:
        lines.append("- Qualification blockers:")
        lines.extend("  - " + _cell(issue) for issue in dict.fromkeys(blockers))
    lines += ["", "## Stages and resources", "", "Stage values are median interval unions, not additive wall-time shares. Warm-cache stages are reported separately.", "",
              "| Cache / role | Observed stage seconds | Sampled tree RSS MiB | CPU lower-bound s |", "|---|---|---:|---:|"]
    for state in CACHE_STATES:
        for role in ROLES:
            stages = summary["stages"][state][role]
            values = "; ".join("{} {}".format(name, _fmt(value["median"])) for name, value in stages.items() if value["median"] is not None) or "unknown"
            resources = summary["resources"][state][role]
            rss = resources["peak_tree_rss_bytes"]["median"]
            lines.append("| {} / {} | {} | {} | {} |".format(state, role, values, _fmt(rss / 1048576 if rss is not None else None, 1), _fmt(resources["cpu_seconds_lower_bound"]["median"])))
    lines += ["", "Native metrics (per-frame adapter and per-block host median/min/max/p90/p95) are in JSON; missing older instrumentation is unknown, not zero.", "", "## Next bottleneck", "", summary["next_bottleneck"], "", "## Limits", ""]
    lines.extend("- " + limit for limit in summary.get("limits", []))
    return "\n".join(lines) + "\n"
