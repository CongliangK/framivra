"""Global cache policy contract; no rendering, host configuration, or network."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vtx.cache.policy import CachePolicy, policy_for, read_policy


class CachePolicyTests(unittest.TestCase):
    def test_default_core_and_explicit_modes(self):
        self.assertEqual(read_policy({}).mode, 'core')
        self.assertEqual(read_policy({'cache': {}}).mode, 'core')
        for mode in ('off', 'core', 'aggressive'):
            with self.subTest(mode=mode):
                policy = read_policy({'cache': {'mode': mode}})
                self.assertEqual(policy.reuse_graphics, mode != 'off')
                self.assertEqual(policy.compress_graphics, mode == 'core')
                self.assertEqual(policy.reuse_media, mode == 'aggressive')
                self.assertEqual(policy.pass_retention, 'all' if mode == 'aggressive' else 'none')

    def test_invalid_explicit_values_fail_instead_of_changing_mode(self):
        for value in (None, True, 0, [], {}, '', 'CORE', 'unknown'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'cache.mode'):
                read_policy({'cache': {'mode': value}})
        for value in (None, True, 0, [], 'off'):
            with self.subTest(cache=value), self.assertRaisesRegex(ValueError, 'cache must'):
                read_policy({'cache': value})
        with self.assertRaises(ValueError):
            CachePolicy('unknown')

    def test_global_file_is_shared_but_an_operation_is_frozen(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({'cache': {'mode': 'off'}, 'bgm': {'keep': 'unchanged'}}))
            with patch.dict(os.environ, {'VTX_CONFIG': str(path)}):
                first, second = SimpleNamespace(), SimpleNamespace()
                self.assertEqual(policy_for(first).mode, 'off')
                path.write_text(json.dumps({'cache': {'mode': 'aggressive'}}))
                self.assertEqual(policy_for(first).mode, 'off')
                self.assertEqual(policy_for(second).mode, 'aggressive')

    def test_no_timeline_override_or_configuration_write(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            raw = b'{"cache":{"mode":"core"},"bgm":{"repo":"keep-me"}}\n'
            path.write_bytes(raw)
            with patch.dict(os.environ, {'VTX_CONFIG': str(path)}):
                context = SimpleNamespace(tl={'cache': {'mode': 'aggressive'}})
                self.assertEqual(policy_for(context).mode, 'core')
            self.assertEqual(path.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
