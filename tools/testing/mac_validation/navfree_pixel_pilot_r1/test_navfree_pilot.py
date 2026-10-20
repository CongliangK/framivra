"""Explicit ordinary CPU/mock contracts only. No discovery, browser, native or fault exercise."""
import copy
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zlib

import navfree_pilot as n

BASE = Path(os.environ["NAVFREE_BASE_HARNESS"])
REPO = Path(os.environ["NAVFREE_PRODUCTION_REPO"])
BOUND = Path(os.environ["NAVFREE_BOUND"])
INVENTORY = Path(os.environ["NAVFREE_INVENTORY"])


def png(mode="RGBA", hidden=1):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    pixels = bytes([hidden, 2, 3, 0, 10, 20, 30, 255]) if mode == "RGBA" else bytes([1, 2, 3, 10, 20, 30])
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 6 if mode == "RGBA" else 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"\0" + pixels)) + chunk(b"IEND", b""))


def stats(arm, duration):
    path = "override" if arm == n.ARMS[0] else "navfree"
    return {"n": duration, "rendered": 24, "failed": [], "tabs": 1, "transparent": True,
            "canvas_export": False, "static_spans": 0,
            "path_counts": {k: 24 if k == path else 0 for k in n.PATH_KEYS},
            "png_capture": {"requested_parameters": {"format": "png", "optimizeForSpeed": True},
                "actual_parameters": {"format": "png", "optimizeForSpeed": True},
                "protocol_checks": {"succeeded": 1, "failed": 0},
                "capture_calls": {"succeeded": 25, "failed": 0},
                "reused_frames": 0, "canvas_frames": 0, "static_copied_frames": 0}}


class NavfreeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = n.load_base(BASE)
        cls.events, cls.render, cls.Ctx = cls.base.import_production(REPO, allow_local_review=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="navfree-ordinary-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_01_exact_source_and_frozen_inputs(self):
        b = self.base
        self.assertEqual(b.identity(BASE)["sha256"], n.BASE_SHA)
        self.assertEqual(b.identity(BOUND / "runtime/shared.js")["sha256"], n.SHARED_SHA)
        rows = b.load_inventory(INVENTORY)
        # The cloud evidence bundle intentionally omitted five media files.
        # Runtime still requires ALL 114 original inputs; this test does not substitute them.
        missing = [r for r in rows if not (BOUND / r["path"]).exists()]
        self.assertEqual([r["path"] for r in missing],
                         ["audio/T17.flac", "audio/T18.flac", "audio/T19.flac",
                          "audio/T20.flac", "media/paper-background.mp4"])
        self.assertTrue(all(r["excluded_media"] for r in missing))
        self.assertEqual(len(b.verify_inputs(BOUND, [r for r in rows if r not in missing])), 109)
        self.assertEqual(b.identity(BOUND / "timeline.json")["sha256"], b.TIMELINE_SHA)
        self.assertEqual(b.identity(BOUND / "entry-manifest.json")["sha256"], b.ENTRY_SHA)
        self.assertEqual(Path(type(self).render.__code__.co_filename).absolute(), REPO / "vtx/gfx_render.py")

    def test_02_exact_48_target_mapping(self):
        self.assertEqual(len(set(n.GLOBAL_FRAMES)), 24)
        self.assertEqual(list(n.GLOBAL_FRAMES), sorted(n.GLOBAL_FRAMES))
        self.assertEqual((n.GLOBAL_FRAMES[0], n.GLOBAL_FRAMES[-1]), (1380, 1679))
        for entry in n.ENTRY_ORDER:
            local = n.frames(self.base, entry, n.ARMS[0])
            self.assertEqual([f + self.base.ENTRIES[entry]["dst_in"] for f in local], list(n.GLOBAL_FRAMES))
        self.assertEqual(n.frames(self.base, "T08.01", n.ARMS[0])[0], 1362)

    def test_03_descending_is_exact_reverse(self):
        for entry in n.ENTRY_ORDER:
            asc = n.frames(self.base, entry, n.ARMS[0])
            self.assertEqual(asc, n.frames(self.base, entry, n.ARMS[1]))
            self.assertEqual(asc[::-1], n.frames(self.base, entry, n.ARMS[2]))

    def test_04_environment_changes_only_navfree(self):
        values = [n.environment(self.base, arm, "contract") for arm in n.ARMS]
        self.assertEqual({k for k in values[0] if values[0][k] != values[1][k]}, {"VTX_GFX_NAVFREE"})
        self.assertEqual(values[1], values[2])
        for env in values:
            self.assertEqual([env[k] for k in ("VTX_GFX_PNG_FAST", "VTX_GFX_PAGES", "VTX_GFX_TABS")], ["1"] * 3)
            self.assertEqual(env["VTX_GFX_SPLIT_FRAMES"], "0")
            self.assertEqual(env["VTX_GFX_SPLIT_MAX"], "1")
        self.assertEqual(self.base.COMMON_ENV["VTX_GFX_NAVFREE"], "0")

    def test_05_actual_path_gate_never_relabels(self):
        for arm in n.ARMS:
            s = stats(arm, 3582)
            before = copy.deepcopy(s)
            gate = n.capture_gate(s, arm, 3582)
            self.assertEqual(s, before)
            self.assertEqual(gate["actual_path_counts"], s["path_counts"])
            self.assertEqual(gate["requested_navfree"], arm != n.ARMS[0])
            self.assertEqual(gate["target_count"], 24)
        with self.assertRaises(ValueError):
            n.capture_gate(stats(n.ARMS[0], 3582), n.ARMS[1], 3582)
        # Frozen C300 helper still rejects NAVFREE unchanged.
        with self.assertRaises(ValueError):
            self.base.capture_gate(stats(n.ARMS[1], 3582), True, count=24)

    def test_06_count_contract_excludes_fallback_and_capture_failures(self):
        for field in ("override", "canvas_fallback", "static_copied", "failed"):
            s = stats(n.ARMS[1], 3582)
            s["path_counts"][field] = 1
            with self.assertRaises(ValueError):
                n.capture_gate(s, n.ARMS[1], 3582)
        for key, value in (("capture_calls", {"succeeded": 25, "failed": 1}),
                           ("protocol_checks", {"succeeded": 2, "failed": 0}),
                           ("reused_frames", 1), ("cleanup_failures", ["ordinary rejected record"])):
            s = stats(n.ARMS[1], 3582)
            s["png_capture"][key] = value
            with self.assertRaises(ValueError):
                n.capture_gate(s, n.ARMS[1], 3582)

    def test_07_three_mock_arms_preserve_frame_order_and_attempts(self):
        for arm in n.ARMS:
            project = self.root / arm
            project.mkdir()
            (project / "timeline.json").write_text(json.dumps({"version": 2,
                "project": {"fps": 30, "width": 1920, "height": 1080, "duration_frames": 3600},
                "sources": [], "video_tracks": [], "audio_tracks": [], "subtitles": []}))
            for entry in n.ENTRY_ORDER:
                path = project / self.base.ENTRIES[entry]["src"]
                path.parent.mkdir(exist_ok=True)
                path.write_text("<html>Ordinary mocked input; never navigated.</html>")
            calls = []
            def renderer(html, outdir, duration, **kwargs):
                calls.append((html, duration, kwargs))
                self.assertIsInstance(kwargs["ctx"], self.Ctx)
                self.assertNotIn("png_fast", kwargs)
                self.assertEqual((kwargs["tabs"], kwargs["width"], kwargs["height"]), (1, 1920, 1080))
                outdir.mkdir(parents=True)
                for frame in kwargs["frames"]:
                    (outdir / f"f{frame:04d}.png").write_bytes(png())
                return stats(arm, duration)
            with mock.patch.dict(os.environ, n.environment(self.base, arm, "fixture")):
                report = n.run_arm(self.base, project, "fixture", "never-invoked-browser", project / "log",
                                   arm, self.events, renderer, self.Ctx)
            self.assertEqual([c[1] for c in calls], [3582, 3600])
            self.assertEqual([c[2]["frames"] for c in calls], [n.frames(self.base, e, arm) for e in n.ENTRY_ORDER])
            self.assertEqual((report["exit_code"], report["event_gate"]["attempts"]), (0, 2))
            self.assertEqual(report["qualification"], "NOT_GRANTED")
            self.assertTrue((project / "log/arm.status.json").exists())
            self.assertTrue((project / "log/event-gate.json").exists())

    def directories(self, entry, hidden=1):
        left, right = self.root / "left", self.root / "right"
        left.mkdir(); right.mkdir()
        for index, frame in enumerate(n.frames(self.base, entry, n.ARMS[0])):
            (left / f"f{frame:04d}.png").write_bytes(png())
            (right / f"f{frame:04d}.png").write_bytes(png(hidden=hidden if index == 0 else 1))
        return left, right

    def test_08_all_sparse_rgba_pairs(self):
        left, right = self.directories("T08.01")
        result = n.compare_and_report(self.base, left, right, "T08.01",
                                      self.root / "pairs.jsonl", self.root / "report.json", 2, 1)
        self.assertEqual((result["rgba_pairs"], result["rgba_equal"]), (24, True))
        self.assertEqual((result["status"], result["expected_pairs"], result["completed_pairs"]), ("MATCH", 24, 24))
        rows = [json.loads(s) for s in (self.root / "pairs.jsonl").read_text().splitlines()]
        self.assertEqual([r["global"] for r in rows], list(n.GLOBAL_FRAMES))

    def test_09_hidden_rgb_mismatch_retains_all_pairs(self):
        left, right = self.directories("T07.01", hidden=2)
        with self.assertRaisesRegex(ValueError, "RGBA mismatch"):
            n.compare_and_report(self.base, left, right, "T07.01",
                                 self.root / "pairs.jsonl", self.root / "report.json", 2, 1)
        result = json.loads((self.root / "report.json").read_text())
        self.assertEqual(result["rgba_pairs"], 24)
        self.assertEqual((result["status"], result["completed_pairs"]), ("MISMATCH", 24))
        self.assertFalse(result["rgba_equal"])
        self.assertEqual(result["mismatch_local_frames"], [1380])
        self.assertEqual(len((self.root / "pairs.jsonl").read_text().splitlines()), 24)

    def test_10_exact_sparse_output_set(self):
        left, right = self.directories("T07.01")
        self.base.exact_target_set(left, n.frames(self.base, "T07.01", n.ARMS[0]))
        (right / "f1382.png").write_bytes(png())
        with self.assertRaises(ValueError):
            n.compare_and_report(self.base, left, right, "T07.01",
                                 self.root / "extra.jsonl", self.root / "extra.json", 2, 1)
        extra = json.loads((self.root / "extra.json").read_text())
        self.assertEqual((extra["status"], extra["expected_pairs"], extra["completed_pairs"]), ("INCOMPLETE", 24, 0))
        self.assertEqual(extra["failure_stage"], "right_target_set")
        (right / "f1382.png").unlink()
        (right / "f1381.png").write_bytes(b"ordinary malformed PNG contract fixture")
        with self.assertRaises(ValueError):
            n.compare_and_report(self.base, left, right, "T07.01",
                                 self.root / "invalid.jsonl", self.root / "invalid.json", 2, 1)
        invalid = json.loads((self.root / "invalid.json").read_text())
        self.assertEqual((invalid["status"], invalid["expected_pairs"], invalid["completed_pairs"]), ("INCOMPLETE", 24, 1))
        self.assertEqual(invalid["failure_stage"], "decode_right")
        self.assertEqual((invalid["failed_target"]["local"], invalid["failed_target"]["side"]), (1381, "right"))
        self.assertEqual(len((self.root / "invalid.jsonl").read_text().splitlines()), 1)
        original = (self.root / "invalid.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "report already exists"):
            n.compare_and_report(self.base, left, right, "T07.01",
                                 self.root / "invalid.jsonl", self.root / "invalid.json", 2, 1)
        self.assertEqual((self.root / "invalid.json").read_bytes(), original)

    def test_11_rgba_decoder_normalizes_rgb_only(self):
        rgb = self.root / "rgb.png"
        rgb.write_bytes(png("RGB"))
        decoded, identity = self.base.decode_rgba(rgb, 2, 1)
        self.assertEqual(decoded, bytes([1, 2, 3, 255, 10, 20, 30, 255]))
        self.assertEqual(identity["color_type"], 2)

    def test_12_normal_probe_accounting(self):
        for arm in n.ARMS:
            for probes in (1, 2):
                s = stats(arm, 3600)
                s["png_capture"]["capture_calls"]["succeeded"] = 24 + probes
                self.assertEqual(n.capture_gate(s, arm, 3600)["probe_calls_inferred"], probes)
