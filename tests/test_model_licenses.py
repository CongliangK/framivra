"""License evidence and download boundaries; no real weights, API or GPU."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import model_license_policy as policy

ROOT = Path(__file__).resolve().parents[1]


class LicenseInventory(unittest.TestCase):
    def test_inventory_records_default_payload_and_unknowns_without_clearance(self):
        data = policy.load_inventory()
        self.assertEqual([k for k, v in data["assets"].items() if v["default_payload"]], ["framivra"])
        for name in ("matanyone2-weights", "qwen-emotion", "chrome-binary", "music-tools-assets", "imgen-api"):
            self.assertEqual(data["assets"][name]["license_status"], "UNKNOWN")
        self.assertEqual(data["assets"]["matanyone2-code"]["license_status"], "RESTRICTED")
        self.assertIn("LGPL-3.0", data["assets"]["edge-tts-code"]["license_identifier"])
        self.assertNotIn("text", data["evidence"]["framivra"])
        self.assertEqual(data["evidence"]["framivra"]["local_path"], "LICENSE")

    def test_saved_evidence_tamper_is_rejected(self):
        data = policy.load_inventory()
        data["evidence"]["resemble-enhance"]["text"] += "tampered"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(policy.LicensePolicyError, "changed evidence"):
                policy.load_inventory(path)

    def test_unknown_default_payload_is_rejected(self):
        data = policy.load_inventory()
        data["assets"]["matanyone2-weights"]["default_payload"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(policy.LicensePolicyError, "unreviewed default"):
                policy.load_inventory(path)

    def test_actual_payload_audit_is_offline_and_not_legal_certification(self):
        report = policy.audit_payload()
        self.assertTrue(report["ok"])
        self.assertIn("no clearance", report["scope"])
        self.assertIn("maskgct-semantic", report["unknown_or_restricted"])

    def test_new_weight_and_binary_payloads_are_rejected(self):
        for name in ("model.safetensors", "torch_weights.bin", "chrome.exe", "lib.so", "checkpoint.pth"):
            with self.subTest(name=name), self.assertRaisesRegex(policy.LicensePolicyError, "model/binary payload"):
                policy.audit_payload(tracked=[name])

    def test_all_unreviewed_chains_refuse_even_if_status_is_forged(self):
        data = policy.load_inventory()
        for channel in data["legacy_downloads"]:
            with self.subTest(channel=channel), self.assertRaisesRegex(policy.LicensePolicyError, "BLOCKED"):
                policy.block_legacy_download(channel, inventory=data)
        data["legacy_downloads"]["indextts"]["status"] = "AUTHORIZED"
        with self.assertRaises(policy.LicensePolicyError):
            policy.block_legacy_download("indextts", inventory=data)


class SnapshotPolicy(unittest.TestCase):
    def setUp(self):
        self.data = policy.load_inventory()
        self.ident = "resemble-enhance"
        self.asset = self.data["assets"][self.ident]
        self.evidence = self.data["evidence"][self.asset["evidence"]]
        self.ack = self.evidence["sha256"]

    def test_known_official_weight_notice_allows_specific_purpose(self):
        for purpose in ("commercial", "noncommercial"):
            asset, evidence = policy.authorize_snapshot(self.ident, purpose, self.ack, inventory=self.data)
            self.assertEqual(asset["download"]["provider"], "huggingface")
            self.assertEqual(evidence["sha256"], self.ack)

    def test_unknown_and_restricted_assets_cannot_be_accepted(self):
        for ident in ("matanyone2-weights", "maskgct-semantic", "indextts-2.5-weights", "qwen-emotion", "edge-api"):
            for purpose in ("commercial", "noncommercial"):
                with self.subTest(ident=ident, purpose=purpose), self.assertRaises(policy.LicensePolicyError):
                    policy.authorize_snapshot(ident, purpose, self.ack, inventory=self.data)

    def test_license_word_in_readme_body_is_not_a_model_card_declaration(self):
        data = copy.deepcopy(self.data)
        evidence = data["evidence"][self.ident]
        evidence["text"] = "---\ntags: [fixture]\n---\nAn example follows:\nlicense: mit\n"
        evidence["sha256"] = hashlib.sha256(evidence["text"].encode()).hexdigest()
        with self.assertRaisesRegex(policy.LicensePolicyError, "frontmatter"):
            policy.authorize_snapshot(self.ident, "commercial", evidence["sha256"], inventory=data)

    def test_source_mirror_latest_purpose_notice_and_weight_scope_fail_closed(self):
        for change in ("mirror", "latest", "purpose", "notice", "code-license"):
            data = copy.deepcopy(self.data)
            ack, purpose = self.ack, "commercial"
            if change == "mirror": data["assets"][self.ident]["download"]["provider"] = "modelscope"
            elif change == "latest": data["assets"][self.ident]["download"]["revision"] = "main"
            elif change == "purpose": purpose = "unknown"
            elif change == "notice": ack = "0" * 64
            else: data["assets"][self.ident]["evidence"] = "resemble-code"
            with self.subTest(change=change), self.assertRaises(policy.LicensePolicyError):
                policy.authorize_snapshot(self.ident, purpose, ack, inventory=data)

    def _download(self, output, downloader):
        return policy.download_snapshot(self.ident, output, "commercial", self.ack,
                                        inventory=self.data, downloader=downloader)

    def test_approved_download_is_pinned_official_credential_free_and_notices_preserved(self):
        calls = []
        def fake(**kwargs):
            calls.append(kwargs)
            stage = Path(kwargs["local_dir"])
            (stage / "README.md").write_text(self.evidence["text"], encoding="utf-8")
            (stage / "LICENSE").write_text("upstream copyright and terms: fixture")
            (stage / "model.safetensors").write_bytes(b"not a model; test fixture")
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"HF_ENDPOINT": "https://unreviewed-mirror.invalid", "HF_TOKEN": "fixture-no-use"}):
            output = Path(directory) / "model"
            self.assertEqual(self._download(output, fake), output)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["revision"], self.asset["download"]["revision"])
            self.assertEqual(calls[0]["endpoint"], "https://huggingface.co")
            self.assertIs(calls[0]["token"], False)
            self.assertEqual(calls[0]["repo_id"], "ResembleAI/resemble-enhance")
            self.assertEqual((output / "LICENSE").read_text(), "upstream copyright and terms: fixture")
            notice = json.loads((output / policy.NOTICE).read_text())
            self.assertEqual(notice["evidence_sha256"], self.ack)
            self.assertEqual(notice["purpose"], "commercial")
            self.assertIn("not a rights grant", notice["notice"])

    def test_existing_user_assets_are_never_overwritten(self):
        downloader = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing"
            output.mkdir()
            asset = output / "user.safetensors"
            asset.write_bytes(b"keep existing user fixture")
            with self.assertRaisesRegex(policy.LicensePolicyError, "already exists"):
                self._download(output, downloader)
            self.assertEqual(asset.read_bytes(), b"keep existing user fixture")
            downloader.assert_not_called()

    def test_notice_mismatch_or_failed_download_never_publish(self):
        for cause in ("bad-notice", "network-error"):
            def fake(**kwargs):
                if cause == "network-error": raise OSError("fixture transport failure")
                (Path(kwargs["local_dir"]) / "README.md").write_text("wrong repository notice")
            with self.subTest(cause=cause), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "model"
                with self.assertRaises((policy.LicensePolicyError, OSError)):
                    self._download(output, fake)
                self.assertFalse(output.exists())
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_unknown_download_never_imports_or_calls_hub(self):
        downloader = mock.Mock()
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(policy.LicensePolicyError):
            policy.download_snapshot("matanyone2-weights", Path(directory) / "model", "commercial", self.ack,
                                     inventory=self.data, downloader=downloader)
        downloader.assert_not_called()


class EntryBoundaries(unittest.TestCase):
    def _stage(self, directory, script):
        root = Path(directory) / "checkout"
        for relative in (script, "scripts/model_license_policy.py", "docs/model-license-inventory.json", "LICENSE"):
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        home = Path(directory) / "home"
        home.mkdir()
        env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / "config"), VTX_CONFIG=str(home / "absent.json"), PYTHONDONTWRITEBYTECODE="1")
        for key in ("PYTHONPATH", "PYTHONHOME", "VT_ASR_IME_ROOT", "COMFYUI_ROOT", "COMFYUI_PY", "CONDA_SH",
                    "DENOISE_PY", "RESEMBLE_WEIGHTS", "HF_ENDPOINT", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
            env.pop(key, None)
        return root, home, env

    def test_init_network_actions_block_before_config_assets_or_environment_writes(self):
        scripts = {"apps/asr/init.sh": "--download", "apps/tts/init.sh": "--download",
                   "apps/matting/init.sh": "--download", "plugins/denoise/denoise_init.sh": "--download",
                   "apps/matting/comfyui_init.sh": "--install"}
        for script, action in scripts.items():
            with self.subTest(script=script), tempfile.TemporaryDirectory() as directory:
                root, home, env = self._stage(directory, script)
                # Invalid config proves no parser/probe is reached before refusal.
                (root / "config.local.json").write_text("not user data: fixture deliberately invalid")
                before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
                result = subprocess.run(["bash", str(root / script), action], cwd=root, env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
                self.assertIn("BLOCKED", result.stderr)
                self.assertEqual(list(home.iterdir()), [])
                self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_matanyone_runtime_blocks_before_third_party_import_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root, home, env = self._stage(directory, "apps/matting/run_matanyone.py")
            output = Path(directory) / "alpha"
            result = subprocess.run([sys.executable, str(root / "apps/matting/run_matanyone.py"),
                                     "absent-input.mp4", "absent-mask.png", str(output)],
                                    cwd=root, env=env, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
            self.assertIn("BLOCKED matanyone-runtime", result.stderr)
            self.assertNotIn("ModuleNotFoundError", result.stderr)
            self.assertFalse(output.exists())
            self.assertEqual(list(home.iterdir()), [])

    def test_default_init_probes_do_not_request_model_hosts_or_create_assets(self):
        scripts = ("apps/asr/init.sh", "apps/tts/init.sh", "apps/matting/init.sh",
                   "plugins/denoise/denoise_init.sh", "apps/matting/comfyui_init.sh")
        for script in scripts:
            with self.subTest(script=script), tempfile.TemporaryDirectory() as directory:
                root, home, env = self._stage(directory, script)
                # Dependency-injection sentinel, not a fake production health PASS.
                # Local port probes are recorded/failed; any remote request is a defect.
                binaries = Path(directory) / "sentinels"
                binaries.mkdir()
                log = Path(directory) / "network-attempts"
                curl = binaries / "curl"
                curl.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FIXTURE_NETWORK_LOG\"\nexit 1\n")
                curl.chmod(0o755)
                env["PATH"] = str(binaries) + os.pathsep + os.defpath
                env["FIXTURE_NETWORK_LOG"] = str(log)
                result = subprocess.run(["bash", str(root / script)], cwd=root, env=env,
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 1 if script == "apps/asr/init.sh" else 0, result.stdout + result.stderr)
                if log.exists():
                    for line in log.read_text().splitlines():
                        self.assertIn("http://127.0.0.1:8188", line)
                        self.assertNotIn("huggingface", line)
                        self.assertNotIn("modelscope", line)
                self.assertEqual(list(home.iterdir()), [])
                self.assertFalse(any(p.name in (".venv", "models", "checkpoints", "MatAnyone2") for p in root.rglob("*")))

    def test_index_worker_blocks_before_missing_assets_import_constructor_or_outputs(self):
        from apps.tts import indextts_worker
        constructor = mock.Mock(side_effect=AssertionError("upstream constructor/network must not be reached"))
        module = type(sys)("indextts.infer_v2_5")
        module.IndexTTS2 = constructor
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(sys.modules, {"indextts.infer_v2_5": module}):
            project = Path(directory) / "project"
            argv = ["worker", "--spec", str(Path(directory) / "absent-spec.json"), "--project", str(project),
                    "--repo", str(Path(directory) / "absent-repo"), "--voice-ref", str(Path(directory) / "absent-voice.wav"), "--runid", "fixture"]
            with mock.patch.object(sys, "argv", argv), mock.patch("builtins.print") as output:
                self.assertEqual(indextts_worker.main(), 4)
            constructor.assert_not_called()
            self.assertFalse(project.exists())
            self.assertTrue(any("BLOCKED indextts" in str(call) for call in output.call_args_list))

    def test_help_and_plan_are_offline_and_no_blanket_override_exists(self):
        with mock.patch.object(policy, "download_snapshot", side_effect=AssertionError("must not download")), mock.patch("builtins.print"):
            self.assertEqual(policy.main(["plan", "matanyone2-weights"]), 0)
            with self.assertRaises(SystemExit) as error:
                policy.main(["download", "matanyone2-weights", "--output", "/unused", "--evidence-sha256", "0" * 64, "--accept-license"])
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
