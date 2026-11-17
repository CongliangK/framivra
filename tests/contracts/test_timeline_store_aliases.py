"""Native case/Unicode lookup aliases must not fork timeline history or locks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from vtx import timeline_store as store
from tests.contracts.test_timeline_store import ROOT, document, sha


class _StoreFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="framivra-store-alias-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def create(self, target, op_id="create"):
        return store.timeline_transaction(target, dict(operation="create", op_id=op_id,
                                          base_revision=None, document=document(ids=True)))


class ExactSpelling(_StoreFixture):
    def test_exact_spelling_history_hash_manifest_and_replay_unchanged(self):
        target = self.root / "MiXeD-caf\u00e9.json"
        result = self.create(target)
        self.assertTrue(result["ok"], result)
        home = self.root / (".vtx-store-" + hashlib.sha256(target.name.encode()).hexdigest())
        self.assertEqual(store._location(target), home)
        manifest = home / "manifest.json"
        expected = store._encode(dict(protocol=1, target=target.name, location=str(target)))
        self.assertEqual(manifest.read_bytes(), expected)
        frozen = {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}
        self.assertTrue(self.create(target)["replayed"])
        self.assertEqual(store.timeline_store_status(target)["evidence_path"], str(home))
        self.assertEqual({p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}, frozen)

    def test_distinct_exact_names_remain_distinct_where_filesystem_supports_them(self):
        first = self.root / "Timeline.json"
        second = self.root / "timeline.json"
        first.write_text(json.dumps(document()))
        if second.exists():
            self.assertEqual(first.stat().st_ino, second.stat().st_ino)
            self.assertEqual(store.timeline_store_status(second)["code"], "path.alias")
            self.assertFalse(store._location(second).exists())
            return
        second.write_text(json.dumps(document()))
        results = [store.timeline_transaction(p, dict(operation="adopt", op_id="adopt",
                   base_revision=sha(p.read_bytes()))) for p in (first, second)]
        self.assertTrue(all(r["ok"] for r in results), results)
        self.assertNotEqual(store._location(first), store._location(second))
        self.assertNotEqual(results[0]["project_id"], results[1]["project_id"])


@unittest.skipUnless(sys.platform == "darwin", "native macOS alias semantics")
class MacOSAliases(_StoreFixture):
    def assert_pair_store_behavior(self, exact_name, alias_name):
        target = self.root / exact_name
        alias = self.root / alias_name
        result = self.create(target)
        self.assertTrue(result["ok"], result)
        if not alias.exists():
            # Case-sensitive APFS may treat this pair as distinct. Exercise both
            # independent stores rather than assuming every Darwin volume aliases.
            before = target.read_bytes()
            second = self.create(alias)
            self.assertTrue(second["ok"], second)
            self.assertNotEqual(target.stat().st_ino, alias.stat().st_ino)
            self.assertNotEqual(result["project_id"], second["project_id"])
            self.assertNotEqual(store._location(target), store._location(alias))
            self.assertEqual(target.read_bytes(), before)
            self.assertTrue(self.create(target)["replayed"])
            self.assertTrue(self.create(alias)["replayed"])
            return
        self.assertEqual(target.stat().st_ino, alias.stat().st_ino)
        home = store._location(target)
        before = target.read_bytes()
        evidence = {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}
        entries = set(self.root.iterdir())
        req = dict(operation="apply", op_id="alias-edit", base_revision=sha(before),
                   changes=[dict(id="object-5", set={"text": "alias"})])
        requests = (store.timeline_store_status(alias),
                    store.timeline_store_status(alias, recover=True),
                    store.timeline_transaction(alias, req),
                    store.timeline_edit_plan(alias, dict(operation="edit", op_id="plan",
                        base_revision=sha(before), operations=[dict(op="project.resize", duration_frames=100)])),
                    store.timeline_write_plan(alias, dict(protocol=2, operation="replace", op_id="plan2",
                        base_revision=sha(before), identity_mode="managed", payload_mode="document",
                        purpose="edit", document=json.loads(before))))
        for result in requests:
            self.assertEqual(result.get("code"), "path.alias", result)
        self.assertEqual(set(self.root.iterdir()), entries)
        self.assertFalse(store._location(alias).exists())
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual({p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}, evidence)
        self.assertTrue(self.create(target)["replayed"])

    def test_capability_requires_native_directory_locks_and_refuses_without_them(self):
        self.assertTrue(store.timeline_store_capabilities()["platform_support"]["darwin"])
        with patch.object(store, "directory_write_capability", return_value=False):
            self.assertFalse(store.timeline_store_capabilities()["platform_support"]["darwin"])
            self.assertEqual(self.create(self.root / "timeline.json")["code"], "platform.unsupported")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_exact_cas_stays_authoritative_after_alias_refusal(self):
        target = self.root / "Case.json"
        self.assertTrue(self.create(target)["ok"])
        before = target.read_bytes()
        doc = json.loads(before)
        request = dict(operation="apply", op_id="edit", base_revision=sha(before),
                       changes=[dict(id=doc["subtitles"][0]["id"], set={"text": "new"})])
        alias = self.root / "case.json"
        aliases = alias.exists()
        self.assertEqual(store.timeline_transaction(alias, request)["code"],
                         "path.alias" if aliases else "conflict")
        if not aliases:
            independent = self.create(alias)
            self.assertTrue(independent["ok"], independent)
            self.assertNotEqual(alias.stat().st_ino, target.stat().st_ino)
            self.assertEqual(target.read_bytes(), before)
        committed = store.timeline_transaction(target, request)
        self.assertTrue(committed["ok"], committed)
        stale = store.timeline_transaction(target, dict(request, op_id="stale"))
        self.assertEqual(stale["code"], "conflict", stale)
        self.assertEqual(sha(target.read_bytes()), committed["revision"])
        self.assertTrue(store.timeline_transaction(target, request)["replayed"])

    def test_existing_case_alias_zero_mutation(self):
        self.assert_pair_store_behavior("Timeline.json", "timeline.json")

    def test_existing_unicode_normalization_alias_zero_mutation(self):
        self.assert_pair_store_behavior("caf\u00e9.json", "cafe\u0301.json")

    def test_nfd_exact_spelling_is_supported_without_normalization(self):
        target = self.root / "cafe\u0301.json"
        self.assertTrue(self.create(target)["ok"])
        self.assertIn(target.name, os.listdir(self.root))
        self.assertTrue(self.create(target)["replayed"])
        alias = self.root / "caf\u00e9.json"
        if alias.exists():
            self.assertEqual(alias.stat().st_ino, target.stat().st_ino)
            self.assertEqual(store.timeline_store_status(alias)["code"], "path.alias")
        else:
            before = target.read_bytes()
            self.assertTrue(self.create(alias)["ok"])
            self.assertNotEqual(alias.stat().st_ino, target.stat().st_ino)
            self.assertEqual(target.read_bytes(), before)
            self.assertTrue(self.create(target)["replayed"])

    def test_parent_case_and_unicode_aliases_refused_before_side_effects(self):
        for exact, alias in (("ParentCase", "parentcase"), ("caf\u00e9", "cafe\u0301")):
            with self.subTest(exact=exact):
                parent = self.root / exact
                parent.mkdir()
                target = parent / "timeline.json"
                self.assertTrue(self.create(target)["ok"])
                before = set(parent.iterdir())
                via = self.root / alias / target.name
                if via.exists():
                    self.assertEqual(via.stat().st_ino, target.stat().st_ino)
                    self.assertEqual(store.timeline_store_status(via)["code"], "path.alias")
                else:
                    original = target.read_bytes()
                    via.parent.mkdir()
                    self.assertTrue(self.create(via)["ok"])
                    self.assertNotEqual(via.parent.stat().st_ino, parent.stat().st_ino)
                    self.assertNotEqual(via.stat().st_ino, target.stat().st_ino)
                    self.assertEqual(target.read_bytes(), original)
                    self.assertTrue(store.timeline_store_status(via)["ok"])
                self.assertEqual(set(parent.iterdir()), before)
                self.assertTrue(store.timeline_store_status(target)["ok"])

    def test_parent_coordination_lock_aliases_and_reserved_project_refused(self):
        lock = self.root / store._PARENT_LOCK
        self.assertEqual(self.create(lock)["code"], "path.alias")
        self.assertFalse(lock.exists())
        sentinel = self.root / "sentinel"
        sentinel.write_bytes(b"sentinel")
        for hardlink in (False, True):
            with self.subTest(hardlink=hardlink):
                if hardlink:
                    os.link(sentinel, lock)
                else:
                    lock.symlink_to(sentinel)
                self.assertEqual(self.create(self.root / "target.json")["code"], "path.alias")
                self.assertFalse((self.root / "target.json").exists())
                self.assertEqual(sentinel.read_bytes(), b"sentinel")
                lock.unlink()

    def test_missing_case_and_unicode_aliases_two_processes_only_one_history(self):
        # Both children complete _canonical while the leaf is still absent,
        # deterministically exercising the race that per-spelling locks miss.
        code = """import json,sys
from vtx import timeline_store as s
original=s._canonical
def canonical(path):
 target=original(path)
 print('ready',flush=True)
 sys.stdin.readline()
 return target
s._canonical=canonical
print(json.dumps(s.timeline_transaction(sys.argv[1],json.loads(sys.argv[2]))))
"""
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        for names in (("Race.json", "race.json"), ("rac\u00e9.json", "race\u0301.json")):
            with self.subTest(names=names), tempfile.TemporaryDirectory(dir=self.root) as tmp:
                parent = Path(tmp)
                targets = [parent / name for name in names]
                # Probe this exact pair on the test volume, then restore absence
                # before either child executes its canonical-path check.
                targets[0].touch()
                aliases = targets[1].exists()
                if aliases:
                    self.assertEqual(targets[0].stat().st_ino, targets[1].stat().st_ino)
                targets[0].unlink()
                procs = []
                try:
                    for i, target in enumerate(targets):
                        req = dict(operation="create", op_id="create-" + str(i),
                                   base_revision=None, document=document(ids=True))
                        procs.append(subprocess.Popen([sys.executable, "-c", code, str(target), json.dumps(req)],
                            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True))
                    for proc in procs:
                        self.assertEqual(proc.stdout.readline().strip(), "ready")
                    for proc in procs:
                        proc.stdin.write("go\n"); proc.stdin.flush()
                    results = []
                    for proc in procs:
                        out, err = proc.communicate(timeout=20)
                        self.assertEqual(proc.returncode, 0, err)
                        results.append(json.loads(out))
                finally:
                    for proc in procs:
                        if proc.poll() is None:
                            proc.kill(); proc.communicate()
                if aliases:
                    self.assertEqual(sum(r["ok"] for r in results), 1, results)
                    self.assertEqual([r["code"] for r in results if not r["ok"]], ["path.alias"], results)
                    winner = targets[next(i for i, r in enumerate(results) if r["ok"])]
                    loser = targets[next(i for i, r in enumerate(results) if not r["ok"])]
                    self.assertTrue(store.timeline_store_status(winner)["ok"])
                    self.assertFalse(store._location(loser).exists())
                else:
                    self.assertTrue(all(r["ok"] for r in results), results)
                    self.assertNotEqual(results[0]["project_id"], results[1]["project_id"])
                    self.assertNotEqual(targets[0].stat().st_ino, targets[1].stat().st_ino)
                    for target in targets:
                        self.assertTrue(store.timeline_store_status(target)["ok"])
                self.assertEqual(len(list(parent.glob(".vtx-store-*/manifest.json"))), 1 if aliases else 2)


if __name__ == "__main__":
    unittest.main()
