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
    # The classic variant's leading BOOLEAN occupies this same aligned slot.
    _fields_ = [('flags', W.DWORD), ('directory', W.HANDLE), ('length', W.DWORD), ('name', W.WCHAR * 1)]


class _Overlapped(C.Structure):
    _fields_ = [('internal', C.c_size_t), ('high', C.c_size_t), ('offset', W.DWORD),
                ('offset_high', W.DWORD), ('event', W.HANDLE)]


class _DirectoryRecord(C.Structure):
    _fields_ = [('next', W.DWORD), ('index', W.DWORD), ('times_sizes', C.c_int64 * 6),
                ('attrs', W.DWORD), ('name_length', W.DWORD), ('ea', W.DWORD),
                ('short_length', C.c_byte), ('short_name', W.WCHAR * 12),
                ('file_id', C.c_int64), ('name', W.WCHAR * 1)]


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
            if status & 0xffffffff in (0xc000050b, 0xc0000103, 0xc00000ba):
                raise reader.WindowsPathAlias('Windows I/O refuses reparse points and the wrong entry type')
            raise C.WinError(self.nt.RtlNtStatusToDosError(status))
        try:
            info = reader._info(self.kernel, handle, directory=directory)
            if reader._final_path(self.kernel, handle) != self.name.rstrip('\\') + '\\' + name:
                raise reader.WindowsPathAlias('Windows I/O requires the exact filesystem name')
            yield handle, info
        finally:
            self.kernel.CloseHandle(handle)

    @contextmanager
    def child(self, name, *, create):
        with self._open(name, reader._GENERIC_READ, disposition=3 if create else 1, share=3, directory=True) as (handle, _):
            yield Directory(handle, self.kernel, self.nt)

    def entries(self):
        """Enumerate names from this exact directory, without resolving its path again."""
        buffer = C.create_string_buffer(64 * 1024)
        info_class = 11  # FileIdBothDirectoryRestartInfo
        prefix = _DirectoryRecord.name.offset
        while self.kernel.GetFileInformationByHandleEx(self.handle, info_class, buffer, len(buffer)):
            info_class = 10  # FileIdBothDirectoryInfo: continue the same scan
            offset = 0
            while True:
                if offset + C.sizeof(_DirectoryRecord) > len(buffer):
                    raise ValueError('invalid NTFS directory record offset')
                row = _DirectoryRecord.from_buffer(buffer, offset)
                end = offset + prefix + row.name_length
                if (not 0 < row.name_length <= 510 or row.name_length % 2 or end > len(buffer)
                        or row.next and (row.next < prefix + row.name_length or row.next % 8)):
                    raise ValueError('invalid NTFS directory name record')
                name = buffer[offset + prefix:end].decode('utf-16-le')
                if name not in ('.', '..'):
                    yield name
                if not row.next:
                    break
                offset += row.next
        error = C.get_last_error()
        if error != 18:  # ERROR_NO_MORE_FILES
            raise C.WinError(error)

    def read(self, name, limit, *, share=1):
        with self._open(name, reader._GENERIC_READ, share=share) as (handle, info):
            return reader._read(self.kernel, handle, limit, info.size)

    @contextmanager
    def open_read(self, name, *, share=1):
        with self._open(name, reader._GENERIC_READ, share=share) as (handle, info):
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
    def lock(self, name, *, check_cancel=None):
        lock = _windows_api('LockFileEx', W.BOOL, W.HANDLE, W.DWORD, W.DWORD, W.DWORD, W.DWORD,
                            C.POINTER(_Overlapped))
        with self._open(name + '.lock', 0xC0000000, disposition=3, share=3) as (handle, _):
            deadline = time.monotonic() + 120
            offset = _Overlapped()
            while True:
                if check_cancel is not None:
                    check_cancel()
                if lock(handle, 3, 0, 1, 0, C.byref(offset)):
                    break
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

    def _rename(self, handle, name, *, replace, while_readers=False):
        if not reader._ordinary(name):
            raise ValueError('Windows publication requires an ordinary leaf name')
        rename = self.nt.NtSetInformationFile
        rename.restype = W.LONG
        rename.argtypes = (W.HANDLE, C.POINTER(reader._IoStatusBlock), C.c_void_p, W.ULONG, W.ULONG)
        encoded = name.encode('utf-16-le')
        length = _Rename.name.offset + len(encoded)
        data = C.create_string_buffer(max(length, C.sizeof(_Rename)))
        header = _Rename.from_buffer(data)
        flags = int(replace) | (2 if while_readers else 0)
        header.flags, header.directory, header.length = flags, self.handle, len(encoded)
        C.memmove(C.addressof(data) + _Rename.name.offset, encoded, len(encoded))
        status = rename(handle, C.byref(reader._IoStatusBlock()), data, len(data), 65 if while_readers else 10)
        if status:
            raise C.WinError(self.nt.RtlNtStatusToDosError(status))

    @contextmanager
    def new_directory(self, name, *, movable=True):
        # A directory to be published needs DELETE for the handle-relative rename.
        # A pinned scratch directory does not: requesting DELETE prevents a
        # pathname-based writer from renaming children on Windows.
        access = reader._GENERIC_READ | (0x10000 if movable else 0)
        with self._open(name, access, disposition=2, share=3, directory=True) as (handle, _):
            yield Directory(handle, self.kernel, self.nt)

    def publish_directory(self, source, name):
        self._rename(source.handle, name, replace=False)

    def write_new(self, name, payload):
        """Fill one exclusive leaf of an unpublished staging directory."""
        write = _windows_api('WriteFile', W.BOOL, W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p)
        flush = _windows_api('FlushFileBuffers', W.BOOL, W.HANDLE)
        with self._open(name, 0xC0000000, disposition=2) as (handle, _):
            count = W.DWORD()
            if not write(handle, payload, len(payload), C.byref(count), None) or count.value != len(payload):
                raise OSError('Windows publication could not write the full file')
            if not flush(handle):
                raise C.WinError(C.get_last_error())

    def publish(self, name, payload, **options):
        self.publish_chunks(name, (payload,), **options)

    def publish_chunks(self, name, chunks, *, replace=True, before_replace=None, while_readers=False, temp_prefix='.svg-'):
        if not reader._ordinary(name):
            raise ValueError('Windows publication requires an ordinary leaf name')
        self._check_existing(name)
        write = _windows_api('WriteFile', W.BOOL, W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p)
        flush = _windows_api('FlushFileBuffers', W.BOOL, W.HANDLE)
        with self._open(temp_prefix + secrets.token_hex(16) + '.tmp', 0xC0010000, disposition=2) as (handle, _):
            try:
                count = W.DWORD()
                for payload in chunks:
                    if not write(handle, payload, len(payload), C.byref(count), None) or count.value != len(payload):
                        raise OSError('Windows SVG publication could not write the full file')
                if not flush(handle):
                    raise C.WinError(C.get_last_error())
                if before_replace is not None:
                    before_replace()
                self._rename(handle, name, replace=replace, while_readers=while_readers)
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


@contextmanager
def anchored_relative(root):
    """Open the direct NTFS volume, then every directory by exact relative name."""
    drive, parts = reader._root_parts(root)
    with reader._volume_root(drive, share=3) as (kernel, nt, handle, _), ExitStack() as pins:
        directory = Directory(handle, kernel, nt)
        for part in parts:
            directory = pins.enter_context(directory.child(part, create=False))
        yield directory
