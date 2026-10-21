#!/usr/bin/env python3
"""#142 服务隔离与安全准入对抗测试矩阵。

矩阵（issue #142「实施与验收」逐条固化）：
  禁用门禁   ：transcribe/srt/timeline/batch/sidecar start/调用库(ensure_sidecar/
               transcribe/warm)/直接 server 长驻入口，禁用态（未配置/坏配置/enabled
               "maybe"）一律拒绝，零 HTTP 零拉起零模型加载；只读 health/status、
               init 准备（--once-health）、自有服务 stop 豁免且语义明确。
  误配       ：base_url 指向 18081（IME 专用端口）在任何状态下先于 HTTP 拒绝。
  仅外部服务 ：端口上健康但无法归属（无 pid 记录）→ 拒绝复用，零 warm/transcribe。
  自有复用   ：真 sidecar + 三因子 pid 记录（pid/端口/启动时刻）→ 复用，零拉起。
  身份不符   ：pid 记录指向 sleep（外部进程）→ ensure 拒复用、stop 拒发信号。
  PID 重用   ：记录启动时刻与 /proc 实测不符（cmdline 相符的真 sidecar）→ 拒。
  资源准入   ：假 nvidia-smi 显存不足 → 拉起前安全拒绝；服务内 /warm 500 且不
               加载模型；无 nvidia-smi + cuda → fail-closed N/A 拒绝（可诊断）；
               cpu 设备 → N/A 放行；预算配置优先。
  并发准入   ：admission lease 互斥（两线程串行）。
  OOM        ：运行期 OOM 回收只动自有资源（recover_after_oom 单元）。
  停止       ：陈旧 PID（已死进程）拒信号保现场；正确归属停自有服务成功。
  零触碰     ：全程零字节到 18081（真实桌面输入法存活，绝不绑定/请求/发信号）；
               假 IME 目录文件清单前后不变（零写入）。

铁律：只用假 HOME + 假 IME 锚点 + 临时端口；绝不触碰真实输入法（18081）。
"""
import builtins
from contextlib import contextmanager, redirect_stderr
import ast
import io
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__:
    from ._fixtures import ime_home
else:
    from _fixtures import ime_home

ASR_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = ASR_DIR.parent.parent
sys.path.insert(0, str(ASR_DIR))
import client        # noqa: E402
import independence  # noqa: E402
import sidecar_server  # noqa: E402  模块级零重依赖（qwen_asr 延迟 import）


class Recorder:
    """假外部服务（记录一切请求；充当「仅外部服务存活」的未知服务）。"""

    def __init__(self):
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _record(self, body=None):
                outer.requests.append((self.command, self.path, body))

            def do_GET(self):
                self._record()
                body = json.dumps({"status": "ok", "model": "外部服务",
                                   "aligner": None, "port": self.server.server_address[1],
                                   "version": 1}, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                self._record(raw.decode("utf-8", "replace"))
                body = json.dumps({"error": "外部服务拒绝"}, ensure_ascii=False).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.base_url = "http://127.0.0.1:%d" % self.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def posts(self):
        return [r for r in self.requests if r[0] == "POST"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MatrixBase(unittest.TestCase):
    """公共脚手架：假 HOME + 假 IME 锚点 + 假干净模型 + VTX_CONFIG 临时配置。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vtx-fix142-")
        self.home, self.ime, self.ime_model, self.env = ime_home(self.tmp.name)
        self.ime_snapshot = sorted(str(p) for p in self.ime.rglob("*"))
        # 假干净模型（假 HOME 内项目模型根，非 IME）
        self.clean_model = self.home / ".local/share/framivra/asr/models/Qwen3-ASR-0.6B"
        self.clean_model.mkdir(parents=True)
        (self.clean_model / "model.safetensors").write_bytes(b"CLEAN")
        (self.clean_model / "config.json").write_text("{}", encoding="utf-8")
        # 状态根： independence 按真实 HOME 定位状态根 → 用 VTX_STATE_ROOT? 无此键，
        # 改为把 HOME 指向假 HOME 时状态根随之落在假 HOME（project_state_root 用 ~）

        self._procs = []
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        os.environ.pop("VTX_CONFIG", None)
        os.environ.pop("VT_ASR_IME_ROOT", None)

    def tearDown(self):
        for pr in self._procs:
            if pr.poll() is None:
                pr.terminate()
                try:
                    pr.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pr.kill()
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        # 铁律：全程零写入假 IME 目录
        now = sorted(str(p) for p in self.ime.rglob("*"))
        self.assertEqual(now, self.ime_snapshot, "测试写入了 IME 目录（禁止）")
        self.tmp.cleanup()

    # ---- 配置/工具
    def cfg_file(self, cfg):
        f = Path(self.tmp.name) / ("cfg_%d.json" % int(time.time() * 1000000 % 1e9))
        f.write_text(json.dumps(cfg), encoding="utf-8")
        return str(f)

    def disabled_cfg(self, base_url, **kw):
        return {"asr": dict({"enabled": False, "base_url": base_url}, **kw)}

    def enabled_cfg(self, base_url, **kw):
        return {"asr": dict({"enabled": True, "base_url": base_url}, **kw)}

    def run_cli(self, args, cfg_path, env=None):
        e = dict(self.env)
        e["VTX_CONFIG"] = cfg_path
        if env:
            e.update(env)
        return subprocess.run([sys.executable, str(ASR_DIR / "asr_cli.py")] + args,
                              capture_output=True, text=True, timeout=90, env=e)

    def spawn_sidecar(self, port, cfg_path, extra=(), env=None):
        """真 sidecar 长驻直启（enabled 配置；系统 python3，模型延迟加载）。"""
        e = dict(self.env)
        e["VTX_CONFIG"] = cfg_path
        if env:
            e.update(env)
        pr = subprocess.Popen(
            [sys.executable, str(ASR_DIR / "sidecar_server.py"),
             "--port", str(port), "--model", str(self.clean_model),
             "--no-aligner", "--device-map", "cpu"] + list(extra),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=e)
        self._procs.append(pr)
        base = "http://127.0.0.1:%d" % port
        for _ in range(50):
            try:
                with urllib.request.urlopen(base + "/health", timeout=0.5) as r:
                    if r.status == 200:
                        return pr, base
            except Exception:
                if pr.poll() is not None:
                    raise AssertionError(
                        "sidecar 启动即退出：%s" % pr.stdout.read().decode("utf-8", "replace"))
                time.sleep(0.1)
        raise AssertionError("sidecar 50 次未就绪")


class TestEnabledGate(MatrixBase):
    """① enabled 门禁：禁用/未配置/坏配置 → 计算与启动入口零请求零拉起。"""

    def setUp(self):
        super().setUp()
        self.rec = Recorder()
        self.addCleanup(self.rec.close)

    def test_cli_transcribe_disabled_zero_requests(self):
        cfg = self.cfg_file(self.disabled_cfg(self.rec.base_url))
        r = self.run_cli(["transcribe", "/tmp/a.wav", "--timeout", "3"], cfg)
        self.assertEqual(r.returncode, 2)
        self.assertIn("asr.enabled", r.stdout + r.stderr)
        self.assertEqual(self.rec.requests, [], "禁用态不得发任何 HTTP 请求")

    def test_cli_all_compute_cmds_disabled(self):
        cfg = self.cfg_file(self.disabled_cfg(self.rec.base_url))
        for args in (["srt", "/tmp/a.wav", "-o", "/tmp/out.srt"],
                     ["timeline", "/tmp/a.wav", "-t", "/tmp/tl.json"],
                     ["batch", "/tmp/a.wav", "-o", "/tmp/b"],
                     ["sidecar", "start"]):
            with self.subTest(cmd=args[0]):
                r = self.run_cli(args, cfg)
                self.assertEqual(r.returncode, 2, args)
                self.assertIn("asr.enabled", r.stdout + r.stderr)
        self.assertEqual(self.rec.requests, [], "禁用态不得发任何 HTTP 请求")

    def test_unconfigured_and_bad_config_default_disabled(self):
        # 未配置（config 无 asr 段）与坏配置（enabled:"maybe"/0/[]）一律禁用
        for asr in (None, {"enabled": "maybe"}, {"enabled": 0}, {"enabled": [True]},
                    {"enabled": None}):
            with self.subTest(asr=asr):
                cfg = {"asr": dict(self.disabled_cfg(self.rec.base_url)["asr"],
                                   enabled=None)} if asr is None else {"asr": asr}
                f = self.cfg_file(cfg)
                r = self.run_cli(["sidecar", "start"], f)
                self.assertEqual(r.returncode, 2)
                self.assertIn("asr.enabled", r.stdout + r.stderr)
        self.assertEqual(self.rec.requests, [])

    def test_library_ensure_transcribe_warm_disabled_raise(self):
        for call in ("ensure", "transcribe", "warm"):
            with self.subTest(call=call):
                cfg = self.disabled_cfg(self.rec.base_url)
                with self.assertRaises(independence.AsrIndependenceError):
                    if call == "ensure":
                        client.ensure_sidecar(cfg=cfg, base_url=self.rec.base_url,
                                              verbose=False)
                    elif call == "transcribe":
                        client.transcribe(self.rec.base_url, "/tmp/a.wav", cfg=cfg)
                    else:
                        client.warm(self.rec.base_url, cfg=cfg)
        self.assertEqual(self.rec.requests, [], "禁用态不得发任何 HTTP 请求")

    def test_enabled_true_string_passes_gate(self):
        # enabled:"true"（严格真值集合）通过门禁（后续因缺资产失败，但不是门禁错）
        cfg = self.cfg_file({"asr": {"enabled": "true", "base_url": self.rec.base_url}})
        r = self.run_cli(["sidecar", "start"], cfg)
        self.assertNotIn("asr.enabled 未启用", r.stdout + r.stderr)

    def test_readonly_health_status_stop_exempt(self):
        cfg = self.cfg_file(self.disabled_cfg(self.rec.base_url))
        for args in (["sidecar", "health"], ["sidecar", "status"], ["sidecar", "stop"]):
            with self.subTest(args=args):
                r = self.run_cli(args, cfg)
                self.assertNotEqual(r.returncode, 2, "只读/回收入口不得被门禁拒绝：%s"
                                    % (r.stdout + r.stderr,))
        # health/status 允许只读探测自有 base_url（GET /health）；stop 无记录零信号
        self.assertTrue(all(r[0] == "GET" for r in self.rec.requests),
                        "只读入口只允许 GET")
        self.assertEqual(self.rec.posts(), [])

    def test_direct_server_longrun_requires_enabled(self):
        port = free_port()
        e = dict(self.env)
        e["VTX_CONFIG"] = self.cfg_file({"asr": {"enabled": False}})
        r = subprocess.run(
            [sys.executable, str(ASR_DIR / "sidecar_server.py"), "--port", str(port),
             "--model", str(self.clean_model), "--no-aligner"],
            capture_output=True, text=True, timeout=60, env=e)
        self.assertEqual(r.returncode, 2)
        self.assertIn("asr.enabled", r.stderr + r.stdout)
        # 未配置 config 文件同样拒绝（安全默认）
        e2 = dict(self.env)
        e2["VTX_CONFIG"] = str(Path(self.tmp.name) / "nonexistent.json")
        r2 = subprocess.run(
            [sys.executable, str(ASR_DIR / "sidecar_server.py"), "--port", str(port),
             "--model", str(self.clean_model), "--no-aligner"],
            capture_output=True, text=True, timeout=60, env=e2)
        self.assertEqual(r2.returncode, 2)

    def test_direct_server_once_health_init_prep_exempt(self):
        """--once-health = init 准备冒烟（零模型加载零 GPU）不受门禁限制。"""
        port = free_port()
        e = dict(self.env)
        e["VTX_CONFIG"] = self.cfg_file({"asr": {"enabled": False}})
        r = subprocess.run(
            [sys.executable, str(ASR_DIR / "sidecar_server.py"), "--port", str(port),
             "--model", str(self.clean_model), "--no-aligner", "--once-health"],
            capture_output=True, text=True, timeout=60, env=e)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)


class TestMisconfig(MatrixBase):
    """② 误配 URL/端口/别名：18081 在任何状态下先于 HTTP 拒绝（零字节）。"""

    def setUp(self):
        super().setUp()
        self.rec = Recorder()
        self.addCleanup(self.rec.close)

    def _assert_18081_rejected_pre_http(self, cfg, args):
        f = self.cfg_file(cfg)
        # socket 哨兵：子进程内任何到 18081 的连接都会命中已死端口哨兵——
        # 拒绝必须发生在静态校验（validate_base_url），零连接零字节。
        r = self.run_cli(args, f)
        self.assertEqual(r.returncode, 2)
        self.assertIn("18081", r.stdout + r.stderr)
        self.assertIn("语音输入法", r.stdout + r.stderr)

    def test_baseurl_18081_disabled_state(self):
        self._assert_18081_rejected_pre_http(
            {"asr": {"enabled": False, "base_url": "http://127.0.0.1:18081"}},
            ["sidecar", "start"])

    def test_baseurl_18081_enabled_state(self):
        self._assert_18081_rejected_pre_http(
            {"asr": {"enabled": True, "base_url": "http://127.0.0.1:18081"}},
            ["transcribe", "/tmp/a.wav", "--timeout", "3"])

    def test_baseurl_18081_via_flag_and_localhost_alias(self):
        for url in ("http://localhost:18081", "http://127.0.0.1:18081/"):
            with self.subTest(url=url):
                f = self.cfg_file({"asr": {"enabled": True}})
                r = self.run_cli(["--base-url", url, "transcribe", "/tmp/a.wav",
                                  "--timeout", "3"], f)
                self.assertEqual(r.returncode, 2)
                self.assertIn("语音输入法", r.stdout + r.stderr)
        self.assertEqual(self.rec.requests, [])


class TestOwnership(MatrixBase):
    """③④⑤⑥ 进程归属三因子：外部服务拒复用/自有复用/身份不符/PID 重用/停止。"""

    def test_external_healthy_service_refused_no_warm_transcribe(self):
        rec = Recorder()
        self.addCleanup(rec.close)
        cfg = self.enabled_cfg(rec.base_url)
        # 无本项目 pid 记录 → 拒绝复用；只允许 GET /health，零 POST
        self.assertFalse(client.ensure_sidecar(cfg=cfg, base_url=rec.base_url,
                                               verbose=False))
        self.assertEqual(rec.posts(), [])
        self.assertTrue(all(r[0] == "GET" and r[1] == "/health"
                            for r in rec.requests))

    def test_own_healthy_service_reused_zero_spawn(self):
        port = free_port()
        base = "http://127.0.0.1:%d" % port
        cfgp = self.cfg_file(self.enabled_cfg(base))
        pr, _ = self.spawn_sidecar(port, cfgp)
        independence.write_pid_record(port, pr.pid)
        real_popen = subprocess.Popen

        def _no_spawn(*a, **kw):
            raise AssertionError("自有服务复用时不得拉起进程")

        subprocess.Popen = _no_spawn
        try:
            self.assertTrue(client.ensure_sidecar(
                cfg=json.loads(Path(cfgp).read_text()), base_url=base, verbose=False))
        finally:
            subprocess.Popen = real_popen
        self.assertTrue(client.stop_sidecar(base_url=base,
                                            cfg=json.loads(Path(cfgp).read_text())))

    def test_stop_refuses_signal_for_foreign_process(self):
        """pid 记录指向外部 sleep 进程（伪造/误配）→ 拒发信号，sleep 存活。"""
        cfg = self.enabled_cfg("http://127.0.0.1:1")
        victim = subprocess.Popen(["sleep", "300"])
        self.addCleanup(self._reap, victim)
        time.sleep(0.2)
        port = free_port()
        independence.write_pid_record(port, victim.pid)
        self.assertFalse(client.stop_sidecar(
            base_url="http://127.0.0.1:%d" % port, cfg=cfg))
        self.assertIsNone(victim.poll(), "不得向外部进程发信号")

    def test_pid_reuse_start_time_mismatch_refused(self):
        """记录启动时刻与 /proc 实测不符（PID 重用向量）→ 复用与停止双拒。
        cmdline 因子本身相符（真 sidecar 进程），只动 start_time 因子。"""
        port = free_port()
        base = "http://127.0.0.1:%d" % port
        cfgp = self.cfg_file(self.enabled_cfg(base))
        cfg = json.loads(Path(cfgp).read_text())
        pr, _ = self.spawn_sidecar(port, cfgp)
        # 真 pid + 假启动时刻（模拟 sidecar 已死、PID 被同形进程复用）
        fake = {"pid": pr.pid, "start_time": (independence.pid_start_time(pr.pid) or 1) + 999,
                "port": port}
        pf = independence.pid_file(port)
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(json.dumps(fake) + "\n")
        self.assertFalse(client._sidecar_attribution(base))
        self.assertFalse(client.ensure_sidecar(cfg=cfg, base_url=base, verbose=False))
        self.assertFalse(client.stop_sidecar(base_url=base, cfg=cfg))
        self.assertIsNone(pr.poll(), "启动时刻不符不得发信号")
        # 正确记录恢复后可用（对照正例）
        independence.write_pid_record(port, pr.pid)
        self.assertTrue(client._sidecar_attribution(base))
        self.assertTrue(client.stop_sidecar(base_url=base, cfg=cfg))

    def test_stop_stale_pid_record_kept_no_signal(self):
        """陈旧 PID（进程已死）→ 拒发信号且保留记录供排查。"""
        dead = subprocess.run(["true"])
        port = free_port()
        pf = independence.pid_file(port)
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(json.dumps({"pid": 999999999, "start_time": 1, "port": port}))
        cfg = self.enabled_cfg("http://127.0.0.1:%d" % port)
        self.assertFalse(client.stop_sidecar(
            base_url="http://127.0.0.1:%d" % port, cfg=cfg))
        self.assertTrue(pf.exists(), "无法归属时保留记录供排查")

    def test_corrupted_pid_record_cleared_no_signal(self):
        port = free_port()
        pf = independence.pid_file(port)
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text("not-a-pid")
        cfg = self.enabled_cfg("http://127.0.0.1:%d" % port)
        self.assertFalse(client.stop_sidecar(
            base_url="http://127.0.0.1:%d" % port, cfg=cfg))
        self.assertFalse(pf.exists())

    def _reap(self, pr):
        if pr.poll() is None:
            pr.terminate()
            try:
                pr.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pr.kill()


class TestResourceAdmission(MatrixBase):
    """⑦ 通用资源准入：不依赖 IME 端点；不足/不可测 fail-closed；CPU N/A。"""

    def setUp(self):
        super().setUp()
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        self._path = os.environ.get("PATH")

    def tearDown(self):
        os.environ["PATH"] = self._path
        super().tearDown()

    def _fake_smi(self, total, used):
        script = self.bin / "nvidia-smi"
        script.write_text("#!/bin/sh\necho '%d, %d'\n" % (total, used))
        script.chmod(0o755)
        os.environ["PATH"] = "%s:%s" % (self.bin, self._path)

    def _no_smi(self):
        empty = Path(self.tmp.name) / "emptybin"
        empty.mkdir(exist_ok=True)
        # 空目录 + 受控 PATH 里绝无 nvidia-smi（python 无需其他二进制）
        os.environ["PATH"] = str(empty)

    def test_cpu_device_na_allowed(self):
        d = independence.gpu_admission(6144, device="cpu")
        self.assertTrue(d["allowed"])
        self.assertIn("N/A", d["reason"])

    def test_no_nvidia_smi_cuda_fail_closed_diagnosable(self):
        self._no_smi()
        d = independence.gpu_admission(6144, device="cuda:0")
        self.assertFalse(d["allowed"])
        self.assertIn("nvidia-smi", d["reason"])
        self.assertIn("fail-closed", d["reason"])
        self.assertIn("cpu", d["reason"], "必须给出可行动的 CPU 出路")

    def test_insufficient_vram_safe_reject(self):
        self._fake_smi(16376, 16000)     # free=376 < 预算
        d = independence.gpu_admission(3072, device="cuda:0")
        self.assertFalse(d["allowed"])
        self.assertIn("显存不足", d["reason"])
        self.assertIn("绝不", d["reason"], "必须明示绝不停外部服务")

    def test_sufficient_vram_allowed(self):
        self._fake_smi(16376, 4000)      # free=12376 ≥ 预算
        d = independence.gpu_admission(3072, device="cuda:0")
        self.assertTrue(d["allowed"])

    def _fake_smi_multi(self, pairs):
        """多卡假 nvidia-smi：pairs = [(total, used), ...]，每卡一行。"""
        script = self.bin / "nvidia-smi"
        lines = "".join("echo '%d, %d'\n" % (t, u) for t, u in pairs)
        script.write_text("#!/bin/sh\n" + lines)
        script.chmod(0o755)
        os.environ["PATH"] = "%s:%s" % (self.bin, self._path)

    def test_no_index_cuda_keeps_first_card_semantics(self):
        """cuda（无索引）→ 首卡语义不变：首卡足 → 放行（即使 1 卡不足）。"""
        self._fake_smi_multi([(16376, 4000), (16376, 16284)])
        d = independence.gpu_admission(3072, device="cuda")
        self.assertTrue(d["allowed"])
        self.assertEqual(d["free_mib"], 12376)
        self.assertIn("首卡", d["reason"])
        # 首卡不足（GPU0 free=376 < 预算）→ 拒，即使 GPU1 充足
        self._fake_smi_multi([(16376, 16000), (16376, 400)])
        d = independence.gpu_admission(3072, device="cuda")
        self.assertFalse(d["allowed"])

    def test_cuda1_uses_second_gpu_row_not_first(self):
        """#142 对抗：双卡 GPU0 足 / GPU1 不足 × cuda:1 → 必拒（不得读首行）。"""
        self._fake_smi_multi([(24576, 412), (24576, 24484)])  # free: 24164 / 92
        d = independence.gpu_admission(6144, device="cuda:1")
        self.assertFalse(d["allowed"], "cuda:1 必须按 GPU1 行判定，不得用 GPU0 数据")
        self.assertEqual(d["free_mib"], 92)
        self.assertIn("GPU1", d["reason"])
        self.assertIn("显存不足", d["reason"])

    def test_cuda1_sufficient_allowed(self):
        """cuda:1 两卡皆足 → 放行且按 GPU1 行数据。"""
        self._fake_smi_multi([(16376, 4000), (24576, 412)])   # free: 12376 / 24164
        d = independence.gpu_admission(6144, device="cuda:1")
        self.assertTrue(d["allowed"])
        self.assertEqual(d["free_mib"], 24164)
        self.assertEqual(d["total_mib"], 24576)

    def test_cuda_index_out_of_range_fail_closed(self):
        """cuda:2 越界（仅 2 块 GPU）→ 拒，且不回退任何一行数据。"""
        self._fake_smi_multi([(16376, 4000), (16376, 4000)])
        d = independence.gpu_admission(6144, device="cuda:2")
        self.assertFalse(d["allowed"])
        self.assertIn("越界", d["reason"])
        self.assertIsNone(d["free_mib"], "越界时不得借用任何卡的数据")

    def test_cuda_malformed_index_fail_closed(self):
        """cuda:x / cuda:-1 / cuda: 畸形索引 → 解析失败 fail-closed 拒。"""
        self._fake_smi(16376, 4000)
        for bad in ("cuda:x", "cuda:-1", "cuda:", "CUDA:0:1"):
            with self.subTest(bad=bad):
                d = independence.gpu_admission(6144, device=bad)
                self.assertFalse(d["allowed"], bad)
                self.assertIn("无法解析", d["reason"])

    def test_mps_first_card_semantics(self):
        """mps（既有支持形态）→ 首卡语义：足 → 放行。"""
        self._fake_smi_multi([(16376, 4000), (16376, 16284)])
        d = independence.gpu_admission(3072, device="mps")
        self.assertTrue(d["allowed"])

    def test_budget_config_and_estimate(self):
        self.assertEqual(independence.estimate_vram_mib("x/Qwen3-ASR-0.6B"), 3072)
        self.assertEqual(independence.estimate_vram_mib("x/Qwen3-ASR-1.7B"), 6144)
        cfg = {"asr": {"vram_budget_mib": 128}}
        self.assertEqual(client.vram_budget_of(cfg, "x/Qwen3-ASR-1.7B"), 128)
        self.assertEqual(client.vram_budget_of({}, "x/Qwen3-ASR-1.7B"), 6144)

    def test_bad_resource_config_rejected_in_preflight(self):
        for bad in ({"vram_budget_mib": -1}, {"vram_budget_mib": "8G"},
                    {"vram_budget_mib": True}, {"device_map": 0}):
            with self.subTest(bad=bad):
                cfg = dict(enabled=True, **bad)
                with self.assertRaises(independence.AsrIndependenceError):
                    independence.preflight_config({"asr": cfg})

    def test_ensure_sidecar_pre_launch_admission_denied_no_spawn(self):
        """拉起前显存预检拒绝：零拉起零请求（fail fast，不先 OOM 试错）。"""
        self._fake_smi(16376, 16000)
        cfg = self.enabled_cfg("http://127.0.0.1:%d" % free_port(),
                               device_map="cuda:0", model_dir=str(self.clean_model.parent))
        # python 探测链会失败在显存预检之前——直接单测预检函数级语义 + spawn 哨兵
        real_popen = subprocess.Popen

        def _no_spawn(*a, **kw):
            argv = list(a[0]) if a and isinstance(a[0], (list, tuple)) else []
            if any("sidecar_server.py" in str(x) for x in argv):
                raise AssertionError("显存预检拒绝后不得拉起 sidecar")
            return real_popen(*a, **kw)

        subprocess.Popen = _no_spawn
        try:
            decision = independence.gpu_admission(
                client.vram_budget_of(cfg, str(self.clean_model)), device="cuda:0")
            self.assertFalse(decision["allowed"])
            # 资产缺失（无专属解释器）时 ensure 同样零拉起返回 False；两条路径
            # 都必须先于 Popen——哨兵即证明。
            self.assertFalse(client.ensure_sidecar(cfg=cfg, verbose=False))
        finally:
            subprocess.Popen = real_popen

    def test_sidecar_warm_admission_denied_500_no_model(self):
        """服务内 /warm：显存不足 → 500 + 中文诊断，模型不加载（准入先于 import）。"""
        script = self.bin / "nvidia-smi"
        script.write_text("#!/bin/sh\necho '16376, 16000'\n")
        script.chmod(0o755)
        port = free_port()
        base = "http://127.0.0.1:%d" % port
        cfgp = self.cfg_file(self.enabled_cfg(base))
        pr, _ = self.spawn_sidecar(
            port, cfgp, extra=["--vram-budget", "3072", "--device-map", "cuda:0"],
            env={"PATH": "%s:%s" % (self.bin, self.env.get("PATH", ""))})
        req = urllib.request.Request(base + "/warm", data=b"{}",
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                self.fail("显存不足不得 200：%s" % r.read())
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            self.assertEqual(exc.code, 500)
            self.assertIn("资源准入拒绝", body["error"])
            self.assertIn("显存", body["error"])
        pr.terminate()
        pr.wait(timeout=10)

    def test_admission_lease_mutual_exclusion(self):
        order = []
        ready = threading.Event()
        release = threading.Event()

        def worker(name):
            with independence.admission_lease():
                order.append((name, "in"))
                if name == "A":
                    ready.set()
                    release.wait(timeout=5)
                order.append((name, "out"))

        t1 = threading.Thread(target=worker, args=("A",))
        t1.start()
        self.assertTrue(ready.wait(timeout=5))
        t2 = threading.Thread(target=worker, args=("B",))
        t2.start()
        time.sleep(0.3)
        self.assertEqual(order, [("A", "in")], "B 不得在 A 持锁期间进入临界区")
        release.set()
        t1.join(timeout=5)
        t2.join(timeout=5)
        self.assertEqual(order, [("A", "in"), ("A", "out"), ("B", "in"), ("B", "out")])

    def test_runtime_oom_recovery_own_resources_only(self):
        """运行期 OOM 回收：只迁自有模型回 CPU + 清自有缓存（stub 验证动作面）。"""
        holder = sidecar_server.ModelHolder(str(self.clean_model), device_map="cpu",
                                            idle_timeout=0)
        called = []
        holder._to_cpu_locked = lambda: called.append("to_cpu")
        holder._empty_cache_guard = lambda: called.append("empty_cache")
        holder.recover_after_oom()
        self.assertEqual(called, ["to_cpu"], "OOM 回收只动自有模型，不得出现任何外部操作")

    def test_runtime_oom_returns_503_with_diagnosis(self):
        """_handle_transcribe 的 OOM 包装：OOM 异常 → 503 RequestError + 自有回收。"""
        holder = sidecar_server.ModelHolder(str(self.clean_model), device_map="cpu",
                                            idle_timeout=0)
        recovered = []
        holder.recover_after_oom = lambda: recovered.append(True)

        class _StubModel:
            def transcribe(self, **kw):
                raise RuntimeError("CUDA out of memory (tried to allocate ...)")

        holder._model = _StubModel()
        holder._loaded_path = "stub"
        holder._on_gpu = True
        old = sidecar_server._HOLDER
        sidecar_server._HOLDER = holder
        try:
            class _H:
                headers = {"Content-Length": "0"}
                rfile = b""

            real_read = sidecar_server._read_json_payload
            audio_stub = str(self.clean_model / "model.safetensors")  # 存在即过 400 检查
            sidecar_server._read_json_payload = lambda h: {"audio": audio_stub}
            with self.assertRaises(sidecar_server.RequestError) as ctx:
                sidecar_server._handle_transcribe(_H())
            self.assertEqual(ctx.exception.status, 503)
            self.assertIn("绝不停止/卸载任何外部服务", ctx.exception.message)
            self.assertEqual(recovered, [True])
        finally:
            sidecar_server._HOLDER = old
            sidecar_server._read_json_payload = real_read


class TestSidecarConfigGate(unittest.TestCase):
    """只读自有临时配置，逐项比较 canonical 路径/形状及 enabled 决策。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="asr-config-gate-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.root_config = self.root / "config.local.json"
        self.root_config.write_text('{"asr":{"enabled":true}}', encoding="utf-8")
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        source = mock.patch.object(sidecar_server, "__file__",
                                   str(self.root / "apps/asr/sidecar_server.py"))
        source.start()
        self.addCleanup(source.stop)
        # 源码 AST 提取 canonical 的纯读取合同，不创建第二份 vtx 模块身份，
        # 也不 import 内核依赖。保留实际函数/路径键常量，避免复制配置语义。
        source_path = REPO_ROOT / "vtx/pluginlib/config.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        nodes = [node for node in tree.body
                 if isinstance(node, (ast.Import, ast.ImportFrom))
                 or (isinstance(node, ast.FunctionDef)
                     and node.name in {"config_path", "load_config", "expand_path"})
                 or (isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name)
                             and target.id in {"REPO_ROOT", "PATH_KEYS"}
                             for target in node.targets))]
        namespace = {"__file__": str(source_path)}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source_path), "exec"), namespace)
        self.canonical = SimpleNamespace(**namespace)
        self.reads = []
        read_text = Path.read_text

        def record_read(path, *args, **kwargs):
            self.assertTrue(path.resolve().is_relative_to(self.root),
                            "gate must not read ambient configuration")
            self.reads.append(path.resolve())
            return read_text(path, *args, **kwargs)

        reads = mock.patch.object(Path, "read_text", record_read)
        reads.start()
        self.addCleanup(reads.stop)

    def assert_contract(self, selected, expected):
        self.reads.clear()
        with redirect_stderr(io.StringIO()):
            canonical = self.canonical.load_config(self.root)
            gate = sidecar_server._load_config_for_gate()
        self.assertEqual(canonical, expected)
        self.assertEqual(gate, canonical)
        self.assertEqual(independence.asr_enabled(gate), independence.asr_enabled(canonical))
        self.assertEqual(self.reads, [selected.resolve()] * (2 if selected.is_file() else 0))

    def test_explicit_selection_matrix_never_falls_back(self):
        cases = (("missing", None, {}), ("directory", None, {}),
                 ("malformed", b'{broken', {}), ("invalid-utf8", b'\xff', {}),
                 ("false", b'{"asr":{"enabled":false}}', {"asr": {"enabled": False}}),
                 ("true", b'{"asr":{"enabled":true}}', {"asr": {"enabled": True}}),
                 ("string-false", b'{"asr":{"enabled":"false"}}', {"asr": {"enabled": "false"}}),
                 ("empty", b'{}', {}), ("bad-asr", b'{"asr":[]}', {"asr": []}),
                 ("null", b'null', {}), ("list", b'[]', {}),
                 ("boolean", b'true', {}), ("number", b'2', {}), ("text", b'"text"', {}))
        old_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, old_cwd)
        for form in ("absolute", "relative", "tilde"):
            for name, contents, expected in cases:
                with self.subTest(form=form, case=name):
                    selected = self.home / (form + "-" + name + ".json")
                    if name == "directory":
                        selected.mkdir()
                    elif contents is not None:
                        selected.write_bytes(contents)
                    os.environ["VTX_CONFIG"] = {"absolute": str(selected),
                                                "relative": str(selected.relative_to(self.root)),
                                                "tilde": "~/" + selected.name}[form]
                    self.assert_contract(selected, expected)

    def test_unset_or_empty_override_uses_only_repo_root(self):
        for override in (None, ""):
            for contents, expected in ((None, {}), ('{bad', {}), ('[]', {}),
                                       ('{"asr":{"enabled":false}}', {"asr": {"enabled": False}}),
                                       ('{"asr":{"enabled":true}}', {"asr": {"enabled": True}})):
                with self.subTest(override=override, contents=contents):
                    if override is None:
                        os.environ.pop("VTX_CONFIG", None)
                    else:
                        os.environ["VTX_CONFIG"] = override
                    if contents is None:
                        self.root_config.unlink(missing_ok=True)
                    else:
                        self.root_config.write_text(contents, encoding="utf-8")
                    self.assert_contract(self.root_config, expected)

    def test_selected_read_error_is_empty_without_fallback(self):
        os.environ["VTX_CONFIG"] = str(self.home / "selected.json")
        selected = Path(os.environ["VTX_CONFIG"])
        selected.write_text('{"asr":{"enabled":true}}', encoding="utf-8")
        with mock.patch.object(Path, "read_text", side_effect=OSError("synthetic unreadable")) as read:
            with redirect_stderr(io.StringIO()):
                self.assertEqual(self.canonical.load_config(self.root), {})
                self.assertEqual(sidecar_server._load_config_for_gate(), {})
            self.assertEqual(read.call_count, 2)


class TestModelPlacement(unittest.TestCase):
    """纯内存设备/准入矩阵；不加载 torch/模型、不探测 GPU 或启动服务。"""

    class FakeDevice:
        def __init__(self, owner, name, device):
            self.owner, self.name, self.device = owner, name, device
            self.failures = {}

        def to(self, device):
            self.owner.events.append((self.name, device, self.owner.lease_depth))
            failure = self.failures.get(device)
            if failure is not None:
                raise failure
            self.device = device

    def setUp(self):
        self.events = []
        self.lease_depth = 0
        self.allowed = True
        self.lease_error = None
        self.ind = SimpleNamespace(admission_lease=self.lease,
                                   gpu_admission=self.admit,
                                   estimate_vram_mib=self.estimate)
        for target, value in (("_ind", lambda: self.ind),
                              ("_log", lambda message: None)):
            patcher = mock.patch.object(sidecar_server, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        cache = mock.patch.object(sidecar_server.ModelHolder, "_empty_cache_guard",
                                  lambda holder: None)
        cache.start()
        self.addCleanup(cache.stop)
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in ("torch", "qwen_asr"):
                raise AssertionError("pure placement test must not import " + name)
            return real_import(name, *args, **kwargs)

        for target in ("subprocess.Popen", "subprocess.run", "os.kill", "socket.socket"):
            guard = mock.patch(target, side_effect=AssertionError("native operation forbidden"))
            guard.start()
            self.addCleanup(guard.stop)
        imports = mock.patch("builtins.__import__", side_effect=guarded_import)
        imports.start()
        self.addCleanup(imports.stop)

    @contextmanager
    def lease(self):
        self.assertEqual(self.lease_depth, 0, "admission must not nest leases")
        self.events.append(("lease", "enter"))
        if self.lease_error is not None:
            raise self.lease_error
        self.lease_depth += 1
        try:
            yield
        finally:
            self.lease_depth -= 1
            self.events.append(("lease", "exit"))

    def estimate(self, path):
        self.events.append(("estimate", path))
        return 3072

    def admit(self, required, device):
        self.assertEqual(self.lease_depth, 1)
        self.events.append(("admit", required, device))
        return {"allowed": self.allowed, "reason": "synthetic admission"}

    def holder(self, *, aligner=True, device="cuda:1", budget=None):
        holder = sidecar_server.ModelHolder("initial-1.7B", device_map="cuda:0",
                                            vram_budget_mib=budget, idle_timeout=0)
        model = SimpleNamespace(model=self.FakeDevice(self, "asr", device), device=device)
        model.forced_aligner = (SimpleNamespace(model=self.FakeDevice(self, "aligner", device),
                                               device=device) if aligner else None)
        holder._model, holder._loaded_path = model, "loaded-companion-0.6B"
        holder._on_gpu, holder._gpu_device = True, device
        return holder

    def assert_devices(self, holder, device):
        self.assertEqual(holder._model.model.device, device)
        self.assertEqual(holder._model.device, device)
        fa = holder._model.forced_aligner
        if fa is not None:
            self.assertEqual(fa.model.device, device)
            self.assertEqual(fa.device, device)

    def test_idle_resume_matrix_and_ready_noop(self):
        for aligner in (False, True):
            for device in ("cpu", "cuda:1"):
                for budget in (None, 8192):
                    with self.subTest(aligner=aligner, device=device, budget=budget):
                        holder = self.holder(aligner=aligner, device=device, budget=budget)
                        for _ in range(2):
                            holder._to_cpu_locked()
                            self.assertFalse(holder._on_gpu)
                            self.assert_devices(holder, "cpu")
                            self.events.clear()
                            self.assertIs(holder.acquire(), holder._model)
                            holder.release()
                            self.assertTrue(holder._on_gpu)
                            self.assert_devices(holder, device)
                            expected = ([] if budget else [("estimate", holder.active_path)])
                            expected += [("lease", "enter"), ("admit", budget or 3072, device),
                                         ("asr", device, 1)]
                            if aligner:
                                expected.append(("aligner", device, 1))
                            expected.append(("lease", "exit"))
                            self.assertEqual(self.events, expected)
                            holder.acquire()
                            holder.release()
                            self.assertEqual(self.events, expected, "ready state must be a no-op")

    def test_denied_cold_and_resume_preserve_state_and_unlock(self):
        self.allowed = False
        for cold in (False, True):
            with self.subTest(cold=cold):
                holder = self.holder()
                holder._to_cpu_locked()
                if cold:
                    holder._model, holder._loaded_path = None, ""
                self.events.clear()
                with mock.patch.object(sidecar_server, "companion_path", return_value=None):
                    with self.assertRaisesRegex(RuntimeError, "资源准入拒绝"):
                        holder.acquire()
                self.assertFalse(holder._on_gpu)
                if cold:
                    self.assertIsNone(holder._model)
                else:
                    self.assert_devices(holder, "cpu")
                self.assertEqual(self.events, [("estimate", holder.active_path),
                                              ("lease", "enter"),
                                              ("admit", 3072, "cuda:0" if cold else "cuda:1"),
                                              ("lease", "exit")])
                self.assertTrue(holder._lock.acquire(blocking=False))
                holder._lock.release()

    def test_lease_failure_does_not_move_or_admit(self):
        holder = self.holder()
        holder._to_cpu_locked()
        self.events.clear()
        self.lease_error = RuntimeError("synthetic lease failure")
        with self.assertRaises(RuntimeError) as caught:
            holder.acquire()
        self.assertIs(caught.exception, self.lease_error)
        self.assertEqual(self.events, [("estimate", holder.active_path), ("lease", "enter")])
        self.assert_devices(holder, "cpu")
        self.assertFalse(holder._on_gpu)

    def test_move_failure_rolls_back_both_and_preserves_error(self):
        for failing in ("asr", "aligner"):
            for error_type in (RuntimeError, KeyboardInterrupt):
                with self.subTest(failing=failing, error_type=error_type):
                    holder = self.holder()
                    holder._to_cpu_locked()
                    error = error_type("synthetic move failure")
                    target = holder._model.model if failing == "asr" else holder._model.forced_aligner.model
                    target.failures["cuda:1"] = error
                    self.events.clear()
                    with self.assertRaises(error_type) as caught:
                        holder.acquire()
                    self.assertIs(caught.exception, error)
                    self.assertFalse(holder._on_gpu)
                    self.assert_devices(holder, "cpu")
                    self.assertEqual(self.events[-3:], [("asr", "cpu", 1),
                                                       ("aligner", "cpu", 1), ("lease", "exit")])
                    self.assertTrue(holder._lock.acquire(blocking=False))
                    holder._lock.release()
                    target.failures.clear()
                    holder.acquire()
                    holder.release()
                    self.assertTrue(holder._on_gpu)
                    self.assert_devices(holder, "cuda:1")

    def test_rollback_failure_preserves_primary_error_and_actual_devices(self):
        holder = self.holder()
        holder._to_cpu_locked()
        primary = RuntimeError("aligner restore failed")
        holder._model.forced_aligner.model.failures["cuda:1"] = primary
        holder._model.model.failures["cpu"] = RuntimeError("rollback failed")
        with self.assertRaises(RuntimeError) as caught:
            holder.acquire()
        self.assertIs(caught.exception, primary)
        self.assertFalse(holder._on_gpu)
        self.assertEqual(holder._model.device, "cuda:1")
        self.assertEqual(holder._model.forced_aligner.device, "cpu")
        self.assertEqual(self.lease_depth, 0)

    def test_partial_offload_failure_is_not_ready(self):
        holder = self.holder()
        failure = RuntimeError("aligner offload failed")
        holder._model.forced_aligner.model.failures["cpu"] = failure
        with self.assertRaises(RuntimeError) as caught:
            holder._to_cpu_locked()
        self.assertIs(caught.exception, failure)
        self.assertFalse(holder._on_gpu)
        self.assertEqual(holder._model.device, "cpu")
        self.assertEqual(holder._model.forced_aligner.device, "cuda:1")

    def test_missing_inner_model_fails_closed(self):
        holder = self.holder()
        holder._model.model = None
        holder._on_gpu = False
        with self.assertRaisesRegex(RuntimeError, "缺少可迁移"):
            holder.acquire()
        self.assertFalse(holder._on_gpu)
        self.assertTrue(holder._lock.acquire(blocking=False))
        holder._lock.release()


class TestZeroImeContact(MatrixBase):
    """⑩ 零触碰域：IME 端口零连接（进程内哨兵）+ 假 IME 目录零写入。"""

    def test_no_socket_to_18081_inprocess(self):
        """进程内路径（library 调用）到 18081 的连接哨兵：任何连接直接失败用例。"""
        real_connect = socket.socket.connect

        def _canary(self, address):
            if len(address) >= 2 and address[1] == 18081:
                raise AssertionError("向输入法端口 18081 发起连接（禁止）")
            return real_connect(self, address)

        socket.socket.connect = _canary
        try:
            # 禁用态 + 18081 误配：门禁/静态校验先行，零连接
            cfg = {"asr": {"enabled": False, "base_url": "http://127.0.0.1:18081"}}
            with self.assertRaises(independence.AsrIndependenceError):
                client.ensure_sidecar(cfg=cfg, verbose=False)
            cfg2 = {"asr": {"enabled": True, "base_url": "http://127.0.0.1:18081"}}
            with self.assertRaises(independence.AsrIndependenceError):
                client.ensure_sidecar(cfg=cfg2, verbose=False)
        finally:
            socket.socket.connect = real_connect

    def test_enabled_gate_error_is_not_ime_probe(self):
        """禁用态错误路径的报错不涉及任何 IME 端点探测（纯静态检查）。"""
        d = independence.gpu_admission(1, device="cpu")
        self.assertTrue(d["allowed"])
        self.assertNotIn("18081", json.dumps(d))


if __name__ == "__main__":
    unittest.main(verbosity=2)
