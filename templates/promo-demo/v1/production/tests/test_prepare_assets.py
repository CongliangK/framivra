"""Source-only tests: no private media, network, or third-party Python modules.

Run: python -m unittest discover -s tests -p test_prepare_assets.py -v
The decoder tests require installed ffmpeg/ffprobe and generate tiny FLAC fixtures.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).absolute().parents[1] / "scripts" / "prepare_assets.py"
LOADER = importlib.util.spec_from_file_location("prepare_assets", SCRIPT)
assets = importlib.util.module_from_spec(LOADER)
LOADER.loader.exec_module(assets)
PATHS = ["audio/" + role + ".flac" for role in assets.STEMS] + [assets.PAPER]


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # This is our newly created fixture, not an untrusted input pack. macOS
        # tempfile paths may use /var -> /private/var; test the canonical path.
        self.base = Path(self.temp.name).resolve()
        self.root, self.pack = self.base / "production", self.base / "pack"
        for root in (self.root, self.pack):
            for name in ("audio", "media"):
                (root / name).mkdir(parents=True)
        self.payloads = {path: ("test payload " + path).encode() for path in PATHS}
        for path, data in self.payloads.items():
            (self.pack / path).write_bytes(data)
        self.stems = dict(format="framivra-lossless-stems/1", stems=[
            dict(id=role, path=path, bytes=len(self.payloads[path]),
                 sha256=assets.digest(self.payloads[path]), pcm24_sha256="1" * 64,
                 sample_rate=48000, channels=2, sample_frames=5760000)
            for role, path in zip(assets.STEMS, PATHS)])
        self.paper = dict(package_path=assets.PAPER,
                          sha256=assets.digest(self.payloads[assets.PAPER]),
                          probe={"format": {"size": str(len(self.payloads[assets.PAPER]))}})
        self.write_manifests()

    def write_manifests(self):
        (self.root / "audio/stems.json").write_text(json.dumps(self.stems))
        (self.root / "media/paper-recipe.json").write_text(json.dumps(self.paper))

    def prepare(self, **kwargs):
        return assets.prepare_assets(kwargs.get("pack", self.pack), kwargs.get("root", self.root))

    def assert_empty(self):
        self.assertTrue(all(not os.path.lexists(self.root / path) for path in PATHS))

    def assert_rejected(self, **kwargs):
        with self.assertRaises((OSError, ValueError, KeyError, TypeError)):
            self.prepare(**kwargs)

    @mock.patch.object(assets, "validate_payload")
    def test_imports_only_five_files_after_all_checks(self, decode):
        (self.pack / "unrelated.txt").write_text("not imported")
        decode.side_effect = lambda *_: self.assert_empty()
        result = self.prepare()
        self.assertEqual(result["installed"], PATHS)
        self.assertEqual(decode.call_count, 5)
        self.assertFalse((self.root / "unrelated.txt").exists())
        for path in PATHS:
            self.assertEqual((self.root / path).read_bytes(), self.payloads[path])

    @mock.patch.object(assets, "validate_payload")
    def test_identical_files_are_retained_unchanged(self, _):
        self.prepare()
        before = {p: (self.root / p).stat() for p in PATHS}
        result = self.prepare()
        self.assertEqual(result["installed"], [])
        self.assertEqual(result["retained"], PATHS)
        for p in PATHS:
            after = (self.root / p).stat()
            self.assertEqual((before[p].st_ino, before[p].st_mtime_ns),
                             (after.st_ino, after.st_mtime_ns))

    @mock.patch.object(assets, "validate_payload")
    def test_differing_last_destination_aborts_without_other_installs(self, decode):
        (self.root / PATHS[-1]).write_bytes(b"keep existing")
        self.assert_rejected()
        self.assertEqual((self.root / PATHS[-1]).read_bytes(), b"keep existing")
        self.assertTrue(all(not (self.root / p).exists() for p in PATHS[:-1]))
        decode.assert_not_called()

    def test_missing_member_aborts_before_install(self):
        (self.pack / PATHS[-1]).unlink()
        self.assert_rejected()
        self.assert_empty()

    def test_truncated_corrupt_and_oversized_members(self):
        p = self.pack / PATHS[0]
        original = p.read_bytes()
        for bad in (original[:-1], bytes(len(original)), original + b"x"):
            p.write_bytes(bad)
            self.assert_rejected()
            self.assert_empty()

    def test_source_file_symlink(self):
        p = self.pack / PATHS[0]
        p.rename(self.pack / "real.flac")
        p.symlink_to(self.pack / "real.flac")
        self.assert_rejected()
        self.assert_empty()

    def test_source_directory_symlink(self):
        (self.pack / "audio").rename(self.pack / "real-audio")
        (self.pack / "audio").symlink_to(self.pack / "real-audio", target_is_directory=True)
        self.assert_rejected()
        self.assert_empty()

    def test_source_root_and_ancestor_symlinks(self):
        alias = self.base / "alias"
        alias.symlink_to(self.pack, target_is_directory=True)
        self.assert_rejected(pack=alias)
        alias.unlink()
        alias.symlink_to(self.base, target_is_directory=True)
        self.assert_rejected(pack=alias / "pack")
        self.assert_empty()

    def test_destination_file_symlink_even_when_identical(self):
        (self.root / PATHS[0]).symlink_to(self.pack / PATHS[0])
        self.assert_rejected()
        self.assertTrue((self.root / PATHS[0]).is_symlink())
        self.assertTrue(all(not (self.root / p).exists() for p in PATHS[1:]))

    def test_destination_directory_symlink(self):
        (self.root / "audio").rename(self.root / "real-audio")
        (self.root / "audio").symlink_to(self.root / "real-audio", target_is_directory=True)
        self.assert_rejected()
        self.assert_empty()

    def test_destination_root_symlink(self):
        alias = self.base / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assert_rejected(root=alias)
        self.assert_empty()

    def test_fifo_and_directory_are_not_files(self):
        path = self.pack / PATHS[0]
        path.unlink()
        os.mkfifo(path)
        self.assert_rejected()
        path.unlink()
        path.mkdir()
        self.assert_rejected()
        self.assert_empty()

    def test_manifest_symlink(self):
        path = self.root / "audio/stems.json"
        path.rename(self.root / "audio/real-stems.json")
        path.symlink_to(self.root / "audio/real-stems.json")
        self.assert_rejected()
        self.assert_empty()

    def test_manifest_paths_cannot_escape_allowlist(self):
        for path in ("/tmp/T17.flac", "../T17.flac", "audio/../T17.flac", "audio//T17.flac"):
            self.stems["stems"][0]["path"] = path
            self.write_manifests()
            self.assert_rejected()
            self.assert_empty()
        self.stems["stems"][0]["path"] = PATHS[0]
        self.paper["package_path"] = "../paper-background.mp4"
        self.write_manifests()
        self.assert_rejected()
        self.assert_empty()

    def test_parent_traversal_in_pack_path(self):
        self.assert_rejected(pack=self.pack / ".." / "pack")
        self.assert_empty()

    def test_invalid_contract_and_hash(self):
        row = self.stems["stems"][0]
        for key, wrong in (("sample_rate", 44100), ("channels", 1),
                           ("sample_frames", 5759999), ("sha256", "bad"),
                           ("pcm24_sha256", None), ("bytes", True),
                           ("bytes", assets.MAX_PACK_BYTES + 1)):
            original = row[key]
            row[key] = wrong
            self.write_manifests()
            self.assert_rejected()
            self.assert_empty()
            row[key] = original
        self.stems["stems"][1]["id"] = "T17"
        self.write_manifests()
        self.assert_rejected()
        self.assert_empty()

    @mock.patch.object(assets, "validate_payload")
    def test_last_decode_failure_leaves_no_installs(self, decode):
        decode.side_effect = [None, None, None, None, ValueError("corrupt paper")]
        self.assert_rejected()
        self.assert_empty()

    @mock.patch.object(assets, "validate_payload")
    def test_late_conflict_rolls_back_only_new_files(self, _):
        original_open = os.open
        def race(name, flags, *args, **kwargs):
            if name == "T20.flac" and flags & os.O_CREAT:
                (self.root / "audio/T20.flac").write_bytes(b"late conflicting file")
            return original_open(name, flags, *args, **kwargs)
        with mock.patch.object(os, "open", side_effect=race):
            self.assert_rejected()
        self.assertEqual((self.root / "audio/T20.flac").read_bytes(), b"late conflicting file")
        self.assertTrue(all(not (self.root / p).exists() for p in PATHS if "T20" not in p))

    @mock.patch.object(assets, "validate_payload")
    def test_write_failure_preserves_identical_files(self, _):
        (self.root / PATHS[0]).write_bytes(self.payloads[PATHS[0]])
        with mock.patch.object(os, "fsync", side_effect=OSError("simulated disk failure")):
            self.assert_rejected()
        self.assertEqual((self.root / PATHS[0]).read_bytes(), self.payloads[PATHS[0]])
        self.assertTrue(all(not (self.root / p).exists() for p in PATHS[1:]))

    @mock.patch.object(assets, "validate_payload")
    def test_input_change_after_snapshot_cannot_change_installed_bytes(self, decode):
        decode.side_effect = lambda *_: (self.pack / PATHS[0]).write_bytes(b"changed later")
        self.prepare()
        self.assertEqual((self.root / PATHS[0]).read_bytes(), self.payloads[PATHS[0]])


class DecoderTests(unittest.TestCase):
    @classmethod
    def flac(cls, seconds, rate=48000, channels="stereo", bits=24):
        return subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "anullsrc=r={}:cl={}".format(rate, channels), "-t", str(seconds),
            "-c:a", "flac", "-sample_fmt", "s32" if bits == 24 else "s16",
            "-f", "flac", "-"], check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=30).stdout

    @classmethod
    def setUpClass(cls):
        cls.silence = cls.flac(120)
        cls.pcm_hash = hashlib.sha256(bytes(assets.PCM_BYTES)).hexdigest()

    def spec(self, pcm=None):
        return dict(path="audio/T17.flac", pcm=pcm or self.pcm_hash)

    def test_real_full_length_pcm24_decode(self):
        assets.validate_payload(self.spec(), self.silence)

    def test_wrong_pcm_hash(self):
        with self.assertRaisesRegex(ValueError, "PCM hash mismatch"):
            assets.validate_payload(self.spec("0" * 64), self.silence)

    def test_short_and_long_audio_rejected(self):
        for seconds in (1, 121):
            with self.assertRaisesRegex(ValueError, "sample count"):
                assets.validate_payload(self.spec(), self.flac(seconds))

    def test_native_rate_channels_and_bitdepth_not_silently_converted(self):
        for kwargs in ({"rate": 44100}, {"channels": "mono"}, {"bits": 16}):
            with self.assertRaisesRegex(ValueError, "native 48 kHz stereo PCM24"):
                assets.validate_payload(self.spec(), self.flac(1, **kwargs))

    def test_real_truncated_and_corrupt_flac_rejected(self):
        for data in (self.silence[:-100], b"not flac"):
            with self.assertRaises(ValueError):
                assets.validate_payload(self.spec(), data)

    def test_wrong_paper_video_contract(self):
        data = subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "color=c=white:s=16x16:r=30", "-f", "lavfi", "-i",
            "anullsrc=r=48000:cl=stereo", "-t", "1", "-c:v", "libx264",
            "-c:a", "aac", "-movflags", "frag_keyframe+empty_moov",
            "-f", "mp4", "-"], check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=30).stdout
        with self.assertRaisesRegex(ValueError, "Wrong paper video format"):
            assets.validate_payload(dict(path=assets.PAPER, pcm=None), data)

    def test_decoder_error_stderr_fails_even_with_zero_status(self):
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout=b"", stderr=b"corrupt frame")):
            with self.assertRaisesRegex(ValueError, "corrupt frame"):
                assets.run(["ffmpeg"], b"")


if __name__ == "__main__":
    unittest.main()
