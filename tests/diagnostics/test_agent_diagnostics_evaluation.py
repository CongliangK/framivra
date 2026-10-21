"""Diagnostic payloads observe native work; they must not do skipped work."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.diagnostics.test_agent_diagnostics_operations import StageOperations
from tests.qc._support import _FakeContext
from vtx import qc
from vtx.infra import Ctx


class EvaluationOrder(StageOperations):
    def test_negative_gfx_start_does_not_observe_missing_or_bad_duration(self):
        for duration in ('missing', None, 'bad'):
            for enabled in (False, True):
                tl = copy.deepcopy(self.tl)
                cl = {'src': 's.wav', 'dst_in': -1}
                if duration != 'missing': cl['dur'] = duration
                tl['video_tracks'] = [{'name': 'gfx', 'kind': 'gfx', 'clips': [cl]}]
                c = self.context(tl, enabled)
                receipts = []; c.receipt = lambda *a, **k: receipts.append(a)
                output, error = self.run_stage(c, 'validate')
                self.assertIsInstance(error, SystemExit)
                self.assertEqual(error.code, 1)
                self.assertIn('clip 区间越界 @dst_in=-1', output)
                self.assertTrue(receipts)
                if enabled:
                    record = next(d for d in c.diagnostics if d['rule_id'] == 'validate.clip_bounds')
                    self.assertEqual(record['measured']['end'], {'status': 'not_observed'})

    def test_negative_subtitle_start_skips_missing_out(self):
        for enabled in (False, True):
            tl = copy.deepcopy(self.tl)
            tl['subtitles'] = [{'in': -1, 'text': 'hello'}]
            c = self.context(tl, enabled)
            output, error = self.run_stage(c, 'validate')
            self.assertIsInstance(error, SystemExit)
            self.assertIn('字幕区间非法', output)
            if enabled:
                record = next(d for d in c.diagnostics if d['rule_id'] == 'validate.subtitle_bounds')
                self.assertEqual(record['measured']['end'], {'status': 'not_observed'})

    def test_provider_facts_are_unknown_at_the_source_reference(self):
        for key in ('fps', 'nb_frames'):
            for value in ('missing', None, 'bad'):
                c = self.context(self.tl)
                facts = dict(fps=30, nb_frames=90, has_video=True, has_audio=True)
                if value == 'missing': del facts[key]
                else: facts[key] = value
                calls = []; c.probe = lambda path: (calls.append(path), facts)[1]
                _, error = self.run_stage(c, 'validate')
                self.assertIsNotNone(error)
                record = c.diagnostics[-1]
                self.assertEqual(record['status'], 'unknown')
                self.assertEqual(record['rule_id'], 'probe.metadata_operation')
                self.assertEqual(record['source']['provider_path'], '$.' + key)
                self.assertEqual(record['json_path'], '$.sources[0].path' if key == 'fps' else '$.video_tracks[0].clips[0].src')
                self.assertEqual(len(calls), 1 if key == 'fps' else 2)


class QCShortCircuit(unittest.TestCase):
    def test_wrong_width_skips_height_and_still_writes_failure_artifacts(self):
        for height in ('missing', None, 'bad'):
            for enabled in (False, True):
                with tempfile.TemporaryDirectory() as directory:
                    stream = {'nb_frames': '24', 'width': 1280}
                    if height != 'missing': stream['height'] = height
                    c = _FakeContext(Path(directory), enabled=enabled, metadata={'streams': [stream]})
                    with contextlib.redirect_stdout(io.StringIO()), patch.object(qc.loudness_cache, 'lookup', return_value={'i_lufs': -14, 'tp_dbfs': -2}):
                        with self.assertRaises(SystemExit) as raised: qc.qc(c)
                    self.assertEqual(raised.exception.code, 1)
                    self.assertTrue(c.receipts)
                    self.assertTrue((c.out/'qc_report.json').exists())
                    if enabled:
                        record = next(d for d in c.diagnostics if d['rule_id'] == 'qc.resolution')
                        self.assertEqual(record['measured']['height'], {'status': 'not_observed'})


class ProbeTerminalConversions(unittest.TestCase):
    def run_probe(self, nb, rate, fmt):
        payload = {'streams': [{'codec_type': 'video', 'nb_frames': nb, 'r_frame_rate': rate, 'width': 160, 'height': 90}], 'format': fmt}
        c = SimpleNamespace(diagnostics=[], probes={}, fps=30, run=lambda cmd: SimpleNamespace(stdout=json.dumps(payload)))
        try: result = Ctx._probe(c, Path('/tmp/fixture.mp4')); error = None
        except BaseException as caught: result = None; error = caught
        return c, result, error

    def test_terminal_conversion_and_null_format_are_provider_unknown(self):
        for nb, rate, fmt, kind, pointer in [
            ('9'*5000, '30/1', {'duration':'3'}, ValueError, '$.streams[video].nb_frames'),
            ('90', 'bad', None, AttributeError, '$.format.duration')]:
            c, _, error = self.run_probe(nb, rate, fmt)
            self.assertIsInstance(error, kind)
            self.assertTrue(getattr(error, '_vtx_diagnostic_handled', False))
            self.assertEqual(len(c.diagnostics), 1)
            self.assertEqual(c.diagnostics[0]['status'], 'unknown')
            self.assertEqual(c.diagnostics[0]['source']['provider_path'], pointer)

    def test_recovered_rate_and_unreached_null_format_do_not_emit(self):
        for rate, fmt in [('bad', {'duration':'3'}), ('30/1', None)]:
            c, result, error = self.run_probe('90', rate, fmt)
            self.assertIsNone(error)
            self.assertEqual(result['fps'], 30)
            self.assertEqual(c.diagnostics, [])

if __name__ == '__main__': unittest.main()
