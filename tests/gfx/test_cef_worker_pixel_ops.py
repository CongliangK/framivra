"""issue #111（CEF-02b）：AVX2/NEON SIMD BGRA 逆预乘算子与 libspng/fpng
双编码原子落盘适配器的门控验收。

门控：VTX_TEST_CEF_WORKER=<vtx_cef_worker 可执行路径>（先
`bash experiments/gfx_cef_worker/build_cef_worker.sh`；pixel_ops_selftest
须在同目录）。未设置即跳过这些 worker 门；#207 独立纯编码门见下文。

验收面（工单 DoD + 父工单验证门）：
  1. 原生自检：pixel_ops_selftest 全 PASS——(c,a)∈[0,255]² 全域穷举
     （含 A∈{0,1,2,127,128,254,255}×C 全通道）SIMD==标量零失配、双编码器
     往返逐字节一致、原子写语义；
  2. 一次性路径（--out-png）双后端产物过 vtx/render/gfx_cli.py:png_invalid_reason
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

#207 纯编码所有权门（不启动/伪造 CEF worker，不采性能基准）：
  VTX_TEST_PNG_OWNERSHIP=1 python3 -m unittest \
    tests.gfx.test_cef_worker_pixel_ops.PurePngOwnershipSelfTest -v
显式启用时需要 Linux、gcc/g++、系统 zlib 开发文件和 Pillow；缺失即失败。
可另设 VTX_TEST_PNG_OWNERSHIP_ASAN=1 编译地址检测版；ASAN_OPTIONS 控制
检测范围，detect_leaks=0 仅表示 address-only，绝不代表 LSan 通过。
默认关闭这个独立门时为 skip，不能计入已执行的原生验收。

独立运行：VTX_TEST_CEF_WORKER=<path> python3 -m unittest
tests.gfx.test_cef_worker_pixel_ops -v
"""
import json
import os
import platform
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(1, str(ROOT / "scripts"))

from vtx.render import cef_client  # noqa: E402
from scripts.gfx import gfx_golden as gg  # noqa: E402

from vtx.render.gfx_cli import png_invalid_reason  # noqa: E402

from tests.gfx.test_cef_worker_shm import _CANARY_URL, _GATE, _WORKER  # noqa: E402
from tests.gfx.test_cef_worker_shm import _ServeFixture  # noqa: E402

_SELFTEST = (Path(_WORKER).resolve().parent / "pixel_ops_selftest"
             if _GATE else None)
_EVIDENCE = Path(os.environ.get("VTX_CEF_EVIDENCE_DIR", "/tmp/framivra-evidence/issue-111"))
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


@unittest.skipUnless(os.environ.get("VTX_TEST_PNG_OWNERSHIP") == "1",
                     "需要显式 VTX_TEST_PNG_OWNERSHIP=1（无 CEF 的原生编码门）")
class PurePngOwnershipSelfTest(unittest.TestCase):
    """Issue #207: real native writer/vendor; opt-in, never launch a CEF worker."""

    @classmethod
    def setUpClass(cls):
        if not sys.platform.startswith("linux"):
            raise RuntimeError("PNG ownership --wrap selftest requires Linux/GNU ld")
        cc, cxx = shutil.which("gcc"), shutil.which("g++")
        if not cc or not cxx:
            raise RuntimeError("Explicit PNG ownership gate requires gcc and g++")
        cls._temp = tempfile.TemporaryDirectory(prefix="vtx_png_ownership_")
        cls.addClassCleanup(cls._temp.cleanup)
        cls.build = Path(cls._temp.name)
        source = ROOT / "experiments" / "gfx_cef_worker"
        includes = ["-I" + str(source / "src"),
                    "-I" + str(source / "third_party" / "spng"),
                    "-I" + str(source / "third_party" / "fpng")]
        sanitize = (["-fsanitize=address", "-fno-omit-frame-pointer"]
                    if os.environ.get("VTX_TEST_PNG_OWNERSHIP_ASAN") == "1" else [])
        common = ["-O1", "-g", *sanitize, *includes]
        x86 = platform.machine().lower() in ("x86_64", "amd64")
        arch = ["-march=x86-64-v3"] if x86 else []
        sources = [
            ("spng", cc, "c99", source / "third_party/spng/spng.c", ["-w"]),
            ("fpng", cxx, "c++20", source / "third_party/fpng/fpng.cpp",
             ["-w", *(["-msse4.1", "-mpclmul"] if x86 else [])]),
            *[(name, cxx, "c++20", source / "src" / (name + ".cpp"),
               ["-Wall", "-Wextra", "-Werror", *arch])
              for name in ("png_writer", "pixel_ops", "pixel_ops_selftest",
                           "png_writer_ownership_selftest")],
        ]
        for name, compiler, standard, path, flags in sources:
            cls._build([compiler, "-std=" + standard, *common, *flags, "-c",
                        str(path), "-o", str(cls.build / (name + ".o"))])
        base = [str(cls.build / (name + ".o"))
                for name in ("png_writer", "spng", "fpng")]
        wrappers = ["spng_ctx_new", "spng_ctx_free", "spng_set_option",
                    "spng_encode_image", "spng_get_png_buffer", "free"]
        cls.ownership = cls.build / "png_writer_ownership_selftest"
        cls.pixels = cls.build / "pixel_ops_selftest"
        cls._build([cxx, *sanitize, *base,
                    str(cls.build / "png_writer_ownership_selftest.o"),
                    *["-Wl,--wrap=" + name for name in wrappers],
                    "-pthread", "-lz", "-o", str(cls.ownership)])
        cls._build([cxx, *sanitize, *base, str(cls.build / "pixel_ops.o"),
                    str(cls.build / "pixel_ops_selftest.o"),
                    "-pthread", "-lz", "-o", str(cls.pixels)])

    @classmethod
    def _build(cls, argv):
        print("PNG_NATIVE_BUILD " + " ".join(argv), flush=True)
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        if proc.returncode:
            raise RuntimeError(f"Native build failed ({proc.returncode}):\n"
                               f"{proc.stdout}\n{proc.stderr}")

    def test_ownership_and_independent_pixel_decode(self):
        from PIL import Image  # Explicit gate requires the independent decoder.
        samples = self.build / "samples"
        samples.mkdir()
        proc = subprocess.run([str(self.ownership), str(samples)],
                              capture_output=True, text=True, timeout=180)
        print(proc.stdout, end="", flush=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OWNERSHIP ALL PASS", proc.stdout)
        rows = [line.split("\t") for line in proc.stdout.splitlines()
                if line.startswith("SAMPLE\t")]
        self.assertEqual(len(rows), 75)
        for _, name, width, height, raw in rows:
            width, height = int(width), int(height)
            path = samples / name
            self.assertIsNone(png_invalid_reason(path, width=width, height=height))
            with Image.open(path) as image:
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.size, (width, height))
                self.assertEqual(image.tobytes(), (samples / raw).read_bytes())

    def test_existing_native_selftest(self):
        proc = subprocess.run([str(self.pixels)], capture_output=True, text=True,
                              timeout=180)
        print(proc.stdout, end="", flush=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ALL PASS", proc.stdout)


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


# CEF-free execution of the actual encoder task/wait/receipt source. The fake
# pump expires one deadline while a controlled writer is blocked, then releases
# it only on the required drain; a permanently blocked writer must fail-stop.
# No SDK, real deadline or browser is needed.
class EncoderCompletion(unittest.TestCase):
    def test_owned_completion_and_failure_receipts(self):
        compiler = shutil.which('c++')
        if compiler is None:
            self.skipTest('C++ compiler unavailable')
        source = (ROOT / 'experiments/gfx_cef_worker/src/serve.cpp').read_text()
        pool = source[source.index('class MiniPool {'):
                      source.index('// ---------------------------------------------------------------- 帧生产')]
        start = source.index('      uint64_t t_alpha = 0, t_png = 0, t_disk = 0')
        end = source.index('    } else {\n      // ================= 模式 2', start)
        delivery = source[start:end]
        exit_start = source.index('[[noreturn]] void ExitUnclosedServe(')
        fail_stop = source[exit_start:source.index('// ---------------------------------------------------------------- RunServe', exit_start)]
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / 'completion.cpp'
            exe = Path(directory) / 'completion'
            cpp.write_text(_ENCODER_PREFIX + pool + fail_stop + _ENCODER_MAIN_START + delivery + _ENCODER_MAIN_END)
            subprocess.run([compiler, '-std=c++17', '-pthread', '-O0',
                            str(cpp), '-o', str(exe)], check=True, timeout=30,
                           capture_output=True, text=True)
            for case in ('ok', 'encode_false', 'write_false', 'encode_throw',
                         'write_throw', 'timeout', 'timeout_write_false'):
                with self.subTest(case=case):
                    subprocess.run([str(exe), case], check=True, timeout=5,
                                   capture_output=True, text=True)
            # The real _Exit primitive must defeat a join on this deliberately
            # stuck native thread, with no receipt, next request, or stack unwind.
            for case in ('timeout_stuck', 'timeout_stuck_stderr_owned'):
                with self.subTest(case=case):
                    stuck = subprocess.run([str(exe), case], timeout=5,
                                           capture_output=True, text=True,
                                           start_new_session=True)
                    self.assertEqual(stuck.returncode, 4, stuck.stdout + stuck.stderr)
                    self.assertNotIn('UNSAFE_UNWIND', stuck.stderr)


_ENCODER_PREFIX = r'''
#include <atomic>
#include <cassert>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
static std::string test_case;
static std::mutex gate_mu;
static std::condition_variable gate_cv;
static bool writer_entered = false, writer_released = false;
static std::atomic<bool> committed{false};
static std::atomic<int> receipts{0}, requests{0}, completed_logs{0};
static std::atomic<int> png_owners{0}, encode_calls{0};
static bool IsTimeout() { return test_case.rfind("timeout", 0) == 0; }
static bool IsStuck() { return test_case.rfind("timeout_stuck", 0) == 0; }
static uint64_t NowUs() { return 1; }
struct PngEncodeWorkspace {};
struct PngBuffer {
  std::vector<uint8_t> bytes;
  PngBuffer() { ++png_owners; }
  ~PngBuffer() { --png_owners; }
  const uint8_t* data() const { return bytes.data(); }
  size_t size() const { return bytes.size(); }
};
static bool EncodePngBgraPremultiplied(const uint8_t* bgra, int width, int height,
    size_t stride, int, PngEncodeWorkspace* workspace, PngBuffer* png,
    uint64_t* alpha_us) {
  assert(width == 1 && height == 1 && stride == 4);
  assert(workspace && png && alpha_us && bgra[0] == 1 && bgra[3] == 4);
  ++encode_calls;
  if (test_case == "encode_throw") throw std::runtime_error("encoder");
  *alpha_us = 0;
  png->bytes.assign(1, 42);
  return test_case != "encode_false";
}
static bool AtomicWriteBytes(const std::string&, const uint8_t*, size_t) {
  if (IsTimeout()) {
    if (test_case == "timeout_stuck_stderr_owned") ::flockfile(stderr);
    std::unique_lock<std::mutex> lock(gate_mu);
    writer_entered = true;
    gate_cv.notify_all();
    gate_cv.wait(lock, [] { return writer_released; });
  }
  if (test_case == "write_throw") throw std::runtime_error("writer");
  if (test_case == "write_false" || test_case == "timeout_write_false") return false;
  committed.store(true);
  return true;
}
static constexpr int kStatusOk = 0, kStatusTimeout = 1, kStatusJsErr = 3;
struct ProduceResult { int status = kStatusOk; unsigned width = 1, height = 1; };
struct FrameSlotHeader { int status_code; };
static void FillReceipt(FrameSlotHeader* h, const ProduceResult& r, uint64_t,
                        unsigned, int, uint64_t, uint64_t, uint64_t) {
  h->status_code = r.status;
}
static bool SendAll(int, const void* data, size_t) {
  assert(!IsStuck());  // No receipt before permanent task exit.
  assert(png_owners.load() == 0);  // Encoded bytes no longer owned by a live task.
  assert(completed_logs.load() == receipts.load() + 1);
  auto* receipt = static_cast<const FrameSlotHeader*>(data);
  if (IsTimeout()) {
    assert(writer_released);  // No receipt while worker still owns captures.
    assert(requests.load() == 1);
    assert(receipt->status_code == kStatusTimeout);
    assert(committed.load() == (test_case != "timeout_write_false"));
  } else {
    assert(receipt->status_code == (test_case == "ok" ? kStatusOk : kStatusJsErr));
    assert(committed.load() == (test_case == "ok"));
  }
  ++receipts;
  return true;
}
struct FakePump {
  unsigned calls = 0;
  bool Until(const std::function<bool()>& done, int timeout_ms) {
    assert(timeout_ms == (IsTimeout() && calls > 0 ? 5000 : 60000));
    ++calls;
    if (IsTimeout() && calls == 1) {
      std::unique_lock<std::mutex> lock(gate_mu);
      gate_cv.wait(lock, [] { return writer_entered; });
      assert(!done());
      return false;
    }
    if (IsStuck()) {
      assert(calls == 2 && receipts.load() == 0 && requests.load() == 1);
      assert(writer_entered && !writer_released && !committed.load());
      return false;  // The grace expires while the encoder still owns its data.
    }
    if (IsTimeout()) {
      // The drain must happen before any error receipt, reuse or scope exit.
      assert(receipts.load() == 0 && requests.load() == 1);
      assert(!committed.load());
      { std::lock_guard<std::mutex> lock(gate_mu); writer_released = true; }
      gate_cv.notify_all();
    }
    while (!done()) std::this_thread::yield();
    return true;
  }
};
'''
_ENCODER_MAIN_START = r'''
int main(int argc, char** argv) {
  assert(argc == 2); test_case = argv[1];
  struct Sentinel {
    ~Sentinel() {
      if (IsStuck()) std::fputs("UNSAFE_UNWIND\n", stderr);
    }
  } sentinel;
  MiniPool pool(2); FakePump pump;
  struct { std::vector<uint8_t> cap_staging{1, 2, 3, 4}; } state;
  struct { int png = 0; } opt;
  struct { unsigned request_id = 1; int frame_index = 0; } req;
  PngEncodeWorkspace encode_workspace;
  const uint64_t capture_wall_us = 0;
  auto finish_sync = [&](uint64_t, uint64_t, uint64_t, uint64_t, uint64_t,
                         uint64_t, int status) {
    assert(pool.Done() && png_owners.load() == 0);
    if (IsTimeout()) {
      assert(writer_released && status == kStatusTimeout);
    } else {
      assert(status == (test_case == "ok" ? kStatusOk : kStatusJsErr));
    }
    ++completed_logs;
  };
  uint64_t run_id = 1; unsigned ok_replies = 0; int conn = 0, exit_code = 0;
  std::string out_path = "same-output.png";
  for (int index = 0; index < 2; ++index) {
    ++requests; ProduceResult r;
'''
_ENCODER_MAIN_END = r'''
  }
  assert(pool.Done());
  assert(requests.load() == (IsTimeout() ? 1 : 2));
  assert(receipts.load() == requests.load());
  assert(completed_logs.load() == requests.load());
  assert(encode_calls.load() == requests.load() && png_owners.load() == 0);
  assert(ok_replies == (test_case == "ok" ? 2u : 0u));
  assert(exit_code == (IsTimeout() ? 4 : 0));
}
'''



if __name__ == "__main__":
    unittest.main()
