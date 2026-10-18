"""Experimental screenshot parameter contract; no Chrome required."""
import base64
import unittest
from unittest.mock import patch
from vtx import gfx_render as gfx


class FastPNG(unittest.TestCase):
    def make(self, fast=False, error=None):
        calls = []
        def call(tab, method, **params):
            calls.append((method, params))
            if method == "Page.captureScreenshot" and error and params.get("optimizeForSpeed"):
                raise error
            return {"data": base64.b64encode(b"png").decode()}
        with patch.object(gfx, "WSConn"), patch.object(gfx.Tab, "call", call):
            tab = gfx.Tab(1, "/", fast_png=fast)
            self.assertEqual(tab.shot(), b"png")
        return tab, calls

    def test_default_has_no_probe_or_parameter(self):
        tab, calls = self.make()
        shots = [p for m, p in calls if m == "Page.captureScreenshot"]
        self.assertEqual(shots, [{"format": "png", "timeout": 30}])
        self.assertIsNone(tab.fast_png_supported)

    def test_enabled_probes_and_uses_parameter(self):
        tab, calls = self.make(True)
        self.assertTrue(tab.fast_png_active)
        self.assertTrue(all(p["optimizeForSpeed"] for m,p in calls if m == "Page.captureScreenshot"))

    def test_explicit_unsupported_warns_once_and_downgrades(self):
        with patch.object(gfx, "_FAST_PNG_WARNED", False), patch("sys.stderr") as stderr:
            for _ in range(2):
                tab, calls = self.make(True, RuntimeError("Unknown parameter optimizeForSpeed"))
                self.assertFalse(tab.fast_png_active)
                self.assertNotIn("optimizeForSpeed", calls[-1][1])
            self.assertEqual(sum("explicitly rejects" in str(c) for c in stderr.write.call_args_list), 1)

    def test_other_errors_propagate(self):
        for error in (RuntimeError("captureScreenshot renderer crashed"),
                      RuntimeError("Invalid parameters"), TimeoutError("timeout"),
                      ConnectionError("socket")):
            with self.subTest(error=error), self.assertRaises(type(error)):
                self.make(True, error)

    def test_real_shot_error_is_not_downgraded(self):
        tab, _ = self.make(True)
        with patch.object(tab, "call", side_effect=RuntimeError("renderer crashed")):
            with self.assertRaisesRegex(RuntimeError, "renderer crashed"):
                tab.shot()
        self.assertTrue(tab.fast_png_active)

    def test_browser_propagates_only_explicit_opt_in(self):
        for enabled in (False,True):
            browser=gfx.Browser(fast_png=enabled)
            browser.port=123;browser.tab_ws_urls=["/a","/b"]
            with patch.object(gfx,"Tab") as tab:
                browser.open_tabs()
            self.assertEqual(tab.call_count,2)
            for call in tab.call_args_list:
                self.assertEqual(call.kwargs, {"fast_png":True} if enabled else {})
