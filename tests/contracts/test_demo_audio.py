"""Independent portable audio-r2 contract; no NumPy, FFmpeg or vendor runtime.

Golden WAV digests are frozen here, not read from the generator's identity or
self-test constants. Actual isolated CLI generations run twice on every test run.
This verifies PCM/arrangement safety, not human listening or final native mixing.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave


REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / 'templates/demo-timeline-svg/audio/build.py'
# Independently frozen after inspecting the generated RIFF/PCM payloads.
GOLDEN = {
    'bed-premaster.wav': '2aa8fab3f9edb1eaff28e299764b6f3f1f3f49acd0c5c4f2c07b24e2c772eb12',
    'content-cue.wav': 'e0a2d22c55a51544d4848781af3db09f00942429124295079cd5686e2ac9833b',
}


def pcm24(raw):
    return [int.from_bytes(raw[i:i + 3], 'little', signed=True)
            for i in range(0, len(raw), 3)]


class DemoAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='framivra-portable-audio-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        # Standalone source, no neighboring project files/vendor. -I -S ignores
        # site packages/PYTHONPATH; PATH has no ffmpeg or other executable tools.
        cls.script = cls.root / 'build.py'
        shutil.copyfile(BUILD, cls.script)
        cls.outputs = []
        for name in ('first', 'second'):
            out = cls.root / name
            result = subprocess.run(
                [sys.executable, '-I', '-S', str(cls.script), '--out', str(out)],
                cwd=cls.root, env={**os.environ, 'PATH': '', 'PYTHONHASHSEED': str(len(cls.outputs) + 1)},
                capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise AssertionError(f'Isolated generation failed: {result.stderr}')
            cls.outputs.append(out)

    def test_independent_golden_and_repeatability(self):
        for name, expected in GOLDEN.items():
            first, second = [(out / 'native-inputs' / name).read_bytes()
                             for out in self.outputs]
            self.assertEqual(hashlib.sha256(first).hexdigest(), expected, name)
            self.assertEqual(first, second, name)
        for name in ('score.json', 'native-binding-fragment.json', 'audio-identity.json'):
            self.assertEqual((self.outputs[0] / name).read_bytes(),
                             (self.outputs[1] / name).read_bytes())

    def test_pcm_spec_non_silence_headroom_and_tail(self):
        for name, frames in [('bed-premaster.wav', 1440000), ('content-cue.wav', 54400)]:
            with wave.open(str(self.outputs[0] / 'native-inputs' / name), 'rb') as wav:
                self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate(),
                                  wav.getnframes(), wav.getcomptype()),
                                 (2, 3, 48000, frames, 'NONE'))
                raw = wav.readframes(frames + 1)
            self.assertEqual(len(raw), frames * 6)
            self.assertEqual(raw[-1600 * 6:], bytes(1600 * 6))
            samples = pcm24(raw)
            peak = max(map(abs, samples))
            self.assertGreater(peak, 100000, name)
            self.assertLess(peak, 8000000, name)  # No clipping, >0.4dB headroom.
            self.assertGreater(sum(x * x for x in samples) // len(samples), 100000000)
            self.assertGreater(len(set(samples)), 10000, name)
            if name.startswith('bed'):
                self.assertNotEqual(samples[::2], samples[1::2])
                # Music throughout every authored phrase, including final chord;
                # excludes a silent/single-one-shot replacement with padded tail.
                for start, end in [(0,4), (4,7), (8,10), (10,13), (16,20), (21,24), (25,28)]:
                    window = samples[start * 96000:end * 96000]
                    self.assertGreater(sum(x != 0 for x in window), 10000)
            else:
                self.assertEqual(samples[::2], samples[1::2])

    def test_score_and_native_binding(self):
        score = json.loads((self.outputs[0] / 'score.json').read_text())
        events = score['events']
        cues = [e for e in events if e['track'] == 'timeline_cue']
        self.assertEqual([e['sample'] for e in cues], [364800, 739200])
        self.assertEqual([{k: v for k, v in e.items() if k != 'sample'} for e in cues],
                         [{'voice': 'knock', 'pitch': 74, 'velocity': 80,
                           'duration_samples': 5760, 'gain_q10': 1024,
                           'track': 'timeline_cue'}] * 2)
        bed = [e for e in events if e['track'] == 'bed']
        self.assertEqual({e['voice'] for e in bed}, {'nylon', 'bass', 'kick', 'motif'})
        self.assertEqual(len(bed), 74)
        self.assertGreater(len({e['pitch'] for e in bed}), 12)
        self.assertTrue(all(e['sample'] not in (364800, 739200) for e in bed))
        self.assertEqual(score['anchors_frames']['old_point_pass'], 411)
        self.assertEqual(score['anchors_frames']['result_carry'], 510)
        binding = json.loads((self.outputs[0] / 'native-binding-fragment.json').read_text())
        tracks = binding['audio_tracks']
        self.assertEqual(tracks[0]['events'], [
            {'src': 'timeline_music_bed', 'in': 0, 'out': 900, 'volume': 1}])
        self.assertEqual(tracks[1]['events'], [
            {'src': 'same_content_cue', 'src_in': 0, 'in': f, 'out': f + 34, 'volume': 1}
            for f in (228, 462)])
        identity = json.loads((self.outputs[0] / 'audio-identity.json').read_text())
        self.assertEqual(identity['human_listening'], 'UNRUN')
        self.assertEqual(identity['native_audio_stage'], 'UNRUN')
        self.assertEqual(identity['output_sha256'], GOLDEN)

    def test_only_standard_library_and_integer_dsp(self):
        tree = ast.parse(BUILD.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
            elif isinstance(node, ast.Constant):
                self.assertNotIsInstance(node.value, (float, complex))
        # Path composition in main legitimately overloads '/'; only DSP must
        # reject true division (which would introduce floating-point values).
        for function in tree.body:
            if isinstance(function, ast.FunctionDef) and function.name in ('score', 'sine', 'note', 'render', 'wav24'):
                for node in ast.walk(function):
                    if isinstance(node, ast.BinOp):
                        self.assertNotIsInstance(node.op, ast.Div)
        self.assertEqual(imported, {'argparse', 'array', 'hashlib', 'json', 'pathlib', 'wave'})
        # A cue-only score must render a zero bed: actual separation, not merely
        # two differently named output WAVs or a claim in generated metadata.
        code = """import runpy
m = runpy.run_path('build.py')
cues = [e for e in m['score']()['events'] if e['voice'] == 'knock']
l, r = m['render'](cues)
assert len(l) == len(r) == 1440000
assert not any(l) and not any(r)
"""
        result = subprocess.run([sys.executable, '-I', '-S', '-c', code],
                                cwd=self.root, env={**os.environ, 'PATH': ''},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
