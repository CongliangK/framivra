"""Regression probes from #140 acceptance: no real models, GPU or IME access."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import venv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import client
import independence


class AcceptanceBoundaries(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "native Windows CLI streams")
    def test_disabled_cli_keeps_refusal_under_legacy_redirected_encoding(self):
        with tempfile.TemporaryDirectory(prefix="asr-disabled-") as tmp:
            home = Path(tmp)
            config = home / "config.json"
            config.write_text('{"asr":{"enabled":false}}', encoding="utf-8")
            env = dict(SystemRoot=os.environ["SystemRoot"], USERPROFILE=str(home), PATH="",
                       VTX_CONFIG=str(config), PYTHONUTF8="0", PYTHONIOENCODING="cp936")
            cli = Path(__file__).resolve().parents[1] / "asr_cli.py"
            result = subprocess.run([sys.executable, "-s", "-B", str(cli), "transcribe", "unused.wav"],
                                    cwd=home, env=env, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("✗ ASR 独立性校验失败", result.stdout.decode("utf-8"))
            self.assertEqual(result.stderr, b"")

    def test_public_http_helpers_reject_ime_port_before_transport(self):
        cfg = {"asr": {"enabled": True}}
        with mock.patch.object(client.urllib.request, "urlopen") as request:
            for url in ("http://127.0.0.1:18081", "http://localhost:18081"):
                for call in (
                    lambda: client.health(url),
                    lambda: client.warm(url, cfg=cfg),
                    lambda: client.transcribe(url, "unused.wav", cfg=cfg),
                ):
                    with self.subTest(url=url, call=call):
                        with self.assertRaises(independence.AsrIndependenceError):
                            call()
            request.assert_not_called()

    def test_direct_compute_helpers_reject_legacy_config_before_transport(self):
        cfg = {"asr": {"enabled": True, "ime_root": "/unused"}}
        with mock.patch.object(client.urllib.request, "urlopen") as request:
            for call in (
                lambda: client.warm(client.DEFAULT_BASE_URL, cfg=cfg),
                lambda: client.transcribe(client.DEFAULT_BASE_URL, "unused.wav", cfg=cfg),
            ):
                with self.assertRaises(independence.AsrIndependenceError):
                    call()
            request.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX symlink venv regression")
    def test_symlink_venv_keeps_environment_and_checks_specs_before_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root / "own-env"
            venv.EnvBuilder(with_pip=False, symlinks=True).create(env)
            py = env / "bin/python"
            site = Path(subprocess.check_output(
                [str(py), "-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
                text=True).strip())
            for name in ("qwen_asr", "torch"):
                (site / name).mkdir()
                (site / name / "__init__.py").write_text("", encoding="utf-8")
            cfg = {"asr": {"python": str(py)}}
            self.assertEqual(client.probe_python(cfg), str(py))
            selected = independence.verify_interpreter(py)
            prefix = subprocess.check_output(
                [selected, "-I", "-c", "import sys;print(sys.prefix)"], text=True).strip()
            self.assertEqual(Path(prefix), env)

            # A top-level package outside the env must not execute before refusal.
            external = root / "external-torch"
            external.mkdir()
            marker = root / "imported"
            (external / "__init__.py").write_text(
                "from pathlib import Path\nPath(%r).touch()\n" % str(marker), encoding="utf-8")
            (site / "torch/__init__.py").unlink()
            (site / "torch").rmdir()
            (site / "torch").symlink_to(external, target_is_directory=True)
            with mock.patch.object(independence, "project_env_root", return_value=root / "missing"):
                self.assertIsNone(client.probe_python(cfg))
            self.assertFalse(marker.exists())

            # Even when the binary resolves outside the forbidden environment,
            # invoking the lexical IME venv path must be rejected before exec.
            with mock.patch.object(independence, "IME_ROOT_ANCHORS", (str(env),)):
                with self.assertRaises(independence.AsrIndependenceError):
                    independence.verify_interpreter(py)


if __name__ == "__main__":
    unittest.main()
