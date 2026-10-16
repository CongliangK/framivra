"""Opt-in current-publication ledger for the bounded strict experiment runner.

No cache scan creates provenance. Only renderer publication hooks add records;
final decoding verifies those records. This is not an environment fingerprint or
an independently authenticated producer, and does not itself issue a receipt.
"""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import threading
from . import events

MAX_FRAMES = 4096
MAX_PUBLICATIONS = 32768
MAX_PNG_BYTES = 128 * 1024 * 1024


def current():
    task = events._ACTIVE.get()
    return getattr(task.execution, 'gfx_publications', None) if task is not None else None


def enabled():
    ledger = current()
    return ledger is not None and ledger.execution.enabled


def published(path, data, *, origin='rendered', copy_head=None, source_token=None, producer_token=None):
    ledger = current()
    if ledger is None:
        return
    try:
        ledger.record(path, data, origin=origin, copy_head=copy_head, source_token=source_token, producer_token=producer_token)
    except Exception:
        ledger.failed = True
        ledger.execution.coverage_incomplete = True


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def browser_ready(browser):
    ledger = current()
    if ledger is None or not ledger.require_runtime:
        return
    try:
        from .gfx_runtime import collect
        raw = collect(browser.port, run_id=ledger.run_id)
        token = str(events.uuid.uuid4())
        with ledger.lock:
            if len(ledger.browser_observations) >= 256:
                raise ValueError('browser observation bound')
            ledger.browser_observations[token] = raw
        browser.publication_browser_id = token
    except Exception:
        ledger.failed = True
        ledger.execution.coverage_incomplete = True


def bind_tab(browser, tab):
    ledger = current()
    if ledger is not None and ledger.require_runtime:
        try:
            tab.publication_browser_id = getattr(browser, 'publication_browser_id', None)
        except Exception:
            ledger.failed = True
            ledger.execution.coverage_incomplete = True


def frame_source(tab, alpha_path):
    ledger = current()
    if ledger is None or not ledger.require_runtime:
        return None
    try:
        witness = tab.eval('({width:window.innerWidth,height:window.innerHeight,device_scale:window.devicePixelRatio})')
        if (not isinstance(witness, dict) or set(witness) != {'width', 'height', 'device_scale'} or
                any(type(witness[k]) is not int or witness[k] <= 0 for k in ('width', 'height')) or
                type(witness['device_scale']) not in (int, float) or
                not math.isfinite(witness['device_scale']) or witness['device_scale'] <= 0 or
                alpha_path not in ('opaque', 'override', 'twoshot', 'canvas')):
            raise ValueError('invalid page observation')
        witness = {'source': 'page-cdp-eval', 'viewport': witness, 'alpha_path': alpha_path}
        token = str(events.uuid.uuid4())
        with ledger.lock:
            browser_id = getattr(tab, 'publication_browser_id', None)
            raw = ledger.browser_observations[browser_id]
            if len(ledger.frame_sources) >= MAX_PUBLICATIONS:
                raise ValueError('page observation bound')
            ledger.frame_sources[token] = {'runtime_sha256': _digest(raw),
                'page_witness_sha256': _digest(witness), 'browser_id': browser_id, 'witness': witness}
        return token
    except Exception:
        ledger.failed = True
        ledger.execution.coverage_incomplete = True
        return None


def before_static_read(path):
    ledger = current()
    if ledger is None:
        return None
    try:
        with ledger.lock:
            # Snapshot only observations already published before the source read.
            return (str(Path(path)), tuple(ledger.records.get(str(Path(path)), ())))
    except Exception:
        ledger.failed = True
        ledger.execution.coverage_incomplete = True
        return None


def staged_bytes(path):
    if not enabled():
        return None
    ledger = current()
    try:
        with open(path, 'rb') as stream:
            data = stream.read(MAX_PNG_BYTES + 1)
        if not 0 < len(data) <= MAX_PNG_BYTES:
            raise ValueError('publication size limit')
        return data
    except Exception:
        ledger.failed = True
        ledger.execution.coverage_incomplete = True
        return None


class PublicationLedger:
    def __init__(self, execution, run_id, targets, *, require_runtime=False, color_profile=None):
        if execution.schema != events.LIFECYCLE_SCHEMA or not events._id(run_id):
            raise ValueError('invalid provenance binding')
        if type(require_runtime) is not bool:
            raise ValueError('invalid runtime observation mode')
        if color_profile not in (None, "srgb") or (color_profile and not require_runtime):
            raise ValueError("invalid experiment color profile")
        self.color_profile = color_profile
        self.require_runtime = require_runtime
        self.browser_observations, self.frame_sources = {}, {}
        self.execution, self.run_id = execution, run_id
        self.targets, self.records = {}, {}
        self.failed, self.count = False, 0
        self.lock = threading.Lock()
        total = 0
        instances = set()
        for target in targets:
            src, dur, key = target['src'], target['dur'], target['gfx_key']
            directory = Path(target['frame_dir'])
            if (not isinstance(src, str) or not src or type(dur) is not int or dur <= 0 or
                    not directory.is_absolute() or '..' in directory.parts or
                    not isinstance(key, str) or not 8 <= len(key) <= 64 or
                    any(ch not in '0123456789abcdef' for ch in key)):
                raise ValueError('invalid provenance target')
            name = str(directory)
            if name in self.targets or (src, dur) in instances:
                raise ValueError('duplicate provenance target')
            instances.add((src, dur)); total += dur
            if total > MAX_FRAMES:
                raise ValueError('provenance frame bound exceeded')
            self.targets[name] = dict(src=src, dur=dur, gfx_key=key, frame_dir=name)
        if not self.targets:
            raise ValueError('empty provenance targets')
        execution.gfx_publications = self

    def record(self, path, data, *, origin, copy_head, source_token=None, producer_token=None):
        path = Path(path)
        target = self.targets.get(str(path.parent))
        if target is None or not path.name.startswith('f') or not path.name.endswith('.png'):
            raise ValueError('publication outside frozen targets')
        index = int(path.name[1:-4])
        if (path.name != f'f{index:04d}.png' or not 0 <= index < target['dur'] or
                not isinstance(data, bytes) or not 0 < len(data) <= MAX_PNG_BYTES or
                origin not in ('rendered', 'static_copy') or
                (origin == 'rendered' and copy_head is not None) or
                (origin == 'static_copy' and (type(copy_head) is not int or not 0 <= copy_head < index))):
            raise ValueError('invalid publication observation')
        digest = hashlib.sha256(data).hexdigest()
        fields = {'gfx_key': target['gfx_key'], 'frame_index': index,
                  'frame_origin': origin, 'png_sha256': digest}
        if self.require_runtime and origin == 'rendered':
            producer = self.frame_sources.get(producer_token)
            if producer is None:
                raise ValueError('publication lacks actual CDP producer')
            fields.update({k: producer[k] for k in ('runtime_sha256', 'page_witness_sha256')})
        if copy_head is not None:
            fields['copy_head'] = copy_head
            source_path = str(path.parent / f'f{copy_head:04d}.png')
            if source_token is None or source_token[0] != source_path:
                raise ValueError('missing current source observation before copy read')
            sources = [entry for entry in source_token[1]
                       if entry['fields']['frame_origin'] == 'rendered' and
                       entry['fields']['png_sha256'] == digest]
            if not sources:
                raise ValueError('copy bytes lack a prior current rendered source')
            source = max(sources, key=lambda entry: entry['ref']['event_id'][1])
            fields['copy_source_seq'] = source['ref']['event_id'][1]
            if self.require_runtime:
                fields.update({k: source['fields'][k] for k in ('runtime_sha256', 'page_witness_sha256')})
        with self.lock:
            if self.count >= MAX_PUBLICATIONS:
                raise ValueError('publication observation bound exceeded')
            self.count += 1
            ref = self.execution.emit('frame_commit', task=events._ACTIVE.get(),
                                      extensions=fields)
            if ref is None:
                raise ValueError('publication event unavailable')
            self.records.setdefault(str(path), []).append({'fields': fields, 'ref': ref})

    def completion_records(self, *, width, height, events_data):
        """Verify final files against actual publications; never fabricate origins."""
        from PIL import Image
        if (not self.execution.finished or self.failed or self.execution.incomplete or
                self.execution.coverage_incomplete):
            raise ValueError('incomplete publication observations')
        parsed = events.read_events(events_data)
        if not parsed['complete'] or parsed['run_id'] != self.execution.run_id or parsed['exit_code'] != 0:
            raise ValueError('producer stream is not successfully closed')
        commits = {row['seq']: row for row in parsed['events'] if row['event'] == 'frame_commit'}
        captured = [entry for entries in self.records.values() for entry in entries]
        if len(commits) != len(captured):
            raise ValueError('publication stream does not match captured records')
        for entry in captured:
            ref = entry['ref']
            row = commits.get(ref['event_id'][1])
            if (row is None or row['event_id'] != ref['event_id'] or
                    row['task_id'] != ref['task_id'] or row['extensions'] != entry['fields']):
                raise ValueError('publication stream does not match captured records')
        if type(width) is not int or type(height) is not int or min(width, height) <= 0:
            raise ValueError('invalid provenance viewport')
        result, identities = [], set()
        for target in self.targets.values():
            directory = Path(target['frame_dir'])
            # Fresh runner roots must not acquire symlink aliases after binding.
            if directory.resolve(strict=True) != directory:
                raise ValueError('aliased provenance directory')
            frames = {}
            dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for index in range(target['dur']):
                    name = f'f{index:04d}.png'
                    observations = self.records.get(str(directory / name), [])
                    if not observations:
                        raise ValueError('frame has no current publication')
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
                    with os.fdopen(fd, 'rb') as stream:
                        before = os.fstat(stream.fileno())
                        identity = (before.st_dev, before.st_ino)
                        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                                not 0 < before.st_size <= MAX_PNG_BYTES or identity in identities):
                            raise ValueError('invalid publication file identity')
                        data = stream.read(MAX_PNG_BYTES + 1)
                        raw_digest = hashlib.sha256(data).hexdigest()
                        matches = [entry['fields'] for entry in observations if entry['fields']['png_sha256'] == raw_digest]
                        origins = {(r['frame_origin'], r.get('copy_head')) for r in matches}
                        if len(origins) != 1:
                            raise ValueError('changed or ambiguous publication')
                        with Image.open(io.BytesIO(data)) as image:
                            if image.format != 'PNG' or image.size != (width, height):
                                raise ValueError('publication viewport/format mismatch')
                            digest = hashlib.sha256(image.convert('RGBA').tobytes()).hexdigest()
                        after = os.fstat(stream.fileno())
                        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                            raise ValueError('publication changed during verification')
                    identities.add(identity)
                    origin, head = next(iter(origins))
                    record = {'run_id': self.run_id, 'origin': origin, 'sha256': digest}
                    if self.require_runtime:
                        chosen = max((entry for entry in observations if entry['fields']['png_sha256'] == raw_digest),
                                     key=lambda entry: entry['ref']['event_id'][1])
                        record.update(publication_seq=chosen['ref']['event_id'][1],
                                      png_sha256=raw_digest, gfx_key=target['gfx_key'],
                                      runtime_sha256=chosen['fields']['runtime_sha256'],
                                      page_witness_sha256=chosen['fields']['page_witness_sha256'])
                    if origin == 'static_copy':
                        source = frames.get(f'{head:04d}')
                        if source is None or source['origin'] != 'rendered' or source['sha256'] != digest:
                            raise ValueError('static copy lacks matching current rendered head')
                        record.update(head_frame=f'{head:04d}', static_status='verified_static')
                    frames[f'{index:04d}'] = record
            finally:
                os.close(dfd)
            result.append({key: target[key] for key in ('src', 'dur', 'frame_dir')} | {'frames': frames})
        return result

    def runtime_bundle(self, *, width, height, expected):
        """Validate every observed startup and page witness before a run receipt."""
        from .gfx_runtime import identity
        if not self.require_runtime or not self.browser_observations or self.failed:
            raise ValueError('missing runtime producer binding')
        runtimes, witnesses = {}, {}
        for raw in self.browser_observations.values():
            if raw.get('run_id') != self.run_id or identity(raw) != expected:
                raise ValueError('runtime differs from frozen experiment')
            runtimes[_digest(raw)] = raw
        for source in self.frame_sources.values():
            witness = source['witness']
            if witness['viewport'] != {'width': width, 'height': height, 'device_scale': 1}:
                raise ValueError('actual page viewport differs from frozen experiment')
            if source['runtime_sha256'] not in runtimes:
                raise ValueError('page witness lacks actual browser')
            witnesses[_digest(witness)] = witness
        if not witnesses:
            raise ValueError('missing actual page witnesses')
        for entries in self.records.values():
            for entry in entries:
                fields = entry['fields']
                if fields.get('runtime_sha256') not in runtimes or fields.get('page_witness_sha256') not in witnesses:
                    raise ValueError('publication lacks runtime/page binding')
        return {'runtime': runtimes, 'page_witnesses': witnesses}
