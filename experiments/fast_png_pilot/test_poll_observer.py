"""Ordinary offline mocks only: no browser, Mac, installation, or timing claim."""
import base64
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from vtx import gfx_render as gfx
from . import adapter
from . import poll_observer as obs
from .test_adapter import PROTOCOL, VERSION, png, valid_stats


def context(rows=((18, 0), (20, 2))):
    return {"job_id": "offline-page", "run_id": "offline", "arm": "candidate", "attempt": 0,
            "batch": "continuous", "entry": {"entryId": "T08.01", "src": "page.html", "windowStart": 18},
            "mapping": [{"global": g, "local": local} for g, local in rows]}


class ScriptedTab:
    navigate = gfx.Tab.navigate
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []
    def call(self, method, timeout=30, **params):
        self.calls.append((method, timeout, params))
        if method == "Runtime.evaluate":
            result = next(self.replies)
            if isinstance(result, BaseException):
                raise result
            return result
        return {}


class ScriptedBrowser:
    def __init__(self, tab=None):
        self.tabs_n = 1
        self.generation = 4
        self.tab = tab
    def open_tabs(self):
        return [self.tab]
    def hard_restart(self):
        self.generation += 1


def response(value):
    return {"result": {"type": "string", "value": value}}


class PollObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def observer(self, job=None):
        return obs.PollObserver(gfx, self.root, context() if job is None else job)

    def pair(self, replies):
        trace = adapter.CaptureTrace("baseline", self.root)
        observer = self.observer()
        Tab, Browser = adapter._classes(trace, ScriptedTab, ScriptedBrowser, observer)
        tab = Tab(replies)
        browser = Browser(tab)
        browser.open_tabs()
        return trace, observer, tab, browser

    def test_one_ready_poll_original_requests_and_unknown_caller(self):
        trace, observer, tab, _ = self.pair([response("?f=0|complete")])
        tab.navigate("file:///page.html?f=0")
        self.assertEqual(tab.calls, [("Page.navigate", 10, {"url": "file:///page.html?f=0"}),
            ("Runtime.evaluate", 30, {"expression": obs.POLL_EXPRESSION, "returnByValue": True})])
        self.assertEqual(observer.counts["poll"], 1)
        poll = observer.polls[0]
        self.assertTrue(poll["matched"])
        self.assertIsNone(poll["planned_sleep"])
        self.assertEqual(observer.navigations[0]["role"], "unknown")
        self.assertEqual(observer.navigations[0]["browser_generation"], 4)
        self.assertEqual(observer.navigations[0]["tab_index"], 0)
        self.assertEqual(observer.navigations[0]["status"], "returned")
        self.assertEqual(observer.status(trace, None), "UNKNOWN")
        self.assertGreaterEqual(poll["wrapper_end"], poll["call_end"])
        self.assertGreaterEqual(poll["evaluate_call_wall_seconds"], 0)

    def test_multiple_poll_and_falsey_values_use_original_partition_semantics(self):
        for first in ("?old|complete", "?f=0|loading", "", None, False, 0):
            with self.subTest(first=first):
                trace, observer, tab, _ = self.pair([response(first), response("?f=0|complete")])
                with patch.object(gfx.time, "sleep") as sleep:
                    tab.navigate("file:///page.html?f=0")
                sleep.assert_called_once_with(0.02)
                self.assertEqual(observer.counts["unmatched"], 1)
                planned = observer.polls[0]["planned_sleep"]
                self.assertEqual(planned["after_poll_sequence"], 1)
                self.assertEqual(planned["requested_seconds"], 0.02)
                self.assertIsNone(planned["actual_enter"])
                self.assertIsNone(planned["actual_exit"])
                self.assertEqual(observer.polls[1]["inter_poll_gap_seconds"],
                                 observer.polls[1]["call_start"] - observer.polls[0]["call_end"])
                self.assertIsNone(observer.polls[0]["inter_poll_gap_seconds"])

    def test_last_unmatched_before_timeout_has_planned_sleep(self):
        _, observer, tab, _ = self.pair([response("?old|loading")])
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        # Mocked time is control-flow evidence, never benchmark data.
        with patch.object(gfx.time, "monotonic", side_effect=lambda: now[0]), \
             patch.object(gfx.time, "sleep", side_effect=sleep):
            with self.assertRaises(TimeoutError):
                tab.navigate("file:///page.html?f=0", timeout=0.01)
        self.assertEqual(observer.counts["unmatched"], 1)
        self.assertIsNotNone(observer.polls[0]["planned_sleep"])
        self.assertEqual(observer.navigations[0]["error_type"], "TimeoutError")
        self.assertEqual(observer.counts["failed_navigation"], 1)

    def test_protocol_transport_unparseable_and_interrupt_preserve_primary(self):
        cases = [(RuntimeError("transport"), RuntimeError, "CALL_OR_PROTOCOL_ERROR"),
                 (KeyboardInterrupt(), KeyboardInterrupt, "CALL_OR_PROTOCOL_ERROR"),
                 ({"exceptionDetails": {}}, adapter.PilotError, "CALL_OR_PROTOCOL_ERROR"),
                 ({"result": []}, adapter.PilotError, "CALL_OR_PROTOCOL_ERROR"),
                 (response(7), AttributeError, "UNPARSEABLE")]
        for payload, kind, parse in cases:
            with self.subTest(kind=kind, parse=parse):
                _, observer, tab, _ = self.pair([payload])
                with self.assertRaises(kind) as caught:
                    tab.navigate("file:///page.html?f=0")
                if isinstance(payload, BaseException):
                    self.assertIs(caught.exception, payload)
                self.assertEqual(observer.polls[0]["parse_status"], parse)
                self.assertIsNone(observer.polls[0]["matched"])
                self.assertIsNone(observer.polls[0]["planned_sleep"])
                self.assertIsNotNone(observer.navigations[0]["exit"])
                self.assertEqual(observer.counts["unmatched"], 0)
                self.assertEqual(observer.navigations[0]["status"], "failed")

    def test_only_exact_expression_inside_navigation_is_a_poll(self):
        _, observer, tab, _ = self.pair([response("?f=0|complete"), response("?f=0|complete"), response(False)])
        tab.call("Runtime.evaluate", expression=obs.POLL_EXPRESSION, returnByValue=True)
        tab.navigate("file:///page.html?f=0")
        tab.call("Runtime.evaluate", expression="unrelated")
        self.assertEqual(observer.counts["poll"], 1)
        self.assertEqual(len(tab.calls), 4)

    def test_recovery_generation_is_actual_and_failure_latched(self):
        trace, observer, tab, browser = self.pair([response("?f=0|complete"), response("?f=0|complete")])
        tab.navigate("file:///page.html?f=0")
        browser.hard_restart()
        browser.open_tabs()
        tab.navigate("file:///page.html?f=0")
        self.assertEqual([n["browser_generation"] for n in observer.navigations], [4, 5])
        self.assertEqual(trace.recovery_attempts, 1)
        self.assertEqual(observer.status(trace, None), "FAILED")
        self.assertEqual(len(observer.polls), 2)

    def test_caps_keep_navigation_semantics_and_explicit_drops(self):
        for changes, key in (({"poll_records": 1}, "dropped_poll"),
                             ({"navigation_records": 0}, "dropped_navigation"),
                             ({"evidence_bytes": obs._METADATA_BYTES + 1}, "dropped_navigation")):
            with self.subTest(changes=changes), patch.dict(obs.LIMITS, changes):
                trace, observer, tab, _ = self.pair([response("old|loading"), response("?f=0|complete")])
                with patch.object(gfx.time, "sleep") as sleep:
                    tab.navigate("file:///page.html?f=0")
                sleep.assert_called_once_with(0.02)
                self.assertEqual(len(tab.calls), 3)
                self.assertTrue(observer.overflow)
                self.assertGreater(observer.counts[key], 0)
                self.assertEqual(observer.status(trace, None), "INCOMPLETE")

    def test_observation_error_does_not_replace_original_or_make_early_timeout(self):
        original = TimeoutError("original primary")
        trace, observer, tab, _ = self.pair([original])
        with patch.object(obs, "clock_now", side_effect=OSError("clock unavailable")):
            with self.assertRaises(TimeoutError) as caught:
                tab.navigate("file:///page.html?f=0")
        self.assertIs(caught.exception, original)
        self.assertGreater(observer.counts["observation_error"], 0)
        self.assertEqual(observer.status(trace, original), "FAILED")
        self.assertEqual(len(tab.calls), 2)

    def test_open_interval_never_gets_fake_zero(self):
        observer = self.observer()
        tab = type("T", (), {})()
        frame = sys._getframe()
        try:
            nav = observer.nav_enter(tab, "file:///page.html?f=0", frame)
        finally:
            del frame
        record = observer.poll_enter(nav)
        trace = adapter.CaptureTrace("baseline", self.root)
        descriptor = observer.persist(trace, None)
        saved = json.loads(Path(descriptor["path"]).read_bytes())
        self.assertEqual(saved["status"], "INCOMPLETE")
        self.assertIsNone(saved["polls"][0]["call_end"])
        self.assertIsNone(saved["polls"][0]["evaluate_call_wall_seconds"])
        self.assertIsNone(saved["navigations"][0]["exit"])
        self.assertIsNotNone(record["unclosed_reason"])

    def test_context_snapshot_and_mapping_validation(self):
        job = context()
        observer = self.observer(job)
        job["mapping"][0]["global"] = 999
        job["entry"]["entryId"] = "changed"
        self.assertEqual(observer.mapping[0], 18)
        self.assertEqual(observer.context["entry"]["entryId"], "T08.01")
        for bad in (context(((19, 0),)), context(((18, 0), (18, 0)))):
            with self.assertRaises(ValueError):
                self.observer(bad)

    @contextlib.contextmanager
    def render_fixture(self, *, behavior=None):
        payload = base64.b64encode(png(np.array([[[12, 34, 56, 128]]], dtype=np.uint8))).decode()
        requests, threads = [], []
        def call(tab, method, timeout=30, **params):
            requests.append((method, timeout, copy.deepcopy(params)))
            if method == "Page.navigate":
                tab.mock_url = params["url"]
                threads.append(threading.get_ident())
            if behavior:
                selected = behavior(tab, method, params)
                if selected is not None:
                    return selected
            if method == "Runtime.evaluate":
                if params["expression"] == obs.POLL_EXPRESSION:
                    return response("?" + tab.mock_url.split("?", 1)[1] + "|complete")
                return {"result": {"type": "boolean", "value": False}}
            if method == "Page.captureScreenshot":
                return {"data": payload}
            return {}
        def start(browser):
            browser.chrome, browser.port, browser.tab_ws_urls = "/mock/chrome", 1234, ["/tab"]
        def endpoint(port, name):
            value = VERSION if name == "version" else PROTOCOL
            return copy.deepcopy(value), json.dumps(value).encode()
        with patch.object(gfx, "WSConn"), patch.object(gfx.Tab, "call", call), \
             patch.object(gfx.Browser, "start", start), patch.object(gfx.Browser, "stop"), \
             patch.object(adapter, "_read_browser_json", side_effect=endpoint), \
             patch.object(gfx.time, "sleep"), \
             patch.dict(os.environ, {"VTX_GFX_STATIC": "0", "VTX_GFX_NAVFREE": "0"}):
            yield requests, threads

    def run_page(self, evidence, *, enabled=True, behavior=None, frames=(0, 2)):
        page = self.root / "page.html"
        page.write_text("not opened; mock CDP only")
        job = context(tuple((i + 18, i) for i in frames))
        with self.render_fixture(behavior=behavior) as (requests, threads):
            with adapter.capture_scope("baseline", self.root / evidence, poll_observation=enabled, job_context=job) as trace:
                stats = gfx.render_page(page, self.root / (evidence + "-frames"), 3,
                                        tabs=1, width=1, height=1, frames=list(frames))
                trace.assert_valid(stats, expected_frames=len(frames))
        return trace, requests, threads

    def test_real_renderer_caller_roles_same_url_mapping_worker_and_persistence(self):
        trace, requests, threads = self.run_page("on")
        descriptor = trace.poll_observation_report()
        data = Path(descriptor["path"]).read_bytes()
        report = json.loads(data)
        self.assertEqual(descriptor["status"], "COMPLETE")
        self.assertEqual(descriptor["bytes"], len(data))
        self.assertEqual(descriptor["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual([n["role"] for n in report["navigations"]], ["initial", "flatten_probe", "target", "target"])
        self.assertEqual([n["local"] for n in report["navigations"]], [0, 0, 0, 2])
        self.assertEqual([n["global"] for n in report["navigations"]], [18, 18, 18, 20])
        self.assertEqual(len({n["url"] for n in report["navigations"][:3]}), 1)
        self.assertEqual(len(set(threads)), 2)
        self.assertEqual(report["planned_sleep_seconds"], 0)
        self.assertEqual(report["counts"]["poll"], 4)
        self.assertTrue(report["restoration"]["original_references_restored"])
        for key in ("actual_sleep_seconds", "actual_sleep_enter", "actual_sleep_exit", "browser_ready_time", "recoverable_wait_seconds"):
            self.assertIsNone(report[key])
        self.assertIn("poll_observation", trace.report())
        self.assertEqual(len([r for r in requests if r[0] == "Page.captureScreenshot"]), 3)

    def test_off_identical_requests_no_poll_clock_frames_object_or_schema(self):
        with patch.object(obs, "clock_now", side_effect=AssertionError("off clock")), \
             patch.object(obs, "PollObserver", side_effect=AssertionError("off observer")):
            off, requests_off, _ = self.run_page("off", enabled=False)
        on, requests_on, _ = self.run_page("on")
        self.assertEqual(requests_off, requests_on)
        self.assertFalse(hasattr(off, "poll_observer"))
        self.assertNotIn("poll_observation", off.report())
        self.assertFalse((self.root / "off/poll-observation.json").exists())
        descriptor = off.poll_observation_report()
        self.assertEqual(descriptor["status"], "DISABLED")
        self.assertIsNone(descriptor["poll_count"])
        self.assertIsNone(descriptor["planned_sleep_seconds"])
        self.assertEqual(set(off.report()), set(on.report()) - {"poll_observation"})

    def test_unknown_or_zero_samples_cannot_qualify(self):
        for job, expected in (({}, "NOT_STARTED"), (context(), "UNKNOWN")):
            observer = self.observer(job)
            trace = adapter.CaptureTrace("baseline", self.root)
            self.assertEqual(observer.status(trace, None), expected)
        with self.assertRaisesRegex(adapter.PilotError, "UNKNOWN"):
            with adapter.capture_scope("baseline", self.root / "empty", poll_observation=True, job_context=context()) as trace:
                trace.validations.append({"offline": True})
        self.assertEqual(trace.poll_observation_report()["status"], "UNKNOWN")

    def test_scope_finally_primary_preserved_when_sidecar_and_trace_writes_fail(self):
        primary = RuntimeError("primary")
        original_tab, original_browser = gfx.Tab, gfx.Browser
        with patch.object(obs.PollObserver, "persist", side_effect=OSError("sidecar")), \
             patch.object(adapter, "_write_json", side_effect=OSError("trace")):
            with self.assertRaises(RuntimeError) as caught:
                with adapter.capture_scope("baseline", self.root / "failure", poll_observation=True, job_context=context()) as trace:
                    raise primary
        self.assertIs(caught.exception, primary)
        self.assertIs(gfx.Tab, original_tab)
        self.assertIs(gfx.Browser, original_browser)
        self.assertEqual(trace.poll_observation_report()["persistence"], "MISSING")
        self.assertEqual(trace.poll_observation_report()["trace_persistence"], "MISSING")
        self.assertFalse(adapter._SCOPE_LOCK.locked())

    def test_sidecar_write_failure_marks_missing_and_fails_scope(self):
        original_open = Path.open
        def failing_open(path, *args, **kwargs):
            if path.name == "poll-observation.json":
                raise OSError("sidecar write")
            return original_open(path, *args, **kwargs)
        with patch.object(Path, "open", failing_open):
            with self.assertRaisesRegex(adapter.PilotError, "FAILED"):
                self.run_page("write-failure")
        report = json.loads((self.root / "write-failure/trace.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["poll_observation"]["persistence"], "MISSING")
        self.assertIsNone(report["poll_observation"]["sha256"])

    def test_reference_pollution_is_ineligible_and_originals_restored(self):
        original_tab, original_browser = gfx.Tab, gfx.Browser
        original_write = gfx._atomic_write_bytes
        with self.assertRaisesRegex(adapter.PilotError, "UNKNOWN"):
            with adapter.capture_scope("baseline", self.root / "pollution", poll_observation=True,
                                       job_context=context()) as trace:
                trace.validations.append({"offline": True})
                gfx._atomic_write_bytes = lambda *args: None
        saved = json.loads((self.root / "pollution/poll-observation.json").read_text())
        self.assertFalse(saved["restoration"]["installed_references_intact"])
        self.assertTrue(saved["restoration"]["original_references_restored"])
        self.assertIs(gfx._atomic_write_bytes, original_write)
        self.assertIs(gfx.Tab, original_tab)
        self.assertIs(gfx.Browser, original_browser)

    def test_clock_call_interval_excludes_record_allocation_and_validation(self):
        trace, observer, tab, _ = self.pair([response("?f=0|complete")])
        counter = [0.0]
        def tick():
            counter[0] += 1
            return counter[0]
        original_enter = observer.poll_enter
        def allocating(nav):
            result = original_enter(nav)
            counter[0] += 100
            return result
        original_check = trace.check_response
        def checking(method, response, **kwargs):
            if method == "Runtime.evaluate":
                counter[0] += 200
            return original_check(method, response, **kwargs)
        with patch.object(obs, "clock_now", side_effect=tick), \
             patch.object(observer, "poll_enter", side_effect=allocating), \
             patch.object(trace, "check_response", side_effect=checking):
            tab.navigate("file:///page.html?f=0")
        poll = observer.polls[0]
        self.assertEqual(poll["evaluate_call_wall_seconds"], 1)
        self.assertEqual(poll["wrapper_end"] - poll["call_end"], 201)

    def test_defensive_serialized_cap_reports_missing_not_in_memory_success(self):
        trace, observer, tab, _ = self.pair([response("?f=0|complete")])
        tab.navigate("file:///page.html?f=0")
        with patch.dict(obs.LIMITS, {"evidence_bytes": 1}):
            descriptor = observer.persist(trace, None)
        self.assertEqual(descriptor["persistence"], "MISSING")
        self.assertEqual(descriptor["status"], "FAILED")
        self.assertTrue(descriptor["incomplete"])
        self.assertTrue(descriptor["overflow"])
        self.assertEqual(descriptor["counts"]["poll"], 1)
        self.assertIsNone(descriptor["path"])
        self.assertEqual(len(observer.polls), 1)

    def test_mode_combination_rejected_before_evidence_directory(self):
        with self.assertRaises(ValueError):
            with adapter.capture_scope("fast", self.root / "invalid", poll_observation=True):
                pass
        self.assertFalse((self.root / "invalid").exists())

    def test_original_single_receiver_handles_interleaved_fetch(self):
        class Policy:
            def response(self, params):
                return "Fetch.continueRequest", {"requestId": params["requestId"]}
            def check(self):
                pass
        class Socket:
            def __init__(self):
                self.sent = []
                self.queue = []
                self.receivers = []
            def send_text(self, data):
                message = json.loads(data)
                self.sent.append(message)
                mid = message["id"]
                if message["method"] == "Runtime.evaluate":
                    self.queue.extend([{"method": "Fetch.requestPaused", "params": {"requestId": "req"}},
                                       {"id": mid, "result": response("?f=0|complete")}])
                else:
                    self.queue.append({"id": mid, "result": {}})
            def recv_msg(self):
                caller = sys._getframe(1)
                try:
                    self.receivers.append(caller.f_code)
                finally:
                    del caller
                return 1, json.dumps(self.queue.pop(0))
        class OriginalTransport:
            call, navigate = gfx.Tab.call, gfx.Tab.navigate
            def __init__(self):
                self._mid, self._policy_commands = 0, set()
                self.request_policy, self.ws = Policy(), Socket()
        trace = adapter.CaptureTrace("baseline", self.root)
        observer = self.observer()
        Tab, _ = adapter._classes(trace, OriginalTransport, ScriptedBrowser, observer)
        tab = Tab()
        tab.navigate("file:///page.html?f=0")
        self.assertEqual([m["method"] for m in tab.ws.sent], ["Page.navigate", "Runtime.evaluate", "Fetch.continueRequest"])
        self.assertEqual(set(tab.ws.receivers), {gfx.Tab.call.__code__})
        self.assertEqual(observer.counts["poll"], 1)
        self.assertTrue(observer.polls[0]["matched"])
        self.assertEqual(tab._policy_commands, set())


if __name__ == "__main__":
    unittest.main()
