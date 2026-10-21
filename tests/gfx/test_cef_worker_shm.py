"""issue #110（CEF-02a）：OnPaint 4-Slot SHM 环形池与定长二进制 IPC 协议。

门控：VTX_TEST_CEF_WORKER=<vtx_cef_worker 可执行路径>（先
`bash experiments/gfx_cef_worker/build_cef_worker.sh`；shm_ring_selftest
须在同目录）。未设置即跳过 worker 门；NativeAttachOwnership 仅以假系统
调用验证实际 C++ header 的所有权，不需要 CEF 或真实 SHM。

验收面（工单 DoD 逐条 + 端到端门）：
  1. 原生自检：shm_ring_selftest 全 PASS（槽位状态机/满溢背压/读写同步/
     步长紧凑/陈旧 run_id/SIMD 逆预乘穷举/金丝雀）；--dump-ring 跨语言
     锁定 Python 解析器偏移表；
  2. 定长协议字段对齐/版本/序号/校验：dump-ring 解析 + Hello/回执/通知
     magic·版本·run_id·request_id·seq 全链校验；
  3. 端到端（A0 软件路径）：CEF OnPaint → SHM 槽 → 消费端读出 ==
     OnPaint 直写路径逐字节一致（.raw 直写对照 + #109 一次性 PNG 解码
     对照双证据）；模式 1 落盘回执计时完备；
  4. 故障注入 DoD：① width=1920,height=1079 尺寸不符 → SIZE_MISMATCH
     明确错误码；② 消费端阻塞 5s → 4 槽环满背压（暂停+恢复+计时入账+
     RSS 增量 ≤64MB 预算）；③ Worker 中途 abort() → 消费端明确 EOF 错误
     而非死等 + /dev/shm 零 vtx_shm_* 残留；
  5. 监管 kill -9 宿主：worker 树 ~105ms 归零 + SHM/临时目录零残留
     （复用 #103 SupervisedProcessGroup + reap）。

安全铁律：一切被杀对象均为本测试自起子进程（worker 独立会话=组长；
宿主=测试直接子进程）；kill 均按 PID/账本核验组进行。

独立运行：VTX_TEST_CEF_WORKER=<path> python3 -m unittest
tests.gfx.test_cef_worker_shm -v
"""
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "gfx_cef_worker"))

import cef_client  # noqa: E402  (experiments 面；工单 vtx/ 落点由边界改道)

from vtx.gfx_cli import png_invalid_reason  # noqa: E402
from tests.gfx._png import unfilter_rgba
from vtx.infra import Ctx  # noqa: E402
from vtx.proc_supervisor import (  # noqa: E402
    SupervisedProcessGroup, group_live_members, reap_orphan_workers)

_LINUX_PROC = Path("/proc/self/stat").is_file()
_WORKER = os.environ.get("VTX_TEST_CEF_WORKER", "")
_GATE = _LINUX_PROC and _WORKER and Path(_WORKER).is_file()
_BIN_DIR = Path(_WORKER).resolve().parent if _GATE else None
_SELFTEST = _BIN_DIR / "shm_ring_selftest" if _BIN_DIR else None
_CANARY_URL = (ROOT / "experiments/gfx_cef_worker/pages/frame_canary.html").as_uri()
_EVIDENCE = Path(os.environ.get("VTX_CEF_EVIDENCE_DIR", "/tmp/framivra-evidence/issue-110"))


def _wait_until(pred, timeout=30.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value:
            return value
        if time.monotonic() >= deadline:
            return pred()
        time.sleep(interval)


def _shm_residue():
    return sorted(p.name for p in Path("/dev/shm").glob("vtx_shm_*"))


def _vm_rss_kb(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        pass
    return -1


def decode_png_rgba(path):
    """纯解码本仓 png_writer 产物（#111 起默认 spng 后端行滤波 SUB/UP，
    故按 PNG 全滤波类型重建；zlib/fpng 后端 filter-0 行为子集），返回
    (w, h, rgba bytes)。"""
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w, h, bpc, color = 8, b"", 0, 0, 8, 6
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            w, h, bpc, color = struct.unpack(">IIBB", body[:10])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    assert bpc == 8 and color == 6, "8-bit RGBA 假设"
    stride = w * 4 + 1
    assert len(raw) == stride * h, "IDAT 总量与 IHDR 尺寸不符"
    return w, h, unfilter_rgba(w, h, raw)


def rgba_to_bgra(rgba):
    b = bytearray(rgba)
    b[0::4], b[2::4] = rgba[2::4], rgba[0::4]
    return bytes(b)


# 独立监管宿主：SupervisedProcessGroup 起 serve worker → 客户端取一帧并
# 占住槽 → 就绪标记。测试对宿主 SIGKILL 即 DoD「宿主暴毙（渲染服务中）」。
_HOST_SUPERVISED = r'''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[5])
import cef_client
from vtx.infra import Ctx
from vtx.proc_supervisor import SupervisedProcessGroup

root, ready_path, worker, url_tpl, exp_dir = (Path(sys.argv[1]), Path(sys.argv[2]),
                                              sys.argv[3], sys.argv[4], sys.argv[5])
c = Ctx(root / "timeline.json")


def worker_argv(run_dir, profile):
    return [worker, "--mode=a0", "--serve",
            f"--ipc-socket={run_dir}/ipc.sock",
            f"--url-template={url_tpl}",
            f"--root-cache-path={run_dir}"]


with SupervisedProcessGroup(c, worker_argv, backend_id="cef_serve") as sg:
    sock = sg.run_dir / "ipc.sock"
    deadline = time.monotonic() + 60
    while not sock.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    with cef_client.CefWorkerClient.connect(sock) as client:
        frame = client.request_slot(3)   # 取帧并占住槽（消费端在场证明）
        ready_path.write_text(json.dumps({
            "run_dir": str(sg.run_dir), "pgid": sg.pgid,
            "host_pid": os.getpid(), "worker_pid": sg.proc.pid,
            "held_slot": frame.slot_index,
            "header_w": frame.header["width"],
            "header_h": frame.header["height"]}), encoding="utf-8")
        time.sleep(600)                    # 保持消费端占槽直到被杀
'''


class _ServeFixture(unittest.TestCase):
    """serve worker 生命周期夹具：自起子进程 + 严格回收（安全铁律）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vtx110_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sock = self.root / "ipc.sock"   # 短路径（64B 请求面/108 路径限）
        self.cache = self.root / "cache"
        self.proc = None
        self.threads = []
        self.addCleanup(self._teardown_worker)

    def _teardown_worker(self):
        proc = getattr(self, "proc", None)
        # 首选父存活守卫 EOF（worker 组长 → 组杀全树，覆盖 CEF 子进程）
        w_fd = getattr(self, "_parent_w", None)
        if w_fd is not None:
            self._parent_w = None
            try:
                os.close(w_fd)
            except OSError:
                pass
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                os.kill(proc.pid, signal.SIGKILL)   # 测试自起子进程
                try:
                    proc.wait(15)
                except subprocess.TimeoutExpired:
                    pass
        for t in self.threads:
            t.join(20)
        if proc is not None:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        self.assertEqual(_shm_residue(), [], "/dev/shm 零 vtx_shm_* 残留")

    def spawn_worker(self, *extra, mode="a0", timeout_ms=45000):
        argv = [_WORKER, f"--mode={mode}", "--serve",
                f"--ipc-socket={self.sock}",
                f"--url-template={_CANARY_URL}?f={{f}}",
                f"--root-cache-path={self.cache}",
                f"--timeout-ms={timeout_ms}", *extra]
        r_fd, w_fd = os.pipe()
        argv.append(f"--parent-alive-fd={r_fd}")
        self.proc = subprocess.Popen(
            argv, start_new_session=True, pass_fds=(r_fd,),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        os.close(r_fd)
        self._parent_w = w_fd
        self.pgid = self.proc.pid  # start_new_session：pgid == pid（wait 前记录）
        # 就绪行监听（stdout 只产少量行；stderr 需排空防写端阻塞）
        self.ready = {}
        ev = threading.Event()

        def drain(stream, key, is_ready):
            for line in stream:
                if is_ready and line.startswith("VTXCEF_SERVE_READY"):
                    parts = dict(kv.split("=", 1) for kv in line.split()[1:])
                    self.ready.update({k: v for k, v in parts.items() if k != "socket"})
                    ev.set()

        t1 = threading.Thread(target=drain, args=(self.proc.stdout, "out", True),
                              daemon=True)
        t2 = threading.Thread(target=drain, args=(self.proc.stderr, "err", False),
                              daemon=True)
        t1.start(); t2.start()
        self.threads += [t1, t2]
        self.assertTrue(ev.wait(60), f"worker 60s 未就绪（ready={self.ready}）")
        return self.ready

    def client(self, timeout=30.0):
        return cef_client.CefWorkerClient.connect(self.sock,
                                                  connect_timeout=timeout,
                                                  ready_timeout=timeout)


@unittest.skipUnless(sys.platform.startswith("linux"), "requires GNU linker --wrap")
class NativeAttachOwnership(unittest.TestCase):
    """Actual C++ header with fake syscalls only: no CEF, SHM, mmap or real fd."""

    def test_all_attach_paths_release_only_owned_resources_once(self):
        cxx = shutil.which("c++")
        if not cxx:
            self.skipTest("requires a C++ compiler")
        source = r'''
#include "shm_ring.hpp"
#include <cstdlib>
#include <iostream>
#include <new>
using namespace vtx_cef;
static std::string scenario;
static int unmaps = 0, closes = 0, maps = 0, ticks = 0;
alignas(64) static unsigned char bytes[2048];
static const uint64_t total = RingGeometry::ForSize(1, 1).total_bytes;
static void check(bool ok) { if (!ok) std::exit(1); }
extern "C" int __wrap_dup(int fd) {
  check(fd == 41); return scenario == "dup" ? -1 : 42;
}
extern "C" int __wrap_fstat(int fd, struct stat* st) {
  check(fd == 42); st->st_size = scenario == "short" ? 1 : total;
  return scenario == "stat" ? -1 : 0;
}
extern "C" void* __wrap_mmap(void*, size_t n, int prot, int flags, int fd, off_t off) {
  check(fd == 42 && n == total && prot == (PROT_READ | PROT_WRITE) && flags == MAP_SHARED && off == 0);
  ++maps; return scenario == "map" ? MAP_FAILED : bytes;
}
extern "C" int __wrap_munmap(void* p, size_t n) {
  check(p == bytes && n == total); ++unmaps; return 0;
}
extern "C" int __wrap_close(int fd) { check(fd == 42); ++closes; return 0; }
extern "C" int __wrap_clock_gettime(clockid_t, struct timespec* ts) {
  ts->tv_sec = 6 * ++ticks; ts->tv_nsec = 0; return 0;
}
extern "C" int __wrap_usleep(useconds_t) { return 0; }
int main(int argc, char** argv) {
  check(argc == 2); scenario = argv[1];
  auto* control = new (bytes) RingControl{};
  control->layout_ready.store(scenario == "timeout" ? 0 : kRingMagic);
  control->magic = scenario == "magic" ? 0 : kRingMagic;
  control->protocol_version = scenario == "version" ? 0 : kProtocolVersion;
  control->max_width = scenario == "geometry" ? 100 : 1;
  control->max_height = 1;
  std::string error;
  auto view = ShmRingView::Attach(41, &error);
  check(bool(view) == (scenario == "ok"));
  if (view) { check(unmaps == 0 && closes == 0); view.reset(); }
  else check(!error.empty());
  check(closes == (scenario == "dup" ? 0 : 1));
  const bool mapped = scenario == "ok" || scenario == "timeout" || scenario == "magic" || scenario == "version" || scenario == "geometry";
  check(maps == (mapped || scenario == "map" ? 1 : 0));
  std::cout << scenario << " unmaps=" << unmaps << " closes=" << closes << std::endl;
  check(unmaps == (mapped ? 1 : 0));
}
'''
        with tempfile.TemporaryDirectory(prefix="vtx_shm_attach_") as td:
            src, binary = Path(td) / "attach.cpp", Path(td) / "attach"
            src.write_text(source, encoding="utf-8")
            wrappers = ("dup", "fstat", "mmap", "munmap", "close", "clock_gettime", "usleep")
            built = subprocess.run(
                [cxx, "-std=c++17", "-pthread", "-I" + str(ROOT / "experiments/gfx_cef_worker/src"),
                 str(src), *["-Wl,--wrap=" + name for name in wrappers], "-o", str(binary)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            for case in ("dup", "stat", "short", "map", "timeout", "magic", "version", "geometry", "ok"):
                with self.subTest(case=case):
                    proc = subprocess.run([str(binary), case], capture_output=True, text=True, timeout=5)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


@unittest.skipUnless(_GATE and _SELFTEST and _SELFTEST.is_file(),
                     "需要 VTX_TEST_CEF_WORKER（含同目录 shm_ring_selftest）+ Linux /proc")
class NativeRingSelfTest(unittest.TestCase):
    """DoD 原生层：shm_ring_selftest 全 PASS + dump-ring 跨语言偏移锁定。"""

    def test_selftest_all_pass(self):
        proc = subprocess.run([str(_SELFTEST)], capture_output=True, text=True,
                              timeout=120)
        self.assertEqual(proc.returncode, 0,
                         f"自检失败:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("ALL PASS", proc.stdout)
        self.assertGreaterEqual(proc.stdout.count("PASS "), 20)

    def test_dump_ring_python_parser_lock(self):
        """C++ 写环 → Python 解析：偏移表/状态机/图案逐字段一致。"""
        with tempfile.TemporaryDirectory(prefix="vtx110_dump_") as td:
            dump = Path(td) / "ring.bin"
            proc = subprocess.run([str(_SELFTEST), f"--dump-ring={dump}"],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            blob = dump.read_bytes()
        self.assertEqual(len(blob), 384 + 4 * cef_client._align64(128 + 64 * 48 * 4))
        mm = blob
        # RingControl
        (layout_ready,) = struct.unpack_from("<I", mm, 0)
        (magic,) = struct.unpack_from("<I", mm, 4)
        (version,) = struct.unpack_from("<H", mm, 8)
        (slots,) = struct.unpack_from("<H", mm, 10)
        (run_id,) = struct.unpack_from("<Q", mm, 32)
        (published,) = struct.unpack_from("<Q", mm, 48)
        self.assertEqual((layout_ready, magic, version, slots),
                         (cef_client.RING_MAGIC, cef_client.RING_MAGIC,
                          cef_client.PROTOCOL_VERSION, 4))
        self.assertEqual(run_id, 0xCAFEF00D)
        self.assertEqual(published, 2)
        stride = cef_client._align64(128 + 64 * 48 * 4)
        for i, f in enumerate((0x2103, 0x0007)):
            off = 384 + stride * i
            values = struct.unpack_from(cef_client._HEADER_FMT, mm, off)
            header = dict(zip(cef_client._HEADER_FIELDS, values))
            self.assertEqual(header["magic"], cef_client.SLOT_MAGIC)
            self.assertEqual(header["protocol_version"], 1)
            self.assertEqual(header["status_code"], 0)
            self.assertEqual(header["run_id"], 0xCAFEF00D)
            self.assertEqual(header["target_frame_index"], f)
            self.assertEqual(header["canary_frame_index"], f)
            self.assertEqual((header["width"], header["height"], header["stride"]),
                             (64, 48, 64 * 4))
            self.assertEqual(header["request_id"], 100 + i)
            self.assertEqual(header["t_capture_raw_us"], 2000 * (i + 1))
            # 门：槽 0/1 READY，2/3 EMPTY
            (state,) = struct.unpack_from("<I", mm, 128 + i * 64)
            self.assertEqual(state, 2)
            # 数据面：对角图案 + 金丝雀像素 (0,0)
            data = mm[off + 128: off + 128 + 64 * 48 * 4]
            px = struct.unpack_from("<4B", data, 0)
            self.assertEqual(px, ((f >> 8) & 255, f & 255, 0x43, 255))
            for (x, y) in ((5, 7), (63, 47), (30, 20)):
                b, g, r, a = struct.unpack_from("<4B", data, (y * 64 + x) * 4)
                self.assertEqual((b, g, r, a), ((x ^ y) & 255, x & 255, y & 255, 255))
        for i in (2, 3):
            (state,) = struct.unpack_from("<I", mm, 128 + i * 64)
            self.assertEqual(state, 0)


@unittest.skipUnless(_GATE and _SELFTEST and _SELFTEST.is_file(),
                     "需要 VTX_TEST_CEF_WORKER（含同目录 shm_ring_selftest）+ Linux /proc")
class ServeEndToEndA0(_ServeFixture):
    """端到端（A0 软件路径）：模式 1/模式 2 + 逐字节一致 + 吞吐/延迟记录。"""

    def test_mode1_png_receipt_and_mode2_byte_exact(self):
        self.spawn_worker()
        with self.client() as client:
            # —— 模式 1：worker 原地落盘 + 128B 计时回执 ——
            png1 = self.root / "m1.png"
            rec = client.request_png(7, png1, request_id=11)
            self.assertEqual(rec.header["status_code"], 0)
            self.assertEqual(rec.header["canary_frame_index"], 7)
            self.assertEqual(rec.header["request_id"], 11)
            self.assertEqual((rec.header["width"], rec.header["height"]),
                             (1920, 1080))
            self.assertEqual(rec.header["stride"], 1920 * 4)
            for field in ("t_ready_wait_us", "t_capture_raw_us",
                          "t_alpha_norm_us", "t_png_encode_us",
                          "t_disk_commit_us"):
                self.assertGreater(rec.header[field], 0, f"{field} 应为正")
            self.assertLess(rec.header["rejected_paints"], 50)
            self.assertIsNone(png_invalid_reason(png1, width=1920, height=1080))

            # —— 模式 2：SHM 槽读出 == .raw 直写（同一 OnPaint 捕获字节）——
            dump = self.root / "m2"
            with client.request_slot(5, request_id=12, dump_raw_path=dump) as frame:
                raw = Path(str(dump) + ".raw").read_bytes()
                self.assertEqual(len(frame.data), 1920 * 1080 * 4)
                self.assertEqual(frame.data, raw, "槽读出 ≠ 直写路径（逐字节）")
                b, g, r, a = struct.unpack_from("<4B", frame.data, 0)
                self.assertEqual((r, g, b, a), (0x43, 5, 0, 255),
                                 "槽数据金丝雀像素 ≠ f=5")
                self.assertEqual(frame.header["canary_frame_index"], 5)
                self.assertEqual(frame.header["request_id"], 12)
                self.assertEqual(frame.header["pixel_format"], 1)  # BGRA_PREMUL
                distinct = set()
                for y in range(0, 1080, 97):        # 跨行采样（水平条纹页）
                    for x in range(0, 1920, 193):
                        off = (y * 1920 + x) * 4
                        distinct.add(frame.data[off:off + 3])
                # 硬边条纹页（两色带+横条+文字）合法色数即 ≥5；此为
                # 空白帧卫兵，非色域断言
                self.assertGreaterEqual(len(distinct), 5, "画面近乎单色（异常帧?）")
                self.assertGreaterEqual(frame.backpressure_wait_us, 0)
            stats = client.stats()
            self.assertGreaterEqual(stats["published"], 1)
            self.assertGreaterEqual(stats["released"], 1)

            # —— 独立证据：#109 一次性直写路径（OnPaint→buffer→PNG）解码
            #    BGRA 与槽字节逐一致 —— 两条独立传输路径互证 ——
            oneshot = self.root / "one5.png"
            argv = [_WORKER, "--mode=a0",
                    f"--url={_CANARY_URL}?f=5", f"--out-png={oneshot}",
                    f"--root-cache-path={self.root / 'cache2'}"]
            proc = subprocess.run(argv, start_new_session=True,
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            w, h, rgba = decode_png_rgba(oneshot)
            self.assertEqual((w, h), (1920, 1080))
            with client.request_slot(5, request_id=13) as frame2:
                self.assertEqual(frame2.data, rgba_to_bgra(rgba),
                                 "SHM 槽帧 ≠ #109 OnPaint 直写 PNG 解码帧")
                self.assertEqual(frame2.data, frame.data)

    def test_sequential_slot_throughput_and_rtt(self):
        """K1 证据面：连续槽帧往返（含导航/捕获/发布/释放）计时。"""
        self.spawn_worker()
        n = 8
        rtts = []
        with self.client() as client:
            for f in range(1, n + 1):
                t0 = time.monotonic()
                with client.request_slot(f, request_id=100 + f) as frame:
                    self.assertEqual(frame.header["canary_frame_index"], f)
                rtts.append((time.monotonic() - t0) * 1000.0)
            stats = client.stats()
        self.assertEqual(stats["published"], n)
        self.assertEqual(stats["released"], n)
        avg, mn, mx = sum(rtts) / n, min(rtts), max(rtts)
        print(f"\n[issue110] 模式2 连续 {n} 帧槽往返: avg={avg:.1f}ms "
              f"min={mn:.1f}ms max={mx:.1f}ms", file=sys.stderr)
        _EVIDENCE.mkdir(parents=True, exist_ok=True)
        (_EVIDENCE / "serve_rtt.json").write_text(json.dumps(
            {"n": n, "rtt_ms": rtts, "avg_ms": avg, "min_ms": mn,
             "max_ms": mx}, ensure_ascii=False, indent=1), encoding="utf-8")


@unittest.skipUnless(_GATE and _SELFTEST and _SELFTEST.is_file(),
                     "需要 VTX_TEST_CEF_WORKER（含同目录 shm_ring_selftest）+ Linux /proc")
class FaultInjectionDoD(_ServeFixture):
    """DoD 故障注入三连：尺寸不符/消费端阻塞背压/Worker abort。"""

    def test_dod1_size_mismatch_clear_error(self):
        """① 注入 1920x1079 帧 → SIZE_MISMATCH 明确错误码 + 实测尺寸。"""
        self.spawn_worker("--fault-inject=size_mismatch")
        with self.client() as client:
            with self.assertRaises(cef_client.CefWorkerError) as ctx:
                client.request_png(0, self.root / "x.png", request_id=1)
            self.assertEqual(ctx.exception.status_code,
                             cef_client.STATUS_SIZE_MISMATCH)
            self.assertEqual(ctx.exception.detail["height"], 1079)
            self.assertEqual(ctx.exception.detail["width"], 1920)

    def test_dod2_consumer_stall_backpressure(self):
        """② 消费端阻塞 5s：4 槽环满 → 背压暂停+计时入账+RSS ≤64MB 预算。"""
        self.spawn_worker()
        with self.client() as client:
            held = [client.request_slot(f) for f in range(1, 5)]  # 占满 4 槽
            for f, frame in enumerate(held, start=1):
                self.assertEqual(frame.header["canary_frame_index"], f)
            rss_before = _vm_rss_kb(self.proc.pid)
            self.assertGreaterEqual(rss_before, 0, "无法读取 worker 阻塞前 VmRSS")

            def stall_then_release():
                time.sleep(5.0)               # 故意阻塞 5 秒（DoD 口径）
                for frame in held:
                    frame.release()

            threading.Thread(target=stall_then_release, daemon=True).start()
            t0 = time.monotonic()
            with client.request_slot(5, request_id=50) as frame:   # 第 5 帧
                stall_s = time.monotonic() - t0
                self.assertEqual(frame.header["canary_frame_index"], 5)
                bp = frame.backpressure_wait_us
            rss_after = _vm_rss_kb(self.proc.pid)
            self.assertGreaterEqual(rss_after, 0, "无法读取 worker 阻塞后 VmRSS")
            # 背压计时入账：等待 ≈ 阻塞时长（允许 1s 收敛误差）
            self.assertGreaterEqual(bp, 4_000_000,
                                    f"backpressure_wait_us={bp}（应≈5s）")
            self.assertLess(bp, 15_000_000)
            self.assertGreaterEqual(stall_s, 4.0)
            self.assertGreaterEqual(
                client.stats()["backpressure_wait_us_total"], bp)
            # RSS 预算：环满期间增量 ≤64MB（4×8.3MB 槽 + 暂存 ≈41MB 为上限量级）
            self.assertLess(rss_after - rss_before, 64 * 1024,
                            f"RSS 增量 {rss_after - rss_before}kB > 64MB 预算")
            print(f"\n[issue110] 背压演练: bp={bp/1e6:.2f}s "
                  f"RSS {rss_before}->{rss_after}kB "
                  f"(Δ={rss_after - rss_before}kB)", file=sys.stderr)
            # 恢复：后续帧正常流转
            with client.request_slot(6, request_id=51) as frame:
                self.assertEqual(frame.header["canary_frame_index"], 6)
            self.assertLess(frame.backpressure_wait_us, 1_000_000)

    def test_dod3_worker_abort_eof_and_zero_residue(self):
        """③ Worker 中途 abort()：明确 EOF 错误（非死等）+ 零 SHM 残留。"""
        self.spawn_worker("--fault-inject=abort_after_frame",
                          "--fault-abort-after-frames=1")
        with self.client() as client:
            with client.request_slot(1) as frame:   # 第 1 帧成功
                self.assertEqual(frame.header["canary_frame_index"], 1)
            t0 = time.monotonic()
            with self.assertRaises(cef_client.CefWorkerError) as ctx:
                client.request_slot(2)              # 第 2 帧 → abort
            elapsed = time.monotonic() - t0
            self.assertIn("EOF", str(ctx.exception))  # 明确错误而非死等
            self.assertLess(elapsed, 90)
        rc = self.proc.wait(60)
        self.assertEqual(rc, -signal.SIGABRT, f"abort 退出码异常: {rc}")
        # 树归零（PDEATHSIG 兜底；pgid 在 spawn 时记录）
        pgid = self.pgid
        _wait_until(lambda: group_live_members(pgid) == [], timeout=30)
        self.assertEqual(group_live_members(pgid), [])
        # 零残留：/dev/shm（即建即解绑设计）+ socket 路径（accept 后已解绑）
        self.assertEqual(_shm_residue(), [])
        self.assertFalse(self.sock.exists(), "socket 路径应已被解绑")


@unittest.skipUnless(_GATE and _SELFTEST and _SELFTEST.is_file(),
                     "需要 VTX_TEST_CEF_WORKER（含同目录 shm_ring_selftest）+ Linux /proc")
class ServeEndToEndA1(_ServeFixture):
    """A1（GPU 合成→CPU Buffer，ozone=headless）加分路径：环境允许才跑。

    与 A0 的差异面：GPU 合成像素可能跨 run 非逐位确定（抗锯齿/量化），
    故本类只做同帧证据（槽读出 == .raw 直写 + 金丝雀），跨 run 内容
    等价性由 A0 类对一次性 PNG 解码对照覆盖。后端归属见 #109 矩阵
    （泄漏 WAYLAND_DISPLAY 时为 nvidia_egl，纯无头为 swiftshader）。
    """

    def test_a1_mode2_slot_byte_exact(self):
        self.spawn_worker("--ozone-platform=headless", mode="a1")
        with self.client() as client:
            with client.request_slot(9, request_id=21,
                                     dump_raw_path=self.root / "a1") as frame:
                raw = Path(str(self.root / "a1") + ".raw").read_bytes()
                self.assertEqual(frame.data, raw, "A1 槽读出 ≠ 直写路径")
                self.assertEqual(frame.header["canary_frame_index"], 9)
                b, g, r, a = struct.unpack_from("<4B", frame.data, 0)
                self.assertEqual((r, g, b, a), (0x43, 9, 0, 255))
                distinct = set()
                for y in range(0, 1080, 97):
                    for x in range(0, 1920, 193):
                        off = (y * 1920 + x) * 4
                        distinct.add(frame.data[off:off + 3])
                self.assertGreaterEqual(len(distinct), 5)


@unittest.skipUnless(_GATE and _LINUX_PROC, "需要 VTX_TEST_CEF_WORKER + Linux /proc")
class SupervisedKillZeroResidue(unittest.TestCase):
    """DoD 监管面：kill -9 宿主 → worker 树 ~105ms 归零 + 零残留。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vtx110sup_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.timeline = self.root / "timeline.json"
        self.timeline.write_text(json.dumps(dict(
            project=dict(fps=30, width=6, height=4, duration_frames=3),
            sources=[], subtitles=[], audio_tracks=[],
            video_tracks=[dict(kind="gfx", clips=[
                dict(src="page.html", dst_in=0, dur=3)])])))
        self.hosts = []
        self.addCleanup(self._kill_hosts_and_reap)

    def ctx(self):
        return Ctx(self.timeline)

    def _kill_hosts_and_reap(self):
        for host in self.hosts:
            if host.poll() is None:
                host.kill()   # 测试自起子进程：允许按 PID 杀
        for host in self.hosts:
            try:
                host.wait(10)
            except subprocess.TimeoutExpired:
                pass
        try:
            reap_orphan_workers(self.ctx())
        except Exception:
            pass
        self.assertEqual(_shm_residue(), [], "宿主 kill 后 /dev/shm 零残留")

    def test_kill9_host_zero_residue(self):
        ready = self.root / "ready.json"
        err = self.root / "host.stderr"
        with err.open("wb") as err_fh:
            host = subprocess.Popen(
                [sys.executable, "-c", _HOST_SUPERVISED, str(self.root),
                 str(ready), _WORKER, f"{_CANARY_URL}?f={{f}}",
                 str(ROOT / "experiments/gfx_cef_worker")],
                cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT)},
                stdout=subprocess.DEVNULL, stderr=err_fh)
        self.hosts.append(host)

        def _load():
            try:
                return json.loads(ready.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None

        rec = _wait_until(_load, timeout=90)
        self.assertIsNotNone(
            rec, f"监管宿主 90s 未就绪（stderr: "
                 f"{err.read_text(errors='replace')[-1500:] if err.exists() else '?'}）")
        self.assertEqual(rec["header_w"], 1920)
        pgid, run_dir = rec["pgid"], Path(rec["run_dir"])
        self.assertGreaterEqual(len(group_live_members(pgid)), 3,
                                "CEF 树应 ≥3 成员")
        # —— DoD：对宿主（本测试直接子进程）SIGKILL，服务进行中 ——
        t0 = time.monotonic()
        os.kill(rec["host_pid"], signal.SIGKILL)
        self.hosts[-1].wait(timeout=10)
        time.sleep(max(0.0, 0.105 - (time.monotonic() - t0)))
        self.assertEqual(group_live_members(pgid), [],
                         "kill -9 宿主 ~105ms 后 worker 树残活必须为 0")
        # 此测试在首帧成功后杀宿主：覆盖已解绑阶段，不证明创建窗口无残留
        self.assertEqual(_shm_residue(), [])
        # 临时目录（root_cache + socket 路径所在）由 reap 清扫
        stats = reap_orphan_workers(self.ctx())
        self.assertGreaterEqual(stats["removed"], 1)
        self.assertFalse(run_dir.exists(), "reap 后 run_dir 残留=0")


if __name__ == "__main__":
    unittest.main()
