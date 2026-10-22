"""A source defect must not hide the operation that interrupted fingerprinting.

These controls exercise the real legacy and agent entry points in separate
processes. Frozen exits include the legacy's permissive empty containers; no
media decoding, rendering, or optional dependency is needed.
"""
from tests._support import temporary_root
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests.diagnostics._support import timeline


REPO = Path(__file__).resolve().parents[2]

# Keep failures below the actual producer, including a deliberately deeper
# callback frame. Only the plugin result is replaced, never fingerprinting.
DIGEST_PROGRAM = '''\
import runpy
import sys
from unittest.mock import patch

module, path, failure = sys.argv[1:]
def deeper_failure():
    raise ImportError("fingerprint plugin failed")
def digest(context):
    if failure == "integer":
        return 1
    return deeper_failure()
sys.argv = [module, "-t", path, "validate"]
if module == "vtx.agent_diagnostics":
    sys.argv += ["--format", "agent-json"]
with patch("vtx.pluginlib.hooks.active_plugins_digest", side_effect=digest):
    runpy.run_module(module, run_name="__main__")
'''


def normalized_path(path):
    return re.sub(r'\["([A-Za-z_][A-Za-z_0-9]*)"\]', r'.\1', path)


class AttributionEntryPoint(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self)
        self.invocations = 0

    def invoke(self, value, *, legacy=False, digest_failure=None):
        self.invocations += 1
        directory = self.root / str(self.invocations)
        directory.mkdir()
        path = directory / 'timeline.json'
        path.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')
        module = 'vtx' if legacy else 'vtx.agent_diagnostics'
        if digest_failure:
            command = [sys.executable, '-c', DIGEST_PROGRAM,
                       module, str(path), digest_failure]
        else:
            command = [sys.executable, '-m', module, '-t', str(path), 'validate']
            if not legacy:
                command += ['--format', 'agent-json']
        result = subprocess.run(
            command, cwd=REPO, capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        return result, directory

    def compare(self, value, *, expected_exit=1, digest_failure=None):
        legacy, _ = self.invoke(value, legacy=True, digest_failure=digest_failure)
        result, directory = self.invoke(value, digest_failure=digest_failure)
        report = json.loads(result.stdout, parse_constant=lambda value: self.fail(
            'Non-finite JSON constant: ' + value))
        self.assertEqual(legacy.returncode, expected_exit, legacy.stdout + legacy.stderr)
        self.assertEqual(result.returncode, expected_exit, report)
        self.assertEqual(result.stderr, '')
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['command'], 'validate')
        self.assertEqual(report['exit_code'], expected_exit)
        self.assertIsInstance(report['legacy_output']['stdout'], str)
        self.assertIsInstance(report['legacy_output']['stderr'], str)
        if expected_exit == 0:
            self.assertEqual(report['diagnostics'], [])
            self.assertEqual(report['legacy_output']['stdout'], legacy.stdout)
            self.assertEqual(report['legacy_output']['stderr'], legacy.stderr)
            receipt = json.loads((directory / 'out/receipt.json').read_text())
            self.assertTrue(receipt['stages']['validate']['ok'])
        return legacy, report

    def assert_precise(self, report, rule, path, *, status='fail'):
        matches = [record for record in report['diagnostics']
                   if record['rule_id'] == rule
                   and normalized_path(record['json_path']) == path]
        self.assertEqual(len(matches), 1, report)
        record = matches[0]
        self.assertEqual(record['severity'], 'error')
        self.assertEqual(record['status'], status)
        self.assertIsNotNone(record['measured'])
        self.assertIsNotNone(record['threshold'])
        self.assertTrue(record['suggested_actions'])
        self.assertNotIn('runtime.unknown', [item['rule_id']
                                           for item in report['diagnostics']])
        return record

    def assert_operation(self, report, path, exception, operation, *, status='fail'):
        record = self.assert_precise(report, 'load.operation', path, status=status)
        self.assertEqual(report['diagnostics'], [record], report)
        self.assertEqual(record['measured']['exception_type'], exception)
        self.assertEqual(record['measured']['operands']['operation'], operation)
        return record

    def assert_interrupted(self, report, exception, message):
        records = [record for record in report['diagnostics']
                   if record['rule_id'] == 'runtime.unknown']
        self.assertEqual(len(records), 1, report)
        record = records[0]
        self.assertEqual(record['json_path'], '$')
        self.assertEqual(record['severity'], 'error')
        self.assertEqual(record['status'], 'unknown')
        self.assertEqual(record['measured']['exception'], exception)
        self.assertIs(record['measured']['interrupted'], True)
        self.assertIn(message, record['message'])
        self.assertIn(message, report['legacy_output']['stderr'])
        self.assertTrue(record['suggested_actions'])
        self.assertEqual(report['diagnostics'], records, report)

    def source_control(self, missing_id):
        value = timeline()
        source = {'path': 'missing.wav'}
        if not missing_id:
            source['id'] = 'source'
        value['sources'] = [source]
        return value

    def test_null_video_track_is_not_hidden_by_missing_source_id(self):
        for missing_id in (False, True):
            with self.subTest(missing_id=missing_id):
                value = self.source_control(missing_id)
                value['video_tracks'] = [None]
                legacy, report = self.compare(value)
                self.assertIn("AttributeError: 'NoneType' object has no attribute 'get'",
                              legacy.stderr)
                self.assert_operation(report, '$.video_tracks[0]', 'AttributeError',
                                      'mapping_get:clips')

    def test_gfx_missing_src_is_not_hidden_by_missing_source_id(self):
        for missing_id in (False, True):
            with self.subTest(missing_id=missing_id):
                value = self.source_control(missing_id)
                value['video_tracks'] = [{'kind': 'gfx', 'clips': [{}]}]
                legacy, report = self.compare(value)
                self.assertEqual(legacy.stdout, "✗ timeline 无效：'src'\n")
                self.assertEqual(legacy.stderr, '')
                self.assert_precise(report, 'load.required_field',
                                    '$.video_tracks[0].clips[0].src')

    def test_integer_digest_has_provider_origin_with_or_without_source_defect(self):
        for missing_id in (False, True):
            with self.subTest(missing_id=missing_id):
                legacy, report = self.compare(self.source_control(missing_id),
                                              digest_failure='integer')
                message = "'int' object has no attribute 'encode'"
                self.assertIn('AttributeError: ' + message, legacy.stderr)
                record = self.assert_operation(report, '$', 'AttributeError', 'encode', status='unknown')
                self.assertEqual(record['source'], {'domain': 'provider',
                                 'provider': 'active_plugins_digest', 'field': 'return_value'})

    def test_deeper_import_error_is_interrupted_with_or_without_source_defect(self):
        for missing_id in (False, True):
            with self.subTest(missing_id=missing_id):
                legacy, report = self.compare(self.source_control(missing_id),
                                              digest_failure='import')
                self.assertIn('ImportError: fingerprint plugin failed', legacy.stderr)
                self.assert_interrupted(report, 'ImportError', 'fingerprint plugin failed')

    def test_multiple_usable_path_source_defects_do_not_hide_later_failures(self):
        for failure in ('video', 'gfx', 'integer', 'import'):
            with self.subTest(failure=failure):
                value = timeline()
                value['sources'] = [
                    {'path': 'z-missing.wav'}, {'id': '', 'path': 'a-missing.wav'},
                    {'id': 3, 'path': 'm-missing.wav'},
                ]
                if failure == 'video':
                    value['video_tracks'] = [None]
                elif failure == 'gfx':
                    value['video_tracks'] = [{'kind': 'gfx', 'clips': [{}]}]
                _, report = self.compare(value, digest_failure=(
                    failure if failure in ('integer', 'import') else None))
                if failure == 'video':
                    self.assert_operation(report, '$.video_tracks[0]', 'AttributeError',
                                          'mapping_get:clips')
                elif failure == 'gfx':
                    self.assert_precise(report, 'load.required_field',
                                        '$.video_tracks[0].clips[0].src')
                elif failure == 'integer':
                    self.assert_operation(report, '$', 'AttributeError', 'encode', status='unknown')
                else:
                    self.assert_interrupted(report, 'ImportError', 'fingerprint plugin failed')

    def test_source_access_failures_keep_original_indices(self):
        # Sorting changes source order. These are failures of the actual source
        # sort key or path access, unlike the independent id-only defect above.
        for source in (None, [], False, 'bad', {}, {'id': 'bad'},
                       {'id': 'bad', 'path': None}, {'id': 'bad', 'path': 4}):
            with self.subTest(source=source):
                value = timeline()
                value['sources'] = [
                    {'id': 'before', 'path': 'z-missing.wav'},
                    {'id': 'also-before', 'path': 'a-missing.wav'}, source,
                ]
                _, report = self.compare(value)
                exception = ('AttributeError' if not isinstance(source, dict) else
                             'KeyError' if 'path' not in source else 'TypeError')
                if exception == 'KeyError':
                    self.assert_precise(report, 'load.required_field', '$.sources[2].path')
                else:
                    self.assert_operation(report, '$.sources[2]' if exception == 'AttributeError'
                                          else '$.sources[2].path', exception,
                                          'mapping_get:path' if exception == 'AttributeError' else 'path_join')

    def test_multiple_malformed_sources_include_actual_sort_key_failure(self):
        value = timeline()
        value['sources'] = [
            {'path': 'z-missing.wav'}, {'id': 'missing-path'}, None,
            {'id': 'wrong-path-type', 'path': 4},
        ]
        legacy, report = self.compare(value)
        self.assertIn("AttributeError: 'NoneType' object has no attribute 'get'", legacy.stderr)
        self.assert_operation(report, '$.sources[2]', 'AttributeError', 'mapping_get:path')

    def test_multiple_malformed_sources_include_actual_sorted_path_failure(self):
        value = timeline()
        value['sources'] = [
            {'path': 'z-missing.wav'}, {'id': 'wrong-path-type', 'path': 4},
            {'id': 'ok', 'path': 'a-missing.wav'}, {'id': 'missing-path'},
        ]
        legacy, report = self.compare(value)
        self.assertEqual(legacy.stdout, "✗ timeline 无效：'path'\n")
        self.assertEqual(legacy.stderr, '')
        self.assert_precise(report, 'load.required_field', '$.sources[3].path')

    def test_later_video_and_clip_failures_keep_original_indices(self):
        for failure in ('video', 'gfx'):
            with self.subTest(failure=failure):
                value = self.source_control(missing_id=True)
                value['video_tracks'] = [{'kind': 'aroll', 'clips': []}, {}]
                if failure == 'video':
                    value['video_tracks'].append(None)
                    path, rule = '$.video_tracks[2]', 'load.operation'
                else:
                    value['video_tracks'].append({
                        'kind': 'gfx', 'clips': [{'src': 'missing.html'}, {}]})
                    path, rule = '$.video_tracks[2].clips[1].src', 'load.required_field'
                _, report = self.compare(value)
                self.assert_precise(report, rule, path)

    def test_source_container_failures_and_permissive_legacy_controls(self):
        # Empty mappings and strings are iterable and the legacy accepts them.
        # Opting into diagnostics must not introduce an array-only schema gate.
        for sources, expected_exit in ((None, 1), (5, 1), (True, 1),
                                       ([], 0), ({}, 0), ('', 0), ({'bad': {}}, 1)):
            with self.subTest(sources=sources):
                value = timeline()
                value['sources'] = sources
                _, report = self.compare(value, expected_exit=expected_exit)
                if expected_exit:
                    if isinstance(sources, dict):
                        self.assert_operation(report, '$.sources[0]', 'AttributeError', 'mapping_get:path')
                    else:
                        self.assert_precise(report, 'load.iterable', '$.sources')

    def test_valid_sources_do_not_suppress_later_validation(self):
        value = self.source_control(missing_id=False)
        _, report = self.compare(value)
        self.assert_precise(report, 'validate.source_exists', '$.sources[0].path')

    def test_dependency_track_scan_precedes_source_encoding(self):
        for tracks in ([], [None]):
            with self.subTest(tracks=tracks):
                value = timeline()
                value['sources'] = [{'id': 's', 'path': '\ud800'}]
                value['video_tracks'] = tracks
                legacy, report = self.compare(value)
                if tracks:
                    # #52 checks all clip dependencies before fingerprinting.
                    # The actual A reference fails on this track before it can
                    # reach source encoding; never fabricate a surrogate cause.
                    self.assertEqual(legacy.stdout, '')
                    self.assertIn("AttributeError: 'NoneType' object has no attribute 'get'", legacy.stderr)
                    self.assert_operation(report, '$.video_tracks[0]', 'AttributeError', 'mapping_get:clips')
                else:
                    self.assertIn('surrogates not allowed', legacy.stdout)
                    self.assertEqual(legacy.stderr, '')
                    self.assert_operation(report, '$.sources[0].path', 'UnicodeEncodeError', 'encode')

    def test_corrected_sources_reach_precise_validation(self):
        for source in ({'path': 'missing.wav'}, {'id': '', 'path': 'missing.wav'},
                       {'id': 's', 'path': ''}):
            with self.subTest(source=source):
                value = timeline()
                value['sources'] = [source]
                _, report = self.compare(value)
                self.assert_precise(report, 'validate.source_structure', '$.sources[0]')

    def test_direct_access_handles_only_its_own_exception(self):
        from types import SimpleNamespace
        from vtx.runtime.diagnostics import required_field, indexed
        for operation, value, rule in ((required_field, {}, 'load.required_field'),
                                       (indexed, None, 'load.iterable')):
            records = []
            context = SimpleNamespace(diagnostics=records)
            with self.assertRaises((KeyError, TypeError)) as caught:
                if operation is required_field:
                    operation(context, value, 'field', '$.input')
                else:
                    operation(context, value, '$.input')
            self.assertTrue(caught.exception._vtx_diagnostic_handled)
            self.assertEqual([item['rule_id'] for item in records], [rule])
            unrelated = TypeError('later failure')
            self.assertFalse(getattr(unrelated, '_vtx_diagnostic_handled', False))
        self.assertEqual(required_field(context, {'field': 3}, 'field', '$.input'), 3)
        self.assertEqual(list(indexed(context, {}, '$.input')), [])

    def test_identity_matches_unwrapped_producer_with_and_without_diagnostics(self):
        from vtx.cache.cache_state import receipt_fingerprint as producer
        from vtx.infra import Ctx, receipt_fingerprint as annotated

        value = timeline()
        value['sources'] = [{'id': 'z', 'path': 'z.asset'},
                            {'id': 'a', 'path': 'a.asset'}]
        value['video_tracks'] = [{'kind': 'gfx', 'clips': [
            {'src': 'graphic.html', 'dst_in': 0, 'dur': 1}]}]
        value['project']['cache_dependencies'] = ['data.json']
        for name, contents in (('z.asset', b'fixture z'), ('a.asset', b'fixture a'),
                               ('graphic.html', b'<p>fixture</p>'), ('data.json', b'{}')):
            (self.root / name).write_bytes(contents)
        path = self.root / 'timeline.json'
        original = json.dumps(value).encode()
        path.write_bytes(original)

        with patch('vtx.pluginlib.hooks.active_plugins_digest', return_value='fixture-digest'):
            plain = Ctx(path)
            records = []
            structured = Ctx(path, diagnostics=records)
            expected = producer(plain, timeline_bytes=original)
            self.assertEqual(plain.input_fingerprint, expected)
            self.assertEqual(structured.input_fingerprint, expected)
            self.assertEqual(annotated(plain, timeline_bytes=original), expected)
            self.assertEqual(annotated(structured, timeline_bytes=original), expected)
            self.assertEqual(structured.timeline_sha256, hashlib.sha256(original).hexdigest())
            self.assertEqual(structured.timeline_md5, hashlib.md5(original).hexdigest())
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(structured.tl, value)
            self.assertEqual(records, [])


if __name__ == '__main__':
    unittest.main()
