"""Lossless page storage, not a producer or an environment/cache eligibility gate.

Callers MUST authenticate the original completion with the selected backend and
current inputs/environment before packing, and reapply that gate when consuming.
No legacy adoption, certificate rewriting, rendering, CLI or dry-run dispatch.
PNG bytes are deduplicated and compressed in independent ~4 MiB zlib blocks, so
one-frame reads need not expand the page. Only fixed completion/producer sidecars
are carried. Electron receipts contain absolute paths: restoring elsewhere does
not make those receipts valid there. Linux fd-anchored I/O is required; there is
no weaker pathname fallback. Publication is atomic, not power-loss durable.
"""
import contextlib
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import sys
import zlib

from ..runtime import fstxn
from .gfx_cli import _png_stream_invalid_reason

_MAGIC = b'VTXGFXP1'
_END = b'VTXGFXE1'
_FOOTER = struct.Struct('>Q32s8s')
_BLOCK_BYTES = 4 << 20
_HEX = re.compile('[0-9a-f]{64}')


class GfxPackError(ValueError):
    """Missing proof, corrupt/oversized data, or unsafe filesystem operation."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise GfxPackError('duplicate JSON member')
            result[key] = value
        return result
    def invalid(value):
        raise GfxPackError('non-finite JSON number')
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise GfxPackError('invalid pack JSON') from error


def _positive(value):
    return type(value) is int and value > 0


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _contract(record, max_frame_bytes):
    # Snapshot caller-owned data; never infer authority from the package itself.
    try:
        canonical = _canonical(record)
        record = _json(canonical)
    except (TypeError, ValueError, RecursionError) as error:
        raise GfxPackError('invalid expected completion') from error
    if not isinstance(record, dict) or not isinstance(record.get('key'), str):
        raise GfxPackError('expected completion/key required')
    names = ['complete.json']
    if record.get('format') == 'framivra-svg-completion/v1':
        source = record.get('input')
        if not isinstance(source, dict):
            raise GfxPackError('SVG input record required')
        n, width, height = (source.get(k) for k in ('frame_count', 'width', 'height'))
    else:
        n, width, height = (record.get(k) for k in ('frames', 'width', 'height'))
        if type(record.get('schema')) is not int or record['schema'] != 1 or record.get('mixed_producer'):
            raise GfxPackError('unsupported/mixed completion')
        if 'backend_identity' in record:
            runs = record.get('render_runs')
            if not isinstance(runs, list) or not runs or not _positive(n) or len(runs) > n:
                raise GfxPackError('producer receipts required')
            covered, ids = [], set()
            for run in runs:
                if not isinstance(run, dict):
                    raise GfxPackError('invalid producer run')
                run_id = run.get('run_id')
                if (not isinstance(run_id, str) or not re.fullmatch('[0-9a-f]{32}', run_id)
                        or run_id in ids or not isinstance(run.get('frames'), list)):
                    raise GfxPackError('invalid/duplicate producer run')
                ids.add(run_id)
                covered.extend(run['frames'])
                names.extend(f'.electron/{run_id}/{name}' for name in
                             ('request.json', 'adapter-result.json', 'result.json'))
            if len(covered) != n or any(type(f) is not int or f != i for i, f in enumerate(covered)):
                raise GfxPackError('producer runs do not cover expected frames')
        elif not isinstance(record.get('environment'), dict) or record['environment'].get('status') != 'input-bound':
            raise GfxPackError('verified environment completion required; no legacy adoption')
    hashes = record.get('sha256')
    if (not all(_positive(x) for x in (n, width, height)) or not isinstance(hashes, list)
            or len(hashes) != n or any(not isinstance(x, str) or not _HEX.fullmatch(x) for x in hashes)):
        raise GfxPackError('invalid expected frame manifest/dimensions')
    limit = width * height * 8 + 65536 if max_frame_bytes is None else max_frame_bytes
    if not _positive(limit):
        raise GfxPackError('positive trusted max_frame_bytes required')
    # Budgets derive from trusted caller data, never dimensions claimed by a pack.
    complete_limit = len(canonical) * 2 + 65536
    meta_limit = complete_limit + n * 32768 + 65536
    header_limit = 4096 + len(canonical) * 2 + (n + len(names)) * 512
    return record, canonical, names, limit, complete_limit, meta_limit, header_limit


def _path(path, root=None):
    path = Path(os.path.abspath(path))
    if root is not None:
        root = Path(os.path.abspath(root))
        if root not in path.parents:
            raise GfxPackError('path escaped caller root')
    if path.parent.resolve(strict=True) != path.parent:
        raise GfxPackError('symlinked parent is not supported')
    return path


def _capability():
    if sys.platform != 'linux' or fstxn.write_capability() != 'fd-anchored':
        raise GfxPackError('gfx packs require Linux fd-anchored I/O')


def _version(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextlib.contextmanager
def _open_regular(path, *, root=None):
    path = _path(path, root)
    identity = fstxn.cleanup_identity(path)
    if identity is None or identity[-1][2] != stat.S_IFREG:
        raise GfxPackError('missing, linked or special input')
    with fstxn.pinned_parent(path, create=False, root=root) as (parent, name, canonical):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(fd)
            if (canonical != path or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or fstxn.entry_identity(before) != identity[-1]):
                raise GfxPackError('linked or changed input')
            def check():
                if (_version(os.fstat(fd)) != _version(before)
                        or fstxn.cleanup_identity(path) != identity):
                    raise GfxPackError('input changed while reading')
            check()
            yield stream, before.st_size, check
            check()


def _read(path, limit, root):
    with _open_regular(path, root=root) as (stream, size, check):
        if size > limit:
            raise GfxPackError('input byte limit exceeded')
        data = stream.read(limit + 1)
        if len(data) != size or len(data) > limit:
            raise GfxPackError('short/oversized input')
        check()
        return data


def _png(data, width, height):
    reason = _png_stream_invalid_reason(io.BytesIO(data), len(data), width=width, height=height)
    if reason:
        raise GfxPackError(f'invalid PNG: {reason}')


def _bound(size):
    # zlib compressBound, for the default window/memory settings used here.
    return size + (size >> 12) + (size >> 14) + (size >> 25) + 13


def pack_page(directory, target, *, expected_record, max_frame_bytes=None,
              verify_inputs=None, root=None):
    """Pack an already authenticated page; preserve every selected byte exactly.

    Parents must exist. Existing regular targets are atomically replaced only
    after all frames/sidecars and optional caller input recheck pass. Source pages
    are never mutated or removed. Returns storage statistics (logical bytes).
    """
    _capability()
    record, canonical, names, limit, complete_limit, meta_limit, header_limit = _contract(expected_record, max_frame_bytes)
    directory, target = _path(directory, root), _path(target, root)
    if directory == target or directory in target.parents:
        raise GfxPackError('pack target must be outside the source page')
    page_identity = fstxn.cleanup_identity(directory)
    if page_identity is None or page_identity[-1][2] != stat.S_IFDIR:
        raise GfxPackError('source page is not a canonical directory')
    parent_identity = fstxn.cleanup_identity(target.parent)
    n = len(record['sha256'])
    dimensions = record.get('input', {}) if record.get('format') == 'framivra-svg-completion/v1' else record
    blobs, blocks, indices, metadata, seen = [], [], [], [], {}
    pending = bytearray()
    complete = _read(directory / names[0], complete_limit, root)
    if _canonical(_json(complete)) != canonical:
        raise GfxPackError('source completion differs from trusted expected record')
    source_bytes = 0
    with fstxn.locked_write(target, root=root) as temporary:
        with temporary.open('wb') as output:
            output.write(_MAGIC)
            def flush():
                if not pending:
                    return
                compressed = zlib.compress(pending, level=6)
                blocks.append({'offset': output.tell(), 'bytes': len(compressed),
                               'raw_bytes': len(pending), 'sha256': _digest(compressed)})
                output.write(compressed)
                pending.clear()
            def add(data):
                digest = _digest(data)
                if digest in seen:
                    return seen[digest]
                if pending and len(pending) + len(data) > _BLOCK_BYTES:
                    flush()
                index = len(blobs)
                seen[digest] = index
                blobs.append({'sha256': digest, 'bytes': len(data),
                              'block': len(blocks), 'offset': len(pending)})
                pending.extend(data)
                return index
            for i, expected in enumerate(record['sha256']):
                data = _read(directory / f'f{i:04d}.png', limit, root)
                _png(data, dimensions['width'], dimensions['height'])
                if _digest(data) != expected:
                    raise GfxPackError(f'frame {i} differs from completion')
                source_bytes += len(data)
                indices.append(add(data))
            meta_bytes = 0
            for name in names:
                data = complete if name == 'complete.json' else _read(directory / name, meta_limit, root)
                meta_bytes += len(data)
                if meta_bytes > meta_limit:
                    raise GfxPackError('metadata byte limit exceeded')
                metadata.append({'name': name, 'blob': add(data)})
            flush()
            header = _canonical({'format': 'vtx.gfx-pack/1', 'record': record,
                                 'record_sha256': _digest(canonical), 'frames': indices,
                                 'blobs': blobs, 'blocks': blocks, 'metadata': metadata})
            if len(header) > header_limit:
                raise GfxPackError('pack index byte limit exceeded')
            output.write(header)
            output.write(_FOOTER.pack(len(header), hashlib.sha256(header).digest(), _END))
            packed_bytes = output.tell()
        if (_read(directory / 'complete.json', complete_limit, root) != complete
                or fstxn.cleanup_identity(directory) != page_identity
                or fstxn.cleanup_identity(target.parent) != parent_identity):
            raise GfxPackError('page/target identity changed before publication')
        if verify_inputs is not None:
            verify_inputs()
        if (fstxn.cleanup_identity(target.parent) != parent_identity
                or fstxn.cleanup_identity(directory) != page_identity):
            raise GfxPackError('page/target identity changed in caller recheck')
    return {'frames': n, 'unique_pngs': len(set(indices)), 'source_png_bytes': source_bytes,
            'packed_bytes': packed_bytes}


def inspect_pack(pack, *, max_header_bytes=8 << 20, root=None):
    """Read a bounded header, including ``record``, WITHOUT authenticating it.

    The record is untrusted data for the caller's existing environment/input
    gate, never proof by itself. No payload is decoded and no files are written.
    Caller must supply trusted frame/byte bounds to PagePack after that gate.
    """
    _capability()
    if not _positive(max_header_bytes):
        raise GfxPackError('positive trusted header limit required')
    with _open_regular(pack, root=root) as (stream, size, check):
        if size < len(_MAGIC) + _FOOTER.size or stream.read(len(_MAGIC)) != _MAGIC:
            raise GfxPackError('unsupported/truncated pack')
        stream.seek(-_FOOTER.size, os.SEEK_END)
        length, digest, end = _FOOTER.unpack(stream.read(_FOOTER.size))
        if end != _END or not 0 < length <= max_header_bytes or length > size - _FOOTER.size - len(_MAGIC):
            raise GfxPackError('invalid/oversized pack header')
        stream.seek(size - _FOOTER.size - length)
        data = stream.read(length)
        if hashlib.sha256(data).digest() != digest:
            raise GfxPackError('pack header digest mismatch')
        header = _json(data)
        if (not isinstance(header, dict) or _canonical(header) != data
                or header.get('format') != 'vtx.gfx-pack/1' or not isinstance(header.get('record'), dict)
                or header.get('record_sha256') != _digest(_canonical(header['record']))):
            raise GfxPackError('invalid/noncanonical pack header')
        check()
        return header


class PagePack:
    """Scoped read-only page snapshot. Use ``with PagePack(...) as page``.

    ``read_frame(i)`` validates exact PNG bytes and keeps at most one decoded
    block. This verifies storage integrity, NOT current backend/input eligibility.
    Unrequested blocks are not silently claimed to have been fully verified.
    """
    def __init__(self, pack, *, expected_record, max_frame_bytes=None, root=None):
        self.pack, self.root = pack, root
        (self.record, self._canonical_record, self._names, self._limit,
         self._complete_limit, self._meta_limit, self._header_limit) = _contract(expected_record, max_frame_bytes)
        self._context = None
        self._cached = None

    def __enter__(self):
        _capability()
        if self._context is not None:
            raise GfxPackError('pack snapshot already open')
        self._context = _open_regular(self.pack, root=self.root)
        try:
            self._stream, self.packed_bytes, self._check = self._context.__enter__()
            self._load()
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(self, *args):
        context, self._context = self._context, None
        self._cached = None
        if context is not None:
            return context.__exit__(*args)

    def _load(self):
        stream = self._stream
        n = len(self.record['sha256'])
        total_limit = n * self._limit + self._meta_limit
        if not len(_MAGIC) + _FOOTER.size < self.packed_bytes <= _bound(total_limit) + (n + len(self._names)) * 13 + self._header_limit + 64:
            raise GfxPackError('pack byte limit exceeded/truncated')
        if stream.read(len(_MAGIC)) != _MAGIC:
            raise GfxPackError('unsupported pack format')
        stream.seek(-_FOOTER.size, os.SEEK_END)
        length, digest, end = _FOOTER.unpack(stream.read(_FOOTER.size))
        if end != _END or not 0 < length <= self._header_limit or length > self.packed_bytes - _FOOTER.size - len(_MAGIC):
            raise GfxPackError('invalid pack footer/index length')
        header_start = self.packed_bytes - _FOOTER.size - length
        stream.seek(header_start)
        data = stream.read(length)
        if hashlib.sha256(data).digest() != digest:
            raise GfxPackError('pack index digest mismatch')
        header = _json(data)
        if (_canonical(header) != data or not isinstance(header, dict)
                or set(header) != {'format', 'record', 'record_sha256', 'frames', 'blobs', 'blocks', 'metadata'}
                or header['format'] != 'vtx.gfx-pack/1' or header['record_sha256'] != _digest(self._canonical_record)
                or _canonical(header['record']) != self._canonical_record):
            raise GfxPackError('noncanonical/mismatched pack index')
        self._blobs, self._blocks, self._frames = (header[k] for k in ('blobs', 'blocks', 'frames'))
        if (not all(isinstance(x, list) for x in (self._blobs, self._blocks, self._frames, header['metadata']))
                or len(self._frames) != n or not 0 < len(self._blobs) <= n + len(self._names)
                or not 0 < len(self._blocks) <= len(self._blobs)):
            raise GfxPackError('pack member count exceeds expected manifest')
        offset, raw_total = len(_MAGIC), 0
        for block in self._blocks:
            if (not isinstance(block, dict) or set(block) != {'offset', 'bytes', 'raw_bytes', 'sha256'}
                    or type(block['offset']) is not int or block['offset'] != offset
                    or not _positive(block['raw_bytes']) or block['raw_bytes'] > max(_BLOCK_BYTES, self._limit, self._meta_limit)
                    or not _positive(block['bytes']) or block['bytes'] > _bound(block['raw_bytes'])
                    or not isinstance(block['sha256'], str) or not _HEX.fullmatch(block['sha256'])):
                raise GfxPackError('invalid/oversized compression block')
            offset += block['bytes']
            raw_total += block['raw_bytes']
        if offset != header_start or raw_total > total_limit:
            raise GfxPackError('pack payload bounds mismatch')
        positions, hashes = [0] * len(self._blocks), set()
        for blob in self._blobs:
            if (not isinstance(blob, dict) or set(blob) != {'sha256', 'bytes', 'block', 'offset'}
                    or not isinstance(blob['sha256'], str) or not _HEX.fullmatch(blob['sha256'])
                    or blob['sha256'] in hashes or not _positive(blob['bytes'])
                    or type(blob['block']) is not int or not 0 <= blob['block'] < len(positions)
                    or type(blob['offset']) is not int or blob['offset'] != positions[blob['block']]):
                raise GfxPackError('invalid/nonunique blob index')
            positions[blob['block']] += blob['bytes']
            hashes.add(blob['sha256'])
        if positions != [b['raw_bytes'] for b in self._blocks]:
            raise GfxPackError('blob/block coverage mismatch')
        def index(value):
            return type(value) is int and 0 <= value < len(self._blobs)
        if any(not index(v) or self._blobs[v]['bytes'] > self._limit or self._blobs[v]['sha256'] != self.record['sha256'][i]
               for i, v in enumerate(self._frames)):
            raise GfxPackError('frame manifest mismatch')
        meta = header['metadata']
        if (len(meta) != len(self._names) or any(not isinstance(m, dict) or set(m) != {'name', 'blob'}
                or m['name'] != name or not index(m['blob']) for m, name in zip(meta, self._names))):
            raise GfxPackError('unexpected metadata path/member')
        self._metadata = {m['name']: m['blob'] for m in meta}
        if (set(self._frames) | set(self._metadata.values()) != set(range(len(self._blobs)))
                or sum(self._blobs[i]['bytes'] for i in self._metadata.values()) > self._meta_limit
                or self._blobs[self._metadata['complete.json']]['bytes'] > self._complete_limit):
            raise GfxPackError('unreferenced/oversized metadata blob')
        if _canonical(_json(self.read_metadata('complete.json'))) != self._canonical_record:
            raise GfxPackError('stored completion differs from trusted expected record')
        self._check()

    def _blob(self, index):
        if self._context is None:
            raise GfxPackError('use an open PagePack context')
        self._check()
        blob = self._blobs[index]
        block_index = blob['block']
        if self._cached is None or self._cached[0] != block_index:
            block = self._blocks[block_index]
            self._stream.seek(block['offset'])
            compressed = self._stream.read(block['bytes'])
            if len(compressed) != block['bytes'] or _digest(compressed) != block['sha256']:
                raise GfxPackError('compressed block digest mismatch/truncated')
            decoder = zlib.decompressobj()
            try:
                raw = decoder.decompress(compressed, block['raw_bytes'] + 1)
            except zlib.error as error:
                raise GfxPackError('corrupt zlib compression block') from error
            if (len(raw) != block['raw_bytes'] or not decoder.eof
                    or decoder.unused_data or decoder.unconsumed_tail):
                raise GfxPackError('truncated/trailing/oversized zlib compression block')
            self._cached = block_index, raw
        start = blob['offset']
        data = self._cached[1][start:start + blob['bytes']]
        if len(data) != blob['bytes'] or _digest(data) != blob['sha256']:
            raise GfxPackError('blob digest mismatch')
        self._check()
        return data

    @property
    def frame_count(self):
        return len(self.record['sha256'])

    @property
    def metadata_names(self):
        """Exact fixed sidecars carried by this caller-bound page contract."""
        return tuple(self._names)

    def read_frame(self, index):
        if type(index) is not int or not 0 <= index < self.frame_count:
            raise GfxPackError('frame index outside expected page')
        data = self._blob(self._frames[index])
        dimensions = self.record['input'] if self.record.get('format') == 'framivra-svg-completion/v1' else self.record
        _png(data, dimensions['width'], dimensions['height'])
        return data

    def read_metadata(self, name):
        """Read only a fixed carried sidecar; names are never extraction authority."""
        if self._context is None:
            raise GfxPackError('use an open PagePack context')
        if name not in self._metadata:
            raise GfxPackError('metadata name not in fixed whitelist')
        return self._blob(self._metadata[name])

    def verify(self):
        """Verify ALL unique PNGs and sidecars, unlike a scoped frame read."""
        seen = set()
        for i, index in enumerate(self._frames):
            if index not in seen:
                self.read_frame(i)
                seen.add(index)
        for name in self._names:
            self.read_metadata(name)
        self._check()
        return {'frames': self.frame_count, 'unique_pngs': len(seen),
                'packed_bytes': self.packed_bytes}


def read_frame(pack, index, *, expected_record, max_frame_bytes=None, root=None):
    """Convenience single-frame read; repeated reads should share PagePack."""
    with PagePack(pack, expected_record=expected_record, max_frame_bytes=max_frame_bytes, root=root) as page:
        return page.read_frame(index)


def _rename_new(parent, source, target, *, destination=None):
    # Linux atomic no-replace: unlike rename(), cannot clobber a raced empty dir.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise GfxPackError('atomic directory no-replace is unsupported')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(source), parent if destination is None else destination,
              os.fsencode(target), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def restore_page(pack, directory, *, expected_record, max_frame_bytes=None,
                 verify_inputs=None, root=None):
    """Verify then publish a new page, or populate an existing lock-only page.

    Never overwrite payloads or remove protected locks. A new directory is one
    atomic rename; a lock-only directory receives no-replace files and the exact
    original complete.json LAST as commit marker. Failure during that latter
    publication can leave incomplete PNGs, never a newly certified partial page.
    Each PNG is a fresh inode. Caller controls dry-run and reruns its normal
    consumer completion/environment gate after restoration.
    """
    _capability()
    directory = _path(directory, root)
    parent_identity = fstxn.cleanup_identity(directory.parent)
    with PagePack(pack, expected_record=expected_record, max_frame_bytes=max_frame_bytes, root=root) as page:
        with contextlib.ExitStack() as stack, fstxn.file_lock(directory, root=root), fstxn.pinned_parent(directory, create=False, root=root) as (parent, name, _):
            existing = fstxn.cleanup_identity(directory)
            destination = None
            metadata_dirs = {parent.as_posix() for meta in page._names for parent in Path(meta).parents
                             if parent != Path('.')}
            def locks_only(descriptor, prefix=''):
                # A fresh open description avoids sharing a prior directory offset.
                scan = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                try:
                    children = os.listdir(scan)
                finally:
                    os.close(scan)
                for child in children:
                    info = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
                    relative = prefix + child
                    if relative in metadata_dirs and stat.S_ISDIR(info.st_mode):
                        nested = os.open(child, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                        try:
                            if fstxn.entry_identity(os.fstat(nested)) != fstxn.entry_identity(info):
                                raise GfxPackError('restore metadata directory changed')
                            locks_only(nested, relative + '/')
                        finally:
                            os.close(nested)
                    elif not child.endswith('.lock') or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise GfxPackError('restore target contains non-lock or linked entries')
            if existing is not None:
                if existing[-1][2] != stat.S_IFDIR:
                    raise GfxPackError('restore target is not a regular directory')
                destination = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                stack.callback(os.close, destination)
                if fstxn.entry_identity(os.fstat(destination)) != existing[-1]:
                    raise GfxPackError('restore target identity changed')
                locks_only(destination)
            temporary = '.' + name + '.' + secrets.token_hex(16) + '.gfx-pack.tmp'
            os.mkdir(temporary, 0o700, dir_fd=parent)
            info = fstxn.entry_identity(os.stat(temporary, dir_fd=parent, follow_symlinks=False))
            stage = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                def write(relative, data):
                    # Only internally generated, contract-validated fixed paths.
                    parts = relative.split('/')
                    descriptors = []
                    current = stage
                    try:
                        for part in parts[:-1]:
                            try:
                                os.mkdir(part, 0o700, dir_fd=current)
                            except FileExistsError:
                                pass
                            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                            descriptors.append(current)
                        fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=current)
                        with os.fdopen(fd, 'wb') as output:
                            output.write(data)
                    finally:
                        for descriptor in reversed(descriptors):
                            os.close(descriptor)
                for i in range(page.frame_count):
                    write(f'f{i:04d}.png', page.read_frame(i))
                for meta in page._names:
                    write(meta, page.read_metadata(meta))
                page._check()
                if verify_inputs is not None:
                    verify_inputs()
                if (fstxn.cleanup_identity(directory.parent) != parent_identity
                        or fstxn.entry_identity(os.stat(temporary, dir_fd=parent, follow_symlinks=False)) != info
                        or fstxn.entry_identity(os.fstat(stage)) != info):
                    raise GfxPackError('restore staging/parent identity changed')
                if destination is None:
                    _rename_new(parent, temporary, name)
                else:
                    if fstxn.cleanup_identity(directory) != existing:
                        raise GfxPackError('restore target changed before publication')
                    locks_only(destination)
                    def publish(relative):
                        # Fixed sidecars publish individually into whitelisted
                        # metadata directories, retaining any nested lock inodes.
                        parts = relative.split('/')
                        with contextlib.ExitStack() as parents:
                            source_parent, target_parent = stage, destination
                            for part in parts[:-1]:
                                source_parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                                        dir_fd=source_parent)
                                parents.callback(os.close, source_parent)
                                try:
                                    os.mkdir(part, 0o700, dir_fd=target_parent)
                                except FileExistsError:
                                    pass
                                target_parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                                        dir_fd=target_parent)
                                parents.callback(os.close, target_parent)
                            _rename_new(source_parent, parts[-1], parts[-1], destination=target_parent)
                    for child in [*(f'f{i:04d}.png' for i in range(page.frame_count)),
                                  *(meta for meta in page._names if meta != 'complete.json')]:
                        publish(child)
                    page._check()
                    if verify_inputs is not None:
                        verify_inputs()
                    if fstxn.cleanup_identity(directory) != existing:
                        raise GfxPackError('restore target changed before marker publication')
                    # Marker bytes are historical producer data, never a new signature.
                    publish('complete.json')
            finally:
                os.close(stage)
                try:
                    fstxn.remove_at(parent, temporary, info, preserve=lambda _: False)
                except FileNotFoundError:
                    pass
    return directory
