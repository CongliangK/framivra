"""Legacy unmanaged-identity precheck semantics for the frame operation.

The frame identity gate must match the write path (preserve_unmanaged=True):
legacy source IDs are references, managed stable IDs stay strict, and the
schema (including the volume maximum) is never relaxed. All projects are
external temporary directories; no media or renderer is required.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from vtx.api import frame_timeline


def legacy_document(source_id="中文素材", *, volume=None):
    event = {"src": source_id, "in": 0, "out": 6, "volume": 0.8}
    if volume is not None:
        event["volume"] = volume
    return {"version": 2, "project": {"fps": 30, "width": 1920, "height": 1080,
                                      "duration_frames": 12},
            "sources": [{"id": source_id, "path": "source.mp4"}],
            "video_tracks": [], "audio_tracks": [{"name": "voice", "events": [event]}],
            "subtitles": []}


class FrameLegacyIdentity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "timeline.json"

    def save(self, doc):
        self.path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def code(self, **kwargs):
        result = frame_timeline(self.path, **kwargs)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["image"])
        return result

    def test_legacy_unicode_source_id_passes_identity_precheck(self):
        self.save(legacy_document())
        # Media is absent, so the run must stop inside core validation/probing
        # -- the previous structure gate falsely reported legacy Unicode source
        # IDs as render.structure failures before any asset was touched.
        result = self.code(frame=1, render=True)
        self.assertEqual(result["diagnostics"][0]["code"], "render.process")
        self.assertIn("source.mp4", result["diagnostics"][0]["message"])
        for diagnostic in result["diagnostics"]:
            self.assertNotIn("/sources/0/id", diagnostic["message"])

    def test_duplicate_and_nonstring_legacy_source_id_rejected(self):
        doc = legacy_document()
        doc["sources"].append({"id": "中文素材", "path": "other.mp4"})
        self.save(doc)
        # Duplicates are refused even before the frame identity gate, by the
        # read-only reference layer; the preserve-unmanaged structure check
        # behind it carries the same refusal (defense in depth).
        result = self.code(frame=1, render=True)
        self.assertEqual(result["diagnostics"][0]["code"], "reference.ambiguous")
        self.assertIn("duplicate source identity", result["diagnostics"][0]["message"])
        from vtx.timeline_structure import structure_errors
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("/sources/1/id: duplicate source identity 中文素材",
                      structure_errors(stored, preserve_unmanaged=True))
        doc = legacy_document()
        doc["sources"] = [{"id": 7, "path": "source.mp4"}]
        doc["audio_tracks"][0]["events"][0]["src"] = 7
        self.save(doc)
        result = self.code(frame=1, render=True)
        self.assertEqual(result["diagnostics"][0]["address"], "/sources/0/id")
        self.assertEqual(result["diagnostics"][0]["code"], "shape.type")
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("/sources/0/id: expected source string",
                      structure_errors(stored, preserve_unmanaged=True))

    def test_managed_unicode_id_still_rejected(self):
        doc = legacy_document()
        doc["_vtx"] = {"protocol": 1, "project_id": "p1", "head": "h1"}
        self.save(doc)
        # Managed documents never get the legacy carve-out: reference itself
        # refuses the Unicode stable ID before any renderer authorization.
        result = self.code(frame=1, render=True)
        self.assertEqual(result["diagnostics"][0]["code"], "reference.identity")
        messages = [d["message"] for d in result["diagnostics"]]
        self.assertTrue(any("/sources/0/id: invalid stable identity" in m for m in messages))

    def test_legacy_volume_2_015_still_rejected(self):
        self.save(legacy_document(volume=2.015))
        result = self.code(frame=1, render=True)
        self.assertEqual(result["diagnostics"][0]["code"], "render.structure")
        self.assertIn("/audio_tracks/0/events/0/volume: outside maximum",
                      result["diagnostics"][0]["message"])
        # Never clamped to the boundary: the stored value itself is refused.
        self.assertIn("2.015", self.path.read_text(encoding="utf-8"))

    def test_unauthorized_and_structure_failure_never_touch_renderer_or_disk(self):
        self.save(legacy_document(volume=2.015))
        raw = self.path.read_bytes()
        with patch("vtx.pipeline.infra.Ctx.__init__", side_effect=AssertionError("Ctx")), \
                patch("subprocess.run", side_effect=AssertionError("process")):
            unauthorized = frame_timeline(self.path, frame=1)
            self.assertEqual(unauthorized["diagnostics"][0]["code"], "render.not_authorized")
            failed = self.code(frame=1, render=True)
        self.assertEqual(failed["diagnostics"][0]["code"], "render.structure")
        self.assertEqual(list(self.root.iterdir()), [self.path])
        self.assertEqual(raw, self.path.read_bytes())

    def test_active_plugin_still_rejected_before_structure_and_ctx(self):
        # Schema-invalid (volume 2.015) yet reference-readable: the static
        # plugin gate must fire before the structure check and any Ctx.
        self.save(legacy_document(volume=2.015))
        with patch("vtx.pluginlib.hooks.active_plugins", return_value=[{"name": "unsafe"}]), \
                patch("vtx.pipeline.infra.Ctx.__init__", side_effect=AssertionError("Ctx")):
            result = self.code(frame=1, render=True)
        self.assertEqual(result["diagnostics"][0]["code"], "plugins.unsupported")
        self.assertEqual(list(self.root.iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
