"""Bounded storage inspection and explicit ordinary-GC transactions.

Sizes are logical regular-file bytes; hardlinks count once per pathname. No
persistent plans, client paths, deep cleanup, or arbitrary-writer coordination.
Callers must stop all writers before clean; the policy lock only coordinates
participating CLI CacheSessions. Deletion belongs exclusively to CleanupPlan.
"""
import contextlib
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import sys
import time

SCHEMA = 'framivra.storage/v1'
WIRE_LIMIT = 8 * 1024 * 1024
REQUEST_LIMIT = 4096
MAX_ENTRIES = 100000
MAX_PLAN_ENTRIES = 10000
SCAN_SECONDS = 15.0
TIMELINE_LIMIT = 8 * 1024 * 1024
CONFIRM = 'stopped-writers-and-cleanup'


class StorageError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _result(operation):
    return dict(schema=SCHEMA, operation=operation, ok=True,
                content_version=None, diagnostics=[])


def _fail(result, code, message):
    result['ok'] = False
    result['diagnostics'].append(dict(code=code, severity='error', message=message[:1024]))
    return result


def encode(result):
    payload = json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    if len(payload.encode('utf-8')) + 1 > WIRE_LIMIT:
        raise StorageError('output.limit', 'storage response exceeds configured wire byte bound')
    return payload


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _token(info):
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _path(path):
    path = Path(os.path.abspath(os.path.expanduser(path)))
    # Match cleanup's no-link ancestry requirement, including the timeline leaf.
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if _linked(info):
            raise StorageError('input.link', 'timeline and project ancestors must not be links')
    if not stat.S_ISREG(path.lstat().st_mode):
        raise StorageError('input.type', 'timeline must be a regular file')
    return path


@contextlib.contextmanager
def _parent_fd(path):
    """Read-only directory pinning; unlike write transactions needs no proc-fd."""
    descriptors = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(path.anchor, flags)
        descriptors.append(fd)
        for name in path.parts[1:-1]:
            fd = os.open(name, flags, dir_fd=fd)
            descriptors.append(fd)
        yield fd
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _version(path):
    from .runtime import fstxn
    identity = fstxn.cleanup_identity(path)
    # Hash raw timeline bytes: the Windows CRT text default folds CRLF.
    flags = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
             | getattr(os, 'O_BINARY', 0))
    if os.name != 'nt' and getattr(os, 'O_NOFOLLOW', 0) and os.open in os.supports_dir_fd:
        with _parent_fd(path) as parent_fd:
            fd = os.open(path.name, flags, dir_fd=parent_fd)
    else:
        fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise StorageError('input.type', 'timeline must be a regular file')
        data = bytearray()
        while len(data) <= TIMELINE_LIMIT:
            block = os.read(fd, min(65536, TIMELINE_LIMIT + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        if len(data) > TIMELINE_LIMIT:
            raise StorageError('input.limit', 'timeline exceeds 8 MiB')
        if _token(before) != _token(os.fstat(fd)) or identity != fstxn.cleanup_identity(path):
            raise StorageError('input.changed', 'timeline changed while reading')
        return 'sha256:' + hashlib.sha256(data).hexdigest()
    finally:
        os.close(fd)


def _scan(root):
    """No-follow tree metadata snapshot; fail closed on races/errors/budgets.

    POSIX opens directories with O_NOFOLLOW and scans their pinned descriptors.
    Other platforms verify directory identity before/after enumeration and refuse
    reparse points. No file content or linked target is opened by this scanner.
    """
    records, skipped = {}, []
    deadline = time.monotonic() + SCAN_SECONDS
    visited = 0

    def budget():
        nonlocal visited
        visited += 1
        if visited > MAX_ENTRIES or time.monotonic() > deadline:
            raise StorageError('scan.limit', 'incomplete storage scan: entry/time bound exceeded')

    def visit(path, rel, parent_fd=None):
        budget()
        before = (os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                  if parent_fd is not None else path.lstat())
        if _linked(before):
            skipped.append(rel)
            return
        records[rel] = _token(before)
        if not stat.S_ISDIR(before.st_mode):
            return
        fd = None
        try:
            if os.name != 'nt' and getattr(os, 'O_NOFOLLOW', 0) and os.scandir in os.supports_fd:
                fd = os.open(path.name if parent_fd is not None else path,
                             os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             **({'dir_fd': parent_fd} if parent_fd is not None else {}))
                if _token(os.fstat(fd)) != _token(before):
                    raise StorageError('scan.changed', 'directory changed during scan')
                source = fd
            else:
                source = path
            with os.scandir(source) as children:
                for child in children:
                    visit(path / child.name, rel + (child.name,), fd)
            after = (os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                     if parent_fd is not None else path.lstat())
            if _token(before) != _token(after) or (fd is not None and _token(os.fstat(fd)) != _token(before)):
                raise StorageError('scan.changed', 'directory changed during scan')
        finally:
            if fd is not None:
                os.close(fd)

    if os.name != 'nt' and getattr(os, 'O_NOFOLLOW', 0) and os.scandir in os.supports_fd:
        with _parent_fd(root) as parent_fd:
            visit(root, (), parent_fd)
    else:
        visit(root, ())
    if time.monotonic() > deadline:
        raise StorageError('scan.limit', 'incomplete storage scan: time bound exceeded')
    return records, sorted(skipped)


def _usage(records, skipped):
    usage = dict(project_bytes=0, cache_bytes=0, out_bytes=0, other_bytes=0,
                 file_count=0, symlinks_skipped=len(skipped))
    for rel, token in records.items():
        if token[2] != stat.S_IFREG:
            continue
        size = token[3]
        usage['project_bytes'] += size
        usage['file_count'] += 1
        category = rel[0] if rel and rel[0] in ('cache', 'out') else 'other'
        usage[category + '_bytes'] += size
    return usage


def _build(path, version, records, skipped):
    # Hygiene's size/protection readers predate this API and may follow links.
    # Refuse such trees rather than creating a second GC/sizing implementation.
    if any(rel and rel[0] in ('cache', 'out') for rel in skipped):
        raise StorageError('plan.links', 'remove cache/out links or reparse points before planning; no linked targets inspected')
    from .pipeline.infra import Ctx
    from .cli.commands import _cleanup_plan
    try:
        c = Ctx(path)
        c.dry = True
        if 'sha256:' + c.timeline_sha256 != version:
            raise StorageError('input.changed', 'timeline changed before planning')
        plan = _cleanup_plan(c, 'gc')
    except StorageError:
        raise
    except (Exception, SystemExit) as error:
        raise StorageError('plan.dependencies', 'ordinary GC requires available timeline inputs/plugins: ' + str(error)) from error
    entries, identities = [], []
    groups = {candidate.relative_to(path.parent).parts: []
              for items in plan.values() for candidate, _, _ in items}
    if len(groups) > MAX_PLAN_ENTRIES:
        raise StorageError('plan.limit', 'too many ordinary GC candidates')
    for key, token in records.items():
        for depth in range(1, len(key) + 1):
            prefix = key[:depth]
            if prefix in groups:
                groups[prefix].append((key, token))
    for category, items in plan.items():
        for candidate, _, reason in items:
            rel = candidate.relative_to(path.parent).parts
            if not rel or rel[0] not in ('cache', 'out'):
                raise StorageError('plan.path', 'GC candidate escapes cache/out')
            members = groups[rel]
            if not members:
                raise StorageError('scan.changed', 'candidate was not present in storage snapshot')
            size = sum(token[3] for _, token in members if token[2] == stat.S_IFREG)
            entries.append(dict(path=Path(*rel).as_posix(), bytes=size, category=category, reason=reason))
            identities.append((rel, plan.identities[candidate.absolute()], sorted(members)))
            if len(entries) > MAX_PLAN_ENTRIES:
                raise StorageError('plan.limit', 'too many ordinary GC candidates')
    if _version(path) != version:
        raise StorageError('input.changed', 'timeline changed during planning')
    # Recheck every candidate subtree: directory tokens alone miss in-place edits.
    current, links = _scan(path.parent)
    selected = {key: token for rel, _, members in identities for key, token in members}
    if links != skipped or any(current.get(key) != token for key, token in selected.items()) or any(
            key not in selected and any(key[:depth] in groups for depth in range(1, len(key) + 1))
            for key in current):
        raise StorageError('scan.changed', 'GC candidate changed during planning')
    canonical = sorted(zip(entries, identities), key=lambda pair: pair[0]['path'])
    digest = hashlib.sha256(json.dumps([version, canonical], ensure_ascii=True, sort_keys=True,
                                       separators=(',', ':')).encode()).hexdigest()
    entries.sort(key=lambda item: item['path'])
    public = dict(id='sha256:' + digest, entries=entries,
                  bytes=sum(item['bytes'] for item in entries), count=len(entries))
    return plan, public, selected


def storage_operation(timeline, operation, request=None):
    result = _result(operation)
    execution_started = False
    try:
        if operation not in ('inspect', 'plan', 'clean'):
            raise StorageError('cli.arguments', 'expected storage inspect, plan, or clean')
        if operation == 'clean':
            if (not isinstance(request, dict) or set(request) != {'plan_id', 'content_version', 'confirm'}
                    or request.get('confirm') != CONFIRM
                    or any(not isinstance(request.get(key), str)
                           or re.fullmatch(r'sha256:[0-9a-f]{64}', request[key]) is None
                           for key in ('plan_id', 'content_version'))):
                raise StorageError('input.request', 'clean requires exact plan_id, content_version and stopped-writers-and-cleanup confirmation')
        path = _path(timeline)
        result['content_version'] = _version(path)
        with contextlib.redirect_stdout(sys.stderr):
            if operation == 'clean':
                from .runtime import fstxn
                # Refuse linked cache roots/lock leaves BEFORE legacy lock
                # creation can follow them. Match CacheSession's exact target;
                # file_lock owns its physical '<target>.lock' sidecar.
                _, links = _scan(path.parent)
                if any(rel and rel[0] in ('cache', 'out') for rel in links):
                    raise StorageError('plan.links', 'cache/out links must be removed before cleanup')
                lock_path = path.parent / 'cache' / '.cache-policy.lock'
                physical_lock = lock_path.with_name(lock_path.name + '.lock')
                try:
                    lock_info = physical_lock.lstat()
                except FileNotFoundError:
                    pass
                else:
                    if not stat.S_ISREG(lock_info.st_mode):
                        raise StorageError('input.link', 'policy lock must be a regular file')
                lock = fstxn.file_lock(lock_path, root=path.parent)
            else:
                lock = contextlib.nullcontext()
            with lock:
                version = _version(path)
                result['content_version'] = version
                records, skipped = _scan(path.parent)
                if operation == 'inspect':
                    from .cache.policy import read_policy
                    result.update(usage=_usage(records, skipped), cache_mode=read_policy().mode)
                    if _version(path) != version:
                        result.pop('usage', None)
                        raise StorageError('input.changed', 'timeline changed during inspection')
                else:
                    plan, public, selected = _build(path, version, records, skipped)
                    result['plan'] = public
                    # Leave room for actual accounting and bounded failure
                    # diagnostics so a post-delete result never overflows.
                    if len(encode(result).encode('utf-8')) > WIRE_LIMIT - 32768:
                        raise StorageError('output.limit', 'plan leaves insufficient response space; no cleanup authorized')
                    if operation == 'clean':
                        if request['content_version'] != version or request['plan_id'] != public['id']:
                            raise StorageError('plan.stale', 'timeline or exact candidate identities changed; request a new plan')
                        failure = None
                        try:
                            execution_started = True
                            plan.execute()
                        except (Exception, SystemExit) as error:
                            failure = str(error)
                        try:
                            after, _ = _scan(path.parent)
                        except (Exception, SystemExit) as error:
                            result['outcome_unknown'] = True
                            raise StorageError('clean.outcome_unknown',
                                'cleanup attempted but actual remaining bytes could not be scanned: ' + str(error)) from error
                        removed_bytes = sum(token[3] for key, token in selected.items()
                                            if token[2] == stat.S_IFREG and key not in after)
                        candidates = {Path(item['path']).parts for item in public['entries']}
                        remaining_count = sum(key in after for key in candidates)
                        remaining_bytes = sum(token[3] for key, token in after.items()
                            if token[2] == stat.S_IFREG and any(key[:depth] in candidates
                                for depth in range(1, len(key) + 1)))
                        result.update(removed=dict(bytes=removed_bytes, count=public['count'] - remaining_count),
                                      remaining=dict(bytes=remaining_bytes, count=remaining_count))
                        if failure or remaining_count:
                            _fail(result, 'clean.incomplete', failure or 'cleanup retained candidates; writers must remain stopped')
        encode(result)
    except StorageError as error:
        _fail(result, error.code, str(error))
    except (Exception, SystemExit) as error:
        _fail(result, 'storage.failed', str(error))
    if execution_started and not all(key in result for key in ('removed', 'remaining')):
        result['outcome_unknown'] = True
    try:
        encode(result)
    except StorageError:
        # Never turn an attempted cleanup into an apparent zero-write failure.
        # Drop the bulky plan/diagnostic text, not the exact version/accounting.
        compact = _result(operation)
        compact['content_version'] = result['content_version']
        for key in ('removed', 'remaining', 'outcome_unknown'):
            if key in result:
                compact[key] = result[key]
        result = _fail(compact, 'clean.output_limit' if execution_started else 'output.limit',
                       'response exceeded byte bound; plan and diagnostic text omitted')
    return result
