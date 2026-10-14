#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_doctor_requires.py —— doctor 依赖面分级与 requires 浏览器别名解析
（Windows 适配 PR-A1，issue #54 部分）。

覆盖三块：
  ① `_resolve_required_binary`/`_probe_requires`：google-chrome 等浏览器类字面名
     走 find_chrome 解析链（env > PATH > Win 标准路径 > Edge 兜底）——win32 上
     shutil.which 找字面名永假的问题在此关门；非浏览器名仍走 which。
  ② `_doctor_components` 分级：builtin 件 requires 缺二进制 / health 未就绪 →
     ℹ unavailable 不计 FAIL（可选能力面，含 python_packages）；external
     enabled 件 health 失败与 requires 缺失仍 FAIL（严格门不回退）。
  ③ `_doctor_dependencies` + 子进程级：缺 ffmpeg 环境 doctor exit 1 且输出含
     error 行；核心齐备环境 exit 0（optional 件缺失只 warn）。

CPU-only：不触 GPU/NVENC（VTX_NO_NVENC=1；编码器能力读 ffmpeg -encoders 文本
清单，不分配 VRAM）；无需真实 Chrome——浏览器解析用 mock/假文件。
运行：env python3 -m unittest tests.test_doctor_requires -v
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import vt as vt_mod  # noqa: E402


class ResolveRequiredBinary(unittest.TestCase):
    """① 浏览器类名走 find_chrome 解析链；其余名字走 shutil.which。"""

    def test_browser_names_delegate_to_find_chrome(self):
        for name in ("google-chrome", "chromium", "msedge", "chromium-browser"):
            with mock.patch.object(vt_mod, "find_chrome", return_value="/x/chrome"), \
                    mock.patch.object(shutil, "which", return_value="/never") as which:
                self.assertEqual(vt_mod._resolve_required_binary(name), "/x/chrome", name)
                which.assert_not_called()

    def test_non_browser_names_use_which(self):
        with mock.patch.object(vt_mod, "find_chrome", return_value="/x/chrome"), \
                mock.patch.object(shutil, "which", return_value="/usr/bin/ffmpeg"):
            self.assertEqual(vt_mod._resolve_required_binary("ffmpeg"), "/usr/bin/ffmpeg")

    def test_probe_requires_reports_browser_chain_and_missing(self):
        manifest = {"requires": {"binaries": ["google-chrome", "ffmpeg"]}}
        with mock.patch.object(vt_mod, "find_chrome", return_value="/x/chrome"), \
                mock.patch.object(shutil, "which", side_effect=lambda n: None):
            ok, findings, missing = vt_mod._probe_requires(manifest)
            self.assertFalse(ok)
            self.assertEqual(missing, ["binary:ffmpeg"])
            self.assertTrue(any("google-chrome" in f and "/x/chrome" in f
                                and "浏览器解析链" in f for f in findings), findings)
            self.assertTrue(any("ffmpeg" in f and "缺失" in f for f in findings))

    def test_windows_standard_path_and_edge_fallback(self):
        """win32 无 PATH 浏览器：标准安装路径 chrome.exe 命中；仅 Edge 也命中。

        win32 风格路径在 POSIX 测试机上表现为「含反斜杠的单文件名」——
        find_chrome 的 paths 字面量拼接在此语义下命中同一文件。
        """
        def win_env(pf):
            return {"PROGRAMFILES": str(pf),
                    "PROGRAMFILES(X86)": str(pf.parent / "pf86"),
                    "LOCALAPPDATA": str(pf.parent / "la"),
                    "VTX_CHROME": ""}

        # POSIX 上 win32 字面拼接 "<PF>\Google\...\chrome.exe" 是 <tmp> 下的
        # 单个含反斜杠文件名——夹具按同语义落点，命中即证明拼接正确。
        with tempfile.TemporaryDirectory(prefix="vt-win-chrome-") as tmp:
            chrome = Path(tmp) / r"ProgramFiles\Google\Chrome\Application\chrome.exe"
            chrome.write_bytes(b"stub")
            pf = Path(tmp) / "ProgramFiles"
            with mock.patch.object(vt_mod, "OS", "win32"), \
                    mock.patch.dict(os.environ, win_env(pf), clear=False), \
                    mock.patch.object(shutil, "which", side_effect=lambda n: None):
                found = vt_mod.find_chrome()
                self.assertIsNotNone(found)
                self.assertEqual(Path(found), chrome)
                ok, _f, missing = vt_mod._probe_requires(
                    {"requires": {"binaries": ["google-chrome"]}})
                self.assertTrue(ok, missing)
        with tempfile.TemporaryDirectory(prefix="vt-win-edge-") as tmp2:
            edge = Path(tmp2) / r"ProgramFiles\Microsoft\Edge\Application\msedge.exe"
            edge.write_bytes(b"stub")
            pf2 = Path(tmp2) / "ProgramFiles"
            with mock.patch.object(vt_mod, "OS", "win32"), \
                    mock.patch.dict(os.environ, win_env(pf2), clear=False), \
                    mock.patch.object(shutil, "which", side_effect=lambda n: None):
                found = vt_mod.find_chrome()
                self.assertIsNotNone(found)
                self.assertEqual(Path(found), edge)
                ok, _f, missing = vt_mod._probe_requires(
                    {"requires": {"binaries": ["google-chrome"]}})
                self.assertTrue(ok, missing)


def _builtin_entry(source="builtin", binaries=("blender",), health=("cmd", 0)):
    """受控组件 entry：manifest 声明 binaries；health 型可注入。"""
    return {"name": "controlled", "kind": "plugin", "source": source,
            "dir": Path("."), "enabled": True, "manifest_errors": [],
            "manifest": {"requires": {"binaries": list(binaries),
                                      "python_packages": [], "platforms": []},
                         "health": {"type": "command", "command": "health.py"}}}


class ComponentGrading(unittest.TestCase):
    """② builtin 降 ℹ / external 严格 / python_packages 仍 FAIL。"""

    def _components(self, entry, which_map=None, health=(True, "ok")):
        which_default = lambda n: which_map.get(n) if which_map else None
        with mock.patch.object(vt_mod._preg, "discover", return_value=[entry]), \
                mock.patch.object(vt_mod._preg, "declared_plugins", return_value={}), \
                mock.patch.object(vt_mod, "_probe_health", return_value=health), \
                mock.patch.object(shutil, "which", side_effect=which_default):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                return vt_mod._doctor_components(json_out=False), out.getvalue()

    def test_builtin_missing_binary_is_unavailable_not_fail(self):
        out, text = self._components(_builtin_entry(), which_map={})
        e = out["entries"][0]
        self.assertFalse(e["fail"], e)
        self.assertEqual(e["unavailable"], ["缺 blender"])
        self.assertFalse(out["fail"])
        self.assertIn("unavailable（缺 blender）", text)

    def test_builtin_health_unready_is_unavailable_not_fail(self):
        # requires 齐备（ffmpeg 在）但 health 探针 unhealthy（如 bgm-render 缺
        # music_tools CLI、denoise 缺 conda 权重）——内置可选能力面，降 ℹ。
        out, _ = self._components(_builtin_entry(binaries=("ffmpeg",), source="builtin"),
                                  which_map={"ffmpeg": "/usr/bin/ffmpeg"},
                                  health=(False, "[MISS] 可选件未装"))
        e = out["entries"][0]
        self.assertFalse(e["fail"], e)
        self.assertIn("health 未就绪", e["unavailable"])
        self.assertFalse(out["fail"])

    def test_builtin_missing_python_package_is_unavailable_not_fail(self):
        # hosted windows-2025 新 shell 实测：裸 Python 无 numpy/requests 属正常
        # 安装态（vtx 内核纯 stdlib）；builtin 能力面缺声明依赖与缺 blender
        # 同语义——ℹ unavailable，不计 FAIL。external 件不回退（见下）。
        entry = _builtin_entry(binaries=())
        entry["manifest"]["requires"]["python_packages"] = ["numpy"]
        with mock.patch.object(vt_mod._preg, "discover", return_value=[entry]), \
                mock.patch.object(vt_mod._preg, "declared_plugins", return_value={}), \
                mock.patch.object(vt_mod, "_probe_health", return_value=(True, "ok")), \
                mock.patch.object(vt_mod.importlib.util, "find_spec", return_value=None):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                result = vt_mod._doctor_components(json_out=True)
        e = result["entries"][0]
        self.assertFalse(e["fail"], e)
        self.assertIn("缺 numpy", e["unavailable"])
        self.assertFalse(result["fail"])

    def test_external_enabled_missing_package_still_fails(self):
        entry = _builtin_entry(source="external", binaries=())
        entry["manifest"]["requires"]["python_packages"] = ["numpy"]
        with mock.patch.object(vt_mod._preg, "discover", return_value=[entry]), \
                mock.patch.object(vt_mod._preg, "declared_plugins", return_value={}), \
                mock.patch.object(vt_mod, "_probe_health", return_value=(True, "ok")), \
                mock.patch.object(vt_mod.importlib.util, "find_spec", return_value=None):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                result = vt_mod._doctor_components(json_out=True)
        self.assertTrue(result["entries"][0]["fail"], result["entries"][0])
        self.assertTrue(result["fail"])

    def test_external_enabled_health_failure_still_fails(self):
        out, _ = self._components(_builtin_entry(source="external"),
                                  which_map={"blender": "/usr/bin/blender"},
                                  health=(False, "exit 1"))
        e = out["entries"][0]
        self.assertTrue(e["fail"], e)
        self.assertTrue(out["fail"])


class DependencyGrading(unittest.TestCase):
    """③ 依赖面 error/warn 分级（in-process，全 mock，不依赖本机工具）。"""

    def _deps(self, which_map, chrome="/x/chrome", encoders="libfdk_aac h264_nvenc"):
        with mock.patch.object(shutil, "which",
                               side_effect=lambda n: which_map.get(n)), \
                mock.patch.object(vt_mod, "find_chrome", return_value=chrome), \
                mock.patch.object(vt_mod, "ffmpeg_encoders", return_value=encoders):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                return vt_mod._doctor_dependencies(json_out=False), buf.getvalue()

    def test_healthy_core_has_no_errors(self):
        out, text = self._deps({"ffmpeg": "/f", "ffprobe": "/fp",
                                "blender": "/b", "node": "/n", "sox": "/s"})
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["warns"], [])
        self.assertIn("✓ ffmpeg", text)

    def test_missing_ffmpeg_ffprobe_are_errors_with_hint(self):
        out, text = self._deps({"blender": "/b", "node": "/n", "sox": "/s"},
                               chrome="/x/chrome")
        self.assertTrue(out["errors"])
        self.assertTrue(any("ffmpeg 缺失" in e for e in out["errors"]), out["errors"])
        self.assertTrue(any("ffprobe 缺失" in e for e in out["errors"]), out["errors"])
        self.assertFalse(out["items"]["ffmpeg"]["ok"])
        self.assertIn("✗ ffmpeg 缺失", text)
        # ffmpeg 缺失时不重复报编码器 warn（error 已覆盖根因）
        self.assertFalse(any("libfdk" in w for w in out["warns"]), out["warns"])

    def test_browser_all_missing_is_error(self):
        out, _ = self._deps({"ffmpeg": "/f", "ffprobe": "/fp"}, chrome=None)
        self.assertTrue(any("浏览器缺失" in e for e in out["errors"]), out["errors"])

    def test_optional_tools_only_warn(self):
        out, _ = self._deps({"ffmpeg": "/f", "ffprobe": "/fp"}, chrome="/x/chrome",
                            encoders="")
        self.assertEqual(out["errors"], [])
        for frag in ("blender 缺失", "node 缺失", "sox 缺失",
                     "libfdk_aac 缺失", "h264_nvenc 缺失"):
            self.assertTrue(any(frag in w for w in out["warns"]), (frag, out["warns"]))


class MissingFfmpegSubprocess(unittest.TestCase):
    """③ 子进程级：构造缺 ffmpeg/ffprobe（核心齐备）环境 → doctor exit 1 含 error 行。

    PATH 只含 env/bash/git/node/google-chrome/python3——组件段 builtin 降 ℹ，
    exit 1 必须来自依赖面 error（error ⇔ exit 1 的合同验证）。
    """

    @staticmethod
    def _env(root: Path, include_ffmpeg: bool):
        tools = root / ("bin-full" if include_ffmpeg else "bin-noff")
        tools.mkdir()
        names = ["env", "bash", "git", "node", "google-chrome"]
        if include_ffmpeg:
            names += ["ffmpeg", "ffprobe"]
        for name in names:
            real = shutil.which(name)
            assert real, f"fixture 需要真实 {name}"
            (tools / name).symlink_to(real)
        (tools / "python3").symlink_to(sys.executable)
        home = root / "home"
        home.mkdir(exist_ok=True)
        (home / ".config").mkdir(exist_ok=True)
        cfg = root / "config.json"
        cfg.write_text("{}", encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("VTX_", "VT_", "BGM_", "RESEMBLE_"))
               and k not in ("PYTHONPATH", "PYTHONHOME")}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   PATH=str(tools), VTX_CONFIG=str(cfg), VTX_DOCTOR_OFFLINE="1",
                   PYTHONNOUSERSITE="1", VT_MOUNT_ROOT=str(home / "video"),
                   VTX_NO_NVENC="1", VTX_SEG_NVENC="0")
        return env

    def test_doctor_fails_closed_without_ffmpeg(self):
        with tempfile.TemporaryDirectory(prefix="vt-noffmpeg-") as tmp:
            root = Path(tmp)
            env = self._env(root, include_ffmpeg=False)
            r = subprocess.run(["env", "python3", str(REPO / "vt.py"), "doctor", "--json"],
                               capture_output=True, text=True, cwd=str(REPO), env=env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            data = json.loads(r.stdout)
            errs = data["dependencies"]["errors"]
            self.assertTrue(any("ffmpeg 缺失" in e for e in errs), errs)
            self.assertTrue(any("ffprobe 缺失" in e for e in errs), errs)
            # 组件段 builtin 全降 ℹ：exit 1 只能来自依赖面（合同分离验证）
            self.assertFalse(data["components"]["fail"], data["components"])
            for e in data["components"]["entries"]:
                self.assertFalse(e["fail"], e)
            # 人类可读面：error 行 + 结论行
            r2 = subprocess.run(["env", "python3", str(REPO / "vt.py"), "doctor"],
                                capture_output=True, text=True, cwd=str(REPO), env=env)
            self.assertEqual(r2.returncode, 1, r2.stdout + r2.stderr)
            self.assertIn("✗ ffmpeg 缺失", r2.stdout)
            self.assertIn("依赖面存在 error", r2.stdout)

    def test_doctor_passes_with_core_present(self):
        with tempfile.TemporaryDirectory(prefix="vt-full-") as tmp:
            root = Path(tmp)
            env = self._env(root, include_ffmpeg=True)
            r = subprocess.run(["env", "python3", str(REPO / "vt.py"), "doctor", "--json"],
                               capture_output=True, text=True, cwd=str(REPO), env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            data = json.loads(r.stdout)
            self.assertEqual(data["dependencies"]["errors"], [])
            # blender 缺失只 warn；对应 builtin 件 ℹ unavailable 而非 FAIL
            self.assertTrue(any("blender 缺失" in w
                                for w in data["dependencies"]["warns"]))
            by_name = {(e["kind"], e["name"]): e for e in data["components"]["entries"]}
            bb = by_name[("plugin", "blender-bridge")]
            self.assertFalse(bb["fail"], bb)
            self.assertIn("缺 blender", bb.get("unavailable", []))
            self.assertIn("② 依赖面", subprocess.run(
                ["env", "python3", str(REPO / "vt.py"), "doctor"],
                capture_output=True, text=True, cwd=str(REPO), env=env).stdout)


if __name__ == "__main__":
    unittest.main()
