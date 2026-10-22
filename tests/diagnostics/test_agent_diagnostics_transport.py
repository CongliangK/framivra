"""Lossless JSON transport and unknown-first opaque-producer regressions."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.diagnostics._support import timeline
from vtx.agent_diagnostics import main
from vtx.cache import cache_state
from vtx import infra
from vtx.runtime.diagnostics import emit, key_path

REPO = Path(__file__).resolve().parents[2]


class UnicodeTransport(unittest.TestCase):
    def invoke(self, value, encoding, *, agent=True):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'timeline.json'
            path.write_text(json.dumps(value), encoding='ascii')
            command = [sys.executable, '-m', 'vtx.agent_diagnostics' if agent else 'vtx',
                       '-t', str(path), 'validate']
            if agent:
                command += ['--format', 'agent-json']
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            if encoding:
                env['PYTHONIOENCODING'] = encoding
            else:
                env.pop('PYTHONIOENCODING', None)
            return subprocess.run(command, cwd=REPO, capture_output=True, env=env, timeout=30)

    def test_root_and_nested_keys_roundtrip_on_strict_output_streams(self):
        for key, nested in (('\ud800', False), ('\udfff', True),
                            ('日本語😀', False), ('quote"[\\\ud800', True)):
            value = timeline()
            container = value['project'] if nested else value
            container[key] = {'nested': ['\udfff', '😀']}
            for encoding in (None, 'utf-8:strict', 'ascii:strict'):
                with self.subTest(key=repr(key), nested=nested, encoding=encoding):
                    result = self.invoke(value, encoding)
                    report = json.loads(result.stdout.decode('ascii'))
                    self.assertEqual(result.returncode, 0, report)
                    self.assertEqual(result.stderr, b'')
                    self.assertEqual(report['exit_code'], 0)
                    expected_path = key_path('$.project' if nested else '$', key)
                    records = [r for r in report['diagnostics'] if r['json_path'] == expected_path]
                    self.assertEqual(len(records), 1, report)
                    self.assertEqual(records[0]['severity'], 'warning')
                    self.assertEqual(records[0]['measured']['key'], key)
            # Human output remains untouched. Encoding failures caused by a
            # deliberately ASCII-only human terminal are outside this comparison.
            human = self.invoke(value, 'utf-8:strict', agent=False)
            self.assertEqual(human.returncode, 0)
            self.assertEqual(report['legacy_output']['stdout'], human.stdout.decode('utf-8'))
            self.assertEqual(human.stderr, b'')

    def test_surrogate_source_id_failure_still_has_a_document(self):
        value = timeline()
        value['sources'] = [{'id': '\ud800', 'path': 'missing.wav'}]
        for encoding in ('utf-8:strict', 'ascii:strict'):
            with self.subTest(encoding=encoding):
                result = self.invoke(value, encoding)
                report = json.loads(result.stdout.decode('ascii'))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, b'')
                record = next(r for r in report['diagnostics'] if r['rule_id'] == 'validate.source_exists')
                self.assertIn('\ud800', record['message'])

    def test_nested_evidence_and_prior_finding_survive_actual_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'timeline.json'; path.write_text(json.dumps(timeline()))
            evidence = {'\ud800': ['\udfff', '日本語😀', {'value': '\ud800'}]}
            def failing_stage(c):
                emit(c, 'incidental \ud800 finding', json_path='$["\\ud800"]',
                     rule_id='fixture.finding', measured=evidence, threshold={'fixture': True})
                raise ValueError('actual interruption \udfff')
            raw = io.BytesIO()
            stream = io.TextIOWrapper(raw, encoding='ascii', errors='strict')
            with patch('vtx.agent_diagnostics.validate', side_effect=failing_stage), \
                    contextlib.redirect_stdout(stream):
                self.assertEqual(main(['-t', str(path), 'validate', '--format', 'agent-json']), 1)
            stream.flush(); report = json.loads(raw.getvalue().decode('ascii'))
            self.assertEqual(report['diagnostics'][0]['measured'], evidence)
            unknown = report['diagnostics'][1]
            self.assertEqual(unknown['rule_id'], 'runtime.unknown')
            self.assertEqual(unknown['message'], 'actual interruption \udfff')
            self.assertEqual(unknown['measured'], {'exception': 'ValueError', 'interrupted': True})


class DependencyCausality(unittest.TestCase):
    def test_opaque_producers_are_direct_imports_without_replay(self):
        self.assertIs(infra.validate_cache_dependencies, cache_state.validate_cache_dependencies)
        self.assertIs(infra.receipt_fingerprint, cache_state.receipt_fingerprint)

    def test_transient_first_failure_never_reads_later_dependencies(self):
        for enabled in (False, True):
            for later_missing in (False, True):
                with self.subTest(enabled=enabled, later_missing=later_missing), tempfile.TemporaryDirectory() as d:
                    root = Path(d); (root/'first.txt').touch(); (root/'second.txt').touch()
                    c = SimpleNamespace(root=root, tl={'project': {'cache_dependencies':
                        ['first.txt', 'missing.txt' if later_missing else 'second.txt']}})
                    if enabled: c.diagnostics = []
                    calls = []; failure = PermissionError('transient first dependency failure')
                    original = Path.is_file
                    def is_file(path):
                        calls.append(path.name)
                        if len(calls) == 1: raise failure
                        return original(path)
                    with patch.object(Path, 'is_file', is_file):
                        with self.assertRaises(PermissionError) as caught:
                            infra.validate_cache_dependencies(c)
                    self.assertIs(caught.exception, failure)
                    self.assertEqual(calls, ['first.txt'])
                    self.assertFalse(getattr(failure, '_vtx_diagnostic_handled', False))
                    self.assertEqual(getattr(c, 'diagnostics', []), [])

    def test_entrypoint_retains_actual_transient_exception_for_both_later_controls(self):
        for later_missing in (False, True):
            with self.subTest(later_missing=later_missing), tempfile.TemporaryDirectory() as d:
                root = Path(d); (root/'first.txt').touch(); (root/'second.txt').touch()
                value = timeline(); value['project']['cache_dependencies'] = [
                    'first.txt', 'missing.txt' if later_missing else 'second.txt']
                path = root/'timeline.json'; path.write_text(json.dumps(value))
                calls = []; original = Path.is_file
                def is_file(target):
                    if target.parent == root:
                        calls.append(target.name)
                        if target.name == 'first.txt' and calls.count('first.txt') == 1:
                            raise PermissionError('transient first dependency failure')
                    return original(target)
                output = io.StringIO()
                with patch.object(Path, 'is_file', is_file), contextlib.redirect_stdout(output):
                    self.assertEqual(main(['-t', str(path), 'validate', '--format', 'agent-json']), 1)
                report = json.loads(output.getvalue())
                self.assertEqual(calls, ['first.txt'])
                self.assertEqual(len(report['diagnostics']), 1)
                record = report['diagnostics'][0]
                self.assertEqual(record['rule_id'], 'runtime.unknown')
                self.assertEqual(record['json_path'], '$')
                self.assertEqual(record['measured'], {'exception': 'PermissionError', 'interrupted': True})
                self.assertEqual(record['message'], 'transient first dependency failure')
                self.assertEqual(record['status'], 'unknown')
                self.assertIsNone(record['threshold'])
                self.assertTrue(record['suggested_actions'])

class ProducerBoundaryCausality(unittest.TestCase):
    def test_stage_start_runtime_error_is_not_a_prerequisite_finding(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'timeline.json'; path.write_text(json.dumps(timeline()))
            for failure in (RuntimeError('lock callback failed'), cache_state.InputIdentityError('identity changed')):
                output = io.StringIO()
                with self.subTest(exception=type(failure).__name__), \
                        patch.object(infra.Ctx, 'begin_stage', side_effect=failure), \
                        contextlib.redirect_stdout(output):
                    self.assertEqual(main(['-t', str(path), 'validate', '--format', 'agent-json']), 1)
                records = json.loads(output.getvalue())['diagnostics']
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['rule_id'], 'runtime.unknown')
                self.assertEqual(records[0]['measured']['exception'], type(failure).__name__)
                self.assertTrue(records[0]['measured']['interrupted'])

    def test_probe_arbitrary_failure_is_not_inferred_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); path = root/'timeline.json'; path.write_text(json.dumps(timeline()))
            c = infra.Ctx(path, diagnostics=[])
            for failure in (PermissionError('probe read failed'), KeyError('callback field'), ValueError('callback value')):
                with self.subTest(exception=type(failure).__name__), \
                        patch.object(c, '_probe', side_effect=failure):
                    with self.assertRaises(type(failure)) as caught:
                        c.probe(root/'asset.mp4')
                self.assertIs(caught.exception, failure)
                self.assertFalse(getattr(failure, '_vtx_diagnostic_handled', False))
                self.assertEqual(c.diagnostics, [])
                self.assertEqual(infra._probe_json_path.get(), '$')

    def test_probe_interruption_survives_earlier_real_finding(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'timeline.json'; path.write_text(json.dumps(timeline()))
            def stage(c):
                emit(c, 'earlier finding', rule_id='fixture.finding', measured={'bad': True})
                with patch.object(c, '_probe', side_effect=PermissionError('probe read failed')):
                    c.probe(path.parent/'asset.mp4')
            output = io.StringIO()
            with patch('vtx.agent_diagnostics.validate', side_effect=stage), contextlib.redirect_stdout(output):
                self.assertEqual(main(['-t', str(path), 'validate', '--format', 'agent-json']), 1)
            records = json.loads(output.getvalue())['diagnostics']
            self.assertEqual([r['rule_id'] for r in records], ['fixture.finding', 'runtime.unknown'])
            self.assertEqual(records[1]['measured'], {'exception': 'PermissionError', 'interrupted': True})


if __name__ == '__main__':
    unittest.main()
