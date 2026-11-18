"""Private Windows SVG input reader, not a rendering or sandbox capability.

The caller authorizes a stable absolute project root on a stable, direct fixed
NTFS drive mapping. Start at the inspected volume handle; only that first open
uses a pathname. Directory handles stay live through the leaf read. This is not
an immutable content snapshot or protection from privileged/filesystem actors.
"""
import ctypes as C
from contextlib import ExitStack
from ctypes import wintypes as W
from functools import lru_cache
import os
from pathlib import PureWindowsPath
import re
import stat


_DRIVE = re.compile(r'[A-Za-z]:\\')
_DEVICE = re.compile(r'\\Device\\HarddiskVolume[0-9]+', re.I | re.ASCII)
_RESERVED = re.compile(r'CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³]', re.I | re.ASCII)
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\ud800-\udfff]')
_SHARE_READ = 1  # neither write nor delete sharing
_GENERIC_READ = 0x80000000
_INVALID_HANDLE = W.HANDLE(-1).value
_DONT_REPARSE = 0x1000  # OBJECT_ATTRIBUTES: OBJ_DONT_REPARSE
_OPEN_REPARSE = 0x00200000  # FILE_OPEN_REPARSE_POINT / FILE_FLAG_OPEN_REPARSE_POINT


def _ordinary(part):
    return (part and not part.endswith((' ', '.')) and not _FORBIDDEN.search(part)
            and not _RESERVED.fullmatch(part.partition('.')[0].rstrip(' '))
            and len(part.encode('utf-16-le')) <= 510)


def relative_parts(name):
    # Path/resolve would silently discard empty and dot components.
    if isinstance(name, str):
        parts = name.split('/')
        if all(map(_ordinary, parts)):
            return parts
    raise ValueError('SVG inputs require ordinary project-relative Windows paths')


def _root_parts(root):
    root = os.fspath(root)
    if isinstance(root, str) and _DRIVE.match(root):
        parts = root[3:].split('\\') if len(root) > 3 else []
        if all(map(_ordinary, parts)):
            return root[:3], parts
    raise ValueError('SVG input requires an ordinary absolute Windows project root')


class _UnicodeString(C.Structure):
    _fields_ = [('length', W.USHORT), ('maximum_length', W.USHORT), ('buffer', W.LPWSTR)]


class _ObjectAttributes(C.Structure):
    _fields_ = [('length', W.ULONG), ('root_directory', W.HANDLE),
                ('object_name', C.POINTER(_UnicodeString)), ('attributes', W.ULONG),
                ('security_descriptor', C.c_void_p), ('security_qos', C.c_void_p)]


class _IoStatusBlock(C.Structure):
    _fields_ = [('status_or_pointer', C.c_void_p), ('information', C.c_size_t)]


class _AttributeTagInfo(C.Structure):
    _fields_ = [('attributes', W.DWORD), ('tag', W.DWORD)]


class _StandardInfo(C.Structure):
    _fields_ = [('allocation_size', C.c_int64), ('size', C.c_int64), ('links', W.DWORD),
                ('delete_pending', W.BYTE), ('directory', W.BYTE)]


@lru_cache(maxsize=1)
def _api():
    kernel, nt = (C.WinDLL(name, winmode=0x800, use_last_error=True)
                  for name in ('kernel32.dll', 'ntdll.dll'))  # LOAD_LIBRARY_SEARCH_SYSTEM32

    def bind(dll, name, result, *args):
        fn = getattr(dll, name)
        fn.restype, fn.argtypes = result, args

    bind(kernel, 'GetDriveTypeW', W.UINT, W.LPCWSTR)
    bind(kernel, 'QueryDosDeviceW', W.DWORD, W.LPCWSTR, W.LPWSTR, W.DWORD)
    bind(kernel, 'CreateFileW', W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p,
         W.DWORD, W.DWORD, W.HANDLE)
    bind(kernel, 'CloseHandle', W.BOOL, W.HANDLE)
    bind(kernel, 'GetVolumeInformationByHandleW', W.BOOL, W.HANDLE, W.LPWSTR, W.DWORD,
         C.c_void_p, C.c_void_p, C.c_void_p, W.LPWSTR, W.DWORD)
    bind(kernel, 'GetFileType', W.DWORD, W.HANDLE)
    bind(kernel, 'GetFileInformationByHandleEx', W.BOOL, W.HANDLE, C.c_int, C.c_void_p, W.DWORD)
    bind(kernel, 'GetFinalPathNameByHandleW', W.DWORD, W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD)
    bind(kernel, 'ReadFile', W.BOOL, W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p)
    bind(nt, 'NtOpenFile', W.LONG, C.POINTER(W.HANDLE), W.DWORD, C.POINTER(_ObjectAttributes),
         C.POINTER(_IoStatusBlock), W.ULONG, W.ULONG)
    bind(nt, 'RtlNtStatusToDosError', W.ULONG, W.LONG)
    return kernel, nt


def _final_path(kernel, handle):
    path = C.create_unicode_buffer(320)  # conventional path bound below + NT volume prefix
    size = kernel.GetFinalPathNameByHandleW(handle, path, len(path), 2)  # normalized VOLUME_NAME_NT
    if not size:
        raise C.WinError(C.get_last_error())
    if size >= len(path):
        raise ValueError('SVG input has an unsupported final Windows path')
    return path.value


def _info(kernel, handle, *, directory):
    if kernel.GetFileType(handle) != 1:  # FILE_TYPE_DISK
        raise ValueError('SVG input must be on a disk filesystem')
    tag, info = _AttributeTagInfo(), _StandardInfo()
    for kind, value in ((9, tag), (1, info)):  # FileAttributeTagInfo, FileStandardInfo
        if not kernel.GetFileInformationByHandleEx(handle, kind, C.byref(value), C.sizeof(value)):
            raise C.WinError(C.get_last_error())
    if tag.tag or tag.attributes & (stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DEVICE):
        raise ValueError('SVG input refuses reparse points and devices')
    if (info.delete_pending or bool(info.directory) != directory
            or bool(tag.attributes & stat.FILE_ATTRIBUTE_DIRECTORY) != directory):
        raise ValueError('SVG input requires ordinary directories and a regular file')
    if not directory and (info.links != 1 or info.size < 0):
        raise ValueError('SVG input requires a regular file with one hard link and known size')
    return info


def _open_child(stack, kernel, nt, parent, part):
    # CreateFileW cannot take a directory handle. Use the user-mode NT open
    # interface with a single name and a held RootDirectory, never a path lookup.
    size = len(part.encode('utf-16-le'))
    buffer = C.create_unicode_buffer(part, size // 2 + 1)
    name = _UnicodeString(size, size + 2, C.cast(buffer, W.LPWSTR))
    attributes = _ObjectAttributes(C.sizeof(_ObjectAttributes), parent, C.pointer(name),
                                   _DONT_REPARSE, None, None)
    handle = W.HANDLE()
    status = nt.NtOpenFile(C.byref(handle), _GENERIC_READ | 0x00100000,  # SYNCHRONIZE
                           C.byref(attributes), C.byref(_IoStatusBlock()),
                           _SHARE_READ, _OPEN_REPARSE | 0x20)  # FILE_SYNCHRONOUS_IO_NONALERT
    if handle.value not in (None, _INVALID_HANDLE):
        stack.callback(kernel.CloseHandle, handle)
    if status:
        raise C.WinError(nt.RtlNtStatusToDosError(status))
    if handle.value in (None, _INVALID_HANDLE):
        raise ValueError('SVG input open returned no handle')
    return handle


def _read(kernel, handle, limit, size):
    if size > limit:
        raise ValueError('SVG input exceeds byte budget')
    data = bytearray()
    buffer = C.create_string_buffer(min(65536, limit + 1))
    count = W.DWORD()
    while True:
        amount = min(len(buffer), limit - len(data) + 1)
        if not kernel.ReadFile(handle, buffer, amount, C.byref(count), None):
            raise C.WinError(C.get_last_error())
        if not count.value:
            break
        if len(data) + count.value > limit:
            raise ValueError('SVG input exceeds byte budget')
        data.extend(buffer[:count.value])
    if len(data) != size or _info(kernel, handle, directory=False).size != size:
        raise ValueError('SVG input size changed during read')
    return bytes(data)


def read(root, name, *, limit, expected_path):
    parts = relative_parts(name)
    drive, parents = _root_parts(root)
    parts = parents + parts
    path = drive + '\\'.join(parts)
    if len(path.encode('utf-16-le')) >= 520:
        raise ValueError('SVG input requires a conventional-length Windows path')
    if expected_path is not None and PureWindowsPath(path).parts != expected_path.parts:
        raise ValueError('SVG dependency identity changed while resolving its relative name')

    kernel, nt = _api()
    target = C.create_unicode_buffer(1024)
    if (kernel.GetDriveTypeW(drive) != 3  # DRIVE_FIXED
            or not kernel.QueryDosDeviceW(drive[:2], target, len(target))
            or not _DEVICE.fullmatch(target.value)):
        raise ValueError('SVG input requires a direct fixed local Windows volume mapping')
    with ExitStack() as stack:
        handle = kernel.CreateFileW(drive, _GENERIC_READ, _SHARE_READ, None, 3,  # OPEN_EXISTING
                                    0x02000000 | _OPEN_REPARSE, None)  # FILE_FLAG_BACKUP_SEMANTICS
        if handle == _INVALID_HANDLE:
            raise C.WinError(C.get_last_error())
        stack.callback(kernel.CloseHandle, handle)
        _info(kernel, handle, directory=True)
        prefix = _final_path(kernel, handle)
        if prefix.casefold() != (target.value + '\\').casefold():
            raise ValueError('SVG input mapping does not identify the opened volume root')
        fs = C.create_unicode_buffer(32)
        if not kernel.GetVolumeInformationByHandleW(handle, None, 0, None, None, None, fs, len(fs)):
            raise C.WinError(C.get_last_error())
        if fs.value != 'NTFS':
            raise ValueError('SVG input requires local NTFS')
        for i, part in enumerate(parts):
            handle = _open_child(stack, kernel, nt, handle, part)
            info = _info(kernel, handle, directory=i < len(parts) - 1)
            prefix += part
            if _final_path(kernel, handle) != prefix:
                raise ValueError('SVG input must use the exact Windows filesystem name')
            prefix += '\\'
        return _read(kernel, handle, limit, info.size)
