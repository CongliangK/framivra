"""Offline, one-attempt CDP navigation model. No transport or renderer imports.

Actions are descriptions, never executed here. LEGACY_CHECK_PASSED records only
the old query/readyState predicate; it grants no screenshot/paint qualification.
"""

from dataclasses import dataclass
from enum import Enum
import math


LEGACY_EXPRESSION = "location.search+'|'+document.readyState"


class State(str, Enum):
    NEW = "new"
    WAIT_NAVIGATE = "wait_navigate"
    WAIT_EVENT = "wait_event"
    WAIT_STRICT = "wait_strict"
    CHECK_PENDING = "check_pending"
    LEGACY_CHECK_PASSED = "legacy_check_passed"
    FALLBACK = "fallback_required"
    FAILED = "failed"


TERMINAL = {State.LEGACY_CHECK_PASSED, State.FALLBACK, State.FAILED}


@dataclass(frozen=True)
class Action:
    kind: str
    message: dict | None = None
    reason: str = ""
    remaining: float | None = None


@dataclass
class Evidence:
    url: str | None = None
    loaded: bool = False
    conflicting_url: bool = False
    invalidated: bool = False


def _text(value, limit):
    return isinstance(value, str) and 0 < len(value) <= limit


def _clock(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


class NavigationWait:
    """Single-use model for one frame, one target, one browser generation.

    The owner supplies decoded messages and monotonic time, plus strict policy
    completion receipts. Neither the connection nor Fetch policy is implemented.
    IDs must be reserved by the owner, distinct from its policy command IDs.
    """

    def __init__(self, *, generation, target, frame, url, navigate_id, check_id,
                 timeout=5.0, event_wait=0.25, max_records=16,
                 max_pending_fetch=256, lifecycle_enabled=True, strict=False):
        if type(generation) is not int or generation < 0:
            raise ValueError("generation must be a nonnegative integer")
        if not _text(target, 1024) or not _text(frame, 1024):
            raise ValueError("known target and main-frame IDs are required")
        if not _text(url, 8192):
            raise ValueError("a bounded, nonempty target URL is required")
        if (type(navigate_id) is not int or type(check_id) is not int
                or min(navigate_id, check_id) < 1 or navigate_id == check_id):
            raise ValueError("two distinct positive reserved command IDs required")
        if not _clock(timeout) or timeout == 0 or not _clock(event_wait):
            raise ValueError("finite positive timeout and nonnegative event_wait required")
        if (type(max_records) is not int or not 1 <= max_records <= 256
                or type(max_pending_fetch) is not int
                or not 1 <= max_pending_fetch <= 256):
            raise ValueError("bounded positive record and Fetch capacities required")
        if type(lifecycle_enabled) is not bool or type(strict) is not bool:
            raise ValueError("capability and strict mode must be explicit booleans")
        self.generation, self.target, self.frame, self.url = generation, target, frame, url
        self.navigate_id, self.check_id = navigate_id, check_id
        self.timeout, self.event_wait = timeout, event_wait
        self.max_records, self.max_pending_fetch = max_records, max_pending_fetch
        self.lifecycle_enabled, self.strict = lifecycle_enabled, strict
        self.state = State.NEW
        self.reason = ""
        self.deadline = self.last_now = self.event_deadline = None
        self.loader = None
        self.document_loader = None
        self.early_same_document = False
        self.records = {}
        self.pending_fetch = set()
        self.check_value = None

    def start(self, now):
        if self.state != State.NEW:
            raise ValueError("one navigation per instance; start cannot be repeated")
        if not _clock(now) or not math.isfinite(now + self.timeout):
            raise ValueError("finite monotonic start time required")
        self.last_now, self.deadline = now, now + self.timeout
        self.state = State.WAIT_NAVIGATE
        return [Action("send", {"id": self.navigate_id, "method": "Page.navigate",
                               "params": {"url": self.url}}, remaining=self.timeout)]

    def remaining(self, now):
        return max(0.0, self.deadline - now)

    def _finish(self, state, reason, now):
        self.state, self.reason = state, reason
        self.records.clear()
        self.document_loader = None
        # Preserve outstanding Fetch IDs for the owner even when we stop waiting.
        return [Action(state.value, reason=reason, remaining=self.remaining(now))]

    def tick(self, now):
        if self.state == State.NEW:
            raise ValueError("start must precede input")
        if self.state in TERMINAL:
            return []
        if not _clock(now) or now < self.last_now:
            return self._finish(State.FAILED, "invalid_monotonic_clock", self.last_now)
        self.last_now = now
        if now >= self.deadline:
            return self._finish(State.FAILED, "deadline_expired", now)
        if (self.state == State.WAIT_EVENT and now >= self.event_deadline
                and not self._matched()):
            return self._finish(State.FALLBACK, "matching_event_missing", now)
        return []

    def _matched(self):
        evidence = self.records.get(self.loader)
        return (evidence is not None and evidence.loaded
                and evidence.url == self.url and not evidence.conflicting_url
                and not evidence.invalidated)

    def _progress(self, now):
        if self.state in TERMINAL or self.loader is None:
            return []
        if self.check_value is not None:
            if not self.pending_fetch:
                return self._finish(State.LEGACY_CHECK_PASSED,
                                    "query_and_ready_state_only", now)
            return []
        if self.state == State.CHECK_PENDING or not self._matched():
            return []
        if self.pending_fetch:
            self.state = State.WAIT_STRICT
            return []
        self.state = State.CHECK_PENDING
        return [Action("send", {"id": self.check_id, "method": "Runtime.evaluate",
                               "params": {"expression": LEGACY_EXPRESSION,
                                          "returnByValue": True}},
                       remaining=self.remaining(now))]

    def receive(self, message, *, generation, target, now):
        """Return actions; unowned messages are returned intact, never discarded.

        Envelope identity comes from the owner, not from CDP payload fields.
        Fetch messages are always forwarded before any timeout/failure action.
        """
        if self.state == State.NEW:
            raise ValueError("start must precede input")
        passthrough = [Action("forward", message)]
        actions = self.tick(now)
        if self.state in TERMINAL:
            return passthrough + actions
        if type(generation) is not int or generation != self.generation or target != self.target:
            return passthrough + actions
        if not isinstance(message, dict):
            return passthrough + self._finish(State.FAILED, "malformed_message", self.last_now)
        forwarded = message.get("method") == "Fetch.requestPaused"
        actions = passthrough if forwarded else []
        if forwarded:
            if self.strict:
                params = message.get("params", {})
                request_id = params.get("requestId") if isinstance(params, dict) else None
                if not _text(request_id, 1024):
                    return actions + self._finish(State.FAILED, "malformed_fetch_pause", now)
                if request_id in self.pending_fetch:
                    return actions + self._finish(State.FAILED, "duplicate_fetch_pause", now)
                if len(self.pending_fetch) >= self.max_pending_fetch:
                    return actions + self._finish(State.FAILED, "fetch_queue_full", now)
                self.pending_fetch.add(request_id)
            return actions
        mid = message.get("id")
        if type(mid) is int and mid == self.navigate_id:
            if self.state != State.WAIT_NAVIGATE:
                return self._finish(State.FAILED, "duplicate_navigation_response", now)
            return self._navigate_response(message, now)
        if type(mid) is int and mid == self.check_id:
            if self.state != State.CHECK_PENDING:
                return self._finish(State.FAILED, "unsolicited_check_response", now)
            return self._check_response(message, now)
        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(params, dict):
            return passthrough + self._finish(State.FAILED, "malformed_event_params", now)
        if method in {"Runtime.executionContextDestroyed", "Runtime.executionContextsCleared"}:
            if self.state == State.CHECK_PENDING:
                return passthrough + self._finish(State.FALLBACK, "context_changed_during_check", now)
            # No context-to-frame map is modeled. Conservatively invalidate all
            # evidence already collected; later duplicates cannot repair it.
            for evidence in self.records.values():
                evidence.invalidated = True
            if self.loader in self.records:
                return passthrough + self._finish(State.FALLBACK, "context_changed_after_evidence", now)
            return passthrough
        if method == "Page.navigatedWithinDocument" and params.get("frameId") == self.frame:
            if self.loader is not None:
                return passthrough + self._finish(State.FALLBACK, "same_document_event", now)
            self.early_same_document = True
            return passthrough
        if method == "Page.frameDetached" and params.get("frameId") == self.frame:
            return passthrough + self._finish(State.FAILED, "main_frame_detached", now)
        if not self.lifecycle_enabled:
            return passthrough
        if method == "Page.frameNavigated":
            frame = params.get("frame", {})
            if not isinstance(frame, dict):
                return passthrough + self._finish(State.FAILED, "malformed_frame_event", now)
            if frame.get("id") != self.frame or frame.get("parentId"):
                return passthrough
            loader, url = frame.get("loaderId"), frame.get("url")
            if not _text(url, 8192):
                return passthrough + self._finish(State.FAILED, "malformed_frame_url", now)
            fragment = frame.get("urlFragment", "")
            if not isinstance(fragment, str) or len(url) + len(fragment) > 8192:
                return passthrough + self._finish(State.FAILED, "malformed_frame_url", now)
            url += fragment
            return passthrough + self._record(loader, url=url, now=now)
        if method == "Page.lifecycleEvent" and params.get("name") == "load":
            if params.get("frameId") != self.frame:
                return passthrough
            return passthrough + self._record(params.get("loaderId"), loaded=True, now=now)
        return passthrough  # Includes strict policy command replies; no dispatcher here.

    def _record(self, loader, *, now, url=None, loaded=False):
        if not _text(loader, 1024):
            return self._finish(State.FAILED, "malformed_loader_id", now)
        if self.loader is not None and loader != self.loader:
            if url is not None:
                return self._finish(State.FALLBACK, "different_document_after_response", now)
            return []  # Old loader's load cannot wake this navigation.
        if loader not in self.records:
            if len(self.records) >= self.max_records:
                return self._finish(State.FAILED, "event_queue_full", now)
            self.records[loader] = Evidence()
        evidence = self.records[loader]
        if url is not None:
            if self.document_loader is not None and self.document_loader != loader:
                self.records[self.document_loader].invalidated = True
            self.document_loader = loader
            evidence.conflicting_url |= evidence.url is not None and evidence.url != url
            evidence.url = url
        evidence.loaded |= loaded
        if self.loader is not None and evidence.invalidated:
            return self._finish(State.FALLBACK, "document_evidence_invalidated", now)
        if self.loader is not None and (evidence.conflicting_url
                                       or evidence.url not in (None, self.url)):
            return self._finish(State.FALLBACK, "document_url_mismatch", now)
        return self._progress(now)

    def _navigate_response(self, message, now):
        if "error" in message:
            return self._finish(State.FAILED, "navigate_protocol_error", now)
        result = message.get("result")
        if not isinstance(result, dict):
            return self._finish(State.FAILED, "malformed_navigate_result", now)
        if result.get("errorText"):
            return self._finish(State.FAILED, "navigate_error_text", now)
        if result.get("isDownload"):
            return self._finish(State.FAILED, "navigation_is_download", now)
        if result.get("frameId") != self.frame:
            return self._finish(State.FAILED, "navigation_frame_mismatch", now)
        loader = result.get("loaderId")
        if loader is None or loader == "":
            return self._finish(State.FALLBACK, "same_document_or_missing_loader", now)
        if not _text(loader, 1024):
            return self._finish(State.FAILED, "malformed_loader_id", now)
        if not self.lifecycle_enabled:
            return self._finish(State.FALLBACK, "lifecycle_not_enabled", now)
        if "#" in self.url:
            return self._finish(State.FALLBACK, "fragment_url_not_modeled", now)
        if self.early_same_document:
            return self._finish(State.FALLBACK, "same_document_before_response", now)
        self.loader = loader
        evidence = self.records.get(loader)
        if evidence is not None and evidence.invalidated:
            return self._finish(State.FALLBACK, "document_evidence_invalidated", now)
        self.records = {loader: evidence} if evidence is not None else {}
        self.document_loader = loader if evidence is not None and evidence.url is not None else None
        if evidence is not None and (evidence.conflicting_url
                                     or evidence.url not in (None, self.url)):
            return self._finish(State.FALLBACK, "document_url_mismatch", now)
        self.state = State.WAIT_EVENT
        self.event_deadline = min(self.deadline, now + self.event_wait)
        return self._progress(now) + self.tick(now)

    def _check_response(self, message, now):
        if "error" in message:
            return self._finish(State.FAILED, "check_protocol_error", now)
        result = message.get("result")
        if not isinstance(result, dict) or "exceptionDetails" in result:
            return self._finish(State.FAILED, "check_exception_or_malformed_result", now)
        remote = result.get("result")
        value = remote.get("value") if isinstance(remote, dict) else None
        if not isinstance(value, str) or remote.get("type") != "string":
            return self._finish(State.FAILED, "check_value_not_string", now)
        search, _, ready = value.partition("|")
        want = "?" + self.url.split("?", 1)[1] if "?" in self.url else ""
        if search != want or ready != "complete":
            return self._finish(State.FALLBACK, "legacy_predicate_not_satisfied", now)
        self.check_value = value
        return self._progress(now)

    def fetch_finished(self, request_id, *, generation, target, now, success):
        """Owner receipt AFTER existing policy command ACK and policy.check().

        This is not a network ACK handler or permission to continue a request.
        Unknown/stale receipts never release a pending policy operation.
        """
        actions = self.tick(now)
        if self.state in TERMINAL:
            return actions
        if type(generation) is not int or generation != self.generation or target != self.target:
            return actions
        if request_id not in self.pending_fetch:
            return self._finish(State.FAILED, "unmatched_fetch_receipt", now)
        if success is not True:
            return self._finish(State.FAILED, "strict_policy_failed", now)
        self.pending_fetch.remove(request_id)
        return self._progress(now)

    def disconnect(self, *, generation, target, now):
        actions = self.tick(now)
        if self.state in TERMINAL:
            return actions
        if type(generation) is not int or generation != self.generation or target != self.target:
            return actions
        return self._finish(State.FAILED, "connection_closed", now)
