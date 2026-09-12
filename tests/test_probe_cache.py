"""probe_cache 单元测试（方案五阶段1 性能修复的正确性自证）。

覆盖：温态命中免 ffprobe（进程内 + 落盘跨"进程"）、mtime/size 变更失配回落
真实 ffprobe（不误跳缓存失效）、文件删除返回 None、失败结果不入缓存。
ffprobe 以 monkeypatch subprocess.run 计数模拟，不依赖真实媒体文件。
"""
import json, os, sys, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vtx"))
import probe_cache


class FakeCtx:
    def __init__(self, root):
        self.cache = Path(root) / "cache"


def fake_probe(n="120"):
    """返回 subprocess.run 替身：ffprobe 恒回 nb_frames=n，并计数调用。"""
    calls = {"n": 0}
    def run(cmd, **kw):
        calls["n"] += 1
        return mock.Mock(returncode=0, stdout=n + "\n")
    return run, calls


class TestProbeCache(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.media = self.root / "a.mp4"
        self.media.write_bytes(b"x" * 64)
        self.c = FakeCtx(self.root)
        probe_cache._MEM.clear()
        probe_cache._DISK_LOADED.clear()

    def test_mem_hit_skips_ffprobe(self):
        run, calls = fake_probe()
        with mock.patch.object(probe_cache.subprocess, "run", run):
            self.assertEqual(probe_cache.ffprobe_nb_frames(self.c, self.media), 120)
            self.assertEqual(probe_cache.ffprobe_nb_frames(self.c, self.media), 120)
        self.assertEqual(calls["n"], 1)   # 第二次命中进程内缓存，零 ffprobe

    def test_disk_hit_skips_ffprobe_across_processes(self):
        run, calls = fake_probe()
        with mock.patch.object(probe_cache.subprocess, "run", run):
            self.assertEqual(probe_cache.ffprobe_nb_frames(self.c, self.media), 120)
        # 模拟新进程：清空进程内态，落盘缓存应命中
        probe_cache._MEM.clear()
        probe_cache._DISK_LOADED.clear()
        run2, calls2 = fake_probe()
        with mock.patch.object(probe_cache.subprocess, "run", run2):
            self.assertEqual(probe_cache.ffprobe_nb_frames(self.c, self.media), 120)
        self.assertEqual(calls2["n"], 0)
        disk = self.c.cache / "preview" / ".probe_cache.json"
        self.assertTrue(disk.exists())

    def test_mtime_change_invalidates(self):
        """缓存失效不误跳：内容重写（mtime 变）→ 键失配 → 重新 ffprobe。"""
        run, calls = fake_probe("120")
        with mock.patch.object(probe_cache.subprocess, "run", run):
            self.assertEqual(probe_cache.ffprobe_nb_frames(self.c, self.media), 120)
        self.media.write_bytes(b"y" * 64)   # 新内容，mtime_ns/size 变
        run2, calls2 = fake_probe("127")
        with mock.patch.object(probe_cache.subprocess, "run", run2):
            self.assertEqual(probe_cache.ffprobe_nb_frames(self.c, self.media), 127)
        self.assertEqual(calls2["n"], 1)

    def test_missing_file_returns_none(self):
        self.media.unlink()
        run, calls = fake_probe()
        with mock.patch.object(probe_cache.subprocess, "run", run):
            self.assertIsNone(probe_cache.ffprobe_nb_frames(self.c, self.media))
        self.assertEqual(calls["n"], 0)   # 不存在连 ffprobe 都不必发

    def test_failure_not_cached(self):
        def run(cmd, **kw):
            return mock.Mock(returncode=1, stdout="")
        with mock.patch.object(probe_cache.subprocess, "run", run):
            self.assertIsNone(probe_cache.ffprobe_nb_frames(self.c, self.media))
        probe_cache._MEM.clear(); probe_cache._DISK_LOADED.clear()
        run2, calls2 = fake_probe("99")
        with mock.patch.object(probe_cache.subprocess, "run", run2):
            self.assertEqual(probe_cache.ffprobe_nb_frames(self.c, self.media), 99)
        self.assertEqual(calls2["n"], 1)   # 失败结果未入缓存，下次真实重试


if __name__ == "__main__":
    unittest.main()
