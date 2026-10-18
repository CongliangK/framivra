#!/usr/bin/env python3
"""client 单测（方案 §9.1：mock sidecar 本地 http.server 随机端口——转写/4xx/超时；
>200s 输入透传不切分断言）。零模型零网络（mock server 绑 127.0.0.1 随机端口）。"""
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import client  # noqa: E402
import independence  # noqa: E402


class MockSidecar:
    """mock sidecar：记录收到的请求体；可切 4xx/慢响应模式。"""

    def __init__(self):
        self.requests = []          # [(method, path, body_dict)]
        self.mode = "ok"            # ok | 4xx | slow
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _read(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {"_raw": raw.decode("utf-8", "replace")}
                outer.requests.append((self.command, self.path, body))
                return body

            def do_GET(self):
                outer.requests.append((self.command, self.path, None))
                body = json.dumps({"status": "ok", "model": "mock", "aligner": None,
                                   "port": self.server.server_address[1], "version": 1},
                                  ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                self._read()
                if outer.mode == "4xx":
                    self._reply(400, {"error": "音频文件不存在：/tmp/x.wav"})
                    return
                if outer.mode == "slow":
                    time.sleep(2.0)
                if outer.mode == "ok":
                    self._reply(200, {"text": "你好世界", "words": [
                        {"w": "你", "s": 0.0, "e": 0.2}, {"w": "好", "s": 0.2, "e": 0.4}],
                        "duration_s": 250.0})
                else:
                    self._reply(200, {"text": "慢响应也回来"})

            def _reply(self, status, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.base_url = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class TestClient(unittest.TestCase):
    def setUp(self):
        self.mock = MockSidecar()
        self.addCleanup(self.mock.close)

    def test_health(self):
        info = client.health(self.mock.base_url, timeout=2.0)
        self.assertIsNotNone(info)
        self.assertEqual(info["status"], "ok")
        self.assertIsNone(client.health("http://127.0.0.1:1", timeout=0.3))

    def test_transcribe_ok(self):
        resp = client.transcribe(self.mock.base_url, "/tmp/a.wav",
                                 timestamps=True, language="Chinese",
                                 cfg={"asr": {"enabled": True}})
        self.assertEqual(resp["text"], "你好世界")
        self.assertEqual(len(resp["words"]), 2)
        method, path, body = self.mock.requests[-1]
        self.assertEqual((method, path), ("POST", "/transcribe"))
        self.assertEqual(body["audio"], "/tmp/a.wav")
        self.assertEqual(body["timestamps"], True)
        self.assertEqual(body["language"], "Chinese")

    def test_4xx_error_path(self):
        self.mock.mode = "4xx"
        with self.assertRaises(client.ClientError) as ctx:
            client.transcribe(self.mock.base_url, "/tmp/missing.wav",
                              cfg={"asr": {"enabled": True}})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("不存在", ctx.exception.message)

    def test_timeout(self):
        self.mock.mode = "slow"
        with self.assertRaises(client.TranscribeTimeout):
            client.transcribe(self.mock.base_url, "/tmp/a.wav", timeout=0.5,
                              cfg={"asr": {"enabled": True}})

    def test_long_audio_passthrough_no_split(self):
        # >200s 输入原样 POST：客户端零切分——单次请求、body 完整（方案 §3.3 P1-1）
        long_input = "/tmp/very_long_interview_250s.wav"
        resp = client.transcribe(self.mock.base_url, long_input, timestamps=True,
                              cfg={"asr": {"enabled": True}})
        posts = [r for r in self.mock.requests if r[0] == "POST" and r[1] == "/transcribe"]
        self.assertEqual(len(posts), 1, "长音频必须单请求透传，不得客户端切分")
        self.assertEqual(posts[0][2]["audio"], long_input)
        self.assertEqual(resp["duration_s"], 250.0)

    def test_ensure_sidecar_already_running_no_spawn(self):
        # 已健康且可归属（本项目 pid 文件 + cmdline）→ 不拉起进程（Popen 必须零调用）
        real_popen = subprocess.Popen
        real_attr = client._sidecar_attribution
        def _no_popen(*a, **kw):
            raise AssertionError("健康状态下不应拉起进程：%s" % (a,))
        subprocess.Popen = _no_popen
        client._sidecar_attribution = lambda base_url: True
        try:
            self.assertTrue(client.ensure_sidecar(
                cfg={"asr": {"enabled": True}},
                base_url=self.mock.base_url, verbose=False))
        finally:
            subprocess.Popen = real_popen
            client._sidecar_attribution = real_attr

    def test_ensure_sidecar_healthy_unattributed_refused(self):
        # issue #141/#140：端口健康但无法归属本项目 → 拒绝复用（零拉起零请求复用）
        real_popen = subprocess.Popen
        def _no_popen(*a, **kw):
            raise AssertionError("无法归属时不应拉起进程：%s" % (a,))
        subprocess.Popen = _no_popen
        try:
            self.assertFalse(client.ensure_sidecar(
                cfg={"asr": {"enabled": True}},
                base_url=self.mock.base_url, verbose=False))
        finally:
            subprocess.Popen = real_popen

    def test_cfg_get(self):
        cfg = {"asr": {"base_url": "http://x:1", "python": None}}
        self.assertEqual(client.cfg_get(cfg, "asr.base_url", "d"), "http://x:1")
        self.assertEqual(client.cfg_get(cfg, "asr.python", "default"), "default")
        self.assertEqual(client.cfg_get(cfg, "asr.model_dir", None), None)
        self.assertEqual(client.cfg_get(cfg, "imgen.key", "缺省"), "缺省")
        self.assertEqual(client.cfg_get({}, "asr.base_url", "d"), "d")
        # 布尔容错："false" 字符串与 false 一致按 False 处理
        self.assertFalse(client.cfg_get({"a": {"b": "false"}}, "a.b", True))
        self.assertTrue(client.cfg_get({"a": {"b": "1"}}, "a.b", False))
        self.assertTrue(client.cfg_get({"a": {"b": True}}, "a.b", False))


if __name__ == "__main__":
    unittest.main()
