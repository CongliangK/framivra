"""Small real CPU cache-mode/phase-handoff probe, not the performance baseline."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires FFmpeg/FFprobe')
class CacheModesIntegration(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vtx-cache-modes-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'config.json'
        self.timeline = self.root / 'timeline.json'
        self.env = dict(os.environ, VTX_CONFIG=str(self.config), VTX_PIPELINE='0',
                        VTX_VIDEO_SEGMENTS='1', VTX_NO_NVENC='1', VTX_SEG_NVENC='0',
                        VTX_IO_WORKERS='1', VTX_FILTER_THREADS='1', VTX_DOCTOR_OFFLINE='1')
        # Authored test source, not a copied production/cache artifact.
        with wave.open(str(self.root / 'tone.wav'), 'wb') as output:
            output.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            output.writeframes(b''.join(struct.pack('<hh', value, value) for value in
                (round(6000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(96000))))
        self.timeline.write_text(json.dumps({
            'version': 2, 'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 60},
            'sources': [{'id': 'tone', 'path': 'tone.wav'}], 'video_tracks': [], 'subtitles': [],
            'audio_tracks': [{'name': 'voice', 'events': [{'src': 'tone', 'in': 0, 'out': 60, 'volume': 1.0}]}],
        }))

    def command(self, argv):
        result = subprocess.run(list(map(str, argv)), cwd=ROOT, env=self.env,
                                capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode(errors='replace'))
        return result.stdout

    def render(self, *args):
        return self.command([sys.executable, '-m', 'vtx', '-t', self.timeline, *args])

    def final_identity(self):
        final = self.root / 'out/final.mp4'
        video = self.command(['ffmpeg', '-v', 'error', '-i', final, '-map', '0:v:0', '-f', 'framemd5', '-'])
        audio = self.command(['ffmpeg', '-v', 'error', '-i', final, '-map', '0:a:0', '-f', 's16le', '-'])
        return hashlib.sha256(video).hexdigest(), hashlib.sha256(audio).hexdigest()

    def test_core_and_off_match_aggressive_pixels_pcm_and_separate_stage_handoff(self):
        source = (self.root / 'tone.wav').read_bytes()
        expected = None
        for mode in ('aggressive', 'core', 'off'):
            with self.subTest(mode=mode):
                self.config.write_text(json.dumps({'cache': {'mode': mode}}))
                if mode == 'off':
                    # Each command has its own empty working set. audio must
                    # reproduce prepared PCM rather than read the prior cache.
                    for stage in ('prepare', 'audio', 'video', 'mux'):
                        self.render(stage)
                else:
                    self.render('all', '--until', 'mux')
                actual = self.final_identity()
                if expected is None:
                    expected = actual
                self.assertEqual(actual, expected)
                self.assertEqual((self.root / 'tone.wav').read_bytes(), source)
                scratch = self.root / 'cache/tmp'
                if scratch.exists():
                    payloads = [p for p in scratch.rglob('*') if p.is_file() and not p.name.endswith('.lock')]
                    self.assertEqual(payloads, [], 'completed operations must not retain temporary media')
                self.assertTrue((self.root / 'out/receipt.json').is_file())
                self.assertTrue((self.root / 'out/VERSIONS.json').is_file())

    def test_complete_core_pipeline_retires_only_handoff_and_preview_stays_available(self):
        self.config.write_text('{"cache":{"mode":"core"}}')
        video = self.root / 'source.mp4'
        self.command(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                      'testsrc2=size=1920x1080:rate=30:duration=2',
                      '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '2',
                      '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-c:a', 'aac', video])
        timeline = json.loads(self.timeline.read_text())
        timeline['sources'].append({'id': 'video', 'path': 'source.mp4'})
        timeline['video_tracks'] = [{'name': 'base', 'clips': [
            {'src': 'video', 'src_in': 0, 'src_out': 60, 'dst_in': 0, 'dur': 60}]}]
        self.timeline.write_text(json.dumps(timeline))
        self.render('all')
        self.assertFalse((self.root / 'cache/audio/loudness_receipts.json').exists())
        for name in ('final.mp4', 'receipt.json', 'VERSIONS.json', 'qc_report.json'):
            self.assertTrue((self.root / 'out' / name).is_file(), name)
        for name in ('audio.wav', 'video_noaudio.mp4'):
            self.assertFalse((self.root / 'out' / name).exists(), name)
        # A preview is an explicit delivery; it survives scratch cleanup and may
        # reconstruct native inputs without changing formal stage evidence.
        receipt = (self.root / 'out/receipt.json').read_bytes()
        self.render('subs', 'preview', 'f10')
        self.assertTrue((self.root / 'out/subs_preview_f10.png').is_file())
        self.assertEqual((self.root / 'out/receipt.json').read_bytes(), receipt)
        self.assertTrue(video.is_file())

    def test_invalid_mode_rejected_without_render_artifacts(self):
        self.config.write_text('{"cache":{"mode":"typo"}}')
        result = subprocess.run([sys.executable, '-m', 'vtx', '-t', str(self.timeline), 'prepare'],
                                cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('cache.mode', result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse((self.root / 'cache').exists())
        self.assertFalse((self.root / 'out').exists())


if __name__ == '__main__':
    unittest.main()
