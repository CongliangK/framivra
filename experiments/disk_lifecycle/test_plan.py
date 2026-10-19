"""Ordinary, retained-fixture tests for the explicit read-only plan contract.

Run only with -B and DISK_LIFECYCLE_TEST_ROOT pointing to a newly approved
evidence directory. Fixtures are deliberately retained; there is no cleanup.
Production source is read as inert text and is never imported or executed.
"""

import ast
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from . import __main__ as cli
from . import inventory
from . import plan


KINDS = ("LOGS", "DIAGNOSTICS", "SUMMARY", "SAFETY_MARGIN")
TABLE_NAMES = ("OUT_PROTECTED_NAMES", "OUT_PROTECTED_DIRS", "OUT_PROTECTED_PATTERNS")
DECISIONS = {"PROTECTED", "HELD", "UNKNOWN", "REVIEW_ONLY_PNG"}


def object_ref(root_id, relative_path):
    return {"root_id": root_id, "relative_path": relative_path}


def new_fixture_directory(prefix):
    location = os.environ.get("DISK_LIFECYCLE_TEST_ROOT")
    if not location or not Path(location).is_dir():
        raise RuntimeError("DISK_LIFECYCLE_TEST_ROOT must name an existing approved evidence directory")
    return Path(tempfile.mkdtemp(prefix=prefix, dir=location))


def source_text():
    # This fixed checkout path is the approved static protection source.
    return (Path(__file__).parents[2] / "vtx" / "hygiene.py").read_text(encoding="utf-8")


def source_tables(source):
    """Extract expected literal values without evaluating any source node."""
    result = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in TABLE_NAMES:
                if not isinstance(node.value, ast.Tuple):
                    raise AssertionError("reviewed source table is no longer a literal tuple")
                values = []
                for value in node.value.elts:
                    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                        raise AssertionError("reviewed source table contains a non-string")
                    values.append(value.value)
                result[target.id] = tuple(values)
    return result


class OrdinaryPlanFixture(unittest.TestCase):
    def setUp(self):
        self.base = new_fixture_directory("plan-")
        self.gfx = self.base / "gfx"
        self.final = self.base / "final"
        self.evidence = self.base / "evidence"
        for directory in (self.gfx, self.final, self.evidence):
            directory.mkdir()
        # These bytes intentionally are not decoded as media or images.
        (self.gfx / "frame.PNG").write_bytes(b"arbitrary synthetic png fixture\n")
        (self.final / "output.bin").write_bytes(b"synthetic final declaration target\n")
        (self.evidence / "receipt.txt").write_bytes(b"caller-supplied evidence fixture\n")
        self.evidence_ref = object_ref("evidence", "receipt.txt")
        self.request = {
            "schema": "framivra.disk-lifecycle-request/1",
            "project_id": "test-project",
            "run_id": "test-run",
            "roots": [
                {"id": "gfx", "path": str(self.gfx), "role": "gfx"},
                {"id": "final", "path": str(self.final), "role": "final"},
                {"id": "evidence", "path": str(self.evidence), "role": "evidence"},
            ],
            "retain_paths": [],
            "hold_reasons": [],
            "code_ref": {"commit": "caller-supplied-commit", "tree": "caller-supplied-tree"},
            "ownership": [self.ownership_for("gfx", "frame.PNG")],
            "delivery_context": {
                "run_id": "test-run",
                "final": object_ref("final", "output.bin"),
                "verification": {"status": "PASS", "evidence_refs": [self.evidence_ref]},
                "finish": {"required": True, "status": "PASS", "evidence_refs": [self.evidence_ref]},
                "notification": {"status": "SENT_WITH_REFERENCE", "reference": "caller:notice:001"},
                "evidence_refs": [self.evidence_ref],
            },
            "reserves": [
                {"reserve_id": kind.lower(), "scope_root_id": "gfx", "kind": kind,
                 "bytes": 0, "rationale": "explicit synthetic incremental reserve"}
                for kind in KINDS
            ],
        }

    def ownership_for(self, root_id, relative_path):
        return {
            "object": object_ref(root_id, relative_path),
            "owner_run_id": "test-run",
            "purpose": "NEW_ISOLATED_DERIVED_PNG",
            "rebuildable": True,
            "activity": {"status": "DECLARED_INACTIVE", "evidence_refs": [self.evidence_ref]},
            "references": {"status": "DECLARED_NONE", "evidence_refs": [self.evidence_ref]},
            "evidence_refs": [self.evidence_ref],
        }

    def report(self, request=None, observed=None):
        request = self.request if request is None else request
        cli.validate_request(request)
        if observed is None:
            observed = inventory.scan(request)
        result = plan.build_plan(request, observed)
        self.assertEqual(result["schema"], "framivra.delivery-cleanup-plan/1")
        self.assertEqual(result["mode"], "READ_ONLY")
        self.assertIs(result["execution_eligible"], False)
        self.assertEqual(result["permission_status"], "NOT_REQUESTED_OR_GRANTED_BY_THIS_REPORT")
        self.assertEqual(result["planned_operations"], [])
        self.assertTrue(result["preserved_experiments_notice"])
        for item in result["items"]:
            self.assertIn(item["decision"], DECISIONS)
            self.assertTrue(item["reasons"])
            self.assertIs(item["execution_eligible"], False)
        # JSON serialization is part of the stdout contract, including unknowns.
        json.dumps(result, allow_nan=False)
        return result

    def item(self, result, root_id="gfx", relative_path="frame.PNG"):
        matches = [item for item in result["items"]
                   if any(ref["root_id"] == root_id and ref["relative_path"] == relative_path
                          for ref in item["objects"])]
        self.assertEqual(len(matches), 1, (root_id, relative_path, result["items"]))
        return matches[0]

    def decision(self, request=None, observed=None, root_id="gfx", relative_path="frame.PNG"):
        return self.item(self.report(request, observed), root_id, relative_path)["decision"]

    def volumes(self, result):
        volumes = result["budget_report"]["volumes"]
        self.assertEqual(len(volumes), 1, "ordinary fixtures should share their evidence volume")
        return volumes[0]

    def write_request(self, request=None, text=None):
        path = self.base / ("request-%s.json" % len(list(self.base.glob("request-*.json"))))
        path.write_text(json.dumps(self.request if request is None else request) if text is None else text,
                        encoding="utf-8")
        return path


class PlanTruthTableTests(OrdinaryPlanFixture):
    def test_complete_supplied_png_is_review_only_and_never_executable(self):
        result = self.report()
        self.assertTrue(result["complete"])
        self.assertEqual(self.item(result)["decision"], "REVIEW_ONLY_PNG")
        self.assertIn("SUPPLIED_NOT_REVERIFIED", json.dumps(self.item(result)))
        self.assertEqual(self.item(result, "final", "output.bin")["decision"], "PROTECTED")
        self.assertEqual(self.item(result, "evidence", "receipt.txt")["decision"], "PROTECTED")
        self.assertIn("inventory", result)

    def test_each_missing_ownership_gate_is_unknown(self):
        for field in ("owner_run_id", "purpose", "rebuildable", "activity", "references", "evidence_refs"):
            with self.subTest(field=field):
                request = copy.deepcopy(self.request)
                del request["ownership"][0][field]
                self.assertEqual(self.decision(request), "UNKNOWN")
        request = copy.deepcopy(self.request)
        del request["ownership"]
        self.assertEqual(self.decision(request), "UNKNOWN")

    def test_activity_and_reference_gates(self):
        cases = (("activity", "ACTIVE", "HELD"), ("activity", "UNKNOWN", "UNKNOWN"),
                 ("references", "PRESENT", "HELD"), ("references", "UNKNOWN", "UNKNOWN"))
        for field, status, expected in cases:
            with self.subTest(field=field, status=status):
                request = copy.deepcopy(self.request)
                request["ownership"][0][field]["status"] = status
                self.assertEqual(self.decision(request), expected)
        for field in ("activity", "references"):
            for change in ("missing_status", "missing_evidence", "empty_evidence"):
                with self.subTest(field=field, change=change):
                    request = copy.deepcopy(self.request)
                    record = request["ownership"][0][field]
                    if change == "missing_status":
                        del record["status"]
                    elif change == "missing_evidence":
                        del record["evidence_refs"]
                    else:
                        record["evidence_refs"] = []
                    self.assertEqual(self.decision(request), "UNKNOWN")

    def test_other_run_and_global_hold_cannot_be_overridden(self):
        for location in ("owner", "delivery", "global_hold"):
            with self.subTest(location=location):
                request = copy.deepcopy(self.request)
                if location == "owner":
                    request["ownership"][0]["owner_run_id"] = "other-run"
                elif location == "delivery":
                    request["delivery_context"]["run_id"] = "other-run"
                else:
                    request["hold_reasons"] = ["Independent review remains open"]
                self.assertEqual(self.decision(request), "HELD")

    def test_experimental_purposes_remain_held_despite_complete_delivery(self):
        for purpose in ("BASELINE", "EXPERIMENT", "PERFORMANCE_COMPARISON",
                        "FAILURE_DIAGNOSTIC", "PENDING_INDEPENDENT_REVIEW"):
            with self.subTest(purpose=purpose):
                request = copy.deepcopy(self.request)
                request["ownership"][0]["purpose"] = purpose
                self.assertEqual(request["hold_reasons"], [])
                self.assertIs(request["ownership"][0]["rebuildable"], True)
                self.assertEqual(self.decision(request), "HELD")
        for purpose, expected in (("SHARED", "PROTECTED"), ("OTHER", "UNKNOWN"), ("UNKNOWN", "UNKNOWN")):
            with self.subTest(purpose=purpose):
                request = copy.deepcopy(self.request)
                request["ownership"][0]["purpose"] = purpose
                self.assertEqual(self.decision(request), expected)

    def test_verification_failure_cancel_pending_and_unknown(self):
        for status in ("FAIL", "CANCELLED", "PENDING", "UNKNOWN"):
            with self.subTest(status=status):
                request = copy.deepcopy(self.request)
                request["delivery_context"]["verification"]["status"] = status
                self.assertEqual(self.decision(request), "UNKNOWN" if status == "UNKNOWN" else "HELD")

    def test_missing_verification_or_notification_status_is_unknown(self):
        for field in ("verification", "notification"):
            with self.subTest(field=field):
                request = copy.deepcopy(self.request)
                del request["delivery_context"][field]["status"]
                self.assertEqual(self.decision(request), "UNKNOWN")

    def test_false_rebuildable_is_not_review_only(self):
        request = copy.deepcopy(self.request)
        request["ownership"][0]["rebuildable"] = False
        self.assertEqual(self.decision(request), "HELD")

    def test_finish_required_status_truth_table(self):
        for required in (True, False):
            for status in ("PASS", "NOT_REQUIRED", "FAIL", "CANCELLED", "PENDING", "UNKNOWN"):
                with self.subTest(required=required, status=status):
                    request = copy.deepcopy(self.request)
                    request["delivery_context"]["finish"] = {
                        "required": required, "status": status,
                        "reason": "No separate finish action applies to this synthetic request",
                        "evidence_refs": [self.evidence_ref],
                    }
                    success = (required and status == "PASS") or (not required and status == "NOT_REQUIRED")
                    expected = "REVIEW_ONLY_PNG" if success else "UNKNOWN" if status == "UNKNOWN" else "HELD"
                    self.assertEqual(self.decision(request), expected)

    def test_finish_missing_evidence_reason_or_required_is_unknown(self):
        for field in ("required", "status", "evidence_refs"):
            with self.subTest(field=field):
                request = copy.deepcopy(self.request)
                del request["delivery_context"]["finish"][field]
                self.assertEqual(self.decision(request), "UNKNOWN")
        for reason in (None, "", "   "):
            with self.subTest(reason=reason):
                request = copy.deepcopy(self.request)
                request["delivery_context"]["finish"] = {"required": False, "status": "NOT_REQUIRED"}
                if reason is not None:
                    request["delivery_context"]["finish"]["reason"] = reason
                self.assertEqual(self.decision(request), "UNKNOWN")
        request = copy.deepcopy(self.request)
        request["delivery_context"]["finish"] = {
            "required": False, "status": "NOT_REQUIRED", "reason": "Not required for this synthetic run"}
        self.assertEqual(self.decision(request), "REVIEW_ONLY_PNG")
        request = copy.deepcopy(self.request)
        request["delivery_context"]["finish"]["evidence_refs"] = []
        self.assertEqual(self.decision(request), "UNKNOWN")

    def test_notification_truth_table_and_nonempty_reference(self):
        for status in ("FAILED", "CANCELLED", "PENDING", "UNKNOWN"):
            with self.subTest(status=status):
                request = copy.deepcopy(self.request)
                request["delivery_context"]["notification"]["status"] = status
                self.assertEqual(self.decision(request), "UNKNOWN" if status == "UNKNOWN" else "HELD")
        for reference in (None, "", "   "):
            with self.subTest(reference=reference):
                request = copy.deepcopy(self.request)
                if reference is None:
                    del request["delivery_context"]["notification"]["reference"]
                else:
                    request["delivery_context"]["notification"]["reference"] = reference
                self.assertEqual(self.decision(request), "UNKNOWN")

    def test_missing_delivery_fields_and_evidence_never_imply_success(self):
        for field in ("run_id", "final", "verification", "finish", "notification", "evidence_refs"):
            with self.subTest(field=field):
                request = copy.deepcopy(self.request)
                del request["delivery_context"][field]
                self.assertEqual(self.decision(request), "UNKNOWN")
        request = copy.deepcopy(self.request)
        del request["delivery_context"]
        self.assertEqual(self.decision(request), "UNKNOWN")
        for location in ("delivery", "verification", "ownership"):
            with self.subTest(location=location):
                request = copy.deepcopy(self.request)
                target = (request["delivery_context"] if location == "delivery" else
                          request["delivery_context"]["verification"] if location == "verification" else
                          request["ownership"][0])
                target["evidence_refs"] = []
                self.assertEqual(self.decision(request), "UNKNOWN")

    def test_unobserved_final_and_evidence_are_unknown(self):
        request = copy.deepcopy(self.request)
        request["delivery_context"]["final"] = object_ref("final", "not-created.bin")
        self.assertEqual(self.decision(request), "UNKNOWN")
        request = copy.deepcopy(self.request)
        request["ownership"][0]["evidence_refs"] = [object_ref("evidence", "not-created.txt")]
        self.assertEqual(self.decision(request), "UNKNOWN")

    def test_synthetic_changed_final_or_evidence_blocks_review(self):
        for root_id, name in (("final", "output.bin"), ("evidence", "receipt.txt")):
            with self.subTest(root_id=root_id):
                observed = inventory.scan(self.request)
                entry = next(entry for entry in observed["entries"]
                             if any(ref["root_id"] == root_id and ref["relative_path"] == name
                                    for ref in entry["objects"]))
                entry["status"] = "CHANGED_DURING_SCAN"
                self.assertIn(self.decision(observed=observed), {"HELD", "UNKNOWN"})

    def test_changed_owned_file_keeps_known_shared_and_baseline_retention(self):
        for purpose, expected in (("SHARED", "PROTECTED"), ("BASELINE", "HELD")):
            with self.subTest(purpose=purpose):
                request = copy.deepcopy(self.request)
                request["ownership"][0]["purpose"] = purpose
                observed = inventory.scan(request)
                entry = next(entry for entry in observed["entries"]
                             if entry["root_id"] == "gfx" and entry["relative_path"] == "frame.PNG")
                entry["status"] = "CHANGED_DURING_SCAN"
                entry["metadata_stable"] = False
                result = self.report(request, observed)
                self.assertEqual(self.item(result)["decision"], expected)
                self.assertFalse(result["complete"])
                self.assertTrue(result["discrepancies"])
                self.assertFalse(any(item["decision"] == "REVIEW_ONLY_PNG" for item in result["items"]))

    def test_unprotected_evidence_and_nonfinal_role_do_not_satisfy_gate(self):
        (self.gfx / "statement.txt").write_bytes(b"synthetic statement\n")
        request = copy.deepcopy(self.request)
        request["ownership"][0]["evidence_refs"] = [object_ref("gfx", "statement.txt")]
        self.assertEqual(self.decision(request), "UNKNOWN")
        request = copy.deepcopy(self.request)
        request["roots"][1]["role"] = "encode"
        self.assertEqual(self.decision(request), "UNKNOWN")

    def test_explicitly_retained_evidence_can_satisfy_gate(self):
        (self.gfx / "statement.txt").write_bytes(b"synthetic retained statement\n")
        request = copy.deepcopy(self.request)
        request["ownership"][0]["evidence_refs"] = [object_ref("gfx", "statement.txt")]
        request["retain_paths"] = [object_ref("gfx", "statement.txt")]
        self.assertEqual(self.decision(request), "REVIEW_ONLY_PNG")

    def test_protected_roles_override_complete_rebuildable_declarations(self):
        for role in ("input", "music", "sdk", "download", "final", "evidence", "history", "shared"):
            with self.subTest(role=role):
                request = copy.deepcopy(self.request)
                request["roots"][0]["role"] = role
                self.assertEqual(self.decision(request), "PROTECTED")

    def test_lock_ledger_receipt_and_completion_metadata_remain_protected(self):
        request = copy.deepcopy(self.request)
        names = ("ordinary.lock", "ledger.json", "receipt.jsonl", "run.complete", "complete")
        for name in names:
            (self.gfx / name).write_bytes(b"ordinary synthetic metadata\n")
            request["ownership"].append(self.ownership_for("gfx", name))
        result = self.report(request)
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(self.item(result, "gfx", name)["decision"], "PROTECTED")

    def test_retain_directory_protects_observed_descendant(self):
        directory = self.gfx / "ordinary"
        directory.mkdir()
        (directory / "child.PNG").write_bytes(b"synthetic child\n")
        request = copy.deepcopy(self.request)
        request["ownership"].append(self.ownership_for("gfx", "ordinary/child.PNG"))
        request["retain_paths"] = [object_ref("gfx", "ordinary")]
        self.assertEqual(self.decision(request, relative_path="ordinary/child.PNG"), "PROTECTED")

    def test_changed_retained_directory_keeps_descendants_protected(self):
        request = copy.deepcopy(self.request)
        request["retain_paths"] = [object_ref("gfx", ".")]
        observed = inventory.scan(request)
        directory = next(entry for entry in observed["entries"]
                         if entry["root_id"] == "gfx" and entry["relative_path"] == ".")
        # A changed ordinary observation is simulated; no file or directory is
        # renamed, removed, replaced, or otherwise changed by this test.
        directory["status"] = "CHANGED_DURING_SCAN"
        directory["metadata_stable"] = False
        result = self.report(request, observed)
        self.assertEqual(self.item(result, "gfx", ".")["decision"], "PROTECTED")
        self.assertEqual(self.item(result)["decision"], "PROTECTED")
        self.assertFalse(result["complete"])
        self.assertTrue(result["discrepancies"])
        self.assertFalse(any(item["decision"] == "REVIEW_ONLY_PNG" for item in result["items"]))

    def test_directory_ownership_is_not_inherited_by_files(self):
        request = copy.deepcopy(self.request)
        request["ownership"][0]["object"] = object_ref("gfx", ".")
        self.assertEqual(self.decision(request), "UNKNOWN")

    def test_identical_relative_names_in_distinct_roots_do_not_share_ownership(self):
        other = self.base / "other-gfx"
        other.mkdir()
        (other / "frame.PNG").write_bytes(b"different ordinary file\n")
        request = copy.deepcopy(self.request)
        request["roots"].append({"id": "other", "path": str(other), "role": "gfx"})
        self.assertEqual(self.decision(request, root_id="other"), "UNKNOWN")

    def test_identical_duplicate_declaration_is_not_a_conflict(self):
        request = copy.deepcopy(self.request)
        request["ownership"].append(copy.deepcopy(request["ownership"][0]))
        self.assertEqual(self.decision(request), "REVIEW_ONLY_PNG")

    def test_conflicting_declarations_are_held(self):
        for field, value in (("owner_run_id", "different-run"), ("purpose", "EXPERIMENT"),
                             ("activity", {"status": "ACTIVE", "evidence_refs": [self.evidence_ref]}),
                             ("references", {"status": "PRESENT", "evidence_refs": [self.evidence_ref]})):
            with self.subTest(field=field):
                request = copy.deepcopy(self.request)
                conflict = copy.deepcopy(request["ownership"][0])
                conflict[field] = value
                request["ownership"].append(conflict)
                self.assertEqual(self.decision(request), "HELD")

    def test_plain_overlapping_roots_preserve_roles_and_conflicts(self):
        request = copy.deepcopy(self.request)
        request["roots"].append({"id": "second-gfx", "path": str(self.gfx), "role": "gfx"})
        conflict = self.ownership_for("second-gfx", "frame.PNG")
        conflict["owner_run_id"] = "different-run"
        request["ownership"].append(conflict)
        result = self.report(request)
        self.assertEqual(self.item(result)["decision"], "HELD")
        self.assertIs(self.item(result), self.item(result, "second-gfx"))
        request["roots"][-1]["role"] = "input"
        self.assertEqual(self.decision(request), "PROTECTED")

    def test_plain_overlapping_nonprotected_roles_are_not_unambiguous_gfx(self):
        for role in ("encode", "audio", "unknown"):
            with self.subTest(role=role):
                request = copy.deepcopy(self.request)
                request["roots"].append({"id": "second-role", "path": str(self.gfx), "role": role})
                self.assertEqual(self.decision(request), "UNKNOWN")

    def test_synthetic_inode_group_propagates_protected_role(self):
        observed = inventory.scan(self.request)
        gfx = next(entry for entry in observed["entries"]
                   if entry["root_id"] == "gfx" and entry["relative_path"] == "frame.PNG")
        protected = next(entry for entry in observed["entries"]
                         if entry["root_id"] == "evidence" and entry["relative_path"] == "receipt.txt")
        # This changes only in-memory observations, never filesystem links.
        protected["inode_group"] = gfx["inode_group"]
        protected["st_dev"] = gfx["st_dev"]
        protected["st_ino"] = gfx["st_ino"]
        protected["st_nlink"] = gfx["st_nlink"] = 2
        self.assertEqual(self.decision(observed=observed), "PROTECTED")

    def test_synthetic_inode_group_propagates_retain_purpose_and_conflict(self):
        (self.gfx / "peer.PNG").write_bytes((self.gfx / "frame.PNG").read_bytes())
        for mode, expected in (("retain", "PROTECTED"), ("purpose", "HELD"), ("conflict", "HELD")):
            with self.subTest(mode=mode):
                request = copy.deepcopy(self.request)
                peer_owner = self.ownership_for("gfx", "peer.PNG")
                request["ownership"].append(peer_owner)
                if mode == "retain":
                    request["retain_paths"] = [object_ref("gfx", "peer.PNG")]
                elif mode == "purpose":
                    peer_owner["purpose"] = "PENDING_INDEPENDENT_REVIEW"
                else:
                    peer_owner["owner_run_id"] = "different-run"
                observed = inventory.scan(request)
                gfx = next(entry for entry in observed["entries"]
                           if entry["root_id"] == "gfx" and entry["relative_path"] == "frame.PNG")
                peer = next(entry for entry in observed["entries"]
                            if entry["root_id"] == "gfx" and entry["relative_path"] == "peer.PNG")
                peer["inode_group"] = gfx["inode_group"]
                peer["st_dev"] = gfx["st_dev"]
                peer["st_ino"] = gfx["st_ino"]
                peer["st_nlink"] = gfx["st_nlink"] = 2
                result = self.report(request, observed)
                self.assertEqual(self.item(result)["decision"], expected)
                self.assertEqual(self.item(result, "gfx", "peer.PNG")["decision"], expected)

    def test_synthetic_unobserved_link_names_are_held_without_reclaim_promise(self):
        observed = inventory.scan(self.request)
        entry = next(entry for entry in observed["entries"]
                     if entry["root_id"] == "gfx" and entry["relative_path"] == "frame.PNG")
        entry["st_nlink"] = 2
        result = self.report(observed=observed)
        self.assertEqual(self.item(result)["decision"], "HELD")
        self.assertIsNone(result["summary"]["estimated_reclaimable_physical_bytes"])

    def test_non_png_and_zero_sized_objects_are_never_review_only(self):
        for name, content in (("ordinary.bin", b"ordinary file"), ("empty.PNG", b"")):
            with self.subTest(name=name):
                (self.gfx / name).write_bytes(content)
                request = copy.deepcopy(self.request)
                request["ownership"].append(self.ownership_for("gfx", name))
                self.assertNotEqual(self.decision(request, relative_path=name), "REVIEW_ONLY_PNG")

    def test_optional_code_ref_is_explicitly_unknown_without_authenticating_labels(self):
        for supplied in (None, {}, {"commit": "caller-commit"}, {"tree": "caller-tree"}):
            with self.subTest(supplied=supplied):
                request = copy.deepcopy(self.request)
                if supplied is None:
                    del request["code_ref"]
                else:
                    request["code_ref"] = supplied
                result = self.report(request)
                code_ref = result["inventory"]["code_ref"]
                for name in ("commit", "tree"):
                    value = (supplied or {}).get(name)
                    self.assertEqual(code_ref[name], value)
                    self.assertEqual(code_ref["field_status"][name], "UNKNOWN" if value is None else "CALLER_SUPPLIED")
                    if value is None:
                        self.assertEqual(code_ref["unknown_fields"][name], "NOT_SUPPLIED")
                # These optional caller labels cannot authenticate a run or
                # replace any ownership, evidence, delivery, or reserve gate.
                self.assertEqual(self.item(result)["decision"], "REVIEW_ONLY_PNG")


class ReserveContractTests(OrdinaryPlanFixture):
    def assert_unknown_total(self, result):
        volume = self.volumes(result)
        self.assertIsNone(volume["required_reserve_logical_total"])
        self.assertIsNone(volume["observed_plus_reserve_logical_bytes"])
        self.assertIsNone(volume["observed_plus_reserve_allocated_bytes"])
        self.assertFalse(result["complete"])
        return volume

    def test_explicit_zero_is_complete_and_logical_only(self):
        result = self.report()
        volume = self.volumes(result)
        self.assertEqual(volume["required_reserve_logical_total"], 0)
        self.assertEqual(volume["known_reserve_logical_subtotal"], 0)
        self.assertEqual(volume["observed_plus_reserve_logical_bytes"], volume["observed_logical_unique_bytes"])
        self.assertIsNone(volume["observed_plus_reserve_allocated_bytes"])
        self.assertEqual(volume["reserve_basis"], "CALLER_DECLARED_INCREMENTAL_LOGICAL_BYTES")
        self.assertIn("RESERVE_ALLOCATION_UNKNOWN", json.dumps(volume))
        self.assertTrue(volume["free_bytes_observed_at"])
        comparison = volume["reserve_vs_free_numeric_comparison"]
        self.assertTrue(comparison["free_observed_at"])
        self.assertEqual(comparison["instant_free_bytes"], volume["volume_free_bytes"])
        budget = result["budget_report"]
        self.assertIsNone(budget["future_growth_bytes"])
        self.assertIsNone(budget["peak_disk_bytes"])
        self.assertIsNone(budget["estimated_reclaimable_physical_bytes"])
        self.assertIs(budget["hard_limit_enforced"], False)
        self.assertEqual(budget["peak_status"], "UNKNOWN_SINGLE_SCAN")

    def test_missing_reserves_and_missing_kind_remain_unknown(self):
        for mode in ("absent", "empty", "missing_kind"):
            with self.subTest(mode=mode):
                request = copy.deepcopy(self.request)
                if mode == "absent":
                    del request["reserves"]
                elif mode == "empty":
                    request["reserves"] = []
                else:
                    request["reserves"].pop()
                volume = self.assert_unknown_total(self.report(request))
                self.assertEqual(volume["known_reserve_logical_subtotal"], 0)

    def test_missing_bytes_or_rationale_is_unknown_without_defaulting(self):
        for field in ("bytes", "rationale"):
            with self.subTest(field=field):
                request = copy.deepcopy(self.request)
                del request["reserves"][0][field]
                self.assert_unknown_total(self.report(request))

    def test_complete_incremental_logical_reserves_sum_exactly_once(self):
        request = copy.deepcopy(self.request)
        for reserve, amount in zip(request["reserves"], (11, 23, 37, 41)):
            reserve["bytes"] = amount
        result = self.report(request)
        volume = self.volumes(result)
        self.assertEqual(volume["required_reserve_logical_total"], 112)
        self.assertEqual(volume["known_reserve_logical_subtotal"], 112)
        self.assertEqual(volume["observed_plus_reserve_logical_bytes"],
                         volume["observed_logical_unique_bytes"] + 112)
        self.assertIsNone(volume["observed_plus_reserve_allocated_bytes"])
        self.assertIn("LOGICAL_BUDGET_VS_INSTANT_FREE_NUMERIC_ONLY", json.dumps(volume))

    def test_same_reserve_id_across_known_same_volume_roots_is_deduplicated(self):
        request = copy.deepcopy(self.request)
        request["reserves"][0]["bytes"] = 71
        repeated = copy.deepcopy(request["reserves"][0])
        repeated["scope_root_id"] = "final"
        request["reserves"].append(repeated)
        self.assertEqual(self.volumes(self.report(request))["required_reserve_logical_total"], 71)

    def test_free_comparison_is_only_a_timestamped_numeric_relation(self):
        request = copy.deepcopy(self.request)
        request["reserves"][0]["bytes"] = 112
        for free, relation in ((111, "GT"), (112, "EQ"), (113, "LT"), (None, "UNKNOWN")):
            with self.subTest(free=free):
                observed = inventory.scan(request)
                observed["volumes"][0]["volume_free_bytes"] = free
                volume = self.volumes(self.report(request, observed))
                comparison = volume["reserve_vs_free_numeric_comparison"]
                self.assertEqual(comparison["relation"], relation)
                self.assertEqual(comparison["reserve_logical_bytes"], 112)
                self.assertEqual(comparison["instant_free_bytes"], free)
                self.assertEqual(comparison["basis"], "LOGICAL_BUDGET_VS_INSTANT_FREE_NUMERIC_ONLY")
                self.assertNotIn("headroom", volume)

    def test_conflicting_same_id_and_same_kind_multiple_ids_are_unknown(self):
        for field, value in (("bytes", 1), ("rationale", "different estimate"),
                             ("kind", "SUMMARY"), ("reserve_id", "another-logs-budget")):
            with self.subTest(field=field):
                request = copy.deepcopy(self.request)
                conflicting = copy.deepcopy(request["reserves"][0])
                conflicting[field] = value
                request["reserves"].append(conflicting)
                self.assert_unknown_total(self.report(request))

    def test_unknown_volume_mapping_does_not_select_the_convenient_record(self):
        request = copy.deepcopy(self.request)
        repeated = copy.deepcopy(request["reserves"][0])
        repeated["scope_root_id"] = "final"
        request["reserves"].append(repeated)
        observed = inventory.scan(request)
        observed["root_observations"]["final"]["volume_id"] = None
        self.assert_unknown_total(self.report(request, observed))

    def test_partial_inventory_has_no_complete_combined_total_or_review(self):
        request = copy.deepcopy(self.request)
        request["max_entries"] = 1
        result = self.report(request)
        self.assertFalse(result["complete"])
        self.assertFalse(any(item["decision"] == "REVIEW_ONLY_PNG" for item in result["items"]))
        for volume in result["budget_report"]["volumes"]:
            self.assertIsNone(volume["observed_plus_reserve_logical_bytes"])
            self.assertIsNone(volume["observed_plus_reserve_allocated_bytes"])


class RequestSchemaTests(OrdinaryPlanFixture):
    def assert_rejected_before_scan(self, request=None, raw=None, extra_args=()):
        path = self.write_request(request, raw)
        output = io.StringIO()
        diagnostic = io.StringIO()
        with mock.patch.object(cli, "scan", side_effect=AssertionError("schema failure started a scan")) as scan:
            with redirect_stdout(output), redirect_stderr(diagnostic):
                code = cli.main(["delivery-cleanup-plan", "--request", str(path), *extra_args])
        self.assertEqual(code, 2)
        scan.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "REQUEST_ERROR")
        self.assertIs(result["scan_started"], False)
        self.assertIs(result["execution_eligible"], False)
        self.assertEqual(result["planned_operations"], [])
        self.assertTrue(diagnostic.getvalue().strip())

    def test_duplicate_root_id_and_undeclared_object_root_reject_before_scan(self):
        request = copy.deepcopy(self.request)
        request["roots"].append(copy.deepcopy(request["roots"][0]))
        self.assert_rejected_before_scan(request)
        request = copy.deepcopy(self.request)
        request["ownership"][0]["object"]["root_id"] = "not-declared"
        self.assert_rejected_before_scan(request)
        request = copy.deepcopy(self.request)
        request["reserves"][0]["scope_root_id"] = "not-declared"
        self.assert_rejected_before_scan(request)

    def test_unknown_fields_invalid_enums_and_mistyped_fields(self):
        changes = [
            ((), "unexpected", True), ((), "schema", "unsupported-schema"),
            ((), "project_id", ""), ((), "hold_reasons", "not-an-array"),
            (("roots", 0), "role", "unrecognized-role"),
            (("roots", 0), "unexpected", True),
            (("ownership", 0), "purpose", "unrecognized-purpose"),
            (("ownership", 0), "rebuildable", 1),
            (("ownership", 0, "activity"), "status", "INACTIVE"),
            (("ownership", 0, "references"), "status", "NONE"),
            (("delivery_context", "verification"), "status", "VERIFIED"),
            (("delivery_context", "finish"), "required", 1),
            (("delivery_context", "finish"), "status", "DONE"),
            (("delivery_context", "notification"), "status", "SENT"),
            (("reserves", 0), "kind", "ALLOCATED"),
            (("reserves", 0), "bytes", -1),
            (("reserves", 0), "bytes", True),
            (("reserves", 0), "bytes", 1.5),
            ((), "max_entries", 0), ((), "max_entries", -1),
            ((), "max_entries", True), ((), "max_entries", 1.5),
            ((), "max_scan_seconds", 0), ((), "max_scan_seconds", -1),
            ((), "max_scan_seconds", True),
        ]
        for location, field, value in changes:
            with self.subTest(location=location, field=field, value=value):
                request = copy.deepcopy(self.request)
                target = request
                for component in location:
                    target = target[component]
                target[field] = value
                self.assert_rejected_before_scan(request)

    def test_missing_required_fields_and_wrong_container_types(self):
        for field in ("schema", "project_id", "run_id", "roots", "retain_paths", "hold_reasons"):
            with self.subTest(field=field):
                request = copy.deepcopy(self.request)
                del request[field]
                self.assert_rejected_before_scan(request)
        for value in ([], None, "ordinary text", 1):
            with self.subTest(value=value):
                self.assert_rejected_before_scan(raw=json.dumps(value))

    def test_duplicate_json_keys_and_nonfinite_numbers_reject_before_scan(self):
        raw = json.dumps(self.request).replace('"project_id": "test-project"',
                                              '"project_id": "test-project", "project_id": "again"')
        self.assert_rejected_before_scan(raw=raw)
        for number in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(number=number):
                request = copy.deepcopy(self.request)
                request["max_scan_seconds"] = number
                self.assert_rejected_before_scan(raw=json.dumps(request))

    def test_unknown_execution_and_output_flags_reject_before_scan(self):
        for flag in ("--apply", "--execute", "--output"):
            with self.subTest(flag=flag):
                self.assert_rejected_before_scan(extra_args=(flag,))

    def test_absent_explicit_request_is_usage_error_without_scan(self):
        output = io.StringIO()
        with mock.patch.object(cli, "scan") as scan:
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                code = cli.main(["inventory"])
        self.assertEqual(code, 2)
        self.assertIs(json.loads(output.getvalue())["scan_started"], False)
        scan.assert_not_called()

    def test_cli_known_hold_is_complete_but_missing_declaration_is_exit_three(self):
        for purpose, code, decision in (("EXPERIMENT", 0, "HELD"), ("UNKNOWN", 3, "UNKNOWN")):
            with self.subTest(purpose=purpose):
                request = copy.deepcopy(self.request)
                request["ownership"][0]["purpose"] = purpose
                path = self.write_request(request)
                output = io.StringIO()
                diagnostic = io.StringIO()
                with redirect_stdout(output), redirect_stderr(diagnostic):
                    actual_code = cli.main(["delivery-cleanup-plan", "--request", str(path)])
                self.assertEqual(actual_code, code)
                result = json.loads(output.getvalue())
                self.assertEqual(self.item(result)["decision"], decision)
                self.assertEqual(diagnostic.getvalue(), "")


class ProtectionSourceTests(OrdinaryPlanFixture):
    def test_current_real_source_is_parsed_as_inert_literal_tables(self):
        text = source_text()
        parsed = plan.parse_protection_source(text)
        self.assertEqual(parsed["parse_status"], "PARSED")
        for name, values in source_tables(text).items():
            self.assertEqual(tuple(parsed["tables"][name]), values)
        loaded = plan.load_protection_source()
        self.assertEqual(loaded["parse_status"], "PARSED")
        self.assertEqual(loaded["relative_path"], "vtx/hygiene.py")
        self.assertEqual(loaded["sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertTrue(loaded["adapter_contract_version"])

    def test_protected_names_patterns_and_directory_children_come_from_single_source(self):
        tables = source_tables(source_text())
        request = copy.deepcopy(self.request)
        paths = list(tables["OUT_PROTECTED_NAMES"])
        paths.extend(pattern.replace("*", "sample").replace("?", "x")
                     for pattern in tables["OUT_PROTECTED_PATTERNS"])
        for name in paths:
            (self.gfx / name).write_bytes(b"ordinary protected-name fixture\n")
            request["ownership"].append(self.ownership_for("gfx", name))
        for name in tables["OUT_PROTECTED_DIRS"]:
            directory = self.gfx / name
            directory.mkdir()
            relative = name + "/child.PNG"
            (directory / "child.PNG").write_bytes(b"ordinary protected descendant\n")
            request["ownership"].append(self.ownership_for("gfx", relative))
            paths.append(relative)
        result = self.report(request)
        for name in paths:
            with self.subTest(name=name):
                self.assertEqual(self.item(result, "gfx", name)["decision"], "PROTECTED")

    def source_variants(self):
        original = ast.parse(source_text())

        def changed(mutator):
            tree = copy.deepcopy(original)
            mutator(tree)
            return ast.unparse(ast.fix_missing_locations(tree))

        def table(tree):
            return next(node for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == TABLE_NAMES[0]
                                for target in node.targets))

        def function(tree):
            return next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "out_protected")

        yield "syntax", "def invalid syntax("
        yield "missing_table", changed(lambda tree: tree.body.remove(table(tree)))
        yield "duplicate_table", changed(lambda tree: tree.body.append(copy.deepcopy(table(tree))))
        yield "rebound_table", changed(lambda tree: tree.body.append(
            ast.Assign(targets=[ast.Name(id=TABLE_NAMES[0], ctx=ast.Store())],
                       value=ast.Tuple(elts=[ast.Constant(value="different")], ctx=ast.Load()))))
        yield "list_not_tuple", changed(lambda tree: setattr(table(tree), "value",
            ast.List(elts=copy.deepcopy(table(tree).value.elts), ctx=ast.Load())))
        yield "computed_tuple", changed(lambda tree: setattr(table(tree), "value",
            ast.BinOp(left=copy.deepcopy(table(tree).value), op=ast.Add(),
                      right=ast.Tuple(elts=[], ctx=ast.Load()))))
        yield "non_string_literal", changed(lambda tree: table(tree).value.elts.append(ast.Constant(value=1)))
        yield "expanded_tuple", changed(lambda tree: table(tree).value.elts.append(
            ast.Starred(value=ast.Name(id="EXTRA", ctx=ast.Load()), ctx=ast.Load())))
        yield "missing_function", changed(lambda tree: tree.body.remove(function(tree)))
        yield "duplicate_function", changed(lambda tree: tree.body.append(copy.deepcopy(function(tree))))
        yield "altered_return", changed(lambda tree: setattr(function(tree), "body", [ast.Return(value=ast.Constant(value=True))]))
        yield "extra_statement", changed(lambda tree: function(tree).body.insert(0, ast.Pass()))
        yield "different_parameter", changed(lambda tree: setattr(function(tree).args.args[0], "arg", "other_name"))
        yield "function_decorator", changed(lambda tree: function(tree).decorator_list.append(ast.Name(id="decorate", ctx=ast.Load())))
        yield "default_argument", changed(lambda tree: function(tree).args.defaults.append(ast.Constant(value="default")))
        yield "chained_assignment", changed(lambda tree: table(tree).targets.append(ast.Name(id="OTHER", ctx=ast.Store())))
        yield "nested_assignment", changed(lambda tree: tree.body.append(ast.If(
            test=ast.Constant(value=False), body=[copy.deepcopy(table(tree))], orelse=[])))
        yield "predicate_rebound", changed(lambda tree: tree.body.append(ast.Assign(
            targets=[ast.Name(id="out_protected", ctx=ast.Store())], value=ast.Constant(value=None))))
        yield "dependency_rebound", changed(lambda tree: tree.body.append(ast.Assign(
            targets=[ast.Name(id="fnmatch", ctx=ast.Store())], value=ast.Constant(value=None))))

    def test_changed_source_shapes_fail_closed_without_execution(self):
        observed = inventory.scan(self.request)
        for label, text in self.source_variants():
            with self.subTest(label=label):
                parsed = plan.parse_protection_source(text)
                self.assertNotEqual(parsed["parse_status"], "PARSED")
                with mock.patch.object(plan, "load_protection_source", return_value=parsed):
                    result = self.report(observed=observed)
                self.assertFalse(result["complete"])
                self.assertFalse(any(item["decision"] == "REVIEW_ONLY_PNG" for item in result["items"]))
                self.assertIn("PROTECTION_SOURCE_UNKNOWN", json.dumps(result))

    def test_unreadable_protection_source_still_allows_inventory_report(self):
        with mock.patch("builtins.open", side_effect=OSError("synthetic source read failure")):
            source = plan.load_protection_source()
        self.assertNotEqual(source["parse_status"], "PARSED")
        observed = inventory.scan(self.request)
        self.assertTrue(observed["complete"])
        with mock.patch.object(plan, "load_protection_source", return_value=source):
            result = self.report(observed=observed)
        self.assertFalse(result["complete"])
        self.assertNotEqual(self.item(result)["decision"], "REVIEW_ONLY_PNG")

    def test_changed_source_metadata_and_bytes_are_unknown(self):
        path = Path(__file__).parents[2] / "vtx" / "hygiene.py"
        original = path.stat()
        changed = SimpleNamespace(**{name: getattr(original, name)
                                    for name in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")})
        changed.st_size += 1
        with mock.patch.object(plan.os, "stat", side_effect=[original, changed]):
            result = plan.load_protection_source()
        self.assertEqual(result["parse_status"], "UNKNOWN")
        self.assertIn("changed", result["reason"])

        raw = path.read_bytes()
        opened = mock.mock_open(read_data=raw)
        opened.return_value.read.side_effect = [raw, raw + b"\n"]
        with mock.patch("builtins.open", opened), mock.patch.object(plan.os, "fstat", return_value=original):
            result = plan.load_protection_source()
        self.assertEqual(result["parse_status"], "UNKNOWN")
        self.assertIn("changed", result["reason"])


class IndependentReviewRegressionTests(OrdinaryPlanFixture):
    def test_python39_ast_api_shape_preserves_normal_source_and_cli(self):
        # This is an API-shape simulation on the current Python, not a claim
        # that an actual Python 3.9 interpreter was executed.
        unavailable = {"MatchAs", "MatchStar", "MatchMapping", "type_param"}
        ast39 = SimpleNamespace(**{name: getattr(ast, name) for name in dir(ast)
                                   if name not in unavailable})
        path = self.write_request()
        with mock.patch.object(plan, "ast", ast39):
            parsed = plan.parse_protection_source(source_text())
            self.assertEqual(parsed["parse_status"], "PARSED")
            for command in ("inventory", "delivery-cleanup-plan"):
                with self.subTest(command=command):
                    output, diagnostic = io.StringIO(), io.StringIO()
                    with redirect_stdout(output), redirect_stderr(diagnostic):
                        code = cli.main([command, "--request", str(path)])
                    self.assertEqual(code, 0)
                    result = json.loads(output.getvalue())
                    self.assertTrue(result["complete"])
                    self.assertIs(result["execution_eligible"], False)
                    self.assertEqual(result["planned_operations"], [])
                    self.assertEqual(diagnostic.getvalue(), "")

    def test_wildcard_import_text_is_unknown_without_loading_any_module(self):
        observed = inventory.scan(self.request)
        original = source_text()
        for suffix in ("\nfrom fnmatch import *\n", "\nfrom ordinary_unavailable_fixture import *\n"):
            with self.subTest(suffix=suffix):
                # The second name deliberately need not exist: static parsing
                # must neither load it nor infer its possible export bindings.
                parsed = plan.parse_protection_source(original + suffix)
                self.assertEqual(parsed["parse_status"], "UNKNOWN")
                self.assertIn("wildcard import", parsed["reason"])
                with mock.patch.object(plan, "load_protection_source", return_value=parsed):
                    result = self.report(observed=observed)
                self.assertFalse(result["complete"])
                self.assertEqual(result["summary"]["counts"]["REVIEW_ONLY_PNG"], 0)
                self.assertIn("PROTECTION_SOURCE_UNKNOWN", json.dumps(result))

    def test_guarded_string_attribute_bindings_are_unknown_without_execution(self):
        original = source_text()
        observed = inventory.scan(self.request)
        templates = [
            "\nmatch None:\n    case {name}:\n        pass\n",
            "\nmatch []:\n    case [*{name}]:\n        pass\n",
            "\nmatch {{}}:\n    case {{**{name}}}:\n        pass\n",
            "\ntry:\n    pass\nexcept ValueError as {name}:\n    pass\n",
        ]
        if hasattr(ast, "type_param"):
            templates.append("\ndef ordinary_generic[{name}]():\n    pass\n")
        for name in TABLE_NAMES + ("out_protected", "fnmatch", "any"):
            for template in templates:
                with self.subTest(name=name, template=template):
                    # Only inert source text is constructed and parsed. It is
                    # never imported, evaluated, or executed by this test.
                    text = original + template.format(name=name)
                    parsed = plan.parse_protection_source(text)
                    self.assertEqual(parsed["parse_status"], "UNKNOWN")
                    with mock.patch.object(plan, "load_protection_source", return_value=parsed):
                        result = self.report(observed=observed)
                    self.assertFalse(result["complete"])
                    self.assertFalse(any(item["decision"] == "REVIEW_ONLY_PNG" for item in result["items"]))
                    self.assertIn("PROTECTION_SOURCE_UNKNOWN", json.dumps(result))

    def test_surrogate_declarations_reject_before_scan_in_both_entrypoints(self):
        for codepoint in (0xD800, 0xDFFF):
            for command in ("inventory", "delivery-cleanup-plan"):
                with self.subTest(codepoint=codepoint, command=command):
                    request = copy.deepcopy(self.request)
                    request["project_id"] = "ordinary-label-" + chr(codepoint)
                    path = self.write_request(request)
                    output, diagnostic = io.StringIO(), io.StringIO()
                    with mock.patch.object(cli, "scan", side_effect=AssertionError("invalid declaration started scan")) as scanner:
                        with redirect_stdout(output), redirect_stderr(diagnostic):
                            code = cli.main([command, "--request", str(path)])
                    self.assertEqual(code, 2)
                    scanner.assert_not_called()
                    parsed = json.loads(output.getvalue())
                    self.assertEqual(parsed["status"], "REQUEST_ERROR")
                    self.assertIs(parsed["scan_started"], False)
                    self.assertIs(parsed["execution_eligible"], False)
                    self.assertEqual(parsed["planned_operations"], [])
                    self.assertIn("Unicode scalar", parsed["error"])
                    self.assertTrue(diagnostic.getvalue())
                    output.getvalue().encode("utf-8")

    def test_surrogate_check_covers_nested_optional_declarations_and_keys(self):
        cases = [
            ((), "hold_reasons", ["ordinary reason " + chr(0xD800)]),
            (("code_ref",), "commit", "ordinary label " + chr(0xD800)),
            (("delivery_context", "finish"), "reason", "ordinary reason " + chr(0xD800)),
            (("delivery_context", "notification"), "reference", "ordinary notice " + chr(0xD800)),
            (("reserves", 0), "rationale", "ordinary estimate " + chr(0xD800)),
            ((), "unknown-key-" + chr(0xD800), "ordinary value"),
        ]
        for location, key, value in cases:
            with self.subTest(location=location, key=ascii(key)):
                request = copy.deepcopy(self.request)
                target = request
                for component in location:
                    target = target[component]
                target[key] = value
                with self.assertRaisesRegex(cli.RequestError, "Unicode scalar"):
                    cli.validate_request(request)

    def test_valid_unicode_and_json_surrogate_pair_round_trip(self):
        request = copy.deepcopy(self.request)
        request["project_id"] = "普通中文项目 🎬 café"
        request["delivery_context"]["notification"]["reference"] = "调用者消息:🎬"
        path = self.write_request(request)
        # json.dumps uses a paired escape for the non-BMP character; JSON
        # decoding yields one valid scalar and must remain accepted.
        self.assertIn(r"\ud83c\udfac", path.read_text())
        for command in ("inventory", "delivery-cleanup-plan"):
            with self.subTest(command=command):
                output = io.StringIO()
                with redirect_stdout(output), redirect_stderr(io.StringIO()):
                    code = cli.main([command, "--request", str(path)])
                self.assertEqual(code, 0)
                result = json.loads(output.getvalue())
                label = result["scope"]["project_id"] if command == "inventory" else result["request"]["project_id"]
                self.assertEqual(label, request["project_id"])
                self.assertIs(result["execution_eligible"], False)
                self.assertEqual(result["planned_operations"], [])
                output.getvalue().encode("utf-8")


if __name__ == "__main__":
    unittest.main()
