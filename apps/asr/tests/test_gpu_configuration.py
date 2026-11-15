"""GPU wiring regressions; fake constructors only, no models or GPU needed."""
from contextlib import nullcontext
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import client
import independence
import sidecar_server as server


class GpuConfiguration(unittest.TestCase):
    def test_initial_aligner_receives_same_device_and_dtype_as_asr(self):
        constructor = mock.Mock(return_value=SimpleNamespace(model=SimpleNamespace(device="cuda:1")))
        qwen = SimpleNamespace(Qwen3ASRModel=SimpleNamespace(from_pretrained=constructor))
        holder = server.ModelHolder("own-model", aligner_path="own-aligner", device_map="cuda:1")
        with mock.patch.object(holder, "_gpu_admission_locked", return_value=nullcontext()), \
                mock.patch.object(holder, "_torch_dtype", return_value="fixture-bfloat16"), \
                mock.patch.object(holder, "_resync_device"), \
                mock.patch.object(server, "_verify_runtime_independence"), \
                mock.patch.object(server, "companion_path", return_value=None), \
                mock.patch.dict(sys.modules, {"qwen_asr": qwen}):
            holder._load_locked()
        kwargs = constructor.call_args.kwargs
        self.assertEqual(kwargs["forced_aligner"], "own-aligner")
        self.assertEqual(kwargs["forced_aligner_kwargs"],
                         {"dtype": kwargs["dtype"], "device_map": kwargs["device_map"]})
        constructor.assert_called_once()

    def test_launcher_passes_effective_budget_to_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "Qwen3-ASR-0.6B"
            model.mkdir()
            (model / "model.safetensors").write_bytes(b"fixture")
            for configured in (None, 8192):
                with self.subTest(configured=configured):
                    cfg = {"asr": {"enabled": True, "device_map": "cuda:0"}}
                    if configured is not None:
                        cfg["asr"]["vram_budget_mib"] = configured
                    record = {"pid": 4242, "port": 19999, "start_time": 1}
                    with mock.patch.object(client, "_sidecar_attribution", return_value=False), \
                            mock.patch.object(independence, "endpoint_listeners", return_value=set()), \
                            mock.patch.object(client, "probe_python", return_value=sys.executable), \
                            mock.patch.object(client, "pick_model", return_value=str(model)), \
                            mock.patch.object(client, "probe_aligner", return_value=None), \
                            mock.patch.object(client, "SIDECAR_LOG", root / "sidecar.log"), \
                            mock.patch.object(independence, "gpu_admission", return_value={"allowed": True}) as admission, \
                            mock.patch.object(client.subprocess, "Popen", return_value=SimpleNamespace(pid=4242)) as spawn, \
                            mock.patch.object(independence, "write_pid_record"), \
                            mock.patch.object(independence, "read_pid_record", return_value=record), \
                            mock.patch.object(client, "health", return_value={"status": "ok"}):
                        self.assertTrue(client.ensure_sidecar(cfg, "http://127.0.0.1:19999", verbose=False))
                    budget = configured or independence.estimate_vram_mib(model)
                    admission.assert_called_once_with(budget, device="cuda:0")
                    argv = spawn.call_args.args[0]
                    self.assertEqual(argv[argv.index("--vram-budget") + 1], str(budget))


if __name__ == "__main__":
    unittest.main()
