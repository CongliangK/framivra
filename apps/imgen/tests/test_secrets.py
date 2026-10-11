#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_secrets.py · key 解析链优先级单测（方案 §9.3）。

优先级：--key（值或文件） > SC_API_KEY > NANOBANANA2_API_KEY > keyring
> ~/.config/vtx-imgen/secrets.json。env 注入断言；全缺 → 提交前失败。
测试隔离：XDG_CONFIG_HOME 指向临时目录（屏蔽真实 ~/.config 目录）、
keyring 以 monkeypatch 模拟（不触网）。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from . import _loader   # v2 P2a：tests 成包后相对导入

keychain = _loader.load("secrets")


class KeyChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="imgen_key_test_")
        self.addCleanup(self.tmp.cleanup)
        # 隔离真实 ~/.config（XDG 指到临时目录）
        self._xdg = os.environ.pop("XDG_CONFIG_HOME", None)
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        self.addCleanup(self._restore_xdg)
        # 清空 env 级 key
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("SC_API_KEY", None)
        os.environ.pop("NANOBANANA2_API_KEY", None)
        self.addCleanup(self._env.stop)
        # keyring 默认未命中
        patcher = mock.patch.object(keychain, "_try_keyring_get", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore_xdg(self):
        if self._xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._xdg

    def _write_secret_file(self, key):
        cfg = Path(self.tmp.name) / keychain.CONFIG_DIR_NAME
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "secrets.json").write_text(json.dumps({"api_key": key}), encoding="utf-8")

    def test_cli_key_value_beats_all(self):
        os.environ["SC_API_KEY"] = "sk-env-1111"
        os.environ["NANOBANANA2_API_KEY"] = "nb-env-2222"
        with mock.patch.object(keychain, "_try_keyring_get", return_value="kr-3333"):
            self._write_secret_file("sf-4444")
            self.assertEqual(keychain.resolve_api_key("K-cli-9999"), "K-cli-9999")

    def test_cli_key_from_file(self):
        key_file = Path(self.tmp.name) / "key.txt"
        key_file.write_text("  sk-file-abcd  \n", encoding="utf-8")
        self.assertEqual(keychain.resolve_api_key(str(key_file)), "sk-file-abcd")

    def test_cli_key_missing_file_raises(self):
        with self.assertRaises(ValueError):
            keychain.resolve_api_key(str(Path(self.tmp.name) / "nope.txt"))

    def test_sc_env_beats_nanobanana_and_stored(self):
        os.environ["SC_API_KEY"] = "sk-env-1111"
        os.environ["NANOBANANA2_API_KEY"] = "nb-env-2222"
        self._write_secret_file("sf-4444")
        self.assertEqual(keychain.resolve_api_key(None), "sk-env-1111")

    def test_nanobanana_env_beats_stored(self):
        os.environ["NANOBANANA2_API_KEY"] = "nb-env-2222"
        self._write_secret_file("sf-4444")
        self.assertEqual(keychain.resolve_api_key(None), "nb-env-2222")

    def test_keyring_beats_secret_file(self):
        self._write_secret_file("sf-4444")
        with mock.patch.object(keychain, "_try_keyring_get", return_value="kr-3333"):
            self.assertEqual(keychain.resolve_api_key(None), "kr-3333")

    def test_secret_file_last(self):
        self._write_secret_file("sf-4444")
        self.assertEqual(keychain.resolve_api_key(None), "sf-4444")

    def test_all_missing_raises_actionable_error(self):
        with self.assertRaises(ValueError) as ctx:
            keychain.resolve_api_key(None)
        message = str(ctx.exception)
        self.assertIn("SC_API_KEY", message)
        self.assertIn("--key", message)

    def test_mask_key_tail4(self):
        self.assertEqual(keychain.mask_key("sk-abcdef1234"), "****1234")
        self.assertEqual(keychain.mask_key("ab"), "****")

    def test_describe_key_chain_reports_levels(self):
        os.environ["SC_API_KEY"] = "sk-env-1111"
        levels, chosen = keychain.describe_key_chain(None)
        names = [name for name, _value in levels]
        self.assertTrue(any("SC_API_KEY" in n for n in names))
        self.assertEqual(chosen[0], "SC_API_KEY")
        self.assertEqual(chosen[1], "sk-env-1111")

    def test_resolve_empty_string_treated_as_missing(self):
        # 空串 --key 视为未提供，落到 env/存储链（上游 strip 语义）
        os.environ["SC_API_KEY"] = "sk-env-1111"
        self.assertEqual(keychain.resolve_api_key("  "), "sk-env-1111")


if __name__ == "__main__":
    unittest.main()
