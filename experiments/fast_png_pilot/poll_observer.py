"""Bounded, opt-in observations of the frozen renderer's navigation polls.

This module never drives navigation, receives CDP messages, or intercepts sleep.
All timestamps are CLOCK_MONOTONIC seconds in the observing process.
"""
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from types import CodeType

POLL_EXPRESSION = "location.search+'|'+document.readyState"
CLOCK_KIND = "time.clock_gettime(time.CLOCK_MONOTONIC)"
LIMITS = {"navigation_records": 1024, "poll_records": 262144,
          "evidence_bytes": 64 * 1024 * 1024, "url_characters": 16384,
          "error_records": 16, "context_rows": 300}
# Conservative reservations bound retained data before page-end JSON encoding.
# A poll has only fixed keys, finite numbers, short enums, and a planned branch.
_POLL_BYTES = 4096
_NAV_BYTES = 4096
_METADATA_BYTES = 512 * 1024
_UNKNOWN_SLEEP = "no sleep boundary instrumented; gap is not actual sleep"


def clock_now():
    value = time.clock_gettime(time.CLOCK_MONOTONIC)
    if not math.isfinite(value):
        raise ValueError("non-finite observation clock")
    return value


def disabled_observation():
    """Explicit off-arm descriptor; calling it is outside the render hot path."""
    return {"schema": "navigation-poll-observation/1", "status": "DISABLED",
            "qualification": "DISABLED", "reason": "poll_observation=False",
            "persistence": "NOT_REQUESTED", "path": None, "bytes": None,
            "sha256": None, "navigation_count": None, "poll_count": None,
            "planned_sleep_seconds": None, "inter_poll_gap_seconds": None,
            "actual_sleep_seconds": None}


def _codes(code):
    yield code
    for constant in code.co_consts:
        if isinstance(constant, CodeType):
            yield from _codes(constant)


def _context(value):
    """Copy only small, known scalar metadata; never mutate a runner's job."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("job_context must be a dictionary or None")
    entry = value.get("entry") or {}
    if not isinstance(entry, dict):
        raise ValueError("job_context.entry must be a dictionary")
    result = {}
    for key in ("job_id", "batch", "arm", "run_id", "attempt"):
        item = value.get(key)
        if item is not None and (type(item) not in (str, int) or len(str(item)) > 1024):
            raise ValueError("job_context scalar exceeds bound: " + key)
        result[key] = item
    result["entry"] = {}
    for key in ("entryId", "src", "windowStart"):
        item = entry.get(key)
        if item is not None and (type(item) not in (str, int) or len(str(item)) > 1024):
            raise ValueError("job_context entry scalar exceeds bound: " + key)
        result["entry"][key] = item
    rows = value.get("mapping", [])
    if not isinstance(rows, (list, tuple)) or len(rows) > LIMITS["context_rows"]:
        raise ValueError("job_context mapping exceeds fixed page bound")
    mapping, globals_seen = {}, set()
    for row in rows:
        if not isinstance(row, dict) or any(type(row.get(k)) is not int for k in ("global", "local")):
            raise ValueError("job_context mapping requires integer global/local")
        g, local = row["global"], row["local"]
        if not 0 <= g <= 3600 or not 0 <= local <= 3600 or local in mapping or g in globals_seen:
            raise ValueError("job_context mapping must be bounded and unique")
        start = entry.get("windowStart")
        if type(start) is not int or g != local + start:
            raise ValueError("job_context global/local/windowStart mismatch")
        mapping[local] = g
        globals_seen.add(g)
    result["mapping"] = [{"global": g, "local": local} for local, g in mapping.items()]
    return result, mapping


class PollObserver:
    def __init__(self, gfx, directory, job_context):
        self.directory = Path(directory)
        self.context, self.mapping = _context(job_context)
        self.navigations = []
        self.polls = []
        self.errors = []
        self.counts = dict(navigation=0, poll=0, failed_navigation=0, failed_poll=0,
                           unmatched=0, unparseable=0, dropped_navigation=0,
                           dropped_poll=0, observation_error=0, unknown_navigation=0)
        self.overflow = False
        self.overflow_reasons = set()
        self.reserved_bytes = _METADATA_BYTES
        self.descriptor = {"schema": "navigation-poll-observation/1", "status": "NOT_STARTED",
                           "qualification": "UNKNOWN", "persistence": "MISSING",
                           "path": None, "bytes": None, "sha256": None}
        self.caller_codes = {}
        source = Path(gfx.__file__).read_bytes()
        compiled = compile(source, gfx.__file__, "exec", dont_inherit=True,
                           optimize=sys.flags.optimize)
        expected = [c for c in _codes(compiled) if c.co_name == "render_page"]
        actual = getattr(gfx.render_page, "__code__", None)
        self.caller_binding = bool(len(expected) == 1 and actual == expected[0]
                                   and gfx.render_page.__globals__ is vars(gfx))
        if self.caller_binding:
            self.caller_codes["render_page"] = actual
            nested = [c for c in _codes(actual) if c.co_name == "do_frame"]
            if len(nested) == 1:
                self.caller_codes["do_frame"] = nested[0]
            else:
                self.caller_binding = False
        self.source_sha256 = hashlib.sha256(source).hexdigest()
        self.restoration = {"installed_references_intact": None, "original_references_restored": None}

    def error(self, where, error):
        self.counts["observation_error"] += 1
        if len(self.errors) < LIMITS["error_records"]:
            # Only type/location, not arbitrary exception text or response objects.
            self.errors.append({"where": where[:128], "type": type(error).__name__[:128]})

    def safely(self, method, *args):
        """Instrumentation faults cannot replace a renderer's primary exception."""
        try:
            return getattr(self, method)(*args)
        except BaseException as error:
            self.error(method, error)
            return None

    def _reserve(self, kind, amount):
        limit_key = "navigation_records" if kind == "navigation" else "poll_records"
        retained = len(self.navigations) if kind == "navigation" else len(self.polls)
        if retained >= LIMITS[limit_key]:
            self.overflow_reasons.add(limit_key)
        if self.reserved_bytes + amount > LIMITS["evidence_bytes"]:
            self.overflow_reasons.add("evidence_bytes_reservation")
        if self.overflow_reasons:
            self.overflow = True
            self.counts["dropped_" + kind] += 1
            return False
        self.reserved_bytes += amount
        return True

    def bind_tabs(self, tabs, generation):
        for index, tab in enumerate(tabs):
            tab._poll_generation = generation if type(generation) is int else None
            tab._poll_tab_index = index

    def nav_enter(self, tab, url, caller):
        entered = clock_now()
        self.counts["navigation"] += 1
        role, local = "unknown", None
        line = caller.f_lineno
        code = caller.f_code
        # Both exact loaded code object identity and pinned source line are needed.
        if self.caller_binding and code is self.caller_codes.get("render_page"):
            if line == 999:
                role = "initial"
                work = caller.f_locals.get("work")
                local = work[0] if isinstance(work, list) and work else None
            elif line == 1060:
                role, local = "flatten_probe", caller.f_locals.get("pf")
        elif self.caller_binding and code is self.caller_codes.get("do_frame") and line == 857:
            role, local = "target", caller.f_locals.get("i")
        base_uri = caller.f_locals.get("base_uri") if role != "unknown" else None
        mapped = (type(local) is int and local in self.mapping and isinstance(base_uri, str)
                  and url == f"{base_uri}?f={local}")
        if not mapped:
            local = local if type(local) is int else None
        full_url = url if isinstance(url, str) and len(url) <= LIMITS["url_characters"] else None
        if full_url is None:
            self.overflow_reasons.add("url_characters_or_type")
        known = (role != "unknown" and mapped and self.context["entry"]["entryId"] is not None
                 and getattr(tab, "_poll_generation", None) is not None
                 and getattr(tab, "_poll_tab_index", None) is not None)
        if not known:
            self.counts["unknown_navigation"] += 1
        record = {"nav_id": self.counts["navigation"], "role": role,
                  "caller": {"name": code.co_name[:128], "line": line,
                             "source_code_verified": role != "unknown"},
                  "local": local, "global": self.mapping.get(local) if mapped else None,
                  "mapping_status": "BOUND" if mapped else "UNKNOWN", "url": full_url,
                  "browser_generation": getattr(tab, "_poll_generation", None),
                  "tab_index": getattr(tab, "_poll_tab_index", None),
                  "enter": entered, "exit": None, "status": "pending",
                  "error_type": None, "poll_count": 0, "previous_poll_end": None,
                  "unclosed_reason": "navigation has not exited"}
        record["retained"] = self._reserve("navigation", _NAV_BYTES + 6 * len(full_url or ""))
        if record["retained"]:
            self.navigations.append(record)
        return record

    def nav_exit(self, record, error):
        if record is None:
            return
        record["exit"] = clock_now()
        record["status"] = "failed" if error is not None else "returned"
        record["error_type"] = type(error).__name__[:128] if error is not None else None
        record["unclosed_reason"] = None
        if error is not None:
            self.counts["failed_navigation"] += 1

    def poll_enter(self, navigation):
        self.counts["poll"] += 1
        navigation["poll_count"] += 1
        record = {"nav_id": navigation["nav_id"], "sequence": navigation["poll_count"],
                  "call_start": None, "call_end": None, "wrapper_end": None,
                  "evaluate_call_wall_seconds": None, "inter_poll_gap_seconds": None,
                  "call_status": "pending", "wrapper_status": "pending", "matched": None,
                  "parse_status": "NOT_RETURNED", "error_type": None,
                  "planned_sleep": None, "unclosed_reason": "poll has not exited"}
        retained = self._reserve("poll", _POLL_BYTES) if navigation["retained"] else False
        if not navigation["retained"]:
            self.counts["dropped_poll"] += 1
        if retained:
            self.polls.append(record)
        return record

    def call_enter(self, record):
        if record is not None:
            # The last observation before delegating: record allocation and cap
            # accounting happen earlier, outside the underlying-call interval.
            record["call_start"] = clock_now()

    def call_exit(self, record, error):
        if record is None:
            return
        record["call_end"] = clock_now()
        if record["call_start"] is not None:
            record["evaluate_call_wall_seconds"] = record["call_end"] - record["call_start"]
        record["call_status"] = "failed" if error is not None else "returned"

    def poll_exit(self, navigation, record, result, error):
        if record is None:
            return
        record["wrapper_status"] = "failed" if error is not None else "returned"
        record["error_type"] = type(error).__name__[:128] if error is not None else None
        if error is not None:
            self.counts["failed_poll"] += 1
            record["parse_status"] = "CALL_OR_PROTOCOL_ERROR"
        else:
            # Same falsey coercion and partition as frozen Tab.navigate; no JSON
            # decode, copied response, additional evaluate, or response mutation.
            value = result.get("result", {}).get("value") or ""
            if isinstance(value, str) and navigation["url"] is not None:
                search, _, ready = value.partition("|")
                url = navigation["url"]
                want = "?" + url.split("?", 1)[1] if "?" in url else ""
                record["matched"] = search == want and ready == "complete"
                record["parse_status"] = "PARSED"
                if not record["matched"]:
                    self.counts["unmatched"] += 1
                    record["planned_sleep"] = {
                        "after_poll_sequence": record["sequence"], "requested_seconds": 0.02,
                        "basis": "frozen-source-branch:gfx_render.py:354-360",
                        "boundary": "after unmatched return; before next loop check",
                        "actual_enter": None, "actual_exit": None,
                        "reason": _UNKNOWN_SLEEP}
            else:
                record["parse_status"] = "UNPARSEABLE"
                self.counts["unparseable"] += 1
        previous = navigation["previous_poll_end"]
        if record["call_start"] is not None and previous is not None:
            record["inter_poll_gap_seconds"] = record["call_start"] - previous
        record["wrapper_end"] = clock_now()
        record["unclosed_reason"] = None
        navigation["previous_poll_end"] = record["call_end"]

    def status(self, trace, primary_error):
        if primary_error is not None or trace.errors or trace.recovery_attempts or self.errors:
            return "FAILED"
        if self.overflow or any(n["exit"] is None for n in self.navigations) or any(
                p["wrapper_end"] is None for p in self.polls):
            return "INCOMPLETE"
        if not self.counts["navigation"]:
            return "UNKNOWN" if self.mapping else "NOT_STARTED"
        if (not self.counts["poll"] or self.counts["unknown_navigation"] or self.counts["unparseable"]
                or any(not n["poll_count"] for n in self.navigations)
                or not self.caller_binding or not all(self.restoration.values())):
            return "UNKNOWN"
        return "COMPLETE"

    def persist(self, trace, primary_error):
        """One page-end sidecar write. Missing persistence never qualifies."""
        status = self.status(trace, primary_error)
        complete = status == "COMPLETE"
        report = {"schema": "navigation-poll-observation/1", "status": status,
                  "qualification": "COMPLETE" if complete else "INELIGIBLE",
                  "scope": {"kind": "one-capture-scope/render-page", "id": str(self.directory),
                            "job_context": self.context},
                  "clock_kind": CLOCK_KIND, "unit": "seconds", "limits": dict(LIMITS),
                  "renderer_sha256": self.source_sha256,
                  "caller_source_code_binding": self.caller_binding,
                  "restoration": self.restoration, "counts": dict(self.counts),
                  "overflow": self.overflow, "overflow_reasons": sorted(self.overflow_reasons),
                  "incomplete": self.overflow or status in ("INCOMPLETE", "UNKNOWN", "NOT_STARTED"),
                  "observation_errors": self.errors,
                  "primary_error_type": type(primary_error).__name__[:128] if primary_error is not None else None,
                  "original_trace_error_count": len(trace.errors),
                  "recovery_attempts": trace.recovery_attempts,
                  "browser_start_attempts": trace.browser_start_attempts,
                  "navigation_count": self.counts["navigation"], "poll_count": self.counts["poll"],
                  "planned_sleep_seconds": self.counts["unmatched"] * 0.02 if complete else None,
                  "planned_sleep_reason": "frozen branch count; not measured sleep" if complete else "incomplete or failed scope",
                  "actual_sleep_seconds": None, "actual_sleep_enter": None, "actual_sleep_exit": None,
                  "actual_sleep_reason": _UNKNOWN_SLEEP, "browser_ready_time": None,
                  "browser_ready_time_reason": "readiness sampled only at evaluate responses",
                  "recoverable_wait_seconds": None,
                  "recoverable_wait_reason": "browser work overlaps gaps; no counterfactual measurement",
                  "evaluate_call_wall_definition": "original Tab.call only; excludes check_response and observer bookkeeping",
                  "wrapper_end_definition": "clock sampled at observer tail after check_response; excludes final assignment/return",
                  "inter_poll_gap_definition": "next call_start minus prior call_end in same nav; includes validation, observer, branch, sleep, scheduling",
                  "excluded_delays": "initial 0.15s and recovery 0.5s are not polls",
                  "navigations": self.navigations, "polls": self.polls,
                  "performance_claim": "none; no pooling or actual sleep inference"}
        try:
            data = (json.dumps(report, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
            if len(data) > LIMITS["evidence_bytes"]:
                self.overflow = True
                self.overflow_reasons.add("serialized_evidence_bytes")
                # Defensive second bound. Retain in-memory samples, never emit an
                # over-limit file or claim that the unpersisted samples exist.
                raise ValueError("serialized observation exceeds evidence byte cap")
            path = self.directory / "poll-observation.json"
            with path.open("xb") as stream:
                stream.write(data)
            self.descriptor = {"schema": report["schema"], "status": status,
                               "qualification": report["qualification"], "persistence": "WRITTEN",
                               "path": str(path), "bytes": len(data),
                               "sha256": hashlib.sha256(data).hexdigest(),
                               "navigation_count": report["navigation_count"], "poll_count": report["poll_count"],
                               "planned_sleep_seconds": report["planned_sleep_seconds"],
                               "actual_sleep_seconds": None}
        except BaseException as error:
            self.error("persist", error)
            self.descriptor.update(status="FAILED", qualification="INELIGIBLE", persistence="MISSING",
                                   path=None, bytes=None, sha256=None,
                                   observation_errors=list(self.errors), primary_error_type=report["primary_error_type"],
                                   overflow=self.overflow, incomplete=True,
                                   overflow_reasons=sorted(self.overflow_reasons), counts=dict(self.counts))
        return self.descriptor
