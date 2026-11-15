"""Direct launch/runtime boundaries, entirely synthetic assets and model constructors."""
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import independence
import sidecar_server as server


class TestSidecarRuntimeIndependence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="asr-direct-boundary-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        ind = mock.patch.object(server, "_ind", return_value=independence)
        ind.start()
        self.addCleanup(ind.stop)
        self.ime = self.home / "文档/01_活跃项目/type/ibus-voice-ime-github"

    def rejected_main(self, cfg=None, executable=None):
        with mock.patch.object(server, "_load_config_for_gate", return_value=cfg or {}), \
                mock.patch.object(sys, "argv", ["sidecar", "--once-health"]), \
                mock.patch.object(sys, "executable", executable or sys.executable), \
                mock.patch("socket.socket", side_effect=AssertionError("no port probe allowed")):
            self.assertEqual(server.main(), 2)

    def test_legacy_environment_refused_before_port_probe(self):
        os.environ["VT_ASR_IME_ROOT"] = str(self.ime)
        self.rejected_main()

    def test_effective_config_paths_and_legacy_key_refused_before_probe(self):
        for cfg in ({"asr": {"ime_root": str(self.ime)}},
                    {"asr": {"model_dir": str(self.ime / "models")}},
                    {"asr": {"base_url": "http://127.0.0.1:18081"}}):
            with self.subTest(cfg=cfg):
                self.rejected_main(cfg)

    def test_current_ime_interpreter_refused_before_port_probe(self):
        self.rejected_main(executable=str(self.ime / ".venv-qwen-asr/bin/python"))

    def test_runtime_checks_both_dependency_origins_before_model_import(self):
        with mock.patch.object(sys, "flags", SimpleNamespace(isolated=1)), \
                mock.patch.object(independence, "verify_interpreter", return_value="own/bin/python"), \
                mock.patch.object(independence, "verify_module_from_own_env") as origin:
            server._verify_runtime_independence()
        self.assertEqual([call.args[:2] for call in origin.call_args_list],
                         [("own/bin/python", "qwen_asr"), ("own/bin/python", "torch")])

    def test_current_nonisolated_process_refused_before_child_origin_probe(self):
        # An isolated child can see clean packages while the actual serving
        # process has PYTHONPATH injection. Child provenance alone is not proof.
        with mock.patch.object(sys, "flags", SimpleNamespace(isolated=0)), \
                mock.patch.object(independence, "verify_module_from_own_env") as origin:
            with self.assertRaisesRegex(independence.AsrIndependenceError, "-I"):
                server._verify_runtime_independence()
            origin.assert_not_called()

    def test_refused_provenance_precedes_model_import(self):
        holder = server.ModelHolder("own-model", device_map="cpu", idle_timeout=0)
        with mock.patch.object(holder, "_gpu_admission_locked", return_value=nullcontext()), \
                mock.patch.object(server, "companion_path", return_value=None), \
                mock.patch.object(server, "_verify_runtime_independence",
                                  side_effect=independence.AsrIndependenceError("foreign dependency")), \
                mock.patch("builtins.__import__", side_effect=AssertionError("no model import allowed")):
            with self.assertRaisesRegex(independence.AsrIndependenceError, "foreign dependency"):
                holder._load_locked()
        self.assertIsNone(holder._model)

    def test_oom_companion_checked_before_fallback_constructor(self):
        initial = self.root / "models/Qwen3-ASR-1.7B"
        initial.mkdir(parents=True)
        (initial / "config.json").write_text("{}")
        (initial / "model.safetensors").write_bytes(b"INITIAL")
        borrowed = self.ime / "model"
        borrowed.mkdir(parents=True)
        (borrowed / "config.json").write_text("{}")
        (borrowed / "model.safetensors").write_bytes(b"FAKE-IME")
        companion = initial.parent / "Qwen3-ASR-0.6B"
        companion.symlink_to(borrowed, target_is_directory=True)
        constructor = mock.Mock(side_effect=RuntimeError("out of memory"))
        qwen = SimpleNamespace(Qwen3ASRModel=SimpleNamespace(from_pretrained=constructor))
        holder = server.ModelHolder(str(initial), device_map="cpu", idle_timeout=0)
        with mock.patch.object(holder, "_gpu_admission_locked", return_value=nullcontext()), \
                mock.patch.object(holder, "_torch_dtype", return_value="fixture-dtype"), \
                mock.patch.object(holder, "_is_oom", return_value=True), \
                mock.patch.object(server, "_verify_runtime_independence"), \
                mock.patch.dict(sys.modules, {"qwen_asr": qwen}):
            with self.assertRaisesRegex(independence.AsrIndependenceError, "语音输入法"):
                holder._load_locked()
        constructor.assert_called_once()
        self.assertIsNone(holder._model)


if __name__ == "__main__":
    unittest.main()
