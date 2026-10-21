"""Attempt/lifetime forgeries and real owned executor paths (mocked processes)."""
import contextlib
import copy
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vtx import events, execution_links, qc, encoders
from vtx.infra import Ctx
from tests.execution.test_execution_events import Buffer, fixture
from tests.execution import test_execution_links


def encode(rows):
    return ''.join(json.dumps(row) + '\n' for row in rows)


def exercise(execution, tick):
    tick[0] = 1
    with execution.task('qc', kind='stage').scope():
        tick[0] = 2
        with execution.task(kind='process').scope() as child:
            tick[0] = 3
            child.attempt(lambda: None)
            tick[0] = 4
            child.attempt(lambda: None)
            tick[0] = 5
        tick[0] = 6
    execution.finish()


def forge(rows, mode):
    rows = copy.deepcopy(rows)
    tid = next(r['task_id'] for r in rows if r['event'] == 'task_start' and r['scope'] == 'process')
    for r in rows:
        if r['task_id'] != tid:
            continue
        if mode == 'terminal_attempt' and r['event'] == 'task_terminal':
            r['attempt'] = 99
        if mode == 'retry_lifetime' and r['attempt'] == 2 and r['event'].startswith('attempt_'):
            r['timing']['start_ms'] = 2500
            if r['event'] == 'attempt_terminal':
                r['monotonic_duration'] = r['timing']['offset_ms'] - 2500
        if mode == 'retry_number' and r['event'] == 'retry_requested':
            r['attempt'] = 999
        if mode in ('child_before_parent', 'attempt_before_task', 'null_start_escape', 'retry_lifetime'):
            selected = mode == 'child_before_parent' or r['event'].startswith('attempt_')
            if selected and r['event'].endswith('_start'):
                r['timing']['start_ms'] = None if mode == 'null_start_escape' else 0
            if selected and r['event'].endswith('_terminal'):
                r['timing']['start_ms'] = 0
                r['monotonic_duration'] = r['timing']['offset_ms']
    if mode == 'overlap':
        end = next(r for r in rows if r['task_id'] == tid and r['event'] == 'attempt_terminal')
        rows.remove(end)
        pos = next(i for i, r in enumerate(rows) if r['event'] == 'attempt_start' and r['attempt'] == 2)
        rows.insert(pos + 1, end)
        for i, r in enumerate(rows, 1):
            r['seq'] = i; r['event_id'] = [r['run_id'], i]
            r['timing']['offset_ms'] = 10000 + i
            if r['monotonic_duration'] is not None:
                r['monotonic_duration'] = r['timing']['offset_ms'] - r['timing']['start_ms']
    return rows


class InvariantTests(unittest.TestCase):
    def rows(self):
        stream = Buffer(); tick = [0]
        execution = events.ExecutionContext(stream, clock=lambda: tick[0])
        exercise(execution, tick)
        parsed = events.read_events(stream.getvalue())
        self.assertTrue(parsed['complete'])
        return parsed['events']

    def test_contradictory_attempts_and_lifetimes(self):
        for mode in ('terminal_attempt', 'retry_number', 'overlap', 'child_before_parent',
                     'attempt_before_task', 'null_start_escape', 'retry_lifetime'):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                events.read_events(encode(forge(self.rows(), mode)))

    def test_legacy_null_starts_and_missing_observations_are_incomplete(self):
        rows = self.rows()
        for r in rows:
            if r['event'].endswith('_start'):
                r['timing']['start_ms'] = None
        self.assertFalse(events.read_events(encode(rows))['complete'])
        for event in ('retry_requested', 'attempt_start', 'attempt_terminal', 'task_terminal'):
            rows = self.rows()
            rows.remove(next(r for r in rows if r['event'] == event))
            with self.subTest(event=event):
                self.assertFalse(events.read_events(encode(rows))['complete'])

    def test_full_package_reanchored_forgeries(self):
        for mode in ('terminal_attempt', 'retry_number', 'overlap', 'child_before_parent',
                     'attempt_before_task', 'null_start_escape', 'retry_lifetime', 'legacy_null', 'missing_commit'):
            with self.subTest(mode=mode):
                case = test_execution_links.PackageLinkTests(); case.setUp()
                try:
                    def render(cmd, **kwargs):
                        result = case.render(cmd, **kwargs)
                        role = execution_links.ROLES[0 if cmd[5] == 'all' else 1]
                        stream, request = execution_links.open_response(cmd[4], role)
                        tick = [0]
                        execution = events.ExecutionContext(stream, request=request, clock=lambda: tick[0])
                        # Add a real post-commit receipt observation before the test workload.
                        with execution.task('qc', kind='stage').scope():
                            execution.receipt('qc', True, lambda ref: None)
                        exercise(execution, tick)
                        return result
                    case.build(render)
                    report = case.verify()
                    self.assertEqual(report['execution_linkage'], 'complete')
                    self.assertFalse(report['accepted'])
                    self.assertEqual(report['gates']['visual_content']['status'], 'unknown')
                    sidecar = case.sidecar(); command = sidecar['commands'][1]
                    path = case.package / command['response']
                    rows = [json.loads(line) for line in path.read_text().splitlines()]
                    if mode == 'legacy_null':
                        for row in rows:
                            if row['event'].endswith('_start'):
                                row['timing']['start_ms'] = None
                    elif mode == 'missing_commit':
                        rows = [r for r in rows if r['event'] != 'receipt_commit']
                    else:
                        rows = forge(rows, mode)
                    path.write_text(encode(rows)); case.rehash(command['response'])
                    if mode in ('legacy_null', 'missing_commit'):
                        # A lie about completeness rejects; honest uncertainty survives.
                        with self.assertRaises(ValueError):
                            case.verify()
                        command.update(evidence_status='incomplete', reason='incomplete_events')
                        case.rewrite(execution_links.SIDECAR, sidecar); case.rehash(execution_links.SIDECAR)
                        report = case.verify()
                        self.assertEqual(report['execution_linkage'], 'incomplete')
                        self.assertFalse(report['accepted'])
                        self.assertEqual(report['gates']['visual_content']['status'], 'unknown')
                    else:
                        with self.assertRaises(ValueError):
                            case.verify()
                finally:
                    case.doCleanups()

    def test_custom_stream_exceptions_do_not_change_execution(self):
        for exception in (RuntimeError, OSError, ValueError, TypeError):
            class Broken(Buffer):
                def write(self, text):
                    raise exception('private')
            with contextlib.redirect_stderr(io.StringIO()) as warning:
                execution = events.ExecutionContext(Broken())
                self.assertEqual(execution.task().call(lambda: 42), 42)
                execution.finish()
            self.assertTrue(execution.incomplete)
            self.assertEqual(warning.getvalue().count('incomplete'), 1)
        for exception in (KeyboardInterrupt, SystemExit):
            class Interrupted(Buffer):
                def write(self, text):
                    raise exception()
            with self.assertRaises(exception):
                events.ExecutionContext(Interrupted())


class OwnedExecutorTests(unittest.TestCase):
    def test_actual_qc_nested_executors_two_simultaneous_runs(self):
        # Three independent QC jobs per run must actually overlap. Sharing one
        # Context object across submissions fails before all six reach this barrier.
        barrier = threading.Barrier(6)
        worker_ids = set(); lock = threading.Lock()
        def fake_run(cmd, **kwargs):
            if cmd[0] == 'ffmpeg':
                with lock:
                    worker_ids.add(threading.get_ident())
                barrier.wait(timeout=10)
                if cmd[-2] == 'rawvideo':
                    Path(cmd[-1]).write_bytes(bytes(48 * 27))
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                'streams': [{'nb_frames': 30, 'width': 64, 'height': 64}],
                'format': {'duration': '1.0'}}),
                stderr='I: -14.0 LUFS\nPeak: -2.0 dBFS\nmean_volume: -20.0 dB')
        def measure(path, fn, **kwargs):
            loudness, peak = fn()
            return {'i_lufs': loudness, 'tp_dbfs': peak}
        def run(root):
            root.mkdir(); stream = Buffer(); execution = events.ExecutionContext(stream)
            c = Ctx(fixture(root), execution=execution)
            c.out.mkdir(exist_ok=True); c.cache.mkdir(exist_ok=True)
            c.tl['video_tracks'] = [{'clips': []}]
            c.tl['audio_tracks'] = [{'events': [{'in': 0, 'out': 30}]}]
            with execution.task('qc', kind='stage').scope() as parent:
                qc.qc(c)
            execution.finish()
            return events.read_events(stream.getvalue()), parent.task_id
        with tempfile.TemporaryDirectory() as temp, \
             patch('vtx.infra.subprocess.run', side_effect=fake_run), \
             patch('vtx.qc.loudness_cache.lookup', return_value=None), \
             patch('vtx.qc.loudness_cache.measure_and_store', side_effect=measure), \
             contextlib.redirect_stdout(io.StringIO()), ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(run, Path(temp) / str(i)) for i in range(2)]
            results = [f.result() for f in futures]
        self.assertEqual(len(worker_ids), 6)
        self.assertEqual(len({r['run_id'] for r, _ in results}), 2)
        for result, parent in results:
            self.assertTrue(result['complete'])
            processes = [r for r in result['events'] if r['event'] == 'task_start' and r['scope'] == 'process']
            self.assertEqual(len(processes), 4)  # main probe + three executor paths
            tids = {r['task_id'] for r in processes}
            for row in result['events']:
                if row['task_id'] in tids:
                    self.assertEqual(row['stage'], 'qc')
                    self.assertEqual(row['parent_task_id'], parent)

    def test_actual_encoder_probe_executor_context(self):
        barrier = threading.Barrier(3)
        def fake_run(*args, **kwargs):
            barrier.wait(timeout=10)
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        with tempfile.TemporaryDirectory() as temp:
            stream = Buffer(); execution = events.ExecutionContext(stream)
            c = Ctx(fixture(Path(temp)), execution=execution)
            with patch('vtx.infra.subprocess.run', side_effect=fake_run):
                with execution.task('video', kind='stage').scope() as parent:
                    self.assertEqual(encoders.nvenc_concurrency(c, 3), 3)
            execution.finish()
            result = events.read_events(stream.getvalue())
        self.assertTrue(result['complete'])
        processes = [r for r in result['events'] if r['event'] == 'task_start' and r['scope'] == 'process']
        self.assertEqual(len(processes), 3)
        self.assertTrue(all(r['parent_task_id'] == parent.task_id and r['stage'] == 'video' for r in processes))


if __name__ == '__main__':
    unittest.main()
