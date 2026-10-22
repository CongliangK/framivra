"""阶段回执溯源：回执绑定输入身份 + 依赖门 + 下游失效（PR#1 与 13bed7a 缓存治理的融合层）。

合并裁定（2026-09-11，PR#1 Windows 适配合入 main）：
  - 放弃 PR 的「项目级全量指纹 + cache/<input-fingerprint>/ 整仓内容寻址」布局：
    每次 CLI 调用全量 sha256 所有源媒体（GB 级 I/O），且与 main 13bed7a 的
    per-产物内容寻址键（hygiene.py gfx/seg/audio 键）+ gc/df/snapshot/manifest
    布局正面冲突。保留 main 的缓存架构。
  - 保留 PR 的回执侧价值：receipt.json 绑定输入身份（输入变 → 不继承旧阶段成功）、
    begin_stage 依赖门（前置阶段未成功即拒绝）、DOWNSTREAM 下游失效表（生产者
    运行前撤销下游成功——失败重跑不得保留旧 QC PASS）。
  - 输入身份 receipt_fingerprint 用「便宜但与缓存键同等敏感」的材料：timeline 字节
    + 源媒体 stat 身份（path+size+mtime_ns+首尾采样哈希，同 hygiene._fp 哲学）+
    gfx html 字节 + 声明依赖（cache_dependencies）内容。任何缓存键会变的输入变化，
    回执门同样重置；不做全量媒体哈希。Electron Alpha 另追加显式后端身份与
    namespace（gfx_backend.py）；其已列出的实现代码变化自动失效。其他实现升级
    不做全仓代码哈希，须显式隔离或清理旧缓存。
"""
import hashlib
import json
import os
import stat
import operator
from pathlib import Path, PosixPath, WindowsPath

from .runtime.diagnostics import emit, indexed, mark_handled, operation, required_field

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


def file_digest(path) -> str:
    """小文件全量 sha256（html/依赖等 KB 级资产）。"""
    p = Path(path)
    if not stat.S_ISREG(p.stat().st_mode):
        raise ValueError(f'content hash input is not a regular file: {p}')
    # A precheck gives a clear error; nonblocking open plus fstat also prevents
    # a regular-file-to-FIFO replacement from hanging between check and open.
    fd = os.open(p, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) |
                 getattr(os, 'O_BINARY', 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f'content hash input is not a regular file: {p}')
        digest = hashlib.sha256()
        for block in iter(lambda: os.read(fd, 1024 * 1024), b''):
            digest.update(block)
        return digest.hexdigest()
    finally:
        os.close(fd)


def media_stat_digest(path) -> str:
    """源媒体 stat 身份：size+mtime_ns+首尾 64KB 采样哈希。

    与 hygiene._fp 同一判据（保持回执敏感度 == 缓存键敏感度）。采样补强：
    同尺寸 + 恢复时间戳的同路径替换（PR#1 回归场景）不再误命中——媒体重编码
    首尾字节必变。非对抗性保证：刻意构造首尾不变的中间字节篡改不在覆盖范围
    （PR 文档同口径：这是流程溯源，不是防篡改完整性证明）。
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
            from .render.gfx_dependencies import DependencyError
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

    from .render import gfx_svg
    try:
        svg_route = gfx_svg.route(c)
    except ValueError as error:
        location = '$.video_tracks'
        for ti, track in enumerate(c.tl.get('video_tracks', [])):
            for ci, clip in enumerate(track.get('clips', [])):
                if 'format' in clip and (track.get('kind') != 'gfx' or clip['format'] not in ('html', 'svg-sequence')):
                    location = f'$.video_tracks[{ti}].clips[{ci}].format'
        if 'strict dependency' in str(error):
            location = '$.project.gfx_dependency_policy'
        emit(c, str(error), json_path=location, rule_id='load.svg_sequence',
             measured={'exception_type': type(error).__name__})
        mark_handled(error)
        raise
    if svg_route == 'svg-sequence':
        for ti, track in enumerate(c.tl.get('video_tracks', [])):
            if track.get('kind') == 'gfx':
                for ci, clip in enumerate(track.get('clips', [])):
                    try:
                        gfx_svg.resolve(c, clip)
                    except (ValueError, OSError) as error:
                        emit(c, str(error), json_path=f'$.video_tracks[{ti}].clips[{ci}].src',
                             rule_id='load.svg_sequence',
                             source={'resource': str(c.root / clip['src'])},
                             measured={'exception_type': type(error).__name__})
                        mark_handled(error)
                        raise
        return
    from .render.gfx_dependencies import policy, resolve
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
    try:
        current = receipt_fingerprint(c)
        from .render import gfx_svg
        if gfx_svg.route(c) != 'svg-sequence':
            from .render.gfx_environment import recheck
            recheck(c)
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
    from .render.gfx_dependencies import resolve, identity, policy
    for clip, path, origin, exists in gfx_inputs:
        from .render import gfx_svg
        if gfx_svg.is_svg(clip):
            h.update(b'svg-sequence|' + gfx_svg.canonical(gfx_svg.resolve(c, clip)))
            continue
        if not exists and policy(c) == 'legacy':
            continue
        record = resolve(c, clip, html_path=path, origin=origin)
        if record['policy'] != 'legacy' or not record['legacy_key_safe']:
            h.update(b'gfx-dependencies-v1|' + identity(record))


def receipt_fingerprint(c, *, timeline_bytes=None) -> str:
    """回执输入身份：timeline 字节 + 源 stat 身份 + gfx html 字节 + 声明依赖内容
    + subtitles_src 文件内容（v2 §1.1 条目 6）+ 生效插件摘要段（v2 §2.1 不变量 ②）。

    v2 两处追加（均在现有哈希序末尾，不改既有成分）：
      - subtitles_src：文件内容 digest（对齐 gfx html/声明依赖处理方式；未声明
        该键的 timeline 不追加任何字节），文件变更 → 回执失效；
      - active_plugins_digest：生效插件名+版本+参数摘要——**空生效清单时省略
        该段**（无插件时间线的指纹与 v1 逐字节一致，v1 回执在 v2 下继续有效；
        影子基线温态复验依赖此兼容）。不变量 ② 仍成立：插件安装/卸载/升级/
        参数变化 → 段出现或内容变化 → 录制侧指纹与重算侧失配 → 旧回执拒绝。
    """
    h = hashlib.sha256()
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
        digest = media_stat_digest(p) if p.is_file() else 'MISSING'
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
    # v2 §2.1 不变量 ②：生效插件清单摘要段（迟导入便于测试替身；pluginlib 零 vtx
    # 依赖无环）。空生效清单时省略段——无插件时间线指纹与 v1 逐字节一致（v1 回执
    # 继续有效，影子基线温态复验依赖）；插件装/卸/升级/改参时段出现或内容变，
    # 录制指纹与重算失配即拒绝陈旧产物。
    from .pluginlib.hooks import active_plugins_digest
    _pdigest = active_plugins_digest(c)
    if _pdigest:
        h.update(_encode(c, _pdigest, '$', status='unknown', source={
            'domain': 'provider', 'provider': 'active_plugins_digest',
            'field': 'return_value'}))
    # Append only after all historical producer operations succeed. The shared
    # #47 record adds path/closure identity without reordering native failures.
    _append_gfx_dependency_identity(c, h, gfx_inputs)
    from .render import gfx_svg
    if gfx_svg.route(c) == 'svg-sequence':
        return h.hexdigest()
    # Selected renderer and namespace revoke downstream success as well.
    from .render.gfx_backend import identity
    from .hygiene import gfx_cache_ns
    backend, namespace = identity(), gfx_cache_ns()
    if backend or namespace:
        h.update(f"gfx-backend:{backend}|ns:{namespace}\n".encode())
    if gfx_inputs:
        from .render.gfx_environment import identity as environment_identity
        environment = environment_identity(c)
        if environment:
            h.update(environment.encode())
    return h.hexdigest()


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
    if (not isinstance(record, dict)
            or record.get('input_fingerprint') != fingerprint
            or not isinstance(record.get('stages'), dict)):
        record = {'input_fingerprint': fingerprint, 'stages': {}}
    # Malformed observations remain diagnostic history. Consumers must require
    # literal success for their own prerequisites, not erase unrelated stages.
    return record
