"""Manifest validation must reject traversal and root-external symlink paths."""
import json

try:
    from ._support import A03_FIXED, SandboxCase
except ImportError:
    from _support import A03_FIXED, SandboxCase

from vtx.pluginlib import manifest


class PluginPathSafety(SandboxCase):
    def setUp(self):
        super().setUp()
        self.plugin = self.root / "plugins/demo"
        for name in ("cli.py", "health.py", "README.md"):
            self.put(self.plugin / name, b"# harmless test fixture\n")
        self.data = {
            "name": "demo", "version": "1.0.0", "kind": "plugin",
            "entry": {"cli": ["cli.py"]},
            "health": {"type": "command", "command": "health.py --offline"},
            "requires": {"core": ">=2.0.0", "binaries": [], "python_packages": [], "platforms": []},
            "config_keys": [], "docs": "README.md",
            "extends": {"schema_keys": [], "hooks": []},
        }
        self.save_manifest()

    def save_manifest(self):
        self.put(self.plugin / "plugin.json", json.dumps(self.data).encode())

    def replace_path(self, field, value):
        if field == "entry.cli":
            self.data["entry"]["cli"] = [value]
        elif field == "health.command":
            self.data["health"]["command"] = value
        else:
            self.data["docs"] = value
        self.save_manifest()

    def assert_rejected(self, field=None):
        before = self.tree(self.sandbox)
        data, errors = manifest.load_manifest(self.plugin)
        self.assertIsNone(data, "root-external path must not be accepted")
        self.assertTrue(errors, "unsafe manifest needs an explicit validation error")
        if field:
            self.assertIn(field, "\n".join(errors))
        self.assertEqual(self.tree(self.sandbox), before, "validation must be read-only")
        self.assert_outside_unchanged()

    def test_valid_in_root_manifest_is_read_only_and_repeatable(self):
        before = self.tree(self.sandbox)
        for _ in range(2):
            data, errors = manifest.load_manifest(self.plugin)
            self.assertEqual(errors, [])
            self.assertEqual(data, self.data)
        self.assertEqual(self.tree(self.sandbox), before)

    def test_dotdot_rejected_for_every_declared_path(self):
        for field in ("entry.cli", "health.command", "docs"):
            with self.subTest(field=field):
                self.replace_path(field, "../escape.py")
                self.assert_rejected(field)
                self.replace_path(field, {"entry.cli": "cli.py", "health.command": "health.py", "docs": "README.md"}[field])

    def test_absolute_paths_rejected_for_every_declared_path(self):
        for field in ("entry.cli", "health.command", "docs"):
            with self.subTest(field=field):
                self.replace_path(field, str(self.sentinel))
                self.assert_rejected(field)
                self.replace_path(field, {"entry.cli": "cli.py", "health.command": "health.py", "docs": "README.md"}[field])

    def test_malformed_manifest_is_a_read_only_error(self):
        self.put(self.plugin / "plugin.json", b"{broken")
        self.assert_rejected()

    @A03_FIXED
    def test_entry_cli_symlink_outside_plugin_is_rejected(self):
        (self.plugin / "escape.py").symlink_to(self.sentinel)
        self.replace_path("entry.cli", "escape.py")
        self.assert_rejected("entry.cli")

    @A03_FIXED
    def test_health_command_symlink_outside_plugin_is_rejected(self):
        (self.plugin / "escape.py").symlink_to(self.sentinel)
        self.replace_path("health.command", "escape.py --offline")
        self.assert_rejected("health.command")

    @A03_FIXED
    def test_docs_symlink_outside_plugin_is_rejected(self):
        (self.plugin / "escape.md").symlink_to(self.sentinel)
        self.replace_path("docs", "escape.md")
        self.assert_rejected("docs")

    @A03_FIXED
    def test_symlinked_parent_directory_outside_plugin_is_rejected(self):
        (self.plugin / "linked").symlink_to(self.outside, target_is_directory=True)
        self.replace_path("entry.cli", "linked/keep.txt")
        self.assert_rejected("entry.cli")

    @A03_FIXED
    def test_root_external_manifest_symlink_is_rejected(self):
        external = self.put(self.outside / "plugin.json", json.dumps(self.data).encode())
        target = self.checked(self.plugin / "plugin.json")
        target.unlink()
        target.symlink_to(external)
        self.assert_rejected()

    @A03_FIXED
    def test_in_root_manifest_symlink_still_accepted(self):
        # 正对照：manifest 符号链接指向插件根内文件（resolve 仍在根内）必须放行
        # ——修复不得扩大到拒绝一切 manifest 符号链接。
        self.put(self.plugin / "manifest.real.json", json.dumps(self.data).encode())
        target = self.checked(self.plugin / "plugin.json")
        target.unlink()
        target.symlink_to("manifest.real.json")
        data, errors = manifest.load_manifest(self.plugin)
        self.assertEqual(errors, [])
        self.assertEqual(data, self.data)

    @A03_FIXED
    def test_builtin_scan_rejects_plugin_root_symlinked_outside_scan_root(self):
        # 插件根本身是指向扫描根外的目录符号链接：scan_builtin 不得把它注册为
        # 内置件（受信扫描根收口；外部目录只能走显式 external 注册通道）。
        from vtx.pluginlib import registry
        for folder in ("apps", "plugins"):
            with self.subTest(folder=folder):
                name = f"{folder}-evil"   # 各扫描根独立命名，防跨 subTest 误并入
                external = self.outside / name
                for fname in ("cli.py", "health.py", "README.md"):
                    self.put(external / fname, b"# external fixture\n")
                data = dict(self.data, name=name)
                self.put(external / "plugin.json", json.dumps(data).encode())
                (self.root / folder).mkdir(parents=True, exist_ok=True)
                (self.root / folder / name).symlink_to(
                    external, target_is_directory=True)
                evil = [e for e in registry.scan_builtin(self.root)
                        if e["name"] == name]
                self.assertEqual(len(evil), 1)
                self.assertIsNone(evil[0]["manifest"],
                                  "扫描根外目录不得以内置件身份通过")
                self.assertTrue(any("扫描根" in e for e in evil[0]["manifest_errors"]),
                                evil[0]["manifest_errors"])
                # 同批合法内置件不受牵连
                demo = [e for e in registry.scan_builtin(self.root)
                        if e["name"] == "demo"]
                self.assertEqual(demo[0]["manifest_errors"], [])
