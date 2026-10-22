"""Real-entry regressions for the reviewed 47-case structural boundary matrix.

Expected exits are frozen from the pre-diagnostics CLI, not inferred from the
implementation under test. In particular, empty object collections and valid
subtitle intervals without string text remain accepted by that CLI.
"""
from tests._support import temporary_root
import contextlib
import copy
import io
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.diagnostics._support import timeline
from vtx.agent_diagnostics import main


REPO = Path(__file__).resolve().parents[2]


def _path(path):
    """Treat dot and JSON-quoted simple keys as the same precise JSONPath."""
    return re.sub(r'\["([A-Za-z_][A-Za-z_0-9]*)"\]', r'.\1', path)


def _matrix():
    cases = []

    def add(name, mutate, paths=(), rules=(), exit_code=1):
        value = timeline()
        mutate(value)
        cases.append((name, value, exit_code, tuple(paths), tuple(rules)))

    for key in ('project', 'sources', 'video_tracks', 'audio_tracks', 'subtitles'):
        add(key + '_missing', lambda t, k=key: t.pop(k), ('$.' + key,))
        for value, label in ((None, 'null'), (5, 'int'), ({}, 'object')):
            # The pre-existing validator iterates empty mappings successfully.
            accepted = key != 'project' and label == 'object'
            if key == 'project':
                paths = ('$.project.fps',) if label == 'object' else ('$.project', '$.project.fps')
            else:
                paths = ('$.' + key,)
            add(key + '_' + label,
                lambda t, k=key, v=value: t.update({k: v}),
                paths, exit_code=0 if accepted else 1)

    for key in ('fps', 'width', 'height', 'duration_frames'):
        add('project_' + key + '_missing',
            lambda t, k=key: t['project'].pop(k), ('$.project.' + key,))

    for source, label in (
        (None, 'null'), ({}, 'empty'), ({'id': 's'}, 'path_missing'),
        ({'path': 'missing.wav'}, 'id_missing'),
        ({'id': 's', 'path': None}, 'path_null'),
        ({'id': 's', 'path': ''}, 'path_empty'),
    ):
        add('source_' + label, lambda t, s=source: t.update(sources=[s]),
            ('$.sources[0]',), ('validate.source_structure',))

    for track, label, paths in (
        (None, 'null', ('$.video_tracks[0]', '$.video_tracks[0].clips')),
        ({}, 'empty', ('$.video_tracks[0].clips',)),
        ({'name': 'v', 'clips': None}, 'clips_null', ('$.video_tracks[0].clips',)),
        ({'name': 'v', 'clips': 5}, 'clips_int', ('$.video_tracks[0].clips',)),
        ({'name': 'v', 'clips': [None]}, 'clip_null',
         ('$.video_tracks[0].clips[0]', '$.video_tracks[0].clips[0].dst_in')),
        ({'name': 'v', 'clips': [{}]}, 'clip_empty',
         ('$.video_tracks[0].clips[0].dst_in',)),
        ({'name': 'g', 'kind': 'gfx', 'clips': [{'dst_in': 0, 'dur': 1}]},
         'gfx_src_missing', ('$.video_tracks[0].clips[0].src',)),
    ):
        add('video_' + label, lambda t, tr=track: t.update(video_tracks=[tr]), paths)

    for track, label, paths, rule in (
        (None, 'null', ('$.audio_tracks[0]',), 'validate.audio_track_structure'),
        ({}, 'empty', ('$.audio_tracks[0].events',), 'validate.audio_events_structure'),
        ({'name': 'a', 'events': 5}, 'events_int',
         ('$.audio_tracks[0].events',), 'validate.audio_events_structure'),
        ({'name': 'a', 'events': [None]}, 'event_null',
         ('$.audio_tracks[0].events',), 'validate.audio_events_structure'),
        ({'name': 'a', 'events': [{}]}, 'event_empty',
         ('$.audio_tracks[0].events[0].src', '$.audio_tracks[0].events[0].in',
          '$.audio_tracks[0].events[0].out', '$.audio_tracks[0].events[0].volume'),
         'validate.audio_event_required'),
        ({'name': 'a', 'events': [{'src': 'x', 'in': 0, 'out': 1}]}, 'volume_missing',
         ('$.audio_tracks[0].events[0].volume',), 'validate.audio_event_required'),
    ):
        add('audio_' + label, lambda t, tr=track: t.update(audio_tracks=[tr]), paths, (rule,))

    for sub, label, paths, exit_code in (
        (None, 'null', ('$.subtitles[0]', '$.subtitles[0].in'), 1),
        ({}, 'empty', ('$.subtitles[0].in',), 1),
        ({'in': 0, 'out': 1}, 'text_missing', (), 0),
        ({'in': 0, 'out': 1, 'text': 5}, 'text_int', (), 0),
    ):
        add('subtitle_' + label, lambda t, s=sub: t.update(subtitles=[s]),
            paths, exit_code=exit_code)
    return cases


MATRIX = _matrix()

# These operations fail during fingerprinting, before later validation gates.
# The expected origin is the executed producer, not the later source predicate.
CACHE_PRODUCERS = {
    'sources_null': ('load.iterable', '$.sources'),
    'sources_int': ('load.iterable', '$.sources'),
    'video_tracks_null': ('load.iterable', '$.video_tracks'),
    'video_tracks_int': ('load.iterable', '$.video_tracks'),
    'source_null': ('load.operation', '$.sources[0]'),
    'source_empty': ('load.required_field', '$.sources[0].path'),
    'source_path_missing': ('load.required_field', '$.sources[0].path'),
    'source_path_null': ('load.operation', '$.sources[0].path'),
    'video_null': ('load.operation', '$.video_tracks[0]'),
    'video_gfx_src_missing': ('load.required_field', '$.video_tracks[0].clips[0].src'),
}


class BoundaryEntryPoint(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self)

    def write_timeline(self, value, name='agent'):
        directory = self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'timeline.json'
        path.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')
        return path

    def invoke(self, value, *, legacy=False):
        path = self.write_timeline(value, 'legacy' if legacy else 'agent')
        command = [sys.executable, '-m', 'vtx' if legacy else 'vtx.agent_diagnostics',
                   '-t', str(path), 'validate']
        if not legacy:
            command.extend(('--format', 'agent-json'))
        return subprocess.run(command, cwd=REPO, text=True, capture_output=True, timeout=30)

    def report(self, result):
        # json.loads rejects extra documents and any unstructured trailing text.
        report = json.loads(result.stdout, parse_constant=lambda value: self.fail(
            'Non-finite JSON constant: ' + value))
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['command'], 'validate')
        self.assertEqual(report['exit_code'], result.returncode)
        self.assertIsInstance(report['diagnostics'], list)
        self.assertIsInstance(report['legacy_output']['stdout'], str)
        self.assertIsInstance(report['legacy_output']['stderr'], str)
        return report

    def assert_known_failure(self, report, paths, rules=()):
        errors = [record for record in report['diagnostics'] if record['severity'] == 'error']
        self.assertTrue(errors, report)
        for record in errors:
            self.assertNotIn(record['rule_id'], ('unknown', 'runtime.unknown'), record)
            self.assertNotEqual(record['json_path'], '$', record)
        precise = [record for record in errors
                   if record['rule_id'].startswith(('load.', 'validate.'))
                   and _path(record['json_path']) in paths
                   and (not rules or record['rule_id'] in rules)]
        self.assertTrue(precise, (paths, rules, errors))
        for record in precise:
            self.assertIsNotNone(record['measured'], record)
            self.assertIsNotNone(record['threshold'], record)
            self.assertTrue(record['suggested_actions'], record)

    def assert_cache_producer(self, report, rule, path):
        self.assertEqual(len(report['diagnostics']), 1, report)
        record = report['diagnostics'][0]
        self.assertEqual(record['rule_id'], rule)
        self.assertEqual(_path(record['json_path']), path)
        self.assertEqual(record['status'], 'fail')
        self.assert_known_failure(report, (path,), (rule,))

    def check_matrix_case(self, value, expected_exit, paths, rules, producer=None):
        # Both subprocesses run their real entry point, with independent receipts.
        legacy = self.invoke(copy.deepcopy(value), legacy=True)
        result = self.invoke(copy.deepcopy(value))
        report = self.report(result)
        self.assertEqual(legacy.returncode, expected_exit, legacy.stdout + legacy.stderr)
        self.assertEqual(result.returncode, expected_exit, report)
        if producer:
            self.assert_cache_producer(report, *producer)
        elif expected_exit:
            # The plugin-key scan's actual iterable failure remains distinct
            # from the earlier completed audio-events structure predicate.
            interrupted = value.get('audio_tracks') == [{'name': 'a', 'events': 5}]
            self.assert_known_failure(report, paths, rules)
            if interrupted:
                failures = [record for record in report['diagnostics']
                            if record['rule_id'] == 'load.iterable']
                self.assertEqual(len(failures), 1, report)
                self.assertEqual(failures[0]['json_path'], '$.audio_tracks[0].events')
                self.assertEqual(failures[0]['measured'], {'type': 'int'})
                self.assertEqual(failures[0]['status'], 'fail')
                self.assertEqual([record['rule_id'] for record in report['diagnostics']],
                                 ['validate.audio_events_structure', 'load.iterable'])
                self.assertTrue(report['legacy_output']['stderr'])
        else:
            self.assertEqual(report['diagnostics'], [], report)
            receipt = json.loads((self.root / 'agent/out/receipt.json').read_text())
            self.assertTrue(receipt['stages']['validate']['ok'])


class StructuralMatrix(BoundaryEntryPoint):
    def test_matrix_inventory_and_frozen_permissive_cases(self):
        self.assertEqual(len(MATRIX), 47)
        self.assertEqual(len({case[0] for case in MATRIX}), 47)
        self.assertEqual({name for name, _, code, _, _ in MATRIX if code == 0}, {
            'sources_object', 'video_tracks_object', 'audio_tracks_object',
            'subtitles_object', 'subtitle_text_missing', 'subtitle_text_int',
        })

    def test_empty_audio_event_identifies_every_missing_required_field(self):
        value = timeline()
        value['audio_tracks'] = [{'name': 'a', 'events': [{}]}]
        report = self.report(self.invoke(value))
        self.assertEqual(report['exit_code'], 1)
        paths = {_path(record['json_path']) for record in report['diagnostics']
                 if record['rule_id'] == 'validate.audio_event_required'}
        self.assertEqual(paths, {'$.audio_tracks[0].events[0].' + key
                                 for key in ('src', 'in', 'out', 'volume')})

    def test_source_structure_uses_original_nonzero_index(self):
        for bad in (None, {}, {'id': 'bad'}, {'path': 'missing.wav'},
                    {'id': 'bad', 'path': None}, {'id': 'bad', 'path': ''},
                    {'id': 'bad', 'path': 4}):
            with self.subTest(source=bad):
                value = timeline()
                value['sources'] = [{'id': 'before', 'path': 'missing-before.wav'},
                                    {'id': 'also-before', 'path': 'missing-also-before.wav'}, bad]
                result = self.invoke(value)
                report = self.report(result)
                self.assertEqual(result.returncode, 1)
                if bad not in ({'path': 'missing.wav'}, {'id': 'bad', 'path': ''}):
                    if bad is None:
                        producer = ('load.operation', '$.sources[2]')
                    elif 'path' not in bad:
                        producer = ('load.required_field', '$.sources[2].path')
                    else:
                        producer = ('load.operation', '$.sources[2].path')
                    self.assert_cache_producer(report, *producer)
                    continue
                self.assert_known_failure(report, ('$.sources[2]',), ('validate.source_structure',))
                malformed = [record for record in report['diagnostics']
                             if record['rule_id'] == 'validate.source_structure']
                self.assertEqual([record['json_path'] for record in malformed], ['$.sources[2]'])

    def test_source_integer_path_identifies_actual_path_operation(self):
        value = timeline()
        value['sources'] = [{'id': 's', 'path': 4}]
        self.check_matrix_case(value, 1, (), (), producer=('load.operation', '$.sources[0].path'))


def _matrix_test(name, value, expected_exit, paths, rules):
    def test(self):
        self.check_matrix_case(value, expected_exit, paths, rules, producer=CACHE_PRODUCERS.get(name))
    return test


# Individual names make a failed branch visible to unittest and strict runners.
for _name, _value, _exit, _paths, _rules in MATRIX:
    setattr(StructuralMatrix, 'test_matrix_' + _name,
            _matrix_test(_name, _value, _exit, _paths, _rules))


CALLBACK_PROGRAM = '''\
import sys
from unittest.mock import patch
from vtx.agent_diagnostics import main

errors = {
    "RuntimeError": RuntimeError("plugin failed"),
    "ImportError": ImportError("plugin optional dependency missing"),
    "OverflowError": OverflowError("probe value overflow"),
    "SystemExit": SystemExit("plugin failed"),
}
def callback(context):
    print("plugin callback stdout")
    print("plugin callback stderr", file=sys.stderr)
    raise errors[sys.argv[2]]
with patch("vtx.pipeline.stages._plugin_hooks.run_validate", side_effect=callback):
    raise SystemExit(main(["-t", sys.argv[1], "validate", "--format", "agent-json"]))
'''


class CallbackBoundaries(BoundaryEntryPoint):
    def callback_result(self, kind, value=None):
        path = self.write_timeline(timeline() if value is None else value)
        result = subprocess.run([sys.executable, '-c', CALLBACK_PROGRAM, str(path), kind],
                                cwd=REPO, text=True, capture_output=True, timeout=30)
        report = self.report(result)
        self.assertEqual(result.returncode, 1, report)
        self.assertIn('plugin callback stdout', report['legacy_output']['stdout'])
        self.assertIn('plugin callback stderr', report['legacy_output']['stderr'])
        unknown = [record for record in report['diagnostics']
                   if record['rule_id'] == 'runtime.unknown']
        self.assertEqual(len(unknown), 1, report)
        self.assertEqual(unknown[0]['json_path'], '$')
        self.assertEqual(unknown[0]['severity'], 'error')
        self.assertEqual(unknown[0]['status'], 'unknown')
        self.assertEqual(unknown[0]['measured']['exception'], kind)
        self.assertIs(unknown[0]['measured']['interrupted'], True)
        self.assertTrue(unknown[0]['suggested_actions'])
        return report

    def test_runtime_error_control_is_strict_json_exit_one(self):
        self.callback_result('RuntimeError')

    def test_import_error_callback_is_strict_json_exit_one(self):
        self.callback_result('ImportError')

    def test_overflow_error_callback_is_strict_json_exit_one(self):
        self.callback_result('OverflowError')

    def test_string_system_exit_callback_is_strict_json_exit_one(self):
        report = self.callback_result('SystemExit')
        self.assertIn('plugin failed', json.dumps(report, ensure_ascii=False))

    def test_callback_interruption_is_not_hidden_by_prior_rule_error(self):
        value = timeline()
        value['audio_tracks'] = [{'name': 'a'}]
        for kind in ('RuntimeError', 'ImportError', 'OverflowError', 'SystemExit'):
            with self.subTest(exception=kind):
                report = self.callback_result(kind, value)
                self.assertIn('validate.audio_events_structure',
                              [record['rule_id'] for record in report['diagnostics']])

    def test_constructor_callback_failure_is_not_misattributed_to_source_shape(self):
        value = timeline()
        # This source has a validation defect, but its path is sufficient for
        # fingerprinting. The deeper callback failure is a different producer.
        value['sources'] = [{'path': 'missing.wav'}]
        path = self.write_timeline(value)
        program = '''\
import sys
from unittest.mock import patch
from vtx.agent_diagnostics import main
with patch("vtx.pluginlib.hooks.active_plugins_digest",
           side_effect=ImportError("fingerprint plugin failed")):
    raise SystemExit(main(["-t", sys.argv[1], "validate", "--format", "agent-json"]))
'''
        result = subprocess.run([sys.executable, '-c', program, str(path)], cwd=REPO,
                                text=True, capture_output=True, timeout=30)
        report = self.report(result)
        self.assertEqual(result.returncode, 1, report)
        self.assertEqual([record['rule_id'] for record in report['diagnostics']],
                         ['runtime.unknown'], report)
        record = report['diagnostics'][0]
        self.assertEqual(record['json_path'], '$')
        self.assertEqual(record['severity'], 'error')
        self.assertEqual(record['status'], 'unknown')
        self.assertEqual(record['measured']['exception'], 'ImportError')
        self.assertIs(record['measured']['interrupted'], True)
        self.assertIn('fingerprint plugin failed', record['message'])

    def test_keyboard_interrupt_propagates_unchanged(self):
        path = self.write_timeline(timeline())
        interruption = KeyboardInterrupt('user interrupted')
        output, errors = io.StringIO(), io.StringIO()
        with patch('vtx.pipeline.stages._plugin_hooks.run_validate', side_effect=interruption), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            with self.assertRaises(KeyboardInterrupt) as caught:
                main(['-t', str(path), 'validate', '--format', 'agent-json'])
        self.assertIs(caught.exception, interruption)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
