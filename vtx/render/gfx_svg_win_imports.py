"""Internal no-launch declaration inventory for the fixed Windows SVG package.

Only the staged on-disk PE32+ module names are inspected. A direct-bin basename
match is a package candidate, not a loader binding. No symbols, forwarders,
API-set hosts, activation contexts, dynamically selected resources, or system
libraries are resolved. The result has no execution or publication authority.
"""
import ctypes as C
from ctypes import wintypes as W
import ntpath
from pathlib import PureWindowsPath
import re
from struct import unpack_from

from . import gfx_svg_win_snapshot as snapshot
from .gfx_svg_win_fontconfig import _DLLS, _FONTCONFIG


_LEAF = re.compile(r'[a-z0-9_+\-][a-z0-9_.+\-]{0,126}\.(?:dll|exe|com|drv|ocx)', re.ASCII)
_API_SET = re.compile(r'(?:api|ext)-ms-[a-z0-9-]+\.dll', re.ASCII)
_MAX_READS, _MAX_BYTES = 1024, 128 * 1024  # per member; snapshot also caps the number of pins
_MAX_DESCRIPTORS, _NAME_BYTES = 256, 256


def _module_name(spelling):
    name = spelling.lower()
    reserved = getattr(ntpath, 'isreserved', lambda value: PureWindowsPath(value).is_reserved())
    if not spelling.isascii() or not _LEAF.fullmatch(name) or reserved(name):
        raise ValueError('SVG PE has an invalid module name')
    return name


def _disjoint(spans):
    ordered = sorted(spans)
    return all(left[1] <= right[0] for left, right in zip(ordered, ordered[1:]))


def _declarations(read_at, size):
    """Parse just the module declarations; read_at never opens or maps a path."""
    reads = total = 0

    def read(offset, count):
        nonlocal reads, total
        if not 0 < count <= 4096 or not 0 <= offset <= size - count:
            raise ValueError('SVG PE metadata is outside file bounds')
        reads, total = reads + 1, total + count
        if reads > _MAX_READS or total > _MAX_BYTES:
            raise ValueError('SVG PE metadata exceeds its read budget')
        data = read_at(offset, count)
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

    directories = []
    for index, kind, width in ((1, 'ordinary', 20), (13, 'delay', 32)):
        if index >= dirs:
            continue
        rva, length = unpack_from('<II', opt, 112 + 8 * index)
        if rva or length:
            if not rva or length < width:
                raise ValueError('SVG PE has an invalid import directory')
            offset, _ = region(rva, length)
            directories.append((kind, width, offset, length))
    if not _disjoint((offset, offset + length) for _, _, offset, length in directories):
        raise ValueError('SVG PE has overlapping import directories')

    names, used, metadata = {'ordinary': [], 'delay': []}, set(), []
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
        else:
            raise ValueError('SVG PE import table has no bounded terminator')
    if not _disjoint(metadata):
        raise ValueError('SVG PE has overlapping module declaration records')
    return {kind: sorted(rows) for kind, rows in names.items()}


def _package_declarations(held):
    # No reopened paths or handle duplication. Read the same original handles
    # used for the staged tree's hashes, after the full manifest has matched.
    seek = snapshot._windows_api('SetFilePointerEx', W.BOOL, W.HANDLE, C.c_int64, C.c_void_p, W.DWORD)
    read = snapshot._windows_api('ReadFile', W.BOOL, W.HANDLE, C.c_void_p, W.DWORD,
                                 C.POINTER(W.DWORD), C.c_void_p)

    def read_member(image):
        def read_at(offset, length):
            data, count = C.create_string_buffer(length), W.DWORD()
            if not seek(image.handle, offset, None, 0):  # FILE_BEGIN
                raise C.WinError(C.get_last_error())
            if not read(image.handle, data, length, C.byref(count), None):
                raise C.WinError(C.get_last_error())
            return data[:count.value] if count.value == length else b''

        return _declarations(read_at, image.size)

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
                    kind: [edge(name) for name in names] for kind, names in declarations.items()}}
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
    return {'schema': 'framivra/windows-svg-pe-declarations/1', 'tree_sha256': snapshot._TREE_SHA256,
            'roots': roots, 'members': dict(sorted(members.items()))}


def _inspect_package_imports(portable_root, *, scratch):
    """Inventory only the approved snapshot, returning data after pins are closed."""
    with snapshot._verified_snapshot(portable_root, scratch=scratch, retain_package_images=True) as (_, held):
        return _package_declarations(held)
