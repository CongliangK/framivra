"""Init acceptance using copied sources, private HOME and zero-model modules."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import venv


ASR = Path(__file__).resolve().parents[1]


class TestInitIndependentAssets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="asr-init-independent-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.asr = self.repo / "apps/asr"
        self.asr.mkdir(parents=True)
        for name in ("init.sh", "client.py", "independence.py", "sidecar_server.py"):
            shutil.copyfile(ASR / name, self.asr / name)
        config_dir = self.repo / "vtx/pluginlib"
        config_dir.mkdir(parents=True)
        shutil.copyfile(ASR.parents[1] / "vtx/pluginlib/config.py", config_dir / "config.py")
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")
                    and k not in ("VT_ASR_IME_ROOT", "VTX_CONFIG", "VT_ASR_PORT")}
        self.env.update(HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        self.calls = self.root / "forbidden-calls"
        self.ime = self.home / "文档/01_活跃项目/type/ibus-voice-ime-github"
        self.forbidden = self.ime / ".venv-qwen-asr/bin/python"
        self.forbidden.parent.mkdir(parents=True)
        self.forbidden.write_text('#!/bin/sh\necho invoked >> "%s"\nexit 0\n' % self.calls)
        self.forbidden.chmod(0o755)

    def config(self, data, path=None):
        path = path or self.repo / "config.local.json"
        path.write_text(json.dumps({"asr": data}))
        return path

    def run_init(self, expected):
        result = subprocess.run(["bash", str(self.asr / "init.sh")], cwd=self.repo,
                                env=self.env, capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, expected, output)
        self.assertFalse(self.calls.exists(), "Forbidden interpreter was executed")
        return output

    def model(self, name):
        model = self.root / "independent-models" / name
        model.mkdir(parents=True)
        (model / "model.safetensors").write_bytes(b"FIXTURE-NOT-A-MODEL")
        (model / "config.json").write_text("{}")
        return model

    def interpreter(self, torch=True):
        env_root = self.root / "own-env"
        venv.EnvBuilder(with_pip=False, symlinks=False).create(env_root)
        py = env_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        result = subprocess.run([str(py), "-I", "-c",
                                 "import sysconfig;print(sysconfig.get_path('purelib'))"],
                                capture_output=True, text=True, check=True, env=self.env)
        site = Path(result.stdout.strip())
        for name in ("qwen_asr", "torch") if torch else ("qwen_asr",):
            module = site / name
            module.mkdir()
            # Import/provenance is allowed, but any compute makes the test fail.
            (module / "__init__.py").write_text(
                "def __getattr__(name):\n    raise AssertionError('no model compute in init')\n")
        return py

    def ready(self, torch=True):
        model = self.model("Qwen3-ASR-0.6B")
        aligner = self.model("explicit-aligner")
        py = self.interpreter(torch=torch)
        self.config({"python": str(py), "model_dir": str(model.parent),
                     "aligner_dir": str(aligner), "enabled": False})
        return model, aligner

    def test_configured_ime_interpreter_refused_before_execution(self):
        self.config({"python": str(self.forbidden)})
        output = self.run_init(2)
        self.assertIn("语音输入法", output)
        self.assertNotIn("冒烟通过", output)

    def test_effective_override_ignores_unsafe_root_config(self):
        self.config({"python": str(self.forbidden)})
        override = self.config({}, self.home / "effective.json")
        self.env["VTX_CONFIG"] = "~/effective.json"
        output = self.run_init(1)
        self.assertIn("缺少模型", output)
        self.assertNotIn("指向语音输入法资产", output)

    def test_host_python_ignores_pythonpath_sitecustomize(self):
        injected = self.root / "injected"
        injected.mkdir()
        (injected / "sitecustomize.py").write_text(
            "from pathlib import Path\nPath(%r).touch()\n" % str(self.calls))
        self.env["PYTHONPATH"] = str(injected)
        self.config({})
        self.run_init(1)

    def test_effective_unsafe_override_refused(self):
        self.config({})
        override = self.config({"python": str(self.forbidden)}, self.root / "effective.json")
        self.env["VTX_CONFIG"] = str(override)
        self.run_init(2)

    def test_model_symlink_refused_before_interpreter_execution(self):
        borrowed = self.ime / "weights"
        borrowed.mkdir()
        weight = borrowed / "model.safetensors"
        weight.write_bytes(b"FAKE-IME")
        model = self.model("Qwen3-ASR-0.6B")
        # Replace only our just-created fixture file, never an external asset.
        (model / "model.safetensors").unlink()
        (model / "model.safetensors").symlink_to(weight)
        self.config({"model_dir": str(model.parent)})
        output = self.run_init(1)
        self.assertIn("符号链接", output)
        self.assertNotIn("冒烟通过", output)

    def test_single_asr_and_configured_aligner_health_without_compute(self):
        model, aligner = self.ready()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.assertNotEqual(port, 18081)
        self.env["VT_ASR_PORT"] = str(port)
        output = self.run_init(0)
        self.assertIn(str(model), output)
        self.assertIn(str(aligner), output)
        self.assertIn("sidecar 冒烟通过", output)
        self.assertIn("未验真实转写", output)

    def test_missing_torch_never_smokes_or_passes(self):
        self.ready(torch=False)
        output = self.run_init(1)
        self.assertIn("torch", output)
        self.assertNotIn("冒烟通过", output)

    def test_missing_explicit_aligner_does_not_fall_back(self):
        model = self.model("Qwen3-ASR-0.6B")
        self.model("Qwen3-ForcedAligner-0.6B")
        self.config({"model_dir": str(model.parent), "aligner_dir": str(self.root / "missing")})
        output = self.run_init(1)
        self.assertIn("不是目录", output)
        self.assertNotIn("冒烟通过", output)

    def test_reserved_port_rejected_before_assets(self):
        self.env["VT_ASR_PORT"] = "18081"
        output = self.run_init(2)
        self.assertIn("18081", output)
        self.assertNotIn("冒烟通过", output)


if __name__ == "__main__":
    unittest.main()
