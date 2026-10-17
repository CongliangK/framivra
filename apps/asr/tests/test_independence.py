#!/usr/bin/env python3
"""independence 对抗单测（issue #141：删除全部 IME 复用链 + 拒绝遗留配置/路径注入）。

铁律：本测试绝不接触真实输入法（本机 18081 是活服务）——
  - 只用假 HOME 下的假 IME 目录（锚点路径在 $HOME 内展开）；
  - 所有「零 HTTP 请求」断言用 urlopen 计数器补获；
  - 所有「零信号」断言用 os.kill 哨兵；
  - 不监听 18081，不向 18081 发任何字节。

矩阵（对应 #141 自动化验收 1/3/4）：
  A. 遗留 env VT_ASR_IME_ROOT / 旧键 asr.ime_root → 迁移错误，零请求零拉起；
  B. base_url 误配（127.0.0.1/localhost/0.0.0.0/别名 × 18081）→ 请求前拒绝，零 HTTP；
  C. 显式 asr.python / model_dir / aligner_dir 指向 IME（含 symlink 逃逸）→ 使用前拒绝；
  D. 仅假 IME 资产、项目资产缺失 → 探测失败且假 IME 解释器零调用、零写入；
  E. 模型权重硬链接（nlink>1）→ 拒绝；
  F. PYTHONPATH/user-site 注入 → sandbox_env 剥离；依赖来源（site-packages 归属）验证；
  G. pid 文件指向外部进程 → 拒绝发信号（零信号）；
  H. 端口健康但无法归属 → 拒绝复用（零拉起）；
  I. 资产清单（版本/来源/SHA256）接口正反例；
  J. 复用链成员已删除（ime_root/ime_sidecar_alive/_ime_root/_ime_sidecar_alive 不存在）。
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import client          # noqa: E402
import independence    # noqa: E402
import sidecar_server  # noqa: E402  (纯 stdlib 头部，可安全导入)


class HttpSentinel(Exception):
    pass


class KillSentinel(Exception):
    pass


class AdversarialBase(unittest.TestCase):
    """公共脚手架：假 HOME + 假 IME + urlopen/kill/Popen 哨兵。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fix141-test-")
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir(parents=True)
        self._real_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self._saved_env = {}
        for k in (independence.LEGACY_ENV, "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE"):
            self._saved_env[k] = os.environ.get(k)
            os.environ.pop(k, None)
        # 假 IME 仓（默认锚点在 $HOME 下展开——绝不触碰真实 ~/文档）
        self.ime = self.home / "文档/01_活跃项目/type/ibus-voice-ime-github"
        (self.ime / ".venv-qwen-asr/bin").mkdir(parents=True)
        (self.ime / "vendor/models/qwen3-asr/Qwen3-ASR-1.7B").mkdir(parents=True)
        (self.ime / "vendor/models/qwen3-asr/Qwen3-ASR-1.7B/model.safetensors").write_bytes(b"FAKE-IME-WEIGHT")
        self.ime_calls = Path(self.tmp.name) / "ime_calls"
        # 假 IME 解释器：任何调用都记录（断言零调用的依据）
        stub = self.ime / ".venv-qwen-asr/bin/python"
        stub.write_text('#!/bin/bash\necho "invoked: $*" >> %s\nexit 0\n' % self.ime_calls)
        stub.chmod(0o755)
        # HTTP 哨兵：默认任何 urlopen 都算违规（逐测试显式放行 mock 服务）
        self.http_calls = []
        self._real_urlopen = urllib.request.urlopen
        urllib.request.urlopen = self._urlopen_guard
        self._real_kill = os.kill
        self._real_popen = subprocess.Popen
        self._real_run = subprocess.run
        self.popen_calls = []

    def tearDown(self):
        urllib.request.urlopen = self._real_urlopen
        os.kill = self._real_kill
        subprocess.Popen = self._real_popen
        subprocess.run = self._real_run
        if self._real_home:
            os.environ["HOME"] = self._real_home
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
        self.tmp.cleanup()

    def _urlopen_guard(self, req, *a, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.http_calls.append(url)
        if ":18081" in url:
            raise AssertionError("向输入法端口 18081 发起了 HTTP 请求：%s" % url)
        return self._real_urlopen(req, *a, **kw)

    def allow_http_to(self, url_prefix):
        """显式放行指向自有 mock 的请求（仍拦截 18081）。"""
        urllib.request.urlopen = lambda req, *a, **kw: (
            self.http_calls.append(getattr(req, "full_url", str(req))),
            self._raise_if_ime(getattr(req, "full_url", str(req))),
            self._real_urlopen(req, *a, **kw))[-1]

    def _raise_if_ime(self, url):
        if ":18081" in url:
            raise AssertionError("向输入法端口发请求：%s" % url)

    def arm_kill_sentinel(self):
        def _no_kill(pid, sig):
            raise KillSentinel("向 pid %s 发信号 %s（禁止）" % (pid, sig))
        os.kill = _no_kill

    def arm_popen_sentinel(self):
        def _no_popen(*a, **kw):
            self.popen_calls.append(a)
            raise AssertionError("不应拉起进程：%s" % (a,))
        subprocess.Popen = _no_popen

    def assert_ime_untouched(self):
        self.assertFalse(self.ime_calls.exists() or
                         (self.ime_calls.exists() and self.ime_calls.read_text().strip()),
                         "假 IME 解释器被调用（禁止）")
        # IME 域零写入：权重文件内容不变
        w = self.ime / "vendor/models/qwen3-asr/Qwen3-ASR-1.7B/model.safetensors"
        self.assertEqual(w.read_bytes(), b"FAKE-IME-WEIGHT")
        self.assertEqual(sorted(p.name for p in self.ime.rglob("*") if p.is_file()),
                         ["model.safetensors", "python"])


class TestLegacyConfig(AdversarialBase):
    """A. 遗留 env/旧键 → 迁移错误，零请求零拉起。"""

    def test_legacy_env_rejected_before_any_request(self):
        os.environ[independence.LEGACY_ENV] = str(self.ime)
        self.arm_popen_sentinel()
        with self.assertRaises(independence.AsrIndependenceError) as ctx:
            client.ensure_sidecar(cfg={}, base_url="http://127.0.0.1:18085",
                                  verbose=False)
        self.assertIn("VT_ASR_IME_ROOT", str(ctx.exception))
        self.assertEqual(self.http_calls, [])
        self.assert_ime_untouched()

    def test_legacy_config_key_rejected(self):
        self.arm_popen_sentinel()
        cfg = {"asr": {"ime_root": str(self.ime), "base_url": "http://127.0.0.1:18085"}}
        with self.assertRaises(independence.AsrIndependenceError) as ctx:
            client.preflight(cfg)
        self.assertIn("asr.ime_root", str(ctx.exception))
        self.assertEqual(self.http_calls, [])

    def test_cli_preflight_exits_closed(self):
        # asr_cli.main 在任何子命令前 preflight：旧 env → 退出码 2、零请求
        os.environ[independence.LEGACY_ENV] = str(self.ime)
        r = subprocess.run(
            [sys.executable, str(Path(client.ASR_DIR) / "asr_cli.py"),
             "sidecar", "health"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ})
        self.assertEqual(r.returncode, 2)
        self.assertIn("VT_ASR_IME_ROOT", (r.stdout + r.stderr))


class TestBaseUrlValidation(AdversarialBase):
    """B. base_url 误配矩阵 → 请求前拒绝，零 HTTP。"""

    def test_ime_port_matrix_rejected(self):
        for bad in ("http://127.0.0.1:18081",
                    "http://localhost:18081",
                    "http://0.0.0.0:18081",
                    "http://127.0.0.1:18081/",
                    "http://localhost:18081/health",
                    "http://ime.local:18081"):   # hosts 别名形态
            with self.assertRaises(independence.AsrIndependenceError, msg=bad):
                independence.validate_base_url(bad)
        self.assertEqual(self.http_calls, [])

    def test_our_default_url_ok(self):
        self.assertEqual(independence.validate_base_url("http://127.0.0.1:18085"),
                         "http://127.0.0.1:18085")

    def test_bad_scheme_rejected(self):
        for bad in ("", "ftp://x:1", "http://", None, 123):
            with self.assertRaises(independence.AsrIndependenceError):
                independence.validate_base_url(bad)

    def test_configured_ime_url_rejected_at_cli_level(self):
        cfg = {"asr": {"base_url": "http://localhost:18081"}}
        with self.assertRaises(independence.AsrIndependenceError):
            independence.preflight_config(cfg)
        self.assertEqual(self.http_calls, [])


class TestPathInjection(AdversarialBase):
    """C/D/E. 显式路径注入（python/model_dir/aligner_dir、symlink、硬链接）。"""

    def test_explicit_python_into_ime_rejected(self):
        self.arm_popen_sentinel()
        cfg = {"asr": {"python": str(self.ime / ".venv-qwen-asr/bin/python")}}
        with self.assertRaises(independence.AsrIndependenceError) as ctx:
            independence.preflight_config(cfg)
        self.assertIn("语音输入法", str(ctx.exception))
        self.assert_ime_untouched()

    def test_symlink_escape_into_ime_rejected(self):
        link = Path(self.tmp.name) / "harmless-venv"
        link.symlink_to(self.ime / ".venv-qwen-asr")
        with self.assertRaises(independence.AsrIndependenceError):
            independence.verify_interpreter(str(link / "bin/python"))
        mlink = Path(self.tmp.name) / "models"
        mlink.symlink_to(self.ime / "vendor/models/qwen3-asr")
        with self.assertRaises(independence.AsrIndependenceError):
            independence.verify_model_dir(str(mlink / "Qwen3-ASR-1.7B"))

    def test_model_dir_into_ime_rejected_in_probe(self):
        cfg = {"asr": {"model_dir": str(self.ime / "vendor/models/qwen3-asr")}}
        with self.assertRaises(independence.AsrIndependenceError):
            client.model_roots(cfg)
        cfg2 = {"asr": {"aligner_dir": str(self.ime / "vendor")}}
        with self.assertRaises(independence.AsrIndependenceError):
            client.probe_aligner(cfg2)
        self.assert_ime_untouched()

    def test_hardlinked_weight_rejected(self):
        d = Path(self.tmp.name) / "own-model/Qwen3-ASR-0.6B"
        d.mkdir(parents=True)
        src = Path(self.tmp.name) / "borrowed.safetensors"
        src.write_bytes(b"BIG")
        os.link(src, d / "model.safetensors")     # nlink=2
        with self.assertRaises(independence.AsrIndependenceError) as ctx:
            independence.verify_model_dir(str(d), check_weights=True)
        self.assertIn("硬链接", str(ctx.exception))

    def test_ime_only_assets_probe_fails_without_touching_ime(self):
        # 验收 1：仅假 IME 环境和模型存在、项目资产缺失 → 明确失败
        # （config.asr.python 指向假 IME venv 也绝不探测使用）
        cfg = {"asr": {"python": str(self.ime / ".venv-qwen-asr/bin/python")}}
        self.assertIsNone(client.probe_python(cfg))
        self.assertIsNone(client.probe_model({}))          # 项目根缺失
        self.assertIsNone(client.probe_aligner({}))
        self.assert_ime_untouched()                        # IME 解释器零调用

    def test_own_model_dir_accepted(self):
        d = Path(self.tmp.name) / "own-model/Qwen3-ASR-0.6B"
        d.mkdir(parents=True)
        (d / "model.safetensors").write_bytes(b"MINE")
        cfg = {"asr": {"model_dir": str(d.parent)}}
        self.assertEqual(client.probe_model(cfg, "0.6b"), str(d))
        self.assertEqual(client.pick_model(cfg), str(d))


class TestEnvInjection(AdversarialBase):
    """F. PYTHONPATH/user-site 注入剥离 + 依赖来源验证。"""

    def test_sandbox_env_strips_injections(self):
        os.environ["PYTHONPATH"] = str(self.ime)
        os.environ["PYTHONHOME"] = str(self.ime)
        os.environ["PYTHONUSERBASE"] = str(self.ime)
        env = independence.sandbox_env()
        for var in independence.SANDBOX_STRIP_VARS:
            self.assertNotIn(var, env, "%s 必须剥离" % var)

    def test_module_origin_from_ime_rejected(self):
        # 假解释器：import qwen_asr 的 __file__ 报告在 IME site-packages 内
        py = Path(self.tmp.name) / "ownenv/bin/python"
        py.parent.mkdir(parents=True)
        fake_sp = self.ime / ".venv-qwen-asr/lib/python3.14/site-packages"
        py.write_text('#!/bin/bash\n[[ "${1:-}" == "-I" ]] && shift\n'
                      'echo "%s/qwen_asr/__init__.py"\nexit 0\n' % fake_sp)
        py.chmod(0o755)
        with self.assertRaises(independence.AsrIndependenceError) as ctx:
            independence.verify_module_from_own_env(str(py), "qwen_asr")
        self.assertIn("site-packages", str(ctx.exception))

    def test_module_origin_outside_env_root_rejected(self):
        py = Path(self.tmp.name) / "ownenv/bin/python"
        py.parent.mkdir(parents=True)
        outside = Path(self.tmp.name) / "user-site/qwen_asr/__init__.py"
        py.write_text('#!/bin/bash\n[[ "${1:-}" == "-I" ]] && shift\necho "%s"\nexit 0\n' % outside)
        py.chmod(0o755)
        with self.assertRaises(independence.AsrIndependenceError) as ctx:
            independence.verify_module_from_own_env(str(py), "qwen_asr")
        self.assertIn("环境根之外", str(ctx.exception))

    def test_module_origin_own_env_ok(self):
        py = Path(self.tmp.name) / "ownenv/bin/python"
        py.parent.mkdir(parents=True)
        origin = Path(self.tmp.name) / "ownenv/lib/python3.12/site-packages/qwen_asr/__init__.py"
        py.write_text('#!/bin/bash\n[[ "${1:-}" == "-I" ]] && shift\necho "%s"\nexit 0\n' % origin)
        py.chmod(0o755)
        self.assertEqual(
            independence.verify_module_from_own_env(str(py), "qwen_asr"),
            str(origin))


class TestProcessOwnership(AdversarialBase):
    """G/H. pid 归属与健康服务复用拒绝。"""

    def test_stop_refuses_foreign_pid_zero_signals(self):
        self.arm_kill_sentinel()
        # pid 文件指向本测试进程（cmdline 不含 sidecar_server.py）
        port = 18185
        pf = independence.pid_file(port)
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(str(os.getpid()))
        real_health = client.health
        client.health = lambda *a, **kw: None      # 不发真实请求
        try:
            self.assertFalse(client.stop_sidecar(base_url="http://127.0.0.1:%d" % port,
                                                 cfg={}))
        finally:
            client.health = real_health
        self.assertTrue(pf.exists())               # pid 文件保留供排查

    def test_pid_owns_sidecar_requires_cmdline_match(self):
        self.assertFalse(independence.pid_owns_sidecar(os.getpid(), 18085))
        self.assertFalse(independence.pid_owns_sidecar(None, 18085))
        self.assertFalse(independence.pid_owns_sidecar(1, 18085))

    def test_healthy_unattributed_service_not_reused(self):
        # 验收 H：端口健康但无法归属 → 拒绝复用、零拉起
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({"status": "ok", "model": "x", "port":
                                   self.server.server_address[1]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        self.arm_popen_sentinel()
        self.assertFalse(client.ensure_sidecar(cfg={}, base_url=base, verbose=False))
        self.assertEqual(self.popen_calls, [])

    def test_sidecar_server_refuses_ime_port(self):
        # sidecar_server.main 绝不绑定 18081（fail-closed，不是探测）
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys;sys.path.insert(0,%r);import sidecar_server as s;"
             "sys.argv=['x','--port','18081'];sys.exit(s.main())" % str(client.ASR_DIR)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("18081", r.stdout + r.stderr)
        self.assertIn("禁止绑定", r.stdout + r.stderr)


class TestAssetManifest(AdversarialBase):
    """I. 资产固定化接口（版本/来源/SHA256）正反例。"""

    def _make_asset(self):
        root = Path(self.tmp.name) / "assets"
        d = root / "Qwen3-ASR-0.6B"
        d.mkdir(parents=True)
        (d / "model.safetensors").write_bytes(b"WEIGHT-0.6B")
        (d / "config.json").write_text("{}")
        return root, d

    def test_manifest_ok(self):
        root, d = self._make_asset()
        sha = independence.sha256_file(d / "model.safetensors")
        entries = [{"name": "Qwen3-ASR-0.6B", "revision": "rev-1",
                    "source": "https://modelscope.cn/Qwen/Qwen3-ASR-0.6B",
                    "sha256": {"model.safetensors": sha}}]
        self.assertEqual(independence.verify_asset_entry(entries[0], root), str(d))

    def test_manifest_tampered(self):
        root, d = self._make_asset()
        entries = [{"name": "Qwen3-ASR-0.6B", "revision": "rev-1", "source": "x",
                    "sha256": {"model.safetensors": "0" * 64}}]
        with self.assertRaises(independence.AsrIndependenceError) as ctx:
            independence.verify_asset_entry(entries[0], root)
        self.assertIn("哈希不符", str(ctx.exception))


class TestReuseChainDeleted(AdversarialBase):
    """J. 复用链成员已删除（防回归再生）。"""

    def test_no_ime_api_left(self):
        for mod, names in ((client, ["ime_root", "ime_sidecar_alive", "IME_ROOT_DEFAULT"]),
                           (sidecar_server, ["_ime_root", "_ime_sidecar_alive",
                                             "IME_ROOT_DEFAULT"])):
            for n in names:
                self.assertFalse(hasattr(mod, n), "%s.%s 仍存在" % (mod.__name__, n))

    def test_no_ime_health_probe_string(self):
        for f in ("client.py", "sidecar_server.py", "asr_cli.py", "init.sh"):
            src = (Path(client.ASR_DIR) / f).read_text(encoding="utf-8")
            self.assertNotIn("18081/health", src, "%s 仍有 IME health 探测" % f)
            self.assertNotIn(".venv-qwen-asr", src, "%s 仍有 IME venv 复用" % f)
            self.assertNotIn("vendor/models", src, "%s 仍有 IME vendor 模型链" % f)

    def test_model_root_constants_in_sync(self):
        self.assertEqual(Path(sidecar_server.PROJECT_MODEL_ROOT),
                         Path(independence.PROJECT_MODEL_ROOT))

    def test_sidecar_model_chain_has_no_ime(self):
        # sidecar 手动运行时的模型根链只含专属根
        roots = sidecar_server._models_roots()
        self.assertEqual(roots, [str(independence.project_model_root())])
        for r in sidecar_server._models_roots("/tmp/extra"):
            self.assertNotIn("文档", r)


if __name__ == "__main__":
    unittest.main()
