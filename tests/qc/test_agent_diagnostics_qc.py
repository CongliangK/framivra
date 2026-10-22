"""QC diagnostics preserve legacy results and report source-level evidence.

Fast mocked fixtures cover exact boundaries, missing evidence and exemptions.
The CPU integration fixture runs the real stages to establish receipts before
checking generated media and deliberately damaged final outputs. No Chrome,
GPU, external assets, network, or manufactured successful receipts are used.

Actual QC operations, with synthetic provider bytes and no media execution.

Each fault stops at the native operation that really failed. Provider callbacks
and I/O remain opaque; valid, skipped and fallback controls retain legacy QC.
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from vtx import qc
from vtx.infra import Ctx
from tests.qc._support import _FakeContext, _run_qc

REPO = Path(__file__).resolve().parents[2]


class TestQCDiagnostics(unittest.TestCase):
    def context(self, **params):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return _FakeContext(Path(tmp.name), **params)

    def run_mocked(self, context):
        with mock.patch.object(qc.loudness_cache, 'lookup', return_value={
                'i_lufs': context.loud, 'tp_dbfs': context.tp}):
            return _run_qc(context)

    def test_opt_in_preserves_outputs_receipts_and_exit(self):
        cases = [({}, 0), ({'loud': -12}, 0), ({'loud': -10}, 1), ({'tp': 0}, 1),
                 ({'loud': None, 'tp': None, 'energy': None}, 1),
                 ({'covered': True}, 0), ({'dry': True}, 0), ({'strict': True}, 0),
                 ({'metadata': {'streams': [{'nb_frames': '23', 'width': 1280, 'height': 720}],
                                'format': {'duration': 0.9}}}, 1)]
        for params, expected_code in cases:
            with self.subTest(params=params):
                enabled = self.context(enabled=True, **params)
                disabled = self.context(**params)
                on = self.run_mocked(enabled); off = self.run_mocked(disabled)
                self.assertEqual(on, off)
                self.assertEqual(enabled.receipts, disabled.receipts)
                self.assertEqual(on[0], expected_code)
                self.assertTrue(enabled.diagnostics)
                for diagnostic in enabled.diagnostics:
                    self.assertNotEqual(diagnostic['rule_id'], 'unknown')
                    self.assertTrue(diagnostic['suggested_actions'])
                    self.assertIn('params', diagnostic['suggested_actions'][0])
                json.dumps(enabled.diagnostics, allow_nan=False)
                if params.get('dry'):
                    self.assertEqual(enabled.diagnostics[0]['status'], 'skipped')
                    self.assertEqual(enabled.commands, [])
                    self.assertIsNone(on[2])
                else:
                    self.assertEqual(len(on[2]['checks']), 11 if params.get('covered') else 12)
                    self.assertEqual(set(on[2]), {'checks', 'skipped_probes', 'quality'})
                    from vtx.runtime.quality import qc_quality
                    self.assertEqual(on[2]['quality'], qc_quality(on[2]))
                    self.assertEqual(enabled.receipts[-1][1],
                                     {'extra': {'skipped_probes': on[2]['skipped_probes']}})
                    self.assertTrue(all(set(check) == {'name', 'pass', 'detail'}
                                        for check in on[2]['checks']))

    def test_original_indices_values_and_thresholds(self):
        context = self.context(enabled=True)
        self.run_mocked(context)
        cuts = [d for d in context.diagnostics if d['rule_id'] == 'qc.cut_frame']
        self.assertEqual([d['json_path'] for d in cuts],
                         ['$.video_tracks[0].clips[1]', '$.video_tracks[0].clips[0]'])
        self.assertEqual(cuts[0]['measured']['source_frame'], {'value': 2, 'unit': 'frames'})
        self.assertEqual(cuts[0]['threshold']['mean_pixel_difference']['lt'], qc.QC_CUT_MEAN_MAX)
        window = next(d for d in context.diagnostics if d['rule_id'] == 'qc.clip_window')
        self.assertEqual(window['json_path'], '$.video_tracks[1].clips[1]')
        self.assertEqual(window['measured']['coverage']['value'], 1.0)
        self.assertEqual(window['threshold']['coverage']['gt'], qc.QC_WINDOW_COVER_MIN)

    def test_exemptions_are_skipped_and_do_not_probe(self):
        context = self.context(enabled=True)
        self.run_mocked(context)
        context.commands.clear(); context.diagnostics.clear()
        code, output, report = self.run_mocked(context)
        self.assertEqual(code, 0)
        skipped = [d for d in context.diagnostics if d['status'] == 'skipped']
        self.assertEqual(len(skipped), 6)
        self.assertEqual(len(report['skipped_probes']), 6)
        self.assertFalse(any('rawvideo' in cmd for cmd in context.commands))
        self.assertTrue(all(d['suggested_actions'][0] == {
            'op': 'run_qc', 'params': {'strict': True}} for d in skipped))

    def test_loudness_boundaries_keep_existing_warning_band(self):
        for loudness, expected in [(-14, 'pass'), (-12.5, 'pass'), (-12.49, 'warn'),
                                   (-11, 'warn'), (-10.99, 'fail')]:
            with self.subTest(loudness=loudness):
                context = self.context(enabled=True, loud=loudness)
                self.run_mocked(context)
                result = next(d for d in context.diagnostics if d['rule_id'] == 'qc.integrated_loudness')
                self.assertEqual(result['status'], expected)
                self.assertEqual(result['measured']['value'], loudness)

    def test_unavailable_evidence_is_unknown(self):
        context = self.context(enabled=True, loud=None, tp=None, energy=None, covered=True)
        self.run_mocked(context)
        unknown = {d['rule_id'] for d in context.diagnostics if d['status'] == 'unknown'}
        self.assertEqual(unknown, {'qc.integrated_loudness', 'qc.true_peak',
                                   'qc.stacked_audio_energy', 'qc.overlay_leak'})
        energy = next(d for d in context.diagnostics if d['rule_id'] == 'qc.stacked_audio_energy')
        self.assertIsNone(energy['measured']['mean_volume']['value'])


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires CPU FFmpeg tools')
class TestQCDiagnosticsCPU(unittest.TestCase):
    def command(self, args, **kwargs):
        result = subprocess.run(list(map(str, args)), capture_output=True, text=True,
                                timeout=180, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr[-5000:])
        return result

    def test_real_stages_then_frame_loudness_and_peak_failures(self):
        with tempfile.TemporaryDirectory(prefix='vtx_qc_diagnostics_') as tmp:
            root = Path(tmp)
            source = root / 'source.mp4'
            ffmpeg = shutil.which('ffmpeg')
            self.command([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                          'color=c=0x708090:s=320x180:r=30:d=2', '-f', 'lavfi', '-i',
                          'sine=frequency=1000:sample_rate=48000:duration=2',
                          '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast',
                          '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ac', '2', '-shortest', source])
            timeline = root / 'timeline.json'
            timeline.write_text(json.dumps({
                'version': 2,
                'project': {'fps': 30, 'width': 320, 'height': 180, 'duration_frames': 60},
                'sources': [{'id': 'source', 'path': 'source.mp4'}],
                'video_tracks': [{'name': 'base', 'kind': 'aroll', 'clips': [
                    {'src': 'source', 'src_in': 0, 'src_out': 60, 'dst_in': 0, 'dur': 60}]}],
                'subtitles': [],
                'audio_tracks': [{'name': 'tone', 'events': [
                    {'src': 'source', 'in': 0, 'out': 60, 'volume': 1.0}]}]}))
            env = dict(os.environ, VTX_PIPELINE='0', VTX_VIDEO_SEGMENTS='1', VTX_SEG_NVENC='0',
                       PYTHONDONTWRITEBYTECODE='1')
            self.command([sys.executable, REPO / 'vtx/vtx.py', '-t', timeline, 'all'], env=env, cwd=REPO)
            receipt = json.loads((root / 'out/receipt.json').read_text())
            for stage in ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc'):
                self.assertTrue(receipt['stages'][stage]['ok'], (stage, receipt))
            context = Ctx(timeline, diagnostics=[])
            context.strict = True
            code, _, report = _run_qc(context)
            self.assertEqual(code, 0)
            self.assertTrue(all(check['pass'] for check in report['checks']))
            by_rule = {d['rule_id']: d for d in context.diagnostics}
            self.assertEqual(by_rule['qc.frame_count']['measured']['value'], 60)
            self.assertEqual(by_rule['qc.frame_count']['measured']['method'], 'decoded_frame_count')
            self.assertEqual(by_rule['qc.integrated_loudness']['status'], 'pass')
            self.assertEqual(by_rule['qc.true_peak']['status'], 'pass')
            final = root / 'out/final.mp4'
            good = root / 'out/good.mp4'
            shutil.copyfile(final, good)
            # Keep later sample frames available while adding actual excess frames.
            # The source video ends at 2 s, so tpad duplicates its final picture.
            damaged = root / 'out/damaged.mp4'
            self.command([ffmpeg, '-v', 'error', '-y', '-i', good, '-vf',
                          'tpad=stop_mode=clone:stop_duration=0.2', '-c:v', 'libx264',
                          '-threads', '1', '-preset', 'ultrafast', '-c:a', 'copy', damaged])
            os.replace(damaged, final)
            context = Ctx(timeline, diagnostics=[]); context.strict = True
            code, _, _ = _run_qc(context)
            self.assertEqual(code, 1)
            count = next(d for d in context.diagnostics if d['rule_id'] == 'qc.frame_count')
            self.assertEqual(count['status'], 'fail')
            self.assertGreater(count['measured']['value'], 60)
            # Replace only the output audio with a near-full-scale tone. Actual
            # ebur128 measurements must fail both loudness and true peak gates.
            self.command([ffmpeg, '-v', 'error', '-y', '-i', good, '-f', 'lavfi', '-i',
                          'aevalsrc=0.99*sin(2*PI*1000*t)|0.99*sin(2*PI*1000*t):s=48000:d=2',
                          '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy',
                          '-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-shortest', damaged])
            os.replace(damaged, final)
            context = Ctx(timeline, diagnostics=[]); context.strict = True
            code, _, report = _run_qc(context)
            self.assertEqual(code, 1)
            by_rule = {d['rule_id']: d for d in context.diagnostics}
            self.assertEqual(by_rule['qc.frame_count']['status'], 'pass')
            self.assertEqual(by_rule['qc.integrated_loudness']['status'], 'fail')
            self.assertEqual(by_rule['qc.true_peak']['status'], 'fail')
            self.assertGreater(by_rule['qc.true_peak']['measured']['value'], qc.QC_TP_MAX)
            self.assertFalse(json.loads((root / 'out/receipt.json').read_text())['stages']['qc']['ok'])

    def test_real_black_canvas_subtitles_audio_through_qc(self):
        # Reduced CPU runtime control; 320x180 is intentionally outside the
        # formal schema's 1920x1080 resolution. The full-size test below owns
        # the formal-input completion regression. 240 frames permits four
        # actual segments with the production cut guard.
        for segments in (1, 4):
            with self.subTest(segments=segments), tempfile.TemporaryDirectory(prefix='vtx_qc_zero_tracks_') as temporary:
                root = Path(temporary)
                self.command(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                              'sine=frequency=1000:sample_rate=48000:duration=8',
                              '-c:a', 'pcm_s16le', root / 'tone.wav'])
                timeline = root / 'timeline.json'
                timeline.write_text(json.dumps({
                    'version': 2,
                    'project': {'fps': 30, 'width': 320, 'height': 180, 'duration_frames': 240},
                    'sources': [{'id': 'tone', 'path': 'tone.wav'}],
                    'video_tracks': [], 'subtitles': [{'in': 3, 'out': 15, 'text': 'TEST'}],
                    'audio_tracks': [{'name': 'tone', 'events': [
                        {'src': 'tone', 'in': 0, 'out': 240, 'volume': 1.0}]}]}))
                env = dict(os.environ, VTX_PIPELINE='0', VTX_VIDEO_SEGMENTS=str(segments),
                           VTX_SEG_NVENC='0', VTX_IO_WORKERS='1', VTX_FILTER_THREADS='1',
                           PYTHONDONTWRITEBYTECODE='1')
                cli = [sys.executable, REPO / 'vtx/vtx.py', '-t', timeline]
                self.command([*cli, 'all'], env=env, cwd=REPO)
                listing = root / 'cache/vseg/list.txt'
                if segments == 4:
                    lines = listing.read_text().splitlines()
                    self.assertEqual(len(lines), 4, lines)
                    starts = []
                    for index, line in enumerate(lines):
                        entry = re.fullmatch(r"file '(.+)'", line)
                        self.assertIsNotNone(entry, line)
                        segment = Path(entry.group(1))
                        name = re.fullmatch(r'seg(\d+)_(\d{6})_[0-9a-f]{12}\.mp4', segment.name)
                        self.assertIsNotNone(name, segment.name)
                        self.assertEqual(int(name.group(1)), index)
                        starts.append(int(name.group(2)))
                        self.assertTrue(segment.is_file(), segment)
                        probe = self.command(['ffprobe', '-v', 'error', '-count_frames',
                                              '-select_streams', 'v:0', '-show_entries',
                                              'stream=nb_read_frames', '-of', 'csv=p=0', segment])
                        self.assertEqual(int(probe.stdout.strip()), 60, segment)
                    self.assertEqual(starts, [0, 60, 120, 180])
                else:
                    self.assertFalse(listing.exists())
                receipt = json.loads((root / 'out/receipt.json').read_text())
                for stage in ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc'):
                    self.assertTrue(receipt['stages'][stage]['ok'], (stage, receipt))
                self.command([*cli, '--strict', 'qc'], env=env, cwd=REPO)
                report = json.loads((root / 'out/qc_report.json').read_text())
                self.assertEqual(len(report['checks']), 7)
                self.assertTrue(all(x['pass'] for x in report['checks']), report)
                leak = next(x for x in report['checks'] if x['name'].startswith('空窗无叠加泄漏'))
                self.assertEqual(leak['name'], '空窗无叠加泄漏 @frame 239')
                self.assertEqual(report['skipped_probes'], [])
                self.assertTrue((root / 'out/final.mp4').is_file())

                # Keep the real audio while replacing only video with visible
                # leakage. No track is added: QC must reject the bad final.
                final = root / 'out/final.mp4'
                damaged = root / 'out/leaked.mp4'
                self.command(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                              'color=c=white:s=320x180:r=30:d=8', '-i', final,
                              '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264',
                              '-threads', '1', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                              '-c:a', 'copy', '-shortest', damaged])
                os.replace(damaged, final)
                context = Ctx(timeline, diagnostics=[]); context.strict = True
                code, _, report = _run_qc(context)
                self.assertEqual(code, 1)
                failures = [x for x in report['checks'] if not x['pass']]
                self.assertEqual(len(failures), 1, report)
                self.assertEqual(failures[0]['name'], '空窗无叠加泄漏 @frame 239')
                self.assertFalse(json.loads((root / 'out/receipt.json').read_text())['stages']['qc']['ok'])


    def test_formal_size_zero_tracks_reports_without_crash(self):
        # Source-bearing 1080p input preserves the formal contract regression.
        # Existing FFmpeg downscale/range behavior can fail the leakage oracle;
        # require a complete, consistent report, never force that check green.
        with tempfile.TemporaryDirectory(prefix='vtx_qc_zero_tracks_1080p_') as temporary:
            root = Path(temporary)
            self.command(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                          'sine=frequency=1000:sample_rate=48000:duration=2',
                          '-c:a', 'pcm_s16le', root / 'tone.wav'])
            timeline = root / 'timeline.json'
            timeline.write_text(json.dumps({
                'version': 2,
                'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 60},
                'sources': [{'id': 'tone', 'path': 'tone.wav'}],
                'video_tracks': [], 'subtitles': [{'in': 3, 'out': 15, 'text': 'TEST'}],
                'audio_tracks': [{'name': 'tone', 'events': [
                    {'src': 'tone', 'in': 0, 'out': 60, 'volume': 1.0}]}]}))
            env = dict(os.environ, VTX_PIPELINE='0', VTX_VIDEO_SEGMENTS='1',
                       VTX_SEG_NVENC='0', VTX_IO_WORKERS='1', VTX_FILTER_THREADS='1',
                       PYTHONDONTWRITEBYTECODE='1')
            cli = [sys.executable, REPO / 'vtx/vtx.py', '-t', timeline]
            self.command([*cli, 'all', '--until', 'mux'], env=env, cwd=REPO)
            result = subprocess.run(list(map(str, [*cli, '--strict', 'qc'])),
                                    text=True, capture_output=True, timeout=180, env=env, cwd=REPO)
            self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            report = json.loads((root / 'out/qc_report.json').read_text())
            self.assertEqual(len(report['checks']), 7, report)
            leak = [x for x in report['checks'] if x['name'].startswith('空窗无叠加泄漏')]
            self.assertEqual(len(leak), 1, report)
            self.assertEqual(leak[0]['name'], '空窗无叠加泄漏 @frame 59')
            self.assertTrue(all(x['pass'] for x in report['checks'] if x not in leak), report)
            passed = all(x['pass'] for x in report['checks'])
            self.assertEqual(result.returncode, 0 if passed else 1)
            self.assertEqual(report['skipped_probes'], [])
            receipt = json.loads((root / 'out/receipt.json').read_text())
            for stage in ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux'):
                self.assertTrue(receipt['stages'][stage]['ok'], (stage, receipt))
            self.assertEqual(receipt['stages']['qc']['ok'], passed)


# Actual-operation failures share the same QC context and diagnostic contract.
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
        # A zero-exit fake producer is not evidence of a real FFmpeg failure.
        for length, position in ((n, p) for n in (0, 1, 1295, 1296, 1297) for p in (1, 2)):
            with self.subTest(length=length, position=position):
                context = self.context(covered=True)
                context.tl['video_tracks'] = [{'name': 'base', 'clips': [
                    {'src': 'a', 'src_in': 0, 'dst_in': 0, 'dur': 24}]}]
                original = context.run
                def run(command, **kwargs):
                    result = original(command, **kwargs)
                    if 'rawvideo' in command:
                        count = sum('rawvideo' in cmd for cmd in context.commands)
                        Path(command[-1]).write_bytes(bytes([100]) * (length if count == position else 1296))
                    return result
                context.run = run
                if length == 1296:
                    self.execute(context)
                    self.assertTrue(context.receipts[-1][0][1])
                    self.assertFalse(any(d['rule_id'] == 'qc.rawvideo_size' for d in context.diagnostics))
                    continue
                with self.assertRaises(SystemExit) as caught:
                    self.execute(context)
                self.assertEqual(caught.exception.code, 1)
                self.assertTrue(getattr(caught.exception, '_vtx_diagnostic_handled', False))
                record = context.diagnostics[-1]
                self.assertEqual(record['json_path'], '$')
                self.assertEqual(record['rule_id'], 'qc.rawvideo_size')
                self.assertEqual(record['status'], 'unknown')
                self.assertEqual(record['measured'], {'bytes': length})
                self.assertEqual(record['threshold'], {'bytes': 48 * 27})
                self.assertEqual(record['source']['provider'], 'ffmpeg')
                self.assertEqual(record['source']['resource'], str(context.src_path('a')))
                self.assertEqual(record['source']['measurement'], 'rawvideo')
                self.assertEqual(record['source']['origin'], '$.video_tracks[0].clips[0]')
                self.assertEqual(record['source']['frame'], 2)
                self.assertEqual(len(context.diagnostics), 1)
                self.assertEqual(sum('rawvideo' in command for command in context.commands), position)
                self.assertEqual(context.receipts, [])
                self.assertFalse((context.out / 'qc_report.json').exists())
                self.assertFalse((context.cache / qc._QC_SEG_RECORD).exists())
                self.assertFalse(list(context.cache.glob('qc_probe*')))

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

    def test_malformed_warm_record_uses_cache_origin(self):
        for location, value, pointer in [('final', 7, '$.final.size'),
                                         ('segment_start', 'bad', '$.segs[0].a')]:
            with self.subTest(location=location):
                context = self.context()
                self.execute(context)
                path = context.cache / qc._QC_SEG_RECORD
                record = json.loads(path.read_text())
                if location == 'final':
                    record['final'] = value
                else:
                    record['segs'][0]['a'] = value
                path.write_text(json.dumps(record))
                context.commands.clear(); context.diagnostics.clear()
                _, diagnostic = self.fault(context, AttributeError if location == 'final' else TypeError)
                self.assertEqual(diagnostic['rule_id'], 'qc.metadata_operation')
                self.assertEqual(diagnostic['source']['provider'], 'qc_pass_record')
                self.assertEqual(normalized(diagnostic['source']['provider_path']), pointer)
                self.assertEqual(len(context.commands), 1)

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
