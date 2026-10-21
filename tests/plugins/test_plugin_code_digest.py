"""Issue #86: bounded, CPU-only plugin identity regressions using temporary trees."""
import hashlib
import importlib
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vtx.pluginlib import hooks
from tests._support import temporary_root


class PluginCodeDigest(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self) / "plugin"
        self.root.mkdir()
        patch = mock.patch.object(hooks._registry, "plugin_module_dir",
                                  return_value=(self.root, "builtin"))
        self.module_dir = patch.start()
        self.addCleanup(patch.stop)

    def write(self, relative, content=b"VALUE = 1\n"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def digest(self):
        return hooks.plugin_code_digest("example")

    def test_package_move_changes_identity(self):
        source = self.write("first/module.py")
        before = self.digest()
        target = self.root / "second/module.py"
        target.parent.mkdir()
        source.rename(target)
        self.assertNotEqual(before, self.digest())

    def test_same_basename_is_stable_across_traversal_order(self):
        paths = [self.write("z/module.py", b"Z = 1\n"),
                 self.write("a/module.py", b"A = 1\n")]
        with mock.patch.object(Path, "rglob", return_value=iter(paths)):
            first = self.digest()
        with mock.patch.object(Path, "rglob", return_value=iter(reversed(paths))):
            second = self.digest()
        self.assertEqual(first, second)

    def test_plugin_root_location_does_not_change_identity(self):
        self.write("子包/内容.py")
        first = self.digest()
        moved = self.root.with_name("relocated")
        self.root.rename(moved)
        self.root = moved
        self.module_dir.return_value = (moved, "external")
        self.assertEqual(first, self.digest())

    def test_path_and_content_have_separate_boundaries(self):
        source = self.write("a.py", b"b.py")
        first = self.digest()
        source.unlink()
        self.write("a.pyb.py", b"")
        self.assertNotEqual(first, self.digest())

    def test_file_records_have_separate_boundaries(self):
        self.write("a.py", b"b.py")
        second = self.write("b.py", b"")
        first = self.digest()
        second.unlink()
        self.write("a.py", b"b.pyb.py")
        self.assertNotEqual(first, self.digest())

    def test_content_changes_even_when_size_and_mtime_match(self):
        source = self.write("module.py", b"VALUE = 1\n")
        original = source.stat()
        first = self.digest()
        source.write_bytes(b"VALUE = 2\n")
        os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertNotEqual(first, self.digest())

    def test_nested_exclusions_and_non_python_files_do_not_change_identity(self):
        self.write("package/module.py")
        first = self.digest()
        for path in ("tests/test_root.py", "package/tests/test_nested.py",
                     "__pycache__/root.py", "package/__pycache__/nested.py",
                     "README.md", "package/data.json"):
            self.write(path, b"excluded")
        self.assertEqual(first, self.digest())
        self.write("package/testing/module.py")
        self.assertNotEqual(first, self.digest())

    def test_exclusions_are_relative_to_plugin_root(self):
        moved = self.root.parent / "__pycache__" / "plugin"
        moved.parent.mkdir()
        self.root.rename(moved)
        self.root = moved
        self.module_dir.return_value = (moved, "external")
        self.write("module.py")
        first = self.digest()
        self.write("module.py", b"VALUE = 2\n")
        self.assertNotEqual(first, self.digest())

    def test_unreadable_marker_is_distinct_from_literal_file_bytes(self):
        self.write("module.py", b"MISSING")
        readable = self.digest()
        with mock.patch.object(Path, "read_bytes", side_effect=OSError("unreadable")):
            missing = self.digest()
            self.assertEqual(missing, self.digest())
        self.assertNotEqual(readable, missing)

    def test_missing_directory_remains_deterministic_and_distinct(self):
        empty = self.digest()
        self.module_dir.return_value = (None, "builtin")
        missing = self.digest()
        self.assertEqual(missing, self.digest())
        self.module_dir.return_value = (self.root / "absent", "builtin")
        self.assertEqual(missing, self.digest())
        self.assertNotEqual(empty, missing)

    def test_algorithm_version_and_public_shape(self):
        self.write("module.py")
        first = self.digest()
        self.assertRegex(first, r"^[0-9a-f]{12}$")
        self.assertEqual(first, "dc519a2c1b50")  # v2 framed-byte-format fixture
        self.assertNotEqual(first, hashlib.sha1(b"module.pyVALUE = 1\n").hexdigest()[:12])
        with mock.patch.object(hooks, "_PLUGIN_CODE_DIGEST_VERSION", b"future\0"):
            self.assertNotEqual(first, self.digest())
        self.assertFalse(hasattr(hooks, "_plugin_code_digest"))

    def test_addition_and_removal_change_identity(self):
        self.write("module.py")
        first = self.digest()
        added = self.write("extra.py")
        self.assertNotEqual(first, self.digest())
        added.unlink()
        self.assertEqual(first, self.digest())

    def test_package_move_reaches_product_key_and_receipt(self):
        from vtx.api import plugin_code_digest
        from vtx.cache_state import receipt_fingerprint

        denoise = importlib.import_module("plugins.denoise")
        source = self.write("first/module.py")
        timeline = self.root.parent / "timeline.json"
        timeline.write_text("{}", encoding="utf-8")
        context = SimpleNamespace(
            tl={"sources": [{"id": "s1", "path": "a.wav", "denoise": True}]},
            tl_path=timeline, root=self.root.parent, cache=self.root.parent / "cache")
        active = [{"name": "denoise", "version": "unchanged"}]
        with mock.patch.object(hooks, "active_plugins", return_value=active), \
             mock.patch.object(hooks._registry, "key_map", return_value={}), \
             mock.patch.object(denoise, "_audio_key", return_value="unchanged"):
            code = plugin_code_digest("denoise")
            product = denoise.product_path(context, "s1")
            receipt = receipt_fingerprint(context)
            target = self.root / "second/module.py"
            target.parent.mkdir()
            source.rename(target)
            self.assertNotEqual(code, plugin_code_digest("denoise"))
            self.assertNotEqual(product, denoise.product_path(context, "s1"))
            self.assertNotEqual(receipt, receipt_fingerprint(context))

    def test_no_active_plugins_preserves_receipt_fingerprint(self):
        from vtx.cache_state import receipt_fingerprint

        timeline = self.root.parent / "timeline.json"
        timeline.write_bytes(b"{}")
        context = SimpleNamespace(tl={}, tl_path=timeline, root=self.root.parent)
        with mock.patch.object(hooks, "active_plugins", return_value=[]):
            self.assertEqual(hooks.active_plugins_digest(context), "")
            self.assertEqual(receipt_fingerprint(context), hashlib.sha256(b"{}").hexdigest())


if __name__ == "__main__":
    unittest.main()
