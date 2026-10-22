"""C03 verifier regressions; fresh venv layout fixtures, no build downloads.

The real build/wheel/sdist installation proof is tests/runners/package_smoke.py.
These adversarial fixtures exercise missing metadata/resources and import leaks,
not a substitute for running that command against actual build artifacts.
"""
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
import venv

import sys

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.runners import package_smoke

REPO = Path(__file__).resolve().parents[2]


class PackageVerifierTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="package-verifier-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.environment = self.root / "venv"
        venv.EnvBuilder(with_pip=False).create(self.environment)
        self.python, self.binary_dir = package_smoke.venv_paths(self.environment)
        probe = subprocess.run([str(self.python), "-I", "-c",
                                "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                               capture_output=True, text=True, check=True)
        self.site = Path(probe.stdout.strip())
        shutil.copytree(REPO / "vtx", self.site / "vtx",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(REPO / "vt.py", self.site / "vt.py")
        self.version = (REPO / "VERSION").read_text().strip()
        self.metadata = self.site / f"framivra-{self.version}.dist-info"
        self.metadata.mkdir()
        self.metadata_file = self.metadata / "METADATA"
        self.metadata_file.write_text(f"Metadata-Version: 2.1\nName: framivra\nVersion: {self.version}\n")
        shutil.copy2(REPO / "plugins/registry.json",
                     self.site / "vtx/pluginlib/builtin_registry.json")
        self.hashes = {name: hashlib.sha256((REPO / path).read_bytes()).hexdigest()
                       for name, path in package_smoke.RESOURCES.items()}
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        (self.home / "empty-config.json").write_text("{}")
        self.env = package_smoke.clean_env(self.home, self.binary_dir)

    def verify(self, expected=None, forbidden=None):
        code = ("import json\n" + inspect.getsource(package_smoke.check_installed)
                + "\nprint(json.dumps(check_installed(" + repr(expected or self.version)
                + ", " + repr(self.hashes) + ", " + repr(forbidden or [str(REPO)]) + ")))\n")
        return subprocess.run([str(self.python), "-I", "-c", code], cwd=self.outside,
                              env=self.env, capture_output=True, text=True, timeout=60)

    def test_fresh_venv_resources_metadata_and_paths(self):
        proc = self.verify()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["versions"]["metadata"], self.version)
        self.assertEqual(data["resources"], self.hashes)
        self.assertTrue(all(state == "not_installed" for state in data["plugin_states"].values()))
        # Copied installed-source layout, not an actual built/installed wheel.
        material = b'{}audio-implementation\0' + b''.join(
            f"{name}:{hashlib.sha256((REPO / name).read_bytes()).hexdigest()}\n".encode()
            for name in ('vtx/pipeline/stages.py', 'vtx/media/audio_filters.py'))
        code = ("from pathlib import Path; from types import SimpleNamespace\n"
                "from unittest.mock import patch; from vtx.cache import cache_state\n"
                "c=SimpleNamespace(tl={},root=Path.cwd())\n"
                "with patch('vtx.render.gfx_backend.identity', return_value=''), "
                "patch('vtx.cache.hygiene.gfx_cache_ns', return_value=''):\n"
                " print(cache_state.receipt_fingerprint(c,timeline_bytes=b'{}'))")
        proc = subprocess.run([str(self.python), "-I", "-c", code], cwd=self.outside,
                              env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), hashlib.sha256(material).hexdigest())
        self.assertFalse((self.site / '.git').exists())
        for name in ('vtx/pipeline/stages.py', 'vtx/media/audio_filters.py'):
            source = self.site / name
            content = source.read_bytes()
            source.unlink()
            try:
                proc = subprocess.run([str(self.python), "-I", "-c", code], cwd=self.outside,
                                      env=self.env, capture_output=True, text=True, timeout=60)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('FileNotFoundError', proc.stderr)
                self.assertNotIn('ImportError', proc.stderr)
                self.assertIn(Path(name).name, proc.stderr)
            finally:
                source.write_bytes(content)

    def test_wrong_expected_version_fails(self):
        proc = self.verify(expected="0.0.1")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("installed version mismatch", proc.stderr)

    def test_wrong_metadata_version_fails(self):
        self.metadata_file.write_text("Metadata-Version: 2.1\nName: framivra\nVersion: 0.0.1\n")
        proc = self.verify()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("installed version mismatch", proc.stderr)

    def test_missing_metadata_does_not_pass_runtime_literal_fallback(self):
        self.metadata_file.unlink()
        proc = self.verify()
        self.assertNotEqual(proc.returncode, 0)

    def test_missing_preview_fails(self):
        (self.site / "vtx/preview.html").unlink()
        proc = self.verify()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("preview.html", proc.stderr)

    def test_corrupted_schema_fails(self):
        (self.site / "vtx/timeline.schema.json").write_text("{}")
        proc = self.verify()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("installed resource mismatch", proc.stderr)

    def test_missing_plugin_declarations_fail(self):
        (self.site / "vtx/pluginlib/builtin_registry.json").unlink()
        proc = self.verify()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("builtin_registry.json", proc.stderr)

    def test_source_path_in_environment_is_not_used(self):
        self.env["PYTHONPATH"] = str(REPO)
        proc = self.verify()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(Path(json.loads(proc.stdout)["imports"]["vtx"]).is_relative_to(self.environment))

    def test_explicit_other_registry_root_does_not_borrow_packaged_declarations(self):
        code = ("from vtx.pluginlib import registry; from pathlib import Path; "
                f"assert registry.load_static_registry(Path({str(self.outside)!r})) == {{'plugins': {{}}}}; "
                "assert registry.load_static_registry()['plugins']")
        proc = subprocess.run([str(self.python), "-I", "-c", code], cwd=self.outside,
                              env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_installed_init_does_not_crash_on_unbundled_apps(self):
        proc = subprocess.run([str(self.python), "-I", "-c",
                               "import vt,sys; sys.argv=['vt','init','--json']; sys.exit(vt.main())"],
                              cwd=self.outside, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertNotIn("Traceback", proc.stderr)
        data = json.loads(proc.stdout[proc.stdout.index('{'):])
        self.assertFalse(data["bridge_json2blend.py"]["ok"])
        self.assertFalse((self.site / "config.local.json").exists())


class SdistSafetyTest(unittest.TestCase):
    def test_archive_traversal_or_links_rejected_before_extraction(self):
        for name, link in (("../escape", False), ("/escape", False), ("pkg/link", True)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = root / "source.tar.gz"
                with tarfile.open(archive, "w:gz") as tar:
                    member = tarfile.TarInfo(name)
                    if link:
                        member.type = tarfile.SYMTYPE
                        member.linkname = "../../escape"
                        tar.addfile(member)
                    else:
                        member.size = 1
                        tar.addfile(member, io.BytesIO(b"x"))
                target = root / "unpacked"
                target.mkdir()
                with self.assertRaises(RuntimeError):
                    package_smoke.unpack_sdist(archive, target)
                self.assertEqual(list(target.iterdir()), [])

    def test_staged_source_excludes_git_tests_apps_and_workflow_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "source"
            package_smoke.stage_source(REPO, target)
            self.assertFalse((target / ".git").exists())
            self.assertFalse((target / "apps").exists())
            self.assertFalse((target / "tests").exists())
            self.assertFalse((target / "templates").exists())
            self.assertFalse((target / "docs" / "skill").exists())
            self.assertEqual((target / "plugins/registry.json").read_bytes(),
                             (REPO / "plugins/registry.json").read_bytes())
            from types import SimpleNamespace
            from vtx.cache import cache_state
            c = SimpleNamespace(root=Path(tmp), tl={})
            expected = cache_state.receipt_fingerprint(c, timeline_bytes=b'{}')
            # The relocated no-Git source is read through the same package-relative
            # lookup; importing from this fixture is not a wheel installation.
            with mock.patch.object(cache_state, '__file__', str(target / 'vtx/cache/cache_state.py')):
                self.assertEqual(cache_state.receipt_fingerprint(c, timeline_bytes=b'{}'), expected)
                for relative in ('vtx/pipeline/stages.py', 'vtx/media/audio_filters.py'):
                    source = target / relative
                    original = source.read_bytes()
                    source.write_bytes(original + b'\n# changed implementation fixture\n')
                    self.assertNotEqual(cache_state.receipt_fingerprint(c, timeline_bytes=b'{}'), expected)
                    source.unlink()
                    with self.assertRaises(FileNotFoundError):
                        cache_state.receipt_fingerprint(c, timeline_bytes=b'{}')
                    source.mkdir()
                    with self.assertRaisesRegex(ValueError, 'not a regular file'):
                        cache_state.receipt_fingerprint(c, timeline_bytes=b'{}')
                    source.rmdir()
                    source.write_bytes(original)
                    self.assertEqual(cache_state.receipt_fingerprint(c, timeline_bytes=b'{}'), expected)


if __name__ == "__main__":
    unittest.main()
