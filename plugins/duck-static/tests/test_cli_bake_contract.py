"""Baked static-duck timelines must retain the raw event fade contract."""
import importlib
import json
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
CLI = REPO / 'plugins' / 'duck-static' / 'bed_static_duck.py'
VTX = REPO / 'vtx' / 'vtx.py'


class DuckCliBakeContract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'timeline.json'
        self.backup = self.root / 'timeline.pre-duck.json'
        with wave.open(str(self.root / 'cue.wav'), 'wb') as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(48000)
            wav.writeframes(b'\0' * (48000 * 2 * 2))
        self.timeline = {
            'version': 2,
            'project': {'fps': 30, 'width': 1920, 'height': 1080,
                        'duration_frames': 30},
            'sources': [{'id': 'cue', 'path': 'cue.wav'}],
            'video_tracks': [], 'subtitles': [],
            'audio_tracks': [
                {'name': 'voice', 'events': [
                    {'src': 'cue', 'in': 0, 'out': 6, 'volume': 0.8}]},
                {'name': 'bed', 'events': [
                    {'src': 'cue', 'in': 0, 'out': 30, 'volume': 1}]},
            ],
        }

    def write_timeline(self):
        self.path.write_text(json.dumps(self.timeline, indent=2), encoding='utf-8')
        return self.path.read_bytes()

    def run_bake(self, *args):
        return subprocess.run([sys.executable, str(CLI), '-t', str(self.path), *args],
                              cwd=REPO, text=True, capture_output=True)

    def snapshot(self):
        return {str(path.relative_to(self.root)):
                (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.root.rglob('*') if path.is_file()}

    def assert_validates(self):
        result = subprocess.run([sys.executable, str(VTX), '-t', str(self.path),
                                 'validate'], cwd=REPO, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('校验通过', result.stdout)

    def test_short_generated_event_rejected_before_any_writes(self):
        self.write_timeline()
        for existing_backup in (False, True):
            if existing_backup:
                self.backup.write_bytes(b'pre-existing backup must stay untouched\n')
            for flags in ((), ('--no-backup',)):
                with self.subTest(existing_backup=existing_backup, flags=flags):
                    self.write_timeline()
                    before = self.snapshot()
                    result = self.run_bake(*flags)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertEqual(self.snapshot(), before)
                    for detail in ('bed', 'events[0]', 'fade_in_frames=5',
                                   'fade_out_frames=6', 'duration_frames=6',
                                   'duck_strategy', '--attack', '--release'):
                        self.assertIn(detail, result.stderr)
                    self.assertNotIn('Traceback', result.stderr)

    def test_original_edge_fades_that_overlap_after_split_are_not_silently_changed(self):
        self.timeline['audio_tracks'][0]['events'][0]['out'] = 13
        self.timeline['audio_tracks'][1]['events'][0].update(
            fade_in_frames=10, fade_out_frames=10)
        self.write_timeline()
        before = self.snapshot()
        result = self.run_bake()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)
        for detail in ('events[0]', 'fade_in_frames=10', 'fade_out_frames=8',
                       'duration_frames=13'):
            self.assertIn(detail, result.stderr)

    def test_valid_bake_preserves_shared_math_backup_and_validate_round_trip(self):
        self.timeline['audio_tracks'][0]['events'][0]['out'] = 13
        original = self.write_timeline()
        result = self.run_bake()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.backup.read_bytes(), original)
        baked = json.loads(self.path.read_text(encoding='utf-8'))
        core = importlib.import_module('plugins.duck-static.bed_static_duck')
        expected = core.transform_events(self.timeline['audio_tracks'][1]['events'],
                                         [[0, 13]], -8.0, -4.0, 5, 8)
        self.assertEqual(baked['audio_tracks'][1]['events'], expected)
        self.assertIn('bed_static_duck', baked['_meta'])
        self.assert_validates()
        # Same parameters remain idempotent, preserving the original backup.
        before = (self.path.read_bytes(), self.backup.read_bytes())
        again = self.run_bake()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual((self.path.read_bytes(), self.backup.read_bytes()), before)

    def test_shorter_attack_release_produce_valid_exact_boundary_without_backup(self):
        self.write_timeline()
        result = self.run_bake('--attack', '2', '--release', '4', '--no-backup')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.backup.exists())
        baked = json.loads(self.path.read_text(encoding='utf-8'))
        event = baked['audio_tracks'][1]['events'][0]
        self.assertEqual(event['fade_in_frames'] + event['fade_out_frames'],
                         event['out'] - event['in'])
        self.assert_validates()


if __name__ == '__main__':
    unittest.main()
