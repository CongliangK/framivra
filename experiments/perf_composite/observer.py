"""Bounded inclusive/exclusive wall observations without changing call outcomes.

Recorded values contain labels and times, never argv, paths, environment values,
exception messages, media bytes, or credentials. Exclusive time means only time
outside the other observed functions, not a claim about the work's purpose.
"""
from contextlib import contextmanager
from functools import wraps
import hashlib
import inspect
import sys
import threading
import time


def union_ns(intervals):
    end = None
    total = 0
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if end is None or a > end:
            total += b - a
        else:
            total += max(0, b - end)
        end = b if end is None else max(end, b)
    return total


class Observer:
    def __init__(self, *, max_spans=300000, clock=time.perf_counter_ns):
        self.clock, self.max_spans = clock, max_spans
        self.origin = clock()
        self.local = threading.local()
        self.lock = threading.Lock()
        self.spans = []
        self.threads = {}
        self.truncated = False
        self.identity = 'UNRUN'
        self._restores = []
        self._wrappers = {}

    def wrap(self, function, name, predicate=None):
        @wraps(function)
        def observed(*args, **kwargs):
            if predicate is not None and not predicate(args, kwargs):
                return function(*args, **kwargs)
            stack = getattr(self.local, 'stack', None)
            if stack is None:
                stack = self.local.stack = []
            with self.lock:
                if len(self.spans) >= self.max_spans:
                    self.truncated = True
                    record = None
                else:
                    ident = threading.get_ident()
                    thread = self.threads.setdefault(ident, len(self.threads) + 1)
                    record = dict(id=len(self.spans), parent=stack[-1]['id'] if stack else None,
                                  thread=thread, name=name, start=self.clock(), end=None,
                                  outcome='returned')
                    self.spans.append(record)
            if record is None:
                return function(*args, **kwargs)
            stack.append(record)
            try:
                return function(*args, **kwargs)
            except BaseException as error:
                # Preserve the same object and original exit/cancellation policy.
                record['outcome'] = ('cancelled' if isinstance(error, KeyboardInterrupt)
                                     else 'raised')
                raise
            finally:
                record['end'] = self.clock()
                stack.pop()
        return observed

    def _replace(self, owner, key, value):
        self._restores.append((owner, key, getattr(owner, key)))
        setattr(owner, key, value)

    def _patch(self, owner, key, name, predicate=None):
        original = getattr(owner, key)
        wrapped = self.wrap(original, name, predicate)
        self._wrappers[wrapped] = original
        # Direct imports bind aliases. Keep all loaded vtx aliases in agreement;
        # late imports obtain the wrapped canonical module attribute normally.
        for module_name, module in list(sys.modules.items()):
            if module_name.startswith('vtx.') and module is not None:
                for alias, value in list(vars(module).items()):
                    if value is original:
                        self._replace(module, alias, wrapped)
        if getattr(owner, key) is original:
            self._replace(owner, key, wrapped)

    def restore(self):
        for owner, key, original in reversed(self._restores):
            setattr(owner, key, original)
        self._restores.clear()
        # Restore aliases imported lazily while the observer was installed.
        for module_name, module in list(sys.modules.items()):
            if module_name.startswith('vtx.') and module is not None:
                for key, value in list(vars(module).items()):
                    if inspect.isfunction(value) and value in self._wrappers:
                        setattr(module, key, self._wrappers[value])
        self._wrappers.clear()

    @contextmanager
    def installed(self):
        from vtx import (cache_state, cli, composite, frame_source, gfx_cli,
                         gfx_environment, hygiene, infra)
        import subprocess
        targets = [
            (cli, '_observed_stage', 'video_stage', lambda a, k: (a[1] if len(a) > 1 else k.get('name')) == 'video'),
            (composite, 'prepare_layers', 'prepare_layers', None),
            (hygiene, 'budget_gate', 'budget_gate', None),
            (hygiene, 'manifest_record', 'manifest_record', None),
            (hygiene, 'seg_key', 'segment_key', None),
            (hygiene, 'gfx_key', 'gfx_key', None),
            (composite, 'media_cache_valid', 'media_cache_validation', None),
            (cache_state, 'verify_input_identity', 'input_identity_validation', None),
            (cache_state, 'receipt_fingerprint', 'input_fingerprint', None),
            (gfx_environment, 'certificate', 'environment_certificate', None),
            (gfx_environment, 'recheck', 'environment_recheck', None),
            (infra.Ctx, 'begin_stage', 'begin_stage', None),
            (infra.Ctx, 'verify_inputs', 'verify_inputs', None),
            (infra.Ctx, 'probe', 'input_probe', None),
            (infra.Ctx, 'run', 'ctx_run', None),
            (frame_source.PngSequence, 'input_args', 'png_input_args', None),
            (frame_source.PngSequence, '_stable_input', 'png_stable_input', None),
            (frame_source.PngSequence, '_validated', 'png_sequence_validation', None),
            (gfx_cli, 'png_invalid_reason', 'png_validation', None),
            (gfx_cli, 'opaque_rgb_to_rgba', 'rgb_to_rgba', None),
            (gfx_cli, 'inspect_page', 'page_inspection', None),
            (gfx_cli, 'complete_page', 'page_completion', None),
            (composite.RenderPlan, '_composite_graph', 'graph_body', None),
            (composite.RenderPlan, '_layer_input_args', 'layer_input_args', None),
            (composite.RenderPlan, '_layer_chain', 'layer_filter_chain', None),
            (subprocess, 'run', 'ffmpeg_subprocess_run', self._is_ffmpeg),
        ]
        # Recompute without using or perturbing the lru_cache. inspect.getsource
        # follows __wrapped__, so these methods must keep their exact source.
        digest = composite._graph_code_digest.__wrapped__
        before = digest()
        source_hashes = [hashlib.sha256(inspect.getsource(getattr(o, k)).encode()).hexdigest()
                         for o, k, _, _ in targets]
        try:
            for owner, key, name, predicate in targets:
                self._patch(owner, key, name, predicate)
            after = digest()
            current = [hashlib.sha256(inspect.getsource(getattr(o, k)).encode()).hexdigest()
                       for o, k, _, _ in targets]
            if before != after or source_hashes != current:
                self.identity = 'FAIL'
                raise RuntimeError('Observer changed source identity; refusing execution')
            self.identity = 'PASS'
            yield self
        finally:
            self.restore()

    @staticmethod
    def _is_ffmpeg(args, kwargs):
        from pathlib import Path
        command = args[0] if args else kwargs.get('args')
        return (isinstance(command, (list, tuple)) and bool(command)
                and isinstance(command[0], (str, bytes, Path))
                and str(command[0]).replace('\\', '/').rsplit('/', 1)[-1] == 'ffmpeg')

    def summary(self, start, end):
        """Clip actual spans, then subtract direct observed children on that thread."""
        rows, intervals, children = {}, [], {}
        for span in self.spans:
            if span['end'] is not None and span['parent'] is not None:
                children.setdefault(span['parent'], []).append((span['start'], span['end']))
        per_thread = {}
        thread_categories = {}
        for span in self.spans:
            if span['end'] is None:
                continue
            a, b = max(start, span['start']), min(end, span['end'])
            if b <= a:
                continue
            inclusive = b - a
            child = union_ns([(max(a, x), min(b, y)) for x, y in children.get(span['id'], [])])
            exclusive = inclusive - child
            row = rows.setdefault(span['name'], dict(calls=0, inclusive_ns=0, exclusive_ns=0))
            row['calls'] += 1
            row['inclusive_ns'] += inclusive
            row['exclusive_ns'] += exclusive
            thread_row = thread_categories.setdefault(str(span['thread']), {}).setdefault(
                span['name'], dict(calls=0, inclusive_ns=0, exclusive_ns=0))
            thread_row['calls'] += 1
            thread_row['inclusive_ns'] += inclusive
            thread_row['exclusive_ns'] += exclusive
            per_thread.setdefault(str(span['thread']), []).append((a, b))
            intervals.append((a, b))
        covered = union_ns(intervals)
        return dict(status='UNKNOWN' if self.truncated else 'OBSERVED', wall_ns=end - start,
                    observed_union_ns=covered, outside_observed_functions_ns=end - start - covered,
                    categories=rows, categories_by_thread=thread_categories,
                    thread_union_ns={t: union_ns(ranges) for t, ranges in per_thread.items()},
                    warning='Inclusive categories overlap; exclusive categories add only within one thread. '
                            'Cross-thread totals are work-time, not elapsed wall. Function self time '
                            'includes uninstrumented work and observer overhead.')

    def report(self):
        end = self.clock()
        stages = [s for s in self.spans if s['name'] == 'video_stage' and s['end'] is not None]
        windows = []
        by_id = {s['id']: s for s in self.spans}
        for stage in stages:
            processes = [s for s in self.spans if s['name'] == 'ffmpeg_subprocess_run'
                         and stage['start'] <= s['start'] < stage['end']]
            first = min(processes, key=lambda s: s['start']) if processes else None
            ctx_calls = [s for s in self.spans if s['name'] == 'ctx_run'
                         and stage['start'] <= s['start'] < stage['end']]
            first_ctx = min(ctx_calls, key=lambda s: s['start']) if ctx_calls else None
            chain, parent = [], first['parent'] if first else None
            while parent is not None:
                ancestor = by_id[parent]
                chain.append(ancestor['name'])
                parent = ancestor['parent']
            windows.append(dict(stage_start_offset_ns=stage['start'] - self.origin,
                                stage_outcome=stage['outcome'],
                                first_ffmpeg_offset_ns=first['start'] - self.origin if first else None,
                                first_ffmpeg_role='unknown', first_ffmpeg_ancestor_categories=chain,
                                first_ctx_run_offset_ns=first_ctx['start'] - self.origin if first_ctx else None,
                                pre_first_ctx_run=self.summary(stage['start'], first_ctx['start']) if first_ctx else None,
                                pre_first_ffmpeg=self.summary(stage['start'], first['start']) if first else None,
                                whole_video=self.summary(stage['start'], stage['end'])))
        return dict(schema='perf-composite-observer/1', source_identity=self.identity,
                    spans_recorded=len(self.spans), truncated=self.truncated,
                    boundary='Python subprocess.run entry for argv[0] ffmpeg; not kernel process creation. '
                             'First FFmpeg may validate, probe, or encode; role remains unknown.',
                    video_windows=windows, whole_invocation=self.summary(self.origin, end),
                    production_equivalence='UNRUN', mac_300_frame_gate='UNRUN', mac_full_film_gate='UNRUN')
