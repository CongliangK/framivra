"""Issue #86: bounded, CPU-only plugin identity regressions using temporary trees."""
import hashlib
import importlib
import os
import subprocess
import sys
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
        original = Path.iterdir
        with mock.patch.object(Path, "iterdir", lambda p: iter(sorted(original(p)))):
            first = self.digest()
        with mock.patch.object(Path, "iterdir", lambda p: iter(sorted(original(p), reverse=True))):
            second = self.digest()
        self.assertEqual(first, second)
        # Global logical ordering must match the existing v2 format, including
        # aliases that would otherwise interleave differently during a DFS.
        shared = self.root.parent / 'shared'
        shared.mkdir()
        (shared / 'module.py').write_bytes(b'SHARED = 1\n')
        for name in ('a-link', 'z-link'):
            (self.root / name).symlink_to(shared, target_is_directory=True)
        files = paths + [self.root / name / 'module.py' for name in ('a-link', 'z-link')]
        expected = hashlib.sha1(hooks._PLUGIN_CODE_DIGEST_VERSION + b'DIR\0')
        for path in sorted(files, key=lambda p: p.relative_to(self.root).as_posix()):
            relative = path.relative_to(self.root).as_posix().encode('utf-8')
            data = path.read_bytes()
            expected.update(len(relative).to_bytes(8, 'big') + relative)
            expected.update(b'CONTENT\0' + len(data).to_bytes(8, 'big') + data)
        self.assertEqual(self.digest(), expected.hexdigest()[:12])
        with mock.patch.object(Path, "iterdir", lambda p: iter(sorted(original(p), reverse=True))):
            self.assertEqual(self.digest(), expected.hexdigest()[:12])

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
        # Fresh imports follow linked subpackages; their bytes need the same
        # content sensitivity, without making the physical target path a key.
        shared = self.root.parent / 'shared'
        shared.mkdir()
        source = shared / 'module.py'
        source.write_bytes(b'VALUE = 1\n')
        linked = self.root / 'linked'
        linked.symlink_to(shared, target_is_directory=True)
        first = self.digest()
        self.assertEqual(first, self.digest())
        original = source.stat()
        source.write_bytes(b'VALUE = 2\n')
        os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
        second = self.digest()
        self.assertNotEqual(first, second)
        equivalent = self.root.parent / 'equivalent'
        equivalent.mkdir()
        (equivalent / 'module.py').write_bytes(source.read_bytes())
        linked.unlink()
        linked.symlink_to(equivalent, target_is_directory=True)
        self.assertEqual(second, self.digest())

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
        shared = self.root.parent / 'shared'
        shared.mkdir()
        (shared / 'module.py').write_bytes(b'SHARED = 1\n')
        for name in ('tests', '__pycache__'):
            (self.root / 'package/testing' / name).symlink_to(shared, target_is_directory=True)
        first = self.digest()
        (shared / 'module.py').write_bytes(b'SHARED = 2\n')
        self.assertEqual(first, self.digest())

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
        directory = self.root / 'subpackage'
        directory.mkdir()
        (directory / 'module.py').write_bytes(b'VALUE = 1\n')
        readable = self.digest()
        original = Path.iterdir
        def denied(path):
            if path == directory:
                raise PermissionError('unreadable subpackage')
            return original(path)
        with mock.patch.object(Path, 'iterdir', denied):
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
        # Preserve rglob's less-common no-link matches too: .py itself and a
        # directory named *.py (historically a MISSING record plus its files).
        self.write('.py')
        self.write('package.py/module.py')
        expected = hashlib.sha1(hooks._PLUGIN_CODE_DIGEST_VERSION + b'DIR\0')
        for path in sorted(self.root.rglob('*.py'),
                           key=lambda p: p.relative_to(self.root).as_posix()):
            relative = path.relative_to(self.root).as_posix().encode('utf-8')
            expected.update(len(relative).to_bytes(8, 'big') + relative)
            try:
                data = path.read_bytes()
            except OSError:
                expected.update(b'MISSING\0')
            else:
                expected.update(b'CONTENT\0' + len(data).to_bytes(8, 'big') + data)
        self.assertEqual(self.digest(), expected.hexdigest()[:12])

    def test_addition_and_removal_change_identity(self):
        self.write("module.py")
        first = self.digest()
        added = self.write("extra.py")
        self.assertNotEqual(first, self.digest())
        added.unlink()
        self.assertEqual(first, self.digest())
        # Cycles must be finite without globally deduplicating ordinary aliases.
        # Isolated children give a regressed traversal a bounded test timeout.
        script = ('from pathlib import Path; from unittest import mock; import sys; '
                  'from vtx.pluginlib import hooks; '
                  'root=Path(sys.argv[1]); '
                  'patch=mock.patch.object(hooks._registry,"plugin_module_dir",'
                  'return_value=(root,"external")); patch.start(); '
                  'print(hooks.plugin_code_digest("example"))')
        def child_digest():
            result = subprocess.run([sys.executable, '-B', '-c', script, str(self.root)],
                                    cwd=Path(__file__).resolve().parents[2],
                                    capture_output=True, text=True, timeout=5,
                                    start_new_session=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        with self.subTest(cycle='self'):
            link = self.root / 'loop'
            link.symlink_to(self.root, target_is_directory=True)
            cycle = child_digest()
            self.assertNotEqual(first, cycle)
            self.assertEqual(cycle, child_digest())
            link.unlink()
            self.assertEqual(first, self.digest())
        with self.subTest(cycle='cross-linked'):
            left = self.root / 'left'
            right = self.root / 'right'
            left.mkdir()
            right.mkdir()
            (left / 'other').symlink_to(right, target_is_directory=True)
            (right / 'other').symlink_to(left, target_is_directory=True)
            cycle = child_digest()
            self.assertEqual(cycle, child_digest())
            (right / 'module.py').write_bytes(b'VALUE = 3\n')
            self.assertNotEqual(cycle, child_digest())
            # Changing a cycle's logical target must not collapse its identity.
            previous = child_digest()
            (left / 'other').unlink()
            (left / 'other').symlink_to(left, target_is_directory=True)
            self.assertNotEqual(previous, child_digest())

    def test_package_move_reaches_product_key_and_receipt(self):
        from vtx.api import plugin_code_digest
        from vtx.cache.cache_state import receipt_fingerprint

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
            shared = self.root.parent / 'shared'
            shared.mkdir()
            linked_source = shared / 'module.py'
            linked_source.write_bytes(b'VALUE = 1\n')
            (self.root / 'linked').symlink_to(shared, target_is_directory=True)
            code = plugin_code_digest('denoise')
            product = denoise.product_path(context, 's1')
            receipt = receipt_fingerprint(context)
            linked_source.write_bytes(b'VALUE = 2\n')
            self.assertNotEqual(code, plugin_code_digest('denoise'))
            self.assertNotEqual(product, denoise.product_path(context, 's1'))
            self.assertNotEqual(receipt, receipt_fingerprint(context))

    def test_no_active_plugins_preserves_receipt_fingerprint(self):
        from vtx.cache.cache_state import receipt_fingerprint

        timeline = self.root.parent / "timeline.json"
        timeline.write_bytes(b"{}")
        context = SimpleNamespace(tl={}, tl_path=timeline, root=self.root.parent)
        with mock.patch.object(hooks, "active_plugins", return_value=[]):
            self.assertEqual(hooks.active_plugins_digest(context), "")
            current = receipt_fingerprint(context)
            self.assertEqual(receipt_fingerprint(context), current)
            self.assertNotEqual(current, hashlib.sha256(b"{}").hexdigest())


if __name__ == "__main__":
    unittest.main()
