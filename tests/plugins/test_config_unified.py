#!/usr/bin/env python3
"""tests/plugins/test_config_unified.py —— v2 §5.3 config 统一加载（vtx/pluginlib/config.py）。

覆盖：
  ① 路径优先级：VTX_CONFIG 环境变量 > repo 根 config.local.json（测试用临时
     repo_root 与 patch.dict 环境隔离，绝不读写真实 repo 配置）；
  ② 坏 JSON：警告（stderr）+ 空 {} 继续（全局化语义，不阻断）；缺文件静默 {}；
  ③ ~ 展开：已知路径型键（tts.repo 等 PATH_KEYS）自动展开；非路径键
     （asr.base_url 等）与清单外字符串值不动；expand_path 工具；
  ④ IMGEN_CONFIG 不生效：默认路径与 VTX_CONFIG 均不读取旧变量；
  ⑤ external_apps[]/external_plugins[] 条目规范化（字符串默认 disabled，
     {path, enabled:true} 显式启用）——registry.scan_external 的输入形状。
运行：bash scripts/run_all_tests.sh。
"""
import ast
import io
import json
import os
import sys
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from vtx.pluginlib import config as pc


class PathPriority(unittest.TestCase):
    def test_env_var_overrides_repo_local(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "config.local.json").write_text('{"a": 1}', encoding="utf-8")
            env_file = root / "override.json"
            env_file.write_text('{"b": 2}', encoding="utf-8")
            with mock.patch.dict(os.environ, {"VTX_CONFIG": str(env_file)}):
                self.assertEqual(pc.load_config(root), {"b": 2})
                self.assertEqual(pc.config_path(root), env_file)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("VTX_CONFIG", None)
                self.assertEqual(pc.load_config(root), {"a": 1})   # 缺省走 repo 根

    def test_missing_file_silent_empty(self):
        with tempfile.TemporaryDirectory() as d:
            err = io.StringIO()
            with redirect_stderr(err), mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("VTX_CONFIG", None)
                cfg = pc.load_config(Path(d))
            self.assertEqual(cfg, {})
            self.assertEqual(err.getvalue(), "")      # 缺文件是常态，不警告


class BadJsonWarns(unittest.TestCase):
    def test_bad_json_warns_and_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "config.local.json").write_text('{oops', encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err), mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("VTX_CONFIG", None)
                cfg = pc.load_config(root)
            self.assertEqual(cfg, {})
            self.assertIn("解析失败", err.getvalue())  # 警告不阻断（全局化语义）

    def test_non_dict_top_level_warns_and_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "config.local.json").write_text('[1, 2]', encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err), mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("VTX_CONFIG", None)
                cfg = pc.load_config(root)
            self.assertEqual(cfg, {})
            self.assertIn("顶层", err.getvalue())


class HomeExpansion(unittest.TestCase):
    def _cfg_file(self, root):
        (root / "config.local.json").write_text(json.dumps({
            "tts": {"repo": "~/AI/IndexTTS/repo",
                    "edge_bin": "~/miniconda3/bin/edge-tts",
                    "engine": "indextts"},          # 清单内节的非路径字符串键：不动
            "asr": {"base_url": "http://127.0.0.1:18085",   # 非路径键：URL 里的 ~ 语义过宽
                    "python": "~/.venv/bin/python"},
            "imgen": {"api_base": "https://api.example.com/~user/"}  # 完全不动
        }), encoding="utf-8")

    def test_path_keys_expanded_others_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._cfg_file(root)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("VTX_CONFIG", None)
                cfg = pc.load_config(root)
            self.assertEqual(cfg["tts"]["repo"], os.path.expanduser("~/AI/IndexTTS/repo"))
            self.assertEqual(cfg["tts"]["edge_bin"], os.path.expanduser("~/miniconda3/bin/edge-tts"))
            self.assertEqual(cfg["tts"]["engine"], "indextts")           # 键不在清单
            self.assertEqual(cfg["asr"]["base_url"], "http://127.0.0.1:18085")
            self.assertEqual(cfg["asr"]["python"], os.path.expanduser("~/.venv/bin/python"))
            self.assertEqual(cfg["imgen"]["api_base"], "https://api.example.com/~user/")

    def test_expand_path_utility(self):
        self.assertEqual(pc.expand_path("~/x"), os.path.expanduser("~/x"))
        self.assertEqual(pc.expand_path(123), 123)          # 非字符串原样
        self.assertEqual(pc.expand_path("/abs"), "/abs")    # 绝对路径幂等


class LegacyImgenvConfig(unittest.TestCase):
    """拒用旧变量回归；保留既有 CI 选择的 case ID，不保留旧配置行为。"""
    def test_imgen_config_deprecation_notice(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            legacy = root / "legacy.json"
            legacy.write_text('{"imgen": {"enabled": true}}', encoding="utf-8")
            (root / "config.local.json").write_text(
                '{"imgen": {"enabled": false}}', encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err), mock.patch.dict(
                    os.environ, {"IMGEN_CONFIG": str(legacy)}):
                os.environ.pop("VTX_CONFIG", None)
                cfg = pc.load_config(root)
            self.assertEqual(cfg, {"imgen": {"enabled": False}})
            self.assertEqual(err.getvalue(), "")
            # VTX_CONFIG 同时设置时以 VTX_CONFIG 为准（不读 legacy、不打公告）
            v2 = root / "v2.json"
            v2.write_text('{"x": 1}', encoding="utf-8")
            err2 = io.StringIO()
            with redirect_stderr(err2), mock.patch.dict(
                    os.environ, {"IMGEN_CONFIG": str(legacy), "VTX_CONFIG": str(v2)}):
                self.assertEqual(pc.load_config(root), {"x": 1})
            self.assertEqual(err2.getvalue(), "")

    def test_no_legacy_env_no_notice(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            err = io.StringIO()
            with redirect_stderr(err), mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("IMGEN_CONFIG", None)
                os.environ.pop("VTX_CONFIG", None)
                pc.load_config(root)
            self.assertEqual(err.getvalue(), "")
        probe = r'''import json, os, sys, tempfile
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path.cwd() / "apps" / "imgen"))
import imgen_cli
from vtx.pluginlib import config
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "config.local.json").write_text('{"source":"default"}')
    legacy = root / "legacy.json"
    legacy.write_text('{"source":"legacy"}')
    selected = root / "selected.json"
    selected.write_text('{"source":"selected"}')
    os.environ["IMGEN_CONFIG"] = str(legacy)
    os.environ.pop("VTX_CONFIG", None)
    with mock.patch.object(config, "REPO_ROOT", root), mock.patch.object(
            imgen_cli, "cmd_postprocess", return_value=0) as command:
        for override, expected in [(None, "default"), (str(selected), "selected")]:
            if override:
                os.environ["VTX_CONFIG"] = override
            assert imgen_cli.main(["postprocess", "unused.png", "--kind", "bg4k", "-o", "unused-out.png"]) == 0
            assert command.call_args.args[1] == {"source": expected}
        assert not hasattr(imgen_cli, "config_path")
        assert not hasattr(imgen_cli, "load_config")
'''
        result = subprocess.run([sys.executable, "-c", probe], cwd=REPO,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


class ExternalEntries(unittest.TestCase):
    def test_normalization(self):
        cfg = {"external_apps": ["/opt/myapp", {"path": "~/apps/x", "enabled": True}],
               "external_plugins": [42, {"no_path": 1}]}
        apps = pc.external_entries(cfg, "external_apps")
        self.assertEqual(apps[0], {"path": "/opt/myapp", "enabled": False})  # 默认 disabled
        self.assertEqual(apps[1], {"path": "~/apps/x", "enabled": True})
        plugins = pc.external_entries(cfg, "external_plugins")
        self.assertTrue(plugins[0]["malformed"])
        self.assertTrue(plugins[1]["malformed"])
        # Explicit JSON true is the code-trust boundary, not truthiness or == 1.
        for key in ("external_apps", "external_plugins"):
            for value in (True, False, None, 0, 1, -1, 0.0, 1.0, "true", "false",
                          "0", "1", "", [], [1], {}, {"enabled": True}):
                with self.subTest(key=key, enabled=value):
                    original = {key: [{"path": "/never-imported", "enabled": value}]}
                    before = json.dumps(original, sort_keys=True)
                    self.assertEqual(pc.external_entries(original, key),
                                     [{"path": "/never-imported", "enabled": value is True}])
                    self.assertEqual(json.dumps(original, sort_keys=True), before)
            self.assertEqual(pc.external_entries({key: [{"path": "/never-imported"}]}, key),
                             [{"path": "/never-imported", "enabled": False}])

    def test_missing_key_returns_empty(self):
        self.assertEqual(pc.external_entries({}, "external_apps"), [])
        self.assertEqual(pc.external_entries(None, "external_plugins"), [])


class CfgGet(unittest.TestCase):
    def test_tolerance_semantics(self):
        cfg = {"asr": {"base_url": "http://x", "python": None, "port": 99},
               "flag_on": "true", "flag_off": "false"}
        self.assertEqual(pc.cfg_get(cfg, "asr.base_url", "d"), "http://x")
        self.assertEqual(pc.cfg_get(cfg, "asr.python", "d"), "d")           # None → default
        self.assertEqual(pc.cfg_get(cfg, "asr.missing", "d"), "d")
        self.assertIs(pc.cfg_get(cfg, "flag_on", False), True)              # 布尔容错
        self.assertIs(pc.cfg_get(cfg, "flag_off", True), False)
        self.assertEqual(pc.cfg_get(cfg, "asr.port", "/default/py"), "/default/py")  # str 默认实得非 str（P2-3）→ default


class ImgenConfigSemantics(unittest.TestCase):
    """只执行真实源码的配置表达式与纯确认门；不 import/运行 API 或 key 链。"""

    @classmethod
    def setUpClass(cls):
        path = REPO / "apps" / "imgen" / "imgen_cli.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        cls.functions = {node.name: node for node in tree.body
                         if isinstance(node, ast.FunctionDef)}
        cls.namespace = {"__file__": str(path), "Path": Path, "sys": sys,
                         "os": os, "Optional": Optional,
                         "models": SimpleNamespace(BASE_URL="https://example.invalid")}
        selected = [node for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "BATCH_WARN_THRESHOLD"
                            for target in node.targets)]
        selected += [cls.functions[name] for name in (
            "_pluginlib_config", "resolve_api_base", "confirm_paid_submit")]
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"),
             cls.namespace)

    def config_assignments(self, command, cfg, yes=False):
        # 刻意排除函数中的 import/API/key/文件写入，只取这四个真实赋值。
        assignments = [node for node in ast.walk(self.functions[command])
                       if isinstance(node, ast.Assign)
                       and len(node.targets) == 1
                       and isinstance(node.targets[0], ast.Name)
                       and node.targets[0].id in (
                           "model_default", "price", "confirm_enabled", "gate_yes")]
        self.assertEqual(len(assignments), 4)
        # cmd_gen 的 price 来自纯模型常量；与本测试配置语义无关，不执行。
        assignments = [node for node in assignments
                       if command == "cmd_batch" or node.targets[0].id != "price"]
        namespace = {**self.namespace, "cfg": cfg, "args": SimpleNamespace(yes=yes)}
        exec(compile(ast.Module(body=assignments, type_ignores=[]),
                     "<imgen-config-expressions>", "exec"), namespace)
        return namespace

    def test_shared_helper_is_canonical_not_a_local_copy(self):
        self.assertNotIn("cfg_get", self.functions)
        self.assertIs(self.namespace["_pluginlib_config"]().cfg_get, pc.cfg_get)

    def test_confirmation_defaults_and_explicit_values(self):
        cases = [("missing", {}, True), ("missing-key", {"imgen": {}}, True)]
        cases += [("root", value, True) for value in (None, [], [1], "bad", False, 9)]
        cases += [("section", {"imgen": value}, True)
                  for value in (None, [], [1], "bad", False, 9)]
        cases += [("value", {"imgen": {"confirm_before_submit": value}}, expected)
                  for value, expected in (
                      (None, True), (False, False), (True, True), (0, False), (1, True),
                      ("false", False), ("0", False), ("NO", False), ("off", False),
                      ("true", True), ("1", True), (" Yes ", True), ("ON", True),
                      ("", False), ("garbage", False))]
        for command in ("cmd_gen", "cmd_batch"):
            for kind, cfg, enabled in cases:
                for yes in (False, True):
                    with self.subTest(command=command, kind=kind, cfg=cfg, yes=yes):
                        values = self.config_assignments(command, cfg, yes)
                        self.assertIs(values["confirm_enabled"], enabled)
                        self.assertIs(values["gate_yes"], yes or not enabled)
                        allowed = self.namespace["confirm_paid_submit"](
                            1, 0.1, values["gate_yes"], False, stderr=io.StringIO())
                        self.assertIs(allowed, yes or not enabled)
                        # >50 张二次确认不因 config 或 --yes 失效。
                        self.assertFalse(self.namespace["confirm_paid_submit"](
                            51, 0.1, values["gate_yes"], False, stderr=io.StringIO()))

    def test_other_imgen_defaults_preserve_existing_types(self):
        cases = [({}, "nano", 0.1, "https://example.invalid"),
                 ({"model_default": None, "price_per_image": None, "api_base": None},
                  "nano", 0.1, "https://example.invalid"),
                 ({"model_default": True, "price_per_image": True, "api_base": 9},
                  "nano", 0.1, "https://example.invalid"),
                 ({"model_default": "gpt-image", "price_per_image": 0.3,
                   "api_base": "https://configured.invalid/"},
                  "gpt-image", 0.3, "https://configured.invalid")]
        with mock.patch.dict(os.environ, {}, clear=True):
            for imgen, model, price, api_base in cases:
                cfg = {"imgen": imgen}
                with self.subTest(cfg=cfg):
                    self.assertEqual(self.config_assignments("cmd_gen", cfg)["model_default"], model)
                    values = self.config_assignments("cmd_batch", cfg)
                    self.assertEqual(values["model_default"], model)
                    self.assertEqual(values["price"], price)
                    self.assertEqual(self.namespace["resolve_api_base"](cfg), api_base)
            os.environ["IMGEN_API_BASE"] = "https://override.invalid/"
            self.assertEqual(self.namespace["resolve_api_base"]({}), "https://override.invalid")


if __name__ == "__main__":
    unittest.main()
