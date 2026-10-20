#!/usr/bin/env python3
"""gfx_render.py — CDP 常驻浏览器 gfx 帧渲染器（vtx gfx 阶段加速后端）

替代「每帧冷启动一个 chrome --headless --screenshot 进程」的 CLI 截图路径：
单个常驻 Chrome + N 个 tab 池（默认 4，env VTX_GFX_TABS），每 tab 一条
WebSocket CDP 连接，Page.navigate → 等 document.readyState → captureScreenshot。

alpha 处理（截图路径；页面 opt-in 的 __vtx_export_png 导出优先）：
  默认尝试对各 tab 设置 Emulation.setDefaultBackgroundColorOverride(a=0)，
  透明页单次 captureScreenshot 直出带 alpha 的 PNG，零重编码。
  仅 omitBackground / fromSurface / 浏览器背景开关的早期 RGB 实测不能推出
  CDP 无法输出 alpha；WSConn 只是 CDP 的 WebSocket 传输，不决定截图透明度。
  override 不可用，或 flatten 守卫发现探针全不透明而页面实际透明时，透明页
  回落白黑双截；opt-in canvas 导出载荷失效的帧也走双截回落。不透明页在
  未启用 override 时单截直写。白黑双截同时是 override 引入期的比对金标准：
      C = a·F + (1−a)·B，a = 1 − (Cw − Cb)/255，F = Cb / a（a=0 处 F=0）。
  像素一致性以实测比对为准，不把合成公式视作任意页面的无损保证。

__vtx_render 加速特性（方案 v2 阶段 2，详见「渲染主体」前注释块）：
  VTX_GFX_NAVFREE（默认 0）=1 时每 tab 免逐帧导航，帧态由页内 __vtx_render 置位；
  VTX_GFX_STATIC（默认 1）按页面 __vtx_static_spans 声明字节复制静态跨度成员帧。
  原 foreignObject 快照批量通路已判死移除（FO 图像上下文光栅化固有偏差）。

传输使用标准库（socket/struct/base64/hashlib/json）；CDP 后端要求 numpy
（透明探针、双截 alpha 解算与 PNG 编解码），Pillow 提供可选 PNG 编解码快路径。
无 numpy 时 available() 返回不可用，调用方全量回落 CLI。

独立渲染（写入 outdir）：env python3 -m vtx.gfx_render <html> <outdir> <N帧> [--tabs 4]
"""
import base64
from . import fstxn
import hashlib
from pathlib import Path
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zlib

from .gfx_cli import png_invalid_reason, verify_frames

try:
    import numpy  # noqa: F401
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

PNG_COMPRESS_LEVEL = int(os.environ.get("VTX_GFX_PNG_LEVEL", "3"))


class GfxRenderError(RuntimeError):
    pass


class FastPNGError(GfxRenderError):
    """Explicit fast capture cannot silently retry without its requested option."""


# ---------------------------------------------------------------- PNG 最小编解码（8-bit 非交错，color_type 2/6）

def png_decode_rgb(data: bytes):
    """解码 Chrome 输出 PNG → (h, w, 3) uint8 numpy。仅支持 8-bit 非交错 RGB/RGBA。"""
    return _png_decode(data)[:, :, :3].copy()


def png_decode_rgba(data: bytes):
    """同上 → (h, w, 4)（RGB 输入补 alpha=255）。"""
    import numpy as np
    arr = _png_decode(data)
    if arr.shape[2] == 3:
        arr = np.dstack([arr, np.full(arr.shape[:2] + (1,), 255, dtype=np.uint8)])
    return arr


def _png_decode(data: bytes):
    import numpy as np
    # PIL 快路径（2026-09-11）：C 层解码 ~27ms 且释放 GIL，替代逐行 numpy unfilter
    # （1080p 实测 181ms 且持 GIL，是透明页双截路径的最大瓶颈；像素级一致性已验）。
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(data))
        if im.mode == "RGB":
            return np.asarray(im)
        return np.asarray(im.convert("RGBA"))
    except ImportError:
        pass
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, ihdr, idat = 8, None, []
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", chunk)
        elif typ == b"IDAT":
            idat.append(chunk)
        elif typ == b"IEND":
            break
        pos += 12 + ln
    w, h, depth, ctype, _comp, _filt, inter = ihdr
    if depth != 8 or inter != 0 or ctype not in (2, 6):
        raise ValueError(f"unsupported PNG depth={depth} ctype={ctype} interlace={inter}")
    nch = 3 if ctype == 2 else 4
    raw = zlib.decompress(b"".join(idat))
    stride = w * nch
    out = np.empty((h, stride), dtype=np.uint8)
    prev = np.zeros(stride, dtype=np.int32)
    p = 0
    for y in range(h):
        f = raw[p]
        p += 1
        line = np.frombuffer(raw, dtype=np.uint8, count=stride, offset=p).astype(np.int32)
        p += stride
        if f == 0:
            cur = line
        elif f == 2:  # Up（Chrome 大图主流选择）
            cur = (line + prev) & 0xFF
        elif f == 1:  # Sub：bpp=nch 字节 → 每通道独立累加和，可向量化
            cur = line.reshape(-1, nch)
            cur = np.cumsum(cur, axis=0).reshape(-1) & 0xFF
        elif f in (3, 4):  # Average / Paeth：依赖重建后左像素，逐像素（行级罕见）
            cur = line.copy()
            if f == 3:
                for i in range(stride):
                    left = cur[i - nch] if i >= nch else 0
                    cur[i] = (cur[i] + ((left + prev[i]) >> 1)) & 0xFF
            else:
                for i in range(stride):
                    left = int(cur[i - nch]) if i >= nch else 0
                    up = int(prev[i])
                    ul = int(prev[i - nch]) if i >= nch else 0
                    pp = left + up - ul
                    pa, pb, pc = abs(pp - left), abs(pp - up), abs(pp - ul)
                    pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else ul)
                    cur[i] = (cur[i] + pred) & 0xFF
        else:
            raise ValueError(f"bad PNG filter {f}")
        out[y] = cur.astype(np.uint8)
        prev = out[y].astype(np.int32)
    return out.reshape(h, w, nch)


def png_encode_rgba(arr) -> bytes:
    """(h,w,4) uint8 numpy → RGBA PNG（filter=0，zlib 级别可调）。"""
    # PIL 快路径（2026-09-11）：compress_level=PNG_COMPRESS_LEVEL，~17ms vs zlib 直压 ~22ms，
    # 且无逐行 bytes 拼接的 Python 开销；输出像素等价（QC 比对像素，不比对字节）。
    try:
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.fromarray(arr, "RGBA").save(
            buf, "PNG", compress_level=PNG_COMPRESS_LEVEL)
        return buf.getvalue()
    except ImportError:
        pass
    h, w, _ = arr.shape

    def chunk(typ, body):
        return (struct.pack(">I", len(body)) + typ + body +
                struct.pack(">I", zlib.crc32(typ + body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    rows = b"".join(b"\x00" + arr[y].tobytes() for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(rows, PNG_COMPRESS_LEVEL)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- 最小 WebSocket 客户端（RFC6455 客户端子集）

_WS_ACCEPT_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_WS_FRAME_MAX = 1 << 30   # 单帧 1GiB 防御上限：流错乱时防内存爆


class WSConn:
    def __init__(self, port, path, timeout=20):
        self._rbuf = bytearray()   # 握手响应同包尾随的帧字节（罕见），交 recv 消费
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.sock.settimeout(timeout)
        try:
            self._handshake(port, path)
        except BaseException:
            self.sock.close()   # 握手半途失败显式关 fd（防异常 traceback 持帧引用拖延回收）
            raise

    def _handshake(self, port, path):
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("ws handshake failed")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        self._rbuf += rest
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"ws handshake rejected: {head[:120]!r}")
        # G1-049 销号（RECP-20260916-002）：校验 Sec-WebSocket-Accept =
        # b64(sha1(key + GUID))（RFC6455 §4.2.2），防握手降级/错接。回环 Chrome
        # 恒带此头，实跑验证不引入回归。
        expected = base64.b64encode(
            hashlib.sha1((key + _WS_ACCEPT_GUID).encode()).digest()).decode()
        accept = None
        for line in head.decode("latin-1").split("\r\n")[1:]:
            name, _, val = line.partition(":")
            if name.strip().lower() == "sec-websocket-accept":
                accept = val.strip()
                break
        if accept != expected:
            raise ConnectionError(f"ws handshake accept mismatch: {accept!r}")

    def _send_frame(self, op, payload):
        hdr = bytearray([0x80 | op])
        n = len(payload)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        self.sock.sendall(bytes(hdr) + bytes(b ^ mask[i & 3] for i, b in enumerate(payload)))

    def send_text(self, text):
        self._send_frame(0x1, text.encode())

    def _recv_exact(self, n):
        buf = bytearray(self._rbuf[:n])
        del self._rbuf[:len(buf)]
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("ws closed")
            buf += chunk
        return bytes(buf)

    def recv_msg(self):
        """读一条完整消息：自动重组分片；ping→回 pong、pong→忽略（RECP-20260916-002：
        原实现把控制帧/分片原样喂 json.loads，Chrome 一旦发 ping 即解析失败，触发
        无谓的浏览器重启自愈）。close 帧仍原样返回由调用方处理。"""
        op, frag = None, bytearray()
        while True:
            hdr = self._recv_exact(2)
            fin, top = hdr[0] & 0x80, hdr[0] & 0x0F
            ln = hdr[1] & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._recv_exact(8))[0]
            if ln > _WS_FRAME_MAX:
                raise ConnectionError(f"ws frame too large: {ln}")
            if hdr[1] & 0x80:  # 服务器帧按协议不掩码，防御性处理
                mask = self._recv_exact(4)
                payload = bytes(b ^ mask[i & 3] for i, b in enumerate(self._recv_exact(ln)))
            else:
                payload = self._recv_exact(ln)
            if top == 0x8:            # close：上抛给 call() 判连接关闭
                return 0x8, payload
            if top == 0x9:            # ping → pong（控帧可夹在数据分片之间）
                self._send_frame(0xA, payload)
                continue
            if top == 0xA:            # pong → 忽略
                continue
            if op is None:
                if top == 0x0:
                    raise ConnectionError("ws unexpected continuation frame")
                op, frag = top, bytearray(payload)
            else:                     # 数据续帧（top==0x0）
                frag += payload
            if fin:
                return op, bytes(frag)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Tab:
    """一个 page target 的 CDP 会话（线程独占）。"""

    def __init__(self, port, ws_path, width=1920, height=1080, *, png_fast=False):
        self.png_fast = png_fast
        self.capture_calls = {"succeeded": 0, "failed": 0}
        self.ws = WSConn(port, ws_path)
        try:
            self._mid = 0
            self.request_policy = None
            self._policy_commands = set()
            self.call("Page.enable")
            self.call("Emulation.setDeviceMetricsOverride",
                      width=width, height=height, deviceScaleFactor=1, mobile=False)
        except BaseException:
            self.ws.close()   # 半初始化 Tab 显式关 fd（RECP-20260916-002）
            raise

    def close(self):
        """释放本 tab 的 CDP socket（对端已死时为无害 no-op，close 内吞 OSError）。"""
        self.ws.close()

    def call(self, method, timeout=30, **params):
        self._mid += 1
        mid = self._mid
        self.ws.send_text(json.dumps({"id": mid, "method": method, "params": params}))
        deadline = time.monotonic() + timeout
        response = None
        events_seen = 0
        while time.monotonic() < deadline:
            op, data = self.ws.recv_msg()
            if op == 0x8:
                raise ConnectionError("ws close frame")
            msg = json.loads(data)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                response = msg.get("result", {})
            if msg.get('id') in self._policy_commands:
                self._policy_commands.remove(msg['id'])
                if 'error' in msg:
                    raise RuntimeError(f"strict request enforcement failed: {msg['error']}")
            if msg.get('method') == 'Fetch.requestPaused' and self.request_policy is not None:
                events_seen += 1
                if events_seen > 10000 or len(self._policy_commands) >= 256:
                    raise RuntimeError('strict request event budget exceeded')
                command, args = self.request_policy.response(msg['params'])
                self._mid += 1
                self._policy_commands.add(self._mid)
                self.ws.send_text(json.dumps({'id': self._mid, 'method': command, 'params': args}))
            if response is not None and not self._policy_commands:
                if self.request_policy is not None:
                    self.request_policy.check()
                return response
            # Other events have no effect on the single-threaded legacy client.
        raise TimeoutError(f"CDP {method} timeout")

    def navigate(self, url, timeout=5.0):
        """导航并等待新文档就绪。坑：Page.navigate 返回时旧文档可能仍在，
        readyState=="complete" 是旧页的（曾致全部帧渲成 f=0 的空页）——
        必须等 location.search 变成目标 url 的 query 才是新文档。"""
        self.call("Page.navigate", url=url, timeout=10)
        want = ("?" + url.split("?", 1)[1]) if "?" in url else ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self.call("Runtime.evaluate",
                          expression="location.search+'|'+document.readyState",
                          returnByValue=True)
            val = r.get("result", {}).get("value") or ""
            search, _, ready = val.partition("|")
            if search == want and ready == "complete":
                return
            time.sleep(0.02)
        raise TimeoutError(f"page load timeout: {url}")

    def eval(self, expression):
        r = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        return r.get("result", {}).get("value")

    def shot(self) -> bytes:
        if not self.png_fast:
            r = self.call("Page.captureScreenshot", format="png", timeout=30)
            return base64.b64decode(r["data"])
        try:
            r = self.call("Page.captureScreenshot", format="png", timeout=30,
                          optimizeForSpeed=True)
            data = base64.b64decode(r["data"])
        except Exception as e:
            self.capture_calls["failed"] += 1
            raise FastPNGError("fast PNG capture failed; fallback forbidden") from e
        self.capture_calls["succeeded"] += 1
        return data


_SUPERVISION_WARNED = False


def _warn_supervision_degraded():
    """O1（红队）：监管不可用 → 回落无监管路径的一次性显式 stderr 告警。
    「一次性」= 每进程首个降级 Browser 打一条（hard_restart/多页多 Browser
    场景不刷屏），但不静默——用户须知道本进程的 Chrome 无 kill -9 归零保障。"""
    global _SUPERVISION_WARNED
    if _SUPERVISION_WARNED:
        return
    _SUPERVISION_WARNED = True
    print("⚠ proc_supervisor 监管不可用（非 Linux 或 /proc 缺失）：Chrome 回落"
          "无监管路径（无 PDEATHSIG/守卫/账本，Profile 落系统临时目录；宿主被"
          "kill -9 后浏览器进程树不会自动归零，需自行清理）", file=sys.stderr)


class Browser:
    """惰性启动的常驻 headless Chrome。崩溃后 hard_restart() 重建（线程安全）。

    ctx 透传（issue #103）：渲染走 vtx gfx 阶段时传入 Ctx，Chrome 交
    proc_supervisor.SupervisedProcessGroup 监管——独立会话/进程组（pgid==pid）、
    PDEATHSIG、父存活管道守卫、tmp_run PID 账本；Profile 落项目
    cache/tmp/<UTC>-gfx_cdp-*/ 下（杜绝写系统 /tmp tmpfs），宿主被 kill -9 时
    全树（Browser/GPU/Zygote/Renderer）由守卫 killpg 归零、残留账本由下一轮
    gfx 阶段 reap_orphan_workers 收口。监管不可用平台（非 Linux/无 /proc，
    supervision_available()=False）真实降级：回落本类 #103 之前的无监管
    形态（裸 Popen + tempfile.mkdtemp，与 ctx=None 同路径）并一次性 stderr
    告警。ctx=None（独立直跑入口）保持原 tempfile.mkdtemp + 裸 Popen 形态。"""

    def __init__(self, chrome_path=None, tabs=4, width=1920, height=1080, ctx=None,
                 *, png_fast=False):
        self.png_fast = png_fast
        self.protocol_checks = {"succeeded": 0, "failed": 0}
        self.capture_counters = []
        self.chrome = chrome_path
        self.tabs_n = max(1, tabs)
        self.width, self.height = width, height
        self.ctx = ctx
        self.group = None       # SupervisedProcessGroup（ctx 模式独有）
        self.proc = None
        self.windows_job = None
        self.port = None
        self.user_data_dir = None
        self.tab_ws_urls = []
        self.lock = threading.Lock()
        self.generation = 0

    def _find_chrome(self):
        if self.chrome:
            return
        for c in [os.environ.get("VTX_CHROME"), "google-chrome", "google-chrome-stable",
                  "chromium", "chromium-browser", "chrome", "msedge"]:
            if c and (shutil.which(c) or os.path.isfile(c)):
                self.chrome = c
                return
        if sys.platform == "win32":   # Windows 标准安装路径（含 (x86) 的系统自带 Edge）
            w = os.environ.get("PROGRAMFILES", r"C:\Program Files")
            pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
            for c in (rf"{w}\Google\Chrome\Application\chrome.exe",
                      rf"{pf86}\Google\Chrome\Application\chrome.exe",
                      rf"{w}\Microsoft\Edge\Application\msedge.exe",
                      rf"{pf86}\Microsoft\Edge\Application\msedge.exe"):
                if os.path.isfile(c):
                    self.chrome = c
                    return
        raise RuntimeError("no chrome found")

    def start(self):
        self._find_chrome()
        from .gfx_environment import CHROME_VISUAL_FLAGS
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        supervised = self.ctx is not None
        if supervised:
            # O1（红队）：非 Linux/无 /proc 平台监管器不可用（账本拒绝编造）。
            # 真实降级 = 回落 #103 之前的无监管路径 + 一次性 stderr 告警
            # （macOS 是 install.sh 官方支持平台，gfx 阶段不得硬失败），
            # 而非让 _write_ledger 的 RuntimeError 打死整个 CDP 默认路径。
            from .proc_supervisor import supervision_available
            supervised = supervision_available()
            if not supervised and sys.platform != "win32":
                _warn_supervision_degraded()
        if supervised:
            # issue #103：监管态——argv 由运行目录动态拼（--user-data-dir 落
            # tmp_run 的 profile/）；supervisor 尾追 --parent-alive-fd=<fd>
            # （Chrome 忽略未知开关，fd 由守卫持有强制全树归零，不依赖协作）。
            from .proc_supervisor import SupervisedProcessGroup
            self.group = SupervisedProcessGroup(
                self.ctx,
                lambda run_dir, profile: [
                    self.chrome, *CHROME_VISUAL_FLAGS,
                    f"--remote-debugging-port={self.port}",
                    f"--user-data-dir={profile}",
                    "--no-first-run", "--no-default-browser-check", "about:blank"],
                backend_id="cdp")
            self.group.start()
            self.proc = self.group.proc
            self.user_data_dir = self.group.profile_dir
        else:
            self.user_data_dir = tempfile.mkdtemp(prefix="vtx_gfx_cdp_")
            argv = [self.chrome, *CHROME_VISUAL_FLAGS,
                    f"--remote-debugging-port={self.port}",
                    f"--user-data-dir={self.user_data_dir}",
                    "--no-first-run", "--no-default-browser-check", "about:blank"]
            if sys.platform == "win32":
                from .windows_browser import BrowserJob
                self.windows_job = BrowserJob(argv)
                self.proc = self.windows_job.proc
            else:
                self.proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/version", timeout=1)
                break
            except Exception:
                if self.proc.poll() is not None:
                    raise RuntimeError("chrome exited during startup")
                time.sleep(0.2)
        else:
            raise RuntimeError("chrome devtools not ready in 15s")
        if self.png_fast:
            self._check_png_fast_protocol()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")
        self.tab_ws_urls = []
        for _ in range(self.tabs_n):
            info = json.load(urllib.request.urlopen(req, timeout=5))
            url = info["webSocketDebuggerUrl"]
            self.tab_ws_urls.append("/" + url.split("/", 3)[3])

    def open_tabs(self):
        # 部分失败回收：第 k 个 Tab 起不来时，前 k-1 个已建会话显式关闭再上抛
        # （RECP-20260916-002；原 list 推导式泄漏半建池，仅靠 GC 兜底）。
        tabs = []
        try:
            for u in self.tab_ws_urls:
                tab = Tab(self.port, u, self.width, self.height,
                          **({"png_fast": True} if self.png_fast else {}))
                tabs.append(tab)
                if self.png_fast:
                    self.capture_counters.append(tab.capture_calls)
        except BaseException:
            for t in tabs:
                t.close()
            raise
        return tabs

    def _check_png_fast_protocol(self):
        """One bounded local protocol check per actual browser start/generation."""
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/protocol", timeout=5) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("protocol response too large")
            protocol = json.loads(data)
            supported = any(
                p.get("name") == "optimizeForSpeed" and p.get("type") == "boolean"
                and p.get("optional") is True
                for domain in protocol.get("domains", []) if domain.get("domain") == "Page"
                for command in domain.get("commands", [])
                if command.get("name") == "captureScreenshot"
                for p in command.get("parameters", []))
            if not supported:
                raise ValueError("optional boolean optimizeForSpeed unavailable")
        except Exception as e:
            self.protocol_checks["failed"] += 1
            raise FastPNGError("fast PNG protocol check failed; fallback forbidden") from e
        self.protocol_checks["succeeded"] += 1

    def png_capture(self):
        from .gfx_backend import png_capture_provenance
        result = png_capture_provenance()
        result["protocol_checks"] = dict(self.protocol_checks)
        for counters in self.capture_counters:
            for key, count in counters.items():
                result["capture_calls"][key] += count
        if sum(result["capture_calls"].values()):
            result["actual_parameters"] = dict(result["requested_parameters"])
        return result

    def stop(self):
        # issue #103：监管态收尾统一走组停机（SIGTERM 主进程 → 宽限 → 组残员
        # killpg → 删账本 → 关管道收守卫 → tmp_run rmtree 运行目录含 Profile）。
        if self.group is not None:
            group, self.group = self.group, None
            group.stop()
            self.proc = None
            self.user_data_dir = None
            return
        if self.windows_job is not None:
            job, self.windows_job = self.windows_job, None
            job.close()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        self.proc = None
        if self.user_data_dir:
            # Windows releases file handles asynchronously after job termination.
            for attempt in range(30):
                try:
                    shutil.rmtree(self.user_data_dir)
                    self.user_data_dir = None
                    break
                except FileNotFoundError:
                    self.user_data_dir = None
                    break
                except OSError:
                    if attempt == 29:
                        raise
                    time.sleep(0.1)

    def hard_restart(self):
        """杀进程重起 + 重建 tab 池 URL（连接方需重开 Tab）。监管态下重起 =
        新监管组（新 tmp_run 目录/账本/守卫），旧组完整停机清场。"""
        self.stop()
        self.start()
        self.generation += 1


# ---------------------------------------------------------------- __vtx_render 两项加速（方案 v2 阶段 2 转向后设计）
#
# 页面契约：window.__vtx_render(f) 为同步纯函数（调用后页面视觉状态 == 以 ?f=f
# 加载的状态，幂等、与调用顺序无关）。
#
# 特性 1 nav-free 截图（VTX_GFX_NAVFREE，默认 0）：页面有 __vtx_render 时每 tab
# 只导航一次，之后每帧页内 __vtx_render(f) 置帧态 + 现有 tab.shot()，省每帧导航。
# 像素风险点=「JS 突变后 captureScreenshot 是否强制重绘出当前帧」，由集成阶段
# golden 全帧比对验收；渲染器侧不做额外校验，仅 navfree×N 打标 + 启用告警。
# eval 用 try/catch 包一层取哨兵值：__vtx_render 抛错的帧立即转回导航路径，
# 绝不把失败帧的前一帧状态静默截下来。
#
# 特性 2 静态跨度去重（VTX_GFX_STATIC，默认 1）：页面可声明
#   window.__vtx_static_spans = [[a,b], ...]（表示 [a,b)，与 __vtx_render 无关）。
#   这是作者的像素恒定承诺，DOM 序列化相等不能证明实际像素相等。渲染器信任声明，
#   区间头渲染/复用，缺失成员字节复制头帧文件（零解码零编码），已存在成员不动；
#   解析失败告警后按无声明渲染。采用前关闭静态复制，以相同输入/环境实渲整个跨度
#   并比对像素；页面或依赖变化后重验。完整合同见 docs/skill/references/gfx.md。
# 原 foreignObject 快照/alpha 探针通路已判死（FO 图像上下文光栅化固有偏差，
# golden 全零门槛不过），相关驱动与 VTX_GFX_BATCH* env 一并移除。

# ---------------------------------------------------------------- 渲染主体

_BG_INJECT_WHITE = (
    "(()=>{let s=document.getElementById('__vtx_bg');if(!s){s=document.createElement('style');"
    "s.id='__vtx_bg';document.documentElement.appendChild(s);}"
    "s.textContent=`html,body{background:#fff !important}`;'ok'})()")
_BG_INJECT_BLACK = (
    "(()=>{const s=document.getElementById('__vtx_bg');"
    "s.textContent=`html,body{background:#000 !important}`;'ok'})()")

# 观测（方案 v2 阶段 0）：导出路径打标 + 每帧分项计时，只加观测不改像素行为。
# 方案 v2 阶段 2 追加：navfree=经页内 __vtx_render 置帧态的免导航截图帧；
# static_copied=按静态跨度声明字节复制头帧产生的成员帧（不计入 rendered）。
_PATH_KEYS = ("override", "canvas", "twoshot", "opaque", "navfree",
              "canvas_fallback", "static_copied", "failed")
_TIME_CATS = ("nav", "export", "decode", "write")


def _empty_path_counts():
    """每帧导出路径计数。键 = 帧实际走的分支（不凭 canvas_export 布尔推断）；
    canvas 校验失败回落帧同时计入实际路径与 canvas_fallback，故总和可大于帧数。"""
    return dict.fromkeys(_PATH_KEYS, 0)


def _timings_summary(seconds_list):
    """一个分项的耗时样本（秒）→ {"p50","p95","sum"} 毫秒；无成功帧时全 0。"""
    import numpy as np
    if not seconds_list:
        return {"p50": 0.0, "p95": 0.0, "sum": 0.0}
    ms = np.asarray(seconds_list, dtype=np.float64) * 1000.0
    return {"p50": float(np.percentile(ms, 50)), "p95": float(np.percentile(ms, 95)),
            "sum": float(ms.sum())}


def page_transparent(tab) -> bool:
    """像素级检测：探针帧分别注白/黑背景截图，无差异（≤1/255）即整页有效不透明
    → 后续帧单截直写 Chrome 原生 PNG；有差异 → 透明页，每帧走两截解算。
    （模板页常见形态=body transparent + 内部不透明 #bg 满幅元素，computed style
    不可靠，像素检测是唯一可靠判据。）"""
    import numpy as np
    tab.eval(_BG_INJECT_WHITE)
    w_png = tab.shot()
    tab.eval(_BG_INJECT_BLACK)
    b_png = tab.shot()
    d = np.abs(png_decode_rgb(w_png).astype(np.int16)
              - png_decode_rgb(b_png).astype(np.int16)).max()
    return bool(d > 1)


def _two_shot_combine(png_white: bytes, png_black: bytes) -> bytes:
    import numpy as np
    cw = png_decode_rgb(png_white).astype(np.int16)
    cb = png_decode_rgb(png_black).astype(np.int16)
    # alpha 理论上各通道同值；取通道最大差 robust against 8-bit 舍入
    a = np.clip(255 - (cw - cb).max(axis=2), 0, 255).astype(np.uint8)  # (h,w)
    af = a.astype(np.float32) / 255.0
    f = np.where(af[..., None] > 0.0,
                 cb / np.maximum(af[..., None], 1e-6), 0.0)  # F = Cb/a（非预乘）
    rgb = np.clip(np.rint(f), 0, 255).astype(np.uint8)
    return png_encode_rgba(np.dstack([rgb, a]))


def decode_canvas_export(data_url, width, height):
    """Validate the opt-in canvas PNG envelope without decoding every pixel."""
    prefix = "data:image/png;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        raise GfxRenderError("invalid canvas export data URL")
    try:
        png = base64.b64decode(data_url[len(prefix):], validate=True)
        if png[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("not a PNG")
        pos, header, image_data, ended = 8, False, False, False
        while pos + 12 <= len(png):
            size = struct.unpack(">I", png[pos:pos + 4])[0]
            end = pos + 12 + size
            if end > len(png):
                raise ValueError("truncated PNG chunk")
            kind = png[pos + 4:pos + 8]
            data = png[pos + 8:pos + 8 + size]
            crc = struct.unpack(">I", png[end - 4:end])[0]
            if zlib.crc32(kind + data) & 0xffffffff != crc:
                raise ValueError("invalid PNG checksum")
            if pos == 8:
                if kind != b"IHDR" or size != 13:
                    raise ValueError("missing PNG header")
                w, h, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
                if (w, h) != (width, height) or depth != 8 or color not in (2, 6):
                    raise ValueError("unexpected canvas dimensions or pixel format")
                if (compression, filtering, interlace) != (0, 0, 0):
                    raise ValueError("unsupported canvas PNG encoding")
                header = True
            if kind == b"IDAT":
                image_data = True
            if kind == b"IEND":
                ended = size == 0 and end == len(png)
                break
            pos = end
        if not (header and image_data and ended):
            raise ValueError("incomplete PNG")
        return png
    except (ValueError, struct.error) as exc:
        raise GfxRenderError(f"invalid canvas PNG: {exc}") from exc


def _normalize_spans(raw, n_frames):
    """__vtx_static_spans 声明解析：[[a,b),…] 帧号左闭右开、0≤a<b≤n_frames。
    缺失（None）→ 空表（无缩减）；形状/数值非法 → None（调用方 warning 后按
    无声明处理）——声明是惰性数据，任何解析问题都不阻断渲染。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        return None
    out = []
    try:
        for sp in raw:
            if not (isinstance(sp, (list, tuple)) and len(sp) == 2):
                return None
            a, b = int(sp[0]), int(sp[1])
            if not (0 <= a < b <= n_frames):
                return None
            out.append((a, b))
    except (TypeError, ValueError):
        return None
    return out


def plan_static_spans(spans, want, outdir, *, width=None, height=None):
    """静态跨度缩减规划（纯函数，文件系统只读探测）。

    spans=[(a,b),…] 声明区间；want=本调用缺失帧号集（现行 work 语义：frames 参数
    ∩ 缺失帧）。返回 (work, copy_now, deferred, n_spans)：
      work      渲染帧号升序表——原 want 加上「缺失且不在本调用」的跨度头
                （跨分片块正确性：块内自足，头不在 frames 参数也并入渲染）
      copy_now  [(member, head_path)] 头帧已缓存 → 立即字节复制
      deferred  [(head, [members])]   头帧由本次渲染落盘后复制
      n_spans   生效跨度数（有成员被复制/待复制的跨度）
    成员只取 want 内的缺失帧——已存在帧不动（与现行续渲语义一致）；帧被第一个
    声明它的跨度认领，重叠声明不重复处理。"""
    want_set = set(want)
    claimed = set()
    work_add = set()
    copy_now, deferred = [], []
    n_spans = 0
    for a, b in spans:
        head = a
        head_path = os.path.join(outdir, f"f{head:04d}.png")
        members = [m for m in range(a + 1, b)
                   if m in want_set and m not in claimed]
        if not members:
            continue
        claimed.update(members)
        if png_invalid_reason(head_path, width=width, height=height) is None:
            copy_now.extend((m, head_path) for m in members)
        else:
            # 头帧缺失：∈want 则已在 work；∉want（其他分片块的范围）也并入本调用
            if head not in want_set:
                work_add.add(head)
            deferred.append((head, members))
        n_spans += 1
    # 被复制成员退出渲染集（字节复制替代渲染）；头帧并入
    work = sorted((set(want) - claimed) | work_add)
    return work, copy_now, deferred, n_spans


def _atomic_write_bytes(path, data):
    """帧落盘原子化（M1）：同目录临时文件写完整字节 → os.replace 原子替换目标。
    背景：同页 split×k 时多个 render_page 并发写同一 outdir（跨度头帧可被两块
    同时渲染并双写），直接 truncate/write 交错会产生撕裂 PNG 并经 deferred 复制
    扩散。os.replace 在同目录（同一文件系统）上是单次原子目录项替换：并发读者/
    双写者见到的目标文件恒为「旧文件或某一次写入的完整新字节」，绝无交错混合。
    字节内容与直接 write 完全一致（像素零影响）；tmp 名由 mkstemp 保证同目录
    唯一（O_EXCL），成功路径 tmp 已被 replace 移走、异常路径 finally 清理，
    两种路径均不留 tmp 残留。"""
    fstxn.atomic_write(path, data)


def _close_fast_browser(browser, pool, error=None, **frame_counts):
    """Keep the primary failure and accounting even when fast cleanup also fails."""
    cleanup_errors = []
    for tab in pool:
        try:
            tab.close()
        except BaseException as e:
            cleanup_errors.append(("tab.close", e))
    try:
        browser.stop()
    except BaseException as e:
        cleanup_errors.append(("browser.stop", e))
    if error is None and not cleanup_errors:
        return
    capture = browser.png_capture()
    capture.update(frame_counts)
    if cleanup_errors:
        capture["cleanup_failures"] = [
            {"operation": operation, "error_type": type(e).__name__}
            for operation, e in cleanup_errors]
    cleanup_only = error is None
    if cleanup_only:
        error = cleanup_errors[0][1]
    error.png_capture = capture
    # Preserve actual exception objects as well as bounded receipt metadata.
    error.png_cleanup_errors = tuple(e for _, e in cleanup_errors)
    if cleanup_only:
        raise error


def render_page(html_path, outdir, n_frames, chrome_path=None, tabs=None,
                width=1920, height=1080, progress=None, frames=None, ctx=None,
                dependency_record=None, png_fast=None):
    """渲一页 → outdir/f{:04d}.png × n（已存在的帧跳过）。

    ctx（issue #103 可选）：vtx gfx 阶段传入 Ctx 后，常驻 Chrome 交
    proc_supervisor 监管（独立进程组/PDEATHSIG/守卫/tmp_run 账本，Profile 落
    项目 cache/tmp/ 而非系统 /tmp；宿主被 kill -9 全树归零）。缺省 None =
    独立直跑形态（tempfile.mkdtemp + 裸 Popen）不变。

    frames=None（默认）= 现行语义：work=全部 n 帧里的缺失帧；显式传帧号列表
    （页内分片，方案 v2 阶段 1）= work=frames 与缺失帧的交集，frames 之外的
    帧一概不碰。work 为空（全缓存页/块）时在构造 Browser 之前早退——不再
    为纯缓存命中白起 Chrome；早退返回 stats 字段与正常路径完全一致。

    返回统计 dict；帧级失败（含浏览器崩溃自愈重试 2 次）列入 failed，
    调用方对这些帧回落 CLI。rendered = work 帧渲染成功数（按 work 归属过滤
    failed 后扣减，deferred 复制失败成员不入该口径；static_copied 字节复制帧
    单独计数不占 rendered）。帧落盘统一走 _atomic_write_bytes（同目录 tmp +
    os.replace 原子替换，并发双写不撕裂）。浏览器起不来抛 GfxRenderError（全页回落 CLI）。
    观测字段（方案 v2 阶段 0，只加观测）：path_counts=各帧实际导出路径计数
    （canvas 校验失败回落帧同时计入实际路径与 canvas_fallback）；timings_ms=
    nav/export/decode/write 各分项 p50/p95/sum 毫秒（只含成功帧；每 tab 线程
    本地累积、join 后单线程合并，运行期无共享写免锁）。

    __vtx_render 两项加速（方案 v2 阶段 2，转向后设计）：
    nav-free 截图（VTX_GFX_NAVFREE，默认 0）：页面有 __vtx_render 时每 tab 只
    导航一次，之后每帧页内 __vtx_render(f) 置帧态 + 现有截图路径（打标 navfree）；
    页内执行抛错的帧立即转回导航路径（页级一次性告警），绝不静默截错帧。
    静态跨度去重（VTX_GFX_STATIC，默认 1）：页面声明 __vtx_static_spans=[[a,b),…]
    （区间画面恒定承诺）时，缺失的区间成员帧字节复制头帧文件（static_copied 计数，
    不占 rendered）；头帧缺失则并入本调用渲染集（跨分片块/续渲天然正确）；声明
    缺失/解析失败 → 无缩减原路径。见模块头注释。
    未启用特性的页行为逐字节不变：VTX_GFX_NAVFREE=0 时零额外 eval；
    VTX_GFX_STATIC 只多一次声明读取 eval（惰性数据，像素零影响）。
    """
    from .gfx_backend import png_fast_requested, png_capture_provenance
    if png_fast is None:
        png_fast = png_fast_requested()
    if dependency_record is not None:
        return render_strict_page(html_path, outdir, n_frames, chrome_path,
                                  width, height, frames, ctx, dependency_record,
                                  png_fast=png_fast)
    if not _HAS_NUMPY:
        raise GfxRenderError("numpy unavailable")
    tabs_n = tabs or int(os.environ.get("VTX_GFX_TABS", "4"))
    html_path = os.path.abspath(html_path)
    outdir = os.path.abspath(outdir)
    fstxn.mkdir(outdir)
    base_uri = Path(html_path).resolve().as_uri()
    requested = list(range(n_frames) if frames is None else frames)
    work = [i for i, _ in verify_frames(outdir, requested, width=width, height=height)]
    if not work:
        capture = png_capture_provenance() if png_fast else None
        if capture is not None:
            capture["reused_frames"] = len(requested)
        return {"n": n_frames, "rendered": 0, "failed": [], "transparent": None,
                "tabs": tabs_n, "ms_total": 0.0, "ms_per_frame": 0.0,
                "canvas_export": False, "path_counts": _empty_path_counts(),
                "static_spans": 0,
                "timings_ms": {c: _timings_summary([]) for c in _TIME_CATS},
                **({"png_capture": capture} if png_fast else {})}
    # Browser 构造/启动放在 work 空判之后（v2 阶段 1 页内分片）：全缓存块零
    # Chrome；ms_total 仍从启动完成后起算（与旧语义一致，不含浏览器启动耗时）
    browser = Browser(chrome_path, tabs_n, width, height, ctx=ctx,
                      **({"png_fast": True} if png_fast else {}))
    try:
        browser.start()
    except BaseException as e:
        # start 在下方 try(其 finally 才调 stop)之外,半途失败时 Chrome 进程
        # (起了但 devtools 未就绪/建 tab 失败)与 mkdtemp 临时目录双泄漏且调用方
        # 拿不到 stop——此处显式回收再上抛(RECP-20260916-002 主修缺陷)。
        if png_fast:
            _close_fast_browser(browser, [], e)
        else:
            browser.stop()
        raise
    pool = []
    t0 = time.monotonic()
    failed = []
    transparent = None
    canvas_export = False
    per_tab = [None] * tabs_n   # 观测槽位：worker 收尾写入 (path_counts, time_samples)
    warned = [False]            # navfree 页内执行失败「首次告警」槽（页级）
    navfree_on = False          # 特性 1：本页 nav-free 截图开关（setup 判定）
    static_spans_n = 0          # 特性 2：生效跨度数（stats 打 span×K）
    static_copied_n = 0         # 特性 2：字节复制成员帧数（write 成功才计数，B5 口径）
    deferred_spans = []         # [(head, [members])]：头帧本次渲染落盘后复制
    reused_frames = len(requested) - len(work)
    worker_errors = [None] * tabs_n

    def note(msg):
        """加速特性告警：progress 缺席（vtx 现行接法）也必须可见——落 stderr。"""
        if progress:
            progress("⚠ gfx_render: " + msg)
        else:
            print("⚠ gfx_render: " + msg, file=sys.stderr)

    def do_frame(tab, i, navfree=False):
        """渲一帧 → (png, 实际路径标, 分项耗时秒)。路径标 ∈ canvas/navfree/override/
        twoshot/opaque，在函数内各分支出口就地打标（与真实走向一致，不凭 canvas_export
        布尔推断）；navfree=True 时免导航——页内 __vtx_render(i) 置帧态（try/catch
        哨兵：页内抛错即 raise，由调用方转回导航路径，绝不静默截前一帧），截图子
        路径统一打标 navfree；canvas 校验失败的回落帧由 worker 另计 canvas_fallback。
        setup 探针帧不走本函数，不进分项样本；write 由 worker 侧单独计。"""
        timing = dict.fromkeys(("nav", "export", "decode"), 0.0)
        t0 = time.monotonic()
        if navfree:
            # 特性 1 nav-free：省每帧导航。哨兵 0 = 页内 __vtx_render 抛错。
            ok = tab.eval("(()=>{try{window.__vtx_render(%d);return 1}"
                          "catch(e){return 0}})()" % i)
            if ok != 1:
                raise GfxRenderError(f"__vtx_render({i}) 页内执行失败")
        else:
            # PR#1 合入：Path.as_uri() 生成 file URL——Windows 盘符保留、空格/Unicode/#/
            # ? 等 URL 保留字全部百分号编码（旧 f"file://{path}" 在含 # 的目录下截断成
            # fragment，Linux 同样中招；无特殊字符的绝对路径两形式逐字符一致）。
            tab.navigate(f"{base_uri}?f={i}")
        timing["nav"] = time.monotonic() - t0
        if canvas_export:
            # Canvas 导出通道（PR#1）：页面可选实现 window.__vtx_export_png() 同步返回
            # 画布 dataURL——校验 PNG 信封（分块边界/CRC/IHDR 尺寸色彩型/IEND）后直用，
            # 失效载荷绝不写入可续渲缓存，回落同页截图路径（见下，两截保 alpha）。
            t0 = time.monotonic()
            ok = True
            try:
                png = decode_canvas_export(tab.eval("window.__vtx_export_png()"), width, height)
            # B6：except 收窄到 (GfxRenderError, TypeError)——decode_canvas_export 已把
            # 载荷类 ValueError/struct.error 包装成 GfxRenderError；json.JSONDecodeError
            # （CDP 响应损坏，ValueError 子类）不再被误吞成「载荷失效」，照常走重试/自愈
            except (GfxRenderError, TypeError):
                ok = False
            timing["export"] = time.monotonic() - t0
            if ok:
                return png, "canvas", timing
        if use_override:
            # 透明快路径（2026-09-11）：Emulation.setDefaultBackgroundColorOverride(a=0)
            # 使 captureScreenshot 直出带 alpha 的 RGBA PNG——单截直写，与白黑双截金标准
            # 逐像素一致（b1_bricks/g6b_endboard/nav_two_pillars 实测 max|Δ|=0），且
            # override 跨导航持久（每 tab 设一次）。替代注入式双截（省一次整页光栅+
            # 两次解码+一次重编码）。注意：override 干不过页面显式 CSS 背景，gfx 页协议
            # 要求 body 透明故无冲突；直写 Chrome 原生 PNG，Python 侧零编解码。
            t0 = time.monotonic()
            png = tab.shot()
            timing["export"] = time.monotonic() - t0
            return png, ("navfree" if navfree else "override"), timing
        if not transparent:
            # 整页有效不透明且 override 未启用：朴素单截直写
            t0 = time.monotonic()
            png = tab.shot()
            timing["export"] = time.monotonic() - t0
            return png, ("navfree" if navfree else "opaque"), timing
        # 白黑两截：注入+截图属浏览器侧导出，解算+重编码属 Python 侧
        t0 = time.monotonic()
        tab.eval(_BG_INJECT_WHITE)
        w_png = tab.shot()
        tab.eval(_BG_INJECT_BLACK)
        b_png = tab.shot()
        timing["export"] = time.monotonic() - t0
        t0 = time.monotonic()
        png = _two_shot_combine(w_png, b_png)
        timing["decode"] = time.monotonic() - t0
        return png, ("navfree" if navfree else "twoshot"), timing

    def apply_override(tabs_):
        """给每个 tab 设透明背景 override；任一失败返回 False（整页回落双截）。"""
        try:
            for t in tabs_:
                t.call("Emulation.setDefaultBackgroundColorOverride",
                       color={"r": 0, "g": 0, "b": 0, "a": 0})
            return True
        except Exception:
            return False

    def worker(idx):
        nonlocal pool, use_override
        # 观测累积全部线程本地（每 worker 独立 dict/list，收尾写 per_tab 自己的槽位，
        # 运行期无共享写免锁），join 后 render_page 单线程合并。
        my_paths = _empty_path_counts()
        my_times = {c: [] for c in _TIME_CATS}
        if png_fast:
            per_tab[idx] = (my_paths, my_times)
        gen = [browser.generation]

        def heal():
            """浏览器/连接级自愈：重启浏览器并重建 tab 池（generation 防多线程
            重复重启：自己那代已被别人换新则直接换用共享新池）。"""
            nonlocal pool, use_override
            try:
                new_pool = None
                with browser.lock:
                    if browser.generation == gen[0]:
                        browser.hard_restart()
                        new_pool = browser.open_tabs()
                        # override 重应用须在锁内、池发布前完成（2026-09-11 修复）：
                        # 重启后的新池若不带 override，透明页会静默渲成白底不透明帧。
                        # 锁内 CDP 调用有 Tab.call 硬超时兜底。
                        if new_pool is not None and use_override and not apply_override(new_pool):
                            use_override = False  # 重应用失败 → 本页回落双截
                if new_pool is not None:
                    pool = new_pool
                    gen[0] = browser.generation
            except FastPNGError:
                raise
            except Exception:
                time.sleep(0.5)

        def shot_with_heal(i, navfree=False):
            """渲一帧并落盘（含自愈重试 ≤2）。B5 语义：路径计数移到 write 成功
            之后，write 失败重试不再重复计数。navfree=True 且页内 __vtx_render
            抛错时本帧自动转回导航路径（不耗重试预算，页级一次性告警）。"""
            nf = navfree
            retries = 0
            while True:
                try:
                    if idx >= len(pool):
                        raise ConnectionError("tab lost after browser restart")
                    png, path, timing = do_frame(pool[idx], i, navfree=nf)
                    t0 = time.monotonic()
                    _atomic_write_bytes(os.path.join(outdir, f"f{i:04d}.png"), png)
                    my_times["write"].append(time.monotonic() - t0)
                    for c in ("nav", "export", "decode"):
                        my_times[c].append(timing[c])
                    if canvas_export and path != "canvas":
                        my_paths["canvas_fallback"] += 1  # canvas 校验失败回落：实际路径仍照常计
                    my_paths[path] += 1
                    gen[0] = browser.generation
                    return True
                except FastPNGError:
                    raise
                except Exception:
                    if nf:
                        # navfree 失败 → 本帧转导航路径；绝不静默（页级告警一次）
                        nf = False
                        if not warned[0]:
                            warned[0] = True
                            note("页内 __vtx_render 执行失败，回落逐帧导航路径"
                                 "（本页首次告警）")
                        continue
                    retries += 1
                    if retries > 2:
                        failed.append(i)
                        my_paths["failed"] += 1  # 失败帧只计数：耗时样本只留成功帧，避免重试噪声
                        note(f"帧 {i} 重试 2 次仍失败，交由调用方回落")
                        return False
                    heal()

        # —— navfree 每 tab 一次性导航（特性 1）：之后帧态全由页内 __vtx_render 置位 ——
        nf_tab = navfree_on
        if nf_tab and queues[idx]:
            try:
                if idx >= len(pool):
                    raise ConnectionError("tab lost after browser restart")
                t0 = time.monotonic()
                pool[idx].navigate(f"{base_uri}?f={queues[idx][0]}")
                my_times["nav"].append(time.monotonic() - t0)
            except Exception:
                nf_tab = False    # 首导航失败 → 本 tab 逐帧导航（各帧自会重试/自愈）
        for i in queues[idx]:
            shot_with_heal(i, navfree=nf_tab)
        # 收尾：写本 worker 的观测槽位（每槽只被所属线程写一次，无竞争）
        per_tab[idx] = (my_paths, my_times)

    def fast_worker(idx):
        try:
            worker(idx)
        except BaseException as e:
            worker_errors[idx] = e

    render_error = None
    try:
        pool = browser.open_tabs()
        pool[0].navigate(f"{base_uri}?f={work[0]}")
        time.sleep(0.15)
        # Canvas 导出通道（PR#1）先于透明检测：opt-in 页直出画布 PNG（自带精确 alpha），
        # 截图路径整体旁路——override/flatten 守卫/两截解算都不需要；导出失败帧在
        # do_frame 内回落白黑双截（transparent=True 保证回落路径走两截，保 alpha）。
        canvas_export = bool(pool[0].eval("typeof window.__vtx_export_png === 'function'"))
        # —— 特性 2 静态跨度去重：导航后读声明并缩减渲染集。必须在 worker 启动前
        # 完成（缺失头帧要并入 work 并重建队列）；声明是惰性数据，读取/解析失败
        # 只影响缩减不影响像素。复制在规划后立即做（头帧已缓存的成员）。——
        raw_spans = None
        if os.environ.get("VTX_GFX_STATIC", "1") != "0":
            try:
                raw_spans = pool[0].eval("window.__vtx_static_spans || null")
            except Exception:
                raw_spans = None
        spans = _normalize_spans(raw_spans, n_frames)
        if spans is None:
            note("__vtx_static_spans 声明解析失败，按无声明原路径渲染")
            spans = []
        copy_now = []
        if spans:
            work, copy_now, deferred_spans, static_spans_n = plan_static_spans(
                spans, work, outdir, width=width, height=height)
        for m, head_path in copy_now:
            # 头帧已缓存：字节复制（B5 口径：写成功才计数）；失败则交回渲染集。
            # 读头帧直读、写成员走 _atomic_write_bytes（并发双写下成员文件同样不撕裂）
            try:
                with open(head_path, "rb") as fh:
                    data = fh.read()
                _atomic_write_bytes(os.path.join(outdir, f"f{m:04d}.png"), data)
                static_copied_n += 1
            except OSError as e:
                note(f"帧 {m} 复制头帧失败（{e}），改渲染")
                work.append(m)
                work.sort()
        queues = [work[k::tabs_n] for k in range(tabs_n)]
        # —— 特性 1 nav-free 资格判定：VTX_GFX_NAVFREE=1 且页面有 __vtx_render。
        # 默认 0：env 关闭时零额外 eval，页面行为与旧路径逐字节一致。——
        if os.environ.get("VTX_GFX_NAVFREE", "0") == "1":
            try:
                navfree_on = bool(pool[0].eval(
                    "typeof window.__vtx_render === 'function'"))
            except Exception:
                navfree_on = False
            if navfree_on:
                note("navfree 已启用：每 tab 仅导航一次、帧态由页内 __vtx_render 置位"
                     "（像素一致性以 golden 全帧比对验收）")
        transparent = None
        use_override = False
        if not canvas_export and work:
            # 跨度缩减后 work 可能为空（缺失帧全是已缓存头帧的成员）→ 跳过透明检测
            use_override = apply_override(pool)  # 透明快路径开关（2026-09-11）
            if use_override:
                # flatten 守卫（2026-09-11）：body/html 级 opacity 的页（标题/端板类淡入
                # 淡出）会使 Chrome 合成组拍平，override 截图静默变全不透明白底（g6a_title
                # 实案）。双探针帧（首帧+中帧）截图若 alpha 全 255 且注入式探针判定页面
                # 为透明 → 判定 flatten，本页回落白黑双截。
                import numpy as np
                probes = [work[0], work[len(work) // 2]]
                flat = True
                for pf in probes:
                    pool[0].navigate(f"{base_uri}?f={pf}")
                    arr = png_decode_rgba(pool[0].shot())
                    if not (arr[:, :, 3] == 255).all():
                        flat = False
                        break
                if flat and page_transparent(pool[0]):
                    use_override = False
                    pool[0].call("Emulation.setDefaultBackgroundColorOverride")
                elif not flat:
                    transparent = True
            if not use_override:
                transparent = page_transparent(pool[0])
        else:
            transparent = True
        if work:
            # 缩减后 work 可能清空（缺失帧全是已缓存头帧的跨度成员）→ 零线程收尾
            threads = [threading.Thread(target=fast_worker if png_fast else worker,
                                        args=(k,)) for k in range(tabs_n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            for error in worker_errors:
                if error is not None:
                    raise FastPNGError("fast PNG worker failed; fallback forbidden") from error
    except BaseException as e:
        render_error = e
        raise
    finally:
        if png_fast:
            _close_fast_browser(browser, pool, render_error,
                                reused_frames=reused_frames,
                                static_copied_frames=static_copied_n,
                                canvas_frames=sum(slot[0]["canvas"] for slot in per_tab
                                                  if slot is not None))
        else:
            for t in pool:
                t.close()   # 显式释放 tab CDP fd(对端将随 stop() 退出,无害)
            browser.stop()
    dt = (time.monotonic() - t0) * 1000
    # —— 特性 2 延后复制：头帧已由本次渲染落盘，缺失成员字节复制头帧文件 ——
    for head, members in deferred_spans:
        head_path = os.path.join(outdir, f"f{head:04d}.png")
        try:
            reason = png_invalid_reason(head_path, width=width, height=height)
            if reason:
                raise OSError(reason)
            with open(head_path, "rb") as fh:
                data = fh.read()
        except OSError as e:
            # 头帧渲染失败/不可读：成员帧交回 failed 由调用方 CLI 补渲，绝不静默丢帧
            note(f"跨度头帧 f{head:04d}.png 不可读（{e}），{len(members)} 个成员帧交由调用方回落")
            for m in members:
                if not os.path.exists(os.path.join(outdir, f"f{m:04d}.png")):
                    failed.append(m)
            continue
        for m in members:
            try:
                _atomic_write_bytes(os.path.join(outdir, f"f{m:04d}.png"), data)
                static_copied_n += 1
            except OSError as e:
                note(f"帧 {m} 复制头帧失败（{e}），交由调用方回落")
                failed.append(m)
    # rendered 口径（m2 修正）：= work 帧渲染成功数，按 work 归属过滤后再扣减。
    # failed 里可能混有 deferred 复制失败的成员帧（成员被跨度认领后不在 work 内），
    # 直接 len(work)-len(failed) 会失真甚至为负；static_copied 字节复制帧按既有
    # 文档口径不计入 rendered（单独计入 path_counts["static_copied"]）。
    failed.extend(i for i, _ in verify_frames(outdir, requested, width=width, height=height))
    failed = sorted(set(failed))
    work_set = set(work)
    rendered_n = len(work) - sum(1 for f in failed if f in work_set)
    # 合并各 tab 线程本地的路径计数与分项耗时（槽位由 worker 收尾写入；None 兜底
    # 防 worker 异常退出缺槽）
    path_counts = _empty_path_counts()
    samples = {c: [] for c in _TIME_CATS}
    for slot in per_tab:
        pc_t, ts_t = slot if slot else (_empty_path_counts(), {c: [] for c in _TIME_CATS})
        for k, v in pc_t.items():
            path_counts[k] += v
        for c, vals in ts_t.items():
            samples[c].extend(vals)
    path_counts["static_copied"] += static_copied_n
    capture = browser.png_capture() if png_fast else None
    if capture is not None:
        capture.update(reused_frames=reused_frames, canvas_frames=path_counts["canvas"],
                       static_copied_frames=static_copied_n)
    return {"n": n_frames, "rendered": rendered_n, "failed": sorted(set(failed)),
            "transparent": transparent, "tabs": tabs_n, "ms_total": dt,
            "ms_per_frame": dt / max(1, len(work)),
            "canvas_export": canvas_export,
            "static_spans": static_spans_n,
            "path_counts": path_counts,
            "timings_ms": {c: _timings_summary(samples[c]) for c in _TIME_CATS},
            **({"png_capture": capture} if png_fast else {})}


def available():
    return _HAS_NUMPY


# ---------------------------------------------------------------- CLI 自测入口
if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(2)
    html, outdir, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    tabs = int(sys.argv[sys.argv.index("--tabs") + 1]) if "--tabs" in sys.argv else None
    t0 = time.monotonic()
    stats = render_page(html, outdir, n, tabs=tabs)
    print(json.dumps({k: v for k, v in stats.items()}, ensure_ascii=False, indent=1))
    print(f"wall: {(time.monotonic() - t0) * 1000:.0f}ms")


def render_strict_page(html_path, outdir, n_frames, chrome_path, width, height,
                       frames, ctx, record, *, png_fast=None):
    """Conservative static-only route, no retry, JS export or CLI downgrade.

    Every invocation uses a fresh enforced target. No partial-frame reuse or
    inherited completion certificate is accepted by this route.
    """
    from .gfx_dependencies import RequestPolicy, DependencyError
    from .gfx_backend import png_fast_requested
    if png_fast is None:
        png_fast = png_fast_requested()
    if record.get('policy') != 'strict' or record.get('closure') != 'static-subset-v1':
        raise DependencyError('strict static contract required')
    browser = Browser(chrome_path, 1, width, height, ctx=ctx,
                      **({"png_fast": True} if png_fast else {}))
    pool = []
    render_error = None
    started = time.monotonic()
    requested = list(range(n_frames) if frames is None else frames)
    try:
        browser.start()
        pool = browser.open_tabs()
        tab = pool[0]
        tab.request_policy = RequestPolicy(record)
        tab.call('Emulation.setScriptExecutionDisabled', value=True)
        tab.call('Network.enable')
        tab.call('Network.setCacheDisabled', cacheDisabled=True)
        tab.call('Fetch.enable', patterns=[{'urlPattern': '*', 'requestStage': 'Request'}])
        tab.call('Emulation.setDefaultBackgroundColorOverride',
                 color={'r': 0, 'g': 0, 'b': 0, 'a': 0})
        for frame in requested:
            tab.request_policy.observed.clear()
            tab.navigate(record['html_uri'] + '?f=' + str(frame))
            ready = tab.call('Runtime.evaluate', expression='document.fonts.ready.then(()=>true)',
                             awaitPromise=True, returnByValue=True)
            if ready.get('exceptionDetails') or ready.get('result', {}).get('value') is not True:
                raise DependencyError('font readiness unproven')
            # Coverage must be observed, not inferred from Fetch.enable success.
            tab.request_policy.check(require_complete=True)
            png = tab.shot()
            tab.call('Runtime.evaluate', expression='true', returnByValue=True)
            tab.request_policy.check(require_complete=True)
            _atomic_write_bytes(str(Path(outdir) / f'f{frame:04d}.png'), png)
        counts = _empty_path_counts()
        counts['override'] = len(requested)
        elapsed = (time.monotonic() - started) * 1000
        return {'n': n_frames, 'rendered': len(requested), 'failed': [],
                'transparent': True, 'tabs': 1, 'ms_total': elapsed,
                'ms_per_frame': elapsed / max(1, len(requested)),
                'canvas_export': False, 'path_counts': counts, 'static_spans': 0,
                'timings_ms': {c: _timings_summary([]) for c in _TIME_CATS},
                'dependency_enforcement': 'fetch-pinned-local-v1',
                'dependency_observed': sorted(tab.request_policy.observed),
                **({'png_capture': browser.png_capture()} if png_fast else {})}
    except BaseException as e:
        render_error = e
        raise
    finally:
        if png_fast:
            _close_fast_browser(browser, pool, render_error)
        else:
            for tab in pool:
                tab.close()
            browser.stop()
