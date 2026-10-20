"""Nineteen explicitly authorized CPU/mock contracts; no discovery or browser."""
import copy
import inspect
import itertools
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zlib

import harness as h

HERE = Path(__file__).absolute().parent
REPO = Path(os.environ["FRAMIVRA_VALIDATION_REPO"]).absolute()
BOUND = Path(os.environ["FRAMIVRA_VALIDATION_BOUND"]).absolute()
INVENTORY = Path(os.environ["FRAMIVRA_VALIDATION_INVENTORY"]).absolute()
PLAN = Path(os.environ["FRAMIVRA_VALIDATION_PLAN"]).absolute()


def png(mode, pixels):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2 if mode == "RGB" else 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"\0" + pixels)) + chunk(b"IEND", b""))


def good_stats(n=3582, fast=False):
    result = {"n": n, "rendered": 300, "failed": [], "tabs": 1, "transparent": True,
              "canvas_export": False, "static_spans": 0, "ms_total": 1.0,
              "path_counts": dict(override=300, canvas=0, twoshot=0, opaque=0,
                                  navfree=0, canvas_fallback=0, static_copied=0, failed=0)}
    if fast:
        result["png_capture"] = {
            "requested_parameters": {"format": "png", "optimizeForSpeed": True},
            "actual_parameters": {"format": "png", "optimizeForSpeed": True},
            "protocol_checks": {"succeeded": 1, "failed": 0},
            "capture_calls": {"succeeded": 301, "failed": 0},
            "reused_frames": 0, "canvas_frames": 0, "static_copied_frames": 0}
    return result


class HarnessContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events, cls.production_render, cls.Ctx = h.import_production(REPO, allow_local_review=True)
        (HERE / "test-work").mkdir(exist_ok=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="contract-", dir=HERE / "test-work")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def project(self):
        project = self.root / "project"
        project.mkdir()
        (project / "timeline.json").write_text(json.dumps({
            "version": 2, "project": {"fps": 30, "width": 1920, "height": 1080,
                                       "duration_frames": 3600},
            "sources": [], "video_tracks": [], "audio_tracks": [], "subtitles": []}))
        for entry in h.ENTRIES.values():
            path = project / entry["src"]
            path.parent.mkdir(exist_ok=True)
            path.write_text("<html>CPU fixture, never navigated</html>")
        return project

    def frame_sets(self, different=False):
        left, right = self.root / "left", self.root / "right"
        left.mkdir(); right.mkdir()
        a = png("RGBA", bytes([1, 2, 3, 0, 10, 20, 30, 255]))
        b = png("RGBA", bytes([2, 2, 3, 0, 10, 20, 30, 255])) if different else a
        for i in range(300):
            (left / f"f{i:04d}.png").write_bytes(a)
            (right / f"f{i:04d}.png").write_bytes(b if i == 0 else a)
        return left, right

    def test_01_frozen_identities(self):
        self.assertEqual(h.identity(PLAN)["sha256"], h.PLAN_SHA)
        self.assertEqual(h.identity(BOUND / "timeline.json")["sha256"], h.TIMELINE_SHA)
        self.assertEqual(h.identity(BOUND / "entry-manifest.json")["sha256"], h.ENTRY_SHA)
        rows = h.load_inventory(INVENTORY)
        self.assertEqual((len(rows), sum(r["bytes"] for r in rows)), (114, 20274304))
        render = type(self).production_render
        self.assertIsNone(inspect.signature(render).parameters["png_fast"].default)
        self.assertIn("png_fast = png_fast_requested()", inspect.getsource(render))
        self.assertNotIn("experiments.fast_png_pilot", Path(h.__file__).read_text())

    def test_02_c300_target_mapping(self):
        entries = {e["entryId"]: e for e in json.loads((BOUND / "entry-manifest.json").read_text())["entries"]}
        for entry_id in ("T08.01", "T07.01"):
            entry, original = h.ENTRIES[entry_id], entries[entry_id]
            self.assertEqual(entry["src"], original["src"])
            self.assertEqual(entry["dur"], original["duration"])
            self.assertEqual(entry["dst_in"], original["windowStart"])
            self.assertEqual(entry["paintIndex"], original["paintIndex"])
            self.assertEqual([f + entry["dst_in"] for f in range(*entry["local"])], list(range(1380, 1680)))
        self.assertEqual(h.ORDER["c300-01"], ["off", "on"])
        self.assertEqual(h.ORDER["c300-02"], ["on", "off"])

    def test_03_t10_projection_preserves_every_other_field(self):
        original = json.loads((BOUND / "timeline.json").read_text())
        before = copy.deepcopy(original)
        projected = h.t10_projection(original)
        self.assertEqual(before, original)
        self.assertEqual({k: v for k, v in original.items() if k != "video_tracks"},
                         {k: v for k, v in projected.items() if k != "video_tracks"})
        self.assertEqual(projected["video_tracks"][0], original["video_tracks"][9])
        self.assertEqual(projected["video_tracks"][0]["clips"],
                         [{"src": "gfx/T10_0330_0630.html", "dst_in": 330, "dur": 300}])
        self.assertEqual(projected["project"]["duration_frames"], 3600)

    def test_04_whitelist_copy_and_hash(self):
        source, target = self.root / "source", self.root / "copy"
        source.mkdir()
        (source / "timeline.json").write_text("ordinary fixture timeline")
        (source / "media").mkdir()
        (source / "media" / "fixture.bin").write_bytes(bytes(range(256)))
        (source / "cache").mkdir()
        (source / "cache" / "old.txt").write_text("not an input")
        rows = [{"path": str(p), **h.identity(source / p)} for p in
                (Path("timeline.json"), Path("media/fixture.bin"))]
        report = h.copy_inputs(source, target, rows)
        self.assertEqual(report["items"], 2)
        self.assertEqual(h.verify_inputs(source, rows), h.verify_inputs(target, rows))
        self.assertFalse((target / "cache").exists())
        self.assertFalse((target / "out").exists())
        self.assertEqual([r["mode"] for r in report["copied"]], [oct((source / r["path"]).stat().st_mode & 0o777) for r in rows])

    def test_05_c300_direct_signature_and_ctx(self):
        project, calls = self.project(), []
        def render(html, outdir, n, **kwargs):
            calls.append((html, outdir, n, kwargs))
            self.assertIsInstance(kwargs["ctx"], self.Ctx)
            self.assertNotIn("png_fast", kwargs)
            return good_stats(n)
        report = h.run_c300(project, "fixture", "unused-chrome", self.root / "log", False,
                            self.events, render, self.Ctx)
        self.assertEqual([c[2] for c in calls], [3582, 3600])
        self.assertEqual([list(c[3]["frames"]) for c in calls], [list(range(1362, 1662)), list(range(1380, 1680))])
        self.assertEqual([c[3]["tabs"] for c in calls], [1, 1])
        self.assertEqual(report["exit_code"], 0)
        reduced = self.events.read_events((self.root / "log/c300.events.jsonl").read_bytes())
        self.assertTrue(reduced["complete"])
        self.assertEqual(len([e for e in reduced["events"] if e["event"] == "attempt_start"]), 2)

    def test_06_events_terminal_and_error_provenance(self):
        project = self.project()
        error = RuntimeError("primary fixture error")
        cleanup_errors = []
        for error_type, text in ((ValueError, "fixture tab close failure"),
                                 (OSError, "fixture browser stop failure")):
            try:
                raise error_type(text)
            except BaseException as cleanup:
                cleanup_errors.append(cleanup)
        error.png_capture = {"cleanup_failures": [
            {"operation": "tab.close", "error_type": "ValueError"},
            {"operation": "browser.stop", "error_type": "OSError"}],
            "capture_calls": {"failed": 1}}
        error.png_cleanup_errors = tuple(cleanup_errors)
        def fail(*args, **kwargs):
            raise error
        with self.assertRaisesRegex(RuntimeError, "primary fixture error"):
            h.run_c300(project, "fixture", "unused-chrome", self.root / "log", True,
                        self.events, fail, self.Ctx)
        page = json.loads((self.root / "log/T08.01.json").read_text())
        self.assertEqual(page["error"]["png_capture"], error.png_capture)
        recorded = page["error"]["png_cleanup_errors"]
        self.assertEqual([r["type"] for r in recorded], ["ValueError", "OSError"])
        self.assertEqual([r["text"] for r in recorded],
                         ["fixture tab close failure", "fixture browser stop failure"])
        for actual, original in zip(recorded, cleanup_errors):
            self.assertIn("test_06_events_terminal_and_error_provenance", actual["traceback"])
            self.assertIn(type(original).__name__ + ": " + str(original), actual["traceback"])
        self.assertIs(error.png_cleanup_errors[0], cleanup_errors[0])
        self.assertEqual(page["error"]["type"], "RuntimeError")
        self.assertEqual(page["error"]["text"], "primary fixture error")
        self.assertIn("primary fixture error", page["error"]["traceback"])
        reduced = self.events.read_events((self.root / "log/c300.events.jsonl").read_bytes())
        self.assertTrue(reduced["complete"])
        self.assertEqual(reduced["exit_code"], 1)
        self.assertEqual([e["outcome"] for e in reduced["events"] if e["event"] == "attempt_terminal"], ["failed"])

    def test_07_on_provenance_gate(self):
        stats = good_stats(fast=True)
        self.assertEqual(h.capture_gate(stats, True)["probe_calls_inferred_from_normal_override_branch"], 1)
        for key, value in [("actual_parameters", None), ("protocol_checks", {"succeeded": 2, "failed": 0}),
                           ("capture_calls", {"succeeded": 301, "failed": 1}), ("reused_frames", 1),
                           ("cleanup_failures", ["fixture failure"])]:
            changed = copy.deepcopy(stats)
            changed["png_capture"][key] = value
            with self.assertRaises(ValueError):
                h.capture_gate(changed, True)
        self.assertIsNone(h.capture_gate(good_stats(), False)["probe_calls"])

    def test_08_rgba_rgb_normalization(self):
        a, b = self.root / "rgb.png", self.root / "rgba.png"
        a.write_bytes(png("RGB", bytes([1, 2, 3, 10, 20, 30])))
        b.write_bytes(png("RGBA", bytes([1, 2, 3, 255, 10, 20, 30, 255])))
        left, il = h.decode_rgba(a, 2, 1)
        right, ir = h.decode_rgba(b, 2, 1)
        self.assertEqual(left, right)
        self.assertNotEqual(il["sha256"], ir["sha256"])
        self.assertEqual((il["color_type"], ir["color_type"]), (2, 6))

    def test_09_hidden_rgb_difference_detected(self):
        left, right = self.frame_sets(different=True)
        index = self.root / "index.jsonl"
        with self.assertRaisesRegex(ValueError, "RGBA mismatch.*local=0 global=330"):
            h.compare_page(left, right, "T10.01", index, 2, 1)
        rows = [json.loads(s) for s in index.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["rgba_equal"])

    def test_10_complete_target_set(self):
        left, right = self.frame_sets()
        h.exact_target_set(left, range(300))
        result = h.compare_page(left, right, "T10.01", self.root / "index.jsonl", 2, 1)
        self.assertEqual(result["rgba_pairs"], 300)
        rows = [json.loads(s) for s in (self.root / "index.jsonl").read_text().splitlines()]
        self.assertEqual([r["global"] for r in rows], list(range(330, 630)))
        with self.assertRaises(ValueError):
            h.exact_target_set(left, range(299))
        with self.assertRaises(ValueError):
            h.exact_target_set(left, range(301))

    def test_11_route_argv_and_environment(self):
        for case in ("t10-multitab", "t10-split"):
            for arm in ("off", "on"):
                result = h.route_commands(REPO, "/usr/bin/python3", self.root / "route", self.root / "log", case, arm, "test_ns")
                self.assertEqual([a[-1] for a in result["argv_in_order"]], ["validate", "gfx"])
                self.assertEqual([a[1:4] for a in result["argv_in_order"]], [["-B", "-m", "vtx"]] * 2)
                self.assertTrue(all("--range" not in a and "--frames" not in a for a in result["argv_in_order"]))
                env = result["env_overrides"]
                self.assertEqual(env["VTX_GFX_PNG_FAST"], "1" if arm == "on" else "0")
                self.assertEqual(env["VTX_GFX_TABS"], "2" if case == "t10-multitab" else "1")
                self.assertEqual(env["VTX_GFX_SPLIT_FRAMES"], "150" if case == "t10-split" else "0")
                self.assertEqual(result["execution"], "NOT_STARTED")
                if case == "t10-multitab":
                    self.assertIn("UNPROVEN", result["per_tab_target_distribution"])
                # Exercise the read-only inspector against real production event
                # emission/reduction; no validate/gfx/render process is executed.
                suffix = case + "-" + arm
                vf, gf = self.root / (suffix + "-validate.jsonl"), self.root / (suffix + "-gfx.jsonl")
                ev = self.events.ExecutionContext(vf.open("x"))
                ev.finish(0)
                ev = self.events.ExecutionContext(gf.open("x"))
                receipt = {"stages": {}}
                with ev.task("gfx", kind="stage").scope():
                    for frame_range in result["expected_ranges"]:
                        block = ev.task("gfx", kind="gfx_block", metadata={
                            "page_id": str(self.events.uuid.uuid4()), "backend": "cdp", "frame_range": frame_range})
                        with block.scope():
                            block.attempt(lambda: None)
                    detail = ("1 gfx clips | T10: " + env["VTX_GFX_TABS"] + " tabs CDP " +
                              "override×300 canvas×0 twoshot×0 opaque×0 navfree×0 canvas_fallback×0 static_copied×0 failed×0" +
                              (", split×2" if case == "t10-split" else ""))
                    stage = {"ok": True, "detail": detail}
                    if arm == "on":
                        p = good_stats(fast=True)["png_capture"]
                        p["protocol_checks"]["succeeded"] = result["expected_gfx_blocks"]
                        p["capture_calls"]["succeeded"] = 300 + result["expected_gfx_blocks"]
                        stage["png_capture"] = p
                    def write(ref):
                        receipt["stages"]["gfx"] = {**stage, "observed_by": ref}
                    ev.receipt("gfx", True, write)
                ev.finish(0)
                inspected = h.inspect_route(case, arm, vf.read_bytes(), gf.read_bytes(), receipt, self.events)
                self.assertEqual(inspected["exposed_route_facts"], "MATCH")
                self.assertEqual(inspected["qualification"], "NOT_GRANTED_BY_THIS_INSPECTOR")

    def test_12_abort_does_not_launch_next_page(self):
        project, calls = self.project(), []
        def fail_gate(*args, **kwargs):
            calls.append(args)
            stats = good_stats(fast=True)
            stats["path_counts"].update(override=299, canvas=1)
            return stats
        with self.assertRaises(ValueError):
            h.run_c300(project, "fixture", "unused-chrome", self.root / "log", True,
                        self.events, fail_gate, self.Ctx)
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.root / "log/T07.01.json").exists())
        status = json.loads((self.root / "log/c300.status.json").read_text())
        self.assertEqual(status["result"], "FAILED")
        self.assertEqual(status["exit_code"], 1)

    def route_fixture(self, path, receipt, stage, code=0, overlap=False, fast=0):
        # Real event writer/reducer, deterministic mock clock; no renderer or threads.
        clock = itertools.count().__next__
        ev = self.events.ExecutionContext(path.open("x"), clock=clock)
        if code:
            ev.finish(code)
            return
        with ev.task(stage, kind="stage").scope():
            if stage == "gfx":
                blocks = [ev.task("gfx", kind="gfx_block", metadata={
                    "page_id": str(self.events.uuid.uuid4()), "backend": "cdp", "frame_range": frames})
                    for frames in ([0, 300], [0, 180])]
                def second():
                    with blocks[1].scope():
                        blocks[1].attempt(lambda: None)
                with blocks[0].scope():
                    blocks[0].attempt(second if overlap else lambda: None)
                    if not overlap:
                        second()  # Overlapping TASK lifetimes, disjoint ATTEMPT lifetimes.
                notes = []
                for entry in (h.ENTRIES["T10.01"], h.ENTRIES["T12.01"]):
                    notes.append(Path(entry["src"]).stem + ": 1 tabs CDP " +
                                 "override×%d canvas×0 twoshot×0 opaque×0 navfree×0 canvas_fallback×0 static_copied×0 failed×0, 1ms/帧 (nav 1 ms@p50)" % entry["dur"])
                record = {"ok": True, "detail": "2 gfx clips｜" + "; ".join(notes)}
                if fast:
                    record["png_capture"] = good_stats(fast=True)["png_capture"]
                    record["png_capture"]["protocol_checks"]["succeeded"] = 2
                    record["png_capture"]["capture_calls"]["succeeded"] = 482
            else:
                record = {"ok": True, "detail": "ordinary validate fixture"}
            ev.receipt(stage, True, lambda ref: receipt["stages"].update({stage: {**record, "observed_by": ref}}))
        ev.finish(0)

    def pages_evidence(self, suffix, overlap, fast=0):
        receipt = {"stages": {}}
        vf, gf = self.root / (suffix + "-validate.jsonl"), self.root / (suffix + "-gfx.jsonl")
        self.route_fixture(vf, receipt, "validate")
        self.route_fixture(gf, receipt, "gfx", overlap=overlap, fast=fast)
        return vf.read_bytes(), gf.read_bytes(), receipt

    def test_pages12_projection_selects_two_exact_clips(self):
        original = json.loads((BOUND / "timeline.json").read_text())
        before = copy.deepcopy(original)
        result = h.pages12_projection(original)
        self.assertEqual(original, before)
        self.assertEqual({k: v for k, v in result.items() if k != "video_tracks"},
                         {k: v for k, v in original.items() if k != "video_tracks"})
        expected = [copy.deepcopy(original["video_tracks"][i]) for i in (9, 12)]
        self.assertEqual(len(expected[1]["clips"]), 2)
        expected[1]["clips"] = expected[1]["clips"][:1]
        self.assertEqual(result["video_tracks"], expected)
        self.assertEqual([c["dur"] for t in result["video_tracks"] for c in t["clips"]], [300, 180])
        self.assertEqual(h.ENTRIES["T12.01"]["global"], [960, 1140])

    def test_pages12_environment_changes_only_pages(self):
        for fast in (0, 1):
            left = h.environment("pages12", "pages1", "fixture", fast_frozen=fast)
            right = h.environment("pages12", "pages2", "fixture", fast_frozen=fast)
            self.assertEqual([k for k in left if left[k] != right[k]], ["VTX_GFX_PAGES"])
            self.assertEqual((left["VTX_GFX_PAGES"], right["VTX_GFX_PAGES"]), ("1", "2"))
            self.assertEqual(left["VTX_GFX_PNG_FAST"], str(fast))
            self.assertEqual([left[k] for k in ("VTX_GFX_TABS", "VTX_GFX_SPLIT_FRAMES", "VTX_GFX_SPLIT_MAX")], ["1", "0", "1"])
        with self.assertRaisesRegex(ValueError, "explicit frozen FAST"):
            h.environment("pages12", "pages1", "fixture")

    def test_route_runner_validate_failure_stops_gfx(self):
        project, log = self.project(), self.root / "route-log"
        plan = h.route_commands(REPO, "/unused/python", project, log, "t10-multitab", "off", "fixture")
        def run(argv, **kwargs):
            self.assertEqual(argv[-1], "validate")
            kwargs["stderr"].write(b"ordinary validation failure")
            self.route_fixture(log / "validate.events.jsonl", {"stages": {}}, "validate", code=2)
            return h.subprocess.CompletedProcess(argv, 2)
        with mock.patch.dict(os.environ, plan["env_overrides"]), mock.patch.object(h.subprocess, "run", side_effect=run) as called:
            result = h.run_route(plan, project, log, self.events)
        self.assertEqual(called.call_count, 1)
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["result"], "FAILED_VALIDATE")
        self.assertFalse((log / "gfx.events.jsonl").exists())
        self.assertEqual((log / "validate.stderr.log").read_bytes(), b"ordinary validation failure")
        self.assertEqual(json.loads((log / "route.status.json").read_text())["exit_code"], 2)

    def test_route_runner_preserves_stage_provenance(self):
        project, log = self.project(), self.root / "route-log"
        plan = h.route_commands(REPO, "/unused/python", project, log, "pages12", "pages1", "fixture", fast_frozen=1)
        receipt, calls = {"stages": {}}, []
        def run(argv, **kwargs):
            stage = argv[-1]
            calls.append(argv)
            self.assertEqual(set(kwargs), {"cwd", "stdin", "stdout", "stderr", "check"})
            self.assertEqual(kwargs["cwd"], str(REPO))
            self.assertFalse(kwargs["check"])
            self.assertEqual(kwargs["stdin"], h.subprocess.DEVNULL)
            if stage == "gfx":
                self.assertEqual((project / "out/receipt.json").read_bytes(), (log / "validate.receipt.json").read_bytes())
                self.assertEqual(list(json.loads((log / "validate.receipt.json").read_text())["stages"]), ["validate"])
            kwargs["stdout"].write(stage.encode() + b" stdout")
            kwargs["stderr"].write(stage.encode() + b" stderr")
            self.route_fixture(Path(argv[argv.index("--events-jsonl") + 1]), receipt, stage, fast=1)
            (project / "out").mkdir(exist_ok=True)
            (project / "out/receipt.json").write_text(json.dumps(receipt))
            return h.subprocess.CompletedProcess(argv, 0)
        with mock.patch.dict(os.environ, plan["env_overrides"]), mock.patch.object(h.subprocess, "run", side_effect=run):
            result = h.run_route(plan, project, log, self.events)
        self.assertEqual(calls, plan["argv_in_order"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["inherited_pgid"], os.getpgrp())
        self.assertNotEqual(result["stages"][0]["run_id"], result["stages"][1]["run_id"])
        for stage in result["stages"]:
            for artifact in [*stage["artifacts"].values(), stage["receipt"]]:
                self.assertEqual({k: artifact[k] for k in ("bytes", "sha256")}, h.identity(artifact["path"]))
            self.assertTrue(stage["evidence_complete"])
            self.assertEqual(stage["exit_code"], 0)
        self.assertEqual(list(json.loads((log / "validate.receipt.json").read_text())["stages"]), ["validate"])
        self.assertEqual(list(receipt["stages"]), ["validate", "gfx"])

    def test_pages12_attempt_overlap_and_serial_control(self):
        for arm, overlap in (("pages1", False), ("pages2", True)):
            evidence = self.pages_evidence(arm, overlap)
            result = h.inspect_pages12(arm, 0, *evidence, self.events)
            self.assertEqual(result["attempt_overlap_ms"] > 0, overlap)
            self.assertEqual(len({p["page_id"] for p in result["pages"]}), 2)
            self.assertEqual([p["frame_range"] for p in result["pages"]], [[0, 300], [0, 180]])
            self.assertGreater(result["attempt_union_ms"], 0)
            with self.assertRaisesRegex(ValueError, "actual attempt overlap"):
                h.inspect_pages12("pages1" if overlap else "pages2", 0, *evidence, self.events)

    def test_pages12_receipt_keeps_both_page_counts(self):
        for fast in (0, 1):
            evidence = self.pages_evidence("counts-" + str(fast), True, fast)
            result = h.inspect_pages12("pages2", fast, *evidence, self.events)
            self.assertEqual([p["path_counts"]["override"] for p in result["pages"]], [300, 180])
            self.assertEqual(result["target_count"], 480)
            self.assertEqual(result["qualification"], "NOT_GRANTED_BY_THIS_INSPECTOR")
            self.assertEqual(result["png_capture"] is None, not fast)

    def test_t12_compare_covers_exact_180_targets(self):
        left, right = self.root / "t12-left", self.root / "t12-right"
        left.mkdir(); right.mkdir()
        data = png("RGBA", bytes([1, 2, 3, 0, 10, 20, 30, 255]))
        for frame in range(180):
            for directory in (left, right):
                (directory / f"f{frame:04d}.png").write_bytes(data)
        index = self.root / "t12-index.jsonl"
        result = h.compare_page(left, right, "T12.01", index, 2, 1)
        rows = [json.loads(row) for row in index.read_text().splitlines()]
        self.assertEqual(result["rgba_pairs"], 180)
        self.assertEqual([row["local"] for row in rows], list(range(180)))
        self.assertEqual([row["global"] for row in rows], list(range(960, 1140)))
        self.assertTrue(all(row["rgba_equal"] for row in rows))


if __name__ == "__main__":
    raise SystemExit("Use the nineteen approved explicit unittest IDs, never discovery.")
