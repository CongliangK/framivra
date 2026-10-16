"""Issue85 cheap boundary regressions; no decoder or real-media performance claim."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vtx import cache_state as cs, hygiene, loudness_cache as lc
from vtx.infra import Ctx


class VerifiedMediaIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.media = self.root / 'source.bin'
        self.media.write_bytes(b'a' * (4 * cs.SAMPLE_BYTES))
        self.tl = self.root / 'timeline.json'
        self.tl.write_text(json.dumps({'project': {'fps': 30, 'duration_frames': 60,
            'width': 100, 'height': 100}, 'sources': [{'id': 's', 'path': 'source.bin'}],
            'video_tracks': [], 'audio_tracks': [], 'subtitles': []}))
        self.c = Ctx(self.tl)
        self.clip = {'src': 's', 'src_in': 0, 'src_out': 60, 'dur': 60}

    def mutate(self):
        st = self.media.stat()
        with self.media.open('r+b') as f:
            f.seek(2 * cs.SAMPLE_BYTES)
            f.write(b'B')
        os.utime(self.media, ns=(st.st_atime_ns, st.st_mtime_ns))

    def seg(self):
        return hygiene.seg_key(self.c, self.clip, 0, 0, False, False, 'x264')

    def test_each_formal_source_consumer_invalidates_middle_mutation(self):
        fast = cs.media_stat_digest(self.media)
        identity = cs.verified_media_identity(self.media)
        seg, audio = self.seg(), hygiene.audio_key(self.c, 's')
        self.c.receipt('prepare', True)
        self.assertEqual(cs.receipt_fingerprint(self.c), self.c.input_fingerprint)
        self.assertEqual(seg, self.seg())
        self.assertEqual(audio, hygiene.audio_key(self.c, 's'))
        self.mutate()
        self.assertEqual(fast, cs.media_stat_digest(self.media))
        self.assertNotEqual(identity, cs.verified_media_identity(self.media))
        self.assertNotEqual(seg, self.seg())
        self.assertNotEqual(audio, hygiene.audio_key(self.c, 's'))
        self.assertNotEqual(self.c.input_fingerprint, cs.receipt_fingerprint(self.c))
        with self.assertRaises(cs.InputIdentityError):
            cs.verify_input_identity(self.c)
        new = Ctx(self.tl)
        self.assertEqual(cs.load_receipt(self.c.out / 'receipt.json', new.input_fingerprint)['stages'], {})

    def test_manifest_is_provenance_not_a_digest_read_bypass(self):
        self.c.receipt('prepare', True)
        record = json.loads((self.c.out / 'receipt.json').read_text())
        self.assertEqual(record['schema'], cs.RECEIPT_SCHEMA)
        self.assertEqual(record['source_identities']['source.bin'], cs.verified_media_identity(self.media))
        self.assertTrue(cs.load_receipt(self.c.out / 'receipt.json', self.c.input_fingerprint)['stages'])
        with patch.object(cs, 'file_digest', side_effect=PermissionError('read denied')):
            with self.assertRaises(cs.InputIdentityError):
                cs.verify_input_identity(self.c)
            for call in (self.seg, lambda: hygiene.audio_key(self.c, 's'), lambda: cs.receipt_fingerprint(self.c)):
                with self.assertRaises(PermissionError):
                    call()
            self.assertIsNone(lc.lookup(self.media, self.root))

    def test_missing_source_cannot_mint_formal_identity(self):
        self.media.unlink()
        with self.assertRaises(OSError):
            Ctx(self.tl)

    def test_legacy_and_malformed_receipt_cannot_inherit_stages(self):
        path = self.root / 'receipt.json'
        for record in ([], {'input_fingerprint': self.c.input_fingerprint, 'stages': {'qc': {'ok': True}}},
                       {'schema': cs.RECEIPT_SCHEMA, 'input_fingerprint': self.c.input_fingerprint, 'stages': []}):
            path.write_text(json.dumps(record))
            self.assertEqual(cs.load_receipt(path, self.c.input_fingerprint)['stages'], {})

    def test_loudness_invalidates_and_remeasures(self):
        calls = []
        def measure():
            calls.append(1)
            return -14., -1.
        lc.measure_and_store(self.media, measure, self.root)
        self.assertIsNotNone(lc.lookup(self.media, self.root))
        self.mutate()
        self.assertIsNone(lc.lookup(self.media, self.root))
        lc.measure_and_store(self.media, measure, self.root)
        self.assertEqual(len(calls), 2)
        self.assertIsNotNone(lc.lookup(self.media, self.root))

    def test_loudness_legacy_or_unreadable_does_not_reuse_or_store(self):
        key, data = lc._key_of(self.media)
        path = lc._receipts_path(self.media, self.root)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'entries': {key: {'i_lufs': -14, 'tp_dbfs': -1}}}))
        self.assertIsNone(lc.lookup(self.media, self.root))
        before = path.read_bytes()
        with patch.object(cs, 'file_digest', side_effect=PermissionError('read denied')):
            lc.measure_and_store(self.media, lambda: (-14, -1), self.root)
        self.assertEqual(before, path.read_bytes())

    def test_loudness_post_measurement_read_error_is_not_persisted(self):
        initial = lc._key_of(self.media)
        with patch.object(lc, '_key_of', side_effect=[initial, None]):
            lc.measure_and_store(self.media, lambda: (-14, -1), self.root)
        self.assertFalse(lc._receipts_path(self.media, self.root).exists())

    def test_loudness_changed_during_measurement_is_not_persisted(self):
        def measure():
            self.mutate()
            return -14, -1
        lc.measure_and_store(self.media, measure, self.root)
        self.assertFalse(lc._receipts_path(self.media, self.root).exists())


if __name__ == '__main__':
    unittest.main()
