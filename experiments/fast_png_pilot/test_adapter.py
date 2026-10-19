"""Explicit offline protocol/PNG tests. No Chrome, FFmpeg or CI registration."""
import base64
import copy
import contextlib
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

import numpy as np
from vtx import gfx_render as gfx
from experiments.fast_png_pilot import adapter as pilot


PROTOCOL = {"domains": [{"domain": "Page", "commands": [{"name": "captureScreenshot", "parameters": [
    {"name": "optimizeForSpeed", "type": "boolean", "optional": True}]}]}]}
VERSION = {"Browser": "MockChrome/offline", "Protocol-Version": "1.3"}


def png(rgba, level=3):
    h, w, _ = rgba.shape
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    raw = b"".join(b"\0" + row.tobytes() for row in rgba)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, level)) + chunk(b"IEND", b"")


def valid_stats(n=1):
    return {"rendered": n, "failed": [], "tabs": 1, "canvas_export": False, "path_counts": {"override": n}}


class FakeTab:
    # Reuse the actual method: the adapter must not replace its decode contract.
    shot = gfx.Tab.shot
    def __init__(self, *args, **kwargs):
        self.calls = []
    def call(self, method, timeout=30, **params):
        self.calls.append((method, timeout, params))
        if method == "Runtime.evaluate":
            return {"result": {"type": "undefined"}}
        return {"data": base64.b64encode(b"unaltered PNG bytes").decode()}


class FakeBrowser:
    def __init__(self, tabs=1):
        self.tabs_n = tabs
        self.chrome = "/mock/chrome"
        self.port = 1234
        self.generation = 0
        self.width = 1920
        self.height = 1080
    def start(self):
        pass


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def trace(self, mode="fast"):
        return pilot.CaptureTrace(mode, self.root)

    def endpoint(self, port, name):
        value = VERSION if name == "version" else PROTOCOL
        return copy.deepcopy(value), json.dumps(value).encode()

    def test_default_production_source_is_pinned_and_has_no_parameter(self):
        self.assertEqual(pilot._sha(Path(gfx.__file__).read_bytes()), pilot.RENDERER_SHA256)
        tab = FakeTab()
        self.assertEqual(tab.shot(), b"unaltered PNG bytes")
        self.assertEqual(tab.calls, [("Page.captureScreenshot", 30, {"format": "png"})])

    def test_only_fast_adds_one_parameter_and_original_shot_bytes_are_preserved(self):
        for mode in ("baseline", "fast"):
            trace = self.trace(mode)
            tab_type, _ = pilot._classes(trace, FakeTab, FakeBrowser)
            tab = tab_type()
            tab.call("Page.navigate", url="file:///bound/page.html?f=1380")
            self.assertEqual(tab.shot(), b"unaltered PNG bytes")
            params = {"format": "png", **({"optimizeForSpeed": True} if mode == "fast" else {})}
            self.assertEqual(tab.calls[-1], ("Page.captureScreenshot", 30, params))
            self.assertEqual(trace.captures[0]["original_params"], {"format": "png"})
            self.assertEqual(trace.captures[0]["actual_params"], params)
            self.assertEqual(trace.captures[0]["navigation_url"], "file:///bound/page.html?f=1380")

    def test_probe_and_both_background_shots_use_fast_parameter(self):
        trace = self.trace()
        tab_type, _ = pilot._classes(trace, FakeTab, FakeBrowser)
        tab = tab_type()
        tab.shot()
        tab.call("Runtime.evaluate", expression=gfx._BG_INJECT_WHITE)
        tab.shot()
        tab.call("Runtime.evaluate", expression=gfx._BG_INJECT_BLACK)
        tab.shot()
        self.assertEqual([c["injected_background"] for c in trace.captures], [None, "white-injection", "black-injection"])
        self.assertTrue(all(c["actual_params"] == {"format": "png", "optimizeForSpeed": True} for c in trace.captures))

    def test_parameter_drift_is_rejected_before_transport(self):
        trace = self.trace()
        tab_type, _ = pilot._classes(trace, FakeTab, FakeBrowser)
        tab = tab_type()
        for params in ({"format": "jpeg"}, {"format": "png", "quality": 1}, {"format": "png", "optimizeForSpeed": False}):
            with self.assertRaises(pilot.PilotError):
                tab.call("Page.captureScreenshot", **params)
        self.assertEqual(tab.calls, [])

    def test_capture_errors_latch_and_no_baseline_retry_is_sent(self):
        for error in (RuntimeError("Unknown parameter optimizeForSpeed"), RuntimeError("renderer crashed"), TimeoutError("timeout"), ConnectionError("socket")):
            trace = self.trace()
            class Broken(FakeTab):
                def call(self, *args, **kwargs):
                    raise error
            tab_type, _ = pilot._classes(trace, Broken, FakeBrowser)
            tab = tab_type()
            with self.assertRaises(type(error)):
                tab.shot()
            with self.assertRaisesRegex(pilot.PilotError, "already failed"):
                tab.shot()
            self.assertEqual(trace.status, "failed")
            with self.assertRaises(pilot.PilotError):
                trace.assert_valid(valid_stats(), expected_frames=1)

    def test_missing_screenshot_payload_fails(self):
        trace = self.trace()
        class Missing(FakeTab):
            def call(self, *args, **kwargs):
                return {}
        tab_type, _ = pilot._classes(trace, Missing, FakeBrowser)
        with self.assertRaisesRegex(pilot.PilotError, "base64"):
            tab_type().shot()

    def test_capability_requires_correct_optional_boolean(self):
        self.assertTrue(pilot.protocol_has_fast_png(PROTOCOL))
        for value in ({}, {"domains": []}, {"domains": [{"domain": "Other"}]}):
            self.assertFalse(pilot.protocol_has_fast_png(value))
        for change in ({"type": "string"}, {"optional": False}, {"name": "other"}):
            changed = copy.deepcopy(PROTOCOL)
            changed["domains"][0]["commands"][0]["parameters"][0].update(change)
            self.assertFalse(pilot.protocol_has_fast_png(changed))

    def test_browser_identity_is_saved_before_capture_without_extra_probe(self):
        trace = self.trace()
        _, browser_type = pilot._classes(trace, FakeTab, FakeBrowser)
        with patch.object(pilot, "_read_browser_json", side_effect=self.endpoint):
            browser_type().start()
        self.assertEqual(trace.captures, [])
        self.assertTrue(trace.browsers[0]["advertises_fast_png"])
        self.assertEqual(json.loads((self.root / "browser-0-version.json").read_text()), VERSION)

    def test_fast_unsupported_fails_baseline_remains_observable(self):
        for mode in ("baseline", "fast"):
            with tempfile.TemporaryDirectory() as d:
                trace = pilot.CaptureTrace(mode, Path(d))
                _, browser_type = pilot._classes(trace, FakeTab, FakeBrowser)
                def endpoint(port, name):
                    value = VERSION if name == "version" else {"domains": []}
                    return value, json.dumps(value).encode()
                with patch.object(pilot, "_read_browser_json", side_effect=endpoint):
                    if mode == "fast":
                        with self.assertRaisesRegex(pilot.PilotError, "no fallback"):
                            browser_type().start()
                    else:
                        browser_type().start()
                self.assertFalse(trace.browsers[0]["advertises_fast_png"])

    def test_multiple_tabs_rejected(self):
        _, browser_type = pilot._classes(self.trace(), FakeTab, FakeBrowser)
        with self.assertRaisesRegex(pilot.PilotError, "single-worker"):
            browser_type(tabs=2)

    def test_nested_scope_and_exception_restore_production_types(self):
        tab, browser = gfx.Tab, gfx.Browser
        with self.assertRaisesRegex(RuntimeError, "abort"):
            with pilot.capture_scope("baseline", self.root / "outer"):
                with self.assertRaisesRegex(pilot.PilotError, "nested/concurrent"):
                    with pilot.capture_scope("fast", self.root / "inner"):
                        pass
                raise RuntimeError("abort")
        self.assertIs(gfx.Tab, tab)
        self.assertIs(gfx.Browser, browser)
        self.assertEqual(json.loads((self.root / "outer/trace.json").read_text())["status"], "failed")

    def test_unvalidated_scope_cannot_exit_successfully(self):
        with self.assertRaisesRegex(pilot.PilotError, "without a clean"):
            with pilot.capture_scope("baseline", self.root / "unvalidated"):
                pass

    def test_reused_evidence_dir_is_refused_and_lock_recovers(self):
        for attempt in range(2):
            with self.assertRaises(FileExistsError):
                with pilot.capture_scope("fast", self.root):
                    pass

    def test_statistics_reject_warm_copies_canvas_navfree_and_missing_frames(self):
        for mutation in ({"rendered": 0}, {"failed": [0]}, {"tabs": 2}, {"canvas_export": True},
                         {"path_counts": {"override": 1, "static_copied": 1}},
                         {"path_counts": {"navfree": 1}}, {"path_counts": {"opaque": 0}}):
            trace = self.trace()
            trace.browsers = [{}]
            trace.browser_start_attempts = 1
            trace.captures = [{"status": "accepted"}]
            stats = valid_stats()
            stats.update(mutation)
            with self.assertRaises(pilot.PilotError):
                trace.assert_valid(stats, expected_frames=1)

    def test_mock_production_render_uses_original_navigation_and_alpha_route(self):
        rgba = np.array([[[12, 34, 56, 128]]], dtype=np.uint8)
        payload = base64.b64encode(png(rgba)).decode()
        def call(tab, method, timeout=30, **params):
            if method == "Page.navigate":
                tab.mock_url = params["url"]
            if method == "Runtime.evaluate":
                expression = params["expression"]
                if expression == "location.search+'|'+document.readyState":
                    return {"result": {"type": "string", "value": "?" + tab.mock_url.split("?", 1)[1] + "|complete"}}
                return {"result": {"type": "boolean", "value": False}}
            return {"data": payload}
        def start(browser):
            browser.chrome, browser.port, browser.tab_ws_urls = "/mock/chrome", 1234, ["/tab"]
        page = self.root / "page.html"
        page.write_text("unused by mock")
        for mode in ("baseline", "fast"):
            with patch.object(gfx, "WSConn"), patch.object(gfx.Tab, "call", call), \
                 patch.object(gfx.Browser, "start", start), patch.object(gfx.Browser, "stop"), \
                 patch.object(pilot, "_read_browser_json", side_effect=self.endpoint), \
                 patch.dict(os.environ, {"VTX_GFX_STATIC": "0", "VTX_GFX_NAVFREE": "0"}):
                with pilot.capture_scope(mode, self.root / (mode + "-evidence")) as trace:
                    stats = gfx.render_page(page, self.root / mode, 3, tabs=1, width=1, height=1, frames=[0, 2])
                    trace.assert_valid(stats, expected_frames=2)
            self.assertEqual(stats["path_counts"]["override"], 2)
            self.assertEqual([Path(self.root / mode / f"f{n:04d}.png").read_bytes() for n in (0, 2)], [png(rgba), png(rgba)])
            self.assertEqual(len(trace.captures), 3)  # production alpha probe + two actual target frames
            self.assertEqual(trace.captures[-1]["navigation_url"], page.as_uri() + "?f=2")

    def test_recovered_frame_failures_never_receive_clean_qualification(self):
        # Reproduces independent review's navigation/base64/write counterexamples,
        # plus Runtime.evaluate and a readiness timeout outside Tab.call.
        original_write, original_navigate = gfx._atomic_write_bytes, gfx.Tab.navigate
        for mode in ("baseline", "fast"):
            for kind in ("navigation", "runtime", "readiness", "base64", "write"):
                with self.subTest(mode=mode, failure=kind):
                    directory = self.root / (mode + "-" + kind)
                    directory.mkdir()
                    page = directory / "page.html"
                    page.write_text("offline retry injection")
                    payload = base64.b64encode(png(np.array([[[12, 34, 56, 128]]], dtype=np.uint8))).decode()
                    state = {"injected": 0, "screenshots": 0, "browser_starts": 0}
                    main_thread = threading.get_ident()
                    def in_worker():
                        return threading.get_ident() != main_thread
                    def call(tab, method, timeout=30, **params):
                        if method == "Page.navigate":
                            if kind == "navigation" and in_worker() and not state["injected"]:
                                state["injected"] += 1
                                raise TimeoutError("injected one frame navigation timeout")
                            tab.mock_url = params["url"]
                        if method == "Runtime.evaluate":
                            if kind == "runtime" and in_worker() and not state["injected"]:
                                state["injected"] += 1
                                raise RuntimeError("injected evaluation failure")
                            if params["expression"] == "location.search+'|'+document.readyState":
                                return {"result": {"type": "string", "value": "?" + tab.mock_url.split("?", 1)[1] + "|complete"}}
                            return {"result": {"type": "boolean", "value": False}}
                        if method == "Page.captureScreenshot":
                            state["screenshots"] += 1
                            if kind == "base64" and state["screenshots"] == 2:
                                state["injected"] += 1
                                return {"data": "A"}
                            return {"data": payload}
                        return {}
                    def navigate(tab, *args, **kwargs):
                        result = original_navigate(tab, *args, **kwargs)
                        if kind == "readiness" and in_worker() and not state["injected"]:
                            state["injected"] += 1
                            raise TimeoutError("injected readiness timeout outside CDP call")
                        return result
                    def start(browser):
                        state["browser_starts"] += 1
                        browser.chrome, browser.port, browser.tab_ws_urls = "/mock/chrome", 1234, ["/tab"]
                    def write(path, data):
                        if kind == "write" and not state["injected"]:
                            state["injected"] += 1
                            raise OSError("injected output write failure")
                        return original_write(path, data)
                    with patch.object(gfx, "WSConn"), patch.object(gfx.Tab, "call", call), \
                         patch.object(gfx.Tab, "navigate", navigate), \
                         patch.object(gfx.Browser, "start", start), patch.object(gfx.Browser, "stop"), \
                         patch.object(gfx, "_atomic_write_bytes", write), patch.object(gfx.time, "sleep"), \
                         patch.object(pilot, "_read_browser_json", side_effect=self.endpoint), \
                         patch.dict(os.environ, {"VTX_GFX_STATIC": "0", "VTX_GFX_NAVFREE": "0"}):
                        with self.assertRaises(pilot.PilotError):
                            with pilot.capture_scope(mode, directory / "evidence") as trace:
                                stats = gfx.render_page(page, directory / "frames", 1, tabs=1,
                                                        width=1, height=1, frames=[0])
                                trace.assert_valid(stats, expected_frames=1)
                    self.assertEqual(state["injected"], 1)
                    self.assertEqual(state["browser_starts"], 1)
                    self.assertEqual(trace.status, "failed")
                    self.assertTrue(trace.errors)
                    self.assertGreater(trace.recovery_attempts, 0)
                    self.assertGreater(trace.browser_start_attempts, 1)
                    expected = {"navigation": "Page.navigate", "runtime": "Runtime.evaluate",
                                "readiness": "Tab.navigate", "base64": "Tab.shot",
                                "write": "gfx._atomic_write_bytes"}[kind]
                    self.assertTrue(any(error["where"] == expected for error in trace.errors))
                    report = json.loads((directory / "evidence/trace.json").read_text())
                    self.assertEqual(report["status"], "failed")
                    self.assertTrue(any(event["status"] == "failed" for event in report["operations"]))
                    self.assertIs(gfx._atomic_write_bytes, original_write)

    def test_initialization_failure_and_repeated_start_are_recorded(self):
        trace = self.trace()
        _, browser_type = pilot._classes(trace, FakeTab, FakeBrowser)
        with patch.object(FakeBrowser, "start", side_effect=OSError("initial start failed")):
            browser = browser_type()
            with self.assertRaises(OSError):
                browser.start()
            with self.assertRaises(pilot.PilotError):
                browser.start()
        self.assertEqual(trace.browser_start_attempts, 2)
        self.assertEqual([event["status"] for event in trace.operations if event["operation"] == "Browser.start"], ["failed", "failed"])
        self.assertIn("initial start failed", str(trace.errors))

    def test_two_shot_operation_failure_is_recorded_and_original_restored(self):
        original = gfx._two_shot_combine
        with self.assertRaises((ValueError, OSError)):
            with pilot.capture_scope("baseline", self.root / "combine-failure") as trace:
                gfx._two_shot_combine(b"broken white PNG", b"broken black PNG")
        self.assertIs(gfx._two_shot_combine, original)
        self.assertTrue(any(error["where"] == "gfx._two_shot_combine" for error in trace.errors))

    def test_rgb_trns_is_rejected_before_the_production_decoder(self):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
        prefix = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        suffix = chunk(b"IDAT", zlib.compress(bytes([0, 12, 34, 56]))) + chunk(b"IEND", b"")
        a, b = self.root / "opaque-rgb.png", self.root / "transparent-trns.png"
        a.write_bytes(prefix + suffix)
        b.write_bytes(prefix + chunk(b"tRNS", struct.pack(">HHH", 12, 34, 56)) + suffix)
        properties = {}
        self.assertIsNone(gfx.png_invalid_reason(b, width=1, height=1, _properties=properties))
        self.assertTrue(properties["transparency"])
        for paths in ((a, b), (b, a)):
            with self.assertRaisesRegex(pilot.PilotError, "tRNS"):
                pilot.compare_png_files(*paths, width=1, height=1)

    def test_structured_protocol_failures_through_original_cdp_parser(self):
        # Based on independent raw-WS repro: do not mock Tab.call or forge stats.
        original_call, original_eval = gfx.Tab.call, gfx.Tab.eval
        payload = base64.b64encode(png(np.array([[[12, 34, 56, 128]]], dtype=np.uint8))).decode()
        for mode in ("baseline", "fast"):
            cases = [
                ("evaluate-exception", None, False), ("navigate-error", None, False),
                ("static-result-missing", {}, False),
                ("static-result-null", {"result": None}, False),
                ("static-result-list", {"result": []}, False),
                ("static-result-string", {"result": "invalid RemoteObject"}, False),
                ("static-type-missing", {"result": {}}, False),
                ("normal-undefined", {"result": {"type": "undefined"}}, True),
                ("normal-null", {"result": {"type": "object", "subtype": "null", "value": None}}, True),
                ("normal-false", {"result": {"type": "boolean", "value": False}}, True),
            ]
            for kind, injected_response, clean in cases:
                with self.subTest(mode=mode, failure=kind):
                    directory = self.root / (kind + "-" + mode)
                    directory.mkdir()
                    page = directory / "page.html"
                    page.write_text("Raw JSON protocol fixture; no browser")
                    owner, state, records = threading.get_ident(), {"injected": 0}, []
                    class Wire:
                        def __init__(self, *args, **kwargs):
                            self.url, self.pending = "about:blank", None
                        def send_text(self, text):
                            self.pending = json.loads(text)
                        def recv_msg(self):
                            request, self.pending = self.pending, None
                            method, params, result = request["method"], request["params"], {}
                            if method == "Page.navigate":
                                if kind == "navigate-error" and threading.get_ident() != owner and not state["injected"]:
                                    # Same f=0 probe document remains loaded; readiness could look valid.
                                    assert self.url == params["url"]
                                    state["injected"] += 1
                                    result = {"frameId": "fixture-frame", "errorText": "net::ERR_ABORTED"}
                                else:
                                    self.url = params["url"]
                                    result = {"frameId": "fixture-frame", "loaderId": "fixture-loader"}
                            elif method == "Runtime.evaluate":
                                expression = params["expression"]
                                if expression == "location.search+'|'+document.readyState":
                                    result = {"result": {"type": "string", "value": "?" + self.url.split("?", 1)[1] + "|complete"}}
                                elif injected_response is not None and expression == "window.__vtx_static_spans || null":
                                    state["injected"] += 1
                                    result = copy.deepcopy(injected_response)
                                elif kind == "evaluate-exception" and expression == "typeof window.__vtx_export_png === 'function'":
                                    state["injected"] += 1
                                    result = {"result": {"type": "object", "subtype": "error", "description": "Error: getter failed"},
                                              "exceptionDetails": {"exceptionId": 1, "text": "Uncaught", "lineNumber": 0, "columnNumber": 0}}
                                else:
                                    result = {"result": {"type": "boolean", "value": False}}
                            elif method == "Page.captureScreenshot":
                                result = {"data": payload}
                            response = {"id": request["id"], "result": result}
                            records.append({"request": request, "response": response})
                            return 1, json.dumps(response).encode()
                        def close(self):
                            pass
                    def start(browser):
                        browser.chrome, browser.port, browser.tab_ws_urls = "/mock/chrome", 1234, ["/tab"]
                    with patch.object(gfx, "WSConn", Wire), patch.object(gfx.Browser, "start", start), \
                         patch.object(gfx.Browser, "stop"), patch.object(gfx.time, "sleep"), \
                         patch.object(pilot, "_read_browser_json", side_effect=self.endpoint), \
                         patch.dict(os.environ, {"VTX_GFX_STATIC": "1", "VTX_GFX_NAVFREE": "0"}):
                        with contextlib.nullcontext() if clean else self.assertRaises(pilot.PilotError):
                            with pilot.capture_scope(mode, directory / "evidence") as trace:
                                self.assertIs(gfx.Tab.__mro__[1].call, original_call)
                                self.assertIs(gfx.Tab.__mro__[1].eval, original_eval)
                                stats = gfx.render_page(page, directory / "frames", 1, tabs=1,
                                                        width=1, height=1, frames=[0])
                                trace.assert_valid(stats, expected_frames=1)
                    self.assertEqual(state["injected"], 1)
                    report = json.loads((directory / "evidence/trace.json").read_text())
                    self.assertEqual(report["protocol_failures"], trace.protocol_failures)
                    if clean:
                        self.assertEqual(trace.status, "request-and-render-contract-checked")
                        self.assertEqual(trace.errors, [])
                        self.assertEqual(stats["rendered"], 1)
                        self.assertEqual(stats["failed"], [])
                    else:
                        self.assertEqual(trace.status, "failed")
                        self.assertTrue(trace.errors)
                        self.assertEqual(len(trace.protocol_failures), 1)
                        if injected_response is not None:
                            self.assertEqual(trace.protocol_failures[0]["response"], injected_response)
                            self.assertTrue(any(error["where"] == "Tab.eval" for error in trace.errors))
                        else:
                            field = "exceptionDetails" if kind == "evaluate-exception" else "errorText"
                            self.assertIn(field, trace.protocol_failures[0]["response"])
                            self.assertTrue(any(field in record["response"]["result"] for record in records))

    def test_caught_constructor_and_guard_failures_remain_fatal(self):
        for mode in ("baseline", "fast"):
            directory = self.root / (mode + "-caught-init")
            with patch.dict(os.environ, {"VTX_GFX_TABS": "4", "VTX_GFX_NAVFREE": "0"}):
                with self.assertRaises(pilot.PilotError):
                    with pilot.capture_scope(mode, directory) as trace:
                        with self.assertRaisesRegex(pilot.PilotError, "single-worker"):
                            gfx.Browser()  # Original constructor's default is four tabs.
                        with self.assertRaisesRegex(pilot.PilotError, "new scope"):
                            gfx.Browser(tabs=1)
                        trace.assert_valid(valid_stats(), expected_frames=1)
            self.assertEqual(trace.status, "failed")
            self.assertEqual(trace.browser_start_attempts, 0)
            failures = [event for event in trace.operations if event["operation"] == "Browser.__init__"]
            self.assertEqual([event["status"] for event in failures], ["failed", "failed"])
            self.assertTrue(any("single-worker" in error["message"] for error in trace.errors))
        # Exceptions thrown by the original constructor itself also latch.
        trace = self.trace()
        _, browser_type = pilot._classes(trace, FakeTab, FakeBrowser)
        with patch.object(FakeBrowser, "__init__", side_effect=ValueError("original init failed")):
            with self.assertRaisesRegex(ValueError, "original init failed"):
                browser_type()
        self.assertEqual(trace.status, "failed")
        self.assertTrue(any(error["where"] == "Browser.__init__" for error in trace.errors))

    def test_explicit_error_objects_downloads_and_malformed_results_reject(self):
        for method, response in (
            ("Runtime.evaluate", {"exceptionDetails": {}}),
            ("Runtime.evaluate", {"result": {"type": "object", "subtype": "error"}}),
            ("Page.navigate", {"errorText": ""}),
            ("Page.navigate", {"isDownload": True}),
            ("Page.enable", []),
        ):
            trace = self.trace()
            with self.assertRaises(pilot.PilotError):
                trace.check_response(method, response, params={})
            self.assertEqual(trace.protocol_failures[0]["response"], response)
            self.assertEqual(trace.status, "failed")
        trace = self.trace()
        normal = {"result": {"type": "boolean", "value": False}}
        self.assertIs(trace.check_response("Runtime.evaluate", normal, params={}), normal)
        self.assertEqual(trace.errors, [])

    def test_evaluate_remote_object_boundary_preserves_values_and_rejects_shape(self):
        for remote in (None, [], "bad", {}, {"value": False}, {"type": None},
                       {"type": []}, {"type": "future-unknown"},
                       {"type": "object", "subtype": None}):
            trace = self.trace()
            with self.subTest(remote=remote), self.assertRaises(pilot.PilotError):
                trace.check_response("Runtime.evaluate", {"result": remote}, params={})
            self.assertTrue(trace.protocol_failures)
        # The adapter must not coerce normal values or change original eval's
        # missing-value behavior, including undefined and non-JSON primitives.
        for remote in ({"type": "undefined"}, {"type": "object", "subtype": "null", "value": None},
                       {"type": "boolean", "value": False}, {"type": "number", "value": 0},
                       {"type": "string", "value": ""}, {"type": "object", "value": []},
                       {"type": "object", "value": {"ok": True}},
                       {"type": "number", "unserializableValue": "NaN"},
                       {"type": "bigint", "unserializableValue": "1n"},
                       {"type": "function", "objectId": "fixture"}, {"type": "symbol"}):
            trace = self.trace()
            class Evaluating(FakeTab):
                eval = gfx.Tab.eval
                def call(self, *args, **kwargs):
                    return {"result": remote}
            tab_type, _ = pilot._classes(trace, Evaluating, FakeBrowser)
            self.assertIs(tab_type().eval("fixture expression"), remote.get("value"))
            self.assertEqual(trace.errors, [])
            self.assertEqual(trace.operations[-1]["operation"], "Tab.eval")
            self.assertEqual(trace.operations[-1]["status"], "returned")

    def test_complete_eval_failure_is_latched_outside_call(self):
        for mode in ("baseline", "fast"):
            trace = self.trace(mode)
            error = AttributeError("injected original eval extraction failure")
            class BrokenEval(FakeTab):
                def eval(self, expression):
                    self.call("Runtime.evaluate", expression=expression, returnByValue=True)
                    raise error
            tab_type, _ = pilot._classes(trace, BrokenEval, FakeBrowser)
            tab = tab_type()
            try:
                tab.eval("window.__vtx_static_spans || null")
            except AttributeError as caught:
                self.assertIs(caught, error)  # Original exception survives unchanged.
            else:
                self.fail("expected the original eval exception")
            self.assertTrue(any(item["where"] == "Tab.eval" for item in trace.errors))
            self.assertEqual(trace.protocol_failures, [])  # Response itself was valid.
            with self.assertRaisesRegex(pilot.PilotError, "already failed"):
                tab.shot()
            with self.assertRaises(pilot.PilotError):
                trace.assert_valid(valid_stats(), expected_frames=1)

    def test_full_rgba_and_alpha_comparison_distinguishes_file_bytes(self):
        rgba = np.zeros((8, 8, 4), dtype=np.uint8)
        rgba[0, 0] = [14, 28, 42, 128]
        a, b = self.root / "a.png", self.root / "b.png"
        a.write_bytes(png(rgba, level=0))
        b.write_bytes(png(rgba, level=9))
        report = pilot.compare_png_files(a, b, width=8, height=8)
        self.assertTrue(report["rgba_equal"])
        self.assertTrue(report["alpha_equal"])
        self.assertFalse(report["png_bytes_equal"])
        changed = rgba.copy()
        changed[1, 1, 0] = 1  # RGB under completely transparent pixel still matters.
        b.write_bytes(png(changed))
        report = pilot.compare_png_files(a, b, width=8, height=8)
        self.assertFalse(report["rgba_equal"])
        self.assertTrue(report["alpha_equal"])
        self.assertEqual(report["different_rgba_components"], 1)
        changed[1, 1, 3] = 1
        b.write_bytes(png(changed))
        self.assertFalse(pilot.compare_png_files(a, b, width=8, height=8)["alpha_equal"])

    def test_corruption_wrong_dimensions_and_mutation_are_rejected(self):
        a, b = self.root / "a.png", self.root / "b.png"
        a.write_bytes(png(np.zeros((1, 1, 4), dtype=np.uint8)))
        b.write_bytes(a.read_bytes()[:-1])
        with self.assertRaises(pilot.PilotError):
            pilot.compare_png_files(a, b, width=1, height=1)
        b.write_bytes(a.read_bytes())
        with self.assertRaises(pilot.PilotError):
            pilot.compare_png_files(a, b, width=2, height=1)
        original = gfx.png_invalid_reason
        def mutate(path, **kwargs):
            result = original(path, **kwargs)
            Path(path).write_bytes(png(np.ones((1, 1, 4), dtype=np.uint8)))
            return result
        with patch.object(gfx, "png_invalid_reason", side_effect=mutate), self.assertRaisesRegex(pilot.PilotError, "changed between"):
            pilot.compare_png_files(a, b, width=1, height=1)


if __name__ == "__main__":
    unittest.main()
