"""Sandbox helpers; all writable paths (including escape targets) are temporary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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
        return subprocess.run(
            ["env", "python3", str(REPO / "vtx/vtx.py"), "-t", str(self.timeline), *args],
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30)

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
