"""Installer safety tests with real local downloads and temporary payloads.

The child fixture invokes scripts/install.py while fencing downloads and host integration.

No network, real HOME, live mounts, or repository cache/out are used. Even malicious
archive link/traversal destinations are explicitly inside TemporaryDirectory.
"""
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

try:
    from . import _install_fixture
    from ._support import SandboxCase, serve_fixtures
except ImportError:
    import _install_fixture
    from _support import SandboxCase, serve_fixtures


class InstallSafety(SandboxCase):
    def setUp(self):
        super().setUp()
        self.bin = self.sandbox / "bin"
        self.fixtures = self.sandbox / "fixtures"
        self.target = self.root / "installed"
        self.bin.mkdir()
        self.fixtures.mkdir()
        self.payload = self.fixtures / "video-tools-9.9.9.tar.gz"
        self.checksums = self.fixtures / "SHA256SUMS"
        self.curl_log = self.sandbox / "curl-calls.jsonl"
        fixtures = ExitStack()
        self.addCleanup(fixtures.close)
        base_url, _ = fixtures.enter_context(serve_fixtures(
            self.fixtures, self.curl_log,
            paths={f'/v9.9.9/{self.payload.name}', '/v9.9.9/SHA256SUMS'}))
        _, dependency_env = _install_fixture.dependency_fixtures(self.bin, self.home)
        self.env.update(dependency_env)
        for key in ("USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            self.env[key] = str(self.home)
        self.env.update(VIDEO_TOOLS_DL_BASE=base_url, SAFETY_INSTALL_LOOPBACK=base_url)
        self.archive()

    def archive(self, extra=()):
        # Only an inert checkout is installed; no actual init/doctor executes.
        program = b'''from pathlib import Path
import sys
with Path("calls.txt").open("a") as stream:
    stream.write(sys.argv[1] + "\\n")
'''
        entries = [("vt.py", program), ("VERSION", b"9.9.9\n"), ("vtx/__init__.py", b"# fixture\n")]
        with tarfile.open(self.payload, "w:gz") as archive:
            for name, content in entries + list(extra):
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        self.valid_checksum()

    def valid_checksum(self):
        digest = hashlib.sha256(self.payload.read_bytes()).hexdigest()
        self.put(self.checksums, f"{digest}  {self.payload.name}\n".encode())

    def install(self, *args, target=None):
        target = self.checked(target or self.target)
        entry = Path(_install_fixture.__file__).resolve()
        return subprocess.run(
            [sys.executable, str(entry), "--dir", str(target), "--version", "9.9.9",
             "--non-interactive", "--no-provenance", *args],
            cwd=self.root, env=self.env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=30)

    def assert_work_clean(self):
        self.assertEqual(list(self.work.iterdir()), [], "installer trap must remove temporary downloads")
        self.assert_outside_unchanged()

    def assert_downloads(self):
        paths = [json.loads(line)['path'] for line in self.curl_log.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(paths, [f'/v9.9.9/{self.payload.name}', '/v9.9.9/SHA256SUMS'])

    def test_install_and_repeated_install_do_not_overwrite_checkout(self):
        first = self.install()
        self.assert_ok(first)
        if os.name == 'nt':
            self.assertIn('已跳过 shim 与用户 PATH 写入', first.stdout)
        self.assertTrue((self.target / ".vt-install.json").is_file())
        self.assertEqual((self.target / "calls.txt").read_text(), "init\ndoctor\n")
        self.put(self.target / "vt.py", b"# user-modified checkout\n")
        before = self.tree(self.target)
        self.assert_ok(self.install())
        self.assertEqual(self.tree(self.target), before)
        self.assert_downloads()  # The second install must not re-download or rewrite the checkout.
        self.assert_work_clean()

    def test_dry_run_does_not_install_or_download(self):
        before = self.tree()
        self.assert_ok(self.install("--dry-run"))
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.curl_log.exists(), "explicit-version dry-run must not download anything")
        self.assert_work_clean()

    def test_existing_non_checkout_directory_is_never_overwritten(self):
        victim = self.put(self.target / "keep.txt", b"existing data")
        before = self.tree(self.target)
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.tree(self.target), before)
        self.assertEqual(victim.read_bytes(), b"existing data")
        self.assertIn('非空且不是 video_tools checkout', result.stderr)
        self.assertFalse(self.curl_log.exists())
        self.assert_work_clean()

    def test_symlink_target_cannot_overwrite_existing_external_directory(self):
        self.target.symlink_to(self.outside, target_is_directory=True)
        before = self.tree(self.outside)
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('拒绝写穿', result.stderr)
        self.assertFalse(self.curl_log.exists())
        self.assertEqual(self.tree(self.outside), before)
        self.assert_work_clean()

    def test_bad_checksum_interrupts_before_creating_destination(self):
        self.put(self.checksums, ("0" * 64 + "  " + self.payload.name + "\n").encode())
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SHA256 校验失败', result.stderr)
        self.assertFalse(self.target.exists())
        self.assert_downloads()
        self.assert_work_clean()

    def test_corrupt_archive_cannot_mark_install_success(self):
        self.put(self.payload, b"not a gzip or tar file")
        self.valid_checksum()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.target / ".vt-install.json").exists())
        self.assertFalse((self.target / "calls.txt").exists())
        self.assertIn('不是有效的 tar.gz 归档', result.stderr)
        self.assert_downloads()
        self.assert_work_clean()

    def test_tar_dotdot_member_is_rejected_without_external_overwrite(self):
        # installed/../../outside resolves to this test's temporary sentinel.
        self.archive(extra=[("../../outside/keep.txt", b"malicious replacement")])
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('归档成员含空/当前/父目录段', result.stderr)
        self.assertFalse((self.target / ".vt-install.json").exists())
        self.assert_downloads()
        self.assert_work_clean()

    def test_tar_symlink_cannot_write_through_to_external_directory(self):
        with tarfile.open(self.payload, "w:gz") as archive:
            link = tarfile.TarInfo("escaped")
            link.type = tarfile.SYMTYPE
            link.linkname = str(self.outside)  # Never point to real user data.
            archive.addfile(link)
            member = tarfile.TarInfo("escaped/keep.txt")
            content = b"malicious replacement"
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        self.valid_checksum()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('符号链接目标越出载荷根', result.stderr)
        self.assertFalse((self.target / ".vt-install.json").exists())
        self.assert_downloads()
        self.assert_work_clean()

    def test_uninstall_without_marker_refuses_deletion(self):
        self.put(self.target / "keep.txt", b"unmarked user data")
        before = self.tree(self.target)
        result = self.install("--uninstall")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('无 .vt-install.json 标记', result.stderr)
        self.assertFalse(self.curl_log.exists())
        self.assertEqual(self.tree(self.target), before)
        self.assert_work_clean()

    def test_marked_uninstall_dry_run_is_read_only(self):
        self.put(self.target / ".vt-install.json", b"{}")
        self.put(self.target / "keep.txt")
        before = self.tree(self.target)
        self.assert_ok(self.install("--uninstall", "--dry-run"))
        self.assertEqual(self.tree(self.target), before)
        self.assert_work_clean()

    def test_marked_uninstall_symlink_does_not_recurse_into_external_target(self):
        self.put(self.outside / ".vt-install.json", b"{}")
        self.target.symlink_to(self.outside, target_is_directory=True)
        before = self.tree(self.outside)
        self.assert_ok(self.install("--uninstall"))
        self.assertFalse(self.target.is_symlink())
        self.assertEqual(self.tree(self.outside), before)
        self.assert_work_clean()
