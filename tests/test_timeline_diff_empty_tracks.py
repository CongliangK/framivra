"""Real CPU reconciliation of zero-track diagnostic impact and selected media.

These source-bearing 1920x1080 fixtures run production through mux, not QC.
Pure edge and estimate controls remain in test_timeline_diff.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from vtx.timeline_diff import diff_timelines

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = os.environ.get("VTX_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "requires real ffmpeg and ffprobe")
class EmptyTrackImpactIntegration(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vtx-empty-impact-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = dict(os.environ, VTX_PIPELINE="0", VTX_VIDEO_SEGMENTS="4",
                        VTX_NO_NVENC="1", VTX_SEG_NVENC="0", VTX_IO_WORKERS="1",
                        VTX_FILTER_THREADS="1", VTX_DOCTOR_OFFLINE="1")

    def run_command(self, argv):
        result = subprocess.run(list(map(str, argv)), cwd=ROOT, env=self.env,
                                capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def timeline(self, duration, start, end):
        self.run_command([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                          "sine=frequency=1000:sample_rate=48000:duration=" + str(duration / 30),
                          "-c:a", "pcm_s16le", self.root / "tone.wav"])
        return {"version": 2,
                "project": {"fps": 30, "width": 1920, "height": 1080,
                            "duration_frames": duration},
                "sources": [{"id": "tone", "path": "tone.wav"}],
                "video_tracks": [], "subtitles": [{"in": start, "out": end, "text": "CROSS"}],
                "audio_tracks": [{"name": "tone", "events": [
                    {"src": "tone", "in": 0, "out": duration, "volume": 1.0}]}]}

    def write_timeline(self, timeline):
        (self.root / "timeline.json").write_text(json.dumps(timeline), encoding="utf-8")

    def render(self, *args):
        self.run_command([sys.executable, ROOT / "vtx/vtx.py", "-t",
                          self.root / "timeline.json", *args])

    def decoded_frames(self, path):
        stream = json.loads(self.run_command([
            FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames,width,height", "-of", "json", path]))["streams"][0]
        self.assertEqual((stream["width"], stream["height"]), (1920, 1080))
        return int(stream["nb_read_frames"])

    def selected_segments(self):
        entries = []
        for line in (self.root / "cache/vseg/list.txt").read_text().splitlines():
            match = re.fullmatch(r"file '(.+)'", line)
            self.assertIsNotNone(match, line)
            path = Path(match[1])
            name = re.fullmatch(r"seg(\d+)_(\d+)_[a-f0-9]+\.mp4", path.name)
            self.assertIsNotNone(name, path.name)
            entries.append({"index": int(name[1]), "start": int(name[2]), "name": path.name,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "mtime_ns": path.stat().st_mtime_ns, "frames": self.decoded_frames(path)})
        return entries

    def test_formal_cold_warm_endpoint_edit_matches_actual_four_segments(self):
        old = self.timeline(240, 58, 62)
        self.write_timeline(old)
        self.render("all", "--until", "mux")
        cold = self.selected_segments()
        self.assertEqual([entry["index"] for entry in cold], [0, 1, 2, 3])
        self.assertEqual([entry["start"] for entry in cold], [0, 60, 120, 180])
        self.assertEqual([entry["frames"] for entry in cold], [60, 60, 60, 60])
        self.assertEqual(self.decoded_frames(self.root / "out/final.mp4"), 240)
        self.render("video")
        self.assertEqual(self.selected_segments(), cold)
        unchanged = diff_timelines(old, self.root, old, self.root, nseg=4)["impact"]
        self.assertEqual(unchanged["vseg_invalid"]["count"], 0)
        self.assertEqual(unchanged["vseg_invalid"]["total"], len(cold))

        new = copy.deepcopy(old)
        new["subtitles"][0]["out"] = 63
        impact = diff_timelines(old, self.root, new, self.root, nseg=4)["impact"]
        self.write_timeline(new)
        self.render("all", "--until", "mux")
        edited = self.selected_segments()
        self.assertEqual(len(edited), len(cold))
        self.assertEqual([entry["frames"] for entry in edited], [60, 60, 60, 60])
        actual_changed = [i for i, (before, after) in enumerate(zip(cold, edited)) if before != after]
        self.assertEqual(actual_changed, [1])
        for key in ("name", "sha256", "mtime_ns"):
            self.assertNotEqual(cold[1][key], edited[1][key], key)
        actual_spans = [[edited[i]["start"], edited[i]["start"] + edited[i]["frames"]]
                        for i in actual_changed]
        self.assertEqual(impact["vseg_invalid"]["segments"], actual_spans)
        self.assertEqual(impact["vseg_invalid"]["count"], len(actual_changed))
        self.assertEqual(impact["vseg_invalid"]["total"], len(edited))
        self.assertEqual(impact["estimated_seconds"]["video"], 37.5)
        self.assertEqual(self.decoded_frames(self.root / "out/final.mp4"), 240)

    def test_short_guard_merges_requested_four_into_one_actual_segment(self):
        old = self.timeline(60, 2, 4)
        self.write_timeline(old)
        self.render("all", "--until", "mux")
        actual = self.selected_segments()
        self.assertEqual([(entry["start"], entry["frames"]) for entry in actual], [(0, 60)])
        self.assertEqual(self.decoded_frames(self.root / "out/final.mp4"), 60)
        new = copy.deepcopy(old)
        new["subtitles"][0]["out"] = 5
        impact = diff_timelines(old, self.root, new, self.root, nseg=4)["impact"]
        self.assertEqual(impact["vseg_invalid"]["total"], len(actual))
        self.assertEqual(impact["vseg_invalid"]["segments"], [[0, 60]])


if __name__ == "__main__":
    unittest.main()
