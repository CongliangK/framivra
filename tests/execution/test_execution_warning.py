"""Ordinary diagnostic-channel failures cannot escape the observation boundary."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from vtx import events, execution_links as links
from tests.execution.test_execution_clock import SampleClock
from tests.execution.test_execution_events import Buffer


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
                (root / links.DIRECTORY).mkdir()
                with contextlib.redirect_stderr(diagnostic):
                    with patch.object(links, '_initialize', side_effect=OSError('PRIVATE')):
                        self.assertFalse(links.initialize(root))
                    with patch.object(links, '_bridge', side_effect=OSError('PRIVATE')):
                        request = links.prepare_request(root, export, 0)
                        self.assertEqual(links.open_response(timeline, links.ROLES[0]), (None, None))
                    output = root / 'output'; (output / 'artifacts').mkdir(parents=True)
                    with patch.object(links, 'response_bytes', side_effect=OSError('PRIVATE')):
                        row = links.retain_attempt(root, output, request, 'returned', 0)
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


if __name__ == '__main__':
    unittest.main()
