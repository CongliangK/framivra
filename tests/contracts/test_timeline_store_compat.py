"""Regressions for filesystem addressing and the opt-in managed validation gate."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from vtx.api import inspect_timeline, query_timeline, timeline_transaction, timeline_store_status
from vtx import timeline_store as store
from vtx.pipeline.infra import Ctx
from vtx.pipeline.stages import validate
from tests.contracts.test_timeline_store import document, sha

ROOT = Path(__file__).resolve().parents[2]


class FilesystemAddressing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="framivra-path-compat-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "local").mkdir()
        (self.root / "elsewhere" / "sub").mkdir(parents=True)
        (self.root / "local" / "link").symlink_to(self.root / "elsewhere" / "sub", target_is_directory=True)
        self.local = self.root / "local" / "timeline.json"
        self.target = self.root / "elsewhere" / "timeline.json"
        self.via = self.root / "local" / "link" / ".." / "timeline.json"
        self.raw = json.dumps(document(), ensure_ascii=False).encode("utf-8")
        self.local.write_bytes(self.raw)
        self.target.write_bytes(self.raw)

    def request(self, operation, op_id, **kw):
        return dict(operation=operation, op_id=op_id,
                    base_revision=inspect_timeline(self.via)["content_version"], **kw)

    def test_absolute_and_relative_reads_writes_recovery_share_actual_target(self):
        # Equal SHA must not disguise a write to the lexical (wrong) project.
        relative = Path(os.path.relpath(self.root)) / "local" / "link" / ".." / "timeline.json"
        for via in (self.via, relative):
            with self.subTest(via=str(via)):
                self.assertEqual(store._canonical(via), self.target)
                self.assertEqual(store._read(store._canonical(via)), self.target.read_bytes())
                self.assertEqual(inspect_timeline(via), inspect_timeline(self.target))
                self.assertEqual(query_timeline(via, frame=0), query_timeline(self.target, frame=0))
        req = self.request("adopt", "adopt")
        adopted = timeline_transaction(relative, req)
        self.assertTrue(adopted["ok"], adopted)
        self.assertNotEqual(self.target.read_bytes(), self.raw)
        self.assertEqual(self.local.read_bytes(), self.raw)
        self.assertTrue(timeline_transaction(self.target, req)["replayed"])
        home = timeline_store_status(self.target)["evidence_path"]
        self.assertEqual(timeline_store_status(relative)["evidence_path"], home)
        self.assertFalse(store._location(self.local).exists())
        sid = json.loads(self.target.read_bytes())["subtitles"][0]["id"]
        req = self.request("apply", "apply", changes=[{"id": sid, "set": {"text": "changed"}}])
        def fault(name):
            if name == "replaced":
                raise OSError("injected post-replace crash")
        with patch.object(store, "_checkpoint", fault):
            result = timeline_transaction(self.via, req)
        self.assertEqual(result["code"], "outcome_unknown", result)
        after = self.target.read_bytes()
        self.assertEqual(json.loads(after)["subtitles"][0]["text"], "changed")
        for via in (relative, self.target, self.via):
            rec = timeline_store_status(via, recover=True)
            self.assertTrue(rec["ok"], rec)
            self.assertEqual(rec["evidence_path"], home)
            self.assertFalse(rec["pending"])
            self.assertEqual(self.target.read_bytes(), after)
            self.assertEqual(self.local.read_bytes(), self.raw)
            self.assertEqual(inspect_timeline(via), inspect_timeline(self.target))
        self.assertTrue(timeline_transaction(self.target, req)["replayed"])

    def test_two_processes_normal_and_dotdot_alias_same_base_one_commit(self):
        req = self.request("adopt", "parallel")
        code = """import json,sys
from vtx.api import timeline_transaction
req=json.loads(sys.argv[2]); req['op_id']=sys.argv[3]
sys.stdin.readline()
print(json.dumps(timeline_transaction(sys.argv[1],req)))
"""
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        procs = [subprocess.Popen([sys.executable, "-c", code, str(via), json.dumps(req), "parallel-" + str(i)],
                                 cwd=self.root, env=env, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for i, via in enumerate((self.target, self.via))]
        results = []
        try:
            for proc in procs:
                proc.stdin.write("go\n"); proc.stdin.flush()
            for proc in procs:
                out, err = proc.communicate(timeout=20)
                self.assertEqual(proc.returncode, 0, err)
                results.append(json.loads(out))
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill(); proc.communicate()
        self.assertEqual(sum(r["ok"] for r in results), 1, results)
        self.assertEqual([r["code"] for r in results if not r["ok"]], ["conflict"])
        self.assertEqual(self.local.read_bytes(), self.raw)
        self.assertFalse(store._location(self.local).exists())

    def test_missing_actual_target_never_falls_back_to_existing_wrong_project(self):
        self.target.unlink()
        self.assertFalse(inspect_timeline(self.via)["ok"])
        req = dict(operation="adopt", op_id="missing", base_revision=None)
        for via in (self.via, Path(os.path.relpath(self.root)) / "local" / "link" / ".." / "timeline.json"):
            result = timeline_transaction(via, req)
            self.assertEqual(result["code"], "input.missing", result)
            self.assertFalse(self.target.exists())
            self.assertEqual(self.local.read_bytes(), self.raw)
        created = timeline_transaction(self.via, dict(operation="create", op_id="create",
                                       base_revision=None, document=document(ids=True)))
        self.assertTrue(created["ok"], created)
        self.assertTrue(self.target.exists())
        self.assertEqual(self.local.read_bytes(), self.raw)

    def test_broken_parent_and_leaf_aliases_fail_closed(self):
        broken = self.root / "local" / "broken"
        broken.symlink_to(self.root / "absent" / "sub", target_is_directory=True)
        via = broken / ".." / "timeline.json"
        self.assertFalse(inspect_timeline(via)["ok"])
        self.assertFalse(timeline_transaction(via, self.request("adopt", "broken"))["ok"])
        self.assertFalse(timeline_store_status(via, recover=True)["ok"])
        self.assertEqual(self.local.read_bytes(), self.raw)
        self.assertFalse(store._location(self.local).exists())
        self.target.unlink()
        self.target.symlink_to(self.local)
        for request in (None, self.request("adopt", "leaf")):
            result = timeline_store_status(self.via) if request is None else timeline_transaction(self.via, request)
            self.assertEqual(result["code"], "path.alias", result)
        self.target.unlink()
        os.link(self.local, self.target)
        self.assertEqual(timeline_store_status(self.via)["code"], "path.alias")
        self.assertEqual(self.local.read_bytes(), self.raw)


class ManagedValidationCompatibility(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="framivra-managed-compat-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "timeline.json"

    def save(self, doc, *, precision=False):
        payload = json.dumps(doc, ensure_ascii=True)
        if precision:
            payload = payload[:-1] + ',"x_vendor":{"exact":0.12345678901234567890123456789}}'
        self.path.write_text(payload, encoding="utf-8")
        return self.path.read_bytes()

    def run_validate(self):
        c = Ctx(self.path, diagnostics=[])
        # This is a unit gate test. Actual WAV + unmocked CLI probes run separately.
        with contextlib.redirect_stdout(io.StringIO()), \
                patch.object(c, "probe", return_value={"has_video": False, "has_audio": True, "nb_frames": 90}), \
                patch("vtx.pipeline.stages._plugin_hooks.run_probe_source", return_value=None):
            try:
                validate(c)
                return True, c.diagnostics
            except SystemExit as error:
                self.assertEqual(error.code, 1)
                return False, c.diagnostics

    def audio_document(self, sid="s"):
        doc = document()
        doc["sources"][0].update(id=sid, path="a.wav")
        (self.path.parent / "a.wav").write_bytes(b"unit-probe-placeholder")
        doc["video_tracks"] = []
        doc["audio_tracks"][0]["events"][0]["src"] = sid
        return doc

    def test_legacy_unicode_and_precision_validate_but_adopt_refuses_unchanged(self):
        for sid, precision in (("口播 main", False), ("s", True)):
            with self.subTest(sid=sid, precision=precision):
                before = self.save(self.audio_document(sid), precision=precision)
                with patch("vtx.timeline_structure.strict_loads", side_effect=AssertionError("legacy must not use write parser")):
                    ok, records = self.run_validate()
                self.assertTrue(ok, records)
                self.assertTrue(inspect_timeline(self.path)["ok"])
                self.assertEqual(self.path.read_bytes(), before)
                result = timeline_transaction(self.path, dict(operation="adopt", op_id="incompatible",
                                              base_revision=sha(before)))
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["code"], "input.json" if precision else "input.structure")
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(json.loads(before)["sources"][0]["id"], sid)

    def test_compatible_adoption_preserves_source_id_refs_and_unknown_extensions(self):
        doc = self.audio_document("source.valid-1:take")
        before = self.save(doc)
        adopted = timeline_transaction(self.path, dict(operation="adopt", op_id="adopt", base_revision=sha(before)))
        self.assertTrue(adopted["ok"], adopted)
        after = json.loads(self.path.read_bytes())
        for _, obj, kind in store.managed_objects(after):
            if kind != "source":
                del obj["id"]
        del after["_vtx"]
        self.assertEqual(after, doc)
        ok, records = self.run_validate()
        self.assertTrue(ok, records)

    def test_managed_bad_or_missing_metadata_fields_never_fall_back(self):
        good = self.audio_document()
        for i, (_, obj, kind) in enumerate(store.managed_objects(good)):
            if kind != "source":
                obj["id"] = "id-" + str(i)
        meta = {"protocol": 1, "project_id": "p", "head": "h"}
        bad_values = [None, False, [], {}, "legacy", dict(meta, protocol=2), dict(meta, protocol=True),
                      dict(meta, extra="x"), *[{k: v for k, v in meta.items() if k != missing}
                                               for missing in meta]]
        for value in bad_values:
            with self.subTest(meta=value):
                self.save(dict(good, _vtx=value))
                ok, records = self.run_validate()
                self.assertFalse(ok)
                self.assertTrue(any(r["rule_id"] == "validate.stored_structure" for r in records), records)
        for mutate in (lambda d: d["sources"][0].update(id="口播 main"),
                       lambda d: d["audio_tracks"][0].pop("id"),
                       lambda d: d["subtitles"][0].update(id="s")):
            doc = copy.deepcopy(good); doc["_vtx"] = meta; mutate(doc)
            self.save(doc)
            self.assertFalse(self.run_validate()[0])
        self.save(dict(good, _vtx=meta), precision=True)
        self.assertFalse(self.run_validate()[0])


if __name__ == "__main__":
    unittest.main()
