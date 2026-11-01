"""Real stdin-byte ASR helper regressions; no model, network or mocked publisher.

Load by absolute path under isolated installed Python as well: ROOT then names
archived scripts, while vtx.api must come from that interpreter's installed wheel.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from vtx.api import read_timeline_for_write, timeline_transaction

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "apps/asr/tl_merge.py"
PRECISE = b'0.12345678901234567890123456789'


def fixture():
    return {"version": 2, "project": {"fps": 30, "width": 1920, "height": 1080,
            "duration_frames": 180}, "sources": [{"id": "source", "path": "absent.wav"}],
            "video_tracks": [], "audio_tracks": [],
            "subtitles": [{"in": 90, "out": 110, "text": "existing"}],
            "unknown": {"typed": [False, 1, "原文"]}}


def tree(root):
    return {str(p.relative_to(root)): (p.stat().st_mode, p.stat().st_ino,
            p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns,
            p.read_bytes() if p.is_file() else None)
            for p in [root, *sorted(root.rglob("*"))]}


def subtitle(extension):
    return b'[{"in":0,"out":24,"text":"precise source","unknown":' + extension + b'}]'


class ASRStrictIngress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="asr-strict-ingress-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "timeline.json"
        self.save(fixture())

    def save(self, document):
        self.path.write_bytes(("\n" + json.dumps(document, ensure_ascii=True, indent=2) + "\n").encode())

    def cli(self, raw, dry=False):
        return subprocess.run([sys.executable, "-I", "-B", str(SCRIPT),
                               "-t", str(self.path), *(["--dry-run"] if dry else [])],
                              input=raw, capture_output=True, timeout=30,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))

    def rejected(self, raw, message=None):
        for dry in (False, True):
            with self.subTest(dry=dry):
                before = tree(self.root)
                result = self.cli(raw, dry)
                self.assertEqual(result.returncode, 1, result)
                self.assertTrue(result.stdout.decode().startswith("✗ "), result)
                self.assertEqual(result.stderr, b"", result)
                if message:
                    self.assertIn(message, result.stdout.decode())
                self.assertEqual(tree(self.root), before)

    def test_original_raw_confidence_precision_rejects_before_any_write(self):
        raw = (b'[{"in":0,"out":24,"text":"precise source","confidence":'
               + PRECISE + b'}]')
        self.rejected(raw, "numeric precision would be lost on write")

    def test_unknown_nested_precision_and_unicode_are_not_predecoded(self):
        self.rejected(subtitle('{"原文":[{"confidence":'.encode() + PRECISE + b'}]}'),
                      "numeric precision would be lost on write")

    def test_malformed_utf8_duplicates_nonfinite_underflow_and_deep_are_human_errors(self):
        for raw in (b'{', b'"\xff"', subtitle(b'{"x":1,"x":2}'),
                    *[subtitle(token) for token in (b'NaN', b'Infinity', b'-Infinity',
                                                   b'1e999', b'1e-999')],
                    b'[' * 100000 + b'0' + b']' * 100000):
            with self.subTest(raw_prefix=raw[:80]):
                self.rejected(raw)

    def test_precision_failure_leaves_existing_managed_history_and_backup_unchanged(self):
        snap = read_timeline_for_write(self.path)
        result = timeline_transaction(self.path, dict(operation="adopt", op_id="adopt",
                                      base_revision=snap["revision"]))
        self.assertTrue(result["ok"], result)
        backup = self.root / "cache/reports/subtitles_backup_existing.json"
        backup.parent.mkdir(parents=True)
        backup.write_text('["unchanged"]\n')
        self.rejected(subtitle(PRECISE), "numeric precision would be lost on write")

    def test_safe_controls_default_replace_unicode_unknown_and_dry_run(self):
        raw = ('[{"in":0,"out":24,"text":"新字幕 😀","unknown":{"typed":[false,1],'
               '"half":0.5,"decimal":0.1,"exponent":1e-3,"big":9007199254740993}}]').encode()
        before = tree(self.root)
        dry = self.cli(raw, True)
        self.assertEqual(dry.returncode, 0, dry)
        self.assertIn("✓ dry-run：未写盘。", dry.stdout.decode())
        self.assertEqual(before, tree(self.root))
        real = self.cli(raw)
        self.assertEqual(real.returncode, 0, real)
        self.assertIn("✓ 合并完成：", real.stdout.decode())
        summary = json.loads(real.stdout.decode().split("：", 1)[1])
        self.assertEqual(summary["old_n"], 1)
        self.assertEqual(summary["new_n"], 1)
        self.assertFalse(summary["dry_run"])
        self.assertTrue(Path(summary["backup"]).is_file())
        after = read_timeline_for_write(self.path)["document"]
        self.assertEqual(after["subtitles"], json.loads(raw))
        self.assertEqual(after["unknown"], fixture()["unknown"])
        self.assertNotIn("_vtx", after)
        self.assertEqual(json.loads(Path(summary["backup"]).read_bytes()), fixture()["subtitles"])

    def test_array_shape_and_invalid_timeline_remain_rejected(self):
        self.rejected(b'null', "subtitles 必须是数组")
        self.rejected(b'{"in":0,"out":24,"text":"not-array"}', "subtitles 必须是数组")
        self.path.write_bytes(b'{"version":2}')
        self.rejected(subtitle(b'0.5'), "timeline.json 不可安全写入")

    def test_python_helper_already_decoded_values_keep_old_contract(self):
        spec = importlib.util.spec_from_file_location("asr_ingress_python_helper", SCRIPT)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        # A Python float cannot remember whether its source token was precise.
        already_decoded = json.loads(subtitle(PRECISE))
        summary = helper.merge_subtitles(self.path, already_decoded)
        self.assertFalse(summary["dry_run"])
        self.assertEqual(read_timeline_for_write(self.path)["document"]["subtitles"], already_decoded)


if __name__ == "__main__":
    unittest.main()
