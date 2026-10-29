"""JSON-value boundaries exercised through real v2 plans, CLI and journals."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from vtx.api import timeline_transaction as tx, timeline_write_plan as plan, timeline_store_status as status
from vtx.timeline_structure import json_equal, strict_loads
from tests.contracts.test_timeline_store import document, sha
from tests.contracts.test_timeline_write import request, tree, ENV


class WriteJSONValues(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="framivra-write-json-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "timeline.json"
        self.doc = document(ids=True)
        self.doc["_vtx"] = dict(protocol=1, project_id="p", head="old")
        self.clip = self.doc["video_tracks"][0]["clips"][0]["id"]
        self.sub = self.doc["subtitles"][0]["id"]
        self.vendor = {"nested": [{"flag": 1}, [0, None, "keep"]], "other": "保留"}
        self.doc["video_tracks"][0]["clips"][0]["vendor"] = copy.deepcopy(self.vendor)
        self.doc["project"]["vendor"] = copy.deepcopy(self.vendor)
        self.doc["vendor"] = copy.deepcopy(self.vendor)
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.doc, ensure_ascii=False, indent=2), encoding="utf-8")

    def req(self, operations, owned=None, op="reconcile"):
        extra = dict(operations=operations)
        if op == "reconcile":
            extra.update(owner="subtitle-writer", owned_ids=owned or [self.sub], new_ids=[])
        return request(self.doc, sha(self.path.read_bytes()), op, "change", **extra)

    def cli(self, req):
        path = self.root / "request.json"
        path.write_text(json.dumps(req))
        child = subprocess.run([sys.executable, "-B", "-m", "vtx", "-t", str(self.path),
                                "store", "--request", str(path)], env=ENV,
                               capture_output=True, text=True, timeout=30)
        return child.returncode, json.loads(child.stdout)

    def test_nonowned_nested_dict_list_project_and_real_value_controls(self):
        for target in ({"id": self.clip}, {"target": "project"}):
            for value in (True, False, 2):
                for leaf in ("dict", "list"):
                    with self.subTest(target=target, value=value, leaf=leaf):
                        changed = copy.deepcopy(self.vendor)
                        if leaf == "dict":
                            changed["nested"][0]["flag"] = value
                        else:
                            changed["nested"][1][0] = value
                        req = self.req([dict(op="parameters.update", **target, path=["vendor"], value=changed)])
                        before = tree(self.root)
                        self.assertEqual(plan(self.path, req)["code"], "reconcile.scope")
                        self.assertEqual(tree(self.root), before)
                        raw = self.path.read_bytes()
                        code, result = self.cli(req)
                        self.assertEqual((code, result["code"]), (1, "reconcile.scope"))
                        self.assertEqual(self.path.read_bytes(), raw)
                        self.assertTrue(status(self.path)["ok"])

    def test_root_projection_uses_same_deep_json_scope_policy(self):
        # Root has no public semantic mutator; cover its projection boundary
        # directly as well as the public rejection/preservation test below.
        from vtx.timeline_write import reconciliation
        req = self.req([dict(op="parameters.update", id=self.sub, path=["text"], value="real")])
        for value in (True, 2):
            changed = copy.deepcopy(self.doc)
            changed["vendor"]["nested"][0]["flag"] = value
            with self.assertRaises(ValueError) as raised:
                reconciliation(self.doc, changed, req)
            self.assertEqual(raised.exception.code, "reconcile.scope")
        reordered = copy.deepcopy(self.doc)
        reordered["vendor"] = dict(reversed(list(self.vendor.items())))
        self.assertEqual(reconciliation(self.doc, reordered, req)["changes"], [])

    def test_root_is_not_a_semantic_target_and_root_history_is_checked(self):
        req = self.req([dict(op="parameters.update", target="root", path=["vendor"], value=True)])
        raw = self.path.read_bytes()
        self.assertFalse(plan(self.path, req)["ok"])
        self.assertFalse(tx(self.path, req)["ok"])
        self.assertEqual(self.path.read_bytes(), raw)
        # The same root data remains preserved through an ordinary owned edit.
        req = self.req([dict(op="parameters.update", id=self.sub, path=["text"], value="real")])
        self.assertTrue(tx(self.path, req)["ok"])
        self.assertTrue(json_equal(json.loads(self.path.read_bytes())["vendor"], self.vendor))
        opdir = next(Path(status(self.path)["evidence_path"]).glob("op-*"))
        after = json.loads((opdir / "after.bin").read_bytes())
        after["vendor"]["nested"][0]["flag"] = True
        (opdir / "after.bin").write_text(json.dumps(after))
        self.assert_bad_history(req)

    def test_owned_real_change_has_typed_changed_ids_and_ranges_in_plan_commit_replay(self):
        changed = copy.deepcopy(self.vendor)
        changed["nested"] = [{"flag": True}, [False, None, "keep"]]
        req = self.req([dict(op="parameters.update", id=self.clip, path=["vendor"], value=changed)], [self.clip])
        predicted = plan(self.path, req)
        code, result = self.cli(req)
        self.assertEqual(code, 0, result)
        track = self.doc["video_tracks"][0]["id"]
        for item in (predicted, result, tx(self.path, req), plan(self.path, req)["result"]):
            self.assertTrue(item["ok"], item)
            self.assertEqual(item["edit"]["changed_ids"], sorted([track, self.clip]))
            self.assertEqual(item["reconciliation"]["changes"], sorted([track, self.clip]))
            ranges = {r["id"]: r for r in item["edit"]["affected_ranges"]}
            self.assertEqual(ranges[self.clip]["before"], [0, 30])
            self.assertEqual(ranges[self.clip]["after"], [0, 30])
        actual = json.loads(self.path.read_bytes())
        self.assertIs(actual["video_tracks"][0]["clips"][0]["vendor"]["nested"][0]["flag"], True)
        self.assertTrue(status(self.path)["ok"])

    def test_project_source_parent_and_known_parameter_causal_ranges(self):
        for target in ("project", "source", "parent", "known"):
            with self.subTest(target=target):
                self.path = self.root / (target + ".json")
                self.doc["sources"][0]["vendor"] = {"flag": 1}
                self.doc["video_tracks"][0]["vendor"] = {"flag": 1}
                self.save()
                op = dict(op="parameters.update", path=["vendor"], value={"flag": True})
                if target == "project":
                    op.update(target="project")
                elif target == "source":
                    op.update(id="s")
                elif target == "parent":
                    op.update(id=self.doc["video_tracks"][0]["id"])
                else:
                    op.update(id=self.clip, path=["opacity"], value=0.5)
                req = self.req([op], op="edit")
                result = tx(self.path, req)
                self.assertTrue(result["ok"], result)
                ranges = {r["id"]: r for r in result["edit"]["affected_ranges"]}
                self.assertIn(self.clip, ranges)
                if target != "known":
                    self.assertIn("reason", ranges[self.clip])
                self.assertTrue(status(self.path)["ok"])
                self.assertTrue(tx(self.path, req)["replayed"])

    def test_object_key_order_ignored_array_order_retained(self):
        reordered = dict(reversed(list(self.vendor.items())))
        req = self.req([dict(op="parameters.update", id=self.clip, path=["vendor"], value=reordered)])
        predicted = plan(self.path, req)
        self.assertTrue(predicted["ok"], predicted)
        self.assertEqual(predicted["reconciliation"]["changes"], [])
        self.assertTrue(tx(self.path, req)["ok"])
        self.assertTrue(status(self.path)["ok"])
        reordered["nested"].reverse()
        req = self.req([dict(op="parameters.update", id=self.clip, path=["vendor"], value=reordered)])
        req["op_id"] = "array-order"
        self.assertEqual(plan(self.path, req)["code"], "reconcile.scope")
        self.assertEqual(tx(self.path, req)["code"], "reconcile.scope")

    def assert_bad_history(self, req):
        before = tree(self.root)
        plan_req = req if "protocol" in req else request(
            self.doc, req["base_revision"], "edit", "probe",
            operations=[dict(op="parameters.update", id=self.sub, path=["text"], value="probe")])
        for result in (status(self.path), status(self.path, recover=True),
                       plan(self.path, plan_req), tx(self.path, req)):
            self.assertEqual(result["code"], "recovery_required", result)
        self.assertEqual(tree(self.root), before)

    def test_typed_receipts_reject_status_plan_replay_without_writes(self):
        req = self.req([dict(op="parameters.update", id=self.sub, path=["text"], value="real")])
        self.assertTrue(tx(self.path, req)["ok"])
        opdir = next(Path(status(self.path)["evidence_path"]).glob("op-*"))
        for filename in ("intent.json", "commit.json"):
            path = opdir / filename
            original = path.read_bytes()
            for keys, value in ((["ok"], 1), (["detached_copy"], 0), (["detached_copy"], True),
                                (["edit", "requires_render_validation"], 1),
                                (["edit", "affected_ranges", 0, "before", 0], False)):
                with self.subTest(file=filename, keys=keys):
                    data = json.loads(original)
                    cursor = data["result"] if filename == "intent.json" else data
                    for key in keys[:-1]:
                        cursor = cursor[key]
                    cursor[keys[-1]] = value
                    path.write_text(json.dumps(data))
                    self.assert_bad_history(req)
                    path.write_bytes(original)
            # JSON object property order alone is not corrupt evidence.
            data = json.loads(original)
            path.write_text(json.dumps(dict(reversed(list(data.items())))))
            self.assertTrue(status(self.path)["ok"])
            self.assertTrue(tx(self.path, req)["replayed"])
            path.write_bytes(original)

    def test_v1_typed_receipt_rejected_without_resigning_valid_history(self):
        req = dict(operation="apply", op_id="v1", base_revision=sha(self.path.read_bytes()),
                   changes=[dict(id=self.sub, set={"text": "v1"})])
        self.assertTrue(tx(self.path, req)["ok"])
        opdir = next(Path(status(self.path)["evidence_path"]).glob("op-*"))
        for name in ("intent.json", "commit.json"):
            path = opdir / name
            original = path.read_bytes()
            data = json.loads(original)
            (data["result"] if name == "intent.json" else data)["ok"] = 1
            path.write_text(json.dumps(data))
            self.assert_bad_history(req)
            path.write_bytes(original)
        before = tree(self.root)
        self.assertTrue(tx(self.path, req)["replayed"])
        self.assertEqual(tree(self.root), before)

    def test_json_numbers_actual_writable_policy_and_non_json_inputs(self):
        for old, new in ((1, 1.0), (0, -0.0), (2**220, 2**220), (0.1, 0.1), (1e-7, 1e-7)):
            with self.subTest(old=old, new=new):
                self.doc["video_tracks"][0]["clips"][0]["vendor"] = {"number": old}
                self.save()
                req = self.req([dict(op="parameters.update", id=self.clip, path=["vendor"], value={"number": new})])
                req["op_id"] = "number-" + str(old)
                predicted = plan(self.path, req)
                self.assertTrue(predicted["ok"], predicted)
                self.assertEqual(predicted["reconciliation"]["changes"], [])
                self.assertTrue(tx(self.path, req)["ok"])
                self.assertTrue(status(self.path)["ok"])
        for value in ((1, 0), {1: "not-string"}, float("nan"), float("inf")):
            req = self.req([dict(op="parameters.update", id=self.clip, path=["vendor"], value=value)], [self.clip])
            req["op_id"] = "bad"
            before = tree(self.root)
            self.assertFalse(plan(self.path, req)["ok"])
            self.assertFalse(tx(self.path, req)["ok"])
            self.assertEqual(tree(self.root), before)
        for token in (b"0.12345678901234567890123456789", b"1e-9999", b"1e9999"):
            with self.assertRaises(ValueError):
                strict_loads(token)

    def test_shared_comparator_deep_types_and_order(self):
        self.assertFalse(json_equal({"a": [1, {"b": 0}]}, {"a": [True, {"b": False}]}))
        self.assertTrue(json_equal({"a": [1, 0], "b": None}, {"b": None, "a": [1.0, -0.0]}))
        self.assertFalse(json_equal([1, 2], [2, 1]))
        self.assertFalse(json_equal([1], (1,)))
        self.assertFalse(json_equal({1: "a"}, {1: "a"}))


if __name__ == "__main__":
    unittest.main()
