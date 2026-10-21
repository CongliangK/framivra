#!/usr/bin/env python3
"""Opt-in CI inventory and partition measurements, not a merge-evidence gate.

The no-argument shell runner remains the full five-Python compatibility gate.
Every discovery root runs in a fresh interpreter, just like that runner. Test
identity includes that root: different apps can legitimately use the same
``tests.test_foo.Case.test_bar`` ID. Source-scanned media gates are classified
as real; explicit optional integrations stay visible; everything else,
including new tests and safety/process/harness contracts, defaults to fast.
"fast" is a proposed placement, not a measured latency guarantee. No tests
are assigned to slow until measurements justify a reviewed placement.
Only ordinary unmodified stdlib TestSuite containers are supported by the
opt-in selector. Custom suite execution/fixture hooks fail closed; use the
unchanged no-argument runner for their original unittest semantics.

Reports describe local execution only. They do not prove a native operation
occurred, authenticate externally supplied evidence, satisfy PR checks, or
replace ci_checks.py's preflight and strict media jobs. No product environment
flags are changed here. Real/optional partitions reject every skip.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
PARTITIONS = ('fast', 'slow', 'real', 'optional')
PYTHONS = ('3.9', '3.10', '3.11', '3.12', '3.13')
# Capture the supported container implementation before importing any tests.
# Custom load_tests suites can carry validation in run()/__call__ or fixture
# hooks; extracting their leaves would silently discard required behavior.
STANDARD_SUITE = unittest.TestSuite
SUITE_METHODS = {name: getattr(STANDARD_SUITE, name) for name in dir(STANDARD_SUITE)
                 if (not name.startswith('__') or name in ('__iter__', '__call__'))
                 and callable(getattr(STANDARD_SUITE, name))}


class PartitionError(RuntimeError):
    pass


def unique(values, label):
    values = list(values)
    duplicates = [key for key, count in Counter(values).items() if count > 1]
    if duplicates:
        raise PartitionError('duplicate %s: %s' % (label, duplicates))
    return values


def load_json(path):
    def pairs(items):
        unique((key for key, _ in items), 'JSON keys')
        return dict(items)
    return json.loads(Path(path).read_text(encoding='utf-8'),
                      object_pairs_hook=pairs)


def write_json(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                     dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.replace(str(temporary), str(path))
        finally:
            temporary.unlink(missing_ok=True)


def batches(root):
    result = ['tests']
    for family in ('apps', 'plugins', 'templates'):
        result.extend(path.relative_to(root).as_posix()
                      for path in sorted((root / family).glob('*/tests'))
                      if path.is_dir())
    if not (root / 'tests').is_dir():
        raise PartitionError('root tests directory is missing')
    return result


def flatten(suite):
    """Only flatten ordinary stdlib containers; unknown semantics fail closed."""
    if type(suite) is not STANDARD_SUITE or any(
            name in vars(suite) or getattr(type(suite), name) is not method
            for name, method in SUITE_METHODS.items()):
        raise PartitionError('unsupported discovery suite semantics: ' + type(suite).__name__)
    for test in suite:
        if isinstance(test, unittest.TestCase):
            yield test
        else:
            yield from flatten(test)


class OriginLoader(unittest.TestLoader):
    """Keep imported TestCase aliases as distinct legacy discovery instances."""
    def __init__(self):
        super().__init__()
        self.origins = {}

    def loadTestsFromModule(self, module, pattern=None):
        suite = super().loadTestsFromModule(module, pattern=pattern)
        for test in flatten(suite):
            self.origins.setdefault(id(test), module.__name__)
        return suite


def discover_batch(root, batch):
    if batch not in batches(root):
        raise PartitionError('unknown discovery root: ' + batch)
    top = root if batch == 'tests' else (root / batch).parent
    loader = OriginLoader()
    suite = loader.discover(str(root / batch), top_level_dir=str(top))
    tests = list(flatten(suite))
    if loader.errors:
        raise PartitionError('discovery failed in %s: %s' % (batch, loader.errors))
    if not tests:
        raise PartitionError('empty discovery root: ' + batch)
    ids = unique((loader.origins[id(test)] + '::' + test.id() for test in tests),
                 'discovery instances in ' + batch)
    if any(isinstance(test, unittest.loader._FailedTest) for test in tests):
        raise PartitionError('failed test import in ' + batch)
    return suite, dict(zip(ids, tests))


def canonical(batch, test_id):
    return test_id if batch == 'tests' else batch[:-len('/tests')].replace('/', '.') + '.' + test_id


def matches(test_id, selector):
    return test_id == selector or test_id.startswith(selector + '.')


def policy_inventory(discovered, policy, media, strict):
    """Validate preservation and placement without depending on installed tools."""
    if policy.get('schema_version') != 1:
        raise PartitionError('unsupported partition policy schema')
    baseline = policy.get('baseline', {}).get('batches')
    if not isinstance(baseline, dict) or not baseline:
        raise PartitionError('missing protected baseline inventory')
    declared_count = policy['baseline'].get('discovered_count')
    if declared_count is not None and declared_count != sum(len(ids) for ids in baseline.values()):
        raise PartitionError('protected baseline count does not match its instances')
    for batch, expected in baseline.items():
        if not isinstance(expected, list) or not expected:
            raise PartitionError('empty baseline batch: ' + batch)
        unique(expected, 'baseline IDs in ' + batch)
        if batch not in discovered:
            raise PartitionError('missing baseline batch: ' + batch)
        missing = set(expected) - set(discovered[batch])
        if missing:
            raise PartitionError('missing baseline test IDs in %s: %s' % (batch, sorted(missing)))
    if not discovered:
        raise PartitionError('empty discovered inventory')
    records = []
    for batch, ids in discovered.items():
        if not ids:
            raise PartitionError('empty discovered batch: ' + batch)
        for instance in unique(ids, 'discovered instances in ' + batch):
            parts = instance.split('::')
            if len(parts) != 2 or not all(parts):
                raise PartitionError('invalid discovery instance: ' + instance)
            origin, test_id = parts
            records.append({'key': batch + '::' + instance, 'batch': batch,
                            'instance': instance, 'discovery_module': origin,
                            'id': test_id, 'canonical': canonical(batch, test_id),
                            'partition': 'fast', 'reason': 'conservative default; five-Python responsibility retained',
                            'new_since_baseline': instance not in baseline.get(batch, []),
                            'strict_gates': []})
    unique((record['key'] for record in records), 'inventory keys')
    placements = policy.get('placements')
    if not isinstance(placements, list):
        raise PartitionError('placements must be a list')
    rules = list(placements) + [
        {'selector': selector, 'partition': 'real', 'reason': 'source-gated ' + kind}
        for selector, kind in sorted(media.items())]
    selectors = unique((rule.get('selector') for rule in rules), 'placement selectors')
    assigned = {}
    for rule, selector in zip(rules, selectors):
        if not isinstance(selector, str) or not selector.strip():
            raise PartitionError('empty placement selector')
        partition = rule.get('partition')
        if partition not in PARTITIONS or not rule.get('reason'):
            raise PartitionError('invalid placement rule: ' + selector)
        selected = [record for record in records if matches(record['canonical'], selector)]
        if not selected:
            raise PartitionError('stale/empty placement selector: ' + selector)
        for record in selected:
            if record['key'] in assigned:
                raise PartitionError('overlapping placement selectors for ' + record['key'])
            assigned[record['key']] = selector
            record.update(partition=partition, reason=rule['reason'])
    for gate, selectors in strict.items():
        if not selectors:
            raise PartitionError('empty strict gate: ' + gate)
        unique(selectors, 'strict selectors in ' + gate)
        for selector in selectors:
            selected = [record for record in records if matches(record['canonical'], selector)]
            if not selected:
                raise PartitionError('stale/empty strict selector: ' + selector)
            for record in selected:
                if gate not in record['strict_gates']:
                    record['strict_gates'].append(gate)
    return records


def run_process(command, root, timeout):
    """On a bounded worker timeout, stop its process group, not just Python."""
    process = subprocess.Popen(command, cwd=root, start_new_session=os.name == 'posix')
    try:
        return process.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        finally:
            if os.name == 'posix':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
            process.wait()
        raise


def collect(root, timeout=60):
    discovered = {}
    with tempfile.TemporaryDirectory(prefix='vtx-ci-discover-') as directory:
        for index, batch in enumerate(batches(root)):
            output = Path(directory) / ('%d.json' % index)
            command = [sys.executable, str(Path(__file__).resolve()), '--worker', 'discover',
                       '--root', str(root), '--batch', batch, '--report', str(output)]
            code = run_process(command, root, timeout)
            if code != 0 or not output.is_file():
                raise PartitionError('discovery worker failed: %s (exit %s)' % (batch, code))
            data = load_json(output)
            if data.get('batch') != batch or not isinstance(data.get('ids'), list):
                raise PartitionError('invalid discovery receipt: ' + batch)
            discovered[batch] = data['ids']
    return discovered


# A TestCase can report several failed/skipped phases (body, tearDown and
# cleanups). These are not final success/expected-failure callbacks. Subtest
# success is also nonterminal; the parent must still complete independently.
OUTCOME_PRIORITY = {None: -1, 'success': 0, 'expected_failure': 1, 'skip': 2,
                    'unexpected_success': 3, 'failure': 4, 'error': 5}
TERMINAL_OUTCOMES = ('success', 'expected_failure', 'unexpected_success')


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        self.instances = kwargs.pop('instances', {})
        super().__init__(*args, **kwargs)
        self.records = []
        self.active = {}
        self.suite_events = []
        # Hold references so Python cannot reuse a finished subtest's identity.
        # Repeated parameter values are legitimate distinct subtest instances.
        self.subtests = {}

    def startTest(self, test):
        super().startTest(test)
        started = time.monotonic()
        record = {'id': self.instances.get(id(test), test.id()), 'original_id': test.id(),
                  'started': started, 'completed': None,
                  'outcome': None, 'outcomes': [], 'stop_count': 0, 'details': [],
                  'events': [{'kind': 'start', 'time': started}]}
        # Retain duplicate starts as separate records: reconciliation rejects
        # duplicate identities rather than silently replacing the first start.
        self.records.append(record)
        self.active[id(test)] = record

    def stopTest(self, test):
        record = self.active.pop(id(test), None)
        if record is None:
            self.suite_events.append({'id': test.id(), 'kind': 'stop',
                                      'detail': 'stopTest outside active lifecycle'})
        else:
            record['completed'] = time.monotonic()
            record['stop_count'] += 1
            record['events'].append({'kind': 'stop', 'time': record['completed']})
        super().stopTest(test)

    def record(self, test, outcome, detail=None, subtest=None):
        record = self.active.get(id(test))
        if record is None:
            self.suite_events.append({'id': test.id(), 'outcome': outcome, 'detail': detail,
                                      'kind': 'subtest' if subtest is not None else 'outcome'})
            return
        event = {'kind': 'outcome', 'outcome': outcome, 'time': time.monotonic()}
        if subtest is not None:
            token = id(subtest)
            self.subtests.setdefault(token, (subtest, len(self.subtests)))
            event.update(kind='subtest', subtest=self.subtests[token][1])
        record['events'].append(event)
        # Successful subtests do not complete their parent or increment the
        # unittest outcome totals. Every other callback remains visible.
        if subtest is None or outcome != 'success':
            record['outcomes'].append(outcome)
            if OUTCOME_PRIORITY[outcome] > OUTCOME_PRIORITY[record['outcome']]:
                record['outcome'] = outcome
        if detail is not None:
            record['details'].append(detail)

    def addSuccess(self, test):
        self.record(test, 'success')
        super().addSuccess(test)

    def addFailure(self, test, error):
        self.record(test, 'failure', self._exc_info_to_string(error, test))
        super().addFailure(test, error)

    def addError(self, test, error):
        self.record(test, 'error', self._exc_info_to_string(error, test))
        super().addError(test, error)

    def addSkip(self, test, reason):
        # Only an actual stdlib subtest has a parent here. Ordinary TestCases
        # may legitimately use a test_case attribute for their own fixtures.
        if isinstance(test, unittest.case._SubTest):
            self.record(test.test_case, 'skip', reason, subtest=test)
        else:
            self.record(test, 'skip', reason)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, error):
        self.record(test, 'expected_failure', self._exc_info_to_string(error, test))
        super().addExpectedFailure(test, error)

    def addUnexpectedSuccess(self, test):
        self.record(test, 'unexpected_success')
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, error):
        outcome = ('success' if error is None else
                   'failure' if issubclass(error[0], test.failureException) else 'error')
        detail = None if error is None else self._exc_info_to_string(error, subtest)
        if not isinstance(subtest, unittest.case._SubTest) or subtest.test_case is not test:
            self.suite_events.append({'id': test.id(), 'outcome': outcome,
                                      'detail': 'invalid subtest parent'})
        else:
            self.record(test, outcome, detail, subtest=subtest)
        super().addSubTest(test, subtest, error)


def reconcile_events(record):
    """Check ordered ordinary unittest semantics, not hostile-code authenticity."""
    label = record['id']
    start, end = record.get('started'), record.get('completed')
    if (type(record.get('stop_count')) is not int or record['stop_count'] != 1
            or type(start) not in (float, int) or type(end) not in (float, int)
            or not math.isfinite(start) or not math.isfinite(end) or end < start):
        raise PartitionError('selected test not completed: ' + label)
    events = record.get('events')
    if not isinstance(events, list) or len(events) < 3:
        raise PartitionError('missing lifecycle/outcome events: ' + label)
    state, previous = 'new', start
    outcomes, subtests = [], set()
    terminal = None
    for event in events:
        if not isinstance(event, dict):
            raise PartitionError('invalid lifecycle event: ' + label)
        stamp, kind = event.get('time'), event.get('kind')
        if (type(stamp) not in (float, int) or not math.isfinite(stamp)
                or stamp < previous or stamp > end):
            raise PartitionError('invalid lifecycle event time: ' + label)
        previous = stamp
        if kind == 'start' and state == 'new' and stamp == start:
            state = 'active'
        elif kind == 'stop' and state == 'active' and stamp == end:
            state = 'stopped'
        elif kind in ('outcome', 'subtest') and state == 'active':
            outcome = event.get('outcome')
            allowed = ('success', 'failure', 'error', 'skip') if kind == 'subtest' else OUTCOME_PRIORITY
            if outcome is None or outcome not in allowed:
                raise PartitionError('invalid outcome event: ' + label)
            if terminal is not None:
                raise PartitionError('duplicate terminal or callback after terminal outcome: ' + label)
            if kind == 'subtest':
                token = event.get('subtest')
                if type(token) is not int or token < 0 or token in subtests:
                    raise PartitionError('invalid or duplicate subtest outcome: ' + label)
                subtests.add(token)
                if outcome == 'success':
                    continue
            elif outcome in TERMINAL_OUTCOMES:
                if outcomes:
                    raise PartitionError('incompatible terminal outcome callbacks: ' + label)
                terminal = outcome
            outcomes.append(outcome)
        else:
            raise PartitionError('invalid lifecycle event order: ' + label)
    if state != 'stopped':
        raise PartitionError('selected test not completed: ' + label)
    if not outcomes:
        raise PartitionError('missing parent outcome callback: ' + label)
    if record.get('outcomes') != outcomes:
        raise PartitionError('outcome callbacks do not match event log: ' + label)
    if record.get('outcome') != max(outcomes, key=OUTCOME_PRIORITY.get):
        raise PartitionError('unsuccessful or inconsistent outcome summary: ' + label)
    return outcomes


def reconcile(request, receipt, exit_code, strict=False):
    """Fail closed on partial execution; a unittest OK alone is insufficient."""
    for field in ('attempt_id', 'batch', 'selected', 'discovered'):
        if receipt.get(field) != request.get(field):
            raise PartitionError('execution receipt mismatch: ' + field)
    suite_events = receipt.get('suite_events')
    if type(suite_events) is not list:
        raise PartitionError('suite_events must be an explicit list')
    selected = unique(request['selected'], 'selected IDs')
    if not selected:
        raise PartitionError('empty execution selection')
    records = receipt.get('records', [])
    ids = unique((record.get('id') for record in records), 'execution IDs')
    if set(ids) != set(selected):
        raise PartitionError('selected/started mismatch; missing=%s unexpected=%s' % (
            sorted(set(selected) - set(ids)), sorted(set(ids) - set(selected))))
    if type(receipt.get('testsRun')) is not int or receipt['testsRun'] != len(selected):
        raise PartitionError('unittest testsRun does not match selected/started tests')
    observed = Counter()
    for record in records:
        outcomes = reconcile_events(record)
        observed.update(outcomes)
        if any(outcome not in ('success', 'expected_failure', 'skip') for outcome in outcomes):
            raise PartitionError('unsuccessful test: ' + record['id'])
    observed.update(event.get('outcome') for event in suite_events)
    for field, outcome in (('skipped', 'skip'), ('failures', 'failure'), ('errors', 'error'),
                           ('unexpectedSuccesses', 'unexpected_success'), ('expectedFailures', 'expected_failure')):
        if type(receipt.get(field)) is not int or receipt[field] != observed[outcome]:
            raise PartitionError('unittest outcome count mismatch: ' + field)
    if strict and receipt['skipped']:
        raise PartitionError('strict execution skipped tests or subtests')
    if suite_events:
        raise PartitionError('setup/teardown did not complete selected tests: %s' % receipt['suite_events'])
    if exit_code != 0 or receipt.get('unittest_success') is not True:
        raise PartitionError('test worker failed (exit %s)' % exit_code)
    return 'COMPLETE_WITH_SKIPS' if receipt['skipped'] else 'PASS'


def run_reconciled_suite(suite, instances, selected_ids, *, batch, stream=None):
    """Run an already selected in-process strict suite; selection stays with callers."""
    selected_ids = unique(selected_ids, batch + ' test instances')
    if not selected_ids:
        raise PartitionError('empty execution selection')
    def recording_result(*args, **kwargs):
        return RecordingResult(*args, instances=instances, **kwargs)
    result = unittest.TextTestRunner(verbosity=2, stream=stream,
                                     resultclass=recording_result).run(suite)
    # A unittest OK or matching testsRun count does not prove selected bodies
    # completed. Both curated and media entrypoints require the same lifecycle.
    request = dict(attempt_id='in-process', batch=batch,
                   selected=selected_ids, discovered=selected_ids)
    receipt = dict(request, records=result.records, suite_events=result.suite_events,
                   unittest_success=result.wasSuccessful(), testsRun=result.testsRun,
                   skipped=len(result.skipped), failures=len(result.failures),
                   errors=len(result.errors),
                   unexpectedSuccesses=len(result.unexpectedSuccesses),
                   expectedFailures=len(result.expectedFailures))
    return reconcile(request, receipt, 0 if result.wasSuccessful() else 1, strict=True)


def worker(args):
    root = Path(args.root).resolve()
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    suite, found = discover_batch(root, args.batch)
    if args.worker == 'discover':
        write_json(args.report, {'batch': args.batch, 'ids': list(found)})
        return 0
    request = load_json(args.request)
    if request['batch'] != args.batch or request['discovered'] != list(found):
        raise PartitionError('discovery changed between inventory and execution')
    selected = unique(request['selected'], 'worker selected IDs')
    if not selected or not set(selected).issubset(found):
        raise PartitionError('empty or unknown worker selection')
    # Preserve original discovery order and unittest class/module fixtures.
    suite = unittest.TestSuite(found[test_id] for test_id in selected)
    instances = {id(test): instance for instance, test in found.items()}
    def recording_result(*runner_args, **kwargs):
        return RecordingResult(*runner_args, instances=instances, **kwargs)
    result = unittest.TextTestRunner(verbosity=2, resultclass=recording_result).run(suite)
    receipt = dict(request, records=result.records, suite_events=result.suite_events,
                   unittest_success=result.wasSuccessful(), testsRun=result.testsRun,
                   skipped=len(result.skipped), failures=len(result.failures), errors=len(result.errors),
                   unexpectedSuccesses=len(result.unexpectedSuccesses), expectedFailures=len(result.expectedFailures))
    write_json(args.report, receipt)
    return 0 if result.wasSuccessful() else 1


def source_identity(root):
    identity = {'python': platform.python_version(), 'platform': platform.platform(),
                'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    for name, argument in (('commit', 'HEAD'), ('tree', 'HEAD^{tree}')):
        result = subprocess.run(['git', 'rev-parse', '--verify', argument], cwd=root,
                                capture_output=True, text=True)
        identity[name] = result.stdout.strip() if result.returncode == 0 else None
    result = subprocess.run(['git', 'status', '--porcelain'], cwd=root,
                            capture_output=True, text=True)
    identity['dirty'] = bool(result.stdout) if result.returncode == 0 else None
    identity['evidence_use'] = 'local diagnostics only; external merge-evidence consumer UNIMPLEMENTED'
    return identity


def inventory(root):
    policy_path = root / 'scripts/ci_partition_policy.json'
    policy = load_json(policy_path)
    spec = importlib.util.spec_from_file_location('_partition_media', root / 'scripts/ci_checks.py')
    media = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(media)
    # Keep the existing independent source scanner and strict selection source
    # authoritative; no copied list can drift silently from that gate.
    media.check_media_inventory(root)
    discovered = collect(root)
    records = policy_inventory(discovered, policy, media.media_inventory(root), media.MEDIA_TESTS)
    result = {'schema_version': 1, 'source': source_identity(root),
              'policy_sha256': hashlib.sha256(policy_path.read_bytes()).hexdigest(),
              'compatibility': {'versions': list(PYTHONS), 'status': 'unchanged existing full matrix; no relocation approved'},
              'baseline': {key: value for key, value in policy['baseline'].items() if key != 'batches'},
              'batches': discovered, 'tests': records,
              'counts': {partition: sum(record['partition'] == partition for record in records) for partition in PARTITIONS},
              'strict_counts': {gate: sum(gate in record['strict_gates'] for record in records) for gate in media.MEDIA_TESTS}}
    return result


def validate_manifest(root, manifest):
    """Do not let a missing batch or forged summary key omit selected work."""
    discovered = manifest.get('batches')
    if not isinstance(discovered, dict) or set(discovered) != set(batches(root)):
        raise PartitionError('execution manifest does not cover every discovery batch')
    expected = set()
    for batch, instances in discovered.items():
        if not isinstance(instances, list) or not instances:
            raise PartitionError('execution manifest contains an empty batch: ' + batch)
        for instance in unique(instances, 'manifest instances in ' + batch):
            expected.add(batch + '::' + instance)
    records = manifest.get('tests')
    if not isinstance(records, list) or not records:
        raise PartitionError('execution manifest contains no test records')
    actual = unique((record['key'] for record in records), 'manifest test keys')
    for record in records:
        if record['key'] != record['batch'] + '::' + record['instance']:
            raise PartitionError('declared selection key does not match execution instance')
        if record['partition'] not in PARTITIONS:
            raise PartitionError('unknown manifest partition')
    if set(actual) != expected:
        raise PartitionError('manifest test records do not match all discovered instances')


def execute(root, manifest, partition, timeout, strict=False):
    validate_manifest(root, manifest)
    chosen = [record for record in manifest['tests'] if partition == 'all' or record['partition'] == partition]
    if not chosen:
        raise PartitionError('empty partition: ' + partition)
    report = {'schema_version': 1, 'source': manifest['source'], 'policy_sha256': manifest['policy_sha256'],
              'partition': partition, 'strict': strict or partition in ('real', 'optional'),
              'selected': [record['key'] for record in chosen], 'attempts': [], 'status': 'RUNNING'}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='vtx-ci-run-') as directory:
        for index, (batch, discovered) in enumerate(manifest['batches'].items()):
            selected = [record['instance'] for record in chosen if record['batch'] == batch]
            if not selected:
                continue
            request = {'attempt_id': str(uuid.uuid4()), 'batch': batch, 'selected': selected, 'discovered': discovered}
            request_path = Path(directory) / ('request-%d.json' % index)
            receipt_path = Path(directory) / ('receipt-%d.json' % index)
            write_json(request_path, request)
            attempt = dict(request, status='RUNNING', exit_code=None)
            report['attempts'].append(attempt)
            begin = time.monotonic()
            try:
                code = run_process([sys.executable, str(Path(__file__).resolve()), '--worker', 'run',
                                    '--root', str(root), '--batch', batch, '--request', str(request_path),
                                    '--report', str(receipt_path)], root, timeout)
                attempt['exit_code'] = code
                if not receipt_path.is_file():
                    raise PartitionError('missing worker receipt')
                receipt = load_json(receipt_path)
                attempt['receipt'] = receipt
                attempt['status'] = reconcile(request, receipt, code, report['strict'])
            except subprocess.TimeoutExpired:
                attempt.update(status='TIMED_OUT', error='batch exceeded %.3fs' % timeout)
            except KeyboardInterrupt:
                attempt.update(status='CANCELLED', error='interrupted')
                report['status'] = 'CANCELLED'
                break
            except (PartitionError, OSError, ValueError, TypeError, KeyError) as error:
                attempt.update(status='FAIL', error=str(error))
            finally:
                attempt['elapsed_seconds'] = time.monotonic() - begin
    report['elapsed_seconds'] = time.monotonic() - started
    statuses = [attempt['status'] for attempt in report['attempts']]
    if report['status'] != 'CANCELLED':
        attempted = [attempt['batch'] + '::' + instance
                     for attempt in report['attempts'] for instance in attempt['selected']]
        if not statuses or Counter(attempted) != Counter(report['selected']):
            report.update(status='FAIL', error='selected/attempted test instances do not reconcile')
            return report
        report['status'] = ('FAIL' if any(status not in ('PASS', 'COMPLETE_WITH_SKIPS') for status in statuses)
                            else 'COMPLETE_WITH_SKIPS' if 'COMPLETE_WITH_SKIPS' in statuses else 'PASS')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    select = parser.add_mutually_exclusive_group()
    select.add_argument('--inventory', action='store_true', help='discover all batches and report placements; do not run test bodies')
    select.add_argument('--partition', choices=('all',) + PARTITIONS)
    parser.add_argument('--report', help='write structured local diagnostic JSON')
    parser.add_argument('--strict', action='store_true', help='reject skips (always enabled for real/optional)')
    parser.add_argument('--timeout', type=float, default=1200, help='per execution batch deadline in seconds')
    parser.add_argument('--worker', choices=('discover', 'run'), help=argparse.SUPPRESS)
    parser.add_argument('--root', default=str(ROOT), help=argparse.SUPPRESS)
    parser.add_argument('--batch', help=argparse.SUPPRESS)
    parser.add_argument('--request', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.worker:
            return worker(args)
        if args.timeout <= 0 or not math.isfinite(args.timeout):
            raise PartitionError('timeout must be finite and positive')
        if not args.inventory and args.partition is None:
            parser.error('choose --inventory or --partition; no-argument full run is bash scripts/run_all_tests.sh')
        manifest = inventory(ROOT)
        report = manifest if args.inventory else execute(ROOT, manifest, args.partition, args.timeout, args.strict)
        if args.report:
            write_json(args.report, report)
        if args.inventory:
            print('Discovered %d tests in %d batches: %s; strict selections: %s' % (
                len(manifest['tests']), len(manifest['batches']), manifest['counts'], manifest['strict_counts']))
        else:
            print('%s: %s; %.3fs; %d selected' % (args.partition, report['status'], report['elapsed_seconds'], len(report['selected'])))
        return 0 if args.inventory or report['status'] in ('PASS', 'COMPLETE_WITH_SKIPS') else 1
    except (PartitionError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print('CI partition failed: %s' % error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
