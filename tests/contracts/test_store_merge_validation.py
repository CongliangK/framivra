"""Merge interactions: main's mechanism gates plus opt-in stored validation."""
import contextlib
import copy
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests._support import temporary_root
from vtx.agent_diagnostics import main as diagnostic_main
from vtx.pipeline.infra import Ctx
from vtx.pipeline.stages import validate
from vtx.timeline_structure import strict_loads, structure_errors


class StoreMergeValidationTest(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self)
        self.path = self.root / 'timeline.json'
        (self.root / 'cue.wav').touch()
        self.doc = {
            'version': 2,
            '_vtx': {'protocol': 1, 'project_id': 'project', 'head': 'head'},
            'project': {'fps': 30, 'width': 1920, 'height': 1080,
                        'duration_frames': 90},
            'sources': [{'id': 'cue', 'path': 'cue.wav'}],
            'video_tracks': [], 'subtitles': [],
            'audio_tracks': [{'id': 'track', 'name': 'cue', 'events': [
                {'id': 'event', 'src': 'cue', 'in': 10, 'out': 40, 'volume': 1,
                 'fade_in_frames': 12, 'fade_out_frames': 18}]}],
        }
        self.probe = Mock(return_value={'fps': None, 'has_video': False,
                                       'has_audio': True, 'nb_frames': 90})

    def context(self, payload=None):
        self.path.write_bytes(payload if payload is not None else json.dumps(self.doc).encode('utf-8'))
        c = Ctx(self.path, diagnostics=[])
        c.probe = self.probe
        c.receipt = Mock()
        return c

    def run_validate(self, c, *, fails=False):
        before = copy.deepcopy(c.tl)
        stored_before = self.path.read_bytes() if self.path.exists() else None
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
                patch('vtx.pipeline.stages._plugin_hooks.run_probe_source', return_value=None), \
                patch('vtx.pipeline.stages._plugin_hooks.run_validate', return_value=[]):
            if fails:
                with self.assertRaises(SystemExit) as failure:
                    validate(c)
                self.assertEqual(failure.exception.code, 1)
                self.assertTrue(failure.exception._vtx_diagnostic_handled)
            else:
                validate(c)
        self.assertEqual(c.tl, before)
        if stored_before is not None:
            self.assertEqual(self.path.read_bytes(), stored_before)
        c.receipt.assert_called_once()
        self.assertEqual(c.receipt.call_args.args[:2], ('validate', not fails))
        json.dumps(c.diagnostics, allow_nan=False)
        return c.diagnostics, output.getvalue()

    def test_main_version_gate_precedes_managed_parser_and_missing_fields(self):
        for value in (None, True, False, '2', 1, 3):
            for missing in (False, True):
                with self.subTest(value=value, missing=missing):
                    doc = {'_vtx': None}
                    if not missing:
                        doc['version'] = value
                    c = SimpleNamespace(tl=doc, diagnostics=[], receipt=Mock())
                    with patch('vtx.timeline_structure.strict_loads') as parser, \
                            patch('vtx.timeline_structure.structure_errors') as checker, \
                            patch('vtx.cache.cache_state.validate_cache_dependencies') as deps:
                        records, text = self.run_validate(c, fails=True)
                    parser.assert_not_called()
                    checker.assert_not_called()
                    deps.assert_not_called()
                    self.assertEqual([r['rule_id'] for r in records], ['validate.timeline_version'])
                    self.assertEqual(records[0]['json_path'], '$.version')
                    self.assertEqual(records[0]['measured'], {'present': not missing, 'value': doc.get('version')})
                    self.assertEqual(records[0]['threshold'], {'const': 2})
                    self.assertTrue(text.startswith('校验失败（1 项）：\n'))
                    c.receipt.assert_called_once_with('validate', False, '1 errors')

    def test_managed_numeric_versions_and_integer_fade_boundaries_pass_both_gates(self):
        for version in (2, 2.0):
            with self.subTest(version=version):
                self.doc['version'] = version
                c = self.context()
                records, _ = self.run_validate(c)
                self.assertEqual(records, [])
                self.assertIs(type(c.tl['version']), type(version))
                self.probe.assert_called()

    def test_managed_failure_preserves_all_records_and_its_own_handled_exit(self):
        self.doc['_vtx'] = None
        self.doc['audio_tracks'][0].pop('id')
        self.doc['audio_tracks'][0]['events'][0]['fade_out_frames'] = 19
        expected = structure_errors(self.doc, require_managed=True)
        self.assertGreater(len(expected), 1)
        c = self.context()
        with patch('vtx.cache.cache_state.validate_cache_dependencies') as deps:
            records, text = self.run_validate(c, fails=True)
        deps.assert_not_called()
        self.probe.assert_not_called()
        self.assertEqual([r['rule_id'] for r in records], ['validate.stored_structure'] * len(expected))
        self.assertEqual([r['message'] for r in records], expected)
        self.assertEqual(text, f'校验失败（{len(expected)} 项）：\n' + ''.join('  ✗ ' + m + '\n' for m in expected))
        c.receipt.assert_called_once_with('validate', False, f'{len(expected)} errors')

    def test_managed_fade_failure_precedes_main_runtime_fade_diagnostics(self):
        self.doc['audio_tracks'][0]['events'][0]['fade_out_frames'] = 19
        c = self.context()
        with patch('vtx.cache.cache_state.validate_cache_dependencies') as deps:
            records, _ = self.run_validate(c, fails=True)
        deps.assert_not_called()
        self.probe.assert_not_called()
        self.assertEqual([r['rule_id'] for r in records], ['validate.stored_structure'])
        self.assertEqual(records[0]['message'], '/audio_tracks/0/events/0: fades exceed duration')
        c.receipt.assert_called_once_with('validate', False, '1 errors')

    def test_managed_strict_parser_checks_original_bytes_not_lossy_loaded_object(self):
        base = json.dumps(self.doc).encode('utf-8')
        for suffix, message in (
                (b',"vendor":0.12345678901234567890123456789}', 'numeric precision would be lost on write'),
                (b',"vendor":1,"vendor":2}', 'duplicate')):
            with self.subTest(suffix=suffix):
                c = self.context(base[:-1] + suffix)
                self.assertIn('vendor', c.tl)
                records, _ = self.run_validate(c, fails=True)
                self.assertEqual([r['rule_id'] for r in records], ['validate.stored_structure'])
                self.assertIn(message, records[0]['message'].lower())
                self.probe.assert_not_called()

    def test_legacy_keeps_float_fades_and_original_fade_diagnostics_without_store_parser(self):
        self.doc.pop('_vtx')
        event = self.doc['audio_tracks'][0]['events'][0]
        event.update(fade_in_frames=12.0, fade_out_frames=18.0)
        with patch('vtx.timeline_structure.strict_loads', side_effect=AssertionError('legacy parser invoked')):
            self.assertEqual(self.run_validate(self.context())[0], [])
            event['fade_out_frames'] = 19.0
            records, _ = self.run_validate(self.context(), fails=True)
        self.assertEqual([r['rule_id'] for r in records], ['validate.audio_event_fade'])
        self.assertEqual(records[0]['json_path'], '$.audio_tracks[0].events[0]')
        self.assertEqual(records[0]['measured'], {'value': 31, 'unit': 'frames'})
        self.assertEqual(records[0]['threshold'], {'max': 30, 'unit': 'frames'})

    def test_main_audio_fade_gate_still_runs_after_valid_managed_stored_gate(self):
        c = self.context()
        # A valid stored document must not bypass runtime checks on Ctx's view.
        c.tl['audio_tracks'][0]['events'][0]['fade_out_frames'] = 19
        with patch('vtx.timeline_structure.structure_errors', wraps=structure_errors) as checker:
            records, _ = self.run_validate(c, fails=True)
        self.assertEqual(checker.call_args.args[0], self.doc)
        self.assertEqual([r['rule_id'] for r in records], ['validate.audio_event_fade'])
        self.assertEqual(records[0]['json_path'], '$.audio_tracks[0].events[0]')
        self.assertEqual(records[0]['measured'], {'value': 31, 'unit': 'frames'})
        c.receipt.assert_called_once_with('validate', False, '1 errors')

    def test_managed_external_subtitles_are_not_required_to_have_stored_ids(self):
        self.doc['subtitles_src'] = 'captions.json'
        captions = [{'in': 0, 'out': 30, 'text': 'external without stable ID'}]
        (self.root / 'captions.json').write_text(json.dumps(captions), encoding='utf-8')
        c = self.context()
        self.assertEqual(c.tl['subtitles'], captions)
        with patch('vtx.timeline_structure.strict_loads', wraps=strict_loads) as parser, \
                patch('vtx.timeline_structure.structure_errors', wraps=structure_errors) as checker:
            records, _ = self.run_validate(c)
        self.assertEqual(records, [])
        parser.assert_called_once_with(c.timeline_bytes)
        checker.assert_called_once_with(self.doc, require_managed=True)
        self.assertEqual(checker.call_args.args[0]['subtitles'], [])

    def test_external_subtitle_bounds_still_use_original_external_diagnostics(self):
        self.doc['subtitles_src'] = 'captions.json'
        (self.root / 'captions.json').write_text(json.dumps([
            {'in': 0, 'out': 91, 'text': 'outside project'}]), encoding='utf-8')
        records, _ = self.run_validate(self.context(), fails=True)
        self.assertEqual([r['rule_id'] for r in records], ['validate.subtitle_bounds'])
        self.assertEqual(records[0]['json_path'], '$.subtitles_src')
        self.assertEqual(records[0]['source'], {'file': 'captions.json', 'format': '.json',
                                                'json_path': '$', 'index': 0})
        self.assertEqual(records[0]['threshold']['max'], 90)

    def test_managed_missing_external_subtitle_keeps_source_failure_not_structure(self):
        self.doc['subtitles_src'] = 'missing.json'
        records, _ = self.run_validate(self.context(), fails=True)
        self.assertEqual([r['rule_id'] for r in records], ['subtitle.source_exists'])
        self.assertEqual(records[0]['json_path'], '$.subtitles_src')

    def test_agent_json_preserves_first_rule_and_does_not_append_unknown_failure(self):
        cases = (
            ('2', None, 'validate.timeline_version', '$.version'),
            (2, None, 'validate.stored_structure', '$'),
            (2, {'protocol': 1, 'project_id': 'project', 'head': 'head'}, None, None),
        )
        for version, meta, rule, path in cases:
            with self.subTest(rule=rule):
                self.doc['version'], self.doc['_vtx'] = version, meta
                c = self.context()
                output = io.StringIO()
                with contextlib.redirect_stdout(output), patch.object(Ctx, 'probe', self.probe), \
                        patch('vtx.pipeline.stages._plugin_hooks.run_probe_source', return_value=None), \
                        patch('vtx.pipeline.stages._plugin_hooks.run_validate', return_value=[]):
                    code = diagnostic_main(['-t', str(c.tl_path), 'validate', '--format', 'agent-json'])
                report = json.loads(output.getvalue())
                self.assertEqual(code, 0 if rule is None else 1)
                self.assertEqual(report['exit_code'], code)
                records = report['diagnostics']
                if rule is None:
                    self.assertEqual(records, [])
                else:
                    count = len(structure_errors(self.doc, require_managed=True)) if rule == 'validate.stored_structure' else 1
                    self.assertEqual(len(records), count)
                    self.assertEqual([(r['rule_id'], r['json_path']) for r in records], [(rule, path)] * count)
                    self.assertEqual([r['status'] for r in records], ['fail'] * count)
                    self.assertIn(f'校验失败（{count} 项）', report['legacy_output']['stdout'])


if __name__ == '__main__':
    unittest.main()
