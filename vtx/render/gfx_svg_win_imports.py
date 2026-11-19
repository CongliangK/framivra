"""Internal no-launch declaration inventory for the fixed Windows SVG package.

Only staged on-disk PE32+ declarations are inspected. The private symbol
opt-in adds lookup names and ordinals, not binding or export resolution. A
direct-bin basename match is only a package candidate. No API-set hosts,
activation contexts, dynamically selected resources, or system images are
resolved. The result has no execution or publication authority.
"""
from bisect import bisect_right
import ctypes as C
from ctypes import wintypes as W
import ntpath
from pathlib import PureWindowsPath
import re
from struct import unpack_from
from time import monotonic

from . import gfx_svg_win_snapshot as snapshot
from .gfx_svg_win_fontconfig import _DLLS, _FONTCONFIG


_LEAF = re.compile(r'[a-z0-9_+\-][a-z0-9_.+\-]{0,126}\.(?:dll|exe|com|drv|ocx)', re.ASCII)
_API_SET = re.compile(r'(?:api|ext)-ms-[a-z0-9-]+\.dll', re.ASCII)
_MAX_READS, _MAX_BYTES = 1024, 128 * 1024  # per member; snapshot also caps the number of pins
_MAX_DESCRIPTORS, _NAME_BYTES = 256, 256
_SYMBOL_READS, _SYMBOL_READ_BYTES = 24 * 1024, 3 * 1024**2  # per member, opt-in only
_MAX_THUNKS, _MAX_NAME_TEXT = 8192, 256 * 1024  # per member, counting repeats
_MAX_SYMBOL_NAME = 1024  # includes the NUL, not the hint
_PACKAGE_READS, _PACKAGE_READ_BYTES = 160 * 1024, 16 * 1024**2
_PACKAGE_THUNKS, _PACKAGE_NAME_TEXT, _SYMBOL_SECONDS = 65536, 2 * 1024**2, 30
_ORDINAL = 1 << 63


class _SymbolBudget:
    def __init__(self):
        self.deadline = monotonic() + _SYMBOL_SECONDS
        self.reads = self.bytes = self.thunks = self.text = 0

    def check_time(self):
        # Cooperative bound for metadata only; cannot interrupt a synchronous OS read.
        if monotonic() >= self.deadline:
            raise ValueError('SVG PE symbol inventory exceeds its metadata time budget')

    def read(self, length):
        self.check_time()
        self.reads += 1
        self.bytes += length
        if self.reads > _PACKAGE_READS or self.bytes > _PACKAGE_READ_BYTES:
            raise ValueError('SVG PE symbol inventory exceeds its package read budget')

    def add(self, thunks, text=0):
        self.thunks += thunks
        self.text += text
        if self.thunks > _PACKAGE_THUNKS or self.text > _PACKAGE_NAME_TEXT:
            raise ValueError('SVG PE symbol inventory exceeds its package output budget')


def _module_name(spelling):
    name = spelling.lower()
    reserved = getattr(ntpath, 'isreserved', lambda value: PureWindowsPath(value).is_reserved())
    if not spelling.isascii() or not _LEAF.fullmatch(name) or reserved(name):
        raise ValueError('SVG PE has an invalid module name')
    return name


def _disjoint(spans):
    ordered = sorted(spans)
    return all(left[1] <= right[0] for left, right in zip(ordered, ordered[1:]))


def _symbol_declarations(rows, metadata, directory, region, read, budget):
    # The IAT directory describes storage, not a source of declaration values.
    # If present it must contain the ordinary IATs and cannot alias other data.
    bound = directory(11)
    iat = directory(12)

    def span(rva, length, alignment):
        if not rva or rva % alignment or length < alignment or length % alignment:
            raise ValueError('SVG PE has an invalid or unaligned import table')
        start, _ = region(rva, length)
        return start, start + length

    if any(bound):
        metadata.append(span(*bound, 1))  # opaque binding metadata; never decoded
    iat_span = span(*iat, 8) if any(iat) else None
    ordinary_iats, lookup_tables = [], []
    thunks = 0
    for kind, name, row in rows:
        if kind == 'ordinary':
            lookup, stamp, forwarders, _, address = unpack_from('<5I', row)
            if not lookup:
                if stamp or forwarders or any(bound):
                    raise ValueError('SVG PE cannot use a possibly bound FirstThunk as declarations')
                lookup = address
                fallback = True
            else:
                fallback = False
        else:
            _, _, handle, address, lookup, bound_delay, unload, stamp = unpack_from('<8I', row)
            if bound_delay or unload or stamp:
                raise ValueError('SVG PE has unsupported delay binding or unload metadata')
            metadata.append(span(handle, 8, 8))  # storage only, never a source of names
            fallback = False
        if not lookup or lookup % 8 or not address or address % 8:
            raise ValueError('SVG PE has a missing or unaligned thunk table')
        start, room = region(lookup, 8)
        values = []
        for index in range(min(room // 8, _MAX_THUNKS - thunks + 1)):
            value, = unpack_from('<Q', read(start + 8 * index, 8))
            if not value:
                break
            budget.add(1)
            thunks += 1
            if thunks > _MAX_THUNKS:
                raise ValueError('SVG PE exceeds its member thunk budget')
            values.append(value)
        else:
            raise ValueError('SVG PE thunk table has no bounded terminator')
        if not values:
            raise ValueError('SVG PE has an empty thunk table')
        width = 8 * (len(values) + 1)
        target = span(address, width, 8)
        if not fallback:
            metadata.append((start, start + width))
        if kind == 'ordinary':
            if iat_span and not iat_span[0] <= target[0] < target[1] <= iat_span[1]:
                raise ValueError('SVG PE ordinary IAT is outside its directory')
            ordinary_iats.append(target)
        else:
            metadata.append(target)
        lookup_tables.append((kind, name, values, None if fallback else target))

    # Validate the complete table geometry before decoding any candidate symbol.
    if not _disjoint(metadata + ordinary_iats) or (iat_span and not _disjoint(metadata + [iat_span])):
        raise ValueError('SVG PE has overlapping import table records')
    reserved = sorted(metadata + ([iat_span] if iat_span else ordinary_iats))
    ends = [end for _, end in reserved]
    cache, hints = {}, []
    result = {'ordinary': {}, 'delay': {}}
    text = 0
    for kind, name, values, address in lookup_tables:
        if address:
            # Check only the shape of this separate IAT. Its entries may be
            # opaque bound values or (delay) helper pointers, never symbols.
            start, end = address
            for cursor in range(start, end - 8, 4096):
                block = read(cursor, min(4096, end - 8 - cursor))
                if any(block[i:i + 8] == bytes(8) for i in range(0, len(block), 8)):
                    raise ValueError('SVG PE IAT terminates before its lookup table')
            if any(read(end - 8, 8)):
                raise ValueError('SVG PE IAT has no matching terminator')
        symbols = result[kind][name] = []
        for value in values:
            if value & _ORDINAL:
                if value & (_ORDINAL - 0x10000):
                    raise ValueError('SVG PE ordinal thunk has reserved bits set')
                symbols.append({'ordinal': value & 0xffff})
                continue
            if value >= 1 << 31 or value % 2:
                raise ValueError('SVG PE has an invalid or unaligned import-by-name RVA')
            if value not in cache:
                # Hint is not identity. Only the NUL-terminated ASCII symbol is
                # returned; punctuation and case are not normalized as paths.
                start, room = region(value, 4)  # two-byte hint, at least one byte and NUL
                limit = start + min(room, 2 + _MAX_SYMBOL_NAME)
                nxt = bisect_right(ends, start)
                if nxt < len(reserved):
                    limit = min(limit, reserved[nxt][0])
                data = bytearray()
                for cursor in range(start + 2, limit, 256):
                    part, nul, _ = read(cursor, min(256, limit - cursor)).partition(b'\0')
                    data.extend(part)
                    if nul:
                        break
                else:
                    raise ValueError('SVG PE symbol name has no bounded non-overlapping terminator')
                if not data or not data.isascii():
                    raise ValueError('SVG PE has an invalid symbol name')
                cache[value] = data.decode('ascii')
                hints.append((start, start + 2 + len(data) + 1))
            spelling = cache[value]
            text += len(spelling)
            budget.add(0, len(spelling))
            if text > _MAX_NAME_TEXT:
                raise ValueError('SVG PE exceeds its member symbol name budget')
            symbols.append({'name': spelling})
    if not _disjoint(hints):  # identical shared records used the cache; partial aliasing is ambiguous
        raise ValueError('SVG PE has overlapping import-by-name records')
    budget.check_time()
    return result


def _declarations(read_at, size, *, symbol_budget=None):
    """Parse module declarations, optionally with symbols, without opening or mapping a path."""
    reads = total = 0
    max_reads, max_bytes = (_MAX_READS, _MAX_BYTES) if symbol_budget is None else (_SYMBOL_READS, _SYMBOL_READ_BYTES)

    def read(offset, count):
        nonlocal reads, total
        if not 0 < count <= 4096 or not 0 <= offset <= size - count:
            raise ValueError('SVG PE metadata is outside file bounds')
        reads, total = reads + 1, total + count
        if reads > max_reads or total > max_bytes:
            raise ValueError('SVG PE metadata exceeds its read budget')
        if symbol_budget is not None:
            symbol_budget.read(count)
        data = read_at(offset, count)
        if symbol_budget is not None:
            symbol_budget.check_time()
        if len(data) != count:
            raise ValueError('SVG PE metadata was truncated')
        return data

    dos = read(0, 64)
    pe, = unpack_from('<I', dos, 60)
    if dos[:2] != b'MZ' or not 64 <= pe <= 64 * 1024:
        raise ValueError('SVG PE has an invalid DOS header')
    coff = read(pe, 24)
    machine, count, _, _, _, opt_size, flags = unpack_from('<HHIIIHH', coff, 4)
    if (coff[:4] != b'PE\0\0' or machine != 0x8664 or not flags & 2
            or not 1 <= count <= 96 or not 112 <= opt_size <= 240):
        raise ValueError('SVG PE requires a bounded AMD64 image header')
    opt = read(pe + 24, opt_size)
    section_align, file_align = unpack_from('<II', opt, 32)
    image_end, headers_end = unpack_from('<II', opt, 56)
    dirs, = unpack_from('<I', opt, 108)
    section_start = pe + 24 + opt_size
    if (opt[:2] != b'\x0b\x02' or dirs > 16 or opt_size != 112 + 8 * dirs
            or not 512 <= file_align <= 65536 or file_align & (file_align - 1)
            or section_align < max(4096, file_align) or section_align & (section_align - 1)
            or section_start + 40 * count > headers_end or headers_end > min(size, image_end)
            or headers_end % file_align or image_end % section_align):
        raise ValueError('SVG PE has an invalid PE32+ header layout')

    sections = read(section_start, 40 * count)
    virtual, raw, readable = [], [], []
    for i in range(0, len(sections), 40):
        vsize, rva, rsize, offset = unpack_from('<IIII', sections, i + 8)
        characteristics, = unpack_from('<I', sections, i + 36)
        extent = max(vsize, rsize)
        end = rva + (extent + section_align - 1) // section_align * section_align
        if not extent or not headers_end <= rva < end <= image_end or rva % section_align:
            raise ValueError('SVG PE has an invalid virtual section range')
        virtual.append((rva, end))
        if rsize:
            if not headers_end <= offset <= size - rsize or offset % file_align or rsize % file_align:
                raise ValueError('SVG PE has an invalid raw section range')
            raw.append((offset, offset + rsize))
            if characteristics & 0x40000000:  # IMAGE_SCN_MEM_READ; do not use raw padding or zero-fill
                readable.append((rva, rva + min(vsize, rsize), offset))
    if not _disjoint(virtual) or not _disjoint(raw):
        raise ValueError('SVG PE has overlapping section ranges')

    def region(rva, count):
        for start, end, offset in readable:
            if start <= rva <= end - count:
                return offset + rva - start, end - rva
        raise ValueError('SVG PE metadata does not map to one readable raw section')

    def directory(index):
        return unpack_from('<II', opt, 112 + 8 * index) if index < dirs else (0, 0)

    directories = []
    for index, kind, width in ((1, 'ordinary', 20), (13, 'delay', 32)):
        rva, length = directory(index)
        if rva or length:
            if not rva or length < width or (symbol_budget is not None and rva % 4):
                raise ValueError('SVG PE has an invalid import directory')
            offset, _ = region(rva, length)
            directories.append((kind, width, offset, length))
    if not _disjoint((offset, offset + length) for _, _, offset, length in directories):
        raise ValueError('SVG PE has overlapping import directories')

    names, used, metadata, rows = {'ordinary': [], 'delay': []}, set(), [], []
    for kind, width, offset, length in directories:
        # The declared span can contain more than the descriptor array. Validate
        # the whole raw mapping above; stop reading at the bounded zero row.
        for i in range(min(length // width, _MAX_DESCRIPTORS)):
            end = offset + width * (i + 1)
            row = read(end - width, width)
            if not any(row):
                metadata.append((offset, end))
                break
            if kind == 'delay':
                flags, name_rva = unpack_from('<II', row)
                if flags != 1:  # accept only the RVA form, not legacy VA or unknown bits
                    raise ValueError('SVG PE has unsupported delay descriptor attributes')
            else:
                name_rva, = unpack_from('<I', row, 12)
            start, available = region(name_rva, 1)
            spelling, nul, _ = read(start, min(_NAME_BYTES, available)).partition(b'\0')
            if not nul:
                raise ValueError('SVG PE module name has no bounded terminator')
            if not spelling.isascii():
                raise ValueError('SVG PE has an invalid module name')
            name = _module_name(spelling.decode('ascii'))
            if name in used:
                raise ValueError('SVG PE has duplicate module declarations')
            used.add(name)
            names[kind].append(name)
            metadata.append((start, start + len(spelling) + 1))
            if symbol_budget is not None:
                rows.append((kind, name, row))
        else:
            raise ValueError('SVG PE import table has no bounded terminator')
    if not _disjoint(metadata):
        raise ValueError('SVG PE has overlapping module declaration records')
    if symbol_budget is not None:
        return _symbol_declarations(rows, metadata, directory, region, read, symbol_budget)
    return {kind: sorted(rows) for kind, rows in names.items()}


def _package_declarations(held, *, include_symbols=False):
    # No reopened paths or handle duplication. Read the same original handles
    # used for the staged tree's hashes, after the full manifest has matched.
    seek = snapshot._windows_api('SetFilePointerEx', W.BOOL, W.HANDLE, C.c_int64, C.c_void_p, W.DWORD)
    read = snapshot._windows_api('ReadFile', W.BOOL, W.HANDLE, C.c_void_p, W.DWORD,
                                 C.POINTER(W.DWORD), C.c_void_p)
    budget = _SymbolBudget() if include_symbols else None

    def read_member(image):
        def read_at(offset, length):
            data, count = C.create_string_buffer(length), W.DWORD()
            if not seek(image.handle, offset, None, 0):  # FILE_BEGIN
                raise C.WinError(C.get_last_error())
            if not read(image.handle, data, length, C.byref(count), None):
                raise C.WinError(C.get_last_error())
            return data[:count.value] if count.value == length else b''

        return _declarations(read_at, image.size, symbol_budget=budget) if budget else _declarations(read_at, image.size)

    candidates = {}
    for relative in held:
        # Keys are exact direct-bin names authenticated by the snapshot, not
        # paths assembled from import strings. Case-fold only ASCII leaf names.
        leaf = _module_name(relative.split('/')[1])
        if leaf in candidates:
            raise ValueError('SVG PE has ambiguous package candidate names')
        candidates[leaf] = relative

    def edge(name):
        if _API_SET.fullmatch(name):
            return {'name': name, 'kind': 'unresolved_api_set'}
        if name in candidates:
            return {'name': name, 'kind': 'package_candidate', 'member': candidates[name]}
        return {'name': name, 'kind': 'unresolved_external'}

    members = {}

    def walk(root):
        if root not in held:
            raise ValueError('SVG PE requires its fixed inventory roots')
        seen, pending = {root}, [root]
        while pending:
            relative = pending.pop()
            if relative not in members:
                image = held[relative]
                try:
                    declarations = read_member(image)
                except ValueError as error:
                    raise ValueError(f'{relative}: {error}') from None
                members[relative] = {'size': image.size, 'sha256': image.sha256, **{
                    kind: ([dict(edge(name), symbols=names[name]) for name in sorted(names)]
                           if include_symbols else [edge(name) for name in names])
                    for kind, names in declarations.items()}}
            for kind in ('ordinary', 'delay'):
                for row in members[relative][kind]:
                    target = row.get('member')
                    if target is not None and target not in seen:
                        seen.add(target)
                        pending.append(target)
        return sorted(seen)

    fontconfig = 'bin/' + _FONTCONFIG
    roots = {root: walk(root) for root in (snapshot._ENTRY, fontconfig)}
    # Corroborate the independent fixed native DLL profile, never update it
    # from the declarations or treat this comparison as actual load evidence.
    expected_dlls = {'bin/' + name: pin for name, pin in _DLLS.items()}
    if ({name: (members[name]['size'], members[name]['sha256']) for name in roots[fontconfig]}
            != expected_dlls):
        raise ValueError('SVG PE Fontconfig candidates differ from the fixed DLL profile')
    version = 2 if include_symbols else 1
    return {'schema': f'framivra/windows-svg-pe-declarations/{version}', 'tree_sha256': snapshot._TREE_SHA256,
            'roots': roots, 'members': dict(sorted(members.items()))}


def _inspect_package_imports(portable_root, *, scratch, include_symbols=False):
    """Private v1 module inventory; opt into v2 ordered symbol declarations only."""
    with snapshot._verified_snapshot(portable_root, scratch=scratch, retain_package_images=True) as (_, held):
        return _package_declarations(held, include_symbols=True) if include_symbols else _package_declarations(held)
