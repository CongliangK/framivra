"""Optional producer identity must fail closed without changing legacy providers."""
import hashlib
import json
import traceback
import types
import unittest
from unittest import mock

from vtx.pluginlib import hooks


class InputIdentityContract(unittest.TestCase):
    def setUp(self):
        self.ctx = types.SimpleNamespace(tl={})
        for patch in (mock.patch.object(hooks._registry, "key_map", return_value={}),
                      mock.patch.object(hooks, "plugin_code_digest", return_value="code")):
            patch.start()
            self.addCleanup(patch.stop)

    def digest(self, module):
        with mock.patch.object(hooks, "_load_plugin_module", return_value=module):
            return hooks._plugin_params_digest(self.ctx, "example")

    def test_old_provider_retains_exact_digest(self):
        blob = json.dumps({"params": {}, "code": "code"}, ensure_ascii=False, sort_keys=True)
        expected = hashlib.sha1(blob.encode()).hexdigest()[:12]
        self.assertEqual(self.digest(types.SimpleNamespace()), expected)
        self.assertNotEqual(self.digest(types.SimpleNamespace(input_identity=lambda c: None)), expected)

    def test_identity_recomputed_and_canonical(self):
        state = {"a": 1, "b": 2}
        provider = types.SimpleNamespace(input_identity=lambda c: state)
        before = self.digest(provider)
        state = {"b": 2, "a": 1}
        self.assertEqual(self.digest(provider), before)
        state["a"] = 3
        self.assertNotEqual(self.digest(provider), before)

    def test_invalid_contributions_fail_closed(self):
        for value in (float("nan"), float("inf"), {1, 2}, object()):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(RuntimeError, "插件 example: input_identity"):
                    self.digest(types.SimpleNamespace(input_identity=lambda c: value))
        with self.assertRaises(RuntimeError):
            self.digest(types.SimpleNamespace(input_identity=42))

    def test_provider_error_does_not_echo_secret_or_exception_chain(self):
        def broken(ctx):
            raise ValueError("secret-credential-must-not-escape")
        try:
            self.digest(types.SimpleNamespace(input_identity=broken))
        except RuntimeError:
            report = traceback.format_exc()
        else:
            self.fail("provider errors must not be swallowed")
        self.assertIn("插件 example", report)
        self.assertNotIn("secret-credential-must-not-escape", report)

    def test_empty_active_set_does_not_load_provider(self):
        with mock.patch.object(hooks, "active_plugins", return_value=[]), \
             mock.patch.object(hooks, "_load_plugin_module", side_effect=AssertionError("loaded")):
            self.assertEqual(hooks.active_plugins_digest(self.ctx), "")


if __name__ == "__main__":
    unittest.main()
