"""Bounded read-only Linux process-tree samples for optional GFX observations.

No process is signalled, no system setting is changed, and no command line,
process name or filesystem path is exported. Numeric observations are samples,
not accounting: shared RSS is counted repeatedly and short-lived children can be
missed. This module has no sink; callers publish through vtx.events.
"""
import os
import math
from pathlib import Path

MAX_PROCESSES = 4096
MAX_STAT_BYTES = 4096
MAX_IDENTITIES = 16384


def parse_stat(raw, pid, *, ticks_per_second, page_size):
    """Parse numeric kernel fields; comm may contain spaces and right parens."""
    if len(raw) > MAX_STAT_BYTES or not raw.endswith('\n'):
        raise ValueError('invalid process stat size')
    prefix, separator, tail = raw.rpartition(') ')
    fields = tail.split()
    if not separator or not prefix.startswith(str(pid) + ' (') or len(fields) < 22:
        raise ValueError('invalid process stat')
    ppid, started = int(fields[1]), int(fields[19])
    ticks, rss = int(fields[11]) + int(fields[12]), int(fields[21])
    if (min(ppid, started, ticks, rss) < 0 or max(ppid, started, ticks, rss) > 2 ** 53 - 1 or
            type(ticks_per_second) is not int or ticks_per_second <= 0 or
            type(page_size) is not int or page_size <= 0):
        raise ValueError('invalid process stat measurement')
    return {'pid': pid, 'ppid': ppid, 'start_ticks': started,
            'cpu_ms': ticks * 1000.0 / ticks_per_second, 'rss_bytes': rss * page_size}


class ProcReader:
    """Bounded numeric /proc snapshot. Injection is for deterministic tests."""
    def __init__(self, root='/proc', *, limit=MAX_PROCESSES):
        if type(limit) is not int or not 1 <= limit <= MAX_PROCESSES:
            raise ValueError('invalid process scan bound')
        self.root, self.limit = Path(root), limit
        self.ticks = os.sysconf('SC_CLK_TCK')
        self.page_size = os.sysconf('SC_PAGE_SIZE')

    def identity(self, pid):
        row = self.read(pid)
        return (row['pid'], row['start_ticks'])

    def read(self, pid):
        if type(pid) is not int or pid <= 0:
            raise ValueError('invalid process ID')
        with (self.root / str(pid) / 'stat').open('r', encoding='ascii') as stream:
            raw = stream.read(MAX_STAT_BYTES + 1)
        return parse_stat(raw, pid, ticks_per_second=self.ticks, page_size=self.page_size)

    def snapshot(self):
        rows, status, visited = {}, 'sampled', 0
        try:
            with os.scandir(self.root) as entries:
                for entry in entries:
                    if not entry.name.isascii() or not entry.name.isdecimal():
                        continue
                    visited += 1
                    if visited > self.limit:
                        return rows, 'limit_reached'
                    try:
                        row = self.read(int(entry.name))
                    except FileNotFoundError:
                        continue  # Raced a normal process exit, not a zero sample.
                    except (OSError, ValueError, UnicodeError):
                        status = 'partial'
                        continue
                    rows[row['pid']] = row
        except OSError:
            return {}, 'unavailable'
        return rows, status


class TreeSamples:
    """Track observed root/descendant identities across exits and restarts.

    CPU is the sum of each observed identity's largest sampled lifetime CPU. It
    is neither utilization nor exact consumed CPU; unobserved final increments
    are missed. RSS sums only currently observed live members of this tree.
    """
    def __init__(self, reader):
        self.reader = reader
        self.roots = set()
        self.cpu_by_identity = {}
        self.known = set()
        self.limit_reached = False
        self.registration_failed = False

    def register(self, pid):
        try:
            identity = self.reader.identity(pid)
        except (OSError, ValueError, UnicodeError):
            self.registration_failed = True
            return False
        if len(self.roots) >= MAX_IDENTITIES:
            self.limit_reached = True
            return False
        self.roots.add(identity)
        return True

    def sample(self):
        if self.limit_reached:
            return self._unavailable('limit_reached')
        if not self.roots:
            return self._unavailable('unavailable' if self.registration_failed else 'not_started')
        rows, status = self.reader.snapshot()
        if status != 'sampled':
            return self._unavailable(status)
        identities = {(pid, row['start_ticks']): row for pid, row in rows.items()}
        # Roots and already observed descendants remain identified after reparenting.
        selected = (self.roots | self.known) & identities.keys()
        children = {}
        for identity, row in identities.items():
            children.setdefault(row['ppid'], []).append(identity)
        pending = list(selected)
        while pending:
            identity = pending.pop()
            for child in children.get(identity[0], ()):
                if child not in selected:
                    selected.add(child)
                    pending.append(child)
        if len(self.known | selected) > MAX_IDENTITIES:
            self.limit_reached = True
            return self._unavailable('limit_reached')
        self.known.update(selected)
        for identity in selected:
            value = identities[identity]['cpu_ms']
            self.cpu_by_identity[identity] = max(value, self.cpu_by_identity.get(identity, 0.0))
        return {'status': 'partial' if self.registration_failed and status == 'sampled' else status,
                'rss_bytes': sum(identities[i]['rss_bytes'] for i in selected),
                'cpu_ms': sum(self.cpu_by_identity.values()), 'process_count': len(selected)}

    @staticmethod
    def _unavailable(status):
        return {'status': status, 'rss_bytes': None, 'cpu_ms': None, 'process_count': None}


# Lifecycle adapter: one existing sink, bounded thread and bounded scan state.
import contextvars
import functools
import threading
import time
from . import events

_TREE = contextvars.ContextVar('vtx_sampled_browser_tree', default=None)
SAMPLE_INTERVAL_SECONDS = .2
MAX_SAMPLES = 18000


def register_browser(process):
    sampler = _TREE.get()
    if sampler is not None:
        try:
            sampler.register(process.pid)
        except Exception:
            sampler.tree.registration_failed = True


class ProcessSampler:
    def __init__(self, task, reader=None):
        self.task = task
        self.tree = TreeSamples(reader if reader is not None else ProcReader())
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.index = 0
        self.previous = None
        self.closed = False
        self.scheduler_failed = False

    def register(self, pid):
        with self.lock:
            if not self.closed:
                self.tree.register(pid)

    def sample(self):
        with self.lock:
            if self.closed or self.index >= MAX_SAMPLES or not self.task.execution.enabled:
                return
            interval = None
            try:
                if self.scheduler_failed:
                    raise ValueError('sampling clock unavailable')
                now = time.monotonic()
                if (type(now) not in (int, float) or not math.isfinite(now) or
                        self.previous is not None and now < self.previous):
                    raise ValueError('invalid sampling clock')
                interval = None if self.previous is None else (now - self.previous) * 1000
                if interval is not None and not math.isfinite(interval):
                    raise ValueError('invalid sampling interval')
                self.previous = now
            except Exception:
                interval = None
                self.scheduler_failed = True
                self.stop_event.set()
            try:
                result = (TreeSamples._unavailable('unavailable') if self.scheduler_failed
                          else self.tree.sample())
            except Exception:
                result = TreeSamples._unavailable('unavailable')
            self.index += 1
            if self.index >= MAX_SAMPLES:
                result = TreeSamples._unavailable('limit_reached')
                self.stop_event.set()
            measured = result['status'] == 'sampled'
            resources = {}
            for name, unit in events.RESOURCE_UNITS.items():
                value = result['cpu_ms'] if name == 'cpu' else result['rss_bytes'] if name == 'rss' else None
                value = value if measured else None
                resources[name] = {'value': value, 'unit': unit,
                    'method': 'proc_stat_tree_snapshot' if value is not None else None,
                    'scope': 'process_tree',
                    'unavailable_reason': None if value is not None else 'not_measured' if name in ('gpu', 'vram') else 'unavailable'}
            self.task.execution.emit('resource_sample', task=self.task, resources=resources,
                extensions={'sample_index': self.index, 'interval_ms': interval,
                            'missed_intervals': 0 if interval is None else max(0, int(interval / (SAMPLE_INTERVAL_SECONDS * 1000)) - 1),
                            'process_count': result['process_count'] if measured else None,
                            'sample_status': result['status']})

    def _run(self):
        while not self.stop_event.wait(SAMPLE_INTERVAL_SECONDS):
            try:
                self.sample()
            except Exception:
                # Preserve a sticky gap even if an unexpected observer failure
                # escapes sample(); final close records unavailable coverage.
                self.scheduler_failed = True
                self.stop_event.set()
                self.task.execution.coverage_incomplete = True
                return
            if not self.task.execution.enabled:
                return

    def start(self):
        self.sample()  # Explicitly unavailable before a browser has been spawned.
        self.thread = threading.Thread(target=self._run, name='vtx-process-samples', daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        # /proc scans have fixed entry/byte budgets; join before the task terminal.
        if self.thread is not None:
            self.thread.join()
        self.sample()  # After rendering's browser cleanup, not before it.
        with self.lock:
            self.closed = True


def observed_tree(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        parent = events._ACTIVE.get()
        if not (events.lifecycle_enabled() and getattr(parent.execution, 'process_samples', False)):
            return fn(*args, **kwargs)
        with events.operation('process_samples') as task:
            if task is None:
                return fn(*args, **kwargs)
            try:
                sampler = ProcessSampler(task)
            except Exception:
                # Optional platform capability failure never changes rendering.
                task.execution.coverage_incomplete = True
                return fn(*args, **kwargs)
            token = _TREE.set(sampler)
            try:
                try:
                    sampler.start()
                except Exception:
                    task.execution.coverage_incomplete = True
                return fn(*args, **kwargs)
            finally:
                try:
                    sampler.close()
                except Exception:
                    task.execution.coverage_incomplete = True
                _TREE.reset(token)
    return wrapped
