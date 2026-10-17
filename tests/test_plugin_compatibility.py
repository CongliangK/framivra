"""Explicit core constraint validation and pre-import rejection, without backends."""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import vtx
from vtx.pluginlib import compatibility, hooks, manifest, registry
from tests.test_pluginlib import _make_plugin_dir

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'


class _clean_import_path:
    """import 卫生：用例内还原洁净 sys.path——先序测试（如 test_cef_worker_pixel_ops
    顶层）可能把仓内 scripts/ 常驻注入 sys.path，令同名顶层模块 demo（scripts/demo.py，
    #177 唯一入口）顶替本用例的 external 插件；hooks 落点断言按设计拒绝，属测试
    环境问题而非被测行为，故在用例内还原。"""

    def __enter__(self):
        self.saved = sys.path[:]
        sys.path[:] = [p for p in sys.path
                       if Path(p or '.').resolve() != _SCRIPTS_DIR.resolve()]
        return self

    def __exit__(self, *exc):
        sys.path[:] = self.saved
        return None


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
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
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
        with mock.patch.object(registry, "_external_cfg", return_value={
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
                registry, "_external_cfg", return_value={"external_plugins": [
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


if __name__ == "__main__":
    unittest.main()
