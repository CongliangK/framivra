"""Exact audio duration and source bounds with real short PCM WAV probes."""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from vtx.infra import Ctx
from vtx.stages import validate


def timeline(media='cue.wav', event_frames=None):
    return {'version': 2,
            'project': {'fps': 30, 'width': 160, 'height': 90, 'duration_frames': 90},
            'sources': [{'id': 'cue', 'path': media}], 'video_tracks': [],
            'subtitles': [], 'audio_tracks': [] if event_frames is None else [
                {'name': 'cue', 'events': [{'src': 'cue', 'in': 0,
                                           'out': event_frames, 'volume': 1}]}]}



@unittest.skipUnless(shutil.which('ffprobe'), 'ffprobe is required for real WAV probes')
class RealAudioDurationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def wav(self, sample_count):
        media = self.root / ('cue-%d.wav' % sample_count)
        # Synthetic nonzero PCM24 stereo samples; only ~320 KiB per short WAV.
        with wave.open(str(media), 'wb') as output:
            output.setparams((2, 3, 48000, 0, 'NONE', 'not compressed'))
            output.writeframes(b'\x01\x00\x00\xff\xff\xff' * sample_count)
        return media

    def context(self, media, event_frames=None):
        path = self.root / 'timeline.json'
        path.write_text(json.dumps(timeline(media.name, event_frames)), encoding='utf-8')
        return Ctx(path, diagnostics=[])

    def test_pcm24_stereo_short_wav_probe_boundaries(self):
        for sample_count, expected in ((54399, 33), (54400, 34), (54401, 34)):
            with self.subTest(sample_count=sample_count):
                media = self.wav(sample_count)
                before = media.read_bytes()
                with wave.open(str(media), 'rb') as source:
                    self.assertEqual((source.getnchannels(), source.getsampwidth(),
                                      source.getframerate(), source.getnframes()),
                                     (2, 3, 48000, sample_count))
                c = self.context(media)
                self.assertEqual(c.probe(media)['nb_frames'], expected)
                self.assertEqual(c.diagnostics, [])
                self.assertEqual(media.read_bytes(), before)

    def test_pcm24_stereo_exact_event_validates_and_overrun_fails(self):
        media = self.wav(54400)
        original = media.read_bytes()
        for frames in (34, 35):
            with self.subTest(event_frames=frames):
                c = self.context(media, frames)
                # Keep validate's bounds and diagnostics real; only suppress its
                # receipt publication. No audio or video rendering is invoked.
                with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(c, 'receipt'):
                    if frames == 34:
                        validate(c)
                    else:
                        with self.assertRaises(SystemExit) as error:
                            validate(c)
                        self.assertEqual(error.exception.code, 1)
                bounds = [item for item in c.diagnostics
                          if item['rule_id'] == 'validate.audio_source_bounds']
                self.assertEqual(len(bounds), 0 if frames == 34 else 1)
                if bounds:
                    self.assertEqual(bounds[0]['measured']['value'], 35)
                    self.assertEqual(bounds[0]['threshold']['max'], 34)
                self.assertEqual(media.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
