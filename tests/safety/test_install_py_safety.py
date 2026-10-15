"""install.py 统一安装器对抗测试(#17/#40 验收面;跨平台,纯临时目录)。

直接子进程调用 [sys.executable, scripts/install.py] 与进程内 monkeypatch
两类手法:
  - 恶意归档矩阵:../、绝对路径、盘符、反斜杠、冒号(ADS)、NTFS 保留名、
    硬链接/设备/FIFO 成员、重名、win32 大小写碰撞、越根符号链接。
  - 事务:升级 rename→.bak→入位;KeyboardInterrupt 中断→目标零污染+staging
    清理;--rollback 回退;并发 install.lock 一胜一拒。
  - win32 物化分支:monkeypatch IS_WINDOWS=True 且 os.symlink 抛错,证明
    win32 路径从不调用 os.symlink 且仓内相对链接物化为副本。
托管 CI runner 是管理员,「真无特权 Win 实机」无法在托管环境复现——该面
由设计保证(win32 分支零 os.symlink 调用)+本单测兜底,如实标注为边界。
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from unittest import mock

try:
    from ._support import REPO, SandboxCase, serve_fixtures, windows_full_path
except ImportError:
    from _support import REPO, SandboxCase, serve_fixtures, windows_full_path

INSTALL_PY = REPO / "scripts" / "install.py"


def _load_installer():
    spec = importlib.util.spec_from_file_location("vt_install_py", INSTALL_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallerSandbox(SandboxCase):
    def setUp(self):
        super().setUp()
        self.bin = self.sandbox / "bin"
        self.fixtures = self.sandbox / "fixtures"
        self.target = self.root / "installed"
        self.bin.mkdir()
        self.fixtures.mkdir()
        self.payload = self.fixtures / "video-tools-9.9.9.tar.gz"
        self.checksums = self.fixtures / "SHA256SUMS"
        # 下载假体 = 本地 http.server + 真实 curl 打 127.0.0.1(PATH 假 curl
        # 是 shebang 脚本,win32 CreateProcess 无法执行会回退真 curl 打外网;
        # 本地方保真全链路:真实下载+SHA256 校验+解包)。每次 GET(含 404)
        # 追加 jsonl 到 SAFETY_CURL_LOG——「零下载」负例断言=日志不存在。
        self._fixtures_stack = contextlib.ExitStack()
        self.addCleanup(self._fixtures_stack.close)
        base_url, self._server_holder = self._fixtures_stack.enter_context(
            serve_fixtures(self.fixtures, self.sandbox / "curl-calls.jsonl"))
        self._server_holder["releases_json"] = b'{"latest":"9.9.9"}'
        # ffmpeg 假体仅 POSIX(sh shim);win32 走注册表合并后的真 ffmpeg
        # (Windows CI 在本套件前 choco install ffmpeg;进程 PATH 可能滞后)。
        if os.name != "nt":
            ffmpeg = self.put(self.bin / "ffmpeg",
                              b"#!/bin/sh\nprintf 'ffmpeg version 6.1 fixture\\n'\n")
            ffmpeg.chmod(0o755)
        self.env.update({"PATH": str(self.bin) + os.pathsep +
                         windows_full_path(self.env["PATH"]),
                         "SAFETY_CURL_LOG": str(self.sandbox / "curl-calls.jsonl"),
                         "VIDEO_TOOLS_DL_BASE": base_url})
        self.base_archive()

    def base_archive(self, extra=(), symlinks=()):
        """官方形制载荷:文件 + 仓内相对符号链接(git archive 语义)。"""
        program = (b'import sys\n'
                   b'from pathlib import Path\n'
                   b'with Path("calls.txt").open("a") as stream:\n'
                   b'    stream.write(sys.argv[1] + "\\n")\n')
        entries = [("vt.py", program), ("VERSION", b"9.9.9\n"),
                   ("vtx/__init__.py", b"# fixture\n")]
        with tarfile.open(self.payload, "w:gz") as archive:
            for name, content in entries + list(extra):
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            for name, linkname in symlinks:
                member = tarfile.TarInfo(name)
                member.type = tarfile.SYMTYPE
                member.linkname = linkname
                archive.addfile(member)
        self.valid_checksum()

    def valid_checksum(self):
        digest = hashlib.sha256(self.payload.read_bytes()).hexdigest()
        self.put(self.checksums, f"{digest}  {self.payload.name}\n".encode())

    def run_install(self, *args, target=None):
        target = self.checked(target or self.target)
        # 安装器已 reconfigure 为 UTF-8 输出;显式同口径解码,Windows 代码页
        # (cp1252/GBK)下 text=True 默认本地解码会错乱/丢输出。
        return subprocess.run(
            [sys.executable, str(INSTALL_PY), "--dir", str(target),
             "--version", "9.9.9", "--no-provenance", *args],
            cwd=self.root, env=self.env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=60)

    def test_official_payload_with_internal_symlinks_installs(self):
        self.base_archive(
            extra=[("apps/tool/run.py", b"print('tool')\n"),
                   ("plugins/tool/run.py", b"print('plugin')\n")],
            symlinks=[("tool-dir", "apps/tool"),
                      ("scripts/tool.py", "../plugins/tool/run.py")])
        result = self.run_install()
        self.assert_ok(result)
        self.assertTrue((self.target / ".vt-install.json").is_file())
        link = self.target / "tool-dir"
        if os.name == "nt":
            self.assertFalse(link.is_symlink())
            self.assertEqual((link / "run.py").read_bytes(), b"print('tool')\n")
        else:
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), "apps/tool")
        script = self.target / "scripts/tool.py"
        self.assertEqual(script.read_bytes(), b"print('plugin')\n")
        if os.name != "nt":
            self.assertEqual(os.readlink(script), "../plugins/tool/run.py")
        for name in ("aroll", "asr", "blender_bridge", "imgen", "matting", "tts"):
            self.assertFalse(os.path.lexists(self.target / name), name)

    # ---- 恶意归档矩阵:每项断言非零退出 + 目标不被标记为成功安装 ----
    def _assert_rejected(self, expect_marker=False):
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0,
                            (result.stdout or "") + (result.stderr or ""))
        self.assertEqual((self.target / ".vt-install.json").exists(), expect_marker)
        self.assert_outside_unchanged()

    def test_absolute_path_member_rejected(self):
        self.base_archive(extra=[("/etc/evil.txt", b"evil")])
        self._assert_rejected()

    def test_drive_letter_member_rejected(self):
        self.base_archive(extra=[("C:/evil.txt", b"evil")])
        self._assert_rejected()

    def test_backslash_member_rejected(self):
        self.base_archive(extra=[("evil\\\\..\\\\evil.txt", b"evil")])
        self._assert_rejected()

    def test_colon_ads_member_rejected(self):
        self.base_archive(extra=[("data.txt:stream", b"evil")])
        self._assert_rejected()

    def test_ntfs_reserved_name_rejected(self):
        self.base_archive(extra=[("con.py", b"evil")])
        self._assert_rejected()

    def test_hardlink_member_rejected(self):
        with tarfile.open(self.payload, "w:gz") as archive:
            member = tarfile.TarInfo("vt.py")
            member.size = 10
            archive.addfile(member, io.BytesIO(b"0123456789"))
            link = tarfile.TarInfo("hardlink")
            link.type = tarfile.LNKTYPE
            link.linkname = "vt.py"
            archive.addfile(link)
        self.valid_checksum()
        self._assert_rejected()

    def test_fifo_member_rejected(self):
        with tarfile.open(self.payload, "w:gz") as archive:
            member = tarfile.TarInfo("vt.py")
            member.size = 10
            archive.addfile(member, io.BytesIO(b"0123456789"))
            fifo = tarfile.TarInfo("pipe")
            fifo.type = tarfile.FIFOTYPE
            archive.addfile(fifo)
        self.valid_checksum()
        self._assert_rejected()

    def test_duplicate_member_rejected(self):
        self.base_archive(extra=[("vt.py", b"duplicate")])
        self._assert_rejected()

    def test_windows_case_collision_rejected(self):
        self.base_archive(extra=[("VTX/__init__.py", b"collision")])
        self._assert_rejected()

    def test_escaping_symlink_rejected(self):
        self.base_archive(symlinks=[("escaped", str(self.outside))])
        self._assert_rejected()

    def test_symlink_dotdot_target_rejected(self):
        self.base_archive(symlinks=[("link", "../../outside")])
        self._assert_rejected()

    # ---- 事务与并发 ----
    def test_upgrade_keeps_backup_then_rollback_restores(self):
        self.put(self.target / ".vt-install.json", json.dumps(
            {"installed_by": "install.py", "version": "9.8.0"}).encode())
        self.put(self.target / "vt.py", b"# old version\n")
        self.put(self.target / "VERSION", b"9.8.0\n")
        (self.target / "vtx").mkdir()
        result = self.run_install("--upgrade")
        self.assert_ok(result)
        self.assertEqual((self.target / "VERSION").read_bytes(), b"9.9.9\n")
        marker = json.loads((self.target / ".vt-install.json").read_text())
        self.assertEqual(marker["prev_version"], "9.8.0")
        self.assertEqual(list(self.target.parent.glob("*.bak-*")), [])
        # 升级成功后备份已清;无备份时 --rollback 报错且不乱动。
        rollback = subprocess.run(
            [sys.executable, str(INSTALL_PY), "--rollback", str(self.target)],
            cwd=self.root, env=self.env, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        self.assertNotEqual(rollback.returncode, 0)
        self.assertEqual((self.target / "VERSION").read_bytes(), b"9.9.9\n")

    def test_upgrade_refused_on_unmarked_checkout(self):
        # git clone(无标记)绝不因 --upgrade 被覆盖。
        self.put(self.target / "vt.py", b"# user git clone\n")
        (self.target / "vtx").mkdir()
        result = self.run_install("--upgrade")
        self.assert_ok(result)  # 复用分支:干净返回 0
        self.assertEqual((self.target / "vt.py").read_bytes(), b"# user git clone\n")

    def test_upgrade_failure_keeps_old_version_via_rollback(self):
        self.put(self.target / ".vt-install.json", json.dumps(
            {"installed_by": "install.py", "version": "9.8.0"}).encode())
        self.put(self.target / "vt.py", b"# old\n")
        (self.target / "vtx").mkdir()
        # 损坏载荷:升级中途失败,旧版仍在或可回退。
        self.put(self.payload, b"not a tar file")
        self.valid_checksum()
        result = self.run_install("--upgrade")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.target / "vt.py").is_file())
        # 模拟「入位前被杀」:手动构造 .bak,验证 rollback 恢复。
        os.rename(self.target, self.target.with_name(self.target.name + ".bak-1"))
        rollback = subprocess.run(
            [sys.executable, str(INSTALL_PY), "--rollback", str(self.target)],
            cwd=self.root, env=self.env, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        self.assert_ok(rollback)
        self.assertEqual((self.target / "vt.py").read_bytes(), b"# old\n")

    def test_interrupt_leaves_target_unpolluted_and_staging_cleaned(self):
        installer = _load_installer()
        # 进程内路径读 os.environ(快照早于本类 setUp 后半段),此处显式补齐:
        # 指向 setUp 起的本地 fixture 服务(真 curl 下载),禁止外网。
        os.environ.update({"VIDEO_TOOLS_DL_BASE":
                           self.env["VIDEO_TOOLS_DL_BASE"],
                           "PATH": self.env["PATH"]})
        args = argparse.Namespace(
            dir=str(self.target), version="9.9.9", chrome=False,
            non_interactive=False, defaults=False, dry_run=False, upgrade=False,
            verify_provenance=False, no_provenance=True)
        original = installer.safe_extract

        def interrupted(payload, destination):
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "partial.txt").write_bytes(b"partial")
            raise KeyboardInterrupt()

        with mock.patch.object(installer, "safe_extract", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                installer.install(args)
        self.assertFalse(self.target.exists())
        self.assertEqual(
            [p.name for p in self.target.parent.glob("*staging*")], [])
        self.assert_outside_unchanged()

    def test_concurrent_install_lock_admits_exactly_one(self):
        installer = _load_installer()
        lock = installer.InstallLock(self.target)
        with lock:
            result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("另一安装正在进行",
                      (result.stdout or "") + (result.stderr or ""))
        self.assertFalse(self.target.exists())
        # 锁释放后安装可正常进行。
        self.assert_ok(self.run_install())

    # ---- win32 物化分支(设计保证:从不调用 os.symlink) ----
    def test_win32_branch_never_calls_symlink_and_materializes(self):
        installer = _load_installer()
        self.base_archive(
            extra=[("apps/tool/run.py", b"print('tool')\n"),
                   ("plugins/tool/run.py", b"print('plugin')\n")],
            symlinks=[("tool-dir", "apps/tool"),
                      ("scripts/tool.py", "../plugins/tool/run.py"), ("dangling", "apps/missing")])
        staging = self.root / "win32-staging"
        with mock.patch.object(installer, "IS_WINDOWS", True), \
                mock.patch.object(installer, "os") as fake_os, \
                mock.patch.object(installer.shutil, "copytree",
                                  wraps=installer.shutil.copytree) as copytree, \
                mock.patch.object(installer.shutil, "copyfile",
                                  wraps=installer.shutil.copyfile) as copyfile:
            fake_os.name = "nt"
            fake_os.symlink.side_effect = AssertionError(
                "win32 分支绝不允许调用 os.symlink")
            with self.assertRaises(installer.InstallError) as caught:
                installer.safe_extract(self.payload, staging)
            # 悬空链接在 win32 无法物化 → fail-closed(而非跳过或调用 symlink)。
            self.assertIn("目标缺失", str(caught.exception))
            # 复制原语确被用于物化(目录树/文件)。
            copytree.assert_called()
            copyfile.assert_called()
        # 去掉悬空链接后:纯仓内链接在 win32 分支完整物化。
        self.base_archive(extra=[("apps/tool/run.py", b"print('tool')\n"),
                   ("plugins/tool/run.py", b"print('plugin')\n")],
                          symlinks=[("tool-dir", "apps/tool"),
                      ("scripts/tool.py", "../plugins/tool/run.py")])
        staging2 = self.root / "win32-staging-2"
        with mock.patch.object(installer, "IS_WINDOWS", True), \
                mock.patch.object(installer, "os") as fake_os:
            fake_os.name = "nt"
            fake_os.symlink.side_effect = AssertionError(
                "win32 分支绝不允许调用 os.symlink")
            installer.safe_extract(self.payload, staging2)
        self.assertFalse((staging2 / "tool-dir").is_symlink())
        self.assertEqual((staging2 / "tool-dir" / "run.py").read_bytes(),
                         b"print('tool')\n")
        script = staging2 / "scripts/tool.py"
        self.assertFalse(script.is_symlink())
        self.assertEqual(script.read_bytes(), b"print('plugin')\n")

    def test_preoccupied_link_destination_is_rejected(self):
        installer = _load_installer()
        self.base_archive(extra=[("plugins/tool/run.py", b"# tool\n")],
                          symlinks=[("scripts/tool.py", "../plugins/tool/run.py")])
        for windows in (False, True):
            with self.subTest(windows=windows):
                staging = self.root / ("occupied-win" if windows else "occupied-posix")
                self.put(staging / "scripts/tool.py", b"user file\n")
                with mock.patch.object(installer, "IS_WINDOWS", windows):
                    with self.assertRaisesRegex(installer.InstallError, "已被占用"):
                        installer.safe_extract(self.payload, staging)
                self.assertEqual((staging / "scripts/tool.py").read_bytes(), b"user file\n")

    def test_doctor_failure_fails_install_gate(self):
        program = (b'import sys\n'
                   b'from pathlib import Path\n'
                   b'with Path("calls.txt").open("a") as s: s.write(sys.argv[1] + "\\n")\n'
                   b'sys.exit(1 if sys.argv[1] == "doctor" else 0)\n')
        self.base_archive(extra=[("vt.py", program)])
        # 用改良载荷替换默认 vt.py 内容:重建 base entries。
        with tarfile.open(self.payload, "w:gz") as archive:
            member = tarfile.TarInfo("vt.py")
            member.size = len(program)
            archive.addfile(member, io.BytesIO(program))
            for name, content in [("VERSION", b"9.9.9\n"), ("vtx/__init__.py", b"# f\n")]:
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        self.valid_checksum()
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("doctor", result.stderr)

    def test_missing_ffmpeg_is_error_not_warning(self):
        # 无 ffmpeg 的 PATH(整段替换,win32 也随之失去 winget/系统 ffmpeg):
        # 失败必须发生在下载之前——零下载请求(本地服务零命中)、目标未创建。
        bare = self.sandbox / "bare-path"
        bare.mkdir()
        self.env["PATH"] = str(bare)
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ffmpeg", result.stderr)
        # 失败发生在下载之前:零 curl 调用、目标未创建。
        self.assertFalse((self.sandbox / "curl-calls.jsonl").exists())
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
