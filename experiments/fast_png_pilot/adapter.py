"""Isolated b287 fast-PNG experiment; no production entry point imports this module.

Use only in a dedicated, single-worker child process. The sole screenshot request
change in the fast arm is optimizeForSpeed=True. No CLI/Canvas fallback is supplied.
"""
import contextlib
import copy
import hashlib
import json
from pathlib import Path
import threading
import time
import urllib.request

from vtx import gfx_render as gfx

BASE_COMMIT = "b287b86d95565e308eaf8fa64900f9bc6d62ae8e"
BASE_TREE = "d281bafa0af5073f73cae2170304be76fa040d23"
RENDERER_SHA256 = "f4fa354b7fee6b3951d11a8f0e6c77a8540393ae9d07bbf5c740611c2c91f6ee"
_SCOPE_LOCK = threading.Lock()


class PilotError(RuntimeError):
    """The experimental arm is invalid; do not substitute baseline results."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write_json(path, value):
    # Evidence paths are new, not production outputs or completion certificates.
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def protocol_has_fast_png(protocol):
    """Check this browser's advertised Page schema, not a latest-web assumption."""
    for domain in protocol.get("domains", []):
        if domain.get("domain") != "Page":
            continue
        for command in domain.get("commands", []):
            if command.get("name") != "captureScreenshot":
                continue
            for parameter in command.get("parameters", []):
                if parameter.get("name") == "optimizeForSpeed":
                    return parameter.get("type") == "boolean" and parameter.get("optional") is True
    return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PilotError("local Chrome identity endpoint redirected")


def _read_browser_json(port, endpoint):
    if type(port) is not int or not 0 < port < 65536 or endpoint not in ("version", "protocol"):
        raise PilotError("invalid local Chrome endpoint")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(f"http://127.0.0.1:{port}/json/{endpoint}", timeout=5) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise PilotError("Chrome identity response exceeded bound")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise PilotError("Chrome identity response is not an object")
    return value, data


class CaptureTrace:
    def __init__(self, mode, directory):
        self.mode = mode
        self.directory = directory
        self.owner_thread = threading.get_ident()
        self.captures = []
        self.browsers = []
        self.errors = []
        self.validations = []
        self.operations = []
        self.protocol_failures = []
        self.browser_start_attempts = 0
        self.recovery_attempts = 0
        self.status = "unverified"
        self.started = time.monotonic()
        self.closed = None

    def fail(self, where, error):
        self.errors.append({"where": where, "type": type(error).__name__, "message": str(error)})
        self.status = "failed"

    def check_response(self, method, response, *, params):
        """CDP can return operation failure inside a successful JSON-RPC envelope."""
        reason = None
        if not isinstance(response, dict):
            reason = "CDP result must be an object"
        elif "exceptionDetails" in response:
            reason = "CDP operation returned exceptionDetails"
        elif method == "Runtime.evaluate":
            # Only this renderer's evaluated-value boundary is checked here,
            # not a generic CDP schema. Undefined and object/null are valid
            # RemoteObjects; a missing/non-object result is not either value.
            remote = response.get("result")
            if not isinstance(remote, dict):
                reason = "Runtime.evaluate result must be a RemoteObject"
            elif remote.get("type") not in ("object", "function", "undefined", "string",
                                             "number", "boolean", "symbol", "bigint"):
                reason = "Runtime.evaluate RemoteObject requires a valid type"
            elif "subtype" in remote and not isinstance(remote["subtype"], str):
                reason = "Runtime.evaluate RemoteObject subtype must be a string"
            elif remote.get("subtype") == "error":
                reason = "Runtime.evaluate returned an error RemoteObject"
        elif method == "Page.navigate" and "errorText" in response:
            reason = "Page.navigate returned errorText"
        elif method == "Page.navigate" and response.get("isDownload") is True:
            reason = "Page.navigate downloaded instead of displaying the requested page"
        if reason is not None:
            self.protocol_failures.append({"method": method, "params": copy.deepcopy(params),
                                           "response": copy.deepcopy(response), "reason": reason})
            error = PilotError(reason)
            self.fail(method + ".response", error)
            raise error
        return response

    def observe(self, operation, function, *args, details=None, **kwargs):
        """Retain complete-operation failures that production may catch/retry."""
        record = {"sequence": len(self.operations), "operation": operation,
                  "details": details or {}, "status": "pending"}
        self.operations.append(record)
        started = time.monotonic()
        try:
            result = function(*args, **kwargs)
            record["status"] = "returned"
            return result
        except BaseException as error:
            record.update(status="failed", error_type=type(error).__name__, error=str(error))
            self.fail(operation, error)
            raise
        finally:
            record["wall_s"] = time.monotonic() - started

    def assert_valid(self, stats, *, expected_frames):
        """Reject cache/Canvas/static/retry contamination, even after recovery."""
        reason = None
        paths = stats.get("path_counts", {})
        if type(expected_frames) is not int or expected_frames <= 0:
            reason = "expected_frames must be a positive integer"
        elif self.errors:
            reason = "operation/identity errors occurred, even if production retry recovered"
        elif self.browser_start_attempts != 1 or self.recovery_attempts or len(self.browsers) != 1:
            reason = "exactly one original browser start and zero recovery attempts required"
        elif not self.browsers or not self.captures:
            reason = "no observed Chrome screenshot work"
        elif stats.get("rendered") != expected_frames or stats.get("failed") != []:
            reason = "not every requested frame was newly rendered"
        elif stats.get("tabs") != 1 or stats.get("canvas_export") is not False:
            reason = "single-tab screenshot route required"
        elif any(paths.get(key, 0) for key in ("canvas", "canvas_fallback", "navfree", "static_copied", "failed")):
            reason = "non-screenshot, copied, fallback or failed output is not this experiment"
        elif sum(paths.get(key, 0) for key in ("override", "twoshot", "opaque")) != expected_frames:
            reason = "screenshot path counts do not cover requested frames"
        elif any(capture["status"] != "accepted" for capture in self.captures):
            reason = "some screenshot requests were not accepted"
        if reason:
            error = PilotError(reason)
            self.fail("validation", error)
            raise error
        self.validations.append({"expected_frames": expected_frames, "stats": copy.deepcopy(stats)})
        self.status = "request-and-render-contract-checked"

    def poll_observation_report(self):
        """Small persisted-file index; off intentionally has no poll samples."""
        observer = getattr(self, "poll_observer", None)
        if observer is None:
            from .poll_observer import disabled_observation
            return disabled_observation()
        return copy.deepcopy(observer.descriptor)

    def report(self):
        report = {
            "schema": "fast-png-pilot-v1", "mode": self.mode, "status": self.status,
            "base_commit": BASE_COMMIT, "base_tree": BASE_TREE,
            "renderer_sha256": _sha(Path(gfx.__file__).read_bytes()),
            "python_png_compress_level": gfx.PNG_COMPRESS_LEVEL,
            "adapter_sha256": _sha(Path(__file__).read_bytes()),
            "requested_capture_params": {"format": "png", **({"optimizeForSpeed": True} if self.mode == "fast" else {})},
            "browsers": self.browsers, "captures": self.captures,
            "errors": self.errors, "validations": self.validations,
            "operations": self.operations, "protocol_failures": self.protocol_failures,
            "browser_start_attempts": self.browser_start_attempts,
            "recovery_attempts": self.recovery_attempts,
            "capture_count": len(self.captures),
            "observed_base64_characters": sum(c.get("base64_characters", 0) for c in self.captures),
            "scope_wall_s": (self.closed or time.monotonic()) - self.started,
            "encoder_internal_effect": "unknown; schema advertisement and accepted request only",
            "quality_and_performance": "not established by this trace; compare retained PNGs and whole-arm wall separately",
        }
        if hasattr(self, "poll_observer"):
            report["poll_observation"] = self.poll_observation_report()
        return copy.deepcopy(report)


def _classes(trace, original_tab, original_browser, poll_observer=None):
    class ExperimentalTab(original_tab):
        # Complete original operations are observed, never reimplemented.
        def __init__(self, *args, **kwargs):
            self._pilot_url = None
            self._pilot_background = None
            trace.observe("Tab.__init__", super().__init__, *args, **kwargs)

        def navigate(self, *args, **kwargs):
            return trace.observe("Tab.navigate", super().navigate, *args,
                                 details={"url": str(args[0]) if args else kwargs.get("url")}, **kwargs)

        def eval(self, expression):
            # Includes the original response-value extraction, which callers
            # such as the static-span probe may catch outside Tab.call.
            return trace.observe("Tab.eval", super().eval, expression,
                                 details={"expression": expression})

        def shot(self):
            # Includes original base64 decoding, outside the narrower CDP call.
            return trace.observe("Tab.shot", super().shot,
                                 details={"navigation_url": self._pilot_url})

        def close(self):
            return trace.observe("Tab.close", super().close)

        def call(self, method, timeout=30, **params):
            if method != "Page.captureScreenshot":
                try:
                    result = super().call(method, timeout=timeout, **params)
                    trace.check_response(method, result, params=params)
                except BaseException as error:
                    trace.fail(method, error)
                    raise
                if method == "Page.navigate":
                    self._pilot_url = params.get("url")
                    self._pilot_background = None
                elif method == "Runtime.evaluate":
                    if params.get("expression") == gfx._BG_INJECT_WHITE:
                        self._pilot_background = "white-injection"
                    elif params.get("expression") == gfx._BG_INJECT_BLACK:
                        self._pilot_background = "black-injection"
                return result
            record = {"sequence": len(trace.captures), "navigation_url": self._pilot_url,
                      "injected_background": self._pilot_background,
                      "original_params": copy.deepcopy(params), "timeout_s": timeout,
                      "status": "pending"}
            trace.captures.append(record)
            start = time.monotonic()
            try:
                if trace.errors:
                    raise PilotError("arm already failed; no subsequent screenshot may count")
                if params != {"format": "png"} or timeout != 30:
                    raise PilotError("production screenshot contract changed")
                actual = dict(params)
                if trace.mode == "fast":
                    actual["optimizeForSpeed"] = True
                record["actual_params"] = actual
                result = super().call(method, timeout=timeout, **actual)
                trace.check_response(method, result, params=actual)
                if not isinstance(result, dict) or not isinstance(result.get("data"), str):
                    raise PilotError("screenshot response has no base64 data string")
                record["base64_characters"] = len(result["data"])
                record["status"] = "accepted"
                return result
            except BaseException as error:
                record["status"] = "failed"
                trace.fail("Page.captureScreenshot", error)
                raise
            finally:
                record["cdp_call_wall_s"] = time.monotonic() - start

    class ExperimentalBrowser(original_browser):
        def __init__(self, *args, **kwargs):
            trace.observe("Browser.__init__", self._pilot_init, *args,
                          details={"positional_argument_count": len(args)}, **kwargs)

        def _pilot_init(self, *args, **kwargs):
            if trace.errors:
                raise PilotError("arm already failed; a corrected constructor needs a new scope")
            super().__init__(*args, **kwargs)
            if self.tabs_n != 1 or threading.get_ident() != trace.owner_thread:
                raise PilotError("dedicated single-worker/one-tab process required")

        def open_tabs(self):
            return trace.observe("Browser.open_tabs", super().open_tabs)

        def stop(self):
            return trace.observe("Browser.stop", super().stop)

        def hard_restart(self):
            trace.recovery_attempts += 1
            trace.fail("Browser.hard_restart", PilotError(
                "production requested recovery after a failed frame attempt; arm is ineligible"))
            return trace.observe("Browser.hard_restart", super().hard_restart)

        def start(self):
            trace.browser_start_attempts += 1
            return trace.observe("Browser.start", self._pilot_start,
                                 details={"attempt": trace.browser_start_attempts})

        def _pilot_start(self):
            try:
                if trace.browser_start_attempts != 1:
                    raise PilotError("a repeated browser start cannot receive clean performance qualification")
                if trace.errors:
                    raise PilotError("arm already failed; no replacement browser may count")
                super().start()
                version, version_bytes = _read_browser_json(self.port, "version")
                protocol, protocol_bytes = _read_browser_json(self.port, "protocol")
                index = len(trace.browsers)
                (trace.directory / f"browser-{index}-version.json").write_bytes(version_bytes)
                (trace.directory / f"browser-{index}-protocol.json").write_bytes(protocol_bytes)
                supported = protocol_has_fast_png(protocol)
                identity = {"version": version, "version_sha256": _sha(version_bytes),
                            "protocol_sha256": _sha(protocol_bytes), "advertises_fast_png": supported,
                            "chrome_path": self.chrome, "generation": self.generation,
                            "width": self.width, "height": self.height, "tabs": self.tabs_n}
                trace.browsers.append(identity)
                if not isinstance(version.get("Browser"), str) or not version.get("Browser"):
                    raise PilotError("Chrome version identity is missing")
                if trace.mode == "fast" and not supported:
                    raise PilotError("this Chrome does not advertise optional boolean optimizeForSpeed; no fallback")
            except BaseException as error:
                trace.fail("browser-start-or-capability", error)
                raise

    if poll_observer is None:
        return ExperimentalTab, ExperimentalBrowser

    # Off uses the original methods above: no poll clocks, frame inspection,
    # records, or extra call dispatch. Only the opt-in subclass pays this cost.
    import sys
    from .poll_observer import POLL_EXPRESSION
    original_call = original_tab.call

    class PollObservedTab(ExperimentalTab):
        def navigate(self, *args, **kwargs):
            navigation = None
            caller = None
            try:
                caller = sys._getframe(1)
                navigation = poll_observer.safely("nav_enter", self,
                    args[0] if args else kwargs.get("url"), caller)
            except BaseException as exc:
                poll_observer.error("caller_frame", exc)
            finally:
                del caller
            previous = getattr(self, "_poll_navigation", None)
            self._poll_navigation = navigation
            error = None
            try:
                return super().navigate(*args, **kwargs)
            except BaseException as exc:
                error = exc
                raise
            finally:
                poll_observer.safely("nav_exit", navigation, error)
                self._poll_navigation = previous

        def call(self, method, timeout=30, **params):
            navigation = getattr(self, "_poll_navigation", None)
            if navigation is None or method != "Runtime.evaluate" or params.get("expression") != POLL_EXPRESSION:
                return super().call(method, timeout=timeout, **params)
            record = poll_observer.safely("poll_enter", navigation)
            result, error = None, None
            try:
                # The very same original receiver owns transport, Fetch handling,
                # timeout and JSON decode. check_response remains outside its wall.
                try:
                    poll_observer.safely("call_enter", record)
                    result = original_call(self, method, timeout=timeout, **params)
                except BaseException as exc:
                    poll_observer.safely("call_exit", record, exc)
                    raise
                else:
                    poll_observer.safely("call_exit", record, None)
                trace.check_response(method, result, params=params)
                return result
            except BaseException as exc:
                error = exc
                trace.fail(method, exc)
                raise
            finally:
                poll_observer.safely("poll_exit", navigation, record, result, error)

    class PollObservedBrowser(ExperimentalBrowser):
        def open_tabs(self):
            tabs = super().open_tabs()
            poll_observer.safely("bind_tabs", tabs, self.generation)
            return tabs

    return PollObservedTab, PollObservedBrowser


@contextlib.contextmanager
def capture_scope(mode, evidence_dir, *, poll_observation=False, job_context=None):
    """Temporarily adapt only b287 CDP requests in a dedicated child process.

    evidence_dir must not exist. Caller owns exact input/output identities, empty
    output enforcement, deadline/cancellation, output verification and A/B pairing.
    Must call trace.assert_valid(stats, expected_frames=N) after each render_page.
    """
    if type(poll_observation) is not bool:
        raise ValueError("poll_observation must be boolean")
    if poll_observation and mode != "baseline":
        raise ValueError("poll observation requires baseline PNG")
    if mode not in ("baseline", "fast"):
        raise ValueError("mode must be baseline or fast")
    if gfx.os.environ.get("VTX_GFX_NAVFREE", "0") != "0":
        raise PilotError("NAVFREE must remain disabled")
    if _sha(Path(gfx.__file__).read_bytes()) != RENDERER_SHA256:
        raise PilotError("renderer is not the reviewed b287 source")
    if not _SCOPE_LOCK.acquire(blocking=False):
        raise PilotError("nested/concurrent fast-PNG capture scopes are forbidden")
    original_tab, original_browser = gfx.Tab, gfx.Browser
    original_functions = {name: getattr(gfx, name) for name in
                          ("_atomic_write_bytes", "_two_shot_combine")}
    trace = None
    observer = None
    primary_error = None
    try:
        directory = Path(evidence_dir)
        directory.mkdir(parents=True, exist_ok=False)
        trace = CaptureTrace(mode, directory)
        if poll_observation:
            from .poll_observer import PollObserver
            observer = PollObserver(gfx, directory, job_context)
            trace.poll_observer = observer
        gfx.Tab, gfx.Browser = _classes(trace, original_tab, original_browser, observer)
        installed_tab, installed_browser = gfx.Tab, gfx.Browser
        def observed_function(name, original):
            def observed(*args, **kwargs):
                details = {"path": str(args[0])} if name == "_atomic_write_bytes" and args else {}
                return trace.observe("gfx." + name, original, *args, details=details, **kwargs)
            return observed
        for name, original in original_functions.items():
            setattr(gfx, name, observed_function(name, original))
        installed_functions = {name: getattr(gfx, name) for name in original_functions}
        try:
            yield trace
            if not trace.validations or trace.errors:
                raise PilotError("scope ended without a clean render-contract validation")
        except BaseException as error:
            primary_error = error
            trace.fail("scope", error)
            raise
        finally:
            if observer is not None:
                observer.restoration["installed_references_intact"] = (
                    gfx.Tab is installed_tab and gfx.Browser is installed_browser and
                    all(getattr(gfx, name) is installed for name, installed in installed_functions.items()))
            gfx.Tab, gfx.Browser = original_tab, original_browser
            for name, original in original_functions.items():
                setattr(gfx, name, original)
            trace.closed = time.monotonic()
            observation_failure = None
            if observer is not None:
                observer.restoration["original_references_restored"] = (
                    gfx.Tab is original_tab and gfx.Browser is original_browser and
                    all(getattr(gfx, name) is original for name, original in original_functions.items()))
                descriptor = observer.safely("persist", trace, primary_error)
                if descriptor is None:
                    observer.descriptor.update(status="FAILED", qualification="INELIGIBLE",
                                               persistence="MISSING", path=None, bytes=None, sha256=None)
                    descriptor = observer.descriptor
                if descriptor["status"] != "COMPLETE":
                    observation_failure = PilotError("poll observation is " + descriptor["status"])
                    trace.fail("poll_observation", observation_failure)
            try:
                _write_json(directory / "trace.json", trace.report())
            except BaseException as error:
                if observer is None or primary_error is None:
                    raise
                observer.error("trace_persistence", error)
                observer.descriptor["trace_persistence"] = "MISSING"
            if observation_failure is not None and primary_error is None:
                raise observation_failure
    finally:
        gfx.Tab, gfx.Browser = original_tab, original_browser
        for name, original in original_functions.items():
            setattr(gfx, name, original)
        _SCOPE_LOCK.release()


def compare_png_files(baseline, candidate, *, width=1920, height=1080):
    """Validate and compare every decoded RGBA byte, including RGB under alpha=0.

    Retains both original PNG files; does not re-encode, normalize or replace them.
    File-byte equality is reported separately and is not the pixel-quality gate.
    """
    import numpy as np
    rows = []
    pixels = []
    for path in (Path(baseline), Path(candidate)):
        fingerprints, properties = [], {}
        if path.stat().st_size > 32 * 1024 * 1024:
            raise PilotError("PNG exceeds the pilot compressed-byte bound")
        reason = gfx.png_invalid_reason(path, width=width, height=height,
                                        _fingerprint=fingerprints, _properties=properties)
        if reason:
            raise PilotError(f"invalid retained PNG {path}: {reason}")
        data = path.read_bytes()
        if fingerprints != [_sha(data)]:
            raise PilotError("PNG changed between validation and comparison")
        if (properties.get("depth") != 8 or properties.get("color") not in (2, 6)
                or properties.get("animated") or properties.get("transparency")
                or properties.get("unsafe_unknown")):
            raise PilotError("pilot requires single 8-bit RGB/RGBA PNG without tRNS or unsupported ancillary chunks")
        rgba = gfx.png_decode_rgba(data)
        if rgba.shape != (height, width, 4) or rgba.dtype != np.uint8:
            raise PilotError("unexpected decoded RGBA shape/type")
        pixels.append(rgba)
        rows.append({"path": str(path), "png_bytes": len(data), "png_sha256": _sha(data),
                     "rgba_sha256": _sha(rgba.tobytes()), "alpha_sha256": _sha(rgba[:, :, 3].tobytes())})
    delta = pixels[0] != pixels[1]
    return {"baseline": rows[0], "candidate": rows[1], "width": width, "height": height,
            "png_bytes_equal": rows[0]["png_sha256"] == rows[1]["png_sha256"],
            "rgba_equal": not bool(delta.any()), "alpha_equal": not bool(delta[:, :, 3].any()),
            "different_rgba_components": int(np.count_nonzero(delta)),
            "different_pixels": int(np.count_nonzero(delta.any(axis=2))),
            "different_alpha_pixels": int(np.count_nonzero(delta[:, :, 3]))}
