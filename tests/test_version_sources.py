#!/usr/bin/env python3
"""tests/test_version_sources.py —— 版本源逻辑与 wheel 布局冒烟（B01/F-03/#25；关联 F-07/#7 版本部分）。

vtx._read_version 回退链（B01 起）：
  repo 根 VERSION → 包内 VERSION → pyproject version 行 → importlib.metadata
  （已安装发行包）→ 内置字面量 _FALLBACK_VERSION（与三源同步，改一必三）。

覆盖：
  ① 三源一致：VERSION == pyproject version == vtx.__version__（运行时值）==
     _FALLBACK_VERSION（F-03 回归锚：旧字面量 2.0.0 曾与 pyproject 2.0.1 漂移）；
  ② 回退优先级（临时布局 + here 注入 + metadata mock）：根 VERSION > 包内
     VERSION > pyproject（行首零缩进口径）> importlib.metadata > 字面量；
  ③ wheel 安装布局冒烟（C03 前置）：临时 site-packages = vtx 包源码副本（无根
     VERSION/pyproject——即 wheel 内容面）+ framivra-<v>.dist-info/METADATA，
     子进程 import 断言 __version__ 取 metadata 值；无 dist-info 时回落字面量
     （== 三源值）。真正 venv + pip install wheel 冒烟 = C03 工单，此处不建
     venv 不产 wheel，只落版本源逻辑与布局校验。
  ④ L6 门本身：python3 scripts/docs/verify_version_sync.py exit 0（本机未装
     framivra → ④ metadata skip 行；已装且同步 → OK 行）。

运行：bash scripts/run_all_tests.sh；或独立跑
  python3 -m unittest tests.test_version_sources
  python3 tests/test_version_sources.py
子进程一律 env python3（argv[0] 劫持纪律，同 test_vt_entry.py）。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # 独立跑（python3 tests/test_version_sources.py）时锚定仓根
    sys.path.insert(0, str(REPO))

import vtx  # noqa: E402

PYPROJECT_VERSION_RE = re.compile(r'(?m)^version\s*=\s*"([^"]*)"\s*$')


def _repo_version():
    return (REPO / "VERSION").read_text(encoding="utf-8").strip()


def _repo_pyproject_version():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    m = PYPROJECT_VERSION_RE.search(text)
    assert m, "pyproject.toml version line missing"
    return m.group(1).strip()


def _repo_pyproject_name():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^name\s*=\s*"([^"]*)"$', text)
    assert m, "pyproject.toml name line missing"
    return m.group(1).strip()


def _make_layout(tmp, *, root_version=None, pkg_version=None, pyproject=None):
    """临时版本源布局：tmp/proj/vtx 为包目录（_read_version 的 here 注入点）。"""
    pkg = Path(tmp) / "proj" / "vtx"
    pkg.mkdir(parents=True, exist_ok=True)
    if root_version is not None:
        (pkg.parent / "VERSION").write_text(root_version, encoding="utf-8")
    if pkg_version is not None:
        (pkg / "VERSION").write_text(pkg_version, encoding="utf-8")
    if pyproject is not None:
        (pkg.parent / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return pkg


class VersionSourcesTest(unittest.TestCase):
    """① 三源一致与 ② 回退链优先级（纯进程内，毫秒级）。"""

    def _read(self, pkg, metadata_version):
        with mock.patch.object(vtx, "_version_from_metadata",
                               return_value=metadata_version):
            return vtx._read_version(here=pkg)

    def test_three_sources_and_fallback_literal_in_sync(self):
        version_ok = _repo_version()
        self.assertTrue(version_ok)
        self.assertEqual(_repo_pyproject_version(), version_ok)
        self.assertEqual(vtx.__version__, version_ok)  # 运行时值（repo 根 VERSION 命中）
        self.assertEqual(vtx._FALLBACK_VERSION, version_ok)  # F-03 回归锚

    def test_root_version_beats_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = _make_layout(tmp, root_version="7.7.7", pkg_version="7.7.8",
                               pyproject='version = "6.6.6"\n')
            self.assertEqual(self._read(pkg, "5.5.5"), "7.7.7")

    def test_package_version_beats_pyproject(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = _make_layout(tmp, pkg_version="7.7.8", pyproject='version = "6.6.6"\n')
            self.assertEqual(self._read(pkg, "5.5.5"), "7.7.8")

    def test_pyproject_beats_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = _make_layout(tmp, pyproject='version = "6.6.6"\n')
            self.assertEqual(self._read(pkg, "5.5.5"), "6.6.6")

    def test_pyproject_zero_indent_only(self):
        # [tool.*] 表内缩进 version 行不算 PEP 621 project.version（L6 同口径）
        with tempfile.TemporaryDirectory() as tmp:
            pkg = _make_layout(tmp, pyproject='[tool.demo]\n  version = "9.9.9"\n')
            self.assertEqual(self._read(pkg, "5.5.5"), "5.5.5")

    def test_metadata_fallback_when_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = _make_layout(tmp)
            self.assertEqual(self._read(pkg, "5.5.5"), "5.5.5")

    def test_literal_fallback_synced_to_repo(self):
        # wheel 无 VERSION 无 dist-info 的兜底：字面量必须 == 三源值（F-03 场景）
        with tempfile.TemporaryDirectory() as tmp:
            pkg = _make_layout(tmp)
            self.assertEqual(self._read(pkg, ""), _repo_version())

    def test_metadata_not_installed_returns_empty(self):
        # 本机未装 framivra（常态）→ 空串；已装 → 与三源同值（L6 门 ④ 守护）
        value = vtx._version_from_metadata()
        self.assertIn(value, ("", _repo_version()))


class WheelLayoutSmokeTest(unittest.TestCase):
    """③ wheel 安装布局冒烟（C03 前置）：临时 site-packages + 子进程 import。

    布局 = vtx 包源码副本（源树本就无 vtx/VERSION，即 wheel 内容面）± 假
    framivra-<v>.dist-info/METADATA；cwd 离开仓根，杜绝 repo VERSION/pyproject
    旁路，只剩回退链后三级可命中。
    """

    def _site(self, tmp, dist_version):
        site = Path(tmp) / "site"
        shutil.copytree(REPO / "vtx", site / "vtx",
                        ignore=shutil.ignore_patterns("__pycache__"))
        if dist_version is not None:
            dist_info = site / ("framivra-%s.dist-info" % dist_version)
            dist_info.mkdir()
            (dist_info / "METADATA").write_text(
                "Metadata-Version: 2.1\nName: framivra\nVersion: %s\n" % dist_version,
                encoding="utf-8")
        return site

    def _import_version(self, site):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(site)
        env["PYTHONNOUSERSITE"] = "1"  # 隔绝真机 user-site 的同名发行包
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            ["env", "python3", "-c", "import vtx; print(vtx.__version__)"],
            capture_output=True, text=True,
            cwd=str(site.parent), env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        self.assertTrue(lines, "empty output")
        return lines[-1].strip()

    def test_wheel_layout_reads_dist_metadata(self):
        # dist-info 在位（pip 正常安装态）→ __version__ == metadata 版本 == 三源值
        version_ok = _repo_version()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._import_version(self._site(tmp, version_ok)), version_ok)

    def test_wheel_layout_without_dist_info_falls_back_to_literal(self):
        # F-03/#25 回归锚：wheel 未含 VERSION 且无 dist-info 时，旧码报漂移
        # 字面量 2.0.0；现须回落与三源同步的 _FALLBACK_VERSION。
        version_ok = _repo_version()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._import_version(self._site(tmp, None)), version_ok)


class GateScriptTest(unittest.TestCase):
    """④ L6 门冒烟：扩展后的 verify_version_sync.py 在仓内必须 exit 0。"""

    def test_l6_version_sync_gate_passes(self):
        proc = subprocess.run(
            ["env", "python3", str(REPO / "scripts/docs/verify_version_sync.py")],
            capture_output=True, text=True, cwd=str(REPO), timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("L6 version sync: VERSION==pyproject==vtx ==", proc.stdout)


class GateAdversarialTest(unittest.TestCase):
    """⑤ L6 门对抗面（B01 红队复审）：篡改/删除各版本源 → 门必须相应 FAIL，
    伪造 dist-info → ④ 必须命中；查询名双源（pyproject name vs vtx._DIST_NAME）
    漂移必须 FAIL（堵「改 name 行让已装陈旧 dist 被 ④ 静默 skip」的绕过）。"""

    def _layout(self, tmp):
        root = Path(tmp)
        shutil.copytree(REPO / "vtx", root / "vtx",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (root / "scripts/docs").mkdir(parents=True)
        shutil.copy2(REPO / "scripts/docs/verify_version_sync.py", root / "scripts/docs/")
        shutil.copy2(REPO / "pyproject.toml", root)
        shutil.copy2(REPO / "VERSION", root)
        return root

    def _gate(self, root, pythonpath=None):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
        if pythonpath is not None:
            env["PYTHONPATH"] = str(pythonpath)
        return subprocess.run(
            ["env", "python3", str(root / "scripts/docs/verify_version_sync.py")],
            capture_output=True, text=True, cwd=str(root), env=env, timeout=120)

    def _runtime_version(self, root):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
        proc = subprocess.run(["env", "python3", "-c", "import vtx; print(vtx.__version__)"],
                              capture_output=True, text=True, cwd=str(root), env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip().splitlines()[-1]

    def _forge_dist(self, root, version, name="framivra"):
        site = root / "site"
        dist = site / ("%s-%s.dist-info" % (name, version))
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: %s\nVersion: %s\n" % (name, version),
            encoding="utf-8")
        return site

    def test_baseline_copy_passses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._layout(tmp)
            proc = self._gate(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("VERSION==pyproject==vtx ==", proc.stdout)

    def test_deleted_version_fails_but_runtime_falls_back_to_pyproject(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._layout(tmp)
            (root / "VERSION").unlink()
            proc = self._gate(root)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("VERSION unreadable", proc.stdout)
            self.assertEqual(self._runtime_version(root), _repo_pyproject_version())

    def test_tampered_pyproject_version_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._layout(tmp)
            text = (root / "pyproject.toml").read_text(encoding="utf-8")
            (root / "pyproject.toml").write_text(
                text.replace('version = "%s"' % _repo_version(), 'version = "9.9.9"'),
                encoding="utf-8")
            proc = self._gate(root)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("pyproject=9.9.9", proc.stdout)

    def test_empty_version_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._layout(tmp)
            (root / "VERSION").write_text("", encoding="utf-8")
            proc = self._gate(root)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("VERSION empty", proc.stdout)

    def test_forged_stale_dist_info_fails_source_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._layout(tmp)
            site = self._forge_dist(root, "0.0.1")
            proc = self._gate(root, pythonpath=site)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("importlib.metadata(framivra)=0.0.1", proc.stdout)

    def test_forged_matching_dist_info_passses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._layout(tmp)
            site = self._forge_dist(root, _repo_version())
            proc = self._gate(root, pythonpath=site)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("importlib.metadata(framivra) == %s OK" % _repo_version(),
                          proc.stdout)

    def test_tampered_name_line_cannot_skip_source_four(self):
        # B01 红队实证绕过的回归锚：改 name 行曾使 ④ 查询名漂移 → 陈旧 dist
        # 被静默 skip（rc0）；现须 FAIL 于 dist name diverges
        with tempfile.TemporaryDirectory() as tmp:
            root = self._layout(tmp)
            site = self._forge_dist(root, "0.0.1")
            path = root / "pyproject.toml"
            path.write_text(path.read_text(encoding="utf-8")
                            .replace('name = "framivra"', 'name = "framivra-evil"'),
                            encoding="utf-8")
            proc = self._gate(root, pythonpath=site)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("dist name diverges", proc.stdout)
            self.assertIn("framivra-evil", proc.stdout)

    def test_missing_name_line_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._layout(tmp)
            path = root / "pyproject.toml"
            path.write_text("\n".join(
                ln for ln in path.read_text(encoding="utf-8").splitlines()
                if not ln.startswith('name = "')), encoding="utf-8")
            proc = self._gate(root)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("name line not found", proc.stdout)

    def test_runtime_reports_pinned_dist_name(self):
        # ③ 的运行时锚必须可打印（缺 _DIST_NAME 常量会让门 fail-closed 报错）
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            ["env", "python3", "-c", "import vtx; print(vtx._DIST_NAME)"],
            capture_output=True, text=True, cwd=str(REPO), env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), _repo_pyproject_name())


if __name__ == "__main__":
    unittest.main(verbosity=2)
