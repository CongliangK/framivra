"""Handle-relative loose SVG cache and receipt I/O for the Windows gfx lane."""
from contextlib import ExitStack, contextmanager
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import secrets
import time

from . import gfx_svg_win_input as reader
from .gfx_svg_win_snapshot import _local_path, _pin_dirs, _windows_api


class _Rename(C.Structure):
    _fields_ = [('replace', W.BYTE), ('directory', W.HANDLE), ('length', W.DWORD), ('name', W.WCHAR * 1)]


class _Overlapped(C.Structure):
    _fields_ = [('internal', C.c_size_t), ('high', C.c_size_t), ('offset', W.DWORD),
                ('offset_high', W.DWORD), ('event', W.HANDLE)]


class Directory:
    def __init__(self, handle, kernel, nt):
        self.handle, self.kernel, self.nt = handle, kernel, nt
        self.name = reader._final_path(kernel, handle)

    @contextmanager
    def _open(self, name, access, *, disposition=1, share=1, directory=False):
        if not reader._ordinary(name):
            raise ValueError('Windows SVG publication requires an ordinary leaf name')
        create = self.nt.NtCreateFile
        create.restype = W.LONG
        create.argtypes = (C.POINTER(W.HANDLE), W.DWORD, C.POINTER(reader._ObjectAttributes),
                           C.POINTER(reader._IoStatusBlock), C.c_void_p, W.ULONG, W.ULONG,
                           W.ULONG, W.ULONG, C.c_void_p, W.ULONG)
        attrs, buffer = reader._child_attributes(self.handle, name)
        handle = W.HANDLE()
        options = reader._OPEN_REPARSE | 0x20 | (1 if directory else 0x40)
        status = create(C.byref(handle), access | 0x00100000, C.byref(attrs), C.byref(reader._IoStatusBlock()),
                        None, 0, share, disposition, options, None, 0)
        if status:
            raise C.WinError(self.nt.RtlNtStatusToDosError(status))
        try:
            info = reader._info(self.kernel, handle, directory=directory)
            if reader._final_path(self.kernel, handle) != self.name.rstrip('\\') + '\\' + name:
                raise ValueError('Windows SVG publication requires the exact filesystem name')
            yield handle, info
        finally:
            self.kernel.CloseHandle(handle)

    @contextmanager
    def child(self, name, *, create):
        with self._open(name, reader._GENERIC_READ, disposition=3 if create else 1, share=3, directory=True) as (handle, _):
            yield Directory(handle, self.kernel, self.nt)

    def read(self, name, limit):
        with self._open(name, reader._GENERIC_READ) as (handle, info):
            return reader._read(self.kernel, handle, limit, info.size)

    @contextmanager
    def open_read(self, name):
        with self._open(name, reader._GENERIC_READ) as (handle, info):
            yield handle, info.size

    def chunks(self, handle, size, *, limit):
        """Stream a still-held, read-only leaf from the beginning, without reopening it."""
        if not 0 < size <= limit:
            raise ValueError('Windows SVG media exceeds its byte budget or is empty')
        buffer = C.create_string_buffer(min(size, 1024 * 1024))
        count = W.DWORD()
        remaining = size
        while remaining:
            amount = min(remaining, len(buffer))
            if not self.kernel.ReadFile(handle, buffer, amount, C.byref(count), None):
                raise C.WinError(C.get_last_error())
            if not count.value:
                raise ValueError('Windows SVG media ended before its certified size')
            remaining -= count.value
            yield buffer[:count.value]
        if reader._info(self.kernel, handle, directory=False).size != size:
            raise ValueError('Windows SVG media changed during streaming')

    def _check_existing(self, name):
        try:
            with self._open(name, reader._GENERIC_READ):
                pass
        except FileNotFoundError:
            pass

    @contextmanager
    def lock(self, name):
        lock = _windows_api('LockFileEx', W.BOOL, W.HANDLE, W.DWORD, W.DWORD, W.DWORD, W.DWORD,
                            C.POINTER(_Overlapped))
        with self._open(name + '.lock', 0xC0000000, disposition=3, share=3) as (handle, _):
            deadline = time.monotonic() + 120
            offset = _Overlapped()
            while not lock(handle, 3, 0, 1, 0, C.byref(offset)):
                error = C.get_last_error()
                if error != 33:
                    raise C.WinError(error)
                if time.monotonic() >= deadline:
                    raise TimeoutError('Windows SVG publication lock deadline expired')
                time.sleep(.05)
            yield  # Closing the exclusively held file handle releases the range lock.

    def _delete(self, handle):
        set_info = _windows_api('SetFileInformationByHandle', W.BOOL, W.HANDLE, C.c_int, C.c_void_p, W.DWORD)
        remove = W.BOOL(True)
        if not set_info(handle, 4, C.byref(remove), C.sizeof(remove)):
            raise C.WinError(C.get_last_error())

    def remove(self, name):
        try:
            with self._open(name, reader._GENERIC_READ | 0x10000) as (handle, _):
                self._delete(handle)
        except FileNotFoundError:
            pass

    def publish(self, name, payload):
        self.publish_chunks(name, (payload,))

    def publish_chunks(self, name, chunks):
        if not reader._ordinary(name):
            raise ValueError('Windows SVG publication requires an ordinary leaf name')
        self._check_existing(name)
        write = _windows_api('WriteFile', W.BOOL, W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p)
        flush = _windows_api('FlushFileBuffers', W.BOOL, W.HANDLE)
        rename = self.nt.NtSetInformationFile
        rename.restype = W.LONG
        rename.argtypes = (W.HANDLE, C.POINTER(reader._IoStatusBlock), C.c_void_p, W.ULONG, W.ULONG)
        encoded = name.encode('utf-16-le')
        length = _Rename.name.offset + len(encoded)
        data = C.create_string_buffer(max(length, C.sizeof(_Rename)))
        header = _Rename.from_buffer(data)
        header.replace, header.directory, header.length = 1, self.handle, len(encoded)
        C.memmove(C.addressof(data) + _Rename.name.offset, encoded, len(encoded))
        with self._open('.svg-' + secrets.token_hex(16) + '.tmp', 0xC0010000, disposition=2) as (handle, _):
            try:
                count = W.DWORD()
                for payload in chunks:
                    if not write(handle, payload, len(payload), C.byref(count), None) or count.value != len(payload):
                        raise OSError('Windows SVG publication could not write the full file')
                if not flush(handle):
                    raise C.WinError(C.get_last_error())
                # FileRenameInformation acts on the opened source, never on a reopened tmp name.
                status = rename(handle, C.byref(reader._IoStatusBlock()), data, length, 10)
                if status:
                    raise C.WinError(self.nt.RtlNtStatusToDosError(status))
            except BaseException as error:
                try:
                    self._delete(handle)
                except OSError as cleanup:
                    raise error from cleanup
                raise


@contextmanager
def anchored(root):
    kernel, nt = reader._api()
    root = _local_path(root)
    with ExitStack() as pins:
        _pin_dirs(pins, root)
        # Use the already-inspected root through another no-delete-sharing pin.
        from .gfx_svg_win_snapshot import _pin_dir
        handle, _ = pins.enter_context(_pin_dir(root))
        fs = C.create_unicode_buffer(32)
        if not kernel.GetVolumeInformationByHandleW(handle, None, 0, None, None, None, fs, len(fs)) or fs.value != 'NTFS':
            raise ValueError('Windows SVG publication requires local NTFS')
        yield Directory(handle, kernel, nt)
