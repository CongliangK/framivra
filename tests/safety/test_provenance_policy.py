#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布身份验证策略测试（#16/#41 keyless provenance）。

三层：
  1) verify_release 模块单测（mock gh 子进程：缺失/失败/坏输出/成功）；
  2) install.py 源级策略单测（official 拒绝 --no-provenance；非 github 源
     默认拒绝、显式旗标放行）；
  3) install.py CLI 端到端（本地 fixture 服务 = 非 github 源）：
     无旗标 → 非零退出 + 指引文案 + 目标零创建；--no-provenance → 安装成功。

mock 原则：gh 子进程全 mock；fixture 载荷走真实本地 http 服务（真 curl
下载 + SHA256 + 解包全链路）；不触网络外站。真实 hosted attestation 正例
由 release-attest.yml 的 verify-only 模式产生（主控 dispatch 核对日志），
本套件不谎称已验证真实证词。
"""
import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO))

from _support import SandboxCase, serve_fixtures  # noqa: E402
import _install_fixture  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY_PY = REPO / "scripts/release" / "verify_release.py"
INSTALL_PY = REPO / "scripts" / "install.py"
INSTALL_FIXTURE = Path(__file__).with_name("_install_fixture.py")


class InstallerFixtureRouting(unittest.TestCase):
    """No installer/HTTP/registry is executed by these host-isolation controls."""

    def test_provenance_cli_uses_host_integration_optout_entrypoint(self):
        case = SimpleNamespace(target=Path("owned/installed"), root=Path("owned"), env={})
        with mock.patch.object(subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            InstallerCliProvenanceGate._run(case, "--no-provenance")
        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], [sys.executable, str(INSTALL_FIXTURE)])
        self.assertNotIn(str(INSTALL_PY), argv)
        self.assertIn("--no-provenance", argv)
        self.assertIn("--non-interactive", argv)

    def test_dependency_path_contains_only_temporary_fixtures(self):
        before = dict(os.environ)
        with tempfile.TemporaryDirectory(prefix="provenance-deps-") as temporary:
            root = Path(temporary)
            ffmpeg, env = _install_fixture.dependency_fixtures(root / "bin", root / "home")
            self.assertEqual(env["PATH"], str(root / "bin"))
            self.assertEqual(Path(shutil.which("ffmpeg", path=env["PATH"])), ffmpeg)
            self.assertIsNone(shutil.which("winget", path=env["PATH"]))
            self.assertIsNotNone(shutil.which("curl", path=env["PATH"]))
            self.assertEqual(env["NO_PROXY"], "*")
            self.assertIn(b"ffmpeg version 6.1 fixture", ffmpeg.read_bytes())
        self.assertEqual(dict(os.environ), before)


class VerifyReleaseModule(unittest.TestCase):
    """① verify_release 模块:fail-closed 语义(mock gh 子进程)。"""

    def setUp(self):
        self.vr = _load("vt_verify_release_test", VERIFY_PY)
        self.artifact = Path(self.id().replace(".", "_") + ".bin")

        def fake_run(cmd, **kwargs):
            return self.run_result

        self.run_result = subprocess.CompletedProcess(
            [], 0, stdout=b"[]", stderr=b"")

    def _patch_run(self):
        return mock.patch.object(self.vr.subprocess, "run",
                                 side_effect=lambda cmd, **kw: self.run_result)

    def test_missing_gh_fails_closed_with_guidance(self):
        with mock.patch.object(self.vr.shutil, "which", return_value=None):
            with self.assertRaises(self.vr.ProvenanceError) as ctx:
                self.vr.verify_artifact(self.artifact)
        self.assertIn("gh", str(ctx.exception))

    def test_gh_nonzero_fails_closed(self):
        self.run_result = subprocess.CompletedProcess(
            [], 1, stdout=b"", stderr=b"some identity mismatch")
        with self._patch_run():
            with mock.patch.object(self.vr.shutil, "which", return_value="/gh"):
                with self.assertRaises(self.vr.ProvenanceError):
                    self.vr.verify_artifact(self.artifact)

    def test_empty_attestation_list_fails(self):
        self.run_result = subprocess.CompletedProcess(
            [], 0, stdout=b"[]", stderr=b"")
        with self._patch_run():
            with mock.patch.object(self.vr.shutil, "which", return_value="/gh"):
                with self.assertRaises(self.vr.ProvenanceError):
                    self.vr.verify_artifact(self.artifact)

    def test_missing_transparency_witness_fails(self):
        # certificate 有、verifiedTimestamps 缺 → 拒绝(只有不可操纵字段算数)。
        payload = [{"verificationResult": {
            "signature": {"certificate": {"SAN": "x"}},
            "verifiedTimestamps": []}}]
        self.run_result = subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(payload).encode(), stderr=b"")
        with self._patch_run():
            with mock.patch.object(self.vr.shutil, "which", return_value="/gh"):
                with self.assertRaises(self.vr.ProvenanceError):
                    self.vr.verify_artifact(self.artifact)

    def test_valid_attestation_passes_and_ref_digest_pinned(self):
        payload = [{"verificationResult": {
            "signature": {"certificate": {"SAN": "x"}},
            "verifiedTimestamps": [{"timestamp": "t"}]}}]
        captured = {}

        def run_capture(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(payload).encode(), stderr=b"")

        with mock.patch.object(self.vr.subprocess, "run", side_effect=run_capture):
            with mock.patch.object(self.vr.shutil, "which", return_value="/gh"):
                count = self.vr.verify_artifact(
                    self.artifact,
                    expect_ref="refs/tags/v1.2.3",
                    expect_digest="a" * 40)
        self.assertEqual(count, 1)
        cmd = captured["cmd"]
        self.assertIn("--source-ref", cmd)
        self.assertIn("refs/tags/v1.2.3", cmd)
        self.assertIn("--source-digest", cmd)
        self.assertIn("a" * 40, cmd)
        for flag in ("--deny-self-hosted-runners", "--signer-workflow",
                     "--cert-oidc-issuer", "--hostname", "--repo"):
            self.assertIn(flag, cmd)

    def test_garbage_output_fails_closed(self):
        self.run_result = subprocess.CompletedProcess(
            [], 0, stdout=b"not json", stderr=b"")
        with self._patch_run():
            with mock.patch.object(self.vr.shutil, "which", return_value="/gh"):
                with self.assertRaises(self.vr.ProvenanceError):
                    self.vr.verify_artifact(self.artifact)


class ProvenanceGatePolicy(unittest.TestCase):
    """② install 源级策略:官方/非官方载荷源 × 旗标矩阵。"""

    def setUp(self):
        self.inst = _load("vt_install_prov_test", INSTALL_PY)

    def _args(self, **over):
        base = dict(verify_provenance=False, no_provenance=False)
        base.update(over)
        return argparse.Namespace(**base)

    def test_official_source_requires_verification(self):
        self.assertEqual(
            self.inst._provenance_gate(
                self._args(),
                "https://github.com/CongliangK/framivra/releases/download"),
            "verify")

    def test_official_source_rejects_no_provenance(self):
        with self.assertRaises(self.inst.InstallError) as ctx:
            self.inst._provenance_gate(
                self._args(no_provenance=True),
                "https://github.com/CongliangK/framivra/releases/download")
        self.assertIn("--no-provenance", str(ctx.exception))

    def test_mirror_source_refused_without_explicit_flag(self):
        with self.assertRaises(self.inst.InstallError) as ctx:
            self.inst._provenance_gate(
                self._args(), "http://127.0.0.1:18999")
        self.assertIn("--no-provenance", str(ctx.exception))
        self.assertIn("127.0.0.1", str(ctx.exception))

    def test_mirror_source_allows_explicit_optout_and_forced_verify(self):
        self.assertEqual(
            self.inst._provenance_gate(self._args(no_provenance=True),
                                       "http://127.0.0.1:18999"),
            "skip")
        self.assertEqual(
            self.inst._provenance_gate(self._args(verify_provenance=True),
                                       "http://127.0.0.1:18999"),
            "verify")

    def test_enforce_provenance_maps_errors_and_passes(self):
        vr = self.inst.verify_release
        artifacts = (Path("p.tar.gz"), Path("SHA256SUMS"))
        # verify 路径:模块错误 → InstallError(fail-closed,不回退)。
        with mock.patch.object(
                vr, "verify_artifact",
                side_effect=vr.ProvenanceError("identity mismatch")):
            with self.assertRaises(self.inst.InstallError):
                self.inst._enforce_provenance("verify", *artifacts, version="1.2.3")
        # skip 路径零调用。
        with mock.patch.object(vr, "verify_artifact") as verify_mock:
            self.inst._enforce_provenance("skip", *artifacts, version="1.2.3")
            verify_mock.assert_not_called()
        # 正式版本号锚定 tag ref(防降级);候选版本不锚。
        calls = {}
        with mock.patch.object(
                vr, "verify_artifact",
                side_effect=lambda a, expect_ref=None, gh=None:
                    calls.setdefault("ref", expect_ref) or 1):
            self.inst._enforce_provenance("verify", *artifacts, version="1.2.3")
            self.assertEqual(calls["ref"], "refs/tags/v1.2.3")
            calls.clear()
            self.inst._enforce_provenance("verify", *artifacts, version="0.0.0-ci")
            self.assertIsNone(calls["ref"])


class InstallerCliProvenanceGate(SandboxCase):
    """③ CLI 端到端:本地 fixture 服务(非 github 源)上的旗标语义。"""

    def setUp(self):
        super().setUp()
        self.fixtures = self.sandbox / "fixtures"
        self.target = self.root / "installed"
        self.fixtures.mkdir()
        self.hits = self.sandbox / "prov-hits.jsonl"
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        base_url, holder = stack.enter_context(
            serve_fixtures(self.fixtures, self.hits))
        holder["releases_json"] = b'{"latest":"9.9.9"}'
        self.base_url = base_url
        self.bin = self.sandbox / "bin"
        self.ffmpeg, dependency_env = _install_fixture.dependency_fixtures(self.bin, self.home)
        self.env.update(dependency_env)
        # 最小官方形制载荷(vt.py+VERSION+vtx/__init__.py)。
        import hashlib
        import io
        import tarfile
        payload = self.fixtures / "video-tools-9.9.9.tar.gz"
        with tarfile.open(payload, "w:gz") as archive:
            for name, content in ((("vt.py"), b"# framivra\n"),
                                   ("VERSION", b"9.9.9\n"),
                                   ("vtx/__init__.py", b"# fixture\n")):
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        (self.fixtures / "SHA256SUMS").write_text(
            "%s  video-tools-9.9.9.tar.gz\n" % digest)
        self.env.update({"VIDEO_TOOLS_DL_BASE": base_url})

    def _run(self, *flags):
        return subprocess.run(
            [sys.executable, str(INSTALL_FIXTURE), "--dir", str(self.target),
             "--version", "9.9.9", "--non-interactive", *flags],
            cwd=self.root, env=self.env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=120)

    def test_mirror_source_without_flag_fails_before_any_download(self):
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        combined = (result.stdout or "") + (result.stderr or "")
        self.assertIn("--no-provenance", combined)
        # fail-closed 先于下载:fixture 服务零命中(命中日志文件不存在)。
        self.assertFalse(self.hits.exists())
        self.assertFalse(self.target.exists())
        self.assertEqual(
            [p.name for p in self.target.parent.glob("*staging*")], [])

    def test_mirror_source_with_explicit_flag_installs(self):
        result = self._run("--no-provenance")
        self.assertEqual(result.returncode, 0,
                         (result.stdout or "") + (result.stderr or ""))
        self.assertTrue((self.target / ".vt-install.json").is_file())
        self.assertTrue(self.hits.exists())  # 真实下载发生


if __name__ == "__main__":
    unittest.main()
