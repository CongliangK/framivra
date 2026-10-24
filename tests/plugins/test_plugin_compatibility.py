"""Core/protocol compatibility validation and pre-import rejection, without backends."""
import io
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import vtx
from vtx.pluginlib import compatibility, hooks, manifest, registry
from tests._support import _make_plugin_dir, _clean_import_path, temporary_root

class CoreConstraintTests(unittest.TestCase):
    def test_supported_minimums(self):
        for value in (">=0.0.0", ">=2.0.0", " >= 2.0.1 ", ">=999.0.0"):
            with self.subTest(value=value):
                self.assertIsNone(compatibility.core_requirement_error(value))

    def test_malformed_and_unsupported_constraints(self):
        for value in (None, 12, "", " ", "banana", ">=2", ">=2.0", "2.0.0",
                      ">=02.0.0", ">=٢.0.0", ">=2.0.0rc1", ">=2.0.0+build",
                      ">=2.0.0,<3.0.0", "^2.0.0", "==2.0.*", ">=2.0.0\nX"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                directory, _ = _make_plugin_dir(Path(tmp), requires={"core": value})
                data, errors = manifest.load_manifest(directory)
                self.assertIsNone(data)
                self.assertIn("requires.core", " ".join(errors))
                self.assertIn("升级", " ".join(errors))

    def test_numeric_order_and_boundaries(self):
        for current, minimum, accepted in (("2.0.1", "2.0.1", True),
                                          ("2.10.0", "2.9.0", True),
                                          ("2.9.0", "2.10.0", False),
                                          ("10.0.0", "9.0.0", True),
                                          ("2.0.0", "2.0.1", False)):
            with self.subTest(current=current, minimum=minimum), \
                    mock.patch.object(vtx, "__version__", current):
                data = {"requires": {"core": ">=" + minimum}}
                if accepted:
                    compatibility.require_compatible_core("demo", data)
                else:
                    with self.assertRaises(RuntimeError):
                        compatibility.require_compatible_core("demo", data)


class LoaderCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self)
        self.directory, self.data = _make_plugin_dir(self.root / "plugins")
        self.data["extends"]["hooks"] = ["audio_events"]
        self.data["extends"]["schema_keys"] = [{"path": "root", "key": "demo"}]
        self.write()
        (self.root / "plugins" / "registry.json").write_text(json.dumps({"plugins": {
            "demo": {"installed": True, "enabled": True, "version": "1.0.0",
                     "hooks": ["audio_events"], "keys": [{"path": "root", "key": "demo"}]}}}))
        patch = mock.patch.object(registry, "REPO_ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        self.module = types.ModuleType("plugins.demo")
        self.module.audio_events = mock.Mock(side_effect=lambda c, tracks: tracks)
        self.context = types.SimpleNamespace(tl={"demo": True})

    def write(self):
        (self.directory / "plugin.json").write_text(json.dumps(self.data))

    def run_bus(self):
        return hooks.run_audio_events(self.context, [])

    def test_compatible_and_missing_constraint_execute(self):
        for requires in ({"core": ">=2.0.0"}, {}):
            with self.subTest(requires=requires):
                self.data["requires"] = requires
                self.write()
                with mock.patch.object(hooks.importlib, "import_module", return_value=self.module) as imp:
                    self.assertEqual(self.run_bus(), [])
                imp.assert_called_once_with("plugins.demo")
        self.assertEqual(self.module.audio_events.call_count, 2)

    def test_rejected_before_import_and_hook(self):
        for constraint in (">=999.0.0", "bad", "", None):
            with self.subTest(constraint=constraint):
                self.data["requires"] = {"core": constraint}
                self.write()
                with mock.patch.object(hooks.importlib, "import_module") as imp:
                    with self.assertRaises(RuntimeError) as caught:
                        self.run_bus()
                imp.assert_not_called()
                self.module.audio_events.assert_not_called()
                self.assertIn(repr(constraint), str(caught.exception))
                self.assertIn("升级", str(caught.exception))
                self.assertIn(vtx.__version__, str(caught.exception))

    def test_incompatible_message_has_version_range_and_upgrade(self):
        self.data["requires"]["core"] = ">=999.0.0"
        self.write()
        with self.assertRaises(RuntimeError) as caught:
            self.run_bus()
        for text in ("demo", vtx.__version__, ">=999.0.0", "升级", "https://github.com/CongliangK/framivra#readme"):
            self.assertIn(text, str(caught.exception))

    def test_invalid_runtime_version_fails_closed(self):
        for current in ("", "bad", "2.0.1rc1", "2.0", None):
            with self.subTest(current=current), mock.patch.object(vtx, "__version__", current), \
                    mock.patch.object(hooks.importlib, "import_module") as imp:
                with self.assertRaisesRegex(RuntimeError, "无法判定"):
                    self.run_bus()
                imp.assert_not_called()

    def test_disabled_and_unreferenced_remain_idle(self):
        self.data["requires"]["core"] = ">=999.0.0"
        self.write()
        with mock.patch.object(hooks.importlib, "import_module") as imp:
            self.context.tl = {}
            self.assertEqual(self.run_bus(), [])
            self.context.tl = {"demo": True}
            with mock.patch.object(registry, "plugin_state", return_value="disabled"):
                self.assertEqual(self.run_bus(), [])
            imp.assert_not_called()

    def test_unknown_hook_is_rejected_before_import(self):
        self.data["extends"]["hooks"] = ["future_protocol_hook"]
        self.write()
        with mock.patch.object(hooks.importlib, "import_module") as imp:
            with self.assertRaisesRegex(RuntimeError, "future_protocol_hook"):
                self.run_bus()
            imp.assert_not_called()

    def test_unspecified_protocol_field_retains_existing_schema_policy(self):
        self.data["protocol"] = "unknown"
        self.write()
        self.assertEqual(manifest.load_manifest(self.directory)[1], [])

    def test_cached_module_does_not_bypass_gate(self):
        self.data["requires"]["core"] = ">=999.0.0"
        self.write()
        with mock.patch.dict(sys.modules, {"plugins.demo": self.module}), \
                mock.patch.object(hooks.importlib, "import_module") as imp:
            with self.assertRaises(RuntimeError):
                self.run_bus()
            imp.assert_not_called()
        self.module.audio_events.assert_not_called()

    def test_external_plugin_rejected_before_path_or_import_changes(self):
        self.data["requires"]["core"] = ">=999.0.0"
        self.write()
        # Exercise the real external discovery/activation route.
        (self.root / "plugins" / "registry.json").unlink()
        external = self.root / "external" / "demo"
        external.parent.mkdir()
        self.directory.rename(external)
        with mock.patch.object(registry._config, "load_config", return_value={
                "external_plugins": [{"path": str(external), "enabled": True}]}), \
                mock.patch.object(hooks.importlib, "import_module") as imp:
            before = list(sys.path)
            with self.assertRaisesRegex(RuntimeError, "999.0.0"):
                self.run_bus()
            self.assertEqual(sys.path, before)
            imp.assert_not_called()

    def test_real_external_import_and_hook_side_effects(self):
        (self.root / "plugins" / "registry.json").unlink()
        external = self.root / "external" / "demo"
        external.parent.mkdir()
        self.directory.rename(external)
        self.directory = external
        imported, called = self.root / "imported", self.root / "called"
        (external / "__init__.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(imported)!r}).touch()\n"
            "def audio_events(c, tracks):\n"
            f"    Path({str(called)!r}).touch()\n"
            "    return tracks\n")
        with _clean_import_path(), mock.patch.dict(sys.modules), mock.patch.object(
                registry._config, "load_config", return_value={"external_plugins": [
                    {"path": str(external), "enabled": True}]}):
            sys.modules.pop("demo", None)
            self.data["requires"]["core"] = ">=999.0.0"
            self.write()
            with self.assertRaises(RuntimeError):
                self.run_bus()
            self.assertFalse(imported.exists())
            self.assertFalse(called.exists())
            self.assertNotIn("demo", sys.modules)
            self.data["requires"]["core"] = ">=2.0.0"
            self.write()
            self.assertEqual(self.run_bus(), [])
            self.assertTrue(imported.exists())
            self.assertTrue(called.exists())

    def test_missing_import_anchor_is_rejected(self):
        with mock.patch.object(registry, "plugin_module_dir", return_value=(None, None)), \
                mock.patch.object(hooks.importlib, "import_module") as imp:
            with self.assertRaisesRegex(RuntimeError, "manifest"):
                self.run_bus()
            imp.assert_not_called()


class ProtocolMatrixTests(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self)
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



class ExternalResolutionTests(unittest.TestCase):
    """Real config/manifest/import selection; only harmless local marker plugins."""

    NAME = "external-resolution-fixture"

    def setUp(self):
        self.root = temporary_root(self)
        self.config = self.root / "config.local.json"
        self.context = types.SimpleNamespace(tl={"resolution_fixture": True})
        self.markers = []
        for patch in (mock.patch.object(registry, "REPO_ROOT", self.root),
                      mock.patch.dict(os.environ, {"VTX_CONFIG": str(self.config)}),
                      mock.patch.dict(sys.modules)):
            patch.start()
            self.addCleanup(patch.stop)
        clean_path = _clean_import_path()
        clean_path.__enter__()
        self.addCleanup(clean_path.__exit__, None, None, None)
        sys.modules.pop(self.NAME, None)
        sys.modules.pop("plugins." + self.NAME, None)

    def fixture(self, parent, label, version="1.0.0", kind="plugin"):
        directory, _ = _make_plugin_dir(
            self.root / parent, name=self.NAME, version=version, kind=kind,
            entry={"cli": []}, health={"type": "config_key", "key": "fixture"},
            requires={"plugin_protocol": "1.2"},
            extends={"hooks": ["validate"],
                     "schema_keys": [{"path": "root", "key": "resolution_fixture"}]})
        imported, called = directory / "imported", directory / "called"
        self.markers.extend((imported, called))
        (directory / "__init__.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(imported)!r}).write_text({label!r})\n"
            "def validate(c):\n"
            f"    Path({str(called)!r}).write_text({label!r})\n"
            f"    return [{(label + ' executed')!r}]\n", encoding="utf-8")
        return directory

    def configure(self, entries, **extra):
        self.config.write_text(json.dumps(dict(external_plugins=entries, **extra)),
                               encoding="utf-8")

    def untouched(self):
        for marker in self.markers:
            self.assertFalse(marker.exists(), f"unexpected plugin marker: {marker}")
        self.assertNotIn(self.NAME, sys.modules)
        self.assertNotIn("plugins." + self.NAME, sys.modules)

    def test_same_name_distinct_roots_rejected_before_import(self):
        a, b = self.fixture("old", "A"), self.fixture("new", "B", "2.0.0")
        for enabled_a, enabled_b, reverse in ((False, True, False), (False, True, True),
                                               (True, True, False), (True, True, True)):
            with self.subTest(a=enabled_a, b=enabled_b, reverse=reverse):
                entries = [{"path": str(a), "enabled": enabled_a},
                           {"path": str(b), "enabled": enabled_b}]
                self.configure(entries[::-1] if reverse else entries)
                try:
                    with self.assertRaisesRegex(RuntimeError, "身份歧义") as caught:
                        hooks.run_validate(self.context)
                finally:
                    # This fails independently on the old first-wins import path.
                    self.untouched()
                for text in (self.NAME, str(a), str(b)):
                    self.assertIn(text, str(caught.exception))
                for action in (lambda: registry.plugin_state(self.NAME),
                               lambda: registry.plugin_module_dir(self.NAME),
                               lambda: hooks._plugin_hooks_declared(self.NAME),
                               lambda: hooks._plugin_version(self.NAME),
                               lambda: registry.lookup_key("resolution_fixture"),
                               registry.key_map):
                    with self.assertRaisesRegex(RuntimeError, "身份歧义"):
                        action()
                self.assertTrue(all(e["manifest_errors"] for e in registry.discover()))

    def test_single_enabled_identity_supplies_state_hooks_version_and_module(self):
        a, b = self.fixture("old", "A"), self.fixture("new", "B", "2.0.0")
        self.configure([{"path": str(b), "enabled": True}])
        self.assertEqual(registry.plugin_state(self.NAME), "active")
        self.assertEqual(registry.plugin_module_dir(self.NAME), (b.resolve(), "external"))
        self.assertEqual(hooks._plugin_hooks_declared(self.NAME), ["validate"])
        self.assertEqual(hooks._plugin_version(self.NAME), "2.0.0")
        self.assertEqual(registry.lookup_key("resolution_fixture")[0], self.NAME)
        self.assertEqual(registry.key_map()["resolution_fixture"]["plugin"], self.NAME)
        self.assertEqual(hooks.run_validate(self.context), ["B executed"])
        self.assertEqual(Path(sys.modules[self.NAME].__file__).resolve(), (b / "__init__.py").resolve())
        self.assertEqual((b / "imported").read_text(), "B")
        self.assertEqual((b / "called").read_text(), "B")
        self.assertFalse((a / "imported").exists())
        self.assertFalse((a / "called").exists())
        self.configure([{"path": str(a), "enabled": False}])
        self.assertEqual(registry.plugin_state(self.NAME), "disabled")
        self.assertEqual(hooks.run_validate(self.context), [])
        self.assertFalse((a / "imported").exists())
        self.assertFalse((a / "called").exists())

    def test_identical_resolved_root_and_authorization_runs_once(self):
        b = self.fixture("new", "B")
        self.configure([{"path": str(b), "enabled": True},
                        {"path": str(b.relative_to(self.root)), "enabled": True}])
        self.assertEqual(len(registry.discover()), 2)  # diagnostics keep config rows
        self.assertEqual(hooks.run_validate(self.context), ["B executed"])

    def test_same_root_contradictory_authorization_is_ambiguous(self):
        a = self.fixture("old", "A")
        for enabled in ((False, True), (True, False)):
            with self.subTest(enabled=enabled):
                self.configure([{"path": str(a), "enabled": value} for value in enabled])
                try:
                    with self.assertRaisesRegex(RuntimeError, "身份歧义"):
                        hooks.run_validate(self.context)
                finally:
                    self.untouched()

    def test_enabled_invalid_manifest_never_becomes_empty_bus_or_bare_source(self):
        for damage in ("json", "missing", "structure", "directory", "kind"):
            with self.subTest(damage=damage):
                directory = self.fixture(damage, damage, kind="app" if damage == "kind" else "plugin")
                manifest_path = directory / "plugin.json"
                if damage == "json":
                    manifest_path.write_text("{broken", encoding="utf-8")
                elif damage == "missing":
                    manifest_path.unlink()
                elif damage == "structure":
                    manifest_path.write_text("{}", encoding="utf-8")
                elif damage == "directory":
                    directory = directory / "missing-plugin"
                self.configure([{"path": str(directory), "enabled": True}])
                entries = registry.discover()
                self.assertEqual(len(entries), 1)
                self.assertTrue(entries[0]["manifest_errors"])
                for action in (lambda: hooks.run_validate(self.context),
                               lambda: hooks.run_prepare_source(self.context, "s1"),
                               lambda: hooks.run_probe_source(self.context, "s1"),
                               lambda: hooks.run_audio_events(self.context, []),
                               lambda: hooks.run_finish(self.context),
                               lambda: hooks.contribute_keys(self.context),
                               lambda: hooks.get_cache_dir_keys(self.context),
                               registry.key_map,
                               lambda: registry.lookup_key("resolution_fixture")):
                    with self.assertRaisesRegex(RuntimeError, entries[0]["name"]):
                        action()
                    self.untouched()

    def test_disabled_broken_and_ambiguous_entries_remain_diagnosable_and_idle(self):
        import vt
        a, b = self.fixture("old", "A"), self.fixture("new", "B")
        (a / "plugin.json").write_text("{broken", encoding="utf-8")
        self.configure([{"path": str(a), "enabled": False},
                        {"path": str(b), "enabled": False}])
        entries = registry.discover()
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(e["manifest_errors"] and not e["enabled"] for e in entries))
        self.assertEqual(hooks.run_validate(self.context), [])
        self.assertEqual(hooks.run_prepare_source(self.context, "s1"), None)
        self.assertEqual(hooks.run_probe_source(self.context, "s1"), None)
        self.assertEqual(registry.key_map(), {})
        with mock.patch.object(vt, "ROOT", self.root), \
                mock.patch.object(vtx, "__file__", str(self.root / "vtx" / "__init__.py")):
            report = vt._doctor_components(json_out=True, kind_filter="plugin")
            self.assertFalse(report["fail"])
            self.assertTrue(all(item["manifest_errors"] for item in report["entries"]))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(vt.cmd_list("plugins"), 0)
            self.assertIn("disabled", output.getvalue())
        self.untouched()

    def test_app_namespace_cannot_supply_plugin_version_or_hooks(self):
        a = self.fixture("app", "APP", "1.0.0", kind="app")
        b = self.fixture("plugin", "B", "2.0.0")
        self.configure([{"path": str(b), "enabled": True}],
                       external_apps=[{"path": str(a), "enabled": True}])
        self.assertEqual(hooks._plugin_version(self.NAME), "2.0.0")
        self.assertEqual(hooks.run_validate(self.context), ["B executed"])
        self.assertFalse((a / "imported").exists())

    def test_static_builtin_identity_and_disable_take_precedence(self):
        builtin = self.fixture("plugins", "BUILTIN", "3.0.0")
        external = self.fixture("external", "EXTERNAL", "4.0.0")
        (external / "plugin.json").write_text("{broken", encoding="utf-8")
        self.configure([{"path": str(external), "enabled": True}])
        declaration = {"installed": True, "enabled": False, "version": "9.0.0",
                       "hooks": ["validate"], "keys": []}
        registry_file = self.root / "plugins" / "registry.json"
        registry_file.write_text(json.dumps({"plugins": {self.NAME: declaration}}))
        self.assertEqual(registry.plugin_state(self.NAME), "disabled")
        self.assertEqual(hooks.run_validate(self.context), [])
        self.untouched()
        declaration["enabled"] = True
        registry_file.write_text(json.dumps({"plugins": {self.NAME: declaration}}))
        package = types.ModuleType("plugins")
        package.__path__ = [str(self.root / "plugins")]
        sys.modules["plugins"] = package
        self.assertEqual(registry.plugin_module_dir(self.NAME), (builtin, "builtin"))
        self.assertEqual(hooks._plugin_version(self.NAME), "9.0.0")
        self.assertEqual(hooks.run_validate(self.context), ["BUILTIN executed"])
        self.assertFalse((external / "imported").exists())

    def test_missing_official_installation_cannot_use_external_replacement(self):
        external = self.fixture("external", "EXTERNAL")
        self.configure([{"path": str(external), "enabled": True}])
        (self.root / "plugins").mkdir()
        (self.root / "plugins" / "registry.json").write_text(json.dumps({"plugins": {
            self.NAME: {"installed": True, "enabled": True, "version": "9.0.0",
                        "hooks": ["validate"], "keys": []}}}))
        self.assertEqual(registry.plugin_state(self.NAME), "not_installed")
        self.assertEqual(registry.plugin_module_dir(self.NAME), (None, None))
        self.assertEqual(hooks.run_validate(self.context), [])
        self.untouched()

    def test_hidden_or_empty_external_name_is_not_a_builtin_identity(self):
        (self.root / "plugins" / "__pycache__").mkdir(parents=True)
        external, _ = _make_plugin_dir(self.root / "external", name="__pycache__")
        imported = external / "imported"
        self.markers.append(imported)
        (external / "__init__.py").write_text(
            "from pathlib import Path\n" + f"Path({str(imported)!r}).touch()\n")
        self.configure([{"path": str(external), "enabled": True}])
        for action in (lambda: hooks.run_validate(self.context), registry.key_map,
                       lambda: registry.lookup_key("demo")):
            with self.assertRaisesRegex(RuntimeError, "__pycache__"):
                action()
        self.untouched()
        # A filesystem root has an empty basename. Inject its diagnostic record
        # directly, without reading or executing any real host-root component.
        empty_name = dict(name="", kind="plugin", source="external", dir=self.root,
                          enabled=True, manifest=None,
                          manifest_errors=["fixture missing manifest"])
        with self.assertRaisesRegex(RuntimeError, "fixture missing manifest"):
            registry.resolve_plugin("", external={"": empty_name})

    def test_cached_other_external_module_is_rejected_before_hook(self):
        a, b = self.fixture("old", "A"), self.fixture("new", "B", "2.0.0")
        self.configure([{"path": str(a), "enabled": True}])
        self.assertEqual(hooks.run_validate(self.context), ["A executed"])
        (a / "called").unlink()
        self.configure([{"path": str(b), "enabled": True}])
        self.assertEqual(hooks._plugin_version(self.NAME), "2.0.0")
        with self.assertRaisesRegex(RuntimeError, "模块落点异常"):
            hooks.run_validate(self.context)
        self.assertFalse((a / "called").exists())
        self.assertFalse((b / "imported").exists())
        self.assertFalse((b / "called").exists())
        self.assertEqual(Path(sys.modules[self.NAME].__file__).resolve(),
                         (a / "__init__.py").resolve())

    def test_config_reader_unexpected_errors_propagate_unchanged(self):
        error = PermissionError("fixture configuration unreadable")
        with mock.patch.object(registry._config, "load_config", side_effect=error):
            for action in (registry.discover, lambda: hooks.run_validate(self.context)):
                with self.assertRaises(PermissionError) as caught:
                    action()
                self.assertIs(caught.exception, error)
        self.untouched()


if __name__ == "__main__":
    unittest.main()
