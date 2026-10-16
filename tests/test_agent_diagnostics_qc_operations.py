"""Actual QC operations, with synthetic provider bytes and no media execution.

Each fault stops at the native operation that really failed. Provider callbacks
and I/O remain opaque; valid, skipped and fallback controls retain legacy QC.
"""
import contextlib
import io
import json
import re
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from vtx import qc
from tests.test_agent_diagnostics_qc import _FakeContext, _run_qc


def normalized(path):
    return re.sub(r'\["([A-Za-z_][A-Za-z_0-9]*)"\]', r'.\1', path)


class TestQCActualOperations(unittest.TestCase):
    def context(self, **kwargs):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return _FakeContext(Path(temporary.name), enabled=True, **kwargs)

    def execute(self, context):
        with mock.patch.object(qc.loudness_cache, 'lookup', return_value={
                'i_lufs': context.loud, 'tp_dbfs': context.tp}):
            with contextlib.redirect_stdout(io.StringIO()):
                qc.qc(context)

    def fault(self, context, expected):
        with self.assertRaises(expected) as caught:
            self.execute(context)
        records = [record for record in context.diagnostics
                   if record['rule_id'] in ('qc.operation', 'qc.metadata_operation', 'qc.required_field')]
        self.assertTrue(records, context.diagnostics)
        self.assertTrue(getattr(caught.exception, '_vtx_diagnostic_handled', False))
        self.assertEqual(records[-1]['message'], str(caught.exception))
        self.assertFalse(any(record['rule_id'] == 'runtime.unknown' for record in records))
        json.dumps(context.diagnostics, allow_nan=False)
        return caught.exception, records[-1]

    def metadata(self, context, *payloads):
        original = context.run
        index = 0
        def run(command, **kwargs):
            nonlocal index
            if command[0] != 'ffprobe':
                return original(command, **kwargs)
            context.commands.append(tuple(command))
            value = payloads[min(index, len(payloads) - 1)]
            index += 1
            return types.SimpleNamespace(stdout=value if isinstance(value, (str, bytes)) else json.dumps(value), stderr='')
        context.run = run

    def test_metadata_parse_access_and_conversion_matrix(self):
        valid_stream = {'nb_frames': '24', 'width': 1920, 'height': 1080}
        cases = [
            ('{', json.JSONDecodeError, None),
            ([], TypeError, '$.streams'),
            ({}, KeyError, '$.streams'),
            ({'streams': []}, IndexError, '$.streams[0]'),
            ({'streams': [valid_stream]}, KeyError, '$.format'),
            ({'streams': [valid_stream], 'format': {}}, KeyError, '$.format.duration'),
            ({'streams': [valid_stream], 'format': {'duration': 'bad'}}, ValueError, '$.format.duration'),
            ({'streams': [valid_stream], 'format': {'duration': None}}, TypeError, '$.format.duration'),
            ({'streams': [None], 'format': {'duration': .8}}, AttributeError, '$.streams[0].nb_frames'),
        ]
        for payload, exception, pointer in cases:
            with self.subTest(payload=payload):
                context = self.context()
                self.metadata(context, payload)
                _, record = self.fault(context, exception)
                self.assertEqual(record['rule_id'], 'qc.metadata_operation')
                self.assertEqual(record['status'], 'unknown')
                self.assertEqual(record['json_path'], '$')
                self.assertEqual(record['source']['provider'], 'ffprobe')
                if pointer:
                    self.assertEqual(normalized(record['source']['provider_path']), pointer)
                self.assertEqual(len(context.commands), 1)
                self.assertEqual(context.receipts, [])

    def test_strict_frame_count_and_estimation_fail_at_actual_operation(self):
        initial = {'streams': [{'width': 1920, 'height': 1080}], 'format': {'duration': .8}}
        for value, exception in [('bad', ValueError), (None, TypeError)]:
            with self.subTest(value=value):
                context = self.context(strict=True)
                self.metadata(context, initial, {'streams': [{'nb_read_frames': value}]})
                _, record = self.fault(context, exception)
                self.assertEqual(record['measured']['operands']['operation'], 'int')
                self.assertEqual(normalized(record['source']['provider_path']), '$.streams[0].nb_read_frames')
                self.assertEqual(len(context.commands), 2)
        context = self.context()
        self.metadata(context, dict(initial, format={'duration': float('inf')}))
        _, record = self.fault(context, OverflowError)
        self.assertEqual(record['measured']['operands']['operation'], 'round')
        self.assertEqual(record['source']['provider'], 'ffprobe')

    def test_missing_resolution_is_not_eagerly_required(self):
        context = self.context(metadata={'streams': [{'nb_frames': '24', 'height': 1080}]})
        _, record = self.fault(context, KeyError)
        self.assertEqual(normalized(record['source']['provider_path']), '$.streams[0].width')
        self.assertTrue(any('rawvideo' in command for command in context.commands))
        self.assertEqual([d['rule_id'] for d in context.diagnostics[:2]], ['qc.frame_count', 'qc.duration'])

    def test_bottom_clip_native_operation_matrix_uses_original_indices(self):
        cases = [
            ('dur', 'bad', TypeError, '$.video_tracks[0].clips[1].dur', 'subtract'),
            ('fade_in_frames', None, TypeError, '$.video_tracks[0].clips[1].fade_in_frames', 'add'),
            ('fade_out_frames', 'bad', TypeError, '$.video_tracks[0].clips[1]', 'subtract'),
            ('src_in', 'bad', TypeError, '$.video_tracks[0].clips[1]', 'add'),
        ]
        for key, value, exception, path, operation in cases:
            with self.subTest(key=key):
                context = self.context()
                context.tl['video_tracks'][0]['clips'][1][key] = value
                _, record = self.fault(context, exception)
                self.assertEqual(record['json_path'], path)
                self.assertEqual(record['measured']['operands']['operation'], operation)
                self.assertEqual(len(context.commands), 1)
                self.assertNotIn('qc.cut_frame', [d['rule_id'] for d in context.diagnostics])

    def test_sort_failure_and_missing_key_are_distinct_and_once(self):
        context = self.context()
        context.tl['video_tracks'][0]['clips'][1]['dst_in'] = 'bad'
        _, record = self.fault(context, TypeError)
        self.assertEqual(record['json_path'], '$.video_tracks[0].clips')
        self.assertEqual(record['measured']['operands']['operation'], 'sorted')
        self.assertEqual(len(context.diagnostics), 1)
        context = self.context()
        del context.tl['video_tracks'][0]['clips'][1]['dst_in']
        _, record = self.fault(context, KeyError)
        self.assertEqual(normalized(record['json_path']), '$.video_tracks[0].clips[1].dst_in')
        self.assertEqual(len(context.diagnostics), 1)

        reads = []
        class CountingDict(dict):
            def __getitem__(self, key):
                reads.append((self['label'] if key != 'label' else 'label', key))
                return super().__getitem__(key)
        a = CountingDict(dst_in=1, label='a')
        b = CountingDict(dst_in=1, label='b')
        entries = qc._QCOperations(context).clips({'clips': [a, b]}, '$.video_tracks[7]')
        self.assertEqual([index for index, _ in entries], [0, 1])
        self.assertEqual([label for label, key in reads if key == 'dst_in'], ['a', 'b'])

    def test_upper_preparation_and_required_name_matrix(self):
        cases = [
            (1, 0, 'dur', None, TypeError, '$.video_tracks[1].clips[0].dur'),
            (1, 1, 'scale', None, TypeError, '$.video_tracks[1].clips[1]'),
            (1, 1, 'x', 'bad', TypeError, '$.video_tracks[1].clips[1].x'),
            (1, 1, 'y', {}, TypeError, '$.video_tracks[1].clips[1].y'),
            (2, 0, 'dur', 'bad', TypeError, '$.video_tracks[2].clips[0].dur'),
            (2, 0, 'src', None, TypeError, '$.video_tracks[2].clips[0].src'),
        ]
        for ti, ci, key, value, exception, path in cases:
            with self.subTest(key=key, track=ti):
                context = self.context()
                context.tl['video_tracks'][ti]['clips'][ci][key] = value
                _, record = self.fault(context, exception)
                self.assertEqual(record['json_path'], path)
        context = self.context()
        del context.tl['video_tracks'][1]['name']
        _, record = self.fault(context, KeyError)
        self.assertEqual(normalized(record['json_path']), '$.video_tracks[1].name')

    def test_source_probe_metadata_is_not_timeline_width(self):
        for response, exception, pointer in [({}, KeyError, '$.w'),
                ({'w': 10}, KeyError, '$.h'), ({'w': None, 'h': 10}, TypeError, None)]:
            with self.subTest(response=response):
                context = self.context()
                context.probe = mock.Mock(return_value=response)
                _, record = self.fault(context, exception)
                self.assertEqual(record['rule_id'], 'qc.metadata_operation')
                self.assertEqual(record['source']['provider'], 'source_probe')
                self.assertEqual(record['source']['origin'], '$.video_tracks[1].clips[1]')
                if pointer:
                    self.assertEqual(normalized(record['source']['provider_path']), pointer)
                self.assertNotEqual(record['json_path'], '$.project.width')
                context.probe.assert_called_once()

    def test_subtitle_and_audio_interval_matrix_and_external_origin(self):
        for kind, field_name, value in [('subtitles', 'in', 'bad'), ('subtitles', 'out', []),
                                       ('audio', 'in', None), ('audio', 'out', {})]:
            with self.subTest(kind=kind, field=field_name):
                context = self.context()
                if kind == 'subtitles':
                    context.tl['subtitles'] = [{'in': 0, 'out': 24}]
                    context.tl['subtitles'][0][field_name] = value
                    expected = '$.subtitles[0]'
                else:
                    context.tl['audio_tracks'][0]['events'][0][field_name] = value
                    expected = '$.audio_tracks[0].events[0]'
                _, record = self.fault(context, TypeError)
                self.assertEqual(record['json_path'], expected)
                self.assertEqual(len(context.commands), 1)
        context = self.context()
        context.subtitles_origin = {'path': 'subs.json', 'format': 'json'}
        context.tl['subtitles'] = [{'in': 'bad', 'out': 24}]
        _, record = self.fault(context, TypeError)
        self.assertEqual(record['json_path'], '$.subtitles_src')
        self.assertEqual(record['source'], dict(context.subtitles_origin, index=0))
        self.assertNotIn('$.subtitles[0]', json.dumps(record))

    def test_external_missing_subtitle_field_and_audio_required_access(self):
        context = self.context()
        context.subtitles_origin = {'path': 'subs.json', 'format': 'json'}
        context.tl['subtitles'] = [{'out': 24}]
        _, record = self.fault(context, KeyError)
        self.assertEqual(record['json_path'], '$.subtitles_src')
        self.assertEqual(record['source']['field'], 'in')
        context = self.context()
        context.tl['audio_tracks'][0]['events'].append({'in': 1})
        _, record = self.fault(context, KeyError)
        self.assertEqual(normalized(record['json_path']), '$.audio_tracks[0].events[1].out')

    def test_opaque_callbacks_and_io_retain_exact_exception(self):
        for callback in ('run', 'probe', 'src_path', 'crop169'):
            with self.subTest(callback=callback):
                context = self.context()
                failure = ValueError('opaque callback sentinel')
                setattr(context, callback, mock.Mock(side_effect=failure))
                with self.assertRaises(ValueError) as caught:
                    self.execute(context)
                self.assertIs(caught.exception, failure)
                self.assertFalse(getattr(failure, '_vtx_diagnostic_handled', False))
                self.assertEqual(context.diagnostics, [])
                # Worker callbacks can run in several independently scheduled probes;
                # none is replayed by diagnostic attribution.
                if callback in ('run', 'probe', 'src_path'):
                    self.assertEqual(getattr(context, callback).call_count, 1)
        context = self.context()
        failure = PermissionError('rawvideo I/O sentinel')
        original = Path.read_bytes
        def read(path):
            if path.name.startswith('qc_probe_'):
                raise failure
            return original(path)
        with mock.patch.object(Path, 'read_bytes', read):
            with self.assertRaises(PermissionError) as caught:
                self.execute(context)
        self.assertIs(caught.exception, failure)
        self.assertFalse(getattr(failure, '_vtx_diagnostic_handled', False))
        self.assertEqual(context.diagnostics, [])

    def test_empty_rawvideo_is_measurement_failure_not_fabricated_metric(self):
        context = self.context(covered=True)
        context.tl['video_tracks'] = [{'name': 'base', 'clips': [
            {'src': 'a', 'src_in': 0, 'dst_in': 0, 'dur': 24}]}]
        original = context.run
        def run(command, **kwargs):
            result = original(command, **kwargs)
            if 'rawvideo' in command:
                Path(command[-1]).write_bytes(b'')
            return result
        context.run = run
        _, record = self.fault(context, ZeroDivisionError)
        self.assertEqual(record['json_path'], '$')
        self.assertEqual(record['rule_id'], 'qc.metadata_operation')
        self.assertEqual(record['source']['measurement'], 'rawvideo')
        self.assertEqual(record['source']['origin'], '$.video_tracks[0].clips[0]')
        self.assertEqual(record['source']['frame'], 2)
        self.assertEqual(len(context.diagnostics), 1)
        self.assertEqual(sum('rawvideo' in command for command in context.commands), 2)
        self.assertEqual(context.receipts, [])

    def test_loudness_cache_values_and_native_exception_identity(self):
        for kwargs, exception, field_name in [({'loud': 'bad'}, TypeError, 'i_lufs'),
                                             ({'tp': []}, TypeError, 'tp_dbfs')]:
            with self.subTest(kwargs=kwargs):
                context = self.context(**kwargs)
                _, record = self.fault(context, exception)
                self.assertEqual(record['source']['provider'], 'loudness_cache')
                self.assertEqual(record['source']['field'], field_name)
                self.assertEqual(record['status'], 'unknown')
        context = self.context()
        failure = TypeError('the exact native operation failure')
        calls = []
        class BadInt(int):
            def __add__(self, other):
                calls.append(other)
                raise failure
        with self.assertRaises(TypeError) as caught:
            qc._QCOperations(context).native('add', BadInt(3), 1, path='$.input')
        self.assertIs(caught.exception, failure)
        self.assertEqual(calls, [1])
        self.assertEqual(len(context.diagnostics), 1)
        json.dumps(context.diagnostics, allow_nan=False)

    def test_cache_and_gfx_fallbacks_and_short_circuit_are_unchanged(self):
        context = self.context()
        (context.cache / qc._QC_SEG_RECORD).write_text('{malformed')
        # Out is read during coverage preparation but never compared when the
        # first comparison is false. No new type/interval gate may reject it.
        context.tl['subtitles'] = [{'in': 999, 'out': []}]
        with mock.patch.object(qc, 'gfx_dir', side_effect=ValueError('optimization fallback')):
            self.execute(context)
        self.assertFalse(any(d['rule_id'].endswith('operation') for d in context.diagnostics))
        self.assertTrue(context.receipts[-1][0][1])
        report = json.loads((context.out / 'qc_report.json').read_text())
        self.assertEqual(set(report), {'checks', 'skipped_probes', 'quality'})
        self.assertEqual(context.receipts[-1][1], {'extra': {'skipped_probes': []}})

    def test_worker_native_failure_keeps_bound_original_origin(self):
        context = self.context(covered=True)
        context.tl['video_tracks'] = [{'name': 'base', 'clips': [
            {'src': 'ignored', 'dst_in': 20, 'dur': 4},
            {'src': 'a', 'src_in': 0, 'dst_in': 0, 'dur': 20}]}]
        context.fps = 0
        _, record = self.fault(context, ZeroDivisionError)
        self.assertEqual(record['json_path'], '$.video_tracks[0].clips[1]')
        self.assertEqual(record['measured']['operands']['operation'], 'divide')
        self.assertEqual(record['measured']['operands']['values'][1]['json_path'], '$.project.fps')
        self.assertEqual(sum('rawvideo' in command for command in context.commands), 0)
        self.assertEqual(len(context.diagnostics), 1)

    def test_live_measurement_conversions_and_cache_fields(self):
        for target, field_name in [('ebur128', 'i_lufs'), ('volumedetect', None)]:
            with self.subTest(target=target):
                context = self.context()
                original = context.run
                def run(command, **kwargs):
                    if target == 'ebur128' and any('ebur128=' in value for value in command):
                        context.commands.append(tuple(command))
                        return types.SimpleNamespace(stdout='', stderr='I: 1.2.3 LUFS Peak: -2 dBFS')
                    result = original(command, **kwargs)
                    if target == 'volumedetect' and 'volumedetect' in command:
                        result.stderr = 'mean_volume: 1.2.3 dB'
                    return result
                context.run = run
                def store(path, measure, **kwargs):
                    loudness, peak = measure()
                    return {'i_lufs': loudness, 'tp_dbfs': peak}
                with mock.patch.object(qc.loudness_cache, 'lookup', return_value=(
                        None if target == 'ebur128' else {'i_lufs': -14, 'tp_dbfs': -2})), \
                        mock.patch.object(qc.loudness_cache, 'measure_and_store', side_effect=store), \
                        contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(ValueError) as caught:
                        qc.qc(context)
                record = context.diagnostics[-1]
                self.assertEqual(record['message'], str(caught.exception))
                self.assertEqual(record['source']['provider'], 'ffmpeg')
                self.assertEqual(record['source']['measurement'], target)
                self.assertEqual(record['source'].get('field'), field_name)
                self.assertEqual(record['measured']['operands']['operation'], 'float')
                self.assertEqual(record['status'], 'unknown')
                self.assertEqual(len(context.diagnostics), 1)
        context = self.context()
        with mock.patch.object(qc.loudness_cache, 'lookup', return_value={'i_lufs': -14}), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyError):
                qc.qc(context)
        self.assertEqual(normalized(context.diagnostics[-1]['source']['provider_path']), '$.tp_dbfs')

    def test_malformed_warm_record_is_cold_and_preserves_provider_diagnostics(self):
        for location, value in [('final', 7), ('segment_start', 'bad')]:
            with self.subTest(location=location):
                context = self.context()
                self.execute(context)
                report_path = context.out / 'qc_report.json'
                cold_report = json.loads(report_path.read_text())
                cold_diagnostics = list(context.diagnostics)
                cold_probes = sum('rawvideo' in cmd for cmd in context.commands)
                self.assertGreater(cold_probes, 0)
                path = context.cache / qc._QC_SEG_RECORD
                record = json.loads(path.read_text())
                if location == 'final':
                    record['final'] = value
                else:
                    record['segs'][0]['a'] = value
                path.write_text(json.dumps(record))
                self.assertIsNone(qc._load_qc_pass_record(context))
                context.commands.clear(); context.diagnostics.clear()
                self.execute(context)
                report = json.loads(report_path.read_text())
                self.assertEqual(report['skipped_probes'], [])
                self.assertEqual(report, cold_report)
                self.assertEqual(context.diagnostics, cold_diagnostics)
                self.assertEqual(sum('rawvideo' in cmd for cmd in context.commands), cold_probes)

                # A rejected cache must not mask an actual provider failure or
                # replace its provenance with the malformed record's fields.
                path.write_text(json.dumps(record))
                context.metadata = {'streams': [{'nb_frames': '24', 'height': 1080}]}
                context.commands.clear(); context.diagnostics.clear()
                _, diagnostic = self.fault(context, KeyError)
                self.assertEqual(diagnostic['rule_id'], 'qc.metadata_operation')
                self.assertEqual(diagnostic['status'], 'unknown')
                self.assertEqual(diagnostic['source']['provider'], 'ffprobe')
                self.assertEqual(normalized(diagnostic['source']['provider_path']), '$.streams[0].width')
                self.assertEqual([item['rule_id'] for item in context.diagnostics[:2]],
                                 ['qc.frame_count', 'qc.duration'])
                self.assertTrue(any('rawvideo' in cmd for cmd in context.commands))
                self.assertFalse(any(item.get('source', {}).get('provider') == 'qc_pass_record'
                                     for item in context.diagnostics))

    def test_incomplete_containers_stop_at_executed_access(self):
        cases = [('video_tracks', None, TypeError, '$.video_tracks'),
                 ('subtitles', None, TypeError, '$.subtitles'),
                 ('audio_tracks', None, TypeError, '$.audio_tracks')]
        for field_name, value, exception, path in cases:
            with self.subTest(field=field_name):
                context = self.context()
                context.tl[field_name] = value
                _, record = self.fault(context, exception)
                self.assertEqual(record['json_path'], path)
                self.assertEqual(len(context.commands), 1)


if __name__ == '__main__':
    unittest.main()
