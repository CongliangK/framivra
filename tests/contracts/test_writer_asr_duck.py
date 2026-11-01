"""B1 product adapters: actual publisher/journal, explicit temporary fixtures only.

ASR media/model service is replaced at _transcribe_words (no model/network).
Neither write entry nor kernel is mocked. Optional installed validation loads
this file by absolute path with a kernel already on isolated Python's sys.path.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from vtx.api import (read_timeline_for_write, timeline_transaction,
                     timeline_write_plan, timeline_store_status)

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "apps" / "asr"))
import tl_merge
import asr_cli
spec = importlib.util.spec_from_file_location("bed_static_duck", ROOT / "plugins/duck-static/bed_static_duck.py")
duck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(duck)


def fixture():
    return {"version": 2, "project": {"fps": 30, "width": 1920, "height": 1080,
            "duration_frames": 180, "typed": [True, 1, 9007199254740993]},
            "sources": [{"id": "口播 源", "path": "unprobed.wav", "keep": {"bool": True}}],
            "video_tracks": [], "audio_tracks": [
                {"name": "voice", "delay_frames": 3, "keep": {"a": False}, "events": [
                    {"src": "口播 源", "in": 30, "out": 60, "volume": 1}]},
                {"name": "bed", "delay_frames": 7, "keep": {"a": 1}, "events": [
                    {"src": "口播 源", "src_in": 11, "in": 0, "out": 120, "volume": 1,
                     "loop": True, "pan": 0.2, "fade_in_frames": 4, "fade_out_frames": 9,
                     "custom": {"x": [False, 1, "你好"]}},
                    {"src": "口播 源", "src_in": 3, "in": 120, "out": 180, "volume": 0.5,
                     "loop": False, "custom": {"x": [True, 0]}}]}],
            "subtitles": [{"in": 90, "out": 110, "text": "existing", "custom": [True, 1]}],
            "_meta": {"other": {"flag": True}}, "unknown": [False, 1, "源"]}


def tree(root):
    return {str(p.relative_to(root)): (p.stat().st_mode, p.stat().st_mtime_ns,
            p.stat().st_ctime_ns, p.read_bytes() if p.is_file() else None)
            for p in [root, *root.rglob("*")]}


def typed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


class WriterProducts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="writer-asr-duck-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "timeline.json"
        self.save(fixture())

    def save(self, doc):
        raw = ("\n  " + json.dumps(doc, ensure_ascii=True, indent=2) + "\n").encode()
        self.path.write_bytes(raw)
        return raw

    def managed(self):
        # Legacy Unicode source IDs are valid preserve-unmanaged but not managed.
        doc = fixture()
        doc["sources"][0]["id"] = "source"
        for track in doc["audio_tracks"]:
            for event in track["events"]:
                event["src"] = "source"
        self.save(doc)
        snap = read_timeline_for_write(self.path)
        result = timeline_transaction(self.path, dict(operation="adopt", op_id="adopt",
                                      base_revision=snap["revision"]))
        self.assertTrue(result["ok"], result)
        return read_timeline_for_write(self.path)

    def history(self, path=None):
        return timeline_store_status(path or self.path)

    def bake_request(self, snap=None, op_id="duck-test"):
        snap = snap or read_timeline_for_write(self.path)
        params = dict(track="bed", voice_track="voice", global_db=-8.0, duck_db=-4.0,
                      merge_gap=20, attack=5, release=8)
        wins = duck.voice_windows(snap["document"]["audio_tracks"][0]["events"], 20)
        marker = dict(params=params, note="test marker", script="product-adapter")
        return snap, *duck.prepare_bake(snap, "bed", wins, params, marker, op_id)

    def test_asr_legacy_preserves_unicode_unknown_and_journal(self):
        before = read_timeline_for_write(self.path)["document"]
        summary = tl_merge.merge_subtitles(self.path, [{"in": 0, "out": 20, "text": "新字幕"}], op_id="asr-legacy")
        after = read_timeline_for_write(self.path)["document"]
        self.assertNotIn("_vtx", after)
        self.assertEqual(typed({k: v for k, v in before.items() if k != "subtitles"}),
                         typed({k: v for k, v in after.items() if k != "subtitles"}))
        self.assertEqual(json.loads(Path(summary["backup"]).read_bytes()), before["subtitles"])
        self.assertIn("asr-legacy", json.dumps(self.history()))
        self.assertNotIn("id", after["subtitles"][0])

    def test_asr_managed_append_preserves_existing_stable_ids(self):
        snap = self.managed()
        old = snap["document"]["subtitles"]
        wanted = [{"in": 0, "out": 20, "text": "先"}] + copy.deepcopy(old)
        req = tl_merge.prepare_subtitle_write(snap, wanted, "append")
        self.assertEqual(req, tl_merge.prepare_subtitle_write(snap, wanted, "append"))
        tl_merge.merge_subtitles(self.path, wanted, snapshot=snap,
                                expected_base=snap["revision"], op_id="append")
        after = read_timeline_for_write(self.path)["document"]
        self.assertEqual(typed(after["subtitles"][1]), typed(old[0]))
        self.assertEqual(after["subtitles"][0]["id"], req["new_ids"][0])
        self.assertEqual(after["sources"], snap["document"]["sources"])
        before_retry = tree(self.root)
        tl_merge.merge_subtitles(self.path, wanted, snapshot=snap,
                                expected_base=snap["revision"], op_id="append")
        self.assertEqual(before_retry, tree(self.root))

    def test_asr_managed_replace_not_positional_id_guess(self):
        snap = self.managed()
        old = snap["document"]["subtitles"][0]
        subs = [{"in": old["in"], "out": old["out"], "text": "replacement"}]
        tl_merge.merge_subtitles(self.path, subs, snapshot=snap, expected_base=snap["revision"], op_id="replace-sub")
        after = read_timeline_for_write(self.path)["document"]
        self.assertNotEqual(after["subtitles"][0]["id"], old["id"])
        self.assertEqual(after["audio_tracks"], snap["document"]["audio_tracks"])

    def test_asr_owned_bool_int_negative(self):
        snap = self.managed()
        changed = copy.deepcopy(snap["document"]["subtitles"])
        changed[0]["custom"][0] = 1
        before = tree(self.root)
        with self.assertRaisesRegex(ValueError, "已有字幕 ID"):
            tl_merge.merge_subtitles(self.path, changed, snapshot=snap, expected_base=snap["revision"])
        self.assertEqual(before, tree(self.root))

    def test_asr_dry_and_nochange_fence_are_zero_write(self):
        for managed in (False, True):
            if managed:
                self.managed()
            snap = read_timeline_for_write(self.path)
            before = tree(self.root)
            tl_merge.merge_subtitles(self.path, [{"in": 0, "out": 20, "text": "dry"}], dry_run=True,
                                    snapshot=snap, expected_base=snap["revision"])
            tl_merge.merge_subtitles(self.path, snap["document"]["subtitles"], snapshot=snap,
                                    expected_base=snap["revision"])
            self.assertEqual(before, tree(self.root))

    def test_external_subtitles_and_missing_early_snapshot_fail_closed(self):
        doc = fixture()
        doc["subtitles_src"] = "external.srt"
        doc["subtitles"] = []
        self.save(doc)
        before = tree(self.root)
        with self.assertRaisesRegex(ValueError, "外部字幕"):
            tl_merge.merge_subtitles(self.path, [])
        with self.assertRaisesRegex(ValueError, "早期 snapshot"):
            tl_merge.merge_subtitles(self.path, [], expected_base="sha256:not-a-base")
        self.assertEqual(before, tree(self.root))

    def asr_main(self, service, *flags):
        out = io.StringIO()
        config = self.root / "explicit-asr-config.json"
        if not config.exists():
            config.write_text('{"asr":{"enabled":true}}', encoding="utf-8")
        before = tree(self.root)
        with patch.dict(os.environ, {"VTX_CONFIG": str(config)}), \
                patch.object(asr_cli, "_transcribe_words", service), patch.object(sys, "argv", [
                str(ROOT / "apps/asr/asr_cli.py"), "timeline", "explicit-fake-media.wav", "-t", str(self.path), *flags]), \
                contextlib.redirect_stdout(out):
            result = asr_cli.main()
        if "--dry-run" in flags:
            self.assertEqual(before, tree(self.root))
        return result, out.getvalue()

    @staticmethod
    def service(*args):
        return {"text": "新字幕"}, [{"w": ch, "s": index * 0.3, "e": (index + 1) * 0.3}
                                      for index, ch in enumerate("新字幕")]

    def test_actual_asr_cli_default_replace_append_dry_stdout(self):
        # The old CLI default is REPLACE, not append; preserve actual argparse.
        result, out = self.asr_main(self.service, "--dry-run")
        self.assertEqual(result, 0, out)
        self.assertIn("dry-run：预览前 8 行（未写盘）", out)
        result, out = self.asr_main(self.service, "--append")
        self.assertEqual(result, 0, out)
        self.assertIn("旧 1 条 → 新 2 条", out)
        self.assertIn("摘要：fps=30 duration=180", out)
        self.assertEqual(len(read_timeline_for_write(self.path)["document"]["subtitles"]), 2)
        result, out = self.asr_main(self.service)
        self.assertEqual(result, 0, out)
        self.assertIn("旧 2 条 → 新 1 条", out)

    def test_actual_asr_cli_subprocess_exit_and_stdout(self):
        # Only the explicitly allowed external media/model boundary is replaced;
        # argparse, segline, reports and the installed/source publisher are real.
        config = self.root / "explicit-asr-config.json"
        config.write_text('{"asr":{"enabled":true}}', encoding="utf-8")
        bootstrap = '''import sys
sys.path.append(sys.argv.pop(1))
import asr_cli
asr_cli._transcribe_words = lambda *args: ({"text":"新"}, [{"w":"新","s":0.0,"e":0.8}])
raise SystemExit(asr_cli.main())
'''
        command = [sys.executable, "-I", "-B", "-c", bootstrap, str(ROOT / "apps/asr"),
                   "timeline", "explicit-fake-media.wav", "-t", str(self.path)]
        env = dict(os.environ, VTX_CONFIG=str(config))
        before = tree(self.root)
        dry = subprocess.run(command + ["--dry-run"], env=env, capture_output=True, text=True)
        self.assertEqual(dry.returncode, 0, dry.stderr + dry.stdout)
        self.assertIn("dry-run：预览前 8 行（未写盘）", dry.stdout)
        self.assertEqual(before, tree(self.root))
        done = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("旧 1 条 → 新 1 条", done.stdout)
        doc = read_timeline_for_write(self.path)["document"]
        doc["subtitles_src"] = "external.srt"
        self.save(doc)
        before = tree(self.root)
        rejected = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(rejected.returncode, 1, rejected.stderr + rejected.stdout)
        self.assertIn("/subtitles_src: inline and external subtitles are mutually exclusive", rejected.stdout)
        self.assertEqual(before, tree(self.root))

    def test_actual_asr_cli_mid_transcription_edit_conflicts(self):
        foreign = []
        def service(*args):
            doc = read_timeline_for_write(self.path)["document"]
            doc["unknown"].append("manual edit before transcription returns")
            foreign.append(self.save(doc))
            return self.service(*args)
        result, out = self.asr_main(service)
        self.assertEqual(result, 1, out)
        self.assertIn("conflict", out)
        self.assertIn("op_id=asr-", out)
        self.assertEqual(self.path.read_bytes(), foreign[0])
        self.assertEqual(set(p.name for p in self.root.iterdir()), {"timeline.json", "explicit-asr-config.json"})

    def test_duck_legacy_exact_backup_preserves_unknown_semantics(self):
        raw = self.path.read_bytes()
        snap, req, events = self.bake_request()
        duck.publish_bake(self.path, snap, req)
        after = read_timeline_for_write(self.path)["document"]
        self.assertEqual(after["audio_tracks"][1]["events"], events)
        for event in events:
            self.assertNotIn("id", event)
        self.assertEqual(after["audio_tracks"][1]["delay_frames"], 7)
        self.assertEqual(after["sources"], snap["document"]["sources"])
        self.assertNotIn("_vtx", after)
        bak = self.root / "timeline.pre-duck.json"
        self.assertEqual(bak.read_bytes(), raw)
        status = self.history(bak)
        self.assertNotEqual(status["evidence_path"], self.history()["evidence_path"])
        self.assertIn("duck-backup-", json.dumps(status))
        before = tree(self.root)
        duck.publish_bake(self.path, snap, req)
        self.assertEqual(before, tree(self.root))

    def test_both_products_share_the_public_operation_history(self):
        tl_merge.merge_subtitles(self.path, [{"in": 0, "out": 20, "text": "journal"}], op_id="asr-shared")
        home = self.history()["evidence_path"]
        snap, req, _ = self.bake_request(op_id="duck-shared")
        duck.publish_bake(self.path, snap, req, backup=False)
        status = self.history()
        self.assertEqual(home, status["evidence_path"])
        self.assertEqual({op["op_id"] for op in status["operations"]}, {"asr-shared", "duck-shared"})
        for intent in Path(home).glob("op-*/intent.json"):
            self.assertEqual(json.loads(intent.read_bytes())["protocol"], 2)

    def test_duck_metadata_only_first_application_has_no_fake_operation(self):
        self.managed()
        doc = read_timeline_for_write(self.path)["document"]
        for event in doc["audio_tracks"][1]["events"]:
            event["volume"] = 0.5
        self.save(doc)
        snap = read_timeline_for_write(self.path)
        params = dict(track="bed", voice_track="voice", global_db=0.0, duck_db=0.0,
                      merge_gap=20, attack=0, release=0)
        req, events = duck.prepare_bake(snap, "bed", [], params, {"params": params}, "meta-only")
        self.assertEqual(req["operations"], [])
        self.assertEqual(typed(events), typed(doc["audio_tracks"][1]["events"]))
        before = tree(self.root)
        plan = timeline_write_plan(self.path, req)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(before, tree(self.root))
        duck.publish_bake(self.path, snap, req, backup=False)
        after = read_timeline_for_write(self.path)["document"]
        self.assertEqual(typed(after["audio_tracks"]), typed(doc["audio_tracks"]))
        self.assertEqual(after["_meta"]["bed_static_duck"], {"params": params})

    def test_duck_managed_split_mapping_exact_scope(self):
        snap = self.managed()
        snap, req, events = self.bake_request(snap)
        plan = timeline_write_plan(self.path, req)
        self.assertTrue(plan["ok"], plan)
        duck.publish_bake(self.path, snap, req)
        after = read_timeline_for_write(self.path)["document"]
        old = snap["document"]["audio_tracks"][1]["events"]
        self.assertEqual(events[0]["id"], old[0]["id"])
        self.assertEqual(events[3]["id"], old[1]["id"])
        self.assertEqual(len(req["new_ids"]), 2)
        self.assertEqual(typed(after["audio_tracks"][1]["events"]), typed(events))
        for key in snap["document"].keys() - {"audio_tracks", "_meta", "_vtx"}:
            self.assertEqual(typed(after[key]), typed(snap["document"][key]))
        self.assertEqual(typed(after["audio_tracks"][0]), typed(snap["document"]["audio_tracks"][0]))
        self.assertEqual(typed({k: v for k, v in after["audio_tracks"][1].items() if k != "events"}),
                         typed({k: v for k, v in snap["document"]["audio_tracks"][1].items() if k != "events"}))
        self.assertEqual(typed(after["_meta"]["other"]), typed(snap["document"]["_meta"]["other"]))
        self.assertEqual((self.root / "timeline.pre-duck.json").read_bytes(), snap["raw_bytes"])

    def test_duck_dry_zero_write_and_backup_never_overwritten(self):
        snap, req, _ = self.bake_request()
        before = tree(self.root)
        duck.publish_bake(self.path, snap, req, dry_run=True)
        self.assertEqual(before, tree(self.root))
        bak = self.root / "timeline.pre-duck.json"
        bak.write_bytes(b"existing backup, even invalid JSON\n")
        duck.publish_bake(self.path, snap, req)
        self.assertEqual(bak.read_bytes(), b"existing backup, even invalid JSON\n")

    def test_duck_stale_before_publish_does_not_create_backup(self):
        snap, req, _ = self.bake_request()
        doc = copy.deepcopy(snap["document"])
        doc["unknown"].append("foreign")
        raw = self.save(doc)
        before = tree(self.root)
        with self.assertRaisesRegex(RuntimeError, "conflict"):
            duck.publish_bake(self.path, snap, req)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(before, tree(self.root))

    def test_actual_duck_cli_skip_is_zero_write_and_stdout(self):
        script = ROOT / "plugins/duck-static/bed_static_duck.py"
        command = [sys.executable, "-I", "-B", str(script), "-t", str(self.path)]
        dry = subprocess.run(command + ["--dry-run"], capture_output=True, text=True)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("纯计划", dry.stdout)
        self.assertEqual(list(self.root.iterdir()), [self.path])
        done = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("✓ bed 2 事件 → 4 段", done.stdout)
        before = tree(self.root)
        skip = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(skip.returncode, 0, skip.stderr)
        self.assertIn("跳过（幂等）", skip.stdout)
        self.assertEqual(before, tree(self.root))
        reject = subprocess.run(command + ["--global-db", "-9"], capture_output=True, text=True)
        self.assertEqual(reject.returncode, 1)
        self.assertIn("参数不同", reject.stderr)
        self.assertEqual(before, tree(self.root))

    def test_duck_mid_backup_external_edit_reports_real_side_effect(self):
        snap, req, _ = self.bake_request()
        foreign = []
        def trace(frame, event, arg):
            if (event == "line" and frame.f_code.co_name == "publish_bake" and
                    frame.f_code.co_filename == duck.__file__ and
                    frame.f_locals.get("backup_created") and not foreign):
                doc = copy.deepcopy(snap["document"])
                doc["unknown"].append("external edit after independent backup")
                foreign.append(self.save(doc))
            return trace
        sys.settrace(trace)
        try:
            with self.assertRaisesRegex(RuntimeError, "backup_created=True"):
                duck.publish_bake(self.path, snap, req)
        finally:
            sys.settrace(None)
        self.assertEqual(self.path.read_bytes(), foreign[0])
        self.assertEqual((self.root / "timeline.pre-duck.json").read_bytes(), snap["raw_bytes"])

    def test_real_publisher_post_replace_io_fault_recover_same_operation(self):
        # Inject at the OS fsync boundary, not the adapter/write entry/transformer.
        # Actual intent, replacement and journal recovery execute on real files.
        snap = read_timeline_for_write(self.path)
        subs = [{"in": 0, "out": 20, "text": "io fault"}]
        original = os.fsync
        fired = []
        def fsync(fd):
            if not fired and self.path.read_bytes() != snap["raw_bytes"]:
                fired.append(True)
                raise OSError(5, "B1 explicit fixture post-replace fsync EIO")
            return original(fd)
        with patch.object(os, "fsync", fsync):
            with self.assertRaises(tl_merge.TimelineWriteError) as caught:
                tl_merge.merge_subtitles(self.path, subs, snapshot=snap,
                                        expected_base=snap["revision"], op_id="asr-eio")
        error = caught.exception
        self.assertTrue(fired)
        self.assertEqual(error.receipt["code"], "outcome_unknown")
        self.assertIn("不能盲提交新 op", str(error))
        self.assertEqual(read_timeline_for_write(self.path)["document"]["subtitles"], subs)
        with self.assertRaises(tl_merge.TimelineWriteError) as pending:
            tl_merge.merge_subtitles(self.path, subs, snapshot=snap,
                                    expected_base=snap["revision"], op_id="asr-eio")
        self.assertEqual(pending.exception.receipt["code"], "recovery_required")
        self.assertTrue(timeline_store_status(self.path, recover=True)["ok"])
        before = tree(self.root)
        tl_merge.merge_subtitles(self.path, subs, snapshot=snap,
                                expected_base=snap["revision"], op_id="asr-eio")
        self.assertEqual(before, tree(self.root))
        mismatch = copy.deepcopy(error.request)
        mismatch["document"]["subtitles"][0]["text"] = "different"
        self.assertEqual(timeline_write_plan(self.path, mismatch)["code"], "idempotency.conflict")
        self.assertEqual(before, tree(self.root))

    def test_invalid_document_no_legacy_fallback(self):
        # Reconstruct the previous ASR fixture's invalid schema, keeping its
        # extensions. Strengthening that fixture must not add a legacy bypass.
        old = json.loads((ROOT / "apps/asr/tests/fixtures/timeline_fixture.json").read_bytes())
        old.pop("version")
        for track in old["video_tracks"]:
            track.pop("name")
            for clip in track["clips"]:
                for key in ("dst_in", "dur", "src_in", "src_out"):
                    clip.pop(key)
        for track in old["audio_tracks"]:
            track.pop("name")
            track.pop("events")
        self.save(old)
        before = tree(self.root)
        with self.assertRaises(RuntimeError):
            tl_merge.merge_subtitles(self.path, [])
        self.assertEqual(before, tree(self.root))


if __name__ == "__main__":
    unittest.main()
