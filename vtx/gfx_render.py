#!/usr/bin/env python3
"""gfx_render.py — CDP 常驻浏览器 gfx 帧渲染器（vtx gfx 阶段加速后端）

替代「每帧冷启动一个 chrome --headless --screenshot 进程」的 CLI 截图路径：
单个常驻 Chrome + N 个 tab 池（默认 4，env VTX_GFX_TABS），每 tab 一条
WebSocket CDP 连接，Page.navigate → 等 document.readyState → captureScreenshot。

alpha 处理（本机 Chrome 152 实测，/tmp/gfx_cdp_dev/REPORT.md 实验1）：
  captureScreenshot 无论 omitBackground:true / fromSurface:false / 浏览器级
  --default-background-color=00000000，返回的都是无 alpha 的 RGB PNG
  （IHDR color_type=2）。
  ⇒ 透明页走「两次截图法」：同帧分别注入纯白/纯黑背景各截一张，DOM 合成
  满足 C = a·F + (1−a)·B：
      a = 1 − (Cw − Cb)/255      （合成 alpha，即要存的 alpha 通道）
      F = Cb / a                  （非预乘前景色；a=0 处 F=0）
  数学无损；不透明页（html/body computed background 不透明）单截直写 Chrome
  原生 PNG，零重编码。

纯标准库（socket/struct/base64/json/zlib）+ numpy（仅两截 alpha 解算与 PNG
编解码；无 numpy 时返回不可用，调用方全量回落 CLI）。

独立测试：python3 gfx_render.py <html> <outdir> <N帧> [--tabs 4] [--opaque-check]
"""
import base64
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

try:
    import numpy  # noqa: F401
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

PNG_COMPRESS_LEVEL = int(os.environ.get("VTX_GFX_PNG_LEVEL", "3"))


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

class WSConn:
    def __init__(self, port, path, timeout=20):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.sock.settimeout(timeout)
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
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"ws handshake rejected: {buf[:120]!r}")

    def send_text(self, text):
        data = text.encode()
        hdr = bytearray([0x81])
        n = len(data)
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
        self.sock.sendall(bytes(hdr) + bytes(b ^ mask[i & 3] for i, b in enumerate(data)))

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("ws closed")
            buf += chunk
        return buf

    def recv_msg(self):
        hdr = self._recv_exact(2)
        op = hdr[0] & 0x0F
        ln = hdr[1] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._recv_exact(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._recv_exact(8))[0]
        if hdr[1] & 0x80:  # 服务器帧按协议不掩码，防御性处理
            mask = self._recv_exact(4)
            return op, bytes(b ^ mask[i & 3] for i, b in enumerate(self._recv_exact(ln)))
        return op, self._recv_exact(ln)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Tab:
    """一个 page target 的 CDP 会话（线程独占）。"""

    def __init__(self, port, ws_path, width=1920, height=1080):
        self.ws = WSConn(port, ws_path)
        self._mid = 0
        self.call("Page.enable")
        self.call("Emulation.setDeviceMetricsOverride",
                  width=width, height=height, deviceScaleFactor=1, mobile=False)

    def call(self, method, timeout=30, **params):
        self._mid += 1
        mid = self._mid
        self.ws.send_text(json.dumps({"id": mid, "method": method, "params": params}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            op, data = self.ws.recv_msg()
            if op == 0x8:
                raise ConnectionError("ws close frame")
            msg = json.loads(data)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            # 其它 id / 事件通知：忽略（每 tab 单线程使用，正常不会有别的 id）
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
        r = self.call("Page.captureScreenshot", format="png", timeout=30)
        return base64.b64decode(r["data"])


class Browser:
    """惰性启动的常驻 headless Chrome。崩溃后 hard_restart() 重建（线程安全）。"""

    def __init__(self, chrome_path=None, tabs=4, width=1920, height=1080):
        self.chrome = chrome_path
        self.tabs_n = max(1, tabs)
        self.width, self.height = width, height
        self.proc = None
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
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.user_data_dir = tempfile.mkdtemp(prefix="vtx_gfx_cdp_")
        self.proc = subprocess.Popen(
            [self.chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
             f"--remote-debugging-port={self.port}",
             f"--user-data-dir={self.user_data_dir}",
             "--no-first-run", "--no-default-browser-check", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")
        self.tab_ws_urls = []
        for _ in range(self.tabs_n):
            info = json.load(urllib.request.urlopen(req, timeout=5))
            url = info["webSocketDebuggerUrl"]
            self.tab_ws_urls.append("/" + url.split("/", 3)[3])

    def open_tabs(self):
        return [Tab(self.port, u, self.width, self.height) for u in self.tab_ws_urls]

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.user_data_dir:
            shutil.rmtree(self.user_data_dir, ignore_errors=True)

    def hard_restart(self):
        """杀进程重起 + 重建 tab 池 URL（连接方需重开 Tab）。"""
        self.stop()
        self.start()
        self.generation += 1


# ---------------------------------------------------------------- 渲染主体

_BG_INJECT_WHITE = (
    "(()=>{let s=document.getElementById('__vtx_bg');if(!s){s=document.createElement('style');"
    "s.id='__vtx_bg';document.documentElement.appendChild(s);}"
    "s.textContent=`html,body{background:#fff !important}`;'ok'})()")
_BG_INJECT_BLACK = (
    "(()=>{const s=document.getElementById('__vtx_bg');"
    "s.textContent=`html,body{background:#000 !important}`;'ok'})()")


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


def render_page(html_path, outdir, n_frames, chrome_path=None, tabs=None,
                width=1920, height=1080, progress=None):
    """渲一页 → outdir/f{:04d}.png × n（已存在的帧跳过）。

    返回统计 dict；帧级失败（含浏览器崩溃自愈重试 2 次）列入 failed，
    调用方对这些帧回落 CLI。浏览器起不来抛 GfxRenderError（全页回落 CLI）。
    """
    if not _HAS_NUMPY:
        raise GfxRenderError("numpy unavailable")
    tabs_n = tabs or int(os.environ.get("VTX_GFX_TABS", "4"))
    html_path = os.path.abspath(html_path)
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    browser = Browser(chrome_path, tabs_n, width, height)
    browser.start()
    pool = []
    t0 = time.monotonic()
    failed = []
    transparent = None
    canvas_export = False
    work = [i for i in range(n_frames)
            if not os.path.exists(os.path.join(outdir, f"f{i:04d}.png"))]
    if not work:
        browser.stop()
        return {"n": n_frames, "rendered": 0, "failed": [], "transparent": None,
                "tabs": tabs_n, "ms_total": 0.0, "ms_per_frame": 0.0,
                "canvas_export": False}
    queues = [work[k::tabs_n] for k in range(tabs_n)]

    def do_frame(tab, i):
        # PR#1 合入：Path.as_uri() 生成 file URL——Windows 盘符保留、空格/Unicode/#/
        # ? 等 URL 保留字全部百分号编码（旧 f"file://{path}" 在含 # 的目录下截断成
        # fragment，Linux 同样中招；无特殊字符的绝对路径两形式逐字符一致）。
        tab.navigate(f"{Path(html_path).resolve().as_uri()}?f={i}")
        if canvas_export:
            # Canvas 导出通道（PR#1）：页面可选实现 window.__vtx_export_png() 同步返回
            # 画布 dataURL——校验 PNG 信封（分块边界/CRC/IHDR 尺寸色彩型/IEND）后直用，
            # 失效载荷绝不写入可续渲缓存，回落同页截图路径（见下，两截保 alpha）。
            try:
                return decode_canvas_export(tab.eval("window.__vtx_export_png()"), width, height)
            except (ValueError, TypeError, GfxRenderError):
                pass
        if use_override:
            # 透明快路径（2026-09-11）：Emulation.setDefaultBackgroundColorOverride(a=0)
            # 使 captureScreenshot 直出带 alpha 的 RGBA PNG——单截直写，与白黑双截金标准
            # 逐像素一致（b1_bricks/g6b_endboard/nav_two_pillars 实测 max|Δ|=0），且
            # override 跨导航持久（每 tab 设一次）。替代注入式双截（省一次整页光栅+
            # 两次解码+一次重编码）。注意：override 干不过页面显式 CSS 背景，gfx 页协议
            # 要求 body 透明故无冲突；直写 Chrome 原生 PNG，Python 侧零编解码。
            return tab.shot()
        if not transparent:
            return tab.shot()
        tab.eval(_BG_INJECT_WHITE)
        w_png = tab.shot()
        tab.eval(_BG_INJECT_BLACK)
        b_png = tab.shot()
        return _two_shot_combine(w_png, b_png)

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
        qi, retries = 0, 0
        my_gen = browser.generation
        while qi < len(queues[idx]):
            i = queues[idx][qi]
            try:
                if idx >= len(pool):
                    raise ConnectionError("tab lost after browser restart")
                png = do_frame(pool[idx], i)
                with open(os.path.join(outdir, f"f{i:04d}.png"), "wb") as fh:
                    fh.write(png)
                qi += 1
                retries = 0
                my_gen = browser.generation
            except Exception:
                retries += 1
                if retries > 2:
                    failed.append(i)
                    qi += 1
                    retries = 0
                    if progress:
                        progress(f"⚠ gfx_render: 帧 {i} 重试 2 次仍失败，交由调用方回落")
                    continue
                # 浏览器/连接级自愈：重启浏览器并重建 tab 池
                # （generation 防多线程重复重启：自己那代已被别人换新则直接换用新池）
                try:
                    new_pool = None
                    with browser.lock:
                        if browser.generation == my_gen:
                            browser.hard_restart()
                            new_pool = browser.open_tabs()
                            # override 重应用须在锁内、池发布前完成（2026-09-11 修复）：
                            # 此前 worker 内对 use_override 赋值使其沦为局部变量，读取即
                            # UnboundLocalError 且被下方 except 吞掉——重启后的新池永远
                            # 不带 override，透明页静默渲成白底不透明帧并被续渲缓存信任；
                            # 且池发布与 override 应用之间存竞态窗口，其它 worker 会抢用
                            # 无 override 的新池。锁内 CDP 调用有 Tab.call 硬超时兜底。
                            if new_pool is not None and use_override and not apply_override(new_pool):
                                use_override = False  # 重应用失败 → 本页回落双截
                    if new_pool is not None:
                        pool = new_pool
                except Exception:
                    time.sleep(0.5)

    try:
        pool = browser.open_tabs()
        pool[0].navigate(f"{Path(html_path).resolve().as_uri()}?f={work[0]}")
        time.sleep(0.15)
        # Canvas 导出通道（PR#1）先于透明检测：opt-in 页直出画布 PNG（自带精确 alpha），
        # 截图路径整体旁路——override/flatten 守卫/两截解算都不需要；导出失败帧在
        # do_frame 内回落白黑双截（transparent=True 保证回落路径走两截，保 alpha）。
        canvas_export = bool(pool[0].eval("typeof window.__vtx_export_png === 'function'"))
        transparent = None
        use_override = False
        if not canvas_export:
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
                    pool[0].navigate(f"{Path(html_path).resolve().as_uri()}?f={pf}")
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
        threads = [threading.Thread(target=worker, args=(k,)) for k in range(tabs_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        browser.stop()
    dt = (time.monotonic() - t0) * 1000
    return {"n": n_frames, "rendered": len(work) - len(failed), "failed": sorted(failed),
            "transparent": transparent, "tabs": tabs_n, "ms_total": dt,
            "ms_per_frame": dt / max(1, len(work)),
            "canvas_export": canvas_export}


class GfxRenderError(RuntimeError):
    pass


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
