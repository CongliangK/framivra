"""Observation failure isolation: clocks, event sinks, warnings and sealed packages."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from vtx import events, execution_links
from tests.execution import test_execution_links
from tests.execution.test_execution_events import Buffer


class SampleClock:
    def __init__(self, fail_at=None, failure='nan'):
        self.calls = 0
        self.fail_at = fail_at
        self.failure = failure

    def __call__(self):
        self.calls += 1
        if self.calls == self.fail_at:
            if self.failure == 'exception':
                raise RuntimeError('PRIVATE clock source')
            if self.failure == 'backwards':
                return self.calls - 2.0
            return {'nan': float('nan'), 'inf': float('inf'), '-inf': -float('inf'),
                    'none': None, 'bool': True, 'text': 'PRIVATE',
                    'overflow': 10 ** 1000}[self.failure]
        return float(self.calls)


def produce(clock):
    stream = Buffer()
    execution = events.ExecutionContext(stream, clock=clock)
    with execution.task('qc', kind='stage').scope() as task:
        result = task.attempt(lambda: 42)
    execution.finish()
    return execution, stream.getvalue(), result


class ClockTests(unittest.TestCase):
    def test_invalid_configuration_is_distinct_from_failed_sample(self):
        for clock in (None, False, 1, 'PRIVATE', {}, []):
            with self.subTest(clock=type(clock).__name__), self.assertRaisesRegex(ValueError, 'configuration'):
                events.ExecutionContext(clock=clock)
        for value in (float('nan'), float('inf'), -float('inf')):
            with contextlib.redirect_stderr(io.StringIO()) as warning:
                execution, data, result = produce(lambda: value)
            self.assertEqual(result, 42)
            self.assertEqual(data, '')
            self.assertTrue(execution.incomplete)
            self.assertFalse(events.read_events(data)['complete'])
            self.assertEqual(warning.getvalue().count('incomplete'), 1)

    def test_all_sampling_boundaries_degrade_without_changing_body(self):
        # Origin, run start, task start sample/event, attempt sample/event,
        # attempt terminal, task terminal, run terminal.
        for failure in ('nan', 'inf', '-inf', 'none', 'bool', 'text', 'overflow', 'exception', 'backwards'):
            for position in range(2 if failure == 'backwards' else 1, 10):
                with self.subTest(failure=failure, position=position):
                    clock = SampleClock(position, failure)
                    with contextlib.redirect_stderr(io.StringIO()) as warning:
                        execution, data, result = produce(clock)
                    self.assertEqual(result, 42)
                    self.assertTrue(execution.incomplete)
                    self.assertFalse(events.read_events(data)['complete'])
                    self.assertEqual(clock.calls, position)  # never silently recovers
                    self.assertEqual(execution.seq, len(data.splitlines()))
                    self.assertEqual(warning.getvalue().count('incomplete'), 1)
                    self.assertNotIn('PRIVATE', data + warning.getvalue())
                    self.assertNotIn('NaN', data)
                    self.assertNotIn('Infinity', data)

    def test_failure_does_not_replace_original_task_exception(self):
        original = LookupError('PRIVATE business error')
        for position in (5, 6, 7, 8, 9):
            clock = SampleClock(position, 'exception'); stream = Buffer()
            with contextlib.redirect_stderr(io.StringIO()):
                execution = events.ExecutionContext(stream, clock=clock)
                try:
                    with execution.task('qc').scope() as task:
                        task.attempt(lambda: (_ for _ in ()).throw(original))
                except LookupError as caught:
                    self.assertIs(caught, original)
                    execution.finish(1, caught)
                else:
                    self.fail('original exception did not propagate')
            self.assertTrue(execution.incomplete)
            self.assertFalse(events.read_events(stream.getvalue())['complete'])
            self.assertNotIn('PRIVATE', stream.getvalue())

    def test_equal_samples_and_negative_absolute_origin_are_valid(self):
        # Only run-relative measurements are nonnegative; the clock's reference
        # point is arbitrary. Repeated finite readings legitimately cost zero.
        for value in (0, 100.0, -100.0):
            execution, data, result = produce(lambda: value)
            parsed = events.read_events(data)
            self.assertTrue(parsed['complete'])
            self.assertFalse(execution.incomplete)
            self.assertEqual(result, 42)
            self.assertTrue(all(r['timing']['offset_ms'] == 0 for r in parsed['events']))
            self.assertTrue(all(r['monotonic_duration'] in (None, 0) for r in parsed['events']))

    def test_raw_difference_overflow_and_invalid_start_are_not_clamped(self):
        readings = iter((-1e308, 1e308))
        with contextlib.redirect_stderr(io.StringIO()):
            execution, data, result = produce(lambda: next(readings))
        self.assertTrue(execution.incomplete)
        self.assertFalse(events.read_events(data)['complete'])
        self.assertEqual(result, 42)
        for start in (float('nan'), float('inf'), -float('inf'), -1, 100, False, 'PRIVATE'):
            stream = Buffer(); execution = events.ExecutionContext(stream, clock=lambda: 0)
            with contextlib.redirect_stderr(io.StringIO()):
                execution.emit('run_terminal', started=start, outcome='succeeded', exit_code=0)
                execution.finish()
            self.assertTrue(execution.incomplete)
            self.assertFalse(events.read_events(stream.getvalue())['complete'])

    def test_disabled_failure_is_quiet_and_activation_reports_once(self):
        clock = SampleClock(1, 'exception')
        with contextlib.redirect_stderr(io.StringIO()) as warning:
            execution = events.ExecutionContext(clock=clock)
            self.assertEqual(execution.task().call(lambda: 42), 42)
        self.assertEqual(warning.getvalue(), '')
        stream = Buffer()
        with contextlib.redirect_stderr(io.StringIO()) as warning:
            execution.activate(stream)
            execution.finish()
        self.assertEqual(warning.getvalue().count('incomplete'), 1)
        self.assertFalse(events.read_events(stream.getvalue())['complete'])

    def test_receipt_commit_survives_observation_clock_failure(self):
        stream = Buffer(); clock = SampleClock(5, 'nan')
        execution = events.ExecutionContext(stream, clock=clock)
        committed = []
        with contextlib.redirect_stderr(io.StringIO()):
            with execution.task('qc', kind='stage').scope():
                result = execution.receipt('qc', True, lambda ref: (committed.append(ref), 42)[1])
            execution.finish()
        self.assertEqual(result, 42)
        self.assertEqual(len(committed), 1)
        self.assertIsNotNone(committed[0])
        parsed = events.read_events(stream.getvalue())
        self.assertFalse(parsed['complete'])
        self.assertNotIn(committed[0]['event_id'], [r['event_id'] for r in parsed['events']])

    def test_process_control_exceptions_are_not_reclassified_as_clock_errors(self):
        for exception in (KeyboardInterrupt, SystemExit):
            def interrupted():
                raise exception()
            with self.assertRaises(exception):
                events.ExecutionContext(clock=interrupted)

    def test_reducer_rejects_nonfinite_and_backwards_wire_measurements(self):
        _, data, _ = produce(SampleClock())
        original = [json.loads(line) for line in data.splitlines()]
        for field in ('offset_ms', 'start_ms', 'resolution_seconds'):
            for value in (float('nan'), float('inf'), -float('inf')):
                rows = copy.deepcopy(original); rows[-1]['timing'][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    events.read_events(''.join(json.dumps(r) + '\n' for r in rows))
        rows = copy.deepcopy(original)
        rows[2]['timing']['offset_ms'] = 0
        rows[2]['timing']['start_ms'] = 0
        with self.assertRaises(ValueError):
            events.read_events(''.join(json.dumps(r) + '\n' for r in rows))

    def test_complete_package_never_certifies_failed_clock(self):
        for position, failure in ((1, 'nan'), (1, 'inf'), (1, 'exception'),
                                  (3, 'backwards'), (5, 'nan'), (7, 'exception'), (9, '-inf')):
            with self.subTest(position=position, failure=failure):
                case = test_execution_links.PackageLinkTests(); case.setUp()
                try:
                    def render(cmd, **kwargs):
                        result = case.render(cmd, **kwargs)
                        stream, request = execution_links.open_response(cmd[4], execution_links.ROLES[0 if cmd[5] == 'all' else 1])
                        execution = events.ExecutionContext(stream, request=request, clock=SampleClock(position, failure))
                        with execution.task('qc', kind='stage').scope() as task:
                            self.assertEqual(task.attempt(lambda: 42), 42)
                        execution.finish()
                        return result
                    with contextlib.redirect_stderr(io.StringIO()):
                        case.build(render)
                    report = case.verify()
                    self.assertTrue(report['integrity_verified'])
                    self.assertEqual(report['execution_linkage'], 'incomplete')
                    self.assertEqual(report['gates']['process']['status'], 'passed')
                    self.assertFalse(report['accepted'])
                    self.assertEqual(report['gates']['visual_content']['status'], 'unknown')
                    sidecar = case.sidecar()
                    for command in sidecar['commands']:
                        self.assertEqual(command['evidence_status'], 'incomplete')
                        command.update(evidence_status='complete', reason=None)
                    case.rewrite(execution_links.SIDECAR, sidecar)
                    case.rehash(execution_links.SIDECAR)
                    with self.assertRaises(ValueError):
                        case.verify()
                finally:
                    case.doCleanups()

    def test_full_package_reanchored_invalid_timing_rejected(self):
        for value in (float('nan'), float('inf'), -float('inf'), -1):
            case = test_execution_links.PackageLinkTests(); case.setUp()
            try:
                case.build(case.observed_render)
                self.assertEqual(case.verify()['execution_linkage'], 'complete')
                command = case.sidecar()['commands'][0]
                path = case.package / command['response']
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                rows[-1]['timing']['offset_ms'] = value
                path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                case.rehash(command['response'])
                with self.assertRaises(ValueError):
                    case.verify()
            finally:
                case.doCleanups()


class BrokenStderr:
    def __init__(self, kind):
        self.kind = kind
        self.writes = 0
        self.closed_stream = io.StringIO()
        self.closed_stream.close()

    def write(self, text):
        self.writes += 1
        if self.kind == 'closed':
            return self.closed_stream.write(text)
        if self.kind == 'unicode':
            raise UnicodeEncodeError('ascii', '\ufffd', 0, 1, 'PRIVATE')
        raise self.kind('PRIVATE diagnostic failure')

    def flush(self):
        pass


FAILURES = ('closed', OSError, ValueError, 'unicode', RuntimeError)


class WarningTests(unittest.TestCase):
    def test_clock_failure_every_boundary_preserves_return_or_original_exception(self):
        for kind in FAILURES:
            for position in range(1, 10):
                for failing_body in (False, True):
                    with self.subTest(kind=kind, position=position, failing_body=failing_body):
                        diagnostic = BrokenStderr(kind); stream = Buffer(); body = []
                        original = LookupError('PRIVATE business failure')
                        def work():
                            body.append(True)
                            if failing_body:
                                raise original
                            return 42
                        with contextlib.redirect_stderr(diagnostic):
                            execution = events.ExecutionContext(stream, clock=SampleClock(position, 'nan'))
                            try:
                                with execution.task('qc').scope() as task:
                                    self.assertEqual(task.attempt(work), 42)
                            except LookupError as caught:
                                self.assertTrue(failing_body)
                                self.assertIs(caught, original)
                                execution.finish(1, caught)
                            else:
                                self.assertFalse(failing_body)
                                execution.finish()
                            execution._warn()  # bounded attempt, even after stderr recovers
                        self.assertEqual(body, [True])
                        self.assertTrue(execution.incomplete)
                        self.assertTrue(execution._warned)
                        self.assertEqual(diagnostic.writes, 1)
                        self.assertFalse(events.read_events(stream.getvalue())['complete'])
                        self.assertNotIn('PRIVATE', stream.getvalue())

    def test_every_sink_write_boundary_preserves_business_and_incomplete(self):
        for kind in FAILURES:
            for fail_at in range(1, 7):
                for failing_body in (False, True):
                    class BrokenSink(Buffer):
                        writes = 0
                        def write(self, text):
                            self.writes += 1
                            if self.writes == fail_at:
                                raise OSError('PRIVATE event sink')
                            return super().write(text)
                    diagnostic = BrokenStderr(kind); stream = BrokenSink()
                    original = LookupError('PRIVATE business failure'); body = []
                    def work():
                        body.append(True)
                        if failing_body:
                            raise original
                        return 42
                    with self.subTest(kind=kind, fail_at=fail_at, failing_body=failing_body), contextlib.redirect_stderr(diagnostic):
                        execution = events.ExecutionContext(stream)
                        try:
                            with execution.task('qc').scope() as task:
                                self.assertEqual(task.attempt(work), 42)
                        except LookupError as caught:
                            self.assertIs(caught, original)
                            execution.finish(1, caught)
                        else:
                            self.assertFalse(failing_body)
                            execution.finish()
                    self.assertEqual(body, [True])
                    self.assertTrue(execution.incomplete)
                    self.assertEqual(diagnostic.writes, 1)
                    self.assertFalse(events.read_events(stream.getvalue())['complete'])

    def test_activation_validation_and_postcommit_warning_paths(self):
        for kind in FAILURES:
            for mode in ('activation', 'invalid_event', 'invalid_start', 'receipt'):
                with self.subTest(kind=kind, mode=mode):
                    diagnostic = BrokenStderr(kind); stream = Buffer(); commits = []
                    execution = events.ExecutionContext(clock=SampleClock(1, 'nan')) if mode == 'activation' else events.ExecutionContext(stream)
                    with contextlib.redirect_stderr(diagnostic):
                        if mode == 'activation':
                            execution.activate(stream)
                        elif mode == 'invalid_event':
                            execution.emit('not_an_event')
                        elif mode == 'invalid_start':
                            execution.emit('run_terminal', started=float('nan'))
                        else:
                            with execution.task('qc', kind='stage').scope():
                                def commit(ref):
                                    commits.append(ref)
                                    execution._clock = lambda: float('nan')
                                    return 42
                                self.assertEqual(execution.receipt('qc', True, commit), 42)
                        self.assertEqual(execution.task().call(lambda: 42), 42)
                        execution.finish()
                    self.assertTrue(execution.incomplete)
                    self.assertEqual(diagnostic.writes, 1)
                    self.assertFalse(events.read_events(stream.getvalue())['complete'])
                    if mode == 'receipt':
                        self.assertEqual(len(commits), 1)
                        self.assertIsNotNone(commits[0])

    def test_bridge_degradation_warnings_are_best_effort_too(self):
        for kind in FAILURES:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp); timeline = root / 'timeline.json'; timeline.write_text('{}')
                export = str(uuid.uuid4()); diagnostic = BrokenStderr(kind)
                (root / execution_links.DIRECTORY).mkdir()
                with contextlib.redirect_stderr(diagnostic):
                    with patch.object(execution_links, '_initialize', side_effect=OSError('PRIVATE')):
                        self.assertFalse(execution_links.initialize(root))
                    with patch.object(execution_links, '_bridge', side_effect=OSError('PRIVATE')):
                        request = execution_links.prepare_request(root, export, 0)
                        self.assertEqual(execution_links.open_response(timeline, execution_links.ROLES[0]), (None, None))
                    output = root / 'output'; (output / 'artifacts').mkdir(parents=True)
                    with patch.object(execution_links, 'response_bytes', side_effect=OSError('PRIVATE')):
                        row = execution_links.retain_attempt(root, output, request, 'returned', 0)
                self.assertEqual(row['evidence_status'], 'incomplete')
                self.assertEqual(row['reason'], 'start_unobserved')
                self.assertEqual(diagnostic.writes, 4)

    def test_process_control_in_warning_channel_is_not_swallowed(self):
        for exception in (KeyboardInterrupt, SystemExit):
            diagnostic = BrokenStderr(exception)
            stream = Buffer(); execution = events.ExecutionContext(stream)
            with contextlib.redirect_stderr(diagnostic), self.assertRaises(exception):
                execution._warn()
            self.assertTrue(execution.incomplete)
            self.assertTrue(execution._warned)
            self.assertEqual(diagnostic.writes, 1)

    def test_original_process_control_business_exception_is_preserved(self):
        for exception in (KeyboardInterrupt, SystemExit):
            original = exception('PRIVATE')
            with contextlib.redirect_stderr(BrokenStderr(OSError)):
                execution = events.ExecutionContext(Buffer(), clock=SampleClock(7, 'nan'))
                try:
                    with execution.task('qc').scope() as task:
                        task.attempt(lambda: (_ for _ in ()).throw(original))
                except BaseException as caught:
                    self.assertIs(caught, original)
                    execution.finish(130, caught)
                else:
                    self.fail('process control was swallowed')
            self.assertTrue(execution.incomplete)


if __name__ == "__main__":
    unittest.main()
