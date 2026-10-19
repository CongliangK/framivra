"""Explicit ordinary-fixture tests; never run frozen path-safety negatives."""
import builtins
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from .inventory import scan


def fixture():
    destination = os.environ.get("DISK_LIFECYCLE_TEST_ROOT")
    if not destination or not Path(destination).is_dir():
        raise RuntimeError("set DISK_LIFECYCLE_TEST_ROOT to an approved new ordinary-fixture directory")
    return Path(tempfile.mkdtemp(prefix="inventory-", dir=destination))


def request(root, role="gfx"):
    return {"schema": "framivra.disk-lifecycle-request/1", "project_id": "synthetic-project", "run_id": "synthetic-run",
            "roots": [{"id": "data", "path": str(root), "role": role}], "retain_paths": [], "hold_reasons": []}


def snapshot(root):
    rows = []
    for path in sorted([root] + list(root.rglob("*"))):
        metadata = path.stat()
        rows.append((str(path.relative_to(root)), metadata.st_dev, metadata.st_ino, metadata.st_size,
                     metadata.st_mtime_ns, metadata.st_ctime_ns,
                     hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None))
    return rows


def synthetic_stat(value, **changes):
    """Copy only stat fields used by inventory; never create filesystem links."""
    names = ("st_mode", "st_dev", "st_ino", "st_nlink", "st_size", "st_blocks", "st_mtime_ns", "st_ctime_ns")
    values = {name: getattr(value, name) for name in names if hasattr(value, name)}
    values.update(changes)
    return SimpleNamespace(**values)


def without_observation_times(value):
    volatile = {"started_at", "ended_at", "duration", "observed_at", "rechecked_at",
                "free_bytes_observed_at", "volume_free_observed_at"}
    if isinstance(value, dict):
        return {key: without_observation_times(item) for key, item in value.items() if key not in volatile}
    if isinstance(value, list):
        return [without_observation_times(item) for item in value]
    return value


class InventoryTests(unittest.TestCase):
    def test_empty_and_ordinary_files(self):
        root = fixture()
        empty = scan(request(root))
        self.assertTrue(empty["complete"])
        self.assertEqual(empty["totals"]["files"], 0)
        self.assertEqual(empty["totals"]["logical_path_bytes"], 0)
        (root / "nested").mkdir()
        (root / "plain.dat").write_bytes(b"abc")
        (root / "nested" / "frame.png").write_bytes(b"12345")
        result = scan(request(root))
        self.assertTrue(result["complete"])
        self.assertEqual(result["totals"]["files"], 2)
        self.assertEqual(result["totals"]["directories"], 2)
        self.assertEqual(result["totals"]["logical_path_bytes"], 8)
        self.assertEqual(result["totals"]["logical_unique_inode_bytes"], 8)
        expected = sum(path.stat().st_blocks * 512 for path in (root / "plain.dat", root / "nested" / "frame.png"))
        self.assertEqual(result["totals"]["allocated_unique_inode_bytes"], expected)
        self.assertIsNone(result["estimated_reclaimable_physical_bytes"])

    def test_overlap_preserves_roles_without_duplicate_entries(self):
        root = fixture()
        child = root / "nested"
        child.mkdir()
        (child / "frame.png").write_bytes(b"abc")
        value = request(root)
        value["roots"] += [{"id": "repeat", "path": str(root), "role": "input"},
                           {"id": "child", "path": str(child), "role": "evidence"}]
        result = scan(value)
        self.assertTrue(result["complete"])
        self.assertEqual(result["totals"]["files"], 1)
        self.assertEqual(result["totals"]["directories"], 2)
        file = next(e for e in result["entries"] if e["entry_type"] == "FILE")
        self.assertEqual(set(file["roles"]), {"gfx", "input", "evidence"})
        self.assertEqual(len(file["objects"]), 3)
        self.assertEqual(len(result["volumes"]), 1)

    def test_limits_preserve_known_subtotals(self):
        root = fixture()
        for index in range(5):
            (root / (str(index) + ".dat")).write_bytes(b"abcd")
        value = request(root)
        value["max_entries"] = 2
        result = scan(value)
        self.assertFalse(result["complete"])
        self.assertLessEqual(len(result["entries"]), 2)
        self.assertIsNone(result["totals"]["logical_path_bytes"])
        self.assertIsNotNone(result["totals"]["partial_known_subtotal"])
        self.assertIsNone(result["totals"]["omitted_count"])
        value["max_entries"] = 100
        value["max_scan_seconds"] = 1e-12
        timed = scan(value)
        self.assertFalse(timed["complete"])
        self.assertIsNone(timed["totals"]["logical_unique_inode_bytes"])

    def test_missing_root_is_unknown(self):
        root = fixture() / "not-created"
        result = scan(request(root))
        self.assertFalse(result["complete"])
        self.assertTrue(result["errors"])
        self.assertIsNone(result["totals"]["logical_path_bytes"])

    def test_free_read_failure_is_unknown(self):
        root = fixture()
        with mock.patch("os.statvfs", side_effect=OSError("synthetic unavailable free counter")):
            result = scan(request(root))
        self.assertFalse(result["complete"])
        self.assertIsNone(result["volumes"][0]["volume_free_bytes"])

    def test_synthetic_hardlink_metadata_deduplicates_and_protects_group(self):
        outer = fixture()
        gfx, inputs = outer / "gfx", outer / "input"
        gfx.mkdir()
        inputs.mkdir()
        frame, source = gfx / "frame.png", inputs / "source.dat"
        frame.write_bytes(b"abc")
        source.write_bytes(b"abc")
        baseline = frame.stat()
        real_stat = os.stat
        value = request(gfx)
        value["roots"].append({"id": "input", "path": str(inputs), "role": "input"})
        for declared_links, expected_external in ((2, "NONE_OBSERVED"), (3, "POSSIBLE")):
            with self.subTest(declared_links=declared_links):
                def ordinary_stat(path, *args, **kwargs):
                    actual = real_stat(path, *args, **kwargs)
                    if os.fspath(path) in {"frame.png", "source.dat"}:
                        return synthetic_stat(baseline, st_nlink=declared_links)
                    return actual

                with mock.patch("experiments.disk_lifecycle.inventory.os.stat", side_effect=ordinary_stat):
                    result = scan(value)
                self.assertTrue(result["complete"])
                self.assertEqual(result["totals"]["logical_path_bytes"], 6)
                self.assertEqual(result["totals"]["logical_unique_inode_bytes"], 3)
                self.assertEqual(result["totals"]["allocated_unique_inode_bytes"], baseline.st_blocks * 512)
                self.assertEqual(result["totals"]["unique_inodes"], 1)
                files = [entry for entry in result["entries"] if entry["entry_type"] == "FILE"]
                self.assertEqual(len({entry["inode_group"] for entry in files}), 1)
                for entry in files:
                    self.assertTrue(entry["group_protected"])
                    self.assertEqual(set(entry["group_roles"]), {"gfx", "input"})
                    self.assertEqual(entry["visible_link_names"], 2)
                    self.assertEqual(entry["st_nlink"], declared_links)
                    self.assertEqual(entry["external_links"], expected_external)
                self.assertIsNone(result["estimated_reclaimable_physical_bytes"])

    def test_missing_blocks_retains_known_logical_subtotal(self):
        root = fixture()
        (root / "plain.dat").write_bytes(b"abc")
        real_stat = os.stat

        def ordinary_stat(path, *args, **kwargs):
            actual = real_stat(path, *args, **kwargs)
            if os.fspath(path) == "plain.dat":
                copied = synthetic_stat(actual)
                del copied.st_blocks
                return copied
            return actual

        with mock.patch("experiments.disk_lifecycle.inventory.os.stat", side_effect=ordinary_stat):
            result = scan(request(root))
        entry = next(entry for entry in result["entries"] if entry["entry_type"] == "FILE")
        self.assertFalse(result["complete"])
        self.assertEqual(entry["status"], "UNKNOWN")
        self.assertEqual(entry["logical_bytes"], 3)
        self.assertIsNone(entry["allocated_bytes"])
        self.assertIn("blocks", entry["unknown_fields"])
        self.assertIsNone(result["totals"]["allocated_unique_inode_bytes"])
        self.assertIsNone(result["totals"]["logical_unique_inode_bytes"])
        self.assertEqual(result["totals"]["partial_known_subtotal"]["logical_unique_inode_bytes"], 3)
        self.assertEqual(result["totals"]["unknown_count"], 1)

    def test_synthetic_ordinary_growth_is_changed_and_not_a_stable_total(self):
        root = fixture()
        (root / "growing.dat").write_bytes(b"abc")
        real_stat = os.stat
        calls = 0

        def ordinary_stat(path, *args, **kwargs):
            nonlocal calls
            actual = real_stat(path, *args, **kwargs)
            if os.fspath(path) == "growing.dat":
                calls += 1
                return synthetic_stat(actual, st_size=actual.st_size + (10 if calls > 1 else 0))
            return actual

        with mock.patch("experiments.disk_lifecycle.inventory.os.stat", side_effect=ordinary_stat):
            result = scan(request(root))
        entry = next(entry for entry in result["entries"] if entry["entry_type"] == "FILE")
        self.assertEqual(calls, 2)
        self.assertEqual(entry["status"], "CHANGED_DURING_SCAN")
        self.assertFalse(entry["metadata_stable"])
        self.assertIsNone(entry["logical_bytes"])
        self.assertIsNone(entry["allocated_bytes"])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["totals"]["logical_path_bytes"])
        self.assertEqual(result["totals"]["partial_known_subtotal"]["logical_path_bytes"], 0)

    def test_synthetic_ordinary_disappearance_is_unknown(self):
        root = fixture()
        (root / "ordinary.dat").write_bytes(b"abc")
        real_stat = os.stat
        calls = 0

        def ordinary_stat(path, *args, **kwargs):
            nonlocal calls
            if os.fspath(path) == "ordinary.dat":
                calls += 1
                if calls == 2:
                    raise FileNotFoundError("synthetic ordinary name disappeared")
            return real_stat(path, *args, **kwargs)

        with mock.patch("experiments.disk_lifecycle.inventory.os.stat", side_effect=ordinary_stat):
            result = scan(request(root))
        entry = next(entry for entry in result["entries"] if entry["entry_type"] == "FILE")
        self.assertEqual(entry["status"], "UNKNOWN")
        self.assertIn("ENTRY_METADATA_RECHECK_FAILED", entry["reasons"])
        self.assertIsNone(entry["logical_bytes"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["errors"])
        self.assertEqual((root / "ordinary.dat").read_bytes(), b"abc")

    def test_synthetic_stat_oserror_preserves_other_known_subtotal(self):
        root = fixture()
        (root / "unreadable.dat").write_bytes(b"abc")
        (root / "known.dat").write_bytes(b"12345")
        real_stat = os.stat

        def ordinary_stat(path, *args, **kwargs):
            if os.fspath(path) == "unreadable.dat":
                raise PermissionError("synthetic metadata unavailable")
            return real_stat(path, *args, **kwargs)

        with mock.patch("experiments.disk_lifecycle.inventory.os.stat", side_effect=ordinary_stat):
            result = scan(request(root))
        self.assertFalse(result["complete"])
        self.assertIsNone(result["totals"]["logical_path_bytes"])
        self.assertEqual(result["totals"]["partial_known_subtotal"]["logical_path_bytes"], 5)
        self.assertTrue(any(error["reason"] == "ENTRY_STAT_FAILED" for error in result["errors"]))

    def test_repeat_structure_is_stable_except_observation_times(self):
        root = fixture()
        (root / "nested").mkdir()
        (root / "a.dat").write_bytes(b"abc")
        (root / "nested" / "b.dat").write_bytes(b"12345")
        # Available space is an independent instantaneous reading, so hold that
        # ordinary numeric input constant when testing deterministic structure.
        fixed_free = SimpleNamespace(f_bavail=100_000, f_frsize=4096)
        with mock.patch("experiments.disk_lifecycle.inventory.os.statvfs", return_value=fixed_free):
            first = scan(request(root))
            second = scan(request(root))
        self.assertTrue(first["complete"])
        self.assertTrue(second["complete"])
        self.assertEqual(without_observation_times(first), without_observation_times(second))

    def test_readonly_both_entrypoints_and_forbidden_side_effect_sentinels(self):
        outer = fixture()
        root = outer / "observed"
        root.mkdir()
        (root / "frame.png").write_bytes(b"synthetic PNG-labelled bytes")
        path = outer / "request.json"
        path.write_text(json.dumps(request(root)), encoding="utf-8")
        before = snapshot(root)
        real_open, real_io_open, real_os_open, real_import = builtins.open, io.open, os.open, builtins.__import__

        def guarded_open(file, mode="r", *args, **kwargs):
            if any(flag in mode for flag in "wax+"):
                raise AssertionError("file write attempted")
            return real_open(file, mode, *args, **kwargs)

        def guarded_io_open(file, mode="r", *args, **kwargs):
            if any(flag in mode for flag in "wax+"):
                raise AssertionError("io file write attempted")
            return real_io_open(file, mode, *args, **kwargs)

        def guarded_os_open(file, flags, *args, **kwargs):
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                raise AssertionError("os file write attempted")
            return real_os_open(file, flags, *args, **kwargs)

        def guarded_import(name, *args, **kwargs):
            if name == "vtx" or name.startswith("vtx."):
                raise AssertionError("production module import attempted")
            return real_import(name, *args, **kwargs)

        for command in ("inventory", "delivery-cleanup-plan"):
            stdout, stderr = io.StringIO(), io.StringIO()
            with ExitStack() as stack:
                stack.enter_context(mock.patch("builtins.open", side_effect=guarded_open))
                stack.enter_context(mock.patch("io.open", side_effect=guarded_io_open))
                stack.enter_context(mock.patch("os.open", side_effect=guarded_os_open))
                stack.enter_context(mock.patch("builtins.__import__", side_effect=guarded_import))
                for operation in ("mkdir", "makedirs", "remove", "unlink", "rmdir", "rename", "replace", "system", "kill"):
                    stack.enter_context(mock.patch.object(os, operation, side_effect=AssertionError("side effect attempted: " + operation)))
                stack.enter_context(mock.patch.object(subprocess, "Popen", side_effect=AssertionError("subprocess attempted")))
                stack.enter_context(mock.patch.object(socket, "socket", side_effect=AssertionError("network attempted")))
                stack.enter_context(mock.patch.object(sys, "argv", ["disk_lifecycle", command, "--request", str(path)]))
                stack.enter_context(redirect_stdout(stdout))
                stack.enter_context(redirect_stderr(stderr))
                with self.assertRaises(SystemExit) as exited:
                    runpy.run_module("experiments.disk_lifecycle", run_name="__main__")
                self.assertIn(exited.exception.code, (0, 3))
            result = json.loads(stdout.getvalue())
            self.assertFalse(result["execution_eligible"])
            self.assertEqual(result["planned_operations"], [])
            self.assertEqual(snapshot(root), before)
        self.assertFalse(any(name == "vtx" or name.startswith("vtx.") for name in sys.modules))
        self.assertFalse(list(root.rglob("__pycache__")))


if __name__ == "__main__":
    unittest.main()
