"""Synthetic-only tests: no runner, subprocess, media tools or private evidence."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from experiments.perf_svg_workflow import summarize as summary
H = 'a' * 64


def command(seconds=1):
    return {'elapsed_seconds': seconds, 'exit_code': 0, 'timed_out': False, 'error': None,
            'argv': ['/PRIVATE/SECRET'], 'resources': {
                'status': 'partial', 'method': 'ps-descendant-tree', 'samples': 4,
                'cpu_seconds_lower_bound': 3, 'peak_tree_rss_bytes': 100,
                'errors': ['/PRIVATE/SECRET'], 'reasons': {'rss': '/PRIVATE/SECRET'},
                'disk_io': {'status': 'partial', 'read_bytes_lower_bound': None,
                            'written_bytes_lower_bound': 7, 'errors': ['/PRIVATE/SECRET']},
                'unexpected': '/PRIVATE/SECRET'},
            'host_memory': {'before': {'pressure_level': {'value': 1, 'state': '/PRIVATE/SECRET'},
                'swapusage': {'used_bytes': 12}, 'errors': ['/PRIVATE/SECRET']}}}


def events():
    from vtx.runtime.events import ExecutionContext
    class Stream(io.StringIO):
        def close(self):
            pass
    stream = Stream()
    now = [0]
    execution = ExecutionContext(stream, clock=lambda: now[0])
    first = execution.task('gfx', kind='stage')
    first.started = 0
    execution.emit('task_start', task=first, scope='stage', started=0)
    now[0] = 1
    second = execution.task('video', kind='stage')
    second.started = 1
    execution.emit('task_start', task=second, scope='stage', started=1)
    now[0] = 2
    first.finish(0)
    now[0] = 3
    second.finish(0)
    execution.finish()
    data = stream.getvalue().encode()
    assert summary.read_events(data)['complete']
    return [json.loads(line) for line in data.splitlines()]


def write(root, name, value, lines=False):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(json.dumps(row) for row in value) + '\n' if lines else json.dumps(value))


def fixture(root):
    sequence = [H] * 900
    pts = {'streams': [{'time_base': '1/30', 'duration_ts': 900}],
           'frames': [{'pts': i} for i in range(900)]}
    cache = {'cache/svg/f%04d.png' % i: {'sha256': H, 'bytes': 1, 'mtime_ns': 1} for i in range(900)}
    rows = []
    for pair, order in enumerate([['baseline', 'candidate'], ['candidate', 'baseline'], ['baseline', 'candidate']], 1):
        for arm in order:
            name = 'formal-v2/pair-%d-%s' % (pair, arm)
            seconds = 100 if arm == 'baseline' else [60, 70, 65][pair - 1]
            raw = {'status': 'passed', 'environment': {'VTX_CONFIG': '/PRIVATE/SECRET'},
                'source_sha256': {'vtx/runtime.py': H if arm == 'baseline' else 'b' * 64},
                'input_sha256': {'timeline.json': H, 'realization.json': ('b' if arm == 'baseline' else 'c') * 64,
                                 'logs/prepare.log': ('d' if arm == 'baseline' else 'e') * 64,
                                 '.generated/internal.json': ('f' if arm == 'baseline' else 'a') * 64},
                'tools_sha256': {'/PRIVATE/SECRET/ffmpeg': H}, 'harness_sha256': H, 'helper_sha256': H,
                'commands': {label: command(seconds if label.endswith('-all') else 1)
                    for label in ['prepare', 'verify', 'cold-all', 'warm-all', 'cold-strict', 'warm-strict']},
                'states': []}
            for state in ['cold', 'warm']:
                raw['states'].append({'state': state, 'status': 'passed', 'exact_pts': True,
                    'media': {'status': 'passed', 'checks': {'full_decode': True},
                              **{key: H for key in ['video_digest', 'audio_digest', 'audio_timeline_digest']},
                              'evidence': {'localpath': '/PRIVATE/SECRET'}},
                    'decoded_audio': {'samples': 1440000, 'sample_rate': 48000, 'channels': 2,
                                      'sample_format': 's16le', 'decoded_duration': '30', 'source_duration': '30', 'pcm_sha256': H},
                    'mixed_audio_receipt': {'input_i': -20.5, 'output_i': -14.0, 'gain_db': 6.5,
                                            'target': -14, 'tp_dbfs': -4.4, 'sha256': H, 'error': '/PRIVATE/SECRET'},
                    'png_sequence_sha256': summary.digest(json.dumps(sequence).encode()),
                    'gfx_certificate_build': {'raster_seconds': 1234}})
                for mode in ['all', 'strict']:
                    write(root, name + '/' + state + '-' + mode + '.events.jsonl', events(), True)
                    write(root, name + '/' + state + ('-all-qc_report.json' if mode == 'all' else '-strict-qc.json'),
                          {'checks': [{'pass': True, 'detail': '/PRIVATE/SECRET'}], 'skipped_probes': []})
                write(root, name + '/' + state + '-exact-pts.json', pts)
                write(root, name + '/' + state + '-cache.json', cache)
                write(root, name + '/' + state + '-svg-complete.json',
                      {'sha256': sequence, 'export_count': 900, 'raster_seconds': 2, 'batch_count': 4,
                       'input': {'libraries': {'/PRIVATE/SECRET': '/PRIVATE/SECRET'}}})
            write(root, name + '/summary.json', raw)
            rows.append({'pair': pair, 'role': arm, 'status': 'passed', 'argv': ['/PRIVATE/SECRET']})
    write(root, 'formal-v2/summary.json', {'status': 'passed', 'rows': rows,
          'runner_sha256': H, 'helper_sha256': H,
          'foreign_media_observations': [], 'identities': {'baseline': {'commit': '8b882fa'},
          'candidate': {'commit': 'f5762ea18bf4c4be5a5893a997aeacd363f49ac3', 'tree': '64a29fa'}}})
    write(root, 'postmeasure.json', {'status': 'passed', 'commands': {'/PRIVATE/SECRET': command()}})


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture(self.root)

    def result(self):
        return summary.summarize(self.root, 'formal-v2')

    def change(self, filename, fn):
        value = json.loads((self.root / filename).read_text())
        fn(value)
        write(self.root, filename, value)

    def test_qualified_redacted_medians_and_separate_timings(self):
        result = self.result()
        self.assertTrue(result['ready'], result['issues'])
        self.assertEqual(result['median_ratios']['cold_all_new_over_base'], .65)
        self.assertEqual(result['trials'][0]['prepare_seconds'], 1)
        self.assertEqual(result['trials'][0]['states']['cold']['prepare_plus_all_seconds'], 101)
        self.assertIsNone(result['trials'][0]['states']['warm']['prepare_plus_all_seconds'])
        self.assertEqual(result['trials'][0]['states']['warm']['certificate_build_provenance']['raster_seconds'], 2)
        self.assertFalse(result['trials'][0]['states']['cold']['mixed_wav_evidence_available'])
        self.assertEqual(result['trials'][0]['states']['cold']['mixed_wav'], {})
        self.assertTrue(result['trials'][0]['states']['cold']['decoded_audio_clock_passed'])
        self.assertEqual(result['trials'][0]['states']['cold']['mixed_audio_receipt']['output_i'], -14.0)
        self.assertEqual(result['trials'][0]['states']['cold']['mixed_audio_receipt']['tp_dbfs'], -4.4)
        text = json.dumps(result)
        self.assertNotIn('/PRIVATE/SECRET', text)
        self.assertNotIn('argv', text)
        self.assertTrue(all(not Path(name).is_absolute() for name in result['evidence_index']))
        resource = result['trials'][0]['commands']['cold-all']['resources']
        self.assertEqual(resource['error_count'], 1)
        self.assertIsNone(resource['disk_io']['read_bytes_lower_bound'])
        self.assertEqual(resource['disk_io']['written_bytes_lower_bound'], 7)

    def test_union_excludes_process_and_rejects_bad_receipt(self):
        rows = events()
        self.assertEqual(summary.event_projection(rows)['all_stages_union_seconds'], 3)
        self.assertEqual(summary.event_projection(rows)['stage_union_seconds'], {'gfx': 2, 'video': 2})
        bad = copy.deepcopy(rows)
        bad.insert(3, dict(bad[2], event='receipt_commit', task_id='b', stage='gfx', extensions={'receipt_stage': 'gfx'}))
        for i, row in enumerate(bad, 1):
            row['seq'] = i
        self.assertFalse(summary.event_projection(bad)['complete_success'])
        process = copy.deepcopy(rows)
        process[1]['scope'] = process[3]['scope'] = 'process'
        self.assertEqual(summary.event_projection(process)['all_stages_union_seconds'], 2)

    def test_foreign_samples_and_observer_absence_refuse_ready(self):
        self.change('formal-v2/summary.json', lambda v: v.update(foreign_media_observations=[{'argv': '/PRIVATE/SECRET'}]))
        result = self.result()
        self.assertFalse(result['ready'])
        self.assertEqual(result['observed_foreign_media_sample_count'], 1)
        self.assertNotIn('/PRIVATE/SECRET', json.dumps(result))
        self.change('formal-v2/summary.json', lambda v: v.pop('foreign_media_observations'))
        self.assertFalse(self.result()['ready'])

    def test_incomplete_wrong_order_and_postmeasure_fail(self):
        self.change('formal-v2/summary.json', lambda v: v['rows'].reverse())
        self.assertFalse(self.result()['ready'])
        self.change('postmeasure.json', lambda v: v['commands']['/PRIVATE/SECRET'].update(exit_code=1))
        self.assertEqual(self.result()['postmeasure_commands']['command_1']['exit_code'], 1)

    def test_exact_pts_and_qc_failure(self):
        name = 'formal-v2/pair-1-baseline/cold-exact-pts.json'
        self.change(name, lambda v: v['frames'][100].update(pts=101))
        result = self.result()
        self.assertFalse(result['ready'])
        self.assertFalse(result['trials'][0]['states']['cold']['exact_900_pts_30sec'])
        self.change('formal-v2/pair-1-candidate/warm-strict-qc.json', lambda v: v['checks'][0].update(pass_=False, **{'pass': False}))
        self.assertFalse(self.result()['trials'][1]['states']['warm']['strict_qc_passed'])

    def test_output_mismatch_and_bad_png_snapshot(self):
        self.change('formal-v2/pair-1-candidate/summary.json', lambda v: v['states'][0]['media'].update(audio_digest='b' * 64))
        self.assertFalse(self.result()['equality']['all_12_output_signatures'])
        self.change('formal-v2/pair-1-baseline/cold-cache.json', lambda v: v['cache/svg/f0000.png'].update(sha256='b' * 64))
        self.assertFalse(self.result()['trials'][0]['states']['cold']['png_900_passed'])

    def test_no_ablation_denominator_and_failures_preserved(self):
        write(self.root, 'ablation-serial/summary.json', {'status': 'failed', 'error': '/PRIVATE/SECRET'})
        write(self.root, 'formal/summary.json', {'status': 'failed', 'error': '/PRIVATE/SECRET', 'rows': []})
        result = summary.summarize(self.root, 'formal-v2', ['formal'])
        self.assertEqual(result['median_ratios']['cold_all_new_over_base'], .65)
        self.assertTrue(result['excluded_failed_attempts'][0]['failed'])
        self.assertTrue(result['excluded_samples'][2]['failure_present'])
        self.assertNotIn('/PRIVATE/SECRET', json.dumps(result))

    def test_serial_tuning_and_wrong_baseline_refused(self):
        self.change('formal-v2/pair-1-baseline/summary.json', lambda v: v['environment'].update(VTX_SVG_FORCE_SERIAL='1'))
        self.assertFalse(self.result()['ready'])
        self.change('formal-v2/summary.json', lambda v: v['identities']['baseline'].update(commit='fffffff'))
        self.assertIn('baseline_8b882fa_identity_not_established', self.result()['issues'])

    def test_decoded_audio_clock_is_not_a_mixed_wav_measurement(self):
        name = 'formal-v2/pair-1-baseline/summary.json'
        self.change(name, lambda v: v['states'][0]['decoded_audio'].update(samples=1440001))
        self.assertFalse(self.result()['trials'][0]['states']['cold']['decoded_audio_clock_passed'])
        self.assertFalse(self.result()['ready'])
        def helper_timing(value):
            row = value['states'][0]
            timing = row.pop('decoded_audio')
            timing['sample_frames'] = 1440000
            timing.pop('samples')
            row['media']['audio_timing'] = timing
        self.change(name, helper_timing)
        result = self.result()
        self.assertTrue(result['ready'])
        self.assertEqual(result['trials'][0]['states']['cold']['decoded_audio_timing_source'], 'media.audio_timing')
        self.assertFalse(result['trials'][0]['states']['cold']['mixed_wav_evidence_available'])

    def test_strict_reducer_receives_original_bytes(self):
        name = 'formal-v2/pair-1-baseline/cold-all.events.jsonl'
        path = self.root / name
        # A valid decoded JSON sequence without its final newline is incomplete.
        path.write_bytes(path.read_bytes().rstrip(b'\n'))
        self.assertFalse(self.result()['ready'])
        with self.assertRaises(ValueError):
            summary.Evidence(self.root).load(name, lines=True)
        invalid = events()
        invalid[3]['monotonic_duration'] = 1000000
        write(self.root, name, invalid, True)
        with self.assertRaises(ValueError):
            summary.Evidence(self.root).load(name, lines=True)

    def test_common_observation_patch_and_candidate_mapping(self):
        patch = 'c' * 40
        def identities(value):
            for arm in ['baseline', 'candidate']:
                value['identities'][arm].update(common_instrumentation_patch=patch + ': vtx/runtime/events.py only', events_py_sha256=H)
            value['identities']['candidate'].update(commit=patch, original_runtime_commit='b423988')
        self.change('formal-v2/summary.json', identities)
        for pair in range(1, 4):
            for arm in ['baseline', 'candidate']:
                self.change('formal-v2/pair-%d-%s/summary.json' % (pair, arm),
                            lambda v: v['source_sha256'].update({'vtx/runtime/events.py': H}))
        self.assertTrue(self.result()['ready'])
        self.change('formal-v2/summary.json', lambda v: v['identities']['candidate'].update(events_py_sha256='b' * 64))
        self.assertIn('common_instrumentation_patch_mismatch', self.result()['issues'])
        self.change('formal-v2/summary.json', lambda v: v['identities']['candidate'].update(original_runtime_commit='fffffff'))
        self.assertIn('candidate_runtime_mapping_not_established', self.result()['issues'])

    def test_invalid_manifest_and_target_fail_closed(self):
        self.change('formal-v2/pair-1-baseline/summary.json', lambda v: v['input_sha256'].update({'invalid.json': '/PRIVATE/SECRET'}))
        self.assertIn('invalid_identity_manifest_entry', self.result()['trials'][0]['issues'])
        for pair in [1, 3]:
            self.change('formal-v2/pair-%d-candidate/summary.json' % pair,
                        lambda v: v['commands']['cold-all'].update(elapsed_seconds=71))
        self.assertFalse(self.result()['cold_all_median_le_0_70'])

    def test_missing_evidence_cli_nonzero_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as empty:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(summary.main(['--evidence-root', empty]), 1)
            self.assertFalse(json.loads(output.getvalue())['ready'])
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / 'secret.json'
            external.write_text('{}')
            (self.root / 'escape.json').symlink_to(external)
            with self.assertRaises(ValueError):
                summary.Evidence(self.root).load('escape.json')
        with self.assertRaises(ValueError):
            summary.summarize(self.root, '../outside')


if __name__ == '__main__':
    unittest.main()
