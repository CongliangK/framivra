"""Execution validation matches the existing v2 version and audio-fade contract."""
import contextlib
import copy
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests._support import temporary_root
from vtx.agent_diagnostics import main as diagnostic_main
from vtx.media.audio_filters import event_chain
from vtx.pipeline.infra import Ctx
from vtx.pipeline.stages import validate


class RuntimeVersionAudioFadesTest(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self)
        (self.root / 'cue.wav').touch()
        self.timeline = {
            'version': 2,
            'project': {'fps': 30, 'width': 1920, 'height': 1080,
                        'duration_frames': 90},
            'sources': [{'id': 'cue', 'path': 'cue.wav'}],
            'video_tracks': [], 'subtitles': [],
            'audio_tracks': [{'name': 'cue', 'events': [
                {'src': 'cue', 'in': 10, 'out': 40, 'volume': 1}]}],
        }

    def context(self):
        path = self.root / 'timeline.json'
        path.write_text(json.dumps(self.timeline), encoding='utf-8')
        c = Ctx(path, diagnostics=[])
        c.probe = Mock(return_value={'fps': None, 'has_video': False,
                                     'has_audio': True, 'nb_frames': 90})
        c.receipt = Mock()
        return c

    def run_validate(self, c, *, fails=False):
        before = copy.deepcopy(c.tl)
        with contextlib.redirect_stdout(io.StringIO()), \
                patch('vtx.pipeline.stages._plugin_hooks.run_probe_source', return_value=None), \
                patch('vtx.pipeline.stages._plugin_hooks.run_validate', return_value=[]):
            if fails:
                with self.assertRaises(SystemExit) as failure:
                    validate(c)
                self.assertEqual(failure.exception.code, 1)
                self.assertTrue(failure.exception._vtx_diagnostic_handled)
            else:
                validate(c)
        # Non-finite invalid inputs need JSON comparison rather than NaN equality.
        self.assertEqual(json.dumps(c.tl, sort_keys=True), json.dumps(before, sort_keys=True))
        self.assertEqual(c.receipt.call_args.args[:2], ('validate', not fails))
        json.dumps(c.diagnostics, allow_nan=False)
        return c.diagnostics

    def test_numeric_v2_including_float_validates_without_coercion(self):
        for version in (2, 2.0):
            with self.subTest(version=version, type=type(version)):
                self.timeline['version'] = version
                c = self.context()
                self.assertEqual(self.run_validate(c), [])
                self.assertIs(type(c.tl['version']), type(version))

    def test_invalid_or_missing_version_fails_before_legacy_fields_or_probes(self):
        invalid = (None, True, False, '2', 0, 1, 3, -2, 2.1, [], {}, float('nan'),
                   float('inf'))
        for tl in ({}, *({'version': value} for value in invalid)):
            with self.subTest(timeline=tl):
                # No project/sources/cache fields: version is the first mechanism gate.
                c = SimpleNamespace(tl=tl, diagnostics=[], receipt=Mock())
                records = self.run_validate(c, fails=True)
                self.assertEqual(len(records), 1)
                record = records[0]
                self.assertEqual(record['rule_id'], 'validate.timeline_version')
                self.assertEqual(record['json_path'], '$.version')
                self.assertEqual(record['status'], 'fail')
                self.assertEqual(record['threshold'], {'const': 2})
                self.assertEqual(record['measured']['present'], 'version' in tl)

    def test_loaded_invalid_version_reaches_the_mechanism_gate(self):
        for value in (None, True, '2', 1, 3):
            with self.subTest(version=value):
                self.timeline['version'] = value
                c = self.context()
                records = self.run_validate(c, fails=True)
                self.assertEqual(records[0]['rule_id'], 'validate.timeline_version')
                c.probe.assert_not_called()

    def test_audio_fade_boundaries_and_schema_integer_equivalence(self):
        for fades in ({}, {'fade_in_frames': 0, 'fade_out_frames': 0},
                      {'fade_in_frames': 30}, {'fade_out_frames': 30},
                      {'fade_in_frames': 12, 'fade_out_frames': 18},
                      {'fade_in_frames': 12.0, 'fade_out_frames': 18.0}):
            with self.subTest(fades=fades):
                event = self.timeline['audio_tracks'][0]['events'][0]
                event.clear()
                event.update(src='cue', **{'in': 10, 'out': 40}, volume=1, **fades)
                self.assertEqual(self.run_validate(self.context()), [])

    def test_audio_fade_sum_uses_event_length_not_absolute_out(self):
        for fades in ({'fade_in_frames': 31}, {'fade_out_frames': 31},
                      {'fade_in_frames': 12, 'fade_out_frames': 19}):
            with self.subTest(fades=fades):
                event = self.timeline['audio_tracks'][0]['events'][0]
                event.clear()
                event.update(src='cue', **{'in': 10, 'out': 40}, volume=1, **fades)
                records = self.run_validate(self.context(), fails=True)
                self.assertEqual(len(records), 1)
                record = records[0]
                self.assertEqual(record['rule_id'], 'validate.audio_event_fade')
                self.assertEqual(record['json_path'], '$.audio_tracks[0].events[0]')
                self.assertEqual(record['measured'], {'value': 31, 'unit': 'frames'})
                self.assertEqual(record['threshold'], {'max': 30, 'unit': 'frames'})

    def test_large_schema_integer_and_float_fades_report_sum_without_overflow(self):
        event = self.timeline['audio_tracks'][0]['events'][0]
        event.update(fade_in_frames=10 ** 400, fade_out_frames=0.0)
        records = self.run_validate(self.context(), fails=True)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['rule_id'], 'validate.audio_event_fade')
        self.assertEqual(records[0]['measured'], {'value': 10 ** 400, 'unit': 'frames'})

    def test_audio_fade_types_and_nonnegative_range_have_field_diagnostics(self):
        for key in ('fade_in_frames', 'fade_out_frames'):
            for value in (None, True, False, '3', [], {}, 0.5, -1, -1.0,
                          float('nan'), float('inf'), float('-inf')):
                with self.subTest(key=key, value=value):
                    event = self.timeline['audio_tracks'][0]['events'][0]
                    event.pop('fade_in_frames', None)
                    event.pop('fade_out_frames', None)
                    event[key] = value
                    records = self.run_validate(self.context(), fails=True)
                    self.assertEqual(len(records), 1)
                    record = records[0]
                    self.assertEqual(record['rule_id'], 'validate.audio_event_fade_value')
                    self.assertEqual(record['json_path'], '$.audio_tracks[0].events[0].' + key)
                    self.assertEqual(record['threshold'], {'type': 'integer', 'min': 0,
                                                           'unit': 'frames'})

    def test_audio_fade_diagnostics_preserve_original_track_and_event_indices(self):
        def event(start, stop, fade=0):
            return {'src': 'cue', 'in': start, 'out': stop, 'volume': 1,
                    'fade_out_frames': fade}
        self.timeline['audio_tracks'] = [
            {'name': 'valid', 'events': [event(0, 10)]},
            {'name': 'unsorted', 'events': [event(40, 70), event(10, 40, 31)]},
        ]
        records = self.run_validate(self.context(), fails=True)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['rule_id'], 'validate.audio_event_fade')
        self.assertEqual(records[0]['json_path'], '$.audio_tracks[1].events[1]')

    def test_agent_json_reports_version_and_fade_failures_without_unknown_fallback(self):
        for version, fade, rule, path in (
                ('2', 0, 'validate.timeline_version', '$.version'),
                (2, 31, 'validate.audio_event_fade', '$.audio_tracks[0].events[0]')):
            with self.subTest(rule=rule):
                self.timeline['version'] = version
                self.timeline['audio_tracks'][0]['events'][0]['fade_in_frames'] = fade
                c = self.context()
                output = io.StringIO()
                with contextlib.redirect_stdout(output), \
                        patch.object(Ctx, 'probe', c.probe), \
                        patch('vtx.pipeline.stages._plugin_hooks.run_probe_source', return_value=None), \
                        patch('vtx.pipeline.stages._plugin_hooks.run_validate', return_value=[]):
                    code = diagnostic_main(['-t', str(c.tl_path), 'validate',
                                            '--format', 'agent-json'])
                self.assertEqual(code, 1)
                report = json.loads(output.getvalue())
                self.assertEqual(report['exit_code'], 1)
                self.assertEqual(len(report['diagnostics']), 1)
                record = report['diagnostics'][0]
                self.assertEqual((record['rule_id'], record['json_path']), (rule, path))
                self.assertEqual(record['status'], 'fail')

    def test_renderer_still_clamps_transformed_short_events(self):
        # Plugins/windowing can shorten already-validated events before this helper.
        c = SimpleNamespace(spf=1 / 30, spf_samples=1600)
        e = {'in': 10, 'out': 13, 'volume': 1,
             'fade_in_frames': 12, 'fade_out_frames': 18}
        before = copy.deepcopy(e)
        chain = event_chain(c, 0, e)
        self.assertIn('afade=t=in:st=0:d=0.100000', chain)
        self.assertIn('afade=t=out:st=0.000000:d=0.100000', chain)
        self.assertEqual(e, before)


if __name__ == '__main__':
    unittest.main()
