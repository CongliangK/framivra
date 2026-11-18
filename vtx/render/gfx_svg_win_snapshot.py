"""Private Windows SVG shell input selection, without launching a process.

The caller supplies a trusted, stable vendor extraction and owns an exclusive
scratch parent on a stable local volume mapping. Source and scratch chains
are pinned and checked for physical overlap before creating the operation.
Pins protect the opened names, not all writes beneath them. No other writer
may touch the operation tree; we do not hold all vendor files open.

Each operation streams at most the pinned number of files and bytes into a
fresh copy, then streams the actual copy again for verification. The copy
uses one vendor tree's worth of scratch space plus filesystem overhead;
traversal depth and entry inventory are bounded. Nothing is shared or reused.

The digest identifies vendor names and default streams, not OS/loader state
(e.g. SxS, registry, system DLLs, dynamic loads or access to other resources).
The inputs are only usable inside the operation. Cleanup assumes exclusive
scratch and no child process; a future launcher needs its own ownership and
exit guarantees before it can use this layout. No cache or completion use.
"""
import ctypes as C
from ctypes import wintypes as W
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import sysconfig
import tempfile
from typing import NamedTuple

from ..runtime.windows_cleanup import pinned


# From an independent extraction after verifying the official 1.4.4 x64 7z:
# https://inkscape.org/release/inkscape-1.4.4/windows/64-bit/compressed-7z/dl/
# Archive SHA-256: c4dbd64a92628abe7d7316c43f9325396e4d48417866027ee3d993d4f5b54c6a
# Re-pin only by verifying a release artifact and reproducing this tree schema.
_TREE_SHA256 = '35bd6cb39084b9c5c0d9e2dc5ab2d2b880065c58206112c4c6bba768a467a79f'
_ENTRY = 'bin/inkscape.com'  # the archive-pinned PE console image, not the GUI or batch launchers
_MAX_FILES, _MAX_DIRS, _MAX_BYTES = 12152, 802, 660170071
_REPARSE = stat.FILE_ATTRIBUTE_REPARSE_POINT


def _windows_api(name, result, *args):
    fn = getattr(C.WinDLL('kernel32.dll', winmode=0x800, use_last_error=True), name)
    fn.restype, fn.argtypes = result, args
    return fn


def _ordinary(name):
    reserved = getattr(os.path, 'isreserved', lambda value: Path(value).is_reserved())
    return (name and name not in ('.', '..') and not name.endswith((' ', '.'))
            and not reserved(name) and not re.search(r'[<>:"/\\|?*\x00-\x1f\ud800-\udfff]', name))


def _local_path(value):
    path = Path(value)
    if (not path.is_absolute() or not re.fullmatch(r'[A-Za-z]:', path.drive)
            or not all(map(_ordinary, path.parts[1:]))
            or _windows_api('GetDriveTypeW', W.UINT, W.LPCWSTR)(path.anchor) != 3):
        raise ValueError('SVG probe requires ordinary paths on local fixed drives')
    target = C.create_unicode_buffer(1024)
    query = _windows_api('QueryDosDeviceW', W.DWORD, W.LPCWSTR, W.LPWSTR, W.DWORD)
    if not query(path.drive, target, len(target)) or not re.fullmatch(r'\\Device\\HarddiskVolume[0-9]+', target.value, re.I):
        raise ValueError('SVG probe requires a direct local volume mapping')
    return path


class _AttributeTagInfo(C.Structure):
    _fields_ = [('attributes', W.DWORD), ('tag', W.DWORD)]


class _FileIdInfo(C.Structure):
    _fields_ = [('volume', C.c_uint64), ('file_id', C.c_ubyte * 16)]


def _handle_info(handle, kernel, kind, info):
    if not kernel.GetFileInformationByHandleEx(handle, kind, C.byref(info), C.sizeof(info)):
        raise C.WinError(C.get_last_error())
    return info


@contextmanager
def _pin_entry(path):
    with pinned(path) as (handle, info, kernel):
        # lstat can report followed metadata for some Windows tags. Check the
        # actual entry on the held OPEN_REPARSE_POINT handle before traversing it.
        tag = _handle_info(handle, kernel, 9, _AttributeTagInfo())  # FileAttributeTagInfo
        if tag.tag or tag.attributes & _REPARSE:
            raise ValueError('SVG snapshot contains a reparse point')
        yield handle, info, kernel, bool(tag.attributes & stat.FILE_ATTRIBUTE_DIRECTORY)


@contextmanager
def _pin_dir(path):
    with _pin_entry(path) as (handle, _, kernel, directory):
        if not directory:
            raise ValueError('SVG snapshot requires a directory')
        yield handle, kernel


def _pin_dirs(stack, path):
    identities = []
    for directory in (*reversed(path.parents), path):
        handle, kernel = stack.enter_context(_pin_dir(directory))
        info = _handle_info(handle, kernel, 18, _FileIdInfo())  # FileIdInfo
        if not any(info.file_id):
            raise ValueError('SVG snapshot requires a valid directory identity')
        identities.append((info.volume, bytes(info.file_id)))
    return identities


def _tree(root, *, destination=None):
    """Copy one bounded tree, or inventory the actual copy, never both at once."""
    files, directories, seen = {}, [], set()
    file_count = total = 0

    def visit(path, relative, depth):
        nonlocal file_count, total
        if depth > 32:
            raise ValueError('SVG probe vendor tree exceeds its depth bound')
        target = destination / relative if destination is not None else None
        with _pin_entry(path) as (_, info, _, directory):
            if directory:
                if relative:
                    directories.append(relative)
                    if len(directories) > _MAX_DIRS:
                        raise ValueError('SVG probe vendor tree exceeds its directory bound')
                with os.scandir(path) as entries:
                    if target is not None:
                        target.mkdir()
                    for entry in entries:
                        if not _ordinary(entry.name):
                            raise ValueError('SVG probe vendor tree contains an invalid name')
                        name = f'{relative}/{entry.name}' if relative else entry.name
                        folded = name.casefold()
                        if folded in seen:
                            raise ValueError('SVG probe vendor tree contains case-ambiguous names')
                        seen.add(folded)
                        visit(Path(entry.path), name, depth + 1)
                return
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('SVG probe vendor tree contains a non-regular or hardlinked file')
            file_count += 1
            total += info.st_size
            if file_count > _MAX_FILES or total > _MAX_BYTES:
                raise ValueError('SVG probe vendor tree exceeds its file or byte bound')
            with path.open('rb') as src:
                with ExitStack() as stack:
                    # Stream only the default bytes, not source ADS or metadata.
                    dst = stack.enter_context(target.open('xb')) if target is not None else None
                    digest = None if dst is not None else hashlib.sha256()
                    remaining = info.st_size
                    while remaining:
                        block = src.read(min(1024 * 1024, remaining))
                        if not block:
                            raise ValueError('SVG probe vendor file was truncated')
                        remaining -= len(block)
                        if dst is not None:
                            dst.write(block)
                        else:
                            digest.update(block)
                    if src.read(1):
                        raise ValueError('SVG probe vendor file grew during the read')
            if digest is not None:
                files[relative] = [info.st_size, digest.hexdigest()]

    visit(root, '', 0)
    if (file_count, len(directories), total) != (_MAX_FILES, _MAX_DIRS, _MAX_BYTES):
        raise ValueError('SVG probe vendor tree identity mismatch')
    if destination is None:
        manifest = {'schema': 'framivra-audit/inkscape-vendor-tree/1',
                    'directories': sorted(directories), 'files': files}
        data = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
        if hashlib.sha256(data).hexdigest() != _TREE_SHA256:
            raise ValueError('SVG probe vendor tree identity mismatch')
        return root / _ENTRY


class _ShellInputs(NamedTuple):
    vendor: Path
    executable: Path
    cwd: Path


@contextmanager
def _runtime_snapshot(portable_root, *, scratch):
    """Yield only the verified shell input paths, active inside this scope.

    portable_root contains the trusted, stable official inkscape/ extraction.
    scratch must be a caller-owned, exclusive existing ASCII directory disjoint
    from that tree. A filesystem without handle-based directory identities is not
    supported. The caller must not start a process with these inputs; this
    only verifies the copy and selects its fixed entry and empty cwd.
    A future shell launcher must take these paths together, without rediscovery.
    """
    if sys.platform != 'win32' or sysconfig.get_platform() != 'win-amd64':
        raise ValueError('SVG snapshot requires Windows x64 Python')
    source, scratch = _local_path(portable_root) / 'inkscape', _local_path(scratch)
    if not str(scratch).isascii() or ';' in str(scratch):
        raise ValueError('SVG snapshot requires ASCII scratch without semicolons')
    with ExitStack() as outer:
        scratch_ids = _pin_dirs(outer, scratch)
        source_ids = _pin_dirs(outer, source)
        if source_ids[-1] in scratch_ids or scratch_ids[-1] in source_ids:
            raise ValueError('SVG snapshot scratch overlaps the vendor tree')
        operation = Path(tempfile.mkdtemp(prefix='vtx-svg-', dir=scratch))
        try:
            with _pin_dir(operation):
                vendor = operation / 'inkscape'
                _tree(source, destination=vendor)
                executable = _tree(vendor)
                cwd = operation / 'shell'
                cwd.mkdir()
                yield _ShellInputs(vendor, executable, cwd)
        finally:
            shutil.rmtree(operation)
