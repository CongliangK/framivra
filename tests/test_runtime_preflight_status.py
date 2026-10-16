"""Mock-only failure diagnostics; no browser or media process launches."""
import json
from pathlib import Path
import tempfile
import traceback
import zipfile
import unittest
from unittest.mock import Mock, patch
from scripts import bench_lifecycle as bench
from tests.test_gfx_experiment import raw_observation
from vtx import gfx_render, gfx_runtime


class RuntimePreflightStatus(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.parent = Path(temp.name)
        self.root = bench.new_root(self.parent)
        self.browser = Mock(port=1234)

    def invoke(self, raw):
        with patch.object(gfx_render, 'Browser', return_value=self.browser), \
             patch.object(gfx_runtime, 'collect', return_value=raw), \
             patch.object(bench, 'host_manifest', return_value={'unchanged': True}):
            return bench.preflight('/mock/browser', [], diagnostic_root=self.root)

    def pack_failure(self, parent, log, *, diagnostic):
        from scripts import pack_lifecycle_evidence as packer
        (parent / 'source.json').write_text('{}')
        (parent / 'runner-result.json').write_text('{"return_code": 1}')
        (parent / 'runner.log').write_text(log)
        output = parent / ('bundle-' + str(len(list(parent.glob('bundle-*')))))
        status = packer.pack(parent, output)
        self.assertFalse(status['evidence_complete'])
        self.assertFalse(status['partial_acceptance'])
        with zipfile.ZipFile(output / 'evidence.zip') as archive:
            self.assertEqual(any(n.endswith('/runtime-preflight-status.json') for n in archive.namelist()), diagnostic)
            for name in archive.namelist():
                self.assertNotIn(b'SECRET_WRITE_FAILURE', archive.read(name))

    def test_failure_saved_before_strict_identity_rejection(self):
        raw = raw_observation('preflight')
        raw['actual_executable'] = None
        raw['errors']['actual_executable'] = 'process_identity_unavailable'
        with self.assertRaisesRegex(ValueError, 'incomplete runtime observation'):
            self.invoke(raw)
        status = json.loads((self.root / 'runtime-preflight-status.json').read_text())
        self.assertEqual(status['fields']['actual_executable'], 'missing')
        self.assertEqual(status['errors']['actual_executable'], 'process_identity_unavailable')
        self.browser.stop.assert_called_once()
        self.assertFalse((self.root / 'preflight.json').exists())
        self.pack_failure(self.parent, 'original identity rejection', diagnostic=True)

    def test_runner_failure_retains_status_without_acceptance(self):
        raw = raw_observation('preflight')
        raw['errors']['system_info'] = 'probe_failed'
        fixture = bench.fixture
        def fake_fixture(root):
            timeline = fixture(root, create_media=False)
            (root / 'media.mp4').write_bytes(b'mock input; never decoded')
            return timeline
        with patch.object(gfx_render, 'Browser', return_value=self.browser), \
             patch.object(gfx_runtime, 'collect', return_value=raw), \
             patch.object(bench, 'fixture', side_effect=fake_fixture), \
             patch.object(bench, 'run_arm') as arm:
            with self.assertRaisesRegex(ValueError, 'incomplete runtime observation'):
                bench.run_experiment(self.parent, '/mock/browser', [])
        arm.assert_not_called()
        roots = [p for p in self.parent.iterdir() if p != self.root]
        self.assertEqual(len(roots), 1)
        self.assertTrue((roots[0] / 'runtime-preflight-status.json').is_file())
        self.assertFalse((roots[0] / 'preflight.json').exists())
        self.assertFalse(list(roots[0].glob('comparison-*')))

    def test_diagnostic_failure_preserves_primary_or_fails_sanitized(self):
        for operation in ('runtime_preflight_status', 'write_json'):
            for valid in (False, True):
                with self.subTest(operation=operation, valid=valid):
                    raw = raw_observation('preflight')
                    if not valid:
                        raw['errors']['system_info'] = 'probe_failed'
                    primary = ValueError('incomplete runtime observation')
                    self.browser.reset_mock()
                    original_identity = gfx_runtime.identity
                    def identity(value):
                        if not valid:
                            raise primary
                        return original_identity(value)
                    with patch.object(bench, operation, side_effect=OSError('SECRET_WRITE_FAILURE')), \
                         patch.object(gfx_runtime, 'identity', side_effect=identity) as check:
                        with self.assertRaises((ValueError, RuntimeError)) as caught:
                            self.invoke(raw)
                    check.assert_called_once_with(raw)
                    self.browser.stop.assert_called_once()
                    failure = caught.exception
                    if valid:
                        self.assertIs(type(failure), RuntimeError)
                        self.assertEqual(str(failure), 'runtime preflight diagnostic unavailable')
                    else:
                        self.assertIs(failure, primary)
                        self.assertEqual(str(failure.__cause__), 'runtime preflight diagnostic unavailable')
                    rendered = ''.join(traceback.format_exception(type(failure), failure, failure.__traceback__))
                    self.assertNotIn('SECRET_WRITE_FAILURE', rendered)
                    self.pack_failure(self.parent, rendered, diagnostic=False)
                    self.assertFalse((self.root / 'runtime-preflight-status.json').exists())

    def test_diagnostic_write_failure_never_starts_runner_arms(self):
        fixture, write = bench.fixture, bench.write_json
        def fake_fixture(root):
            timeline = fixture(root, create_media=False)
            (root / 'media.mp4').write_bytes(b'mock input; never decoded')
            return timeline
        def fail_diagnostic(path, value):
            if path.name == 'runtime-preflight-status.json':
                raise OSError('SECRET_WRITE_FAILURE')
            return write(path, value)
        with patch.object(gfx_render, 'Browser', return_value=self.browser), \
             patch.object(gfx_runtime, 'collect', return_value=raw_observation('preflight')), \
             patch.object(bench, 'fixture', side_effect=fake_fixture), \
             patch.object(bench, 'write_json', side_effect=fail_diagnostic), \
             patch.object(bench, 'run_arm') as arm:
            with self.assertRaisesRegex(RuntimeError, '^runtime preflight diagnostic unavailable$'):
                bench.run_experiment(self.parent, '/mock/browser', [])
        arm.assert_not_called()
        self.browser.stop.assert_called_once()
        roots = [p for p in self.parent.iterdir() if p != self.root]
        self.assertEqual(len(roots), 1)
        self.assertTrue((roots[0] / 'owner.json').is_file())
        self.assertFalse((roots[0] / 'preflight.json').exists())
        self.assertFalse(list(roots[0].glob('comparison-*')))
        # Required receipt missing: no partial diagnostic root can become PASS.
        from scripts import pack_lifecycle_evidence as packer
        self.pack_failure(self.parent, 'runtime preflight diagnostic unavailable', diagnostic=False)

    def test_success_return_values_unchanged(self):
        raw = raw_observation('preflight')
        identity, manifest, result = self.invoke(raw)
        self.assertEqual(identity, gfx_runtime.identity(raw))
        self.assertIs(result, raw)
        self.assertEqual(manifest, {'unchanged': True})
        self.assertTrue((self.root / 'runtime-preflight-status.json').is_file())
        self.browser.stop.assert_called_once()

    def test_fixed_size_privacy_and_safe_serialization(self):
        secret = 'PRIVATE_TOKEN_' * 100000
        raw = raw_observation('preflight')
        raw.update(effective_argv=[secret], system_info={'headers': secret},
                   actual_executable={'path': secret}, extra=secret)
        raw['errors'].update({secret: secret, 'system_info': secret, 'session_close': float('nan')})
        raw['viewport'] = float('inf')
        raw['alpha_path'] = object()
        status = bench.runtime_preflight_status(raw)
        encoded = json.dumps(status, allow_nan=False)
        self.assertLess(len(encoded), 2048)
        self.assertNotIn('PRIVATE_TOKEN', encoded)
        self.assertEqual(status['errors']['system_info'], 'unrecognized_error')
        self.assertEqual(status['errors']['session_close'], 'unrecognized_error')
        self.assertEqual(status['fields']['viewport'], 'invalid_shape')
        self.assertTrue(status['unrecognized_error_fields'])

    def test_invalid_root_shape_does_not_get_promoted(self):
        for raw in (None, [], {'schema': True, 'errors': []}):
            status = bench.runtime_preflight_status(raw)
            self.assertFalse(status['schema_valid'])
            self.assertFalse(status['errors_shape_valid'])
            json.dumps(status, allow_nan=False)
            with self.assertRaises(ValueError):
                gfx_runtime.identity(raw)

    def test_fixed_owned_destination_ignores_provider_paths(self):
        raw = raw_observation('preflight')
        raw['errors']['../../outside'] = '../../outside'
        with self.assertRaises(ValueError):
            # Force a recognized failing field while injecting arbitrary keys.
            raw['errors']['browser_version'] = 'probe_failed'
            self.invoke(raw)
        self.assertEqual({p.name for p in self.root.iterdir()},
                         {'owner.json', 'runtime-preflight-status.json'})
        self.assertEqual(list(self.parent.iterdir()), [self.root])


if __name__ == '__main__':
    unittest.main()
