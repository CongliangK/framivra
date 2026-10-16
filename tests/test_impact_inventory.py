"""Adversarial contracts used by BOTH HTML and real-media acceptance runners."""
import os
from pathlib import Path
import tempfile
import unittest

from tests.impact_inventory import category, delta, observe, validate_sidecars


class ImpactInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, data=b'product'):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def products(self):
        return observe(self.root)['products']

    def test_exact_new_gfx_frame_expansion_passes(self):
        self.write('cache/gfx/retired/f0000.png')
        before = self.products()
        for i in range(90):
            self.write(f'cache/gfx/current/f{i:04d}.png')
        result = delta(before, self.products(), ['cache/gfx/current'], {'cache/gfx/current': 90})
        self.assertTrue(result['passed'], result)
        self.assertEqual(len(result['actual_raw_added']), 90)

    def test_extra_retired_directory_png_fails_even_with_correct_new_directory(self):
        self.write('cache/gfx/retired/f0000.png')
        before = self.products()
        self.write('cache/gfx/current/f0000.png')
        self.write('cache/gfx/retired/f0090.png')
        result = delta(before, self.products(), ['cache/gfx/current'], {'cache/gfx/current': 1})
        self.assertFalse(result['passed'])
        self.assertEqual(result['unexpected_raw_added'], ['cache/gfx/retired/f0090.png'])
        # This is precisely the case that the former directory-only check accepted.
        self.assertEqual(result['actual_new'], ['cache/gfx/current'])

    def test_unexpected_audio_seg_and_unknown_namespace_files_fail(self):
        for name in ('cache/audio/unexpected.wav', 'cache/seg/unexpected.mp4',
                     'cache/other/unexpected.bin'):
            with self.subTest(name=name):
                before = self.products()
                self.write(name)
                result = delta(before, self.products(), [])
                self.assertFalse(result['passed'])
                self.assertIn(name, result['unexpected_raw_added'])

    def test_wrong_frame_name_same_count_fails(self):
        self.write('cache/gfx/current/f0001.png')
        result = delta({}, self.products(), ['cache/gfx/current'], {'cache/gfx/current': 1})
        self.assertFalse(result['passed'])
        self.assertEqual(result['missing_raw_added'], ['cache/gfx/current/f0000.png'])

    def test_unrelated_product_removal_fails_with_expected_addition(self):
        for old in ('cache/audio/old.wav', 'cache/seg/old.mp4', 'cache/gfx/retired/f0000.png'):
            with self.subTest(old=old):
                path = self.write(old)
                before = self.products()
                path.unlink()
                new = self.write('cache/vseg/new.mp4')
                expected = [] if 'cache/vseg/new.mp4' in before else ['cache/vseg/new.mp4']
                result = delta(before, self.products(), expected)
                self.assertFalse(result['passed'])
                self.assertIn(old, result['removed'])
                new.unlink()

    def test_same_size_restored_mtime_overwrite_fails(self):
        for name in ('cache/audio/old.wav', 'cache/seg/old.mp4', 'cache/gfx/retired/f0000.png'):
            with self.subTest(name=name):
                path = self.write(name, b'AAAA')
                before = self.products()
                stat = path.stat()
                path.write_bytes(b'BBBB')
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                after = self.products()
                self.assertEqual(before[name]['size'], after[name]['size'])
                self.assertEqual(before[name]['mtime_ns'], after[name]['mtime_ns'])
                result = delta(before, after, [])
                self.assertFalse(result['passed'])
                self.assertIn(name, result['rewritten'])

    def test_pass_sidecar_requires_predicted_media_and_actual_frame_count(self):
        name = 'cache/vseg_pass/p0_0123456789ab.mkv'
        self.write(name)
        self.write(name + '.n', b'90')
        products = self.products()
        self.assertTrue(delta({}, products, [name])['passed'])
        verified = {}
        checks = validate_sidecars(self.root, products, lambda path: 90, verified)
        self.assertEqual(checks[name + '.n']['actual_decoded_frames'], 90)
        # A warm reuse is authorized only by the exact media content digest.
        validate_sidecars(self.root, products, lambda path: self.fail('duplicate probe'), verified)
        with self.assertRaises(AssertionError):
            validate_sidecars(self.root, products, lambda path: 89, {})
        (self.root / name).unlink()
        with self.assertRaisesRegex(AssertionError, 'orphan'):
            validate_sidecars(self.root, self.products(), lambda path: 90, {})

    def test_old_pass_sidecar_changes_and_deletions_fail(self):
        name = 'cache/vseg_pass/p0_0123456789ab.mkv'
        self.write(name)
        sidecar = self.write(name + '.n', b'90')
        before = self.products()
        stat = sidecar.stat()
        sidecar.write_bytes(b'89')
        os.utime(sidecar, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        changed = self.products()
        self.assertFalse(delta(before, changed, [])['passed'])
        with self.assertRaises(AssertionError):
            validate_sidecars(self.root, changed, lambda path: 90, {})
        sidecar.unlink()
        self.assertFalse(delta(before, self.products(), [])['passed'])
        # Recreating an unrelated old pass sidecar is not a predicted addition.
        without = self.products()
        self.write(name + '.n', b'90')
        self.assertFalse(delta(without, self.products(), [])['passed'])

    def test_segment_metadata_exceptions_do_not_hide_products_elsewhere(self):
        for name in ('cache/seg/list.txt', 'cache/audio/.graph_digest',
                     'cache/gfx/retired/list.txt', 'cache/vseg/nested/list.txt',
                     'cache/vseg/list.txt.mp4', 'cache/other/subs_000000.ass',
                     'cache/seg/p0_0123456789ab.mkv.n', 'cache/vseg_pass/random.mkv.n',
                     'cache/vseg_pass/p0_0123456789ab.mkv.n.mp4'):
            with self.subTest(name=name):
                before = self.products()
                self.write(name)
                self.assertEqual(category(name), 'products')
                result = delta(before, self.products(), [])
                self.assertFalse(result['passed'])
                self.assertIn(name, result['unexpected_raw_added'])

    def test_mutable_classes_are_observed_separately_not_no_write_claims(self):
        examples = {
            'cache/manifest.json': 'mutable_metadata',
            'cache/vseg/subs_000000.ass': 'mutable_metadata',
            'cache/vseg/list.txt': 'mutable_metadata',
            'cache/vseg/.graph_digest': 'mutable_metadata',
            'cache/vseg_qc/list.txt': 'mutable_metadata',
            'cache/vseg_draft/.graph_digest': 'mutable_metadata',
            'cache/gfx/key/complete.json': 'mutable_metadata',
            'cache/vseg/file.mp4.complete.json': 'mutable_metadata',
            'cache/vseg/file.mp4.lock': 'mutable_metadata',
            'out/receipt.json': 'mutable_metadata',
            'cache/qc_clean.mp4': 'mutable_outputs',
            'out/video_noaudio.mp4': 'mutable_outputs',
            'cache/tmp_run/ledger.json': 'temporary_runtime',
        }
        for name, group in examples.items():
            self.write(name)
            self.assertEqual(category(name), group)
        observation = observe(self.root)
        self.assertEqual(observation['products'], {})
        for name, group in examples.items():
            self.assertIn(name, observation[group])
        self.assertTrue(delta({}, observation['products'], [])['passed'])


if __name__ == '__main__':
    unittest.main()
