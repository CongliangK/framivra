"""Adversarial schema/lifecycle tests paired with supported event streams."""
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from vtx.runtime import events
from vtx.pipeline.infra import run_parallel, run_wave
from tests.execution.test_execution_events import Buffer, fixture


def encode(rows):
    return ''.join(json.dumps(row) + '\n' for row in rows)


class ContractTests(unittest.TestCase):
    def rows(self, *, receipt=False):
        stream = Buffer(); execution = events.ExecutionContext(stream)
        with execution.task('qc', kind='stage').scope():
            if receipt:
                execution.receipt('qc', True, lambda ref: None)
            else:
                events.current(SimpleNamespace(execution=execution)).attempt(lambda: None)
        execution.finish()
        result = events.read_events(stream.getvalue())
        self.assertTrue(result['complete'])
        return result['events']

    def test_observations_require_active_task_not_just_matching_uuid(self):
        rows = self.rows(receipt=True)
        commit = next(row for row in rows if row['event'] == 'receipt_commit')
        orphan = copy.deepcopy(rows)
        orphan[2]['task_id'] = str(uuid.uuid4())
        self.assertFalse(events.read_events(encode(orphan))['complete'])
        for target in (1, 3):
            moved = copy.deepcopy(rows)
            # Preserve clocks and sequencing while swapping observation with boundary.
            moved[2]['event'], moved[target]['event'] = moved[target]['event'], moved[2]['event']
            moved[2]['extensions'], moved[target]['extensions'] = moved[target]['extensions'], moved[2]['extensions']
            moved[2]['outcome'], moved[target]['outcome'] = moved[target]['outcome'], moved[2]['outcome']
            moved[2]['exit_code'], moved[target]['exit_code'] = moved[target]['exit_code'], moved[2]['exit_code']
            if target == 3:
                moved[2]['monotonic_duration'] = moved[3]['monotonic_duration']
                moved[2]['timing']['start_ms'] = moved[3]['timing']['start_ms']
                moved[2]['monotonic_duration'] = moved[2]['timing']['offset_ms'] - moved[2]['timing']['start_ms']
                moved[2]['timing']['unavailable_reason'] = None
                moved[3]['monotonic_duration'] = None
                moved[3]['timing']['start_ms'] = None
                moved[3]['timing']['unavailable_reason'] = 'not_terminal'
            self.assertFalse(events.read_events(encode(moved))['complete'])

    def test_receipt_stage_scope_and_outcome_must_match(self):
        for field, value in [('stage', 'audio'), ('scope', 'task'), ('outcome', None)]:
            rows = self.rows(receipt=True)
            rows[2][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                events.read_events(encode(rows))
        rows = self.rows(receipt=True)
        for row in rows:
            if row['task_id'] is not None:
                row['stage'] = 'audio'
        with self.assertRaises(ValueError):
            events.read_events(encode(rows))

    def test_timing_valid_edge_and_contradictory_fields(self):
        for field, value in [('start_ms', -100), ('offset_ms', True),
                             ('resolution_seconds', 0), ('resolution_seconds', float('inf')),
                             ('unavailable_reason', 'PRIVATE')]:
            rows = self.rows()
            rows[-1]['timing'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                events.read_events(encode(rows))
        for duration in (None, True, -1, 1e100, 10000):
            rows = self.rows(); rows[-1]['monotonic_duration'] = duration
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                events.read_events(encode(rows))
        tick = [0.0]; stream = Buffer()
        execution = events.ExecutionContext(stream, clock=lambda: tick[0])
        with execution.task('load').scope():
            pass
        execution.finish()
        self.assertTrue(events.read_events(stream.getvalue())['complete'])

    def test_resource_null_and_supported_measurements(self):
        rows = self.rows()
        for row in rows:
            self.assertIsNone(row['resources']['rss']['value'])
        valid = copy.deepcopy(rows)
        valid[-1]['resources']['rss'].update(value=1024, method='getrusage', unavailable_reason=None)
        valid[-1]['resources']['cpu'].update(value=0.0, method='process_time', unavailable_reason=None)
        self.assertTrue(events.read_events(encode(valid))['complete'])
        for change in ({'value': -1}, {'value': True}, {'value': 1.1},
                       {'value': 1024, 'method': None}, {'unit': 'MB'},
                       {'scope': 'secret'}, {'unavailable_reason': 'path /private'}):
            bad = copy.deepcopy(rows); bad[-1]['resources']['rss'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                events.read_events(encode(bad))

    def test_partial_writes_completed_or_marked_incomplete(self):
        class Short(Buffer):
            def write(self, text):
                return super().write(text[:10])
        stream = Short(); execution = events.ExecutionContext(stream); execution.finish()
        self.assertFalse(execution.incomplete)
        self.assertTrue(events.read_events(stream.getvalue())['complete'])
        for result in (0, None, -1, True, 10000000):
            class Broken(Buffer):
                def write(self, text):
                    return result
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                execution = events.ExecutionContext(Broken())
                self.assertEqual(execution.task().call(lambda: 9), 9)
                execution.finish()
            self.assertTrue(execution.incomplete)
            self.assertEqual(execution.seq, 0)
            self.assertEqual(stderr.getvalue().count('incomplete'), 1)
        class StallAfterPrefix(Buffer):
            def write(self, text):
                if self.tell():
                    return 0
                return super().write(text[:10])
        with contextlib.redirect_stderr(io.StringIO()):
            stream = StallAfterPrefix(); execution = events.ExecutionContext(stream); execution.finish()
        self.assertTrue(execution.incomplete)
        self.assertFalse(events.read_events(stream.getvalue())['complete'])

    def test_metadata_is_filtered_and_reader_rejects_unbounded_data(self):
        stream = Buffer(); execution = events.ExecutionContext(stream)
        with execution.task('gfx', metadata={'argv': 'SECRET /private/token', 'data': 'X' * 100000,
                                             'backend': 'cdp', 'frames': [2, 4]}).scope():
            execution.emit('diagnostic', extensions={'rule_id': 'RULE.ONE', 'argv': 'SECRET'})
        execution.finish()
        self.assertNotIn('SECRET', stream.getvalue())
        self.assertLess(len(stream.getvalue()), 10000)
        rows = events.read_events(stream.getvalue())['events']
        self.assertTrue(rows[1]['extensions']['metadata_redacted'])
        self.assertEqual(rows[1]['extensions']['frames'], [2, 4])
        for field, value in [('extensions', {'argv': 'SECRET'}), ('scope', {'data': 'SECRET'}),
                             ('extensions', {'frames': list(range(events.MAX_FRAME_ITEMS + 1))}),
                             ('extensions', {'backend': '/private'}), ('stage', [])]:
            bad = copy.deepcopy(rows); bad[1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                events.read_events(encode(bad))
        self.assertEqual(events.frame_metadata(range(100000)), {'frame_range': [0, 100000]})

    def test_common_envelope_fields_cannot_export_arbitrary_data(self):
        for key, value in [('scope', {'secret': 'SECRET'}), ('outcome', 'SECRET'),
                           ('exit_code', 'SECRET'), ('attempt', 'SECRET')]:
            stream = Buffer(); execution = events.ExecutionContext(stream)
            with contextlib.redirect_stderr(io.StringIO()):
                execution.emit('diagnostic', **{key: value})
                execution.finish()
            self.assertNotIn('SECRET', stream.getvalue())
            self.assertTrue(execution.incomplete)
        for key, value in [('scope', {'secret': 'SECRET'}), ('outcome', 'SECRET'),
                           ('exit_code', 'SECRET'), ('attempt', 'SECRET')]:
            rows = self.rows(); rows[0][key] = value
            with self.assertRaises(ValueError):
                events.read_events(encode(rows))

    def test_error_chain_privacy_and_success_exit_normalization(self):
        stream = Buffer(); execution = events.ExecutionContext(stream)
        try:
            with execution.task('load').scope():
                try:
                    raise FileNotFoundError('SECRET /private/token')
                except FileNotFoundError as cause:
                    raise RuntimeError('SECRET wrapper') from cause
        except RuntimeError as error:
            execution.finish(1, error)
        parsed = events.read_events(stream.getvalue())
        self.assertTrue(parsed['complete'])
        self.assertEqual(parsed['events'][-1]['error']['causes'], ['FileNotFoundError'])
        self.assertNotIn('SECRET', stream.getvalue())
        for key, value in [('type', 'SECRET'), ('message', '/private'), ('causes', ['SECRET'])]:
            bad = copy.deepcopy(parsed['events']); bad[-1]['error'][key] = value
            with self.assertRaises(ValueError):
                events.read_events(encode(bad))
        stream = Buffer(); execution = events.ExecutionContext(stream)
        try:
            with execution.task().scope() as task:
                task.attempt(lambda: (_ for _ in ()).throw(SystemExit(0)))
        except SystemExit as error:
            execution.finish(0, error)
        parsed = events.read_events(stream.getvalue())
        self.assertTrue(parsed['complete'])
        self.assertTrue(all(row['error'] is None for row in parsed['events']))

    def test_worker_translated_exit_retains_original_cause(self):
        for wave in (False, True):
            stream = Buffer(); execution = events.ExecutionContext(stream)
            c = SimpleNamespace(execution=execution, dry=False)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    if wave:
                        run_wave(c, [('one', lambda: (_ for _ in ()).throw(FileNotFoundError('SECRET')))])
                    else:
                        with patch('vtx.pipeline.infra.subprocess.run', side_effect=FileNotFoundError('SECRET')):
                            run_parallel(c, [('one', ['missing'])], 'fixture')
                except BaseException as error:
                    execution.finish(events.exit_code(error), error)
            result = events.read_events(stream.getvalue())
            self.assertTrue(result['complete'])
            self.assertIn('FileNotFoundError', [result['events'][-1]['error']['type']] + result['events'][-1]['error']['causes'])
            self.assertNotIn('SECRET', stream.getvalue())

    def test_input_failures_remain_distinguishable_with_identical_human_behavior(self):
        baseline = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for case, contents, cause in [('missing', None, 'FileNotFoundError'),
                                           ('parse', '{bad', 'JSONDecodeError'),
                                           ('type', '[]', 'TypeError'),
                                           ('key', '{}', 'KeyError')]:
                path = root / (case + '.json')
                if contents is not None:
                    path.write_text(contents)
                cmd = [sys.executable, '-m', 'vtx', '-t', str(path), 'validate']
                before = subprocess.run(cmd, cwd=baseline, capture_output=True, text=True)
                evidence = root / (case + '.jsonl')
                after = subprocess.run(cmd + ['--events-jsonl', str(evidence)], capture_output=True, text=True)
                self.assertEqual((before.returncode, before.stdout, before.stderr),
                                 (after.returncode, after.stdout, after.stderr))
                error = events.read_events(evidence.read_bytes())['events'][-1]['error']
                self.assertIn(cause, [error['type']] + error['causes'])


if __name__ == '__main__':
    unittest.main()
