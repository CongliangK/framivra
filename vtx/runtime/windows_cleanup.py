"""Windows safe cleanup using non-delete-sharing handles to pin every ancestor.

No path-based deletion fallback: all traversed directories reject reparse
points, their handles stay open, and FileDispositionInfo deletes the pinned
leaf. A concurrent new child makes directory deletion fail harmlessly.
"""
import contextlib
import ctypes
from ctypes import wintypes
from pathlib import Path
import os
import stat


@contextlib.contextmanager
def pinned(path, *, delete=False, directory=False):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    # GENERIC_READ participates in sharing checks (metadata-only access does
    # not prevent rename on Windows). Share READ only: deny both rename and in-place reparse mutation.
    handle = kernel.CreateFileW(str(path), 0x80000000 | (0x10000 if delete else 0), 1, None, 3,
                                0x02000000 | 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = path.lstat()
        if directory and (not stat.S_ISDIR(info.st_mode) or info.st_file_attributes & 0x400):
            raise ValueError('cleanup ancestor is a reparse point or not a directory')
        # OPEN_REPARSE_POINT prevents following the leaf; path.lstat is stable
        # because every ancestor and this directory entry cannot be renamed.
        yield handle, info, kernel
    finally:
        kernel.CloseHandle(handle)


def remove_pinned(path, expected, preserve, retain_if_present=None):
    from .fstxn import entry_identity, _cleanup_error
    path = Path(os.path.abspath(path))
    chain = list(reversed(path.parents))
    try:
        with contextlib.ExitStack() as stack:
            for index, ancestor in enumerate(chain):
                _, info, _ = stack.enter_context(pinned(ancestor, directory=True))
                if entry_identity(info) != expected[index]:
                    raise ValueError('cleanup ancestor identity changed')
            _remove(path, expected[-1], preserve, retain_if_present)
    except FileNotFoundError:
        return
    except (OSError, ValueError) as error:
        raise _cleanup_error(path, f'Windows pinned cleanup refused ({error})') from error


def _remove(path, expected, preserve, retain_if_present=None):
    from .fstxn import entry_identity
    # Deleting a reparse leaf removes the link itself, never its destination.
    with pinned(path, delete=True) as (handle, info, kernel):
        if entry_identity(info) != expected:
            raise ValueError('cleanup entry identity changed')
        # Every ancestor and the selected leaf are pinned before this lookup.
        # Any marker type retains; permission/I/O uncertainty fails closed.
        if retain_if_present:
            directory, marker = retain_if_present
            if directory == path and (not stat.S_ISDIR(info.st_mode) or info.st_file_attributes & 0x400):
                raise ValueError('retention directory is a reparse point or not a directory')
            try:
                (directory / marker).lstat()
            except FileNotFoundError:
                pass
            else:
                return False
        if preserve(path):
            return False
        if stat.S_ISDIR(info.st_mode) and not info.st_file_attributes & 0x400:
            complete = True
            for child in sorted(path.iterdir()):
                try:
                    child_info = child.lstat()
                    complete = _remove(child, entry_identity(child_info), preserve) and complete
                except FileNotFoundError:
                    pass
            if not complete:
                return False
        disposition = wintypes.BOOL(True)
        if not kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            code = ctypes.get_last_error()
            if code == 145:  # ERROR_DIR_NOT_EMPTY: concurrent writer/lock
                return False
            raise ctypes.WinError(code)
        return True
