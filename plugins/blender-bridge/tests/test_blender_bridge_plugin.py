#!/usr/bin/env python3
"""tests/test_blender_bridge_plugin.py —— blender-bridge 插件（缝 D finish + validate）测试。

覆盖：
  ① validate 钩子：true / 合法对象 / 非法值（字符串、未知参数、out 绝对路径与
     ..、verify 非布尔）逐形状判；
  ② finish 接线：伪 blender 桩（PATH 前置可执行脚本）验证「键声明 → all 收尾
     自动导出」的子进程命令组装与产物断言；verify=true 追加校验次调用；
  ③ fail-hard：blender 不在 PATH → 错误行明确指向安装；
  ④ 无键恒等：timeline 无 blender_bridge 键 → validate/finish 全空（零行为）；
  ⑤ out 越出项目根拒绝；
  ⑥ 三分支（真 registry）：未装（mock plugin_state）→ validate 报「未安装」。
运行：repo 根 `bash scripts/run_all_tests.sh`；本插件内独立跑：
cd plugins/blender-bridge && env python3 -m unittest discover -s tests -t . -v
（本机 ZCode 环境必须 env python3 前缀：AppImage 劫持 argv[0]，见全局 AGENTS.md）。
"""
import importlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]   # plugins/blender-bridge/tests → repo 根
sys.path.insert(0, str(REPO))

_bb = importlib.import_module("plugins.blender-bridge")

from vtx.pluginlib import hooks as pl_hooks   # noqa: E402


class _FakeCtx:
    """validate/finish 只消费 c.tl / c.root / c.tl_path——鸭子类型上下文。"""

    def __init__(self, tl, root):
        self.tl = tl
        self.root = root
        self.tl_path = root / "timeline.json"


def _tl(root: Path, key_value=...):
    tl = {"version": 2,
          "project": {"fps": 30, "duration_frames": 90, "width": 640, "height": 360},
          "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
    if key_value is not ...:
        tl["blender_bridge"] = key_value
    p = root / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return tl


def _make_fake_blender(d: Path, calls_file: Path):
    """伪 blender：调用记入 calls_file；对末参（json2blend 的 out.blend /
    verify_blend 的 blend 路径）写入内容模拟产物落盘。"""
    fake = d / "blender"
    fake.write_text(
        "#!/bin/bash\n"
        f"echo \"$@\" >> {calls_file}\n"
        "out=\"${@: -1}\"\n"
        "echo \"FAKE BLEND\" > \"$out\"\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


class ValidateShape(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_key_is_noop(self):
        c = _FakeCtx(_tl(self.root), self.root)
        self.assertEqual(_bb.validate(c), [])
        self.assertEqual(_bb.finish(c), [])

    def test_true_passes(self):
        c = _FakeCtx(_tl(self.root, True), self.root)
        self.assertEqual(_bb.validate(c), [])

    def test_bad_shapes_fail(self):
        for bad in ("yes", 42, [], {"out": 1}, {"unknown": 1}, {"verify": "y"},
                    {"out": "/abs/preview.blend"}, {"out": "../esc.blend"}):
            c = _FakeCtx(_tl(self.root, bad), self.root)
            errs = _bb.validate(c)
            self.assertTrue(errs, f"期望形状报错：{bad!r}")

    def test_good_object_passes(self):
        c = _FakeCtx(_tl(self.root, {"out": "preview.blend", "verify": True}), self.root)
        self.assertEqual(_bb.validate(c), [])


class FinishWiring(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.bindir = self.root / "bin"
        self.bindir.mkdir()
        self.calls = self.root / "calls.log"
        self._old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bindir}:{self._old_path}"
        # json2blend/verify_blend 桩：模块级常量 mock（不动盘上仓件）——伪 blender
        # 只按末参落盘，桩文件只需在盘（内容不被执行）
        self._stub = _make_fake_blender(self.bindir, self.calls)
        self._j2b = _bb._JSON2BLEND
        self._ver = _bb._VERIFY
        _bb._JSON2BLEND = self.bindir / "json2blend_stub.py"
        _bb._VERIFY = self.bindir / "verify_stub.py"
        _bb._JSON2BLEND.write_text("# stub\n", encoding="utf-8")
        _bb._VERIFY.write_text("# stub\n", encoding="utf-8")

    def tearDown(self):
        os.environ["PATH"] = self._old_path
        _bb._JSON2BLEND = self._j2b
        _bb._VERIFY = self._ver
        self._tmp.cleanup()

    def test_finish_exports_blend(self):
        c = _FakeCtx(_tl(self.root, True), self.root)
        self.assertEqual(_bb.finish(c), [])
        out = self.root / "preview.blend"
        self.assertTrue(out.is_file(), "伪 blender 应已产出 .blend")
        self.assertIn("--factory-startup", self.calls.read_text())

    def test_finish_verify_flag_appends_second_call(self):
        c = _FakeCtx(_tl(self.root, {"verify": True}), self.root)
        self.assertEqual(_bb.finish(c), [])
        self.assertEqual(len(self.calls.read_text().splitlines()), 2,
                         "verify=true 应追加第二次 blender 调用")

    def test_finish_custom_out(self):
        c = _FakeCtx(_tl(self.root, {"out": "audit/ep42.blend"}), self.root)
        self.assertEqual(_bb.finish(c), [])
        self.assertTrue((self.root / "audit" / "ep42.blend").is_file())

    def test_finish_escape_rejected(self):
        c = _FakeCtx(_tl(self.root, {"out": "../outside.blend"}), self.root)
        errs = _bb.finish(c)
        self.assertTrue(errs and "越出项目根" in errs[0])

    def test_finish_no_blender_fails_hard(self):
        os.environ["PATH"] = str(self.root / "nonexistent")
        c = _FakeCtx(_tl(self.root, True), self.root)
        errs = _bb.finish(c)
        self.assertTrue(errs and "blender 不在 PATH" in errs[0])


class BusIntegration(unittest.TestCase):
    """经 hooks 总线（真 registry active）跑 validate/finish 分发。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_run_validate_dispatch(self):
        c = _FakeCtx(_tl(self.root, "bad-shape"), self.root)
        errs = pl_hooks.run_validate(c)
        self.assertTrue(any("blender_bridge" in e for e in errs))

    def test_run_finish_dispatch_noop_without_key(self):
        c = _FakeCtx(_tl(self.root), self.root)
        self.assertEqual(pl_hooks.run_finish(c), [])

    def test_three_branch_not_installed(self):
        # 键未装 → validate FAIL（三分支：报「未安装」+ 指引）
        from vtx.stages import _scan_plugin_keys
        c = _FakeCtx(_tl(self.root, True), self.root)
        errs = []
        with mock.patch("vtx.pluginlib.registry.plugin_state",
                        return_value="not_installed"):
            _scan_plugin_keys(c, errs)
        self.assertTrue(any("未安装" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
