"""Private Windows SVG shell input selection; this module starts no process.

The caller supplies a trusted, stable vendor extraction and owns an exclusive
scratch parent on a stable local volume mapping. Source and scratch chains
are pinned and checked for physical overlap before creating the operation.
Pins protect the opened names, not all writes beneath them. No other writer
may touch the operation tree. Only the fixed raster can retain its verified
package-bin image files; the pins bind on-disk streams, not mapped bytes.

Each operation streams at most the pinned number of files and bytes into a
fresh copy, then streams the actual copy again for verification. The copy
uses one vendor tree's worth of scratch space plus filesystem overhead;
traversal depth and entry inventory are bounded. Nothing is shared or reused.

The digest identifies vendor names and default streams, not OS/loader state
(e.g. SxS, registry, system DLLs, dynamic loads or access to other resources).
The inputs are only usable inside the operation. The no-launch consumer can
always clean its exclusive scratch; the owned shell must withhold cleanup
until it has settled its writers. No cache or completion use.
"""
import ctypes as C
from ctypes import wintypes as W
from contextlib import ExitStack, contextmanager, nullcontext
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
# The pinned release has 153 eligible leaves, ~282 MiB total, largest ~42 MiB.
_MAX_IMAGES, _MAX_IMAGE_BYTES, _MAX_IMAGES_BYTES = 160, 64 * 1024**2, 300 * 1024**2
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


def _normalized_dos_name(handle):
    query = _windows_api('GetFinalPathNameByHandleW', W.DWORD, W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD)
    buffer = C.create_unicode_buffer(32768)
    size = query(handle, buffer, len(buffer), 0)  # normalized FILE_NAME_NORMALIZED / VOLUME_NAME_DOS
    path = buffer.value
    if not 4 < size < len(buffer) or not path.startswith('\\\\?\\') or not path.isascii() or ';' in path:
        raise ValueError('Windows SVG requires normalized ASCII DOS paths without semicolons')
    return path[4:]


class _StandardInfo(C.Structure):
    _fields_ = [('allocation_size', C.c_int64), ('size', C.c_int64), ('links', W.DWORD),
                ('delete_pending', W.BYTE), ('directory', W.BYTE)]


def _image_size(handle, kernel):
    tag = _handle_info(handle, kernel, 9, _AttributeTagInfo())  # FileAttributeTagInfo
    info = _handle_info(handle, kernel, 1, _StandardInfo())  # FileStandardInfo
    if (tag.tag or tag.attributes & (_REPARSE | stat.FILE_ATTRIBUTE_DEVICE | stat.FILE_ATTRIBUTE_DIRECTORY)
            or info.directory or info.delete_pending or info.links != 1
            or _windows_api('GetFileType', W.DWORD, W.HANDLE)(handle) != 1):  # FILE_TYPE_DISK
        raise ValueError('SVG package image requires a regular single-link disk file')
    return info.size


def _image_digest(handle, kernel, size):
    read = _windows_api('ReadFile', W.BOOL, W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p)
    buffer, count = C.create_string_buffer(min(1024 * 1024, size)), W.DWORD()
    digest, remaining = hashlib.sha256(), size
    while remaining:
        if not read(handle, buffer, min(len(buffer), remaining), C.byref(count), None):
            raise C.WinError(C.get_last_error())
        if not count.value:
            raise ValueError('SVG package image was truncated')
        digest.update(buffer[:count.value])
        remaining -= count.value
    if not read(handle, buffer, 1, C.byref(count), None):
        raise C.WinError(C.get_last_error())
    if count.value or _image_size(handle, kernel) != size:
        raise ValueError('SVG package image changed during authentication')
    return digest.hexdigest()


def _tree(root, *, destination=None, retain=None):
    """Copy one bounded tree, or authenticate it and optionally retain image pins."""
    image_pins, image_bin = retain if retain is not None else (None, None)
    files, directories, seen = {}, [], set()
    images = set()
    file_count = total = image_bytes = 0

    def visit(path, relative, depth):
        nonlocal file_count, total, image_bytes
        if depth > 32:
            raise ValueError('SVG probe vendor tree exceeds its depth bound')
        target = destination / relative if destination is not None else None
        image = image_pins is not None and (relative == _ENTRY or (
            relative.startswith('bin/') and relative.count('/') == 1 and relative.lower().endswith(('.dll', '.exe'))))
        pin = _pin_entry(path)
        if image:
            if len(images) >= _MAX_IMAGES:
                raise ValueError('SVG package images exceed the handle bound')
            pin = nullcontext(image_pins.enter_context(pin))
        with pin as (handle, info, kernel, directory):
            if image:
                size = _image_size(handle, kernel)
                image_bytes += size
                if not 0 < size <= _MAX_IMAGE_BYTES or image_bytes > _MAX_IMAGES_BYTES:
                    raise ValueError('SVG package images exceed the byte bound')
                expected = str(image_bin / relative.split('/')[1])
                if _normalized_dos_name(handle) != expected:
                    raise ValueError('SVG package image name differs from its exact staged path')
            else:
                size = info.st_size
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
            if not image and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise ValueError('SVG probe vendor tree contains a non-regular or hardlinked file')
            file_count += 1
            total += size
            if file_count > _MAX_FILES or total > _MAX_BYTES:
                raise ValueError('SVG probe vendor tree exceeds its file or byte bound')
            if image:
                files[relative] = [size, _image_digest(handle, kernel, size)]
                images.add(expected)
                return
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
        if image_bin is not None:
            executable = image_bin / _ENTRY.split('/')[1]
            if str(executable) not in images:
                raise ValueError('SVG probe requires the exact pinned package entry')
            return executable, frozenset(images)
        return root / _ENTRY


class _ShellInputs(NamedTuple):
    vendor: Path
    executable: Path
    cwd: Path


class _SnapshotOperation:
    def __init__(self, inputs, package_images=None):
        self.inputs = inputs
        self.package_images = package_images  # present only while owned by the fixed raster
        self.keep = False  # the shell owner must set this before it can start a writer


@contextmanager
def _snapshot_operation(portable_root, *, scratch, retain_package_images=False):
    """Own the verified private copy. Close pins, but never delete while keep is set."""
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
        directory = Path(tempfile.mkdtemp(prefix='vtx-svg-', dir=scratch))
        operation = None
        try:
            with _pin_dir(directory) as (directory_handle, _), ExitStack() as images:
                vendor = directory / 'inkscape'
                _tree(source, destination=vendor)
                package_images = None
                if retain_package_images:
                    images.enter_context(_pin_dir(vendor))
                    bin_handle, _ = images.enter_context(_pin_dir(vendor / 'bin'))
                    expected_bin = _local_path(_normalized_dos_name(directory_handle)) / 'inkscape' / 'bin'
                    if _normalized_dos_name(bin_handle) != str(expected_bin):
                        raise ValueError('SVG package bin differs from its exact staged path')
                    executable, package_images = _tree(vendor, retain=(images, expected_bin))
                else:
                    executable = _tree(vendor)
                cwd = directory / 'shell'
                cwd.mkdir()
                operation = _SnapshotOperation(_ShellInputs(vendor, executable, cwd), package_images)
                yield operation
        finally:
            if operation is None or not operation.keep:
                shutil.rmtree(directory)


@contextmanager
def _runtime_snapshot(portable_root, *, scratch):
    """Verify/select inputs only; this consumer must not start a process.

    Use a trusted stable extraction and a caller-owned exclusive existing
    ASCII scratch directory on a local volume, disjoint from the vendor tree.
    """
    with _snapshot_operation(portable_root, scratch=scratch) as operation:
        yield operation.inputs
