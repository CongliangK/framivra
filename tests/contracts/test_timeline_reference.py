"""Lossless references via real files, public Python and public CLI; no rendering."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from vtx.api import reference_timeline, inspect_timeline, timeline_transaction
from vtx import timeline

ROOT = Path(__file__).resolve().parents[2]
LIMIT = 8 * 1024 * 1024


def document():
    return {"version": 2, "project": {"fps": 30, "width": 1920, "height": 1080, "duration_frames": 60},
            "sources": [{"id": "source", "path": "missing.mp4"}],
            "video_tracks": [{"name": "picture", "kind": "aroll", "clips": [
                {"src": "source", "src_in": 4, "src_out": 14, "dst_in": 0, "dur": 10},
                {"src": "source", "src_in": 14, "src_out": 24, "dst_in": 10, "dur": 10}]}],
            "audio_tracks": [{"name": "voice", "events": [
                {"src": "source", "in": 0, "out": 30, "loop": True, "volume": 1.0}]}],
            "subtitles": [{"in": 10, "out": 20, "text": "字幕"}],
            "plugins": {"not_installed": {"never_execute": True}}}


class TimelineReferenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="framivra-reference-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "时间线.json"
        self.doc = document()
        self.store()

    def store(self):
        self.path.write_text(json.dumps(self.doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def version(self):
        return "sha256:" + hashlib.sha256(self.path.read_bytes()).hexdigest()

    def ref(self, **kwargs):
        return reference_timeline(self.path, **kwargs)

    def error(self, code, **kwargs):
        result = self.ref(**kwargs)
        self.assertFalse(result["ok"], result)
        self.assertIn(code, [d["code"] for d in result["diagnostics"]], result)
        self.assertIsNone(result["selection"])
        return result

    def cli(self, *args):
        proc = subprocess.run([sys.executable, "-m", "vtx", "-t", str(self.path), "reference", *args],
                              cwd=self.root, env=dict(os.environ, PYTHONPATH=str(ROOT),
                                                     PYTHONDONTWRITEBYTECODE="1"),
                              capture_output=True, text=True)
        self.assertEqual(proc.stderr, "")
        return proc.returncode, json.loads(proc.stdout)

    def test_document_raw_authority_unknown_numbers_escapes_and_block_spans(self):
        block = ('{ "src":"source", "src_in":4,"src_out":14,"dst_in":0,"dur":10, '
                 '"unknown":{"huge":900719925474099312345,"decimal":0.123456789012345678901234567890,'
                 '"nested":[{"a/b~c":"\\u4e2d\\ud83d\\ude00\\\\\\\""},true,null]} }')
        original = json.dumps(self.doc, ensure_ascii=False)
        old_block = json.dumps(self.doc["video_tracks"][0]["clips"][0], ensure_ascii=False)
        text = " \n" + original.replace(old_block, block) + "\t\n"
        self.path.write_bytes(text.encode("utf-8"))
        before = self.path.read_bytes()
        result = self.ref(global_frame=0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["schema"], "framivra.timeline.reference/v1")
        self.assertEqual(result["document"]["raw"].encode("utf-8"), before)
        self.assertEqual(result["document"]["content_version"], self.version())
        selected = result["selection"]["blocks"][0]
        self.assertEqual(selected["raw"], block)
        self.assertEqual(selected["source_id"], "source")
        self.assertNotIn("source_frame", selected)  # native source clock is unprobed
        self.assertIsNone(selected["stable_id"])
        self.assertEqual(result["image"], {"state": "not-generated", "kind": None})
        self.assertEqual(result["document"]["project"], {"kind": "legacy", "project_id": None, "head": None})
        code, wire = self.cli("--frame", "0")
        self.assertEqual(code, 0)
        self.assertEqual(wire, result)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_empty_selection_references_document_only_and_legacy_has_no_stable_ids(self):
        self.doc["video_tracks"][0]["id"] = "legacy-not-protocol"
        self.store()
        result = self.ref()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["selection"]["blocks"], [])
        self.assertEqual(result["selection"]["tracks"], [])
        self.error("reference.unmanaged", track_ids=["legacy-not-protocol"])
        result = self.ref(track_addresses=["/video_tracks/0"], expected_content_version=self.version())
        self.assertTrue(result["ok"], result)
        self.assertTrue(all(t["stable_id"] is None for t in result["selection"]["tracks"]))
        self.assertEqual(len(result["selection"]["blocks"]), 2)

    def test_managed_real_store_ids_multiple_selectors_and_subtitle_virtual_track(self):
        adopted = timeline_transaction(self.path, {"operation": "adopt", "op_id": "adopt",
                                                  "base_revision": self.version()})
        self.assertTrue(adopted["ok"], adopted)
        doc = json.loads(self.path.read_bytes())
        vt, at = doc["video_tracks"][0], doc["audio_tracks"][0]
        ids = [vt["clips"][1]["id"], doc["subtitles"][0]["id"]]
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = self.ref(track_ids=[at["id"]], block_ids=ids, global_frame=10)
        self.assertTrue(result["ok"], result)
        self.assertEqual([b["stable_id"] for b in result["selection"]["blocks"]],
                         [ids[0], at["events"][0]["id"], ids[1]])
        self.assertIsNone(result["selection"]["tracks"][-1]["stable_id"])
        self.assertEqual(result["document"]["project"], dict(kind="managed", project_id=doc["_vtx"]["project_id"], head="adopt"))
        code, actual = self.cli("--track-id", at["id"], "--block-id", ids[0], "--block-id", ids[1], "--frame", "10")
        self.assertEqual(code, 0)
        self.assertEqual(actual, result)
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.error("reference.not_found", block_ids=[vt["id"]])
        self.error("reference.conflict", block_ids=[vt["clips"][0]["id"]], global_frame=10)
        self.error("reference.duplicate", block_ids=[ids[0], ids[0]])
        self.error("reference.duplicate", block_ids=[ids[0]], block_addresses=["/video_tracks/0/clips/1"],
                   expected_content_version=self.version())

    def test_addresses_require_exact_version_stale_never_selects_reordered_block(self):
        version = self.version()
        args = {"block_addresses": ["/video_tracks/0/clips/0"]}
        self.error("reference.version_required", **args)
        result = self.ref(**args, expected_content_version=version)
        self.assertTrue(result["ok"], result)
        self.doc["video_tracks"][0]["clips"].reverse()
        self.store()
        self.error("reference.stale", **args, expected_content_version=version)
        self.error("reference.not_found", block_addresses=["/video_tracks/0/clips/20"], expected_content_version=self.version())
        self.error("reference.not_found", track_addresses=["/sources/0"], expected_content_version=self.version())

    def test_windows_boundaries_fps_and_invalid_selectors(self):
        self.assertEqual(self.ref(global_frame=9)["selection"]["blocks"][0]["range"], [0, 10])
        self.assertEqual(self.ref(global_frame=10)["selection"]["blocks"][0]["range"], [10, 20])
        self.assertEqual(self.ref(global_frame=59)["selection"]["blocks"], [])
        self.assertEqual(len(self.ref(interval=[9, 10])["selection"]["blocks"]), 2)
        for frame in (-1, 60):
            self.error("reference.bounds", global_frame=frame)
        for frame in (True, 1.0, "1", {}):
            self.error("reference.integer", global_frame=frame)
        for interval in ([0, 61], [1, 1], [-1, 1]):
            self.error("reference.bounds", interval=interval)
        for interval in ([0, True], [0, 1.0], [], {}, "01"):
            self.error("reference.interval", interval=interval)
        self.error("reference.conflict", global_frame=1, interval=[0, 2])
        self.error("reference.selector", track_ids="not-a-list")
        self.error("reference.selector", block_addresses=[{}])
        self.doc["project"]["fps"] = 30.0
        self.store()
        self.assertEqual(self.ref()["selection"]["fps"], 30.0)
        for fps in (24, 29.97, True):
            self.doc["project"]["fps"] = fps
            self.store()
            self.error("timeline.fps")

    def test_shared_strict_metadata_global_identity_duplicate_json_and_ambiguity(self):
        for meta in (None, {}, {"protocol": True, "project_id": "p", "head": "h"},
                     {"protocol": 2, "project_id": "p", "head": "h"},
                     {"protocol": 1, "project_id": "p", "head": "h", "extra": 1}):
            self.doc["_vtx"] = meta
            self.store()
            self.error("reference.identity")
        self.doc.pop("_vtx")
        self.doc["sources"].append(copy.deepcopy(self.doc["sources"][0]))
        self.store()
        self.error("reference.ambiguous")
        self.doc = document()
        self.doc["video_tracks"].append(copy.deepcopy(self.doc["video_tracks"][0]))
        self.store()
        self.error("reference.ambiguous")
        self.doc = document()
        self.doc["video_tracks"][0]["clips"][0]["src"] = "unknown"
        self.store()
        self.error("reference.source")
        for raw in (b'{"x":{"a":1,"a":2}}', b'{"x":NaN}', b'\xff', b'{'):
            self.path.write_bytes(raw)
            self.error("input.json")
        self.doc = document()
        self.store()
        adopted = timeline_transaction(self.path, {"operation": "adopt", "op_id": "adopt", "base_revision": self.version()})
        self.assertTrue(adopted["ok"], adopted)
        self.doc = json.loads(self.path.read_bytes())
        self.doc["subtitles"][0]["id"] = self.doc["sources"][0]["id"]
        self.store()
        self.error("reference.identity")

    def test_external_subtitles_coverage_and_conflicting_inline(self):
        self.doc.pop("subtitles")
        self.doc["subtitles_src"] = "missing.srt"
        self.store()
        result = self.ref(global_frame=10)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["unexpanded"][0]["reference"], "missing.srt")
        self.assertFalse(any(b["family"] == "subtitles" for b in result["selection"]["blocks"]))
        virtual = self.ref(track_addresses=["/subtitles"], expected_content_version=self.version())
        self.assertTrue(virtual["ok"], virtual)
        self.assertIsNone(virtual["selection"]["tracks"][0]["raw"])
        self.assertIsNone(virtual["selection"]["tracks"][0]["stable_id"])
        self.doc["subtitles"] = [{"in": 0, "out": 1, "text": "conflict"}]
        self.store()
        self.error("timeline.subtitles_conflict")
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_fps_rounding_is_not_reported_as_exact_and_legacy_unicode_sources_stay_legacy(self):
        self.doc["sources"][0]["id"] = "源🎬"
        for track in self.doc["video_tracks"]:
            for block in track["clips"]:
                block["src"] = "源🎬"
        self.doc["audio_tracks"][0]["events"][0]["src"] = "源🎬"
        self.store()
        result = self.ref(global_frame=0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["selection"]["blocks"][0]["source_id"], "源🎬")
        text = self.path.read_text(encoding="utf-8").replace('"fps": 30', '"fps": 30.000000000000000000001')
        self.path.write_text(text, encoding="utf-8")
        self.error("reference.precision")

    def test_input_output_limits_and_safe_derived_integers_never_truncate(self):
        self.path.write_bytes(b" " * (LIMIT + 1))
        self.error("input.limit")
        self.doc["unknown"] = "中" * (LIMIT // 4)
        self.store()
        self.assertLess(self.path.stat().st_size, LIMIT)
        result = self.error("output.limit")
        self.assertIsNone(result["document"])
        self.assertLess(len(json.dumps(result)), LIMIT)
        code, actual = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(actual, result)
        self.doc = document()
        self.doc["project"]["duration_frames"] = 2 ** 53
        self.store()
        self.error("reference.precision")
        self.doc = document()
        self.doc["video_tracks"][0]["clips"][0]["src_in"] = 2 ** 53
        self.store()
        self.error("reference.precision", global_frame=0)

    def test_one_snapshot_even_if_file_changes_after_shared_parse(self):
        original = self.path.read_bytes()
        version = self.version()
        check = timeline._check_shape
        def changing(result):
            check(result)
            self.path.write_bytes(b"{}")
        with patch.object(timeline, "_check_shape", side_effect=changing):
            result = self.ref(global_frame=0, expected_content_version=version)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["document"]["raw"].encode("utf-8"), original)
        self.assertEqual(result["content_version"], version)
        self.assertEqual(result["selection"]["blocks"][0]["range"], [0, 10])

    def test_cli_envelope_exit_codes_and_read_only(self):
        before = self.path.read_bytes()
        for argv, expected in (((), self.ref()), (("--frame", "10"), self.ref(global_frame=10)),
                               (("--interval", "0", "10"), self.ref(interval=[0, 10]))):
            code, actual = self.cli(*argv)
            self.assertEqual(code, 0)
            self.assertEqual(actual, expected)
        for argv in (("--frame", "60"), ("--frame", "true"), ("--block-address", "/subtitles/0")):
            code, actual = self.cli(*argv)
            self.assertEqual(code, 1)
            self.assertFalse(actual["ok"])
        for argv in (("--draft",), ("--json", str(self.root / "report.json")),
                     ("--events-jsonl", str(self.root / "events.jsonl")), ("--family", "video")):
            code, actual = self.cli(*argv)
            self.assertEqual(code, 2)
            self.assertEqual(actual["schema"], "framivra.timeline.reference/v1")
            self.assertEqual(actual["diagnostics"][0]["code"], "cli.arguments")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.path])
        self.assertEqual(inspect_timeline(self.path)["content_version"], self.ref()["content_version"])


if __name__ == "__main__":
    unittest.main()
