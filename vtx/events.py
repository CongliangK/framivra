"""Optional local execution observations; content identities never depend on these IDs.

A sink owns one invocation and one writer lock. Contexts are explicitly captured
before thread dispatch; no mutable stage lives on the shared rendering context.
This is observation, not a cancellation owner or an authenticity mechanism.
"""
import contextlib
import contextvars
import json
import math
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path

SCHEMA = 'vtx.events/1'
EVENTS = frozenset(('run_start', 'run_terminal', 'task_start', 'task_terminal',
                    'attempt_start', 'attempt_terminal', 'receipt_commit',
                    'cache_hit', 'retry_requested', 'diagnostic',
                    'cancel_requested', 'cancelled', 'cleanup_failed', 'progress'))
STAGES = frozenset(('validate', 'audit', 'prepare', 'gfx', 'video', 'audio', 'mux',
                    'qc', 'finish', 'video_draft', 'mux_draft', 'load'))
_ACTIVE = contextvars.ContextVar('vtx_execution_task', default=None)


def canonical_uuid(value):
    return isinstance(value, str) and str(uuid.UUID(value)) == value


def exit_code(error):
    if isinstance(error, SystemExit):
        return int(error.code) if isinstance(error.code, int) else (1 if error.code else 0)
    return 130 if isinstance(error, KeyboardInterrupt) else 1


# Serialization limits are schema limits, not claims about hardware capacity.
MAX_NUMBER = 2 ** 53 - 1
MAX_BYTES = 2 ** 63 - 1
MAX_FRAME = 2 ** 31 - 1
MAX_FRAME_ITEMS = 4096
MAX_EVENT_CHARS = 131072
MAX_CAUSES = 8
SCOPES = frozenset(('run', 'task', 'stage', 'attempt', 'cache', 'process', 'ffmpeg',
                    'wave_job', 'preencode', 'gfx_block', 'gfx_fallback', 'video_segment', 'load'))
ERROR_TYPES = frozenset(('SystemExit', 'KeyboardInterrupt', 'RuntimeError', 'ValueError',
                        'TypeError', 'KeyError', 'OSError', 'FileNotFoundError',
                        'PermissionError', 'InputIdentityError', 'ProjectPathError',
                        'JSONDecodeError', 'UnicodeDecodeError', 'ZeroDivisionError',
                        'TimeoutError', 'Exception'))
RESOURCE_UNITS = {'cpu': 'ms', 'rss': 'bytes', 'gpu': 'ms', 'vram': 'bytes'}


def safe_error(error):
    if error is None:
        return None
    if not isinstance(error, BaseException):
        return {'type': 'Exception', 'message': '[redacted]', 'rule_id': None,
                'causes': [], 'causes_truncated': False}
    # Only fixed type labels, including bounded explicit/implicit cause chains.
    # Never inspect repr/str, exception arguments, paths or diagnostic messages.
    seen, types = set(), []
    while error is not None and id(error) not in seen and len(types) < MAX_CAUSES:
        seen.add(id(error))
        name = type(error).__name__
        types.append(name if name in ERROR_TYPES else 'Exception')
        cause = error.__cause__
        error = cause if cause is not None else (None if error.__suppress_context__ else error.__context__)
    return {'type': types[0], 'message': '[redacted]', 'rule_id': None,
            'causes': types[1:], 'causes_truncated': error is not None}


def _number(value, maximum=MAX_NUMBER):
    return type(value) in (int, float) and 0 <= value <= maximum and math.isfinite(value)


def _integer(value, maximum=MAX_NUMBER):
    return type(value) is int and 0 <= value <= maximum


def _id(value):
    try:
        return canonical_uuid(value)
    except (ValueError, TypeError, AttributeError):
        return False


def _frame_range(value):
    return (isinstance(value, list) and len(value) == 2 and
            all(_integer(v, MAX_FRAME) for v in value) and value[0] < value[1])


def frame_metadata(frames):
    values = list(frames)
    if not values:
        return {'frames': []}
    if all(b == a + 1 for a, b in zip(values, values[1:])):
        return {'frame_range': [values[0], values[-1] + 1]}
    if len(values) <= MAX_FRAME_ITEMS:
        return {'frames': values}
    ranges = []
    for value in values:
        if ranges and ranges[-1][1] == value:
            ranges[-1][1] += 1
        else:
            ranges.append([value, value + 1])
    return {'frame_ranges': ranges}


def _metadata(event, metadata, *, strict):
    """One allowlist at production and replay boundaries; no arbitrary namespaces.

    Unknown/invalid producer fields are dropped with a fixed redaction marker;
    readers reject them rather than accepting a forged complete-evidence claim.
    """
    geometry = {'page_id', 'backend', 'frame_range', 'frames', 'frame_ranges'}
    allowed = {
        'run_start': {'observation', 'resumed_from_run_id', 'replay_of_run_id'},
        'run_terminal': {'evidence_complete'},
        'task_start': geometry, 'attempt_start': geometry, 'attempt_terminal': geometry,
        'cache_hit': {'validator', 'producer', 'producer_reason', 'frame_range'},
        'receipt_commit': {'receipt_stage'}, 'diagnostic': {'rule_id'},
        'cancel_requested': {'source'}, 'cancelled': {'source'}, 'cleanup_failed': {'source'},
        'progress': {'completed', 'total'},
    }.get(event, set()) | {'metadata_redacted'}
    if not isinstance(metadata, dict):
        if strict:
            raise ValueError('invalid event metadata')
        return {'metadata_redacted': True}
    result, redacted = {}, False
    for key, value in metadata.items():
        valid = key in allowed
        if valid:
            if key == 'observation':
                try:
                    if value is not None:
                        from .execution_links import validate_request
                        validate_request(value)
                except (ValueError, TypeError, AttributeError):
                    valid = False
            elif key in ('resumed_from_run_id', 'replay_of_run_id'):
                valid = value is None or _id(value)
            elif key == 'page_id':
                valid = _id(value)
            elif key == 'backend':
                valid = value in ('cdp', 'cli', 'cef', 'electron')
            elif key == 'frame_range':
                valid = value is None or _frame_range(value)
            elif key == 'frames':
                valid = (isinstance(value, list) and len(value) <= MAX_FRAME_ITEMS and
                         all(_integer(v, MAX_FRAME) for v in value) and
                         all(a < b for a, b in zip(value, value[1:])))
            elif key == 'frame_ranges':
                valid = (isinstance(value, list) and len(value) <= MAX_FRAME_ITEMS and
                         all(_frame_range(v) for v in value) and
                         all(a[1] <= b[0] for a, b in zip(value, value[1:])))
            elif key == 'receipt_stage':
                valid = isinstance(value, str) and value in STAGES
            elif key == 'validator':
                valid = value in ('inspect_page', 'verify_frames', 'cached_runs', 'media_cache_valid', 'audio_cache_valid')
            elif key == 'producer':
                valid = value is None
            elif key == 'producer_reason':
                valid = value == 'legacy_unknown'
            elif key == 'rule_id':
                valid = isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', value) is not None
            elif key == 'source':
                valid = value == 'owner_observation'
            elif key in ('completed', 'total'):
                valid = _integer(value)
            else:
                valid = type(value) is bool
        if not valid:
            if strict:
                raise ValueError('unsupported or invalid event metadata')
            redacted = True
        else:
            # Copy bounded validated containers so caller mutation cannot race serialization.
            result[key] = json.loads(json.dumps(value, allow_nan=False))
    if redacted:
        result['metadata_redacted'] = True
    if 'completed' in result and 'total' in result and result['completed'] > result['total']:
        if strict:
            raise ValueError('invalid progress counters')
        return {'metadata_redacted': True}
    return result


def _validate_values(row):
    if not isinstance(row['event'], str) or row['event'] not in EVENTS:
        raise ValueError('invalid event vocabulary')
    if not isinstance(row['scope'], str) or row['scope'] not in SCOPES:
        raise ValueError('invalid event scope')
    if row['outcome'] is not None and (not isinstance(row['outcome'], str) or
            row['outcome'] not in ('succeeded', 'failed', 'cancelled')):
        raise ValueError('invalid event outcome')
    if row['exit_code'] is not None and (type(row['exit_code']) is not int or
            not -(2 ** 31) <= row['exit_code'] < 2 ** 31):
        raise ValueError('invalid exit code')
    if not _id(row['run_id']) or not _integer(row['seq']) or row['seq'] < 1:
        raise ValueError('invalid event identity')
    if row['stage'] is not None and (not isinstance(row['stage'], str) or row['stage'] not in STAGES):
        raise ValueError('invalid stage')
    if row['task_id'] is None:
        if row['scope'] != 'run' or row['attempt'] is not None or row['parent_task_id'] is not None or row['stage'] is not None:
            raise ValueError('invalid run attribution')
    elif (row['scope'] == 'run' or not _id(row['task_id']) or
          not _integer(row['attempt']) or row['attempt'] < 1 or
          row['parent_task_id'] is not None and not _id(row['parent_task_id'])):
        raise ValueError('invalid task attribution')
    terminal = row['event'] in ('run_terminal', 'task_terminal', 'attempt_terminal')
    if not terminal and row['exit_code'] is not None:
        raise ValueError('nonterminal event has exit code')
    if not terminal and row['event'] != 'receipt_commit' and (row['outcome'] is not None or row['error'] is not None):
        raise ValueError('nonterminal event has terminal outcome')


def _validate_measurements(row):
    timing = row['timing']
    fields = {'offset_ms', 'start_ms', 'unit', 'method', 'resolution_seconds', 'unavailable_reason'}
    if not isinstance(timing, dict) or set(timing) != fields or timing['unit'] != 'ms' or timing['method'] != 'monotonic':
        raise ValueError('invalid event timing')
    offset, start, duration, resolution = (timing['offset_ms'], timing['start_ms'],
                                         row['monotonic_duration'], timing['resolution_seconds'])
    if not _number(offset) or not _number(resolution, 1) or resolution == 0:
        raise ValueError('invalid clock measurement')
    if start is not None and (not _number(start) or start > offset):
        raise ValueError('invalid monotonic start')
    terminal = row['event'] in ('run_terminal', 'task_terminal', 'attempt_terminal')
    if duration is None:
        if terminal or timing['unavailable_reason'] != 'not_terminal':
            raise ValueError('missing terminal duration')
    elif (not terminal or not _number(duration) or start is None or
          timing['unavailable_reason'] is not None or
          not math.isclose(duration, offset - start, rel_tol=1e-12,
                           abs_tol=max(1e-6, resolution * 4000))):
        raise ValueError('contradictory monotonic duration')
    resources = row['resources']
    if not isinstance(resources, dict) or set(resources) != set(RESOURCE_UNITS):
        raise ValueError('invalid resource measurements')
    for name, unit in RESOURCE_UNITS.items():
        value = resources[name]
        if not isinstance(value, dict) or set(value) != {'value', 'unit', 'method', 'scope', 'unavailable_reason'}:
            raise ValueError('invalid resource measurement fields')
        if value['unit'] != unit or value['scope'] not in ('process', 'task', 'run'):
            raise ValueError('invalid resource measurement unit/scope')
        if value['value'] is None:
            if value['method'] is not None or value['unavailable_reason'] not in ('not_measured', 'not_supported', 'unavailable'):
                raise ValueError('invalid unavailable resource measurement')
        elif (not (_integer(value['value'], MAX_BYTES) if unit == 'bytes' else _number(value['value'])) or
              value['method'] not in ('process_time', 'getrusage', 'resource_snapshot', 'owner_observation') or
              value['unavailable_reason'] is not None):
            raise ValueError('invalid measured resource value')


def _validate_error(error):
    if error is None:
        return
    if not isinstance(error, dict) or set(error) not in (
            {'type', 'message', 'rule_id'},
            {'type', 'message', 'rule_id', 'causes', 'causes_truncated'}):
        raise ValueError('invalid structured error fields')
    if (not isinstance(error['type'], str) or error['type'] not in ERROR_TYPES or
            error['message'] != '[redacted]' or error['rule_id'] is not None):
        raise ValueError('invalid structured error value')
    if 'causes' in error:
        if (not isinstance(error['causes'], list) or len(error['causes']) >= MAX_CAUSES or
                any(not isinstance(v, str) or v not in ERROR_TYPES for v in error['causes']) or
                type(error['causes_truncated']) is not bool):
            raise ValueError('invalid structured error causes')


def open_sink(path):
    """Explicit destination only; never overwrite, append, or create parent dirs."""
    path = Path(os.path.abspath(path))
    from .execution_links import _directory
    parent = _directory(path.parent)
    try:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW, 0o600, dir_fd=parent)
        return os.fdopen(fd, 'w', encoding='utf-8')
    finally:
        os.close(parent)


def observation_warning(message):
    """Best-effort observation diagnostics must never replace business results.

    Callers supply fixed, private-data-free text. Process-control exceptions are
    deliberately not caught; a failed ordinary stderr write has no fallback I/O.
    """
    try:
        print(message, file=sys.stderr)
    except Exception:
        pass


class ExecutionContext:
    def __init__(self, stream=None, request=None, *, resumed_from_run_id=None,
                 replay_of_run_id=None, clock=time.monotonic):
        for value in (resumed_from_run_id, replay_of_run_id):
            if value is not None and not canonical_uuid(value):
                raise ValueError('invalid predecessor UUID')
        self.run_id = str(uuid.uuid4())
        self.resumed_from_run_id = resumed_from_run_id
        self.replay_of_run_id = replay_of_run_id
        if not callable(clock):
            raise ValueError('invalid observation clock configuration')
        self._clock = clock
        self._last_clock = None
        self.lock = threading.RLock()
        self.seq = 0
        self.stream = None
        self.incomplete = False
        self.finished = False
        self._warned = False
        self.request = None
        self.cancellation = None
        self.origin = self.clock()
        if stream is not None:
            self.activate(stream, request)

    def clock(self):
        """Sample without inventing timings or disrupting ordinary task results.

        Serialize all reads, including task/attempt starts, so a backwards sample
        cannot hide behind a later valid event. Failure is sticky for this run.
        """
        with self.lock:
            if self.incomplete or self.finished:
                return None
            try:
                value = self._clock()
                if (type(value) not in (int, float) or not math.isfinite(value) or
                        self._last_clock is not None and value < self._last_clock):
                    raise ValueError('invalid monotonic observation')
            except Exception:
                self.incomplete = True
                if self.stream is not None:
                    self._warn()
                return None
            self._last_clock = value
            return value

    @property
    def enabled(self):
        return self.stream is not None and not self.incomplete and not self.finished

    def _warn(self):
        self.incomplete = True
        if not self._warned:
            self._warned = True
            observation_warning('Warning: execution evidence incomplete; rendering status unchanged.')

    def activate(self, stream, request=None):
        if self.stream is not None or self.finished:
            raise ValueError('execution sink already selected')
        self.stream, self.request = stream, request
        if self.incomplete:
            self._warn()
        self.emit('run_start', started=self.origin, extensions={'observation': request,
                  'resumed_from_run_id': self.resumed_from_run_id,
                  'replay_of_run_id': self.replay_of_run_id})

    def emit(self, event, *, task=None, scope=None, attempt=None, started=None,
             outcome=None, error=None, exit_code=None, extensions=None):
        if not self.enabled:
            return None
        with self.lock:
            if not self.enabled:
                return None
            if not isinstance(event, str) or event not in EVENTS:
                self._warn()
                return None
            now = self.clock()
            if now is None:
                return None
            try:
                if started is not None and (type(started) not in (int, float) or
                        not math.isfinite(started) or not self.origin <= started <= now):
                    raise ValueError('invalid observation start')
                offset = (now - self.origin) * 1000
                start_offset = None if started is None else (started - self.origin) * 1000
                duration = (None if started is None or event.endswith('_start') else
                            (now - started) * 1000)
            except Exception:
                self._warn()
                return None
            seq = self.seq + 1
            row = {'schema_version': SCHEMA, 'run_id': self.run_id, 'seq': seq,
                   'event_id': [self.run_id, seq], 'event': event,
                   'scope': scope or ('task' if task else 'run'),
                   'stage': task.stage if task else None,
                   'task_id': task.task_id if task else None,
                   'parent_task_id': task.parent_task_id if task else None,
                   'attempt': attempt if attempt is not None else (1 if task else None),
                   'monotonic_duration': duration, 'outcome': outcome,
                   'exit_code': exit_code, 'error': safe_error(error) if outcome != 'succeeded' else None,
                   'timing': {'offset_ms': offset, 'start_ms': start_offset,
                              'unit': 'ms', 'method': 'monotonic',
                              'resolution_seconds': time.get_clock_info('monotonic').resolution,
                              'unavailable_reason': 'not_terminal' if duration is None else None},
                   'resources': {name: {'value': None, 'unit': unit,
                                 'method': None, 'scope': 'process',
                                 'unavailable_reason': 'not_measured'} for name, unit in
                                 (('cpu', 'ms'), ('rss', 'bytes'), ('gpu', 'ms'), ('vram', 'bytes'))},
                   'extensions': _metadata(event, extensions or {}, strict=False)}
            try:
                _validate_values(row)
                _validate_measurements(row)
                _validate_error(row['error'])
                payload = json.dumps(row, ensure_ascii=True, allow_nan=False,
                                     separators=(',', ':')) + '\n'
                if len(payload) > MAX_EVENT_CHARS:
                    raise ValueError('oversized event')
                offset_written = 0
                # TextIO's contract counts characters, not encoded bytes. Preserve
                # one writer lock across bounded retries; no seq until full flush.
                for _ in range(128):
                    count = self.stream.write(payload[offset_written:])
                    if type(count) is not int or not 0 < count <= len(payload) - offset_written:
                        raise OSError('invalid or stalled event write')
                    offset_written += count
                    if offset_written == len(payload):
                        break
                else:
                    raise OSError('event write retry budget exhausted')
                self.stream.flush()
            except Exception:
                self._warn()
                return None
            self.seq = seq
            return {'run_id': self.run_id, 'task_id': row['task_id'],
                    'event_id': row['event_id']}

    def task(self, stage=None, *, parent=None, kind='task', metadata=None):
        if parent is None:
            current = _ACTIVE.get()
            parent = current if current is not None and current.execution is self else None
        return Task(self, stage if stage is not None else (parent.stage if parent else None),
                    parent, kind, metadata)

    def diagnostic(self, rule_id):
        """Adapter for a future diagnostics producer; no free-text metadata export."""
        if not isinstance(rule_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', rule_id):
            raise ValueError('invalid diagnostic rule ID')
        current_task = _ACTIVE.get()
        self.emit('diagnostic', task=current_task if current_task is not None and
                  current_task.execution is self else None,
                  extensions={'rule_id': rule_id})

    def observe_cancellation(self, state):
        """Record an owner's observation, never request or perform process cleanup."""
        if state not in ('cancel_requested', 'cancelled', 'cleanup_failed'):
            raise ValueError('invalid cancellation observation')
        if state in ('cancelled', 'cleanup_failed') and self.cancellation != 'cancel_requested':
            raise ValueError('cancellation requires a preceding owner request observation')
        if self.cancellation in ('cancelled', 'cleanup_failed'):
            raise ValueError('cancellation already terminal')
        self.cancellation = state
        self.emit(state, extensions={'source': 'owner_observation'})

    def finish(self, code=0, error=None):
        code = int(code) if type(code) is bool else code
        with self.lock:
            if self.finished:
                return
            self.emit('run_terminal', started=self.origin,
                      outcome=('succeeded' if code == 0 else
                               'cancelled' if self.cancellation == 'cancelled' else 'failed'),
                      error=error, exit_code=code,
                      extensions={'evidence_complete': not self.incomplete})
            self.finished = True
            if self.stream is not None:
                try:
                    self.stream.close()
                except Exception:
                    self._warn()

    def receipt(self, stage, ok, write):
        """Link inside the existing transaction; publish only after it commits.

        The lock reserves the next seq without emitting a speculative success.
        A crash/sink failure after commit leaves a dangling reference (incomplete).
        """
        current = _ACTIVE.get()
        if not self.enabled or current is None or current.execution is not self:
            return write(None)
        with self.lock:
            ref = {'run_id': self.run_id, 'task_id': current.task_id,
                   'event_id': [self.run_id, self.seq + 1]}
            result = write(ref)
            self.emit('receipt_commit', task=current, scope='stage',
                      outcome='succeeded' if ok else 'failed',
                      error=None if ok else RuntimeError(),
                      extensions={'receipt_stage': stage})
            return result


class Task:
    def __init__(self, execution, stage, parent, kind, metadata):
        self.execution = execution
        self.stage = stage if stage in STAGES else None
        self.task_id = str(uuid.uuid4())
        self.parent_task_id = parent.task_id if parent else None
        self.kind = kind if isinstance(kind, str) and kind in SCOPES - {'run'} else 'task'
        self.metadata = _metadata('task_start', metadata or {}, strict=False)
        if self.kind != kind:
            self.metadata['metadata_redacted'] = True
        self.started = None
        self.terminal = False
        self.attempt_no = 0

    @contextlib.contextmanager
    def bind(self):
        token = _ACTIVE.set(self)
        try:
            yield self
        finally:
            _ACTIVE.reset(token)

    @contextlib.contextmanager
    def scope(self):
        self.started = self.execution.clock()
        self.execution.emit('task_start', task=self, scope=self.kind, started=self.started,
                            extensions=self.metadata)
        with self.bind():
            try:
                yield self
            except BaseException as error:
                self.finish(exit_code(error), error)
                raise
            else:
                self.finish(0)

    def finish(self, code, error=None):
        code = int(code) if type(code) is bool else code
        if not self.terminal:
            self.execution.emit('task_terminal', task=self, scope=self.kind,
                                attempt=max(1, self.attempt_no), started=self.started,
                                outcome='succeeded' if code == 0 else 'failed',
                                exit_code=code, error=error)
            self.terminal = True

    def call(self, fn, *args, **kwargs):
        with self.scope():
            return fn(*args, **kwargs)

    def attempt(self, fn, *args, event_metadata=None, **kwargs):
        self.attempt_no += 1
        number = self.attempt_no
        if number > 1:
            self.execution.emit('retry_requested', task=self, attempt=number)
        start = self.execution.clock()
        self.execution.emit('attempt_start', task=self, attempt=number, started=start,
                            extensions=event_metadata or self.metadata)
        try:
            result = fn(*args, **kwargs)
        except BaseException as error:
            self.execution.emit('attempt_terminal', task=self, attempt=number,
                                started=start, outcome='succeeded' if exit_code(error) == 0 else 'failed',
                                error=error, exit_code=exit_code(error))
            raise
        code = getattr(result, 'returncode', 0)
        if isinstance(result, dict) and result.get('failed'):
            code = 1
        self.execution.emit('attempt_terminal', task=self, attempt=number,
                            started=start, outcome='succeeded' if code == 0 else 'failed',
                            exit_code=code,
                            error=None if code == 0 else RuntimeError(),
                            extensions=event_metadata or self.metadata)
        return result


def context(c):
    return getattr(c, 'execution', None)


def task(c, stage=None, **kwargs):
    execution = context(c)
    if execution is None:
        execution = ExecutionContext()
    return execution.task(stage, **kwargs)


def current(c):
    value = _ACTIVE.get()
    return value if value is not None and value.execution is context(c) else None


def cache_hit(c, method, *, frames=None):
    execution = context(c)
    if execution is not None and execution.enabled and not getattr(c, 'dry', False):
        t = execution.task(kind='cache')
        with t.scope():
            execution.emit('cache_hit', task=t, extensions={
                'validator': method, 'producer': None,
                'producer_reason': 'legacy_unknown', 'frame_range': frames})


def read_events(data, replay=False):
    """Pure reducer: never execute evidence. Invalid facts raise ValueError.

    Missing/truncated boundaries and gaps are incomplete; duplicate event IDs
    are rejected except byte-equivalent decoded records in explicit replay mode.
    """
    truncated_bytes = isinstance(data, bytes) and bool(data and not data.endswith(b'\n'))
    if isinstance(data, bytes):
        try:
            data = data.decode('utf-8')
        except UnicodeDecodeError as error:
            if not truncated_bytes or error.end != len(data):
                raise ValueError('invalid UTF-8 event stream') from None
            data = data[:error.start].decode('utf-8')
    rows, by_seq = [], {}
    truncated = truncated_bytes or bool(data and not data.endswith('\n'))
    lines = data.splitlines()
    for index, line in enumerate(lines):
        try:
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError('duplicate JSON field')
                    result[key] = value
                return result
            def reject_constant(value):
                raise ValueError('nonfinite JSON number')
            if len(line) > MAX_EVENT_CHARS:
                raise ValueError('oversized event')
            row = json.loads(line, object_pairs_hook=pairs, parse_constant=reject_constant)
        except (ValueError, TypeError):
            if truncated and index == len(lines) - 1:
                break
            raise ValueError('invalid event JSON') from None
        if not isinstance(row, dict) or row.get('schema_version') != SCHEMA:
            raise ValueError('unsupported event schema')
        try:
            valid_id = canonical_uuid(row.get('run_id'))
        except (ValueError, TypeError, AttributeError):
            valid_id = False
        seq = row.get('seq')
        if not valid_id or not _integer(seq) or seq < 1:
            raise ValueError('invalid event identity')
        if row.get('event_id') != [row['run_id'], seq] or not isinstance(row.get('event'), str) or row['event'] not in EVENTS:
            raise ValueError('invalid event identity/type')
        if rows and row['run_id'] != rows[0]['run_id']:
            raise ValueError('mixed execution IDs')
        if seq in by_seq:
            if replay and row == by_seq[seq]:
                continue
            raise ValueError('duplicate event ID')
        for key in ('stage', 'task_id', 'attempt', 'monotonic_duration', 'error'):
            if key not in row:
                raise ValueError('missing event field')
        duration = row['monotonic_duration']
        if duration is not None and not _number(duration):
            raise ValueError('invalid duration')
        if row['stage'] is not None and (not isinstance(row['stage'], str) or row['stage'] not in STAGES):
            raise ValueError('invalid stage')
        if row['task_id'] is not None:
            if not _id(row['task_id']) or not _integer(row['attempt']) or row['attempt'] < 1:
                raise ValueError('invalid task identity')
        required = {'schema_version', 'run_id', 'seq', 'event_id', 'event', 'scope',
                    'stage', 'task_id', 'parent_task_id', 'attempt', 'monotonic_duration',
                    'outcome', 'exit_code', 'error', 'timing', 'resources', 'extensions'}
        if set(row) != required or not isinstance(row['extensions'], dict):
            raise ValueError('invalid event fields')
        _validate_values(row)
        _validate_measurements(row)
        _validate_error(row['error'])
        _metadata(row['event'], row['extensions'], strict=True)
        offset = row['timing']['offset_ms']
        if rows and offset < rows[-1]['timing']['offset_ms']:
            raise ValueError('backwards monotonic clock')
        if row['outcome'] == 'succeeded' and row['error'] is not None:
            raise ValueError('successful event cannot carry failure')
        if row['parent_task_id'] is not None and not _id(row['parent_task_id']):
            raise ValueError('invalid parent task identity')
        if row['event'] in ('run_start', 'run_terminal') and (
                row['task_id'] is not None or row['stage'] is not None or row['attempt'] is not None):
            raise ValueError('invalid run boundary scope')
        by_seq[seq] = row
        rows.append(row)
    complete = bool(rows) and not truncated
    contiguous = [r['seq'] for r in rows] == list(range(1, len(rows) + 1))
    complete = complete and contiguous
    starts = [r for r in rows if r['event'] == 'run_start']
    terminals = [r for r in rows if r['event'] == 'run_terminal']
    if len(starts) > 1 or len(terminals) > 1:
        raise ValueError('duplicate run boundary')
    if starts and starts[0] is not rows[0] or terminals and terminals[0] is not rows[-1]:
        raise ValueError('misordered run boundary')
    complete = complete and len(starts) == len(terminals) == 1
    if terminals:
        terminal = terminals[0]
        code = terminal.get('exit_code')
        expected = ('succeeded' if code == 0 else
                    'cancelled' if any(r['event'] == 'cancelled' for r in rows) else 'failed')
        if type(code) is not int or terminal.get('outcome') != expected:
            raise ValueError('inconsistent terminal exit')
        complete = complete and terminal.get('extensions', {}).get('evidence_complete') is True
    task_starts, task_ends, attempts, attempt_ends = {}, set(), {}, set()
    # Missing observations stay uncertain; explicit contradictory observations reject.
    last_attempt, active_attempt, pending_retry = {}, {}, {}
    task_terminals, attempt_terminals = {}, {}
    cancel_state = None
    for row in rows:
        event, tid = row['event'], row['task_id']
        if tid is not None and event != 'task_start' and (tid not in task_starts or tid in task_ends):
            complete = False
        if event == 'receipt_commit':
            if (tid is None or row['scope'] != 'stage' or row['stage'] is None or
                    row['extensions'].get('receipt_stage') != row['stage'] or
                    row['outcome'] not in ('succeeded', 'failed')):
                raise ValueError('invalid receipt commit stage/scope/outcome')
        if event == 'task_start':
            if tid is None or tid in task_starts:
                raise ValueError('duplicate/missing task start identity')
            parent = row['parent_task_id']
            if parent is not None and (parent not in task_starts or parent in task_ends):
                complete = False
            if row['attempt'] != 1:
                raise ValueError('task start must use first attempt')
            task_starts[tid] = row
        elif event == 'task_terminal':
            if tid is None or tid in task_ends:
                raise ValueError('duplicate/missing task terminal identity')
            if tid not in task_starts:
                complete = False
            if row['attempt'] != max(1, last_attempt.get(tid, 0)):
                if contiguous:
                    raise ValueError('task terminal attempt does not match executed attempts')
                complete = False
            if tid in active_attempt or tid in pending_retry:
                complete = False
            task_terminals[tid] = row
            task_ends.add(tid)
        elif event == 'retry_requested':
            number = row['attempt']
            if tid in pending_retry or number < 2:
                raise ValueError('invalid retry request')
            if tid in active_attempt:
                if contiguous:
                    raise ValueError('retry overlaps active attempt')
                complete = False
            if number != last_attempt.get(tid, 0) + 1:
                if contiguous:
                    raise ValueError('retry does not identify next attempt')
                complete = False
            pending_retry[tid] = number
        elif event in ('attempt_start', 'attempt_terminal'):
            key = (tid, row['attempt'])
            if tid not in task_starts or tid in task_ends:
                complete = False
            if event == 'attempt_start':
                if key in attempts or row['attempt'] <= last_attempt.get(tid, 0):
                    raise ValueError('invalid attempt ordering')
                if tid in active_attempt:
                    if contiguous:
                        raise ValueError('overlapping attempts for one task')
                    complete = False
                if row['attempt'] != last_attempt.get(tid, 0) + 1:
                    complete = False
                requested = pending_retry.pop(tid, None)
                if requested is not None and requested != row['attempt']:
                    raise ValueError('retry request and attempt disagree')
                if row['attempt'] > 1 and requested is None:
                    complete = False
                last_attempt[tid] = row['attempt']
                active_attempt[tid] = row['attempt']
                attempts[key] = row
            else:
                if key in attempt_ends:
                    raise ValueError('duplicate attempt terminal')
                if key not in attempts:
                    complete = False
                if active_attempt.get(tid) == row['attempt']:
                    active_attempt.pop(tid)
                attempt_terminals[key] = row
                attempt_ends.add(key)
        elif event == 'cancel_requested':
            if cancel_state is not None:
                raise ValueError('repeated cancellation request')
            cancel_state = event
        elif event in ('cancelled', 'cleanup_failed'):
            if cancel_state != 'cancel_requested':
                raise ValueError('unrequested cancellation outcome')
            cancel_state = event
        if tid is not None and tid in task_starts:
            start = task_starts[tid]
            if row['parent_task_id'] is not None and row['parent_task_id'] in task_ends:
                complete = False
            if row['stage'] != start['stage'] or row['parent_task_id'] != start['parent_task_id']:
                raise ValueError('task attribution changed')
        if event in ('run_terminal', 'task_terminal', 'attempt_terminal'):
            start_event = (starts[0] if starts else None) if event == 'run_terminal' else (
                task_starts.get(tid) if event == 'task_terminal' else attempts.get((tid, row['attempt'])))
            if start_event is not None:
                expected_start = start_event['timing']['start_ms']
                actual_start = row['timing']['start_ms']
                if expected_start is None:
                    complete = False
                if expected_start is not None and not math.isclose(actual_start, expected_start, rel_tol=1e-12, abs_tol=1e-6):
                    raise ValueError('terminal duration uses a different start')
                if actual_start > start_event['timing']['offset_ms']:
                    raise ValueError('terminal start follows its start event')
        if event in ('task_terminal', 'attempt_terminal'):
            code = row['exit_code']
            if type(code) is not int or row['outcome'] != ('succeeded' if code == 0 else 'failed'):
                raise ValueError('inconsistent task/attempt outcome')
    # Enclosing lifetimes are checked after reduction so legacy starts with null
    # timestamps cannot hide contradictions with known terminal start claims.
    def lifetime(start, terminal):
        nonlocal complete
        if start is None or terminal is None:
            complete = False
        began = start['timing']['start_ms'] if start is not None else None
        if began is None:
            complete = False
            began = terminal['timing']['start_ms'] if terminal is not None else None
        return began, terminal['timing']['offset_ms'] if terminal is not None else None

    def enclosed(inner, outer):
        for child, parent, before in ((inner[0], outer[0], True), (inner[1], outer[1], False)):
            if child is not None and parent is not None and (
                    child < parent if before else child > parent):
                if not math.isclose(child, parent, rel_tol=1e-12, abs_tol=1e-6):
                    raise ValueError('execution lifetime outside enclosing lifetime')

    run_lifetime = lifetime(starts[0] if starts else None, terminals[0] if terminals else None)
    task_lifetimes = {tid: lifetime(start, task_terminals.get(tid)) for tid, start in task_starts.items()}
    for tid, span in task_lifetimes.items():
        parent = task_starts[tid]['parent_task_id']
        enclosed(span, task_lifetimes.get(parent, run_lifetime))
    for key, start in attempts.items():
        span = lifetime(start, attempt_terminals.get(key))
        enclosed(span, task_lifetimes.get(key[0], run_lifetime))
        previous = attempt_terminals.get((key[0], key[1] - 1))
        if previous is not None and span[0] is not None and span[0] < previous['timing']['offset_ms']:
            raise ValueError('retry lifetime overlaps preceding attempt')
    complete = complete and not pending_retry and not active_attempt
    complete = complete and set(task_starts) == task_ends and set(attempts) == attempt_ends
    return {'run_id': starts[0]['run_id'] if starts else None,
            'complete': bool(complete), 'events': rows,
            'exit_code': terminals[0]['exit_code'] if terminals else None,
            'reason': None if complete else 'incomplete_events'}
