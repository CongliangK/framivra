"""Ordinary-file unit tests for the opt-in historical fixture runner.

These tests use small synthetic byte strings, framehash text, and directly owned
Python subprocesses. They do not run the real replay, decode PNGs, or qualify a
Mac environment, a performance result, or an independent source review.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from experiments.perf_frame_source import real_fixture as runner


class TemporaryFiles(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='fixture-unit-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)


class TestEvidenceFiles(TemporaryFiles):
    def test_sha_bytes_known_vector(self):
        self.assertEqual(runner.sha_bytes(b'abc'),
                         'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

    def test_file_hash_reads_multiple_blocks_and_empty_file(self):
        path = self.root / 'evidence.bin'
        for data in (b'', b'a' * 65536 + b'last block\x00\xff'):
            with self.subTest(length=len(data)):
                path.write_bytes(data)
                self.assertEqual(runner.file_hash(path), hashlib.sha256(data).hexdigest())

    def test_json_pin_accepts_exact_bytes_at_size_limit(self):
        path = self.root / 'evidence.json'
        data = b'{ "frames": [1, 2], "note": "fixture" }\n'
        path.write_bytes(data)
        self.assertEqual(runner.read_json(path, hashlib.sha256(data).hexdigest(), len(data)),
                         {'frames': [1, 2], 'note': 'fixture'})

    def test_json_pin_rejects_equivalent_json_with_changed_bytes(self):
        path = self.root / 'evidence.json'
        original = b'{"frames":2}\n'
        path.write_bytes(b'{ "frames": 2 }\n')
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            runner.read_json(path, hashlib.sha256(original).hexdigest())

    def test_json_rejects_over_size_limit(self):
        path = self.root / 'evidence.json'
        data = b'{"frames":2}\n'
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'size limit'):
            runner.read_json(path, maximum=len(data) - 1)

    def test_json_rejects_invalid_json_even_with_matching_digest(self):
        path = self.root / 'evidence.json'
        data = b'{"frames":\n'
        path.write_bytes(data)
        with self.assertRaises(json.JSONDecodeError):
            runner.read_json(path, hashlib.sha256(data).hexdigest())

    def test_write_json_roundtrip_and_existing_file_is_not_replaced(self):
        path = self.root / 'new.json'
        value = {'note': '\u666e\u901a\u6d4b\u8bd5', 'frames': [0, 1]}
        runner.write_json(path, value)
        before = path.read_bytes()
        self.assertEqual(runner.read_json(path), value)
        self.assertTrue(before.endswith(b'\n'))
        with self.assertRaises(FileExistsError):
            runner.write_json(path, {'different': True})
        self.assertEqual(path.read_bytes(), before)


class TestFramehash(TemporaryFiles):
    def make_framehash(self, *, dimensions='2x2', time_base='1/30', rows=None):
        if rows is None:
            rows = [
                [0, 0, 0, 1, 16, 'A' * 64],
                [0, 1, 1, 1, 16, 'B' * 64],
            ]
        text = '#format: frame checksums\n#tb 0: %s\n#dimensions 0: %s\n' % (time_base, dimensions)
        text += '\n'.join(', '.join(map(str, row)) for row in rows) + '\n'
        path = self.root / 'frames.txt'
        path.write_text(text, encoding='utf-8')
        return path

    def test_accepts_exact_dimensions_timing_size_and_normalizes_hashes(self):
        self.assertEqual(runner.parse_framehash(self.make_framehash(), 2, 2, 2),
                         ['a' * 64, 'b' * 64])

    def test_accepts_single_channel_alpha_size(self):
        path = self.make_framehash(rows=[[0, 0, 0, 1, 4, 'c' * 64]])
        self.assertEqual(runner.parse_framehash(path, 1, 2, 2, channels=1), ['c' * 64])

    def test_rejects_dimensions_mismatch(self):
        path = self.make_framehash(dimensions='3x2')
        with self.assertRaisesRegex(ValueError, 'dimensions mismatch'):
            runner.parse_framehash(path, 2, 2, 2)

    def test_rejects_header_prefix_matches_and_duplicate_headers(self):
        for kwargs in ({'time_base': '1/300'}, {'dimensions': '2x20'}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError, 'mismatch'):
                    runner.parse_framehash(self.make_framehash(**kwargs), 2, 2, 2)
        path = self.make_framehash()
        path.write_text(path.read_text() + '#tb 0: 1/30\n')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            runner.parse_framehash(path, 2, 2, 2)

    def test_rejects_time_base_mismatch(self):
        path = self.make_framehash(time_base='1/25')
        with self.assertRaisesRegex(ValueError, 'time base'):
            runner.parse_framehash(path, 2, 2, 2)

    def test_rejects_pts_dts_duration_stream_and_size_mismatch(self):
        for column, value in ((0, 1), (1, 0), (2, 2), (3, 2), (4, 15)):
            with self.subTest(column=column, value=value):
                second = [0, 1, 1, 1, 16, 'b' * 64]
                second[column] = value
                path = self.make_framehash(rows=[[0, 0, 0, 1, 16, 'a' * 64], second])
                with self.assertRaisesRegex(ValueError, 'PTS/duration/size mismatch'):
                    runner.parse_framehash(path, 2, 2, 2)

    def test_rejects_too_few_or_too_many_frames(self):
        path = self.make_framehash()
        for expected in (1, 3):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, 'frame count mismatch'):
                    runner.parse_framehash(path, expected, 2, 2)

    def test_rejects_invalid_hash_or_row_field_count(self):
        for row in ([0, 0, 0, 1, 16, 'a' * 63],
                    [0, 0, 0, 1, 16, 'z' * 64],
                    [0, 0, 0, 1, 16],
                    [0, 0, 0, 1, 16, 'a' * 64, 'extra']):
            with self.subTest(row=row):
                with self.assertRaisesRegex(ValueError, 'invalid decoded framehash row'):
                    runner.parse_framehash(self.make_framehash(rows=[row]), 1, 2, 2)


class TestCompareArms(unittest.TestCase):
    def setUp(self):
        self.fixture = {'identity': 'ordinary-fixture', 'frames': [
            {'filename': 'f0000.png'}, {'filename': 'f0001.png'}]}
        self.left = self.make_arm('baseline')
        self.right = self.make_arm('candidate')

    def make_arm(self, directory):
        phases = {}
        for phase in ('cold', 'warm'):
            phases[phase] = {
                'argv': ['-framerate', '30', '-start_number', '0', '-i',
                         str(Path(directory) / 'rgba8-fixture' / 'f%04d.png')],
                'marker': {'sha256': 'c' * 64, 'stat': [directory]},
            }
        return {'fixture_identity': 'ordinary-fixture', 'phases': phases,
                'canonical': {'f0000.png': {'sha256': 'a' * 64, 'stat': [directory]},
                              'f0001.png': {'sha256': 'b' * 64, 'stat': [directory]}}}

    def test_accepts_matching_bytes_in_separate_directories_without_mutating_records(self):
        before = copy.deepcopy((self.left, self.right, self.fixture))
        runner.compare_arms(self.left, self.right, self.fixture)
        self.assertEqual((self.left, self.right, self.fixture), before)

    def test_rejects_fixture_identity_difference(self):
        self.right['fixture_identity'] = 'different-fixture'
        with self.assertRaisesRegex(ValueError, 'fixture identity'):
            runner.compare_arms(self.left, self.right, self.fixture)

    def test_rejects_shared_identity_not_matching_expected_fixture(self):
        self.fixture['identity'] = 'expected-other-fixture'
        with self.assertRaisesRegex(ValueError, 'fixture identity'):
            runner.compare_arms(self.left, self.right, self.fixture)

    def test_rejects_canonical_byte_digest_difference(self):
        self.right['canonical']['f0001.png']['sha256'] = 'd' * 64
        with self.assertRaisesRegex(ValueError, 'canonical PNG bytes differ'):
            runner.compare_arms(self.left, self.right, self.fixture)

    def test_rejects_argument_option_difference_in_either_phase(self):
        for phase in ('cold', 'warm'):
            with self.subTest(phase=phase):
                right = copy.deepcopy(self.right)
                right['phases'][phase]['argv'][1] = '25'
                with self.assertRaisesRegex(ValueError, 'input arguments differ'):
                    runner.compare_arms(self.left, right, self.fixture)

    def test_rejects_input_pattern_filename_difference(self):
        self.right['phases']['warm']['argv'][-1] = 'candidate/rgba8-fixture/f%05d.png'
        with self.assertRaisesRegex(ValueError, 'input arguments differ'):
            runner.compare_arms(self.left, self.right, self.fixture)

    def test_rejects_marker_digest_difference_in_either_phase(self):
        for phase in ('cold', 'warm'):
            with self.subTest(phase=phase):
                right = copy.deepcopy(self.right)
                right['phases'][phase]['marker']['sha256'] = 'd' * 64
                with self.assertRaisesRegex(ValueError, 'marker bytes differ'):
                    runner.compare_arms(self.left, right, self.fixture)

    def test_rejects_equal_arm_counts_that_do_not_match_fixture(self):
        self.fixture['frames'].append({'filename': 'f0002.png'})
        with self.assertRaisesRegex(ValueError, 'canonical count mismatch'):
            runner.compare_arms(self.left, self.right, self.fixture)


class TestBudget(TemporaryFiles):
    def setUp(self):
        super().setUp()
        self.directory = self.root / 'owned-process'
        self.directory.mkdir()
        self.owned_children = []

    def budget(self, **changes):
        limits = dict(runner.LIMITS)
        limits.update(total_seconds=30, disk_bytes=1 << 20, evidence_bytes=1024)
        limits.update(changes)
        return runner.Budget(self.root, limits)

    @staticmethod
    def reap_owned_child(process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)

    def run_child(self, budget, code, *, name='child', seconds=5):
        original_popen = subprocess.Popen

        def spawn(command, *args, **kwargs):
            process = original_popen(command, *args, **kwargs)
            if command[0] == sys.executable:
                self.owned_children.append(process)
                self.addCleanup(self.reap_owned_child, process)
            return process

        with mock.patch.object(runner.subprocess, 'Popen', side_effect=spawn):
            return budget.run([sys.executable, '-c', code], self.directory, name, seconds)

    def assert_process_record(self, budget, failure=None):
        self.assertEqual(len(self.owned_children), 1)
        process = self.owned_children[0]
        self.assertIsNotNone(process.poll())
        self.assertEqual(len(budget.processes), 1)
        record = budget.processes[0]
        self.assertEqual(record['pid'], process.pid)
        self.assertEqual(record['returncode'], process.returncode)
        if failure is None:
            self.assertIsNone(record['failure'])
        else:
            self.assertIn(failure, record['failure'])
        for channel in ('stdout', 'stderr'):
            data = (self.root / record[channel]).read_bytes()
            self.assertEqual(record[channel + '_sha256'], hashlib.sha256(data).hexdigest())
        return record

    def test_success_records_owned_process_and_output_digests(self):
        budget = self.budget()
        output = self.run_child(budget, 'import sys; print("ok"); print("note", file=sys.stderr)')
        self.assertEqual(output.read_bytes(), b'ok\n')
        self.assertEqual((self.directory / 'child.stderr').read_bytes(), b'note\n')
        record = self.assert_process_record(budget)
        self.assertEqual(record['returncode'], 0)

    def test_nonzero_child_is_rejected_and_recorded(self):
        budget = self.budget()
        with self.assertRaisesRegex(RuntimeError, 'exit 7'):
            self.run_child(budget, 'import sys; print("failure", file=sys.stderr); sys.exit(7)')
        self.assertEqual(self.assert_process_record(budget, 'exit 7')['returncode'], 7)

    def test_child_timeout_is_rejected_and_child_is_reaped(self):
        budget = self.budget()
        with self.assertRaisesRegex(TimeoutError, 'deadline'):
            self.run_child(budget, 'import time; time.sleep(10)', seconds=0.15)
        self.assert_process_record(budget, 'deadline')

    def test_stdout_limit_is_rejected_and_recorded(self):
        budget = self.budget(evidence_bytes=64)
        with self.assertRaisesRegex(ValueError, 'evidence output exceeded'):
            self.run_child(budget, 'import sys; sys.stdout.buffer.write(b"x" * 4096)')
        record = self.assert_process_record(budget, 'evidence output exceeded')
        self.assertGreater((self.root / record['stdout']).stat().st_size, 64)

    def test_stderr_limit_is_rejected_and_recorded(self):
        budget = self.budget(evidence_bytes=64)
        with self.assertRaisesRegex(ValueError, 'evidence output exceeded'):
            self.run_child(budget, 'import sys; sys.stderr.buffer.write(b"x" * 4096)')
        record = self.assert_process_record(budget, 'evidence output exceeded')
        self.assertGreater((self.root / record['stderr']).stat().st_size, 64)

    def test_mocked_rss_over_limit_rejects_and_reaps_owned_child(self):
        budget = self.budget(rss_bytes=1024 * 1024)
        probe_result = subprocess.CompletedProcess(['ps'], 0, stdout='1025\n', stderr='')
        with mock.patch.object(runner.subprocess, 'run', return_value=probe_result) as probe:
            with self.assertRaisesRegex(ValueError, 'RSS exceeds budget'):
                self.run_child(budget, 'import time; time.sleep(10)')
        record = self.assert_process_record(budget, 'RSS exceeds budget')
        self.assertEqual(record['sampled_peak_rss_bytes'], 1025 * 1024)
        self.assertEqual(record['rss_samples'][0][1], 1025 * 1024)
        self.assertEqual(probe.call_args.args[0], ['ps', '-o', 'rss=', '-p', str(record['pid'])])

    def test_total_deadline_check_rejects_without_spawning(self):
        with mock.patch.object(runner.time, 'monotonic', return_value=100.0):
            budget = self.budget(total_seconds=1)
        with mock.patch.object(runner.time, 'monotonic', return_value=101.0):
            with self.assertRaisesRegex(TimeoutError, 'total replay deadline'):
                budget.check()
        self.assertEqual(budget.processes, [])

    def test_owned_disk_check_accepts_boundary_then_rejects_excess(self):
        path = self.directory / 'ordinary.bin'
        path.write_bytes(b'12345678')
        budget = self.budget(disk_bytes=8)
        budget.check()
        path.write_bytes(b'123456789')
        with self.assertRaisesRegex(ValueError, 'scratch disk budget'):
            budget.check()


class TestCopyCase(TemporaryFiles):
    def setUp(self):
        super().setUp()
        self.origin = self.root / 'ordinary-originals'
        self.origin.mkdir()
        self.output = self.root / 'owned-output'
        self.output.mkdir()
        self.payloads = [b'ordinary frame 0', b'ordinary frame 1',
                         b'ordinary frame 2', b'ordinary frame 3']
        rows = []
        for index, data in enumerate(self.payloads):
            filename = 'f%04d.png' % index
            (self.origin / filename).write_bytes(data)
            rows.append({'filename': filename, 'bytes': len(data),
                         'sha256': hashlib.sha256(data).hexdigest()})
        marker = self.origin / 'complete.json'
        marker.write_text(json.dumps({'schema': 1, 'key': 'test-page', 'frames': 4,
            'width': 2, 'height': 2, 'sha256': [row['sha256'] for row in rows],
            'environment': {'fixture': 'unit test'}}) + '\n', encoding='utf-8')
        self.source = {'directory': str(self.origin), 'rows': rows,
                       'spec': {'frames': 4, 'width': 2, 'height': 2,
                                'rgb': (2, 3), 'small': (1, 4)},
                       'marker': str(marker), 'marker_sha256': runner.file_hash(marker),
                       'marker_stat': runner.stat_token(marker)}
        limits = dict(runner.LIMITS, total_seconds=30, disk_bytes=1 << 20)
        self.budget = runner.Budget(self.output, limits)
        self.guards = {}

    def copy(self, *, interval=(1, 4), name='case'):
        directory = self.output / name
        fixture = runner.copy_case('test-page', self.source, interval,
                                   directory, self.budget, self.guards)
        return directory, fixture

    def test_copy_preserves_source_bytes_and_maps_local_original_rgb_indices(self):
        originals = {path.name: path.read_bytes() for path in self.origin.iterdir()}
        directory, fixture = self.copy()
        self.assertEqual(fixture['interval'], [1, 4])
        self.assertEqual(fixture['schema'], runner.SCHEMA)
        self.assertEqual(fixture['frames'], [
            {'local': local, 'original': original, 'filename': 'f%04d.png' % local,
             'bytes': len(self.payloads[original]),
             'sha256': hashlib.sha256(self.payloads[original]).hexdigest(),
             'rgb': original == 2}
            for local, original in enumerate((1, 2, 3))])
        for local, original in enumerate((1, 2, 3)):
            self.assertEqual((directory / 'source' / ('f%04d.png' % local)).read_bytes(),
                             self.payloads[original])
        self.assertEqual((directory / 'origin-complete.json').read_bytes(), originals['complete.json'])
        self.assertEqual({path.name: path.read_bytes() for path in self.origin.iterdir()}, originals)
        self.assertEqual(runner.read_json(directory / 'fixture.json'), fixture)
        identity_input = dict(fixture)
        identity_input.pop('identity')
        identity_bytes = json.dumps(identity_input, sort_keys=True, separators=(',', ':')).encode()
        self.assertEqual(fixture['identity'],
                         'historical-b287-fixture-' + hashlib.sha256(identity_bytes).hexdigest())
        runner.source_guards({'test-page': self.source}, self.guards)

    def test_same_interval_has_stable_identity_across_owned_output_directories(self):
        _, first = self.copy(name='first')
        _, second = self.copy(name='second')
        self.assertEqual(first, second)

    def test_rejects_invalid_intervals(self):
        for index, interval in enumerate(((-1, 2), (2, 2), (3, 2), (0, 5))):
            with self.subTest(interval=interval):
                with self.assertRaisesRegex(ValueError, 'invalid source fixture interval'):
                    self.copy(interval=interval, name='invalid-%d' % index)

    def test_rejects_copy_that_exceeds_owned_disk_budget(self):
        self.budget.limits['disk_bytes'] = sum(row['bytes'] for row in self.source['rows'][1:]) - 1
        with self.assertRaisesRegex(ValueError, 'scratch budget for source copy'):
            self.copy()
        self.assertFalse((self.output / 'case' / 'fixture.json').exists())

    def test_rejects_same_length_source_content_change_against_pinned_digest(self):
        path = self.origin / 'f0001.png'
        path.write_bytes(b'X' * len(self.payloads[1]))
        with self.assertRaisesRegex(ValueError, 'pinned digest'):
            self.copy()
        self.assertFalse((self.output / 'case' / 'fixture.json').exists())

    def test_rejects_source_length_change_before_copy(self):
        (self.origin / 'f0001.png').write_bytes(self.payloads[1][:-1])
        with self.assertRaisesRegex(ValueError, 'metadata changed before copy'):
            self.copy()

    def test_rejects_missing_ordinary_source_file(self):
        (self.origin / 'f0001.png').unlink()
        with self.assertRaises(FileNotFoundError):
            self.copy()

    def test_rejects_mocked_short_read_before_expected_eof(self):
        original_open = Path.open
        origin = self.origin / 'f0001.png'

        def short_read(path, *args, **kwargs):
            if path == origin and args == ('rb',):
                return io.BytesIO(self.payloads[1][:-1])
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, 'open', short_read):
            with self.assertRaisesRegex(ValueError, 'pinned digest'):
                self.copy()

    def test_rejects_mocked_growth_after_expected_eof_with_bounded_read(self):
        original_open = Path.open
        origin = self.origin / 'f0001.png'
        requests = []

        class ExtraByteStream(io.BytesIO):
            def read(self, size=-1):
                requests.append(size)
                return super().read(size)

        def extra_byte(path, *args, **kwargs):
            if path == origin and args == ('rb',):
                return ExtraByteStream(self.payloads[1] + b'!')
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, 'open', extra_byte):
            with self.assertRaisesRegex(ValueError, 'grew during copy'):
                self.copy()
        self.assertEqual(requests, [len(self.payloads[1]) + 1])
        self.assertEqual(origin.read_bytes(), self.payloads[1])

    def test_rejects_ordinary_certificate_content_change(self):
        Path(self.source['marker']).write_text('{"changed": true}\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'original certificate changed'):
            self.copy()

    def test_source_guards_reject_source_change_between_copies(self):
        self.copy(name='first')
        (self.origin / 'f0001.png').write_bytes(b'changed ordinary content')
        with self.assertRaisesRegex(ValueError, 'original source changed'):
            runner.source_guards({'test-page': self.source}, self.guards)
        with self.assertRaisesRegex(ValueError, 'metadata changed before copy'):
            self.copy(name='second')

    def test_source_guards_reject_certificate_change_after_copy(self):
        self.copy()
        Path(self.source['marker']).write_text('{"changed": true}\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'certificate changed across the replay'):
            runner.source_guards({'test-page': self.source}, self.guards)

    def test_audit_page_returns_exact_hashes_and_file_count(self):
        directory, fixture = self.copy()
        audit = runner.audit_page(directory / 'source', fixture)
        self.assertEqual(set(audit), {'f0000.png', 'f0001.png', 'f0002.png'})
        for row in fixture['frames']:
            self.assertEqual(audit[row['filename']]['sha256'], row['sha256'])
            self.assertEqual(audit[row['filename']]['stat'],
                             runner.stat_token(directory / 'source' / row['filename']))

    def test_audit_page_rejects_appended_frame_after_eof(self):
        directory, fixture = self.copy()
        (directory / 'source' / 'f0003.png').write_bytes(b'extra ordinary frame')
        with self.assertRaisesRegex(ValueError, 'EOF mismatch'):
            runner.audit_page(directory / 'source', fixture)

    def test_audit_page_rejects_missing_middle_frame(self):
        directory, fixture = self.copy()
        (directory / 'source' / 'f0001.png').unlink()
        with self.assertRaisesRegex(ValueError, 'EOF mismatch'):
            runner.audit_page(directory / 'source', fixture)


if __name__ == '__main__':
    unittest.main()
