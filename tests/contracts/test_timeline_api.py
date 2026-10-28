"""Read-protocol behavior, not schema/render validation or a media smoke."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from vtx.api import inspect_timeline, query_timeline, timeline_capabilities

ROOT = Path(__file__).resolve().parents[2]


def document():
    return {
        "version": 2, "project": {"fps": 30, "duration_frames": 60, "unknown": {"色": "红"}},
        "sources": [{"id": "missing", "path": "absent.mp4", "extra": [None, False, 1]}],
        "video_tracks": [
            {"name": "duplicate", "kind": "aroll", "extension": {"x": 1}, "clips": [
                {"src": "missing", "dst_in": 10, "dur": 10, "src_in": 4, "scale": 0.5,
                 "crop": {"left": 1}, "custom": {"any": [1, 2]}},
                {"src": "missing", "dst_in": 0, "dur": 10}]},
            {"name": "duplicate", "kind": "gfx", "clips": [
                {"src": "missing", "dst_in": 5, "dur": 15, "plugin_config": {"a": 1}}]},
            {"name": "empty", "kind": "screen", "clips": []}],
        "audio_tracks": [{"name": "voice", "custom": True, "events": [
            {"src": "missing", "in": 0, "out": 10, "gain_db": -3, "pan": 0.25},
            {"src": "missing", "in": 10, "out": 30, "loop": True}]}],
        "subtitles": [{"in": 10, "out": 12, "text": "字幕", "style_extension": {"b": 2}}],
        "unknown_root": {"id": "retain me", "array": [3, 2, 1]},
        "plugins": {"not_installed": {"must_not_execute": True}},
    }


class TimelineReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "时间线.json"
        self.doc = document()
        self.store(self.doc)

    def store(self, doc):
        self.path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    def cli(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run([sys.executable, "-m", "vtx", "-t", str(self.path), *args],
                              cwd=self.root, env=env, text=True, capture_output=True)
        self.assertEqual(proc.stderr, "")
        return proc.returncode, json.loads(proc.stdout)

    def test_full_document_extensions_hash_and_no_assets(self):
        before = self.path.read_bytes()
        with patch("vtx.pipeline.infra.Ctx", side_effect=AssertionError("Ctx forbidden")), \
             patch("subprocess.run", side_effect=AssertionError("process forbidden")):
            result = inspect_timeline(self.path)
            queried = query_timeline(self.path, frame=10)
            cap = timeline_capabilities()
        self.assertTrue(result["ok"])
        self.assertEqual(result["document"], self.doc)
        self.assertEqual(result["content_version"], "sha256:" + hashlib.sha256(before).hexdigest())
        self.assertEqual(queried["content_version"], result["content_version"])
        self.assertEqual(result["schema"], cap["schema"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_order_addresses_and_complete_track_data(self):
        result = query_timeline(self.path)
        self.assertTrue(result["ok"])
        self.assertEqual([b["address"] for b in result["blocks"]], [
            "/video_tracks/0/clips/0", "/video_tracks/0/clips/1", "/video_tracks/1/clips/0",
            "/audio_tracks/0/events/0", "/audio_tracks/0/events/1", "/subtitles/0"])
        self.assertEqual(result["blocks"][0]["range"], [10, 20])
        self.assertEqual(result["tracks"][0]["data"], self.doc["video_tracks"][0])
        self.assertEqual(result["tracks"][2]["data"]["clips"], [])
        self.assertEqual(result["tracks"][-1]["data"], self.doc["subtitles"])

    def test_position_and_combined_selectors(self):
        result = query_timeline(self.path, family="video", track_index=0, block_index=0, frame=10)
        self.assertEqual(len(result["blocks"]), 1)
        self.assertEqual(result["blocks"][0]["data"], self.doc["video_tracks"][0]["clips"][0])
        self.assertEqual(query_timeline(self.path, family="video", track_index=0,
                                       block_index=0, frame=9)["blocks"], [])
        self.assertEqual(query_timeline(self.path, family="subtitles", track_index=0,
                                       block_index=0)["blocks"][0]["address"], "/subtitles/0")

    def test_half_open_frames_and_interval_overlap(self):
        self.assertEqual([b["block_index"] for b in query_timeline(
            self.path, family="video", track_index=0, frame=10)["blocks"]], [0])
        self.assertEqual(query_timeline(self.path, family="video", frame=20)["blocks"], [])
        result = query_timeline(self.path, family="video", interval=(9, 10))
        self.assertEqual([b["address"] for b in result["blocks"]],
                         ["/video_tracks/0/clips/1", "/video_tracks/1/clips/0"])
        self.assertEqual(query_timeline(self.path, frame=59)["blocks"], [])
        self.assertEqual(len(query_timeline(self.path, interval=(0, 60))["blocks"]), 6)

    def test_bad_query_arguments_are_structured(self):
        cases = [{"frame": v} for v in (-1, 60, True, 1.0, "1", {}, [])]
        cases += [{"interval": v} for v in ((0, 61), (-1, 1), (1, 1), (2, 1),
                                           (0, False), (0, 1.0), "01", (0,), {})]
        cases += [{"family": "VIDEO"}, {"family": []}, {"track_index": 0},
                  {"block_index": 0}, {"family": "video", "track_index": 3},
                  {"family": "video", "track_index": True},
                  {"family": "video", "track_index": 0, "block_index": 2},
                  {"frame": 0, "interval": [0, 1]}]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                result = query_timeline(self.path, **kwargs)
                self.assertFalse(result["ok"])
                self.assertTrue(result["diagnostics"])
                self.assertEqual(result["blocks"], [])
                for diag in result["diagnostics"]:
                    self.assertEqual(diag["severity"], "error")
                    self.assertIn("address", diag)
                    self.assertIn("code", diag)

    def test_inspect_retains_bad_shape_query_rejects(self):
        cases = [None, [], 42, {"version": 1}, {"project": []}]
        for field, value in (("project", []), ("video_tracks", {}), ("audio_tracks", [None]),
                             ("subtitles", ["bad"]), ("sources", [None])):
            doc = document()
            doc[field] = value
            cases.append(doc)
        for doc in cases:
            with self.subTest(doc=doc):
                self.store(doc)
                result = inspect_timeline(self.path)
                self.assertFalse(result["ok"])
                self.assertEqual(result["document"], doc)
                self.assertTrue(result["content_version"])
                self.assertEqual(query_timeline(self.path)["blocks"], [])

    def test_invalid_frame_fields_and_fps_do_not_crash(self):
        for field in ("dst_in", "dur", "src_in", "src_out", "fade_in_frames", "fade_out_frames"):
            for value in (None, "0", True, 1.5, -1):
                with self.subTest(field=field, value=value):
                    doc = document()
                    doc["video_tracks"][0]["clips"][0][field] = value
                    self.store(doc)
                    self.assertFalse(inspect_timeline(self.path)["ok"])
        for fps in (None, False, 24, 29.97, {}, []):
            doc = document()
            doc["project"]["fps"] = fps
            self.store(doc)
            self.assertFalse(inspect_timeline(self.path)["ok"])
        self.doc["project"]["fps"] = 30.0
        self.store(self.doc)
        self.assertTrue(inspect_timeline(self.path)["ok"])

    def test_reversed_and_out_of_duration_blocks(self):
        for kind in ("video", "audio", "subtitles"):
            doc = document()
            if kind == "video":
                doc["video_tracks"][0]["clips"][0]["dur"] = 100
            elif kind == "audio":
                doc["audio_tracks"][0]["events"][0]["out"] = 0
            else:
                doc["subtitles"][0]["out"] = 9
            self.store(doc)
            self.assertFalse(query_timeline(self.path)["ok"])

    def test_strict_json_errors(self):
        for payload in (b'{"version":2,"version":1}', b'{"x":{"a":1,"a":2}}',
                        b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}',
                        b'{"x":1e10000}', b'{', b'\xff'):
            with self.subTest(payload=payload):
                self.path.write_bytes(payload)
                result = inspect_timeline(self.path)
                self.assertFalse(result["ok"])
                self.assertEqual(result["diagnostics"][0]["code"], "input.json")
                self.assertIsNone(result["document"])
                self.assertTrue(result["content_version"])

    def test_missing_and_directory_input(self):
        for path in (self.root / "not-there.json", self.root, None, "bad\0path"):
            result = inspect_timeline(path)
            self.assertFalse(result["ok"])
            self.assertEqual(result["diagnostics"][0]["code"], "input.read")
            self.assertIsNone(result["content_version"])

    def test_external_subtitles_explicitly_incomplete_and_never_opened(self):
        self.doc.pop("subtitles")
        self.doc["subtitles_src"] = "absent.srt"
        self.store(self.doc)
        for result in (inspect_timeline(self.path), query_timeline(self.path, frame=10),
                       query_timeline(self.path, family="subtitles")):
            self.assertTrue(result["ok"])
            self.assertFalse(result["coverage"]["complete"])
            self.assertEqual(result["coverage"]["unexpanded"][0]["reference"], "absent.srt")
        self.assertEqual(query_timeline(self.path, family="subtitles")["blocks"], [])
        self.doc["subtitles"] = [{"in": 0, "out": 1, "text": "conflict"}]
        self.store(self.doc)
        self.assertFalse(inspect_timeline(self.path)["ok"])

    def test_changed_bytes_change_version_even_for_equal_documents(self):
        old = inspect_timeline(self.path)
        self.path.write_text(json.dumps(self.doc, ensure_ascii=False), encoding="utf-8")
        new = inspect_timeline(self.path)
        self.assertEqual(old["document"], new["document"])
        self.assertNotEqual(old["content_version"], new["content_version"])

    def test_cli_api_equality_and_read_only_even_with_dry_run(self):
        before = self.path.read_bytes()
        cases = [(('inspect',), inspect_timeline(self.path)),
                 (('query', '--frame', '10'), query_timeline(self.path, frame=10)),
                 (('query', '--family', 'video', '--track-index', '0', '--block-index', '1'),
                  query_timeline(self.path, family="video", track_index=0, block_index=1)),
                 (('query', '--interval', '9', '10'), query_timeline(self.path, interval=(9, 10))),
                 (('capabilities',), timeline_capabilities()),
                 (('--dry-run', 'inspect'), inspect_timeline(self.path))]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                code, actual = self.cli(*argv)
                self.assertEqual(code, 0)
                self.assertEqual(actual, expected)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_cli_structured_failures_and_rejected_write_options(self):
        for selector in ("10.0", "true", "-1", "60"):
            code, result = self.cli("query", "--frame", selector)
            self.assertEqual(code, 1)
            self.assertFalse(result["ok"])
        for argv in (("inspect", "--frame", "0"), ("query", "--draft"),
                     ("query", "--events-jsonl", str(self.root / "events.jsonl")),
                     ("query", "unconsumed")):
            code, result = self.cli(*argv)
            self.assertEqual(code, 2)
            self.assertEqual(result["diagnostics"][0]["code"], "cli.arguments")
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_cli_handles_escaped_surrogate_extensions(self):
        self.doc["unknown_root"]["escaped"] = "\ud800"
        self.path.write_text(json.dumps(self.doc), encoding="utf-8")
        code, actual = self.cli("inspect")
        self.assertEqual(code, 0)
        self.assertEqual(actual["document"], self.doc)

    def test_developer_errors_propagate(self):
        with patch("vtx.timeline._check_shape", side_effect=RuntimeError("developer fault")):
            with self.assertRaisesRegex(RuntimeError, "developer fault"):
                inspect_timeline(self.path)


if __name__ == "__main__":
    unittest.main()
