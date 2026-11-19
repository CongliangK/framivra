"""Held disk streams for the private Windows SVG 900 audit only.

The trusted internal caller supplies the frozen scope in memory. These checks
neither establish current catalog trust nor identify objects or bytes mapped by
Windows. No sampled process path is an input to this module.
"""
import ctypes as C
from ctypes import wintypes as W
import hashlib
from pathlib import Path
import re
import stat
import time

from ..runtime.windows_browser import _check
from .gfx_svg_win_input import _api
from .gfx_svg_win_snapshot import (_AttributeTagInfo, _FileIdInfo, _StandardInfo, _handle_info,
                                    _local_path, _normalized_dos_name, _ordinary, _windows_api)


_MAX_LEAVES, _MAX_ASSEMBLIES = 96, 16
_MAX_LEAF_BYTES, _MAX_SCOPE_BYTES = 32 * 1024**2, 256 * 1024**2
_PART = re.compile(r'[a-z0-9][a-z0-9_.-]{0,254}')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_UNWRITTEN = '\uffff' * 1024


def _pathname_set_sha256(records):
    digest = hashlib.sha256(b'framivra:windows-svg:system-pathname-set:v1\n')
    for record in sorted(set(records)):
        digest.update(record + b'\n')
    return digest.hexdigest()


def _validate_scope(scope):
    if (type(scope) is not tuple or len(scope) != 2 or type(scope[0]) is not int or type(scope[1]) is not tuple
            or not 0 < scope[0] == len(scope[1]) <= _MAX_LEAVES):
        raise ValueError('Windows SVG expected a counted tuple of system scope rows')
    seen, assemblies, total = set(), set(), 0
    for row in scope[1]:
        if (type(row) is not tuple or len(row) != 4 or type(row[0]) is not str or len(row[0]) > 518
                or type(row[1]) is not int or not 0 < row[1] <= _MAX_LEAF_BYTES
                or type(row[2]) is not str or not _SHA256.fullmatch(row[2])
                or type(row[3]) is not int or row[3] not in (1, 2)):
            raise ValueError('Windows SVG invalid system scope row')
        name, size, _, _ = row
        parts = name.split('/')
        system32 = len(parts) == 2 and parts[0] == 'system32'
        winsxs = len(parts) == 3 and parts[0] == 'winsxs'
        if (not (system32 or winsxs) or not all(_PART.fullmatch(part) and _ordinary(part) for part in parts[1:])
                or name in seen):
            raise ValueError('Windows SVG invalid or duplicate system scope name')
        seen.add(name)
        if winsxs:
            assemblies.add(parts[1])
        total += size
        if len(assemblies) > _MAX_ASSEMBLIES or total > _MAX_SCOPE_BYTES:
            raise ValueError('Windows SVG system scope exceeds its bounds')
    return _pathname_set_sha256(name.encode('ascii') for name in seen)


class _BasicInfo(C.Structure):
    # The last access time is not stable under reads and is not a predicate.
    _fields_ = [('creation', C.c_int64), ('access', C.c_int64), ('write', C.c_int64),
                ('change', C.c_int64), ('attributes', W.DWORD)]


def _open_pin(pins, kernel, path, *, directory):
    flags = 0x00200000 | (0x02000000 if directory else 0)  # OPEN_REPARSE_POINT; BACKUP_SEMANTICS
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, flags, None)  # READ, SHARE_READ, OPEN_EXISTING
    _check(handle and handle != W.HANDLE(-1).value, 'CreateFileW (system scope)')
    pins.callback(kernel.CloseHandle, handle)
    return handle


def _facts(kernel, handle, path, device, *, directory):
    if kernel.GetFileType(handle) != 1:  # FILE_TYPE_DISK
        raise ValueError('Windows SVG system scope requires a disk handle')
    tag = _handle_info(handle, kernel, 9, _AttributeTagInfo())     # FileAttributeTagInfo
    info = _handle_info(handle, kernel, 1, _StandardInfo())         # FileStandardInfo
    identity = _handle_info(handle, kernel, 18, _FileIdInfo())      # FileIdInfo
    if (tag.tag or tag.attributes & (stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DEVICE)
            or bool(tag.attributes & stat.FILE_ATTRIBUTE_DIRECTORY) != directory
            or bool(info.directory) != directory or info.delete_pending or not any(identity.file_id)):
        raise ValueError('Windows SVG system scope requires a non-reparse ordinary entry')
    nt = C.create_unicode_buffer(1024)
    n = kernel.GetFinalPathNameByHandleW(handle, nt, len(nt), 2)  # normalized NT name
    dos = _normalized_dos_name(handle)
    text = str(path)
    if (not 0 < n == len(nt.value) < len(nt) or not nt.value.isascii()
            or nt.value.lower() != (device + text[2:]).lower() or dos.lower() != text.lower()):
        raise ValueError('Windows SVG system scope handle has a different name')
    key = identity.volume, bytes(identity.file_id)
    if directory:
        return key
    basic = _handle_info(handle, kernel, 0, _BasicInfo())  # FileBasicInfo
    if basic.attributes != tag.attributes or info.allocation_size < 0:
        raise ValueError('Windows SVG system scope file metadata is inconsistent')
    return key, dos, nt.value, info.size, info.links, info.allocation_size, basic.creation, basic.write, basic.change, tag.attributes


def _scope_time(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError('Windows SVG system scope deadline expired')


class _HeldSystemScope:
    def __init__(self, kernel, root, image_device):
        self.kernel, self.root, self.image_device = kernel, root, image_device
        self.device = image_device(root.drive, _UNWRITTEN)
        self.parents, self.leaves = {}, []
        self.volume = None

    def _parent(self, pins, path):
        if path not in self.parents:
            handle = _open_pin(pins, self.kernel, path, directory=True)
            identity = _facts(self.kernel, handle, path, self.device, directory=True)
            if self.volume is None:
                self.volume = identity[0]
                fs = C.create_unicode_buffer(32)
                _check(self.kernel.GetVolumeInformationByHandleW(handle, None, 0, None, None, None, fs, len(fs)),
                       'GetVolumeInformationByHandleW (system scope)')
                if fs.value != 'NTFS':
                    raise ValueError('Windows SVG system scope requires local NTFS')
            if identity[0] != self.volume:
                raise ValueError('Windows SVG system scope parent crossed volumes')
            self.parents[path] = handle, identity

    def _check_parents(self, deadline):
        _scope_time(deadline)
        if (self.kernel.GetDriveTypeW(self.root.anchor) != 3
                or self.image_device(self.root.drive, _UNWRITTEN) != self.device):
            raise ValueError('Windows SVG system scope drive mapping changed')
        for path, (handle, identity) in self.parents.items():
            _scope_time(deadline)
            if _facts(self.kernel, handle, path, self.device, directory=True) != identity:
                raise ValueError('Windows SVG system scope parent identity changed')

    def _digest(self, path, handle, facts, deadline):
        _scope_time(deadline)
        if _facts(self.kernel, handle, path, self.device, directory=False) != facts:
            raise ValueError('Windows SVG system scope file metadata changed')
        rewind = _windows_api('SetFilePointerEx', W.BOOL, W.HANDLE, C.c_longlong, C.c_void_p, W.DWORD)
        _check(rewind(handle, 0, None, 0), 'SetFilePointerEx (system scope)')
        buffer, count = C.create_string_buffer(65536), W.DWORD()
        remaining, digest = facts[3], hashlib.sha256()
        while remaining:
            _scope_time(deadline)
            amount = min(remaining, len(buffer))
            _check(self.kernel.ReadFile(handle, buffer, amount, C.byref(count), None), 'ReadFile (system scope)')
            if not 0 < count.value <= amount:
                raise ValueError('Windows SVG system scope file was short-read')
            digest.update(buffer[:count.value])
            remaining -= count.value
        _check(self.kernel.ReadFile(handle, buffer, 1, C.byref(count), None), 'ReadFile (system scope EOF)')
        if count.value or _facts(self.kernel, handle, path, self.device, directory=False) != facts:
            raise ValueError('Windows SVG system scope file identity mismatch')
        return digest.hexdigest()

    def pin_catalogued_image(self, pins, event_key, verify_signature, deadline, *, console_host=False):
        """Retain an installed OS image only if the loader object and trust agree."""
        _scope_time(deadline)
        name, volume, file_id = event_key
        path = Path(name)
        try:
            parts = path.relative_to(self.root).parts
        except ValueError:
            parts = ()
        if (not (len(parts) == 2 and parts[0].lower() == 'system32'
                 or len(parts) == 3 and parts[0].lower() == 'winsxs' and parts[1].lower().startswith('amd64_'))
                or (tuple(part.lower() for part in parts) != ('system32', 'conhost.exe') if console_host
                    else path.suffix.lower() not in ('.dll', '.drv'))
                or volume != self.volume
                or not all(_PART.fullmatch(part.lower()) and _ordinary(part) for part in parts)
                or len(self.leaves) >= _MAX_LEAVES):
            raise ValueError('Windows SVG loader selected an unsupported system image')
        self._parent(pins, path.parent)
        self._check_parents(deadline)
        handle = _open_pin(pins, self.kernel, path, directory=False)
        facts = _facts(self.kernel, handle, path, self.device, directory=False)
        if (facts[0] != (volume, file_id) or facts[1] != name or facts[4] not in (1, 2)
                or not 0 < facts[3] <= _MAX_LEAF_BYTES
                or facts[3] + sum(row[2][3] for row in self.leaves) > _MAX_SCOPE_BYTES):
            raise ValueError('Windows SVG catalogued image differs from the loader file')
        digest = self._digest(path, handle, facts, deadline)
        signer = verify_signature(path, handle, deadline)
        _scope_time(deadline)
        if _facts(self.kernel, handle, path, self.device, directory=False) != facts:
            raise ValueError('Windows SVG catalogued image changed during verification')
        self.leaves.append((path, handle, facts, digest))
        return '/'.join(parts).lower(), digest, signer

    def verify(self, deadline):
        self._check_parents(deadline)
        for path, handle, facts, expected in self.leaves:
            if self._digest(path, handle, facts, deadline) != expected:
                raise ValueError('Windows SVG system scope file identity mismatch')
        self._check_parents(deadline)
        _scope_time(deadline)


def _hold_scope(pins, windows, rows, deadline, image_device):
    # windows is from GetSystemWindowsDirectoryW, never from the scope/caller.
    # Independently require the OS System32 result to match that direct child.
    _scope_time(deadline)
    system32, winsxs = windows / 'System32', windows / 'WinSxS'
    query = _windows_api('GetSystemDirectoryW', W.UINT, W.LPWSTR, W.UINT)
    buffer = C.create_unicode_buffer(512)
    if (not 0 < query(buffer, len(buffer)) < len(buffer) or not buffer.value.isascii()
            or buffer.value.lower() != str(system32).lower()):
        raise ValueError('Windows SVG system directory mismatch')
    return _hold_under_roots(pins, windows, system32, winsxs, rows, deadline, image_device), system32, winsxs


def _hold_under_roots(pins, windows, system32, winsxs, rows, deadline, image_device):
    # Internal filesystem seam: production's sole caller supplies the OS roots;
    # inert native tests can exercise the same pinning against private fixtures.
    windows = _local_path(windows)
    if not str(windows).isascii() or len(windows.parents) > 12:
        raise ValueError('Windows SVG system scope root exceeds its bounds')
    kernel, _ = _api()
    scope = _HeldSystemScope(kernel, windows, image_device)
    for path in (*reversed(windows.parents), windows, system32, winsxs):
        _scope_time(deadline)
        scope._parent(pins, path)
    seen = set()
    for name, size, digest, links in rows:
        _scope_time(deadline)
        group, *parts = name.split('/')  # already validated before any open
        parent = system32 if group == 'system32' else winsxs / parts[0]
        scope._parent(pins, parent)
        path = parent / parts[-1]
        handle = _open_pin(pins, kernel, path, directory=False)
        facts = _facts(kernel, handle, path, scope.device, directory=False)
        if facts[0][0] != scope.volume or facts[0] in seen or facts[3:5] != (size, links):
            raise ValueError('Windows SVG system scope file metadata mismatch')
        seen.add(facts[0])
        scope.leaves.append((path, handle, facts, digest))
    scope.verify(deadline)
    return scope
