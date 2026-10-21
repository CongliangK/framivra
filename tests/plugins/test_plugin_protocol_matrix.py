"""Explicit protocol matrix: temporary manifests, no backend execution."""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import vtx
from vtx.pluginlib import compatibility, hooks, manifest, registry
from tests._support import _make_plugin_dir, _clean_import_path

class ProtocolMatrixTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory, self.data = _make_plugin_dir(self.root / "plugins")
        self.data["extends"] = {"hooks": ["audio_events"], "schema_keys": []}
        self.registry_file = self.root / "plugins" / "registry.json"
        self.write()
        self.write_registry(["audio_events"])
        patch = mock.patch.object(registry, "REPO_ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        self.context = types.SimpleNamespace(tl={})

    def write(self):
        (self.directory / "plugin.json").write_text(json.dumps(self.data))

    def write_registry(self, declared):
        self.registry_file.write_text(json.dumps({"plugins": {"demo": {
            "installed": True, "enabled": True, "version": "1.0.0",
            "hooks": declared, "keys": []}}}))

    def external(self):
        self.registry_file.unlink()
        external = self.root / "external" / "demo"
        external.parent.mkdir()
        self.directory.rename(external)
        self.directory = external
        patch = mock.patch.object(registry._config, "load_config", return_value={
            "external_plugins": [{"path": str(external), "enabled": True}]})
        patch.start()
        self.addCleanup(patch.stop)
        return external

    def assert_error(self, action, value):
        before = list(sys.path)
        with mock.patch.object(hooks.importlib, "import_module") as imp:
            with self.assertRaises(RuntimeError) as caught:
                action()
            imp.assert_not_called()
        self.assertEqual(sys.path, before)
        for text in ("demo", repr(value), vtx.__version__, "1.1", "1.2", "升级",
                     "https://github.com/CongliangK/framivra#readme"):
            self.assertIn(text, str(caught.exception))

    def test_exact_supported_protocols_and_legacy(self):
        for protocol, declared in (("1.1", ["audio_events"]),
                                   ("1.2", list(hooks.HOOK_NAMES)),
                                   (None, list(hooks.HOOK_NAMES))):
            with self.subTest(protocol=protocol):
                self.data["requires"] = {}  # Independent legacy core omission.
                if protocol is not None:
                    self.data["requires"]["plugin_protocol"] = protocol
                self.data["extends"]["hooks"] = declared
                self.write()
                self.write_registry(declared)
                module = types.ModuleType("plugins.demo")
                for name in declared:
                    setattr(module, name, mock.Mock(return_value=[]))
                with mock.patch.object(hooks.importlib, "import_module", return_value=module):
                    self.assertEqual(hooks.run_audio_events(self.context, []), [])
                module.audio_events.assert_called_once()

    def test_unknown_and_malformed_remain_discoverable_but_fail_runtime(self):
        for value in ("1.0", "1.3", "2.0", "", " 1.1 ", "v1.2", "1.2.0",
                      ">=1.1", None, True, 1.2, [], {}, ["1.2"]):
            with self.subTest(value=value):
                self.data["requires"]["plugin_protocol"] = value
                self.write()
                self.assertEqual(manifest.load_manifest(self.directory)[1], [])
                self.assertEqual([p["name"] for p in hooks.active_plugins(self.context)], ["demo"])
                self.assert_error(lambda: hooks.run_audio_events(self.context, []), value)

    def test_v11_cannot_declare_v12_hooks(self):
        for hook in ("probe_source", "finish"):
            with self.subTest(hook=hook):
                self.data["requires"]["plugin_protocol"] = "1.1"
                self.data["extends"]["hooks"] = ["audio_events", hook]
                self.write()
                self.assert_error(lambda: hooks.run_audio_events(self.context, []), "1.1")

    def test_registry_cannot_widen_explicit_protocol(self):
        self.data["requires"]["plugin_protocol"] = "1.1"
        self.write()
        self.write_registry(["audio_events", "finish"])
        self.assert_error(lambda: hooks.run_finish(self.context), "1.1")

    def test_core_precedes_protocol_error_deterministically(self):
        self.data["requires"] = {"core": ">=999.0.0", "plugin_protocol": "9.9"}
        self.write()
        with mock.patch.object(hooks.importlib, "import_module") as imp:
            with self.assertRaisesRegex(RuntimeError, "requires.core") as caught:
                hooks.run_audio_events(self.context, [])
            self.assertNotIn("requires.plugin_protocol", str(caught.exception))
            imp.assert_not_called()
        self.data["requires"]["core"] = ">=0.0.0"
        self.write()
        self.assert_error(lambda: hooks.run_audio_events(self.context, []), "9.9")

    def test_every_execution_entrypoint_checks_before_import(self):
        self.data["extends"]["hooks"] = list(hooks.HOOK_NAMES)
        self.data["requires"]["plugin_protocol"] = "9.9"
        self.write()
        self.write_registry(list(hooks.HOOK_NAMES))
        actions = (
            lambda: hooks.run_prepare_source(self.context, "s1"),
            lambda: hooks.resolve_source_wav(self.context, "s1"),
            lambda: hooks.run_audio_events(self.context, []),
            lambda: hooks.run_validate(self.context),
            lambda: hooks.run_probe_source(self.context, "s1"),
            lambda: hooks.run_finish(self.context),
            lambda: hooks.contribute_keys(self.context),
            lambda: hooks.get_cache_dir_keys(self.context),
        )
        for index, action in enumerate(actions):
            with self.subTest(entrypoint=index):
                self.assert_error(action, "9.9")

    def test_external_real_import_side_effects_and_cached_manifest_recheck(self):
        external = self.external()
        imported, called = self.root / "imported", self.root / "called"
        (external / "__init__.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(imported)!r}).touch()\n"
            "def audio_events(c, tracks):\n"
            f"    Path({str(called)!r}).touch()\n"
            "    return tracks\n")
        with _clean_import_path(), mock.patch.dict(sys.modules):
            sys.modules.pop("demo", None)
            for value in ("9.9", None, {}, "1.0"):
                self.data["requires"]["plugin_protocol"] = value
                self.write()
                entries = registry.discover()
                self.assertEqual(len(entries), 1)
                self.assertIsNotNone(entries[0]["manifest"])
                self.assertEqual([p["name"] for p in hooks.active_plugins(self.context)], ["demo"])
                self.assert_error(lambda: hooks.run_audio_events(self.context, []), value)
                self.assertFalse(imported.exists())
                self.assertFalse(called.exists())
                self.assertNotIn("demo", sys.modules)
            self.data["requires"]["plugin_protocol"] = "1.2"
            self.write()
            self.assertEqual(hooks.run_audio_events(self.context, []), [])
            self.assertTrue(imported.exists())
            self.assertTrue(called.exists())
            self.assertIn("demo", sys.modules)
            called.unlink()
            self.data["requires"]["plugin_protocol"] = "9.9"
            self.write()
            self.assert_error(lambda: hooks.run_audio_events(self.context, []), "9.9")
            self.assertFalse(called.exists())

    def test_disabled_and_unreferenced_are_idle(self):
        self.data["requires"]["plugin_protocol"] = "9.9"
        self.write()
        with mock.patch.object(hooks.importlib, "import_module") as imp:
            with mock.patch.object(registry, "plugin_state", return_value="disabled"):
                self.assertEqual(hooks.run_audio_events(self.context, []), [])
            with mock.patch.object(hooks, "_timeline_declares", return_value=False):
                self.assertEqual(hooks.run_audio_events(self.context, []), [])
            imp.assert_not_called()

    def test_builtins_are_unchanged_unversioned_and_loadable(self):
        root = Path(__file__).resolve().parents[2]
        entries = [e for e in registry.scan_builtin(root) if e["kind"] == "plugin"]
        self.assertTrue(entries)
        for entry in entries:
            with self.subTest(plugin=entry["name"]):
                self.assertFalse(entry["manifest_errors"])
                self.assertNotIn("plugin_protocol", entry["manifest"]["requires"])
                compatibility.require_compatible_core(entry["name"], entry["manifest"])
                compatibility.require_compatible_protocol(entry["name"], entry["manifest"])

    def test_missing_requires_object_remains_invalid(self):
        self.data.pop("requires")
        self.write()
        self.assertIsNone(manifest.load_manifest(self.directory)[0])


if __name__ == "__main__":
    unittest.main()
