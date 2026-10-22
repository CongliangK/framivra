"""Event production, lifecycle invariants and owned executor contexts.

CPU synthetic tests, not proof of native renderer cancellation/cleanup."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from vtx import cli
from vtx.runtime import events, execution_links
from vtx.inspection import qc
from vtx.media import encoders
from tests.execution import test_execution_links
from vtx.infra import Ctx, run_ffmpeg_task, run_parallel, run_wave


class Buffer(io.StringIO):
    def close(self):
        pass


def fixture(root):
    path = root / 'timeline.json'
    path.write_text(json.dumps({'version': 2,
        'project': {'fps': 30, 'width': 64, 'height': 64, 'duration_frames': 30},
        'sources': [], 'video_tracks': [], 'audio_tracks': [], 'subtitles': []}))
    return path


class EventsTests(unittest.TestCase):
    def execution(self, **kw):
        self.stream = Buffer()
        self.execution_context = events.ExecutionContext(self.stream, **kw)
        return self.execution_context

    def rows(self, code=0):
        self.execution_context.finish(code)
        result = events.read_events(self.stream.getvalue())
        self.assertTrue(result['complete'])
        return result['events']

    def test_parallel_serialization_identity_and_explicit_parent(self):
        execution = self.execution()
        parent = execution.task('prepare', kind='stage')
        with parent.scope():
            tasks = [execution.task(metadata={'same_content': True}) for _ in range(20)]
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda t: t.call(lambda: sum(range(1000))), tasks))
        rows = self.rows()
        self.assertEqual([r['seq'] for r in rows], list(range(1, len(rows) + 1)))
        starts = [r for r in rows if r['event'] == 'task_start']
        self.assertEqual(len({r['task_id'] for r in starts}), 21)
        self.assertTrue(all(r['parent_task_id'] == parent.task_id for r in starts[1:]))
        self.assertTrue(all(r['stage'] == 'prepare' for r in starts))
        self.assertEqual(sum(r['event'] == 'task_terminal' for r in rows), 21)

    def test_clock_wall_time_not_task_sum_and_null_resources(self):
        tick = [0.0]
        execution = self.execution(clock=lambda: tick[0])
        t1, t2 = execution.task(), execution.task()
        with t1.scope(), t2.scope():
            tick[0] = 0.25
        rows = self.rows()
        terminal = rows[-1]
        self.assertEqual(terminal['monotonic_duration'], 250)
        self.assertEqual(sum(r['monotonic_duration'] or 0 for r in rows[1:-1]), 500)
        for value in terminal['resources'].values():
            self.assertIsNone(value['value'])
            self.assertEqual(value['unavailable_reason'], 'not_measured')
        self.assertIsNone(rows[0]['monotonic_duration'])

    def test_retry_attempt_costs_one_terminal_and_privacy(self):
        execution = self.execution()
        task = execution.task('video')
        with task.scope():
            first = task.attempt(lambda: SimpleNamespace(returncode=1, stderr='SECRET /tmp/private'))
            second = task.attempt(lambda: SimpleNamespace(returncode=0))
        rows = self.rows()
        attempts = [r for r in rows if r['event'] == 'attempt_terminal']
        self.assertEqual([(r['attempt'], r['outcome']) for r in attempts], [(1, 'failed'), (2, 'succeeded')])
        self.assertEqual(sum(r['event'] == 'task_terminal' for r in rows), 1)
        self.assertNotIn('SECRET', self.stream.getvalue())
        self.assertNotIn('/tmp/private', self.stream.getvalue())

    def test_sink_failure_warns_once_without_changing_task_result(self):
        class Broken(Buffer):
            def write(self, text):
                raise OSError('SECRET')
        with contextlib.redirect_stderr(io.StringIO()) as output:
            execution = events.ExecutionContext(Broken())
            self.assertEqual(execution.task().call(lambda: 42), 42)
            execution.finish(0)
        self.assertTrue(execution.incomplete)
        self.assertEqual(output.getvalue().count('incomplete'), 1)
        self.assertNotIn('SECRET', output.getvalue())

    def test_replay_duplicate_conflict_gaps_truncation(self):
        self.execution(); self.rows()
        text = self.stream.getvalue()
        lines = text.splitlines(True)
        self.assertTrue(events.read_events(lines[0] + lines[0] + lines[1], replay=True)['complete'])
        with self.assertRaises(ValueError):
            events.read_events(lines[0] + lines[0] + lines[1])
        changed = json.loads(lines[0]); changed['extensions']['changed'] = True
        with self.assertRaises(ValueError):
            events.read_events(lines[0] + json.dumps(changed) + '\n' + lines[1], replay=True)
        self.assertFalse(events.read_events(lines[0] + '{"schema')['complete'])
        self.assertFalse(events.read_events(lines[0])['complete'])
        self.assertFalse(events.read_events(text.rstrip())['complete'])
        for data in ('{broken}', '{"x":1,"x":2}', 'Infinity', '-Infinity'):
            with self.assertRaisesRegex(ValueError, 'invalid event JSON'):
                events.read_events(lines[0] + data + '\n')

    def test_resume_does_not_reuse_identity(self):
        one = events.ExecutionContext()
        two = events.ExecutionContext(resumed_from_run_id=one.run_id)
        self.assertNotEqual(one.run_id, two.run_id)
        self.assertEqual(two.resumed_from_run_id, one.run_id)
        with self.assertRaises(ValueError):
            events.ExecutionContext(resumed_from_run_id='../bad')

    def test_receipt_postcommit_reference_and_failed_transaction(self):
        with tempfile.TemporaryDirectory() as temp:
            execution = self.execution()
            c = Ctx(fixture(Path(temp)), execution=execution)
            c.draft = False
            cli._observed_stage(c, 'validate', lambda c: c.receipt('validate', True))
            ref = json.loads((c.out / 'receipt.json').read_text())['stages']['validate']['observed_by']
            before = execution.seq
            with execution.task('prepare', kind='stage').scope():
                with patch('vtx.infra.fstxn.locked_update', side_effect=OSError('SECRET')):
                    with self.assertRaises(OSError):
                        c.receipt('prepare', True)
            rows = self.rows()
            event = next(r for r in rows if r['event_id'] == ref['event_id'])
            self.assertEqual(event['event'], 'receipt_commit')
            self.assertEqual(event['task_id'], ref['task_id'])
            self.assertEqual(sum(r['event'] == 'receipt_commit' for r in rows), 1)

    def test_concurrent_stage_receipts_remain_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            execution = self.execution()
            c = Ctx(fixture(Path(temp)), execution=execution)
            tasks = [execution.task(name, kind='stage') for name in ('gfx', 'audio')]
            with ThreadPoolExecutor(2) as pool:
                list(pool.map(lambda t: t.call(c.receipt, t.stage, True), tasks))
            rows = self.rows()
            stages = json.loads((c.out / 'receipt.json').read_text())['stages']
            for name in ('gfx', 'audio'):
                ref = stages[name]['observed_by']
                row = next(r for r in rows if r['event_id'] == ref['event_id'])
                self.assertEqual(row['stage'], name)
            self.assertNotEqual(stages['gfx']['observed_by']['task_id'], stages['audio']['observed_by']['task_id'])

    def test_cache_identity_unchanged_and_old_producer_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            path = fixture(Path(temp))
            a = Ctx(path)
            execution = self.execution()
            b = Ctx(path, execution=execution)
            self.assertEqual(a.input_fingerprint, b.input_fingerprint)
            events.cache_hit(b, 'media_cache_valid')
            row = next(r for r in self.rows() if r['event'] == 'cache_hit')
            self.assertIsNone(row['extensions']['producer'])
            self.assertEqual(row['extensions']['producer_reason'], 'legacy_unknown')

    def test_worker_actual_attempts_and_existing_failure_selection(self):
        execution = self.execution()
        c = SimpleNamespace(execution=execution, dry=False)
        with patch('vtx.infra.subprocess.run', return_value=SimpleNamespace(returncode=1, stderr='secret')), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                run_parallel(c, [('same', ['fake']), ('same', ['fake'])], 'fixture')
        rows = self.rows(1)
        self.assertEqual(sum(r['event'] == 'attempt_start' for r in rows), 2)
        self.assertEqual(len({r['task_id'] for r in rows if r['event'] == 'task_start'}), 2)

    def test_wave_waits_for_other_job_no_fabricated_cancellation(self):
        execution = self.execution()
        c = SimpleNamespace(execution=execution)
        ran = []
        def fail():
            raise RuntimeError('first')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                run_wave(c, [('first', fail), ('other', lambda: ran.append(True))])
        rows = self.rows(1)
        self.assertEqual(ran, [True])
        self.assertFalse(any(r['event'] in ('cancel_requested', 'cancelled') for r in rows))

    def test_synthetic_cancellation_owner_observations(self):
        for terminal, outcome in (('cancelled', 'cancelled'), ('cleanup_failed', 'failed')):
            execution = self.execution()
            with self.assertRaises(ValueError):
                execution.observe_cancellation(terminal)
            execution.observe_cancellation('cancel_requested')
            self.assertFalse(events.read_events(self.stream.getvalue())['complete'])
            execution.observe_cancellation(terminal)
            rows = self.rows(130)
            self.assertEqual(rows[-1]['outcome'], outcome)
            self.assertEqual([r['event'] for r in rows[1:-1]], ['cancel_requested', terminal])

    def test_diagnostic_hook_exports_only_rule_id(self):
        execution = self.execution()
        execution.diagnostic('INPUT.MISSING')
        with self.assertRaises(ValueError):
            execution.diagnostic('/private/path secret')
        row = next(r for r in self.rows() if r['event'] == 'diagnostic')
        self.assertEqual(row['extensions'], {'rule_id': 'INPUT.MISSING'})

    def test_partial_cdp_attempt_is_failure_not_fresh_success(self):
        execution = self.execution()
        with execution.task('gfx').scope() as block:
            block.attempt(lambda: {'failed': [2, 4]}, event_metadata={'backend': 'cdp'})
            child = execution.task('gfx', metadata={'backend': 'cli', 'frames': [2, 4]})
            with child.scope():
                child.attempt(lambda: 2)
        rows = self.rows()
        attempts = [r for r in rows if r['event'] == 'attempt_terminal']
        self.assertEqual([r['outcome'] for r in attempts], ['failed', 'succeeded'])
        self.assertEqual(attempts[1]['parent_task_id'], block.task_id)
        self.assertEqual(attempts[1]['extensions']['frames'], [2, 4])

    def test_qc_dependency_failure_does_not_get_caught_as_producer_failure(self):
        execution = self.execution()
        c = SimpleNamespace(execution=execution, draft=False,
                            begin_stage=lambda stage: (_ for _ in ()).throw(RuntimeError('dependency')))
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            cli._observed_stage(c, 'qc', lambda c: None, capture_exit=True)
        self.rows(1)


class CLIEventsTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'vtx', *map(str, args)],
                              capture_output=True, text=True)

    def test_loading_failure_human_parity_with_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            missing = root / 'missing.json'
            baseline = self.run_cli('-t', missing, 'validate')
            observed = self.run_cli('-t', missing, '--events-jsonl', root / 'events.jsonl', 'validate')
            self.assertEqual((observed.returncode, observed.stdout, observed.stderr),
                             (baseline.returncode, baseline.stdout, baseline.stderr))
            parsed = events.read_events((root / 'events.jsonl').read_bytes())
            self.assertTrue(parsed['complete'])
            self.assertEqual(parsed['exit_code'], 1)

    def test_dry_and_readonly_do_not_create_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); timeline = fixture(root)
            before = sorted(p.name for p in root.iterdir())
            for command in (['validate', '--dry-run'], ['df']):
                result = self.run_cli('-t', timeline, '--events-jsonl', root / 'e.jsonl', *command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(sorted(p.name for p in root.iterdir()), before)

    def test_bridge_observes_loading_failure_before_ctx(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); timeline = root / 'timeline.json'; timeline.write_text('{bad')
            execution_links.initialize(root)
            request = execution_links.prepare_request(root, str(uuid.uuid4()), 0)
            result = self.run_cli('-t', timeline, 'all', '--until', 'mux')
            self.assertEqual(result.returncode, 1)
            parsed = events.read_events(execution_links.response_bytes(root, request))
            self.assertTrue(parsed['complete'])
            self.assertEqual(parsed['events'][0]['extensions']['observation'], request)
            self.assertEqual(parsed['exit_code'], 1)


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
             patch('vtx.inspection.qc.loudness_cache.lookup', return_value=None), \
             patch('vtx.inspection.qc.loudness_cache.measure_and_store', side_effect=measure), \
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


if __name__ == "__main__":
    unittest.main()
