"""vtx_cef_worker 帧服务 Python 消费端（issue #110 CEF-02a）。

工单原文落点为 vtx/cef_client.py；本单边界禁改 vtx/ 生产面（#110 仍属
experiments 面），故与 worker 同目录交付，接线 vtx/ 留待后续工单。

协议权威：experiments/gfx_cef_worker/IPC_PROTOCOL.md（偏移表与
src/ipc_protocol.hpp 静态断言、src/shm_ring.hpp 控制块布局逐字段一致；
tests/test_cef_worker_shm.py 以 shm_ring_selftest --dump-ring 跨语言锁定）。

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

import mmap
import os
import socket
import struct
import time
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- 协议常量（镜像 ipc_protocol.hpp）

SLOT_MAGIC = 0x56545846        # "VTXF"
RING_MAGIC = 0x56545852        # "VTXR"
REQUEST_MAGIC = 0x56545851     # "VTXQ"
NOTICE_MAGIC = 0x5654584E      # "VTXN"
HELLO_MAGIC = 0x56545848       # "VTXH"
PROTOCOL_VERSION = 1

STATUS_OK = 0
STATUS_TIMEOUT = 1
STATUS_SIZE_MISMATCH = 2
STATUS_JS_ERR = 3
STATUS_DUPLICATE = 4  # #114：request_id 重放（状态机重复请求守卫）

FLAG_WANT_SLOT = 1 << 0
FLAG_DUMP_RAW = 1 << 1

RING_SLOTS = 4
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
        self._client._release_slot(self.slot_index)
        self._released = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


class CefWorkerClient:
    """单连接帧服务客户端（协议见 IPC_PROTOCOL.md）。"""

    def __init__(self, sock: socket.socket, shm_fd: int, hello: dict):
        self._sock = sock
        self._shm_fd = shm_fd
        self.hello = hello
        size = os.fstat(shm_fd).st_size
        self._mm = mmap.mmap(shm_fd, size)  # MAP_SHARED 读写（门释放需要）
        self._validate_ring(size)
        self._closed = False

    # ------------------------------------------------------------ 连接

    @classmethod
    def connect(cls, socket_path, connect_timeout=30.0, ready_timeout=30.0):
        """连 serve worker；socket_path 为其 --ipc-socket 路径。"""
        path = str(socket_path)
        deadline = time.monotonic() + connect_timeout
        last_err = None
        while True:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(path)
                break
            except (FileNotFoundError, ConnectionRefusedError) as exc:
                sock.close()
                last_err = exc
                if time.monotonic() >= deadline:
                    raise CefWorkerError(
                        f"连接 worker 失败: {path} ({last_err})") from exc
                time.sleep(0.05)
        sock.settimeout(ready_timeout)
        # Hello + SCM_RIGHTS(SHM fd) 单次 recvmsg
        data, ancdata, _flags, _addr = sock.recvmsg(
            64, socket.CMSG_SPACE(struct.calcsize("i")))
        if len(data) != 64:
            raise CefWorkerError(f"Hello 长度异常: {len(data)}")
        shm_fd = None
        for level, ctype, cdata in ancdata:
            if level == socket.SOL_SOCKET and ctype == socket.SCM_RIGHTS:
                (shm_fd,) = struct.unpack("i", cdata[:4])
        if shm_fd is None:
            raise CefWorkerError("Hello 未附带 SHM fd（SCM_RIGHTS 缺失）")
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
        return cls(sock, shm_fd, hello)

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
        self.frame_bytes = frame_bytes
        self.slot_stride = _align64(128 + frame_bytes)
        expected = _CTRL_SIZE + self.slot_stride * RING_SLOTS
        if expected != size:
            raise CefWorkerError(f"环尺寸不符: 期望 {expected} 实际 {size}")

    # ------------------------------------------------------------ 内部

    def _slot_offset(self, slot: int) -> int:
        return _CTRL_SIZE + self.slot_stride * slot

    def _gate_state(self, slot: int) -> int:
        return struct.unpack_from("<I", self._mm, _CTRL_GATES + slot * 64)[0]

    def _release_slot(self, slot: int) -> None:
        """READY→EMPTY（release-store 语义：Python 侧单写者即足）。"""
        struct.pack_into("<I", self._mm, _CTRL_GATES + slot * 64, _GATE_EMPTY)
        (released,) = struct.unpack_from("<Q", self._mm, _CTRL_RELEASED)
        struct.pack_into("<Q", self._mm, _CTRL_RELEASED, released + 1)

    def _recv_exact(self, n: int, what: str) -> bytes:
        buf = b""
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
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

    def _send_request(self, frame_index: int, out_path: str, flags: int,
                      request_id: int, expected_wh) -> None:
        if len(out_path) > MAX_OUT_PNG_PATH:
            raise CefWorkerError(
                f"out_png_path 超长（{len(out_path)}>{MAX_OUT_PNG_PATH}，"
                "64B 定长请求面约束；请用短路径）")
        exp_w, exp_h = expected_wh
        packet = struct.pack(
            _REQUEST_FMT, REQUEST_MAGIC, PROTOCOL_VERSION, flags, request_id,
            frame_index, exp_w, exp_h, out_path.encode("utf-8"))
        self._sock.sendall(packet)

    def _parse_header_at(self, offset: int) -> dict:
        values = struct.unpack_from(_HEADER_FMT, self._mm, offset)
        return dict(zip(_HEADER_FIELDS, values))

    def _validate_slot_header(self, header: dict) -> None:
        if header["magic"] != SLOT_MAGIC:
            raise CefWorkerError(f"槽头 magic 不符: {header['magic']:#x}")
        if header["protocol_version"] != PROTOCOL_VERSION:
            raise CefWorkerError(f"槽头版本不符: {header['protocol_version']}")
        if header["run_id"] != self.hello["run_id"]:
            raise CefWorkerError("槽头 run_id 陈旧（跨 run 残帧拒收）")

    # ------------------------------------------------------------ 公共 API

    def request_png(self, frame_index: int, out_png_path,
                    request_id: int = 0, expected_wh=(0, 0),
                    timeout: float = 90.0) -> FrameReceipt:
        """模式 1：worker 原地落盘 PNG，回 FrameSlotHeader 计时回执。"""
        path = os.path.abspath(str(out_png_path))
        self._send_request(frame_index, path, 0, request_id, expected_wh)
        self._sock.settimeout(timeout)
        raw = self._recv_exact(128, "模式 1 回执")
        (magic, version, status) = struct.unpack_from("<IHH", raw, 0)
        if magic != SLOT_MAGIC or version != PROTOCOL_VERSION:
            raise CefWorkerError(f"回执 magic/版本不符: {magic:#x}/{version}")
        values = struct.unpack_from(_HEADER_FMT, raw, 0)
        header = dict(zip(_HEADER_FIELDS, values))
        receipt = FrameReceipt(header=header)
        if status != STATUS_OK:
            raise CefWorkerError(
                f"帧请求失败 status={status}（1=TIMEOUT 2=SIZE_MISMATCH "
                f"3=JS_ERR）：w={header['width']} h={header['height']} "
                f"rejected={header['rejected_paints']}",
                status_code=status, detail=header)
        return receipt

    def request_slot(self, frame_index: int, request_id: int = 0,
                     dump_raw_path=None, timeout: float = 90.0) -> SlotFrame:
        """模式 2：经 SHM 槽取帧（零拷贝 mmap 读 + 显式释放）。"""
        dump = dump_raw_path is not None
        path = (os.path.abspath(str(dump_raw_path)) if dump else "/dev/null")
        flags = FLAG_WANT_SLOT | (FLAG_DUMP_RAW if dump else 0)
        self._send_request(frame_index, path, flags, request_id, (0, 0))
        self._sock.settimeout(timeout)
        raw = self._recv_exact(32, "槽索引通知")
        (magic, version, status, req_id, f_idx, slot, _rsv, seq,
         backpressure) = struct.unpack(_NOTICE_FMT, raw)
        if magic != NOTICE_MAGIC or version != PROTOCOL_VERSION:
            raise CefWorkerError(f"槽通知 magic/版本不符: {magic:#x}/{version}")
        if status != STATUS_OK:
            raise CefWorkerError(
                f"槽帧失败 status={status} backpressure_wait_us="
                f"{backpressure}", status_code=status)
        if slot >= RING_SLOTS:
            raise CefWorkerError(f"槽索引越界: {slot}")
        # 门须已 READY（发布先于通知；acquire 语义此处由进程内一致时序保证）
        if self._gate_state(slot) != _GATE_READY:
            raise CefWorkerError(f"槽 {slot} 门状态非 READY")
        offset = self._slot_offset(slot)
        header = self._parse_header_at(offset)
        self._validate_slot_header(header)
        w = header["width"]
        h = header["height"]
        data = bytes(self._mm[offset + 128: offset + 128 + w * h * 4])
        if dump:
            raw_path = str(dump_raw_path) + ".raw"
            expect = Path(raw_path)
            if not expect.is_file():
                raise CefWorkerError(f"dump_raw 侧写缺失: {raw_path}")
        return SlotFrame(self, slot, seq, header, data, backpressure)

    def stats(self) -> dict:
        """环级账目（诊断/证据面）。"""
        mm = self._mm
        (published,) = struct.unpack_from("<Q", mm, _CTRL_PUBLISHED)
        (released,) = struct.unpack_from("<Q", mm, _CTRL_RELEASED)
        (backpressure,) = struct.unpack_from("<Q", mm, _CTRL_BACKPRESSURE)
        gates = [self._gate_state(i) for i in range(RING_SLOTS)]
        return {"published": published, "released": released,
                "backpressure_wait_us_total": backpressure, "gates": gates}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.close()
        finally:
            self._mm.close()
            os.close(self._shm_fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
