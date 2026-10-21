"""Sandbox helpers; all writable paths (including escape targets) are temporary."""
import contextlib
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

# Importing the production modules must not create source-tree bytecode either.
sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

EXPECT_FIXED = os.environ.get("VTX_SAFETY_EXPECT_FIXED") == "1"
A01_FIXED = unittest.skipUnless(
    EXPECT_FIXED, "待修复 A01: dry-run 必须零写入/零删除; 设置 VTX_SAFETY_EXPECT_FIXED=1")
A03_FIXED = unittest.skipUnless(
    EXPECT_FIXED, "待修复 A03: 路径/符号链接必须限制在白名单根; 设置 VTX_SAFETY_EXPECT_FIXED=1")


class SandboxCase(unittest.TestCase):
    vtx_timeout = 30
    vtx_diagnostics = False
    vtx_traceback_after = None
    vtx_environment_fixture = False

    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory(prefix="vtx-safety-")
        self.addCleanup(self.temporary.cleanup)
        self.sandbox = Path(self.temporary.name).resolve()
        self.assertFalse(self.sandbox.is_relative_to(REPO))
        self.root = self.sandbox / "project"
        self.outside = self.sandbox / "outside"
        self.home = self.sandbox / "home"
        self.work = self.sandbox / "work"
        for p in (self.root, self.outside, self.home, self.work):
            p.mkdir()
        self.env = dict(os.environ)
        for key in tuple(self.env):
            if key.startswith(("VTX_", "VT_", "VIDEO_TOOLS_", "CHROME_")):
                self.env.pop(key)
        self.env.update({
            "HOME": str(self.home), "TMPDIR": str(self.work),
            "TMP": str(self.work), "TEMP": str(self.work),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "XDG_CACHE_HOME": str(self.home / "cache"),
            "XDG_DATA_HOME": str(self.home / "data"),
            "PYTHONDONTWRITEBYTECODE": "1", "VTX_DOCTOR_OFFLINE": "1",
        })
        environment = mock.patch.dict(os.environ, self.env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        # Relative paths in direct production API calls are sandboxed as well.
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.timeline = self.root / "timeline.json"
        self.tl = {
            "version": 2,
            "project": {"fps": 30, "duration_frames": 30, "width": 16, "height": 16},
            "sources": [], "video_tracks": [], "audio_tracks": [], "subtitles": [],
        }
        self.save_timeline()
        self.sentinel = self.put(self.outside / "keep.txt", b"external sentinel")

    def checked(self, path):
        path = Path(path)
        self.assertTrue(path.resolve().is_relative_to(self.sandbox), str(path))
        return path

    def put(self, path, content=b"fixture"):
        path = self.checked(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def save_timeline(self):
        self.put(self.timeline, json.dumps(self.tl).encode())

    def context(self):
        from vtx.infra import Ctx
        c = Ctx(self.timeline)
        c.gpu = False
        c.cache.mkdir(exist_ok=True)
        c.out.mkdir(exist_ok=True)
        self.checked(c.cache)
        self.checked(c.out)
        return c

    def run_vtx(self, *args):
        self.checked(self.timeline)
        command = self._vtx_command(args)
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                cwd=self.root, env=self.env, capture_output=True, text=True,
                timeout=self.vtx_timeout)
        except subprocess.TimeoutExpired as error:
            if self.vtx_diagnostics:
                self._vtx_diagnostic(args, started, "timeout", error.stdout, error.stderr)
            raise  # A timeout still fails; never retry or substitute success.
        if self.vtx_diagnostics:
            self._vtx_diagnostic(args, started, "exit=%s" % result.returncode,
                                 result.stdout if result.returncode else None,
                                 result.stderr if result.returncode else None)
        return result

    def _vtx_command(self, args):
        script = str(REPO / "vtx/vtx.py")
        command = ["env", "python3"]
        if self.vtx_environment_fixture:
            # Explicit dependency isolation for path-only controls. Keep the
            # real interpreter, inherited loader environment, CLI, filesystem
            # and receipt implementation. Never used by renderer acceptance.
            wrapper = (
                "import faulthandler, os, runpy, sys\n"
                "from unittest.mock import patch\n"
                "del sys.argv[0]\n"
                "sys.path[0] = os.path.dirname(os.path.dirname(sys.argv[0]))\n"
                "from tests.gfx._environment import collect, find_chrome\n"
                "after = %r\n"
                "if after is not None: faulthandler.dump_traceback_later(after, repeat=False)\n"
                "try:\n"
                "    with patch('vtx.gfx_environment.collect', side_effect=collect), "
                "patch('vtx.encoders.find_chrome', side_effect=find_chrome):\n"
                "        runpy.run_path(sys.argv[0], run_name='__main__')\n"
                "finally:\n"
                "    faulthandler.cancel_dump_traceback_later()\n"
            ) % self.vtx_traceback_after
            command.extend(["-c", wrapper])
        elif self.vtx_traceback_after is not None:
            # Opt-in diagnostic only: preserve the launcher's argv and script
            # search path, and let SystemExit/exceptions retain their exit code.
            wrapper = (
                "import faulthandler, os, runpy, sys\n"
                "del sys.argv[0]\n"
                "sys.path[0] = os.path.dirname(sys.argv[0])\n"
                "faulthandler.dump_traceback_later(%r, repeat=False)\n"
                "try:\n"
                "    runpy.run_path(sys.argv[0], run_name='__main__')\n"
                "finally:\n"
                "    faulthandler.cancel_dump_traceback_later()\n"
            ) % self.vtx_traceback_after
            command.extend(["-c", wrapper])
        return [*command, script, "-t", str(self.timeline), *args]

    def _vtx_diagnostic(self, args, started, outcome, stdout, stderr):
        print("vtx safety command=%r elapsed=%.3fs timeout=%ss %s" %
              (args, time.monotonic() - started, self.vtx_timeout, outcome),
              file=sys.stderr, flush=True)
        # Only bounded captured output on failure, never environment variables.
        for name, value in (("stdout", stdout), ("stderr", stderr)):
            if outcome != "exit=0":
                if value is None:
                    value = "<not captured>"
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace")
                limit = (16384 if name == "stderr" and self.vtx_traceback_after is not None
                         else 2048)
                print("%s tail=%r%s" % (name, value[-limit:],
                      " [truncated]" if len(value) > limit else ""),
                      file=sys.stderr, flush=True)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_outside_unchanged(self):
        self.assertEqual(self.sentinel.read_bytes(), b"external sentinel")

    def tree(self, root=None):
        """Non-following, byte-exact inventory: catches additions and removals too."""
        root = root or self.root
        result = {}
        for base, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                path = Path(base) / name
                rel = str(path.relative_to(root))
                if path.is_symlink():
                    result[rel] = ("link", os.readlink(path))
                elif path.is_dir():
                    result[rel] = ("dir",)
                else:
                    result[rel] = ("file", path.read_bytes())
        return result


@contextlib.contextmanager
def serve_fixtures(directory, log_path):
    """本地 fixture 下载服务:真实 curl 打 127.0.0.1 下载,全链路(下载+SHA+
    解包)保真——替代 PATH 假 curl(shebang 脚本 win32 CreateProcess 无法
    执行,会回退真 curl 打外网)。每次 GET(含 404)按序追加 jsonl 到
    log_path,保持「零下载」负例断言(日志文件不存在=零请求)。
    /releases.json 由 releases_json 形参内容直接提供;其余按相对路径映射
    directory 内文件。"""
    holder = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server 约定)
            with open(log_path, "a", encoding="utf-8") as stream:
                stream.write(json.dumps({"path": self.path}) + "\n")
            if self.path == "/releases.json":
                body = holder.get("releases_json", b'{"latest":"9.9.9"}')
                status = 200
            else:
                # 版本目录段(/v9.9.9/<name>)按 basename 映射 fixtures 平铺
                # 文件(与原 PATH 假 curl 的文件名匹配语义一致)。
                name = self.path.rsplit("/", 1)[-1]
                target = Path(directory) / (name or "")
                if name and ".." not in name and target.is_file():
                    body, status = target.read_bytes(), 200
                else:
                    body, status = b"not found", 404
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # 静默:测试日志由 jsonl 承担

    class Server(http.server.ThreadingHTTPServer):
        def server_bind(self):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            super().server_bind()

    server = Server(("127.0.0.1", 0), Handler)
    holder["server"] = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_address[1], holder
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
