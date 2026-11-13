"""Public frame authorization, snapshot/cache negatives and real CPU pixels.

Integrations require VTX_TEST_FFMPEG=1; all projects are external temporary dirs.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from vtx.api import frame_timeline
from vtx.pipeline.infra import Ctx
from vtx.pipeline.stages import validate, validate_core

REPO = Path(__file__).resolve().parents[2]


def document():
    return {"version": 2, "project": {"fps": 30, "width": 1920, "height": 1080,
                                      "duration_frames": 12},
            "sources": [{"id": "s", "path": "source.mp4"}],
            "video_tracks": [], "audio_tracks": [], "subtitles": [],
            "unknown": {"exact": "原文"}}


class FrameReadOnly(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "timeline.json"
        self.path.write_text(json.dumps(document(), ensure_ascii=False), encoding="utf-8")

    def test_default_and_invalid_never_construct_renderer_or_probe(self):
        raw = self.path.read_bytes()
        with patch("vtx.pipeline.infra.Ctx.__init__", side_effect=AssertionError("Ctx")), \
                patch("subprocess.run", side_effect=AssertionError("process")):
            for kwargs in ({"frame": 1}, {"frame": True}, {"frame": 1.0},
                           {"frame": -1}, {"frame": 12}, {"frame": None},
                           {"frame": 1, "render": "true"},
                           {"frame": 1, "max_png_bytes": 0},
                           {"frame": 1, "render": True, "expected_content_version": "stale"}):
                result = frame_timeline(self.path, **kwargs)
                self.assertFalse(result["ok"], kwargs)
                self.assertIsNone(result["image"])
        self.assertEqual(list(self.root.iterdir()), [self.path])
        self.assertEqual(raw, self.path.read_bytes())
        result = frame_timeline(self.path, frame=1)
        self.assertEqual(result["reference"]["document"]["raw"].encode(), raw)
        self.assertEqual(result["content_version"], "sha256:" + hashlib.sha256(raw).hexdigest())

    def test_cli_python_equal_and_foreign_flags_refused(self):
        cmd = [sys.executable, "-m", "vtx", "-t", str(self.path), "timeline", "frame", "--frame", "1"]
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), frame_timeline(self.path, frame=1))
        for extra in (["--dry-run"], ["--events-jsonl", str(self.root / "events")], ["--json", "bad"]):
            proc = subprocess.run(cmd + extra, cwd=REPO, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["diagnostics"][0]["code"], "cli.arguments")
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_cli_signal_handler_is_scoped_and_restored(self):
        import signal
        from vtx.cli.timeline_frame import _cancel_on_sigterm
        previous = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(KeyboardInterrupt):
            with _cancel_on_sigterm():
                self.assertNotEqual(signal.getsignal(signal.SIGTERM), previous)
                raise KeyboardInterrupt
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)
        frame_timeline(self.path, frame=1)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_active_plugin_rejected_before_ctx(self):
        with patch("vtx.pluginlib.hooks.active_plugins", return_value=[{"name": "unsafe"}]), \
                patch("vtx.pipeline.infra.Ctx.__init__", side_effect=AssertionError("Ctx")):
            result = frame_timeline(self.path, frame=1, render=True)
        self.assertEqual(result["diagnostics"][0]["code"], "plugins.unsupported")
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_platform_rejects_before_write_but_default_reference_works(self):
        with patch("vtx.timeline_frame.sys.platform", "win32"):
            self.assertEqual(frame_timeline(self.path, frame=1, render=True)["diagnostics"][0]["code"], "platform.unsupported")
            self.assertEqual(frame_timeline(self.path, frame=1)["diagnostics"][0]["code"], "render.not_authorized")
        self.assertEqual(list(self.root.iterdir()), [self.path])


@unittest.skipUnless(os.environ.get("VTX_TEST_FFMPEG") and shutil.which("ffmpeg"),
                     "set VTX_TEST_FFMPEG to ffmpeg executable for real CPU pixels")
class FramePixels(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"VTX_NO_NVENC": "1", "VTX_SEG_NVENC": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "timeline.json"
        self.config = self.root / "config.json"
        self.config.write_text('{"cache":{"mode":"aggressive"}}')
        self.config_env = patch.dict(os.environ, {"VTX_CONFIG": str(self.config)})
        self.config_env.start()
        self.addCleanup(self.config_env.stop)
        self.doc = document()
        proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "color=c=blue:s=1920x1080:r=30:d=0.4", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-frames:v", "12", "-shortest", "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(self.root / "source.mp4")], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.doc, ensure_ascii=False), encoding="utf-8")

    def prepare(self):
        from vtx.pipeline.composite import prepare_layers
        with contextlib.redirect_stdout(io.StringIO()):
            prepare_layers(Ctx(self.path))

    def pixels(self, image):
        proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.root / image["path"]),
                               "-f", "rawvideo", "-pix_fmt", "rgb24", "-threads", "1", "-"], capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(proc.stdout), 1920 * 1080 * 3)
        return proc.stdout

    def test_black_actual_pixels_cache_corruption_and_byte_limit(self):
        first = frame_timeline(self.path, frame=1, render=True)
        self.assertTrue(first["ok"], first["diagnostics"])
        self.assertEqual(set(self.pixels(first["image"])), {0})
        second = frame_timeline(self.path, frame=1, render=True)
        self.assertTrue(second["image"]["cache_hit"])
        self.assertEqual(first["image"]["render_key"], second["image"]["render_key"])
        image = self.root / first["image"]["path"]
        image.write_bytes(b"truncated")
        repaired = frame_timeline(self.path, frame=1, render=True)
        self.assertTrue(repaired["ok"], repaired["diagnostics"])
        self.assertFalse(repaired["image"]["cache_hit"])
        small = frame_timeline(self.path, frame=2, render=True, max_png_bytes=10)
        self.assertFalse(small["ok"])
        self.assertFalse(list((self.root / "cache" / "preview").glob("*/complete.json")) == [])
        # Only the successful key has a success marker; oversized key has none.
        self.assertEqual(len(list((self.root / "cache" / "preview").glob("*/complete.json"))), 1)
        self.assertFalse((self.root / "out").exists())
        self.assertFalse((self.root / "cache" / "seg").exists())

    def test_ass_and_actual_filter_thread_option_invalidate(self):
        first = frame_timeline(self.path, frame=1, render=True)
        self.assertTrue(first["ok"], first["diagnostics"])
        with patch.dict(os.environ, {"VTX_FILTER_THREADS": "2"}):
            option = frame_timeline(self.path, frame=1, render=True)
        self.assertTrue(option["ok"], option["diagnostics"])
        self.assertNotEqual(option["image"]["render_key"], first["image"]["render_key"])
        self.doc["subtitles"] = [{"in": 0, "out": 2, "text": "ACTUAL ASS"}]
        self.save()
        ass = frame_timeline(self.path, frame=1, render=True)
        self.assertTrue(ass["ok"], ass["diagnostics"])
        self.assertNotEqual(ass["image"]["render_key"], first["image"]["render_key"])
        self.assertNotEqual(ass["image"]["sha256"], first["image"]["sha256"])
        self.assertEqual(ass["image"]["content_version"], ass["reference"]["content_version"])

    def test_missing_native_seg_is_prepared_only_in_preview_never_uses_decoy_final(self):
        self.doc["video_tracks"] = [{"name": "base", "kind": "aroll", "clips": [
            {"src": "s", "src_in": 0, "src_out": 12, "dst_in": 0, "dur": 12}]}]
        self.save()
        (self.root / "out").mkdir()
        (self.root / "out" / "final.mp4").write_bytes(b"decoy")
        result = frame_timeline(self.path, frame=3, render=True)
        self.assertTrue(result["ok"], result["diagnostics"])
        self.assertFalse(result["image"]["cache_hit"])
        self.assertFalse((self.root / "cache" / "seg").exists())
        self.assertFalse(list((self.root / "cache" / "preview").rglob("*.mp4")))
        self.assertFalse(list((self.root / "cache" / "preview").rglob("*.ass")))
        self.assertFalse(list((self.root / "cache" / "preview").glob("*/inputs_*")))
        self.assertEqual((self.root / "out" / "final.mp4").read_bytes(), b"decoy")

    def test_core_off_keep_exact_native_encoder_pixels_without_reusing_preview_or_media(self):
        self.doc['video_tracks'] = [
            {'name': 'base', 'kind': 'aroll', 'clips': [
                {'src': 's', 'src_in': 0, 'src_out': 12, 'dst_in': 0, 'dur': 12}]},
            {'name': 'window', 'kind': 'pip', 'clips': [
                {'src': 's', 'src_in': 1, 'src_out': 10, 'dst_in': 2, 'dur': 9,
                 'scale': 0.25, 'x': 100, 'y': 80, 'opacity': 0.5,
                 'fade_in_frames': 3, 'fade_out_frames': 3}]}]
        self.save()
        self.prepare()
        warm = frame_timeline(self.path, frame=3, render=True)
        self.assertTrue(warm['ok'], warm['diagnostics'])
        expected = self.pixels(warm['image'])
        canonical = {str(p.relative_to(self.root)): p.read_bytes()
                     for p in (self.root / 'cache' / 'seg').rglob('*') if p.is_file()}
        for mode in ('core', 'off'):
            self.config.write_text(json.dumps({'cache': {'mode': mode}}))
            with patch('vtx.media.subs._layer_ready', side_effect=AssertionError('old seg read')):
                first = frame_timeline(self.path, frame=3, render=True)
                second = frame_timeline(self.path, frame=3, render=True)
            for result in (first, second):
                self.assertTrue(result['ok'], result['diagnostics'])
                self.assertFalse(result['image']['cache_hit'])
                self.assertEqual(self.pixels(result['image']), expected)
                self.assertTrue((self.root / result['image']['path']).is_file())
            self.assertNotEqual(first['image']['path'], second['image']['path'])
            self.assertEqual(first['image']['render_key'], second['image']['render_key'])
        after = {str(p.relative_to(self.root)): p.read_bytes()
                 for p in (self.root / 'cache' / 'seg').rglob('*') if p.is_file()}
        self.assertEqual(after, canonical)
        preview = self.root / 'cache' / 'preview'
        self.assertFalse(list(preview.rglob('*.mp4')))
        self.assertFalse(list(preview.rglob('*.mov')))
        self.assertFalse(list(preview.rglob('*.ass')))
        self.assertFalse(list(preview.glob('*/inputs_*')))
        self.assertFalse((self.root / 'out').exists())

    def test_core_packed_gfx_matches_authenticated_bare_pixels_without_producer(self):
        # Synthetic producer bytes/certificate, REAL reader + compositor. This is
        # not browser/environment acceptance or a performance baseline.
        from tests.gfx._environment import bind_environment
        from tests.gfx._png import write_png
        from vtx.cache.hygiene import gfx_dir
        from vtx.cache.session import pack_path
        from vtx.render import gfx_cli, gfx_environment, gfx_pack
        bind_environment(self)
        self.config.write_text('{"cache":{"mode":"core"}}')
        (self.root / 'page.html').write_text('<html><body>fixture</body></html>')
        self.doc['video_tracks'] = [{'name': 'gfx', 'kind': 'gfx', 'clips': [
            {'src': 'page.html', 'dst_in': 0, 'dur': 3, 'scale': 0.5,
             'x': 100, 'y': 100, 'fade_in_frames': 3}]}]
        self.save()
        with patch.dict(os.environ, {'VTX_GFX': 'cdp', 'VTX_GFX_CACHE_NS': ''}), \
                patch('vtx.render.gfx_cli.render_cli', side_effect=AssertionError('no graphics producer')):
            c = Ctx(self.path)
            clip = c.tl['video_tracks'][0]['clips'][0]
            directory = gfx_dir(c, clip)
            directory.mkdir(parents=True)
            for i in range(3):
                write_png(directory / f'f{i:04d}.png', width=c.w, height=c.h, frame=i)
            gfx_cli.complete_page(directory, 3, directory.name, c.w, c.h,
                                 verify_inputs=lambda: c.verify_inputs(None),
                                 environment=gfx_environment.certificate(c))
            bare = frame_timeline(self.path, frame=2, render=True)
            self.assertTrue(bare['ok'], bare['diagnostics'])
            record = json.loads((directory / 'complete.json').read_bytes())
            archive = pack_path(c, clip)
            archive.parent.mkdir(parents=True)
            gfx_pack.pack_page(directory, archive, expected_record=record, root=self.root)
            for name in ['complete.json', *(f'f{i:04d}.png' for i in range(3))]:
                (directory / name).unlink()
            before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*')
                      if p.is_file() and 'preview' not in p.parts}
            packed = frame_timeline(self.path, frame=2, render=True)
            self.assertTrue(packed['ok'], packed['diagnostics'])
            self.assertFalse(packed['image']['cache_hit'])
            self.assertEqual(packed['image']['render_key'], bare['image']['render_key'])
            self.assertEqual(self.pixels(packed['image']), self.pixels(bare['image']))
            after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*')
                     if p.is_file() and 'preview' not in p.parts}
            self.assertEqual(before, after)
            self.assertFalse(list(directory.glob('f*.png')))
            self.assertFalse(list((self.root / 'cache' / 'preview').glob('*/inputs_*')))

    def test_off_visible_gfx_refuses_even_existing_preview_without_browser_or_writes(self):
        self.config.write_text('{"cache":{"mode":"off"}}')
        (self.root / 'page.html').write_text('<html>cold, never execute</html>')
        self.doc['video_tracks'] = [{'name': 'gfx', 'kind': 'gfx', 'clips': [
            {'src': 'page.html', 'dst_in': 0, 'dur': 4}]}]
        self.save()
        with patch('vtx.render.gfx_packed_reader.read_frame', side_effect=AssertionError('old gfx read')):
            result = frame_timeline(self.path, frame=1, render=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['diagnostics'][0]['code'], 'inputs.not_ready')
        self.assertIsNone(result['image'])
        self.assertFalse((self.root / 'cache').exists())
        self.assertFalse((self.root / 'out').exists())

    def test_layer_endpoints_subtitles_and_edit_invalidate(self):
        self.doc["video_tracks"] = [{"name": "base", "kind": "aroll", "clips": [
            {"src": "s", "src_in": 0, "src_out": 6, "dst_in": 3, "dur": 6}]}]
        self.doc["subtitles"] = [{"in": 4, "out": 5, "text": "FRAME"}]
        self.save()
        self.prepare()
        results = [frame_timeline(self.path, frame=f, render=True) for f in (2, 3, 4, 5, 8, 9)]
        for result in results:
            self.assertTrue(result["ok"], result["diagnostics"])
        pixels = [self.pixels(r["image"]) for r in results]
        self.assertEqual(set(pixels[0]), {0})
        self.assertEqual(set(pixels[-1]), {0})
        self.assertEqual(pixels[1], pixels[3])
        self.assertEqual(pixels[1], pixels[4])
        self.assertNotEqual(pixels[2], pixels[1])
        old = results[1]
        self.doc["unknown"]["exact"] += "!"
        self.save()
        changed = frame_timeline(self.path, frame=3, render=True)
        self.assertTrue(changed["ok"], changed["diagnostics"])
        self.assertNotEqual(changed["image"]["render_key"], old["image"]["render_key"])
        self.assertEqual(changed["reference"]["document"]["raw"].encode(), self.path.read_bytes())
        self.assertFalse((self.root / "out").exists())

    def test_snapshot_change_during_render_has_no_success_marker(self):
        from vtx.pipeline.composite import RenderPlan
        original = RenderPlan.composite
        def mutate(plan, *args, **kwargs):
            value = original(plan, *args, **kwargs)
            self.path.write_bytes(self.path.read_bytes() + b" ")
            return value
        with patch.object(RenderPlan, "composite", mutate):
            result = frame_timeline(self.path, frame=1, render=True)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["image"])
        self.assertEqual(list((self.root / "cache" / "preview").glob("*/complete.json")), [])

    def test_window_alpha_fade_real_pixels_and_same_different_key_concurrency(self):
        from concurrent.futures import ThreadPoolExecutor
        self.doc["video_tracks"] = [{"name": "window", "kind": "pip", "clips": [
            {"src": "s", "src_in": 0, "src_out": 12, "dst_in": 0, "dur": 12,
             "scale": 0.25, "x": 100, "y": 80, "opacity": 0.5,
             "fade_in_frames": 3, "fade_out_frames": 3}]}]
        self.save()
        self.prepare()
        with ThreadPoolExecutor(max_workers=2) as pool:
            same = list(pool.map(lambda _: frame_timeline(self.path, frame=6, render=True), range(2)))
        for result in same:
            self.assertTrue(result["ok"], result["diagnostics"])
        self.assertEqual(sum(result["image"]["cache_hit"] for result in same), 1)
        self.assertEqual(same[0]["image"]["sha256"], same[1]["image"]["sha256"])
        rgb = self.pixels(same[0]["image"])
        def pixel(x, y):
            i = (y * 1920 + x) * 3
            return tuple(rgb[i:i+3])
        self.assertEqual(pixel(50, 50), (0, 0, 0))
        self.assertLess(max(pixel(200, 150)[:2]), 6)
        self.assertTrue(115 <= pixel(200, 150)[2] <= 140, pixel(200, 150))
        with ThreadPoolExecutor(max_workers=2) as pool:
            different = list(pool.map(lambda f: frame_timeline(self.path, frame=f, render=True), (0, 11)))
        for result in different:
            self.assertTrue(result["ok"], result["diagnostics"])
            self.assertFalse(result["image"]["cache_hit"])
        self.assertNotEqual(different[0]["image"]["render_key"], different[1]["image"]["render_key"])
        self.assertEqual(set(self.pixels(different[0]["image"])), {0})
        self.assertNotEqual(different[1]["image"]["sha256"], same[0]["image"]["sha256"])

    def test_cancel_and_failure_never_publish_partial_or_reuse_old_marker(self):
        from vtx.pipeline.composite import RenderPlan
        for error in (KeyboardInterrupt(), RuntimeError("injected encoder failure")):
            def partial(plan, layers, path, **kwargs):
                path.write_bytes(b"partial")
                raise error
            with patch.object(RenderPlan, "composite", partial):
                result = frame_timeline(self.path, frame=2, render=True)
            self.assertFalse(result["ok"])
            self.assertIsNone(result["image"])
            self.assertEqual(list((self.root / "cache" / "preview").glob("*/complete.json")), [])
            self.assertEqual(list((self.root / "cache" / "preview").glob("*/image.png")), [])
        success = frame_timeline(self.path, frame=2, render=True)
        self.assertTrue(success["ok"], success["diagnostics"])
        image = self.root / success["image"]["path"]
        old_marker = (image.parent / "complete.json").read_bytes()
        image.write_bytes(b"broken")
        with patch.object(RenderPlan, "composite", side_effect=RuntimeError("rebuild failure")):
            failed = frame_timeline(self.path, frame=2, render=True)
        self.assertFalse(failed["ok"])
        self.assertIsNone(failed["image"])
        self.assertEqual((image.parent / "complete.json").read_bytes(), old_marker)
        repaired = frame_timeline(self.path, frame=2, render=True)
        self.assertTrue(repaired["ok"], repaired["diagnostics"])
        self.assertFalse(repaired["image"]["cache_hit"])

    @unittest.skipUnless(os.environ.get("VTX_TEST_CHROME"), "set VTX_TEST_CHROME for real warm HTML gfx pixels")
    def test_real_warm_html_gfx_alpha_motion_and_half_open_end(self):
        from vtx.cli.commands import gfx
        (self.root / "page.html").write_text('''<!doctype html><style>html,body{margin:0;background:transparent}#r{position:absolute;top:10px;width:100px;height:100px;background:rgba(255,0,0,.5)}</style><div id=r></div><script>window.__vtx_render=f=>document.getElementById('r').style.left=(10+100*f)+'px';window.__vtx_render(Number(new URLSearchParams(location.search).get('f')||0));</script>''')
        self.doc["video_tracks"] = [
            {"name": "base", "kind": "aroll", "clips": [
                {"src": "s", "src_in": 0, "src_out": 12, "dst_in": 0, "dur": 12}]},
            {"name": "moving", "kind": "gfx", "clips": [{"src": "page.html", "dst_in": 4, "dur": 3}]}]
        self.save()
        with patch.dict(os.environ, {"VTX_GFX": "cdp", "VTX_CHROME": os.environ["VTX_TEST_CHROME"],
                                     "VTX_GFX_PAGES": "1"}):
            self.prepare()
            with contextlib.redirect_stdout(io.StringIO()):
                gfx(Ctx(self.path))
            before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*")
                      if p.is_file() and "preview" not in p.parts}
            results = [frame_timeline(self.path, frame=f, render=True) for f in (4, 5, 6, 7)]
            for result in results:
                self.assertTrue(result["ok"], result["diagnostics"])
            pixels = [self.pixels(r["image"]) for r in results]
            def pixel(data, x):
                i = (50 * 1920 + x) * 3
                return tuple(data[i:i+3])
            for local in range(3):
                purple = pixel(pixels[local], 50 + 100 * local)
                self.assertTrue(110 < purple[0] < 145 and 110 < purple[2] < 145, purple)
                self.assertLess(purple[1], 8)
            self.assertNotEqual(pixels[0], pixels[1])
            self.assertTrue(pixel(pixels[-1], 250)[2] > 240)
            self.assertTrue(pixel(pixels[-1], 250)[0] < 8)
            after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*")
                     if p.is_file() and "preview" not in p.parts}
            self.assertEqual(before, after)

    def test_sigint_cancels_waited_ffmpeg_child_without_success_marker(self):
        import signal
        self._cancel_process(signal.SIGINT, cli=False)

    def test_cli_sigint_sigterm_parent_cancellation_reaps_ffmpeg(self):
        import signal
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                self._cancel_process(sig, cli=True)

    def test_native_preencode_cancellation_reaps_child_and_cleans_scratch(self):
        import signal
        self.config.write_text('{"cache":{"mode":"core"}}')
        self.doc['video_tracks'] = [{'kind': 'aroll', 'name': 'native', 'clips': [
            {'src': 's', 'src_in': 0, 'src_out': 12, 'dst_in': 0, 'dur': 12}]}]
        self.save()
        self._cancel_process(signal.SIGINT, cli=False, preencode=True)
        self.assertFalse((self.root / 'cache' / 'seg').exists())
        self.assertFalse(list((self.root / 'cache' / 'preview').glob('*/inputs_*')))
        self.assertFalse(list((self.root / 'cache' / 'preview').rglob('*.mp4')))

    def _cancel_process(self, cancellation, cli, preencode=False):
        import signal
        import time
        child_pid = self.root / ("child-" + str(int(cancellation)) + ".pid")
        script = self.root / "cancel_driver.py"
        script.write_text('''import json, pathlib, subprocess, sys
from vtx.api import frame_timeline
from vtx.pipeline.composite import RenderPlan
real = subprocess.Popen
pidpath = pathlib.Path(sys.argv[2])
path = sys.argv[1]
mode = sys.argv[3]
def tracked(*args, **kwargs):
    if mode == 'preencode' and args[0][0] == 'ffmpeg' and '/seg/' in str(args[0][-1]):
        command = ['ffmpeg','-v','error','-y','-re','-f','lavfi','-i','color=c=black:s=1920x1080:r=30',
                   '-t','60','-c:v','libx264','-threads','1','-f','mp4',str(args[0][-1])]
        args = (command, *args[1:])
    p = real(*args, **kwargs)
    if '-re' in args[0]: pidpath.write_text(str(p.pid))
    return p
subprocess.Popen = tracked
def slow(plan, layers, path, **kwargs):
    return plan.c.run(['ffmpeg','-v','error','-y','-re','-f','lavfi','-i','color=c=black:s=1920x1080:r=30','-t','60','-c:v','png','-threads','1','-f','image2','-update','1',str(path)])
if mode != 'preencode': RenderPlan.composite = slow
if mode == 'cli':
    sys.argv = ['vtx','-t',path,'timeline','frame','--frame','2','--render']
    from vtx.cli.commands import main
    raise SystemExit(main())
print(json.dumps(frame_timeline(path,frame=2,render=True)))
''')
        process = subprocess.Popen([sys.executable, str(script), str(self.path), str(child_pid), 'preencode' if preencode else 'cli' if cli else 'api'],
                                   cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO)},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + 30
            while not child_pid.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(child_pid.exists(), "ffmpeg did not start")
            process.send_signal(cancellation)
            out, err = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 1 if cli else 0, err)
            result = json.loads(out)
            self.assertFalse(result["ok"])
            self.assertEqual(result["diagnostics"][0]["code"], "render.cancelled")
            self.assertIsNone(result["image"])
            with self.assertRaises(ProcessLookupError):
                os.kill(int(child_pid.read_text()), 0)
            self.assertEqual(list((self.root / "cache" / "preview").glob("*/complete.json")), [])
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=10)
            if child_pid.exists():
                pid = int(child_pid.read_text())
                cmdline = Path(f"/proc/{pid}/cmdline")
                try:
                    identity = cmdline.read_bytes()
                    if b"ffmpeg" in identity and str(self.root).encode() in identity:
                        os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, FileNotFoundError):
                    pass

    def test_validate_default_core_differential_success_and_failure(self):
        from vtx.pluginlib import hooks
        for invalid in (False, True):
            self.doc["subtitles"] = [{"in": 0, "out": 13 if invalid else 1, "text": "X"}]
            self.save()
            c = Ctx(self.path)
            receipts = []
            c.receipt = lambda *args, **kwargs: receipts.append((args, kwargs))
            with patch.object(hooks, "run_probe_source", return_value=None) as probe, \
                    patch.object(hooks, "run_validate", return_value=[]) as plugin_validate, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                if invalid:
                    with self.assertRaises(SystemExit):
                        validate(c)
                else:
                    self.assertIsNone(validate(c))
                self.assertEqual(probe.call_count, 1)
                self.assertEqual(plugin_validate.call_count, 1)
            self.assertEqual(receipts[0][0][0:2], ("validate", not invalid))
            self.assertIn("校验失败" if invalid else "校验通过", output.getvalue())
            with patch.object(hooks, "run_probe_source", side_effect=AssertionError("hook")), \
                    patch.object(hooks, "run_validate", side_effect=AssertionError("hook")), \
                    contextlib.redirect_stdout(io.StringIO()) as quiet:
                errors = validate_core(c)
            self.assertEqual(bool(errors), invalid)
            if invalid:
                self.assertIn(errors[0], output.getvalue())
            self.assertEqual(quiet.getvalue(), "")
            self.assertEqual(len(receipts), 1)


if __name__ == "__main__":
    unittest.main()
