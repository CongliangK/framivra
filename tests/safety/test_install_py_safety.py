"""install.py 统一安装器对抗测试(#17/#40 验收面;跨平台,纯临时目录)。

子进程通过 sys.executable 调用测试入口(仅选择跳过 Windows shim/用户 PATH
集成),安装器其余逻辑保持真实;另有进程内 monkeypatch。两类手法:
  - 恶意归档矩阵:../、绝对路径、盘符、反斜杠、冒号(ADS)、NTFS 保留名、
    硬链接/设备/FIFO 成员、重名、win32 大小写碰撞、越根符号链接。
  - 事务:升级 rename→.bak→入位;KeyboardInterrupt 中断→目标零污染+staging
    清理;--rollback 回退;并发 install.lock 一胜一拒。
  - win32 物化分支:monkeypatch IS_WINDOWS=True 且 os.symlink 抛错,证明
    win32 路径从不调用 os.symlink 且仓内相对链接物化为副本。
托管 CI runner 是管理员,「真无特权 Win 实机」无法在托管环境复现——该面
由设计保证(win32 分支零 os.symlink 调用)+本单测兜底,如实标注为边界。
本套件的 FFmpeg、vt.py init/doctor 是安全分支前置 fixture,不验收真实媒体
能力;真实载荷/FFmpeg/注册表/新 shell doctor 由 CI 后续独立候选安装步骤验收。
PATH 仅含临时 fixture,不借用主机 FFmpeg/winget。Windows 用原生 .cmd,
不把 POSIX shebang 当作 CreateProcess 可执行文件;curl 仅访问回环载荷服务。
"""
import argparse
import ast
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock

try:
    from ._support import REPO, SandboxCase, serve_fixtures
    from . import _install_fixture
except ImportError:
    from _support import REPO, SandboxCase, serve_fixtures
    import _install_fixture

_load_installer = _install_fixture.load_installer
INSTALL_PY = REPO / "scripts" / "install.py"
INSTALL_FIXTURE = Path(__file__).with_name("_install_fixture.py")


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
        self.ffmpeg, dependency_env = _install_fixture.dependency_fixtures(self.bin, self.home)
        self.env.update(dependency_env)
        self.env.update({"SAFETY_CURL_LOG": str(self.sandbox / "curl-calls.jsonl"),
                         "VIDEO_TOOLS_DL_BASE": base_url})
        # 进程内调用也使用同一受控 PATH/回环源,不能被 Linux 主机 ffmpeg 掩盖。
        os.environ.update({key: self.env[key] for key in (
            "PATH", "CURL_HOME", "NO_PROXY", "no_proxy", "VIDEO_TOOLS_DL_BASE")})
        self.base_archive()

    def command_fixture(self, name, posix, windows, directory=None):
        directory = directory or self.bin
        if os.name == "nt":
            return self.put(directory / (name + ".cmd"), windows.encode("utf-8"))
        command = self.put(directory / name, ("#!/bin/sh\n" + posix).encode("utf-8"))
        command.chmod(0o755)
        return command

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

    def run_install(self, *args, target=None, entrypoint=INSTALL_FIXTURE):
        target = self.checked(target or self.target)
        # 安装器已 reconfigure 为 UTF-8 输出;显式同口径解码,Windows 代码页
        # (cp1252/GBK)下 text=True 默认本地解码会错乱/丢输出。
        return subprocess.run(
            [sys.executable, str(entrypoint), "--dir", str(target),
             "--version", "9.9.9", "--non-interactive", "--no-provenance", *args],
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
        self.assertEqual((self.target / "calls.txt").read_text().splitlines(),
                         ["init", "doctor"])
        # 临时安全 fixture 不写宿主注册表,也不冒充 shim 集成验收。
        self.assertFalse((self.target / "bin").exists())
        if os.name == "nt":
            self.assertIn("已跳过 shim 与用户 PATH 写入", result.stdout)
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
        # 必须抵达原来的归档安全分支,不能因缺 FFmpeg/curl 而假绿。
        self.assertRegex(result.stderr, "归档成员|符号链接目标越出载荷根")
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
        self.assertIn("载荷不是有效的 tar.gz 归档", result.stderr)
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
        args = argparse.Namespace(
            dir=str(self.target), version="9.9.9", chrome=False,
            non_interactive=False, defaults=False, dry_run=False, upgrade=False,
            verify_provenance=False, no_provenance=True)
        def interrupted(payload, destination):
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "partial.txt").write_bytes(b"partial")
            raise KeyboardInterrupt()

        with mock.patch.object(installer, "safe_extract", side_effect=interrupted) as extract:
            with self.assertRaises(KeyboardInterrupt):
                installer.install(args)
            extract.assert_called_once()
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
        self.assertFalse((self.sandbox / "curl-calls.jsonl").exists())
        # 锁释放后安装可正常进行。
        self.assert_ok(self.run_install())
        self.assertEqual((self.target / "calls.txt").read_text().splitlines(),
                         ["init", "doctor"])

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
        self.assertEqual((self.target / "calls.txt").read_text().splitlines(),
                         ["init", "doctor"])

    def test_child_fixture_uses_real_shim_skip_without_registry(self):
        installer = _load_installer()
        wizard = installer.Wizard(enabled=False)
        wizard.ask("shims_path", "fixture", default=True)

        def exercise_shims(argv):
            self.assertEqual(argv, ["--non-interactive"])
            installer.write_shims(self.target, wizard)
            return 0

        output = io.StringIO()
        with mock.patch.object(_install_fixture, "load_installer", return_value=installer), \
                mock.patch.object(installer, "IS_WINDOWS", True), \
                mock.patch.object(installer, "main", side_effect=exercise_shims), \
                mock.patch.dict(sys.modules, {"winreg": None}), \
                contextlib.redirect_stdout(output):
            self.assertEqual(_install_fixture.main(["--non-interactive"]), 0)
        self.assertIs(wizard.choices["shims_path"], False)
        self.assertIn("已跳过 shim 与用户 PATH 写入", output.getvalue())
        self.assertFalse(self.target.exists())

    def test_ffmpeg_fixture_is_used_without_host_tools(self):
        self.assertEqual(self.env["PATH"], str(self.bin))
        self.assertEqual(Path(shutil.which("ffmpeg")), self.ffmpeg)
        self.assertIsNone(shutil.which("winget"))
        result = subprocess.run(
            [str(self.ffmpeg), "-version"], cwd=self.root, env=self.env,
            capture_output=True, text=True, timeout=20)
        self.assert_ok(result)
        self.assertEqual(result.stdout.strip(), "ffmpeg version 6.1 fixture")
        installer = _load_installer()
        # 不 patch _ffmpeg_major/plan/enforce:真实探测必须命中前置 fixture。
        for windows in (False, True):
            with self.subTest(windows=windows), \
                    mock.patch.object(installer, "IS_WINDOWS", windows), \
                    mock.patch.object(installer, "_winget_install",
                                      side_effect=AssertionError("winget forbidden")) as winget:
                wizard = installer.Wizard(enabled=False)
                plan = installer.plan_ffmpeg(wizard)
                self.assertEqual(plan, {"major": 6, "asked_winget": False})
                installer.enforce_ffmpeg(wizard, plan)
                winget.assert_not_called()

    def test_missing_ffmpeg_is_error_not_warning(self):
        # 无 ffmpeg 的 PATH(整段替换,win32 也随之失去 winget/系统 ffmpeg):
        # 失败必须发生在下载之前——零下载请求(本地服务零命中)、目标未创建。
        bare = self.sandbox / "bare-path"
        bare.mkdir()
        self.env["PATH"] = str(bare)
        # 原始产品 CLI 的独立负例,不用子进程 shim 隔离入口。
        result = self.run_install(entrypoint=INSTALL_PY)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未检测到 ffmpeg", result.stderr)
        # 失败发生在下载之前:零 curl 调用、目标未创建。
        self.assertFalse((self.sandbox / "curl-calls.jsonl").exists())
        self.assertFalse(self.target.exists())

    def test_missing_ffmpeg_with_winget_declined_never_installs_or_downloads(self):
        installer = _load_installer()
        bare = self.sandbox / "only-winget"
        bare.mkdir()
        # winget 即使可见也只能是临时哨兵;调用即失败,绝不安装软件。
        self.command_fixture("winget", "exit 91\n", "@exit /b 91\r\n", bare)
        for windows in (False, True):
            with self.subTest(windows=windows), contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.dict(os.environ, {"PATH": str(bare)}))
                stack.enter_context(mock.patch.object(installer, "IS_WINDOWS", windows))
                answers = iter(["y", "n", "n"] if windows else ["y"])
                wizard = installer.Wizard(enabled=True, prompt=lambda _: next(answers))
                stack.enter_context(mock.patch.object(installer, "_make_wizard", return_value=wizard))
                # 版本固定,缺依赖必须在任何安装/下载/子进程前抛错。
                guards = [stack.enter_context(mock.patch.object(
                    owner, name, side_effect=AssertionError(name + " forbidden")))
                    for owner, name in ((installer, "_winget_install"),
                                        (installer, "fetch"),
                                        (installer, "discover_version"),
                                        (installer.subprocess, "run"))]
                args = argparse.Namespace(
                    dir=str(self.target), version="9.9.9", chrome=False,
                    non_interactive=False, defaults=False, dry_run=False, upgrade=False,
                    verify_provenance=False, no_provenance=True)
                self.assertIsNone(shutil.which("ffmpeg"))
                self.assertIsNotNone(shutil.which("winget"))
                with self.assertRaisesRegex(installer.InstallError, "未检测到 ffmpeg"):
                    installer.install(args)
                if windows:
                    self.assertIs(wizard.choices["winget_ffmpeg"], False)
                for guard in guards:
                    guard.assert_not_called()
                self.assertFalse((self.sandbox / "curl-calls.jsonl").exists())
                self.assertFalse(self.target.exists())


class InstallerLocalPrimitives(unittest.TestCase):
    """只调用临时目录中的归档/锁 helper;无安装、下载、HTTP 或系统集成。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="installer-primitives-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.installer = _load_installer()

    def extract(self, links, name="payload"):
        archive = self.root / (name + ".tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            data = b"owned payload"
            member = tarfile.TarInfo("b/payload")
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
            for path, target in links:
                member = tarfile.TarInfo(path)
                member.type = tarfile.SYMTYPE
                member.linkname = target
                tar.addfile(member)
        destination = self.root / name
        self.installer.safe_extract(archive, destination)
        return destination

    def test_link_destination_cannot_follow_another_link(self):
        sentinel = self.root / "outside"
        sentinel.write_bytes(b"unchanged")
        with self.assertRaises(self.installer.InstallError):
            self.extract([("a/link", "../b"), ("a/link/x", "../../outside")])
        self.assertEqual(sentinel.read_bytes(), b"unchanged")
        self.assertFalse((self.root / "payload/b/x").is_symlink())

    @unittest.skipIf(os.name == "nt", "POSIX link graph; Windows materializes copies")
    def test_complete_link_graph_rejects_escape_in_either_archive_order(self):
        sentinel = self.root / "outside"
        sentinel.write_bytes(b"unchanged")
        links = [("a/link", "../b"), ("out", "a/link/../../outside")]
        for index, ordered in enumerate((links, links[::-1])):
            with self.subTest(order=index), self.assertRaises(self.installer.InstallError):
                self.extract(ordered, "payload%d" % index)
        self.assertEqual(sentinel.read_bytes(), b"unchanged")

    @unittest.skipIf(os.name == "nt", "POSIX symlink and dangling-link semantics")
    def test_cycle_rejected_but_internal_dangling_and_parent_targets_allowed(self):
        with self.assertRaises(self.installer.InstallError):
            self.extract([("a", "b/link"), ("b/link", "../a")], "cycle")
        destination = self.extract(
            [("scripts/data", "../b/payload"), ("scripts/missing", "../b/absent")])
        self.assertEqual((destination / "scripts/data").read_bytes(), b"owned payload")
        self.assertTrue((destination / "scripts/missing").is_symlink())
        self.assertFalse((destination / "scripts/missing").exists())

    def test_windows_target_spellings_rejected_on_every_host(self):
        for index, target in enumerate((r"..\outside", "b/payload:stream", r"C:\outside")):
            with self.subTest(target=target), self.assertRaises(self.installer.InstallError):
                self.extract([("link", target)], "bad%d" % index)

    @unittest.skipIf(os.name == "nt", "POSIX ELOOP; newer resolve behavior is simulated")
    def test_cycle_rejected_even_when_non_strict_resolve_does_not_raise(self):
        real_resolve = Path.resolve

        def permissive_resolve(path, *args, **kwargs):
            if path.name in ("a", "link"):
                return path.absolute()
            return real_resolve(path, *args, **kwargs)

        with mock.patch.object(Path, "resolve", permissive_resolve), \
                self.assertRaises(self.installer.InstallError):
            self.extract([("a", "b/link"), ("b/link", "../a")])

    def test_windows_materialization_retains_internal_parent_target(self):
        with mock.patch.object(self.installer, "IS_WINDOWS", True), \
                mock.patch.object(os, "symlink", side_effect=AssertionError("symlink forbidden")):
            destination = self.extract([("scripts/data", "../b/payload")])
        self.assertFalse((destination / "scripts/data").is_symlink())
        self.assertEqual((destination / "scripts/data").read_bytes(), b"owned payload")

    @unittest.skipIf(os.name == "nt", "controlled real fcntl inode handoff")
    def test_unlock_handoff_keeps_one_inode_and_failed_contender_closes(self):
        import fcntl
        first = self.installer.InstallLock(self.root / "installed")
        second = self.installer.InstallLock(self.root / "installed")
        contender = self.installer.InstallLock(self.root / "installed")
        first.__enter__()
        first_fd = first.handle.fileno()
        real_flock = fcntl.flock

        def acquire_second_at_unlock(fd, operation):
            real_flock(fd, operation)
            if fd == first_fd and operation == fcntl.LOCK_UN:
                second.__enter__()

        try:
            with mock.patch.object(fcntl, "flock", side_effect=acquire_second_at_unlock):
                first.__exit__(None, None, None)
            self.assertEqual(first.path.stat().st_ino, os.fstat(second.handle.fileno()).st_ino)
            with self.assertRaises(self.installer.InstallError):
                contender.__enter__()
            self.assertIsNone(contender.handle)
        finally:
            for lock in (contender, second, first):
                if lock.handle is not None:
                    lock.__exit__(None, None, None)

    def test_failed_windows_lock_closes_open_handle(self):
        windows = types.SimpleNamespace(LK_NBLCK=1, locking=mock.Mock(side_effect=OSError("busy")))
        lock = self.installer.InstallLock(self.root / "installed")
        handle = (self.root / "handle").open("a+b")
        self.addCleanup(handle.close)
        with mock.patch.object(self.installer, "IS_WINDOWS", True), \
                mock.patch.dict(sys.modules, {"msvcrt": windows}), \
                mock.patch("builtins.open", return_value=handle), \
                self.assertRaises(self.installer.InstallError):
            lock.__enter__()
        self.assertTrue(handle.closed)
        self.assertIsNone(lock.handle)


class InstallerPathUndo(unittest.TestCase):
    """PATH/launcher checks use an in-memory registry, never the host registry."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="installer-path-undo-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.selected, self.other = self.root / "selected", self.root / "other"
        for target in (self.selected, self.other):
            (target / "bin").mkdir(parents=True)
            (target / ".vt-install.json").write_text("{}", encoding="utf-8")
        self.installer = _load_installer()
        self.value = os.pathsep.join((str(self.selected / "bin"), "",
                                     str(self.other / "bin"), " untouched "))
        self.registry = types.SimpleNamespace(
            HKEY_CURRENT_USER=1, KEY_READ=2, KEY_WRITE=4, REG_EXPAND_SZ=7,
            OpenKey=mock.MagicMock(),
            QueryValueEx=mock.Mock(side_effect=lambda *a: (self.value, 7)),
            SetValueEx=mock.Mock(side_effect=self.write_value))
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(self.installer, "IS_WINDOWS", True))
        self.stack.enter_context(mock.patch.dict(sys.modules, {"winreg": self.registry}))

    def write_value(self, key, name, reserved, kind, value):
        self.assertEqual((name, reserved, kind), ("Path", 0, 7))
        self.value = value

    def write_shims(self, target, python):
        with mock.patch.object(self.installer.sys, "executable", python):
            self.installer.write_shims(target, self.installer.Wizard(False))

    def test_shims_store_exact_python_as_ascii_without_codepage_changes(self):
        # This is generation/lifecycle coverage on every host, not a cmd emulator.
        python = str(self.root / "解释器 & %PATH% !bang! ('venv')" / "python.exe")
        self.write_shims(self.selected, python)
        launcher = (self.selected / "bin/_vt_launcher.py").read_bytes().decode("ascii")
        binding = next(node for node in ast.parse(launcher).body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "BOUND_PYTHON"
                               for t in node.targets))
        self.assertEqual(ast.literal_eval(binding.value), python)
        for name in ("vt", "vtx"):
            content = (self.selected / ("bin/" + name + ".cmd")).read_bytes()
            self.assertNotIn(b"\n", content.replace(b"\r\n", b""))
            text = content.decode("ascii")
            self.assertIn("setlocal EnableExtensions DisableDelayedExpansion", text)
            self.assertIn('set "ERRORLEVEL="', text)
            self.assertIn(f'-I -S -X utf8 "%~dp0_vt_launcher.py" {name} %*', text)
            self.assertNotIn("chcp", text.lower())
            self.assertNotIn("PYTHONPATH", text)
        self.assertEqual({p.name for p in (self.selected / "bin").iterdir()},
                         {"vt.cmd", "vtx.cmd", "_vt_launcher.py"})
        self.registry.SetValueEx.assert_not_called()  # already on this user's PATH
        before = self.value
        second = str(self.root / "replacement" / "python.exe")
        self.write_shims(self.selected, second)
        self.assertIn("BOUND_PYTHON = " + ascii(second),
                      (self.selected / "bin/_vt_launcher.py").read_text("ascii"))
        self.assertEqual(self.value, before)
        self.registry.SetValueEx.assert_not_called()

    def test_shim_skip_and_invalid_interpreter_do_not_write(self):
        target = self.root / "not-created"
        wizard = self.installer.Wizard(False)
        wizard.choices["shims_path"] = False
        with mock.patch.object(self.installer.sys, "executable", None):
            self.installer.write_shims(target, wizard)
        self.assertFalse(target.exists())
        for python in (None, "", "python.exe"):
            with self.subTest(python=python), self.assertRaises(self.installer.InstallError):
                self.write_shims(target, python)
            self.assertFalse(target.exists())
        self.registry.OpenKey.assert_not_called()

    def test_missing_path_value_adds_only_this_shim_directory(self):
        self.registry.QueryValueEx.side_effect = FileNotFoundError
        self.write_shims(self.selected, sys.executable)
        self.assertEqual(self.value, str(self.selected / "bin"))
        self.registry.SetValueEx.assert_called_once()

    def test_checkout_reuse_does_not_silently_rebind_python(self):
        (self.selected / "vt.py").write_text("# fixture\n", encoding="ascii")
        (self.selected / "vtx").mkdir()
        self.write_shims(self.selected, sys.executable)
        before = (self.selected / "bin/_vt_launcher.py").read_bytes()
        with mock.patch.object(self.installer.sys, "executable", str(self.root / "other.exe")), \
                mock.patch.object(self.installer, "fetch", side_effect=AssertionError("no fetch")):
            self.assertEqual(self.installer.main(
                ["--dir", str(self.selected), "--version", "9.9.9", "--non-interactive",
                 "--verify-provenance"]), 0)
        self.assertEqual((self.selected / "bin/_vt_launcher.py").read_bytes(), before)
        self.registry.SetValueEx.assert_not_called()

    def windows_launcher_fixture(self):
        """Real distinct venv executables, no host PATH/registry or batch Python fake."""
        import venv
        bound = self.root / "解释器 & %PATH% !home! (A)"
        bootstrap = self.root / "bootstrap-B"
        for directory in (bound, bootstrap):
            venv.EnvBuilder(with_pip=False).create(directory)
        python = bound / "Scripts/python.exe"
        bootstrap_python = bootstrap / "Scripts/python.exe"
        (bound / "Lib/site-packages/_framivra_bound_probe.py").write_text(
            "TOKEN = 'only-in-bound-A'\n", encoding="ascii")
        target = self.root / "安装 & %PATH% !root! (vt)"
        target.mkdir()
        (target / "vtx").mkdir()
        (target / "vtx/__init__.py").write_bytes(b"")
        program = (
            "import json, os, sys\n"
            "import _framivra_bound_probe\n"
            "print(json.dumps(dict(executable=sys.executable, prefix=sys.prefix,\n"
            "    argv=sys.argv[1:], cwd=os.getcwd(), token=_framivra_bound_probe.TOKEN,\n"
            "    stdin=sys.stdin.read())), flush=True)\n"
            "sys.exit(int(os.environ.get('VT_TEST_EXIT', '0')))\n")
        for file in (target / "vt.py", target / "vtx/__main__.py"):
            file.write_text(program, encoding="ascii")
        self.write_shims(target, str(python))
        caller = self.root / "caller"
        caller.mkdir()
        env = dict(os.environ, PATH=str(bootstrap / "Scripts"), ERRORLEVEL="0",
                   PATHEXT=".COM;.EXE;.BAT;.CMD")
        # Prove PATH chooses B, while the application-only probe is unavailable.
        probe = subprocess.run(
            [str(bootstrap_python), "-I", "-S", "-c",
             "import importlib.util,json,sys; print(json.dumps([sys.executable,"
             "importlib.util.find_spec('_framivra_bound_probe') is None]))"],
            cwd=caller, env=env, capture_output=True, text=True, timeout=30, check=True)
        actual, missing = json.loads(probe.stdout)
        self.assertEqual(os.path.normcase(actual), os.path.normcase(str(bootstrap_python)))
        self.assertTrue(missing)
        # The isolated B must not import its own sitecustomize before switching.
        site_marker = self.root / "bootstrap-site-ran"
        (bootstrap / "Lib/site-packages/sitecustomize.py").write_text(
            "from pathlib import Path\nPath(%r).write_text('unexpected')\n" % str(site_marker),
            encoding="utf-8")
        return target, python, bootstrap_python, caller, env, site_marker

    def run_windows_shim(self, target, name, caller, env, arguments='""', delayed=False):
        cmd = str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe")
        # Environment substitution is nonrecursive: literal %PATH% in these
        # values remains data. Pass a raw cmd command line, not CRT-escaped /c.
        env = dict(env, VT_TEST_SHIM=str(target / ("bin/" + name + ".cmd")),
                   VT_TEST_ARGS=arguments)
        if delayed:
            # The caller must not consume ! before entering the shim. Resolve its
            # basename from cwd; the shim's own %~dp0 still contains literal !.
            command = '"%s" /d /v:on /s /c "%s.cmd delayed"' % (cmd, name)
        else:
            command = '"%s" /d /v:off /s /c ""%%VT_TEST_SHIM%%" %%VT_TEST_ARGS%%"' % cmd
        return subprocess.run(command, cwd=caller, env=env, input="stdin fixture\n",
                              capture_output=True, text=True, encoding="utf-8",
                              errors="strict", timeout=30,
                              creationflags=subprocess.CREATE_NO_WINDOW)

    @unittest.skipUnless(os.name == "nt", "requires native Windows cmd/executables")
    def test_windows_shims_bind_python_preserve_argv_cwd_and_dword_exit(self):
        target, python, bootstrap, caller, env, site_marker = self.windows_launcher_fixture()
        expected = ["", "中文 空格", "amp & value", "%PATH%", "!bang!", "apostrophe's",
                    "(parens)", "end slash\\", 'a"b']
        arguments = r'''"" "中文 空格" "amp & value" "%PATH%" "!bang!" "apostrophe's" "(parens)" "end slash\\" "a\"b"'''
        for name in ("vt", "vtx"):
            for code in (0, 37, 0x80000000, 0xc000013a, 0xffffffff):
                with self.subTest(name=name, code=code):
                    signed = code - 0x100000000 if code > 0x7fffffff else code
                    result = self.run_windows_shim(
                        target, name, caller, dict(env, VT_TEST_EXIT=str(signed)), arguments)
                    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                    observed = json.loads(result.stdout)
                    self.assertEqual(os.path.normcase(observed["executable"]),
                                     os.path.normcase(str(python)))
                    self.assertEqual(os.path.normcase(observed["prefix"]),
                                     os.path.normcase(str(python.parent.parent)))
                    self.assertEqual(observed["argv"], expected)
                    self.assertEqual(observed["cwd"], str(caller))
                    self.assertEqual(observed["token"], "only-in-bound-A")
                    self.assertEqual(observed["stdin"], "stdin fixture\n")
            result = self.run_windows_shim(target, name, target / "bin", env, delayed=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["argv"], ["delayed"])
        self.assertFalse(site_marker.exists())
        moved = self.root / "搬迁 & %PATH% !target!"
        target.rename(moved)
        for name in ("vt", "vtx"):
            result = self.run_windows_shim(moved, name, caller, env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["token"], "only-in-bound-A")

    @unittest.skipUnless(os.name == "nt", "requires native Windows cmd/executables")
    def test_windows_shims_fail_closed_without_bound_python_or_bootstrap(self):
        target, python, bootstrap, caller, env, _ = self.windows_launcher_fixture()
        for binding in (self.root / "missing-python.exe", self.root / "invalid-python.exe"):
            if binding.name.startswith("invalid"):
                binding.write_text("not an executable\n", encoding="ascii")
            self.write_shims(target, str(binding))
            for name in ("vt", "vtx"):
                with self.subTest(binding=binding, name=name):
                    result = self.run_windows_shim(target, name, caller, env)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("cannot start installed Python", result.stderr)
                    self.assertIn("--upgrade --dir", result.stderr)
                    self.assertEqual(result.stdout, "")
        self.write_shims(target, str(python))
        empty = self.root / "empty-path"
        empty.mkdir()
        for name in ("vt", "vtx"):
            result = self.run_windows_shim(target, name, caller, dict(env, PATH=str(empty)))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no Python bootstrap found", result.stderr)
            self.assertEqual(result.stdout, "")

    @unittest.skipUnless(os.name == "nt", "requires an owned native Windows console")
    def test_windows_launcher_waits_for_console_signal_cleanup(self):
        # Exercise the added trampoline, not cmd's pre-existing Ctrl-C prompt or
        # venv redirectors. Distinct-venv binding is covered by real cmd above.
        # Only a private CREATE_NEW_CONSOLE controller may broadcast. Never attach
        # to the test runner's console, or use taskkill/PIDs for cleanup.
        target = self.selected
        python = getattr(sys, "_base_executable", sys.executable)
        self.write_shims(target, python)
        (target / "vt.py").write_text(r'''
import json, os, signal, sys, time
from pathlib import Path
case = os.environ["VT_SIGNAL_CASE"]
def cleanup(*_):
    time.sleep(0.6)  # Longer than subprocess.call's KeyboardInterrupt grace.
    Path(os.environ["VT_CLEANUP"]).write_text("cleaned", encoding="ascii")
    sys.exit(37)
if case != "default_break":
    signal.signal(signal.SIGINT if case == "int" else signal.SIGBREAK, cleanup)
ready = Path(os.environ["VT_READY"])
staging = ready.with_suffix(".tmp")
staging.write_text(json.dumps(dict(
    pid=os.getpid(), case=case, executable=sys.executable)), encoding="ascii")
staging.replace(ready)
# If safety validation fails, no event is sent and this fixture self-terminates.
deadline = float(os.environ["VT_FIXTURE_DEADLINE"])
while time.monotonic() < deadline:
    time.sleep(0.05)
sys.exit(99)
''', encoding="ascii")
        controller = self.root / "private-console-controller.py"
        controller.write_text(r'''
import ctypes, json, os, signal, subprocess, sys, time
from ctypes import wintypes as w
from pathlib import Path
k = ctypes.WinDLL("kernel32", use_last_error=True)
def api(name, result, *args):
    function = getattr(k, name)
    function.restype, function.argtypes = result, args
    return function
close = api("CloseHandle", w.BOOL, w.HANDLE)
console = api("GetConsoleProcessList", w.DWORD, ctypes.POINTER(w.DWORD), w.DWORD)
open_process = api("OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
wait = api("WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
terminate = api("TerminateProcess", w.BOOL, w.HANDLE, w.UINT)
times = api("GetProcessTimes", w.BOOL, w.HANDLE, *([ctypes.POINTER(w.FILETIME)] * 4))
generate = api("GenerateConsoleCtrlEvent", w.BOOL, w.DWORD, w.DWORD)
input_cp = api("GetConsoleCP", w.UINT)
output_cp = api("GetConsoleOutputCP", w.UINT)
def require(condition, message):
    if not condition:
        raise RuntimeError(message)
def console_pids():
    entries = (w.DWORD * 64)()
    count = console(entries, len(entries))
    require(0 < count <= len(entries), "unknown/truncated console membership")
    return set(entries[:count])
def identity(handle):
    values = [w.FILETIME() for _ in range(4)]
    require(times(handle, *(ctypes.byref(value) for value in values)), "process times failed")
    return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
own = os.getpid()
require(console_pids() == {own}, "controller did not start in an exclusive console")
codepages = (input_cp(), output_cp())
require(all(codepages), "console code pages unavailable")
signal.signal(signal.SIGINT, lambda *_: None)
signal.signal(signal.SIGBREAK, lambda *_: None)
child, handles, identities, owned = None, {}, {}, set()
expires = time.monotonic() + 15
try:
    child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=dict(os.environ, VT_FIXTURE_DEADLINE=str(expires)))
    ready = Path(os.environ["VT_READY"])
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        require(child.poll() is None, "bootstrap exited before readiness")
        time.sleep(0.02)
    require(ready.exists(), "application readiness timed out")
    record = json.loads(ready.read_text("ascii"))
    require(record["case"] == os.environ["VT_SIGNAL_CASE"], "stale readiness")
    require(os.path.normcase(record["executable"]) == os.path.normcase(os.environ["VT_BOUND"]),
            "wrong application interpreter")
    require(child.poll() is None, "bootstrap exited before signal validation")
    members = {own, child.pid, record["pid"]}
    require(len(members) == 3 and console_pids() == members, "unexpected console process")
    for pid in members:
        # Hold query/synchronize/terminate handles, not just reusable numeric PIDs.
        handle = open_process(0x1000 | 0x100000 | 1, False, pid)
        require(handle, "cannot pin console process identity")
        handles[pid] = handle
        identities[pid] = identity(handle)
    require(identities[own] <= identities[child.pid] <= identities[record["pid"]],
            "unexpected process creation order")
    # Enumeration and broadcast are not atomic. A dedicated, non-shared console
    # plus two matching observations fails closed on every observed change.
    require(console_pids() == members, "console membership changed")
    require(all(wait(handle, 0) == 258 and identity(handle) == identities[pid]
                for pid, handle in handles.items()), "process identity/liveness changed")
    require(child.poll() is None, "owned bootstrap exited before signal")
    owned = members - {own}
    event = 0 if os.environ["VT_SIGNAL_CASE"] == "int" else 1
    require(generate(event, 0), "console event failed")
    stdout, stderr = child.communicate(timeout=12)
    require((input_cp(), output_cp()) == codepages, "console code pages changed")
    print(json.dumps(dict(code=child.returncode, members=len(members),
                          stdout=stdout.decode("utf-8"), stderr=stderr.decode("utf-8"))))
finally:
    failed, errors = sys.exc_info()[0] is not None, []
    # Before validation we cannot claim to reap A. Wait past its absolute fixture
    # deadline (not 15 seconds after startup) before temporary-directory cleanup.
    if child is not None and not owned:
        time.sleep(max(0, expires + 1 - time.monotonic()))
    # Every retained, verified handle gets a cleanup attempt; never look up PIDs
    # afresh. One shared five-second budget bounds all waits, not five per object.
    deadline = time.monotonic() + 5
    for pid in owned:
        handle = handles[pid]
        try:
            if wait(handle, 0) == 258 and identity(handle) == identities[pid]:
                require(terminate(handle, 98), "owned process cleanup failed")
        except Exception as exc:
            errors.append(str(exc))
    try:
        if child is not None and child.poll() is None:
            child.terminate()  # Popen's already-owned process handle only.
            child.wait(timeout=max(0, deadline - time.monotonic()))
    except Exception as exc:
        errors.append(str(exc))
    for pid in owned:
        try:
            require(wait(handles[pid], max(0, int((deadline - time.monotonic()) * 1000))) == 0,
                    "owned process cleanup timed out")
        except Exception as exc:
            errors.append(str(exc))
    for handle in handles.values():
        if not close(handle):
            errors.append("CloseHandle failed")
    if errors:
        print("; ".join(errors), file=sys.stderr)
        if not failed:
            raise RuntimeError("private console cleanup failed")
''', encoding="ascii")
        for case in ("int", "break", "default_break"):
            with self.subTest(case=case):
                ready, cleaned = self.root / (case + "-ready"), self.root / (case + "-cleaned")
                signal_env = dict(os.environ, VT_SIGNAL_CASE=case, VT_READY=str(ready),
                                  VT_CLEANUP=str(cleaned), VT_BOUND=python)
                result = subprocess.run(
                    [python, "-I", "-S", str(controller), python, "-I", "-S",
                     "-X", "utf8", str(target / "bin/_vt_launcher.py"), "vt"],
                    cwd=self.root, env=signal_env, capture_output=True, text=True,
                    encoding="utf-8", timeout=35, creationflags=subprocess.CREATE_NEW_CONSOLE)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                observation = json.loads(result.stdout)
                self.assertEqual(observation["members"], 3)
                self.assertEqual(observation["code"],
                                 0xc000013a if case == "default_break" else 37, observation)
                if case == "default_break":
                    self.assertFalse(cleaned.exists())
                else:
                    self.assertEqual(cleaned.read_text("ascii"), "cleaned")

    def test_explicit_undo_dry_run_has_no_registry_write(self):
        before = self.value
        self.assertEqual(self.installer.main(["--undo", str(self.selected), "--dry-run"]), 0)
        self.assertEqual(self.value, before)
        self.registry.SetValueEx.assert_not_called()
        self.assertEqual(self.registry.OpenKey.call_args.args[-1], self.registry.KEY_READ)
        self.assertTrue(self.selected.is_dir())

    def test_explicit_undo_removes_only_selected_install_and_is_idempotent(self):
        self.assertEqual(self.installer.main(["--undo", str(self.selected)]), 0)
        self.assertEqual(self.value, os.pathsep.join(("", str(self.other / "bin"), " untouched ")))
        self.assertTrue(self.selected.is_dir())
        self.assertTrue((self.selected / "bin").is_dir())
        self.assertEqual(self.installer.main(["--undo", str(self.selected)]), 0)
        self.registry.SetValueEx.assert_called_once()

    def test_undo_without_value_honors_dir(self):
        self.assertEqual(self.installer.main(["--undo", "--dir", str(self.selected)]), 0)
        self.assertNotIn(str(self.selected / "bin"), self.value)
        self.assertIn(str(self.other / "bin"), self.value)

    def test_explicit_undo_precedes_dir(self):
        self.assertEqual(self.installer.main(["--undo", str(self.selected), "--dir", str(self.other)]), 0)
        self.assertNotIn(str(self.selected / "bin"), self.value)
        self.assertIn(str(self.other / "bin"), self.value)

    def test_uninstall_removes_only_its_own_path_entry(self):
        self.assertEqual(self.installer.uninstall(self.selected, False), 0)
        self.assertFalse(self.selected.exists())
        self.assertTrue(self.other.is_dir())
        self.assertIn(str(self.other / "bin"), self.value)
        self.assertNotIn(str(self.selected / "bin"), self.value)

    def test_unmarked_target_does_not_write_registry(self):
        (self.selected / ".vt-install.json").unlink()
        before = self.value
        self.installer.undo_path(self.selected)
        self.assertEqual(self.value, before)
        self.registry.OpenKey.assert_not_called()

    def test_uninstall_path_failure_reports_manual_target_cleanup(self):
        self.registry.OpenKey.side_effect = OSError("fixture registry unavailable")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(self.installer.uninstall(self.selected, False), 0)
        self.assertFalse(self.selected.exists())
        self.assertIn("请手动从用户 PATH 移除 %s" % (self.selected / "bin"), output.getvalue())
        self.assertNotIn("稍后手动运行 --undo", output.getvalue())
        self.assertTrue(self.other.exists())

    def test_missing_path_value_is_not_created(self):
        self.registry.QueryValueEx.side_effect = FileNotFoundError
        self.installer.undo_path(self.selected)
        self.registry.SetValueEx.assert_not_called()


if __name__ == "__main__":
    unittest.main()
