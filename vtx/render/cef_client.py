"""vtx_cef_worker 帧服务 Python 消费端（issue #110 CEF-02a）。

生产 CEF Alpha 与 experiments 自检共用的唯一协议客户端。

协议权威：experiments/gfx_cef_worker/IPC_PROTOCOL.md（偏移表与
src/ipc_protocol.hpp 静态断言、src/shm_ring.hpp 控制块布局逐字段一致；
tests/gfx/test_cef_worker_shm.py 以 shm_ring_selftest --dump-ring 跨语言锁定）。

职责：
  - 连接 worker serve 模式 Unix Socket，收 Hello + SCM_RIGHTS(SHM fd)；
  - 模式 1（request_png）：发 64B FrameRequest，收 128B FrameSlotHeader
    计时回执；
  - 模式 2（request_slot）：发请求收 32B 槽通知，经同一 mmap 句柄零拷贝
    读帧，SlotFrame.release() 写槽门 EMPTY 归还（消费端自主节流）；
  - 任何 worker 侧死亡（abort/kill）都以 socket EOF 显形为本模块的
    CefWorkerError —— 消费端绝不死等。

仅标准库（socket/struct/mmap/os）。
"""
from __future__ import annotations

import math
import mmap
import os
import socket
import struct
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- 协议常量（镜像 ipc_protocol.hpp）

SLOT_MAGIC = 0x56545846        # "VTXF"
RING_MAGIC = 0x56545852        # "VTXR"
REQUEST_MAGIC = 0x56545851     # "VTXQ"
NOTICE_MAGIC = 0x5654584E      # "VTXN"
HELLO_MAGIC = 0x56545848       # "VTXH"
PROTOCOL_VERSION = 1
HELLO_FLAG_PAIRED_EXACT = 1 << 1  # active paired exact path, not generic protocol-v1 support

STATUS_OK = 0
STATUS_TIMEOUT = 1
STATUS_SIZE_MISMATCH = 2
STATUS_JS_ERR = 3
STATUS_DUPLICATE = 4  # #114：request_id 重放（状态机重复请求守卫）

FLAG_WANT_SLOT = 1 << 0
FLAG_DUMP_RAW = 1 << 1

RING_SLOTS = 4
MAX_VIEWPORT_SIZE = 16384  # ShmRingWriter::Create native capacity guard
MAX_OUT_PNG_PATH = 39  # 不含 NUL

# 定长消息（小端 packed；与 C++ 原生偏移一致）
_REQUEST_FMT = "<IHHIiii40s"      # 64B
_HELLO_FMT = "<IHHQiIIII7I"       # 64B
_NOTICE_FMT = "<IHHIiB3sIQ"       # 32B
_HEADER_FMT = "<IHHQIIiiIIIBBHQQQQQ"     # 88B（槽头 128B 区域的字段面）
assert struct.calcsize(_REQUEST_FMT) == 64
assert struct.calcsize(_HELLO_FMT) == 64
assert struct.calcsize(_NOTICE_FMT) == 32
assert struct.calcsize(_HEADER_FMT) == 88

# RingControl 偏移（镜像 shm_ring.hpp RingControl 静态断言）
_CTRL_LAYOUT_READY = 0    # u32
_CTRL_MAGIC = 4           # u32
_CTRL_VERSION = 8         # u16
_CTRL_SLOTS = 10          # u16
_CTRL_MAX_W = 12          # u32
_CTRL_MAX_H = 16
_CTRL_FRAME_BYTES = 20
_CTRL_SLOT_STRIDE = 24
_CTRL_RUN_ID = 32         # u64
_CTRL_OWNER_PID = 40      # i32
_CTRL_PUBLISHED = 48      # u64
_CTRL_RELEASED = 56       # u64
_CTRL_BACKPRESSURE = 64   # u64
_CTRL_GATES = 128         # SlotGate[4]：state u32 / seq u32，各专享缓存行
_CTRL_SIZE = 384

_GATE_EMPTY = 0
_GATE_READY = 2

_HEADER_FIELDS = (
    "magic", "protocol_version", "status_code", "run_id", "document_gen",
    "request_id", "target_frame_index", "canary_frame_index", "width",
    "height", "stride", "pixel_format", "color_space", "rejected_paints",
    "t_ready_wait_us", "t_capture_raw_us", "t_alpha_norm_us",
    "t_png_encode_us", "t_disk_commit_us",
)


class CefWorkerError(RuntimeError):
    """worker/协议错误（含回执状态码非 OK 与连接 EOF——消费端不死等）。"""

    def __init__(self, message, status_code=None, detail=None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail or {}


def _align64(v: int) -> int:
    return (v + 63) & ~63


@dataclass(frozen=True)
class _PendingPng:
    frame_index: int
    request_id: int
    deadline: float | None
    output_fd: int | None


@dataclass(frozen=True)
class FrameReceipt:
    """模式 1 计时回执（FrameSlotHeader 解析面）。"""
    header: dict

    @property
    def status_code(self) -> int:
        return self.header["status_code"]


class SlotFrame:
    """模式 2 槽帧：槽头 + 数据快照；release() 归还槽（写门 EMPTY）。"""

    def __init__(self, client: "CefWorkerClient", slot_index: int, seq: int,
                 header: dict, data: bytes, backpressure_wait_us: int):
        self._client = client
        self.slot_index = slot_index
        self.seq = seq
        self.header = header
        self.data = data
        self.backpressure_wait_us = backpressure_wait_us
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._client._release_slot(self.slot_index, self.seq)
        self._released = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        try:
            self.release()
        except BaseException:
            if exc_type is None:
                raise


class CefWorkerClient:
    """Single-owner (not thread-safe) frame client; see IPC_PROTOCOL.md."""

    def __init__(self, sock: socket.socket, shm_fd: int, hello: dict):
        self._sock = sock
        self._shm_fd = shm_fd
        self.hello = hello
        size = os.fstat(shm_fd).st_size
        self._mm = mmap.mmap(shm_fd, size)  # MAP_SHARED 读写（门释放需要）
        with ExitStack() as cleanup:
            cleanup.callback(self._mm.close)
            self._validate_ring(size)
            self._closed = False
            self._pending_png = None
            cleanup.pop_all()

    # ------------------------------------------------------------ 连接

    @classmethod
    def connect(cls, socket_path, connect_timeout=30.0, ready_timeout=30.0):
        """连 serve worker；socket_path 为其 --ipc-socket 路径。"""
        path, fd = str(socket_path), None
        if sys.platform == 'darwin' and len(path.encode()) >= 104:
            raise CefWorkerError("Darwin socket path too long; use an inherited socketpair")
        if len(path.encode()) >= 108:
            parent, name = os.path.split(os.path.abspath(path))
            fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            path = f"/proc/{os.getpid()}/fd/{fd}/{name}"
        try:
            return cls._connect(path, connect_timeout, ready_timeout)
        finally:
            if fd is not None:
                os.close(fd)

    @classmethod
    def _connect(cls, path, connect_timeout, ready_timeout):
        if not math.isfinite(connect_timeout) or connect_timeout <= 0:
            raise ValueError("worker connect timeout must be finite and positive")
        deadline = time.monotonic() + connect_timeout
        last_err = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CefWorkerError(
                    f"连接 worker 超时: {path} ({last_err})") from last_err
            # Own every attempt, including denied/failed connect and interruption.
            # A full listen backlog must not block beyond the startup deadline.
            with ExitStack() as cleanup:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                cleanup.callback(sock.close)
                sock.settimeout(remaining)
                try:
                    sock.connect(path)
                except (FileNotFoundError, ConnectionRefusedError) as exc:
                    last_err = exc
                except socket.timeout as exc:
                    raise CefWorkerError(f"连接 worker 超时: {path}") from exc
                else:
                    cleanup.pop_all()  # from_socket owns it, including Hello failure
                    return cls.from_socket(sock, ready_timeout)
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    @classmethod
    def from_socket(cls, sock, ready_timeout=30.0):
        """Take socket ownership; accept one complete Hello and exactly one SHM fd."""
        with ExitStack() as cleanup:
            cleanup.callback(sock.close)
            sock.settimeout(ready_timeout)
            deadline = None if ready_timeout is None else time.monotonic() + ready_timeout
            fds = []

            def own_fd(fd):
                cleanup.callback(os.close, fd)
                fds.append(fd)

            data = cls._recv_exact(sock, 64, "Hello", receive_fd=own_fd, deadline=deadline)
            if len(fds) != 1:
                raise CefWorkerError(f"Hello 必须附带一个 SHM fd（实际 {len(fds)}）")
            values = struct.unpack(_HELLO_FMT, data)
            hello = {
                "magic": values[0], "protocol_version": values[1],
                "flags": values[2], "run_id": values[3], "pid": values[4],
                "ring_slots": values[5], "max_width": values[6],
                "max_height": values[7], "frame_bytes": values[8],
            }
            if hello["magic"] != HELLO_MAGIC:
                raise CefWorkerError(f"Hello magic 不符: {hello['magic']:#x}")
            if hello["protocol_version"] != PROTOCOL_VERSION:
                raise CefWorkerError(
                    f"协议版本不符: {hello['protocol_version']}")
            sock.settimeout(ready_timeout)  # restore before the next request sends
            client = cls(sock, fds[0], hello)
            cleanup.pop_all()  # only a fully validated client owns socket + fd
            return client

    def _validate_ring(self, size: int) -> None:
        mm = self._mm
        deadline = time.monotonic() + 5.0
        while struct.unpack_from("<I", mm, _CTRL_LAYOUT_READY)[0] != RING_MAGIC:
            if time.monotonic() >= deadline:
                raise CefWorkerError("环布局就绪超时（layout_ready）")
            time.sleep(0.0002)
        (magic,) = struct.unpack_from("<I", mm, _CTRL_MAGIC)
        (version,) = struct.unpack_from("<H", mm, _CTRL_VERSION)
        (slots,) = struct.unpack_from("<H", mm, _CTRL_SLOTS)
        (frame_bytes,) = struct.unpack_from("<I", mm, _CTRL_FRAME_BYTES)
        (run_id,) = struct.unpack_from("<Q", mm, _CTRL_RUN_ID)
        if magic != RING_MAGIC or version != PROTOCOL_VERSION:
            raise CefWorkerError(f"环 magic/版本不符: {magic:#x}/{version}")
        if slots != RING_SLOTS:
            raise CefWorkerError(f"环槽数不符: {slots}")
        if run_id != self.hello["run_id"]:
            raise CefWorkerError("环 run_id 与 Hello 不一致（跨 run 污染）")
        width, height = struct.unpack_from("<II", mm, _CTRL_MAX_W)
        (slot_bytes,) = struct.unpack_from("<I", mm, _CTRL_SLOT_STRIDE)
        if (width <= 0 or height <= 0 or frame_bytes != width * height * 4
                or (width, height, frame_bytes, slots) !=
                   (self.hello["max_width"], self.hello["max_height"],
                    self.hello["frame_bytes"], self.hello["ring_slots"])):
            raise CefWorkerError("环几何/容量与 Hello 不符")
        self.frame_bytes = frame_bytes
        self.slot_stride = _align64(128 + frame_bytes)
        if slot_bytes != self.slot_stride:
            raise CefWorkerError("环槽步长与容量不符")
        expected = _CTRL_SIZE + self.slot_stride * RING_SLOTS
        if sys.platform == 'darwin':
            page = mmap.PAGESIZE
            expected = ((expected + page - 1) // page) * page
        if expected != size:
            raise CefWorkerError(f"环尺寸不符: 期望 {expected} 实际 {size}")

    # ------------------------------------------------------------ 内部

    def _slot_offset(self, slot: int) -> int:
        return _CTRL_SIZE + self.slot_stride * slot

    def _gate(self, slot: int):
        return struct.unpack_from("<II", self._mm, _CTRL_GATES + slot * 64)

    def _release_slot(self, slot: int, seq: int) -> None:
        """Release only this published generation; closed sessions own no live slots."""
        if self._closed:
            return
        if self._gate(slot) != (_GATE_READY, seq):
            try:
                self.close()
            finally:
                raise CefWorkerError("槽世代已变，拒绝释放另一次发布")
        struct.pack_into("<I", self._mm, _CTRL_GATES + slot * 64, _GATE_EMPTY)
        (released,) = struct.unpack_from("<Q", self._mm, _CTRL_RELEASED)
        struct.pack_into("<Q", self._mm, _CTRL_RELEASED, released + 1)

    @staticmethod
    def _recv_exact(sock, n: int, what: str, *, receive_fd=None, deadline=None) -> bytes:
        buf = b""
        while len(buf) < n:
            try:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise socket.timeout()
                    sock.settimeout(remaining)
                if receive_fd is None:
                    chunk = sock.recv(n - len(buf))
                else:
                    fd_size = struct.calcsize("i")
                    chunk, ancillary, flags, _ = sock.recvmsg(
                        n - len(buf), socket.CMSG_SPACE(fd_size))
                    malformed = False
                    for level, kind, data in ancillary:
                        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                            count = len(data) // fd_size
                            for fd in struct.unpack(f"{count}i", data[:count * fd_size]):
                                receive_fd(fd)  # own every delivered fd before any rejection
                            malformed |= bool(len(data) % fd_size)
                    if malformed or flags & socket.MSG_CTRUNC:
                        raise CefWorkerError(f"{what} SHM fd 控制消息损坏/截断")
            except socket.timeout as exc:
                raise CefWorkerError(f"等待 {what} 超时") from exc
            except (ConnectionResetError, BrokenPipeError) as exc:
                # worker 暴毙（abort/kill）且残留未读数据时内核发 RST 而非
                # FIN —— 与 EOF 同源：明确错误，绝不死等。
                raise CefWorkerError(
                    f"worker 连接中断（EOF/RST={exc.errno}，等待 {what}）"
                    "——worker 已死亡或中止") from exc
            if not chunk:
                raise CefWorkerError(
                    f"worker 连接中断（EOF，等待 {what}）——worker 已死或中止",
                    status_code=None)
            buf += chunk
        return buf

    @staticmethod
    def _request_packet(frame_index: int, out_path: str, flags: int,
                        request_id: int, expected_wh) -> bytes:
        if len(out_path.encode("utf-8")) > MAX_OUT_PNG_PATH or "\0" in out_path:
            raise CefWorkerError(
                f"out_png_path 超长（{len(out_path.encode('utf-8'))}>{MAX_OUT_PNG_PATH}，"
                "64B 定长请求面约束；请用短路径）")
        exp_w, exp_h = expected_wh
        return struct.pack(
            _REQUEST_FMT, REQUEST_MAGIC, PROTOCOL_VERSION, flags, request_id,
            frame_index, exp_w, exp_h, out_path.encode("utf-8"))

    def _send_request(self, frame_index: int, out_path: str, flags: int,
                      request_id: int, expected_wh) -> None:
        self._sock.sendall(self._request_packet(
            frame_index, out_path, flags, request_id, expected_wh))

    def _parse_header_at(self, offset: int) -> dict:
        values = struct.unpack_from(_HEADER_FMT, self._mm, offset)
        return dict(zip(_HEADER_FIELDS, values))

    def _validate_slot_header(self, header: dict, request_id: int, frame_index: int) -> None:
        if header["magic"] != SLOT_MAGIC:
            raise CefWorkerError(f"槽头 magic 不符: {header['magic']:#x}")
        if header["protocol_version"] != PROTOCOL_VERSION:
            raise CefWorkerError(f"槽头版本不符: {header['protocol_version']}")
        if header["run_id"] != self.hello["run_id"]:
            raise CefWorkerError("槽头 run_id 陈旧（跨 run 残帧拒收）")
        if header["request_id"] != request_id or header["target_frame_index"] != frame_index:
            raise CefWorkerError("receipt request/frame identity mismatch")

    # ------------------------------------------------------------ 公共 API

    def submit_png(self, frame_index: int, out_png_path,
                   request_id: int = 0, expected_wh=(0, 0),
                   timeout: float = 90.0, *, worker_relative=False) -> None:
        """Send one Mode 1 request; receive_png() must consume it before another.

        Splitting send/receive overlaps host validation/commit of the preceding
        file with native work. It does NOT imply a parallel native worker: only
        one request is ever in flight, including on protocol-v1 workers without
        a capability handshake. The output directory fd and absolute deadline
        belong to this request until its receipt is consumed or the client closes.
        """
        if self._closed:
            raise CefWorkerError("PNG client is closed")
        if self._pending_png is not None:
            raise CefWorkerError("PNG request already pending; receive it before submitting another")
        if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("PNG request timeout must be finite and positive, or None")
        path, fd = str(out_png_path), None
        if worker_relative:
            if not path or path in (".", "..") or Path(path).name != path:
                raise CefWorkerError("worker-relative output must be a plain basename")
        else:
            path = os.path.abspath(path)
            if len(path.encode("utf-8")) > MAX_OUT_PNG_PATH:
                if sys.platform == 'darwin':
                    raise CefWorkerError("Darwin PNG output requires worker-relative basename")
                parent, name = os.path.split(path)
                fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                path = f"/proc/{os.getpid()}/fd/{fd}/{name}"
        try:
            # Local argument failures occur before any bytes are sent. They may
            # be corrected without poisoning the otherwise untouched session.
            packet = self._request_packet(frame_index, path, 0, request_id, expected_wh)
        except BaseException:
            if fd is not None:
                os.close(fd)
            raise
        deadline = None if timeout is None else time.monotonic() + timeout
        self._pending_png = _PendingPng(frame_index, request_id, deadline, fd)
        try:
            self._sock.settimeout(timeout)
            self._sock.sendall(packet)
        except BaseException as error:
            self._png_transport_error(error)

    def _png_transport_error(self, error):
        # A partially sent request or unread/corrupt receipt cannot be retried
        # on this stream: doing so might consume another frame's completion.
        try:
            self.close()
        finally:
            if isinstance(error, (OSError, ValueError, struct.error)):
                raise CefWorkerError(f"PNG transport/layout failed: {error}") from error
            raise error

    def receive_png(self) -> FrameReceipt:
        """Consume the single pending PNG receipt with one end-to-end deadline."""
        if self._closed:
            raise CefWorkerError("PNG client is closed")
        pending = self._pending_png
        if pending is None:
            raise CefWorkerError("no PNG request pending")
        try:
            raw = self._recv_exact(self._sock, 128, "模式 1 回执", deadline=pending.deadline)
            values = struct.unpack_from(_HEADER_FMT, raw, 0)
            header = dict(zip(_HEADER_FIELDS, values))
            self._validate_slot_header(header, pending.request_id, pending.frame_index)
            status = header["status_code"]
            if status not in (STATUS_OK, STATUS_TIMEOUT, STATUS_SIZE_MISMATCH,
                              STATUS_JS_ERR, STATUS_DUPLICATE):
                raise CefWorkerError(f"unknown PNG receipt status={status}")
            # Only a fully received, identity-bound response clears admission.
            self._pending_png = None
            if pending.output_fd is not None:
                os.close(pending.output_fd)
            if status != STATUS_OK:
                raise CefWorkerError(
                    f"帧请求失败 status={status}（1=TIMEOUT 2=SIZE_MISMATCH "
                    f"3=JS_ERR 4=DUPLICATE）：w={header['width']} h={header['height']} "
                    f"rejected={header['rejected_paints']}",
                    status_code=status, detail=header)
            return FrameReceipt(header=header)
        except BaseException as error:
            if isinstance(error, CefWorkerError) and error.status_code is not None:
                raise  # a complete bound worker error leaves no ambiguous bytes
            self._png_transport_error(error)

    def request_png(self, frame_index: int, out_png_path,
                    request_id: int = 0, expected_wh=(0, 0),
                    timeout: float = 90.0, *, worker_relative=False) -> FrameReceipt:
        """Synchronous compatibility API, using the same bounded request state."""
        self.submit_png(frame_index, out_png_path, request_id, expected_wh,
                        timeout, worker_relative=worker_relative)
        return self.receive_png()

    def request_slot(self, frame_index: int, request_id: int = 0,
                     dump_raw_path=None, timeout: float = 90.0) -> SlotFrame:
        path, fd = dump_raw_path, None
        if path is not None and len(os.path.abspath(str(path)).encode()) > MAX_OUT_PNG_PATH:
            if sys.platform == 'darwin':
                raise CefWorkerError("Darwin raw dump path exceeds protocol limit")
            parent, name = os.path.split(os.path.abspath(str(path)))
            fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            path = f"/proc/{os.getpid()}/fd/{fd}/{name}"
        try:
            return self._request_slot(frame_index, request_id, path, timeout)
        finally:
            if fd is not None:
                os.close(fd)

    def _request_slot(self, frame_index: int, request_id: int = 0,
                     dump_raw_path=None, timeout: float = 90.0) -> SlotFrame:
        """模式 2：经 SHM 槽取帧（零拷贝 mmap 读 + 显式释放）。"""
        dump = dump_raw_path is not None
        path = (os.path.abspath(str(dump_raw_path)) if dump else "/dev/null")
        flags = FLAG_WANT_SLOT | (FLAG_DUMP_RAW if dump else 0)
        if self._closed:
            raise CefWorkerError("slot client is closed")
        if self._pending_png is not None:
            raise CefWorkerError("PNG request pending; cannot mix slot and PNG receipts")
        try:
            self._send_request(frame_index, path, flags, request_id, (0, 0))
            self._sock.settimeout(timeout)
            raw = self._recv_exact(self._sock, 32, "槽索引通知")
            (magic, version, status, req_id, f_idx, slot, _rsv, seq,
             backpressure) = struct.unpack(_NOTICE_FMT, raw)
            if magic != NOTICE_MAGIC or version != PROTOCOL_VERSION:
                raise CefWorkerError(f"槽通知 magic/版本不符: {magic:#x}/{version}")
            if req_id != request_id or f_idx != frame_index:
                raise CefWorkerError("slot notice request/frame identity mismatch")
            if status != STATUS_OK:
                if status not in (STATUS_TIMEOUT, STATUS_SIZE_MISMATCH, STATUS_JS_ERR, STATUS_DUPLICATE) or slot != 0xFF:
                    raise CefWorkerError("无效的槽失败通知")
                raise CefWorkerError(
                    f"槽帧失败 status={status} backpressure_wait_us={backpressure}", status_code=status)
            if slot >= RING_SLOTS:
                raise CefWorkerError(f"槽索引越界: {slot}")
            if self._gate(slot) != (_GATE_READY, seq):
                raise CefWorkerError("槽未就绪或发布序号不符")
            offset = self._slot_offset(slot)
            header = self._parse_header_at(offset)
            self._validate_slot_header(header, request_id, frame_index)
            w, h = header["width"], header["height"]
            size = w * h * 4
            if (header["status_code"] != STATUS_OK or header["canary_frame_index"] != frame_index
                    or (header["pixel_format"], header["color_space"]) != (1, 1)
                    or (w, h) != (self.hello["max_width"], self.hello["max_height"])
                    or w <= 0 or h <= 0 or header["stride"] != w * 4 or size > self.frame_bytes):
                raise CefWorkerError("槽帧状态/金丝雀/格式/几何超出已验证契约")
            data = bytes(self._mm[offset + 128:offset + 128 + size])
            if len(data) != size or self._gate(slot) != (_GATE_READY, seq):
                raise CefWorkerError("槽快照截断或复制时发布世代已变")
            if dump and not Path(str(dump_raw_path) + ".raw").is_file():
                raise CefWorkerError(f"dump_raw 侧写缺失: {dump_raw_path}.raw")
            return SlotFrame(self, slot, seq, header, data, backpressure)
        except BaseException as error:
            if isinstance(error, CefWorkerError) and error.status_code is not None:
                raise  # a bound worker error owns no slot; held slots can still be released
            try:
                self.close()  # protocol/transport uncertainty: never reuse or blindly release
            finally:
                if isinstance(error, (OSError, ValueError, struct.error)):
                    raise CefWorkerError(f"槽传输/布局失败: {error}") from error
                raise error

    def stats(self) -> dict:
        """环级账目（诊断/证据面）。"""
        mm = self._mm
        (published,) = struct.unpack_from("<Q", mm, _CTRL_PUBLISHED)
        (released,) = struct.unpack_from("<Q", mm, _CTRL_RELEASED)
        (backpressure,) = struct.unpack_from("<Q", mm, _CTRL_BACKPRESSURE)
        gates = [self._gate(i)[0] for i in range(RING_SLOTS)]
        return {"published": published, "released": released,
                "backpressure_wait_us_total": backpressure, "gates": gates}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.close()
        finally:
            try:
                self._mm.close()
            finally:
                try:
                    os.close(self._shm_fd)
                finally:
                    pending, self._pending_png = self._pending_png, None
                    if pending is not None and pending.output_fd is not None:
                        os.close(pending.output_fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
