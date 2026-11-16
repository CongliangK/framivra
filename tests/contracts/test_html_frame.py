"""Portable HTML-preview contract tests; native browser is mocked, not certified."""
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from vtx.api import frame_timeline, timeline_capabilities
from vtx.timeline_html_frame import _IDENTITY, _read_regular

ADDRESS = "/video_tracks/0/clips/0"
REPO = Path(__file__).resolve().parents[2]


def png(width=1920, height=1080):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data +
                struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress((b"\0" + b"\0" * width * 4) * height)) + chunk(b"IEND", b""))


class HtmlFrame(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "timeline.json"
        (self.root / "gfx").mkdir()
        self.page = self.root / "gfx" / "page.html"
        self.page.write_text("<html><script>/* trusted */</script></html>")
        self.doc = {"version": 2, "project": {"fps": 30, "width": 1920, "height": 1080,
                                             "duration_frames": 80},
                    "sources": [{"id": "missing", "path": "not-present.mp4"}],
                    "video_tracks": [{"name": "page", "kind": "gfx", "clips": [
                        {"src": "gfx/page.html", "dst_in": 20, "dur": 40}]}],
                    "audio_tracks": [], "subtitles": []}
        self.save()
        self.data = png()

    def save(self):
        self.path.write_text(json.dumps(self.doc))
        self.version = "sha256:" + hashlib.sha256(self.path.read_bytes()).hexdigest()

    def call(self, **kwargs):
        args = dict(frame=27, render=True, trust_html=True,
                    html_block_address=ADDRESS, expected_content_version=self.version)
        args.update(kwargs)
        return frame_timeline(self.path, **args)

    def fake_render(self, page, outdir, **kwargs):
        self.render_args = (page, outdir, kwargs)
        print("renderer progress is not JSON")
        directory = Path(outdir)
        directory.mkdir(parents=True)
        (directory / "f0007.png").write_bytes(self.data)
        return {"failed": []}

    @contextlib.contextmanager
    def renderer(self, effect=None):
        with patch("vtx.runtime.proc_supervisor.supervision_available", return_value=True), \
                patch("vtx.timeline_html_frame._browser", return_value=("/mock/chrome", {"backend": "cdp"})), \
                patch("vtx.render.gfx_render.render_page", side_effect=effect or self.fake_render) as render:
            yield render

    def test_authorization_is_zero_asset_read_or_write(self):
        with patch("vtx.timeline_html_frame._render", side_effect=AssertionError("render")), \
                patch("vtx.timeline_html_frame._snapshot", side_effect=AssertionError("assets")), \
                patch("vtx.pipeline.infra.Ctx.__init__", side_effect=AssertionError("Ctx")):
            for kwargs in ({"render": False}, {"trust_html": False}, {"trust_html": "yes"},
                           {"expected_content_version": None}, {"expected_content_version": "stale"},
                           {"frame": 0}, {"frame": 60}, {"max_png_bytes": 0},
                           {"html_block_address": "/video_tracks/00/clips/0"},
                           {"html_block_address": "/audio_tracks/0/events/0"}):
                with self.subTest(kwargs=kwargs):
                    result = self.call(**kwargs)
                    self.assertFalse(result["ok"])
                    self.assertIsNone(result["image"])
        self.assertFalse((self.root / "cache").exists())

    def test_sparse_native_arguments_and_standard_identity_inline(self):
        stdout = io.StringIO()
        with self.renderer() as render, contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(io.StringIO()), \
                patch("vtx.pipeline.infra.Ctx.__init__", side_effect=AssertionError("Ctx")), \
                patch("vtx.pluginlib.hooks.active_plugins", side_effect=AssertionError("plugins")):
            result = self.call(include_png=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(render.call_count, 1)
        page, output, args = self.render_args
        self.assertEqual(page, str(self.page))
        self.assertEqual({k: args[k] for k in ("frames", "tabs", "n_frames", "width", "height")},
                         dict(frames=[7], tabs=1, n_frames=40, width=1920, height=1080))
        self.assertEqual(args["ctx"].root, self.root)
        self.assertIn(self.root / "cache" / "preview", args["ctx"].cache.parents)
        image = result["image"]
        self.assertEqual(image["layer"], dict(block_address=ADDRESS, local_frame=7,
                                             duration_frames=40, html="gfx/page.html"))
        self.assertEqual(image["kind"], "html-layer")
        self.assertFalse(image["approximate"])
        self.assertFalse(image["cache_hit"])
        self.assertEqual(image["identity_semantics"], _IDENTITY)
        self.assertEqual(image["sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(base64.b64decode(image["inline"]["data"]), self.data)
        self.assertEqual((self.root / image["path"]).read_bytes(), self.data)
        self.assertRegex(image["render_key"], "^[0-9a-f]{64}$")
        self.assertRegex(image["input_fingerprint"], "^[0-9a-f]{64}$")
        self.assertEqual(list((self.root / image["path"]).parent.iterdir()), [self.root / image["path"]])

    def test_never_reuses_image_or_full_gfx_marker(self):
        with self.renderer() as render, contextlib.redirect_stderr(io.StringIO()):
            first = self.call()
            second = self.call()
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(render.call_count, 2)
        self.assertNotEqual(first["image"]["path"], second["image"]["path"])
        self.assertEqual(first["image"]["render_key"], second["image"]["render_key"])
        self.assertEqual(list((self.root / "cache").iterdir()), [self.root / "cache" / "preview"])
        self.assertFalse(list(self.root.rglob("*complete*")))

    def test_unsupported_supervision_fails_without_writes(self):
        with patch("vtx.runtime.proc_supervisor.supervision_available", return_value=False):
            result = self.call()
        self.assertEqual(result["diagnostics"][0]["code"], "platform.unsupported")
        self.assertFalse((self.root / "cache").exists())

    def test_wrong_target_strict_dimensions_and_paths(self):
        for change in ("native", "svg", "strict", "size", "float_size", "escape", "extension"):
            with self.subTest(change=change):
                doc = json.loads(json.dumps(self.doc))
                if change == "native":
                    doc["video_tracks"][0].update(kind="video")
                    doc["video_tracks"][0]["clips"][0]["src"] = "missing"
                elif change == "svg":
                    doc["video_tracks"][0]["clips"][0]["format"] = "svg-sequence"
                elif change == "strict":
                    doc["project"]["gfx_dependency_policy"] = "strict"
                elif change in ("size", "float_size"):
                    doc["project"]["width"] = 4 if change == "size" else 1920.0
                else:
                    doc["video_tracks"][0]["clips"][0]["src"] = "../page.html" if change == "escape" else "timeline.json"
                self.path.write_text(json.dumps(doc))
                self.version = "sha256:" + hashlib.sha256(self.path.read_bytes()).hexdigest()
                with self.renderer() as render:
                    result = self.call()
                self.assertFalse(result["ok"], result)
                render.assert_not_called()
                self.assertFalse((self.root / "cache").exists())

    def test_mutations_discard_image(self):
        dep = self.root / "style.css"
        dep.write_text("before")
        self.doc["project"]["cache_dependencies"] = ["style.css"]
        self.save()
        for target in (self.path, self.page, dep):
            with self.subTest(target=target):
                original = target.read_bytes()
                def mutate(*args, **kwargs):
                    stats = self.fake_render(*args, **kwargs)
                    target.write_bytes(original + b" ")
                    return stats
                with self.renderer(mutate), contextlib.redirect_stderr(io.StringIO()):
                    result = self.call()
                self.assertFalse(result["ok"], result)
                self.assertIsNone(result["image"])
                self.assertEqual(result["diagnostics"][0]["code"], "snapshot.changed")
                self.assertFalse(list((self.root / "cache" / "preview").iterdir()))
                target.write_bytes(original)

    def test_bad_png_and_requested_byte_limit(self):
        for data, kwargs in ((b"not png", {}), (png(1, 1), {}), (self.data, {"max_png_bytes": 10})):
            self.data = data
            with self.renderer(), contextlib.redirect_stderr(io.StringIO()):
                result = self.call(**kwargs)
            self.assertFalse(result["ok"], result)
            self.assertIsNone(result["image"])
            self.assertFalse(list((self.root / "cache" / "preview").iterdir()))

    def test_dependency_limits_and_nonregular(self):
        self.doc["project"]["cache_dependencies"] = ["gfx"]
        self.save()
        with self.renderer() as render:
            self.assertFalse(self.call()["ok"])
            render.assert_not_called()
        self.doc["project"]["cache_dependencies"] = ["timeline.json"] * 1025
        self.save()
        with self.renderer() as render:
            self.assertFalse(self.call()["ok"])
            render.assert_not_called()
        with self.assertRaises(ValueError):
            _read_regular(self.page, 1)
        self.assertFalse((self.root / "cache").exists())

    def test_root_escape_symlink_and_bounded_snapshots(self):
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external) / "outside.html"
            outside.write_text("outside")
            self.page.unlink()
            self.page.symlink_to(outside)
            with self.renderer() as render:
                result = self.call()
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["diagnostics"][0]["code"], "inputs.path")
            render.assert_not_called()
        self.page.unlink()
        self.page.write_text("page bytes")
        from vtx import timeline_html_frame as html
        with patch.object(html, "_INPUT_LIMIT", 2), self.renderer() as render:
            self.assertFalse(self.call()["ok"])
            render.assert_not_called()
        with patch.object(html, "_TOTAL_LIMIT", 2), self.renderer() as render:
            self.assertFalse(self.call()["ok"])
            render.assert_not_called()
        self.assertFalse((self.root / "cache").exists())

    def test_declared_content_changes_identity(self):
        dependency = self.root / "style.css"
        dependency.write_text("first")
        self.doc["video_tracks"][0]["clips"][0]["cache_dependencies"] = ["style.css"]
        self.save()
        with self.renderer(), contextlib.redirect_stderr(io.StringIO()):
            first = self.call()
            dependency.write_text("other")
            second = self.call()
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(first["content_version"], second["content_version"])
        self.assertNotEqual(first["image"]["input_fingerprint"], second["image"]["input_fingerprint"])
        self.assertNotEqual(first["image"]["render_key"], second["image"]["render_key"])

    def test_native_failure_no_fallback_and_cancel_cleanup(self):
        for outcome in (KeyboardInterrupt(), RuntimeError("native failed"), {"failed": [7]}):
            with self.renderer() as render:
                render.side_effect = outcome if isinstance(outcome, BaseException) else None
                render.return_value = outcome
                result = self.call()
            self.assertFalse(result["ok"], result)
            self.assertIsNone(result["image"])
            self.assertFalse(list((self.root / "cache" / "preview").iterdir()))

    def test_unsettled_ledger_preserved(self):
        def unsettled(*args, **kwargs):
            stats = self.fake_render(*args, **kwargs)
            ledger = kwargs["ctx"].cache / "tmp" / "worker" / ".worker_pgid.json"
            ledger.parent.mkdir(parents=True)
            ledger.write_text('{"unsettled":true}')
            return stats
        with self.renderer(unsettled), contextlib.redirect_stderr(io.StringIO()):
            result = self.call()
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["diagnostics"][0]["code"], "render.unsettled")
        self.assertEqual(len(list(self.root.rglob(".worker_pgid.json"))), 1)
        self.assertFalse(list(self.root.rglob("image.png")))

    def test_capability_and_cli_foreign_flags(self):
        self.assertEqual(timeline_capabilities()["capabilities"]["frame"]["html_layer"],
                         dict(flag="--html-layer", trust_flag="--trust-html", kind="html-layer", saved_block_only=True))
        base = [sys.executable, "-m", "vtx", "-t", str(self.path)]
        for command in ("inspect", "query", "capabilities", "validate", "store"):
            for flags in (["--html-layer", ADDRESS], ["--trust-html"]):
                proc = subprocess.run(base + [command] + flags, cwd=REPO, capture_output=True, text=True)
                self.assertEqual(proc.returncode, 2, (command, flags, proc.stdout, proc.stderr))
        cmd = base + ["timeline", "frame", "--frame", "27", "--html-layer", ADDRESS,
                      "--expected-content-version", self.version, "--render"]
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), self.call(trust_html=False))
        proc = subprocess.run(base + ["timeline", "frame", "--frame", "27", "--trust-html"],
                              cwd=REPO, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertFalse((self.root / "cache").exists())


if __name__ == "__main__":
    unittest.main()
