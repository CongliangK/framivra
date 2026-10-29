"""Public v2 write foundation: real temporary files, CLI, processes and faults.

No production writer migration, render, network or pinned artifact claim.
"""
import copy
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from vtx.api import (read_timeline_for_write, timeline_transaction,
                     timeline_write_plan, timeline_store_status)
from vtx import timeline_store as store
from tests.contracts.test_timeline_store import document, sha

ROOT = Path(__file__).resolve().parents[2]
ENV = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")


def request(doc, base, op="replace", op_id="write", **extra):
    managed = isinstance(doc, dict) and "_vtx" in doc
    mode = "managed" if managed else "preserve-unmanaged"
    value = dict(protocol=2, operation=op, op_id=op_id, base_revision=base,
                 identity_mode=mode, payload_mode="document",
                 purpose="create" if op == "create" else "edit" if managed else "legacy-write")
    if op in ("create", "replace"):
        value["document"] = copy.deepcopy(doc)
    return dict(value, **extra)


def tree(root):
    """No atime assertion: filesystem read accounting is not application writing."""
    return {str(p.relative_to(root)): (p.stat().st_mode, p.stat().st_size,
            p.stat().st_mtime_ns, p.stat().st_ctime_ns,
            p.read_bytes() if p.is_file() else None) for p in [root, *root.rglob("*")]}


class WriteV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="framivra-write-v2-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "时间线.json"
        self.doc = document()
        self.save(self.doc)

    def save(self, doc):
        raw = ("\n  " + json.dumps(doc, ensure_ascii=True, indent=2) + "\n").encode()
        self.path.write_bytes(raw)
        return raw

    def snapshot(self):
        return read_timeline_for_write(self.path)

    def replace(self, text="changed", op_id="write"):
        snap = self.snapshot()
        doc = snap["document"]
        doc["subtitles"][0]["text"] = text
        return request(doc, snap["revision"], op_id=op_id)

    def managed(self):
        result = timeline_transaction(self.path, dict(operation="adopt", op_id="adopt",
                                      base_revision=sha(self.path.read_bytes())))
        self.assertTrue(result["ok"], result)
        return json.loads(self.path.read_bytes())

    def exact(self, path=None, op_id="exact", purpose="frozen-copy", raw=None, mode="preserve-unmanaged"):
        return dict(protocol=2, operation="create", op_id=op_id, base_revision=None,
                    identity_mode=mode, payload_mode="exact-import", purpose=purpose,
                    raw_bytes=self.path.read_bytes() if raw is None else raw)

    def test_zero_write_snapshot_single_raw_and_mutable_return_not_live(self):
        before = tree(self.root)
        original = store._read
        with patch.object(store, "_read", wraps=original) as reads, \
             patch("vtx.pipeline.infra.Ctx", side_effect=AssertionError("no Ctx")):
            snap = self.snapshot()
        self.assertEqual(reads.call_count, 1)
        self.assertEqual(snap["raw_bytes"], self.path.read_bytes())
        self.assertEqual(snap["revision"], sha(snap["raw_bytes"]))
        self.assertEqual(snap["document"], self.doc)
        self.assertEqual(snap["identity_mode"], "preserve-unmanaged")
        snap["document"]["project"]["fps"] = 24
        self.assertEqual(before, tree(self.root))
        missing = read_timeline_for_write(self.root / "missing.json")
        self.assertEqual(missing, dict(raw_bytes=None, revision=None, document=None,
                                     identity_mode="preserve-unmanaged"))
        self.assertEqual(before, tree(self.root))

    def test_legacy_unicode_source_and_custom_ids_untouched(self):
        doc = document()
        doc["sources"][0]["id"] = "口播 main"
        doc["video_tracks"][0]["clips"][0]["src"] = "口播 main"
        doc["audio_tracks"][0]["events"][0]["src"] = "口播 main"
        # These are legacy extensions, not protocol global inventory.
        doc["video_tracks"][0]["id"] = "same free string"
        doc["audio_tracks"][0]["id"] = "same free string"
        doc["subtitles"][0]["id"] = "自由字幕 ID"
        before = self.save(doc)
        req = self.replace()
        with patch("vtx.pipeline.infra.Ctx", side_effect=AssertionError("no Ctx")):
            result = timeline_transaction(self.path, req)
        self.assertTrue(result["ok"], result)
        after = json.loads(self.path.read_bytes())
        doc["subtitles"][0]["text"] = "changed"
        self.assertEqual(after, doc)
        self.assertNotIn("_vtx", after)
        self.assertEqual(result["schema"], "framivra.timeline.store/v2")
        self.assertIsNone(result["project_id"])
        home = Path(timeline_store_status(self.path)["evidence_path"])
        intent = json.loads(next(home.glob("op-*/intent.json")).read_bytes())
        self.assertEqual(intent["protocol"], 2)
        self.assertEqual(intent["before_identity_mode"], "preserve-unmanaged")
        undo = request(after, result["revision"], "undo", "undo", undo_of="write")
        self.assertTrue(timeline_transaction(self.path, undo)["ok"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_bad_json_and_metadata_never_missing_or_legacy_fallback(self):
        payloads = [b"null", b'{"x":1,"x":2}', b'{"x":NaN}', b"\xff"]
        base = json.dumps(document()).encode()
        payloads += [base[:-1] + b',"x":0.12345678901234567890123456789}',
                     base[:-1] + b',"x":1e-99999999999999999999999999999}']
        payloads += [json.dumps(dict(document(), _vtx=meta)).encode()
                     for meta in (None, {}, {"protocol": 2, "project_id": "p", "head": "h"})]
        for raw in payloads:
            with self.subTest(raw=raw[:90]):
                self.path.write_bytes(raw)
                before = tree(self.root)
                with self.assertRaises((ValueError, UnicodeError)):
                    self.snapshot()
                req = request(document(), sha(raw))
                self.assertFalse(timeline_write_plan(self.path, req)["ok"])
                self.assertEqual(before, tree(self.root))
                result = timeline_transaction(self.path, req)
                self.assertFalse(result["ok"], result)
                self.assertEqual(self.path.read_bytes(), raw)
        # Duplicate legacy source strings are not vendor extension IDs.
        doc = document()
        doc["sources"].append(copy.deepcopy(doc["sources"][0]))
        self.save(doc)
        with self.assertRaises(ValueError):
            self.snapshot()

    def test_plan_modes_exact_create_and_existing_have_no_side_effects(self):
        for managed in (False, True):
            if managed:
                self.managed()
            before = tree(self.root)
            req = self.replace()
            plan = timeline_write_plan(self.path, req)
            self.assertTrue(plan["ok"], plan)
            self.assertEqual(before, tree(self.root))
            result = timeline_transaction(self.path, req)
            self.assertEqual(plan["revision"], result["revision"])
            before = tree(self.root)
            replay = timeline_write_plan(self.path, req)
            self.assertTrue(replay["replayed"], replay)
            self.assertEqual(before, tree(self.root))
            # Use a new name for subsequent managed pass.
            if not managed:
                self.path = self.root / "managed.json"
                self.save(document())
        for mode in ("preserve-unmanaged", "managed"):
            target = self.root / ("create-" + mode + ".json")
            req = request(document(ids=mode == "managed"), None, "create", mode)
            req["identity_mode"] = mode
            before = tree(self.root)
            plan = timeline_write_plan(target, req)
            self.assertTrue(plan["ok"], plan)
            self.assertEqual(before, tree(self.root))
            self.assertEqual(plan["revision"], timeline_transaction(target, req)["revision"])
        req = self.exact(raw=json.dumps(document()).encode())
        target = self.root / "exact.json"
        before = tree(self.root)
        plan = timeline_write_plan(target, req)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["revision"], sha(req["raw_bytes"]))
        self.assertEqual(before, tree(self.root))

    def test_replay_precedes_cas_but_null_always_rejected_and_modes_digest_bound(self):
        req = self.replace("one")
        one = timeline_transaction(self.path, req)
        two = timeline_transaction(self.path, self.replace("two", "two"))
        self.assertTrue(two["ok"], two)
        replay = timeline_transaction(self.path, req)
        self.assertTrue(replay["replayed"], replay)
        self.assertEqual(replay["revision"], one["revision"])
        self.assertEqual(replay["current_revision"], two["revision"])
        self.assertEqual(json.loads(self.path.read_bytes())["subtitles"][0]["text"], "two")
        for changed in (dict(req, base_revision=two["revision"]),
                        dict(req, identity_mode="managed", purpose="edit"),
                        self.exact(op_id=req["op_id"])):
            self.assertEqual(timeline_transaction(self.path, changed)["code"], "idempotency.conflict")
            self.assertEqual(timeline_write_plan(self.path, changed)["code"], "idempotency.conflict")
        self.path.write_bytes(b"null")
        self.assertEqual(timeline_transaction(self.path, req)["code"], "input.structure")
        self.assertEqual(timeline_write_plan(self.path, req)["code"], "input.structure")

    def test_mixed_history_original_v1_results_and_undo_unchanged(self):
        # v2 unmanaged -> v1 adopt -> v2 edit -> v1 apply -> v2 undo -> v1 replay.
        legacy = timeline_transaction(self.path, self.replace())
        self.assertTrue(legacy["ok"], legacy)
        adopt = dict(operation="adopt", op_id="adopt", base_revision=legacy["revision"])
        adopted = timeline_transaction(self.path, adopt)
        self.assertEqual(adopted["schema"], "framivra.timeline.store/v1")
        doc = self.snapshot()["document"]
        req = request(doc, adopted["revision"], "edit", "edit2", operations=[
            {"op": "parameters.update", "id": doc["subtitles"][0]["id"], "path": ["text"], "value": "v2"}])
        edited = timeline_transaction(self.path, req)
        self.assertTrue(edited["ok"], edited)
        v1 = dict(operation="apply", op_id="v1", base_revision=edited["revision"],
                  changes=[dict(id=doc["subtitles"][0]["id"], set={"text": "v1"})])
        first = timeline_transaction(self.path, v1)
        self.assertTrue(first["ok"], first)
        current = self.snapshot()
        undo = request(current["document"], current["revision"], "undo", "undo2", head="v1")
        self.assertTrue(timeline_transaction(self.path, undo)["ok"])
        replay = timeline_transaction(self.path, v1)
        for key in first.keys() - {"current_revision", "replayed"}:
            self.assertEqual(replay[key], first[key])
        self.assertTrue(replay["replayed"])
        self.assertEqual(json.loads(self.path.read_bytes())["subtitles"][0]["text"], "v2")
        self.assertTrue(timeline_transaction(self.path, adopt)["replayed"])
        legacy_replay = timeline_transaction(self.path, self.replace(op_id="newer"))
        self.assertTrue(legacy_replay["ok"], legacy_replay)

    def test_managed_replace_identity_and_reconcile_owned_semantic_batch(self):
        doc = self.managed()
        sid = doc["subtitles"][0]["id"]
        req = request(doc, sha(self.path.read_bytes()), "reconcile", "reconcile", owner="asr",
                      owned_ids=[sid], new_ids=["asr-new"], operations=[
                          {"op": "parameters.update", "id": sid, "path": ["text"], "value": "owned"},
                          {"op": "block.insert", "track_id": "subtitles", "data": {
                              "id": "asr-new", "in": 30, "out": 60, "text": "new", "vendor": {"deep": True}}}])
        bad = dict(req, owned_ids=["/subtitles/0"])
        self.assertFalse(timeline_write_plan(self.path, bad)["ok"])
        other = dict(req, operations=[{"op": "parameters.update", "target": "project",
                                      "path": ["unknown", "x"], "value": ["lost"]}])
        self.assertFalse(timeline_write_plan(self.path, other)["ok"])
        before = self.path.read_bytes()
        renamed = copy.deepcopy(doc)
        renamed["subtitles"][0]["id"] = "different"
        self.assertEqual(timeline_transaction(self.path, request(renamed, sha(before)))["code"], "input.identity")
        result = timeline_transaction(self.path, req)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["reconciliation"]["owner"], "asr")
        after = self.snapshot()["document"]
        self.assertEqual(after["unknown_root"], doc["unknown_root"])
        self.assertEqual(after["subtitles"][0]["color"], "red")
        self.assertEqual(after["subtitles"][1]["vendor"], {"deep": True})
        self.assertTrue(timeline_transaction(self.path, req)["replayed"])
        # Applying without an owner inventory is still ordinary managed edit;
        # reconciliation does not broaden replace or add implicit mappings.
        latest = self.snapshot()
        req = request(latest["document"], latest["revision"], "reconcile", "bad-scope", owner="asr",
                      owned_ids=[sid], new_ids=[], operations=[
                          {"op": "parameters.update", "id": "asr-new", "path": ["text"], "value": "not owned"}])
        self.assertEqual(timeline_transaction(self.path, req)["code"], "reconcile.scope")

    def test_reconcile_cannot_move_nonowned_child_between_owned_tracks(self):
        doc = self.managed()
        track = doc["video_tracks"][0]
        nonowned = track["clips"][0]["id"]
        ownclip = copy.deepcopy(track["clips"][0])
        ownclip.update(id="owned-old-clip", dst_in=30)
        track["clips"].append(ownclip)
        destination = copy.deepcopy(track)
        destination.update(id="owned-destination", name="destination")
        destination["clips"] = [dict(ownclip, id="owned-new-clip", dst_in=60)]
        doc["video_tracks"].append(destination)
        self.save(doc)
        req = request(doc, sha(self.path.read_bytes()), "reconcile", "relocation", owner="mg",
                      owned_ids=[track["id"], "owned-destination", "owned-old-clip", "owned-new-clip"],
                      new_ids=[], operations=[dict(op="block.move", id=nonowned,
                                                   track_id="owned-destination", at=0)])
        before = self.path.read_bytes()
        self.assertEqual(timeline_write_plan(self.path, req)["code"], "reconcile.scope")
        self.assertEqual(timeline_transaction(self.path, req)["code"], "reconcile.scope")
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_edit_summary_is_typed_recovery_required(self):
        doc = self.managed()
        req = request(doc, sha(self.path.read_bytes()), "edit", "typed-summary", operations=[
            dict(op="parameters.update", id=doc["subtitles"][0]["id"], path=["text"], value="v2")])
        self.assertTrue(timeline_transaction(self.path, req)["ok"])
        opdir = store._location(self.path) / ("op-" + store.hashlib.sha256(b"typed-summary").hexdigest())
        metadata = json.loads((opdir / "intent.json").read_bytes())
        metadata["result"]["edit"] = []
        (opdir / "intent.json").write_text(json.dumps(metadata))
        before = self.path.read_bytes()
        self.assertEqual(timeline_store_status(self.path, recover=True)["code"], "recovery_required")
        self.assertEqual(timeline_write_plan(self.path, req)["code"], "recovery_required")
        self.assertEqual(self.path.read_bytes(), before)

    def test_exact_import_freezes_bytes_not_path_detached_history_and_collision(self):
        # Accepted noncanonical numeric spelling must survive just like escapes.
        raw = self.path.read_bytes().rstrip()[:-1] + b', "vendor_numeric":1.000e+0}\n'
        target = self.root / "frozen.json"
        req = self.exact(raw=raw)
        result = timeline_transaction(target, req)
        self.assertTrue(result["ok"], result)
        self.assertEqual(target.read_bytes(), raw)
        self.assertEqual(result["revision"], sha(raw))
        req2 = dict(req, op_id="collision", base_revision=sha(raw))
        self.assertEqual(timeline_transaction(target, req2)["code"], "create.exists")
        live = dict(req, operation="replace", base_revision=sha(raw), purpose="legacy-write")
        self.assertEqual(timeline_transaction(target, live)["code"], "input.policy")
        self.managed()
        managed_raw = self.path.read_bytes()
        detached = self.root / "detached.json"
        req = self.exact(raw=managed_raw, mode="managed", purpose="detached-copy")
        self.assertTrue(timeline_transaction(detached, req)["ok"])
        self.assertEqual(detached.read_bytes(), managed_raw)
        self.assertEqual(self.path.read_bytes(), managed_raw)
        self.assertNotEqual(store._location(detached), store._location(self.path))
        self.assertEqual(len(timeline_store_status(detached)["operations"]), 1)
        self.assertFalse(timeline_transaction(self.root / "fake.json", dict(req, purpose="frozen-copy"))["ok"])
        undo = request(json.loads(managed_raw), sha(managed_raw), "undo", "undo-copy",
                       head=json.loads(managed_raw)["_vtx"]["head"])
        self.assertFalse(timeline_transaction(detached, undo)["ok"])
        # Exact text (whitespace/escape/order) is part of digest, not just doc value.
        conflict = dict(self.exact(raw=raw.strip()), base_revision=None)
        self.assertEqual(timeline_transaction(target, conflict)["code"], "idempotency.conflict")

    def test_exact_invalid_inputs_do_not_round_or_create(self):
        raw = self.path.read_bytes()
        for payload in (raw[:-2] + b',"x":0.12345678901234567890123456789}',
                        b'{"x":1,"x":2}', b'{"x":NaN}', b"null", b"\xff"):
            target = self.root / "invalid.json"
            req = self.exact(raw=payload)
            before = tree(self.root)
            self.assertFalse(timeline_write_plan(target, req)["ok"])
            self.assertEqual(before, tree(self.root))
            self.assertFalse(timeline_transaction(target, req)["ok"])
            self.assertFalse(target.exists())

    def test_unmanaged_undo_requires_explicit_exact_committed_after(self):
        before = self.path.read_bytes()
        result = timeline_transaction(self.path, self.replace())
        after = self.path.read_bytes()
        undo = request(json.loads(after), sha(after), "undo", "undo", undo_of="write")
        # Semantic equality is not enough for selective byte undo.
        self.path.write_bytes(after + b"\n")
        wrong = dict(undo, base_revision=sha(self.path.read_bytes()))
        self.assertEqual(timeline_transaction(self.path, wrong)["code"], "undo.conflict")
        self.path.write_bytes(after)
        self.assertTrue(timeline_write_plan(self.path, undo)["ok"])
        self.assertTrue(timeline_transaction(self.path, undo)["ok"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(timeline_transaction(self.path, undo)["replayed"])
        target = self.root / "created.json"
        created = timeline_transaction(target, request(document(), None, "create", "create"))
        no_delete = request(document(), created["revision"], "undo", "undo", undo_of="create")
        self.assertEqual(timeline_transaction(target, no_delete)["code"], "undo.unsupported")
        self.assertTrue(target.exists())

    def test_public_cli_plan_commit_payload_file_freeze_and_exit_codes(self):
        req = self.exact()
        req.pop("raw_bytes")
        req["payload_file"] = str(self.path)
        reqfile = self.root / "request.json"
        reqfile.write_text(json.dumps(req))
        target = self.root / "cli.json"
        def cli(*extra):
            return subprocess.run([sys.executable, "-B", "-m", "vtx", "-t", str(target),
                                  "store", *extra, "--request", str(reqfile)],
                                  env=ENV, cwd=self.root, capture_output=True, text=True, timeout=30)
        before = tree(self.root)
        plan = cli("plan")
        self.assertEqual(plan.returncode, 0, plan.stderr + plan.stdout)
        self.assertEqual(before, tree(self.root))
        result = cli()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(plan.stdout)["revision"], json.loads(result.stdout)["revision"])
        self.assertEqual(target.read_bytes(), self.path.read_bytes())
        self.assertEqual(cli("--dry-run").returncode, 2)
        self.path.write_bytes(self.path.read_bytes() + b"\n")
        changed = cli()
        self.assertEqual(changed.returncode, 1)
        self.assertEqual(json.loads(changed.stdout)["code"], "idempotency.conflict")
        # Source leaf aliases/dirs are not accepted as payload files.
        alias = self.root / "alias.json"
        alias.symlink_to(self.path)
        req["payload_file"] = str(alias)
        reqfile.write_text(json.dumps(req))
        self.assertEqual(cli().returncode, 1)
        req["payload_file"] = str(self.root)
        reqfile.write_text(json.dumps(req))
        self.assertEqual(cli().returncode, 1)
        for invalid in (None, [], {}, 42, ""):
            with self.subTest(payload_file=invalid):
                req["payload_file"] = invalid
                reqfile.write_text(json.dumps(req))
                rejected = cli()
                self.assertEqual(rejected.returncode, 1, rejected.stderr)
                self.assertFalse(json.loads(rejected.stdout)["ok"])

    def test_two_process_cas_each_identity_and_exact_collision(self):
        for lane in ("legacy", "managed", "exact"):
            with self.subTest(lane=lane):
                self.path = self.root / (lane + ".json")
                self.save(document())
                if lane == "managed":
                    self.managed()
                req = self.replace()
                target = self.path
                if lane == "exact":
                    target = self.root / "new.json"
                    req = self.exact()
                    req["raw_utf8"] = req.pop("raw_bytes").decode()
                code = """import json,sys
from vtx.api import timeline_transaction
req=json.loads(sys.argv[2]);req['op_id']=sys.argv[3]
if 'raw_utf8' in req: req['raw_bytes']=req.pop('raw_utf8').encode()
sys.stdin.readline()
print(json.dumps(timeline_transaction(sys.argv[1],req)))
"""
                procs = [subprocess.Popen([sys.executable, "-B", "-c", code, str(target), json.dumps(req), "p" + str(i)],
                         env=ENV, cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True) for i in range(2)]
                results = []
                try:
                    for proc in procs:
                        proc.stdin.write("go\n"); proc.stdin.flush()
                    for proc in procs:
                        out, err = proc.communicate(timeout=30)
                        self.assertEqual(proc.returncode, 0, err)
                        results.append(json.loads(out))
                finally:
                    for proc in procs:
                        if proc.poll() is None:
                            proc.kill(); proc.communicate()
                self.assertEqual(sum(r["ok"] for r in results), 1, results)
                self.assertEqual([r["code"] for r in results if not r["ok"]], ["conflict"])


class WriteV2Faults(unittest.TestCase):
    lanes = ("legacy", "managed", "exact", "detached")
    points = ("init_mkdir", "publish_created", "publish_written", "publish_synced",
              "publish_replaced", "publish_parent_synced", "init_manifest_synced",
              "init_published", "init_parent_synced", "locked", "recovered", "read",
              "validated", "intent_published", "staged", "final_cas", "replaced",
              "parent_synced", "committed")

    def fixture(self, root, lane):
        target = Path(root) / "timeline.json"
        managed = lane in ("managed", "detached")
        doc = document(ids=managed)
        if managed:
            doc["_vtx"] = dict(protocol=1, project_id="project", head="imported")
        before = json.dumps(doc, ensure_ascii=True, indent=2).encode() + b"\n"
        if lane in ("exact", "detached"):
            req = dict(protocol=2, operation="create", op_id="fault", base_revision=None,
                       identity_mode="managed" if managed else "preserve-unmanaged", payload_mode="exact-import",
                       purpose="detached-copy" if managed else "frozen-copy", raw_bytes=before)
            return target, None, req, before
        target.write_bytes(before)
        doc["subtitles"][0]["text"] = "after"
        req = request(doc, sha(before), op_id="fault")
        plan = timeline_write_plan(target, req)
        self.assertTrue(plan["ok"], plan)
        expected = store._encode(plan["document"])
        return target, before, req, expected

    def assert_atomic_recover(self, target, before, expected):
        current = target.read_bytes() if target.exists() else None
        self.assertIn(current, (before, expected))
        result = timeline_store_status(target, recover=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(target.read_bytes() if target.exists() else None, current)
        self.assertFalse(result["pending"])
        return current

    def test_exception_every_checkpoint_each_lane_and_pending_plan_zero_write(self):
        for lane in self.lanes:
            for point in self.points:
                with self.subTest(lane=lane, point=point), tempfile.TemporaryDirectory() as root:
                    target, before, req, expected = self.fixture(root, lane)
                    def stop(name):
                        if name == point:
                            raise OSError(errno.EIO, "checkpoint EIO " + point)
                    with patch.object(store, "_checkpoint", stop):
                        result = timeline_transaction(target, req)
                    self.assertFalse(result["ok"], result)
                    state = tree(Path(root))
                    plan = timeline_write_plan(target, req)
                    self.assertEqual(state, tree(Path(root)))
                    if point in ("intent_published", "staged", "final_cas", "replaced", "parent_synced"):
                        self.assertEqual(plan["code"], "recovery_required", plan)
                    current = self.assert_atomic_recover(target, before, expected)
                    retry = timeline_transaction(target, req)
                    if point in ("intent_published", "staged", "final_cas"):
                        self.assertEqual(current, before)
                        self.assertEqual(retry["code"], "aborted", retry)
                    elif point in ("replaced", "parent_synced", "committed"):
                        self.assertTrue(retry["replayed"], retry)
                        self.assertEqual(current, expected)
                    else:
                        self.assertTrue(retry["ok"], retry)
                        self.assertEqual(target.read_bytes(), expected)

    @unittest.skipUnless(os.name == "posix", "POSIX SIGKILL recovery")
    def test_sigkill_every_checkpoint_each_lane(self):
        code = """import json,os,signal,sys
from vtx import timeline_store as s
req=json.loads(sys.argv[3])
if 'raw_utf8' in req: req['raw_bytes']=req.pop('raw_utf8').encode()
def stop(name):
 if name==sys.argv[2]: os.kill(os.getpid(),signal.SIGKILL)
s._checkpoint=stop
s.timeline_transaction(sys.argv[1],req)
"""
        for lane in self.lanes:
            for point in self.points:
                with self.subTest(lane=lane, point=point), tempfile.TemporaryDirectory() as root:
                    target, before, req, expected = self.fixture(root, lane)
                    wire = dict(req)
                    if "raw_bytes" in wire:
                        wire["raw_utf8"] = wire.pop("raw_bytes").decode()
                    child = subprocess.run([sys.executable, "-B", "-c", code, str(target), point, json.dumps(wire)],
                            cwd=root, env=ENV, capture_output=True, timeout=30)
                    self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr)
                    current = self.assert_atomic_recover(target, before, expected)
                    if point in ("replaced", "parent_synced", "committed"):
                        self.assertEqual(current, expected)
                    else:
                        self.assertEqual(current, before)

    def test_eio_every_actual_write_sync_rename_call_each_lane(self):
        # Every actual invocation, not one probe per helper. Shares real publisher
        # with v1; assertion never substitutes renderability for byte atomicity.
        for lane in self.lanes:
            for helper in ("_write_new", "_sync_dir", "os.replace", "os.rename", "os.fsync"):
                obj, attr = (store.os, helper[3:]) if helper.startswith("os.") else (store, helper)
                original = getattr(obj, attr)
                calls = []
                def count(*args, **kwargs):
                    calls.append(args)
                    return original(*args, **kwargs)
                with tempfile.TemporaryDirectory() as root:
                    target, _, req, _ = self.fixture(root, lane)
                    with patch.object(obj, attr, count):
                        result = timeline_transaction(target, req)
                    self.assertTrue(result["ok"], result)
                self.assertGreater(len(calls), 0)
                for fail_at in range(len(calls)):
                    with self.subTest(lane=lane, helper=helper, fail_at=fail_at), tempfile.TemporaryDirectory() as root:
                        target, before, req, expected = self.fixture(root, lane)
                        counter = [0]
                        def fail(*args, **kwargs):
                            index = counter[0]
                            counter[0] += 1
                            if index == fail_at:
                                raise OSError(errno.EIO, "injected EIO " + helper)
                            return original(*args, **kwargs)
                        with patch.object(obj, attr, fail):
                            result = timeline_transaction(target, req)
                        self.assertFalse(result["ok"], result)
                        current = self.assert_atomic_recover(target, before, expected)
                        if current != before:
                            self.assertEqual(result["code"], "outcome_unknown", result)

    def test_pending_neither_before_nor_after_and_corrupt_evidence_fail_closed(self):
        for lane in self.lanes:
            for missing in (False, True):
                with self.subTest(lane=lane, missing=missing), tempfile.TemporaryDirectory() as root:
                    target, _, req, _ = self.fixture(root, lane)
                    def stop(name):
                        if name == "intent_published":
                            raise OSError("durable pending")
                    with patch.object(store, "_checkpoint", stop):
                        timeline_transaction(target, req)
                    external = b'{"external":"never overwrite"}'
                    target.write_bytes(external)
                    if missing:
                        target.unlink()
                    # Missing is the valid before for create-only artifacts.
                    result = timeline_store_status(target, recover=True)
                    if lane in ("exact", "detached") and missing:
                        self.assertTrue(result["ok"], result)
                    else:
                        self.assertEqual(result["code"], "recovery_required", result)
                    self.assertEqual(target.read_bytes() if target.exists() else None,
                                     None if missing else external)
            for field, value in (("identity_mode", "managed"), ("payload_mode", "raw"),
                                 ("before_revision", "wrong"), ("result", [])):
                with self.subTest(lane=lane, field=field), tempfile.TemporaryDirectory() as root:
                    target, _, req, _ = self.fixture(root, lane)
                    self.assertTrue(timeline_transaction(target, req)["ok"])
                    home = store._location(target)
                    intent = next(home.glob("op-*/intent.json"))
                    data = json.loads(intent.read_bytes())
                    data[field] = ("preserve-unmanaged" if req["identity_mode"] == "managed" else "managed") if field == "identity_mode" else value
                    intent.write_text(json.dumps(data))
                    raw = target.read_bytes()
                    self.assertEqual(timeline_store_status(target, recover=True)["code"], "recovery_required")
                    self.assertEqual(target.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
