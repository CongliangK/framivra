"""Runtime receipt identities; semantics live in docs/cache-provenance.md.

Formal source boundaries re-read complete SHA-256 content. The public sampled
media_stat_digest API remains Fast only. No persistent digest is a read bypass.
"""
import hashlib
import json
import os
import stat
import operator
from pathlib import Path, PosixPath, WindowsPath

from .diagnostics import emit, indexed, mark_handled, operation, required_field

SAMPLE_BYTES = 65536   # 源媒体首/尾采样字节数（stat 身份的内容成分）


_NATIVE_VALUES = (type(None), bool, int, float, str, bytes, list, dict, tuple)
_NO_DEFAULT = object()


def _operand(value, path):
    evidence = {'json_path': path, 'type': type(value).__name__}
    if type(value) in (type(None), bool, int, float, str):
        evidence['value'] = value
    return evidence


def _native_operation(c, value, function, path, name, *, source=None, status='fail'):
    # Only native operations on native values belong to this producer. A custom
    # mapping, __str__, __fspath__, or encode callback must keep its own error.
    if type(value) not in _NATIVE_VALUES:
        return function()
    return operation(c, function, path=path, rule='load.operation',
                     operands={'operation': name, 'values': [_operand(value, path)]},
                     source=source, status=status)


def _get(c, obj, key, parent, default=_NO_DEFAULT):
    call = (lambda: obj.get(key)) if default is _NO_DEFAULT else (lambda: obj.get(key, default))
    return _native_operation(c, obj, call, parent,
                             'mapping_get:' + key)


def _field(c, obj, key, parent):
    if type(obj) not in _NATIVE_VALUES:
        return obj[key]
    return required_field(c, obj, key, parent)


def _indexed(c, value, path):
    if type(value) not in _NATIVE_VALUES:
        return enumerate(iter(value))
    return indexed(c, value, path)


class _SourceEntries:
    """Carry indices without skipping sorted()'s original length-hint read."""
    def __init__(self, c, sources):
        self.c, self.sources = c, sources

    def __iter__(self):
        return _indexed(self.c, self.sources, '$.sources')

    def __length_hint__(self):
        # Custom __len__/__length_hint__ callbacks stay outside annotation.
        return operator.length_hint(self.sources, 8)


def _path(c, value, path):
    root = c.root
    if type(root) not in (Path, PosixPath, WindowsPath):
        return root / value
    return _native_operation(c, value, lambda: root / value, path, 'path_join')


def _text(c, value, path):
    # Container repr can call methods on its contents; do not wrap that callback.
    if type(value) in (list, dict, tuple):
        return str(value)
    return _native_operation(c, value, lambda: str(value), path, 'str')


def _encode(c, value, path, *, source=None, status='fail'):
    return _native_operation(c, value, lambda: value.encode(), path, 'encode',
                             source=source, status=status)


def _identity_io(function, *args):
    """Tag only failures at concrete file-I/O calls, preserving the exception."""
    try:
        return function(*args)
    except OSError as error:
        error._vtx_identity_io_unavailable = True
        raise


def require_trusted_context(c):
    """Failure-evidence contexts never acquire formal capabilities in place."""
    if getattr(c, '_unverified_validation', False):
        raise InputIdentityError('unverified validation context cannot certify or reuse products')


def file_digest(path) -> str:
    """Stream complete SHA-256 content in bounded blocks; require a regular file."""
    p = Path(path)
    if not stat.S_ISREG(_identity_io(p.stat).st_mode):
        error = ValueError(f'content hash input is not a regular file: {p}')
        error._vtx_identity_io_unavailable = True
        raise error
    # A precheck gives a clear error; nonblocking open plus fstat also prevents
    # a regular-file-to-FIFO replacement from hanging between check and open.
    fd = _identity_io(os.open, p, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) |
                 getattr(os, 'O_BINARY', 0))
    try:
        if not stat.S_ISREG(_identity_io(os.fstat, fd).st_mode):
            error = ValueError(f'content hash input is not a regular file: {p}')
            error._vtx_identity_io_unavailable = True
            raise error
        digest = hashlib.sha256()
        for block in iter(lambda: _identity_io(os.read, fd, 1024 * 1024), b''):
            digest.update(block)
        return digest.hexdigest()
    finally:
        os.close(fd)


def media_stat_digest(path) -> str:
    """Fast compatibility token: size, mtime_ns and sampled first/last bytes.

    Not a Verified identity; interior mutations may be invisible.
    """
    p = Path(path)
    st = p.stat()
    h = hashlib.sha1()
    if st.st_size <= 2 * SAMPLE_BYTES:
        h.update(p.read_bytes())
    else:
        with p.open('rb') as f:
            h.update(f.read(SAMPLE_BYTES))
            f.seek(st.st_size - SAMPLE_BYTES)
            h.update(f.read(SAMPLE_BYTES))
    return f"{st.st_size}:{st.st_mtime_ns}:{h.hexdigest()[:16]}"


IDENTITY_SCHEMA = 'framivra.media-identity.v1'
RECEIPT_SCHEMA = 'framivra.receipt.v2'


def verified_media_identity(path):
    """Read all bytes at this boundary; errors propagate, no stat-backed memo."""
    return {'schema': IDENTITY_SCHEMA, 'level': 'Verified',
            'algorithm': 'sha256', 'scope': 'complete-file',
            'digest': file_digest(path)}


def verified_media_token(path):
    """Domain-separated canonical complete-content identity for runtime keys."""
    return json.dumps(verified_media_identity(path), sort_keys=True, separators=(',', ':'))


def _dependency_list(owner, label, *, c=None, origin='$'):
    deps = _get(c, owner, "cache_dependencies", origin, [])
    invalid_index = None
    def valid(index, value):
        nonlocal invalid_index
        invalid_index = index
        return isinstance(value, str) and value.strip() and "\0" not in value
    if not isinstance(deps, list) or not all(valid(i, p) for i, p in enumerate(deps)):
        error = ValueError(f'{label}.cache_dependencies must be a list of nonempty file paths')
        location = origin + '.cache_dependencies' if origin != '$' else '$'
        if invalid_index is not None and origin != '$':
            location += f'[{invalid_index}]'
        emit(c, str(error), json_path=location, rule_id='load.cache_dependencies',
             measured={'type': type(deps).__name__},
             threshold={'type': 'list[nonempty file path without NUL]'})
        mark_handled(error)
        raise error
    return deps


def _declaration_inputs(c, clip, *, origin='$'):
    """Read each owner once; do not replay native operations for attribution."""
    project = _dependency_list(_get(c, c.tl, "project", '$', {}), 'project',
                               c=c, origin='$.project')
    local = []
    if clip is not None and _native_operation(c, clip, lambda: "cache_dependencies" in clip,
                                              origin, "clip_dependency_membership"):
        local = _dependency_list(clip, 'clip', c=c, origin=origin)
    return project, local


def _declaration_paths(c, project, local):
    if not local:
        return project
    return sorted({str((c.root / item).resolve()) for item in project + local})


def declared_dependencies(c, clip=None):
    """Compatibility path adapter from the shared declaration resolver.

    Project-only ordering is historical; nonempty clip lists use canonical union.
    No HTML parsing or transitive/network closure is implied by this adapter.
    """
    project, local = _declaration_inputs(c, clip)
    return _declaration_paths(c, project, local)


def resolved_declarations(c, clip=None, *, origin='$'):
    """Resolve declared identity with explicit origins and full content hashes.

    Native field/list operations remain here for operation-level diagnostics;
    file I/O is deliberately separate from native operation attribution.
    """
    project, local = _declaration_inputs(c, clip, origin=origin)
    paths = _declaration_paths(c, project, local)
    origins = {}
    for scope, items in [('project', project), ('clip', local)]:
        for index, item in enumerate(items):
            path = str((c.root / item).resolve())
            origins.setdefault(path, []).append({'scope': scope, 'index': index, 'declaration': item})
    entries = []
    for item in paths:
        path = str((c.root / item).resolve())
        try:
            value = file_digest(c.root / item)
        except (OSError, ValueError) as error:
            from .gfx_dependencies import DependencyError
            raise DependencyError('declared dependency unreadable', path, origins[path], detail=str(error)) from error
        entries.append({'path': path, 'sha256': value, 'origins': origins[path], 'declaration': item})
    return entries



class _DependencyValues:
    """Observe the existing list/extend iteration, preserving its length hint."""
    def __init__(self, values, origin, origins):
        self.values, self.origin, self.origins = values, origin, origins

    def __iter__(self):
        iterator = iter(self.values)
        def observed():
            for index, value in enumerate(iterator):
                # Custom str subclasses may have effectful hashing. They remain
                # opaque; diagnostic bookkeeping must not invoke their callbacks.
                if self.origins is not None and type(value) is str:
                    self.origins.setdefault(value, f'{self.origin}[{index}]')
                yield value
        return observed()

    def __length_hint__(self):
        return operator.length_hint(self.values, 8)


def all_declared_dependencies(c, *, _origins=None):
    """Every declaration, preserving native order and first duplicate origin."""
    deps = list(_DependencyValues(declared_dependencies(c),
                                 '$.project.cache_dependencies', _origins))
    for ti, track in _indexed(c, _get(c, c.tl, 'video_tracks', '$', []), '$.video_tracks'):
        track_origin = f'$.video_tracks[{ti}]'
        for ci, clip in _indexed(c, _get(c, track, 'clips', track_origin, []), track_origin + '.clips'):
            origin = f'{track_origin}.clips[{ci}]'
            if _native_operation(c, clip, lambda: 'cache_dependencies' in clip,
                                 origin, 'clip_dependency_membership'):
                if _get(c, track, 'kind', track_origin) != 'gfx':
                    error = ValueError('clip.cache_dependencies is only valid on gfx tracks')
                    emit(c, str(error), json_path=origin + '.cache_dependencies',
                         rule_id='load.cache_dependencies', measured={'declaration_present': True},
                         threshold={'track_kind': 'gfx'})
                    mark_handled(error)
                    raise error
                deps.extend(_DependencyValues(_dependency_list(clip, 'clip', c=c, origin=origin),
                                              origin + '.cache_dependencies', _origins))
    return list(dict.fromkeys(deps))


def validate_cache_dependencies(c):
    """Declared local dependencies must exist and be regular files."""
    origins = {}
    for item in all_declared_dependencies(c, _origins=origins):
        origin = origins.get(item, '$') if type(item) is str else '$'
        path = _path(c, item, origin)
        if not path.is_file():
            error = ValueError(f'cache dependency is not a file: {item}')
            emit(c, str(error), json_path=origin, rule_id='load.cache_dependency',
                 measured={'path': item, 'is_file': False}, threshold={'is_file': True},
                 source={'resource': str(path)})
            mark_handled(error)
            raise error

    from .gfx_dependencies import policy, resolve
    if policy(c) == 'strict':
        for ti, track in _indexed(c, _get(c, c.tl, 'video_tracks', '$', []), '$.video_tracks'):
            track_origin = f'$.video_tracks[{ti}]'
            if _get(c, track, 'kind', track_origin) == 'gfx':
                for ci, clip in _indexed(c, _get(c, track, 'clips', track_origin, []), track_origin + '.clips'):
                    resolve(c, clip, origin=f'{track_origin}.clips[{ci}]')


class InputIdentityError(RuntimeError):
    """A build cannot certify products after its pinned inputs have changed."""


def verify_input_identity(c):
    """Re-read inputs; never adopt the current identity for an in-flight build."""
    if c.input_fingerprint is None:
        raise InputIdentityError('inspection context has no verified input identity')
    try:
        current = receipt_fingerprint(c)
    except (OSError, ValueError, RuntimeError) as e:
        raise InputIdentityError(
            f"issue #32：输入在渲染期间变化或无法复核（{e}）；"
            "本次产物不标成功，请恢复输入或重新运行 vtx all") from e
    expected = c.input_fingerprint
    if current != expected:
        error = InputIdentityError(
            "issue #32：输入在渲染期间被修改；本次产物不标成功，"
            "请恢复输入或重新运行 vtx all")
        emit(c, str(error), rule_id='runtime.input_identity',
             measured={'input_fingerprint': current},
             threshold={'input_fingerprint': expected},
             suggested_actions=[{'op': 'restore_or_restart_inputs', 'params': {}}])
        mark_handled(error)
        raise error


def _append_gfx_dependency_identity(c, h, gfx_inputs):
    """Versioned #47 suffix; historical serialization stays an explicit adapter."""
    from .gfx_dependencies import resolve, identity, policy
    for clip, path, origin, exists in gfx_inputs:
        if not exists and policy(c) == 'legacy':
            continue
        record = resolve(c, clip, html_path=path, origin=origin)
        if record['policy'] != 'legacy' or not record['legacy_key_safe']:
            h.update(b'gfx-dependencies-v1|' + identity(record))


def receipt_fingerprint(c, *, timeline_bytes=None, source_identities=None) -> str:
    require_trusted_context(c)
    return _receipt_fingerprint(c, timeline_bytes=timeline_bytes,
                                source_identities=source_identities)


def _receipt_fingerprint(c, *, timeline_bytes=None, source_identities=None,
                         unavailable_sources=None):
    """Full source identities plus timeline and existing declared dependencies.

    source_identities optionally collects this read's provenance. It is never
    consumed as a shortcut by subsequent verification boundaries.
    """
    h = hashlib.sha256()
    h.update((RECEIPT_SCHEMA + '\0').encode())
    h.update(c.tl_path.read_bytes() if timeline_bytes is None else timeline_bytes)
    def source_key(entry):
        index, source = entry
        origin = f'$.sources[{index}]'
        return _text(c, _get(c, source, 'path', origin, ''), origin + '.path')

    # Keep one sort/key evaluation and carry original indices through its order.
    # Keys are str(), so native key comparisons cannot fail on mixed JSON types.
    sources = _get(c, c.tl, 'sources', '$', [])
    for index, s in sorted(_SourceEntries(c, sources), key=source_key):
        origin = f'$.sources[{index}]'
        p = _path(c, _field(c, s, 'path', origin), origin + '.path')
        prefix = _text(c, _get(c, s, 'path', origin, ''), origin + '.path') + ':'
        identity = None
        try:
            available = True
            if unavailable_sources is not None:
                available = (_identity_io(p.is_file) if type(p) in (Path, PosixPath, WindowsPath)
                             else p.is_file())
            if available:
                identity = verified_media_identity(p)
            else:
                unavailable_sources.append(prefix[:-1])
        except (OSError, ValueError) as error:
            if unavailable_sources is None or not getattr(error, '_vtx_identity_io_unavailable', False):
                raise
            unavailable_sources.append(prefix[:-1])
        if source_identities is not None and identity is not None:
            source_identities[prefix[:-1]] = identity
        # This marker is traversal-only: an unavailable source forces None below.
        digest = (json.dumps(identity, sort_keys=True, separators=(',', ':'))
                  if identity is not None else 'UNVERIFIED')
        h.update(_encode(c, prefix + digest + '\n', origin + '.path'))
    gfx_inputs = []
    tracks = _get(c, c.tl, 'video_tracks', '$', [])
    for ti, tr in _indexed(c, tracks, '$.video_tracks'):
        track_origin = f'$.video_tracks[{ti}]'
        if _get(c, tr, 'kind', track_origin) != "gfx":
            continue
        clips = _get(c, tr, 'clips', track_origin, [])
        for ci, cl in _indexed(c, clips, track_origin + '.clips'):
            origin = f'{track_origin}.clips[{ci}]'
            p = _path(c, _field(c, cl, 'src', origin), origin + '.src')
            prefix = _text(c, _field(c, cl, 'src', origin), origin + '.src') + ':'
            exists = p.is_file()
            digest = file_digest(p) if exists else 'MISSING'
            h.update(_encode(c, prefix + digest + '\n', origin + '.src'))
            gfx_inputs.append((cl, p, origin, exists))
    for index, item in enumerate(declared_dependencies(c)):
        origin = f'$.project.cache_dependencies[{index}]'
        prefix = item + ':'
        digest = file_digest(_path(c, item, origin))
        h.update(_encode(c, prefix + digest + '\n', origin))
    for ti, track in _indexed(c, _get(c, c.tl, 'video_tracks', '$', []), '$.video_tracks'):
        track_origin = f'$.video_tracks[{ti}]'
        for ci, clip in _indexed(c, _get(c, track, 'clips', track_origin, []), track_origin + '.clips'):
            origin = f'{track_origin}.clips[{ci}]'
            if not _native_operation(c, clip, lambda: 'cache_dependencies' in clip,
                                     origin, 'clip_dependency_membership'):
                continue
            for index, item in enumerate(_dependency_list(clip, 'clip', c=c, origin=origin)):
                location = f'{origin}.cache_dependencies[{index}]'
                prefix = f"clip_dependency:{ti}:{ci}:" + item + ":"
                digest = file_digest(_path(c, item, location))
                h.update(_encode(c, prefix + digest + "\n", location))
    # v2 §1.1 条目 6：subtitles_src 文件内容并入（缺失记 MISSING——validate 侧 FAIL）
    src = _get(c, c.tl, 'subtitles_src', '$')
    if isinstance(src, str) and src:
        p = _path(c, src, '$.subtitles_src')
        digest = file_digest(p) if p.is_file() else 'MISSING'
        h.update(_encode(c, 'subtitles_src:' + digest + '\n', '$.subtitles_src'))
    # Preserve the existing plugin dependency axis after the v2 identity domain.
    # An empty active plugin set adds no plugin bytes; v1 receipts still expire.
    from .pluginlib.hooks import active_plugins_digest
    _pdigest = active_plugins_digest(c)
    if _pdigest:
        h.update(_encode(c, _pdigest, '$', status='unknown', source={
            'domain': 'provider', 'provider': 'active_plugins_digest',
            'field': 'return_value'}))
    # Append only after all historical producer operations succeed. The shared
    # #47 record adds path/closure identity without reordering native failures.
    _append_gfx_dependency_identity(c, h, gfx_inputs)
    return None if unavailable_sources else h.hexdigest()


# 生产者运行前撤销其下游旧成功（含失败重跑场景——PR#1 的核心回归修复之一）。
DOWNSTREAM = {
    'prepare': ['audio', 'mux', 'mux_draft', 'qc'],
    'gfx': ['video', 'video_draft', 'mux', 'mux_draft', 'qc'],
    'video': ['mux', 'qc'], 'video_draft': ['mux_draft'],
    'audio': ['mux', 'mux_draft', 'qc'], 'mux': ['qc'],
}


def load_receipt(path, fingerprint):
    """读回执；输入身份不符（或文件缺失/损坏）→ 空阶段表，不继承任何旧成功。"""
    try:
        record = json.loads(Path(path).read_text(encoding='utf-8'))
    # OSError（P6 修复 B-P3-5d）：权限类读失败与缺失/损坏同语义（空表），不裸栈
    except (OSError, ValueError):
        record = {}
    if (fingerprint is None or not isinstance(record, dict) or record.get('schema') != RECEIPT_SCHEMA
            or record.get('input_fingerprint') != fingerprint
            or not isinstance(record.get('stages'), dict)):
        record = {'schema': RECEIPT_SCHEMA, 'input_fingerprint': fingerprint, 'stages': {}}
    return record
