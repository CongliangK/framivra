"""Empty video-track lists use the same black-canvas QC oracle as uncovered clips.

An empty list is legal; an explicitly empty track remains a validation error.
CPU production fixtures live in test_agent_diagnostics_qc; reduced runtime
controls are kept distinct from the source-bearing formal-size regression.
"""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tests.test_agent_diagnostics_qc import _FakeContext, _run_qc
from vtx import qc, stages
from vtx.infra import Ctx


class EmptyVideoTracksQC(unittest.TestCase):
    def context(self, *, tracks=None, base=0, final=None, strict=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        c = _FakeContext(Path(temporary.name), enabled=True, strict=strict)
        c.tl['video_tracks'] = [] if tracks is None else tracks
        original = c.run

        def run(cmd, **kwargs):
            if 'rawvideo' not in cmd:
                return original(cmd, **kwargs)
            c.commands.append(tuple(cmd))
            frame = round(float(cmd[cmd.index('-ss') + 1]) * c.fps)
            source = cmd[cmd.index('-i') + 1]
            intensity = final(frame) if final and source.endswith('final.mp4') else base
            Path(cmd[-1]).write_bytes(bytes([intensity]) * (48 * 27))
            return subprocess.CompletedProcess(cmd, 0, '', '')

        c.run = run
        return c

    def run_qc(self, c):
        with mock.patch.object(qc.loudness_cache, 'lookup', return_value={
                'i_lufs': -14, 'tp_dbfs': -2}):
            return _run_qc(c)

    def leak(self, report):
        return [x for x in report['checks'] if x['name'].startswith('空窗无叠加泄漏')]

    def test_zero_tracks_keeps_black_canvas_probe_and_all_global_checks(self):
        for strict in (False, True):
            with self.subTest(strict=strict):
                c = self.context(strict=strict)
                code, _, report = self.run_qc(c)
                self.assertEqual(code, 0)
                self.assertEqual(len(report['checks']), 7)
                self.assertEqual(self.leak(report)[0]['name'], '空窗无叠加泄漏 @frame 23')
                self.assertTrue(self.leak(report)[0]['pass'])
                self.assertEqual(report['skipped_probes'], [])
                probes = [cmd for cmd in c.commands if 'rawvideo' in cmd]
                self.assertEqual(len(probes), 1)
                self.assertEqual(probes[0][probes[0].index('-i') + 1], str(c.out / 'final.mp4'))
                self.assertEqual(c.receipts[-1][0][:2], ('qc', True))
                self.assertFalse(any(d['rule_id'] == 'qc.required_field' for d in c.diagnostics))

    def test_zero_tracks_still_rejects_visible_leak(self):
        c = self.context(final=lambda frame: 100)
        code, _, report = self.run_qc(c)
        self.assertEqual(code, 1)
        self.assertFalse(self.leak(report)[0]['pass'])
        self.assertEqual(c.receipts[-1][0][:2], ('qc', False))

    def test_subtitle_coverage_still_controls_probe_selection(self):
        c = self.context()
        c.tl['subtitles'] = [{'in': 10, 'out': 24, 'text': 'covered'}]
        code, _, report = self.run_qc(c)
        self.assertEqual(code, 0)
        self.assertEqual(self.leak(report)[0]['name'], '空窗无叠加泄漏 @frame 9')

    def test_fully_covered_zero_tracks_retains_unknown_leak_diagnostic(self):
        c = self.context()
        c.tl['subtitles'] = [{'in': 0, 'out': 24, 'text': 'covered'}]
        code, _, report = self.run_qc(c)
        self.assertEqual(code, 0)
        self.assertEqual(self.leak(report), [])
        self.assertEqual(len(report['checks']), 6)
        leak = next(d for d in c.diagnostics if d['rule_id'] == 'qc.overlay_leak')
        self.assertEqual(leak['status'], 'unknown')
        self.assertFalse(any('rawvideo' in cmd for cmd in c.commands))

    def test_empty_clips_direct_qc_uses_black_without_changing_validation(self):
        # Direct QC can inspect existing media; this does not make this track
        # valid for the all pipeline. The validation boundary is asserted below.
        c = self.context(tracks=[{'name': 'empty', 'kind': 'aroll', 'clips': []}])
        code, _, report = self.run_qc(c)
        self.assertEqual(code, 0)
        self.assertTrue(self.leak(report)[0]['pass'])

    def test_normal_bottom_fade_is_avoided_with_one_or_multiple_tracks(self):
        bottom = {'name': 'base', 'kind': 'aroll', 'clips': [
            {'src': 'a', 'src_in': 0, 'dst_in': 0, 'dur': 24,
             'fade_in_frames': 2, 'fade_out_frames': 5}]}
        upper = {'name': 'over', 'kind': 'aroll', 'clips': [
            {'src': 'b', 'src_in': 0, 'dst_in': 0, 'dur': 12}]}
        for multiple in (False, True):
            with self.subTest(multiple=multiple):
                c = self.context(tracks=[bottom, upper] if multiple else [bottom],
                                 base=100, final=lambda frame: 200 if multiple and frame < 12 else 100)
                code, _, report = self.run_qc(c)
                self.assertEqual(code, 0)
                self.assertEqual(self.leak(report)[0]['name'], '空窗无叠加泄漏 @frame 18')
                self.assertEqual(sum(x['name'].startswith('底轨切点') for x in report['checks']), 1)
                self.assertEqual(sum(x['name'].startswith('全屏 clip') for x in report['checks']), int(multiple))

    def test_runtime_validator_accepts_zero_tracks_but_rejects_an_empty_track(self):
        # Isolate the runtime track rule: this reduced, sourceless fixture is
        # not evidence of whole-input formal schema conformance.
        with tempfile.TemporaryDirectory() as temporary:
            timeline = Path(temporary) / 'timeline.json'
            value = {'version': 2,
                     'project': {'fps': 30, 'width': 320, 'height': 180, 'duration_frames': 60},
                     'sources': [], 'video_tracks': [], 'subtitles': [], 'audio_tracks': []}
            timeline.write_text(json.dumps(value))
            c = Ctx(timeline, diagnostics=[])
            with contextlib.redirect_stdout(io.StringIO()):
                stages.validate(c)
            value['video_tracks'] = [{'name': 'empty', 'kind': 'aroll', 'clips': []}]
            timeline.write_text(json.dumps(value))
            c = Ctx(timeline, diagnostics=[])
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
                stages.validate(c)
            self.assertEqual(caught.exception.code, 1)
            self.assertTrue(any(d['rule_id'] == 'validate.track_empty' for d in c.diagnostics))


if __name__ == '__main__':
    unittest.main()
