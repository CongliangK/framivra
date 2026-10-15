"""issue #111（CEF-02b）：AVX2/NEON SIMD BGRA 逆预乘算子与 libspng/fpng
双编码原子落盘适配器的门控验收。

门控：VTX_TEST_CEF_WORKER=<vtx_cef_worker 可执行路径>（先
`bash experiments/gfx_cef_worker/build_cef_worker.sh`；pixel_ops_selftest
须在同目录）。未设置即跳过——本测试需要真实 CEF 二进制。

验收面（工单 DoD + 父工单验证门）：
  1. 原生自检：pixel_ops_selftest 全 PASS——(c,a)∈[0,255]² 全域穷举
     （含 A∈{0,1,2,127,128,254,255}×C 全通道）SIMD==标量零失配、双编码器
     往返逐字节一致、原子写语义；
  2. 一次性路径（--out-png）双后端产物过 vtx/gfx_cli.py:png_invalid_reason
     (width=1920, height=1080)，且 {spng L1, spng L3, fpng, zlib} 四路像素
     互相 Tier-1 全等（#104 analyze_frame_diff）+ 金丝雀像素核验；
  3. serve 模式 1：--png-encoder=spng 与 =fpng 两 worker 同帧 f 产物过
     png_invalid_reason 且互相 Tier-1（两编码器像素等价，文件字节允许不同）；
  4. 原子落盘崩溃窗口：自起 serve worker 持续写帧 → SIGKILL（仅自起进程）
     → 产物目录内 f*.png 全部完整或不存在（无半件）、残件仅
     *.tmp.<pid>.<tid> 可辨识模式且模式清扫后归零；
  5. 性能证据：--bench-json 采集 → /tmp/framivra-evidence/issue-111/。

安全铁律：一切被杀对象均为本测试自起子进程（worker 独立会话=组长；
kill 按 PID 核验）。绝不触碰无关进程。

独立运行：VTX_TEST_CEF_WORKER=<path> python3 -m unittest
tests.test_cef_worker_pixel_ops -v
"""
import json
import os
import re
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "gfx_cef_worker"))
sys.path.insert(1, str(ROOT / "scripts"))

import cef_client  # noqa: E402
import gfx_golden as gg  # noqa: E402

from vtx.gfx_cli import png_invalid_reason  # noqa: E402

from tests.test_cef_worker_shm import _CANARY_URL, _GATE, _WORKER  # noqa: E402
from tests.test_cef_worker_shm import _ServeFixture  # noqa: E402

_SELFTEST = (Path(_WORKER).resolve().parent / "pixel_ops_selftest"
             if _GATE else None)
_EVIDENCE = Path("/tmp/framivra-evidence/issue-111")
_TMP_RE = re.compile(r"\.tmp\.\d+\.\d+$")


def _decode_rgba_pil(path):
    """PIL 全滤波解码（spng SUB/UP 行滤波/fpng filter-0/未知 fdEC 块均覆盖）。"""
    from PIL import Image
    with Image.open(str(path)) as img:
        assert img.size == (1920, 1080), f"尺寸 {img.size}"
        assert img.mode == "RGBA", f"模式 {img.mode}"
        return img.tobytes()


def _collect_bench():
    proc = subprocess.run([str(_SELFTEST), "--bench-json"],
                          capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, f"selftest+bench 失败:\n{proc.stdout[-2000:]}"
    rows = [json.loads(line[len("BENCH_JSON:"):])
            for line in proc.stdout.splitlines()
            if line.startswith("BENCH_JSON:")]
    assert rows, "无 BENCH_JSON 行"
    return rows, proc.stdout


@unittest.skipUnless(_GATE and _SELFTEST and _SELFTEST.is_file(),
                     "需要 VTX_TEST_CEF_WORKER（含同目录 pixel_ops_selftest）")
class NativeSelfTest(unittest.TestCase):
    """DoD 原生层：全域穷举 + 编码器往返 + 原子写 + 性能证据采集。"""

    def test_selftest_all_pass_and_collect_bench(self):
        proc = subprocess.run([str(_SELFTEST)], capture_output=True, text=True,
                              timeout=300)
        self.assertEqual(proc.returncode, 0,
                         f"自检失败:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("ALL PASS", proc.stdout)
        # 验证门 2：全域穷举零失配（两行都在）
        self.assertIn("65,536", proc.stdout)
        self.assertIn("mismatch=0", proc.stdout)
        _EVIDENCE.mkdir(parents=True, exist_ok=True)
        (_EVIDENCE / "selftest.log").write_text(proc.stdout, encoding="utf-8")
        rows, full = _collect_bench()
        (_EVIDENCE / "bench.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        (_EVIDENCE / "bench-native.log").write_text(full, encoding="utf-8")
        # 摘要断言（性能数字本身属证据非硬门；DoD 冲突见报告）：
        #   SIMD 逆预乘不透明帧应显著快于标量（≥5x）
        un = {r["content"]: r for r in rows
              if r["kind"] == "unpremultiply"}
        self.assertGreater(un["opaque"]["speedup"], 5.0)
        self.assertLess(un["opaque"]["simd_ms"], 1.0,
                        "1080p 不透明 SIMD 逆预乘应在 1ms 量级")
        #   fpng 单帧管线（unpremul+encode）应在 ~8ms 内
        fp = {r["content"]: r for r in rows
              if r["kind"] == "pipeline" and r.get("filter") == "fpng"}
        for content, row in fp.items():
            self.assertLess(row["unpremul_plus_fpng_ms"], 8.0,
                            f"fpng 管线 {content} 超出 DoD 8ms")


@unittest.skipUnless(_GATE and _SELFTEST and _SELFTEST.is_file(),
                     "需要 VTX_TEST_CEF_WORKER（含同目录 pixel_ops_selftest）")
class WorkerOneshotEncoders(unittest.TestCase):
    """一次性路径（--out-png）：四后端产物全过校验 + 互相 Tier-1 全等。"""

    ENCODERS = [(["--png-encoder=spng"], "spng_L1"),
                (["--png-encoder=spng", "--png-level=3"], "spng_L3"),
                (["--png-encoder=spng", "--png-filter=none"], "spng_L1_none"),
                (["--png-encoder=fpng"], "fpng"),
                (["--png-encoder=zlib"], "zlib_legacy")]

    def test_all_backends_valid_and_tier1_identical(self):
        frames = {}
        sizes = {}
        with tempfile.TemporaryDirectory(prefix="vtx111_one_") as td:
            td = Path(td)
            for flags, name in self.ENCODERS:
                out = td / f"{name}.png"
                argv = [_WORKER, "--mode=a0",
                        f"--url={_CANARY_URL}?f=5", f"--out-png={out}",
                        f"--root-cache-path={td / 'cache'}", *flags]
                proc = subprocess.run(argv, start_new_session=True,
                                      capture_output=True, text=True,
                                      timeout=180)
                self.assertEqual(proc.returncode, 0,
                                 f"{name} 一次性渲染失败: {proc.stderr[-600:]}")
                reason = png_invalid_reason(out, width=1920, height=1080)
                self.assertIsNone(reason, f"{name} 未过 png_invalid_reason: {reason}")
                frames[name] = _decode_rgba_pil(out)
                sizes[name] = out.stat().st_size
        # 金丝雀像素：f=5 → RGBA(0x43, 5, 0, 255)
        for name, rgba in frames.items():
            self.assertEqual(struct.unpack_from("<4B", rgba, 0),
                             (0x43, 5, 0, 255), f"{name} 金丝雀像素不符")
        # #104 Tier-1：四后端像素互相全等（spng L1 为基准）
        base = frames["spng_L1"]
        for name, rgba in frames.items():
            self.assertEqual(rgba, base,
                             f"{name} 与 spng_L1 像素不一致（应 Tier-1 全等）")
        _EVIDENCE.mkdir(parents=True, exist_ok=True)
        (_EVIDENCE / "oneshot_sizes.json").write_text(
            json.dumps(sizes, ensure_ascii=False, indent=1), encoding="utf-8")
        # 文件字节允许不同（编码器本质差异），此处记录差值供报告
        self.assertGreater(len(set(sizes.values())), 1,
                           "不同编码器文件大小应不同（字节不同的证据）")


@unittest.skipUnless(_GATE and _SELFTEST and _SELFTEST.is_file(),
                     "需要 VTX_TEST_CEF_WORKER（含同目录 pixel_ops_selftest）")
class ServeMode1DualEncoder(_ServeFixture):
    """serve 模式 1：spng/fpng 双 worker 同帧产物 Tier-1 互证。"""

    def test_mode1_spng_vs_fpng_tier1(self):
        receipts = {}
        pngs = {}
        for enc in ("spng", "fpng"):
            self.spawn_worker(f"--png-encoder={enc}")
            out = self.root / f"m1_{enc}.png"
            with self.client() as client:
                rec = client.request_png(5, out, request_id=21)
                self.assertEqual(rec.header["status_code"], 0,
                                 f"{enc} 模式 1 回执非 OK")
                for field in ("t_ready_wait_us", "t_capture_raw_us",
                              "t_alpha_norm_us", "t_png_encode_us",
                              "t_disk_commit_us"):
                    self.assertGreater(rec.header[field], 0)
                receipts[enc] = {k: rec.header[k] for k in
                                 ("t_alpha_norm_us", "t_png_encode_us",
                                  "t_disk_commit_us")}
            reason = png_invalid_reason(out, width=1920, height=1080)
            self.assertIsNone(reason, f"{enc} serve 产物未过校验: {reason}")
            pngs[enc] = out
            self._teardown_worker_once()
        # #104 三级裁决：Tier-1 verified_exact
        report = gg.analyze_frame_diff(str(pngs["spng"]), str(pngs["fpng"]))
        self.assertEqual(report["tier"], gg.TIER_EXACT,
                         f"spng vs fpng 非全等: {report}")
        # 逐字节解码等价（Python 独立解码器复核）
        self.assertEqual(_decode_rgba_pil(pngs["spng"]),
                         _decode_rgba_pil(pngs["fpng"]))
        _EVIDENCE.mkdir(parents=True, exist_ok=True)
        (_EVIDENCE / "mode1_receipts.json").write_text(
            json.dumps({"receipts": receipts,
                        "tier": report["tier"],
                        "sizes": {k: v.stat().st_size for k, v in pngs.items()}},
                       ensure_ascii=False, indent=1), encoding="utf-8")

    def _teardown_worker_once(self):
        """复用 _ServeFixture 清理（其 addCleanup 已注册，双重调用安全无益；
        本类每 test 起两个 worker —— 以显式早收替代，防止 socket 路径冲突。"""
        proc = self.proc
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
                os.kill(proc.pid, signal.SIGKILL)  # 测试自起子进程
                proc.wait(15)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


@unittest.skipUnless(_GATE and _SELFTEST and _SELFTEST.is_file(),
                     "需要 VTX_TEST_CEF_WORKER（含同目录 pixel_ops_selftest）")
class AtomicWriteKillWindow(unittest.TestCase):
    """DoD 崩溃窗口：写者被 SIGKILL 后无半件、残件可辨识可清扫。

    只杀本测试自起的 serve worker（start_new_session 独立进程组）。
    """

    def test_kill_writer_no_partial_products(self):
        # 路径约束：FrameRequest.out_png_path ≤39+NUL 字节（64B 定长请求面），
        # 故用短前缀临时目录 + 短子名（/tmp/v111_XXXXXX/f/f0001.png）。
        with tempfile.TemporaryDirectory(prefix="v111_") as td:
            root = Path(td)
            outdir = root / "f"
            outdir.mkdir()
            cache = root / "c"
            sock = root / "ipc.sock"
            r_fd, w_fd = os.pipe()
            argv = [_WORKER, "--mode=a0", "--serve",
                    f"--ipc-socket={sock}",
                    f"--url-template={_CANARY_URL}?f={{f}}",
                    f"--root-cache-path={cache}",
                    "--fault-inject=none",
                    f"--parent-alive-fd={r_fd}"]
            proc = subprocess.Popen(
                argv, start_new_session=True, pass_fds=(r_fd,),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            os.close(r_fd)
            self.addCleanup(os.close, w_fd)
            self.addCleanup(self._reap, proc)

            # 就绪等待（socket 文件出现即可连；避免阻塞式 stdout 读）
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not sock.exists():
                time.sleep(0.05)
            self.assertTrue(sock.exists(), "worker 60s 未就绪")

            # 后台持续请求模式 1 落盘（帧号循环 1..24，输出 f{:04d}.png 轮转覆盖）
            stop = threading.Event()
            errors = []

            def requester():
                sys.path.insert(0, str(ROOT / "experiments" / "gfx_cef_worker"))
                try:
                    with cef_client.CefWorkerClient.connect(
                            sock, connect_timeout=30, ready_timeout=30) as client:
                        f = 0
                        while not stop.is_set():
                            f = f % 24 + 1
                            out = outdir / f"f{f:04d}.png"
                            client.request_png(f, out)
                except Exception as exc:  # kill 后 EOF 属预期
                    errors.append(str(exc))

            thread = threading.Thread(target=requester, daemon=True)
            thread.start()
            time.sleep(6.0)          # 让写循环进入稳态
            os.kill(proc.pid, signal.SIGKILL)   # 仅自起 worker
            stop.set()
            proc.wait(30)
            thread.join(30)
            if errors:
                print(f"\n[issue111] requester 终止原因（kill 后 EOF 属预期）: "
                      f"{errors[-1]}", file=sys.stderr)
            if proc.poll() is None:  # 未及被杀（写循环仍在）→ 补杀（自起进程）
                os.kill(proc.pid, signal.SIGKILL)
            self.assertEqual(proc.returncode, -signal.SIGKILL,
                             f"worker 应死于 SIGKILL（rc={proc.returncode}）")

            # —— 不变量 1：产物目录 f*.png 全部完整（png_invalid_reason 放行）
            #           或根本不存在 —— 绝无截断半件
            products = sorted(outdir.glob("f*.png"))
            self.assertGreaterEqual(len(products), 1, "至少应有已交付帧")
            for p in products:
                reason = png_invalid_reason(p, width=1920, height=1080)
                self.assertIsNone(reason,
                                  f"崩溃窗口产生半件 {p.name}: {reason}")

            # —— 不变量 2：残件仅 *.tmp.<pid>.<tid> 可辨识模式
            residue = sorted(outdir.iterdir())
            tmp_residue = [p for p in residue if ".tmp." in p.name]
            unexpected = [p for p in residue
                          if p not in set(products) and p not in set(tmp_residue)]
            self.assertEqual(unexpected, [],
                             f"存在不可辨识残件: {[p.name for p in unexpected]}")
            for p in tmp_residue:
                self.assertRegex(p.name, _TMP_RE,
                                 f"残件不符 .tmp.<pid>.<tid> 命名: {p.name}")

            # —— 不变量 3：模式清扫后目录只剩完整产物（可清理性证明）
            for p in tmp_residue:
                p.unlink()
            self.assertEqual(sorted(outdir.iterdir()), products)
            _EVIDENCE.mkdir(parents=True, exist_ok=True)
            (_EVIDENCE / "kill_window.json").write_text(
                json.dumps({"complete_products": len(products),
                            "tmp_residue": [p.name for p in tmp_residue],
                            "requester_tail_errors": errors[-3:]},
                           ensure_ascii=False, indent=1), encoding="utf-8")

    @staticmethod
    def _reap(proc):
        if proc.poll() is None:
            try:
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait(15)
            except (OSError, subprocess.TimeoutExpired):
                pass


if __name__ == "__main__":
    unittest.main()
