"""Semantic edits use real temporary managed projects and the sole store publisher."""
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from vtx.api import timeline_transaction, timeline_edit_plan, timeline_store_status
from vtx.timeline_edit import EditError, transform_timeline, EDIT_OPERATIONS
from vtx.timeline_structure import managed_objects, structure_errors
from vtx import timeline_store as store

ROOT = Path(__file__).resolve().parents[2]


def fixture():
    return {"version": 2, "project": {"fps": 30, "width": 1920, "height": 1080, "duration_frames": 120},
            "sources": [{"id": "s", "path": "missing.mov", "extension": {"id": "s", "nested": [None, False, 1.25]}}],
            "video_tracks": [{"id": "v", "name": "main", "kind": "aroll", "clips": [
                {"id": "c", "src": "s", "src_in": 10, "src_out": 40, "dst_in": 0, "dur": 30, "extension": {"ref": "c", "nested": {"x": [1, 2]}}},
                {"id": "c2", "src": "s", "src_in": 50, "src_out": 80, "dst_in": 60, "dur": 30}]},
                {"id": "g", "name": "graphics", "kind": "gfx", "clips": [
                    {"id": "gc", "src": "missing.html", "dst_in": 0, "dur": 30}]}],
            "audio_tracks": [{"id": "a", "name": "voice", "events": [
                {"id": "e", "src": "s", "src_in": 7, "in": 0, "out": 90, "volume": 1, "loop": True}]},
                {"id": "bed", "name": "bed", "duck": True, "duck_strategy": {"voice_track": "voice", "extra": "voice"}, "events": [
                    {"id": "be", "src": "s", "in": 0, "out": 100, "volume": 0.2}],
                 "_meta": {"bed_static_duck": {"params": {"track": "bed", "voice_track": "voice"}}}}],
            "subtitles": [{"id": "sub", "in": 0, "out": 30, "text": "你好", "extension": {"preserve": "sub"}}],
            "unknown_root": {"references": ["c", "voice", "s"], "nested": {"anything": [False, None, {"id": "v"}]}}}


def sha(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class TimelineEditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="framivra-edit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "时间线.json"
        result = timeline_transaction(self.path, dict(operation="create", op_id="create", base_revision=None, document=fixture()))
        self.assertTrue(result["ok"], result)
        self.counter = 0

    def doc(self):
        return json.loads(self.path.read_bytes())

    def request(self, operations, op_id=None):
        self.counter += 1
        return dict(operation="edit", op_id=op_id or "edit-" + str(self.counter), base_revision=sha(self.path.read_bytes()), operations=operations)

    def commit(self, *operations):
        result = timeline_transaction(self.path, self.request(list(operations)))
        self.assertTrue(result["ok"], result)
        self.assertFalse(structure_errors(self.doc(), require_managed=True))
        return result

    def reject(self, *operations):
        before = self.path.read_bytes()
        status = timeline_store_status(self.path)
        result = timeline_transaction(self.path, self.request(list(operations)))
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(timeline_store_status(self.path)["operations"], status["operations"])
        return result

    def test_pure_deepcopy_and_request_immutable(self):
        before = self.doc()
        saved = copy.deepcopy(before)
        operations = [{"op": "block.copy", "id": "c", "new_id": "copy", "track_id": "v", "at": 30}]
        after, summary = transform_timeline(before, operations)
        self.assertEqual(before, saved)
        self.assertEqual(operations[0]["new_id"], "copy")
        copied = next(b for b in after["video_tracks"][0]["clips"] if b["id"] == "copy")
        self.assertEqual(copied["extension"], before["video_tracks"][0]["clips"][0]["extension"])
        copied["extension"]["nested"]["x"].append(3)
        self.assertEqual(before, saved)
        self.assertEqual(summary["created_ids"], ["copy"])
        self.assertIn("hidden object/file references", summary["warnings"][0])
        self.assertEqual(after["unknown_root"], saved["unknown_root"])

    def test_source_add_update_remove_and_shared_impact(self):
        result = self.commit({"op": "source.update", "id": "s", "set": {"path": "elsewhere.mov"}},
                             {"op": "source.add", "data": {"id": "unused", "path": "elsewhere.mov", "extra": [False]}},
                             {"op": "source.remove", "id": "unused"})
        self.assertEqual(result["edit"]["created_ids"], [])
        self.assertEqual(result["edit"]["source_users"]["s"], ["be", "c", "c2", "e"])
        self.assertEqual({r["id"] for r in result["edit"]["affected_ranges"]}, {"be", "c", "c2", "e"})
        self.assertEqual(self.doc()["sources"][0]["extension"], fixture()["sources"][0]["extension"])
        self.reject({"op": "source.remove", "id": "s"})
        self.reject({"op": "source.remove", "id": "s", "cascade": ["c"]})
        self.reject({"op": "source.update", "id": "s", "set": {"id": "different"}})

    def test_source_explicit_cascade_plus_explicit_tracks_removal(self):
        self.commit({"op": "source.add", "data": {"id": "spare", "path": "missing.wav"}},
                    {"op": "source.remove", "id": "s", "cascade": ["c", "c2", "e", "be"]},
                    {"op": "track.remove", "id": "v"}, {"op": "track.remove", "id": "bed"}, {"op": "track.remove", "id": "a"})
        self.assertEqual([s["id"] for s in self.doc()["sources"]], ["spare"])
        self.assertFalse((self.root / "missing.mov").exists())

    def test_track_add_copy_reorder_remove_and_descendant_ids(self):
        result = self.commit({"op": "track.add", "family": "video", "index": 1, "data": {
            "id": "pip", "name": "pip", "kind": "pip", "clips": [{"id": "pc", "src": "s", "src_in": 0, "src_out": 10, "dst_in": 0, "dur": 10}], "extension": {"c": "c"}}},
            {"op": "track.copy", "id": "pip", "index": 0, "name": "pip-copy", "ids": {"pip": "pip2", "pc": "pc2"}},
            {"op": "track.reorder", "family": "video", "ids": ["g", "v", "pip", "pip2"]},
            {"op": "track.remove", "id": "pip"})
        self.assertEqual([t["id"] for t in self.doc()["video_tracks"]], ["g", "v", "pip2"])
        self.assertEqual(result["edit"]["created_ids"], ["pc2", "pip2"])
        self.assertEqual(self.doc()["video_tracks"][-1]["extension"], {"c": "c"})
        self.reject({"op": "track.copy", "id": "v", "index": 0, "name": "bad", "ids": {"v": "v2", "c": "cc"}})
        self.reject({"op": "track.reorder", "family": "video", "ids": ["v", "v", "g"]})
        self.reject({"op": "track.add", "family": "video", "index": 0, "data": {"id": "empty", "name": "empty", "kind": "aroll", "clips": []}})

    def test_track_rename_known_implicit_explicit_and_static_refs_only(self):
        before = self.doc()
        result = self.commit({"op": "track.rename", "id": "a", "name": "narration"})
        bed = self.doc()["audio_tracks"][1]
        self.assertEqual(bed["duck_by"], ["narration"])
        self.assertEqual(bed["duck_strategy"]["voice_track"], "narration")
        self.assertEqual(bed["duck_strategy"]["extra"], "voice")
        self.assertEqual(bed["_meta"], before["audio_tracks"][1]["_meta"])
        self.assertEqual(self.doc()["unknown_root"], before["unknown_root"])
        self.assertEqual(len(result["edit"]["changed_references"]["added"]), 2)
        self.commit({"op": "track.rename", "id": "a", "name": "voice-again"})
        self.assertEqual(self.doc()["audio_tracks"][1]["duck_by"], ["voice-again"])
        self.reject({"op": "track.remove", "id": "a"})
        self.reject({"op": "track.rename", "id": "a", "name": "bed"})
        self.commit({"op": "track.references", "id": "bed", "set": {"duck_by": []}, "unset": ["duck_strategy"]},
                    {"op": "track.remove", "id": "a"})

    def test_duck_static_default_reference_rename(self):
        self.commit({"op": "track.references", "id": "bed", "set": {"duck_by": ["voice"], "duck_strategy": {"global_db": -2}}})
        self.commit({"op": "track.rename", "id": "a", "name": "speech"})
        self.assertEqual(self.doc()["audio_tracks"][1]["duck_strategy"], {"global_db": -2, "voice_track": "speech"})

    def test_audio_track_add_copy_reorder(self):
        self.commit({"op": "track.copy", "id": "a", "name": "voice-copy", "index": 1, "ids": {"a": "a2", "e": "e2"}},
                    {"op": "track.add", "family": "audio", "index": 3, "data": {"id": "fx", "name": "fx", "events": [{"id": "fxe", "src": "s", "in": 1, "out": 2, "volume": 1}]}},
                    {"op": "track.reorder", "family": "audio", "ids": ["fx", "bed", "a2", "a"]})
        self.assertEqual([t["id"] for t in self.doc()["audio_tracks"]], ["fx", "bed", "a2", "a"])
        self.assertEqual(self.doc()["audio_tracks"][2]["events"][0]["src"], "s")

    def test_batch_swap_transient_overlap_one_intent_publish(self):
        calls = []
        original = store._publish_intent
        def publish(*args):
            calls.append(args)
            return original(*args)
        with patch.object(store, "_publish_intent", side_effect=publish):
            result = self.commit({"op": "block.move", "id": "c", "track_id": "v", "at": 60},
                                 {"op": "block.move", "id": "c2", "track_id": "v", "at": 0})
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["edit"]["created_ids"], [])
        self.assertEqual([b["dst_in"] for b in self.doc()["video_tracks"][0]["clips"]], [60, 0])
        self.assertEqual(self.doc()["video_tracks"][0]["clips"][0]["src_in"], 10)

    def test_block_insert_remove_copy_all_families(self):
        self.commit({"op": "block.insert", "track_id": "v", "data": {"id": "new", "src": "s", "dst_in": 30, "dur": 15, "src_in": 0, "src_out": 15}},
                    {"op": "block.copy", "id": "new", "new_id": "nc", "track_id": "v", "at": 45},
                    {"op": "block.remove", "id": "new"},
                    {"op": "block.copy", "id": "gc", "new_id": "gc2", "track_id": "g", "at": 30},
                    {"op": "block.copy", "id": "sub", "new_id": "sub2", "track_id": "subtitles", "at": 30},
                    {"op": "block.insert", "track_id": "a", "data": {"id": "e-new", "src": "s", "in": 90, "out": 100, "volume": 0.5}})
        self.assertEqual(next(b for b in self.doc()["video_tracks"][0]["clips"] if b["id"] == "nc")["src_in"], 0)
        self.reject({"op": "block.remove", "id": "gc"}, {"op": "block.remove", "id": "gc2"})
        self.commit({"op": "block.remove", "id": "e"}, {"op": "block.remove", "id": "e-new"}, {"op": "track.remove", "id": "a"},
                    {"op": "track.references", "id": "bed", "set": {"duck_by": []}, "unset": ["duck_strategy"]})

    def test_cross_video_kind_move_and_wrong_family_reject(self):
        self.commit({"op": "track.copy", "id": "v", "name": "screen", "index": 1, "ids": {"v": "v2", "c": "cc", "c2": "cc2"}},
                    {"op": "block.remove", "id": "cc"}, {"op": "block.move", "id": "c", "track_id": "v2", "at": 30})
        self.assertEqual(self.doc()["video_tracks"][1]["clips"][-1]["id"], "c")
        self.reject({"op": "block.move", "id": "c", "track_id": "a", "at": 30})
        self.reject({"op": "block.copy", "id": "c", "new_id": "bad", "track_id": "g", "at": 60})
        self.reject({"op": "block.insert", "track_id": "g", "data": {"id": "bad", "src": "s", "src_in": 0, "src_out": 10, "dst_in": 60, "dur": 10}})

    def test_video_trim_split_edl_and_original_left_id(self):
        result = self.commit({"op": "block.trim", "id": "c", "start": 5, "end": 25},
                             {"op": "block.split", "id": "c", "at": 15, "new_id": "right"})
        left, right = self.doc()["video_tracks"][0]["clips"][:2]
        self.assertEqual((left["id"], left["dst_in"], left["src_in"], left["src_out"], left["dur"]), ("c", 5, 15, 25, 10))
        self.assertEqual((right["id"], right["dst_in"], right["src_in"], right["src_out"], right["dur"]), ("right", 15, 25, 35, 10))
        self.assertEqual(result["edit"]["created_ids"], ["right"])
        self.assertEqual(left["extension"], right["extension"])
        self.assertEqual(result["edit"]["validation"]["source_bounds"], "unprobed")
        self.reject({"op": "block.trim", "id": "c", "start": 0, "end": 15})
        self.reject({"op": "block.split", "id": "c", "at": 5, "new_id": "edge"})

    def test_audio_trim_split_loop_phase_no_invented_src_out(self):
        self.commit({"op": "block.trim", "id": "e", "start": 5, "end": 80},
                    {"op": "block.split", "id": "e", "at": 30, "new_id": "er"})
        left, right = self.doc()["audio_tracks"][0]["events"]
        self.assertEqual((left["in"], left["out"], left["src_in"]), (5, 30, 12))
        self.assertEqual((right["in"], right["out"], right["src_in"]), (30, 80, 37))
        self.assertTrue(left["loop"] and right["loop"])
        self.assertNotIn("src_out", right)
        self.commit({"op": "block.move", "id": "er", "track_id": "a", "at": 35})
        self.assertEqual(self.doc()["audio_tracks"][0]["events"][-1]["src_in"], 37)

    def test_subtitle_explicit_split_text_policies_trim(self):
        self.reject({"op": "block.split", "id": "sub", "at": 15, "new_id": "sr"})
        self.commit({"op": "block.split", "id": "sub", "at": 15, "new_id": "sr", "text_policy": "explicit", "texts": ["你", "好"]},
                    {"op": "block.trim", "id": "sr", "start": 16, "end": 25})
        self.assertEqual([s["text"] for s in self.doc()["subtitles"]], ["你", "好"])
        self.commit({"op": "block.split", "id": "sr", "at": 20, "new_id": "sr2", "text_policy": "duplicate-text"})
        self.assertEqual([s["text"] for s in self.doc()["subtitles"]], ["你", "好", "好"])

    def test_gfx_move_copy_delete_but_no_continuous_edit(self):
        for operation in ({"op": "block.trim", "id": "gc", "start": 1, "end": 30},
                          {"op": "block.split", "id": "gc", "at": 15, "new_id": "gcr"}):
            self.reject(operation)
        self.commit({"op": "block.move", "id": "gc", "track_id": "g", "at": 30},
                    {"op": "block.copy", "id": "gc", "track_id": "g", "new_id": "gcr", "at": 60},
                    {"op": "block.remove", "id": "gc"})
        self.assertEqual(self.doc()["video_tracks"][1]["clips"][0]["dst_in"], 60)

    def test_fades_preserve_outer_reject_internal_reapply_never_clamp(self):
        self.commit({"op": "parameters.update", "id": "c", "path": ["fade_in_frames"], "value": 10},
                    {"op": "parameters.update", "id": "c", "path": ["fade_out_frames"], "value": 5})
        self.reject({"op": "block.split", "id": "c", "at": 5, "new_id": "bad"})
        self.reject({"op": "block.trim", "id": "c", "start": 5, "end": 30})
        self.reject({"op": "block.split", "id": "c", "at": 5, "new_id": "bad", "fade_policy": "reapply-edge-fades"})
        self.commit({"op": "block.split", "id": "c", "at": 15, "new_id": "cr"})
        left, right = self.doc()["video_tracks"][0]["clips"][:2]
        self.assertEqual((left["fade_in_frames"], left["fade_out_frames"]), (10, 0))
        self.assertEqual((right["fade_in_frames"], right["fade_out_frames"]), (0, 5))
        result = self.commit({"op": "block.trim", "id": "c", "start": 2, "end": 15, "fade_policy": "reapply-edge-fades"})
        self.assertTrue(any("reapply-edge-fades" in w for w in result["edit"]["warnings"]))
        self.assertEqual(self.doc()["video_tracks"][0]["clips"][0]["fade_in_frames"], 10)

    def test_parameters_known_unknown_unset_and_protected_fields(self):
        self.commit({"op": "parameters.update", "id": "c", "path": ["opacity"], "value": 0.5},
                    {"op": "parameters.update", "id": "c", "path": ["extension", "nested", "x"], "value": [False, None, {"id": "c"}]},
                    {"op": "parameters.update", "id": "e", "path": ["pan"], "value": 0.25},
                    {"op": "parameters.update", "id": "s", "path": ["transform"], "value": "crop_center_scale"},
                    {"op": "parameters.update", "target": "project", "path": ["cache_dependencies"], "value": ["missing.css"]},
                    {"op": "parameters.unset", "id": "c", "path": ["opacity"]})
        self.assertNotIn("opacity", self.doc()["video_tracks"][0]["clips"][0])
        for identity, key in (("c", "id"), ("c", "src"), ("c", "dst_in"), ("v", "clips"), ("a", "events"), ("a", "duck_by"), ("a", "duck_strategy"), ("s", "path"), ("v", "name"), ("v", "kind"), ("e", "src_out")):
            self.reject({"op": "parameters.update", "id": identity, "path": [key], "value": []})
        self.reject({"op": "parameters.update", "target": "project", "path": ["duration_frames"], "value": 200})
        self.reject({"op": "parameters.unset", "id": "e", "path": ["volume"]})
        self.reject({"op": "parameters.update", "id": "c", "path": ["_vtx"], "value": {}})

    def test_project_resize_explicit_atomic_and_no_auto_growth(self):
        self.reject({"op": "block.move", "id": "c", "track_id": "v", "at": 110})
        self.reject({"op": "project.resize", "duration_frames": 70})
        self.commit({"op": "block.move", "id": "c", "track_id": "v", "at": 110},
                    {"op": "project.resize", "duration_frames": 150})
        self.assertEqual(self.doc()["project"]["duration_frames"], 150)

    def test_ripple_explicit_scope_shift_and_canvas_resize(self):
        self.commit({"op": "range.ripple", "mode": "shift", "at": 60, "delta": 10, "crossing": "reject", "resize": True,
                     "scope": {"track_ids": ["v"], "subtitles": False}})
        self.assertEqual(self.doc()["video_tracks"][0]["clips"][1]["dst_in"], 70)
        self.assertEqual(self.doc()["project"]["duration_frames"], 130)
        self.assertEqual(self.doc()["audio_tracks"][0]["events"][0]["in"], 0)
        self.reject({"op": "range.ripple", "mode": "shift", "at": 10, "delta": 10, "crossing": "reject", "resize": False,
                     "scope": {"track_ids": ["v"], "subtitles": False}})
        self.reject({"op": "range.ripple", "mode": "shift", "at": 60, "delta": 10, "crossing": "reject", "resize": False})

    def test_ripple_delete_crossing_video_audio_subtitle_source_math(self):
        result = self.commit({"op": "range.ripple", "mode": "delete", "start": 10, "end": 20, "crossing": "cut", "resize": True,
                              "scope": {"track_ids": ["v", "a"], "subtitles": True},
                              "split_ids": {"c": "cr", "e": "er", "sub": "sr"}, "text_policy": "duplicate-text"})
        video = self.doc()["video_tracks"][0]["clips"]
        self.assertEqual([(b["dst_in"], b["dur"], b["src_in"], b["src_out"]) for b in video], [(0, 10, 10, 20), (10, 10, 30, 40), (50, 30, 50, 80)])
        events = self.doc()["audio_tracks"][0]["events"]
        self.assertEqual([(b["in"], b["out"], b["src_in"]) for b in events], [(0, 10, 7), (10, 80, 27)])
        self.assertEqual([s["text"] for s in self.doc()["subtitles"]], ["你好", "你好"])
        self.assertEqual(result["edit"]["created_ids"], ["cr", "er", "sr"])
        self.assertEqual(self.doc()["audio_tracks"][1]["events"][0]["out"], 100)

    def test_ripple_delete_left_right_fully_covered_and_empty_reject(self):
        self.commit({"op": "range.ripple", "mode": "delete", "start": 20, "end": 70, "crossing": "cut", "resize": False,
                     "scope": {"track_ids": ["v"], "subtitles": False}})
        clips = self.doc()["video_tracks"][0]["clips"]
        self.assertEqual([(b["dst_in"], b["dur"], b["src_in"], b["src_out"]) for b in clips], [(0, 20, 10, 30), (20, 20, 60, 80)])
        self.reject({"op": "range.ripple", "mode": "delete", "start": 0, "end": 120, "crossing": "cut", "resize": False,
                     "scope": {"track_ids": ["v"], "subtitles": False}})
        self.commit({"op": "range.ripple", "mode": "delete", "start": 0, "end": 20, "crossing": "cut", "resize": False,
                     "scope": {"track_ids": ["v"], "subtitles": False}})
        self.assertEqual(self.doc()["video_tracks"][0]["clips"][0]["id"], "c2")

    def test_ripple_gfx_crossing_reject_but_whole_delete_allowed(self):
        self.reject({"op": "range.ripple", "mode": "delete", "start": 10, "end": 20, "crossing": "cut", "resize": False,
                     "scope": {"track_ids": ["g"], "subtitles": False}, "split_ids": {"gc": "gcr"}})
        self.commit({"op": "range.ripple", "mode": "delete", "start": 0, "end": 30, "crossing": "cut", "resize": False,
                     "scope": {"track_ids": ["g"], "subtitles": False}}, {"op": "track.remove", "id": "g"})

    def test_negative_bool_float_overlap_bad_ids_references_and_late_failure(self):
        for value in (-1, True, 1.0):
            self.reject({"op": "block.move", "id": "c", "track_id": "v", "at": value})
            self.reject({"op": "block.split", "id": "c", "new_id": "cr", "at": value})
            self.reject({"op": "project.resize", "duration_frames": value})
        self.reject({"op": "block.move", "id": "c", "track_id": "v", "at": 60})
        self.reject({"op": "block.relink", "id": "e", "src": "missing-id"})
        self.reject({"op": "block.copy", "id": "c", "track_id": "v", "at": 30, "new_id": "c"})
        self.reject({"op": "block.remove", "id": "c"}, {"op": "block.copy", "id": "c2", "track_id": "v", "at": 0, "new_id": "c"})
        self.reject({"op": "source.update", "id": "s", "set": {"path": "changed.mov"}}, {"op": "block.remove", "id": "missing"})
        self.reject({"op": "parameters.update", "id": "c", "path": ["opacity"], "value": True})

    def test_external_subtitle_readonly_conflict_and_no_file_access(self):
        document = self.doc()
        document["subtitles"] = []
        document["subtitles_src"] = "absent.srt"
        # Test fixture injection, not a production writer.
        self.path.write_text(json.dumps(document), encoding="utf-8")
        self.commit({"op": "source.update", "id": "s", "set": {"path": "next.mov"}})
        self.reject({"op": "block.insert", "track_id": "subtitles", "data": {"id": "newsub", "in": 0, "out": 10, "text": "x"}})
        self.reject({"op": "range.ripple", "mode": "shift", "at": 0, "delta": 1, "crossing": "reject", "resize": False,
                     "scope": {"track_ids": [], "subtitles": True}})
        self.assertFalse((self.root / "absent.srt").exists())

    def test_plan_zero_write_without_history_and_cli_python_same(self):
        # A managed project without copied history is a valid readonly plan input.
        other = self.root / "no-history.json"
        other.write_bytes(self.path.read_bytes())
        req = self.request([{"op": "block.split", "id": "c", "at": 15, "new_id": "cr"}])
        before = sorted(p.name for p in self.root.iterdir())
        planned = timeline_edit_plan(other, req)
        self.assertTrue(planned["ok"], planned)
        self.assertIn("unlocked", planned["prediction"])
        self.assertEqual(planned["base_revision"], req["base_revision"])
        self.assertEqual(planned["edit"]["validation"]["source_bounds"], "unprobed")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)
        self.assertFalse(store._location(other).exists())
        request_file = self.root / "request.json"
        request_file.write_text(json.dumps(req), encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        cli = subprocess.run([sys.executable, "-m", "vtx", "-t", str(other), "store", "plan", "--request", str(request_file)], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr + cli.stdout)
        self.assertEqual(json.loads(cli.stdout), planned)
        committed = timeline_transaction(other, req)
        self.assertTrue(committed["ok"], committed)
        self.assertEqual(committed["edit"], planned["edit"])
        self.assertEqual(self.doc()["_vtx"]["head"], "create")

    def tree_snapshot(self):
        # Reads may update filesystem atime; no content/inode/mode/mtime/ctime changes.
        return {str(p.relative_to(self.root)): (
            p.read_bytes() if p.is_file() else None,
            p.stat().st_mode, p.stat().st_size, p.stat().st_ino,
            p.stat().st_mtime_ns, p.stat().st_ctime_ns)
            for p in [self.root, *self.root.rglob("*")]}

    def readonly_plan_both(self, request, code=None):
        request_file = self.root / "plan-request.json"
        request_file.write_text(json.dumps(request), encoding="utf-8")
        before = self.tree_snapshot()
        with patch.object(store, "transform_timeline", side_effect=AssertionError("persisted decisions must not transform")):
            result = timeline_edit_plan(self.path, request)
        self.assertEqual(self.tree_snapshot(), before)
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        cli = subprocess.run([sys.executable, "-m", "vtx", "-t", str(self.path), "store", "plan", "--request", str(request_file)],
                             cwd=self.root, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(cli.returncode, int(code is not None), cli.stderr + cli.stdout)
        self.assertEqual(json.loads(cli.stdout), result)
        self.assertEqual(self.tree_snapshot(), before)
        self.assertNotIn("document", result)
        self.assertNotIn("edit", result)
        if code is not None:
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["code"], code)
        else:
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["replayed"])
        return result

    def test_plan_committed_original_old_base_returns_frozen_replay_cli_python(self):
        request = self.request([{"op": "block.move", "id": "sub", "track_id": "subtitles", "at": 30}], "once")
        committed = timeline_transaction(self.path, request)
        self.assertTrue(committed["ok"], committed)
        planned = self.readonly_plan_both(request)
        replay = timeline_transaction(self.path, request)
        self.assertEqual(planned["result"], replay)
        self.assertEqual(planned["result"]["edit"], committed["edit"])
        self.assertEqual(planned["base_revision"], request["base_revision"])
        self.assertNotEqual(planned["base_revision"], planned["current_revision"])

    def test_plan_same_id_new_payload_or_only_base_conflicts_before_cas(self):
        request = self.request([{"op": "block.move", "id": "sub", "track_id": "subtitles", "at": 30}], "once")
        self.assertTrue(timeline_transaction(self.path, request)["ok"])
        for altered in (dict(request, base_revision=sha(self.path.read_bytes())),
                        dict(request, operations=[{"op": "block.move", "id": "sub", "track_id": "subtitles", "at": 60}]),
                        dict(request, base_revision=sha(self.path.read_bytes()), operations=[{"op": "block.move", "id": "sub", "track_id": "subtitles", "at": 60}])):
            with self.subTest(request=altered):
                self.readonly_plan_both(altered, "idempotency.conflict")
                self.assertEqual(timeline_transaction(self.path, altered)["code"], "idempotency.conflict")

    def test_plan_aborted_original_refuses_new_window_cli_python(self):
        request = self.request([{"op": "block.move", "id": "sub", "track_id": "subtitles", "at": 30}], "aborted")
        def fault(name):
            if name == "intent_published":
                raise OSError("abort plan regression intent")
        with patch.object(store, "_checkpoint", side_effect=fault):
            self.assertFalse(timeline_transaction(self.path, request)["ok"])
        self.assertTrue(timeline_store_status(self.path, recover=True)["ok"])
        self.readonly_plan_both(request, "aborted")
        self.assertEqual(timeline_transaction(self.path, request)["code"], "aborted")
        altered = dict(request, base_revision="stale")
        self.readonly_plan_both(altered, "idempotency.conflict")
        self.assertEqual(timeline_transaction(self.path, altered)["code"], "idempotency.conflict")

    def test_plan_pending_before_and_after_replace_requires_explicit_recover(self):
        for stage in ("intent_published", "replaced"):
            request = self.request([{"op": "parameters.update", "id": "sub", "path": ["text"], "value": stage}], stage)
            def fault(name):
                if name == stage:
                    raise OSError("pending plan regression intent")
            with patch.object(store, "_checkpoint", side_effect=fault):
                self.assertFalse(timeline_transaction(self.path, request)["ok"])
            self.readonly_plan_both(request, "recovery_required")
            self.readonly_plan_both(dict(request, op_id="unrelated"), "recovery_required")
            with patch.object(store, "transform_timeline", side_effect=AssertionError("recovery must not transform")):
                self.assertTrue(timeline_store_status(self.path, recover=True)["ok"])
            self.readonly_plan_both(request, "aborted" if stage == "intent_published" else None)

    def test_plan_replay_after_undo_is_historical_not_current_document(self):
        request = self.request([{"op": "block.split", "id": "c", "at": 15, "new_id": "right"}], "split")
        committed = timeline_transaction(self.path, request)
        self.assertTrue(committed["ok"], committed)
        undone = timeline_transaction(self.path, dict(operation="undo", op_id="undo", base_revision=committed["revision"], head="split"))
        self.assertTrue(undone["ok"], undone)
        planned = self.readonly_plan_both(request)
        self.assertEqual(planned["result"]["revision"], committed["revision"])
        self.assertEqual(planned["result"]["edit"]["created_ids"], ["right"])
        self.assertEqual(planned["current_revision"], undone["revision"])
        self.assertEqual(planned["result"], timeline_transaction(self.path, request))
        self.assertNotIn("right", [o["id"] for _, o, _ in managed_objects(self.doc())])

    def test_plan_corrupt_history_is_not_a_fresh_plan_and_never_repairs(self):
        home = store._location(self.path)
        record = next(home.glob("op-*"))
        request = self.request([{"op": "block.remove", "id": "sub"}])
        for evidence in (home / "manifest.json", record / "intent.json", record / "commit.json", record / "after.bin"):
            original = evidence.read_bytes()
            for corruption in (b"{broken", None):
                with self.subTest(evidence=evidence.name, missing=corruption is None):
                    if corruption is None:
                        evidence.unlink()
                    else:
                        evidence.write_bytes(corruption)
                    self.readonly_plan_both(request, "recovery_required")
                    evidence.write_bytes(original)
        unknown = home / "unexpected"
        unknown.write_bytes(b"not a record")
        self.readonly_plan_both(request, "recovery_required")

    def test_plan_corrupt_current_timeline_rejects_before_persisted_decision(self):
        request = self.request([{"op": "block.remove", "id": "sub"}], "once")
        self.assertTrue(timeline_transaction(self.path, request)["ok"])
        original = self.path.read_bytes()
        for content, code in ((b"null", "input.structure"), (b"false", "input.structure"),
                              (b"[]", "input.structure"), (b"{}", "input.structure"),
                              (b"{broken", "input.json")):
            self.path.write_bytes(content)
            # Both a historical replay and a new request must validate existing
            # bytes before idempotency/CAS; JSON null is not a missing file.
            for candidate in (request, dict(request, op_id="fresh", base_revision=sha(content))):
                with self.subTest(content=content, op_id=candidate["op_id"]):
                    self.readonly_plan_both(candidate, code)
                    with patch.object(store, "transform_timeline", side_effect=AssertionError("invalid current must not transform")):
                        result = timeline_transaction(self.path, candidate)
                    self.assertEqual(result["code"], code)
                    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
                    cli = subprocess.run([sys.executable, "-m", "vtx", "-t", str(self.path), "store", "--request", str(self.root / "plan-request.json")],
                                         cwd=self.root, env=env, capture_output=True, text=True, timeout=20)
                    self.assertEqual(cli.returncode, 1, cli.stderr + cli.stdout)
                    self.assertEqual(json.loads(cli.stdout), result)
                    self.assertEqual(self.path.read_bytes(), content)
        self.path.write_bytes(original)

    def test_plan_missing_current_retains_historical_replay_cli_python(self):
        request = self.request([{"op": "block.remove", "id": "sub"}], "once")
        committed = timeline_transaction(self.path, request)
        self.assertTrue(committed["ok"], committed)
        self.path.unlink()  # Only missing raw bytes use the historical replay exception.
        planned = self.readonly_plan_both(request)
        self.assertIsNone(planned["current_revision"])
        self.assertIsNone(planned["result"]["current_revision"])
        expected = dict(committed, replayed=True, current_revision=None)
        self.assertEqual(planned["result"], expected)
        with patch.object(store, "transform_timeline", side_effect=AssertionError("historical replay must not transform")):
            self.assertEqual(timeline_transaction(self.path, request), expected)
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        cli = subprocess.run([sys.executable, "-m", "vtx", "-t", str(self.path), "store", "--request", str(self.root / "plan-request.json")],
                             cwd=self.root, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(cli.returncode, 0, cli.stderr + cli.stdout)
        self.assertEqual(json.loads(cli.stdout), expected)
        self.assertFalse(self.path.exists())
        for base, code in ((None, "input.missing"), ("stale", "conflict")):
            candidate = dict(request, op_id="fresh", base_revision=base)
            with self.subTest(base=base):
                self.readonly_plan_both(candidate, code)
                with patch.object(store, "transform_timeline", side_effect=AssertionError("missing current must not transform")):
                    result = timeline_transaction(self.path, candidate)
                self.assertEqual(result["code"], code)
                cli = subprocess.run([sys.executable, "-m", "vtx", "-t", str(self.path), "store", "--request", str(self.root / "plan-request.json")],
                                     cwd=self.root, env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual(cli.returncode, 1, cli.stderr + cli.stdout)
                self.assertEqual(json.loads(cli.stdout), result)
                self.assertFalse(self.path.exists())

    def test_plan_empty_or_lock_only_home_never_initializes_or_recovers(self):
        self.path = self.root / "partial.json"
        self.path.write_bytes((self.root / "时间线.json").read_bytes())
        request = self.request([{"op": "block.remove", "id": "sub"}])
        home = store._location(self.path)
        home.mkdir()
        self.readonly_plan_both(request, "recovery_required")
        (home / "store.lock").write_bytes(b"")
        self.readonly_plan_both(request, "recovery_required")
        self.assertEqual([p.name for p in home.iterdir()], ["store.lock"])

    def test_plan_no_timeline_no_store_zero_write_and_no_transform(self):
        self.path = self.root / "absent.json"
        for base, expected in ((None, "input.missing"), ("stale", "conflict")):
            request = dict(operation="edit", op_id="missing", base_revision=base, operations=[{"op": "block.remove", "id": "sub"}])
            self.readonly_plan_both(request, expected)
            self.assertFalse(store._location(self.path).exists())
            self.assertEqual(timeline_transaction(self.root / "commit-absent.json", request)["code"], expected)

    def test_plan_no_history_preserves_entire_tree_and_does_not_reserve(self):
        self.path = self.root / "copied.json"
        self.path.write_bytes((self.root / "时间线.json").read_bytes())
        request = self.request([{"op": "block.split", "id": "c", "at": 15, "new_id": "right"}])
        before = self.tree_snapshot()
        first = timeline_edit_plan(self.path, request)
        second = timeline_edit_plan(self.path, request)
        self.assertTrue(first["ok"], first)
        self.assertFalse(first["replayed"])
        self.assertEqual(first, second)
        self.assertEqual(self.tree_snapshot(), before)
        self.assertFalse(store._location(self.path).exists())

    def test_plan_observed_concurrent_history_or_timeline_change_is_not_ready(self):
        request = self.request([{"op": "block.remove", "id": "sub"}])
        original = store._plan_history
        for change_history in (True, False):
            calls = []
            def read_history(home, target):
                records = original(home, target)
                calls.append(1)
                if len(calls) == 2:
                    if change_history:
                        concurrent = self.request([{"op": "parameters.update", "id": "sub", "path": ["text"], "value": "concurrent"}])
                        self.assertTrue(timeline_transaction(self.path, concurrent)["ok"])
                        return original(home, target)
                    # An external writer can change only the current file.
                    self.path.write_bytes(self.path.read_bytes() + b"\n")
                return records
            with self.subTest(history=change_history), patch.object(store, "_plan_history", side_effect=read_history), patch.object(store, "transform_timeline", wraps=store.transform_timeline) as transform:
                result = timeline_edit_plan(self.path, request)
                self.assertEqual(result["code"], "plan.not_ready", result)
                self.assertEqual(transform.call_count, int(change_history))  # only the concurrent writer
                self.assertNotIn("document", result)

    def test_plan_success_does_not_reserve_and_later_cas_reject(self):
        request = self.request([{"op": "block.split", "id": "c", "at": 15, "new_id": "cr"}])
        self.assertTrue(timeline_edit_plan(self.path, request)["ok"])
        self.commit({"op": "parameters.update", "id": "sub", "path": ["text"], "value": "later"})
        self.assertEqual(timeline_transaction(self.path, request)["code"], "conflict")
        self.assertEqual(timeline_edit_plan(self.path, request)["code"], "conflict")

    def test_commit_retry_original_ids_summary_undo_new_change_list(self):
        before = self.doc()
        request = self.request([{"op": "block.split", "id": "c", "at": 15, "new_id": "cr"}], "split-once")
        first = timeline_transaction(self.path, request)
        self.assertTrue(first["ok"], first)
        payload = self.path.read_bytes()
        with patch.object(store, "transform_timeline", side_effect=AssertionError("retry/recovery must not re-run operations")):
            self.assertTrue(timeline_store_status(self.path, recover=True)["ok"])
            replay = timeline_transaction(self.path, request)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["edit"], first["edit"])
        self.assertEqual(self.path.read_bytes(), payload)
        self.assertEqual(timeline_transaction(self.path, dict(request, operations=[{"op": "block.remove", "id": "c"}]))["code"], "idempotency.conflict")
        undone = timeline_transaction(self.path, {"operation": "undo", "op_id": "undo-split", "base_revision": sha(payload), "head": "split-once"})
        self.assertTrue(undone["ok"], undone)
        self.assertEqual(undone["edit"]["deleted_ids"], ["cr"])
        self.assertEqual(undone["edit"]["created_ids"], [])
        restored = self.doc()
        restored["_vtx"]["head"] = before["_vtx"]["head"]
        self.assertEqual(restored, before)
        self.assertTrue(timeline_store_status(self.path)["ok"])
        replay_after_undo = timeline_transaction(self.path, request)
        self.assertEqual(replay_after_undo["revision"], first["revision"])
        self.assertEqual(replay_after_undo["current_revision"], undone["revision"])

    def test_real_process_cas_only_one_edit_wins(self):
        request = self.request([{"op": "block.split", "id": "c", "at": 15, "new_id": "cr"}])
        code = "import json,sys; from vtx.api import timeline_transaction; r=json.loads(sys.argv[2]);r['op_id']=sys.argv[3];sys.stdin.readline();print(json.dumps(timeline_transaction(sys.argv[1],r)))"
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        procs = [subprocess.Popen([sys.executable, "-c", code, str(self.path), json.dumps(request), "parallel-" + str(i)], cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(2)]
        for proc in procs:
            proc.stdin.write("go\n"); proc.stdin.flush()
        results = []
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 0, stderr)
            results.append(json.loads(stdout))
        self.assertEqual(sum(r["ok"] for r in results), 1, results)
        self.assertEqual([r["code"] for r in results if not r["ok"]], ["conflict"])

    def test_process_crash_recovery_does_not_apply_edit_twice(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        for checkpoint, committed in (("intent_published", False), ("replaced", True), ("committed", True)):
            other = self.root / (checkpoint + ".json")
            other.write_bytes(self.path.read_bytes())
            request = self.request([{"op": "block.split", "id": "c", "at": 15, "new_id": "cr"}], "crash-" + checkpoint)
            code = """import json,os,signal,sys
from vtx import timeline_store as s
def checkpoint(name):
 if name == sys.argv[3]: os.kill(os.getpid(), signal.SIGKILL)
s._checkpoint=checkpoint
s.timeline_transaction(sys.argv[1],json.loads(sys.argv[2]))
"""
            proc = subprocess.run([sys.executable, "-c", code, str(other), json.dumps(request), checkpoint], cwd=self.root, env=env, capture_output=True, text=True)
            self.assertEqual(proc.returncode, -signal.SIGKILL, proc.stderr)
            recovered = timeline_store_status(other, recover=True)
            self.assertTrue(recovered["ok"], recovered)
            result = timeline_transaction(other, request)
            self.assertEqual(result["ok"], committed, result)
            ids = [obj["id"] for _, obj, _ in managed_objects(json.loads(other.read_bytes()))]
            self.assertEqual(ids.count("cr"), int(committed))
            if committed:
                self.assertTrue(result["replayed"])
                self.assertEqual(result["edit"]["created_ids"], ["cr"])
            else:
                self.assertEqual(result["code"], "aborted")

    def test_relink_preserves_source_window_and_reports_reference_change(self):
        result = self.commit({"op": "source.add", "data": {"id": "other", "path": "other.mov"}},
                             {"op": "block.relink", "id": "c", "src": "other"},
                             {"op": "block.relink", "id": "e", "src": "other"})
        clip = self.doc()["video_tracks"][0]["clips"][0]
        self.assertEqual((clip["src"], clip["src_in"], clip["src_out"]), ("other", 10, 40))
        self.assertEqual(result["edit"]["source_users"]["other"], ["c", "e"])
        self.assertEqual(len(result["edit"]["changed_references"]["removed"]), 2)

    def test_semantic_cli_commit_and_python_replay_match(self):
        request = self.request([{"op": "block.split", "id": "c", "at": 15, "new_id": "cli-right"}], "cli-split")
        request_file = self.root / "request.json"
        request_file.write_text(json.dumps(request), encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        for launcher in ([sys.executable, "-m", "vtx"], [sys.executable, str(ROOT / "vtx/vtx.py")]):
            proc = subprocess.run([*launcher, "-t", str(self.path), "store", "--request", str(request_file)], cwd=self.root, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            result = json.loads(proc.stdout)
            replay = timeline_transaction(self.path, request)
            for key in ("revision", "base_revision", "edit", "request_digest"):
                self.assertEqual(result[key], replay[key])
        self.assertEqual(sum(b["id"] == "cli-right" for b in self.doc()["video_tracks"][0]["clips"]), 1)

    def test_protected_parameters_reject_plausible_typed_values(self):
        for identity, key, value in (("e", "src_out", 100), ("e", "src", "s"), ("e", "in", 1), ("a", "duck_by", ["voice"]), ("a", "events", []), ("v", "name", "new-name"), ("s", "path", "new.mov"), ("sub", "id", "newsub")):
            self.reject({"op": "parameters.update", "id": identity, "path": [key], "value": value})
        self.reject({"op": "block.insert", "track_id": "a", "data": {"id": "bad-event", "src": "s", "src_out": 100, "in": 90, "out": 100, "volume": 1}})
        self.reject({"op": "block.copy", "id": "e", "track_id": "a", "at": 1, "new_id": "overlap-audio"})
        self.reject({"op": "block.copy", "id": "sub", "track_id": "subtitles", "at": 1, "new_id": "overlap-sub"})

    def test_edit_requires_managed_and_does_not_adopt(self):
        legacy = fixture()
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        request = self.request([{"op": "block.remove", "id": "sub"}])
        before = self.path.read_bytes()
        self.assertFalse(timeline_edit_plan(self.path, request)["ok"])
        self.assertFalse(timeline_transaction(self.path, request)["ok"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn("_vtx", self.doc())

    def test_extensions_are_not_reinterpreted_on_gfx_copy(self):
        document = self.doc()
        document["video_tracks"][1]["clips"][0]["src_in"] = 17
        document["video_tracks"][1]["clips"][0]["src_out"] = 21
        self.path.write_text(json.dumps(document), encoding="utf-8")
        self.commit({"op": "block.copy", "id": "gc", "new_id": "gcr", "track_id": "g", "at": 30})
        self.assertEqual(self.doc()["video_tracks"][1]["clips"][1]["src_in"], 17)
        self.assertEqual(self.doc()["video_tracks"][1]["clips"][1]["src_out"], 21)
        self.reject({"op": "block.split", "id": "gcr", "at": 45, "new_id": "gc2"})

    def test_no_ctx_ffmpeg_plugin_execution_and_precision_rejection(self):
        with patch("vtx.pipeline.infra.Ctx", side_effect=AssertionError("Ctx forbidden")), patch("subprocess.run", side_effect=AssertionError("subprocess forbidden")):
            self.commit({"op": "block.split", "id": "e", "at": 30, "new_id": "er"})
        raw = self.path.read_bytes().replace(b'"unknown_root":', b'"precision":0.123456789012345678901,"unknown_root":')
        self.path.write_bytes(raw)
        request = self.request([{"op": "block.remove", "id": "sub"}])
        result = timeline_transaction(self.path, request)
        self.assertEqual(result["code"], "input.json")
        self.assertEqual(self.path.read_bytes(), raw)


class EditOperationFieldsTest(unittest.TestCase):
    """The module-level field table is the single authority for field admission."""

    def managed_doc(self):
        doc = fixture()
        doc["_vtx"] = {"protocol": 1, "project_id": "p1", "head": "h1"}
        return doc

    def test_table_covers_exactly_the_twenty_operations(self):
        from vtx.timeline_edit import EDIT_OPERATION_FIELDS
        self.assertEqual(len(EDIT_OPERATIONS), 20)
        self.assertEqual(set(EDIT_OPERATION_FIELDS), set(EDIT_OPERATIONS))
        self.assertEqual(list(EDIT_OPERATION_FIELDS), list(EDIT_OPERATIONS))

    def test_table_shapes_are_disjoint_and_op_free(self):
        from vtx.timeline_edit import EDIT_OPERATION_FIELDS
        for name, (required, optional) in EDIT_OPERATION_FIELDS.items():
            with self.subTest(op=name):
                self.assertTrue(required, "every operation needs required keys")
                self.assertFalse(required & optional)
                self.assertNotIn("op", required | optional)

    def test_validator_admits_exactly_table_fields(self):
        from vtx.timeline_edit import EDIT_OPERATION_FIELDS, fields
        for name, (required, optional) in EDIT_OPERATION_FIELDS.items():
            with self.subTest(op=name):
                admitted = {key: None for key in required | optional}
                fields({**admitted, "op": name}, *EDIT_OPERATION_FIELDS[name])  # admitted: no error
                with self.assertRaises(EditError):
                    fields({**admitted, "op": name, "zzz_unknown": 1}, *EDIT_OPERATION_FIELDS[name])
                for missing in required:
                    partial = {k: v for k, v in admitted.items() if k != missing}
                    with self.assertRaises(EditError):
                        fields({**partial, "op": name}, *EDIT_OPERATION_FIELDS[name])

    def test_transform_timeline_rejects_unknown_and_missing_fields_for_every_operation(self):
        from vtx.timeline_edit import EDIT_OPERATION_FIELDS
        for name, (required, optional) in EDIT_OPERATION_FIELDS.items():
            with self.subTest(op=name):
                admitted = {key: None for key in required | optional}
                with self.assertRaises(EditError) as ctx:
                    transform_timeline(self.managed_doc(), [{**admitted, "op": name, "zzz_unknown": 1}])
                self.assertIn("missing or unknown fields", str(ctx.exception))
                first_required = next(iter(sorted(required)))
                partial = {k: v for k, v in admitted.items() if k != first_required}
                with self.assertRaises(EditError) as ctx:
                    transform_timeline(self.managed_doc(), [{**partial, "op": name}])
                self.assertIn("missing or unknown fields", str(ctx.exception))

    def test_capabilities_are_pure_static_data_zero_io(self):
        from vtx.timeline_edit import EDIT_OPERATION_FIELDS
        from vtx.timeline_store import timeline_store_capabilities
        with patch("builtins.open", side_effect=AssertionError("capabilities must not open files")):
            caps = timeline_store_capabilities()
        self.assertEqual(caps["edit"]["operation_fields"],
                         {name: {"required": sorted(required), "optional": sorted(optional)}
                          for name, (required, optional) in EDIT_OPERATION_FIELDS.items()})
        with patch("builtins.open", side_effect=AssertionError("capabilities must not open files")):
            again = timeline_store_capabilities()
        self.assertEqual(caps["edit"]["operation_fields"], again["edit"]["operation_fields"],
                         "no cached mutable state between capability calls")

    def test_capabilities_lists_are_copy_isolated_from_the_validator(self):
        from vtx.timeline_store import timeline_store_capabilities
        caps = timeline_store_capabilities()
        caps["edit"]["operation_fields"]["block.trim"]["required"].append("zzz")
        caps["edit"]["operation_fields"]["block.trim"]["optional"].clear()
        caps["edit"]["operations"].append("zzz.fake")
        fresh = timeline_store_capabilities()
        self.assertEqual(fresh["edit"]["operation_fields"]["block.trim"]["required"], ["end", "id", "start"])
        self.assertEqual(fresh["edit"]["operation_fields"]["block.trim"]["optional"], ["fade_policy"])
        self.assertNotIn("zzz.fake", fresh["edit"]["operations"])
        with self.assertRaises(EditError):
            transform_timeline(self.managed_doc(),
                               [{"op": "block.trim", "id": "c", "start": 0, "end": 10, "zzz": 1}])

    def test_field_table_itself_is_immutable(self):
        from vtx.timeline_edit import EDIT_OPERATION_FIELDS
        for name, (required, optional) in EDIT_OPERATION_FIELDS.items():
            with self.subTest(op=name):
                self.assertIsInstance(required, frozenset)
                self.assertIsInstance(optional, frozenset)


if __name__ == "__main__":
    unittest.main()
