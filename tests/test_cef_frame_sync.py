"""issue #114（CEF-03a）：五阶段单调世代状态机与 v2 双像素 Canary 原地还原。

门控：VTX_TEST_CEF_WORKER=<vtx_cef_worker 可执行路径>（先
`bash experiments/gfx_cef_worker/build_cef_worker.sh`；frame_sync_selftest
须在同目录）。未设置即跳过——本测试需要真实 CEF 二进制与多进程树
（#115 契约：门控变量 VTX_TEST_CEF_WORKER；不使用 MEDIA_CONDITIONS）。

验收面（工单 DoD 逐条）：
  1. 原生对抗自检：frame_sync_selftest 全 PASS（旧 paint/跨世代同帧/
     重复请求/签名半更新/超时/早期 paint/尺寸不符/晚到回调等确定性
     fixture）；--jsonl-out 供本文件跨语言全量 JSON 校验；
  2. 真实 CEF（A0 软件路径 + A1 GPU 路径）：
     - Mode N（逐帧导航 ?f=N）/ Mode P（常驻页 __vtx_render(N)）双驱动；
     - 每帧 S0→S1→S2→S3→S4→S5 严格序（JSONL 全事件序列断言）；
     - receipt canary 帧号来自真实解码结果而非请求值回显（#136）；
     - Clean 双像素验签 + 原地恢复两个与请求绑定且已验证的原像素
       （交付 PNG (0,0)/(0,1) == 透明真值，canary 字节不在；非邻域
       通用化判定由原生 fixture 证明）；
     - 强制注入：stale_canary（恒画 f-1 → S4 持续拒收 rendered=f-1 +
       5000ms 硬超时）、no_ready（S1→S3 超时）、重复 request_id
       （status=4 DUPLICATE）、size_mismatch（status=2）；
     - 跨世代同帧（Mode N 同帧号二次请求）+ Mode P 跨请求旧 canary
       拒收归因（stale_paint:rendered=前帧）—— 世代隔离证据；
  3. JSONL 可观测性：每帧 rejected_stale_paint_count 与
     accepted_elapsed_us_since_ready（S3→S5）逐事件在档。

安全铁律：一切被杀对象均为本测试自起子进程（worker 独立会话=组长）；
kill 均按 PID/账本核验组进行。

独立运行：VTX_TEST_CEF_WORKER=<path> python3 -m unittest
tests.test_cef_frame_sync -v
"""
import json
import os
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "gfx_cef_worker"))

import cef_client  # noqa: E402  (experiments 面；#110 起随 worker 同目录)

from vtx.gfx_cli import png_invalid_reason  # noqa: E402

_LINUX_PROC = Path("/proc/self/stat").is_file()
_WORKER = os.environ.get("VTX_TEST_CEF_WORKER", "")
_GATE = _LINUX_PROC and _WORKER and Path(_WORKER).is_file()
_BIN_DIR = Path(_WORKER).resolve().parent if _GATE else None
_SELFTEST = _BIN_DIR / "frame_sync_selftest" if _BIN_DIR else None
_PAGE = (ROOT / "experiments/gfx_cef_worker/pages/frame_sync_v2.html").as_uri()
_EVIDENCE = Path(os.environ.get("VTX_CEF_EVIDENCE_DIR",
    "/home/liangruochong/.local/state/framivra-orchestrate/evidence/issue-114"))


def _wait_until(pred, timeout=30.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value:
            return value
        if time.monotonic() >= deadline:
            return pred()
        time.sleep(interval)


def decode_png_rgba(path):
    """纯解码 PNG（#111 起 spng 默认行滤波 SUB/UP → 按全滤波类型重建），
    返回 (w, h, rgba bytes)。"""
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = w * 4 + 1
    out = bytearray(w * h * 4)
    prev = bytearray(w * 4)
    for y in range(h):
        ftype = raw[y * stride]
        row = bytearray(raw[y * stride + 1:(y + 1) * stride])
        if ftype == 1:
            for i in range(4, len(row)):
                row[i] = (row[i] + row[i - 4]) & 255
        elif ftype == 2:
            for i in range(len(row)):
                row[i] = (row[i] + prev[i]) & 255
        elif ftype == 3:
            for i in range(len(row)):
                left = row[i - 4] if i >= 4 else 0
                row[i] = (row[i] + (left + prev[i]) // 2) & 255
        elif ftype == 4:
            for i in range(len(row)):
                a = row[i - 4] if i >= 4 else 0
                b = prev[i]
                c = prev[i - 4] if i >= 4 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                row[i] = (row[i] + pred) & 255
        else:
            assert ftype == 0, f"非法定行滤波类型 {ftype}"
        out[y * w * 4:(y + 1) * w * 4] = row
        prev = row
    return w, h, bytes(out)


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def _dump_evidence(name, files):
    """证据落盘（仓外 orchestrate 目录；目录不可写不致测试失败）。"""
    try:
        _EVIDENCE.mkdir(parents=True, exist_ok=True)
        for src, arc in files:
            src = Path(src)
            if src.is_file():
                (target := _EVIDENCE / arc).write_bytes(src.read_bytes())
    except OSError:
        pass


class _SyncServeFixture(unittest.TestCase):
    """sync serve worker 生命周期夹具（自起子进程 + 严格回收）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vtx114_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sock = self.root / "ipc.sock"
        self.cache = self.root / "cache"
        self.sync_log = self.root / "sync.jsonl"
        self.proc = None
        self.threads = []
        self.addCleanup(self._teardown_worker)

    def _teardown_worker(self):
        proc = getattr(self, "proc", None)
        if proc is not None and proc.poll() is None:
            w_fd = getattr(self, "_parent_w", None)
            if w_fd is not None:
                try:
                    os.close(w_fd)
                except OSError:
                    pass
            try:
                proc.wait(15)
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

    def spawn_worker(self, *extra, mode="a0"):
        argv = [_WORKER, f"--mode={mode}", "--serve",
                "--ozone-platform=headless",
                f"--ipc-socket={self.sock}",
                f"--url-template={_PAGE}?f={{f}}",
                f"--root-cache-path={self.cache}",
                "--sync", f"--sync-log={self.sync_log}", *extra]
        r_fd, w_fd = os.pipe()
        argv.append(f"--parent-alive-fd={r_fd}")
        self.proc = subprocess.Popen(
            argv, start_new_session=True, pass_fds=(r_fd,),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        os.close(r_fd)
        self._parent_w = w_fd
        ev = threading.Event()

        def drain(stream, is_ready):
            for line in stream:
                if is_ready and line.startswith("VTXCEF_SERVE_READY"):
                    ev.set()

        t1 = threading.Thread(target=drain, args=(self.proc.stdout, True),
                              daemon=True)
        t2 = threading.Thread(target=drain, args=(self.proc.stderr, False),
                              daemon=True)
        t1.start()
        t2.start()
        self.threads += [t1, t2]
        self.assertTrue(ev.wait(90), "worker 90s 未就绪")

    def client(self, timeout=60.0):
        return cef_client.CefWorkerClient.connect(self.sock,
                                                  connect_timeout=timeout,
                                                  ready_timeout=timeout)

    def jsonl(self):
        self.assertTrue(self.sync_log.is_file(), "JSONL 日志未落盘")
        return load_jsonl(self.sync_log)


@unittest.skipUnless(_GATE and _SELFTEST and _SELFTEST.is_file(),
                     "需要 VTX_TEST_CEF_WORKER（含同目录 frame_sync_selftest）"
                     "+ Linux /proc")
class NativeSelfTest(unittest.TestCase):
    """DoD 原生层：状态机对抗自检全 PASS + JSONL 跨语言全量校验。"""

    def test_selftest_all_pass(self):
        proc = subprocess.run([str(_SELFTEST)], capture_output=True, text=True,
                              timeout=120)
        self.assertEqual(proc.returncode, 0,
                         f"自检失败:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("ALL PASS", proc.stdout)
        self.assertGreaterEqual(proc.stdout.count("PASS "), 100)

    def test_jsonl_cross_language_validation(self):
        """C++ 产出 JSONL → Python 全量解析：字段完备 + 事件序 + DoD 口径。"""
        with tempfile.TemporaryDirectory(prefix="vtx114_jsonl_") as td:
            out = Path(td) / "sync.jsonl"
            proc = subprocess.run(
                [str(_SELFTEST), f"--jsonl-out={out}"],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            events = load_jsonl(out)
        base = {"seq", "ts_us", "event", "drive_mode", "document_gen",
                "request_id", "target_frame_index", "state"}
        for e in events:
            self.assertTrue(base <= set(e.keys()), f"事件缺基础字段: {e}")
            self.assertEqual(e["document_gen"], 42)
        seq_events = [e["event"] for e in events]
        # 混合场景：开局 → S1→S2 → S2→S3 → S3→S4 → 拒收 paint → 接受 → 收尾
        self.assertEqual(seq_events[0], "request_begin")
        self.assertIn("state_transition", seq_events)
        self.assertIn("paint", seq_events)
        self.assertEqual(seq_events[-1], "request_end")
        # DoD 口径：S3→S5 经历的拒收计数与接受时延必须在档
        rejected = [e for e in events if e["event"] == "paint"
                    and e.get("disposition") == "rejected_stale_paint"]
        self.assertTrue(rejected, "缺少拒收 paint 事件")
        self.assertIn("rejected_stale_paint_count", rejected[0])
        accepted = [e for e in events if e["event"] == "state_transition"
                    and e.get("to") == "S5_ONPAINT_VERIFIED"]
        self.assertTrue(accepted, "缺少 S5 接受事件")
        self.assertGreater(accepted[0]["accepted_elapsed_us_since_ready"], 0)
        self.assertEqual(accepted[0]["decoded_frame"], 1234)
        end = events[-1]
        self.assertEqual(end["status"], "accepted")
        self.assertIn("rejected_stale_paint_count", end)
        self.assertIn("early_paint_dropped", end)


@unittest.skipUnless(_GATE and _SELFTEST and _SELFTEST.is_file(),
                     "需要 VTX_TEST_CEF_WORKER（含同目录 frame_sync_selftest）"
                     "+ Linux /proc")
class SyncModeN(_SyncServeFixture):
    """真实 CEF（A0）Mode N：逐帧导航 + 严格状态序 + 原地恢复 + 跨世代同帧。"""

    def test_sequence_restore_and_cross_generation(self):
        frames = [(0, 1), (450, 2), (899, 3), (0, 4)]   # 末位 = 跨世代同帧
        self.spawn_worker()
        outputs = {}
        with self.client() as client:
            for f, rid in frames:
                png = self.root / f"n_f{f}_{rid}.png"
                rec = client.request_png(f, str(png), request_id=rid)
                h = rec.header
                self.assertEqual(h["status_code"], 0, f"f={f} 非 OK")
                # receipt canary 来自真实解码结果（非请求值回显）
                self.assertEqual(h["canary_frame_index"], f)
                # document_gen 随导航单调递增（Mode N 每请求一导航）
                self.assertEqual(h["document_gen"], rid)
                self.assertEqual(h["request_id"], rid)
                self.assertIsNone(png_invalid_reason(png, width=1920,
                                                     height=1080))
                outputs[(f, rid)] = png.read_bytes()
        # —— 交付像素：canary 已剥、绑定原像素已恢复（透明真值）——
        for (f, rid), blob in outputs.items():
            w, h, rgba = decode_png_rgba(self.root / f"n_f{f}_{rid}.png")
            self.assertEqual((rgba[0], rgba[1], rgba[2], rgba[3]),
                             (0, 0, 0, 0), f"f={f} (0,0) 非恢复透明真值")
            self.assertEqual((rgba[4], rgba[5], rgba[6], rgba[7]),
                             (0, 0, 0, 0), f"f={f} (0,1) 非恢复透明真值")
        # 跨世代同帧（f=0 请求两次，gen 1 vs 4）：f 纯函数页 → 逐字节一致
        self.assertEqual(outputs[(0, 1)], outputs[(0, 4)],
                         "跨世代同帧号交付应逐字节一致（页面状态 = f 纯函数）")
        # —— JSONL 严格状态序（每请求 S0→S1→…→S5→request_end）——
        events = self.jsonl()
        for f, rid in frames:
            mine = [e for e in events if e["request_id"] == rid]
            # S0→S1 记录在 request_begin（to=S1）；后续跃迁在 state_transition
            states = [e["to"] for e in mine
                      if e["event"] in ("request_begin", "state_transition")]
            self.assertEqual(states, ["S1_NAV_OR_EVAL_ISSUED",
                                      "S2_ASSETS_FONTS_READY",
                                      "S3_JS_FRAME_APPLIED_AND_CANARY_SET",
                                      "S4_COMPOSITOR_PUMPING",
                                      "S5_ONPAINT_VERIFIED"],
                             f"req={rid} 状态序不严格")
            end = mine[-1]
            self.assertEqual(end["event"], "request_end")
            self.assertEqual(end["status"], "accepted")
            self.assertGreater(end["accepted_elapsed_us_since_ready"], 0,
                               "S3→S5 时延缺失")
        # 早期丢弃证据：S4 前泵送推进 rAF 期间有残余 Paint 被围栏丢弃
        early = [e for e in events if e["event"] == "paint"
                 and e.get("disposition") == "early_paint_dropped"]
        self.assertGreater(len(early), 0, "无早期 paint 丢弃证据")
        _dump_evidence("mode_n", [(self.sync_log, "mode_n_sync.jsonl"),
                                  (self.root / "n_f450_2.png", "mode_n_f450.png")])

    def test_mode2_slot_restored_bytes(self):
        """sync + 模式 2（SHM 槽）：恢复后的字节经槽与 .raw 直写双路一致。"""
        self.spawn_worker()
        with self.client() as client:
            with client.request_slot(21, request_id=9,
                                     dump_raw_path=self.root / "slot") as fr:
                raw = Path(str(self.root / "slot") + ".raw").read_bytes()
                self.assertEqual(fr.data, raw, "槽读出 ≠ 直写路径")
                self.assertEqual(fr.header["canary_frame_index"], 21)
                self.assertEqual(fr.data[0:8], b"\x00" * 8,
                                 "槽 (0,0)/(0,1) 应为恢复后的透明原像素")
                # canary 预乘签名字节确实不在交付面（非「未画」的假恢复）
                self.assertNotEqual(fr.data[0:4],
                                    bytes((90, 21, 0, 254)))
        _dump_evidence("mode2", [(self.sync_log, "mode2_sync.jsonl")])


@unittest.skipUnless(_GATE and _SELFTEST and _SELFTEST.is_file(),
                     "需要 VTX_TEST_CEF_WORKER（含同目录 frame_sync_selftest）"
                     "+ Linux /proc")
class SyncModeP(_SyncServeFixture):
    """真实 CEF（A0）Mode P：常驻页 __vtx_render + 跨请求旧 canary 拒收归因。"""

    def test_jump_sequence_and_stale_attribution(self):
        seq = [(0, 1), (450, 2), (12, 3), (12, 4), (899, 5)]
        self.spawn_worker("--sync-mode=p")
        with self.client() as client:
            for f, rid in seq:
                png = self.root / f"p_f{f}_{rid}.png"
                rec = client.request_png(f, str(png), request_id=rid)
                h = rec.header
                self.assertEqual(h["status_code"], 0, f"f={f} 非 OK")
                self.assertEqual(h["canary_frame_index"], f)
                self.assertEqual(h["document_gen"], 1,
                                 "Mode P 常驻页 document_gen 恒 1")
        events = self.jsonl()
        # —— 世代隔离证据：请求 2（目标 450）的 S4 拒收应归因到旧 canary
        #    （rendered=0 —— 上一请求的帧签名仍在画布上，即 CEF #4166
        #    「旧软件缓冲/上一帧尾随 Paint」形态）。——
        stale_attributed = [
            e for e in events
            if e["event"] == "paint" and e["request_id"] == 2
            and e.get("reason") == "stale_paint:rendered=0"]
        self.assertGreater(len(stale_attributed), 0,
                           "Mode P 跨请求旧 canary 无拒收归因证据")
        # 每请求仍走严格状态序（request_begin 的 to=S1 + 4 次跃迁）
        for f, rid in seq:
            states = [e["to"] for e in events
                      if e["request_id"] == rid
                      and e["event"] in ("request_begin", "state_transition")]
            self.assertEqual(len(states), 5, f"req={rid} 状态转移数异常")
        _dump_evidence("mode_p", [(self.sync_log, "mode_p_sync.jsonl"),
                                  (self.root / "p_f899_5.png",
                                   "mode_p_f899.png")])


@unittest.skipUnless(_GATE and _SELFTEST and _SELFTEST.is_file(),
                     "需要 VTX_TEST_CEF_WORKER（含同目录 frame_sync_selftest）"
                     "+ Linux /proc")
class SyncFaultInjection(_SyncServeFixture):
    """强制注入面：stale_canary / no_ready / 重复请求 / size_mismatch。"""

    def test_stale_canary_pump_timeout(self):
        """恒画 f-1 签名：S4 持续拒收（rendered=f-1 归因）→ 5000ms 硬超时。"""
        self.spawn_worker("--sync-fault=stale_canary",
                          "--sync-pump-timeout-ms=3000")
        t0 = time.monotonic()
        with self.client() as client:
            with self.assertRaises(cef_client.CefWorkerError) as ctx:
                client.request_png(7, str(self.root / "x.png"), request_id=1)
            self.assertEqual(ctx.exception.status_code,
                             cef_client.STATUS_TIMEOUT)
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 2.5, "未经历泵送窗口即超时")
        self.assertLess(elapsed, 30)
        events = self.jsonl()
        stale = [e for e in events if e["event"] == "paint"
                 and e.get("reason") == "stale_paint:rendered=6"]
        self.assertGreater(len(stale), 50, "陈旧签名拒收证据不足")
        end = [e for e in events if e["event"] == "request_end"][-1]
        self.assertEqual(end["status"], "failed")
        self.assertEqual(end["reason"], "pump_timeout")
        self.assertGreaterEqual(end["rejected_stale_paint_count"], 50)
        _dump_evidence("stale_canary",
                       [(self.sync_log, "stale_canary_sync.jsonl")])

    def test_no_ready_timeout(self):
        """不发就绪信号：S1→S3 等待超时（不进 S4，无 settle 碰运气）。"""
        self.spawn_worker("--sync-fault=no_ready",
                          "--sync-ready-timeout-ms=3000")
        with self.client() as client:
            with self.assertRaises(cef_client.CefWorkerError) as ctx:
                client.request_png(7, str(self.root / "x.png"), request_id=1)
            self.assertEqual(ctx.exception.status_code,
                             cef_client.STATUS_TIMEOUT)
        events = self.jsonl()
        states = [e["to"] for e in events if e["event"] == "state_transition"]
        self.assertNotIn("S3_JS_FRAME_APPLIED_AND_CANARY_SET", states,
                         "no_ready 不应到达 S3")
        end = [e for e in events if e["event"] == "request_end"][-1]
        self.assertEqual(end["reason"], "ready_timeout")

    def test_duplicate_request_rejected(self):
        """同 request_id 重发：第二次显式 DUPLICATE(4)，不静默重渲。"""
        self.spawn_worker()
        with self.client() as client:
            rec = client.request_png(3, str(self.root / "a.png"),
                                     request_id=42)
            self.assertEqual(rec.header["status_code"], 0)
            with self.assertRaises(cef_client.CefWorkerError) as ctx:
                client.request_png(3, str(self.root / "b.png"),
                                   request_id=42)
            self.assertEqual(ctx.exception.status_code,
                             cef_client.STATUS_DUPLICATE)
        events = self.jsonl()
        dups = [e for e in events if e["event"] == "duplicate_request_rejected"]
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["request_id"], 42)

    def test_size_mismatch(self):
        """注入 1920x1079：SIZE_MISMATCH(2) + JSONL paint 事件在档。"""
        self.spawn_worker("--fault-inject=size_mismatch")
        with self.client() as client:
            with self.assertRaises(cef_client.CefWorkerError) as ctx:
                client.request_png(3, str(self.root / "x.png"), request_id=1)
            self.assertEqual(ctx.exception.status_code,
                             cef_client.STATUS_SIZE_MISMATCH)
            self.assertEqual(ctx.exception.detail["height"], 1079)
        events = self.jsonl()
        self.assertTrue(any(e["event"] == "paint"
                            and e.get("disposition") == "size_mismatch"
                            for e in events), "尺寸不符 paint 事件缺失")


@unittest.skipUnless(_GATE and _SELFTEST and _SELFTEST.is_file(),
                     "需要 VTX_TEST_CEF_WORKER（含同目录 frame_sync_selftest）"
                     "+ Linux /proc")
class SyncA1Gpu(_SyncServeFixture):
    """A1（GPU 合成→CPU Buffer，ozone=headless）：sync 状态机同帧证据。

    A1 像素跨 run 非逐位确定（抗锯齿/量化），故本类只做状态机口径
    （状态序/解码 canary/恢复源），不做跨 run 字节对照。
    """

    def test_a1_mode_n_frames(self):
        self.spawn_worker("--ozone-platform=headless", mode="a1")
        with self.client() as client:
            for f, rid in ((5, 1), (450, 2)):
                png = self.root / f"a1_f{f}.png"
                rec = client.request_png(f, str(png), request_id=rid)
                h = rec.header
                self.assertEqual(h["status_code"], 0, f"A1 f={f} 非 OK")
                self.assertEqual(h["canary_frame_index"], f)
        events = self.jsonl()
        for rid in (1, 2):
            states = [e["to"] for e in events
                      if e["request_id"] == rid
                      and e["event"] in ("request_begin", "state_transition")]
            self.assertEqual(len(states), 5)
        acc = [e for e in events if e["event"] == "state_transition"
               and e.get("to") == "S5_ONPAINT_VERIFIED"]
        self.assertTrue(all(e.get("restore_source") == "reported_original"
                            for e in acc), "A1 恢复源非上报原像素")
        _dump_evidence("a1", [(self.sync_log, "a1_sync.jsonl")])


if __name__ == "__main__":
    unittest.main()
