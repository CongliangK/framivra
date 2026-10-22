"""Exact single-stream audio duration and legacy fallback from probe metadata."""
import contextlib
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from vtx.pipeline.infra import Ctx


def audio_payload():
    return {'streams': [dict(codec_type='audio', duration_ts=54400,
                             time_base='1/48000')],
            'format': {'duration': '1.133333'}}


class AudioDurationProbeTest(unittest.TestCase):
    def probe(self, payload):
        # Ordinary JSON provider data; no media, subprocess, or filesystem fixture.
        c = Ctx.__new__(Ctx)
        c.fps = 30
        c.probes = {}
        c.diagnostics = []
        calls = []

        def run(cmd):
            calls.append(cmd)
            return SimpleNamespace(stdout=json.dumps(payload))

        c.run = run
        result = c.probe(Path('cue.wav'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(c.diagnostics, [])
        return result

    def test_exact_ticks_preserve_frame_boundary(self):
        result = self.probe(audio_payload())
        self.assertEqual(result, {'fps': None, 'nb_frames': 34, 'w': 0, 'h': 0,
                                  'has_video': False, 'has_audio': True})

    def test_exact_ticks_floor_partial_frames(self):
        for samples, expected in ((54399, 33), (54401, 34)):
            with self.subTest(samples=samples):
                payload = audio_payload()
                payload['streams'][0]['duration_ts'] = samples
                # The stream clock is authoritative even if rounded container
                # text would suggest an extra full frame.
                payload['format']['duration'] = '1.133334'
                self.assertEqual(self.probe(payload)['nb_frames'], expected)

    def test_integer_tick_text_and_nonunit_time_base(self):
        for ticks, time_base, expected in (('54400', '1/48000', 34),
                                           (54400, '2/96000', 34),
                                           (1600, '1001/48000', 1001),
                                           (1470, '1/44100', 1)):
            with self.subTest(ticks=ticks, time_base=time_base):
                payload = audio_payload()
                payload['streams'][0].update(duration_ts=ticks, time_base=time_base)
                self.assertEqual(self.probe(payload)['nb_frames'], expected)

    def test_zero_ticks_are_zero_frames(self):
        payload = audio_payload()
        payload['streams'][0]['duration_ts'] = 0
        self.assertEqual(self.probe(payload)['nb_frames'], 0)

    def test_missing_tick_fields_keep_format_fallback(self):
        for fields in (('duration_ts',), ('time_base',), ('duration_ts', 'time_base')):
            with self.subTest(fields=fields):
                payload = audio_payload()
                for field in fields:
                    del payload['streams'][0][field]
                self.assertEqual(self.probe(payload)['nb_frames'], 33)

    def test_invalid_tick_fields_keep_format_fallback(self):
        invalid = [('duration_ts', value) for value in
                   (None, True, -1, '-1', 54400.0, 54400.5, '54400.0',
                    '54_400', '', 'N/A', 'nan', {}, [])]
        invalid += [('time_base', value) for value in
                    (None, True, 1, '', 'N/A', 'nan', '1/0', '0/48000',
                     '-1/48000', '1/-48000', '-1/-48000', '1.0/48000',
                     '1/48_000', '1/48000/1', {}, [])]
        for field, value in invalid:
            with self.subTest(field=field, value=repr(value)[:60]):
                payload = audio_payload()
                payload['streams'][0][field] = value
                self.assertEqual(self.probe(payload)['nb_frames'], 33)

    def test_multiple_streams_keep_format_duration(self):
        for extra in ({'codec_type': 'audio', 'duration_ts': 96000, 'time_base': '1/48000'},
                      {'codec_type': 'subtitle'}, {'codec_type': 'data'}):
            for prepend in (False, True):
                with self.subTest(extra=extra, prepend=prepend):
                    payload = audio_payload()
                    payload['streams'].insert(0 if prepend else 1, extra)
                    payload['format']['duration'] = '2.0'
                    self.assertEqual(self.probe(payload)['nb_frames'], 60)
        payload = audio_payload()
        payload['streams'][0]['codec_type'] = 'data'
        self.assertFalse(self.probe(payload)['has_audio'])
        self.assertEqual(self.probe(payload)['nb_frames'], 33)

    def test_video_stream_keeps_video_frame_count(self):
        payload = audio_payload()
        payload['streams'].append({'codec_type': 'video', 'nb_frames': '90',
                                   'r_frame_rate': '30/1', 'width': 160, 'height': 90})
        result = self.probe(payload)
        self.assertEqual(result['nb_frames'], 90)
        self.assertEqual(result['fps'], 30)
        self.assertTrue(result['has_video'])
        self.assertTrue(result['has_audio'])

    def test_exact_ticks_work_without_format_duration(self):
        for format_value in ({}, None, {'duration': 'N/A'}):
            payload = audio_payload()
            payload['format'] = format_value
            self.assertEqual(self.probe(payload)['nb_frames'], 34)
        payload = audio_payload()
        del payload['format']
        self.assertEqual(self.probe(payload)['nb_frames'], 34)

    def test_missing_all_durations_keeps_failure(self):
        payload = {'streams': [{'codec_type': 'audio'}], 'format': {}}
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.probe(payload)
        self.assertEqual(error.exception.code, 1)


if __name__ == '__main__':
    unittest.main()
