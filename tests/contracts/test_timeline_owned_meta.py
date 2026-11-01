"""Explicit root metadata + stable-ID reconciliation, never a second publisher."""
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from vtx.api import timeline_transaction as tx, timeline_write_plan as plan, timeline_store_status as status
from vtx import timeline_store as store
from vtx.timeline_structure import json_equal
from vtx.timeline_write import reconciliation
from tests.contracts.test_timeline_edit import fixture
from tests.contracts.test_timeline_store import sha
from tests.contracts.test_timeline_write import request, tree, ENV


class OwnedMeta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="framivra-owned-meta-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "时间线.json"
        self.doc = fixture()
        self.doc["_vtx"] = dict(protocol=1, project_id="owned-meta", head="old")
        self.doc["_meta"] = {"sibling": {"nested": [1, {"flag": 0}]}}
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.doc, ensure_ascii=False, indent=2), encoding="utf-8")

    def req(self, operations=None, **extra):
        values = dict(owner="duck-static", owned_ids=["be"], new_ids=[],
                      operations=[] if operations is None else operations,
                      owned_meta_keys=["bed_static_duck"], meta_updates={"bed_static_duck": {"baked": True}})
        values.update(extra)
        return request(self.doc, sha(self.path.read_bytes()), "reconcile", "duck", **values)

    def cli(self, req, planning=False):
        path = self.root / "request.json"
        path.write_text(json.dumps(req), encoding="utf-8")
        child = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve().parents[2] / "vtx/vtx.py"), "-t", str(self.path), "store",
                                *(["plan"] if planning else []), "--request", str(path)],
                               env=ENV, capture_output=True, text=True, timeout=30)
        return child.returncode, json.loads(child.stdout)

    def test_duck_split_volume_marker_one_candidate_one_publish_and_undo(self):
        # Hand-computed [0,100) bed -> [0,30), [30,70), [70,100).
        ops = [dict(op="block.split", id="be", at=30, new_id="duck-middle"),
               dict(op="block.split", id="duck-middle", at=70, new_id="duck-right"),
               dict(op="parameters.update", id="duck-middle", path=["volume"], value=0.05)]
        marker = {"params": {"track": "bed", "voice_track": "voice"}, "baked": True,
                  "unknown": {"id": "be", "_vtx": {"head": "not-a-global-id"}}}
        req = self.req(ops, new_ids=["duck-middle", "duck-right"], meta_updates={"bed_static_duck": marker})
        saved = copy.deepcopy(req)
        before = self.path.read_bytes()
        previous = tree(self.root)
        predicted = plan(self.path, req)
        self.assertTrue(predicted["ok"], predicted)
        self.assertEqual(previous, tree(self.root))
        with patch.object(store.os, "replace", wraps=store.os.replace) as replace:
            result = tx(self.path, req)
        self.assertTrue(result["ok"], result)
        self.assertEqual(sum(Path(c.args[1]) == self.path for c in replace.call_args_list), 1)
        self.assertEqual(req, saved)
        self.assertEqual(predicted["revision"], result["revision"])
        actual = json.loads(self.path.read_bytes())
        events = actual["audio_tracks"][1]["events"]
        self.assertEqual([(e["id"], e["in"], e["out"], e["volume"]) for e in events],
                         [("be", 0, 30, 0.2), ("duck-middle", 30, 70, 0.05), ("duck-right", 70, 100, 0.2)])
        self.assertEqual(actual["_meta"], dict(self.doc["_meta"], bed_static_duck=marker))
        self.assertEqual(actual["sources"], self.doc["sources"])
        self.assertEqual(actual["project"], self.doc["project"])
        self.assertEqual(result["reconciliation"]["changed_meta_keys"], ["bed_static_duck"])
        self.assertEqual(result["reconciliation"]["creates"], ["duck-middle", "duck-right"])
        self.assertEqual(result["edit"]["changed_ids"], ["be", "bed"])
        evidence = Path(status(self.path)["evidence_path"])
        self.assertEqual(len(list(evidence.glob("op-*/intent.json"))), 1)
        self.assertEqual(len(list(evidence.glob("op-*/commit.json"))), 1)
        stable = tree(self.root)
        self.assertTrue(tx(self.path, req)["replayed"])
        self.assertTrue(plan(self.path, req)["replayed"])
        self.assertEqual(stable, tree(self.root))
        undo = request(actual, result["revision"], "undo", "undo-duck", head="duck")
        self.assertTrue(tx(self.path, undo)["ok"])
        expected = json.loads(before)
        expected["_vtx"]["head"] = "undo-duck"
        self.assertTrue(json_equal(json.loads(self.path.read_bytes()), expected))

    def test_meta_only_missing_container_literal_keys_and_null(self):
        del self.doc["_meta"]
        # Strings resembling paths and other data are literal keys/values.
        self.doc["project"]["bed_static_duck"] = "unchanged"
        self.doc["sources"][0]["bed_static_duck"] = "unchanged"
        self.save()
        updates = {"bed_static_duck": None, "project/bed.static": {"id": "s", "_vtx": [True, 1]}}
        req = self.req(owned_meta_keys=list(updates), meta_updates=updates)
        previous = tree(self.root)
        code, predicted = self.cli(req, planning=True)
        # CLI request creation is caller-owned; plan itself must not initialize.
        self.assertEqual(code, 0, predicted)
        self.assertFalse(any(p.name.startswith(".vtx-store") for p in self.root.iterdir()))
        code, result = self.cli(req)
        self.assertEqual(code, 0, result)
        actual = json.loads(self.path.read_bytes())
        self.assertEqual(actual["_meta"], updates)
        self.assertEqual(actual["project"], self.doc["project"])
        self.assertEqual(actual["sources"], self.doc["sources"])
        self.assertEqual(result["edit"]["changed_ids"], [])
        self.assertEqual(result["reconciliation"]["changed_meta_keys"], sorted(updates))
        self.assertTrue(status(self.path)["ok"])

    def test_real_duck_first_marker_only_zero_db_no_voice_window(self):
        self.doc["audio_tracks"][0]["events"][0].update({"in": 100, "out": 120})
        self.doc["audio_tracks"][1]["events"][0].update(src_in=0, volume=0.5)
        self.save()
        marker = {"params": {"global_db": 0, "duck_db": 0, "track": "bed", "voice_track": "voice"}}
        req = self.req(meta_updates={"bed_static_duck": marker})
        before = self.path.read_bytes()
        predicted = plan(self.path, req)
        self.assertTrue(predicted["ok"], predicted)
        result = tx(self.path, req)
        self.assertTrue(result["ok"], result)
        actual, expected = json.loads(self.path.read_bytes()), json.loads(before)
        expected["_meta"]["bed_static_duck"] = marker
        expected["_vtx"]["head"] = "duck"
        self.assertTrue(json_equal(actual, expected))
        for item in (predicted, result):
            self.assertEqual(item["edit"]["changed_ids"], [])
            self.assertEqual(item["edit"]["affected_ranges"], [])
            self.assertEqual(item["reconciliation"]["changes"], [])
            self.assertEqual(item["reconciliation"]["changed_meta_keys"], ["bed_static_duck"])

    def test_cli_negative_nonowned_change_and_invalid_scope(self):
        raw = self.path.read_bytes()
        for req in (self.req(owned_meta_keys=[]), self.req([
                dict(op="source.update", id="s", set={"path": "forbidden.wav"})])):
            code, result = self.cli(req)
            self.assertEqual(code, 1, result)
            self.assertFalse(result["ok"])
            self.assertEqual(self.path.read_bytes(), raw)

    def test_typed_values_presence_order_and_equal_numbers(self):
        for old, new, changed in [(1, True, True), (False, 0, True), (1, 1.0, False),
                                  ({"a": 1, "b": [0]}, {"b": [-0.0], "a": 1.0}, False),
                                  ([1, 2], [2, 1], True), (None, None, False)]:
            with self.subTest(old=old, new=new):
                self.path = self.root / ("case-" + str(len(list(self.root.glob("*.json")))) + ".json")
                self.doc["_meta"]["bed_static_duck"] = old
                self.save()
                req = self.req(meta_updates={"bed_static_duck": new})
                predicted = plan(self.path, req)
                self.assertTrue(predicted["ok"], predicted)
                self.assertEqual(predicted["reconciliation"]["changed_meta_keys"], ["bed_static_duck"] if changed else [])
                self.assertTrue(tx(self.path, req)["ok"])
                self.assertTrue(status(self.path)["ok"])

    def test_bad_scope_pairs_actions_and_non_json_rejected_zero_write(self):
        base = self.req()
        bad = []
        for scope in ([], [""], [" "], [1], [None], [True], ["x", "x"], ["_meta"], ["_vtx"]):
            bad.append(dict(base, owned_meta_keys=scope))
        for updates in ({}, [], {"sibling": 1}, {"bed_static_duck": float("nan")}, {1: "bad"}, {"bed_static_duck": (1,)}):
            bad.append(dict(base, meta_updates=updates))
        for field in ("owned_meta_keys", "meta_updates"):
            value = dict(base)
            del value[field]
            bad.append(value)
        bad += [dict(base, identity_mode="preserve-unmanaged", purpose="legacy-write"),
                dict(base, operation="edit"), dict(base, operation="replace"),
                dict(base, meta_remove=["bed_static_duck"])]
        for req in bad:
            with self.subTest(req=req):
                previous = tree(self.root)
                self.assertFalse(plan(self.path, req)["ok"])
                self.assertFalse(tx(self.path, req)["ok"])
                self.assertEqual(previous, tree(self.root))

    def test_bad_meta_container_never_coerced(self):
        for value in (None, [], 0, False, "marker"):
            self.doc["_meta"] = value
            self.save()
            req = self.req()
            previous = tree(self.root)
            self.assertEqual(plan(self.path, req)["code"], "reconcile.meta_container")
            self.assertEqual(previous, tree(self.root))
            self.assertEqual(tx(self.path, req)["code"], "reconcile.meta_container")
            self.assertEqual(json.loads(self.path.read_bytes())["_meta"], value)

    def test_nonowned_deep_scope_project_source_and_identity(self):
        req = self.req()
        paths = [("_meta", "sibling", "nested", 0), ("_meta", "sibling", "nested", 1, "flag"),
                 ("project", "fps"), ("sources", 0, "extension", "nested", 2),
                 ("unknown_root", "nested", "anything", 0), ("_vtx", "protocol")]
        for path in paths:
            changed = copy.deepcopy(self.doc)
            changed["_meta"]["bed_static_duck"] = {"baked": True}
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = True
            with self.subTest(path=path), self.assertRaises(ValueError):
                reconciliation(self.doc, changed, req)
        for value in (None, [], {}, {"bed_static_duck": {"baked": True}}):
            changed = copy.deepcopy(self.doc)
            changed["_meta"] = value
            with self.subTest(container=value), self.assertRaises(ValueError):
                reconciliation(self.doc, changed, req)
        for op in (dict(op="parameters.update", target="project", path=["fps"], value=24),
                   dict(op="source.update", id="s", set={"path": "changed.mov"}),
                   dict(op="parameters.update", target="root", path=["_meta"], value={})):
            previous = tree(self.root)
            self.assertFalse(plan(self.path, self.req([op]))["ok"])
            self.assertEqual(previous, tree(self.root))

    def test_old_contract_empty_batch_and_new_scope_idempotency_identity(self):
        req = self.req()
        old = {k: v for k, v in req.items() if k not in ("owned_meta_keys", "meta_updates")}
        self.assertFalse(plan(self.path, old)["ok"])
        self.assertTrue(tx(self.path, req)["ok"])
        for changed in (dict(req, owned_meta_keys=["bed_static_duck", "other"]),
                        dict(req, meta_updates={"bed_static_duck": {"baked": 1}}), old):
            self.assertEqual(tx(self.path, changed)["code"], "idempotency.conflict")

    def assert_bad_history(self, req):
        previous = tree(self.root)
        for result in (status(self.path), status(self.path, recover=True), plan(self.path, req), tx(self.path, req)):
            self.assertEqual(result["code"], "recovery_required", result)
        self.assertEqual(previous, tree(self.root))

    def test_forged_receipts_missing_changes_or_typed_scope_rejected(self):
        req = self.req()
        self.assertTrue(tx(self.path, req)["ok"])
        home = Path(status(self.path)["evidence_path"])
        for filename in ("intent.json", "commit.json"):
            path = next(home.glob("op-*/" + filename))
            original = path.read_bytes()
            for field, value in (("changed_meta_keys", []), ("changed_meta_keys", [True]),
                                 ("owned_meta_keys", ["sibling"])):
                data = json.loads(original)
                result = data["result"] if filename == "intent.json" else data
                result["reconciliation"][field] = value
                path.write_text(json.dumps(data))
                self.assert_bad_history(req)
                path.write_bytes(original)
            data = json.loads(original)
            result = data["result"] if filename == "intent.json" else data
            del result["reconciliation"]["changed_meta_keys"]
            path.write_text(json.dumps(data))
            self.assert_bad_history(req)
            path.write_bytes(original)

    def test_frozen_after_metadata_payload_typed_mismatch_even_with_rehashed_receipts(self):
        req = self.req()
        self.assertTrue(tx(self.path, req)["ok"])
        home = Path(status(self.path)["evidence_path"])
        opdir = next(home.glob("op-*"))
        originals = {n: (opdir / n).read_bytes() for n in ("after.bin", "intent.json", "commit.json")}
        changed = json.loads(originals["after.bin"])
        changed["_meta"]["bed_static_duck"]["baked"] = 1  # True != number 1.
        after = store._encode(changed)
        (opdir / "after.bin").write_bytes(after)
        for filename in ("intent.json", "commit.json"):
            data = json.loads(originals[filename])
            if filename == "intent.json":
                data["after_revision"] = sha(after)
                data["result"]["revision"] = sha(after)
            else:
                data["revision"] = sha(after)
            (opdir / filename).write_text(json.dumps(data))
        self.assert_bad_history(req)

    def test_two_writers_scopes_same_base_cas_only_one_wins(self):
        first = self.req()
        second = self.req(owned_meta_keys=["writer2"], meta_updates={"writer2": [False, 0]})
        second["op_id"] = "writer2"
        code = """import json,sys
sys.path.insert(0, sys.argv.pop(1))
from vtx.api import timeline_transaction
sys.stdin.readline()
print(json.dumps(timeline_transaction(sys.argv[1], json.loads(sys.argv[2]))))
"""
        children = [subprocess.Popen([sys.executable, "-I", "-B", "-c", code,
                    str(Path(__file__).resolve().parents[2]), str(self.path), json.dumps(req)],
                    env=ENV, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for req in (first, second)]
        for child in children:
            self.addCleanup(lambda child=child: child.kill() if child.poll() is None else None)
            child.stdin.write("go\n")
            child.stdin.flush()
        results = []
        for child in children:
            out, err = child.communicate(timeout=30)
            self.assertEqual(child.returncode, 0, err)
            results.append(json.loads(out))
        self.assertEqual(sum(r["ok"] for r in results), 1, results)
        self.assertEqual([r["code"] for r in results if not r["ok"]], ["conflict"])
        actual = json.loads(self.path.read_bytes())["_meta"]
        self.assertEqual(sum(k in actual for k in ("bed_static_duck", "writer2")), 1)
        self.assertEqual(actual["sibling"], self.doc["_meta"]["sibling"])
        self.assertTrue(status(self.path)["ok"])

    def test_crash_published_intent_and_replaced_target_original_boundaries(self):
        # Real child SIGKILL at existing shared store boundaries, bounded 2 cases.
        for boundary, committed in (("intent_published", False), ("replaced", True)):
            target = self.root / (boundary + ".json")
            target.write_bytes(self.path.read_bytes())
            req = self.req()
            code = """import json,os,signal,sys
sys.path.insert(0, sys.argv.pop(1))
from vtx import timeline_store as s
from vtx.api import timeline_transaction
path, boundary, req = sys.argv[1:]
def kill(stage):
    if stage == boundary: os.kill(os.getpid(), signal.SIGKILL)
s._checkpoint = kill
timeline_transaction(path, json.loads(req))
"""
            child = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(Path(__file__).resolve().parents[2]), str(target), boundary, json.dumps(req)],
                                   env=ENV, capture_output=True, timeout=30)
            self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr)
            recovered = status(target, recover=True)
            self.assertTrue(recovered["ok"], recovered)
            replay = tx(target, req)
            self.assertEqual(replay["ok"], committed, replay)
            self.assertEqual("bed_static_duck" in json.loads(target.read_bytes())["_meta"], committed)


if __name__ == "__main__":
    unittest.main()
