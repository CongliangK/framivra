"""Cheap QC reuse regressions: byte fixtures and mocked probes, no media tools."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from vtx import qc


class VerifiedQCRecordsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.c = SimpleNamespace(root=root, out=root / 'out', cache=root / 'cache',
                                 dur=20, fps=20, spf=.05, w=48, h=27,
                                 strict=False, dry=False,
                                 tl={'video_tracks': [{'clips': []}],
                                     'subtitles': [], 'audio_tracks': []},
                                 receipt=lambda *a, **k: None)
        self.c.out.mkdir()
        (self.c.cache / 'vseg').mkdir(parents=True)
        self.final = self.c.out / 'final.mp4'
        self.seg = self.c.cache / 'vseg' / 'seg0_000000_012345abcdef.mp4'
        self.other = self.c.cache / 'vseg' / 'seg1_000010_abcdef012345.mp4'
        for path in (self.final, self.seg, self.other):
            path.write_bytes(b'A' * (256 * 1024))
        self.lst = self.c.cache / 'vseg' / 'list.txt'
        self.lst.write_text(f"file '{self.seg.name}'\nfile '{self.other.name}'\n")
        self.record = self.c.cache / qc._QC_SEG_RECORD
        self.probes = []
        self.c.run = self.run_probe

    def run_probe(self, cmd):
        self.probes.append(cmd)
        if cmd[0] == 'ffprobe':
            return SimpleNamespace(stdout=json.dumps({'streams': [
                {'nb_frames': '20', 'nb_read_frames': '20', 'width': 48, 'height': 27}],
                'format': {'duration': '1'}}))
        if cmd[-1] != '-':
            Path(cmd[-1]).write_bytes(bytes(48 * 27))
        return SimpleNamespace(stderr='mean_volume: -20 dB')

    def write_record(self):
        qc._write_qc_pass_record(self.c, 20, [{'name': 'sample', 'pass': True}])
        return json.loads(self.record.read_text())

    def mutate_interior(self, path):
        before = path.stat()
        with path.open('r+b') as stream:
            stream.seek(128 * 1024)
            stream.write(b'B')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(before.st_size, path.stat().st_size)
        self.assertEqual(before.st_mtime_ns, path.stat().st_mtime_ns)

    def run_qc(self):
        with patch.object(qc.loudness_cache, 'lookup', return_value={
                'i_lufs': -14, 'tp_dbfs': -2}), redirect_stdout(io.StringIO()):
            qc.qc(self.c)
        return json.loads((self.c.out / 'qc_report.json').read_text())

    def test_unchanged_reuses_full_identity_records(self):
        saved = self.write_record()
        self.assertEqual(saved['version'], 2)
        self.assertEqual(saved, qc._load_qc_pass_record(self.c))
        self.assertEqual(saved['final']['content_identity']['level'], 'Verified')

    def test_final_interior_same_stat_rejects_entire_record(self):
        self.write_record()
        self.mutate_interior(self.final)
        self.assertIsNone(qc._load_qc_pass_record(self.c))

    def test_segment_interior_same_stat_only_rejects_changed_segment(self):
        self.write_record()
        self.mutate_interior(self.seg)
        loaded = qc._load_qc_pass_record(self.c)
        self.assertEqual([s['name'] for s in loaded['segs']], [self.other.name])

    def test_legacy_and_malformed_records_are_cold(self):
        valid = self.write_record()
        bad = [None, [], {'version': 1}, {**valid, 'final': []},
               {**valid, 'segs': [None]}, {**valid, 'checks': {'sample': 'true'}}]
        for location in ('final', 'segment'):
            rec = copy.deepcopy(valid)
            for field, value in [('scope', 'sampled'), ('digest', '0' * 63), ('level', 'Quick')]:
                variant = copy.deepcopy(rec)
                dest = variant['final'] if location == 'final' else variant['segs'][0]
                dest['content_identity'][field] = value
                bad.append(variant)
        for rec in bad:
            with self.subTest(record=rec):
                self.record.write_text(json.dumps(rec))
                self.assertIsNone(qc._load_qc_pass_record(self.c))

    def test_read_failures_are_cold_and_do_not_write_records(self):
        self.write_record()
        original = qc.cache_state.verified_media_identity
        for target in (self.final, self.seg):
            def failing(path):
                if Path(path) == target:
                    raise OSError('unreadable fixture')
                return original(path)
            with self.subTest(target=target), patch.object(
                    qc.cache_state, 'verified_media_identity', side_effect=failing):
                self.assertIsNone(qc._load_qc_pass_record(self.c))
                self.record.unlink(missing_ok=True)
                qc._write_qc_pass_record(self.c, 20, [])
                self.assertFalse(self.record.exists())
            self.write_record()
        self.lst.write_bytes(b'\xff')
        self.assertIsNone(qc._load_qc_pass_record(self.c))

    def test_transaction_safety_errors_are_not_swallowed(self):
        with patch.object(qc.fstxn, 'atomic_write', side_effect=RuntimeError('unsafe write')):
            with self.assertRaisesRegex(RuntimeError, 'unsafe write'):
                self.write_record()

    def test_active_list_changes_and_malformed_lines_are_cold(self):
        self.write_record()
        for text in (f"file '{self.seg.name}'\n", 'not a concat entry\n',
                     f"file '{self.other.name}'\nfile '{self.seg.name}'\n"):
            with self.subTest(text=text):
                self.lst.write_text(text)
                self.assertIsNone(qc._load_qc_pass_record(self.c))

    def test_duplicate_failed_check_never_becomes_pass(self):
        qc._write_qc_pass_record(self.c, 20, [
            {'name': 'sample', 'pass': True}, {'name': 'sample', 'pass': False}])
        self.assertFalse(qc._load_qc_pass_record(self.c)['checks']['sample'])

    def test_qc_checks_and_strict_mode_preserved(self):
        cold = self.run_qc()
        warm = self.run_qc()
        self.assertEqual(len(warm['skipped_probes']), 1)
        self.assertEqual([(c['name'], c['pass']) for c in cold['checks']],
                         [(c['name'], c['pass']) for c in warm['checks']])
        self.c.strict = True
        with patch.object(qc, '_load_qc_pass_record', side_effect=AssertionError('strict loads record')):
            strict = self.run_qc()
        self.assertEqual(strict['skipped_probes'], [])
        self.assertTrue(any('-count_frames' in cmd for cmd in self.probes))

    def test_real_qc_rechecks_mutated_final_and_active_segment(self):
        for target in (self.final, self.other):
            with self.subTest(target=target):
                self.run_qc()
                self.assertEqual(len(self.run_qc()['skipped_probes']), 1)
                self.mutate_interior(target)
                self.assertEqual(self.run_qc()['skipped_probes'], [])


if __name__ == '__main__':
    unittest.main()
