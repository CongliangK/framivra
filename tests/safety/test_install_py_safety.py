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
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
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
        # 原生 .cmd 可由 Windows 子进程执行;不依赖 choco/winget/真实 FFmpeg。
        self.ffmpeg = self.command_fixture(
            "ffmpeg", "printf 'ffmpeg version 6.1 fixture\\n'\n",
            "@echo off\r\necho ffmpeg version 6.1 fixture\r\n")
        # 不把 curl 的宿主目录(及其它真实依赖)混回 PATH。安装器以裸名
        # 调 curl,Windows 必须提供真 curl.exe(CreateProcess 不查找 .cmd)。
        if os.name == "nt":
            curl = Path(os.environ["SystemRoot"]) / "System32" / "curl.exe"
            self.assertTrue(curl.is_file(), "本地 HTTP fixture 需要 Windows 内建 curl.exe")
            shutil.copyfile(curl, self.bin / "curl.exe")
        else:
            curl = shutil.which("curl")
            self.assertIsNotNone(curl, "本地 HTTP fixture 需要原生 curl")
            self.command_fixture(
                "curl", 'exec %s -q --noproxy "*" "$@"\n' % shlex.quote(curl), "")
        self.env.update({"PATH": str(self.bin), "CURL_HOME": str(self.home),
                         "NO_PROXY": "*", "no_proxy": "*",
                         "SAFETY_CURL_LOG": str(self.sandbox / "curl-calls.jsonl"),
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


if __name__ == "__main__":
    unittest.main()
