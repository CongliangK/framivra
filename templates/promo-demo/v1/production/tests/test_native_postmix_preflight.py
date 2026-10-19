"""Exact PCM/bin checks with synthetic audio, no renderer or published writes.

Run: python -m unittest discover -s tests -p test_native_postmix_preflight.py -v
Real WAV decode tests require installed ffmpeg/ffprobe; Python is standard-library only.
"""
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import wave


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("native_postmix_preflight", ROOT / "scripts/preflight.py")
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)
FRAMES = 5760000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def oracle_fixture():
    """Independent integer PCM16 impulses and analytically known stereo extrema."""
    pcm16 = bytearray(FRAMES * 4)
    f32 = bytearray(FRAMES * 8)
    expected = []

    def impulse(sample, channel, value):
        struct.pack_into("<h", pcm16, (sample * 2 + channel) * 2, value)
        struct.pack_into("<f", f32, (sample * 2 + channel) * 4, value / 32768)

    for index in range(480):
        frame = 1680 + index
        start, end = frame * 1600, (frame + 1) * 1600
        low, high = (-32768, 32767) if index == 0 else (-1000 - index, 2000 + index)
        # Alternate channels. Every bin has an impulse at both half-open edges.
        impulse(start, index % 2, low)
        impulse(end - 1, 1 - index % 2, high)
        expected.append(dict(frame=frame, sample_start=start, sample_end=end,
                             min=low / 32768, max=high / 32768,
                             value=max(-low, high) / 32768))
    # Values outside S5 must affect the full hash, never the 480 bins.
    impulse(0, 0, -32768)
    impulse(2687999, 1, 32767)
    impulse(3456000, 0, -32768)
    impulse(FRAMES - 1, 1, 32767)
    return bytes(pcm16), bytes(f32), expected


def write_wav(path, pcm, channels=2, rate=48000, width=2):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(pcm)


class WaveformSamplesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, cls.pcm, cls.expected = oracle_fixture()

    def test_all_480_bins_equal_independent_integer_oracle(self):
        self.assertEqual(preflight.pcm_waveform_samples(self.pcm), self.expected)

    def test_rejects_short_extra_and_partial_frames(self):
        for pcm in (b"", self.pcm[:-8], self.pcm[:-1], self.pcm + b"\0" * 8):
            with self.subTest(length=len(pcm)), self.assertRaises(ValueError):
                preflight.pcm_waveform_samples(pcm)

    def test_error_stderr_rejected_even_when_decoder_reports_success(self):
        result = subprocess.CompletedProcess([], 0, stdout=self.pcm, stderr=b"corrupt audio")
        with mock.patch.object(preflight.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(ValueError, "decode error"):
                preflight.decode("supplied.wav", "pcm_f32le", "f32le")

    def test_rejects_nonfinite_and_out_of_range_even_outside_s5(self):
        for sample, value in ((0, float("nan")), (0, float("inf")),
                              (2688000, -float("inf")), (FRAMES - 1, 1.25)):
            bad = bytearray(self.pcm)
            struct.pack_into("<f", bad, sample * 8, value)
            with self.subTest(sample=sample, value=value), self.assertRaises(ValueError):
                preflight.pcm_waveform_samples(bad)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
class NativePostmixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)
        cls.pcm16, cls.pcm, cls.expected = oracle_fixture()
        cls.wav = cls.base / "native.wav"
        write_wav(cls.wav, cls.pcm16)

    def test_native_decode_matches_independent_f32_oracle(self):
        pcm, actual_hash = preflight.native_pcm(self.wav)
        self.assertEqual(len(pcm), FRAMES * 8)
        self.assertEqual(actual_hash, digest(self.pcm))
        self.assertEqual(pcm, self.pcm)
        self.assertEqual(preflight.pcm_waveform_samples(pcm), self.expected)

    def test_rejects_same_decoded_byte_count_wrong_channels_rate_or_codec(self):
        # Each file would decode to the same byte count as the valid source.
        cases = (("mono", self.pcm16, 1, 48000, 2),
                 ("wrong-rate", self.pcm16, 2, 44100, 2),
                 ("pcm24", bytes(FRAMES * 6), 2, 48000, 3))
        for name, pcm, channels, rate, width in cases:
            path = self.base / (name + ".wav")
            write_wav(path, pcm, channels, rate, width)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "pcm_s16le WAV stream"):
                preflight.native_pcm(path)

    def test_rejects_wrong_decoded_frame_count(self):
        for name, pcm in (("short", self.pcm16[:-4]), ("long", self.pcm16 + b"\0" * 4)):
            path = self.base / (name + ".wav")
            write_wav(path, pcm)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "5760000"):
                preflight.native_pcm(path)

    def test_probe_error_stderr_rejected_even_with_successful_metadata(self):
        metadata = {"format": {"format_name": "wav"}, "streams": [dict(
            codec_type="audio", codec_name="pcm_s16le", sample_rate="48000",
            channels=2, bits_per_sample=16)]}
        result = subprocess.CompletedProcess([], 0, stdout=json.dumps(metadata), stderr="corrupt WAV")
        with mock.patch.object(preflight.subprocess, "run", return_value=result) as run:
            with self.assertRaisesRegex(ValueError, "ffprobe reported"):
                preflight.native_pcm(self.wav)
        self.assertEqual(run.call_count, 1)

    def test_rejects_malformed_truncated_missing_and_non_wav_sources(self):
        malformed = self.base / "malformed.wav"
        malformed.write_bytes(b"not a wave file")
        truncated = self.base / "truncated.wav"
        truncated.write_bytes(self.wav.read_bytes()[:-1024])
        not_wav = self.base / "renamed.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                        "anullsrc=r=48000:cl=stereo", "-t", "0.01", "-c:a", "pcm_s16le",
                        "-f", "aiff", str(not_wav)], check=True, capture_output=True, timeout=30)
        for path in (malformed, truncated, self.base / "missing.wav", not_wav):
            with self.subTest(path=path.name), self.assertRaises((ValueError, subprocess.SubprocessError)):
                preflight.native_pcm(path)

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        for relative in ("timeline.json", "entry-manifest.json", "media/paper-recipe.json",
                         "production/caption-font.json"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        # Test only the paired postmix branch. Missing original private sources
        # remain explicitly reported; these fixtures never claim a full pass.
        (self.root / "runtime-dependencies.json").write_text("[]")
        self.binding = json.loads((ROOT / "audio/native-postmix.json").read_text())
        self.envelope = json.loads((ROOT / "audio/waveform-S5.json").read_text())
        self.provenance = json.loads((ROOT / "audio/native-audio-provenance.json").read_text())
        self.binding["postmix"]["sha256"] = preflight.sha(self.wav)
        self.binding["postmix"]["pcm_sha256"] = digest(self.pcm)
        self.envelope["samples"] = copy.deepcopy(self.expected)
        self.save_binding()

    def write_json(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True, indent=2))
        return preflight.sha(path)

    def save_binding(self):
        self.provenance["postmix_pcm_contract"] = {
            k: v for k, v in self.binding["postmix"].items() if k != "mix_recipe_sha256"}
        self.provenance["reference_execution_summary"]["postmix_sha256"] = self.binding["postmix"]["sha256"]
        identity = self.write_json("audio/native-audio-provenance.json", self.provenance)
        self.binding["portable_provenance_sha256"] = identity
        self.binding["postmix"]["mix_recipe_sha256"] = identity
        self.envelope["provenance"] = copy.deepcopy(self.binding["postmix"])
        self.binding["waveform_sha256"] = self.write_json("audio/waveform-S5.json", self.envelope)
        self.write_json("audio/native-postmix.json", self.binding)
        self.write_json("src/assets/media-bindings.json", {"waveform": self.envelope})

    def check(self, postmix=None):
        with mock.patch.object(preflight, "ROOT", self.root):
            return preflight.check(caption_font=self.root / "unavailable-font", postmix=postmix)

    def test_no_supplied_audio_never_claims_pcm_or_bin_verification(self):
        result = self.check()
        self.assertTrue(result["waveform"]["reference_link_verified"])
        self.assertTrue(result["waveform"]["portable_provenance_verified"])
        self.assertFalse(result["waveform"]["supplied_postmix_pcm_verified"])
        self.assertFalse(result["waveform"]["supplied_postmix_bins_verified"])
        self.assertFalse(result["waveform"]["execution_receipt_verified"])

    def test_check_verifies_actual_pcm_and_every_bin_without_execution_claim(self):
        before = self.check()
        result = self.check(self.wav)
        self.assertEqual(result["source_errors"], before["source_errors"])
        self.assertTrue(result["waveform"]["supplied_postmix_pcm_verified"])
        self.assertTrue(result["waveform"]["supplied_postmix_bins_verified"])
        self.assertFalse(result["waveform"]["execution_receipt_verified"])
        self.assertFalse(result["production_ready"])
        self.assertFalse(result["browser_or_engine_run"])
        self.assertEqual(result["pending_acceptance"], before["pending_acceptance"])

    def test_self_consistent_altered_bins_cannot_pass_supplied_pcm_check(self):
        for index in (0, 239, 479):
            with self.subTest(bin=index):
                self.envelope["samples"] = copy.deepcopy(self.expected)
                self.envelope["samples"][index].update(min=-0.125, max=0.25, value=0.25)
                self.save_binding()
                before = self.check()
                self.assertTrue(before["waveform"]["reference_link_verified"])
                self.assertTrue(before["waveform"]["portable_provenance_verified"])
                result = self.check(self.wav)
                self.assertEqual(result["source_errors"], before["source_errors"] + [
                    "actual postmix PCM bins differ from the S5 waveform reference"])
                self.assertTrue(result["waveform"]["supplied_postmix_pcm_verified"])
                self.assertFalse(result["waveform"]["supplied_postmix_bins_verified"])

    def test_altered_minimum_and_maximum_are_checked_besides_value(self):
        for index, key, value in ((1, "min", -0.015625), (0, "max", 0.125),
                                   (1, "min", math.nextafter(self.expected[1]["min"], 0))):
            with self.subTest(field=key):
                self.envelope["samples"] = copy.deepcopy(self.expected)
                # Keep the dominant peak/value while changing only one extremum.
                # Even a single representable JSON float step is not tolerated.
                self.envelope["samples"][index][key] = value
                self.save_binding()
                self.assertFalse(preflight.waveform_errors(self.envelope, self.binding))
                result = self.check(self.wav)
                self.assertTrue(result["waveform"]["supplied_postmix_pcm_verified"])
                self.assertFalse(result["waveform"]["supplied_postmix_bins_verified"])

    def test_hash_mismatch_blocks_verification_even_when_bins_match(self):
        self.binding["postmix"]["pcm_sha256"] = "0" * 64
        self.save_binding()
        result = self.check(self.wav)
        self.assertIn("actual postmix PCM differs from the S5 waveform reference", result["source_errors"])
        self.assertFalse(result["waveform"]["supplied_postmix_pcm_verified"])
        self.assertFalse(result["waveform"]["supplied_postmix_bins_verified"])

    def test_nonfinite_envelope_and_malformed_present_audio_fail_closed(self):
        self.envelope["samples"][0]["min"] = float("nan")
        self.save_binding()
        result = self.check(self.wav)
        self.assertIn("malformed or unbound real postmix waveform", result["source_errors"])
        self.assertFalse(result["waveform"]["supplied_postmix_bins_verified"])
        malformed = self.root / "bad.wav"
        malformed.write_bytes(b"invalid")
        result = self.check(malformed)
        self.assertTrue(any("invalid or unbound real postmix waveform:" in e for e in result["source_errors"]))
        self.assertFalse(result["waveform"]["supplied_postmix_pcm_verified"])
        self.assertFalse(result["waveform"]["supplied_postmix_bins_verified"])

    def test_missing_or_malformed_envelope_keeps_explicit_false_verification(self):
        path = self.root / "audio/waveform-S5.json"
        path.unlink()
        without_audio = self.check()
        self.assertIn("missing real postmix PCM waveform", without_audio["pending_acceptance"])
        result = self.check(self.wav)
        self.assertEqual(result["source_errors"], without_audio["source_errors"] + [
            "cannot verify supplied postmix without the S5 waveform reference"])
        self.assertFalse(result["waveform"]["supplied_postmix_bins_verified"])
        path.write_text("malformed JSON")
        result = self.check(self.wav)
        self.assertTrue(any("invalid or unbound real postmix waveform:" in e for e in result["source_errors"]))
        self.assertFalse(result["waveform"]["supplied_postmix_pcm_verified"])
        self.assertFalse(result["waveform"]["supplied_postmix_bins_verified"])


if __name__ == "__main__":
    unittest.main()
