"""Standard-library source/fixture tests. No browser, rendering, or audio audition."""
import array
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import wave

sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
sys.path.insert(0, str(HERE))
import prepare_preview as P
import inspect_preview as I


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo"
        self.production = self.repo / "templates/promo-demo/v1/production"
        self.source = self.production / "design/redesign-opening"
        self.source.mkdir(parents=True)
        (self.production / "audio").mkdir()
        self.assets = self.base / "pack"
        (self.assets / "audio").mkdir(parents=True)
        self.out = self.base / "new-project"
        rows = []
        for role in P.STEMS:
            payload = ("fixture-only-not-audio:" + role).encode()
            (self.assets / "audio" / (role + ".flac")).write_bytes(payload)
            rows.append({"id": role, "path": "audio/" + role + ".flac", "sha256": P.sha(payload),
                         "bytes": len(payload), "sample_rate": 48000, "channels": 2, "sample_frames": 5760000})
        self.stems = P.json_bytes({"format": "framivra-lossless-stems/1", "stems": rows})
        (self.production / "audio/stems.json").write_bytes(self.stems)
        for name in P.SOURCE_NAMES:
            (self.source / name).write_bytes((HERE / name).read_bytes())
        manifest = {"format": "framivra-opening-source-identity/1", "files": {n: P.sha((self.source / n).read_bytes()) for n in P.SOURCE_NAMES},
                    "production_stems_sha256": P.sha(self.stems)}
        content = P.json_bytes(manifest)
        (self.source / "source-manifest.json").write_bytes(content)
        self.expected = P.sha(content)

    def prepare(self, write=False, **overrides):
        values = dict(assets=self.assets, out=self.out, expected_manifest_sha256=self.expected,
                      write=write, source=self.source, production=self.production)
        values.update(overrides)
        return P.prepare(**values)

    def test_read_only_plan_never_creates_output(self):
        result = self.prepare()
        self.assertEqual(result["status"], "READ_ONLY_PLAN_VALIDATED")
        self.assertFalse(self.out.exists())
        self.assertEqual(result["binding"]["visual_entries"], 1)
        self.assertFalse(result["binding"]["original_mix_reused_verbatim"])

    def test_new_tree_has_one_html_and_exact_original_stems(self):
        result = self.prepare(write=True)
        self.assertEqual(len(list(self.out.rglob("*.html"))), 1)
        self.assertFalse((self.out / "cache").exists())
        self.assertFalse((self.out / "out").exists())
        for role in P.STEMS:
            self.assertEqual((self.out / "audio" / (role + ".flac")).read_bytes(),
                             (self.assets / "audio" / (role + ".flac")).read_bytes())
        checked = I.check_project(self.out, self.expected, self.source, self.production)
        self.assertEqual(checked["input_files_verified"], 11)
        self.assertEqual(result["status"], "PREPARED_NOT_RENDERED")

    def test_existing_destination_is_never_reused_or_overwritten(self):
        self.prepare(write=True)
        before = {str(p.relative_to(self.out)): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        with self.assertRaises(FileExistsError):
            self.prepare(write=True)
        self.assertEqual(before, {str(p.relative_to(self.out)): p.read_bytes() for p in self.out.rglob("*") if p.is_file()})

    def test_wrong_manifest_and_changed_runtime_fail_before_writing(self):
        with self.assertRaises(ValueError):
            self.prepare(write=True, expected_manifest_sha256="0" * 64)
        self.assertFalse(self.out.exists())
        (self.source / "opening.js").write_text("changed")
        with self.assertRaises(ValueError):
            self.prepare(write=True)
        self.assertFalse(self.out.exists())

    def test_missing_and_corrupt_stem_fail_before_writing(self):
        p = self.assets / "audio/T20.flac"
        p.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.prepare(write=True)
        self.assertFalse(self.out.exists())
        p.unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare(write=True)
        self.assertFalse(self.out.exists())

    def test_source_asset_and_destination_parent_symlinks_are_rejected(self):
        alias = self.base / "alias"
        alias.symlink_to(self.assets, target_is_directory=True)
        with self.assertRaises(OSError):
            self.prepare(write=True, assets=alias)
        alias.unlink()
        alias.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(OSError):
            self.prepare(write=True, out=alias / "target")
        alias.unlink()
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(OSError):
            self.prepare(write=True, source=alias)
        self.assertFalse(self.out.exists())

    def test_input_file_symlink_is_rejected_even_if_bytes_match(self):
        p = self.assets / "audio/T20.flac"
        real = self.base / "real.flac"
        p.rename(real)
        p.symlink_to(real)
        with self.assertRaises(OSError):
            self.prepare(write=True)
        self.assertFalse(self.out.exists())

    def test_destination_inside_repository_or_assets_is_rejected(self):
        for out in (self.repo / "new", self.production / "new", self.assets / "new"):
            with self.assertRaises(ValueError):
                self.prepare(write=True, out=out)
            self.assertFalse(out.exists())

    def test_mutated_prepared_input_fails_even_with_forged_binding_digest(self):
        self.prepare(write=True)
        changed = self.out / "scene/opening.js"
        changed.write_text("wrong")
        binding = json.loads((self.out / "source-binding.json").read_text())
        binding["files"]["scene/opening.js"] = P.sha(b"wrong")
        (self.out / "source-binding.json").write_bytes(P.json_bytes(binding))
        with self.assertRaises(ValueError):
            I.check_project(self.out, self.expected, self.source, self.production)

    def test_timeline_rejects_split_visuals_changed_gain_or_duration(self):
        data = json.loads((HERE / "preview-timeline.json").read_text())
        for edit in (lambda t: t["video_tracks"].append(t["video_tracks"][0]),
                     lambda t: t["audio_tracks"][0]["events"][0].update(volume=.5),
                     lambda t: t["project"].update(duration_frames=299)):
            changed = json.loads(json.dumps(data)); edit(changed)
            with self.assertRaises(ValueError):
                P.validate_timeline(P.json_bytes(changed))


class InspectionTests(unittest.TestCase):
    @staticmethod
    def wav(frames=480000):
        raw = io.BytesIO()
        with wave.open(raw, "wb") as wav:
            wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(48000)
            wav.writeframes(b"\x00\x00\xff\x7f" * frames)
        return raw.getvalue()

    def test_pcm_hash_is_exact_and_explicit_about_new_window_mix(self):
        data = self.wav(); result = I.wav_identity(data)
        self.assertEqual(result["pcm_s16le_sha256"], P.sha(b"\x00\x00\xff\x7f" * 480000))
        self.assertEqual(result["sample_frames"], 480000)
        self.assertIn("NOT_ASSERTED", result["same_as_120_second_postmix_prefix"])
        self.assertEqual(result["file_sha256"], hashlib.sha256(data).hexdigest())

    def test_wrong_sample_count_is_not_an_acceptable_audio_identity(self):
        for frames in (479999, 480001):
            with self.assertRaises(ValueError):
                I.wav_identity(self.wav(frames))

    def test_actual_video_count_size_rate_and_audio_required(self):
        from test_inspection_contracts import nominal_probe
        probe = nominal_probe()
        self.assertEqual(I.video_contract(probe)["video"]["nb_read_frames"], "300")
        for key, bad in (("width", 960), ("nb_read_frames", "299"), ("r_frame_rate", "30000/1001")):
            p = json.loads(json.dumps(probe)); p["streams"][0][key] = bad
            with self.assertRaises(ValueError):
                I.video_contract(p)
        with self.assertRaises(ValueError):
            I.video_contract({"streams": [probe["streams"][0]]})

    def test_mux_ledger_binds_final_timeline_and_native_audio(self):
        row = {"product": "final.mp4", "cmd": "mux", "md5": "a" * 32,
               "bytes": 42, "timeline_md5": "b" * 32, "audio_md5": "c" * 32}
        self.assertEqual(I.check_mux_ledger({"versions": [row]}, "a" * 32, 42, "b" * 32, "c" * 32), row)
        for key in ("md5", "bytes", "timeline_md5", "audio_md5", "cmd"):
            bad = dict(row); bad[key] = "changed"
            with self.assertRaises(ValueError):
                I.check_mux_ledger({"versions": [bad]}, "a" * 32, 42, "b" * 32, "c" * 32)
        with self.assertRaises(ValueError):
            I.check_mux_ledger({"versions": [{**row, "superseded": True}]}, "a" * 32, 42, "b" * 32, "c" * 32)

    def test_inspector_refuses_wrong_or_dirty_engine_before_import(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(I.subprocess, "run", return_value=mock.Mock(stdout="b" * 40)):
                with self.assertRaises(ValueError):
                    I.checked_engine(Path(temp).resolve(), "a" * 40)
            with mock.patch.object(I.subprocess, "run", side_effect=[mock.Mock(stdout="a" * 40), mock.Mock(stdout=" M vtx/infra.py")]):
                with self.assertRaises(ValueError):
                    I.checked_engine(Path(temp).resolve(), "a" * 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
