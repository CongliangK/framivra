"""Real file/process tests in external temporary projects, no render or media writes."""
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from vtx.api import (inspect_timeline, timeline_transaction, timeline_store_status,
                     timeline_store_capabilities)
from vtx import timeline_store as store
from vtx.timeline_structure import identity_errors, structure_errors

ROOT = Path(__file__).resolve().parents[2]


def document(ids=False):
    doc = {"version": 2, "project": {"fps": 30, "width": 1920, "height": 1080,
                                    "duration_frames": 90, "unknown": {"x": [False, None]}},
           "sources": [{"id": "s", "path": "missing.wav", "extension": {"retain": "你好"}}],
           "video_tracks": [{"name": "main", "kind": "aroll", "clips": [
               {"src": "s", "dst_in": 0, "dur": 30, "src_in": 0, "src_out": 30,
                "extension": {"custom": [1, 2]}}]}],
           "audio_tracks": [{"name": "voice", "events": [
               {"src": "s", "in": 0, "out": 60, "volume": 1}]}],
           "subtitles": [{"in": 0, "out": 30, "text": "你好", "color": "red"}],
           "unknown_root": {"id": "not-a-managed-object", "anything": [3, 2, 1]}}
    if ids:
        for i, (_, obj, kind) in enumerate(store.managed_objects(doc)):
            if kind != "source":
                obj["id"] = "object-" + str(i)
    return doc


def sha(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class TimelineStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="framivra-store-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "剪辑.json"
        self.path.write_text(json.dumps(document(), ensure_ascii=False, indent=2), encoding="utf-8")

    def revision(self):
        return sha(self.path.read_bytes())

    def doc(self):
        return json.loads(self.path.read_bytes())

    def request(self, op, op_id, **kw):
        return dict(operation=op, op_id=op_id, base_revision=self.revision(), **kw)

    def adopt(self):
        request = self.request("adopt", "adopt-1")
        result = timeline_transaction(self.path, request)
        self.assertTrue(result["ok"], result)
        return result

    def apply_request(self, op_id="edit-1", **values):
        return self.request("apply", op_id, changes=[{"id": self.doc()["subtitles"][0]["id"], "set": values or {"text": "新"}}])

    def test_adopt_extensions_strict_bytes_and_idempotency(self):
        before = self.path.read_bytes()
        doc = self.doc()
        inspected = inspect_timeline(self.path)
        self.assertEqual(list(self.root.iterdir()), [self.path])
        req = self.request("adopt", "adopt-1")
        self.assertEqual(req["base_revision"], inspected["content_version"])
        with patch("vtx.pipeline.infra.Ctx", side_effect=AssertionError("forbidden")):
            result = timeline_transaction(self.path, req)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["base_revision"], sha(before))
        self.assertEqual(result["revision"], self.revision())
        after = self.doc()
        self.assertFalse(identity_errors(after))
        for address, obj, kind in store.managed_objects(after):
            if kind != "source":
                del obj["id"]
        del after["_vtx"]
        self.assertEqual(after, doc)
        retry = timeline_transaction(self.path, req)
        self.assertTrue(retry["replayed"])
        self.assertEqual(retry["id_mapping"], result["id_mapping"])
        conflict = timeline_transaction(self.path, dict(req, base_revision="changed"))
        self.assertEqual(conflict["code"], "idempotency.conflict")
        self.assertEqual(self.doc()["sources"][0]["id"], "s")

    def test_retry_original_result_after_later_edit_never_rolls_back(self):
        self.adopt()
        first = self.apply_request(text="one")
        one = timeline_transaction(self.path, first)
        self.assertTrue(one["ok"], one)
        two = timeline_transaction(self.path, self.apply_request("edit-2", text="two"))
        self.assertTrue(two["ok"], two)
        retry = timeline_transaction(self.path, first)
        self.assertEqual(retry["revision"], one["revision"])
        self.assertEqual(retry["current_revision"], two["revision"])
        self.assertEqual(self.doc()["subtitles"][0]["text"], "two")

    def test_create_only_requires_ids_refuses_existing(self):
        req = dict(operation="create", op_id="create", base_revision=None, document=document(ids=True))
        other = self.root / "other.json"
        result = timeline_transaction(other, req)
        self.assertTrue(result["ok"], result)
        req.update(op_id="create2", base_revision=result["revision"])
        self.assertEqual(timeline_transaction(other, req)["code"], "create.exists")
        bad = self.root / "no-ids.json"
        req.update(op_id="create3", base_revision=None, document=document())
        self.assertFalse(timeline_transaction(bad, req)["ok"])
        self.assertFalse(bad.exists())

    def test_two_targets_and_alias_parent_share_correct_lock_identity(self):
        self.adopt()
        second = self.root / "second.json"
        second.write_bytes(json.dumps(document()).encode())
        result = timeline_transaction(second, dict(operation="adopt", op_id="adopt-1", base_revision=sha(second.read_bytes())))
        self.assertTrue(result["ok"], result)
        self.assertNotEqual(store._location(self.path), store._location(second))
        alias = self.root / "parent-link"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(timeline_store_status(alias / self.path.name)["evidence_path"],
                         timeline_store_status(self.path)["evidence_path"])

    def test_two_processes_same_base_exactly_one_commit(self):
        self.adopt()
        request = self.apply_request()
        code = """import json,sys
from vtx.api import timeline_transaction
req=json.loads(sys.argv[2]);req['op_id']=sys.argv[3]
sys.stdin.readline()
print(json.dumps(timeline_transaction(sys.argv[1],req)))
"""
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        procs = [subprocess.Popen([sys.executable, "-c", code, str(self.path), json.dumps(request), "parallel-" + str(i)],
                                 cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for i in range(2)]
        for proc in procs:
            proc.stdin.write("go\n"); proc.stdin.flush()
        results = []
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 0, stderr)
            results.append(json.loads(stdout))
        self.assertEqual(sum(r["ok"] for r in results), 1, results)
        self.assertEqual([r["code"] for r in results if not r["ok"]], ["conflict"])

    def test_two_threads_one_commit_and_cold_home_creation(self):
        req = self.request("adopt", "thread1")
        barrier = threading.Barrier(2)
        results = []
        def work(i):
            barrier.wait()
            results.append(timeline_transaction(self.path, dict(req, op_id="thread" + str(i))))
        threads = [threading.Thread(target=work, args=(i,)) for i in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=20); self.assertFalse(thread.is_alive())
        self.assertEqual(sum(r["ok"] for r in results), 1, results)

    def test_apply_whitelist_and_replace_preserves_extensions(self):
        self.adopt()
        before = self.path.read_bytes()
        for field in ("id", "_vtx", "../text", "/text", "src", "extension"):
            result = timeline_transaction(self.path, self.apply_request("bad-" + str(len(field)), **{field: "x"}))
            self.assertFalse(result["ok"], result)
            self.assertEqual(self.path.read_bytes(), before)
        doc = self.doc()
        doc["subtitles"][0]["text"] = "replacement"
        result = timeline_transaction(self.path, self.request("replace", "replace", document=doc))
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.doc()["unknown_root"], doc["unknown_root"])
        self.assertEqual(self.doc()["subtitles"][0]["color"], "red")
        committed = self.path.read_bytes()
        for change in (lambda d: d["subtitles"][0].update(id="new-id"),
                       lambda d: d["subtitles"].clear(),
                       lambda d: d["subtitles"].append({"id": "new-id", "in": 30, "out": 60, "text": "copy"})):
            replacement = self.doc(); change(replacement)
            result = timeline_transaction(self.path, self.request("replace", "invalid-inventory", document=replacement))
            self.assertEqual(result["code"], "input.identity", result)
            self.assertEqual(self.path.read_bytes(), committed)
        self.assertIn("no add/remove/copy/split", timeline_store_capabilities()["replace"])

    def test_undo_current_head_new_cas_does_not_overwrite_later_edit(self):
        self.adopt()
        first = timeline_transaction(self.path, self.apply_request(text="one"))
        req = self.request("undo", "undo1", head="edit-1")
        undone = timeline_transaction(self.path, req)
        self.assertTrue(undone["ok"], undone)
        self.assertEqual(self.doc()["subtitles"][0]["text"], "你好")
        self.assertEqual(self.doc()["_vtx"]["head"], "undo1")
        self.assertNotEqual(undone["revision"], first["base_revision"])
        stale = timeline_transaction(self.path, dict(req, op_id="undo2"))
        self.assertEqual(stale["code"], "conflict")
        current_wrong_head = timeline_transaction(self.path, self.request("undo", "undo3", head="edit-1"))
        self.assertEqual(current_wrong_head["code"], "undo.conflict")

    def test_subtitles_src_is_never_expanded_or_double_written(self):
        doc = document()
        doc["subtitles"] = []
        doc["subtitles_src"] = "missing-external.srt"
        self.path.write_bytes(json.dumps(doc).encode())
        self.adopt()
        result = timeline_transaction(self.path, self.request("apply", "source-path", changes=[{"id": "s", "set": {"path": "elsewhere.wav"}}]))
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.doc()["subtitles"], [])
        self.assertEqual(self.doc()["subtitles_src"], "missing-external.srt")
        self.assertFalse((self.root / "missing-external.srt").exists())

    def test_invalid_legacy_ids_types_refs_and_future_protocol_refused(self):
        mutations = [lambda d: d["sources"].append(copy.deepcopy(d["sources"][0])),
                     lambda d: d["subtitles"][0].update(id=False),
                     lambda d: d["subtitles"][0].update(id=""),
                     lambda d: d["subtitles"][0].update(id="foreign-existing-id"),
                     lambda d: d["subtitles"][0].update(**{"in": True}),
                     lambda d: d["video_tracks"][0]["clips"][0].update(src_out=5),
                     lambda d: d["audio_tracks"][0]["events"][0].update(src="unknown"),
                     lambda d: d.update(_vtx={"protocol": 2, "project_id": "p", "head": "h"})]
        # Existing legacy IDs are not owned by this protocol; adopt must not bless them.
        for i, mutate in enumerate(mutations):
            with self.subTest(i=i):
                doc = document(); mutate(doc)
                self.path.write_bytes(json.dumps(doc).encode())
                before = self.path.read_bytes()
                result = timeline_transaction(self.path, self.request("adopt", "invalid-" + str(i)))
                self.assertFalse(result["ok"], result)
                self.assertEqual(self.path.read_bytes(), before)
        for raw in (b'{"version":2,"version":2}', b'{"x":NaN}', b'{"x":1e999}', b'not JSON'):
            self.path.write_bytes(raw)
            result = timeline_transaction(self.path, self.request("adopt", "badjson"))
            self.assertEqual(result["code"], "input.json")
            self.assertEqual(self.path.read_bytes(), raw)

    def test_leaf_symlink_hardlink_and_sidecar_symlink_refused(self):
        alias = self.root / "alias.json"; alias.symlink_to(self.path)
        self.assertEqual(timeline_store_status(alias)["code"], "path.alias")
        hard = self.root / "hard.json"; os.link(self.path, hard)
        self.assertEqual(timeline_store_status(self.path)["code"], "path.alias")
        hard.unlink()
        home = store._location(self.path); home.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(timeline_store_status(self.path)["code"], "path.alias")

    def test_fault_every_checkpoint_current_is_before_or_after_recovery_never_reapply(self):
        points = ("locked", "recovered", "read", "validated", "intent_published", "staged", "final_cas", "replaced", "parent_synced", "committed")
        for point in points:
            with self.subTest(point=point), tempfile.TemporaryDirectory(prefix="framivra-fault-") as tmp:
                target = Path(tmp) / "timeline.json"
                target.write_bytes(json.dumps(document()).encode())
                before = target.read_bytes()
                req = dict(operation="adopt", op_id="failure", base_revision=sha(before))
                def fault(name):
                    if name == point: raise OSError("injected disk failure at " + name)
                with patch.object(store, "_checkpoint", fault):
                    result = timeline_transaction(target, req)
                self.assertFalse(result["ok"], result)
                if point in ("replaced", "parent_synced", "committed"):
                    self.assertEqual(result["code"], "outcome_unknown")
                current = target.read_bytes()
                recovered = timeline_store_status(target, recover=True)
                self.assertTrue(recovered["ok"], recovered)
                self.assertEqual(target.read_bytes(), current)
                retry = timeline_transaction(target, req)
                if point in ("intent_published", "staged", "final_cas"):
                    self.assertEqual(current, before)
                    self.assertEqual(retry["code"], "aborted")
                elif point in ("replaced", "parent_synced", "committed"):
                    self.assertTrue(retry["ok"], retry)
                    self.assertTrue(retry["replayed"])
                    self.assertEqual(sha(current), retry["revision"])

    @unittest.skipUnless(os.name == "posix", "POSIX kill recovery")
    def test_sigkill_each_phase(self):
        code = """import json,os,signal,sys
from vtx import timeline_store as s
point=sys.argv[2]
def checkpoint(name):
 if name==point: os.kill(os.getpid(),signal.SIGKILL)
s._checkpoint=checkpoint
p=sys.argv[1]
b=open(p,'rb').read()
s.timeline_transaction(p,dict(operation='adopt',op_id='killed',base_revision=s._sha(b)))
"""
        for point in ("locked", "recovered", "read", "validated", "intent_published", "staged", "final_cas", "replaced", "parent_synced", "committed"):
            with self.subTest(point=point), tempfile.TemporaryDirectory(prefix="framivra-kill-") as tmp:
                target = Path(tmp) / "timeline.json"; target.write_bytes(json.dumps(document()).encode())
                before = target.read_bytes()
                proc = subprocess.run([sys.executable, "-c", code, str(target), point], cwd=tmp,
                                      env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1"), capture_output=True, timeout=20)
                self.assertEqual(proc.returncode, -signal.SIGKILL, proc.stderr)
                current = target.read_bytes()
                result = timeline_store_status(target, recover=True)
                self.assertTrue(result["ok"], result)
                self.assertEqual(target.read_bytes(), current)
                if point not in ("replaced", "parent_synced", "committed"):
                    self.assertEqual(current, before)
                else:
                    self.assertFalse(identity_errors(json.loads(current)))

    def test_corrupt_history_pending_external_change_and_missing_file_never_overwritten(self):
        self.adopt()
        home = store._location(self.path)
        rec = next(home.glob("op-*"))
        commit = rec / "commit.json"
        commit.write_text("{bad", encoding="utf-8")
        before = self.path.read_bytes()
        self.assertEqual(timeline_store_status(self.path, recover=True)["code"], "recovery_required")
        self.assertEqual(self.path.read_bytes(), before)
        # A separate project with a durable pending intent and unrelated current bytes.
        other = self.root / "pending.json"; other.write_bytes(json.dumps(document()).encode())
        req = dict(operation="adopt", op_id="pending", base_revision=sha(other.read_bytes()))
        def fault(name):
            if name == "intent_published": raise OSError("stop")
        with patch.object(store, "_checkpoint", fault): timeline_transaction(other, req)
        external = b'{"external":"change"}'
        other.write_bytes(external)
        self.assertEqual(timeline_store_status(other, recover=True)["code"], "recovery_required")
        self.assertEqual(other.read_bytes(), external)
        other.unlink()
        self.assertEqual(timeline_store_status(other, recover=True)["code"], "recovery_required")
        self.assertFalse(other.exists())

    def test_real_syscall_disk_failures_leave_only_before_or_after(self):
        # Inject EIO at every invocation of the real fsync/rename/replace/write paths.
        # No claim of testing a physical power loss: this checks process-visible failures.
        for helper in ("_write_new", "_sync_dir", "os.replace", "os.rename", "os.fsync"):
            obj, attr = (store.os, helper[3:]) if helper.startswith("os.") else (store, helper)
            original = getattr(obj, attr)
            calls = []
            with tempfile.TemporaryDirectory(prefix="framivra-count-") as tmp:
                target = Path(tmp) / "timeline.json"; target.write_bytes(json.dumps(document()).encode())
                def count(*args, **kw):
                    calls.append(args)
                    return original(*args, **kw)
                with patch.object(obj, attr, count):
                    result = timeline_transaction(target, dict(operation="adopt", op_id="count", base_revision=sha(target.read_bytes())))
                self.assertTrue(result["ok"], result)
            for fail_at in range(len(calls)):
                with self.subTest(helper=helper, index=fail_at), tempfile.TemporaryDirectory(prefix="framivra-eio-") as tmp:
                    target = Path(tmp) / "timeline.json"; target.write_bytes(json.dumps(document()).encode())
                    before = target.read_bytes()
                    counter = [0]
                    def fail(*args, **kw):
                        index = counter[0]; counter[0] += 1
                        if index == fail_at: raise OSError(5, "injected EIO " + helper)
                        return original(*args, **kw)
                    with patch.object(obj, attr, fail):
                        result = timeline_transaction(target, dict(operation="adopt", op_id="disk-failure", base_revision=sha(before)))
                    self.assertFalse(result["ok"], result)
                    current = target.read_bytes()
                    self.assertIsInstance(json.loads(current), dict)
                    self.assertTrue(current == before or self.doc_is_managed(current))
                    recovered = timeline_store_status(target, recover=True)
                    self.assertEqual(target.read_bytes(), current, recovered)
                    if current != before:
                        self.assertEqual(result["code"], "outcome_unknown", result)

    @staticmethod
    def doc_is_managed(payload):
        doc = json.loads(payload)
        return not structure_errors(doc) and not identity_errors(doc) and "_vtx" in doc

    def test_initialization_sigkill_boundaries_never_publish_partial_home(self):
        code = """import os,signal,sys
from vtx import timeline_store as s
def stop(name):
 if name==sys.argv[2]: os.kill(os.getpid(),signal.SIGKILL)
s._checkpoint=stop
s.timeline_store_status(sys.argv[1])
"""
        points = ("init_mkdir", "publish_created", "publish_written", "publish_synced",
                  "publish_replaced", "publish_parent_synced", "init_manifest_synced",
                  "init_published", "init_parent_synced")
        for point in points:
            with self.subTest(point=point), tempfile.TemporaryDirectory(prefix="framivra-init-") as tmp:
                target = Path(tmp) / "timeline.json"
                raw = json.dumps(document()).encode(); target.write_bytes(raw)
                child = subprocess.run([sys.executable, "-c", code, str(target), point], cwd=tmp,
                                       env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1"),
                                       capture_output=True, timeout=20)
                self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr)
                home = store._location(target)
                self.assertEqual(home.exists(), point in ("init_published", "init_parent_synced"))
                # Unpublished artifacts are not adopted, modified or removed.
                leftovers = {p: {f.name: f.read_bytes() for f in p.iterdir()}
                             for p in Path(tmp).glob(home.name + ".initializing-*")}
                for recover in (False, True):
                    self.assertTrue(timeline_store_status(target, recover=recover)["ok"])
                    self.assertEqual(target.read_bytes(), raw)
                for p, entries in leftovers.items():
                    self.assertEqual({f.name: f.read_bytes() for f in p.iterdir()}, entries)
                result = timeline_transaction(target, dict(operation="adopt", op_id="retry", base_revision=sha(raw)))
                self.assertTrue(result["ok"], result)

    def test_unknown_missing_manifest_homes_never_initialized_or_cleaned(self):
        for kind in ("empty", "lock", "valid-temp", "bad-temp", "multiple", "unknown", "alias"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix="framivra-orphan-") as tmp:
                target = Path(tmp) / "timeline.json"
                raw = json.dumps(document()).encode(); target.write_bytes(raw)
                home = store._location(target); home.mkdir()
                if kind != "empty": (home / "store.lock").touch()
                if kind in ("valid-temp", "multiple"):
                    (home / ".publish-one").write_bytes(store._encode({"protocol": 1, "target": target.name}))
                if kind in ("bad-temp", "multiple"): (home / ".publish-two").write_bytes(b'{bad')
                if kind == "unknown": (home / "foreign").write_bytes(b'important')
                if kind == "alias": (home / "manifest.json").symlink_to(target)
                entries = {p.name: p.read_bytes() for p in home.iterdir()}
                for result in (timeline_store_status(target), timeline_store_status(target, recover=True),
                               timeline_transaction(target, dict(operation="adopt", op_id="orphan", base_revision=sha(raw)))):
                    self.assertEqual(result["code"], "recovery_required", result)
                self.assertEqual({p.name: p.read_bytes() for p in home.iterdir()},
                                 dict(entries, **{"store.lock": b""}))
                self.assertEqual(target.read_bytes(), raw)

    def test_manifest_location_refuses_same_sha_decoy_and_accepts_legacy_manifest(self):
        self.assertTrue(timeline_store_status(self.path)["ok"])
        manifest = store._location(self.path) / "manifest.json"
        other = self.root / "decoy"; other.mkdir()
        target = other / self.path.name; target.write_bytes(self.path.read_bytes())
        home = store._location(target); home.mkdir()
        (home / "manifest.json").write_bytes(manifest.read_bytes())
        before = target.read_bytes()
        self.assertEqual(timeline_store_status(target, recover=True)["code"], "recovery_required")
        self.assertEqual(target.read_bytes(), before)
        # Published v1 homes remain readable without a migration/rewrite.
        legacy = store._encode({"protocol": 1, "target": self.path.name})
        manifest.write_bytes(legacy)
        self.assertTrue(timeline_store_status(self.path)["ok"])
        self.assertEqual(manifest.read_bytes(), legacy)

    def test_initial_directory_publish_parent_sync_error_is_retryable_not_success(self):
        original = store._sync_dir
        def fail(path):
            if path == self.root: raise OSError(5, "init parent fsync failure")
            return original(path)
        raw = self.path.read_bytes()
        req = self.request("adopt", "after-init-failure")
        with patch.object(store, "_sync_dir", fail):
            result = timeline_transaction(self.path, req)
        self.assertFalse(result["ok"], result)
        self.assertTrue((store._location(self.path) / "manifest.json").is_file())
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertTrue(timeline_store_status(self.path, recover=True)["ok"])
        self.assertTrue(timeline_transaction(self.path, req)["ok"])

    def test_bootstrap_lock_aliases_are_refused_without_blessing_home(self):
        for hardlink in (False, True):
            with self.subTest(hardlink=hardlink), tempfile.TemporaryDirectory(prefix="framivra-bootstrap-") as tmp:
                target = Path(tmp) / "timeline.json"
                target.write_text(json.dumps(document()))
                home = store._location(target)
                lock = home.with_name(home.name + ".bootstrap.lock")
                sentinel = Path(tmp) / "sentinel"; sentinel.write_bytes(b"evidence")
                if hardlink: os.link(sentinel, lock)
                else: lock.symlink_to(sentinel)
                raw = target.read_bytes()
                for result in (timeline_store_status(target), timeline_store_status(target, recover=True),
                               timeline_transaction(target, dict(operation="adopt", op_id="locked", base_revision=sha(raw)))):
                    self.assertEqual(result["code"], "path.alias", result)
                self.assertFalse(home.exists())
                self.assertEqual(target.read_bytes(), raw)
                self.assertEqual(sentinel.read_bytes(), b"evidence")
                self.assertTrue(lock.is_symlink() if not hardlink else lock.stat().st_nlink == 2)

    def test_cold_adopt_create_compete_on_single_fact(self):
        adopt = self.request("adopt", "adopt")
        create = dict(operation="create", op_id="create", base_revision=None, document=document(ids=True))
        code = """import json,sys
from vtx.api import timeline_transaction
sys.stdin.readline()
print(json.dumps(timeline_transaction(sys.argv[1],json.loads(sys.argv[2]))))
"""
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        procs = [subprocess.Popen([sys.executable, "-c", code, str(self.path), json.dumps(req)],
                                  cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for req in (adopt, create)]
        try:
            for proc in procs: proc.stdin.write("go\n"); proc.stdin.flush()
            results = []
            for proc in procs:
                out, err = proc.communicate(timeout=20)
                self.assertEqual(proc.returncode, 0, err); results.append(json.loads(out))
        finally:
            for proc in procs:
                if proc.poll() is None: proc.kill(); proc.communicate()
        self.assertTrue(results[0]["ok"], results)
        self.assertEqual(results[1]["code"], "conflict", results)
        self.assertEqual(len(timeline_store_status(self.path)["operations"]), 1)

    def test_decimal_exponent_boundary_structured_without_hiding_internal_errors(self):
        raw = json.dumps(document())[:-1] + ',"x_exact":1e-9999999999999999999}'
        self.path.write_text(raw, encoding="utf-8"); before = self.path.read_bytes()
        self.assertTrue(inspect_timeline(self.path)["ok"])
        for result in (timeline_store_status(self.path), timeline_store_status(self.path, recover=True),
                       timeline_transaction(self.path, self.request("adopt", "precision"))):
            self.assertEqual(result["code"], "input.json", result)
            self.assertIn("numeric precision outside writable range", result["message"])
            self.assertEqual(self.path.read_bytes(), before)
        with patch.object(store, "strict_loads", side_effect=RuntimeError("internal bug")):
            with self.assertRaisesRegex(RuntimeError, "internal bug"):
                timeline_store_status(self.path)

    def test_tampered_history_full_payload_hash_and_bytes_are_checked(self):
        for entry in ("before.bin", "after.bin", "request", "result", "protocol", "mapping"):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory(prefix="framivra-corrupt-") as tmp:
                target = Path(tmp) / "timeline.json"; target.write_bytes(json.dumps(document()).encode())
                req = dict(operation="adopt", op_id="adopt", base_revision=sha(target.read_bytes()))
                self.assertTrue(timeline_transaction(target, req)["ok"])
                rec = next(store._location(target).glob("op-*"))
                if entry.endswith(".bin"):
                    (rec / entry).write_bytes(b'{"broken":"evidence"}')
                else:
                    intent = json.loads((rec / "intent.json").read_bytes())
                    if entry == "request": intent[entry]["operation"] = "replace"
                    elif entry == "result": intent[entry] = []
                    elif entry == "protocol": intent[entry] = True
                    else: intent["result"]["id_mapping"] = {}
                    (rec / "intent.json").write_bytes(json.dumps(intent).encode())
                before = target.read_bytes()
                result = timeline_store_status(target, recover=True)
                self.assertEqual(result["code"], "recovery_required", result)
                self.assertEqual(target.read_bytes(), before)

    def test_cli_source_launchers_and_api_same(self):
        req = self.request("adopt", "cli-adopt")
        path = self.root / "request.json"; path.write_text(json.dumps(req), encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        for launcher in ([sys.executable, "-m", "vtx"], [sys.executable, str(ROOT / "vtx/vtx.py")]):
            proc = subprocess.run([*launcher, "-t", str(self.path), "store", "--request", str(path)],
                                  cwd=self.root, env=env, text=True, capture_output=True, timeout=20)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            result = json.loads(proc.stdout)
            retry = timeline_transaction(self.path, req)
            for key in ("revision", "base_revision", "id_mapping", "request_digest"):
                self.assertEqual(result[key], retry[key])
        for args in (["store", "--dry-run", "--request", str(path)], ["store", "status", "--draft"]):
            proc = subprocess.run([sys.executable, "-m", "vtx", *args], cwd=self.root, env=env, text=True, capture_output=True)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        capabilities = timeline_store_capabilities()
        self.assertIn("block.split", capabilities["edit"]["operations"])
        self.assertIn("gfx continuous trim/split/crossing ripple", capabilities["unsupported"])


if __name__ == "__main__":
    unittest.main()
